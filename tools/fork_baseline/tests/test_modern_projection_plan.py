# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable plan/ownership tests, never an Xcode compilation claim."""
import copy
import json
from pathlib import Path
import plistlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modern_projection_plan as modern
import test_projection_replay as fixtures


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

    def test_codesign_xml_accepts_real_executable_warning_prefix_without_exposing_or_guessing_it(self):
        xml = plistlib.dumps({modern.CONTROL_DEBUG_ENTITLEMENT: True})
        prefix = (b'Executable=/private/control-private-canary/AyuProjectionControl\n'
                  b"Warning: Specifying ':' in the path is deprecated and will not work in a future release\n")
        self.assertTrue(modern.control_debug_entitlements(xml))
        self.assertTrue(modern.control_debug_entitlements(prefix + xml))
        for data in (prefix + xml + xml, b'private-unrecognized-prefix\n' + xml, xml + b'private-tail',
                     b'<?xml version="1.0"?><plist><dict><key>private-canary</key>',
                     plistlib.dumps({modern.CONTROL_DEBUG_ENTITLEMENT: 1}), b'x' * 65537):
            self.assertFalse(modern.control_debug_entitlements(data))

    def execute_fixture(self, root, control_outcome='passed', mutate=False):
        output = root / modern.OUTPUT; output.mkdir(parents=True)
        capture = fixtures.captured(root, current=True)
        info = capture / 'TEST_ROOT' / modern.products.APP / 'PlugIns' / modern.products.TEST / 'Info.plist'
        value = plistlib.loads(info.read_bytes())
        value['CFBundleIdentifier'] = 'org.livara.ayugram.tests.native-security.projectionTests'
        info.write_bytes(plistlib.dumps(value)); fixtures.ProjectionReplayTests.refresh(capture)
        shutil.copytree(capture, output / 'private-products')
        calls = []
        def command(args, timeout=30):
            calls.append((args, timeout))
            if args == ['xcodebuild', '-version']: return complete(b'Xcode 27.1\nBuild version 27A9275\n')
            if '--show-sdk-version' in args: return complete(b'27.1')
            if args[:4] == ['/usr/bin/codesign', '-d', '--entitlements', ':-']:
                return complete(plistlib.dumps({modern.CONTROL_DEBUG_ENTITLEMENT: True}))
            if 'build-for-testing' in args:
                products = Path(args[args.index('-derivedDataPath') + 1]) / 'Build/Products'
                plan, _, _ = apple_fixture(products, 2)
                (products / 'control.xctestrun').write_bytes(plistlib.dumps(plan))
            elif args[:3] == ['xcrun', 'simctl', 'create']: return complete(b'11111111-2222-3333-4444-555555555555')
            elif 'test-without-building' in args:
                if sum('test-without-building' in args for args, _ in calls) == 1:
                    return complete(control_log(control_outcome), 0 if control_outcome == 'passed' else 65)
                if mutate: (output / 'private-products/capture.json').write_bytes(b'private-corruption-canary')
                return complete(fixtures.log())
            return complete()
        def reseal(original, destination, *args, **kwargs):
            shutil.copytree(original, destination)
            return {'host_code_unchanged': True, 'test_tree_unchanged': True}
        original = output / 'private-products'
        with patch.object(modern, 'MANIFEST_SHA256', modern.projection.digest(original / 'manifest.json')), \
             patch.object(modern, 'CAPTURE_SHA256', modern.projection.digest(original / 'capture.json')), \
             patch.object(modern.projection, 'verify_current_checkout'), patch.object(modern.projection, 'prepare_control', side_effect=reseal):
            proof = modern.verification(original, modern.projection.validate_products(original, modern.SOURCE))
            modern.projection.write(output / 'verification.json', proof)
            report = modern.run(root=root, command=command)
        return report, calls

    def test_failed_authoritative_control_never_attempts_projection_or_claims_test_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); result, calls = self.execute_fixture(root, 'failed')
            self.assertFalse(result['passed']); self.assertEqual(result['control']['started'], 1)
            self.assertEqual(result['control']['passed'], 0); self.assertFalse(result['projection']['attempted'])
            self.assertEqual(sum('test-without-building' in args for args, _ in calls), 1)
            self.assertEqual(sum(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls), 1)
            self.assertTrue(result['control']['cleanup_completed']); self.assertTrue(result['original_products_unchanged'])

    def test_control_pass_then_exact_41_single_attempt_preserves_source_and_runtime_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls = self.execute_fixture(Path(directory).resolve())
            self.assertTrue(result['passed']); self.assertEqual(result['control']['passed'], 1)
            self.assertEqual(result['projection']['started'], 41); self.assertEqual(result['projection']['passed'], 41)
            self.assertTrue(result['original_products_unchanged']); self.assertTrue(result['projection']['cleanup_completed'])
            self.assertEqual([timeout for args, timeout in calls if 'test-without-building' in args], [300, 300])
            self.assertEqual(sum('build-for-testing' in args for args, _ in calls), 1)
            for key in ('application_recompiled', 'projection_tests_recompiled', 'physical_device_verified', 'release_qualified'):
                self.assertFalse(result[key])
            self.assertFalse(any('bazel' in ' '.join(args).lower() for args, _ in calls))

    def test_post_execution_source_mutation_never_leaves_a_passed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.execute_fixture(Path(directory).resolve(), mutate=True)
            self.assertEqual(result['projection']['passed'], 41)
            self.assertFalse(result['original_products_unchanged']); self.assertFalse(result['passed'])


if __name__ == '__main__': unittest.main()
