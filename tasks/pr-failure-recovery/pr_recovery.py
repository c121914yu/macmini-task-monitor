"""One bounded Codex diagnosis per PR head; preserve the ordinary retry budget."""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path


def eligible(entry, maximum, now):
    """Recovery is separate from retries; never bypass policy blocks or stale-head waiting."""
    head = entry.get('snapshot', {}).get('head')
    return bool(head and entry.get('status') == 'error'
                and entry.get('attempts', 0) >= maximum and not entry.get('retry_blocked')
                and entry.get('recovery_head') != head and entry.get('phase') != 'active'
                and entry.get('ready_at') is not None and entry['ready_at'] <= now)


def candidates(state, maximum, now):
    return sorted((n for n,e in state['entries'].items() if eligible(e,maximum,now)),
                  key=lambda n:(state['entries'][n].get('queued_at',0),int(n)))


def git_preparation_error(error):
    """Only the preparation boundary triggers immediate diagnosis; no text implies authorship."""
    text = str(error).lower()
    return any(word in text for word in ('git failed:', 'submodule', 'worktree', 'comparison base'))


def evidence(root, job, error):
    """Capture only this snapshot's failures; never expose credentials or unrelated PR logs."""
    from review_failures import failure_fields
    if failure_fields(error).get('retry_blocked'):
        raise RuntimeError('Policy-blocked snapshot requires manual reconciliation')
    from queue_state import queue_lock, load_queue, save_queue
    with queue_lock():
        queue = load_queue()
        entry = queue['entries'].get(str(job['number']), {})
        if entry.get('retry_blocked'):
            raise RuntimeError('Policy-blocked snapshot requires manual reconciliation')
        if entry.get('snapshot', {}).get('head') != job['head_sha']:
            from pr_eligibility import SnapshotSuperseded
            raise SnapshotSuperseded('Recovery snapshot changed')
        entry['recovery_head'] = job['head_sha']
        entry['job_key'] = job['key']
        save_queue(queue)
    history = []
    for channel in ('runs','dependency-runs'):
        for path in (Path(root)/channel).glob(f'{job["number"]}-*/state.json'):
            source = path.with_name('job.json')
            if not source.exists():
                continue
            old = json.loads(source.read_text())
            if old.get('head_sha') != job['head_sha'] or old.get('repo') != job['repo']:
                continue
            data = json.loads(path.read_text())
            if data.get('error'):
                history.append({'path':str(path),'error':data['error'],'status':data.get('status')})
    return {'repo':job['repo'],'number':job['number'],'head':job['head_sha'],
            'error':str(error),'history':history,'captured_at':time.time()}


