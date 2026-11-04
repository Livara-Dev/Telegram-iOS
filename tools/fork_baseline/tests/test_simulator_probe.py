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
    def test_only_synthetic_host_is_launched_and_outputs_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Synthetic.app'; app.mkdir()
            identifier = '11111111-2222-3333-4444-555555555555'
            devices = {'devices': {'com.apple.CoreSimulator.SimRuntime.iOS-27-0': [
                {'name': 'BAZEL_TEST_iPhone 17_27.0', 'state': 'Booted', 'udid': identifier},
                {'name': 'Personal phone', 'state': 'Booted', 'udid': 'do-not-touch'}]}}
            replies = [json.dumps(devices).encode(), str(app).encode(), b'private-fixture-secret', b'host pid=3']
            with patch.object(simulator_probe.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout=s) for s in replies]) as run:
                simulator_probe.capture(root, ['private-fixture-secret'])
            calls = [c.args[0] for c in run.call_args_list]
            self.assertEqual(calls[-1], ['xcrun', 'simctl', 'launch', '--terminate-running-process', identifier, simulator_probe.BUNDLE])
            report = (root / 'build/fork-evidence/projection-launch-probe.json').read_text()
            self.assertNotIn('private-fixture-secret', report)
            self.assertNotIn('Personal phone', report)
            self.assertNotIn(str(root), report)
            self.assertIn('[REDACTED]', report)
            self.assertTrue(json.loads(report)['probe_only_not_test_result'])

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
