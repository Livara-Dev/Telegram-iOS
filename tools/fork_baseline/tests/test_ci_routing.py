"""Prevent feature commits from multiplying expensive hosted Apple builds."""
from pathlib import Path
import re
import unittest

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"


class CIRoutingTests(unittest.TestCase):
    def test_only_verification_branch_automatically_starts_full_build(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        events = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("  push:\n    branches: [ci/verify-latest]\n", events)
        self.assertEqual(re.findall(r"^  ([a-z_]+):", events, re.M), ["push", "workflow_dispatch"])

    def test_one_global_queue_preserves_the_active_qualification(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        self.assertIn("concurrency:\n  group: ${{", source)
        self.assertIn("format('ayugram-startup-replay-{0}', inputs.replay_source)", source)
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

    def test_startup_build_is_independent_of_unrelated_native_failures(self):
        source = (WORKFLOWS / "archive-integration.yml").read_text()
        application = source.split("  application-build:\n", 1)[1]
        self.assertNotIn("needs: integration-tests", application)
        self.assertIn("baseline.py simulator", application)

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
