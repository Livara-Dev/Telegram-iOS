# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable plan/ownership tests, never an Xcode compilation claim."""
import copy
import json
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modern_projection_plan as modern
import test_projection_replay as fixtures

EXPECTED_ENTITLEMENTS = {'get-task-allow': True}


def binary(kind):
    return fixtures.macho(b'private-synthetic-machine-code', kind=kind)


def bundle(path, identifier, executable, kind):
    path.mkdir(parents=True)
    (path / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier, 'CFBundleExecutable': executable}))
    (path / executable).write_bytes(binary(kind)); (path / executable).chmod(0o700)
    return path


def apple_fixture(root, version=1, executable=True, sibling=False):
    app = bundle(root / 'Debug-iphonesimulator' / (modern.CONTROL_APP + '.app'), modern.CONTROL_APP_ID, modern.CONTROL_APP, 2)
    test = bundle((app.parent if sibling else app / 'PlugIns') / (modern.CONTROL_TEST + '.xctest'), modern.CONTROL_TEST_ID, modern.CONTROL_TEST, 8)
    host_path = '__TESTROOT__/' + app.relative_to(root).as_posix() + ('/' + modern.CONTROL_APP if executable else '')
    test_path = '__TESTROOT__/' + test.relative_to(root).as_posix() if sibling else '__TESTHOST__/PlugIns/' + test.name
    runner = {'ProductModuleName': modern.CONTROL_TEST, 'TestHostPath': host_path, 'TestBundlePath': test_path,
              'TestHostBundleIdentifier': modern.CONTROL_APP_ID, 'IsAppHostedTestBundle': True,
              'DependentProductPaths': ['__TESTROOT__/' + app.relative_to(root).as_posix(), test_path],
              'TestingEnvironmentVariables': {'XCInjectBundleInto': '__TESTHOST__/' + modern.CONTROL_APP,
                   'DYLD_INSERT_LIBRARIES': '__PLATFORMS__/iPhoneSimulator.platform/Developer/usr/lib/libXCTestBundleInject.dylib'}}
    if version == 1: plan = {modern.CONTROL_TEST: runner}
    else: plan = {'__xctestrun_metadata__': {'FormatVersion': 2}, 'TestConfigurations': [{'Name': 'Debug', 'TestTargets': [runner]}]}
    return plan, app, test


def captured_app(root):
    app = bundle(root / 'ProjectionTestHost.app', modern.products.BUNDLE, 'ProjectionTestHost', 2)
    bundle(app / 'PlugIns' / modern.products.TEST, 'org.livara.ayugram.tests.native-security.projectionTests', 'ChatLocalTextFilterProjectionTests', 8)
    (app / 'unchanged-resource.data').write_bytes(b'captured-private-resource-canary')
    return app


def complete(data=b'', code=0):
    return {'status': 'completed' if code == 0 else 'commandFailed', 'exit_code': code,
            'timeout': False, 'output_limit': False, 'captured_bytes': len(data)}, data


def control_log(outcome='passed'):
    key = modern.CONTROL_CASE.replace('.', ' ')
    return ("Test Case '-[" + key + "]' started.\nTest Case '-[" + key + "]' " + outcome + ' (0.001 seconds).\n').encode()


IDENTIFIER = '11111111-2222-3333-4444-555555555555'


def owned_fixture(home):
    data = home / 'Library/Developer/CoreSimulator/Devices' / IDENTIFIER / 'data'
    data.parent.mkdir(parents=True)
    (data.parent / 'device.plist').write_bytes(plistlib.dumps({'UDID': IDENTIFIER, 'name': 'owned-control',
        'deviceType': modern.DEVICE_TYPE, 'runtime': modern.RUNTIME}))
    result = {}
    for role, relative in (('data', 'Containers/Data/Application'), ('app', 'Containers/Bundle/Application')):
        parent = data / relative / 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'; parent.mkdir(parents=True)
        (parent / '.com.apple.mobile_container_manager.metadata.plist').write_bytes(
            plistlib.dumps({'MCMMetadataIdentifier': modern.CONTROL_APP_ID}))
        result[role] = parent if role == 'data' else bundle(parent / (modern.CONTROL_APP + '.app'),
            modern.CONTROL_APP_ID, modern.CONTROL_APP, 2)
    return result


