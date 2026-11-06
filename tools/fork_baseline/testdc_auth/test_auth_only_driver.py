# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable synthetic checks only. Never invokes Apple tools or authentication."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import auth_only_driver as d


def native_log(stages=None, outcome='passed', name=d.CASE):
    stages = list(d.SUCCESS_TRACES[1]) if stages is None else stages
    cls, method = name.split('.')
    return ("Test Case '-[Module." + cls + ' ' + method + "]' started.\n"
        + ''.join(d.PREFIX + stage + '\n' for stage in stages)
        + "Test Case '-[Module." + cls + ' ' + method + "]' " + outcome + ' (0.1 seconds).\n').encode()


def plan_v1():
    return {'__xctestrun_metadata__': {'FormatVersion': 1}, d.TARGET: {
        'IsUITestBundle': True, 'ProductModuleName': d.TARGET,
        'TestHostPath': '__TESTROOT__/Test-Runner.app',
        'TestBundlePath': '__TESTHOST__/PlugIns/' + d.TARGET + '.xctest',
        'DependentProductPaths': ['__TESTROOT__/Test-Runner.app']}}


def synthetic_gate():
    gate = d.expected_review()
    gate['startup_control_run'] = d.STARTUP_CONTROL_RUN
    return gate


class EvidenceTests(unittest.TestCase):
    def test_accepts_exact_code_flow(self):
        self.assertEqual(d.parse_auth_evidence(native_log(), 0), list(d.SUCCESS_TRACES[1]))

    def test_accepts_stock_sent_code_success(self):
        self.assertEqual(d.parse_auth_evidence(native_log(d.SUCCESS_TRACES[0]), 0), list(d.SUCCESS_TRACES[0]))

    def test_rejects_green_xctest_without_auth_marker(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(d.SUCCESS_TRACES[1][:-1]), 0)

    def test_rejects_auth_marker_without_case(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence((d.PREFIX + 'authorizedRootInstalled\n').encode(), 0)

    def test_rejects_nonzero_process_exit(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(), 1)

    def test_rejects_bool_process_exit(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(), False)

    def test_rejects_skipped_failed_crashed_cases(self):
        for outcome in ('skipped', 'failed', 'crashed'):
            with self.subTest(outcome=outcome), self.assertRaises(d.Rejected):
                d.parse_auth_evidence(native_log(outcome=outcome), 0)

    def test_rejects_all_blocked_terminals(self):
        for stage in ('disabled', 'signupRequired', 'passwordRequired', 'timedOut', 'blockedOrUnexpected', 'rejectedBeforeSubmission'):
            with self.subTest(stage=stage), self.assertRaises(d.Rejected):
                d.parse_auth_evidence(native_log([stage]), 0)

    def test_rejects_repeated_auth_markers(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(list(d.SUCCESS_TRACES[1]) + ['authorizedRootInstalled']), 0)

    def test_rejects_repeated_code_attempt(self):
        stages = list(d.SUCCESS_TRACES[1]); stages.insert(3, 'publicCodeSubmitted')
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(stages), 0)

    def test_rejects_case_retry_even_second_pass(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(outcome='failed') + native_log(), 0)

    def test_rejects_unknown_and_malformed_stage(self):
        for stage in ('arbitraryPrivateText', 'authorizedRootInstalled trailing', 'authorizedRootInstalled123'):
            with self.subTest(stage=stage), self.assertRaises(d.Rejected):
                d.parse_auth_evidence(native_log([stage]), 0)

    def test_rejects_other_test_case(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(name='OtherClass.testOther'), 0)

    def test_rejects_case_before_stages(self):
        data = native_log([]) + ''.join(d.PREFIX + s + '\n' for s in d.SUCCESS_TRACES[1]).encode()
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(data, 0)

    def test_rejects_reordered_stages(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(native_log(reversed(d.SUCCESS_TRACES[1])), 0)

    def test_rejects_oversized_output(self):
        with self.assertRaises(d.Rejected):
            d.parse_auth_evidence(b'x' * (d.MAX_COMMAND_BYTES + 1), 0)

    def test_unrelated_raw_bytes_never_appear_in_result(self):
        marker = b'PRIVATE_SYNTHETIC_DO_NOT_EXPORT'
        parsed = d.parse_auth_evidence(marker + b'\n' + native_log() + marker, 0)
        result = d.closed_result(); result['stages'] = parsed
        self.assertNotIn(marker.decode(), json.dumps(result))
        self.assertNotIn('stdout', result)
        self.assertNotIn('xcresult', result)


class GateAndPinsTests(unittest.TestCase):
    def test_auth_only_review_blocks_setup_mode_before_any_subprocess(self):
        self.assertEqual(d.REVIEWED_EXECUTION_SCOPE, 'authOnly')
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess')), \
                patch('subprocess.run', side_effect=AssertionError('No subprocess')), self.assertRaises(d.Rejected) as caught:
            d.execute(None, None, None, None, None, setup_only=True)
        self.assertEqual(caught.exception.code, 'reviewGate')

    def test_native_execution_disabled_without_subprocess(self):
        with (patch.object(d, 'EXECUTION_REVIEWED', False),
              patch('subprocess.Popen', side_effect=AssertionError('must not execute')),
              patch('subprocess.run', side_effect=AssertionError('must not execute'))):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(d.main(['--execute']), 2)
        self.assertEqual(json.loads(out.getvalue())['state'], 'disabled')

    def test_execute_direct_call_also_disabled(self):
        with patch.object(d, 'EXECUTION_REVIEWED', False), self.assertRaises(d.Rejected) as caught:
            d.execute(None, None, None, None, None)
        self.assertEqual(caught.exception.code, 'disabled')

    def test_unset_startup_review_run_never_accepted(self):
        with patch.object(d, 'REVIEWED_STARTUP_RUN', 0), self.assertRaises(d.Rejected):
            d.validate_review(synthetic_gate())

    def test_enabled_cli_missing_arguments_rejected_before_any_command(self):
        with (patch.object(d, 'EXECUTION_REVIEWED', True),
              patch('subprocess.Popen', side_effect=AssertionError('must not execute')),
              patch('subprocess.run', side_effect=AssertionError('must not execute'))):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(d.main(['--execute']), 2)
        self.assertEqual(json.loads(out.getvalue()), {'state': 'rejected', 'failure': 'inputIntegrity'})

    def test_accepts_closed_synthetic_gate_only_when_run_pinned(self):
        with patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN):
            d.validate_review(synthetic_gate())

    def test_gate_requires_actual_startup_source_and_launches(self):
        for field in ('startup_control_run', 'simulator_source_run', 'simulator_artifact_id', 'startup_authorization_launches'):
            gate = synthetic_gate(); gate[field] = 0
            with self.subTest(field=field), patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN), self.assertRaises(d.Rejected):
                d.validate_review(gate)

    def test_gate_requires_passed_source_control_and_origin(self):
        for field, value in (('startup_control_job_passed', False), ('source_codesign_verified', False), ('application_source', '0' * 40)):
            gate = synthetic_gate(); gate[field] = value
            with self.subTest(field=field), patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN), self.assertRaises(d.Rejected):
                d.validate_review(gate)

    def test_gate_rejects_unknown_fields(self):
        gate = synthetic_gate(); gate['raw_log'] = 'PRIVATE_SYNTHETIC'
        with patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN), self.assertRaises(d.Rejected):
            d.validate_review(gate)

    def test_gate_rejects_bool_integer_substitution(self):
        gate = synthetic_gate(); gate['schema'] = True
        with patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN), self.assertRaises(d.Rejected):
            d.validate_review(gate)

    def test_review_receipt_rejects_changed_python_driver(self):
        gate = synthetic_gate(); gate['driver_source_sha256'] = '0' * 64
        with patch.object(d, 'REVIEWED_STARTUP_RUN', d.STARTUP_CONTROL_RUN), self.assertRaises(d.Rejected):
            d.validate_review(gate)

    def test_frozen_private_source_hashes_match(self):
        data = d.read_file(d.BASE / 'driver-review-pins.json')
        self.assertEqual(hashlib.sha256(data).hexdigest(), d.REVIEW_PINS_SHA256)
        pins = json.loads(data)
        for relative, digest in pins['private_files'].items():
            self.assertEqual(d.sha256(d.BASE / relative), digest)
        self.assertEqual(len(pins['application_sources']), 11)

    def test_changed_application_source_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = {}
            for i in range(11):
                path = root / 'submodules' / (str(i) + '.swift'); path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'synthetic')
                expected[path.relative_to(root).as_posix()] = d.sha256(path)
            d.validate_source(root, {'application_sources': expected})
            path.write_bytes(b'changed')
            with self.assertRaises(d.Rejected):
                d.validate_source(root, {'application_sources': expected})

    def test_source_traversal_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            pins = {'application_sources': {'submodules/../outside' + str(i): '0' * 64 for i in range(11)}}
            with self.assertRaises(d.Rejected):
                d.validate_source(temporary, pins)

    def test_artifact_wrong_metadata_refused_before_content(self):
        with self.assertRaises(d.Rejected):
            d.validate_artifact(Path('/not-a-file'), {'artifact_id': 1})

    def test_environment_strips_tokens_and_loader_overrides(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic', 'TELEGRAM_API_HASH': '<synthetic-test-value>', 'DYLD_INSERT_LIBRARIES': '/synthetic', 'PYTHONPATH': '/synthetic'}):
            clean = d.clean_environment()
        for key in ('GH_TOKEN', 'TELEGRAM_API_HASH', 'DYLD_INSERT_LIBRARIES', 'PYTHONPATH'):
            self.assertNotIn(key, clean)


class PlanTests(unittest.TestCase):
    def test_accepts_v1_and_v2_single_ui_target(self):
        one = plan_v1(); target = one[d.TARGET]
        two = {'__xctestrun_metadata__': {'FormatVersion': 2}, 'TestConfigurations': [{'TestTargets': [target]}]}
        self.assertIs(d.plan_target(one), target)
        self.assertIs(d.plan_target(two), target)

    def test_rejects_extra_targets(self):
        plan = plan_v1(); plan['Other'] = copy.deepcopy(plan[d.TARGET])
        with self.assertRaises(d.Rejected):
            d.plan_target(plan)

    def test_rejects_wrong_ui_target(self):
        for field, value in (('IsUITestBundle', False), ('ProductModuleName', 'Wrong')):
            plan = plan_v1(); plan[d.TARGET][field] = value
            with self.subTest(field=field), self.assertRaises(d.Rejected):
                d.plan_target(plan)

    def test_rejects_filtered_cases(self):
        for field in ('OnlyTestIdentifiers', 'SkipTestIdentifiers'):
            plan = plan_v1(); plan[d.TARGET][field] = ['Other/testAnything']
            with self.subTest(field=field), self.assertRaises(d.Rejected):
                d.plan_target(plan)

    def test_binds_full_app_without_mutating_generated_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); plan = plan_v1(); original = copy.deepcopy(plan)
            result = d.bind_plan(plan, root / 'Telegram.app', root / 'bound.xctestrun')
            self.assertEqual(plan, original)
            target = result[d.TARGET]
            self.assertEqual(target['UITargetAppBundleIdentifier'], d.BUNDLE)
            self.assertEqual(target['SystemAttachmentLifetime'], 'keepNever')
            self.assertEqual(target['UserAttachmentLifetime'], 'keepNever')
            self.assertEqual(target['EnvironmentVariables'], {'AYU_PRIVATE_TESTDC_AUTH_SMOKE': 'PUBLIC_TESTDC_AUTH_ONLY'})

    def test_rejects_existing_unexpected_target_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_v1(); plan[d.TARGET]['UITargetAppPath'] = '/personal.app'
            with self.assertRaises(d.Rejected):
                d.bind_plan(plan, Path(temporary) / 'Telegram.app', Path(temporary) / 'bound')

    def test_rejects_preinjected_auth_execution_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_v1(); plan[d.TARGET]['EnvironmentVariables'] = {'AYU_PRIVATE_TESTDC_AUTH_SMOKE': 'bad'}
            with self.assertRaises(d.Rejected):
                d.bind_plan(plan, Path(temporary) / 'Telegram.app', Path(temporary) / 'bound')

    def test_product_path_cannot_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for value in ('__TESTROOT__/../outside', '/personal.app', '__UNKNOWN__/x', '__TESTROOT__/x\\y'):
                with self.subTest(value=value), self.assertRaises(d.Rejected):
                    d.resolve_product(value, root)

    def test_project_has_only_ui_test_and_no_screenshot_code(self):
        path = d.BASE / 'private_helpers/tiny_ui_test_project.py'
        spec = importlib.util.spec_from_file_location('private_tiny_fixture', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            source = d.read_file(d.BASE / 'OfficialTestDCAuthenticationSmoke.swift')
            project = d.create_project(module, Path(temporary).resolve() / 'driver', source)
            pbx = (project['project'] / 'project.pbxproj').read_text()
            self.assertNotIn('com.apple.product-type.application', pbx)
            self.assertEqual(pbx.count('com.apple.product-type.bundle.ui-testing'), 1)
            self.assertNotIn('XCTAttachment(', source.decode())
            self.assertNotIn('.screenshot()', source.decode())
            self.assertNotIn('.debugDescription', source.decode())

    def test_swift_attempted_failures_fail_and_disabled_skips(self):
        source = d.read_file(d.BASE / 'OfficialTestDCAuthenticationSmoke.swift').decode()
        self.assertIn('func testOfficialPublicTestDCAuthenticationOnly() throws', source)
        self.assertIn('throw XCTSkip(', source)
        self.assertIn('XCTFail(', source)
        for stage in ('signupRequired', 'passwordRequired', 'timedOut', 'blockedOrUnexpected'):
            self.assertIn('fail(.' + stage + ')', source)


class OwnedSimulatorTests(unittest.TestCase):
    OLD = '11111111-1111-1111-1111-111111111111'
    NEW = '22222222-2222-2222-2222-222222222222'

    def fixture(self, shutdown_throws=False, wrong_return=False, delete_fails=False):
        rows = [{'udid': self.OLD, 'name': 'Existing personal simulator', 'deviceTypeIdentifier': d.DEVICE}]
        calls = []
        def command(arguments, **kwargs):
            calls.append(arguments)
            if arguments[2:5] == ['list', 'devices', '--json']:
                return 0, json.dumps({'devices': {d.RUNTIME: rows}}).encode()
            if arguments[2] == 'create':
                rows.append({'udid': self.NEW, 'name': arguments[3], 'deviceTypeIdentifier': d.DEVICE})
                return 0, (self.OLD if wrong_return else self.NEW).encode()
            if arguments[2] == 'shutdown' and shutdown_throws:
                raise d.Rejected('commandTimeout')
            if arguments[2] == 'delete':
                if delete_fails:
                    raise d.Rejected('commandFailure')
                rows[:] = [row for row in rows if row['udid'] != arguments[3]]
            return 0, b''
        return d.OwnedSimulator(command), rows, calls

    def test_create_and_cleanup_only_new_owned_uuid(self):
        simulator, rows, calls = self.fixture()
        self.assertEqual(simulator.create(), self.NEW)
        self.assertTrue(simulator.cleanup())
        self.assertEqual([row['udid'] for row in rows], [self.OLD])
        for call in calls:
            if call[2] in ('terminate', 'shutdown', 'delete'):
                self.assertEqual(call[3], self.NEW)

    def test_cleanup_still_deletes_after_shutdown_timeout(self):
        simulator, rows, calls = self.fixture(shutdown_throws=True)
        simulator.create()
        self.assertTrue(simulator.cleanup())
        self.assertTrue(any(call[2] == 'delete' for call in calls))

    def test_wrong_create_result_does_not_delete_preexisting_device(self):
        simulator, rows, calls = self.fixture(wrong_return=True)
        with self.assertRaises(d.Rejected):
            simulator.create()
        self.assertTrue(simulator.cleanup())
        self.assertEqual([row['udid'] for row in rows], [self.OLD])

    def test_delete_failure_cannot_claim_cleanup(self):
        simulator, rows, calls = self.fixture(delete_fails=True)
        simulator.create()
        with self.assertRaises(d.Rejected):
            simulator.cleanup()
        self.assertIn(self.NEW, [row['udid'] for row in rows])

    def test_renamed_device_is_not_destructively_cleaned(self):
        simulator, rows, calls = self.fixture()
        simulator.create(); rows[-1]['name'] = 'Not owned now'
        with self.assertRaises(d.Rejected):
            simulator.cleanup()
        self.assertFalse(any(call[2] == 'delete' for call in calls))

    def test_no_creation_means_no_cleanup_commands(self):
        simulator, rows, calls = self.fixture()
        self.assertTrue(simulator.cleanup())
        self.assertEqual(calls, [])

    def test_duplicate_device_ids_refused(self):
        value = {'devices': {d.RUNTIME: [{'udid': self.OLD}, {'udid': self.OLD}]}}
        with self.assertRaises(d.Rejected):
            d.device_rows(json.dumps(value).encode())

    def test_duplicate_top_level_inventory_cannot_prove_absence(self):
        data = ('{"devices":{"' + d.RUNTIME + '":[{"udid":"' + self.NEW + '"}]},"devices":{}}').encode()
        with self.assertRaises(d.Rejected):
            d.device_rows(data)

    def test_duplicate_runtime_inventory_cannot_prove_absence(self):
        data = ('{"devices":{"' + d.RUNTIME + '":[{"udid":"' + self.NEW + '"}],"' + d.RUNTIME + '":[]}}').encode()
        with self.assertRaises(d.Rejected):
            d.device_rows(data)

    def test_cleanup_rejects_ambiguous_final_absence(self):
        simulator, rows, calls = self.fixture()
        simulator.create()
        underlying = simulator.command
        deleted = False
        def ambiguous_command(arguments, **kwargs):
            nonlocal deleted
            if arguments[2] == 'delete':
                deleted = True
                return 0, b''  # Synthetic failure: claimed success, owned row remains.
            if deleted and arguments[2:5] == ['list', 'devices', '--json']:
                real = json.dumps({d.RUNTIME: rows})
                return 0, ('{"devices":' + real + ',"devices":{}}').encode()
            return underlying(arguments, **kwargs)
        simulator.command = ambiguous_command
        with self.assertRaises(d.Rejected):
            simulator.cleanup()
        self.assertIn(self.NEW, [row['udid'] for row in rows])

    def test_container_outside_owned_device_refused(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, 'home', return_value=Path(temporary)):
            simulator, _, _ = self.fixture(); simulator.identifier = self.NEW
            base = Path(temporary) / 'Library/Developer/CoreSimulator/Devices' / self.NEW / 'data/Containers'
            base.mkdir(parents=True)
            outsider = Path(temporary) / 'personal'; outsider.mkdir()
            with self.assertRaises(d.Rejected):
                d.owned_container(outsider, simulator, 'data')


class PrivateIOTests(unittest.TestCase):
    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fixture.json'; path.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(d.Rejected):
                d.json_object(path)

    def test_private_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'file'; d.private_write(path, b'old')
            with self.assertRaises(FileExistsError):
                d.private_write(path, b'new')
            self.assertEqual(path.read_bytes(), b'old')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_symlink_input_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary); (path / 'real').write_bytes(b'synthetic')
            (path / 'link').symlink_to(path / 'real')
            with self.assertRaises(d.Rejected):
                d.read_file(path / 'link')

    def test_private_command_does_not_print_output(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()) as out:
            command = d.PrivateCommands(temporary)
            code, data = command([sys.executable, '-c', 'print("PRIVATE_SYNTHETIC")'])
            self.assertEqual((code, data), (0, b'PRIVATE_SYNTHETIC\n'))
            self.assertEqual(out.getvalue(), '')
            self.assertEqual((Path(temporary) / 'command-0001.bin').stat().st_mode & 0o777, 0o600)

    def test_successful_child_may_close_output_before_exit_within_original_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, data = d.PrivateCommands(temporary)([sys.executable, '-c',
                'import os,time; os.close(1); os.close(2); time.sleep(0.1)'], timeout=2)
            self.assertEqual((code, data), (0, b''))

    def test_child_that_hangs_after_output_eof_still_obeys_original_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(d.Rejected) as caught:
                d.PrivateCommands(temporary)([sys.executable, '-c',
                    'import os,time; os.close(1); os.close(2); time.sleep(3)'], timeout=0.1)
            self.assertEqual(caught.exception.code, 'commandTimeout')

    def test_private_command_timeout_is_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(d.Rejected) as caught:
                d.PrivateCommands(temporary)([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=0.1)
            self.assertEqual(caught.exception.code, 'commandTimeout')

    def test_private_command_output_limit_is_closed(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(d, 'MAX_COMMAND_BYTES', 128):
            with self.assertRaises(d.Rejected) as caught:
                d.PrivateCommands(temporary)([sys.executable, '-c', 'print("x" * 500)'])
            self.assertEqual(caught.exception.code, 'commandOutputLimit')

    def test_private_command_nonzero_does_not_publish_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(d.Rejected) as caught:
                d.PrivateCommands(temporary)([sys.executable, '-c', 'print("PRIVATE_SYNTHETIC"); raise SystemExit(1)'])
            self.assertEqual(str(caught.exception), 'commandFailure')

    def test_hanging_inherited_pipe_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(d.Rejected) as caught:
                d.PrivateCommands(temporary)([sys.executable, '-c', 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(10)"])'], timeout=2)
            self.assertEqual(caught.exception.code, 'commandTimeout')


class FreshPrivateSeedTests(unittest.TestCase):
    def setUp(self):
        path = d.BASE / 'private_helpers/storage_evidence.py'
        spec = importlib.util.spec_from_file_location('private_storage_fixture', path)
        self.storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.storage)

    def test_seeds_only_absent_fresh_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            d.seed_fresh_private(root, self.storage)
            path = root / 'Library/Preferences' / (d.BUNDLE + '.plist')
            self.assertEqual(plistlib.loads(path.read_bytes()),
                {self.storage.STORAGE_SELECTION: {'version': 1, 'kind': 'private'}})

    def test_existing_preferences_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            d.seed_fresh_private(root, self.storage)
            path = root / 'Library/Preferences' / (d.BUNDLE + '.plist')
            before = path.read_bytes()
            with self.assertRaises((d.Rejected, ValueError)):
                d.seed_fresh_private(root, self.storage)
            self.assertEqual(path.read_bytes(), before)

    def test_existing_test_store_refused_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            history = root / 'Library/Application Support/AyuGram/telegram-ui-tests-data'
            history.mkdir(parents=True); sentinel = history / 'synthetic'; sentinel.write_bytes(b'preserve')
            with self.assertRaises(d.Rejected):
                d.seed_fresh_private(root, self.storage)
            self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_dangling_test_root_symlink_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            history = root / 'Library/Application Support/AyuGram/telegram-ui-tests-data'
            history.parent.mkdir(parents=True); history.symlink_to(root / 'missing')
            with self.assertRaises(d.Rejected):
                d.seed_fresh_private(root, self.storage)
            self.assertTrue(history.is_symlink())

    def test_existing_ordinary_account_store_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            history = root / 'Library/Application Support/AyuGram/telegram-data'
            history.mkdir(parents=True)
            with self.assertRaises((d.Rejected, ValueError)):
                d.seed_fresh_private(root, self.storage)
            self.assertTrue(history.is_dir())


class ProductIntegrityTests(unittest.TestCase):
    @staticmethod
    def thin(kind=2, platform=7):
        return struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, kind, 1, 24, 0, 0) + struct.pack('<6I', 0x32, 24, platform, 0, 0, 0)

    def test_accepts_synthetic_arm64_simulator_runner_and_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic'
            for kind in (2, 8):
                path.write_bytes(self.thin(kind))
                self.assertTrue(d.simulator_binary(path, kind))

    def test_rejects_device_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic'; path.write_bytes(self.thin(platform=2))
            self.assertFalse(d.simulator_binary(path, 2))

    def test_rejects_wrong_product_kind(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic'; path.write_bytes(self.thin(kind=8))
            self.assertFalse(d.simulator_binary(path, 2))

    def test_rejects_truncated_macho(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic'; path.write_bytes(self.thin()[:-1])
            self.assertFalse(d.simulator_binary(path, 2))

    def test_accepts_valid_universal_runner_arm64_slice(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'synthetic'
            thin = self.thin()
            table = struct.pack('>2I', 0xcafebabe, 1) + struct.pack('>5I', 0x0100000c, 0, 256, len(thin), 8)
            path.write_bytes(table + bytes(256 - len(table)) + thin)
            self.assertTrue(d.simulator_binary(path, 2))
            self.assertFalse(d.simulator_binary(path, 8))

    def test_snapshot_detects_source_change_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'binary'; path.write_bytes(b'synthetic')
            before = d.snapshot_tree(root); path.write_bytes(b'changed')
            self.assertNotEqual(d.snapshot_tree(root), before)
            (root / 'link').symlink_to(path)
            with self.assertRaises(d.Rejected):
                d.snapshot_tree(root)


if __name__ == '__main__':
    unittest.main()
