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


def native_log(outcome='passed'):
    def event(key, state):
        name, method = key.split('.')
        return "Test Case '-[Module." + name + ' ' + method + "]' " + state + '.\n'
    return (event(parser.WRAPPER, 'started') + ''.join(event(key, 'started') + event(key, outcome)
        for key in sorted(parser.EXPECTED)) + event(parser.WRAPPER, 'passed')).encode()


def completed_summary():
    return {'schema': 1, **parser.COUNTS, **parser.BOOLEANS, **parser.STRINGS}


class DeferredRunnerTests(unittest.TestCase):
    def execute(self, root, mutation=None, control='passed', payload='passed', xc='complete', counter=None,
                cleanup=False, identity=False, repeated=False):
        output = root / parser.OUTPUT; output.mkdir(parents=True)
        original = captured.captured(root, current=True)
        app = original / 'TEST_ROOT' / parser.modern.products.APP
        test_info = app / 'PlugIns' / parser.modern.products.TEST / 'Info.plist'
        value = plistlib.loads(test_info.read_bytes()); value['CFBundleIdentifier'] = 'org.livara.ayugram.tests.native-security.projectionTests'
        test_info.write_bytes(plistlib.dumps(value)); base.captured_resources(app)
        captured.ProjectionHostControlTests.refresh_capture(original)
        shutil.copytree(original, output / 'private-products')
        wrapper_source = (Path(__file__).resolve().parents[1] / parser.WRAPPER_SOURCE).read_bytes()
        (root / 'tools/fork_baseline').mkdir(parents=True)
        (root / 'tools/fork_baseline' / parser.WRAPPER_SOURCE).write_bytes(wrapper_source)
        (output / 'private-wrapper-source.swift').write_bytes(wrapper_source)
        parser.tiny.create_project(output / 'private-project')
        platform = root / 'SDK/iPhoneSimulator.platform'
        for relative in ('Developer/Library/Frameworks/XCTest.framework/XCTest', 'Developer/usr/lib/libXCTestSwiftSupport.dylib'):
            path = platform / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'first-party-SDK')
        calls, options, identifiers = [], [], []
        self.last_options = options
        def signed_line(path): return shlex.join(['/usr/bin/codesign', '--force', '--sign', '-', str(path)])
        def command(args, timeout=30, **extra):
            calls.append((args, timeout)); options.append((args, extra))
            if args == ['xcodebuild', '-version']: return base.complete(b'Xcode 27.1\nBuild version 27A9275\n')
            if '--show-sdk-version' in args: return base.complete(b'27.1')
            if '--show-sdk-platform-path' in args: return base.complete(str(platform).encode())
            if args[:2] in (['xcodebuild', 'build-for-testing'], ['xcodebuild', 'build']):
                if args[1] == 'build-for-testing':
                    self.assertEqual(args[args.index('-scheme') + 1], parser.modern.CONTROL_APP)
                    derived = Path(args[args.index('-derivedDataPath') + 1]); products = derived / 'Build/Products'
                    if mutation == 'control_compile': return base.complete(b'private-compiler-failure-canary', 65)
                    plan, host, test = base.apple_fixture(products, 1, False)
                    (products / 'control.xctestrun').write_bytes(plistlib.dumps(plan))
                    return base.complete((signed_line(host) + '\n' + signed_line(test) + '\n').encode())
                self.assertEqual(args[args.index('-target') + 1], parser.modern.CONTROL_TEST)
                for option in ('-scheme', '-derivedDataPath', '-destination'):
                    self.assertNotIn(option, args)
                derived = output / 'private-wrapper-derived'; products = derived / 'Build/Products'
                self.assertIn('SYMROOT=' + str(products), args)
                self.assertIn('OBJROOT=' + str(derived / 'Build/Intermediates.noindex'), args)
                host_executable = output / 'private-derived/Build/Products/Debug-iphonesimulator' / (parser.modern.CONTROL_APP + '.app') / parser.modern.CONTROL_APP
                self.assertTrue(host_executable.is_file())
                self.assertIn('TEST_HOST=' + str(host_executable), args)
                self.assertIn('BUNDLE_LOADER=' + str(host_executable), args)
                if mutation == 'wrapper_compile': return base.complete(b'private-compiler-failure-canary', 65)
                self.assertTrue(any(value.startswith('TEST_HOST=') for value in args))
                project = Path(args[args.index('-project') + 1]).parent
                self.assertNotIn('com.apple.product-type.application', (project / 'DeferredProjection.xcodeproj/project.pbxproj').read_text())
                test = base.bundle(products / 'Debug-iphonesimulator' / (parser.modern.CONTROL_TEST + '.xctest'),
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
                summary = completed_summary()
                if counter: summary[counter] = 0 if summary[counter] else 1
                directory = container / parser.REPORT_RELATIVE; directory.mkdir(parents=True)
                (directory / 'stage').write_bytes(b'completed'); (directory / 'summary.json').write_text(json.dumps(summary))
                if payload == 'timeout': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                return base.complete(native_log(payload), 0 if payload == 'passed' else 65)
            if parser.JSON_WRAPPER in args:
                value = invocation() if '--id' not in args else summaries([case(parser.WRAPPER)] if xc == 'wrapperOnly' else None)
                if xc == 'malformed': value = {'private-canary': 'raw'}
                Path(args[3]).write_text(json.dumps(value)); Path(args[3]).chmod(0o600)
                return base.complete()
            return base.complete()
        with patch.object(parser.projection, 'verify_current_checkout'), \
             patch.object(parser.modern, 'MANIFEST_SHA256', parser.projection.digest(original / 'manifest.json')), \
             patch.object(parser.modern, 'CAPTURE_SHA256', parser.projection.digest(original / 'capture.json')), \
             patch.object(parser.modern.Path, 'home', return_value=root):
            parser.projection.write(output / 'verification.json', parser.verification(output / 'private-products',
                parser.projection.validate_products(output / 'private-products', parser.SOURCE)))
            report = parser.run(root=root, command=command)
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
        for mutation in ('control_compile', 'wrapper_compile', 'wrapper_identity', 'wrapper_platform', 'control_mutated',
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
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'AYU_TEST_SECRET': 'private-compiler-failure-canary'}):
            root = Path(temporary).resolve(); report, calls = self.execute(root, mutation='wrapper_compile')
            self.assertFalse(report['passed']); self.assertFalse(any(args[:3] == ['xcrun', 'simctl', 'create'] for args, _ in calls))
            log = (root / parser.OUTPUT / 'control-build.log').read_text()
            self.assertNotIn('private-compiler-failure-canary', log); self.assertIn('REDACTED', log)

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


if __name__ == '__main__': unittest.main()