class ModernPlanTests(unittest.TestCase):
    def test_observed_v1_bundle_and_v2_executable_models_are_preserved_without_changing_capture(self):
        for version, executable, sibling in [(1, False, False), (2, True, False), (2, True, True)]:
            with self.subTest(version=version, executable=executable, sibling=sibling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); products = root / 'Products'
                plan, _, _ = apple_fixture(products, version, executable, sibling)
                capture = captured_app(root / 'capture')
                before = modern.projection.snapshot_tree(capture)
                path, report = modern.bind_plan(plan, products, capture, root / 'modern')
                bound = modern._read_plan(path); _, target = modern.target(bound)
                self.assertEqual(report['apple_format_version'], version)
                self.assertEqual(report['apple_host_path_kind'], 'executable' if executable else 'bundle')
                self.assertEqual(report['test_bundle_has_separate_product_copy'], sibling)
                self.assertEqual(target['TestHostBundleIdentifier'], modern.products.BUNDLE)
                self.assertEqual(target['ProductModuleName'], 'ChatLocalTextFilterProjectionTests')
                self.assertEqual(modern.projection.snapshot_tree(capture), before)
                self.assertTrue(report['host_and_test_bytes_unchanged'])
                self.assertFalse(report['modern_plan_executed']); self.assertFalse(report['original_plan_executed'])
                self.assertNotIn('AyuProjectionControl', plistlib.dumps(bound).decode())
                for forbidden in ('OnlyTestIdentifiers', 'SkipTestIdentifiers'): self.assertNotIn(forbidden, plistlib.dumps(bound).decode())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_generated_host_semantics_require_real_products_and_exact_identity(self):
        for kind in ('identifier', 'module', 'host', 'bundle', 'platform'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); plan, app, test = apple_fixture(root)
                _, target = modern.target(plan)
                if kind == 'identifier': target['TestHostBundleIdentifier'] = 'private-canary'
                elif kind == 'module': target['ProductModuleName'] = 'private-canary'
                elif kind == 'host': target['TestHostPath'] += '/unverified-file'
                elif kind == 'bundle': target['TestBundlePath'] += '-other'
                else: (test / modern.CONTROL_TEST).write_bytes(fixtures.macho(b'wrong-platform', kind=8, platform=2))
                with self.assertRaises(ValueError): modern.generated_products(plan, root)

    def test_unknown_format_multiple_targets_ui_mode_or_selection_filters_reject_before_copy(self):
        for kind in ('format', 'targets', 'ui', 'only', 'skip'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); plan, _, _ = apple_fixture(root / 'Products', 2)
                _, target = modern.target(plan)
                if kind == 'format': plan['__xctestrun_metadata__']['FormatVersion'] = 3
                elif kind == 'targets': plan['TestConfigurations'][0]['TestTargets'].append(copy.deepcopy(target))
                elif kind == 'ui': target['IsUITestBundle'] = True
                else: target['OnlyTestIdentifiers' if kind == 'only' else 'SkipTestIdentifiers'] = ['OldTests/testCase']
                capture = captured_app(root / 'capture')
                with self.assertRaises(ValueError): modern.bind_plan(plan, root / 'Products', capture, root / 'bound')
                self.assertFalse((root / 'bound').exists())

    def test_documented_optional_hosted_flag_can_be_absent_but_explicit_false_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); plan, _, _ = apple_fixture(root / 'Products', 2)
            _, runner = modern.target(plan); runner.pop('IsAppHostedTestBundle')
            capture = captured_app(root / 'capture')
            _, metadata = modern.bind_plan(plan, root / 'Products', capture, root / 'modern')
            self.assertFalse(metadata['apple_supplies_app_hosted_flag'])
            runner['IsAppHostedTestBundle'] = False
            with self.assertRaises(modern.PlanError): modern.target(plan)

    def test_product_paths_cannot_escape_link_or_use_guessed_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); inside = root / 'Products'; inside.mkdir()
            outside = root / 'outside'; outside.mkdir(); (inside / 'link').symlink_to(outside, target_is_directory=True)
            for value in ('__TESTROOT__/../outside', '__TESTROOT__/link/private', '__UNPROVEN__/private', str(outside), 'relative-path'):
                with self.subTest(value=value), self.assertRaises(ValueError): modern.resolved_path(value, inside)

    def test_approved_root_double_underscores_are_not_treated_as_unresolved_plan_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'ordinary__PRIVATE__approved'
            root.mkdir()
            plan, app, _ = apple_fixture(root, version=2, executable=False, sibling=True)
            version, _, resolved, _, kind = modern.generated_products(plan, root)
            self.assertEqual((version, resolved, kind), (2, app, 'bundle'))
            for value in ('__TESTROOT__/__UNPROVEN__/item', '__OTHER__/item', '__unverified__/item'):
                with self.subTest(value=value), self.assertRaises(modern.PlanError):
                    modern.resolved_path(value, root)

    def test_one_control_requires_exact_start_and_pass_without_retry_unknown_skip_or_partial_outcome(self):
        self.assertTrue(modern.control_cases(control_log())['all_expected_passed'])
        for data in (b'', control_log('failed'), control_log('skipped'), control_log('crashed'), control_log() * 2,
                     control_log().replace(b'AyuProjectionControlTests', b'OtherTests'),
                     control_log().splitlines()[1] + b'\n', control_log().splitlines()[0] + b'\n'):
            with self.subTest(data=data[:30]): self.assertFalse(modern.control_cases(data)['all_expected_passed'])

    def test_source_guard_precedes_authenticated_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); api, fetch = Mock(), Mock()
            with patch.object(modern.projection, 'verify_current_checkout', side_effect=ValueError('private-canary')):
                with self.assertRaises(ValueError): modern.prepare(root=root, api=api, fetch=fetch)
            api.assert_not_called(); fetch.assert_not_called()

    def test_only_private_xml_content_is_accepted_not_merged_codesign_diagnostics(self):
        xml = plistlib.dumps(EXPECTED_ENTITLEMENTS)
        prefix = (b'Executable=/private/control-private-canary/AyuProjectionControl\n'
                  b"Warning: Specifying ':' in the path is deprecated and will not work in a future release\n")
        self.assertEqual(modern.entitlement_document(xml), EXPECTED_ENTITLEMENTS)
        for data in (prefix + xml, prefix + xml + xml, b'private-unrecognized-prefix\n' + xml, xml + b'private-tail',
                     b'<?xml version="1.0"?><plist><dict><key>private-canary</key>',
                     b'x' * 65537):
            with self.assertRaises(modern.PlanError): modern.entitlement_document(data)
        for value in ({'get-task-allow': 1}, {'get-task-allow': False}, {'get-task-allow': True, 'extra': True}):
            self.assertFalse(modern.typed_equal(EXPECTED_ENTITLEMENTS, modern.entitlement_document(plistlib.dumps(value))))
        self.assertFalse(modern.typed_equal({'nested': [True]}, {'nested': [1]}))

    def test_signature_readback_requires_bounded_regular_independent_file_and_sets_private_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); xml = plistlib.dumps(EXPECTED_ENTITLEMENTS)
            good = root / 'readback.plist'; good.write_bytes(xml); good.chmod(0o644)
            self.assertEqual(modern.private_bytes(good), xml)
            link = root / 'link'; link.symlink_to(good)
            hardlink = root / 'hardlink'; os.link(good, hardlink)
            empty = root / 'empty'; empty.write_bytes(b'')
            oversized = root / 'oversized'; oversized.write_bytes(b'x' * 65537)
            fifo = root / 'fifo'; os.mkfifo(fifo)
            for path in (root / 'missing', root, link, hardlink, empty, oversized, fifo):
                with self.assertRaises(modern.PlanError): modern.private_bytes(path)

    def execute_fixture(self, root, control_outcome='passed', mutate=False, readback='correct', entitlements=None, warming='correct',
                        test_entitlements=None, test_readback='missing', host_input=True, device_metadata='correct', boot_status=0,
                        projection_outcome='passed', cleanup_failure=False, mutate_control=False, repeat_uuid=False):
        output = root / modern.OUTPUT; output.mkdir(parents=True)
        capture = fixtures.captured(root, current=True)
        info = capture / 'TEST_ROOT' / modern.products.APP / 'PlugIns' / modern.products.TEST / 'Info.plist'
        value = plistlib.loads(info.read_bytes())
        value['CFBundleIdentifier'] = 'org.livara.ayugram.tests.native-security.projectionTests'
        info.write_bytes(plistlib.dumps(value)); fixtures.ProjectionReplayTests.refresh(capture)
        shutil.copytree(capture, output / 'private-products')
        calls = []; created_ids = []; self.fixture_options = []; entitlements = entitlements or EXPECTED_ENTITLEMENTS
        installed, data_root = root / 'owned-device/installed.app', root / 'owned-device/data-container'
        data_root.mkdir(parents=True)
        def command(args, timeout=30, **options):
            calls.append((args, timeout))
            self.fixture_options.append((args, options))
            if args == ['xcodebuild', '-version']: return complete(b'Xcode 27.1\nBuild version 27A9275\n')
            if '--show-sdk-version' in args: return complete(b'27.1')
            if args[:3] == ['/usr/bin/codesign', '--display', '--verbose=4']:
                identifier = modern.CONTROL_APP_ID if Path(args[-1]).suffix == '.app' else modern.CONTROL_TEST_ID
                return complete(('Identifier=' + identifier + '\nSignature=adhoc\nTeamIdentifier=not set\n').encode())
            if args[:3] == ['/usr/bin/codesign', '--display', '--entitlements']:
                path = Path(args[3]); self.assertFalse(path.exists()); self.assertEqual(args[4], '--xml')
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                xml = plistlib.dumps(entitlements)
                if Path(args[-1]).suffix != '.app':
                    if test_readback == 'empty': path.write_bytes(b'')
                    elif test_readback == 'xml': path.write_bytes(plistlib.dumps(test_entitlements or {}))
                    return complete()
                if not host_input and readback == 'correct': return complete()
                if readback == 'correct': path.write_bytes(xml)
                elif readback == 'wrong': path.write_bytes(plistlib.dumps({'get-task-allow': False}))
                elif readback == 'malformed': path.write_bytes(b'<?xml version="1.0"?><plist><dict><key>private-canary</key>')
                elif readback == 'symlink': path.symlink_to(output / 'private-derived/Build/Intermediates.noindex/control.app.xcent')
                elif readback == 'missing': pass
                elif readback == 'command_failed': return complete(b'private-command-canary', 1)
                elif readback == 'timed_out': return {'status': 'timedOut', 'exit_code': None, 'timeout': True,
                    'output_limit': False, 'captured_bytes': 0}, b''
                else: raise AssertionError('Unknown readback fixture')
                # A valid stdout plist is deliberately ignored: evidence must
                # come from the requested signature-output file, independently.
                return complete(b'Executable=/private/readback-private-canary\n' + xml)
            if 'build-for-testing' in args:
                products = Path(args[args.index('-derivedDataPath') + 1]) / 'Build/Products'
                plan, app, test = apple_fixture(products, 2)
                (products / 'control.xctestrun').write_bytes(plistlib.dumps(plan))
                intermediates = products.parent / 'Intermediates.noindex'; intermediates.mkdir()
                host_path = intermediates / (modern.CONTROL_APP + ('.app-Simulated.xcent' if host_input == 'simulated' else '.app.xcent'))
                if host_input: host_path.write_bytes(plistlib.dumps(entitlements))
                unused_name = modern.CONTROL_APP + ('.app.xcent' if host_input == 'simulated' else '.app-Simulated.xcent')
                (intermediates / unused_name).write_bytes(plistlib.dumps({'different-variant': True}))
                if test_entitlements is not None:
                    test_path = intermediates / (modern.CONTROL_TEST + '.xctest.xcent')
                    test_path.write_bytes(plistlib.dumps(test_entitlements))
                commands = []
                for product, explicit in ((app, host_path if host_input else None), (test, test_path if test_entitlements is not None else None)):
                    arguments = ['/usr/bin/codesign', '--force', '--sign', '-']
                    if explicit is not None: arguments += ['--entitlements', str(explicit)]
                    commands.append(shlex.join(arguments + ['--timestamp=none', '--generate-entitlement-der', str(product)]))
                return complete(('\n'.join(commands) + '\n').encode())
            elif args[:3] == ['xcrun', 'simctl', 'create']:
                identifier = IDENTIFIER if not created_ids or repeat_uuid else '66666666-7777-8888-9999-AAAAAAAAAAAA'
                created_ids.append(identifier)
                device = root / 'Library/Developer/CoreSimulator/Devices' / identifier; device.mkdir(parents=True, exist_ok=True)
                metadata = device / 'device.plist'
                value = {'UDID': identifier, 'name': args[3], 'deviceType': modern.DEVICE_TYPE, 'runtime': modern.RUNTIME}
                if warming == 'wrong_identity': value['name'] = 'other'
                if device_metadata in {'unsupported', 'fallback_timeout'}: value = {'UDID': identifier}
                if device_metadata == 'partial_mismatch': value = {'UDID': '00000000-0000-0000-0000-000000000000'}
                if device_metadata != 'missing': metadata.write_bytes(plistlib.dumps(value))
                return complete(identifier.encode())
            elif args[:3] == ['xcrun', 'simctl', 'bootstatus']:
                return complete(code=boot_status)
            elif args[:4] == ['xcrun', 'simctl', 'list', 'devices']:
                if device_metadata == 'fallback_timeout':
                    return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                name = next(value[0][3] for value in reversed(calls) if value[0][:3] == ['xcrun', 'simctl', 'create'])
                return complete(json.dumps({'devices': {modern.RUNTIME: [{'udid': created_ids[-1],
                    'name': name if warming != 'wrong_identity' else 'other', 'state': 'Booted' if warming != 'not_booted' else 'Shutdown', 'isAvailable': True,
                    'deviceTypeIdentifier': modern.DEVICE_TYPE}]}}).encode())
            elif args[:2] == ['xcrun', 'simctl'] and args[2] in {'install', 'launch', 'terminate'}:
                raise AssertionError('The native XCTest command owns installation and launch')
            elif args[:2] == ['xcrun', 'simctl'] and args[2] in {'shutdown', 'delete'} and cleanup_failure and len(created_ids) == 1:
                return complete(code=1)
            elif 'test-without-building' in args:
                if sum('test-without-building' in args for args, _ in calls) == 1:
                    if mutate_control: (output / 'private-products/capture.json').write_bytes(b'private-corruption-before-projection')
                    if control_outcome == 'timeout':
                        return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                    return complete(control_log(control_outcome), 0 if control_outcome == 'passed' else 65)
                if mutate: (output / 'private-products/capture.json').write_bytes(b'private-corruption-canary')
                return complete(fixtures.log(projection_outcome), 0 if projection_outcome == 'passed' else 65)
            return complete()
        def reseal(original, destination, *args, **kwargs):
            shutil.copytree(original, destination)
            return {'host_code_unchanged': True, 'test_tree_unchanged': True}
        original = output / 'private-products'
        with patch.object(modern, 'MANIFEST_SHA256', modern.projection.digest(original / 'manifest.json')), \
             patch.object(modern, 'CAPTURE_SHA256', modern.projection.digest(original / 'capture.json')), \
             patch.object(modern.projection, 'verify_current_checkout'), patch.object(modern.projection, 'prepare_control', side_effect=reseal), \
             patch.object(modern, 'control_containers', return_value={'app': installed, 'data': data_root}), \
             patch.object(modern.Path, 'home', return_value=root):
            proof = modern.verification(original, modern.projection.validate_products(original, modern.SOURCE))
            modern.projection.write(output / 'verification.json', proof)
            report = modern.run(root=root, command=command, probe=lambda *args, **kwargs: None)
        return report, calls

    def test_failed_diagnostic_control_preserves_failure_and_still_runs_all_projection_cases_after_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); result, calls = self.execute_fixture(root, 'failed')
            self.assertFalse(result['passed']); self.assertEqual(result['control']['started'], 1)
            self.assertEqual(result['control']['passed'], 0); self.assertTrue(result['projection']['attempted'])
            self.assertEqual(result['projection']['passed'], 41); self.assertEqual(result['control']['failure'], 'tiny_hosted_control_not_passed')
            self.assertEqual(sum('test-without-building' in args for args, _ in calls), 2)
            self.assertEqual(sum(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls), 2)
            self.assertTrue(result['control']['cleanup_completed']); self.assertTrue(result['original_products_unchanged'])

    def test_control_pass_then_exact_41_single_attempt_preserves_source_and_runtime_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.execute_fixture(Path(directory).resolve())
            self.assertTrue(result['passed']); self.assertEqual(result['control']['passed'], 1)
            self.assertEqual(result['schema'], 2)
            self.assertEqual(result['tiny_control_entitlement_readback'], 'matchedActualCodeSignInput')
            self.assertEqual(result['projection']['started'], 41); self.assertEqual(result['projection']['passed'], 41)
            self.assertTrue(result['original_products_unchanged']); self.assertTrue(result['projection']['cleanup_completed'])
            self.assertEqual([timeout for args, timeout in calls if 'test-without-building' in args], [300, 300])
            self.assertEqual(sum('build-for-testing' in args for args, _ in calls), 1)
            for key in ('application_recompiled', 'projection_tests_recompiled', 'physical_device_verified', 'release_qualified'):
                self.assertFalse(result[key])
            self.assertFalse(any('bazel' in ' '.join(args).lower() for args, _ in calls))

    def test_missing_unsafe_false_or_malformed_signature_output_blocks_all_tests_despite_valid_input_and_stdout(self):
        for readback in ('missing', 'symlink', 'wrong', 'malformed', 'command_failed', 'timed_out'):
            with self.subTest(readback=readback), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); result, calls = self.execute_fixture(root, readback=readback)
                self.assertFalse(result['passed']); self.assertFalse(result['tiny_control_signing_verified'])
                self.assertEqual(result['tiny_signing']['host']['readback'], {
                    'missing': 'generatedSignatureMismatch', 'symlink': 'unsafeOrInvalidFile', 'wrong': 'generatedSignatureMismatch',
                    'malformed': 'unsafeOrInvalidFile', 'command_failed': 'commandFailed', 'timed_out': 'commandFailed'}[readback])
                self.assertFalse(result['control']['attempted']); self.assertFalse(result['projection']['attempted'])
                self.assertFalse(any('test-without-building' in args for args, _ in calls))
                self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls))
                self.assertNotIn('private-canary', json.dumps(result))
                self.assertNotIn('private-command-canary', json.dumps(result))

    def test_post_execution_source_mutation_never_leaves_a_passed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.execute_fixture(Path(directory).resolve(), mutate=True)
            self.assertEqual(result['projection']['passed'], 41)
            self.assertFalse(result['original_products_unchanged']); self.assertFalse(result['passed'])

    def test_native_xcode_signing_owns_default_install_and_launch_on_two_distinct_verified_devices(self):
        for generated in ({'get-task-allow': True}, {'com.apple.security.get-task-allow': True}):
            with self.subTest(generated=generated), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), entitlements=generated)
                self.assertTrue(result['passed']); self.assertTrue(result['tiny_signing']['host']['verified'])
                self.assertTrue(result['tiny_signing']['test']['verified'])
                self.assertFalse(result['tiny_signing']['test']['generated_signed_entitlements_present'])
                self.assertFalse(any('--sign' in args or 'sign_control' in args for args, _ in calls))
                build = next(args for args, _ in calls if 'build-for-testing' in args)
                self.assertIn('CODE_SIGN_IDENTITY=-', build); self.assertIn('CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES', build)
                self.assertIn('DEVELOPMENT_TEAM=', build); self.assertNotIn('-allowProvisioningUpdates', build)
                actions = [args[2] for args, _ in calls if args[:2] == ['xcrun', 'simctl']]
                self.assertLess(actions.index('boot'), actions.index('bootstatus'))
                self.assertTrue(all(action not in {'install', 'launch', 'terminate'} for action in actions))
                for role in ('control', 'projection'):
                    self.assertTrue(result[role]['xcode_manages_install_and_launch'])
                    self.assertFalse(result[role]['manual_prewarm_performed']); self.assertTrue(result[role]['device_identity']['identity_verified'])
                identifiers = [args[3] for args, _ in calls if args[:3] == ['xcrun', 'simctl', 'boot']]
                self.assertEqual(len(set(identifiers)), 2)
                observers = [(args, options) for args, options in self.fixture_options if 'bootstrap_diagnostic' in options]
                self.assertEqual(len(observers), 1); self.assertIn('test-without-building', observers[0][0])

    def test_rejected_owned_identity_stops_before_any_test_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.execute_fixture(Path(directory).resolve(), warming='wrong_identity')
            self.assertFalse(result['passed']); self.assertFalse(result['projection']['attempted'])
            self.assertFalse(result['control']['xctest_command_attempted']); self.assertFalse(result['control']['tests_started'])
            self.assertFalse(any('test-without-building' in args for args, _ in calls))
            self.assertTrue(result['control']['cleanup_completed'])

    def test_zero_case_control_timeout_still_attempts_all_41_once_after_proven_cleanup_but_cannot_qualify(self):
        for outcome in ('passed', 'failed'):
            with self.subTest(projection=outcome), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), control_outcome='timeout', projection_outcome=outcome)
                self.assertFalse(result['passed']); self.assertEqual(result['control']['started'], 0)
                self.assertEqual(result['control']['failure'], 'tiny_hosted_control_not_passed')
                self.assertTrue(result['control']['cleanup_completed']); self.assertEqual(result['projection']['started'], 41)
                self.assertEqual(sum('test-without-building' in args for args, _ in calls), 2)
                self.assertEqual(result['projection']['passed'], 41 if outcome == 'passed' else 0)
                creates = [index for index, (args, _) in enumerate(calls) if args[:3] == ['xcrun', 'simctl', 'create']]
                deleted = next(index for index, (args, _) in enumerate(calls) if args[:3] == ['xcrun', 'simctl', 'delete'])
                self.assertLess(deleted, creates[1])

    def test_unverified_control_cleanup_or_changed_original_never_attempts_projection(self):
        for kind in ('cleanup', 'source'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), cleanup_failure=kind == 'cleanup', mutate_control=kind == 'source')
                self.assertFalse(result['passed']); self.assertFalse(result['projection']['attempted'])
                self.assertEqual(sum('test-without-building' in args for args, _ in calls), 1)
                self.assertEqual(sum(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls), 1)
                self.assertEqual(result['failure'], 'tiny_control_cleanup_unverified' if kind == 'cleanup' else 'original_products_changed_before_projection')
                if kind == 'source': self.assertFalse(result['original_products_unchanged'])

    def test_reused_device_uuid_never_runs_the_second_suite_on_the_old_device(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.execute_fixture(Path(directory).resolve(), repeat_uuid=True)
            self.assertFalse(result['passed']); self.assertEqual(result['failure'], 'modern_owned_device_reused')
            self.assertFalse(result['projection']['xctest_command_attempted'])
            self.assertEqual(sum('test-without-building' in args for args, _ in calls), 1)

    def test_actual_signing_arguments_bind_unique_exact_products_and_explicit_input_without_filename_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); derived = root / 'derived'; intermediate = derived / 'Build/Intermediates.noindex'
            intermediate.mkdir(parents=True); app, test = root / 'Real Product.app', root / 'Tests.xctest'
            explicit = intermediate / 'Unpredicted-Simulated.xcent'; explicit.write_bytes(plistlib.dumps(EXPECTED_ENTITLEMENTS))
            unused = intermediate / (app.name + '.xcent'); unused.write_bytes(plistlib.dumps({'unconsumed': True}))
            commands = [shlex.join(['/usr/bin/codesign', '--force', '--sign', '-', '--entitlements', str(explicit), str(app)]),
                        shlex.join(['/usr/bin/codesign', '--force', '--sign', '-', str(test)])]
            report = {'host': {}, 'test': {}}
            inputs = modern.actual_signing_inputs('\n'.join(commands).encode(), derived, app, test, report)
            self.assertEqual(inputs, {'host': EXPECTED_ENTITLEMENTS, 'test': None})
            self.assertEqual(report['host']['code_sign_argv']['entitlement_name_kind'], 'simulatedNamedXcent')
            self.assertEqual(report['test']['code_sign_argv']['entitlement_input_kind'], 'none')
            for text, count in ((commands[1], 0), ('\n'.join(commands + commands[:1]), 2)):
                report = {'host': {}, 'test': {}}
                with self.assertRaises(modern.PlanError): modern.actual_signing_inputs(text.encode(), derived, app, test, report)
                self.assertEqual(report['host']['code_sign_argv']['matching_invocations'], count)
                self.assertEqual(report['host']['code_sign_argv']['entitlement_input_kind'], 'unavailable')
            for extra in (shlex.join(['/usr/bin/env', 'KEY=private-canary', '/usr/bin/codesign', '--sign', '-', str(app)]),
                          shlex.join(['/usr/bin/codesign', '--sign', '-', str(app), 'unrelated.app'])):
                report = {'host': {}, 'test': {}}
                with self.assertRaises(modern.PlanError):
                    modern.actual_signing_inputs(('\n'.join(commands) + '\n' + extra).encode(), derived, app, test, report)
                self.assertFalse(report['code_sign_log_scan_completed'])
                self.assertFalse(report['host']['code_sign_argv']['verified'])
                self.assertNotIn('private-canary', json.dumps(report))

    def test_unknown_duplicate_or_unsafe_codesign_arguments_never_become_no_entitlement_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); derived = root / 'derived'; intermediate = derived / 'Build/Intermediates.noindex'
            intermediate.mkdir(parents=True); app, test = root / 'App.app', root / 'Tests.xctest'
            outside = root / 'outside'; outside.write_bytes(plistlib.dumps(EXPECTED_ENTITLEMENTS))
            linked = intermediate / 'linked'; linked.symlink_to(outside)
            unsafe = [[], ['--sign', 'certificate'], ['--sign', '-', '--sign', '-'],
                ['--sign', '-', '--entitlements'], ['--sign', '-', '--entitlements', str(outside)],
                ['--sign', '-', '--entitlements', str(linked)], ['--sign', '-', '--preserve-metadata=entitlements'],
                ['--sign', '-', '--unexpected-option'], ['--sign', '-', str(test)]]
            for options in unsafe:
                with self.subTest(options=options):
                    lines = [shlex.join(['/usr/bin/codesign', *options, str(app)]),
                        shlex.join(['/usr/bin/codesign', '--sign', '-', str(test)])]
                    report = {'host': {}, 'test': {}}
                    with self.assertRaises(modern.PlanError): modern.actual_signing_inputs('\n'.join(lines).encode(), derived, app, test, report)
                    self.assertFalse(report['host']['code_sign_argv']['verified'])
                    self.assertNotEqual(report['host']['code_sign_argv']['entitlement_input_kind'], 'none')
                    self.assertNotIn(str(root), json.dumps(report))

    def test_actual_no_input_host_requires_zero_rights_and_explicit_simulated_named_input_is_honored(self):
        for host_input in (False, 'simulated'):
            with self.subTest(host_input=host_input), tempfile.TemporaryDirectory() as directory:
                result, _ = self.execute_fixture(Path(directory).resolve(), host_input=host_input)
                self.assertTrue(result['passed'])
                self.assertEqual(result['tiny_signing']['host']['code_sign_argv']['entitlement_input_kind'], 'none' if host_input is False else 'explicitFile')
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.execute_fixture(Path(directory).resolve(), host_input=False, readback='wrong')
            self.assertFalse(result['passed']); self.assertFalse(result['control']['attempted'])

    def test_compiler_log_keeps_both_bounded_redacted_segments_including_final_signing_evidence(self):
        secret = 'private-compiler-canary'
        raw = ('FIRST '+secret+'\n').encode() + b'x' * 70000 + ('\nLAST CodeSign '+secret+'\n').encode()
        value = modern.compiler_log(raw, (secret,))
        self.assertIn('FIRST', value); self.assertIn('LAST CodeSign', value); self.assertNotIn(secret, value)
        self.assertLess(len(json.dumps(value).encode()), 16384)

    def test_exact_owned_uuid_model_runtime_name_and_booted_state_are_all_required(self):
        correct = {'udid': IDENTIFIER, 'name': 'owned', 'state': 'Booted', 'isAvailable': True,
                   'deviceTypeIdentifier': modern.DEVICE_TYPE}
        def payload(row, runtime=modern.RUNTIME): return json.dumps({'devices': {runtime: [row]}}).encode()
        self.assertTrue(modern.verify_owned_device(payload(correct), IDENTIFIER, 'owned'))
        for key, value in (('udid', '00000000-0000-0000-0000-000000000000'), ('name', 'other'),
                           ('state', 'Shutdown'), ('isAvailable', 1), ('deviceTypeIdentifier', 'other')):
            row = {**correct, key: value}
            self.assertFalse(modern.verify_owned_device(payload(row), IDENTIFIER, 'owned'))
        self.assertFalse(modern.verify_owned_device(payload(correct, 'other-runtime'), IDENTIFIER, 'owned'))
        self.assertFalse(modern.verify_owned_device(b'private-json-canary', IDENTIFIER, 'owned'))

    def test_owned_device_metadata_proves_only_exact_identity_and_never_plist_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); owned_fixture(root)
            metadata = root / 'Library/Developer/CoreSimulator/Devices' / IDENTIFIER / 'device.plist'
            original = plistlib.loads(metadata.read_bytes()); original['state'] = 'Shutdown'
            metadata.write_bytes(plistlib.dumps(original))
            with patch.object(modern.projection.subprocess, 'Popen') as popen:
                proof = modern.owned_device_identity(IDENTIFIER, 'owned-control', home=root)
            popen.assert_not_called(); self.assertTrue(proof['identity_verified']); self.assertFalse(proof['readiness_asserted'])
            self.assertNotIn(str(root), json.dumps(proof)); self.assertNotIn(IDENTIFIER, json.dumps(proof))
            for key, wrong in (('UDID', '00000000-0000-0000-0000-000000000000'), ('name', 'other'),
                               ('deviceType', 'other'), ('runtime', 'other'), ('UDID', 1)):
                metadata.write_bytes(plistlib.dumps({key: wrong}))
                proof = modern.owned_device_identity(IDENTIFIER, 'owned-control', home=root)
                self.assertEqual(proof['state'], 'rejected'); self.assertFalse(proof['identity_verified'])

    def test_owned_device_metadata_rejects_unsafe_paths_and_unknown_schema_grants_no_filesystem_identity(self):
        for kind in ('symlink', 'hardlink', 'fifo', 'owner', 'parent_link', 'unknown', 'malformed', 'oversized'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); owned_fixture(root)
                device = root / 'Library/Developer/CoreSimulator/Devices' / IDENTIFIER; metadata = device / 'device.plist'
                if kind == 'symlink':
                    outside = root / 'outside'; metadata.rename(outside); metadata.symlink_to(outside)
                elif kind == 'hardlink': os.link(metadata, root / 'linked-metadata')
                elif kind == 'fifo': metadata.unlink(); os.mkfifo(metadata)
                elif kind == 'parent_link':
                    outside = root / 'outside'; device.rename(outside); device.symlink_to(outside, target_is_directory=True)
                elif kind == 'unknown': metadata.write_bytes(plistlib.dumps({'UDID': IDENTIFIER}))
                elif kind == 'malformed': metadata.write_bytes(b'<?xml version="1.0"?><plist><dict><key>private-metadata-canary</key>')
                elif kind == 'oversized': metadata.write_bytes(b'x' * 65537)
                proof = modern.owned_device_identity(IDENTIFIER, 'owned-control', home=root, uid=os.getuid() + 1 if kind == 'owner' else None)
                self.assertEqual(proof['state'], 'unavailable' if kind in {'unknown', 'malformed', 'oversized'} else 'rejected')
                self.assertFalse(proof['identity_verified']); self.assertFalse(proof['readiness_asserted'])
                self.assertNotIn('private-metadata-canary', json.dumps(proof))

    def test_direct_verified_identity_skips_inventory_rpc_and_unknown_uses_one_bounded_fallback(self):
        for kind in ('correct', 'unsupported', 'missing'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), device_metadata=kind)
                self.assertTrue(result['passed'])
                inventory = [(args, limit) for args, limit in calls if args[:4] == ['xcrun', 'simctl', 'list', 'devices']]
                self.assertEqual(len(inventory), 0 if kind == 'correct' else 2)
                self.assertTrue(all(limit == 15 for _, limit in inventory))
                proof = result['control']['device_identity']
                self.assertFalse(proof['readiness_asserted'])
                if kind == 'correct': self.assertTrue(proof['identity_verified'])
                else: self.assertTrue(result['control']['identity_fallback']['identity_verified'])

    def test_known_partial_metadata_mismatch_never_falls_back_or_installs_and_unknown_timeout_stays_failed(self):
        for kind in ('partial_mismatch', 'fallback_timeout'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), device_metadata=kind)
                self.assertFalse(result['passed']); self.assertFalse(result['control']['xctest_command_attempted'])
                self.assertFalse(result['projection']['attempted']); self.assertTrue(result['control']['cleanup_completed'])
                self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'install'] for args, _ in calls))
                self.assertEqual(sum(args[:4] == ['xcrun', 'simctl', 'list', 'devices'] for args, _ in calls),
                                 0 if kind == 'partial_mismatch' else 1)

    def test_bootstatus_149_still_requires_actual_fresh_booted_inventory_even_with_valid_metadata(self):
        for warming in ('correct', 'not_booted'):
            with self.subTest(warming=warming), tempfile.TemporaryDirectory() as directory:
                result, calls = self.execute_fixture(Path(directory).resolve(), boot_status=149, warming=warming)
                self.assertEqual(result['passed'], warming == 'correct')
                self.assertTrue(any(args[:4] == ['xcrun', 'simctl', 'list', 'devices'] for args, _ in calls))
                if warming != 'correct':
                    self.assertFalse(result['control']['xctest_command_attempted'])
                    self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'install'] for args, _ in calls))

    def test_direct_filesystem_discovery_binds_exact_owned_uuid_and_both_bundle_metadata_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); expected = owned_fixture(root)
            with patch.object(modern.Path, 'home', return_value=root):
                self.assertEqual(modern.control_containers(IDENTIFIER), expected)
                with self.assertRaises(Exception): modern.control_containers('00000000-0000-0000-0000-000000000000')
                metadata = expected['data'] / '.com.apple.mobile_container_manager.metadata.plist'
                metadata.write_bytes(plistlib.dumps({'MCMMetadataIdentifier': 'other-private-canary'}))
                with self.assertRaises(modern.PlanError): modern.control_containers(IDENTIFIER)

    def test_direct_discovery_rejects_foreign_metadata_symlinks_duplicate_containers_and_changed_app_identity(self):
        for kind in ('duplicate', 'symlink', 'identity'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); expected = owned_fixture(root)
                if kind == 'duplicate': shutil.copytree(expected['data'], expected['data'].with_name('duplicate'))
                elif kind == 'symlink':
                    metadata = expected['data'] / '.com.apple.mobile_container_manager.metadata.plist'
                    outside = root / 'outside'; shutil.move(metadata, outside); metadata.symlink_to(outside)
                else: (expected['app'] / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'other', 'CFBundleExecutable': modern.CONTROL_APP}))
                with patch.object(modern.Path, 'home', return_value=root), self.assertRaises(modern.PlanError):
                    modern.control_containers(IDENTIFIER)

    def test_marker_reads_exact_frozen_c_writer_bytes_and_clear_never_follows_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); path = root / modern.tiny.MARKER_RELATIVE; path.parent.mkdir(parents=True)
            self.assertEqual(modern.control_marker(root), {'state': 'absent'})
            for stage in modern.tiny.STAGES:
                path.write_bytes(stage.encode()); self.assertEqual(modern.control_marker(root), {'state': 'observed', 'stage': stage})
            self.assertEqual(modern.control_marker(root, clear=True), {'state': 'cleared'})
            outside = root / 'private-outside'; outside.write_bytes(b'sceneVisible'); path.symlink_to(outside)
            self.assertNotEqual(modern.control_marker(root, clear=True), {'state': 'cleared'})
            self.assertEqual(outside.read_bytes(), b'sceneVisible')

    def test_post_exit_group_selection_is_only_same_uid_verified_group_counts_with_no_private_names(self):
        uid = os.getuid(); secret = 'private-process-canary'
        raw = ('124 1 123 ' + str(uid) + ' CoreSimulatorService\n125 1 123 ' + str(uid) + ' ' + secret + '\n').encode()
        with patch.object(modern.simulator_probe, 'diagnostic_command', return_value=complete(raw)) as command:
            value = modern.group_observation(123, 123, (secret,))
        self.assertEqual(value['state'], 'observed'); self.assertEqual(value['counts']['CoreSimulatorService'], 1)
        self.assertEqual(value['counts']['other'], 1); self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(command.call_args.args[0], ['/bin/ps', '-g', '123', '-o', 'pid=,ppid=,pgid=,uid=,ucomm='])
        for data in (raw.replace(b' 123 ', b' 999 '), raw.replace(str(uid).encode(), str(uid + 1).encode()), raw * 129):
            with patch.object(modern.simulator_probe, 'diagnostic_command', return_value=complete(data)):
                self.assertNotEqual(modern.group_observation(123, 123, ())['state'], 'observed')
        for status in ({'status': 'commandFailed', 'exit_code': 1},
                       {'status': 'commandFailed', 'exit_code': 1, 'stdout_bytes': 0, 'stderr_bytes': 10}):
            with patch.object(modern.simulator_probe, 'diagnostic_command', return_value=(status, b'')):
                self.assertEqual(modern.group_observation(123, 123, ())['state'], 'unavailable')
        with patch.object(modern.simulator_probe, 'diagnostic_command', return_value=(
                {'status': 'commandFailed', 'exit_code': 1, 'stdout_bytes': 0, 'stderr_bytes': 0}, b'')):
            self.assertEqual(modern.group_observation(123, 123, ())['state'], 'observed')

    def test_live_callback_private_paths_or_unbounded_extra_fields_never_enter_public_result(self):
        secret = 'private-observer-canary'
        value = {'state': secret, 'marker': {'state': 'observed', 'stage': secret, 'path': secret},
                 'host_process': {'state': secret, 'pid': 987}, 'sample': {'state': secret, 'target': secret,
                    'selection': {'wait_categories': {'mach_message_wait': True, secret: 123}}}, secret: secret}
        actual = modern.public_live_observation(value)
        self.assertNotIn(secret, json.dumps(actual)); self.assertNotIn('987', json.dumps(actual))
        self.assertEqual(actual['sample']['selection']['wait_categories']['mach_message_wait'], 0)
        self.assertFalse(modern.public_group_observation({**value, 'received_before_retirement': True})['received_before_retirement'])
        for item in ([], {}, 1, True, None):
            value = {'state': item, 'marker': {'state': item}, 'host_process': {'state': item}, 'sample': {'target': item, 'state': item}}
            self.assertEqual(modern.public_live_observation(value)['state'], 'unavailable')

    def test_only_optional_test_zero_rights_accepts_missing_empty_or_xml_empty_signature_output(self):
        for expected in (None, {}):
            for readback in ('missing', 'empty', 'xml'):
                with self.subTest(expected=expected, readback=readback), tempfile.TemporaryDirectory() as directory:
                    result, _ = self.execute_fixture(Path(directory).resolve(), test_entitlements=expected, test_readback=readback)
                    self.assertTrue(result['passed']); self.assertTrue(result['tiny_signing']['test']['verified'])
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.execute_fixture(Path(directory).resolve(), test_entitlements={'get-task-allow': True})
            self.assertFalse(result['passed']); self.assertFalse(result['control']['attempted'])

    def test_live_filesystem_marker_and_kernel_path_uid_proof_choose_only_exact_owned_control_process(self):
        for failure in (None, 'path', 'uid', 'exited', 'metadata'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); containers = owned_fixture(root)
                marker = containers['data'] / modern.tiny.MARKER_RELATIVE; marker.parent.mkdir(parents=True); marker.write_bytes(b'mainEntered')
                if failure == 'metadata':
                    (root / 'Library/Developer/CoreSimulator/Devices' / IDENTIFIER / 'device.plist').write_bytes(plistlib.dumps({'UDID': IDENTIFIER}))
                calls = []; uid = os.getuid()
                def command(args, secrets, timeout=3):
                    calls.append((args, timeout))
                    if args[:4] == ['/bin/ps', '-p', '123', '-o']: return complete(('123 1 123 ' + str(uid) + ' xcodebuild\n').encode())
                    if args[:3] == ['/usr/bin/pgrep', '-x', '-u']: return complete(b'456\n')
                    if modern.KERNEL_PATH in args: return complete(str(containers['app'] / modern.CONTROL_APP).encode() if failure != 'path' else b'/private/foreign-executable')
                    if args[:4] == ['/bin/ps', '-p', '456', '-o']: return complete(('456 ' + str(uid if failure != 'uid' else uid + 1)).encode())
                    if modern.bootstrap.SAMPLE_EXEC_WRAPPER in args:
                        Path(args[-1]).write_bytes(b'Call graph:\n    1 Thread_0 DispatchQueue_1: com.apple.main-thread (serial)\n    1 mach_msg2_trap (in libsystem_kernel.dylib) + 0 [0xabc]\nBinary Images:\nprivate-sample-canary\n')
                        return complete()
                    raise AssertionError(args)
                alive = Mock(side_effect=[True, True, False]) if failure == 'exited' else lambda: True
                with patch.object(modern.Path, 'home', return_value=root), patch.object(modern.bootstrap, 'command', side_effect=command):
                    observed = modern.live_control_observation(123, 123, IDENTIFIER, modern.control_inventory(containers['app']), (), alive, 'owned-control')
                value = modern.public_live_observation(observed)
                self.assertNotIn('private-sample-canary', json.dumps(value)); self.assertNotIn(str(root), json.dumps(value))
                if failure == 'exited':
                    self.assertFalse(value['marker']['captured_while_child_alive']); self.assertFalse(any(modern.bootstrap.SAMPLE_EXEC_WRAPPER in args for args, _ in calls))
                elif failure == 'metadata':
                    self.assertFalse(value['owned_container_verified']); self.assertFalse(value['marker']['captured_while_child_alive'])
                    self.assertEqual(value['sample']['target'], 'xcodebuild')
                    self.assertFalse(any(args[:2] == ['xcrun', 'simctl'] for args, _ in calls))
                else:
                    self.assertEqual(value['marker'], {'state': 'observed', 'stage': 'mainEntered', 'captured_while_child_alive': True})
                    self.assertEqual(value['sample']['target'], 'tinyControl' if failure is None else 'xcodebuild')
                    self.assertTrue(value['sample']['selection']['main_thread_observed'])
                    self.assertTrue(all(timeout <= 10 for _, timeout in calls))


if __name__ == '__main__': unittest.main()
