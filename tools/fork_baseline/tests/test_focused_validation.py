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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import baseline

WORKFLOW = Path(__file__).resolve().parents[3] / '.github/workflows/archive-integration.yml'
EXPECTED = (
    '//submodules/PasscodeCore:PasscodeCoreTests',
    '//submodules/LocalMessageArchive:LocalMessageArchiveTests',
    '//submodules/LocalMessageArchive:LocalArchivePlaybackTests',
)


def fixture():
    return {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'IOS_BUNDLE_ID': 'org.livara.ayugram'}


class FocusedValidationTests(unittest.TestCase):
    def run_focused(self, root, fail=None):
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
                    baseline.build('simulator', fixture(), 1, integration_tests=True, tests_only=True, focused_tests=True)
            else:
                baseline.build('simulator', fixture(), 1, integration_tests=True, tests_only=True, focused_tests=True)
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
    def condition(self, job, event, scope, device):
        source = WORKFLOW.read_text()
        section = re.search(r'(?ms)^  ' + re.escape(job) + r':\n(.*?)(?=^  [a-z-]+:\n|\Z)', source).group(1)
        match = re.search(r'^    if: (.+)$', section, re.M)
        if not match:
            return True
        # Interpret only the boolean/equality subset used by these job gates.
        # Read the actual condition while refusing calls or other Python syntax.
        expression = match.group(1).replace('github.event_name', 'event').replace('inputs.validation_scope', 'scope').replace('inputs.unsigned_device', 'device').replace('||', 'or').replace('&&', 'and')
        inputs = {'event': event, 'scope': scope, 'device': device}
        def value(node):
            if isinstance(node, ast.Name):
                return inputs[node.id]
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
                return node.value
            if isinstance(node, ast.BoolOp):
                self.assertIn(type(node.op), (ast.And, ast.Or))
                values = [bool(value(item)) for item in node.values]
                return all(values) if isinstance(node.op, ast.And) else any(values)
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                return value(node.left) == value(node.comparators[0])
            self.fail('Unsupported workflow job condition syntax')
        return bool(value(ast.parse(expression, mode='eval').body))

    def test_focused_dispatch_starts_only_native_test_job_even_if_device_requested(self):
        for device in (False, True):
            self.assertTrue(self.condition('integration-tests', 'workflow_dispatch', 'focused', device))
            self.assertFalse(self.condition('application-build', 'workflow_dispatch', 'focused', device))
            self.assertFalse(self.condition('unsigned-device-build', 'workflow_dispatch', 'focused', device))

    def test_full_push_and_legacy_dispatch_keep_existing_build_behavior(self):
        for event, scope, device, expected_device in [('push', 'focused', False, True), ('push', '', False, True), ('workflow_dispatch', 'full', True, True), ('workflow_dispatch', '', True, True), ('workflow_dispatch', 'full', False, False)]:
            with self.subTest(event=event, scope=scope, device=device):
                self.assertTrue(self.condition('integration-tests', event, scope, device))
                self.assertTrue(self.condition('application-build', event, scope, device))
                self.assertEqual(self.condition('unsigned-device-build', event, scope, device), expected_device)

    def test_unknown_scope_cannot_start_full_application_jobs(self):
        for job in ('application-build', 'unsigned-device-build'):
            self.assertFalse(self.condition(job, 'workflow_dispatch', 'invalid', True))
        source = WORKFLOW.read_text()
        self.assertIn("          VALIDATION_SCOPE: ${{ inputs.validation_scope || 'full' }}", source)
        self.assertIn('case "$VALIDATION_SCOPE" in', source)
        self.assertIn('repair_options=(--integration-tests --tests-only)', source)
        self.assertIn('focused) repair_options+=(--focused-tests)', source)
        self.assertIn('full) ;;', source)
        self.assertIn('*) echo "Invalid validation scope" >&2; exit 1', source)
        self.assertIn('"${repair_options[@]}"', source)
