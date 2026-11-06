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

    @staticmethod
    def diagnostic_inventory(payload, probe=None):
        def execute(arguments, secrets, timeout=20):
            if arguments == ['xcrun', 'simctl', 'list', 'devices', 'available', '--json']:
                if timeout != 10: raise AssertionError('Inventory budget must remain ten seconds')
                data = json.dumps(payload).encode()
                return {'exit_code': 0, 'status': 'completed', 'timeout': False, 'output_limit': False,
                        'stdout_bytes': len(data), 'stderr_bytes': 0, 'stderr': ''}, data
            return (probe or SimulatorProbeTests.empty_diagnostic)(arguments, secrets, timeout)
        return execute

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
            replies = iter([str(app).encode(), str(data).encode(), b'host pid=3'])
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
            with patch.object(simulator_probe.subprocess, 'run', side_effect=observed_reply) as run, patch.object(simulator_probe, 'capture_crashes', side_effect=crash_snapshot), patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(devices)) as diagnostic:
                simulator_probe.capture(root, ['private-fixture-secret'], started_at=1234.5)
            calls = [c.args[0] for c in run.call_args_list]
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1], ['xcrun', 'simctl', 'get_app_container', identifier, simulator_probe.BUNDLE, 'data'])
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
            self.assertEqual(diagnostic.call_count, 3)
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
            replies = iter([str(app).encode(), str(data).encode(), b'pid=999'])
            def reply(args, **kwargs):
                value = next(replies)
                if 'launch' in args:
                    marker.write_text('{"stage":"sceneVisible"}')
                return SimpleNamespace(returncode=1 if args[0] == '/usr/bin/codesign' else 0, stdout=value)
            def diagnostic(arguments, secrets, timeout=20):
                code = 1 if arguments[0] == '/usr/bin/codesign' else 0
                return {'exit_code': code, 'status': 'commandFailed' if code else 'completed', 'stderr': 'injected seal invalid' if code else ''}, b'[]'
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply), patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(devices, diagnostic)):
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
            with patch.object(simulator_probe.subprocess, 'run') as run, patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(payload)) as diagnostic:
                simulator_probe.capture(root, [])
            run.assert_not_called()
            self.assertEqual(diagnostic.call_count, 1)
            report = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
            self.assertNotIn('Personal phone', report)
            self.assertNotIn('standalone_host_launch', report)

    def test_inventory_failure_retains_real_bounded_redacted_status_without_launching_any_app(self):
        original_command, original_popen = simulator_probe.diagnostic_command, subprocess.Popen
        secret = 'inventory-private-sentinel'
        for status in ('timedOut', 'commandFailed', 'unavailable', 'outputLimit'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def launch(arguments, **kwargs):
                    self.assertEqual(arguments, ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'])
                    if status == 'unavailable': raise OSError('private launch exception ' + secret)
                    code = ('import sys,time;sys.stderr.write(' + repr(secret + ' /private/inventory-secret-path/file.log')
                            + ');sys.stderr.flush();')
                    if status == 'timedOut': code += 'time.sleep(10)'
                    elif status == 'commandFailed': code += 'sys.stdout.write("unparsed-private-inventory");sys.exit(7)'
                    else: code += 'sys.stdout.write("unparsed-private-inventory" * 10000)'
                    return original_popen([sys.executable, '-c', code], **kwargs)
                def bounded_inventory(arguments, secrets, timeout=20):
                    self.assertEqual(timeout, 10)
                    return original_command(arguments, secrets, timeout=0.1 if status == 'timedOut' else 2)
                with patch.object(simulator_probe.subprocess, 'Popen', side_effect=launch), patch.object(simulator_probe.subprocess, 'run') as run, patch.object(simulator_probe, 'diagnostic_command', side_effect=bounded_inventory) as diagnostic:
                    simulator_probe.capture(root, [secret])
                run.assert_not_called(); self.assertEqual(diagnostic.call_count, 1)
                path = root / 'build/fork-evidence/projection-launch-probe.json'
                text = path.read_text(); report = json.loads(text)
                inventory = report['commands']['devices']
                self.assertEqual(inventory['status'], status)
                self.assertEqual(inventory['timeout'], status == 'timedOut')
                self.assertEqual(inventory['output_limit'], status == 'outputLimit')
                self.assertEqual(inventory['exit_code'], 7 if status == 'commandFailed' else None)
                for private in (secret, '/private/inventory-secret-path', 'unparsed-private-inventory', 'private launch exception'):
                    self.assertNotIn(private, text)
                self.assertNotIn('diagnostics', report)
                self.assertNotIn('standalone_host_launch', report['commands'])
                self.assertTrue(report['probe_only_not_test_result'])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_invalid_or_ambiguous_inventory_never_runs_device_probes_or_retains_raw_payload(self):
        owned = {'name': simulator_probe.DEVICE_NAME, 'state': 'Booted',
                 'udid': '11111111-2222-3333-4444-555555555555'}
        payloads = [
            'private inventory string', [], {'devices': 'private inventory value'},
            {'devices': {simulator_probe.RUNTIME: 'private malformed devices'}},
            {'devices': {simulator_probe.RUNTIME: [owned, owned]}},
            {'devices': {simulator_probe.RUNTIME: [dict(owned, state='Shutdown')]}},
            {'devices': {simulator_probe.RUNTIME: [dict(owned, udid=None)]}},
            {'devices': {'unexpected-' + simulator_probe.RUNTIME: [owned]}},
            {'devices': {simulator_probe.RUNTIME + '-other': [owned]}},
            {'devices': {simulator_probe.RUNTIME: [dict(owned, name='Personal phone')]}},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(simulator_probe.subprocess, 'run') as run, patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(payload)) as diagnostic:
                    simulator_probe.capture(root, [])
                run.assert_not_called(); self.assertEqual(diagnostic.call_count, 1)
                text = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
                report = json.loads(text)
                self.assertEqual(report['commands']['devices']['status'], 'completed')
                self.assertNotIn('diagnostics', report)
                for private in ('private inventory', 'private malformed', 'Personal phone', owned['udid']):
                    self.assertNotIn(private, text)

    def test_failed_or_invalid_app_container_does_not_suppress_selected_bootstrap_logs(self):
        owned = {'name': simulator_probe.DEVICE_NAME, 'state': 'Booted',
                 'udid': '11111111-2222-3333-4444-555555555555'}
        devices = {'devices': {simulator_probe.RUNTIME: [owned]}}
        secret = 'bootstrap-private-sentinel'
        events = [
            {'process': 'testmanagerd', 'eventMessage': 'ProjectionTestHost install failed ' + secret + ' /private/container-secret-path/app'},
            {'process': 'unapproved', 'eventMessage': 'ProjectionTestHost private dropped event'},
        ]
        for failure in ('commandFailed', 'absent', 'relative', 'symlink'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app = root / 'Synthetic.app'; app.mkdir()
                link = root / 'Linked.app'; link.symlink_to(app, target_is_directory=True)
                container = {'commandFailed': app, 'absent': root / 'Absent.app', 'relative': 'relative.app', 'symlink': link}[failure]
                order = []
                def reply(arguments, **kwargs):
                    order.append('container')
                    self.assertEqual(arguments, ['xcrun', 'simctl', 'get_app_container', owned['udid'], simulator_probe.BUNDLE, 'app'])
                    return SimpleNamespace(returncode=1 if failure == 'commandFailed' else 0, stdout=str(container).encode())
                def bootstrap(arguments, secrets, timeout=20):
                    order.append('bootstrap')
                    self.assertEqual(arguments, ['xcrun', 'simctl', 'spawn', owned['udid'], 'log', 'show',
                        '--style', 'json', '--last', '15m', '--info', '--debug', '--predicate', simulator_probe.BOOTSTRAP_PREDICATE])
                    return self.empty_diagnostic(arguments, secrets, timeout)[0], json.dumps(events).encode()
                with patch.object(simulator_probe.subprocess, 'run', side_effect=reply) as run, patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(devices, bootstrap)) as diagnostic:
                    simulator_probe.capture(root, [secret])
                self.assertEqual(order, ['bootstrap', 'container'])
                self.assertEqual(run.call_count, 1); self.assertEqual(diagnostic.call_count, 2)
                text = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
                report = json.loads(text)
                self.assertEqual(report['commands']['installed_host']['exit_code'], 1 if failure == 'commandFailed' else 0)
                selected = report['bootstrap_events_before_standalone_launch']
                self.assertEqual(selected['state'], 'parsed'); self.assertEqual(len(selected['events']), 1)
                self.assertIn('ProjectionTestHost install failed', selected['events'][0]['message'])
                self.assertIn('<REDACTED_CREDENTIAL>', selected['events'][0]['message'])
                self.assertNotIn('standalone_host_launch', report['commands'])
                self.assertNotIn('installed_signature', report['diagnostics'])
                for private in (secret, '/private/container-secret-path', 'private dropped event', str(root), owned['udid']):
                    self.assertNotIn(private, text)

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
            replies = iter([str(app).encode(), str(data).encode(), b''])
            order = []
            def reply(arguments, **kwargs):
                if 'launch' in arguments: order.append('standalone')
                return SimpleNamespace(returncode=0, stdout=next(replies))
            def diagnostic(arguments, secrets, timeout=20):
                order.append('signature' if arguments[0] == '/usr/bin/codesign' else 'bootstrap')
                return {'status': 'outputLimit', 'exit_code': None, 'stderr': '<REDACTED_CREDENTIAL>',
                    'stdout_bytes': 65536, 'stderr_bytes': 0}, b'[{"eventMessage":"unparsed-private-sentinel"'
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply), patch.object(simulator_probe, 'diagnostic_command', side_effect=self.diagnostic_inventory(devices, diagnostic)):
                simulator_probe.capture(root, [])
            path = root / 'build/fork-evidence/projection-launch-probe.json'
            text = path.read_text(); report = json.loads(text)
            self.assertNotIn('unparsed-private-sentinel', text)
            self.assertEqual(report['bootstrap_events_before_standalone_launch'], {'state': 'unavailable', 'reason': 'outputLimit', 'events': []})
            self.assertEqual(order, ['bootstrap', 'signature', 'standalone'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class ReplaySimulatorProbeTests(unittest.TestCase):
    PROFILE = 'replay-iphone15pro'
    NAME = 'AYU_REPLAY_iPhone 15 Pro_27.0'
    TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
    OWNED = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
    OTHER = '11111111-2222-3333-4444-555555555555'

    def owned_row(self, **changes):
        return {'name': self.NAME, 'deviceTypeIdentifier': self.TYPE, 'state': 'Booted',
                'udid': self.OWNED, **changes}

    @staticmethod
    def completed(data=b'[]'):
        return ({'exit_code': 0, 'status': 'completed', 'timeout': False, 'output_limit': False,
                 'stdout_bytes': len(data), 'stderr_bytes': 0, 'stderr': ''}, data)

    def read_report(self, root):
        path = root / 'build/fork-evidence/projection-launch-probe.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        text = path.read_text()
        for private in (self.OWNED, self.OWNED.lower(), self.OTHER, 'replay-private-sentinel', str(root)):
            self.assertNotIn(private, text)
        return json.loads(text)

    def assert_replay_metadata(self, report, verified=False, profile=PROFILE):
        self.assertTrue(report['probe_only_not_test_result'])
        self.assertEqual(report['profile'], profile)
        self.assertEqual(report['device_type'], self.TYPE)
        self.assertEqual(report['runtime'], simulator_probe.RUNTIME)
        self.assertEqual(report['device_identity'], {'state': 'verified' if verified else 'unknown'})

    def test_replay_probes_exact_owned_device_after_unique_full_identity_proof(self):
        self.assertEqual(simulator_probe.REPLAY_PROFILE, self.PROFILE)
        self.assertEqual(simulator_probe.REPLAY_DEVICE_NAME, self.NAME)
        self.assertEqual(simulator_probe.REPLAY_DEVICE_TYPE, self.TYPE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Synthetic.app'; app.mkdir()
            data = root / 'Data'; data.mkdir()
            marker = data / simulator_probe.MARKER
            marker.parent.mkdir(parents=True)
            marker.write_text('{"stage":"sceneVisible"}')
            payload = {'devices': {simulator_probe.RUNTIME: [
                self.owned_row(udid=self.OTHER), self.owned_row(udid=self.OWNED.lower())],
                'com.apple.CoreSimulator.SimRuntime.iOS-26-5': [
                    {'name': 'Personal private device', 'state': 'Booted', 'udid': '99999999-8888-7777-6666-555555555555'}]}}
            order = []
            def diagnostic(arguments, secrets, timeout=20):
                for value in ('replay-private-sentinel', self.OWNED, self.OWNED.lower()):
                    self.assertIn(value, secrets)
                if arguments == ['xcrun', 'simctl', 'list', 'devices', self.NAME, '--json']:
                    self.assertEqual(timeout, 10)
                    order.append('inventory')
                    return self.completed(json.dumps(payload).encode())
                if arguments == ['xcrun', 'simctl', 'spawn', self.OWNED, 'log', 'show', '--style', 'json',
                                 '--last', '15m', '--info', '--debug', '--predicate', simulator_probe.BOOTSTRAP_PREDICATE]:
                    order.append('logs')
                    return self.completed(json.dumps([{'process': 'testmanagerd',
                        'eventMessage': 'ProjectionTestHost ' + self.OWNED + ' ' + self.OWNED.lower() +
                            ' replay-private-sentinel'}]).encode())
                self.assertEqual(arguments, ['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', str(app)])
                self.assertEqual(timeout, 10)
                order.append('signature')
                return self.completed()
            def run(arguments, **kwargs):
                self.assertEqual(kwargs['timeout'], 10)
                if arguments == ['xcrun', 'simctl', 'get_app_container', self.OWNED, simulator_probe.BUNDLE, 'app']:
                    order.append('app')
                    return SimpleNamespace(returncode=0, stdout=str(app).encode())
                if arguments == ['xcrun', 'simctl', 'get_app_container', self.OWNED, simulator_probe.BUNDLE, 'data']:
                    order.append('data')
                    return SimpleNamespace(returncode=0, stdout=str(data).encode())
                self.assertEqual(arguments, ['xcrun', 'simctl', 'launch', '--terminate-running-process', self.OWNED, simulator_probe.BUNDLE])
                order.append('launch')
                return SimpleNamespace(returncode=0, stdout=b'private pid=999')
            with patch.object(simulator_probe, 'diagnostic_command', side_effect=diagnostic) as commands, \
                    patch.object(simulator_probe.subprocess, 'run', side_effect=run) as launches, \
                    patch.object(simulator_probe, 'capture_crashes', return_value={'state': 'available', 'reports': []}) as crashes:
                simulator_probe.capture(root, ['replay-private-sentinel'], started_at=1234.5,
                                        owned_identifier=self.OWNED, profile=self.PROFILE)
            crashes.assert_called_once_with(1234.5)
            self.assertEqual(commands.call_count, 3)
            self.assertEqual(launches.call_count, 3)
            self.assertEqual(order, ['inventory', 'logs', 'app', 'data', 'signature', 'launch'])
            report = self.read_report(root)
            self.assert_replay_metadata(report, verified=True)
            self.assertEqual(report['commands']['standalone_host_launch'], {'exit_code': 0})
            self.assertEqual(report['marker_after_standalone_launch'], {'state': 'observed', 'stage': 'sceneVisible'})
            self.assertNotIn('Personal private device', json.dumps(report))
            self.assertNotIn('private pid', json.dumps(report))
            events = report['bootstrap_events_before_standalone_launch']['events']
            self.assertEqual(len(events), 1)
            self.assertIn('<REDACTED_CREDENTIAL>', events[0]['message'])

    def test_invalid_replay_options_write_closed_identity_without_any_commands_or_crash_capture(self):
        options = [
            {'owned_identifier': self.OWNED}, {'profile': self.PROFILE},
            {'owned_identifier': self.OWNED, 'profile': 'replay-private-sentinel'},
            {'owned_identifier': self.OWNED, 'profile': []},
            {'owned_identifier': self.OWNED, 'profile': True},
            *({'owned_identifier': value, 'profile': self.PROFILE}
              for value in ('', 'replay-private-sentinel', self.OWNED + 'X', ' ' + self.OWNED,
                            self.OWNED + '\n', 1, True, [], {})),
        ]
        for kwargs in options:
            with self.subTest(options=kwargs), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(simulator_probe, 'diagnostic_command') as diagnostic, \
                        patch.object(simulator_probe.subprocess, 'run') as run, \
                        patch.object(simulator_probe, 'capture_crashes') as crashes:
                    simulator_probe.capture(root, ['replay-private-sentinel'], **kwargs)
                diagnostic.assert_not_called(); run.assert_not_called(); crashes.assert_not_called()
                report = self.read_report(root)
                self.assert_replay_metadata(report)
                self.assertEqual(report['commands'], {})
                self.assertNotIn('diagnostics', report)
                self.assertNotIn('crash_summary_before_standalone_launch', report)

    def test_replay_rejects_missing_mismatched_duplicate_and_malformed_inventory_before_device_commands(self):
        owned = self.owned_row()
        runtime = simulator_probe.RUNTIME
        wrong_runtime = 'com.apple.CoreSimulator.SimRuntime.iOS-26-5'
        payloads = [
            None, [], 'replay-private-sentinel', {}, {'devices': None}, {'devices': []}, {'devices': {}},
            {'devices': {runtime: None}}, {'devices': {runtime: {}}}, {'devices': {runtime: [None]}},
            {'devices': {runtime: ['replay-private-sentinel']}}, {'devices': {runtime: []}},
            {'devices': {runtime: [self.owned_row(udid=self.OTHER)]}},
            {'devices': {runtime: [self.owned_row(name=simulator_probe.DEVICE_NAME)]}},
            {'devices': {runtime: [self.owned_row(deviceTypeIdentifier='com.apple.CoreSimulator.SimDeviceType.iPhone-17')]}},
            {'devices': {runtime: [{key: value for key, value in owned.items() if key != 'deviceTypeIdentifier'}]}},
            {'devices': {runtime: [self.owned_row(state='Shutdown')]}},
            {'devices': {wrong_runtime: [owned]}},
            {'devices': {runtime: [owned, owned]}},
            {'devices': {runtime: [owned, self.owned_row(udid=self.OWNED.lower())]}},
            {'devices': {runtime: [owned], wrong_runtime: [owned]}},
            {'devices': {runtime: [owned], wrong_runtime: [self.owned_row(udid=self.OWNED.lower())]}},
            {'devices': {runtime: [owned], wrong_runtime: None}},
            {'devices': {runtime: [owned], wrong_runtime: [None]}},
            {'devices': {runtime: [owned], wrong_runtime: [{'name': 'replay-private-sentinel'}]}},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def inventory(arguments, secrets, timeout=20):
                    self.assertEqual(arguments, ['xcrun', 'simctl', 'list', 'devices', self.NAME, '--json'])
                    self.assertEqual(timeout, 10)
                    return self.completed(json.dumps(payload).encode())
                with patch.object(simulator_probe, 'diagnostic_command', side_effect=inventory) as diagnostic, \
                        patch.object(simulator_probe.subprocess, 'run') as run, \
                        patch.object(simulator_probe, 'capture_crashes', return_value={'state': 'available', 'reports': []}):
                    simulator_probe.capture(root, ['replay-private-sentinel'], owned_identifier=self.OWNED, profile=self.PROFILE)
                run.assert_not_called(); self.assertEqual(diagnostic.call_count, 1)
                report = self.read_report(root)
                self.assert_replay_metadata(report)
                self.assertEqual(set(report['commands']), {'devices'})
                self.assertNotIn('diagnostics', report)

    def test_failed_replay_inventory_never_falls_back_to_available_or_default_device(self):
        for status in ('timedOut', 'commandFailed', 'unavailable', 'outputLimit'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def inventory(arguments, secrets, timeout=20):
                    self.assertEqual(arguments, ['xcrun', 'simctl', 'list', 'devices', self.NAME, '--json'])
                    self.assertEqual(timeout, 10)
                    metadata, data = self.completed(json.dumps({'devices': {simulator_probe.RUNTIME: [self.owned_row()]}}).encode())
                    metadata.update(status=status, exit_code=1 if status == 'commandFailed' else None,
                                    timeout=status == 'timedOut', output_limit=status == 'outputLimit')
                    return metadata, data
                with patch.object(simulator_probe, 'diagnostic_command', side_effect=inventory) as diagnostic, \
                        patch.object(simulator_probe.subprocess, 'run') as run, \
                        patch.object(simulator_probe, 'capture_crashes', return_value={'state': 'available', 'reports': []}):
                    simulator_probe.capture(root, [], owned_identifier=self.OWNED, profile=self.PROFILE)
                run.assert_not_called(); self.assertEqual(diagnostic.call_count, 1)
                report = self.read_report(root)
                self.assert_replay_metadata(report)
                self.assertEqual(report['commands']['devices']['status'], status)
                self.assertEqual(set(report['commands']), {'devices'})

    def test_default_capture_keeps_available_inventory_and_omits_replay_identity_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def inventory(arguments, secrets, timeout=20):
                self.assertEqual(arguments, ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'])
                self.assertEqual(timeout, 10)
                return self.completed(json.dumps({'devices': {simulator_probe.RUNTIME: []}}).encode())
            with patch.object(simulator_probe, 'diagnostic_command', side_effect=inventory) as diagnostic, \
                    patch.object(simulator_probe.subprocess, 'run') as run, \
                    patch.object(simulator_probe, 'capture_crashes', return_value={'state': 'available', 'reports': []}) as crashes:
                simulator_probe.capture(root, [], started_at=1234.5)
            self.assertEqual(simulator_probe.DEVICE_NAME, 'BAZEL_TEST_iPhone 17_27.0')
            run.assert_not_called(); self.assertEqual(diagnostic.call_count, 1)
            crashes.assert_called_once_with(1234.5)
            report = self.read_report(root)
            for key in ('profile', 'device_type', 'runtime', 'device_identity'):
                self.assertNotIn(key, report)
