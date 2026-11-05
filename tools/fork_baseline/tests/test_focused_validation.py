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
        return bool(self.expression(match.group(1), event, scope, device))

    def expression(self, expression, event, scope, device, replay_source='c' * 40):
        # Interpret the checked-in boolean/equality gates and the one fixed
        # concurrency formatter. No arbitrary Python calls or code execution.
        expression = expression.replace('github.event_name', 'event').replace('inputs.validation_scope', 'scope').replace('inputs.unsigned_device', 'device').replace('inputs.replay_source', 'source').replace('||', 'or').replace('&&', 'and')
        inputs = {'event': event, 'scope': scope, 'device': device, 'source': replay_source}
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
                self.assertEqual(value(node.args[0]), 'ayugram-startup-replay-{0}')
                return 'ayugram-startup-replay-' + value(node.args[1])
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
        for event, scope in [('push', 'startup'), ('push', ''), ('workflow_dispatch', 'full'), ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'probe')]:
            self.assertEqual(self.expression(expression, event, scope, True), 'ayugram-qualification')
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'startup', False), 'ayugram-startup-replay-' + 'c' * 40)
        self.assertEqual(self.expression(expression, 'workflow_dispatch', 'startup', False, 'd' * 40), 'ayugram-startup-replay-' + 'd' * 40)
        self.assertIn('cancel-in-progress: false', block)

    def test_startup_job_reuses_existing_artifact_without_submodules_or_api_credentials(self):
        source = WORKFLOW.read_text().split('  startup-artifact-replay:\n', 1)[1]
        self.assertIn('      actions: read', source)
        self.assertNotIn('secrets.TELEGRAM_', source)
        self.assertNotIn('secrets.APPLE_', source)
        self.assertNotIn("['git', 'submodule'", source)
        self.assertNotIn('baseline.py', source)
        self.assertNotIn('prepare_host.py', source)
        self.assertNotIn('swiftc', source)
        self.assertIn('replay_artifact.py --run "$REPLAY_RUN" --source "$REPLAY_SOURCE"', source)
        self.assertIn('startup_replay.py --ipa build/fork-evidence/startup-replay/Telegram.ipa', source)
        published = source.split('      - name: Publish bounded startup replay evidence only', 1)[1]
        self.assertNotIn('Telegram.ipa', published)
        self.assertNotIn('entitlement', published)

    def test_probe_dispatch_starts_only_host_probe_without_application_jobs(self):
        for device in (False, True):
            with self.subTest(device=device):
                self.assertTrue(self.condition('native-api-probe', 'workflow_dispatch', 'probe', device))
                for job in ('integration-tests', 'application-build', 'unsigned-device-build'):
                    self.assertFalse(self.condition(job, 'workflow_dispatch', 'probe', device))

    def test_other_scopes_never_start_host_probe(self):
        for event, scope in [('push', 'probe'), ('push', ''), ('workflow_dispatch', 'focused'), ('workflow_dispatch', 'full'), ('workflow_dispatch', ''), ('workflow_dispatch', 'invalid')]:
            with self.subTest(event=event, scope=scope):
                self.assertFalse(self.condition('native-api-probe', event, scope, True))

    def test_host_probe_has_no_submodule_api_secret_or_bazel_build_path(self):
        source = WORKFLOW.read_text().split('  native-api-probe:\n', 1)[1]
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
