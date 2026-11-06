# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable native-driver contracts; no fresh Apple XCTest execution is implied."""
import copy
import contextlib
import functools
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import custom_options_xctest as replay
import test_custom_options_replay as retained_fixture
from test_test_product_capture import macho


def generated(root, version=1):
    app = root / 'Debug-iphonesimulator' / (replay.driver.TARGET + '-Runner.app')
    bundle = app / 'PlugIns' / (replay.driver.TARGET + '.xctest'); bundle.mkdir(parents=True)
    for directory, identifier, executable, kind in (
        (app, replay.driver.TEST_BUNDLE + '.xctrunner', 'AppleUITestRunner', 2),
        (bundle, replay.driver.TEST_BUNDLE, replay.driver.TARGET, 8)):
        (directory / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier,
            'CFBundleExecutable': executable, 'CFBundleSupportedPlatforms': ['iPhoneSimulator']}))
        (directory / executable).write_bytes(macho(b'synthetic Apple-code fixture', kind=kind))
    item = {'ProductModuleName': replay.driver.TARGET, 'IsUITestBundle': True,
        'TestHostPath': '__TESTROOT__/Debug-iphonesimulator/' + app.name,
        'TestBundlePath': '__TESTHOST__/PlugIns/' + bundle.name,
        'TestHostBundleIdentifier': replay.driver.TEST_BUNDLE + '.xctrunner',
        'DependentProductPaths': ['__TESTROOT__/Debug-iphonesimulator/' + app.name],
        'TestingEnvironmentVariables': {'DYLD_FRAMEWORK_PATH': '__TESTROOT__/Debug-iphonesimulator'}}
    plan = {'Runner': item} if version == 1 else {'__xctestrun_metadata__': {'FormatVersion': 2},
        'TestConfigurations': [{'TestTargets': [item]}]}
    (root / 'generated.xctestrun').write_bytes(plistlib.dumps(plan))
    return plan, app, bundle


@functools.lru_cache(maxsize=1)
def png():
    def chunk(kind, payload): return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    data = struct.pack('>IIBBBBB', 1179, 2556, 8, 2, 0, 0, 0)
    pixels = (b'\0' + b'\0' * (1179 * 3)) * 2556
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', data) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def case_log(state='passed'):
    name, method = replay.driver.EXPECTED_CASE.split('.')
    return (f"Test Case '-[{name}.{name} {method}]' started.\n" +
            f"Test Case '-[{name}.{name} {method}]' {state} (1.0 seconds).\n").encode()


def universal(endian='>', wide=False):
    arm = macho(b'synthetic ARM64 executable'); other = bytearray(macho(b'synthetic x86 executable'))
    struct.pack_into('<II', other, 4, 0x01000007, 3)
    output = bytearray(512 + len(other)); output[128:128 + len(arm)] = arm; output[512:] = other
    struct.pack_into(endian + 'II', output, 0, 0xCAFEBABF if wide else 0xCAFEBABE, 2)
    for index, (cpu, subtype, offset, length) in enumerate(((0x0100000C, 0, 128, len(arm)), (0x01000007, 3, 512, len(other)))):
        values = (cpu, subtype, offset, length, 7) + ((0,) if wide else ())
        struct.pack_into(endian + ('IIQQII' if wide else 'IIIII'), output, 8 + index * (32 if wide else 20), *values)
    return output