async def execute(job, state, error):
    """The caller holds the PR/job locks; diagnosis and subsequent review reuse its chat."""
    import review_runner as rr
    from review_failures import failure_fields
    await asyncio.to_thread(rr.require_reviewable,job,state)
    folder=Path(job['folder'])
    receipt=folder/'recovery.json'
    if receipt.exists():
        # Starting/sent/uncertain results cannot be reset into another paid turn.
        raise RuntimeError('This snapshot already has a recovery receipt; reconcile it before further work')
    context=await asyncio.to_thread(evidence,rr.ROOT,job,error)
    rr.write_json(folder/'recovery-context.json',context)
    rr.write_json(receipt,{'status':'starting','head':job['head_sha'],'started_at':time.time()})
    job['recovery_mode']=True
    job['worktree']=str(folder)
    state.update(status='diagnosing',attempt_id=os.environ.get('FASTGPT_REVIEW_ATTEMPT_ID'))
    rr.write_json(folder/'job.json',job)
    rr.write_json(folder/'state.json',state)
    try:
        await rr.review_job(job,state)
        decision=json.loads((folder/'recovery-decision.json').read_text())
        if (decision.get('head')!=job['head_sha'] or decision.get('cause') not in ('pr','local','unknown')
                or not decision.get('evidence')):
            raise RuntimeError('Recovery diagnosis lacks a fixed head, cause or evidence')
        await asyncio.to_thread(rr.require_reviewable,job,state)
        if decision['cause']=='pr':
            actor=json.loads(rr.command(['gh','api','user']))['login']
            review=await asyncio.to_thread(rr.published_review,job,actor)
            if not review:
                raise RuntimeError('Remote recovery review has no confirmed publication')
            state.update(status='published',reviewId=review['id'],review_url=review['html_url'],
                         recovery_cause='pr')
            from queue_state import queue_lock,load_queue,save_queue
            with queue_lock():
                queue=load_queue()
                entry=queue['entries'].get(str(job['number']),{})
                if entry.get('snapshot',{}).get('head')==job['head_sha']:
                    entry['recovery_review_url']=review['html_url']
                    save_queue(queue)
        elif decision['cause']=='local' and decision.get('repaired') is True and decision.get('validation'):
            state.update(status='recovery_repaired',recovery_cause='local')
        else:
            raise RuntimeError('Root cause or local repair remains unverified; no further automatic retry')
        rr.write_json(receipt,{'status':'completed','head':job['head_sha'],'decision':decision,
                               'finished_at':time.time()})
        rr.write_json(folder/'state.json',state)
        return decision['cause']
    except BaseException as failure:
        state.update(status='needs_inspection',**failure_fields(failure))
        rr.write_json(folder/'state.json',state)
        rr.write_json(receipt,{'status':'failed','head':job['head_sha'],'error_type':type(failure).__name__,
                               'finished_at':time.time()})
        raise
    finally:
        job.pop('recovery_mode',None)


async def run_number(number):
    """Exhausted snapshots get one diagnostic slot, never a reset of attempts."""
    import review_runner as rr
    from queue_state import queue_lock,load_queue,save_queue
    from pr_eligibility import skip_reason,SnapshotSuperseded
    with queue_lock():
        entry=dict(load_queue()['entries'][str(number)])
    if entry.get('retry_blocked'):
        raise RuntimeError('Policy-blocked snapshot requires manual reconciliation')
    pr=await asyncio.to_thread(rr.pr_metadata,number)
    if skip_reason(pr):
        rr.record_skip(number,skip_reason(pr))
        return
    if pr['headRefOid']!=entry['snapshot']['head']:
        raise SnapshotSuperseded('Recovery head changed before start')
    try:
        job=await asyncio.to_thread(rr.prepare_job,number)
    except Exception:
        # A missing GitHub comparison base is a disclosed blocker, never a fabricated base.
        identity={'repo':rr.REPO,'number':number,'head_sha':pr['headRefOid'],
                  'base_ref':pr['baseRefName'],'merge_base':None}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        folder=rr.ROOT/'runs'/f'{number}-{key[:16]}';folder.mkdir(parents=True,exist_ok=True)
        job={**identity,'key':key,'folder':str(folder),'pr':pr,'pr_url':pr['url'],'title':pr['title'],
             'marker':f'<!-- fastgpt-codex-review:v2:{rr.REPO}:{number}:{pr["headRefOid"]}:{pr["baseRefName"]}:unknown -->'}
    folder=Path(job['folder'])
    state=json.loads((folder/'state.json').read_text()) if (folder/'state.json').exists() else {}
    actor=json.loads(rr.command(['gh','api','user']))['login']
    existing=await asyncio.to_thread(rr.published_review,job,actor)
    if existing:
        state.update(status='published',reviewId=existing['id'],review_url=existing['html_url'])
        rr.write_json(folder/'state.json',state)
        return
    import fcntl
    with (folder/'job.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        cause=await execute(job,state,state.get('error') or entry.get('error','Regular retries exhausted'))
    if cause=='local':
        if entry.get('route') == 'dependency_update':
            from dependency_runner import run
            await run(number)
            return
        # Strict host checkout/Pro/dependency preparation verifies the repair once.
        actor=json.loads(rr.command(['gh','api','user']))['login']
        await rr.run_job(job,{'actor':actor,'retry':True,'check_only':False,'notify_only':False,'rerun':False})
