"""瞬时网络错误的读取重试；模拟子进程和等待，不调用 GitHub、模型或通知。"""
import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch
import release_docs as docs


class NetworkRetryTests(unittest.TestCase):
    def test_paginated_get_recovers_from_eof_without_restarting_scan(self):
        pages = [[{'number': 42}]]
        replies = [subprocess.CompletedProcess([], 1, '', 'Get "https://api.github.com/": EOF'),
                   subprocess.CompletedProcess([], 0, json.dumps(pages), '')]
        output = io.StringIO()
        with patch.object(docs.subprocess, 'run', side_effect=replies) as run, \
                patch.object(docs.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            self.assertEqual(docs.gh(['api', 'repos/labring/FastGPT/commits/sha/pulls?per_page=100',
                                      '--paginate', '--slurp']), pages)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(10)
        event = json.loads(output.getvalue())
        self.assertEqual(event['reason'], 'network_eof')
        self.assertNotIn('https://', output.getvalue())

    def test_transient_failures_exhaust_exactly_three_retries(self):
        for error in ('EOF', 'HTTP 502: Bad Gateway', 'HTTP 429: Too Many Requests',
                      'LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443'):
            with self.subTest(error=error), \
                    patch.object(docs.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], 1, '', error)) as run, \
                    patch.object(docs.time, 'sleep') as sleep, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    docs.gh(['api', 'repos/labring/FastGPT/releases'])
                self.assertEqual(run.call_count, 4)
                self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 30, 60])

    def test_auth_not_found_and_unknown_errors_fail_without_wait(self):
        for error in ('HTTP 401: Bad credentials', 'HTTP 403: Forbidden',
                      'HTTP 404: Not Found', 'HTTP 422: Validation Failed',
                      'unknown error', 'HTTP 404: EOF'):
            with self.subTest(error=error), \
                    patch.object(docs.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], 1, '', error)) as run, \
                    patch.object(docs.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    docs.gh(['api', 'repos/labring/FastGPT/releases'])
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()

    def test_write_requests_never_retry_even_if_result_is_unknown(self):
        for args in (
                ['api', '--method', 'POST', 'repos/labring/FastGPT/pulls', '--input', 'request.json'],
                ['api', '--method=PATCH', 'repos/labring/FastGPT/pulls/42', '--input=request.json'],
                ['api', 'repos/labring/FastGPT/pulls', '-ftitle=value']):
            with self.subTest(args=args), \
                    patch.object(docs.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], 1, '', 'EOF')) as run, \
                    patch.object(docs.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    docs.gh(args)
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()

    def test_fetch_can_retry_but_push_and_local_merge_cannot(self):
        for operation, count in [('fetch', 2), ('ls-remote', 2), ('push', 1), ('merge', 1)]:
            replies = [subprocess.CompletedProcess([], 1, '', 'connection reset by peer'),
                       subprocess.CompletedProcess([], 0, 'ok\n', '')]
            with self.subTest(operation=operation), \
                    patch.object(docs.subprocess, 'run', side_effect=replies) as run, \
                    patch.object(docs.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                if count == 2:
                    self.assertEqual(docs.git('/fake', [operation, 'upstream']), 'ok')
                else:
                    with self.assertRaises(RuntimeError):
                        docs.git('/fake', [operation, 'upstream'])
                self.assertEqual(run.call_count, count)

    def test_timeout_retries_reads_and_preserves_final_failure(self):
        with patch.object(docs.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired(['gh'], 180)) as run, \
                patch.object(docs.time, 'sleep') as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired):
                docs.gh(['api', 'repos/labring/FastGPT/releases'])
            self.assertEqual(run.call_count, 4)
            self.assertEqual(sleep.call_count, 3)

    def test_failed_discovery_never_advances_cursor_or_calls_ai(self):
        # pull_requests 必须获取全部提交关联 PR 后才返回；中途失败不能推进扫描游标。
        with patch.object(docs, 'git', side_effect=['old', 'first\nsecond']), \
                patch.object(docs, 'gh', side_effect=[[[{'number': 1, 'merged_at': 'now',
                    'base': {'ref': 'main'}, 'title': 'one', 'html_url': 'https://example.invalid/1'}]],
                    RuntimeError('EOF')]), \
                patch.object(docs, 'save') as save, patch.object(docs, 'generate') as generate:
            with self.assertRaises(RuntimeError):
                docs.pull_requests('/fake', ('old', 'new'))
            save.assert_not_called()
            generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
