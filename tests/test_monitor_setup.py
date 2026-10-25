"""Offline tests for scheduler payloads, idempotency, and secret handling."""
import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import setup_render_monitor as monitor


class MonitorPayloadTests(unittest.TestCase):
    def test_default_schedule_is_exactly_every_fifteen_minutes(self):
        job = monitor.make_job()
        self.assertEqual(job['title'], 'HeXa Render monitor')
        self.assertEqual(job['url'], 'https://hexa-userbot.onrender.com/healthz')
        self.assertEqual(job['schedule'], {'timezone': 'UTC', 'expiresAt': 0,
            'hours': [-1], 'mdays': [-1], 'minutes': [0, 15, 30, 45], 'months': [-1], 'wdays': [-1]})
        self.assertIs(job['enabled'], True)
        self.assertIs(job['saveResponses'], False)
        self.assertEqual(job['requestMethod'], 0)
        self.assertEqual(job['requestTimeout'], 30)
        self.assertNotIn('notification', job)

    def test_ten_minute_margin_requires_explicit_selection(self):
        self.assertEqual(monitor.make_job(interval=10)['schedule']['minutes'], [0, 10, 20, 30, 40, 50])
        with self.assertRaises(monitor.SetupError):
            monitor.make_job(interval=5)

    def test_dry_run_has_no_key_prompt_or_network(self):
        output = io.StringIO()
        with patch('setup_render_monitor._api_key') as key, \
                patch('setup_render_monitor.api_request') as request, \
                contextlib.redirect_stdout(output):
            self.assertEqual(monitor.main(['--dry-run']), 0)
        key.assert_not_called()
        request.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {'job': monitor.make_job()})


class MonitorIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.job = monitor.make_job()
        self.key = 'private-test-api-key'

    def test_create_then_rerun_updates_same_job_without_touching_unrelated_jobs(self):
        stored = {91: {'jobId': 91, 'title': 'Unrelated', 'url': self.job['url']},
                  92: {'jobId': 92, 'title': self.job['title'], 'url': 'https://example.com/healthz'}}

        def service(key, method, path, payload=None):
            self.assertEqual(key, self.key)
            if (method, path) == ('GET', '/jobs'):
                return {'jobs': list(stored.values()), 'someFailed': False}
            if (method, path) == ('PUT', '/jobs'):
                stored[93] = {'jobId': 93, **payload['job']}
                return {'jobId': 93}
            if (method, path) == ('PATCH', '/jobs/93'):
                stored[93].update(payload['job'])
                return {}
            if (method, path) == ('GET', '/jobs/93'):
                return {'jobDetails': stored[93]}
            self.fail('Unexpected API operation')

        request = Mock(side_effect=service)
        unrelated = {key: dict(value) for key, value in stored.items()}
        self.assertEqual(monitor.ensure_job(self.job, self.key, request), 93)
        self.assertEqual(monitor.ensure_job(self.job, self.key, request), 93)
        mutations = [(call.args[1], call.args[2]) for call in request.call_args_list if call.args[1] != 'GET']
        self.assertEqual(mutations, [('PUT', '/jobs'), ('PATCH', '/jobs/93')])
        self.assertEqual({key: stored[key] for key in unrelated}, unrelated)

    def test_incomplete_listing_or_duplicate_matches_causes_no_mutation(self):
        matching = {'jobId': 7, 'title': self.job['title'], 'url': self.job['url']}
        for result in [{'jobs': [], 'someFailed': True}, {'jobs': [matching, dict(matching, jobId=8)]},
                       {'jobs': None}, {'jobs': ['not-a-job']}]:
            with self.subTest(result=result):
                request = Mock(return_value=result)
                with self.assertRaises(monitor.SetupError):
                    monitor.ensure_job(self.job, self.key, request)
                request.assert_called_once_with(self.key, 'GET', '/jobs')

    def test_ambiguous_create_response_is_not_retried(self):
        for result in [{}, {'jobId': '7'}, {'jobId': True}, {'jobId': -1}]:
            with self.subTest(result=result):
                request = Mock(side_effect=[{'jobs': []}, result])
                with self.assertRaisesRegex(monitor.SetupError, 'Run this helper again'):
                    monitor.ensure_job(self.job, self.key, request)
                self.assertEqual(request.call_count, 2)

    def test_network_error_during_create_is_not_retried(self):
        request = Mock(side_effect=[{'jobs': []}, monitor.SetupError('Request could not complete.')])
        with self.assertRaisesRegex(monitor.SetupError, 'Run this helper again') as raised:
            monitor.ensure_job(self.job, self.key, request)
        self.assertNotIn(self.key, str(raised.exception))
        self.assertEqual(request.call_count, 2)

    def test_saved_enabled_url_method_and_schedule_are_verified(self):
        changes = [{'enabled': False}, {'url': 'https://example.com/healthz'}, {'requestMethod': 1},
                   {'requestMethod': False}, {'schedule': dict(self.job['schedule'], minutes=[0])},
                   {'schedule': dict(self.job['schedule'], timezone='Europe/Berlin')}, {'schedule': None}]
        for change in changes:
            with self.subTest(change=change):
                request = Mock(side_effect=[{'jobs': []}, {'jobId': 7},
                                           {'jobDetails': dict(self.job, **change)}])
                with self.assertRaisesRegex(monitor.SetupError, 'could not be verified'):
                    monitor.ensure_job(self.job, self.key, request)


