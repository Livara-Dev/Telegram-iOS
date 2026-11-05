import copy
import io
import json
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import startup_replay as replay
from ipa_inspect import inspect_ipa


def application(root):
    app = Path(root) / 'Telegram.app'; app.mkdir()
    info = {'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'Telegram',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator']}
    (app / 'Info.plist').write_bytes(plistlib.dumps(info))
    binary = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
    binary += struct.pack('<6I', 0x32, 24, 7, 0x000f0000, 0x001b0100, 0)
    (app / 'Telegram').write_bytes(binary); (app / 'Telegram').chmod(0o700)
    framework = app / 'Frameworks/Sample.framework/Sample'
    framework.parent.mkdir(parents=True); framework.write_bytes(b'synthetic framework bytes')
    return app


def entitlements():
    return {replay.GROUPS: ['group.org.livara.ayugram'],
            'application-identifier': 'TESTTEAM00.org.livara.ayugram',
            'keychain-access-groups': ['TESTTEAM00.org.livara.ayugram'],
            'get-task-allow': True, 'com.example.unchanged': {'flag': True}}


class Codesign:
    def __init__(self, original=None, changed=None, mutate_framework=False):
        self.original = entitlements() if original is None else original
        self.changed = changed
        self.mutate_framework = mutate_framework
        self.calls, self.displays = [], 0

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if '-d' in args:
            value = self.original if self.displays == 0 else (self.changed if self.changed is not None else replay.filtered_entitlements(self.original))
            self.displays += 1
            kwargs['stdout'].write(plistlib.dumps(value))
        if '--force' in args and self.mutate_framework:
            (Path(args[-1]) / 'Frameworks/Sample.framework/Sample').write_bytes(b'changed')
        return subprocess.CompletedProcess(args, 0)


