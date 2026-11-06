# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06.
"""Exact source/request controls; fixtures mutate only temporary synthetic Git repositories."""
import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recipient_expiry_request as request


class RecipientExpiryRequestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.command("init", "-q")
        self.command("config", "user.name", "Owned fixture")
        self.command("config", "user.email", "fixture@example.invalid")
        self.original = (request.WORKER_SIGNATURE + b"subscriber.putNext(network.globalTimeDifference)\n}\n"
                         b"func managedAutoexpireStoryOperations() {\nsubscriber.putNext(network.globalTimeDifference)\n}\n")
        self.write(request.WORKER, self.original)
        self.write("unchanged-neighbor.txt", b"pinned\n")
        self.production = self.commit("owned baseline")
        self.pin = patch.object(request, "PRODUCTION_SOURCE", self.production)
        self.pin.start()
        self.addCleanup(self.pin.stop)
        for name in request.CANDIDATE_PATHS:
            self.write(name, request.expected_worker(self.original) if name == request.WORKER else b"owned reviewed fixture\n")

    def command(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.root), *arguments], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, data):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def commit(self, message):
        self.command("add", "-A")
        self.command("commit", "-qm", message)
        return self.command("rev-parse", "HEAD")

    def payload(self, candidate):
        return {"schemaVersion": 1, "candidate_sha": candidate, "production_source_sha": self.production,
                "validation_scope": "core-only", "nonce": "owned-request-1"}

    def environment(self, revision):
        return {"GITHUB_SHA": revision, "GITHUB_REPOSITORY": request.REPOSITORY,
                "GITHUB_REF": request.BRANCH, "GITHUB_EVENT_NAME": "push"}

    def request_commit(self, payload):
        self.write(request.REQUEST, json.dumps(payload).encode())
        return self.commit("explicit owned request")

    def test_exact_nine_path_worktree_and_committed_candidate_are_accepted(self):
        self.assertEqual(len(request.CANDIDATE_PATHS), 9)
        self.assertIsNone(request.check_worktree(self.root)["candidate_sha"])
        self.assertFalse((self.root / request.REQUEST).exists())
        candidate = self.commit("reviewed candidate")
        request.validate_candidate(self.root, candidate)
        self.assertEqual(request.check_worktree(self.root)["candidate_sha"], candidate)

    def test_direct_request_commit_emits_only_verified_source_metadata(self):
        candidate = self.commit("reviewed candidate")
        revision = self.request_commit(self.payload(candidate))
        result = request.validate_request(self.root, self.environment(revision))
        self.assertEqual(result, {"candidate_sha": candidate, "production_source_sha": self.production,
                                 "request_sha": revision, "validation_scope": "core-only"})

    def test_wrong_event_repository_branch_or_checkout_reject(self):
        candidate = self.commit("reviewed candidate")
        revision = self.request_commit(self.payload(candidate))
        for field, value in [("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_EVENT_NAME", "pull_request"),
                             ("GITHUB_REPOSITORY", "other/repository"), ("GITHUB_REF", "refs/heads/main"),
                             ("GITHUB_SHA", candidate), ("GITHUB_SHA", "../unsafe")]:
            with self.subTest(field=field, value=value):
                environment = self.environment(revision)
                environment[field] = value
                with self.assertRaises(request.RequestError):
                    request.validate_request(self.root, environment)

    def test_schema_unknown_duplicate_oversized_and_unsafe_fields_reject(self):
        candidate = self.commit("reviewed candidate")
        for field, value in [("schemaVersion", True), ("schemaVersion", 2), ("candidate_sha", "A" * 40),
                             ("production_source_sha", "0" * 40), ("validation_scope", "full"),
                             ("nonce", "../unsafe"), ("nonce", ""), ("nonce", "a" * 81), ("extra", "field")]:
            with self.subTest(field=field, value=value):
                payload = self.payload(candidate)
                payload[field] = value
                with self.assertRaises(request.RequestError):
                    request.parse_request(json.dumps(payload).encode())
        for data in (b"[]", b"{", b" " * 4097, b"[" * 1100 + b"]" * 1100,
                     b'{"schemaVersion":1,"schemaVersion":1}'):
            with self.subTest(data=data[:60]), self.assertRaises(request.RequestError):
                request.parse_request(data)

    def test_request_commit_with_extra_source_change_rejects(self):
        candidate = self.commit("reviewed candidate")
        self.write("unchanged-neighbor.txt", b"unreviewed change\n")
        revision = self.request_commit(self.payload(candidate))
        with self.assertRaisesRegex(request.RequestError, "only the request"):
            request.validate_request(self.root, self.environment(revision))

    def test_request_must_be_direct_child_of_declared_candidate(self):
        candidate = self.commit("reviewed candidate")
        self.command("commit", "--allow-empty", "-qm", "intermediate commit")
        revision = self.request_commit(self.payload(candidate))
        with self.assertRaisesRegex(request.RequestError, "directly follow"):
            request.validate_request(self.root, self.environment(revision))

    def test_untracked_neighbor_and_missing_reviewed_path_reject(self):
        name = ".github/workflows/recipient-expiry-tests-extra.yml"
        self.write(name, b"unreviewed neighbor\n")
        with self.assertRaisesRegex(request.RequestError, "nine-file"):
            request.check_worktree(self.root)
        (self.root / name).unlink()
        (self.root / "docs/fork/RECIPIENT-EXPIRY-QUALIFICATION.md").unlink()
        with self.assertRaisesRegex(request.RequestError, "nine-file"):
            request.check_worktree(self.root)

    def test_committed_neighbor_deletion_and_executable_file_reject(self):
        self.write("unchanged-neighbor.txt", b"unreviewed change\n")
        with self.assertRaises(request.RequestError):
            request.validate_candidate(self.root, self.commit("unreviewed candidate"))
        self.command("reset", "--soft", self.production)
        self.write("unchanged-neighbor.txt", b"pinned\n")
        candidate_file = self.root / "docs/fork/RECIPIENT-EXPIRY-QUALIFICATION.md"
        candidate_file.chmod(0o755)
        with self.assertRaisesRegex(request.RequestError, "regular non-executable"):
            request.validate_candidate(self.root, self.commit("executable candidate"))

    def test_worker_body_or_story_reader_mutation_rejects(self):
        expected = request.expected_worker(self.original)
        for altered in (expected + b"// unreviewed extra\n", expected.replace(
                b"subscriber.putNext(network.globalTimeDifference)", b"subscriber.putNext(0)")):
            with self.subTest(altered=altered[-60:]):
                self.write(request.WORKER, altered)
                with self.assertRaisesRegex(request.RequestError, "reader-only seam"):
                    request.check_worktree(self.root)
        self.write(request.WORKER, expected)
        self.assertEqual(request.expected_worker(self.original).count(b"subscriber.putNext(network.globalTimeDifference)"), 1)

    def test_request_or_symlink_present_during_preparation_rejects(self):
        file = self.root / request.REQUEST
        self.write(request.REQUEST, b"{}")
        with self.assertRaisesRegex(request.RequestError, "remain absent"):
            request.check_worktree(self.root)
        file.unlink()
        file.symlink_to(self.root / "missing-owned-file")
        with self.assertRaisesRegex(request.RequestError, "remain absent"):
            request.check_worktree(self.root)

    def test_cli_failure_is_closed_and_never_echoes_request_values(self):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(request, "ROOT", self.root), patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(request.main([]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Recipient-expiry request verification failed; no build authorized.\n")


class RecipientExpiryWorkflowTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[3]

    def source(self):
        return (self.root / ".github/workflows/recipient-expiry-tests.yml").read_text()

    def test_only_explicit_request_push_can_start_one_native_job(self):
        source = self.source()
        events = source.split("on:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(events, "  push:\n    branches: [test/recipient-expiry-worker]\n    paths: [ci/recipient-expiry-request.json]\n")
        jobs = source.split("\njobs:\n", 1)[1]
        self.assertEqual(re.findall(r"^  ([a-z0-9-]+):", jobs, re.M), ["recipient-core-tests"])
        self.assertEqual(re.findall(r"^    runs-on: (.+)$", jobs, re.M), ["xcode-27"])
        self.assertIn("if: github.repository == 'Livara-Dev/Telegram-iOS' && github.ref == 'refs/heads/test/recipient-expiry-worker'", jobs)
        self.assertNotIn("workflow_dispatch", source)
        self.assertNotIn("matrix:", source)
        self.assertFalse((self.root / request.REQUEST).exists(), "Preparation must not create a native-CI request")

    def test_only_fixed_core_tests_run_after_exact_candidate_selection(self):
        source = self.source()
        commands = re.findall(r"^          python3 tools/fork_baseline/baseline\.py (.+)$", source, re.M)
        self.assertEqual(commands, ["simulator --integration-tests --tests-only --core-only-tests 2>&1 | tee build/fork-evidence/native-core.log"])
        self.assertIn("subprocess.run(['git', 'checkout', '--detach', candidate], check=True)", source)
        self.assertLess(source.index("Validate the request and select only its reviewed candidate"), source.index("Initialize only the pinned recursive dependencies"))
        self.assertLess(source.index("Initialize only the pinned recursive dependencies"), source.index("Run the fixed native Core suite without an application build"))
        self.assertIn("'--depth=2'", source)
        self.assertIn(request.PRODUCTION_SOURCE, source)
        self.assertIn("279f89e4d4f63f17227bf877dc3d8990f22ecd58", source)
        self.assertIn("sudo xcode-select -s /Applications/Xcode_27.1.app/Contents/Developer", source)
        self.assertIn("python3 tools/fork_baseline/prepare_host.py --simulator", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertNotIn("cancel-in-progress: true", source)

    def test_credentials_are_step_scoped_permissions_read_only_and_actions_pinned(self):
        source = self.source()
        self.assertEqual(re.findall(r"(?m)^permissions:\n((?:  [^\n]+\n)+)", source), ["  contents: read\n"])
        self.assertEqual(len(re.findall(r"(?m)^\s*permissions:", source)), 1)
        self.assertEqual(re.findall(r"\$\{\{ secrets\.([A-Z_]+) \}\}", source),
                         ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"] * 2)
        sections = re.split(r"(?m)^      - name: ", source)[1:]
        secret_steps = [section.splitlines()[0] for section in sections if "${{ secrets." in section]
        self.assertEqual(secret_steps, ["Validate build configuration without exposing credentials",
                                       "Run the fixed native Core suite without an application build"])
        token_steps = [section.splitlines()[0] for section in sections if "${{ github.token }}" in section]
        self.assertEqual(token_steps, ["Check out the exact request without persistent credentials"])
        actions = re.findall(r"uses: ([^\s]+)", source)
        self.assertEqual(actions, ["actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"])
        self.assertNotIn("dependency_cache.py", source)
        self.assertNotIn("FORK_BASELINE_PUBLIC_CACHE", source)

    def test_upload_contains_only_explicit_redacted_evidence_including_native_xml(self):
        source = self.source()
        upload = source.split("      - name: Publish redacted Core evidence only\n", 1)[1]
        expected = ["recipient-request.json", "portable-tests.log", "host.log", "runtime.log", "native-core.log",
                    "integration-tests.json", "prebuild-telegram-core/collection.json",
                    "prebuild-telegram-core/test.xml", "prebuild-telegram-core/test.log", "recipient-SHA256SUMS"]
        prefix = "${{ github.workspace }}/fork-source/build/fork-evidence/"
        actual = re.findall(r"(?m)^            (.+)$", upload)
        self.assertEqual(actual, [prefix + name for name in expected])
        self.assertIn("        if: always()\n", upload)
        self.assertIn("include-hidden-files: false", upload)
        self.assertNotIn(".ipa", upload)
        self.assertNotIn("bazel-", upload)


if __name__ == "__main__":
    unittest.main()
