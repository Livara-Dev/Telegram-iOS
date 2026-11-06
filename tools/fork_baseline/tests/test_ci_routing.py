"""Prevent feature commits from multiplying expensive hosted Apple builds."""
import ast
import os
from pathlib import Path
import re
import tempfile
import textwrap
import unittest
from unittest.mock import patch

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"


class CIRoutingTests(unittest.TestCase):
    def job(self, name):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        match = re.search(r"(?ms)^  " + re.escape(name) + r":\n(.*?)(?=^  [a-z-]+:\n|\Z)", source)
        self.assertIsNotNone(match, "Required qualification job is missing")
        return match.group(1)

    def step(self, job, name):
        match = re.search(r"(?ms)^      - name: " + re.escape(name) + r"\n(.*?)(?=^      - name: |\Z)", job)
        self.assertIsNotNone(match, "Required qualification step is missing")
        return match.group(1)

    def allows(self, job, event, scope, retained=False):
        condition = re.search(r"^    if: (.+)$", self.job(job), re.M)
        self.assertIsNotNone(condition)
        expression = condition.group(1).replace("github.event_name", "event").replace("inputs.validation_scope", "scope").replace("inputs.retained_projection_tests", "retained").replace("||", "or").replace("&&", "and")
        values = {"event": event, "scope": scope, "retained": retained}
        def value(node):
            if isinstance(node, ast.Name):
                return values[node.id]
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
                children = [bool(value(item)) for item in node.values]
                return all(children) if isinstance(node.op, ast.And) else any(children)
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
                equal = value(node.left) == value(node.comparators[0])
                return equal if isinstance(node.ops[0], ast.Eq) else not equal
            self.fail("Unsupported qualification routing condition")
        return bool(value(ast.parse(expression, mode="eval").body))

    def test_only_verification_branch_automatically_starts_full_build(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        events = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("  push:\n    branches: [ci/verify-latest]\n", events)
        self.assertEqual(re.findall(r"^  ([a-z_]+):", events, re.M), ["push", "workflow_dispatch"])

    def test_primary_queue_preserves_active_qualification_with_separate_diagnostics(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        self.assertIn("concurrency:\n  group: ${{", source)
        self.assertIn("format('ayugram-startup-replay-{0}', inputs.replay_source)", source)
        self.assertIn("(inputs.validation_scope == 'custom-options-ui' || inputs.validation_scope == 'custom-options-replay') && 'ayugram-ui-qualification'", source)
        self.assertIn("inputs.validation_scope == 'core-only' && 'ayugram-core-qualification'", source)
        self.assertIn("|| 'ayugram-qualification' }}\n  cancel-in-progress: false\n", source)
        self.assertEqual(len(re.findall(r"^\s*concurrency:", source, re.M)), 1)
        self.assertNotIn("cancel-in-progress: true", source)

    def test_optional_diagnostics_require_explicit_dispatch(self):
        for name in ["archive-tests.yml", "archive-compatibility.yml", "fork-baseline.yml"]:
            with self.subTest(workflow=name):
                source = (WORKFLOWS / name).read_text()
                events = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
                self.assertEqual(re.findall(r"^  ([a-z_]+):", events, re.M), ["workflow_dispatch"])
                self.assertNotIn("cancel-in-progress: true", source)

    def test_full_qualification_runs_tests_and_app_in_one_private_build_root(self):
        application = self.job("application-build")
        self.assertNotIn("needs: integration-tests", application)
        build = self.step(application, "Build the full simulator app and run TextFormat regression tests")
        commands = re.findall(r"^          python3 tools/fork_baseline/baseline\.py simulator (.+)$", build, re.M)
        self.assertEqual(len(commands), 1)
        self.assertIn("--integration-tests", commands[0])
        self.assertNotIn("--tests-only", commands[0])
        self.assertNotIn("--focused-tests", commands[0])
        self.assertNotIn("--projection-tests", commands[0])
        self.assertEqual(application.count("python3 tools/fork_baseline/baseline.py simulator"), 1)

    def test_full_and_targeted_dispatch_do_not_duplicate_simulator_compilation(self):
        excluded = {"full", "", "probe", "startup", "custom-options-ui"}
        for event in ("push", "workflow_dispatch"):
            for scope in (*sorted(excluded), "native-only", "core-only", "focused", "projection", "projection-only", "invalid"):
                with self.subTest(event=event, scope=scope):
                    self.assertEqual(self.allows("integration-tests", event, scope), event == "workflow_dispatch" and scope not in excluded)
                    self.assertEqual(self.allows("application-build", event, scope), event == "push" or scope in {"full", ""})
        native = self.job("integration-tests")
        self.assertIn('case "$VALIDATION_SCOPE" in', native)
        self.assertIn('*) echo "Invalid validation scope" >&2; exit 1', native)
        self.assertIn('repair_options=(--integration-tests --tests-only)', native)

    def test_full_branding_and_two_launch_smoke_require_successful_combined_build(self):
        application = self.job("application-build")
        build_name = "Build the full simulator app and run TextFormat regression tests"
        for name, command in (
                ("Verify active fork branding in the full simulator IPA", "branding.py --ipa build/fork-baseline-simulator/Telegram.ipa"),
                ("Launch the real simulator application twice on a fresh device", "launch_smoke.py --ipa build/fork-baseline-simulator/Telegram.ipa")):
            with self.subTest(step=name):
                step = self.step(application, name)
                self.assertIn(command, step)
                self.assertGreater(application.index(name), application.index(build_name))
                self.assertLess(application.index(name), application.index("Publish simulator build evidence and successful package"))
                self.assertNotIn("continue-on-error:", step)
                condition = re.search(r"^        if: (.+)$", step, re.M)
                # Missing `if` has GitHub's success() behavior. Explicitly
                # running after a failed native/build gate is not acceptable.
                if condition:
                    self.assertEqual(condition.group(1), "success()")
        self.assertIn("name: archive-integration-${{ github.sha }}", application)

    def test_full_job_always_retains_native_evidence_for_a_failure_before_app_build(self):
        application = self.job("application-build")
        artifact = re.search(r"(?ms)^      - name: [^\n]+\n(?:(?!^      - name: ).)*?name: archive-integration-tests-\$\{\{ github.sha \}\}.*?(?=^      - name: |\Z)", application)
        self.assertIsNotNone(artifact, "A failed native suite must still publish its evidence")
        self.assertIn("        if: always()\n", artifact.group(0))
        self.assertIn("path: ${{ github.workspace }}/fork-source/build/fork-evidence/", artifact.group(0))

    def test_only_sanitized_public_downloads_enter_the_shared_cache(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        self.assertEqual(source.count("uses: actions/cache/restore@0400d5f644dc74513175e3cd8d07132dd4860809"), 3)
        self.assertEqual(source.count("uses: actions/cache/save@0400d5f644dc74513175e3cd8d07132dd4860809"), 3)
        self.assertEqual(source.count("run: python3 tools/fork_baseline/dependency_cache.py prepare"), 3)
        self.assertEqual(source.count("run: python3 tools/fork_baseline/dependency_cache.py export"), 3)
        cached_paths = re.findall(r"path: (.*ayugram-public-dependencies-v1.*)", source)
        self.assertEqual(len(cached_paths), 6)
        self.assertTrue(all(path == "${{ runner.temp }}/ayugram-public-dependencies-v1/portable" for path in cached_paths))

    def test_cache_infrastructure_is_optional_but_build_and_smoke_are_not(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        optional_steps = re.findall(r"      - name: ([^\n]+)\n        continue-on-error: true", source)
        self.assertEqual(len(optional_steps), 12)
        self.assertEqual(set(optional_steps), {"Restore public dependency downloads", "Verify restored public dependency bytes",
                                               "Export verified public dependency downloads", "Save public dependency downloads"})

    def test_projection_signing_control_cannot_reach_application_or_device_builds(self):
        probe = self.job("native-api-probe")
        replay = self.step(probe, "Replay verified synthetic Projection products without recompilation")
        self.assertIn("if: inputs.validation_scope == 'retained-clean' || inputs.replay_run != ''", replay)
        self.assertIn("PROJECTION_HOST_CONTROL: ${{ inputs.validation_scope == 'retained-clean' && 'resealed-host' || inputs.projection_host_control || 'original' }}", replay)
        self.assertIn('--host-control "$PROJECTION_HOST_CONTROL"', replay)
        self.assertIn("PROJECTION_BOOTSTRAP_SAMPLE: ${{ (inputs.validation_scope == 'retained-clean' || inputs.projection_bootstrap_sample) && 'true' || 'false' }}", replay)
        self.assertIn('projection_args+=(--bootstrap-sample)', replay)
        self.assertIn('"${projection_args[@]}"', replay)
        for name in ("application-build", "unsigned-device-build", "integration-tests", "custom-options-ui", "startup-artifact-replay"):
            with self.subTest(job=name):
                self.assertNotIn("PROJECTION_HOST_CONTROL", self.job(name))
                self.assertNotIn("--host-control", self.job(name))
                self.assertNotIn("PROJECTION_BOOTSTRAP_SAMPLE", self.job(name))
                self.assertNotIn("--bootstrap-sample", self.job(name))

    def test_startup_fetches_the_explicitly_verified_device_pair_after_real_startup_only(self):
        startup = self.job("startup-artifact-replay")
        fetch = self.step(startup, "Fetch the matching device package only after variant startup passes")
        self.assertIn("if: success() && inputs.startup_variant == 'main-app-only'", fetch)
        self.assertIn("from replay_artifact import paired_device_run", fetch)
        self.assertIn("paired_device_run(os.environ['REPLAY_RUN'], os.environ['REPLAY_SOURCE'])", fetch)
        self.assertIn("'--mode', 'unsigned-device', '--run', str(device_run)", fetch)
        self.assertGreater(startup.index("Fetch the matching device package"), startup.index("Replay the selected startup control"))

    def test_retained_probe_fetches_the_source_object_but_keeps_the_driver_checkout(self):
        step = self.step(self.job("native-api-probe"), "Check out the exact probe revision without submodules")
        script = textwrap.dedent(step.split("python3 - <<'PYTHON'\n", 1)[1].split("\n          PYTHON", 1)[0])
        driver, source = 'a' * 40, 'c' * 40
        for run_id, captured_source, expected in (('', '', [driver]), ('42160468600', source, [driver, source]),
                                                ('42160468600', driver, [driver])):
            with self.subTest(run=run_id, source=captured_source), tempfile.TemporaryDirectory() as directory:
                environment = {'GITHUB_WORKSPACE': directory, 'GITHUB_SHA': driver,
                    'GITHUB_REPOSITORY': 'Livara-Dev/Telegram-iOS', 'CHECKOUT_TOKEN': 'public-fixture',
                    'REPLAY_RUN': run_id, 'REPLAY_SOURCE': captured_source}
                with patch.dict(os.environ, environment, clear=True), patch('subprocess.run') as command:
                    exec(compile(script, '<checkout-fixture>', 'exec'), {})
                calls = [call.args[0] for call in command.call_args_list]
                self.assertIn(['git', 'fetch', '--no-tags', '--depth', '1', 'origin', *expected], calls)
                self.assertIn(['git', 'checkout', '--detach', driver], calls)
                fetch = next(call for call in command.call_args_list if call.args[0][1] == 'fetch')
                self.assertNotIn('CHECKOUT_TOKEN', fetch.kwargs['env'])
        for run_id, captured_source in (('', source), ('0', source), ('42160468600', '../invalid'),
                                        (str(2**63), source)):
            with self.subTest(invalid_run=run_id), tempfile.TemporaryDirectory() as directory:
                environment = {'GITHUB_WORKSPACE': directory, 'GITHUB_SHA': driver,
                    'GITHUB_REPOSITORY': 'Livara-Dev/Telegram-iOS', 'CHECKOUT_TOKEN': 'public-fixture',
                    'REPLAY_RUN': run_id, 'REPLAY_SOURCE': captured_source}
                with patch.dict(os.environ, environment, clear=True), patch('subprocess.run') as command:
                    with self.assertRaises(SystemExit):
                        exec(compile(script, '<checkout-fixture>', 'exec'), {})
                command.assert_not_called()
