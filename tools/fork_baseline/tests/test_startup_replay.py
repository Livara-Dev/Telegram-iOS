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
from storage_evidence import STORAGE_SELECTION, inspect_storage


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


def signed_application(root):
    app = application(root)
    binary = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 2, 40, 0, 0)
    binary += struct.pack('<6I', 0x32, 24, 7, 0x000f0000, 0x001b0100, 0)
    binary += struct.pack('<4I', 0x1d, 16, 96, 16)
    binary += b'original-code'.ljust(24, b'\0') + b'signature-bytes!'
    (app / 'Telegram').write_bytes(binary)
    for name in ('Share.appex', 'Notification.appex'):
        extension = app / 'PlugIns' / name
        extension.mkdir(parents=True)
        (extension / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'example.' + name}))
    resource = app / 'Resources/PlugIns/retained.txt'
    resource.parent.mkdir(parents=True)
    resource.write_text('nested resource called PlugIns must be preserved')
    return app


def group_storage_failure(root):
    preferences = Path(root) / 'group-preferences.plist'
    preferences.write_bytes(plistlib.dumps({STORAGE_SELECTION: {'version': 1, 'kind': 'group',
                                                              'identifier': 'group.' + replay.BUNDLE}}))
    return {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
            'authentication_codes_used': False, 'passed': False, 'expected_storage_kind': 'private',
            'storage_test_setup': 'freshAutomatic', 'phase': 'checkStorageSelection',
            'launches': [{'attempt': 1, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                          'storage_kind': 'group', 'storage_evidence': inspect_storage(preferences), 'seconds_to_marker': 3}],
            'failure': {'code': 'invalidStorageEvidence', 'phase': 'checkStorageSelection', 'return_code': None,
                        'timeout': False, 'system_errors': []}}


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
    def test_private_only_flag_rejects_original_mode_and_nonboolean_values_before_tool_invocation(self):
        for variant, flag in (('original', True), ('main-app-only', 'true')):
            with self.subTest(variant=variant, flag=flag), mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command') as command, mock.patch.object(replay, 'smoke') as launch:
                with self.assertRaisesRegex(replay.ReplayError, 'invalid_private_control_mode'):
                    replay.replay(Path('source.ipa'), Path('evidence'), variant, private_control_only=flag)
                command.assert_not_called(); launch.assert_not_called()

    def test_explicit_private_mode_runs_one_fresh_control_and_no_automatic_launch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); app = signed_application(root); ipa = root / 'source.ipa'; replay.repack_app(app, ipa)
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', return_value={'extensions_removed': 2, 'main_code_unchanged': True}), \
                 mock.patch.object(replay, 'smoke', return_value={'passed': True}) as launch:
                result = replay.replay(ipa, root / 'evidence', 'main-app-only', private_control_only=True)
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(launch.call_args.kwargs, {'expected_storage_kind': 'private', 'seed_private_storage': True})
            self.assertTrue(result['passed'])
            self.assertEqual(result['private_mode_control'], 'explicitFreshPinnedSelection')
            self.assertFalse(result['automatic_control_run'])
            self.assertFalse(result['automatic_group_selection_observed'])
            self.assertFalse(result['automatic_fallback_verified'])
            self.assertEqual(result['automatic_storage_kind'], 'notObserved')
            self.assertFalse((root / 'evidence/main-app-only-automatic').exists())

    def test_explicit_private_mode_failure_never_retries_or_claims_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); app = signed_application(root); ipa = root / 'source.ipa'; replay.repack_app(app, ipa)
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', return_value={'extensions_removed': 2, 'main_code_unchanged': True}), \
                 mock.patch.object(replay, 'smoke', side_effect=replay.SmokeError('invalidStorageEvidence', 'checkPrivateAccountStore')) as launch:
                with self.assertRaises(replay.SmokeError):
                    replay.replay(ipa, root / 'evidence', 'main-app-only', private_control_only=True)
            self.assertEqual(launch.call_count, 1)
            report = json.loads((root / 'evidence/replay.json').read_text())
            self.assertFalse(report['passed'])
            self.assertFalse(report['automatic_control_run'])
            self.assertFalse(report['automatic_group_selection_observed'])
            self.assertEqual(report['phase'], 'explicitPrivateControl')

    def test_group_failure_admission_rejects_missing_malformed_or_wrong_identity_preferences(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); value = group_storage_failure(root); path = root / 'result.json'
            error = replay.SmokeError('invalidStorageEvidence', 'checkStorageSelection')
            path.write_text(json.dumps(value)); self.assertTrue(replay.automatic_group_failure(error, path))
            for field in ('file_exists', 'plist_parsed', 'selection_present', 'version_integer', 'version_is_one',
                          'identifier_matches_expected_group', 'selection_unknown_keys'):
                changed = copy.deepcopy(value)
                changed['launches'][0]['storage_evidence'][field] = not changed['launches'][0]['storage_evidence'][field]
                path.write_text(json.dumps(changed))
                with self.subTest(field=field): self.assertFalse(replay.automatic_group_failure(error, path))
            path.write_text(json.dumps(value))
            self.assertFalse(replay.automatic_group_failure(replay.SmokeError('commandFailed', 'checkStorageSelection'), path))
            self.assertFalse(replay.automatic_group_failure(replay.SmokeError('invalidStorageEvidence', 'checkPrivateAccountStore'), path))

    def test_valid_automatic_group_selection_is_preserved_before_a_separate_fresh_private_control(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); app = signed_application(root); ipa = root / 'source.ipa'; replay.repack_app(app, ipa)
            output = root / 'evidence'; calls = []
            def fake(ipa, destination, **kwargs):
                calls.append((destination, kwargs))
                if len(calls) == 1:
                    destination.mkdir(parents=True)
                    (destination / 'result.json').write_text(json.dumps(group_storage_failure(root)))
                    raise replay.SmokeError('invalidStorageEvidence', 'checkStorageSelection')
                self.assertTrue(kwargs['seed_private_storage'])
                destination.mkdir(parents=True)
                return {'passed': True}
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', return_value={'extensions_removed': 2, 'main_code_unchanged': True}), \
                 mock.patch.object(replay, 'smoke', side_effect=fake):
                result = replay.replay(ipa, output, 'main-app-only')
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][1], {'expected_storage_kind': 'private'})
            self.assertEqual(calls[1][1], {'expected_storage_kind': 'private', 'seed_private_storage': True})
            self.assertTrue((output / 'main-app-only-automatic/launch-smoke/result.json').is_file())
            self.assertTrue(result['passed'])
            self.assertTrue(result['automatic_group_selection_observed'])
            self.assertFalse(result['automatic_fallback_verified'])
            self.assertEqual(result['private_mode_control'], 'freshPinnedSelection')

    def test_missing_preference_evidence_never_admits_seeded_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); app = signed_application(root); ipa = root / 'source.ipa'; replay.repack_app(app, ipa)
            calls = []
            def fake(ipa, destination, **kwargs):
                calls.append(kwargs); destination.mkdir(parents=True)
                report = group_storage_failure(root)
                report['launches'][0]['storage_evidence'] = inspect_storage(root / 'does-not-exist.plist')
                report['launches'][0]['storage_kind'] = 'unverified'
                (destination / 'result.json').write_text(json.dumps(report))
                raise replay.SmokeError('invalidStorageEvidence', 'checkStorageSelection')
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', return_value={'extensions_removed': 2, 'main_code_unchanged': True}), \
                 mock.patch.object(replay, 'smoke', side_effect=fake):
                with self.assertRaises(replay.SmokeError): replay.replay(ipa, root / 'evidence', 'main-app-only')
            self.assertEqual(len(calls), 1)
            result = json.loads((root / 'evidence/replay.json').read_text())
            self.assertFalse(result['passed'])
            self.assertFalse(result['pinned_private_control_attempted'])

    def test_main_only_removes_exact_extension_boundary_and_preserves_all_entitlements(self):
        with tempfile.TemporaryDirectory() as d:
            app = signed_application(d)
            original = entitlements()
            runner = Codesign(original=original, changed=original)
            result = replay.main_app_only(app, d, run=runner)
            self.assertEqual(result['extensions_removed'], 2)
            self.assertTrue(result['main_code_unchanged'])
            self.assertTrue(result['other_entitlements_preserved'])
            self.assertFalse(result['app_group_entitlement_absent_after'])
            self.assertFalse((app / 'PlugIns').exists())
            self.assertTrue((app / 'Resources/PlugIns/retained.txt').is_file())
            self.assertEqual((app / 'Frameworks/Sample.framework/Sample').read_bytes(), b'synthetic framework bytes')
            self.assertEqual(len([a for a, _ in runner.calls if '--force' in a]), 1)
            self.assertTrue(any(a[1:4] == ['--verify', '--deep', '--strict'] for a, _ in runner.calls))
            supplied = plistlib.loads((Path(d) / 'filtered-entitlements.plist').read_bytes())
            self.assertEqual(supplied, original)

    def test_main_only_rejects_link_or_unknown_item_before_removal(self):
        for unsafe in ('symlink', 'file'):
            with tempfile.TemporaryDirectory() as d:
                app = signed_application(d); runner = Codesign()
                path = app / 'PlugIns/unsafe'
                path.symlink_to('/tmp') if unsafe == 'symlink' else path.write_bytes(b'not an extension')
                with self.assertRaises(replay.ReplayError): replay.main_app_only(app, d, run=runner)
                self.assertTrue((app / 'PlugIns/Share.appex').exists())
                self.assertFalse(runner.calls)

    def test_main_only_rejects_executable_code_or_framework_mutation(self):
        for target in ('Telegram', 'Frameworks/Sample.framework/Sample'):
            with tempfile.TemporaryDirectory() as d:
                app = signed_application(d); original = {'get-task-allow': True}
                codesign = Codesign(original=original, changed=original)
                def mutate(args, **kwargs):
                    result = codesign(args, **kwargs)
                    if '--force' in args:
                        path = app / target
                        data = bytearray(path.read_bytes()); data[-17 if target == 'Telegram' else -1] ^= 1
                        path.write_bytes(data)
                    return result
                with self.assertRaises(replay.ReplayError): replay.main_app_only(app, d, run=mutate)

    def test_main_code_digest_permits_signature_size_but_never_code_or_offset_changes(self):
        with tempfile.TemporaryDirectory() as d:
            app = signed_application(d); path = app / 'Telegram'
            before = replay.main_code_digest(app); original = path.read_bytes()
            data = bytearray(original); struct.pack_into('<I', data, 68, 8); path.write_bytes(data)
            self.assertEqual(before, replay.main_code_digest(app))
            data[80] ^= 1; path.write_bytes(data)
            self.assertNotEqual(before, replay.main_code_digest(app))
            data = bytearray(original); struct.pack_into('<I', data, 64, 95); path.write_bytes(data)
            self.assertNotEqual(before, replay.main_code_digest(app))

    def test_main_only_runs_only_one_variant_private_control_without_normal_install(self):
        with tempfile.TemporaryDirectory() as d:
            app = signed_application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            def prepare(app, directory):
                import shutil
                shutil.rmtree(app / 'PlugIns')
                return {'extensions_removed': 2, 'main_code_unchanged': True}
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', side_effect=prepare), \
                 mock.patch.object(replay, 'smoke', return_value={'passed': True}) as launched:
                result = replay.replay(ipa, Path(d) / 'evidence', 'main-app-only')
            self.assertEqual(launched.call_count, 1)
            self.assertIn('main-app-only/launch-smoke', str(launched.call_args.args[1]))
            self.assertEqual(launched.call_args.kwargs, {'expected_storage_kind': 'private'})
            self.assertEqual(result['mode'], 'main_app_only')
            self.assertFalse(result['normal_control_passed'])
            self.assertTrue(result['source_ipa_unchanged'])
            self.assertTrue(result['main_app_only_control_passed'])
            self.assertTrue(result['passed'])

    def test_main_only_fails_before_launch_when_source_groups_would_change_another_variable(self):
        with tempfile.TemporaryDirectory() as d:
            app = signed_application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps(entitlements())), \
                 mock.patch.object(replay, 'smoke') as launched:
                with self.assertRaises(replay.ReplayError): replay.replay(ipa, Path(d) / 'evidence', 'main-app-only')
            launched.assert_not_called()

    def test_failed_variant_never_reports_pass_or_retries_the_original(self):
        with tempfile.TemporaryDirectory() as d:
            app = signed_application(d); ipa = Path(d) / 'source.ipa'; replay.repack_app(app, ipa)
            output = Path(d) / 'evidence'
            with mock.patch.object(replay.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(replay, 'private_command', return_value=plistlib.dumps({'get-task-allow': True})), \
                 mock.patch.object(replay, 'main_app_only', return_value={'extensions_removed': 2, 'main_code_unchanged': True}), \
                 mock.patch.object(replay, 'smoke', return_value={'passed': False}) as launched:
                with self.assertRaises(replay.ReplayError): replay.replay(ipa, output, 'main-app-only')
            self.assertEqual(launched.call_count, 1)
            result = json.loads((output / 'replay.json').read_text())
            self.assertEqual(result['phase'], 'mainAppOnlyControl')
            self.assertFalse(result['normal_control_passed'])
            self.assertFalse(result['passed'])

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
