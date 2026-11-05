"""No live GitHub requests: exercise dispatch safety and permission boundaries."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_qualification as dispatch

SHA = 'a' * 40
RUN = {'id': 42, 'head_sha': SHA, 'head_branch': dispatch.BRANCH,
       'status': 'queued', 'conclusion': None, 'html_url': 'https://untrusted.invalid/ignore'}


class API:
    def __init__(self, runs=None, errors=None, settings=None, ref=SHA):
        self.runs = list(runs if runs is not None else [[], [RUN]])
        self.errors = list(errors or [])
        self.settings = list(settings or [])
        self.ref = ref
        self.calls = []

    def __call__(self, method, endpoint, payload=None, timeout=15):
        self.calls.append((method, endpoint, payload, timeout))
        if method == 'GET' and endpoint == dispatch.REF:
            return {'object': {'type': 'commit', 'sha': self.ref}}
        if method == 'GET' and endpoint.startswith(dispatch.RUNS + '?'):
            value = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
            return {'workflow_runs': value}
        if endpoint == dispatch.PERMISSIONS:
            if method == 'GET':
                value = self.settings.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
            return None
        if method == 'POST':
            error = self.errors.pop(0) if self.errors else None
            if error:
                raise dispatch.DispatchError(error)
            return None
        raise AssertionError('Unexpected mock API request')


class Clock:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


class DispatchQualificationTests(unittest.TestCase):
    def test_invalid_sha_and_changed_branch_never_dispatch(self):
        for sha, api in [('not-a-sha', API()), (SHA, API(ref='b' * 40))]:
            with self.subTest(sha=sha), self.assertRaises(dispatch.DispatchError):
                dispatch.dispatch_qualification(sha, api=api)
            self.assertFalse(any(call[0] == 'POST' for call in api.calls))

    def test_existing_completed_or_running_run_prevents_redundant_build(self):
        for status in ['completed', 'in_progress', 'queued']:
            api = API(runs=[[dict(RUN, status=status)]])
            value = dispatch.dispatch_qualification(SHA, enable_actions=True, api=api)
            self.assertTrue(value['existing_run'])
            self.assertFalse(value['dispatch_accepted'])
            self.assertEqual(value['status'], status)
            self.assertFalse(any(call[0] != 'GET' for call in api.calls))
            self.assertEqual(value['run_url'], 'https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42')

    def test_dispatch_observes_matching_run_and_keeps_queued_status(self):
        other = dict(RUN, id=100, head_sha='b' * 40)
        api = API(runs=[[other], [other], [other, RUN]])
        clock = Clock()
        value = dispatch.dispatch_qualification(SHA, api=api, clock=clock.now, sleep=clock.sleep)
        self.assertTrue(value['run_observed'])
        self.assertEqual(value['status'], 'queued')
        self.assertNotIn('started', value)
        posts = [c for c in api.calls if c[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], {'ref': 'ci/verify-latest', 'inputs': {'unsigned_device': True}})

    def test_accepted_dispatch_without_observed_run_fails_after_bounded_poll(self):
        api, clock = API(runs=[[]]), Clock()
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_qualification(SHA, api=api, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(caught.exception.code, 'accepted_run_not_observed')
        self.assertTrue(caught.exception.dispatch_accepted)
        self.assertEqual(clock.value, 30)
        self.assertEqual(sum(c[0] == 'POST' for c in api.calls), 1)

    def test_disabled_actions_is_not_mutated_without_explicit_option(self):
        api = API(runs=[[]], errors=['actions_disabled'])
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_qualification(SHA, api=api)
        self.assertEqual(caught.exception.code, 'actions_disabled')
        self.assertFalse(any(c[1] == dispatch.PERMISSIONS for c in api.calls))

    def test_explicit_enable_preserves_policy_and_retries_dispatch_once(self):
        settings = [{'enabled': False, 'allowed_actions': 'selected'},
                    {'enabled': True, 'allowed_actions': 'selected'}]
        api = API(runs=[[], [], [RUN]], errors=['actions_disabled', None], settings=settings)
        result = dispatch.dispatch_qualification(SHA, enable_actions=True, api=api)
        self.assertTrue(result['run_observed'])
        writes = [c for c in api.calls if c[0] == 'PUT']
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], {'enabled': True, 'allowed_actions': 'selected'})
        self.assertEqual(sum(c[0] == 'POST' for c in api.calls), 2)

    def test_repository_administration_permission_failure_is_closed(self):
        api = API(runs=[[]], errors=['actions_disabled'], settings=[dispatch.DispatchError('forbidden')])
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_qualification(SHA, enable_actions=True, api=api)
        self.assertEqual(caught.exception.code, 'repository_administration_permission_required')
        self.assertFalse(caught.exception.dispatch_accepted)
        self.assertEqual(sum(c[0] == 'POST' for c in api.calls), 1)
        self.assertFalse(any(c[0] == 'PUT' for c in api.calls))

    def test_settings_policy_change_fails_closed(self):
        api = API(runs=[[]], errors=['actions_disabled'], settings=[
            {'enabled': False, 'allowed_actions': 'selected'}, {'enabled': True, 'allowed_actions': 'all'}])
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_qualification(SHA, enable_actions=True, api=api)
        self.assertEqual(caught.exception.code, 'actions_enable_not_verified')
        self.assertEqual(sum(c[0] == 'POST' for c in api.calls), 1)

    def test_cli_error_payload_does_not_expose_raw_auth_diagnostics(self):
        private = 'do-not-print-private-credential'
        completed = subprocess.CompletedProcess([], 1, private, 'gh: Resource not accessible by integration (HTTP 403) ' + private)
        with mock.patch.object(dispatch.subprocess, 'run', return_value=completed) as run:
            with self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.github_api('GET', dispatch.PERMISSIONS)
        self.assertEqual(str(caught.exception), 'forbidden')
        self.assertNotIn(private, str(caught.exception))
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.args[0][:5], ['gh', 'api', '--hostname', 'github.com', '--method'])
        output = io.StringIO()
        with mock.patch.object(dispatch, 'dispatch_qualification', side_effect=dispatch.DispatchError('repository_administration_permission_required')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(dispatch.main(['--expected-sha', SHA, '--enable-actions']), 1)
        value = json.loads(output.getvalue())
        self.assertFalse(value['run_observed'])
        self.assertNotIn(private, output.getvalue())
