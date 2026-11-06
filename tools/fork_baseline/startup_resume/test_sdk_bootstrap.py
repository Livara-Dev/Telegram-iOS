# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Portable tests; no real Apple SDK or sudo commands run.
import copy
import contextlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import sdk_bootstrap as sdk


def known_tool(*unused):
    value = sdk.blank_tool()
    value.update(state='observed', xcrun_lookup_is_usr_bin=True,
                 selected_simctl_path_verified=True, root_owned_nonwritable=True,
                 stable_bytes_verified=True, file_kind='machO')
    return value


class BootstrapTests(unittest.TestCase):
    def execute(self, changes=None, fail=None, probe=known_tool, host=('Darwin', 'arm64', '27')):
        changes = changes or {}; calls = []
        def runner(arguments, timeout, directory, label):
            calls.append((arguments, timeout, label))
            if label == fail: raise sdk.Rejected('commandTimedOut')
            values = {'version': (0, sdk.VERSION.encode()), 'sdk': (0, b'27.1\n'),
                      'before': (1, b'components pending'), 'initialize': (0, b'components ready'),
                      'after': (0, b'')}
            return changes.get(label, values[label])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve() / 'report.json'
            result = sdk.initialize(output, runner=runner, probe=probe, host=host)
            self.assertEqual(json.loads(output.read_bytes()), result)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        return result, calls

    def test_normal_supported_initialization_and_readiness_are_explicit(self):
        result, calls = self.execute()
        self.assertTrue(result['success']); self.assertEqual(result['stage'], 'complete')
        self.assertEqual(result['before_exit_code'], 1)
        self.assertEqual(result['after_exit_code'], 0)
        self.assertEqual([entry[2] for entry in calls], ['version', 'sdk', 'before', 'initialize', 'after'])
        self.assertEqual(calls[3][:2], (['/usr/bin/sudo', '-n', sdk.XCODEBUILD, '-runFirstLaunch'], 300))
        self.assertFalse(result['application_recompiled']); self.assertFalse(result['physical_device_verified'])
        self.assertFalse(any('simctl' in entry[0] for entry in calls))

    def test_already_ready_status_does_not_claim_missing_components(self):
        result, _ = self.execute({'before': (0, b'')})
        self.assertTrue(result['success']); self.assertEqual(result['before_exit_code'], 0)

    def test_before_error_is_recorded_without_guessing_its_cause(self):
        result, _ = self.execute({'before': (69, b'private error text')})
        self.assertTrue(result['success']); self.assertEqual(result['before_exit_code'], 69)
        self.assertNotIn('private error text', json.dumps(result))

    def test_initialization_failure_never_becomes_readiness(self):
        result, calls = self.execute({'initialize': (69, b'synthetic/private/token')})
        self.assertFalse(result['success']); self.assertEqual(result['failure'], 'commandFailed')
        self.assertTrue(result['initialization_completed']); self.assertEqual(result['initialization_exit_code'], 69)
        self.assertFalse(result['after_completed']); self.assertEqual(len(calls), 4)
        self.assertNotIn('synthetic/private/token', json.dumps(result))

    def test_after_check_must_return_zero(self):
        result, _ = self.execute({'after': (1, b'still not ready')})
        self.assertFalse(result['success']); self.assertEqual(result['failure'], 'notReady')

    def test_timeout_preserves_unknown_outcome_and_stops_commands(self):
        result, calls = self.execute(fail='initialize')
        self.assertFalse(result['success']); self.assertEqual(result['failure'], 'commandTimedOut')
        self.assertTrue(result['initialization_attempted'])
        self.assertFalse(result['initialization_completed']); self.assertIsNone(result['initialization_exit_code'])
        self.assertEqual(len(calls), 4)

    def test_host_and_both_exact_versions_reject_before_setup(self):
        result, calls = self.execute(host=('Linux', 'x86_64', '7'))
        self.assertEqual(result['failure'], 'host'); self.assertEqual(calls, [])
        for change in ({'version': (0, b'Xcode 27.2')}, {'sdk': (0, b'27.0')}):
            result, calls = self.execute(change)
            self.assertEqual(result['failure'], 'version')
            self.assertFalse(result['initialization_attempted'])

    def test_tool_metadata_unavailable_does_not_fabricate_identity_or_negate_actual_readiness(self):
        result, _ = self.execute(probe=lambda *args: sdk.blank_tool())
        self.assertTrue(result['success'])
        self.assertEqual(result['tool_after']['state'], 'unavailable')
        self.assertFalse(result['tool_after']['stable_bytes_verified'])

    def test_rejected_tool_reason_never_negates_actual_initialization_and_after_zero(self):
        def rejected_tool(*unused):
            return {**sdk.blank_tool(), 'state': 'rejected', 'rejection_reason': 'owner'}
        result, _ = self.execute(probe=rejected_tool)
        self.assertTrue(result['success'])
        self.assertEqual(result['initialization_exit_code'], 0)
        self.assertEqual(result['after_exit_code'], 0)
        self.assertEqual(result['tool_before']['rejection_reason'], 'owner')
        self.assertEqual(result['tool_after']['rejection_reason'], 'owner')

    def test_closed_schema_rejects_canaries_forged_success_and_numeric_bools(self):
        result, _ = self.execute()
        variants = [{**result, 'secret': 'synthetic/private/token'}, {**result, 'after_exit_code': 1},
                    {**result, 'schema': True}, {**result, 'after_exit_code': 0.0},
                    {**result, 'initialization_attempted': False}]
        for value in variants:
            with self.subTest(value=list(value)), self.assertRaises(sdk.Rejected): sdk.validate_report(value)
        bad = copy.deepcopy(result); bad['tool_after']['private_path'] = '/synthetic/private'
        with self.assertRaises(sdk.Rejected): sdk.validate_report(bad)

    def test_tool_claims_require_exact_typed_metadata(self):
        result, _ = self.execute()
        for key in ('root_owned_nonwritable', 'selected_simctl_path_verified', 'stable_bytes_verified'):
            bad = copy.deepcopy(result); bad['tool_after'][key] = False
            with self.subTest(key=key), self.assertRaises(sdk.Rejected): sdk.validate_report(bad)
        bad = copy.deepcopy(result); bad['tool_after']['wrapper_mentions_first_launch'] = True
        with self.assertRaises(sdk.Rejected): sdk.validate_report(bad)

    def test_tool_reason_is_closed_typed_and_consistent_with_state(self):
        result, _ = self.execute()
        self.assertEqual(result['schema'], 2)
        legacy = copy.deepcopy(result); legacy['schema'] = 1
        with self.assertRaises(sdk.Rejected): sdk.validate_report(legacy)
        for reason in ('synthetic/private/token', False, 1, {}, [], 'unknown'):
            bad = copy.deepcopy(result)
            bad['tool_after'].update(state='rejected', rejection_reason=reason)
            with self.subTest(reason=reason), self.assertRaises(sdk.Rejected): sdk.validate_report(bad)
        for state, reason in (('observed', 'owner'), ('unavailable', 'read'), ('rejected', None)):
            bad = copy.deepcopy(result)
            bad['tool_after'].update(state=state, rejection_reason=reason)
            with self.subTest(state=state), self.assertRaises(sdk.Rejected): sdk.validate_report(bad)
        for reason in sdk.TOOL_REJECTION_REASONS:
            value = copy.deepcopy(result)
            value['tool_after'] = {**sdk.blank_tool(), 'state': 'rejected', 'rejection_reason': reason}
            sdk.validate_report(value)

    def test_sdk_commands_receive_only_the_minimal_environment(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic-private', 'TELEGRAM_API_HASH': 'REDACTED',
                                     'ACTIONS_RUNTIME_TOKEN': 'synthetic-private', 'DYLD_INSERT_LIBRARIES': 'synthetic-private'}):
            environment = sdk.clean_environment()
            self.assertLessEqual(set(environment), {'HOME', 'PATH', 'TMPDIR', 'DEVELOPER_DIR'})
            self.assertNotIn('synthetic-private', environment.values())

    def test_file_backed_command_waits_for_parent_not_daemon_pipe_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve(); pid_file = directory / 'owned-grandchild.pid'
            script = ('import subprocess,sys;from pathlib import Path;'
                      'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
                      'Path(sys.argv[1]).write_text(str(p.pid));print("parent-completed",flush=True)')
            try:
                code, output = sdk.private_command([sys.executable, '-c', script, str(pid_file)], 5, directory, 'held-log-fd')
                self.assertEqual(code, 0); self.assertIn(b'parent-completed', output)
            finally:
                if pid_file.exists():
                    try: os.kill(int(pid_file.read_text()), signal.SIGTERM)
                    except ProcessLookupError: pass

    def test_private_output_is_not_followed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve(); log = directory / 'existing.private-log'
            log.write_text('preserve')
            with self.assertRaises(FileExistsError):
                sdk.private_command([sys.executable, '-c', 'print(1)'], 5, directory, 'existing')
            self.assertEqual(log.read_text(), 'preserve')

    def test_output_is_monitored_while_child_is_still_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve(); children = []
            import subprocess
            original = subprocess.Popen
            def launch(*args, **kwargs):
                child = original(*args, **kwargs); children.append(child); return child
            script = 'import os,time;os.write(1,b"X"*(1024*1024+4096));time.sleep(60)'
            with patch.object(sdk.subprocess, 'Popen', side_effect=launch), self.assertRaises(sdk.Rejected) as caught:
                sdk.private_command([sys.executable, '-c', script], 5, directory, 'output-limit')
            self.assertEqual(caught.exception.code, 'outputLimit')
            self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].poll())

    def test_parent_timeout_remains_failure_and_retires_only_that_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(sdk.Rejected) as caught:
                sdk.private_command([sys.executable, '-c', 'import time;time.sleep(60)'], 0.1,
                                    Path(temporary).resolve(), 'timeout')
            self.assertEqual(caught.exception.code, 'commandTimedOut')


