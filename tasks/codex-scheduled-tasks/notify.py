#!/usr/bin/env python3
"""Only notification entry for migrated tasks. AI does not receive webhook URLs."""
import argparse
import fcntl
import hashlib
import json
import sys
import time
from pathlib import Path
from common import ROOT, configuration, read, run_folder, save

sys.path.insert(0, str(Path.home() / '.local/share/webhook-delivery'))
from webhook_delivery import send, DeliveryError

BLOCKED = {'sending', 'unknown', 'rate_limited', 'failed', 'rejected'}


def validate(payloads):
    """Validate all segments before sending any; hash freezes the exact batch."""
    if not isinstance(payloads, list) or not payloads:
        raise ValueError('Nonempty payloads required')
    for payload in payloads:
        card = payload.get('card', {}) if isinstance(payload, dict) else {}
        if (payload.get('msg_type') != 'interactive' or card.get('schema') != '2.0'
                or not isinstance(card.get('body', {}).get('elements'), list)
                or 'elements' in card):
            raise ValueError('Card 2.0 body.elements required')
        if len(json.dumps(payload, ensure_ascii=False).encode()) >= 19000:
            raise ValueError('Card must be smaller than 19 KB')
    return hashlib.sha256(json.dumps(payloads, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_business(task, key, folder, payloads):
    """Delivery cannot precede source coverage and exact mail-identity validation."""
    if task == 'mail-digest':
        manifest = read(folder/'mail-included.json')
        if not isinstance(manifest, dict):
            raise ValueError('Mail inclusion manifest missing')
        captured = read(folder/'gmail-captured.json', {'messages': []})
        gmail = {x['message_id']:x for x in captured['messages']}
        included = manifest.get('gmail', [])
        if not isinstance(included, list) or len(set(included)) != len(included):
            raise ValueError('Invalid Gmail IDs')
        from common import window, ZONE
        import datetime as dt
        start,end = window(task,key,0)
        for identifier in included:
            stamp = dt.datetime.fromisoformat(gmail[identifier]['received_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None or not start <= stamp < end:
                raise ValueError('Included Gmail message outside report window')
        imap = {x['username']:x for x in read(folder/'imap.json')}
        for mailbox, ids in manifest.get('imap', {}).items():
            source = imap[mailbox]
            if source['status'] != 'complete' or not set(ids) <= {x['uid'] for x in source['messages']}:
                raise ValueError('Included IMAP UID not captured')
        rendered = json.dumps(payloads, ensure_ascii=False)
        config = read(ROOT/'private.json')[task]
        for name in [config['gmail_display']] + [x['username'] for x in config['imap']]:
            if name not in rendered:
                raise ValueError('Mailbox section missing')
    elif task == 'signoz-report':
        totals = {k:v['total'] for k,v in read(folder/'signoz.json')['current'].items()}
        expected = f'Zod {totals["zod"]} · LLM {totals["llm"]} · 插件不存在 {totals["plugin"]}'
        actual = payloads[0]['card']['header'].get('subtitle',{}).get('content')
        if actual != expected:
            raise ValueError('Signoz card totals differ from independent counts')
    elif task == 'pr-weekly-report':
        text = (folder/'document-readback.xml').read_text()
        for items in read(folder/'prs.json').values():
            for item in items:
                if item['url'] not in text or 'https://github.com/'+item['author']['login'] not in text:
                    raise ValueError('Weekly document source coverage incomplete')
    else:
        raise ValueError('Task does not authorize webhook notifications')


def deliver(task, key, batch_path, sender=send):
    """Resend only the original payload within the shared 60s/3-retry budget."""
    folder = run_folder(task, key)
    batch_path = Path(batch_path).resolve()
    if not batch_path.is_relative_to(folder.resolve()) or batch_path.name != 'notifications.json':
        raise ValueError('Batch must be this run notifications.json')
    batch = read(batch_path)
    payloads = batch['payloads']
    digest = validate(payloads)
    validate_business(task, key, folder, payloads)
    config = read(ROOT / 'private.json')[task]
    webhook = config['webhook']
    target = hashlib.sha256(webhook.encode()).hexdigest()
    receipt_path = folder / 'notification.json'
    with (folder / 'notification.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = read(receipt_path, {})
        if receipt:
            if receipt.get('payload_hash') != digest or receipt.get('target_hash') != target:
                raise RuntimeError('Existing delivery identity changed; reconcile manually')
            if receipt.get('status') == 'sent':
                return receipt
            # A crash after the intent may have delivered; never assume it failed.
            if receipt.get('status') in BLOCKED:
                last = (receipt.get('segments') or [{}])[-1]
                if receipt.get('status') != 'sending' or last.get('status') not in ('retry_wait','sent'):
                    raise RuntimeError('Delivery already failed or uncertain; original receipt retained')
                # An explicit limit response or confirmed success is safe to resume.
                # A persisted sending intent without a response always remains unknown.
        else:
            receipt = {'payload_hash': digest, 'target_hash': target,
                       'status': 'prepared', 'segments': []}
            save(receipt_path, receipt)
        for index, payload in enumerate(payloads):
            if index < len(receipt['segments']) and receipt['segments'][index]['status'] == 'sent':
                continue
            if index < len(receipt['segments']):
                segment = receipt['segments'][index]
                if segment.get('status') != 'retry_wait':
                    raise RuntimeError('Uncertain segment requires reconciliation')
                initial_attempts = segment['attempt']
                not_before = segment.get('next_attempt_at')
            else:
                segment = {'index': index, 'status': 'sending', 'attempts': 0}
                receipt['segments'].append(segment)
                initial_attempts, not_before = 0, None
            receipt['status'] = 'sending'
            save(receipt_path, receipt)
            def observe(event):
                segment.update(event)
                save(receipt_path, receipt)
            try:
                result = sender(webhook, payload, on_attempt=observe,
                                initial_attempts=initial_attempts, not_before=not_before)
            except DeliveryError as error:
                receipt['status'] = getattr(error, 'status', 'unknown')
                if receipt['status'] not in BLOCKED:
                    receipt['status'] = 'unknown'
                segment['status'] = receipt['status']
                save(receipt_path, receipt)
                raise RuntimeError('Webhook delivery ' + receipt['status']) from None
            segment.update({'status': 'sent', 'sent_at': time.time()})
            receipt['status'] = 'prepared'
            save(receipt_path, receipt)
        receipt.update({'status': 'sent', 'sent_at': time.time()})
        save(receipt_path, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--period', required=True)
    parser.add_argument('--batch', required=True)
    args = parser.parse_args()
    try:
        result = deliver(args.task, args.period, args.batch)
        print(json.dumps({'event': 'scheduled_notification', 'status': result['status'],
                          'segments': len(result['segments'])}))
    except Exception as error:
        print(json.dumps({'event': 'scheduled_notification', 'status': 'failed',
                          'error_type': type(error).__name__}))
        sys.exit(1)


if __name__ == '__main__':
    main()