class StartupReplayTests(unittest.TestCase):
    def test_entitlements_drop_only_app_groups_and_do_not_mutate_input(self):
        original = entitlements(); before = copy.deepcopy(original)
        filtered = replay.filtered_entitlements(original)
        self.assertEqual(original, before)
        self.assertNotIn(replay.GROUPS, filtered)
        self.assertEqual(filtered, {key: value for key, value in before.items() if key != replay.GROUPS})

    def test_malformed_or_oversize_entitlements_fail_closed(self):
        values = [b'invalid', plistlib.dumps([]), plistlib.dumps({replay.GROUPS: 'not an array'}),
                  plistlib.dumps({'keychain-access-groups': [True]}),
                  plistlib.dumps({'application-identifier': 123}), b'x' * (replay.LIMIT + 1)]
        for value in values:
            with self.subTest(size=len(value)), self.assertRaises(replay.ReplayError) as caught:
                replay.parse_entitlements(value)
            self.assertEqual(str(caught.exception), 'invalid_entitlements')

    def test_resign_preserves_identity_keychain_and_frameworks_without_deep_sign(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); runner = Codesign()
            result = replay.resign_main(app, d, run=runner)
            self.assertTrue(result['other_entitlements_preserved'])
            self.assertTrue(result['app_group_entitlement_absent_after'])
            self.assertEqual(result['framework_files_unchanged'], 1)
            signs = [args for args, _ in runner.calls if '--force' in args]
            self.assertEqual(len(signs), 1)
            self.assertEqual(signs[0][signs[0].index('--sign') + 1], '-')
            self.assertNotIn('--deep', signs[0])
            self.assertEqual(signs[0][-1], str(app))
            supplied = plistlib.loads(Path(signs[0][signs[0].index('--entitlements') + 1]).read_bytes())
            self.assertEqual(supplied['keychain-access-groups'], entitlements()['keychain-access-groups'])
            self.assertEqual(supplied['application-identifier'], entitlements()['application-identifier'])
            self.assertTrue(all(not kwargs.get('shell') for _, kwargs in runner.calls))
            self.assertNotIn('TESTTEAM00', json.dumps(result))
            self.assertEqual(stat.S_IMODE((Path(d) / 'filtered-entitlements.plist').stat().st_mode), 0o600)

    def test_unexpected_entitlement_or_framework_changes_are_rejected(self):
        changed = replay.filtered_entitlements(entitlements()); changed.pop('get-task-allow')
        for runner in [Codesign(changed=changed), Codesign(mutate_framework=True)]:
            with tempfile.TemporaryDirectory() as d, self.assertRaises(replay.ReplayError):
                replay.resign_main(application(d), d, run=runner)

    def test_main_bundle_validation_precedes_codesign(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d)
            (app / 'Telegram').unlink(); (app / 'Telegram').symlink_to('/bin/sh')
            runner = Codesign()
            with self.assertRaises(replay.ReplayError): replay.resign_main(app, d, run=runner)
            self.assertFalse(runner.calls)

    def test_repacked_package_remains_simulator_only_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); destination = Path(d) / 'control.ipa'
            replay.repack_app(app, destination)
            self.assertEqual(inspect_ipa(destination, 'simulator', replay.BUNDLE)['mach_o_platform'], 7)
            (app / 'link').symlink_to('/bin/sh')
            with self.assertRaises(replay.ReplayError): replay.repack_app(app, Path(d) / 'bad.ipa')

    def test_codesign_failures_expose_no_private_output(self):
        def failure(args, **kwargs):
            kwargs['stderr'].write(b'private entitlement value must not appear')
            return subprocess.CompletedProcess(args, 1)
        with tempfile.TemporaryDirectory() as d, self.assertRaises(replay.ReplayError) as caught:
            replay.private_command(['/usr/bin/codesign'], d, 'failure', run=failure)
        self.assertEqual(str(caught.exception), 'codesign_failed')

    def test_replay_requests_private_storage_assertions_without_app_recompile(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps(entitlements())), \
                 mock.patch.object(replay, 'resign_main', return_value={'other_entitlements_preserved': True}), \
                 mock.patch.object(replay, 'smoke', return_value={'passed': True}) as launched:
                result = replay.replay(ipa, Path(d) / 'evidence')
            self.assertEqual(launched.call_count, 2)
            self.assertEqual(launched.call_args_list[0].kwargs, {'expected_storage_kind': None})
            self.assertEqual(launched.call_args.kwargs, {'expected_storage_kind': 'private'})
            self.assertEqual(result['mode'], 'without_app_groups')
            self.assertFalse(result['application_recompiled'])
            self.assertFalse(result['physical_device_verified'])
            self.assertTrue(result['passed'])
            self.assertEqual(result['app_group_control'], 'strippedVariant')

    def test_already_absent_groups_are_covered_by_one_private_normal_control(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'resign_main') as resign, \
                 mock.patch.object(replay, 'smoke', return_value={'passed': True}) as launched:
                result = replay.replay(ipa, Path(d) / 'evidence')
            resign.assert_not_called()
            self.assertEqual(launched.call_count, 1)
            self.assertEqual(launched.call_args.kwargs, {'expected_storage_kind': 'private'})
            self.assertEqual(result['app_group_control'], 'coveredByNormal')
            self.assertFalse(result['no_app_groups_control_attempted'])
            self.assertFalse(result['main_bundle_resigned_ad_hoc'])
            self.assertTrue(result['source_ipa_unchanged'])

    def test_failed_normal_control_never_resigns_or_attempts_another_variant(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            output = Path(d) / 'evidence'
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps(entitlements())), \
                 mock.patch.object(replay, 'resign_main') as resign, \
                 mock.patch.object(replay, 'smoke', side_effect=RuntimeError('closed smoke failure')) as launched:
                with self.assertRaises(RuntimeError): replay.replay(ipa, output)
            resign.assert_not_called()
            self.assertEqual(launched.call_count, 1)
            result = json.loads((output / 'replay.json').read_text())
            self.assertFalse(result['passed'])
            self.assertFalse(result['normal_control_passed'])
            self.assertFalse(result['no_app_groups_control_attempted'])
            self.assertEqual(result['phase'], 'normalControl')

    def test_source_signature_failure_prevents_launch_and_keeps_exact_phase(self):
        with tempfile.TemporaryDirectory() as d:
            app = application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            output = Path(d) / 'evidence'
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', side_effect=replay.ReplayError('codesign_failed')) as verify, \
                 mock.patch.object(replay, 'smoke') as launched:
                with self.assertRaises(replay.ReplayError): replay.replay(ipa, output)
            launched.assert_not_called()
            command = verify.call_args.args[0]
            self.assertEqual(command[:4], ['/usr/bin/codesign', '--verify', '--deep', '--strict'])
            result = json.loads((output / 'replay.json').read_text())
            self.assertEqual(result['phase'], 'verifyCodeSignature')
            self.assertFalse(result['source_codesign_verified'])
            self.assertFalse(result['normal_control_passed'])
            self.assertFalse(result['passed'])

    def test_non_macos_replay_never_runs_codesign_or_installs_any_app(self):
        with mock.patch.object(replay.platform, 'system', return_value='Linux'), \
             mock.patch.object(replay, 'private_command') as command, \
             self.assertRaises(replay.ReplayError):
            replay.replay(Path('source.ipa'), Path('evidence'))
        command.assert_not_called()