class ToolInspectionTests(unittest.TestCase):
    PRIVATE = b'SYNTHETIC_PRIVATE_CANARY'
    MACHO = b'\xcf\xfa\xed\xfe' + PRIVATE

    def metadata(self, **changes):
        fields = dict(st_dev=1, st_ino=2, st_uid=0, st_mode=stat.S_IFREG | 0o755,
                      st_nlink=1, st_size=len(self.MACHO), st_mtime_ns=3, st_ctime_ns=4)
        return SimpleNamespace(**{**fields, **changes})

    def inspect(self, *, metadata=None, launcher='/usr/bin/xcrun', runner=None, data=None,
                lstats=None, fstats=None, resolve_error=None, open_error=None, read_error=None,
                close_error=None):
        before = metadata or self.metadata()
        runner = runner or (lambda *unused: (0, (str(sdk.SIMCTL) + '\n').encode()))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sdk.shutil, 'which', return_value=launcher))
            stack.enter_context(patch.object(Path, 'resolve', side_effect=resolve_error)
                                if resolve_error else patch.object(Path, 'resolve', lambda value, **unused: value))
            stack.enter_context(patch.object(Path, 'lstat', side_effect=lstats or [before, before]))
            stack.enter_context(patch.object(sdk.os, 'open', side_effect=open_error, return_value=42))
            stack.enter_context(patch.object(sdk.os, 'fstat', side_effect=fstats or [before, before]))
            stack.enter_context(patch.object(sdk.os, 'read', side_effect=read_error,
                                            return_value=self.MACHO if data is None else data))
            close = stack.enter_context(patch.object(sdk.os, 'close', side_effect=close_error))
            value = sdk.inspect_tool(runner, Path('/synthetic/private'), 'tool')
        self.assertNotIn(self.PRIVATE.decode(), json.dumps(value))
        self.assertNotIn('/synthetic/private', json.dumps(value))
        self.assertNotIn(str(sdk.SIMCTL), json.dumps(value))
        return value, close

    def rejected(self, reason, **options):
        value, _ = self.inspect(**options)
        self.assertEqual(value['state'], 'rejected')
        self.assertEqual(value['rejection_reason'], reason)
        report = sdk.blank(); report['tool_before'] = value
        sdk.validate_report(report)

    def test_successful_macho_and_shell_observations_have_no_rejection_reason(self):
        value, close = self.inspect()
        self.assertEqual(value['state'], 'observed')
        self.assertEqual(value['file_kind'], 'machO')
        self.assertIsNone(value['rejection_reason']); close.assert_called_once_with(42)
        script = b'#!/bin/bash\n-runFirstLaunch\n' + self.PRIVATE
        value, _ = self.inspect(metadata=self.metadata(st_size=len(script)), data=script)
        self.assertEqual(value['file_kind'], 'shellWrapper')
        self.assertTrue(value['wrapper_mentions_first_launch']); self.assertIsNone(value['rejection_reason'])

    def test_lookup_resolution_failure_is_fixed_reason_and_advisory_false_is_preserved(self):
        self.rejected('lookupMismatch', resolve_error=OSError(self.PRIVATE.decode()))
        for launcher in (None, '/synthetic/private/xcrun'):
            value, _ = self.inspect(launcher=launcher)
            self.assertEqual(value['state'], 'observed')
            self.assertFalse(value['xcrun_lookup_is_usr_bin']); self.assertIsNone(value['rejection_reason'])

    def test_nonzero_missing_or_timed_out_lookup_command_is_closed(self):
        self.rejected('commandUnavailable', runner=lambda *unused: (69, self.PRIVATE))
        def unavailable(*unused): raise OSError(self.PRIVATE.decode())
        def timed_out(*unused): raise sdk.Rejected('commandTimedOut')
        self.rejected('commandUnavailable', runner=unavailable)
        self.rejected('commandUnavailable', runner=timed_out)

    def test_selected_path_and_output_bounds_are_independent_guards(self):
        for raw in (b'/synthetic/private/simctl\n', b'\xff'):
            self.rejected('pathMismatch', runner=lambda *unused: (0, raw))
        for raw in (b'', b'x' * 4097):
            self.rejected('size', runner=lambda *unused: (0, raw))

    def test_file_type_owner_mode_links_and_size_keep_existing_trust_guards(self):
        variants = [('type', dict(st_mode=stat.S_IFLNK | 0o755)),
                    ('type', dict(st_mode=stat.S_IFDIR | 0o755)), ('owner', dict(st_uid=501)),
                    ('mode', dict(st_mode=stat.S_IFREG | 0o775)),
                    ('mode', dict(st_mode=stat.S_IFREG | 0o757)), ('links', dict(st_nlink=2)),
                    ('size', dict(st_size=0)), ('size', dict(st_size=32 * 1024**2 + 1))]
        for reason, changes in variants:
            with self.subTest(reason=reason, changes=changes): self.rejected(reason, metadata=self.metadata(**changes))

    def test_read_and_open_failures_never_publish_exception_text(self):
        self.rejected('read', lstats=[OSError(self.PRIVATE.decode())])
        self.rejected('read', open_error=OSError(self.PRIVATE.decode()))
        self.rejected('read', read_error=OSError(self.PRIVATE.decode()))
        self.rejected('read', close_error=OSError(self.PRIVATE.decode()))
        self.rejected('read', data=b'')

    def test_opened_final_descriptor_and_final_path_changes_are_unstable(self):
        before, changed = self.metadata(), self.metadata(st_ino=99)
        self.rejected('unstable', fstats=[changed])
        self.rejected('unstable', fstats=[before, changed])
        self.rejected('unstable', lstats=[before, changed])

    def test_unknown_file_format_is_only_closed_format_rejection(self):
        data = b'not-an-Apple-tool-' + self.PRIVATE
        self.rejected('format', metadata=self.metadata(st_size=len(data)), data=data)


if __name__ == '__main__':
    unittest.main()
