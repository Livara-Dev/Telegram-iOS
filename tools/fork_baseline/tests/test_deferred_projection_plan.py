# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable fixtures; native compilation/XCTest-manager support require macOS."""
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import deferred_projection_plan as parser
import test_modern_projection_plan as base
import test_projection_replay as captured

def typed(name, **fields):
    return {'_type': {'_name': name}, **fields}


def string(value):
    return typed('String', _value=value)


def array(values):
    return typed('Array', _values=values)


def action(token='0~RealisticOpaque_12+/=-'):
    result = typed('ActionResult')
    if token is not None: result['testsRef'] = typed('Reference', id=string(token))
    return typed('ActionRecord', actionResult=result)


def invocation(actions=None):
    return typed('ActionsInvocationRecord', actions=array([action()] if actions is None else actions))


def case(key, status='Success', module=None, parenthesized=True, kind='ActionTestMetadata'):
    owner, method = key.split('.')
    name = (module + '.' if module else '') + owner + '/' + method + ('()' if parenthesized else '')
    return typed(kind, identifier=string(name), testStatus=string(status))


def summaries(cases=None, split=False):
    if cases is None: cases = [case(key) for key in sorted(parser.EXPECTED_XCRESULT_CASES)]
    groups = [cases[:21], cases[21:]] if split else [cases]
    return typed('ActionTestPlanRunSummaries', summaries=array([
        typed('ActionTestPlanRunSummary', testableSummaries=array([
            typed('ActionTestableSummary', tests=array([
                typed('ActionTestSummaryGroup', subtests=array(group))]))])) for group in groups]))


def encoded(value):
    return json.dumps(value).encode()


class DeferredXCResultParserTests(unittest.TestCase):
    def error_is_private(self, method, raw, message):
        try:
            method(raw)
        except parser.InvalidXCResult as error:
            self.assertEqual(str(error), message)
            rendered = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            self.assertNotIn('private-canary', rendered)
            self.assertNotIn('/private/raw', rendered)
        else:
            self.fail('Malformed result unexpectedly accepted')

    def test_exact_payload_set_is_the_current_captured_41_and_one_wrapper(self):
        source = (Path(__file__).resolve().parents[1] / 'projection_replay.py').read_text()
        assignment = next(value for value in ast.parse(source).body if isinstance(value, ast.Assign) and
            any(isinstance(name, ast.Name) and name.id == 'EXPECTED' for name in value.targets))
        self.assertEqual(parser.PAYLOAD_CASES, frozenset(ast.literal_eval(assignment.value.args[0])))
        self.assertEqual(len(parser.PAYLOAD_CASES), 41); self.assertEqual(len(parser.EXPECTED_XCRESULT_CASES), 42)

    def test_root_selects_exactly_one_private_opaque_ref_across_all_actions(self):
        token = '0~RealisticOpaque_12+/=-'
        self.assertEqual(parser.strict_xcresult_root(encoded(invocation())), token)
        self.assertEqual(parser.strict_xcresult_root(encoded(invocation([action(None), action(token)]))), token)
        self.assertEqual(parser.strict_xcresult_root(encoded(invocation([action('abc123')]))), 'abc123')

    def test_missing_multiple_duplicate_or_malformed_action_refs_reject(self):
        for value in [invocation([]), invocation([action(None)]), invocation([action(), action()]),
                      invocation([action(), action('Second')]), invocation([action(), {}])]:
            with self.subTest(value=repr(value)[:40]):
                self.error_is_private(parser.strict_xcresult_root, encoded(value), 'xcresult_root_invalid')
        value = invocation(); value['shadow'] = {'testsRef': 'private-canary'}
        self.error_is_private(parser.strict_xcresult_root, encoded(value), 'xcresult_root_invalid')

    def test_ref_is_bounded_typed_safe_opaque_token_and_not_an_option_or_path(self):
        for token in ['', '-option', '/private/raw', 'a.b', 'private-canary\n', 'a b', 'é', 'a\0b', 'a' * 257]:
            with self.subTest(token=token[:20]):
                self.error_is_private(parser.strict_xcresult_root, encoded(invocation([action(token)])), 'xcresult_root_invalid')
        value = invocation(); value['actions']['_values'][0]['actionResult']['testsRef']['id'] = 'private-canary'
        self.error_is_private(parser.strict_xcresult_root, encoded(value), 'xcresult_root_invalid')

    def test_complete_42_real_metadata_records_across_every_summary_pass(self):
        result = parser.strict_xcresult_cases(encoded(summaries(split=True)))
        self.assertTrue(result['all_expected_passed']); self.assertEqual(result['case_records'], 42)
        self.assertEqual(result['passed'], 42); self.assertEqual(result['payload_passed'], 41)
        self.assertTrue(result['wrapper_passed']); self.assertEqual(result['cases'], {k: 'passed' for k in sorted(parser.EXPECTED_XCRESULT_CASES)})
        self.assertEqual(result['missing'], []); self.assertEqual(result['unknown_case_records'], 0)
        self.assertFalse(result['duplicate_case_events']); self.assertNotIn('started', result)

    def test_wrapper_only_or_one_missing_payload_never_proves_nested_suite_pass(self):
        for leaves in [[case(parser.WRAPPER)], [case(k) for k in sorted(parser.EXPECTED_XCRESULT_CASES)[1:]], []]:
            result = parser.strict_xcresult_cases(encoded(summaries(leaves)))
            self.assertFalse(result['all_expected_passed']); self.assertTrue(result['missing'])

    def test_module_aliases_are_exact_and_full_bazel_namespace_is_supported(self):
        leaves = [case(key, module=sorted(parser.PAYLOAD_MODULES)[-1], parenthesized=False)
                  for key in sorted(parser.PAYLOAD_CASES)]
        leaves.append(case(parser.WRAPPER, module='AyuProjectionControlTests', kind='ActionTestSummary'))
        self.assertTrue(parser.strict_xcresult_cases(encoded(summaries(leaves)))['all_expected_passed'])
        leaves[0]['identifier'] = string('private_canary.' + leaves[0]['identifier']['_value'].split('.', 1)[1])
        result = parser.strict_xcresult_cases(encoded(summaries(leaves)))
        self.assertFalse(result['all_expected_passed']); self.assertEqual(result['unknown_case_records'], 1)
        self.assertNotIn('private_canary', json.dumps(result))

    def test_unknown_case_and_wrong_wrapper_module_are_not_basename_matches_or_public_names(self):
        for extra in [case('PrivateClass.testPrivate'), case(parser.WRAPPER, module='WrongModule')]:
            leaves = [case(k) for k in sorted(parser.EXPECTED_XCRESULT_CASES)] + [extra]
            result = parser.strict_xcresult_cases(encoded(summaries(leaves)))
            self.assertFalse(result['all_expected_passed']); self.assertEqual(result['unknown_case_records'], 1)
            self.assertNotIn('PrivateClass', json.dumps(result)); self.assertNotIn('WrongModule', json.dumps(result))

    def test_duplicate_case_even_under_a_different_approved_alias_fails_closed(self):
        key = sorted(parser.PAYLOAD_CASES)[0]
        leaves = [case(k) for k in sorted(parser.EXPECTED_XCRESULT_CASES)] + [case(key, module='ChatLocalTextFilterProjectionTestsLib')]
        result = parser.strict_xcresult_cases(encoded(summaries(leaves)))
        self.assertFalse(result['all_expected_passed']); self.assertTrue(result['duplicate_case_events'])
        self.assertEqual(result['cases'][key], 'duplicate'); self.assertEqual(result['case_records'], 43)

    def test_failure_skip_unknown_status_and_nonpassing_group_are_never_passes(self):
        for status in ['Failure', 'Skipped', 'Expected Failure', 'private-canary /private/raw']:
            leaves = [case(k) for k in sorted(parser.EXPECTED_XCRESULT_CASES)]
            leaves[0]['testStatus'] = string(status)
            result = parser.strict_xcresult_cases(encoded(summaries(leaves)))
            self.assertFalse(result['all_expected_passed']); self.assertEqual(result['passed'], 41)
            self.assertNotIn('private-canary', json.dumps(result)); self.assertNotIn('/private/raw', json.dumps(result))
        value = summaries(); value['summaries']['_values'][0]['testableSummaries']['_values'][0]['tests']['_values'][0]['testStatus'] = string('Failure')
        result = parser.strict_xcresult_cases(encoded(value))
        self.assertTrue(result['nonpassing_group']); self.assertFalse(result['all_expected_passed'])

    def test_malformed_typed_strings_arrays_or_case_groups_cannot_be_ignored(self):
        for kind in ['plainStatus', 'booleanIdentifier', 'wrongArray', 'unknownLeaf', 'groupWithoutSubtests', 'caseWithSubtests']:
            value = summaries(); group = value['summaries']['_values'][0]['testableSummaries']['_values'][0]['tests']['_values'][0]
            leaf = group['subtests']['_values'][0]
            if kind == 'plainStatus': leaf['testStatus'] = 'private-canary'
            elif kind == 'booleanIdentifier': leaf['identifier'] = typed('String', _value=True)
            elif kind == 'wrongArray': group['subtests']['_type']['_name'] = 'private-canary'
            elif kind == 'unknownLeaf': leaf['_type']['_name'] = 'UnknownTestCase'
            elif kind == 'groupWithoutSubtests': group.pop('subtests')
            else: leaf['subtests'] = array([])
            with self.subTest(kind=kind):
                self.error_is_private(parser.strict_xcresult_cases, encoded(value), 'xcresult_cases_invalid')

    def test_hidden_tests_or_terminal_metadata_outside_consumed_tree_reject(self):
        for hidden in [{'tests': array([case(parser.WRAPPER)])}, case(parser.WRAPPER),
                       {'identifier': string('private-canary'), 'testStatus': string('Success')}]:
            value = summaries(); value['ignored'] = hidden
            self.error_is_private(parser.strict_xcresult_cases, encoded(value), 'xcresult_cases_invalid')

    def test_oversize_depth_node_count_duplicate_json_keys_and_nonfinite_json_reject_privately(self):
        raw_values = [b' ' * (parser.MAX_XCRESULT_JSON_BYTES + 1), b'{"private-canary":',
                      b'{"a":1,"a":2}', b'{"private-canary":NaN}', b'{"private-canary":1e999}']
        deep = {'private-canary': []}; current = deep['private-canary']
        for _ in range(33): child = []; current.append(child); current = child
        raw_values += [encoded(deep), encoded({'private-canary': list(range(4100))})]
        for method, message in [(parser.strict_xcresult_root, 'xcresult_root_invalid'), (parser.strict_xcresult_cases, 'xcresult_cases_invalid')]:
            for raw in raw_values:
                with self.subTest(method=method.__name__, size=len(raw)):
                    self.error_is_private(method, raw, message)


