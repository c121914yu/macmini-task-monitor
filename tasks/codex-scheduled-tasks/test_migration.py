"""Focused migration regressions; no accounts, models, GitHub writes or messages."""
import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import common
import inputs
import run
import notify
from webhook_delivery import DeliveryError


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(m,'ROOT',self.root) for m in (common,inputs,run,notify)]
        for p in self.patches:
            p.start()
        self.folder = common.run_folder('signoz-report','2026-11-04')
        self.folder.mkdir(parents=True)
        common.save(self.root/'private.json',{'signoz-report':{'webhook':'https://open.feishu.cn/open-apis/bot/v2/hook/test'}})
        common.save(self.folder/'signoz.json',{'current':{k:{'total':0} for k in inputs.FILTERS}})
        self.payload = {'msg_type':'interactive','card':{'schema':'2.0',
                        'header':{'subtitle':{'content':'Zod 0 · LLM 0 · 插件不存在 0'}},
                        'body':{'elements':[]}}}
        self.batch = self.folder/'notifications.json'
        common.save(self.batch,{'payloads':[self.payload]})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_migrated_project_id_resolves_by_existing_checkout(self):
        config={'project_id':'old','cwd':'/tmp/plugin'}
        projects=[{'id':'current','roots':[{'path':'/tmp/plugin'}]},
                  {'id':'unrelated','roots':[{'path':'/tmp/other'}]}]
        self.assertEqual(run.resolve_project(config,projects),'current')
        self.assertIsNone(run.resolve_project({'project_id':None,'cwd':'/tmp'},projects))
        with self.assertRaises(RuntimeError):
            run.resolve_project(config,[])
        with self.assertRaises(RuntimeError):
            run.resolve_project(config,projects+[{'id':'duplicate','roots':[{'path':'/tmp/plugin'}]}])
        self.assertEqual(run.resolve_project({**config,'project_id':'current'},
            projects+[{'id':'duplicate','roots':[{'path':'/tmp/plugin'}]}]),'current')

    def test_partial_outcomes_still_require_business_evidence(self):
        common.save(self.folder/'result.json',{'status':'partial','summary':'with limits',
                    'log_analysis_complete':True})
        common.save(self.folder/'signoz.json',{'current':{k:{'complete':True}for k in inputs.FILTERS}})
        with self.assertRaises(RuntimeError):
            run.validate_result('signoz-report',self.folder)
        common.save(self.folder/'notification.json',{'status':'sent'})
        self.assertEqual(run.validate_result('signoz-report',self.folder)['status'],'partial')
        with self.assertRaises(RuntimeError):
            run.validate_result('plugin-model-sync',self.folder)
        (self.folder/'report.md').write_text('Verified changes; catalog limitations recorded')
        self.assertEqual(run.validate_result('plugin-model-sync',self.folder)['status'],'partial')
        with self.assertRaises(RuntimeError):
            run.validate_result('pr-weekly-report',self.folder)

    def test_shanghai_period_and_original_friday_gap(self):
        now=dt.datetime(2026,11,6,10,tzinfo=common.ZONE)
        self.assertEqual(common.period('mail-digest',now),'2026-11-05')
        self.assertEqual(common.period('plugin-model-sync',now),'2026-11-06')
        start,end=common.window('pr-weekly-report','2026-11-06',now.timestamp())
        self.assertEqual(start.isoformat(),'2026-10-30T12:00:00+08:00')
        self.assertEqual(end,now)
        self.assertEqual(common.period('pr-weekly-report',now+dt.timedelta(days=1)),'2026-11-06')

    def test_duplicate_delivery_never_posts_twice(self):
        sender=MagicMock(return_value={'status':'sent'})
        notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.assertEqual(sender.call_count,1)

    def test_unknown_and_exhausted_budget_do_not_reset(self):
        for status in ('unknown','rate_limited'):
            with self.subTest(status=status):
                (self.folder/'notification.json').unlink(missing_ok=True)
                sender=MagicMock(side_effect=DeliveryError(status,[]))
                with self.assertRaises(RuntimeError):
                    notify.deliver('signoz-report','2026-11-04',self.batch,sender)
                with self.assertRaises(RuntimeError):
                    notify.deliver('signoz-report','2026-11-04',self.batch,sender)
                self.assertEqual(sender.call_count,1)

    def test_segment_resume_skips_confirmed_first_segment(self):
        other=json.loads(json.dumps(self.payload))
        other['card']['body']['elements']=[{'tag':'markdown','content':'segment 2'}]
        values=[self.payload,other]
        common.save(self.batch,{'payloads':values})
        import hashlib
        target=hashlib.sha256(common.read(self.root/'private.json')['signoz-report']['webhook'].encode()).hexdigest()
        common.save(self.folder/'notification.json',{'status':'prepared','payload_hash':notify.validate(values),
            'target_hash':target,'segments':[{'index':0,'status':'sent'}]})
        sender=MagicMock(return_value={'status':'sent'})
        notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.assertEqual(sender.call_count,1)
        self.assertEqual(sender.call_args.args[1],other)

    def test_interrupted_rate_wait_keeps_attempt_budget_and_deadline(self):
        import hashlib,time
        target=hashlib.sha256(common.read(self.root/'private.json')['signoz-report']['webhook'].encode()).hexdigest()
        deadline=time.time()+60
        common.save(self.folder/'notification.json',{'status':'sending',
            'payload_hash':notify.validate([self.payload]),'target_hash':target,
            'segments':[{'index':0,'status':'retry_wait','attempt':2,'next_attempt_at':deadline}]})
        sender=MagicMock(return_value={'status':'sent'})
        notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.assertEqual(sender.call_args.kwargs['initial_attempts'],2)
        self.assertEqual(sender.call_args.kwargs['not_before'],deadline)
        self.assertEqual(len(common.read(self.folder/'notification.json')['segments']),1)

    def test_uncertain_sending_intent_cannot_restart(self):
        sender=MagicMock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        with self.assertRaises(RuntimeError):
            notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.assertEqual(sender.call_count,1)

    def test_changed_payload_or_target_is_not_new_delivery(self):
        sender=MagicMock(return_value={'status':'sent'})
        notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.payload['card']['body']['elements']=[{'tag':'markdown','content':'changed'}]
        common.save(self.batch,{'payloads':[self.payload]})
        with self.assertRaises(RuntimeError):
            notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        self.assertEqual(sender.call_count,1)

    def test_all_cards_validated_before_first_post(self):
        bad={'msg_type':'text'}
        common.save(self.batch,{'payloads':[self.payload,bad]})
        sender=MagicMock()
        with self.assertRaises(ValueError):
            notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        sender.assert_not_called()

    def test_false_signoz_total_blocks_delivery(self):
        self.payload['card']['header']['subtitle']['content']='Zod 1 · LLM 0 · 插件不存在 0'
        common.save(self.batch,{'payloads':[self.payload]})
        sender=MagicMock()
        with self.assertRaises(ValueError):
            notify.deliver('signoz-report','2026-11-04',self.batch,sender)
        sender.assert_not_called()

    def test_mail_write_is_gated_on_every_segment_sent(self):
        with patch.object(inputs.imaplib,'IMAP4_SSL') as client:
            with self.assertRaises(RuntimeError):
                inputs.mark_imap(self.folder)
            client.assert_not_called()

    def test_mark_imap_rejects_uid_outside_snapshot(self):
        common.save(self.folder/'notification.json',{'status':'sent'})
        common.save(self.folder/'mail-included.json',{'imap':{'a@example.com':['99']}})
        common.save(self.folder/'imap.json',[{'username':'a@example.com','status':'complete','messages':[{'uid':'1'}]}])
        common.save(self.root/'private.json',{'mail-digest':{'imap':[{'username':'a@example.com'}]}})
        with patch.object(inputs.imaplib,'IMAP4_SSL') as client:
            with self.assertRaises(ValueError):
                inputs.mark_imap(self.folder)
            client.assert_not_called()

    def test_uidvalidity_change_never_marks_reassigned_uid(self):
        common.save(self.folder/'notification.json',{'status':'sent'})
        common.save(self.folder/'mail-included.json',{'imap':{'a@example.com':['1']}})
        common.save(self.folder/'imap.json',[{'username':'a@example.com','status':'complete',
                    'uidvalidity':'100','messages':[{'uid':'1'}]}])
        common.save(self.root/'private.json',{'mail-digest':{'imap':[{
                    'username':'a@example.com','host':'example.invalid','password':'fixture'}]}})
        client=MagicMock()
        client.__enter__.return_value=client
        client.select.return_value=('OK',[])
        client.response.return_value=('UIDVALIDITY',[b'101'])
        with patch.object(inputs.imaplib,'IMAP4_SSL',return_value=client),patch.object(inputs.time,'sleep'):
            with self.assertRaises(RuntimeError):
                inputs.mark_imap(self.folder)
        client.uid.assert_not_called()

    def test_imap_collection_is_read_only_and_filters_actual_time(self):
        client=MagicMock()
        client.__enter__.return_value=client
        client.select.return_value=('OK',[])
        client.response.return_value=('UIDVALIDITY',[b'100'])
        def uid(op,*args):
            if op=='search':
                return 'OK',[b'1 2']
            timestamp=b'03-Nov-2026 23:59:59 +0800' if args[0]==b'1' else b'04-Nov-2026 00:00:00 +0800'
            return 'OK',[(b'1 (INTERNALDATE "'+timestamp+b'" BODY[] {40}',b'Subject: fixture\r\nFrom: a@example.com\r\n\r\nText'),b')']
        client.uid.side_effect=uid
        start,end=common.window('mail-digest','2026-11-04',0)
        with patch.object(inputs.imaplib,'IMAP4_SSL',return_value=client):
            result=inputs.imap_collect({'host':'example.invalid','username':'a','password':'fixture'},start,end)
        self.assertEqual([m['uid']for m in result['messages']],['2'])
        client.select.assert_called_once_with('INBOX',readonly=True)
        self.assertTrue(all(c.args[2]=='(INTERNALDATE BODY.PEEK[])' for c in client.uid.call_args_list if c.args[0]=='fetch'))

    def test_read_only_signoz_collects_both_days_and_three_categories(self):
        start,end=common.window('signoz-report','2026-11-04',0)
        queries=[]
        def query(sql,lo,hi):
            queries.append(sql)
            return {'data':[[0]]}
        with patch.object(inputs,'signoz_query',side_effect=query):
            result=inputs.collect_signoz(self.folder,start,end)
        self.assertEqual(result['totals'],{'zod':0,'llm':0,'plugin':0})
        self.assertEqual(len(queries), 2*3*24*12)  # 两日、三类、每小时十二个五分钟窗口。
        self.assertTrue(all(q.startswith('SELECT count()') for q in queries))
        self.assertTrue(all(x.get('complete') for d in common.read(self.folder/'signoz.json').values() if isinstance(d,dict) for x in d.values()))

    def test_github_splits_cap_before_requesting_rejected_pages(self):
        start,end=common.window('pr-weekly-report','2026-11-06',
                               dt.datetime(2026,11,6,10,tzinfo=common.ZONE).timestamp())
        requests=[]
        def invoke(args,**kwargs):
            requests.append(args)
            data={'total_count':1000 if len(requests)==1 else 0,
                  'incomplete_results':False,'items':[]}
            return __import__('subprocess').CompletedProcess(args,0,json.dumps(data),'')
        with patch.object(inputs.subprocess,'run',side_effect=invoke):
            result=inputs.collect_prs(self.folder,start,end)
        self.assertEqual(len(requests),5)
        self.assertTrue(all('--paginate' not in args for args in requests))
        for args in requests:
            fields = [args[i+1] for i,value in enumerate(args) if value == '-f']
            self.assertTrue(all('=' in field for field in fields))
            self.assertEqual(len([args[i+1] for i,value in enumerate(args) if value == '-f' and args[i+1].startswith('q=')]),1)
        self.assertEqual(sum(result['counts'].values()),0)

    def test_github_incomplete_results_are_not_zero(self):
        start,end=common.window('pr-weekly-report','2026-11-06',
                               dt.datetime(2026,11,6,10,tzinfo=common.ZONE).timestamp())
        reply=__import__('subprocess').CompletedProcess([],0,
              json.dumps({'total_count':0,'incomplete_results':True,'items':[]}),'')
        with patch.object(inputs.subprocess,'run',return_value=reply):
            with self.assertRaises(RuntimeError):
                inputs.collect_prs(self.folder,start,end)

    def test_inflight_or_complete_period_does_not_start_another_model(self):
        folder=common.run_folder('plugin-model-sync','2026-11-05')
        folder.mkdir(parents=True)
        for state in ({'status':'running','phase':'model_running'},
                      {'status':'complete','phase':'complete'}):
            common.save(folder/'state.json',state)
            with patch.object(run,'check_config',return_value={'enabled':True,'native_id':'plugin'}), \
                 patch.object(run,'native_paused',return_value=True), \
                 patch.object(run,'period',return_value='2026-11-05'), \
                 patch.object(run,'execute_model') as model:
                run.run('plugin-model-sync')
                model.assert_not_called()


if __name__=='__main__':
    unittest.main()