class Native(retained_fixture.Native):
    def __call__(self, args, timeout=30):
        if args[:3] == ['xcrun', 'simctl', 'install']: raise AssertionError('Only the bound Apple UI runner may install this target')
        if args == ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version']:
            self.calls.append((args, timeout)); return retained_fixture.complete(b'27.1')
        if self.kind == 'driverSignature' and args[0] == '/usr/bin/codesign' and hasattr(self, 'runner') and args[-1] == str(self.runner):
            self.calls.append((args, timeout)); return {**retained_fixture.complete()[0], 'status': 'commandFailed', 'exit_code': 1}, b''
        if args[:2] == ['xcodebuild', 'build-for-testing']:
            self.calls.append((args, timeout))
            if self.kind == 'compile': return {**retained_fixture.complete()[0], 'status': 'commandFailed', 'exit_code': 65}, b''
            root = Path(args[args.index('-derivedDataPath') + 1]) / 'Build/Products'; root.mkdir(parents=True)
            self.generated, self.runner, self.bundle = generated(root)
            if self.kind == 'plan':
                replay.target(self.generated)['IsUITestBundle'] = False
                (root / 'generated.xctestrun').write_bytes(plistlib.dumps(self.generated))
            return retained_fixture.complete(b'Build successful /Users/private PRIVATE-CANARY -I' + str(root).encode() + b'/private-header')
        if args[:2] == ['xcodebuild', 'test-without-building']:
            self.calls.append((args, timeout)); self.assert_native_args(args, timeout)
            bound = replay.read_plan(Path(args[args.index('-xctestrun') + 1])); self.bound_app = Path(replay.target(bound)['UITargetAppPath'])
            if self.installed.exists(): raise AssertionError('No target app may be preinstalled before the Apple UI test')
            if str(self.bound_app) not in replay.target(bound)['DependentProductPaths']: raise AssertionError('Frozen app dependency missing')
            self.app = self.bound_app
            if self.kind != 'missingInstalled':
                self.installed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.bound_app, self.installed)
            Path(args[args.index('-resultBundlePath') + 1]).mkdir()
            data = case_log('failed' if self.kind == 'caseFailed' else 'passed')
            if self.kind == 'missingCase': data = b''
            if self.kind == 'timeout': data = case_log().splitlines(keepends=True)[0]
            if self.kind == 'sourceMutate': (self.bound_app / 'CustomeOptionsHost').write_bytes(b'changed source executable')
            if self.kind == 'installedMutate': (self.installed / 'CustomeOptionsHost').write_bytes(b'changed installed executable')
            if self.kind == 'runnerMutate': (self.runner / 'AppleUITestRunner').write_bytes(b'changed runner executable')
            status = retained_fixture.complete(data)[0]
            if self.kind == 'caseFailed': status.update(status='commandFailed', exit_code=65)
            if self.kind == 'timeout': status.update(status='timedOut', exit_code=None, timeout=True)
            return status, data
        if args[:4] == ['xcrun', 'xcresulttool', 'export', 'attachments']:
            self.calls.append((args, timeout))
            if self.kind == 'export': return {**retained_fixture.complete()[0], 'status': 'commandFailed', 'exit_code': 1}, b''
            exported = Path(args[args.index('--output-path') + 1]); (exported / 'native.png').write_bytes(png())
            (exported / 'manifest.json').write_text(json.dumps([{'configuredName': 'retained-root', 'exportedFileName': 'native.png'}]))
            return retained_fixture.complete()
        return super().__call__(args, timeout)

    def assert_native_args(self, args, timeout):
        if timeout != 600: raise AssertionError('Bounded UI experiment deadline changed')
        if args[args.index('-parallel-testing-enabled') + 1] != 'NO': raise AssertionError('Parallel XCTest enabled')
        if args[args.index('-destination') + 1] != 'platform=iOS Simulator,id=' + retained_fixture.IDENTIFIER: raise AssertionError('Wrong device')
        for forbidden in ('-test-iterations', '-retry-tests-on-failure', '-run-tests-until-failure', '-only-testing', '-skip-testing'):
            if forbidden in args: raise AssertionError('Unverified test selection/retry')


