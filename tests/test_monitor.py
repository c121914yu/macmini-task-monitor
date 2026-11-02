"""只用临时账本和无业务副作用的子进程验证记录边界。"""

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_suite = tempfile.TemporaryDirectory(prefix='mini-monitor-test-')
os.environ['TASK_MONITOR_DATA'] = _suite.name

import collector
import config
import install
import server
import store
from http.server import ThreadingHTTPServer


class MonitorTests(unittest.TestCase):
    def setUp(self):
        with store.connect() as db:
            db.executescript('DROP TABLE IF EXISTS runs;DROP TABLE IF EXISTS tasks;DROP TABLE IF EXISTS metadata;')
        store.initialize()

    def runner(self, code, task_id='test', **kwargs):
        return subprocess.run([sys.executable, str(ROOT / 'runner.py'), '--task-id', task_id, '--', sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=15, **kwargs)

    def latest(self, task_id='test'):
        return store.list_runs({'task_id': task_id})['items'][0]

    def test_exit_code_and_unicode_and_redaction(self):
        result = self.runner('import sys;print("运行完成 中文");print("Authorization: Bearer fake-test-value-123456789",file=sys.stderr);sys.exit(7)')
        self.assertEqual(result.returncode, 7)
        self.assertIn('fake-test-value', result.stderr)  # 原业务输出保留；仅记录副本脱敏。
        row = store.read_run(self.latest()['id'])
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['exit_code'], 7)
        self.assertGreaterEqual(row['duration'], 0)
        self.assertIn('运行完成 中文', row['log_tail'])
        self.assertNotIn('fake-test-value', row['log_tail'])

    def test_worker_result_is_not_overwritten_by_idle(self):
        result = self.runner('print(\'{"event":"worker_finished","pr":123,"code":0,"status":"processed"}\');print(\'{"event":"worker_idle"}\')')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.latest()['status'], 'success')
        self.assertIn('1 个 PR', self.latest()['summary'])

    def test_business_failure_is_visible_even_when_worker_exits_zero(self):
        self.runner('print(\'{"event":"worker_finished","pr":123,"code":1,"status":"error"}\');print(\'{"event":"worker_idle"}\')')
        self.assertEqual(self.latest()['status'], 'failed')
        self.assertEqual(self.latest()['exit_code'], 0)

    def test_idle_is_skipped(self):
        self.runner('print(\'{"event":"worker_idle"}\')')
        self.assertEqual(self.latest()['status'], 'skipped')

    def test_empty_scan_is_skipped_even_with_later_output(self):
        result = self.runner('print(\'{"event":"scan","scan_id":9,"queued":[]}\');print("x"*30000)', task_id='pr-scan')
        self.assertEqual(result.returncode, 0)
        row = self.latest('pr-scan')
        self.assertEqual(row['status'], 'skipped')
        self.assertIn('未发现待执行任务', row['summary'])

    def test_scan_with_tasks_is_successful(self):
        result = self.runner('print(\'{"event":"scan","scan_id":10,"queued":[123]}\')', task_id='pr-scan')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.latest('pr-scan')['status'], 'success')

    def test_empty_scan_failure_is_not_skipped(self):
        result = self.runner('import sys;print(\'{"event":"scan","scan_id":11,"queued":[]}\');sys.exit(7)', task_id='pr-scan')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.latest('pr-scan')['status'], 'failed')

    def test_scan_missing_queue_is_not_inferred_empty(self):
        for event in [{'event': 'scan', 'scan_id': 12}, {'event': 'scan', 'scan_id': 13, 'queued': None}]:
            self.runner('print(' + repr(json.dumps(event)) + ')', task_id='pr-scan')
            row = self.latest('pr-scan')
            self.assertEqual(row['status'], 'success')
            self.assertIn('队列未知', row['summary'])

    def test_existing_empty_scans_are_normalized_without_losing_evidence(self):
        backup = config.DATA / 'backups/monitor-before-empty-scan-v1.sqlite3'
        if backup.exists():
            backup.unlink()  # 仅清理本测试进程临时目录里上一测例的备份。
        examples = [
            ('empty', 'execution', 'success', 0, {'event': 'scan', 'queued': []}),
            ('has-tasks', 'execution', 'success', 0, {'event': 'scan', 'queued': [123]}),
            ('failed', 'execution', 'failed', 1, {'event': 'scan', 'queued': []}),
            ('unknown', 'execution', 'success', 0, {'event': 'scan'}),
            ('history', 'history', 'success', None, {'event': 'scan', 'queued': []})
        ]
        for run_id, kind, status, code, event in examples:
            store.insert_run({'id': run_id, 'external_key': run_id, 'task_id': 'pr-scan', 'kind': kind,
                              'status': status, 'exit_code': code, 'created_at': time.time(), 'started_at': 1000,
                              'finished_at': 1010, 'summary': 'original', 'log_tail': json.dumps(event)})
        self.assertEqual(collector.normalize_empty_scans(), 1)
        self.assertEqual(collector.normalize_empty_scans(), 0)
        self.assertEqual(store.list_runs({})['total'], 5)
        row = store.read_run('empty')
        self.assertEqual(row['status'], 'skipped')
        self.assertEqual(row['duration'], 10)
        self.assertEqual(json.loads(row['log_tail'])['queued'], [])
        for run_id, _, status, _, _ in examples[1:]:
            self.assertEqual(store.read_run(run_id)['status'], status)
        import sqlite3
        connection = sqlite3.connect('file:' + str(backup) + '?mode=ro', uri=True)
        try:
            self.assertEqual(connection.execute("SELECT status FROM runs WHERE id='empty'").fetchone()[0], 'success')
        finally:
            connection.close()

    def test_late_scan_completion_is_normalized(self):
        store.insert_run({'id': 'late', 'external_key': 'late', 'task_id': 'pr-scan', 'kind': 'execution',
                          'status': 'running', 'created_at': time.time(), 'started_at': time.time()})
        self.assertEqual(collector.normalize_empty_scans(), 0)
        store.update_run('late', status='success', exit_code=0, log_tail='{"event":"scan","queued":[]}')
        self.assertEqual(collector.normalize_empty_scans(), 1)
        self.assertEqual(store.read_run('late')['status'], 'skipped')

    def test_imported_scan_uses_its_own_original_event(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'pr-poll.log').write_text('{"event":"scan","scan_id":55,"queued":[]}\n{"event":"scan","scan_id":56,"queued":[123]}\n')
            store.insert_run({'id': 'legacy-scan', 'external_key': 'scan:55', 'task_id': 'pr-scan', 'kind': 'history',
                              'status': 'success', 'created_at': time.time(), 'summary': '原扫描凭据'})
            with patch.object(collector, 'PR_ROOT', root):
                self.assertEqual(collector.normalize_empty_scans(), 1)
            row = store.read_run('legacy-scan')
            self.assertEqual(row['status'], 'skipped')
            self.assertIn('#55', row['summary'])
            self.assertIsNone(row['exit_code'])
            self.assertIsNone(row['duration'])

    def test_non_skipped_filter_applies_before_count_and_pagination(self):
        statuses = ['success', 'skipped', 'failed', 'running', 'interrupted', 'unknown']
        for index, status in enumerate(statuses):
            store.insert_run({'id': str(index), 'external_key': str(index), 'task_id': 'test', 'kind': 'execution',
                              'status': status, 'created_at': index})
        result = store.list_runs({'status': 'non_skipped', 'limit': 2, 'offset': 2})
        self.assertEqual(result['total'], 5)
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(all(row['status'] != 'skipped' for row in result['items']))
        self.assertEqual(store.list_runs({'status': 'skipped'})['total'], 1)
        self.assertEqual(store.list_runs({})['total'], 6)

    def test_database_failure_does_not_block_business(self):
        with tempfile.TemporaryDirectory() as temp:
            invalid = Path(temp) / 'file'
            invalid.write_text('not a directory')
            env = {**os.environ, 'TASK_MONITOR_DATA': str(invalid)}
            result = self.runner('print("business completed")', env=env)
            self.assertEqual(result.returncode, 0)
            self.assertIn('business completed', result.stdout)
            self.assertIn('原任务继续执行', result.stderr)

    def test_signal_forwarding(self):
        process = subprocess.Popen([sys.executable, str(ROOT / 'runner.py'), '--task-id', 'signal-test', '--', sys.executable, '-c',
                                    'import time;print("ready",flush=True);time.sleep(60)'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143)
            self.assertEqual(self.latest('signal-test')['status'], 'interrupted')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_launchd_bootstrap_retries_transient_removal(self):
        failed = subprocess.CompletedProcess([], 5, '', 'Bootstrap failed: 5: Input/output error')
        ready = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(install, 'launchctl', side_effect=[failed, ready]) as operation, patch.object(install.time, 'sleep'):
            install.bootstrap(Path('/tmp/fixture.plist'))
        self.assertEqual(operation.call_count, 2)

    def test_history_dedup_and_missing_duration_and_resolution(self):
        data = {'task_id': 'pr-report', 'external_key': 'test-receipt', 'created_at': 1000, 'finished_at': 1000,
                'status': 'unknown', 'summary': '等待确认', 'details': {}}
        collector.import_record(data)
        collector.import_record({**data, 'status': 'success', 'summary': '已发送'})
        result = store.list_runs({})
        self.assertEqual(result['total'], 1)
        self.assertIsNone(result['items'][0]['duration'])
        self.assertEqual(result['items'][0]['status'], 'success')

    def test_receipt_covered_by_execution_is_not_duplicated(self):
        store.insert_run({'id': 'real', 'task_id': 'pr-report', 'external_key': 'real', 'kind': 'execution',
                          'status': 'success', 'created_at': 1000, 'started_at': 1000, 'finished_at': 1020})
        self.assertFalse(collector.needs_receipt('pr-report', 1010))
        self.assertTrue(collector.needs_receipt('pr-report', 1030))

    def test_orphan_finish_time_remains_unknown(self):
        store.insert_run({'id': 'orphan', 'task_id': 'test', 'kind': 'execution', 'external_key': 'orphan',
                          'status': 'running', 'pid': 99999999, 'created_at': time.time()-100, 'started_at': time.time()-100})
        collector.recover_orphans()
        row = store.read_run('orphan')
        self.assertEqual(row['status'], 'interrupted')
        self.assertIsNone(row['finished_at'])
        self.assertIsNone(row['duration'])

    def test_metaso_parser_and_idempotence(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'memory.md').write_text('- 运行时间: 2026-10-31 10:03:55 (Asia/Shanghai)\n- 结果: 脚本退出码 0；余额 109950 点，未触发告警。\n')
            with patch.object(collector, 'METASO_ROOT', folder):
                data = collector.metaso_data()
                collector.metaso_data()
            self.assertEqual(data['balance'], 109950)
            self.assertEqual(store.list_runs({})['total'], 1)

    def test_hourly_calendar_crosses_midnight(self):
        now = dt.datetime(2026, 11, 2, 23, 59, tzinfo=config.ZONE).timestamp()
        task = collector.schedule_info({'id':'release-docs'}, {'StartCalendarInterval':{'Minute':0}}, now)
        self.assertEqual(task['next_at'], dt.datetime(2026, 11, 3, 0, 0, tzinfo=config.ZONE).timestamp())
        self.assertEqual(task['previous_at'], dt.datetime(2026, 11, 2, 23, 0, tzinfo=config.ZONE).timestamp())
        self.assertEqual(task['schedule']['text'], '每小时整点')
        self.assertFalse(task['next_estimated'])

    def test_document_outcome_preserves_idle_and_errors(self):
        from outcomes import docs_result
        self.assertEqual(docs_result({'event':'docs_idle'})[0], 'skipped')
        self.assertEqual(docs_result({'event':'docs_retry_limit'})[0], 'failed')
        self.assertIsNone(docs_result({'event':'docs_progress'}))
        result = self.runner('import json;print(json.dumps({"event":"docs_idle"}))', task_id='release-docs')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.latest('release-docs')['status'], 'skipped')

    def test_schedule_uses_shanghai_and_estimates_only_intervals(self):
        now = dt.datetime(2026, 10, 31, 11, 0, tzinfo=config.ZONE).timestamp()
        task = collector.schedule_info({'id': 'test', 'source': 'launchd'}, {'StartCalendarInterval': {'Hour': 1, 'Minute': 0}}, now)
        expected = dt.datetime(2026, 11, 1, 1, 0, tzinfo=config.ZONE).timestamp()
        self.assertEqual(task['next_at'], expected)
        self.assertFalse(task['next_estimated'])
        task = collector.schedule_info({'id': 'test', 'source': 'launchd', 'last_run': {'started_at': now-60}}, {'StartInterval': 300}, now)
        self.assertEqual(task['next_at'], now+240)
        self.assertTrue(task['next_estimated'])

    def test_weekly_schedule_and_automation_changes(self):
        rules = collector.automation_calendar('RRULE:FREQ=WEEKLY;BYHOUR=9;BYMINUTE=30;BYDAY=MO')
        now = dt.datetime(2026, 10, 31, 11, 0, tzinfo=config.ZONE).timestamp()
        task = collector.schedule_info({'id': 'test', 'source': 'codex-script'}, {'StartCalendarInterval': rules}, now)
        self.assertEqual(task['schedule']['text'], '每周一 09:30')
        self.assertEqual(task['next_at'], dt.datetime(2026, 11, 2, 9, 30, tzinfo=config.ZONE).timestamp())
        self.assertEqual(task['previous_at'], dt.datetime(2026, 10, 26, 9, 30, tzinfo=config.ZONE).timestamp())
        self.assertIsNone(collector.automation_calendar('RRULE:FREQ=MONTHLY;BYHOUR=10'))
        self.assertEqual(collector.automation_calendar('RRULE:FREQ=WEEKLY;BYHOUR=11;BYMINUTE=5;BYDAY=SU,MO,TU,WE,TH,FR,SA'), [{'Hour': 11, 'Minute': 5}])

    def test_log_storage_is_bounded(self):
        self.runner('print("x"*120000)')
        self.assertLessEqual(len(store.read_run(self.latest()['id'])['log_tail']), 24000)

    def test_http_read_only_host_and_pagination_validation(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        http.allowed_hosts = {'127.0.0.1'}
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(http.server_port)
        try:
            with urlopen(base + '/api/runs?limit=10') as response:
                self.assertEqual(json.load(response)['total'], 0)
            with urlopen(base + '/api/runs?status=non_skipped') as response:
                self.assertEqual(json.load(response)['total'], 0)
            for path, status in [('/api/runs?limit=-1', 400), ('/api/runs?offset=invalid', 400), ('/../../etc/passwd', 404)]:
                with self.assertRaises(HTTPError) as context:
                    urlopen(base + path)
                self.assertEqual(context.exception.code, status)
                context.exception.close()
            with self.assertRaises(HTTPError) as context:
                urlopen(Request(base + '/api/overview', headers={'Host': 'untrusted.example'}))
            self.assertEqual(context.exception.code, 403)
            context.exception.close()
            with self.assertRaises(HTTPError) as context:
                urlopen(Request(base + '/api/overview', method='POST', data=b''))
            self.assertEqual(context.exception.code, 405)
            context.exception.close()
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
