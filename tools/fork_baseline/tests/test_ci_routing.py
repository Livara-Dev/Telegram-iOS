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
        self.assertIn("concurrency:\n  group: ayugram-qualification\n  cancel-in-progress: false\n", source)
        self.assertEqual(len(re.findall(r"^\s*concurrency:", source, re.M)), 1)
        self.assertNotIn("cancel-in-progress: true", source)

    def test_optional_diagnostics_require_explicit_dispatch(self):
        for name in ["archive-tests.yml", "archive-compatibility.yml", "fork-baseline.yml"]:
            with self.subTest(workflow=name):
                source = (WORKFLOWS / name).read_text()
                events = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
                self.assertEqual(re.findall(r"^  ([a-z_]+):", events, re.M), ["workflow_dispatch"])
                self.assertNotIn("cancel-in-progress: true", source)