class RetainedNativeUIDriverTests(unittest.TestCase):
    def prepared(self): return retained_fixture.RetainedSettingsReplayTests().prepared()

    def test_owned_driver_log_redacts_attached_flags_temp_aliases_and_secrets_before_utf8_byte_cap(self):
        private = Path('/private/var/folders/private-user/T/ayu-options-xctest-private')
        home = Path('/Users/private-user')
        raw = (b'swift -I/var/folders/private-user/T/ayu-options-xctest-private/derived/header '
               b'-F/private/var/folders/private-user/T/ayu-options-xctest-private/framework '
               b'-iquote/Users/private-user/source "-I/Users/private-user/path with spaces" '
               b'PRIVATE-CANARY\n' + ('emoji \U0001f600 ' * 7000).encode('utf-8'))
        original_length = len(raw)
        selected = replay.redacted_driver_log(raw, ['PRIVATE-CANARY'], (private, home))
        self.assertLessEqual(len(selected.encode('utf-8')), 16384)
        self.assertEqual(len(raw), original_length)
        for value in ('/var/folders/', '/private/var/', '/Users/private-user', 'PRIVATE-CANARY'):
            self.assertNotIn(value, selected)
        self.assertIn('<PRIVATE_ROOT>', selected); self.assertIn('<REDACTED_CREDENTIAL>', selected)
        self.assertEqual(selected.encode('utf-8').decode('utf-8'), selected)

    def test_canonical_product_root_may_contain_ordinary_double_underscores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'legitimate__temporary_root'; root.mkdir()
            plan, runner, _ = generated(root)
            self.assertEqual(replay.generated_products(plan, root)[0], runner)
            with self.assertRaisesRegex(replay.DriverError, '^ui_product_path_invalid$'):
                replay.resolve_product('__UNKNOWN_APPLE_TOKEN__/runner.app', root)

    def test_stock_runner_supports_valid_universal_formats_without_broadening_authored_bundle(self):
        for endian in ('>', '<'):
            for wide in (False, True):
                with self.subTest(endian=endian, wide=wide), tempfile.TemporaryDirectory() as directory:
                    data = universal(endian, wide)
                    self.assertTrue(replay.runner_macho(lambda offset, amount: bytes(data[offset:offset + amount]), len(data)))
                    root = Path(directory).resolve(); plan, runner, bundle = generated(root)
                    (runner / 'AppleUITestRunner').write_bytes(data)
                    self.assertEqual(replay.generated_products(plan, root)[0], runner)
                    (bundle / replay.driver.TARGET).write_bytes(data)
                    with self.assertRaisesRegex(replay.DriverError, '^ui_product_not_arm64_simulator$'):
                        replay.generated_products(plan, root)

    def test_universal_runner_rejects_ambiguous_overlapping_truncated_or_device_code(self):
        for kind in ('duplicate', 'twoARM', 'missingARM', 'overlap', 'tableOverlap', 'range', 'alignment',
                     'cpuMismatch', 'wrongKind', 'device', 'count', 'truncated', 'reserved'):
            with self.subTest(kind=kind):
                data = universal('>', True); second = 40
                if kind == 'duplicate':
                    struct.pack_into('>II', data, second, 0x0100000C, 0); struct.pack_into('<II', data, 512 + 4, 0x0100000C, 0)
                if kind == 'twoARM':
                    struct.pack_into('>II', data, second, 0x0100000C, 1); struct.pack_into('<II', data, 512 + 4, 0x0100000C, 1)
                if kind == 'missingARM':
                    struct.pack_into('>I', data, 8, 0x01000007); struct.pack_into('<I', data, 128 + 4, 0x01000007)
                if kind == 'overlap': struct.pack_into('>Q', data, 8 + 16, len(data) - 128)
                if kind == 'tableOverlap': struct.pack_into('>Q', data, 8 + 8, 16)
                if kind == 'range': struct.pack_into('>Q', data, 8 + 8, 2**63)
                if kind == 'alignment': struct.pack_into('>I', data, second + 24, 10)
                if kind == 'cpuMismatch': struct.pack_into('<I', data, 128 + 4, 0x01000007)
                if kind == 'wrongKind': struct.pack_into('<I', data, 128 + 12, 8)
                if kind == 'device':
                    arm = macho(b'synthetic ARM64 executable', platform=2); data[128:128 + len(arm)] = arm
                if kind == 'count': struct.pack_into('>I', data, 4, 17)
                if kind == 'truncated': data = data[:40]
                if kind == 'reserved': struct.pack_into('>I', data, second + 28, 1)
                self.assertFalse(replay.runner_macho(lambda offset, amount: bytes(data[offset:offset + amount]), len(data)))

    def test_generated_apple_ui_plan_binds_only_frozen_target_and_preserves_runner_and_root(self):
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); plan, runner, _ = generated(root)
                app = root / 'CustomeOptionsHost.app'; app.mkdir(); (app / 'Info.plist').write_bytes(plistlib.dumps(retained_fixture.metadata()))
                if version == 2:
                    plan = {'__xctestrun_metadata__': {'FormatVersion': 2}, 'TestConfigurations': [{'TestTargets': [replay.target(plan)]}]}
                before = copy.deepcopy(plan); destination = root / 'retained.xctestrun'
                bound = replay.bind_plan(plan, root, app, destination)
                self.assertEqual(plan, before); self.assertEqual(replay.target(bound)['TestHostPath'], replay.target(before)['TestHostPath'])
                self.assertEqual(replay.target(bound)['TestBundlePath'], replay.target(before)['TestBundlePath'])
                self.assertEqual(replay.target(bound)['TestingEnvironmentVariables'], replay.target(before)['TestingEnvironmentVariables'])
                self.assertEqual(replay.target(bound)['UITargetAppPath'], str(app))
                self.assertEqual(replay.generated_products(bound, root)[0], runner)
                self.assertEqual(replay.read_plan(destination), bound)

    def test_plan_adds_only_frozen_app_dependency_when_apple_omits_optional_dependency_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); plan, _, _ = generated(root)
            replay.target(plan).pop('DependentProductPaths')
            before = copy.deepcopy(plan)
            app = root / 'Frozen.app'; app.mkdir(); (app / 'Info.plist').write_bytes(plistlib.dumps(retained_fixture.metadata()))
            bound = replay.bind_plan(plan, root, app, root / 'retained.xctestrun')
            self.assertEqual(replay.target(bound)['DependentProductPaths'], [str(app)])
            for key, value in replay.target(before).items(): self.assertEqual(replay.target(bound)[key], value)
            self.assertEqual(replay.target(bound)['UITargetAppPath'], str(app))

    def test_wrong_plan_identity_filter_application_product_or_outside_path_is_rejected(self):
        for kind in ('unit', 'module', 'hostID', 'filter', 'destination', 'outside', 'existingApp', 'deviceCode', 'symlink', 'multiple'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); plan, runner, bundle = generated(root); item = replay.target(plan)
                app = root / 'Frozen.app'; app.mkdir(); (app / 'Info.plist').write_bytes(plistlib.dumps(retained_fixture.metadata()))
                if kind == 'unit': item['IsUITestBundle'] = False
                if kind == 'module': item['ProductModuleName'] = 'Unrelated'
                if kind == 'hostID': item['TestHostBundleIdentifier'] = replay.retained.BUNDLE
                if kind == 'filter': item['SkipTestIdentifiers'] = ['testRetainedCustomeOptionsRoot']
                if kind == 'destination': item['UseDestinationArtifacts'] = True
                if kind == 'outside': item['TestHostPath'] = '/private/unrelated.app'
                if kind == 'existingApp': item['UITargetAppPath'] = '__TESTROOT__/Unrelated.app'
                if kind == 'deviceCode': (bundle / replay.driver.TARGET).write_bytes(macho(b'device', kind=8, platform=2))
                if kind == 'symlink': (bundle / 'Info.plist').unlink(); (bundle / 'Info.plist').symlink_to(runner / 'Info.plist')
                if kind == 'multiple': plan['Other'] = copy.deepcopy(item)
                with self.assertRaises(ValueError): replay.bind_plan(plan, root, app, root / 'retained.xctestrun')

    def test_exact_one_case_must_start_and_pass_once_with_no_extra_or_skipped_cases(self):
        self.assertTrue(replay.case_results(case_log())['all_expected_passed'])
        for data in (b'', case_log('failed'), case_log('skipped'), case_log() + case_log(),
                     case_log().splitlines(keepends=True)[1], case_log() + b"Test Case '-[Other testUnrelated]' started.\n"):
            with self.subTest(data=data): self.assertFalse(replay.case_results(data)['all_expected_passed'])

    def test_prefixed_and_merged_native_case_events_cannot_hide_extra_or_failed_outcomes(self):
        prefix = b'2026-11-06 10:00:00 XCTest: '
        self.assertTrue(replay.case_results(prefix + case_log().replace(b'\n', b' '))['all_expected_passed'])
        extra = prefix + b"Test Case '-[Other testUnrelated]' started. Test Case '-[Other testUnrelated]' passed.\n"
        self.assertFalse(replay.case_results(case_log() + extra)['all_expected_passed'])
        self.assertFalse(replay.case_results(case_log() + b"Test Case '-[Unexpected.Nested.Type testUnrelated]' started.\n")['all_expected_passed'])
        failed = prefix + case_log('failed').splitlines(keepends=True)[1]
        result = replay.case_results(case_log() + failed)
        self.assertFalse(result['all_expected_passed']); self.assertTrue(result['duplicate_or_unstarted_outcome'])
        self.assertEqual(result['failed'], 1)

    def test_attachment_parser_requires_one_safe_named_png_and_bounded_realistic_geometry(self):
        for kind in (None, 'traversal', 'duplicate', 'symlink', 'crc', 'wrongName', 'headerOnly', 'truncated', 'trailing'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); image = root / 'native.png'; image.write_bytes(png())
                manifest = [{'configuredName': 'retained-root', 'exportedFileName': 'native.png'}]
                if kind == 'traversal': manifest[0]['exportedFileName'] = '../outside.png'
                if kind == 'duplicate': manifest.append(dict(manifest[0]))
                if kind == 'symlink': image.unlink(); image.symlink_to(root / 'missing.png')
                if kind == 'crc': image.write_bytes(png()[:-4] + b'\0\0\0\0')
                if kind == 'headerOnly': image.write_bytes(png()[:33])
                if kind == 'truncated': image.write_bytes(png()[:-12])
                if kind == 'trailing': image.write_bytes(png() + b'private trailing bytes')
                if kind == 'wrongName': manifest[0]['configuredName'] = 'private unrelated attachment'
                (root / 'manifest.json').write_text(json.dumps(manifest))
                if kind:
                    with self.assertRaises(ValueError): replay.screenshot_attachment(root)
                else: self.assertEqual(replay.screenshot_attachment(root), (image, {'width': 1179, 'height': 2556}))

    def test_complete_mocked_native_protocol_preserves_frozen_host_and_runs_one_case(self):
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'), \
                patch.dict(os.environ, {'GH_TOKEN': 'PRIVATE-CANARY'}):
            native = Native(root); report = replay.execute(root=root, command=native, popen=native.popen)
            self.assertTrue(report['passed']); self.assertEqual(report['new_xctest_cases_executed'], 1)
            self.assertEqual(report['native_ui_timeout_seconds'], 600)
            self.assertEqual(report['installation_strategy'], 'appleXcodeUITestRunner')
            self.assertFalse(report['installed_host_pre_execution_verified'])
            for key in ('installed_host_verified', 'post_installed_host_verified', 'source_host_unchanged', 'cleanup_passed', 'recording_available', 'driver_codesign_verified'):
                self.assertTrue(report[key])
            for key in ('application_recompiled', 'renderer_recompiled', 'logged_in_settings_verified', 'physical_device_verified', 'release_qualified'):
                self.assertFalse(report[key])
            self.assertEqual(report['historical_controller_cases_passed'], 8); self.assertEqual(report['historical_interaction_cases_passed'], 1)
            self.assertEqual(report['screenshot_origin'], 'xctestAttachment')
            self.assertEqual(len([a for a, _ in native.calls if a[:2] == ['xcodebuild', 'test-without-building']]), 1)
            self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'launch'] for a, _ in native.calls))
            self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'install'] for a, _ in native.calls))
            commands = [item['command'] for item in report['commands']]
            self.assertLess(commands.index('nativeUITest'), commands.index('postInstalled_signature'))
            self.assertLess(commands.index('postInstalled_signature'), commands.index('deleteOwnedSimulator'))
            self.assertEqual(json.loads((output / 'case-results.json').read_text())['passed'], 1)
            for name in ('result.json', 'xctest.log', 'case-results.json', 'driver-compiler.log'):
                text = (output / name).read_text()
                for private in ('PRIVATE-CANARY', '/Users/private', str(root), retained_fixture.IDENTIFIER): self.assertNotIn(private, text)

    def test_only_native_ui_budget_changes_and_per_command_elapsed_is_measured_separately_from_status(self):
        ticks = iter(index / 8 for index in range(1000))
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Darwin'), \
                patch.object(replay.time, 'monotonic', side_effect=lambda: next(ticks)):
            native = Native(root); report = replay.execute(root=root, command=native, popen=native.popen)
            self.assertTrue(report['passed']); self.assertEqual(report['native_ui_timeout_seconds'], 600)
            self.assertTrue(all(item['elapsed_ms'] == 125 for item in report['commands']))
            operations = {args[2]: timeout for args, timeout in native.calls if args[:2] == ['xcrun', 'simctl']}
            self.assertEqual(operations['boot'], 120); self.assertEqual(operations['bootstatus'], 240)
            self.assertEqual(operations['shutdown'], 30); self.assertEqual(operations['delete'], 30)
            self.assertEqual([timeout for args, timeout in native.calls if args[:2] == ['xcodebuild', 'build-for-testing']], [180])
            self.assertEqual([timeout for args, timeout in native.calls if args[:2] == ['xcodebuild', 'test-without-building']], [600])

    def test_native_failures_mutations_and_missing_attachment_never_pass(self):
        for kind in ('compile', 'plan', 'driverSignature', 'caseFailed', 'missingCase', 'timeout', 'export', 'sourceMutate', 'installedMutate', 'installedSignature', 'missingInstalled', 'runnerMutate', 'cleanup', 'recordSpawn'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                with self.assertRaises(ValueError): replay.execute(root=root, command=native, popen=native.popen)
                result = json.loads((output / 'result.json').read_text()); self.assertFalse(result['passed'])
                self.assertFalse(result['root_title_automatically_verified'])
                if kind in ('compile', 'plan', 'driverSignature'):
                    self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'create'] for a, _ in native.calls))
                else:
                    self.assertTrue(any(a[:3] == ['xcrun', 'simctl', 'delete'] for a, _ in native.calls))
                if kind == 'export': self.assertEqual(result['screenshot_origin'], 'ownedSimulatorDiagnostic')
                if kind in ('missingInstalled', 'installedMutate', 'installedSignature'):
                    self.assertFalse(result['installed_host_verified']); self.assertFalse(result['post_installed_host_verified'])
                self.assertFalse(result['installed_host_pre_execution_verified'])
                self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'install'] for a, _ in native.calls))
                self.assertNotIn('PRIVATE-CANARY', (output / 'result.json').read_text())

    def test_shutdown_timeout_does_not_fake_successful_shutdown_when_delete_and_absence_are_proven(self):
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'shutdownOnly'); result = replay.execute(root=root, command=native, popen=native.popen)
            self.assertTrue(result['passed']); self.assertTrue(result['cleanup_passed'])
            self.assertEqual(result['cleanup_results'], {'shutdown_completed': False, 'delete_command_completed': True, 'owned_uuid_absent': True})

    def test_native_test_failure_remains_cli1_even_with_preserved_screenshot_video_and_cleanup(self):
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'caseFailed'); capture = io.StringIO(); execute = replay.execute
            def invoke(destination): return execute(destination, root=root, command=native, popen=native.popen)
            with patch.object(replay, 'execute', side_effect=invoke), contextlib.redirect_stdout(capture):
                code = replay.main(['run', '--output', str(output)])
            result = json.loads((output / 'result.json').read_text())
            self.assertEqual(code, 1); self.assertFalse(result['passed']); self.assertFalse(result['root_title_automatically_verified'])
            self.assertTrue(result['cleanup_passed']); self.assertTrue(result['diagnostic_root_captured']); self.assertTrue(result['recording_available'])
            self.assertEqual(result['new_xctest_cases_executed'], 1)
            self.assertEqual(json.loads(capture.getvalue()), {'diagnostic_role': replay.ROLE, 'passed': False, 'error': 'native_ui_replay_rejected'})

    def test_malformed_xml_plan_and_external_destination_are_closed_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); bad = root / 'bad.plist'; bad.write_bytes(b'<plist><dict>PRIVATE-CANARY</broken>')
            with self.assertRaisesRegex(replay.DriverError, '^ui_plan_invalid$'): replay.read_plan(bad)
            products = root / 'products'; products.mkdir(); plan, _, _ = generated(products)
            app = root / 'Frozen.app'; app.mkdir(); (app / 'Info.plist').write_bytes(plistlib.dumps(retained_fixture.metadata()))
            with self.assertRaisesRegex(replay.DriverError, '^ui_plan_destination_invalid$'):
                replay.bind_plan(plan, products, app, root / 'wrong-root.xctestrun')

    def test_stale_source_or_linux_replay_never_attempts_native_commands(self):
        for kind in ('stale', 'linux'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _):
                command = Mock()
                if kind == 'stale':
                    proof = json.loads((output / 'verification.json').read_text()); proof['source_commit'] = 'f' * 40
                    (output / 'verification.json').write_text(json.dumps(proof))
                with patch.object(replay.platform, 'system', return_value='Linux' if kind == 'linux' else 'Darwin'), self.assertRaises(ValueError):
                    replay.execute(root=root, command=command)
                command.assert_not_called()


if __name__ == '__main__': unittest.main()
