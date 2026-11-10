"""只验证 SigNoz 的采集网络边界；所有请求、等待、模型和通知均为模拟。"""
import contextlib
import datetime as dt
import io
import json
import re
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import common
import inputs
import run


class SigNozNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root/'runs/signoz-report/2026-11-08'
        self.folder.mkdir(parents=True)
        common.save(self.root/'private.json', {'signoz-report': {
            'base_url': 'https://example.invalid', 'api_key': 'private-fixture-key'}})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for module in (inputs, common, run):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.sleep = self.stack.enter_context(patch.object(inputs.time, 'sleep'))
        self.start, self.end = common.window('signoz-report', '2026-11-08', 0)
        self.lo = int(self.start.timestamp()*1000)
        self.hi = self.lo+3600000
        self.window_hi = self.lo+inputs.SIGNOZ_QUERY_WINDOW_MS
        self.sql = 'SELECT count() AS total '+inputs.signoz_clause(self.lo, self.hi)

    def response(self):
        return io.BytesIO(json.dumps({'data': {'data': {'results': [{'data': [[0]]}]}}}).encode())

    def test_eof_retries_exact_request_and_keeps_diagnostics_private(self):
        error = urllib.error.URLError(ssl.SSLEOFError('fixture transport closed'))
        with patch.object(inputs.urllib.request, 'urlopen', side_effect=[error, self.response()]) as request:
            self.assertEqual(inputs.signoz_query(self.sql, self.lo, self.hi)['data'], [[0]])
        self.assertEqual(request.call_count, 2)
        self.assertIs(request.call_args_list[0].args[0], request.call_args_list[1].args[0])
        self.sleep.assert_called_once_with(10)
        event = json.loads(self.output.getvalue())
        self.assertEqual(event['category'], 'tls_eof')
        self.assertEqual(event['attempts'], 1)
        self.assertNotIn('private-fixture-key', self.output.getvalue())
        self.assertNotIn('SELECT', self.output.getvalue())
        self.assertNotIn('example.invalid', self.output.getvalue())

    def test_outage_exhausts_four_attempts_without_shrinking_window_or_publishing_data(self):
        for error in (urllib.error.URLError('connection reset'), TimeoutError('fixture timeout')):
            self.sleep.reset_mock()
            with self.subTest(error=type(error).__name__), patch.object(
                    inputs.urllib.request, 'urlopen', side_effect=error) as request:
                with self.assertRaises(inputs.SigNozRequestError) as caught:
                    inputs.collect_signoz(self.folder, self.start, self.end)
                self.assertEqual(request.call_count, 4)
                payloads = [json.loads(call.args[0].data) for call in request.call_args_list]
                self.assertTrue(all(payload == payloads[0] for payload in payloads))
                self.assertEqual(payloads[0]['start'], self.lo)
                self.assertEqual(payloads[0]['end'], self.window_hi-1)
                self.assertEqual(caught.exception.details['attempts'], 4)
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [10, 30, 60])
                self.assertFalse((self.folder/'signoz.json').exists())

    def test_certificate_and_authentication_errors_stop_at_one_request(self):
        certificate = ssl.SSLCertVerificationError(1, 'certificate verify failed')
        errors = [(urllib.error.URLError(certificate), 'certificate_verification'),
                  (urllib.error.URLError('CERTIFICATE_VERIFY_FAILED'), 'certificate_verification')]
        errors += [(urllib.error.HTTPError('https://example.invalid', code, 'denied', {}, None),
                    'authentication' if code in (401, 403) else 'http_error') for code in (401, 403, 404)]
        for error, category in errors:
            self.sleep.reset_mock()
            with self.subTest(category=category), patch.object(
                    inputs.urllib.request, 'urlopen', side_effect=error) as request:
                with self.assertRaises(inputs.SigNozRequestError) as caught:
                    inputs.collect_signoz(self.folder, self.start, self.end)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(caught.exception.details['category'], category)
                self.assertFalse(caught.exception.details['retryable'])
                self.sleep.assert_not_called()

    def test_temporary_http_errors_can_recover(self):
        for status in (429, 502, 503, 504):
            error = urllib.error.HTTPError('https://example.invalid', status, 'temporary', {}, None)
            with self.subTest(status=status), patch.object(inputs.urllib.request, 'urlopen',
                    side_effect=[error, self.response()]) as request:
                self.assertEqual(inputs.signoz_query(self.sql, self.lo, self.hi)['data'], [[0]])
                self.assertEqual(request.call_count, 2)

    def test_wrapped_clickhouse_memory_error_is_a_window_limit_without_network_retry(self):
        body = json.dumps({'status': 'error', 'error': {
            'type': 'internal', 'code': 'internal',
            'message': 'code: 241, message: Query memory limit exceeded: maximum: 3.60 GiB',
            'errors': [], 'suggestions': []}}).encode()
        error = urllib.error.HTTPError('https://example.invalid', 500, 'internal', {}, io.BytesIO(body))
        with patch.object(inputs.urllib.request, 'urlopen', side_effect=error) as request:
            with self.assertRaises(inputs.SigNozQueryWindowError):
                inputs.signoz_query(self.sql, self.lo, self.hi)
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()
        self.assertNotIn('maximum', self.output.getvalue())

    def test_unrelated_500_remains_a_server_failure(self):
        errors = [urllib.error.HTTPError('https://example.invalid', 500, 'internal', {},
                   io.BytesIO(b'{"error":{"message":"code: 241, unrelated internal error"}}')) for _ in range(4)]
        with patch.object(inputs.urllib.request, 'urlopen', side_effect=errors) as request:
            with self.assertRaises(inputs.SigNozRequestError) as caught:
                inputs.signoz_query(self.sql, self.lo, self.hi)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(caught.exception.details['category'], 'server_error')

    def test_minimum_window_limit_has_visible_summary_and_does_not_start_model(self):
        with patch.object(run, 'check_config', return_value={'enabled': True}), \
                patch.object(run, 'native_paused', return_value=True), \
                patch.object(run, 'period', return_value='2026-11-08'), \
                patch.dict(run.COLLECTORS, {'signoz-report': lambda *args: (
                    _ for _ in ()).throw(inputs.SigNozQueryWindowError('fixture limit'))}), \
                patch.object(run, 'execute_model') as model:
            self.assertEqual(run.run('signoz-report'), 1)
        state = common.read(self.folder/'state.json')
        self.assertEqual(state['collection_error']['category'], 'query_window_limit')
        self.assertFalse(state['collection_error']['retryable'])
        self.assertIn('最小时间窗口', state['summary'])
        model.assert_not_called()

    def test_explicit_resource_limit_keeps_window_split_behavior(self):
        error = urllib.error.HTTPError('https://example.invalid', 500, 'query resource limit', {},
                                       io.BytesIO(b'{"message":"MEMORY_LIMIT_EXCEEDED"}'))
        with patch.object(inputs.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(inputs.SigNozQueryWindowError):
                inputs.signoz_query(self.sql, self.lo, self.hi)
        calls = []
        def query(sql, lo, hi):
            calls.append((lo, hi))
            if len(calls) == 1:
                raise inputs.SigNozQueryWindowError('fixture truncation')
            return {'data': [[0]]}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            inputs.collect_signoz(self.folder, self.start, self.end)
        self.assertEqual(calls[:3], [(self.lo, self.window_hi),
                                    (self.lo, (self.lo+self.window_hi)//2),
                                    ((self.lo+self.window_hi)//2, self.window_hi)])
        self.assertTrue((self.folder/'signoz.json').exists())

    def test_trace_correlation_resource_limit_splits_without_losing_matches(self):
        tid = 'a'*32
        for day in ['current', 'previous']:
            for kind in inputs.FILTERS:
                rows = [[self.lo*1000000, 'event', tid]] if day=='current' and kind=='llm' else []
                common.save(self.folder/(day+'-'+kind+'.json'),
                            {'complete':True,'total':len(rows),'rows':rows})
        calls = []
        route = [tid, '/api/example', '/api/example']
        def query(sql, lo, hi):
            calls.append((lo, hi))
            if len(calls) == 1:
                raise inputs.SigNozQueryWindowError('fixture memory limit')
            return {'data': [route] if len(calls)==2 else []}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            inputs.collect_signoz(self.folder, self.start, self.end)
        data = common.read(self.folder/'signoz.json')
        self.assertEqual(data['http_correlations'], [route])
        self.assertEqual(data.get('route_query_failures', 0), 0)
        self.assertEqual(calls[:3], [(self.lo,self.window_hi),
                                    (self.lo,(self.lo+self.window_hi)//2),
                                    ((self.lo+self.window_hi)//2,self.window_hi)])

    def test_trace_transport_gap_is_recorded_without_recursive_splitting(self):
        tid = 'b'*32
        for day in ['current', 'previous']:
            for kind in inputs.FILTERS:
                rows = [[self.lo*1000000, 'event', tid]] if day=='current' and kind=='llm' else []
                common.save(self.folder/(day+'-'+kind+'.json'),
                            {'complete':True,'total':len(rows),'rows':rows})
        details = {'category':'tls_eof','retryable':True,'attempts':4,
                   'error_type':'URLError','reason_type':'SSLEOFError'}
        calls = []
        def query(sql, lo, hi):
            calls.append((lo,hi))
            if len(calls)==1:
                raise inputs.SigNozRequestError(details)
            return {'data':[]}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            inputs.collect_signoz(self.folder, self.start, self.end)
        data = common.read(self.folder/'signoz.json')
        self.assertEqual(len(calls), 24*60//5)
        self.assertEqual(data['route_query_failures'], 1)
        self.assertEqual(data['route_query_failure_details'][0]['category'], 'tls_eof')
        self.assertEqual(data['route_query_failure_details'][0]['start_ms'], self.lo)
        self.assertNotIn('private-fixture-key', json.dumps(data))

    def test_small_log_windows_cover_both_days_and_tail_without_duplicate_boundaries(self):
        end = self.start + dt.timedelta(minutes=11, milliseconds=17)
        spans = []
        def query(sql, lo, hi):
            if sql.startswith('SELECT count()'):
                spans.append((lo, hi))
                return {'data': [[2]]}
            return {'data': [[lo*1000000, f'{lo}-first', ''],
                             [(hi-1)*1000000, f'{lo}-last', '']]}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            inputs.collect_signoz(self.folder, self.start, end)
        snapshot = common.read(self.folder/'signoz.json')
        day_spans = {
            'current': (self.lo, int(end.timestamp()*1000)),
            'previous': (self.lo-86400000, self.lo)
        }
        for day, (left, right) in day_spans.items():
            for kind in inputs.FILTERS:
                item = snapshot[day][kind]
                self.assertTrue(item['complete'])
                self.assertEqual(item['total'], len(item['rows']))
                self.assertEqual(len({row[1] for row in item['rows']}), item['total'])
                self.assertTrue(all(left*1000000 <= row[0] < right*1000000 for row in item['rows']))
            windows = [span for span in spans if left <= span[0] < right]
            unique = sorted(set(windows))
            self.assertEqual(len(windows), len(unique)*len(inputs.FILTERS))
            self.assertEqual(unique[0][0], left)
            self.assertEqual(unique[-1][1], right)
            self.assertTrue(all(0 < hi-lo <= 300000 for lo, hi in unique))
            self.assertTrue(all(a[1] == b[0] for a,b in zip(unique, unique[1:])))

    def test_trace_id_batches_each_cover_small_windows_including_the_tail(self):
        end = self.start + dt.timedelta(minutes=11, milliseconds=17)
        end_ms = int(end.timestamp()*1000)
        tids = [f'{n:032x}' for n in range(1,42)]
        for day in ['current', 'previous']:
            for kind in inputs.FILTERS:
                rows = [[self.lo*1000000, f'event-{i}', tid] for i,tid in enumerate(tids)] if day=='current' and kind=='llm' else []
                common.save(self.folder/(day+'-'+kind+'.json'),
                            {'complete':True,'total':len(rows),'rows':rows})
        spans = {}
        route = [tids[-1], '/api/example', '/api/example']
        def query(sql, lo, hi):
            batch = tuple(re.findall(r"'([0-9a-f]{32})'", sql))
            spans.setdefault(batch, []).append((lo,hi))
            return {'data': [route] if tids[-1] in batch and hi==end_ms else []}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            inputs.collect_signoz(self.folder, self.start, end)
        self.assertEqual(sorted(map(len, spans)), [1,40])
        self.assertEqual({tid for batch in spans for tid in batch}, set(tids))
        for windows in spans.values():
            self.assertEqual(windows[0][0], self.lo)
            self.assertEqual(windows[-1][1], end_ms)
            self.assertTrue(all(0 < hi-lo <= 300000 for lo,hi in windows))
            self.assertTrue(all(a[1] == b[0] for a,b in zip(windows, windows[1:])))
        snapshot = common.read(self.folder/'signoz.json')
        self.assertEqual(snapshot['http_correlations'], [route])
        self.assertEqual(snapshot.get('route_query_failures', 0), 0)

    def test_count_mismatch_in_minimum_window_never_publishes_incomplete_data(self):
        end = self.start + dt.timedelta(minutes=1)
        calls = []
        def query(sql,lo,hi):
            calls.append((lo,hi))
            return {'data': [[1]]} if sql.startswith('SELECT count()') else {'data': []}
        with patch.object(inputs, 'signoz_query', side_effect=query):
            with self.assertRaises(inputs.SigNozQueryWindowError):
                inputs.collect_signoz(self.folder, self.start, end)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.folder/'signoz.json').exists())
        self.assertFalse((self.folder/'current-zod.json').exists())

    def test_invalid_api_shape_is_not_retried_or_split_as_data_volume(self):
        with patch.object(inputs.urllib.request, 'urlopen', return_value=io.BytesIO(b'{}')) as request:
            with self.assertRaises(KeyError):
                inputs.collect_signoz(self.folder, self.start, self.end)
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

    def test_delivered_partial_report_reconciles_without_model_after_contract_marker_is_verified(self):
        common.save(self.folder/'state.json', {'status':'failed','phase':'model_completed',
                    'inputs_complete':True,'turn_id':'existing-turn'})
        common.save(self.folder/'signoz.json', {'current': {
            k:{'complete':True,'total':0,'rows':[]} for k in inputs.FILTERS}})
        common.save(self.folder/'notification.json', {'status':'sent'})
        result = {'status':'partial','summary':'报告已发送，HTTP归因有缺口'}
        common.save(self.folder/'result.json',result)
        with patch.object(run, 'check_config', return_value={'enabled':True}), \
                patch.object(run,'native_paused',return_value=True), \
                patch.object(run,'period',return_value='2026-11-08'), \
                patch.object(run,'execute_model') as model:
            self.assertEqual(run.run('signoz-report'),1)
            state=common.read(self.folder/'state.json')
            self.assertEqual(state['result_error']['category'],'result_contract')
            self.assertIn('log_analysis_complete',state['summary'])
            result['log_analysis_complete']=True
            common.save(self.folder/'result.json',result)
            self.assertEqual(run.run('signoz-report'),0)
            state=common.read(self.folder/'state.json')
            self.assertEqual(state['status'],'partial')
            self.assertNotIn('result_error',state)
            model.assert_not_called()

    def test_runner_records_safe_failure_without_starting_model(self):
        details = {'category': 'tls_eof', 'retryable': True, 'attempts': 4,
                   'error_type': 'URLError', 'reason_type': 'SSLEOFError',
                   'start_ms': self.lo, 'end_ms': self.hi}
        with patch.object(run, 'check_config', return_value={'enabled': True}), \
                patch.object(run, 'native_paused', return_value=True), \
                patch.object(run, 'period', return_value='2026-11-08'), \
                patch.dict(run.COLLECTORS, {'signoz-report': lambda *args: (
                    _ for _ in ()).throw(inputs.SigNozRequestError(details))}), \
                patch.object(run, 'execute_model') as model:
            self.assertEqual(run.run('signoz-report'), 1)
        state = common.read(self.folder/'state.json')
        self.assertEqual(state['collection_error'], details)
        self.assertEqual(state['phase'], 'collecting')
        self.assertIn('TLS', state['summary'])
        self.assertIn('3', state['summary'])
        self.assertNotIn('private-fixture-key', json.dumps(state))
        self.assertFalse(state.get('inputs_complete', False))
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