def native_log(outcome='passed', execution_profile='deferred'):
    def event(key, state):
        name, method = key.split('.')
        return "Test Case '-[Module." + name + ' ' + method + "]' " + state + '.\n'
    if execution_profile == 'discovery':
        return (''.join(event(key, 'started') + event(key, outcome) for key in sorted(parser.EXPECTED)) +
            event(parser.DISCOVERY_WRAPPER, 'started') + event(parser.DISCOVERY_WRAPPER, 'passed')).replace('Module.', '').encode()
    return (event(parser.WRAPPER, 'started') + ''.join(event(key, 'started') + event(key, outcome)
        for key in sorted(parser.EXPECTED)) + event(parser.WRAPPER, 'passed')).encode()


def completed_summary(execution_profile='deferred'):
    booleans, strings, _phases, _failures = parser.summary_configuration(execution_profile)
    return {'schema': 1, **parser.COUNTS, **booleans, **strings}


class DeferredRunnerTests(unittest.TestCase):
    def execute(self, root, mutation=None, control='passed', payload='passed', xc='complete', counter=None,
                cleanup=False, identity=False, repeated=False, baseline_layout='hostedPlugIns', wrapper_layout='sibling',
                projection_code=None, projection_raw=None, execution_profile='deferred', prepared_profile=None):
        output = root / parser.OUTPUT; output.mkdir(parents=True)
        original = captured.captured(root, current=True)
        app = original / 'TEST_ROOT' / parser.modern.products.APP
        test_info = app / 'PlugIns' / parser.modern.products.TEST / 'Info.plist'
        value = plistlib.loads(test_info.read_bytes()); value['CFBundleIdentifier'] = 'org.livara.ayugram.tests.native-security.projectionTests'
        test_info.write_bytes(plistlib.dumps(value)); base.captured_resources(app)
        captured.ProjectionHostControlTests.refresh_capture(original)
        shutil.copytree(original, output / 'private-products')
        selected = parser.profile_configuration(execution_profile)
        wrapper_source = (Path(__file__).resolve().parents[1] / selected['source']).read_bytes()
        (root / 'tools/fork_baseline').mkdir(parents=True)
        (root / 'tools/fork_baseline' / selected['source']).write_bytes(wrapper_source)
        (output / 'private-wrapper-source.swift').write_bytes(wrapper_source)
        parser.tiny.create_project(output / 'private-project')
        platform = root / 'SDK/iPhoneSimulator.platform'
        for relative in ('Developer/Library/Frameworks/XCTest.framework/XCTest', 'Developer/usr/lib/libXCTestSwiftSupport.dylib'):
            path = platform / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'first-party-SDK')
        calls, options, identifiers = [], [], []
        selected_wrapper = None
        self.last_options = options
        def signed_line(path): return shlex.join(['/usr/bin/codesign', '--force', '--sign', '-', str(path)])
        def command(args, timeout=30, **extra):
            nonlocal selected_wrapper
            calls.append((args, timeout)); options.append((args, extra))
            if args == ['xcodebuild', '-version']: return base.complete(b'Xcode 27.1\nBuild version 27A9275\n')
            if '--show-sdk-version' in args: return base.complete(b'27.1')
            if '--show-sdk-platform-path' in args: return base.complete(str(platform).encode())
            if args[:2] in (['xcodebuild', 'build-for-testing'], ['xcodebuild', 'build']):
                if args[1] == 'build-for-testing':
                    self.assertEqual(args[args.index('-scheme') + 1], parser.modern.CONTROL_APP)
                    derived = Path(args[args.index('-derivedDataPath') + 1]); products = derived / 'Build/Products'
                    if mutation == 'control_compile': return base.complete(b'private-compiler-failure-canary', 65)
                    plan, host, test = base.apple_fixture(products, 1, False, sibling=baseline_layout != 'hostedPlugIns')
                    if baseline_layout == 'both':
                        shutil.copytree(test, host / 'PlugIns' / test.name)
                    (products / 'control.xctestrun').write_bytes(plistlib.dumps(plan))
                    return base.complete((signed_line(host) + '\n' + signed_line(test) + '\n').encode())
                self.assertEqual(args[args.index('-target') + 1], parser.modern.CONTROL_TEST)
                for option in ('-scheme', '-derivedDataPath', '-destination'):
                    self.assertNotIn(option, args)
                derived = output / 'private-wrapper-derived'; products = derived / 'Build/Products'
                self.assertIn('SYMROOT=' + str(products), args)
                self.assertIn('OBJROOT=' + str(derived / 'Build/Intermediates.noindex'), args)
                host_executable = products / 'Debug-iphonesimulator' / (parser.modern.CONTROL_APP + '.app') / parser.modern.CONTROL_APP
                self.assertTrue(host_executable.is_file())
                self.assertIn('TEST_HOST=' + str(host_executable), args)
                self.assertIn('BUNDLE_LOADER=' + str(host_executable), args)
                if mutation in {'clone_failed_resource', 'clone_failed_code'}:
                    path = host_executable.parent / 'Info.plist' if mutation == 'clone_failed_resource' else host_executable
                    value = bytearray(path.read_bytes()); value[min(256, len(value) - 1)] ^= 1; path.write_bytes(value)
                    return base.complete(b'private-compiler-failure-canary', 65)
                if mutation == 'wrapper_compile': return base.complete(b'private-compiler-failure-canary', 65)
                self.assertTrue(any(value.startswith('TEST_HOST=') for value in args))
                project = Path(args[args.index('-project') + 1]).parent
                self.assertNotIn('com.apple.product-type.application', (project / 'DeferredProjection.xcodeproj/project.pbxproj').read_text())
                self.assertIsNotNone(selected_wrapper)
                test = base.bundle(selected_wrapper,
                    parser.modern.CONTROL_TEST_ID, parser.modern.CONTROL_TEST, 8)
                info = plistlib.loads((test / 'Info.plist').read_bytes()); info['CFBundleSupportedPlatforms'] = ['iPhoneSimulator']
                if mutation == 'wrapper_identity': info['CFBundleIdentifier'] = 'foreign-private-canary'
                (test / 'Info.plist').write_bytes(plistlib.dumps(info))
                if mutation == 'wrapper_platform': (test / parser.modern.CONTROL_TEST).write_bytes(captured.macho(b'bad-platform', kind=8, platform=2))
                if mutation == 'control_mutated':
                    control_host = output / 'private-derived/Build/Products/Debug-iphonesimulator' / (parser.modern.CONTROL_APP + '.app')
                    (control_host / 'unexpected').write_bytes(b'changed')
                return base.complete((signed_line(test) + '\n').encode())
            if args[:3] == ['/usr/bin/codesign', '--display', '--verbose=4']:
                identifier = parser.modern.CONTROL_APP_ID if Path(args[-1]).suffix == '.app' else parser.modern.CONTROL_TEST_ID
                return base.complete(('Identifier=' + identifier + '\nSignature=adhoc\nTeamIdentifier=not set\n').encode())
            if args[:3] == ['/usr/bin/codesign', '--display', '--entitlements']:
                if mutation == 'rights' and 'private-deferred-host' in args[-1]: Path(args[3]).write_bytes(plistlib.dumps({'unverified-right': True}))
                return base.complete()
            if '--sign' in args:
                host = Path(args[-1])
                if mutation == 'payload_bytes':
                    (host / parser.PAYLOAD / 'ChatLocalTextFilterProjectionTests').write_bytes(b'changed-payload')
                if mutation == 'resource_bytes': (host / 'en.lproj/Localizable.strings').write_bytes(b'changed-resource')
                if mutation == 'host_code':
                    path = host / parser.modern.CONTROL_APP; data = bytearray(path.read_bytes()); data[256] ^= 1; path.write_bytes(data)
                return base.complete()
            if args[:3] == ['xcrun', 'simctl', 'create']:
                identifier = base.IDENTIFIER if not identifiers or repeated else '66666666-7777-8888-9999-AAAAAAAAAAAA'
                identifiers.append(identifier); device = root / 'Library/Developer/CoreSimulator/Devices' / identifier
                device.mkdir(parents=True, exist_ok=True)
                metadata = {'UDID': identifier, 'name': 'wrong' if identity else args[3], 'deviceType': parser.modern.DEVICE_TYPE,
                            'runtime': parser.modern.RUNTIME, 'state': 'Shutdown'}
                (device / 'device.plist').write_bytes(plistlib.dumps(metadata))
                return base.complete(identifier.encode())
            if args[:2] == ['xcrun', 'simctl'] and args[2] in {'bootstatus', 'install', 'launch'}:
                raise AssertionError('Only the single Xcode command manages readiness/install/launch')
            if args[:2] == ['xcrun', 'simctl'] and args[2] in {'shutdown', 'delete'}:
                return base.complete(code=1 if cleanup and len(identifiers) == 1 else 0)
            if 'test-without-building' in args:
                index = sum('test-without-building' in command for command, _ in calls)
                if index == 1:
                    if control == 'timeout': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                    return base.complete(base.control_log(control), 0 if control == 'passed' else 65)
                plan = Path(args[args.index('-xctestrun') + 1]); _, runner = parser.modern.target(parser.modern._read_plan(plan))
                host = parser.modern.resolved_path(runner['TestHostPath'], plan.parent)
                if host.suffix != '.app': host = host.parent
                device_data = root / 'Library/Developer/CoreSimulator/Devices' / identifiers[-1] / 'data'
                parent = device_data / 'Containers/Bundle/Application' / base.IDENTIFIER
                parent.mkdir(parents=True)
                (parent / '.com.apple.mobile_container_manager.metadata.plist').write_bytes(plistlib.dumps({'MCMMetadataIdentifier': parser.modern.CONTROL_APP_ID}))
                installed = parent / host.name; shutil.copytree(host, installed)
                if mutation == 'installed_bytes': (installed / 'en.lproj/Localizable.strings').write_bytes(b'changed')
                container = device_data / 'Containers/Data/Application' / base.IDENTIFIER; container.mkdir(parents=True)
                (container / '.com.apple.mobile_container_manager.metadata.plist').write_bytes(plistlib.dumps({'MCMMetadataIdentifier': parser.modern.CONTROL_APP_ID}))
                summary = completed_summary(execution_profile)
                if counter: summary[counter] = 0 if summary[counter] else 1
                directory = container / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
                (directory / 'stage').write_bytes(b'completed'); (directory / 'summary.json').write_text(json.dumps(summary))
                if payload == 'timeout': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                return base.complete(native_log(payload, execution_profile) if projection_raw is None else projection_raw,
                    (0 if payload == 'passed' else 65) if projection_code is None else projection_code)
            if parser.JSON_WRAPPER in args:
                if '-showBuildSettings' in args:
                    if mutation == 'settings_failure': return base.complete(b'private-settings-failure-canary', 65)
                    host_executable = next(value.split('=', 1)[1] for value in args if value.startswith('TEST_HOST='))
                    host = Path(host_executable).parent
                    selected_wrapper = (host / 'PlugIns' if wrapper_layout == 'hostedPlugIns' else host.parent) / (parser.modern.CONTROL_TEST + '.xctest')
                    value = [{'target': parser.modern.CONTROL_TEST, 'buildSettings': {'TEST_HOST': host_executable,
                        'BUNDLE_LOADER': host_executable, 'FULL_PRODUCT_NAME': parser.modern.CONTROL_TEST + '.xctest',
                        'TARGET_BUILD_DIR': str(selected_wrapper.parent), 'CODESIGNING_FOLDER_PATH': str(selected_wrapper)}}]
                else:
                    expected = parser.PAYLOAD_CASES | {selected['wrapper']}
                    value = invocation() if '--id' not in args else summaries([case(selected['wrapper'])] if xc == 'wrapperOnly' else [case(key) for key in sorted(expected)])
                    if xc == 'malformed' or xc == 'casesMalformed' and '--id' in args: value = {'private-canary': 'raw'}
                Path(args[3]).write_text(json.dumps(value)); Path(args[3]).chmod(0o600)
                return base.complete()
            return base.complete()
        with patch.object(parser.projection, 'verify_current_checkout'), \
             patch.object(parser.modern, 'MANIFEST_SHA256', parser.projection.digest(original / 'manifest.json')), \
             patch.object(parser.modern, 'CAPTURE_SHA256', parser.projection.digest(original / 'capture.json')), \
             patch.object(parser.modern.Path, 'home', return_value=root):
            parser.projection.write(output / 'verification.json', parser.verification(output / 'private-products',
                parser.projection.validate_products(output / 'private-products', parser.SOURCE), execution_profile if prepared_profile is None else prepared_profile))
            report = parser.run(root=root, command=command, execution_profile=execution_profile)
        return report, calls

    def test_complete_pipeline_requires_real_42_result_records_counters_bytes_seals_and_two_clean_devices(self):
        with tempfile.TemporaryDirectory() as temporary:
            report, calls = self.execute(Path(temporary).resolve())
            self.assertTrue(report['passed'], report.get('failure'))
            self.assertEqual(report['control']['passed'], 1); self.assertEqual(report['projection']['passed'], 41)
            self.assertTrue(report['projection']['wrapper_passed']); self.assertTrue(report['native_xcresult_case_accounting_verified'])
            self.assertFalse(report['nested_testmanager_equivalence_verified']); self.assertFalse(report['release_qualified'])
            self.assertTrue(report['original_products_unchanged']); self.assertTrue(report['original_built_products_unchanged'])
            self.assertEqual([timeout for args, timeout in calls if 'test-without-building' in args], [600, 600])
            self.assertEqual([args[args.index('-destination-timeout') + 1] for args, _ in calls if 'test-without-building' in args], ['600', '600'])
            tested = [(args, options) for args, options in self.last_options if 'test-without-building' in args]
            self.assertNotIn('bootstrap_after_seconds', tested[0][1]); self.assertEqual(tested[1][1]['bootstrap_after_seconds'], 480)
            self.assertEqual(sum(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls), 2)
            self.assertTrue(report['control']['cleanup_completed']); self.assertTrue(report['projection']['cleanup_completed'])

    def test_all_new_compilation_signing_and_plan_work_precedes_any_device_or_suite(self):
        with tempfile.TemporaryDirectory() as temporary:
            report, calls = self.execute(Path(temporary).resolve()); self.assertTrue(report['passed'])
            first_device = next(i for i, (args, _) in enumerate(calls) if args[:3] == ['xcrun', 'simctl', 'create'])
            build = [i for i, (args, _) in enumerate(calls) if args[:2] in (['xcodebuild', 'build-for-testing'], ['xcodebuild', 'build'])]
            self.assertEqual(len(build), 2); self.assertTrue(all(i < first_device for i in build))
            seals = [i for i, (args, _) in enumerate(calls) if '--verify' in args]
            self.assertTrue(seals); self.assertTrue(all(i < first_device for i in seals))

    def test_actual_settings_and_build_use_identical_clone_args_for_both_product_layouts(self):
        for baseline in ('hostedPlugIns', 'sibling', 'both'):
            for layout in ('hostedPlugIns', 'sibling'):
                with self.subTest(baseline=baseline, layout=layout), tempfile.TemporaryDirectory() as temporary:
                    report, calls = self.execute(Path(temporary).resolve(), baseline_layout=baseline, wrapper_layout=layout)
                    self.assertTrue(report['passed'], report.get('failure'))
                    self.assertEqual(report['wrapper_product_layout'], layout)
                    self.assertTrue(report['wrapper_build_host']['independent_host_copy_verified'])
                    self.assertEqual(report['wrapper_build_host']['old_control_plugin_removed'], baseline != 'sibling')
                    self.assertTrue(report['original_built_products_unchanged'])
                    query = next(args for args, _ in calls if '-showBuildSettings' in args)
                    build = next(args for args, _ in calls if args[:2] == ['xcodebuild', 'build'])
                    self.assertEqual(query[query.index('-json') + 1:], build[2:])
                    self.assertEqual(query[1:3], ['-c', parser.JSON_WRAPPER])
                    self.assertTrue(Path(next(value.split('=', 1)[1] for value in build if value.startswith('TEST_HOST='))).is_file())

    def test_control_outcome_failure_does_not_suppress_independent_payload_after_safe_cleanup(self):
        for control in ('failed', 'timeout'):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as temporary:
                report, calls = self.execute(Path(temporary).resolve(), control=control)
                self.assertFalse(report['passed']); self.assertEqual(report['projection']['passed'], 41)
                self.assertTrue(report['projection']['cleanup_completed']); self.assertEqual(report['failure'], 'deferred_control_not_passed')
                self.assertEqual(sum('test-without-building' in args for args, _ in calls), 2)

    def test_unverified_control_cleanup_or_identity_blocks_the_second_device(self):
        for option in ('cleanup', 'identity'):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                report, calls = self.execute(Path(temporary).resolve(), **{option: True})
                self.assertFalse(report['passed']); self.assertFalse(report['projection']['attempted'])
                self.assertEqual(sum(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls), 1)

    def test_wrapper_only_native_results_cannot_qualify_even_with_41_logged_passes_and_counters(self):
        with tempfile.TemporaryDirectory() as temporary:
            report, _ = self.execute(Path(temporary).resolve(), xc='wrapperOnly')
            self.assertEqual(report['projection']['passed'], 41); self.assertTrue(report['projection']['actual_run_counters_matched'])
            self.assertFalse(report['passed']); self.assertFalse(report['actual_native_xcresult_verified'])
            self.assertFalse(report['native_xcresult_case_accounting_verified'])

    def test_exit65_and_invalid_native_schema_remain_hard_failures_despite_every_logged_pass_and_counter(self):
        for code, schema in ((65, 'complete'), (0, 'malformed'), (65, 'malformed'), (65, 'casesMalformed')):
            with self.subTest(code=code, schema=schema), tempfile.TemporaryDirectory() as temporary:
                report, _ = self.execute(Path(temporary).resolve(), projection_code=code, xc=schema)
                self.assertFalse(report['passed']); self.assertFalse(report['native_xcresult_case_accounting_verified'])
                self.assertTrue(report['projection']['all_expected_passed'])
                self.assertTrue(report['projection']['actual_run_counters_matched'])
                self.assertTrue(report['projection']['installed_bytes_match'])
                self.assertTrue(report['projection']['cleanup_completed'])
                self.assertEqual(report['commands']['projection_xctest']['exit_code'], code)
                if schema != 'complete':
                    actual = report['projection']['xcresult']
                    self.assertFalse(actual['all_expected_passed'])
                    self.assertEqual(actual['reason'], 'xcresult_root_invalid' if schema == 'malformed' else 'xcresult_cases_invalid')
                    self.assertEqual(actual['diagnostic']['first_rejection']['reason'], 'typedObjectMismatch')

    def test_native_log_preserves_redacted_start_and_final_exit_reason_within_existing_cap(self):
        secret = 'private-diagnostic-secret-' + 'z' * 50
        # The same literal spans several prospective prefix/tail cut positions;
        # full-input redaction must happen before either retained selection.
        raw = b'XCODE_HEAD_MARKER\n' + native_log() + (b'a' * 4000 + secret.encode()) * 16
        raw += '\nXCODE_FINAL_EXIT_REASON nested-manager-accounting\n'.encode()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'AYU_TEST_SECRET': secret}):
            root = Path(temporary).resolve(); report, _ = self.execute(root, projection_code=65, projection_raw=raw)
            self.assertFalse(report['passed']); self.assertTrue(report['projection']['all_expected_passed'])
            log = (root / parser.OUTPUT / 'projection-test.log').read_text()
            self.assertIn('XCODE_HEAD_MARKER', log); self.assertIn('XCODE_FINAL_EXIT_REASON nested-manager-accounting', log)
            self.assertNotIn(secret, log); self.assertIn('REDACTED', log)
            self.assertLessEqual(len(json.dumps(log, ensure_ascii=True).encode()), 16 * 1024)

    def test_every_counter_or_runtime_status_failure_remains_failed_despite_complete_native_events(self):
        for counter in ('run_execution_count', 'observer_passed', 'run_total_failure_count', 'run_skip_count'):
            with self.subTest(counter=counter), tempfile.TemporaryDirectory() as temporary:
                report, _ = self.execute(Path(temporary).resolve(), counter=counter)
                self.assertFalse(report['passed']); self.assertFalse(report['projection']['actual_run_counters_matched'])
        with tempfile.TemporaryDirectory() as temporary:
            report, _ = self.execute(Path(temporary).resolve(), payload='timeout')
            self.assertFalse(report['passed']); self.assertEqual(report['projection']['started'], 0)
            self.assertEqual(len(report['projection']['missing']), 41); self.assertTrue(report['projection']['cleanup_completed'])

    def test_compile_identity_platform_rights_payload_host_or_installed_mutations_never_qualify(self):
        for mutation in ('control_compile', 'settings_failure', 'wrapper_compile', 'wrapper_identity', 'wrapper_platform', 'control_mutated',
                         'payload_bytes', 'resource_bytes', 'host_code', 'rights', 'installed_bytes'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                report, calls = self.execute(Path(temporary).resolve(), mutation=mutation)
                self.assertFalse(report['passed'], mutation); self.assertFalse(report['native_xcresult_case_accounting_verified'])
                if mutation != 'installed_bytes': self.assertFalse(any('test-without-building' in args for args, _ in calls))

    def test_changed_prepared_swift_source_is_rejected_before_any_artifact_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); source = root / 'tools/fork_baseline'; source.mkdir(parents=True)
            (source / parser.WRAPPER_SOURCE).write_bytes(b'private-source-canary')
            with patch.object(parser.projection, 'verify_current_checkout'), patch.object(parser.modern, 'prepare') as download:
                with self.assertRaisesRegex(parser.DeferredError, '^deferred_source_mismatch$'):
                    parser.prepare(root=root)
            download.assert_not_called()

    def test_baseline_project_must_exactly_match_the_unmodified_tiny_source_before_building(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); project = root / 'project'; parser.tiny.create_project(project)
            parser.verify_control_project(project)
            path = project / parser.modern.CONTROL_APP / 'main.m'; path.write_bytes(b'private-unverified-source-canary')
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_control_project_changed$'):
                parser.verify_control_project(project)

    def test_wrapper_project_has_exactly_one_test_target_no_host_build_and_pinned_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); source = (Path(__file__).resolve().parents[1] / parser.WRAPPER_SOURCE).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), parser.WRAPPER_SHA256)
            project = root / 'wrapper'; parser.create_wrapper_project(project, source)
            pbx = (project / 'DeferredProjection.xcodeproj/project.pbxproj').read_text()
            self.assertEqual(pbx.count('isa = PBXNativeTarget;'), 1)
            self.assertNotIn('com.apple.product-type.application', pbx); self.assertNotIn('PBXTargetDependency', pbx)
            self.assertNotIn('.m;', pbx); self.assertNotIn('TEST_HOST =', pbx)
            scheme = ET.parse(project / 'DeferredProjection.xcodeproj/xcshareddata/xcschemes/DeferredProjection.xcscheme').getroot()
            self.assertEqual(scheme.find('BuildAction').attrib['buildImplicitDependencies'], 'NO')
            entries = scheme.findall('./BuildAction/BuildActionEntries/BuildActionEntry')
            self.assertEqual(len(entries), 1); self.assertEqual(entries[0].find('BuildableReference').attrib['BuildableName'], 'AyuProjectionControlTests.xctest')
            self.assertRegex(entries[0].find('BuildableReference').attrib['BlueprintIdentifier'], r'^[0-9A-F]{24}$')
            self.assertEqual((project / 'Tests/wrapper.swift').read_bytes(), source)
            with self.assertRaises(parser.DeferredError): parser.create_wrapper_project(root / 'bad', source + b'changed')

    def test_swift_live_case_origins_are_pinned_independently_of_log_display_aliases(self):
        source = (Path(__file__).resolve().parents[1] / parser.WRAPPER_SOURCE).read_text()
        expected = set(re.findall(r'"((?:ChatLocalArchiveProjectionTests|ChatLocalArchiveRowRendererTests|ChatLocalTextFilterProjectionTests)\.test\w+)"', source))
        self.assertEqual(expected, parser.EXPECTED)
        self.assertIn('NSStringFromClass(type(of: testCase))', source)
        self.assertIn('Bundle(for: type(of: testCase))', source)
        self.assertIn('submodules_TelegramUI_Components_Chat_ChatHistoryEntry_ChatLocalTextFilterProjectionTestsLib', source)
        self.assertNotIn('XCTestSuite.default', source); self.assertEqual(source.count('suite.run()'), 1)
        self.assertEqual(source.count('bundle.loadAndReturnError()'), 1); self.assertIn('!bundle.isLoaded', source)
        self.assertIn('payloadLoadStart', source); self.assertIn('suiteRun', source)

    def test_separate_42_log_profile_rejects_merged_malformed_unknown_duplicate_or_outside_wrapper_events(self):
        valid = native_log(); self.assertTrue(parser.cases(valid)['all_expected_passed'])
        self.assertFalse(parser.projection.cases(valid)['all_expected_passed'])
        for extra in (b"Test Case '-[malformed]' failed.", b"Test Case '-[OtherTests testUnknown]' unsupported.",
                      b"Test Case '-[OtherTests testUnknown]' failed."):
            value = parser.cases(valid.rstrip() + extra)
            self.assertFalse(value['all_expected_passed']); self.assertGreater(value['unknown_event_count'], 0)
        self.assertFalse(parser.cases(valid + valid)['all_expected_passed'])
        start = ("Test Case '-[Module." + parser.WRAPPER.replace('.', ' ') + "]' started.\n").encode()
        self.assertFalse(parser.cases(valid.replace(start, b'') + start)['all_expected_passed'])

    def test_typed_summary_and_public_progress_never_export_unknown_fields_or_privileged_claims(self):
        complete = completed_summary(); self.assertTrue(parser.summary_matches(complete))
        for key, value in (('run_execution_count', True), ('observer_passed', 40), ('phase', 'private-canary'),
                           ('release_qualified', True), ('nestedtestmanager_unverified', False), ('load_error_code', 2**32)):
            candidate = {**complete, key: value}; self.assertFalse(parser.summary_matches(candidate))
            actual = parser.public_progress({'state': 'observed', 'phase': 'completed', 'summary': candidate})
            self.assertNotIn('private-canary', json.dumps(actual))
        for value in ({'state': [], 'path': 'private-canary'}, {'state': 'observed', 'phase': 'completed',
                      'summary': {**complete, 'session': 'private-canary'}}):
            self.assertEqual(parser.public_progress(value), {'state': 'unavailable'})

    def test_private_progress_is_atomic_typed_bounded_and_never_follows_a_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); directory = root / parser.REPORT_RELATIVE
            self.assertEqual(parser.progress(root), {'state': 'absent'})
            directory.mkdir(parents=True); stage = directory / 'stage'; summary = directory / 'summary.json'
            stage.write_bytes(b'completed'); summary.write_text(json.dumps(completed_summary()))
            self.assertEqual(parser.progress(root)['phase'], 'completed')
            stage.write_bytes(b'payloadLoadStart'); self.assertEqual(parser.progress(root)['state'], 'incomplete')
            stage.write_bytes(b'completed'); outside = root / 'outside'; summary.rename(outside); summary.symlink_to(outside)
            self.assertEqual(parser.progress(root)['state'], 'unavailable')
            summary.unlink(); summary.write_bytes(b'x' * 16385)
            self.assertEqual(parser.progress(root)['state'], 'unavailable')

    def test_private_progress_rejects_redirected_ancestor_directories_and_duplicate_counter_keys(self):
        for component in ('Library', 'Caches', 'ayu-deferred-projection-loader'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); container = root / 'data'; container.mkdir()
                directory = container / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
                (directory / 'stage').write_bytes(b'completed'); (directory / 'summary.json').write_text(json.dumps(completed_summary()))
                redirected = next(path for path in (directory, *directory.parents) if path.name == component)
                outside = root / 'outside'; redirected.rename(outside); redirected.symlink_to(outside, target_is_directory=True)
                self.assertEqual(parser.progress(container), {'state': 'unavailable'})
                self.assertEqual(parser.public_progress(parser.progress(container)), {'state': 'unavailable'})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); directory = root / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
            (directory / 'stage').write_bytes(b'completed')
            value = json.dumps(completed_summary())
            value = value[:-1] + ', "run_total_failure_count": 0}'
            (directory / 'summary.json').write_text(value)
            self.assertEqual(parser.progress(root), {'state': 'unavailable'})

    def test_progress_precedes_ps_failure_and_never_grants_unknown_process_sampling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); paths = base.owned_fixture(root)
            directory = paths['data'] / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
            summary = completed_summary(); summary.update(phase='payloadLoadStart', checks_consistent=False)
            (directory / 'stage').write_bytes(b'payloadLoadStart'); (directory / 'summary.json').write_text(json.dumps(summary))
            calls = []
            def command(args, secrets, timeout=3):
                calls.append((args, timeout)); self.assertTrue(read.called, 'Progress must be read before process probes')
                return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False,
                        'stdout_bytes': 0, 'stderr_bytes': 0}, b''
            with patch.object(parser.modern.Path, 'home', return_value=root), \
                 patch.object(parser, 'progress', wraps=parser.progress) as read, \
                 patch.object(parser.modern.bootstrap, 'command', side_effect=command):
                value = parser.public_live(parser.live_observation(123, 123, base.IDENTIFIER,
                    parser.projection.digest(paths['app'] / parser.modern.CONTROL_APP), (), lambda: True, 'owned-control'))
            self.assertEqual(value['deferred_progress']['phase'], 'payloadLoadStart')
            self.assertTrue(value['deferred_progress_captured_while_child_alive'])
            self.assertFalse(value['xcode_identity_verified']); self.assertEqual(value['sample']['state'], 'notAttempted')
            self.assertFalse(any(parser.modern.bootstrap.SAMPLE_EXEC_WRAPPER in args for args, _ in calls))
            self.assertNotIn(str(root), json.dumps(value)); self.assertTrue(all(timeout <= 2 for _, timeout in calls))

    def test_observer_deadline_retains_only_earlier_progress_without_starting_late_probe_or_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); paths = base.owned_fixture(root); now = [0]
            directory = paths['data'] / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
            (directory / 'stage').write_bytes(b'completed'); (directory / 'summary.json').write_text(json.dumps(completed_summary()))
            calls = []
            def command(args, secrets, timeout=3):
                calls.append(args); now[0] = 19
                return base.complete(('123 1 123 ' + str(os.getuid()) + ' xcodebuild\n').encode())
            with patch.object(parser.modern.Path, 'home', return_value=root), patch.object(parser.time, 'monotonic', side_effect=lambda: now[0]), \
                 patch.object(parser.modern.bootstrap, 'command', side_effect=command):
                value = parser.public_live(parser.live_observation(123, 123, base.IDENTIFIER,
                    parser.projection.digest(paths['app'] / parser.modern.CONTROL_APP), (), lambda: True, 'owned-control'))
            self.assertEqual(value['reason'], 'observerDeadline'); self.assertEqual(len(calls), 1)
            self.assertEqual(value['deferred_progress']['phase'], 'completed'); self.assertEqual(value['sample']['state'], 'notAttempted')

    def test_compiler_failure_retains_redacted_log_before_any_device_is_created(self):
        for mutation, canary in (('wrapper_compile', 'private-compiler-failure-canary'),
                                 ('settings_failure', 'private-settings-failure-canary')):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'AYU_TEST_SECRET': canary}):
                root = Path(temporary).resolve(); report, calls = self.execute(root, mutation=mutation)
                self.assertFalse(report['passed']); self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls))
                log = (root / parser.OUTPUT / 'control-build.log').read_text()
                self.assertNotIn(canary, log); self.assertIn('REDACTED', log)
                if mutation == 'settings_failure':
                    self.assertEqual(report['failure'], 'deferred_wrapper_build_settings_failed')
                    self.assertFalse(any(args[:2] == ['xcodebuild', 'build'] for args, _ in calls))

    def test_failed_build_still_checks_clone_integrity_and_preserves_original_baseline(self):
        for mutation in ('clone_failed_resource', 'clone_failed_code'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                report, calls = self.execute(Path(temporary).resolve(), mutation=mutation)
                self.assertFalse(report['passed']); self.assertEqual(report['failure'], 'deferred_build_host_changed')
                self.assertFalse(report['wrapper_build_host']['integrity_verified'])
                self.assertTrue(report['original_built_products_unchanged']); self.assertTrue(report['original_products_unchanged'])
                self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls))

    def test_explicit_wrapper_entitlements_use_the_actual_wrapper_root_and_exact_typed_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); derived = root / 'private-wrapper-derived'
            intermediate = derived / 'Build/Intermediates.noindex'; intermediate.mkdir(parents=True)
            wrapper = derived / 'Build/Products/Tests.xctest'
            actual_input = intermediate / 'Unexpected-Simulated.xcent'; rights = {'get-task-allow': True}
            actual_input.write_bytes(plistlib.dumps(rights))
            raw = shlex.join(['/usr/bin/codesign', '--force', '--sign', '-', '--entitlements', str(actual_input), str(wrapper)]).encode()
            calls = []
            def call(label, args):
                calls.append(args)
                if '--verbose=4' in args:
                    return base.complete(('Identifier=' + parser.modern.CONTROL_TEST_ID + '\nSignature=adhoc\nTeamIdentifier=not set\n').encode())
                if '--entitlements' in args: Path(args[3]).write_bytes(plistlib.dumps(rights))
                return base.complete()
            result = parser.verify_wrapper_signing(derived, wrapper, raw, call)
            self.assertTrue(result['typed_signature_readback_matched']); self.assertEqual(result['actual_input_kind'], 'explicitFile')
            self.assertTrue(any('--verify' in args for args in calls))
            outside = root / 'private-derived/Build/Intermediates.noindex/Other.xcent'
            outside.parent.mkdir(parents=True); outside.write_bytes(plistlib.dumps(rights))
            escaped = shlex.join(['/usr/bin/codesign', '--sign', '-', '--entitlements', str(outside), str(wrapper)]).encode()
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_wrapper_entitlement_path_invalid$'):
                parser.verify_wrapper_signing(derived, wrapper, escaped, call)
            wrong = {'get-task-allow': False}
            def mismatch(label, args):
                if '--verbose=4' in args: return call(label, args)
                if '--entitlements' in args: Path(args[3]).write_bytes(plistlib.dumps(wrong))
                return base.complete()
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_wrapper_rights_changed$'):
                parser.verify_wrapper_signing(derived, wrapper, raw, mismatch)

    def test_cli_preserves_only_known_preflight_codes_without_native_or_compiler_claims(self):
        for error in (parser.projection.artifacts.ReplayError('application_source_changed'),
                      parser.modern.PlanError('modern_fixed_capture_mismatch'), parser.DeferredError('deferred_source_mismatch')):
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); output = io.StringIO()
                with patch.object(parser, 'prepare', side_effect=error), redirect_stdout(output):
                    code = parser.main(['prepare', '--root', str(root)])
                value = json.loads(output.getvalue()); self.assertEqual(code, 1)
                self.assertEqual(value['failure'], error.args[0]); self.assertEqual(value['phase'], 'prepare')
                self.assertTrue(value['failure_report_retained']); self.assertFalse(value['control']['attempted'])
                self.assertFalse(value['projection']['attempted']); self.assertFalse(value['tiny_control_compiled'])
                retained = root / parser.OUTPUT / 'result.json'
                self.assertEqual(retained.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(retained.read_text())['failure'], error.args[0])
                self.assertNotIn(str(root), output.getvalue())

    def test_unknown_preflight_error_never_calls_str_or_exports_private_arguments(self):
        class PrivateError(RuntimeError):
            def __str__(self): raise AssertionError('Stringifying a private exception is forbidden')
        for error in (PrivateError('private-secret-canary /private/session'),
                      parser.DeferredError('private-secret-canary /private/session'),
                      parser.projection.artifacts.ReplayError('application_source_changed', '/private/session')):
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); output = io.StringIO()
                with patch.object(parser, 'prepare', side_effect=error), redirect_stdout(output):
                    self.assertEqual(parser.main(['prepare', '--root', str(root)]), 1)
                value = json.loads(output.getvalue()); self.assertEqual(value['failure'], 'deferred_preflight_unavailable')
                self.assertNotIn('private-secret-canary', output.getvalue()); self.assertNotIn('/private/session', output.getvalue())
                self.assertNotIn('Traceback', output.getvalue())
                self.assertNotIn('/private/session', (root / parser.OUTPUT / 'result.json').read_text())

    def test_cli_failure_never_overwrites_or_poison_preexisting_prepared_evidence(self):
        for existing_result in (False, True):
            with self.subTest(existing_result=existing_result), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); directory = root / parser.OUTPUT; directory.mkdir(parents=True)
                original = directory / ('result.json' if existing_result else 'verification.json')
                original.write_bytes(b'preserved-evidence')
                output = io.StringIO()
                with patch.object(parser, 'prepare', side_effect=parser.modern.PlanError('modern_plan_output_already_exists')), redirect_stdout(output):
                    self.assertEqual(parser.main(['prepare', '--root', str(root)]), 1)
                self.assertFalse(json.loads(output.getvalue())['failure_report_retained']); self.assertEqual(original.read_bytes(), b'preserved-evidence')
                if not existing_result: self.assertFalse((directory / 'result.json').exists())

    def test_cli_failure_does_not_create_reports_through_unsafe_outputs_or_in_run_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); outside = root / 'outside'; outside.mkdir()
            (root / 'build').symlink_to(outside, target_is_directory=True)
            for action in ('prepare', 'run'):
                output = io.StringIO()
                with patch.object(parser, action, side_effect=parser.projection.artifacts.ReplayError('application_source_changed')), redirect_stdout(output):
                    self.assertEqual(parser.main([action, '--root', str(root)]), 1)
                self.assertFalse(json.loads(output.getvalue())['failure_report_retained'])
            self.assertEqual(list(outside.iterdir()), [])

    def test_prepare_failure_writer_uses_held_directory_descriptors_when_ancestor_is_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); outside = root / 'outside'; outside.mkdir()
            output = root / parser.OUTPUT; old_output = root / 'original-output'
            opened, swapped = [], []
            actual_open = os.open
            def raced_open(path, flags, mode=0o777, *, dir_fd=None):
                if path == 'result.json':
                    self.assertIsNotNone(dir_fd)
                    output.rename(old_output); output.symlink_to(outside, target_is_directory=True); swapped.append(True)
                descriptor = actual_open(path, flags, mode, dir_fd=dir_fd)
                opened.append(descriptor); return descriptor
            report = parser.preflight_failure(parser.projection.artifacts.ReplayError('application_source_changed'), 'prepare')
            with patch.object(parser.os, 'open', side_effect=raced_open):
                self.assertTrue(parser.retain_prepare_failure(parser.OUTPUT, root, report, True))
            self.assertEqual(swapped, [True]); self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(json.loads((old_output / 'result.json').read_text())['failure'], 'application_source_changed')
            for descriptor in opened:
                with self.assertRaises(OSError): os.fstat(descriptor)


