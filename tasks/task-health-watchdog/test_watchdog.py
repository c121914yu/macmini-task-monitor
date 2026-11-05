import asyncio,copy,json,sqlite3,tempfile,unittest
from pathlib import Path
import watchdog as w
import repair_agent as agent

class WatchdogTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.now=100000
  self.task={'id':'sample','name':'示例','label':'com.yjl.sample','source':'launchd','instrumented':True,'loaded':True,'updated_at':self.now,'state':'attention','data':{}}
 def failures(self,n=3):
  return [{'id':str(i),'status':'failed','created_at':self.now-4000-i*100,'started_at':self.now-4000-i*100,'summary':'failed'} for i in range(n)]
 def scan(self,runs,dispatch=lambda *a:None,offset=0,readonly=False):
  self.task['updated_at']=self.now+offset
  return w.scan(self.root,collect=lambda:([self.task],{self.task['id']:runs}),dispatch=dispatch,now=self.now+offset,inspect_only=readonly)
 def test_healthy_old_launchd_error_never_dispatches(self):
  self.task['last_exit_code']=1
  self.scan([{'status':'success'}],lambda *a:self.fail())
 def test_threshold_and_success_reset(self):
  for n in [1,2]:self.assertIsNone(w.signal(self.task,self.failures(n),self.now,w.DEFAULTS))
  self.assertIsNotNone(w.signal(self.task,self.failures(),self.now,w.DEFAULTS))
  self.assertIsNone(w.signal(self.task,[{'status':'success'},*self.failures()],self.now,w.DEFAULTS))
  self.assertIsNone(w.signal(self.task,[{**r,'started_at':self.now-60} for r in self.failures()],self.now,w.DEFAULTS))
 def test_dedupe_cooldown_and_three_round_limit(self):
  calls=[]
  for offset in [0,0,3599,3600,7200,10800]:
   self.scan(self.failures(),lambda s,i,f:calls.append(i['id']),offset)
  self.assertEqual(len(calls),3);self.assertEqual(len(set(calls)),1)
 def test_uncertain_never_redelivers(self):
  calls=[]
  def dispatch(s,i,f):
   calls.append(1);i['status']='uncertain';raise RuntimeError('unknown')
  self.scan(self.failures(),dispatch);self.scan(self.failures(),dispatch,7200)
  self.assertEqual(calls,[1])
 def test_paused_process_needs_persistence(self):
  self.task.update(id='pr-worker',state='running',data={'storage':{'paused':True}})
  calls=[]
  self.scan([{'status':'running','heartbeat_at':self.now}],lambda *a:calls.append(1))
  self.assertFalse(calls)
  self.scan([{'status':'running','heartbeat_at':self.now+3600}],lambda *a:calls.append(1),3600)
  self.assertEqual(calls,[1])
 def test_excludes_self_disabled_stale_other_vendors(self):
  variants=[{**self.task,'id':w.SELF_ID},{**self.task,'loaded':False},{**self.task,'updated_at':1},{**self.task,'label':'com.apple.example'}]
  self.assertEqual(w.eligible_tasks(variants,self.now,w.DEFAULTS),[])
 def test_real_success_required_for_resolution(self):
  state=self.scan(self.failures())
  self.assertEqual(state['incidents']['sample']['status'],'awaiting_verification')
  state=self.scan([{'status':'success'}])
  self.assertEqual(state['incidents']['sample']['status'],'resolved')
 def test_check_only_no_state_or_model(self):
  self.scan(self.failures(),lambda *a:self.fail(),readonly=True)
  self.assertFalse((self.root/'state.json').exists())
 def test_readonly_execution_ledger(self):
  p=self.root/'db';db=sqlite3.connect(p)
  db.execute('create table tasks(snapshot text)')
  db.execute('insert into tasks values (?)',(json.dumps(self.task),))
  db.execute('create table runs(id text,task_id text,kind text,status text,created_at real,started_at real,finished_at real,heartbeat_at real,summary text,log_tail text)')
  for key,kind,status in [('real','execution','failed'),('skip','execution','skipped'),('old','history','failed')]:
   db.execute('insert into runs values(?,?,?,?,?,?,?,?,?,?)',(key,'sample',kind,status,1,1,2,2,'',''))
  db.commit();db.close()
  self.assertEqual([r['id'] for r in w.snapshot(p)[1]['sample']],['real'])
 def test_new_calendar_install_waits_for_first_due_time(self):
  definition=self.root/'job.plist';definition.write_text('fixture')
  import os
  os.utime(definition,(self.now,self.now))
  self.task.update(state='overdue',previous_at=self.now-3600,definition_path=str(definition))
  self.assertIsNone(w.signal(self.task,[],self.now,w.DEFAULTS))
  self.task['previous_at']=self.now+3600
  self.assertIsNotNone(w.signal(self.task,[],self.now+4000,w.DEFAULTS))
 def test_redacted_evidence(self):
  e=w.evidence(self.task,[{'summary':'password=private','log_tail':'https://open.feishu.cn/open-apis/bot/v2/hook/private'}],{})
  self.assertNotIn('private',json.dumps(e))

class FakeServer:
 calls=[];fail_turn=False
 def __init__(self,folder):
  self.folder=folder;self.model_provider='openai';self.config={};self.events=asyncio.Queue()
 async def __aenter__(self):return self
 async def __aexit__(self,*a):pass
 async def send(self,method,params):
  type(self).calls.append((method,params))
  if method=='project/list':return {'data':[{'id':'p','name':'FastGPT','roots':[{'path':'/Volumes/code/dev/FastGPT'}]}]}
  if method in ['thread/start','thread/resume']:return {'thread':{'id':'chat'},'model':'gpt-6.1-sol','reasoningEffort':'xhigh','serviceTier':None}
  if method=='turn/start':
   if type(self).fail_turn:raise RuntimeError('response lost')
   (self.folder/'result.json').write_text('{"status":"repaired"}')
   await self.events.put({'method':'turn/completed','params':{'threadId':'chat','turn':{'id':'turn','status':'completed'}}})
   return {'turn':{'id':'turn'}}
  return {}

class DispatchTests(unittest.IsolatedAsyncioTestCase):
 async def test_reuses_chat_and_persists_creation_intent(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);state={};i={'id':'incident','task_id':'sample','name':'示例','attempts':1}
   FakeServer.calls=[];FakeServer.fail_turn=False;saved=[]
   await agent.repair(p,state,i,p,lambda:saved.append(copy.deepcopy(state)),FakeServer)
   await agent.repair(p,state,i,p,lambda:None,FakeServer)
   methods=[m for m,a in FakeServer.calls]
   self.assertEqual(methods.count('thread/start'),1);self.assertEqual(methods.count('thread/resume'),1)
   self.assertTrue(any(s['threads']['sample'].get('creation_pending') for s in saved))
   for m,a in FakeServer.calls:
    if m=='turn/start':self.assertIsNone(a['serviceTier']);self.assertEqual(a['effort'],'xhigh')
 async def test_unknown_start_response_is_not_retried(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);state={};i={'task_id':'sample','name':'示例','attempts':1}
   FakeServer.calls=[];FakeServer.fail_turn=True
   try:
    with self.assertRaisesRegex(RuntimeError,'response lost'):await agent.repair(p,state,i,p,lambda:None,FakeServer)
    self.assertTrue(i['turn_pending']);self.assertEqual(i['status'],'uncertain')
   finally:FakeServer.fail_turn=False

if __name__=='__main__':unittest.main()