class MonitorRequestTests(unittest.TestCase):
    def test_credentials_only_go_to_fixed_api_origin_as_a_header(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"jobs": [], "someFailed": false}'
        opener = Mock()
        opener.open.return_value = response
        secret = 'private-test-api-key'
        with patch('setup_render_monitor.build_opener', return_value=opener) as build:
            monitor.api_request(secret, 'GET', '/jobs')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.cron-job.org/jobs')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + secret)
        self.assertNotIn(secret, request.full_url)
        self.assertIsNone(request.data)
        self.assertIsInstance(build.call_args.args[0], monitor._NoRedirects)
        self.assertIsNone(build.call_args.args[0].redirect_request(None, None, 302, '', {}, 'https://example.com'))
        for path in ['https://example.com/jobs', '//example.com/jobs', '/jobs/7?key=secret', '/jobs/../other']:
            with self.subTest(path=path), self.assertRaises(monitor.SetupError):
                monitor.api_request(secret, 'GET', path)
        self.assertEqual(opener.open.call_count, 1)

    def test_errors_do_not_echo_api_key_or_response_body(self):
        secret = 'private-test-api-key'
        sensitive_body = 'private-response-body'
        for error in [HTTPError(monitor.API_URL + '/jobs', 401, secret, {}, io.BytesIO(sensitive_body.encode())),
                      URLError(secret), ValueError(secret)]:
            with self.subTest(error=type(error).__name__), \
                    patch('setup_render_monitor.build_opener') as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(monitor.SetupError) as raised:
                    monitor.api_request(secret, 'GET', '/jobs')
                self.assertNotIn(secret, str(raised.exception))
                self.assertNotIn(sensitive_body, str(raised.exception))

    def test_main_never_prints_the_environment_key(self):
        output, errors = io.StringIO(), io.StringIO()
        secret = 'private-test-api-key'
        with patch.dict('os.environ', {'CRON_JOB_API_KEY': secret}), \
                patch('setup_render_monitor.api_request', side_effect=monitor.SetupError('API returned HTTP 401.')), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(monitor.main([]), 1)
        self.assertNotIn(secret, output.getvalue() + errors.getvalue())

    def test_invalid_json_response_does_not_echo_its_body(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'private-response-body'
        with patch('setup_render_monitor.build_opener') as build:
            build.return_value.open.return_value = response
            with self.assertRaises(monitor.SetupError) as raised:
                monitor.api_request('private-test-api-key', 'GET', '/jobs')
        self.assertNotIn('private-response-body', str(raised.exception))

    def test_success_prints_only_job_id_interval_and_url(self):
        output = io.StringIO()
        with patch('setup_render_monitor._api_key', return_value='private-test-api-key'), \
                patch('setup_render_monitor.ensure_job', return_value=7), \
                contextlib.redirect_stdout(output):
            self.assertEqual(monitor.main([]), 0)
        self.assertEqual(output.getvalue(), f'Job 7: every 15 minutes, GET {monitor.DEFAULT_URL}\n')

    def test_missing_key_does_not_prompt_in_a_headless_process(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch('setup_render_monitor.sys.stdin.isatty', return_value=False), \
                patch('setup_render_monitor.getpass.getpass') as prompt:
            with self.assertRaisesRegex(monitor.SetupError, 'Set CRON_JOB_API_KEY privately'):
                monitor._api_key()
        prompt.assert_not_called()

    def test_local_prompt_is_hidden_and_returns_only_to_the_caller(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch('setup_render_monitor.sys.stdin.isatty', return_value=True), \
                patch('setup_render_monitor.getpass.getpass', return_value='private-test-api-key') as prompt:
            self.assertEqual(monitor._api_key(), 'private-test-api-key')
        prompt.assert_called_once()

    def test_getpass_echo_fallback_is_refused(self):
        import getpass
        import warnings

        def unsafe_prompt(*args):
            warnings.warn('Cannot hide typing', getpass.GetPassWarning)
            self.fail('An echoed input fallback must not run')

        with patch.dict('os.environ', {}, clear=True), \
                patch('setup_render_monitor.sys.stdin.isatty', return_value=True), \
                patch('setup_render_monitor.getpass.getpass', side_effect=unsafe_prompt):
            with self.assertRaisesRegex(monitor.SetupError, 'cannot hide input'):
                monitor._api_key()


if __name__ == '__main__':
    unittest.main()