class DeferredBuildLayoutTests(unittest.TestCase):
    def context(self, root, sibling=False, both=False):
        products = root / 'baseline/Build/Products'
        _plan, host, test = base.apple_fixture(products, sibling=sibling)
        (host / 'preserved-resource').write_bytes(b'private-resource-canary')
        (host / '_CodeSignature').mkdir()
        (host / '_CodeSignature/CodeResources').write_bytes(b'original-seal')
        if both: shutil.copytree(test, host / 'PlugIns' / test.name)
        original = parser.projection.snapshot_tree(host)
        context = parser.create_build_host(host, test, products, root / 'wrapper')
        self.assertEqual(parser.projection.snapshot_tree(host), original)
        return context

    def settings(self, context, hosted=False):
        host = context['host']; executable = str(host / parser.modern.CONTROL_APP)
        product = (host / 'PlugIns' if hosted else host.parent) / (parser.modern.CONTROL_TEST + '.xctest')
        return [{'target': parser.modern.CONTROL_TEST, 'buildSettings': {'TEST_HOST': executable,
            'BUNDLE_LOADER': executable, 'FULL_PRODUCT_NAME': product.name,
            'TARGET_BUILD_DIR': str(product.parent), 'CODESIGNING_FOLDER_PATH': str(product)}}]

    def product(self, path):
        return base.bundle(path, parser.modern.CONTROL_TEST_ID, parser.modern.CONTROL_TEST, 8)

    def test_clone_removes_only_matching_old_plugin_and_preserves_original_bytes_modes_and_inodes(self):
        for sibling, both in ((False, False), (True, False), (True, True)):
            with self.subTest(sibling=sibling, both=both), tempfile.TemporaryDirectory() as temporary:
                context = self.context(Path(temporary).resolve(), sibling, both)
                self.assertEqual(context['old_plugin_removed'], not sibling or both)
                self.assertEqual(list((context['host'] / 'PlugIns').iterdir()), [])
                self.assertEqual(parser.projection.snapshot_tree(context['source']), context['source_snapshot'])
                for relative, value in context['before'].items():
                    if value[0] == 'file':
                        self.assertNotEqual((context['host'] / relative).stat().st_ino, (context['source'] / relative).stat().st_ino)
                self.assertTrue(parser.verify_build_host(context)['original_control_unchanged'])

    def test_foreign_or_changed_old_plugin_and_extra_plugins_are_never_deleted_or_exempted(self):
        for mutation in ('bytes', 'identity', 'extraPlugin'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); products = root / 'baseline'
                _plan, host, test = base.apple_fixture(products, sibling=True)
                plugin = host / 'PlugIns' / test.name; shutil.copytree(test, plugin)
                if mutation == 'bytes': (plugin / parser.modern.CONTROL_TEST).write_bytes(b'foreign-code')
                elif mutation == 'identity':
                    info = plistlib.loads((plugin / 'Info.plist').read_bytes()); info['CFBundleIdentifier'] = 'foreign'
                    (plugin / 'Info.plist').write_bytes(plistlib.dumps(info))
                else: (host / 'PlugIns' / 'Foreign.xctest').mkdir()
                original = parser.projection.snapshot_tree(host)
                with self.assertRaises(parser.DeferredError): parser.create_build_host(host, test, products, root / 'wrapper')
                self.assertEqual(parser.projection.snapshot_tree(host), original)
                self.assertTrue(plugin.is_dir())

    def test_settings_are_actual_target_authority_for_only_hosted_or_sibling_product(self):
        for hosted in (False, True):
            with self.subTest(hosted=hosted), tempfile.TemporaryDirectory() as temporary:
                context = self.context(Path(temporary).resolve()); value = self.settings(context, hosted)
                product = parser.wrapper_location(encoded(value), context)
                self.assertEqual(product, Path(value[0]['buildSettings']['CODESIGNING_FOLDER_PATH']))
                self.product(product)
                self.assertEqual(parser.verify_build_host(context, require_product=True)['wrapper_product_copies'], 1)

    def test_settings_wrong_target_host_loader_product_path_or_signing_authority_fail_closed(self):
        for mutation in ('noTarget', 'duplicateTarget', 'wrongTarget', 'noSettings', 'host', 'loader', 'name',
                         'escape', 'relative', 'dot', 'privateCanary', 'signingPath'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                context = self.context(Path(temporary).resolve()); value = self.settings(context); settings = value[0]['buildSettings']
                if mutation == 'noTarget': value = []
                elif mutation == 'duplicateTarget': value *= 2
                elif mutation == 'wrongTarget': value[0]['target'] = 'WrongTarget'
                elif mutation == 'noSettings': value[0]['buildSettings'] = []
                elif mutation == 'host': settings['TEST_HOST'] = '/private/private-canary'
                elif mutation == 'loader': settings['BUNDLE_LOADER'] = '$(TEST_HOST)'
                elif mutation == 'name': settings['FULL_PRODUCT_NAME'] = 'Other.xctest'
                elif mutation == 'escape': settings['TARGET_BUILD_DIR'] += '/../Debug-iphonesimulator'
                elif mutation == 'relative': settings['TARGET_BUILD_DIR'] = 'Debug-iphonesimulator'
                elif mutation == 'dot': settings['TARGET_BUILD_DIR'] += '/.'
                elif mutation == 'privateCanary': settings['TARGET_BUILD_DIR'] = '/private/private-canary'
                else: settings['CODESIGNING_FOLDER_PATH'] = '/private/private-canary'
                with self.assertRaises(parser.DeferredError) as caught: parser.wrapper_location(encoded(value), context)
                self.assertNotIn('private-canary', str(caught.exception))
                self.assertNotIn('product', context)

    def test_settings_private_json_is_bounded_duplicate_free_and_never_publishes_parse_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.context(Path(temporary).resolve())
            for raw in (b'{"private-canary":', b'[{"a":1,"a":2}]', b'[{"a":NaN}]',
                        b' ' * (parser.MAX_XCRESULT_JSON_BYTES + 1), encoded([{'x': [0] * 4100}])):
                with self.subTest(size=len(raw)), self.assertRaisesRegex(parser.DeferredError, '^deferred_wrapper_settings_json_invalid$'):
                    parser.wrapper_location(raw, context)

    def test_stale_product_in_either_layout_and_symlink_paths_reject_before_build(self):
        for kind in ('hosted', 'sibling', 'symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); context = self.context(root); host = context['host']
                path = (host / 'PlugIns' if kind == 'hosted' else host.parent) / (parser.modern.CONTROL_TEST + '.xctest')
                if kind == 'symlink': path.symlink_to(root / 'private-canary')
                else: self.product(path)
                with self.assertRaises(parser.DeferredError): parser.wrapper_location(encoded(self.settings(context)), context)

    def test_clone_resource_keyset_modes_and_normalized_code_are_checked_even_without_product(self):
        for mutation in ('resource', 'extraFile', 'removedFile', 'mode', 'code', 'exeMode', 'extraPlugin', 'original',
                         'sealRemoved', 'sealMode', 'sealDirectoryMode'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                context = self.context(Path(temporary).resolve()); host = context['host']
                if mutation == 'resource': (host / 'preserved-resource').write_bytes(b'changed')
                elif mutation == 'extraFile': (host / 'unapproved').write_bytes(b'changed')
                elif mutation == 'removedFile': (host / 'preserved-resource').unlink()
                elif mutation == 'mode': (host / 'preserved-resource').chmod(0o700)
                elif mutation == 'code':
                    executable = host / parser.modern.CONTROL_APP; value = bytearray(executable.read_bytes()); value[256] ^= 1
                    executable.write_bytes(value)
                elif mutation == 'exeMode': (host / parser.modern.CONTROL_APP).chmod(0o600)
                elif mutation == 'extraPlugin': (host / 'PlugIns' / 'Foreign.xctest').mkdir()
                elif mutation == 'sealRemoved': (host / '_CodeSignature/CodeResources').unlink()
                elif mutation == 'sealMode': (host / '_CodeSignature/CodeResources').chmod(0o700)
                elif mutation == 'sealDirectoryMode':
                    directory = host / '_CodeSignature'; directory.chmod((directory.stat().st_mode & 0o777) ^ 0o040)
                else: (context['source'] / 'preserved-resource').write_bytes(b'changed')
                with self.assertRaises(parser.DeferredError): parser.verify_build_host(context)

    def test_only_original_root_seal_bytes_may_change_in_build_clone(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.context(Path(temporary).resolve())
            (context['host'] / '_CodeSignature/CodeResources').write_bytes(b'updated-seal')
            self.assertTrue(parser.verify_build_host(context)['root_seal_bytes_may_change'])
            self.assertEqual((context['source'] / '_CodeSignature/CodeResources').read_bytes(), b'original-seal')

    def test_final_verification_remains_bound_to_selected_product_and_rejects_divergent_second_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = self.context(Path(temporary).resolve()); selected = parser.wrapper_location(encoded(self.settings(context)), context)
            self.product(selected); duplicate = context['host'] / 'PlugIns' / selected.name
            shutil.copytree(selected, duplicate)
            self.assertEqual(parser.verify_build_host(context, require_product=True)['wrapper_product_copies'], 2)
            (duplicate / parser.modern.CONTROL_TEST).write_bytes(b'changed')
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_duplicate_wrapper_mismatch$'):
                parser.verify_build_host(context)


class DeferredXCResultDiagnosticTests(unittest.TestCase):
    def diagnostic(self, raw):
        try: parser.strict_xcresult_cases(raw)
        except parser.InvalidXCResult as error: return parser.xcresult_structure(raw, 'cases', error)
        return parser.xcresult_structure(raw, 'cases')

    def assert_private(self, value):
        public = json.dumps(value, ensure_ascii=True, allow_nan=False)
        self.assertNotIn('private-canary', public); self.assertNotIn('/private/raw', public)
        self.assertNotIn('RealisticOpaque', public)
        self.assertLess(len(public.encode()), 4096)
        self.assertNotIn('all_expected_passed', value)

    def test_exact_42_accounting_has_only_closed_counts_and_cannot_qualify_independently(self):
        value = self.diagnostic(encoded(summaries()))
        self.assertTrue(value['diagnostic_only']); self.assertEqual(value['state'], 'observed')
        self.assertEqual(value['first_rejection'], {'stage': 'none', 'reason': 'none'})
        self.assertEqual(value['case_identities'], {'known': 42, 'unknown': 0, 'missing': 0, 'invalid': 0})
        self.assertEqual(value['case_statuses']['success'], 42); self.assertEqual(value['test_nodes']['leaves'], 42)
        self.assertEqual(value['typed_objects']['ActionTestMetadata'], 42)
        self.assert_private(value)

    def test_first_schema_rejection_is_fixed_and_preserved_without_accepting_any_new_shape(self):
        for mutation, reason in (('leafChildren', 'leafHasSubtests'), ('unknownNode', 'unknownTestNodeType'),
                                 ('plainIdentifier', 'typedStringMismatch'), ('booleanStatus', 'stringShape'),
                                 ('unknownBranch', 'unconsumedBranch'), ('shadowLeaf', 'shadowTestMetadata'),
                                 ('wrongArray', 'typedArrayMismatch'), ('arrayShape', 'arrayShape'),
                                 ('invalidNode', 'invalidTestNode'), ('privateRootType', 'typedObjectMismatch')):
            with self.subTest(mutation=mutation):
                raw = summaries(); group = raw['summaries']['_values'][0]['testableSummaries']['_values'][0]['tests']['_values'][0]
                leaf = group['subtests']['_values'][0]
                if mutation == 'leafChildren': leaf['subtests'] = array([])
                elif mutation == 'unknownNode': leaf['_type']['_name'] = 'private-canary'
                elif mutation == 'plainIdentifier': leaf['identifier'] = '/private/raw'
                elif mutation == 'booleanStatus': leaf['testStatus'] = typed('String', _value=True)
                elif mutation == 'unknownBranch': raw['private-canary'] = {'tests': array([])}
                elif mutation == 'shadowLeaf': raw['private-canary'] = case('ForeignTests.testPrivate')
                elif mutation == 'wrongArray': group['subtests'] = []
                elif mutation == 'arrayShape': group['subtests']['_values'] = '/private/raw'
                elif mutation == 'invalidNode': group['subtests']['_values'][0] = '/private/raw'
                else: raw['_type']['_name'] = 'private-canary'
                value = self.diagnostic(encoded(raw))
                self.assertEqual(value['state'], 'observed'); self.assertEqual(value['first_rejection']['reason'], reason)
                self.assertEqual(value['first_rejection']['stage'], parser.XCRESULT_REJECTIONS[reason])
                self.assert_private(value)

    def test_unknown_identities_statuses_types_and_invalid_values_are_counted_without_retaining_them(self):
        leaves = [case(parser.WRAPPER), case('PrivateClass.testPrivate', 'private-canary /private/raw')]
        leaves += [typed('private-canary', identifier=string('/private/raw'), testStatus=string('Failure'))]
        leaves += [typed('ActionTestMetadata', testStatus=string('Skipped')),
                   typed('ActionTestSummary', identifier=True),
                   typed('ActionTestMetadata', identifier=string('private-canary'), testStatus='private-canary')]
        raw = summaries(leaves); raw['private-canary'] = {'_type': {'_name': {'private-canary': '/private/raw'}}}
        value = self.diagnostic(encoded(raw))
        self.assertEqual(value['typed_objects']['other'], 2)
        self.assertEqual(value['case_identities'], {'known': 1, 'unknown': 3, 'missing': 1, 'invalid': 1})
        self.assertEqual(value['case_statuses'], {'success': 1, 'failure': 1, 'skipped': 1, 'other': 1, 'missing': 1, 'invalid': 1})
        self.assertEqual(value['test_nodes']['unrecognized_outcomes'], 1)
        self.assert_private(value)

    def test_json_duplicates_encoding_and_existing_limits_fail_closed_with_zero_partial_counts(self):
        deep = {'private-canary': []}; child = deep['private-canary']
        for _ in range(33): nested = []; child.append(nested); child = nested
        fixtures = [(b'{"private-canary":', 'invalidJSON'), (b'\xff', 'invalidJSON'),
                    (b'{"private-canary":1,"private-canary":2}', 'duplicateKey'),
                    (b'{"private-canary":NaN}', 'nonfiniteJSON'),
                    (b'{"private-canary":1e999}', 'nonfiniteJSON'),
                    (b' ' * (parser.MAX_XCRESULT_JSON_BYTES + 1), 'byteLimit'),
                    (encoded(deep), 'depthLimit'), (encoded({'private-canary': [0] * 4100}), 'nodeLimit')]
        for raw, reason in fixtures:
            with self.subTest(reason=reason):
                value = self.diagnostic(raw)
                self.assertEqual(value['first_rejection'], {'stage': 'json', 'reason': reason})
                self.assertEqual(value['state'], 'unavailable')
                self.assertEqual(sum(value['typed_objects'].values()), 0)
                self.assertEqual(sum(value['case_identities'].values()), 0)
                self.assert_private(value)

    def test_root_reference_never_survives_closed_diagnostics_and_rejection_properties_are_validated(self):
        value = parser.xcresult_structure(encoded(invocation()), 'root')
        self.assertEqual(value['typed_objects']['Reference'], 1); self.assert_private(value)
        error = parser.InvalidXCResult('xcresult_cases_invalid', 'private-canary')
        self.assertEqual(error.reason, 'invalidStructure')
        error.reason = ['private-canary']
        value = parser.xcresult_structure(encoded(summaries()), 'cases', error)
        self.assertEqual(value['first_rejection']['reason'], 'none'); self.assert_private(value)
        with self.assertRaisesRegex(ValueError, '^Invalid result diagnostic document$'):
            parser.xcresult_structure(encoded(summaries()), ['/private/raw'])


class DiscoveryProfileTests(unittest.TestCase):
    def log(self, wrapper=None):
        value = native_log(execution_profile='discovery')
        return value if wrapper is None else value.replace(parser.DISCOVERY_WRAPPER.replace('.', ' ').encode(), wrapper.replace('.', ' ').encode())

    def metadata(self, status='Success'):
        leaves = [case(key) for key in sorted(parser.PAYLOAD_CASES)]
        leaves.append(case(parser.DISCOVERY_WRAPPER, status, module='AyuProjectionControlTests'))
        return summaries(leaves)

    def test_discovery_requires_sequential_41_then_one_wrapper_and_legacy_remains_distinct(self):
        value = parser.cases(self.log(), parser.DISCOVERY_WRAPPER)
        self.assertTrue(value['all_expected_passed']); self.assertEqual(value['passed'], 41)
        self.assertTrue(value['wrapper_passed']); self.assertEqual(value['invalid_case_overlap_count'], 0)
        self.assertFalse(parser.cases(self.log())['all_expected_passed'])
        self.assertTrue(parser.cases(native_log())['all_expected_passed'])
        self.assertFalse(parser.cases(native_log(), parser.DISCOVERY_WRAPPER)['all_expected_passed'])

    def test_nested_overlapping_reordered_duplicate_missing_failed_or_unknown_events_never_qualify(self):
        text = self.log().decode(); events = text.splitlines(keepends=True)
        variants = [text + text, ''.join(events[:-2]), ''.join(events[-2:] + events[:-2]),
                    ''.join([events[0], events[2], events[1], *events[3:]]),
                    text.replace("passed.", "failed.", 1), text.replace("passed.", "skipped.", 1),
                    text + "Test Case '-[PrivateTests testSecret]' passed.\n",
                    text + "Test Case '-[malformed]' failed.\n"]
        for variant in variants:
            with self.subTest(size=len(variant)):
                value = parser.cases(variant.encode(), parser.DISCOVERY_WRAPPER)
                self.assertFalse(value['all_expected_passed'])
                self.assertNotIn('PrivateTests', json.dumps(value))

    def test_exact_namespaces_are_required_even_for_known_basename_and_merged_events_are_counted(self):
        data = self.log()
        self.assertTrue(parser.cases(b'prefix:' + data.replace(b'\n', b''), parser.DISCOVERY_WRAPPER)['all_expected_passed'])
        first = sorted(parser.EXPECTED)[0].replace('.', ' ').encode()
        for prefix in (b'PrivateModule.', b'Wrong.Parent.'):
            modified = data.replace(first, prefix + first, 1)
            value = parser.cases(modified, parser.DISCOVERY_WRAPPER)
            self.assertFalse(value['all_expected_passed']); self.assertGreater(value['unknown_event_count'], 0)

    def test_native_metadata_profile_cannot_substitute_the_wrong_wrapper_or_infer_log_order(self):
        native = encoded(self.metadata())
        self.assertTrue(parser.strict_xcresult_cases(native, parser.DISCOVERY_WRAPPER)['all_expected_passed'])
        self.assertFalse(parser.strict_xcresult_cases(native)['all_expected_passed'])
        self.assertFalse(parser.strict_xcresult_cases(encoded(summaries()), parser.DISCOVERY_WRAPPER)['all_expected_passed'])
        self.assertFalse(parser.strict_xcresult_cases(encoded(self.metadata('Failure')), parser.DISCOVERY_WRAPPER)['all_expected_passed'])
        diagnostic = parser.xcresult_structure(native, 'cases', wrapper=parser.DISCOVERY_WRAPPER)
        self.assertEqual(diagnostic['case_identities']['known'], 42)
        self.assertEqual(diagnostic['case_identities']['unknown'], 0)
        for wrapper in (None, [], 'PrivateTests.testSecret'):
            with self.subTest(wrapper=repr(wrapper)), self.assertRaisesRegex(ValueError, '^deferred_execution_profile_invalid$'):
                parser.cases(b'', wrapper)

    def test_only_exact_omitted_empty_typed_array_is_an_allowed_schema_extension(self):
        empty = {'_type': {'_name': 'Array'}}
        self.assertEqual(parser._array(empty), [])
        for value in ({**empty, 'private-canary': []}, {'_type': {'_name': 'Array', 'private-canary': 1}},
                      {**empty, '_values': None}, {**empty, '_values': False}, {**empty, '_values': {}}, {}):
            with self.subTest(value=value), self.assertRaises(parser.InvalidXCResult): parser._array(value)
        raw = self.metadata('Failure')
        testable = raw['summaries']['_values'][0]['testableSummaries']['_values'][0]
        testable['tests']['_values'].append(typed('ActionTestSummaryGroup', subtests=empty))
        actual = parser.strict_xcresult_cases(encoded(raw), parser.DISCOVERY_WRAPPER)
        self.assertEqual(actual['case_records'], 42); self.assertEqual(actual['passed'], 41)
        self.assertEqual(actual['failed'], 1); self.assertFalse(actual['all_expected_passed'])
        raw['private-canary'] = {'tests': empty}
        with self.assertRaisesRegex(parser.InvalidXCResult, '^xcresult_cases_invalid$'):
            parser.strict_xcresult_cases(encoded(raw), parser.DISCOVERY_WRAPPER)

    def test_summaries_and_prepared_source_are_strictly_isolated_between_modes(self):
        for mode in ('deferred', 'discovery'):
            summary = completed_summary(mode)
            self.assertTrue(parser.summary_matches(summary, mode))
            self.assertFalse(parser.summary_matches(summary, 'discovery' if mode == 'deferred' else 'deferred'))
        summary = completed_summary('discovery'); summary['manual_suite_run_performed'] = True
        self.assertFalse(parser.summary_matches(summary, 'discovery'))
        with self.assertRaisesRegex(parser.DeferredError, '^deferred_summary_scope_invalid$'):
            parser.closed_summary(summary, 'discovery')
        for mode in (None, [], 'private-canary'):
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_execution_profile_invalid$'):
                parser.profile_configuration(mode)

    def test_pipeline_keeps_all_integrity_control_42_native_and_cleanup_gates_in_discovery_mode(self):
        fixture = DeferredRunnerTests()
        with tempfile.TemporaryDirectory() as temporary:
            report, calls = fixture.execute(Path(temporary).resolve(), execution_profile='discovery')
            self.assertTrue(report['passed'], report.get('failure')); self.assertEqual(report['execution_profile'], 'discovery')
            self.assertEqual(report['profile'], parser.DISCOVERY_PROFILE)
            self.assertTrue(report['native_xcresult_case_accounting_verified'])
            self.assertEqual(report['projection']['xcresult']['case_records'], 42)
            self.assertTrue(report['original_products_unchanged']); self.assertTrue(report['original_built_products_unchanged'])
            self.assertFalse(report['application_recompiled']); self.assertFalse(report['projection_tests_recompiled'])
            self.assertFalse(report['release_qualified']); self.assertFalse(report['original_bazel_host_executed'])
            self.assertEqual([timeout for args, timeout in calls if 'test-without-building' in args], [600, 600])
            self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'bootstatus'] for args, _ in calls))
        for option in ({'projection_code': 65}, {'xc': 'wrapperOnly'}, {'counter': 'observer_passed'}, {'cleanup': True},
                       {'mutation': 'payload_bytes'}, {'mutation': 'installed_bytes'}, {'control': 'failed'}):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                report, _ = fixture.execute(Path(temporary).resolve(), execution_profile='discovery', **option)
                self.assertFalse(report['passed']); self.assertFalse(report['native_xcresult_case_accounting_verified'])

    def test_prepare_profile_mismatch_rejects_before_any_build_or_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(parser.DeferredError, '^deferred_prepared_capture_mismatch$'):
                DeferredRunnerTests().execute(Path(temporary).resolve(), execution_profile='discovery', prepared_profile='deferred')

    def test_new_swift_returns_framework_suite_and_does_not_modify_or_manually_execute_captured_assertions(self):
        source = (Path(__file__).resolve().parents[1] / parser.DISCOVERY_SOURCE).read_text()
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), parser.DISCOVERY_SHA256)
        self.assertIn('override class var defaultTestSuite: XCTestSuite', source)
        self.assertIn('DiscoveryFactory.request(super.defaultTestSuite)', source)
        self.assertIn('composite.addTest(suite)', source); self.assertIn('composite.addTest(verification)', source)
        self.assertNotIn('suite.run()', source); self.assertNotIn('.runTest()', source); self.assertNotIn('XCTestSuite.default', source)
        self.assertIn('prepared.payload.testRun', source); self.assertIn('Bundle(for: type(of: testCase))', source)
        build = source[source.index('private static func build('):source.index('static func request(')]
        self.assertLess(build.index('hostIdentity()'), build.index('SandboxReport.emit('))
        self.assertLess(build.index('hostIdentity()'), build.index('ActiveUIKitProbe()'))
        self.assertLess(build.index('hostSceneVisible()'), build.index('ActiveUIKitProbe()'))
        self.assertLess(build.index('guard readiness == .completed'), build.index('bundle.loadAndReturnError()'))
        scene = source[source.index('static func hostSceneVisible()'):source.index('@MainActor\n    private static func build(')]
        self.assertNotIn('UIApplication', scene.replace('// This proof uses Foundation/POSIX only; it never creates UIApplication.', ''))
        self.assertIn('openat(parent, $0, flags)', scene); self.assertIn('O_NOFOLLOW', scene)
        self.assertIn('info.st_uid == getuid()', scene); self.assertIn('info.st_nlink == 1', scene)
        self.assertIn('info.st_size <= 128', scene); self.assertIn('Data("sceneVisible".utf8)', scene)
        self.assertIn('Thread.isMainThread && !hostSceneVisible()', source)
        expected = set(re.findall(r'"((?:ChatLocalArchiveProjectionTests|ChatLocalArchiveRowRendererTests|ChatLocalTextFilterProjectionTests)\.test\w+)"', source))
        self.assertEqual(expected, parser.EXPECTED)
        old = (Path(__file__).resolve().parents[1] / parser.WRAPPER_SOURCE).read_bytes()
        self.assertEqual(hashlib.sha256(old).hexdigest(), parser.WRAPPER_SHA256)

    def test_cli_selects_exact_discovery_profile_for_prepare_and_run_without_new_public_paths(self):
        for command in ('prepare', 'run'):
            with self.subTest(command=command), patch.object(parser, command, return_value={'passed': True}) as selected, redirect_stdout(io.StringIO()):
                self.assertEqual(parser.main([command, '--execution-profile', 'discovery']), 0)
                selected.assert_called_once_with(Path(parser.OUTPUT), None, execution_profile='discovery')
        failure = parser.preflight_failure(parser.DeferredError('deferred_source_mismatch'), 'prepare', 'discovery')
        self.assertEqual(failure['profile'], parser.DISCOVERY_PROFILE); self.assertFalse(failure['passed'])


if __name__ == '__main__': unittest.main()
