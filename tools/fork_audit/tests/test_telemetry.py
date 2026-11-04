import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('telemetry_guard', Path(__file__).resolve().parents[1] / 'telemetry.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class TelemetryGuardTests(unittest.TestCase):
    def test_detects_known_rpc_and_collector_reintroduction(self):
        for text in ['Api.functions.help.saveAppLog(events: data)', 'Api.functions.messages.reportReadMetrics(peer: peer)', 'Api.functions.messages.reportMusicListen(id: id)', 'Api.functions.phone.saveCallLog(peer: peer)', 'AppCenter.start(withAppSecret: secret)', 'MusicListenTracker(engine: engine)', 'MessageReadMetricsTracker()', 'NetworkStatsContext(postbox: postbox)', 'managedNetworkTelemetryReports(postbox: postbox, network: network)']:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'submodules/Example/Sources/Example.swift'
                source.parent.mkdir(parents=True)
                source.write_text(text)
                self.assertEqual(guard.violations(root), ['submodules/Example/Sources/Example.swift'])

    def test_leaves_auth_messaging_user_rating_and_declarations_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'submodules/Example/Sources/Example.swift'
            source.parent.mkdir(parents=True)
            source.write_text('Api.functions.auth.signIn()\nApi.functions.messages.sendMessage()\nApi.functions.phone.setCallRating()\nfunc managedNetworkTelemetryReports(postbox: Postbox) {}\n')
            self.assertEqual(guard.violations(root), [])

    def test_sdk_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'submodules/TelegramUI/BUILD'
            source.parent.mkdir(parents=True)
            source.write_text('deps = ["@appcenter_sdk//:AppCenter"]')
            self.assertEqual(guard.violations(root), ['submodules/TelegramUI/BUILD'])
