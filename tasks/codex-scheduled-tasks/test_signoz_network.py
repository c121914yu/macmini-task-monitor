"""只验证 SigNoz 的采集网络边界；所有请求、等待、模型和通知均为模拟。"""
import contextlib
import io
import json
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
                self.assertEqual(payloads[0]['end'], self.hi-1)
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
        self.assertEqual(calls[:3], [(self.lo, self.hi),
                                    (self.lo, self.lo+1800000), (self.lo+1800000, self.hi)])
        self.assertTrue((self.folder/'signoz.json').exists())

    def test_invalid_api_shape_is_not_retried_or_split_as_data_volume(self):
        with patch.object(inputs.urllib.request, 'urlopen', return_value=io.BytesIO(b'{}')) as request:
            with self.assertRaises(KeyError):
                inputs.collect_signoz(self.folder, self.start, self.end)
        self.assertEqual(request.call_count, 1)
        self.sleep.assert_not_called()

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
