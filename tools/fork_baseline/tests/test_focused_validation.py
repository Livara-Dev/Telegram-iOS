"""Focused native repair validation cannot accidentally compile/publish an app."""
import ast
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline

WORKFLOW = Path(__file__).resolve().parents[3] / '.github/workflows/archive-integration.yml'
EXPECTED = (
    '//submodules/PasscodeCore:PasscodeCoreTests',
    '//submodules/LocalMessageArchive:LocalMessageArchiveTests',
    '//submodules/LocalMessageArchive:LocalArchivePlaybackTests',
)
PROJECTION = EXPECTED + (
    '//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests',
)
CORE_ONLY = ('//submodules/TelegramCore:TelegramCoreTests',)


def fixture():
    return {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'IOS_BUNDLE_ID': 'org.livara.ayugram'}


class FocusedValidationTests(unittest.TestCase):
    def run_focused(self, root, fail=None, projection=False, projection_only=False, core_only=False):
        invoked = []
        def command(args, env, secrets):
            self.assertIn('test', args)
            self.assertNotIn('build', args)
            self.assertNotIn('TELEGRAM_API_ID', env)
            self.assertNotIn('TELEGRAM_API_HASH', env)
            target = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--target='))
            invoked.append(target)
            if target == fail:
                raise baseline.InvalidSetup('synthetic repair failure')
        (root / 'versions.json').write_text('{"xcode":"27.1"}')
        with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'preflight', return_value=Path('/bazel')), patch.object(baseline, 'run_redacted', side_effect=command), patch.object(baseline.subprocess, 'run'), patch.object(baseline, 'output', return_value='c' * 40), patch.object(baseline, 'publish_artifact') as publish, contextlib.redirect_stdout(io.StringIO()):
            if fail:
                with self.assertRaisesRegex(baseline.InvalidSetup, 'synthetic repair failure'):
                    baseline.build('simulator', fixture(), 1, integration_tests=True, tests_only=True, focused_tests=not (projection or projection_only or core_only), projection_tests=projection, projection_only_tests=projection_only, core_only_tests=core_only)
            else:
                baseline.build('simulator', fixture(), 1, integration_tests=True, tests_only=True, focused_tests=not (projection or projection_only or core_only), projection_tests=projection, projection_only_tests=projection_only, core_only_tests=core_only)
            publish.assert_not_called()
        self.assertFalse((root / 'build/fork-baseline-simulator').exists())
        return invoked, json.loads((root / 'build/fork-evidence/integration-tests.json').read_text())

    def test_only_fixed_repair_targets_run_and_summary_does_not_claim_full_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory))
        self.assertEqual(tuple(invoked), EXPECTED)
        self.assertEqual(summary['tests'], dict.fromkeys(EXPECTED, 'passed'))
        self.assertEqual(summary['source_commit'], 'c' * 40)
        self.assertEqual(summary['validation_scope'], 'focused')
        self.assertFalse(summary['full_integration_suite_requested'])
        self.assertFalse(summary['full_application_built'])
        self.assertFalse(summary['physical_device_verified'])

    def test_focused_failure_attempts_remaining_repairs_but_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory), fail=EXPECTED[0])
        self.assertEqual(tuple(invoked), EXPECTED)
        self.assertEqual(summary['tests'], {target: 'failed' if target == EXPECTED[0] else 'passed' for target in EXPECTED})
        self.assertEqual(summary['validation_scope'], 'focused')
        self.assertFalse(summary['full_integration_suite_requested'])

    def test_focused_option_is_rejected_before_credentials_tools_or_building(self):
        for mode, integration, tests_only in [('simulator', False, False), ('simulator', True, False), ('unsigned-device', False, False), ('device', True, True), ('preflight', False, False)]:
            with self.subTest(mode=mode, integration=integration, tests_only=tests_only), patch.object(baseline, 'preflight') as preflight, patch.object(baseline, 'configuration') as configuration:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build(mode, {}, 1, integration_tests=integration, tests_only=tests_only, focused_tests=True)
                preflight.assert_not_called()
                configuration.assert_not_called()

    def test_projection_runs_only_fixed_four_targets_and_cannot_publish_an_app(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory), projection=True)
        self.assertEqual(tuple(invoked), PROJECTION)
        self.assertEqual(tuple(baseline.PROJECTION_TEST_TARGETS), PROJECTION)
        self.assertEqual(summary['tests'], dict.fromkeys(PROJECTION, 'passed'))
        self.assertEqual(summary['validation_scope'], 'projection')
        self.assertFalse(summary['full_integration_suite_requested'])
        self.assertFalse(summary['full_application_built'])
        self.assertFalse(summary['physical_device_verified'])

    def test_projection_failure_still_retests_every_shared_host(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory), fail=PROJECTION[-1], projection=True)
        self.assertEqual(tuple(invoked), PROJECTION)
        self.assertEqual(summary['tests'], {target: 'failed' if target == PROJECTION[-1] else 'passed' for target in PROJECTION})
        self.assertEqual(summary['validation_scope'], 'projection')
        self.assertFalse(summary['full_application_built'])

    def test_projection_only_runs_one_fixed_target_and_never_claims_other_suites(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory), projection_only=True)
        self.assertEqual(tuple(invoked), (PROJECTION[-1],))
        self.assertEqual(summary['tests'], {PROJECTION[-1]: 'passed'})
        self.assertEqual(summary['validation_scope'], 'projection-only')
        self.assertFalse(summary['full_application_built'])
        self.assertFalse(summary['full_integration_suite_requested'])
        self.assertFalse(summary['physical_device_verified'])

    def test_projection_only_rejects_conflicting_flags_and_full_build_modes(self):
        for mode, integration, tests_only, focused, projection in [
                ('simulator', False, False, False, False), ('simulator', True, False, False, False),
                ('device', True, True, False, False), ('simulator', True, True, True, False),
                ('simulator', True, True, False, True)]:
            with self.subTest(mode=mode, focused=focused, projection=projection), patch.object(baseline, 'configuration') as configuration:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build(mode, {}, 1, integration_tests=integration, tests_only=tests_only, focused_tests=focused, projection_tests=projection, projection_only_tests=True)
                configuration.assert_not_called()

    def test_core_only_runs_the_existing_core_bundle_without_build_or_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invoked, summary = self.run_focused(root, core_only=True)
            collection = json.loads((root / 'build/fork-evidence/prebuild-telegram-core/collection.json').read_text())
            self.assertEqual(collection['target'], CORE_ONLY[0])
            self.assertFalse(any(root.rglob('*.ipa')))
        self.assertEqual(tuple(baseline.CORE_ONLY_TEST_TARGETS), CORE_ONLY)
        self.assertTrue(set(CORE_ONLY).issubset(baseline.INTEGRATION_TEST_TARGETS))
        self.assertTrue(set(CORE_ONLY).issubset(baseline.TEST_TARGET_PATHS))
        self.assertEqual(tuple(invoked), CORE_ONLY)
        self.assertEqual(summary['tests'], dict.fromkeys(CORE_ONLY, 'passed'))
        self.assertEqual(summary['validation_scope'], 'core-only')
        self.assertTrue(summary['tests_only'])
        self.assertFalse(summary['full_integration_suite_requested'])
        self.assertFalse(summary['full_application_build_requested'])
        self.assertFalse(summary['full_application_built'])
        self.assertFalse(summary['physical_device_verified'])

    def test_core_only_failure_is_retained_without_invoking_predecessor_or_app_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            invoked, summary = self.run_focused(Path(directory), fail=CORE_ONLY[0], core_only=True)
        self.assertEqual(tuple(invoked), CORE_ONLY)
        self.assertEqual(summary['tests'], {CORE_ONLY[0]: 'failed'})
        self.assertEqual(summary['validation_scope'], 'core-only')
        self.assertFalse(summary['full_integration_suite_requested'])
        self.assertFalse(summary['full_application_built'])

    def test_core_only_rejects_invalid_modes_and_every_conflicting_scope_before_setup(self):
        cases = [('simulator', False, False, {}), ('simulator', True, False, {}),
                 ('simulator', False, True, {}), ('unsigned-device', False, False, {}),
                 ('device', True, True, {}), ('preflight', False, False, {})]
        cases += [('simulator', True, True, {flag: True}) for flag in ('focused_tests', 'projection_tests', 'projection_only_tests')]
        for mode, integration, tests_only, flags in cases:
            with self.subTest(mode=mode, flags=flags), patch.object(baseline, 'preflight') as preflight, patch.object(baseline, 'configuration') as configuration:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build(mode, {}, 1, integration_tests=integration, tests_only=tests_only, core_only_tests=True, **flags)
                preflight.assert_not_called()
                configuration.assert_not_called()

    def test_core_only_cli_routes_only_the_fixed_scope_and_rejects_conflicts_before_setup(self):
        valid = ['baseline.py', 'simulator', '--integration-tests', '--tests-only', '--core-only-tests']
        with patch.object(sys, 'argv', valid), patch.object(baseline, 'build') as build, patch.object(baseline, 'preflight') as preflight:
            self.assertEqual(baseline.main(), 0)
            build.assert_called_once()
            self.assertEqual(build.call_args.args[0], 'simulator')
            self.assertEqual(build.call_args.kwargs, {'integration_tests': True, 'tests_only': True,
                'focused_tests': False, 'projection_tests': False, 'projection_only_tests': False, 'core_only_tests': True})
            preflight.assert_not_called()
        for other in ('--focused-tests', '--projection-tests', '--projection-only-tests', '--target=//unreviewed:Target'):
            with self.subTest(other=other), patch.object(sys, 'argv', valid + [other]), patch.object(baseline, 'preflight') as preflight, patch.object(baseline, 'configuration') as configuration, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    baseline.main()
                self.assertEqual(error.exception.code, 2)
                preflight.assert_not_called()
                configuration.assert_not_called()
        for argv in (['preflight', '--core-only-tests'], ['simulator', '--core-only-tests'], ['simulator', '--integration-tests', '--core-only-tests']):
            with self.subTest(argv=argv), patch.object(sys, 'argv', ['baseline.py'] + argv), patch.object(baseline, 'preflight') as preflight, patch.object(baseline, 'configuration') as configuration, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(baseline.main(), 1)
                preflight.assert_not_called()
                configuration.assert_not_called()

    def test_failed_capture_cannot_replace_the_original_projection_failure(self):
        order = []
        def products(*args, **kwargs):
            order.append('products')
            raise zipfile.BadZipFile('private exception text must be omitted')
        def probe(*args, **kwargs):
            order.append('probe')
            self.assertGreater(kwargs['started_at'], 0)
        with tempfile.TemporaryDirectory() as directory, patch.object(baseline, 'retain_projection_products', side_effect=products), patch.object(baseline, 'capture_projection_launch', side_effect=probe):
            root = Path(directory)
            invoked, summary = self.run_focused(root, fail=PROJECTION[-1], projection_only=True)
            collection = (root / 'build/fork-evidence/projection-products-collection.json').read_text()
        self.assertEqual(order, ['products', 'probe'])
        self.assertEqual(invoked, [PROJECTION[-1]])
        self.assertEqual(summary['tests'], {PROJECTION[-1]: 'failed'})
        self.assertNotIn('private exception text', collection)
        self.assertEqual(json.loads(collection)['state'], 'unavailable')

    def test_projection_scope_rejects_invalid_modes_and_conflicting_flags_before_setup(self):
        for mode, integration, tests_only, focused in [
                ('simulator', False, False, False), ('simulator', True, False, False),
                ('unsigned-device', False, False, False), ('device', True, True, False),
                ('preflight', False, False, False), ('simulator', True, True, True)]:
            with self.subTest(mode=mode, integration=integration, tests_only=tests_only, focused=focused), patch.object(baseline, 'preflight') as preflight, patch.object(baseline, 'configuration') as configuration:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build(mode, {}, 1, integration_tests=integration, tests_only=tests_only, focused_tests=focused, projection_tests=True)
                preflight.assert_not_called()
                configuration.assert_not_called()

    def test_cli_scopes_are_mutually_exclusive_before_preflight(self):
        with patch.object(sys, 'argv', ['baseline.py', 'simulator', '--integration-tests', '--tests-only', '--focused-tests', '--projection-tests']), patch.object(baseline, 'preflight') as preflight, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                baseline.main()
            self.assertEqual(error.exception.code, 2)
            preflight.assert_not_called()

    def test_full_summary_remains_all_requested_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'versions.json').write_text('{}')
            with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'output', return_value='d' * 40):
                baseline.retain_integration_test_summary({EXPECTED[0]: 'failed'})
            summary = json.loads((root / 'build/fork-evidence/integration-tests.json').read_text())
        self.assertEqual(set(summary['tests']), set(baseline.INTEGRATION_TEST_TARGETS))
        self.assertEqual(summary['tests'][EXPECTED[0]], 'failed')
        self.assertEqual(summary['validation_scope'], 'full')
        self.assertTrue(summary['full_integration_suite_requested'])
        self.assertTrue(all(value == 'not_run' for target, value in summary['tests'].items() if target != EXPECTED[0]))


