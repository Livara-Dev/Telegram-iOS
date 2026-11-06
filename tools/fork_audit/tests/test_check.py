import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("fork_audit_check", Path(__file__).resolve().parents[1] / "check.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Guard Test")
        self.git("config", "user.email", "guard@example.invalid")
        self.write("app.swift", "let authenticationUnchanged = true\n")
        self.write(".gitignore", "build-input/\n")
        self.write("versions.json", '{"xcode":"27.1"}\n')
        self.write(".github/workflows/build.yml", "name: Legacy\n")
        self.git("add", ".")
        self.git("commit", "-qm", "upstream")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        self.write("docs/fork/provenance.json", json.dumps(guard.provenance(self.root, self.base)))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def write(self, path, contents):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)

    def failures(self):
        return guard.check(self.root, self.base)

    def assertFailure(self, expected):
        self.assertTrue(any(expected in item for item in self.failures()), self.failures())

    def add_upstream_fixture(self, path):
        self.write(path, "# upstream fixture\n" if path.endswith(".py") else "// upstream fixture\n")
        self.git("add", path)
        self.git("commit", "-qm", "upstream fixture")
        self.base = self.git("rev-parse", "HEAD").decode().strip()
        self.write("docs/fork/provenance.json", json.dumps(guard.provenance(self.root, self.base)))

    def test_clean_baseline_and_audit_addition_pass(self):
        self.write("docs/fork/AUDIT.md", "Source reviewed; runtime checks pending.\n")
        self.assertEqual([], self.failures())

    def test_requested_top_level_documents_allowed(self):
        for path in ("docs/AYUGRAM_FEATURE_MATRIX.md", "docs/WINDOWS_INSTALL.md", "docs/SECURITY_ASSESSMENT.md"):
            self.write(path, "Audit result; runtime checks pending.\n")
        self.assertEqual([], self.failures())

    def test_other_top_level_document_not_implicitly_allowed(self):
        self.write("docs/unrequested.md", "Unknown addition.\n")
        self.assertFailure("Addition outside audit/build scope")

    def test_worktree_application_edit_rejected(self):
        self.write("app.swift", "let authenticationUnchanged = false\n")
        self.assertFailure("Upstream source differs")

    def test_staged_application_edit_rejected(self):
        self.write("app.swift", "let authenticationUnchanged = false\n")
        self.git("add", "app.swift")
        self.assertFailure("Upstream source differs")

    def test_staged_source_change_cannot_hide_behind_restored_worktree(self):
        self.write("app.swift", "let authenticationUnchanged = false\n")
        self.git("add", "app.swift")
        self.write("app.swift", "let authenticationUnchanged = true\n")
        self.assertFailure("Upstream source differs")

    def test_committed_application_edit_rejected(self):
        self.write("app.swift", "let authenticationUnchanged = false\n")
        self.git("add", "app.swift")
        self.git("commit", "-qm", "unauthorized change")
        self.assertFailure("Upstream source differs")

    def test_new_application_file_rejected(self):
        self.write("submodules/TelegramCore/NewNetwork.swift", "// replacement\n")
        self.assertFailure("Addition outside audit/build scope")

    def test_archive_additions_stay_within_named_feature_scope(self):
        for path in ("submodules/TelegramCore/Sources/LocalArchive/Capture.swift",
                     "submodules/TelegramCore/Tests/LocalArchiveCaptureTests.swift",
                     "submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift",
                     ".github/workflows/archive-integration.yml"):
            self.write(path, "// feature fixture\n")
        self.assertEqual([], self.failures())

    def test_exact_unsigned_delivery_workflow_is_tooling_but_neighbor_is_not(self):
        self.write(".github/workflows/unsigned-device-delivery.yml", "name: Qualified unsigned delivery\n")
        self.assertEqual([], self.failures())
        self.write(".github/workflows/unsigned-device-delivery-extra.yml", "name: Unreviewed workflow\n")
        self.assertFailure("Addition outside audit/build scope")

    def test_exact_unsigned_delivery_request_is_tooling_but_neighbor_is_not(self):
        self.write("ci/unsigned-device-request.json", '{"schema":1}\n')
        self.assertEqual([], self.failures())
        self.write("ci/unsigned-device-request-extra.json", '{"schema":1}\n')
        self.assertFailure("Addition outside audit/build scope")

    def test_exact_source_snapshot_workflow_is_tooling_but_neighbor_is_not(self):
        self.write(".github/workflows/ayu-source-snapshot.yml", "name: Tracked source snapshot\n")
        self.assertEqual([], self.failures())
        self.write(".github/workflows/ayu-source-snapshot-extra.yml", "name: Unreviewed workflow\n")
        self.assertFailure("Addition outside audit/build scope")

    def test_exact_reviewed_seam_can_change_but_neighbor_cannot(self):
        path = "submodules/TelegramCore/Sources/Account/Account.swift"
        self.add_upstream_fixture(path)
        self.write(path, "// account lifecycle integration fixture\n")
        self.assertEqual([], self.failures())
        self.write("submodules/TelegramCore/Sources/Account/Authentication.swift", "// unreviewed\n")
        self.assertFailure("Addition outside audit/build scope")

    def test_staged_secret_in_existing_reviewed_seam_is_detected(self):
        path = "submodules/TelegramCore/Sources/Account/Account.swift"
        self.add_upstream_fixture(path)
        self.write(path, 'let api_hash = "' + "1234567890abcdef" * 2 + '"\n')
        self.git("add", path)
        self.write(path, "// safe working copy\n")
        self.assertFailure("Literal credential")

    def test_feature_allowlist_does_not_unpin_dependencies_or_crypto(self):
        for path in ("MODULE.bazel", "submodules/MtProtoKit/Sources/MTEncryption.m",
                     "submodules/Postbox/Sources/SqliteValueBox.swift"):
            self.add_upstream_fixture(path)
            self.write(path, "// forbidden change\n")
            self.assertFailure("Upstream source differs")
            self.write(path, "# upstream fixture\n" if path.endswith(".py") else "// upstream fixture\n")

    def test_signing_file_rejected(self):
        self.write("docs/fork/signing.p12", "fixture")
        self.assertFailure("Signing/credential file")

    def test_private_key_is_rejected_without_disclosure(self):
        self.write("docs/fork/notes.txt", "-----BEGIN " + "PRIVATE KEY-----\nsecret-test-value\n")
        self.assertFailure("Private-key marker")
        self.assertNotIn("secret-test-value", "\n".join(self.failures()))

    def test_literal_hash_is_rejected_without_disclosure(self):
        secret = "1234567890abcdef" * 2
        self.write("docs/fork/config.json", json.dumps({"api_hash": secret}))
        self.assertFailure("Literal credential")
        self.assertNotIn(secret, "\n".join(self.failures()))

    def test_staged_secret_cannot_hide_behind_safe_worktree(self):
        secret = "1234567890abcdef" * 2
        self.write("docs/fork/config.json", json.dumps({"api_hash": secret}))
        self.git("add", "docs/fork/config.json")
        self.write("docs/fork/config.json", "{}")
        self.assertFailure("Literal credential")

    def test_placeholder_assignment_passes(self):
        self.write("docs/fork/config.json", json.dumps({"api_hash": "<runner-secret>"}))
        self.assertEqual([], self.failures())

    def test_only_synthetic_fixture_hash_is_allowed(self):
        self.write("tools/fork_audit/tests/fixture.py", 'api_hash = "' + "a" * 32 + '"\n')
        self.assertEqual([], self.failures())
        self.write("tools/fork_audit/tests/fixture.py", 'api_hash = "' + "1234567890abcdef" * 2 + '"\n')
        self.assertFailure("Literal credential")

    def test_unreviewed_dependency_rejected(self):
        self.write("tools/fork_audit/new.py", "import requests\n")
        self.assertFailure("Non-stdlib Python import")

    def test_upstream_import_exception_is_limited_to_its_exact_test(self):
        path = "build-system/Make/tests/test_postbox_import_check.py"
        self.add_upstream_fixture(path)
        self.write(path, "import PostboxImportCheck\n")
        self.assertEqual([], self.failures())
        self.write("tools/fork_baseline/new.py", "import PostboxImportCheck\n")
        self.assertFailure("Non-stdlib Python import")

    def test_stdlib_tool_passes(self):
        self.write("tools/fork_audit/new.py", "import json\nfrom pathlib import Path\n")
        self.assertEqual([], self.failures())

    def test_provenance_tamper_rejected(self):
        self.write("docs/fork/provenance.json", "{}")
        self.assertFailure("Provenance manifest does not match")

    def test_provenance_missing_rejected(self):
        (self.root / "docs/fork/provenance.json").unlink()
        self.assertFailure("Provenance manifest is missing")

    def test_symlink_rejected(self):
        (self.root / "docs/fork/link").symlink_to(self.root / "app.swift")
        self.assertFailure("must be a regular file")

    def test_broken_symlink_rejected(self):
        (self.root / "docs/fork/link").symlink_to(self.root / "missing")
        self.assertFailure("must be a regular file")

    def test_staged_symlink_cannot_hide_behind_regular_worktree(self):
        target = self.root / "docs/fork/link"
        target.symlink_to(self.root / "app.swift")
        self.git("add", "docs/fork/link")
        target.unlink()
        target.write_text("safe working copy")
        self.assertFailure("Staged addition must be a regular file")

    def test_legacy_publish_workflow_only_removal_allowed(self):
        (self.root / ".github/workflows/build.yml").unlink()
        self.assertEqual([], self.failures())
        self.write(".github/workflows/build.yml", "name: Changed\n")
        self.assertFailure("Upstream source differs")


if __name__ == "__main__":
    unittest.main()
