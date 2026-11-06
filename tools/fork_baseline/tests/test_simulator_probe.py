import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simulator_probe


class SimulatorProbeTests(unittest.TestCase):
    @staticmethod
    def empty_diagnostic(arguments, secrets, timeout=20):
        return {'exit_code': 0, 'status': 'completed', 'stderr': ''}, b'[]'

    def test_only_synthetic_host_is_launched_and_marker_is_read_before_and_after(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Synthetic.app'; app.mkdir()
            data = root / 'SyntheticData'; data.mkdir()
            marker = data / simulator_probe.MARKER
            marker.parent.mkdir(parents=True)
            marker.write_text('{"stage":"didFinish"}')
            identifier = '11111111-2222-3333-4444-555555555555'
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [
                {'name': 'BAZEL_TEST_iPhone 17_27.0', 'state': 'Booted', 'udid': identifier},
                {'name': 'Personal phone', 'state': 'Booted', 'udid': 'do-not-touch'}]}}
            replies = iter([json.dumps(devices).encode(), str(app).encode(), str(data).encode(), b'host pid=3'])
            def reply(args, **kwargs):
                value = next(replies)
                if 'launch' in args:
                    marker.write_text('{"stage":"sceneVisible"}')
                return SimpleNamespace(returncode=0, stdout=value)
            order = []
            def crash_snapshot(started_at):
                self.assertEqual(started_at, 1234.5)
                order.append('crash')
                return {'probe_only_not_test_result': True, 'state': 'available', 'reports': []}
            def observed_reply(args, **kwargs):
                if 'launch' in args:
                    order.append('standalone')
                return reply(args, **kwargs)
            with patch.object(simulator_probe.subprocess, 'run', side_effect=observed_reply) as run, patch.object(simulator_probe, 'capture_crashes', side_effect=crash_snapshot), patch.object(simulator_probe, 'diagnostic_command', side_effect=self.empty_diagnostic) as diagnostic:
                simulator_probe.capture(root, ['private-fixture-secret'], started_at=1234.5)
            calls = [c.args[0] for c in run.call_args_list]
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls[2], ['xcrun', 'simctl', 'get_app_container', identifier, simulator_probe.BUNDLE, 'data'])
            self.assertEqual(calls[-1], ['xcrun', 'simctl', 'launch', '--terminate-running-process', identifier, simulator_probe.BUNDLE])
            text = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
            for private_value in ('private-fixture-secret', 'Personal phone', str(root), identifier, 'pid=3'):
                self.assertNotIn(private_value, text)
            report = json.loads(text)
            self.assertTrue(report['probe_only_not_test_result'])
            self.assertEqual(report['marker_before_standalone_launch'], {'state': 'observed', 'stage': 'didFinish'})
            self.assertEqual(report['marker_after_standalone_launch'], {'state': 'observed', 'stage': 'sceneVisible'})
            self.assertEqual(report['commands']['installed_signature'], {'exit_code': 0})
            self.assertEqual(report['commands']['standalone_host_launch'], {'exit_code': 0})
            self.assertEqual(diagnostic.call_count, 2)
            self.assertEqual(diagnostic.call_args_list[0].kwargs['timeout'], 10)
            log_args = diagnostic.call_args_list[1].args[0]
            self.assertEqual(log_args, ['xcrun', 'simctl', 'spawn', identifier, 'log', 'show', '--style', 'json',
                '--last', '15m', '--info', '--debug', '--predicate', simulator_probe.BOOTSTRAP_PREDICATE])
            self.assertEqual(stat.S_IMODE((root / 'build/fork-evidence/projection-launch-probe.json').stat().st_mode), 0o600)
            self.assertEqual(order, ['crash', 'standalone'])
            self.assertEqual(report['crash_summary_before_standalone_launch'], {'probe_only_not_test_result': True, 'state': 'available', 'reports': []})

    def test_closed_marker_reader_rejects_unknown_schema_and_large_or_unsafe_files(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            marker = data / simulator_probe.MARKER
            marker.parent.mkdir(parents=True)
            self.assertEqual(simulator_probe.read_marker(data), {'state': 'absent'})
            for value in ({'stage': 'didFinish'}, {'stage': 'sceneVisible'}):
                marker.write_text(json.dumps(value))
                self.assertEqual(simulator_probe.read_marker(data), {'state': 'observed', **value})
            for value in ({'stage': 'private'}, {'stage': 'didFinish', 'private': 'secret'}, {}, [], {'stage': []}):
                marker.write_text(json.dumps(value))
                self.assertNotEqual(simulator_probe.read_marker(data)['state'], 'observed')
            marker.write_text('{"stage":"didFinish"}' + ' ' * 128)
            self.assertEqual(simulator_probe.read_marker(data), {'state': 'invalid'})
            marker.unlink()
            target = data / 'private-file'; target.write_text('{"stage":"didFinish"}')
            marker.symlink_to(target)
            self.assertEqual(simulator_probe.read_marker(data), {'state': 'unsafe'})
            marker.unlink()
            marker.mkdir()
            self.assertEqual(simulator_probe.read_marker(data), {'state': 'invalid'})
            self.assertEqual(simulator_probe.read_marker(None), {'state': 'unavailable'})

    def test_missing_xctest_marker_stays_distinct_from_standalone_visible_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Host.app'; app.mkdir()
            data = root / 'Data'; data.mkdir()
            marker = data / simulator_probe.MARKER
            marker.parent.mkdir(parents=True)
            identifier = '11111111-2222-3333-4444-555555555555'
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [
                {'name': 'BAZEL_TEST_iPhone 17_27.0', 'state': 'Booted', 'udid': identifier}]}}
            replies = iter([json.dumps(devices).encode(), str(app).encode(), str(data).encode(), b'pid=999'])
            def reply(args, **kwargs):
                value = next(replies)
                if 'launch' in args:
                    marker.write_text('{"stage":"sceneVisible"}')
                return SimpleNamespace(returncode=1 if args[0] == '/usr/bin/codesign' else 0, stdout=value)
            def diagnostic(arguments, secrets, timeout=20):
                code = 1 if arguments[0] == '/usr/bin/codesign' else 0
                return {'exit_code': code, 'status': 'commandFailed' if code else 'completed', 'stderr': 'injected seal invalid' if code else ''}, b'[]'
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply), patch.object(simulator_probe, 'diagnostic_command', side_effect=diagnostic):
                simulator_probe.capture(root, [])
            report = json.loads((root / 'build/fork-evidence/projection-launch-probe.json').read_text())
            self.assertEqual(report['marker_before_standalone_launch'], {'state': 'absent'})
            self.assertEqual(report['marker_after_standalone_launch'], {'state': 'observed', 'stage': 'sceneVisible'})
            self.assertTrue(report['probe_only_not_test_result'])
            self.assertEqual(report['commands']['installed_signature'], {'exit_code': 1})
            self.assertEqual(report['diagnostics']['installed_signature']['stderr'], 'injected seal invalid')

    def test_os_alias_above_approved_data_root_is_valid_but_root_link_is_unsafe(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            actual_parent = base / 'actual'; actual_parent.mkdir()
            data = actual_parent / 'Data'; data.mkdir()
            marker = data / simulator_probe.MARKER
            marker.parent.mkdir(parents=True)
            marker.write_text('{"stage":"sceneVisible"}')
            ancestor_alias = base / 'os-alias'
            ancestor_alias.symlink_to(actual_parent, target_is_directory=True)
            self.assertEqual(simulator_probe.read_marker(ancestor_alias / 'Data'), {'state': 'observed', 'stage': 'sceneVisible'})
            direct_root_link = base / 'direct-data-link'
            direct_root_link.symlink_to(data, target_is_directory=True)
            self.assertEqual(simulator_probe.read_marker(direct_root_link), {'state': 'unsafe'})

    def test_directory_symlink_inside_approved_data_root_is_unsafe(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            external = base / 'External'
            external_marker = external / 'Caches/AyuGramNativeTestHost/projection-lifecycle.json'
            external_marker.parent.mkdir(parents=True)
            external_marker.write_text('{"stage":"didFinish"}')
            data = base / 'Data'; data.mkdir()
            (data / 'Library').symlink_to(external, target_is_directory=True)
            self.assertEqual(simulator_probe.read_marker(data), {'state': 'unsafe'})

    def test_no_matching_test_device_never_launches_a_personal_simulator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [{'name': 'Personal phone', 'state': 'Booted', 'udid': '11111111-2222-3333-4444-555555555555'}]}}
            with patch.object(simulator_probe.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode())) as run:
                simulator_probe.capture(root, [])
            self.assertEqual(run.call_count, 1)
            report = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
            self.assertNotIn('Personal phone', report)
            self.assertNotIn('standalone_host_launch', report)

    def test_timeout_does_not_change_test_outcome_or_launch_another_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(simulator_probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('simctl', 10)) as run:
                simulator_probe.capture(root, [])
            self.assertEqual(run.call_count, 1)
            report = json.loads((root / 'build/fork-evidence/projection-launch-probe.json').read_text())
            self.assertEqual(report['commands']['devices'], {'state': 'unavailable'})

    def test_diagnostic_command_redacts_known_credentials_paths_and_tokens_and_sanitizes_child_environment(self):
        secret = 'synthetic-' + 'private-sentinel'
        token = 'gh' + 'p_' + 'A' * 32
        code = ('import os,sys;sys.stdout.write("transient stdout must not be retained");'
                'sys.stderr.write("ProjectionTestHost: " + sys.argv[1] + " " + sys.argv[2] + "\\n");'
                'sys.stderr.write("file /private/synthetic-secret-path/device.log\\n");'
                'sys.stderr.write("context_value=" + os.environ.get("CI_PROBE_SECRET", "absent"))')
        with patch.dict(os.environ, {'CI_PROBE_SECRET': secret}):
            report, stdout = simulator_probe.diagnostic_command([sys.executable, '-c', code, secret, token], [secret])
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['exit_code'], 0)
        self.assertEqual(stdout, b'transient stdout must not be retained')
        text = json.dumps(report)
        for value in (secret, token, '/private/synthetic-secret-path', stdout.decode()): self.assertNotIn(value, text)
        self.assertIn('context_value=absent', report['stderr'])
        self.assertIn('<REDACTED_CREDENTIAL>', report['stderr'])
        self.assertIn('<REDACTED_PATH>', report['stderr'])

    def test_diagnostic_command_timeout_kills_direct_child_and_rejects_unbounded_deadline(self):
        original = subprocess.Popen
        children = []
        def launched(*args, **kwargs):
            process = original(*args, **kwargs); children.append(process); return process
        started = time.monotonic()
        with patch.object(simulator_probe.subprocess, 'Popen', side_effect=launched):
            report, stdout = simulator_probe.diagnostic_command([sys.executable, '-c', 'import time;time.sleep(10)'], [], timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(report['status'], 'timedOut')
        self.assertTrue(report['timeout']); self.assertEqual(stdout, b'')
        self.assertIsNotNone(children[0].poll())
        with patch.object(simulator_probe.subprocess, 'Popen') as process:
            for timeout in (0, -1, 21, float('nan'), float('inf')):
                with self.assertRaises(ValueError): simulator_probe.diagnostic_command(['unused'], [], timeout=timeout)
            process.assert_not_called()

    def test_diagnostic_command_caps_combined_pipes_before_retention(self):
        report, stdout = simulator_probe.diagnostic_command([sys.executable, '-c',
            'import sys;sys.stdout.write("x"*40000);sys.stdout.flush();sys.stderr.write("y"*40000);sys.stderr.flush()'], [])
        self.assertEqual(report['status'], 'outputLimit')
        self.assertTrue(report['output_limit'])
        self.assertLessEqual(report['stdout_bytes'] + report['stderr_bytes'], 65536)
        self.assertLessEqual(len(json.dumps(report)), 20 * 1024)
        self.assertEqual(len(stdout), report['stdout_bytes'])

    def test_bootstrap_log_filters_again_before_retaining_only_redacted_selected_fields(self):
        secret = 'synthetic-' + 'bootstrap-secret'
        message = 'ProjectionTestHost failed at /private/synthetic/device.log ' + secret
        records = [
            {'processImagePath': '/usr/libexec/testmanagerd', 'eventMessage': message, 'messageType': 'Error',
             'timestamp': '2026-11-05 23:32:01.123456+0000', 'privateData': 'never-copy-extra-field'},
            {'process': 'runningboardd', 'eventMessage': 'another app private contents'},
            {'process': 'PersonalApp', 'eventMessage': 'ProjectionTestHost unrelated actor'},
            {'process': 'xctest', 'eventMessage': 'org.livara.ayugram.tests.native-security.projection bootstrap',
             'timestamp': 'private timestamp field'},
        ]
        report = simulator_probe.bootstrap_events(json.dumps(records).encode(), [secret])
        self.assertEqual(report['state'], 'parsed')
        self.assertEqual(len(report['events']), 2)
        self.assertEqual(report['events'][0]['timestamp'], '2026-11-05 23:32:01.123456+0000')
        self.assertNotIn('timestamp', report['events'][1])
        text = json.dumps(report)
        for value in (secret, '/private/synthetic', '/usr/libexec', 'never-copy-extra-field', 'another app', 'PersonalApp', 'private timestamp'):
            self.assertNotIn(value, text)
        self.assertIn('<REDACTED_CREDENTIAL>', text)

    def test_bootstrap_log_caps_encoded_evidence_and_never_retains_malformed_payload(self):
        records = [{'process': 'testmanagerd', 'eventMessage': 'ProjectionTestHost ' + '\u2060' * 500} for _ in range(30)]
        report = simulator_probe.bootstrap_events(json.dumps(records, ensure_ascii=False).encode(), [])
        self.assertLess(len(json.dumps(report).encode()), 46 * 1024)
        self.assertGreater(report.get('records_omitted', 0), 0)
        for data in (b'{"private":"do-not-retain"', b'private sentinel', b'[]' + b' ' * 65536):
            value = simulator_probe.bootstrap_events(data, [])
            self.assertEqual(value, {'state': 'invalidPayload', 'events': []})

    def test_capped_bootstrap_command_discards_raw_json_before_standalone_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Synthetic.app'; app.mkdir()
            data = root / 'Data'; data.mkdir()
            identifier = '11111111-2222-3333-4444-555555555555'
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [
                {'name': 'BAZEL_TEST_iPhone 17_27.0', 'state': 'Booted', 'udid': identifier}]}}
            replies = iter([json.dumps(devices).encode(), str(app).encode(), str(data).encode(), b''])
            order = []
            def reply(arguments, **kwargs):
                if 'launch' in arguments: order.append('standalone')
                return SimpleNamespace(returncode=0, stdout=next(replies))
            def diagnostic(arguments, secrets, timeout=20):
                order.append('signature' if arguments[0] == '/usr/bin/codesign' else 'bootstrap')
                return {'status': 'outputLimit', 'exit_code': None, 'stderr': '<REDACTED_CREDENTIAL>',
                    'stdout_bytes': 65536, 'stderr_bytes': 0}, b'[{"eventMessage":"unparsed-private-sentinel"'
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply), patch.object(simulator_probe, 'diagnostic_command', side_effect=diagnostic):
                simulator_probe.capture(root, [])
            path = root / 'build/fork-evidence/projection-launch-probe.json'
            text = path.read_text(); report = json.loads(text)
            self.assertNotIn('unparsed-private-sentinel', text)
            self.assertEqual(report['bootstrap_events_before_standalone_launch'], {'state': 'unavailable', 'reason': 'outputLimit', 'events': []})
            self.assertEqual(order, ['signature', 'bootstrap', 'standalone'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