class FocusedWorkflowRoutingTests(unittest.TestCase):
    def section(self, job):
        return re.search(r'(?ms)^  ' + re.escape(job) + r':\n(.*?)(?=^  [a-z-]+:\n|\Z)', WORKFLOW.read_text()).group(1)

    def condition(self, job, event, scope, device):
        section = self.section(job)
        match = re.search(r'^    if: (.+)$', section, re.M)
        if not match:
            return True
        return bool(self.expression(match.group(1), event, scope, device))

    def expression(self, expression, event, scope, device, replay_source='c' * 40, replay_run=''):
        # Interpret the checked-in boolean/equality gates and the one fixed
        # concurrency formatter. No arbitrary Python calls or code execution.
        expression = expression.replace('github.event_name', 'event').replace('inputs.validation_scope', 'scope').replace('inputs.unsigned_device', 'device').replace('inputs.replay_source', 'source').replace('inputs.replay_run', 'run').replace('||', 'or').replace('&&', 'and')
        inputs = {'event': event, 'scope': scope, 'device': device, 'source': replay_source, 'run': replay_run}
        def value(node):
            if isinstance(node, ast.Name):
                return inputs[node.id]
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
                return node.value
            if isinstance(node, ast.BoolOp):
                self.assertIn(type(node.op), (ast.And, ast.Or))
                result = value(node.values[0])
                for item in node.values[1:]:
                    if isinstance(node.op, ast.And):
                        result = value(item) if result else result
                    else:
                        result = result if result else value(item)
                return result
            if isinstance(node, ast.Call):
                self.assertIsInstance(node.func, ast.Name)
                self.assertEqual(node.func.id, 'format')
                self.assertEqual(len(node.args), 2)
                self.assertEqual(node.keywords, [])
                template = value(node.args[0])
                self.assertIn(template, {'ayugram-startup-replay-{0}', 'ayugram-projection-replay-{0}'})
                return template.replace('{0}', value(node.args[1]))
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                self.assertIn(type(node.ops[0]), (ast.Eq, ast.NotEq))
                equal = value(node.left) == value(node.comparators[0])
                return equal if isinstance(node.ops[0], ast.Eq) else not equal
            self.fail('Unsupported workflow job condition syntax')
        return value(ast.parse(expression, mode='eval').body)

    def test_focused_dispatch_starts_only_native_test_job_even_if_device_requested(self):
        for device in (False, True):
            self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'focused', device))
            self.assertFalse(self.condition('application-build', 'workflow_dispatch', 'focused', device))
            self.assertFalse(self.condition('unsigned-device-build', 'workflow_dispatch', 'focused', device))

    def test_projection_dispatch_starts_only_fixed_native_test_job(self):
        for device in (False, True):
            with self.subTest(device=device):
                self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'projection', device))
                for job in ('application-build', 'unsigned-device-build', 'native-api-probe', 'startup-artifact-replay'):
                    self.assertFalse(self.condition(job, 'workflow_dispatch', 'projection', device))
        self.assertIn('options: [full, native-only, core-only, focused, projection, projection-only, custom-options-ui, probe, startup]', WORKFLOW.read_text())
        self.assertIn('projection) repair_options+=(--projection-tests)', WORKFLOW.read_text())

    def test_projection_only_dispatch_skips_every_other_build_or_probe(self):
        for device in (False, True):
            self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'projection-only', device))
            for job in ('application-build', 'unsigned-device-build', 'native-api-probe', 'startup-artifact-replay'):
                self.assertFalse(self.condition(job, 'workflow_dispatch', 'projection-only', device))
        self.assertIn('projection-only) repair_options+=(--projection-only-tests)', WORKFLOW.read_text())

    def test_core_only_dispatch_starts_only_native_core_job_in_its_own_preserved_queue(self):
        for device in (False, True):
            with self.subTest(device=device):
                self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'core-only', device))
                for job in ('application-build', 'unsigned-device-build', 'custom-options-ui', 'native-api-probe', 'startup-artifact-replay'):
                    self.assertFalse(self.condition(job, 'workflow_dispatch', 'core-only', device))
        source = self.section('integration-tests')
        self.assertIn('core-only) repair_options+=(--core-only-tests)', source)
        self.assertIn('repair_options=(--integration-tests --tests-only)', source)
        block = WORKFLOW.read_text().split('concurrency:\n', 1)[1].split('\njobs:', 1)[0]
        expression = re.search(r'^  group: \$\{\{ (.+) \}\}$', block, re.M).group(1)
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'core-only', True), 'ayugram-core-qualification')
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'core-only', False), 'ayugram-core-qualification')
        self.assertEqual(self.expression(expression, 'push', 'core-only', True), 'ayugram-qualification')
        self.assertIn('cancel-in-progress: false', block)

    def test_startup_scope_runs_only_artifact_replay_without_any_compilation(self):
        for device in (False, True):
            with self.subTest(device=device):
                self.assertTrue(self.condition('startup-artifact-replay', 'workflow_dispatch', 'startup', device))
                for job in ('integration-tests', 'application-build', 'unsigned-device-build', 'native-api-probe'):
                    self.assertFalse(self.condition(job, 'workflow_dispatch', 'startup', device))
        for event, scope in [('push', 'startup'), ('workflow_dispatch', 'full'), ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'probe'), ('workflow_dispatch', '')]:
            self.assertFalse(self.condition('startup-artifact-replay', event, scope, True))

    def test_replay_concurrency_is_source_scoped_without_replacing_full_qualification(self):
        block = WORKFLOW.read_text().split('concurrency:\n', 1)[1].split('\njobs:', 1)[0]
        expression = re.search(r'^  group: \$\{\{ (.+) \}\}$', block, re.M).group(1)
        for event, scope in [('push', 'startup'), ('push', ''), ('push', 'custom-options-ui'), ('workflow_dispatch', 'full'), ('workflow_dispatch', 'native-only'), ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'projection'), ('workflow_dispatch', 'projection-only'), ('workflow_dispatch', 'probe')]:
            self.assertEqual(self.expression(expression, event, scope, True), 'ayugram-qualification')
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'custom-options-ui', False), 'ayugram-ui-qualification')
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'startup', False), 'ayugram-startup-replay-' + 'c' * 40)
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'startup', False, 'd' * 40), 'ayugram-startup-replay-' + 'd' * 40)
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'probe', False, replay_run='42134690056'), 'ayugram-projection-replay-' + 'c' * 40)
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'probe', False, 'd' * 40, '42134690056'), 'ayugram-projection-replay-' + 'd' * 40)
        self.assertEqual(self.expression(expression, 'push', 'probe', False, replay_run='42134690056'), 'ayugram-qualification')
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'full', False, replay_run='42134690056'), 'ayugram-qualification')
        self.assertIn('cancel-in-progress: false', block)

    def test_probe_replay_and_compilation_paths_are_mutually_exclusive(self):
        section = self.section('native-api-probe')
        self.assertIn('      actions: read', section)
        self.assertIn('timeout-minutes: 25', section)
        steps = {name: body for name, body in re.findall(r'(?ms)^      - name: ([^\n]+)\n(.*?)(?=^      - name: |\Z)', section)}
        replay_steps = ('Test retained Projection replay tooling', 'Replay verified synthetic Projection products without recompilation')
        compile_steps = ('Prepare only the verified synthetic movie fixture', 'Compile the production loader with the host API probe',
                         'Run host API diagnostics without account data', 'Compare host API behavior with an ad-hoc signed app identity')
        for run in ('', '42134690056'):
            for name in (*replay_steps, *compile_steps):
                condition = re.search(r'^        if: (.+)$', steps[name], re.M).group(1)
                selected = bool(self.expression(condition, 'workflow_dispatch', 'probe', False, replay_run=run))
                self.assertEqual(selected, bool(run) if name in replay_steps else not run, name)
        self.assertIn('projection_replay.py --run "$REPLAY_RUN" --source "$REPLAY_SOURCE"', steps[replay_steps[1]])
        published = steps['Publish host probe diagnostics only']
        for name in ('result.json', 'case-results.json', 'test.log', 'projection-launch-probe.json', 'SHA256SUMS'):
            self.assertIn('/projection-replay/' + name, published)
        self.assertNotIn('/projection-replay/**', published)
        self.assertNotIn('.xcresult', published)

    def test_startup_job_reuses_existing_artifact_without_submodules_or_api_credentials(self):
        source = self.section('startup-artifact-replay')
        self.assertIn('      actions: read', source)
        self.assertNotIn('secrets.TELEGRAM_', source)
        self.assertNotIn('secrets.APPLE_', source)
        self.assertNotIn("['git', 'submodule'", source)
        self.assertNotIn('baseline.py', source)
        self.assertNotIn('prepare_host.py', source)
        self.assertNotIn('swiftc', source)
        tests = source.index('      - name: Test replay tooling before downloading or installing an app')
        self.assertLess(source.index('      - name: Check out the replay tool revision'), tests)
        self.assertLess(tests, source.index('      - name: Download and verify the existing simulator build'))
        self.assertIn('run: python3 -m unittest discover -s tools/fork_baseline/tests', source)
        self.assertIn('replay_artifact.py --run "$REPLAY_RUN" --source "$REPLAY_SOURCE"', source)
        self.assertIn('startup_replay.py --ipa build/fork-evidence/startup-replay/Telegram.ipa', source)
        published = source.split('      - name: Publish bounded startup replay evidence only', 1)[1]
        self.assertNotIn('Telegram.ipa', published)
        self.assertNotIn('entitlement', published)
        self.assertIn("'checker-compiler.log'", source.split('      - name: Hash startup replay diagnostics', 1)[1])
        for control in ('normal', 'no-app-groups', 'main-app-only', 'main-app-only-automatic'):
            self.assertIn('/replay-controls/' + control + '/launch-smoke/checker-compiler.log', published)

    def test_main_only_device_package_is_gated_on_successful_variant_and_has_no_signing_secrets(self):
        workflow = WORKFLOW.read_text()
        self.assertIn('      startup_variant:\n', workflow)
        self.assertIn('        options: [original, main-app-only]\n        default: original', workflow)
        self.assertIn('      startup_private_only:\n', workflow)
        self.assertIn('        description: Run only a fresh explicitly pinned private-storage main-app control\n        type: boolean\n        default: false', workflow)
        source = self.section('startup-artifact-replay')
        self.assertIn("STARTUP_PRIVATE_ONLY: ${{ inputs.startup_private_only && 'true' || 'false' }}", source)
        self.assertIn('startup_args=(--startup-variant "$STARTUP_VARIANT")', source)
        self.assertIn('startup_args+=(--private-control-only)', source)
        control = source.index('      - name: Replay the selected startup control')
        fetch = source.index('      - name: Fetch the matching device package')
        package = source.index('      - name: Package the verified unsigned main app')
        publish = source.index('      - name: Publish separate unsigned main-app-only device package')
        self.assertLess(control, fetch); self.assertLess(fetch, package); self.assertLess(package, publish)
        for start, stop in ((fetch, package), (package, publish), (publish, source.index('      - name: Hash startup'))):
            self.assertIn("if: success() && inputs.startup_variant == 'main-app-only'", source[start:stop])
        self.assertIn('--mode unsigned-device', source[fetch:package])
        self.assertIn('--startup-proof ', source[package:publish])
        self.assertIn('--startup-result ', source[package:publish])
        self.assertIn('GH_TOKEN:', source[fetch:package])
        self.assertNotIn('GH_TOKEN:', source[package:])
        self.assertNotIn('secrets.', source)
        self.assertNotIn('Make.py', source)
        self.assertIn('AyuGram-*-main-app-only-unsigned-device.ipa', source[publish:])

    def test_probe_dispatch_starts_only_host_probe_without_application_jobs(self):
        for device in (False, True):
            with self.subTest(device=device):
                self.assertTrue(self.condition('native-api-probe', 'workflow_dispatch', 'probe', device))
                for job in ('integration-tests', 'application-build', 'unsigned-device-build'):
                    self.assertFalse(self.condition(job, 'workflow_dispatch', 'probe', device))

    def test_other_scopes_never_start_host_probe(self):
        for event, scope in [('push', 'probe'), ('push', ''), ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'core-only'), ('workflow_dispatch', 'full'), ('workflow_dispatch', ''), ('workflow_dispatch', 'invalid')]:
            with self.subTest(event=event, scope=scope):
                self.assertFalse(self.condition('native-api-probe', event, scope, True))

    def test_host_probe_has_no_submodule_api_secret_or_bazel_build_path(self):
        source = self.section('native-api-probe')
        self.assertNotIn('secrets.TELEGRAM_', source)
        self.assertNotIn('secrets.APPLE_', source)
        self.assertNotIn('submodule', source.split('    steps:\n', 1)[1].split('      - name: Select', 1)[0].replace('without submodules', ''))
        self.assertNotIn('baseline.py', source)
        self.assertNotIn('prepare_host.py', source)
        self.assertIn('xcrun swiftc -swift-version 5 -target arm64-apple-macosx13.0 submodules/LocalMessageArchive/Sources/LocalArchivePlayback/ArchivePlaybackAsset.swift tools/fork_baseline/native_api_probe.swift', source)
        self.assertIn('build/native-api-probe/probe build/native-api-probe/fixture.mp4 > build/fork-evidence/native-api-probe.json', source)
        self.assertIn('name: native-api-probe-${{ github.sha }}', source)
        self.assertIn('codesign --force --sign - --timestamp=none build/native-api-probe/Probe.app', source)
        self.assertIn('build/native-api-probe/Probe.app/Contents/MacOS/Probe build/native-api-probe/fixture.mp4 > build/fork-evidence/native-api-probe-bundle.json', source)
        self.assertIn('build/fork-evidence/native-api-probe-bundle.json', source)
        self.assertIn('build/fork-evidence/native-api-probe-SHA256SUMS', source)

    def test_custom_options_scope_starts_only_the_small_renderer_job(self):
        jobs = ('integration-tests', 'application-build', 'unsigned-device-build', 'native-api-probe', 'startup-artifact-replay')
        for device in (False, True):
            self.assertTrue(self.condition('custom-options-ui', 'workflow_dispatch', 'custom-options-ui', device))
            for job in jobs:
                self.assertFalse(self.condition(job, 'workflow_dispatch', 'custom-options-ui', device))
        for event, scope in [('push', 'custom-options-ui'), ('push', ''), ('workflow_dispatch', 'full'),
                             ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'core-only'), ('workflow_dispatch', 'projection'),
                             ('workflow_dispatch', 'projection-only'), ('workflow_dispatch', 'probe'),
                             ('workflow_dispatch', 'startup'), ('workflow_dispatch', '')]:
            self.assertFalse(self.condition('custom-options-ui', event, scope, True))

    def test_native_only_qualifies_all_fixed_suites_and_renderer_without_full_app(self):
        for device in (False, True):
            self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'native-only', device))
            self.assertTrue(self.condition('custom-options-ui', 'workflow_dispatch', 'native-only', device))
            for job in ('application-build', 'unsigned-device-build', 'native-api-probe', 'startup-artifact-replay'):
                self.assertFalse(self.condition(job, 'workflow_dispatch', 'native-only', device))
        section = self.section('integration-tests')
        self.assertIn('repair_options=(--integration-tests --tests-only)', section)
        self.assertIn('native-only) ;;', section)
        self.assertEqual(len(baseline.INTEGRATION_TEST_TARGETS), 8)
        self.assertEqual(tuple(baseline.INTEGRATION_TEST_TARGETS), (
            '//submodules/PasscodeCore:PasscodeCoreTests',
            '//submodules/Postbox:PostboxTests',
            '//submodules/LocalMessageArchive:LocalMessageArchiveTests',
            '//submodules/TelegramCore:TelegramCoreTests',
            '//submodules/LocalMessageArchive:LocalArchivePlaybackTests',
            '//submodules/TextFormat:TextFormatTests',
            '//submodules/SettingsUI:SettingsUITests',
            '//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests',
        ))

    def test_native_only_keeps_full_native_evidence_without_claiming_application_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'versions.json').write_text('{}')
            outcomes = dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, 'passed')
            with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'output', return_value='d' * 40):
                baseline.retain_integration_test_summary(outcomes)
            summary = json.loads((root / 'build/fork-evidence/integration-tests.json').read_text())
        self.assertEqual(summary['tests'], outcomes)
        self.assertTrue(summary['full_integration_suite_requested'])
        self.assertTrue(summary['tests_only'])
        self.assertFalse(summary['full_application_built'])
        self.assertFalse(summary['physical_device_verified'])

    def test_small_renderer_job_uses_fixed_wrapper_and_has_no_signing_credentials(self):
        section = self.section('custom-options-ui')
        self.assertIn('custom_options_ui.py', section)
        self.assertIn('secrets.TELEGRAM_API_ID', section)
        self.assertIn('secrets.TELEGRAM_API_HASH', section)
        self.assertNotIn('secrets.APPLE_', section)
        self.assertNotIn('startup_replay.py', section)
        self.assertNotIn('baseline.py simulator', section)
        self.assertNotIn('baseline.py unsigned-device', section)

    def test_branding_guard_applies_only_to_new_full_app_artifacts(self):
        for job, mode in [('unsigned-device-build', 'unsigned-device'), ('application-build', 'simulator')]:
            section = self.section(job)
            command = 'branding.py --ipa build/fork-baseline-' + mode + '/Telegram.ipa --bundle org.livara.ayugram --output build/fork-evidence/branding.json'
            self.assertIn(command, section)
            self.assertLess(section.index('baseline.py ' + mode), section.index(command))
            package_step = 'Publish simulator build evidence and successful package' if mode == 'simulator' else 'Publish unsigned device package and build evidence'
            self.assertLess(section.index(command), section.index('      - name: ' + package_step))
        for job in ('integration-tests', 'native-api-probe', 'startup-artifact-replay', 'custom-options-ui'):
            self.assertNotIn('branding.py', self.section(job))

    def test_full_push_and_legacy_dispatch_keep_existing_build_behavior(self):
        for event, scope, device, expected_device in [('push', 'focused', False, True), ('push', 'core-only', False, True), ('push', '', False, True), ('workflow_dispatch', 'full', True, True), ('workflow_dispatch', '', True, True), ('workflow_dispatch', 'full', False, False)]:
            with self.subTest(event=event, scope=scope, device=device):
                self.assertFalse(self.condition('integration-tests', event, scope, device))
                self.assertTrue(self.condition('application-build', event, scope, device))
                self.assertEqual(self.condition('unsigned-device-build', event, scope, device), expected_device)

    def test_unknown_scope_cannot_start_full_application_jobs(self):
        for job in ('application-build', 'unsigned-device-build'):
            self.assertFalse(self.condition(job, 'workflow_dispatch', 'invalid', True))
        self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'invalid', True))
        source = WORKFLOW.read_text()
        self.assertIn("          VALIDATION_SCOPE: ${{ inputs.validation_scope || 'full' }}", source)
        self.assertIn('case "$VALIDATION_SCOPE" in', source)
        self.assertIn('repair_options=(--integration-tests --tests-only)', source)
        self.assertIn('focused) repair_options+=(--focused-tests)', source)
        self.assertIn('full) ;;', source)
        self.assertIn('*) echo "Invalid validation scope" >&2; exit 1', source)
        self.assertIn('"${repair_options[@]}"', source)
