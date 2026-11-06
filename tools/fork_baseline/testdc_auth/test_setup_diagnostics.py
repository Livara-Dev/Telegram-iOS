# SPDX-License-Identifier: GPL-2.0-or-later
# Setup-only control-flow fixtures, 2026-11-06. No Apple tools/network/auth.
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import auth_only_driver as d
import ci_gate as c


class SetupOnlyTests(unittest.TestCase):
    def setUp(self):
        scope = patch.object(d, 'REVIEWED_EXECUTION_SCOPE', 'setupOnly')
        scope.start()
        self.addCleanup(scope.stop)

    PRIVATE = 'SYNTHETIC_PRIVATE_PATH_AND_MESSAGE_MUST_NOT_ESCAPE'

    def run_setup(self, fail_phase=None, error_type=ValueError, cleanup_error=False, post_seed_error=False, metadata_reason=None):
        original_result = d.closed_result
        captured = {}
        commands = []
        self.seed_binding_events = []
        metadata_spec = importlib.util.spec_from_file_location('setup_error_metadata_fixture', d.BASE / 'private_helpers/owned_simulator_metadata.py')
        metadata_module = importlib.util.module_from_spec(metadata_spec); metadata_spec.loader.exec_module(metadata_module)

        def result(**kwargs):
            value = original_result(**kwargs)
            captured['report'] = value
            return value

        def boundary():
            if captured['report']['phase'] == fail_phase:
                if metadata_reason is not None: raise metadata_module.PlanError(metadata_reason)
                raise error_type(self.PRIVATE)

        def snapshot(_):
            boundary()
            return {}

        def extract(_, path):
            path.mkdir()
            return path

        def project(_, path, source):
            path.mkdir()
            return {'project': path / 'Fixture.xcodeproj'}

        def bind(plan, app, destination):
            boundary()
            destination.write_bytes(b'SYNTHETIC_PLAN_NOT_EXECUTABLE')

        def seed(*args):
            boundary()
            self.seed_binding_events.append('seed')

        def revalidate(*args):
            boundary()
            if post_seed_error and 'seed' in self.seed_binding_events:
                raise ValueError(self.PRIVATE)
            self.seed_binding_events.append('verify:' + args[3])

        def container(path, simulator, kind):
            boundary()
            return Path(path)

        class Simulator:
            def __init__(self, command, metadata_helper=None): self.name = 'SYNTHETIC_OWNED'
            def create(self):
                boundary()
                return '12345678-1234-4234-8234-123456789012'
            def verify(self): boundary()
            def cleanup(self):
                if cleanup_error: raise PermissionError(SetupOnlyTests.PRIVATE)
                return True

        class Commands:
            def __init__(self, directory): pass
            def __call__(self, args, **kwargs):
                commands.append(args)
                boundary()
                if 'test-without-building' in args:
                    raise AssertionError('Setup diagnostic must never invoke native UI authentication')
                if args[:2] == ['xcodebuild', '-version']:
                    return 0, b'Xcode 27.1\nBuild version 27A9275'
                if '--show-sdk-version' in args:
                    return 0, b'27.1'
                if 'build-for-testing' in args:
                    derived = Path(args[args.index('-derivedDataPath') + 1])
                    products = derived / 'Build/Products'; products.mkdir(parents=True)
                    (products / 'generated.xctestrun').write_bytes(plistlib.dumps({'fixture': True}))
                if args[1:5] == ['simctl', 'list', 'runtimes', '--json']:
                    return 0, json.dumps({'runtimes': [{'identifier': d.RUNTIME, 'isAvailable': True}]}).encode()
                if 'get_app_container' in args:
                    return 0, b'/SYNTHETIC_OWNER_BOUND_CONTAINER'
                return 0, b''

        helpers = {'preserved_app': SimpleNamespace(extract_app=extract,
            main_app_only=lambda *args, **kwargs: {'main_code_unchanged': True, 'other_entitlements_preserved': True},
            main_code_digest=lambda *args: 'SYNTHETIC_CODE_DIGEST'),
            'storage_evidence': object(), 'tiny_ui_test_project': object(),
            'owned_simulator_metadata': SimpleNamespace(PlanError=metadata_module.PlanError,
                owned_container_capability=lambda *args: (boundary(), SimpleNamespace(path=Path('/SYNTHETIC_OWNER_BOUND_CONTAINER')))[1],
                revalidate_container_capability=revalidate)}
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            replacements = {'closed_result': result, 'PrivateCommands': Commands, 'OwnedSimulator': Simulator,
                'load_helpers': lambda pins: helpers, 'validate_source': lambda *args: Path(temp),
                'validate_artifact': lambda *args: None, 'snapshot_tree': snapshot, 'create_project': project,
                'generated_products': lambda plan, products: (products / 'Fixture-Runner.app', products / 'Fixture.xctest'),
                'bind_plan': bind, 'owned_container': container, 'seed_fresh_private': seed}
            for key, value in replacements.items(): stack.enter_context(patch.object(d, key, value))
            stack.enter_context(patch.object(d.platform, 'system', return_value='Darwin'))
            stack.enter_context(patch.object(d.platform, 'machine', return_value='arm64'))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No real subprocess')))
            stack.enter_context(patch('subprocess.run', side_effect=AssertionError('No real subprocess')))
            output = Path(temp) / 'result.json'
            synthetic_review = Path(temp) / 'synthetic-setup-review.json'
            synthetic_review.write_text(json.dumps(d.expected_review()))
            value = d.execute(Path(temp) / 'fixture.ipa', d.BASE / 'artifact-evidence.json', Path(temp),
                synthetic_review, output, setup_only=True)
            serialized = output.read_text()
            self.assertNotIn(self.PRIVATE, serialized)
            self.assertFalse(any('test-without-building' in args for args in commands))
        c.exportable(value)
        return value

    def test_complete_setup_keeps_every_auth_success_flag_false(self):
        value = self.run_setup()
        self.assertTrue(value['setup_completed'])
        self.assertTrue(value['cleanup_passed'])
        self.assertEqual(value['execution_scope'], 'setupOnly')
        self.assertEqual(value['phase'], 'setupComplete')
        self.assertIsNone(value['exception_kind'])
        for key in ['passed', 'native_xctest_passed', 'authentication_completed', 'physical_device_verified']:
            self.assertFalse(value[key])
        self.assertEqual(value['stages'], [])
        self.assertEqual(self.seed_binding_events, ['verify:data', 'verify:app', 'seed', 'verify:data', 'verify:app'])

    def test_closed_phase_identifies_each_setup_failure_without_private_exception(self):
        phases = ['planBind', 'runtimeInventory', 'simulatorCreate', 'boot', 'install',
            'dataContainer', 'appContainer', 'installedIntegrity', 'privateSeed', 'contacts', 'sourceIntegrity']
        for phase in phases:
            with self.subTest(phase=phase):
                value = self.run_setup(fail_phase=phase)
                self.assertEqual(value['phase'], phase)
                self.assertEqual(value['exception_kind'], 'ValueError')
                self.assertEqual(value['failure'], 'internal')
                self.assertFalse(value['setup_completed'])
                self.assertTrue(value['cleanup_passed'])
                self.assertFalse(value['passed'])

    def test_postseed_binding_failure_cannot_mark_seed_or_setup_as_verified(self):
        value = self.run_setup(post_seed_error=True)
        self.assertEqual(self.seed_binding_events, ['verify:data', 'verify:app', 'seed'])
        self.assertEqual(value['phase'], 'privateSeed')
        self.assertFalse(value['private_selection_seeded'])
        self.assertFalse(value['setup_completed'])
        self.assertFalse(value['authentication_completed'])
        self.assertTrue(value['cleanup_passed'])

    def test_known_pinned_helper_error_is_reported_only_as_closed_metadata_reason(self):
        value = self.run_setup(fail_phase='dataContainer', metadata_reason='owned_container_not_unique')
        self.assertEqual(value['metadata_failure'], 'owned_container_not_unique')
        self.assertEqual(value['exception_kind'], 'otherException')
        self.assertEqual(value['phase'], 'dataContainer')
        self.assertFalse(value['private_selection_seeded'])
        self.assertFalse(value['authentication_completed'])

    def test_container_os_failure_retains_only_builtin_kind(self):
        value = self.run_setup(fail_phase='dataContainer', error_type=FileNotFoundError)
        self.assertEqual(value['phase'], 'dataContainer')
        self.assertEqual(value['exception_kind'], 'FileNotFoundError')

    def test_cleanup_failure_prevents_setup_success(self):
        value = self.run_setup(cleanup_error=True)
        self.assertTrue(value['setup_completed'])
        self.assertFalse(value['cleanup_passed'])
        self.assertEqual(value['phase'], 'cleanup')
        self.assertEqual(value['exception_kind'], 'PermissionError')
        self.assertEqual(value['failure'], 'cleanup')
        self.assertFalse(value['passed'])

    def test_cleanup_permission_error_does_not_erase_primary_setup_failure(self):
        value = self.run_setup(fail_phase='runtimeInventory', cleanup_error=True)
        self.assertEqual(value['phase'], 'runtimeInventory')
        self.assertEqual(value['failure'], 'internal')
        self.assertEqual(value['exception_kind'], 'ValueError')
        self.assertEqual(value['cleanup_failure'], 'cleanup')
        self.assertEqual(value['cleanup_exception_kind'], 'PermissionError')
        self.assertFalse(value['cleanup_passed'])
        self.assertFalse(value['setup_completed'])

    def test_setup_only_review_blocks_auth_mode_before_any_subprocess(self):
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess')), \
                patch('subprocess.run', side_effect=AssertionError('No subprocess')), self.assertRaises(d.Rejected) as caught:
            d.execute(None, None, None, None, None)
        self.assertEqual(caught.exception.code, 'reviewGate')


