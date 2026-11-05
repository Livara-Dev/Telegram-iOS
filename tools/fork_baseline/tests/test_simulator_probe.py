import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simulator_probe


class SimulatorProbeTests(unittest.TestCase):
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
            replies = iter([json.dumps(devices).encode(), str(app).encode(), str(data).encode(), b'private-fixture-secret', b'host pid=3'])
            def reply(args, **kwargs):
                value = next(replies)
                if 'launch' in args:
                    marker.write_text('{"stage":"sceneVisible"}')
                return SimpleNamespace(returncode=0, stdout=value)
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply) as run:
                simulator_probe.capture(root, ['private-fixture-secret'])
            calls = [c.args[0] for c in run.call_args_list]
            self.assertEqual(len(calls), 5)
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
            replies = iter([json.dumps(devices).encode(), str(app).encode(), str(data).encode(), b'injected seal invalid', b'pid=999'])
            def reply(args, **kwargs):
                value = next(replies)
                if 'launch' in args:
                    marker.write_text('{"stage":"sceneVisible"}')
                return SimpleNamespace(returncode=1 if args[0] == '/usr/bin/codesign' else 0, stdout=value)
            with patch.object(simulator_probe.subprocess, 'run', side_effect=reply):
                simulator_probe.capture(root, [])
            report = json.loads((root / 'build/fork-evidence/projection-launch-probe.json').read_text())
            self.assertEqual(report['marker_before_standalone_launch'], {'state': 'absent'})
            self.assertEqual(report['marker_after_standalone_launch'], {'state': 'observed', 'stage': 'sceneVisible'})
            self.assertTrue(report['probe_only_not_test_result'])
            self.assertEqual(report['commands']['installed_signature'], {'exit_code': 1})

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
