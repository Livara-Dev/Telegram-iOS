import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('prepare_runtime', ROOT / 'tools/fork_baseline/prepare_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class RuntimeTests(unittest.TestCase):
    def test_installed_runtime_creates_matching_device(self):
        runtime = {'version': '27.0', 'identifier': 'com.apple.CoreSimulator.SimRuntime.iOS-27-0', 'isAvailable': True}
        with patch.object(host, 'command', side_effect=[json.dumps({'runtimes': [runtime]}), json.dumps({'devices': {}}), 'new-device']) as command:
            host.prepare_simulator()
        self.assertEqual(command.call_args.args, ('xcrun', 'simctl', 'create', 'iPhone 17', 'com.apple.CoreSimulator.SimDeviceType.iPhone-17', runtime['identifier']))

    def test_old_or_unavailable_runtime_is_not_silently_used(self):
        for runtime in [{'version': '26.5', 'isAvailable': True}, {'version': '27.0', 'isAvailable': False}]:
            with patch.object(host, 'command', return_value=json.dumps({'runtimes': [runtime]})):
                with self.assertRaises(RuntimeError):
                    host.prepare_simulator()

    def test_every_test_target_has_the_same_explicit_runtime(self):
        paths = ['Postbox', 'TelegramCore', 'LocalMessageArchive', 'TextFormat', 'TelegramUI/Components/Chat/ChatHistoryEntry']
        for path in paths:
            source = (ROOT / 'submodules' / path / 'BUILD').read_text()
            self.assertIn('os_version = "27.0"', source, path)
            self.assertNotIn('os_version = "26.5"', source, path)