class ClosedDiagnosticTests(unittest.TestCase):
    def setUp(self):
        scope = patch.object(d, 'REVIEWED_EXECUTION_SCOPE', 'setupOnly')
        scope.start()
        self.addCleanup(scope.stop)

    def setup_success(self):
        value = d.closed_result(setup_only=True)
        for key in ['setup_completed', 'driver_compiled', 'signature_checks_passed', 'private_selection_seeded', 'cleanup_passed']:
            value[key] = True
        value['phase'] = 'setupComplete'
        return value

    def test_exception_classifier_does_not_format_messages_or_custom_class_names(self):
        class PrivateException(Exception):
            def __str__(self): raise AssertionError('No formatting')
        self.assertEqual(d.builtin_exception_kind(PrivateException()), 'otherException')
        self.assertEqual(d.builtin_exception_kind(ValueError('private')), 'ValueError')
        self.assertEqual(d.builtin_exception_kind(FileNotFoundError('private')), 'FileNotFoundError')
        PrivateException.__name__ = 'ValueError'; PrivateException.__module__ = 'builtins'
        self.assertEqual(d.builtin_exception_kind(PrivateException()), 'otherException')

    def test_unknown_phase_kind_or_scope_is_never_exported(self):
        for key, item in [('phase', 'private-path'), ('exception_kind', 'private-message'), ('execution_scope', 'unknown')]:
            value = self.setup_success(); value[key] = item
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(value)

    def test_complete_auth_green_record_is_rejected_under_setup_only_review(self):
        value = d.closed_result(setup_only=False)
        for key in ['setup_completed', 'driver_compiled', 'signature_checks_passed',
                'private_selection_seeded', 'cleanup_passed', 'passed', 'native_xctest_passed', 'authentication_completed']:
            value[key] = True
        value['phase'] = 'authComplete'
        value['stages'] = list(d.SUCCESS_TRACES[1])
        self.assertEqual(d.REVIEWED_EXECUTION_SCOPE, 'setupOnly')
        with self.assertRaises(d.Rejected): c.exportable(value)

    def test_cleanup_metadata_remains_closed_and_consistent_with_cleanup_flag(self):
        for key, item in [('cleanup_failure', 'private-message'), ('cleanup_exception_kind', 'private-message'),
                ('cleanup_failure', 'cleanup'), ('cleanup_exception_kind', 'PermissionError')]:
            value = self.setup_success(); value[key] = item
            with self.subTest(key=key, item=item), self.assertRaises(d.Rejected): c.exportable(value)

    def test_setup_success_cannot_be_relabelled_as_authentication(self):
        for key, item in [('passed', True), ('native_xctest_passed', True), ('authentication_completed', True),
                ('stages', list(d.SUCCESS_TRACES[1]))]:
            value = self.setup_success(); value[key] = item
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(value)

    def test_setup_completed_requires_actual_compilation_signature_and_seed_flags(self):
        for key in ['driver_compiled', 'signature_checks_passed', 'private_selection_seeded']:
            value = self.setup_success(); value[key] = False
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(value)

    def test_setup_only_cli_reports_green_setup_without_auth_pass(self):
        value = self.setup_success()
        args = ['--execute', '--setup-only', '--ipa', 'fixture', '--artifact-evidence', 'fixture',
            '--source-root', 'fixture', '--review-gate', 'fixture', '--result', 'fixture']
        with patch.object(d, 'execute', return_value=value) as invoke, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(d.main(args), 0)
        self.assertTrue(invoke.call_args.kwargs['setup_only'])
        self.assertEqual(json.loads(out.getvalue()), {'state': 'setupCompleted', 'failure': None, 'execution_scope': 'setupOnly'})
        self.assertFalse(value['passed'])

    def test_setup_only_cli_cannot_turn_auth_success_into_green_setup(self):
        value = self.setup_success(); value['passed'] = True; value['authentication_completed'] = True
        args = ['--execute', '--setup-only', '--ipa', 'fixture', '--artifact-evidence', 'fixture',
            '--source-root', 'fixture', '--review-gate', 'fixture', '--result', 'fixture']
        with patch.object(d, 'execute', return_value=value), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(d.main(args), 1)
        self.assertEqual(json.loads(out.getvalue())['state'], 'failed')

    def test_setup_only_cli_cleanup_failure_is_not_green(self):
        value = self.setup_success(); value['cleanup_passed'] = False; value['failure'] = 'cleanup'
        args = ['--execute', '--setup-only', '--ipa', 'fixture', '--artifact-evidence', 'fixture',
            '--source-root', 'fixture', '--review-gate', 'fixture', '--result', 'fixture']
        with patch.object(d, 'execute', return_value=value), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.main(args), 1)


if __name__ == '__main__':
    unittest.main()
