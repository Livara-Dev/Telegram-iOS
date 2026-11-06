import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifact_licensing
SPEC = importlib.util.spec_from_file_location("baseline", Path(__file__).resolve().parents[1] / "baseline.py")
baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline)


def fixture():
    dummy_hash = "a" * 32
    return {"TELEGRAM_API_ID": "1234567", "TELEGRAM_API_HASH": dummy_hash,
            "IOS_BUNDLE_ID": "org.livara.ayugram", "APPLE_TEAM_ID": "ABCDE12345"}


class ConfigurationTests(unittest.TestCase):
    def test_valid_config_disables_optional_cloud_services(self):
        value = baseline.configuration(fixture())
        self.assertEqual(value["app_center_id"], "")
        self.assertFalse(value["enable_icloud"])
        self.assertFalse(value["enable_siri"])

    def test_unsigned_requires_no_apple_identity(self):
        env = fixture()
        del env["APPLE_TEAM_ID"]
        self.assertEqual(baseline.configuration(env, unsigned=True)["team_id"], "UNSIGNED00")
        with self.assertRaises(baseline.InvalidSetup):
            baseline.configuration(env)

    def test_unsigned_marker_cannot_sign(self):
        env = fixture()
        env["APPLE_TEAM_ID"] = "UNSIGNED00"
        with self.assertRaises(baseline.InvalidSetup):
            baseline.configuration(env)

    def test_missing_secrets_fail_without_value_disclosure(self):
        for key in fixture():
            env = fixture()
            del env[key]
            with self.subTest(key=key), self.assertRaises(baseline.InvalidSetup):
                baseline.configuration(env)

    def test_rejects_injection_and_official_credentials(self):
        for name, bad in (("TELEGRAM_API_ID", "8"), ("TELEGRAM_API_ID", "2147483648"),
                          ("TELEGRAM_API_HASH", '"\ncode()'), ("IOS_BUNDLE_ID", "ph.telegra.Telegraph"),
                          ("IOS_BUNDLE_ID", 'org.test."evil'), ("APPLE_TEAM_ID", "C67CF9S4VU")):
            env = fixture()
            env[name] = bad
            with self.subTest(name=name), self.assertRaises(baseline.InvalidSetup):
                baseline.configuration(env)


class PreflightTests(unittest.TestCase):
    def test_linux_fails_before_tools_or_credentials(self):
        with patch.object(baseline.platform, "system", return_value="Linux"), patch.object(baseline, "output") as call:
            with self.assertRaisesRegex(baseline.InvalidSetup, "macOS"):
                baseline.preflight({})
            call.assert_not_called()

    def test_partial_divergent_conflicted_submodule_rejected(self):
        for status in ("", "-abc modules/a\n", "+abc modules/a\n", "Uabc modules/a\n", " abc modules/a\n-abc modules/b"):
            with self.subTest(status=status), self.assertRaises(baseline.InvalidSetup):
                baseline.check_submodules(status)
        baseline.check_submodules(" abc modules/a\n def modules/b\n")

    def test_binary_hash_checked_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "bazel"
            binary.write_bytes(b"test executable")
            binary.chmod(0o700)
            with patch.object(baseline, "output", return_value="bazel 9.2.0") as call:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.verify_bazel(binary, "9.2.0:" + "0" * 64)
                call.assert_not_called()
                digest = hashlib.sha256(binary.read_bytes()).hexdigest()
                self.assertEqual(baseline.verify_bazel(binary, "9.2.0:" + digest), binary.resolve())

    def test_verified_binary_returns_canonical_path_for_directory_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual"
            actual.mkdir()
            binary = actual / "bazel"
            binary.write_bytes(b"test executable")
            binary.chmod(0o700)
            alias = root / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            with patch.object(baseline, "output", return_value="bazel 9.2.0") as call:
                self.assertEqual(baseline.verify_bazel(alias / "bazel", "9.2.0:" + digest), binary.resolve())
                call.assert_called_once_with([str(binary.resolve()), "--version"])

    def test_unsigned_proxy_disables_signing_and_remote_services(self):
        command = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), "unsigned-device", ["--output_user_root=/private", "build", "Telegram/Telegram"])
        self.assertIn("--features=disable_legacy_signing", command)
        self.assertIn("--//Telegram:disableProvisioningProfiles=true", command)
        self.assertIn("--remote_cache=", command)
        self.assertIn("--lockfile_mode=error", command)
        self.assertLess(command.index("--output_user_root=/private"), command.index("build"))

    def test_proxy_disables_disk_cache_without_changing_startup_or_signing(self):
        for mode in ("simulator", "unsigned-device", "device"):
            for action in ("build", "test"):
                with self.subTest(mode=mode, action=action):
                    command = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), mode,
                        ["--output_user_root=/private", action, "--disk_cache=/old-cache",
                         "//Telegram:Telegram", "--disk_cache", "/later-cache"])
                    self.assertEqual([arg for arg in command if arg.startswith("--disk_cache")], ["--disk_cache="])
                    self.assertNotIn("/later-cache", command)
                    self.assertIn("//Telegram:Telegram", command)
                    self.assertLess(command.index("--output_user_root=/private"), command.index(action))
                    self.assertEqual("--features=disable_legacy_signing" in command, mode == "unsigned-device")
                    self.assertNotIn("--features=apple.skip_codesign_simulator_bundles", command)

    def test_warm_simulator_is_scoped_to_simulator_tests(self):
        flags = {"--test_env=REUSE_GLOBAL_SIMULATOR=1", "--test_env=SIMULATOR_REUSE_SIMULATOR=1"}
        for mode in ("simulator", "unsigned-device", "device"):
            for action in ("test", "build"):
                args = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), mode, [action])
                expected = flags if mode == "simulator" and action == "test" else set()
                self.assertEqual(flags.intersection(args), expected)
                self.assertNotIn("--features=apple.skip_codesign_simulator_bundles", args)

    def test_unsigned_overlay_is_rejected_for_normal_signing(self):
        for mode in ("simulator", "device"):
            with self.assertRaises(baseline.InvalidSetup):
                baseline.proxy_arguments(Path("/bazel"), Path("/repo"), mode, ["build"], rules_override="/private/overlay")
        command = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), "unsigned-device", ["build"], rules_override="/private/overlay")
        self.assertIn("--override_module=rules_apple=/private/overlay", command)
        self.assertIn("--features=ayugram.unsigned_device", command)
        self.assertIn("--features=disable_legacy_signing", command)

    def test_device_signing_not_disabled_and_version_clean(self):
        command = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), "device", ["build"])
        self.assertNotIn("--features=disable_legacy_signing", command)
        self.assertNotIn("--//Telegram:disableProvisioningProfiles=true", command)
        command = baseline.proxy_arguments(Path("/bazel"), Path("/repo"), "simulator", ["--version"])
        self.assertEqual(command, ["/bazel", "--version"])


class BundleDiagnosticsTests(unittest.TestCase):
    def make_bundle(self, root, private):
        app = private / "output/Telegram_archive-root/Payload/Telegram.app"
        framework = app / "Frameworks/TelegramUIFramework.framework"
        framework.mkdir(parents=True)
        (app / "Telegram").write_bytes(b"test application binary")
        (framework / "TelegramUIFramework").write_bytes(b"test framework binary")
        link = root / "bazel-bin/Telegram"
        link.parent.mkdir(parents=True)
        link.symlink_to(private / "output", target_is_directory=True)
        return app

    def test_diagnostics_are_read_only_bounded_redacted_and_inside_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            private = Path(directory) / "private"
            root.mkdir()
            self.make_bundle(root, private)
            secret = "private-api-value"
            diagnostic = (secret + " " + str(private) + " " + str(root) + "\n").encode() + b"x" * 70000
            with patch.object(baseline, "ROOT", root), patch.object(baseline.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=diagnostic)) as command:
                baseline.retain_bundle_diagnostics(private, (secret,))
            self.assertEqual(command.call_count, 6)
            allowed = {"--display", "--verify"}
            for call in command.call_args_list:
                args = call.args[0]
                self.assertEqual(call.kwargs["timeout"], 8)
                self.assertTrue(args[0] == "/usr/bin/xattr" or args[1] in allowed)
                self.assertNotIn("--sign", args)
                self.assertNotIn("--remove-signature", args)
                self.assertTrue(Path(args[-1]).is_relative_to(private.resolve()))
            text = (root / "build/fork-evidence/bundle-diagnostics.json").read_text()
            self.assertNotIn(secret, text)
            self.assertNotIn(str(private), text)
            self.assertNotIn(str(root), text)
            report = json.loads(text)
            self.assertTrue(report["read_only"])
            self.assertFalse(report["signature_or_installation_verified"])
            self.assertEqual(set(report["bundles"]), {"application", "ui_framework"})
            self.assertLess(len(text), 400000)

    def test_diagnostic_paths_redact_both_alias_and_physical_names(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / "actual"
            real.mkdir()
            alias = base / "alias"
            alias.symlink_to(real, target_is_directory=True)
            root = alias / "source"
            private = alias / "private"
            root.mkdir()
            self.make_bundle(root, private)
            raw = " ".join(str(p) for p in (root, root.resolve(), private, private.resolve())).encode()
            with patch.object(baseline, "ROOT", root), patch.object(baseline.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=raw)):
                baseline.retain_bundle_diagnostics(private, ())
            text = (root / "build/fork-evidence/bundle-diagnostics.json").read_text()
            for path in (root, root.resolve(), private, private.resolve()):
                self.assertNotIn(str(path), text)
            self.assertIn("[PRIVATE_BUILD]", text)
            self.assertIn("[SOURCE]", text)

    def test_build_output_escape_is_rejected_without_running_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            private = Path(directory) / "private"
            unrelated = Path(directory) / "unrelated"
            root.mkdir()
            private.mkdir()
            self.make_bundle(root, unrelated)
            with patch.object(baseline, "ROOT", root), patch.object(baseline.subprocess, "run") as command:
                baseline.retain_bundle_diagnostics(private, ())
            command.assert_not_called()
            self.assertFalse((root / "build/fork-evidence/bundle-diagnostics.json").exists())

    def test_timeouts_do_not_hide_failure_or_overwrite_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            private = Path(directory) / "private"
            root.mkdir()
            self.make_bundle(root, private)
            with patch.object(baseline, "ROOT", root), patch.object(baseline.subprocess, "run", side_effect=subprocess.TimeoutExpired("codesign", 8)):
                baseline.retain_bundle_diagnostics(private, ())
                evidence = root / "build/fork-evidence/bundle-diagnostics.json"
                before = evidence.read_bytes()
                baseline.retain_bundle_diagnostics(private, ())
                self.assertEqual(evidence.read_bytes(), before)
            report = json.loads(before)
            for bundle in report["bundles"].values():
                self.assertTrue(all(command == {"state": "unavailable"} for command in bundle["commands"].values()))


class PrivacyAndArtifactsTests(unittest.TestCase):
    def setUp(self):
        materials = patch.object(baseline, 'material_bytes',
            side_effect=lambda root, commit: artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], commit))
        materials.start()
        self.addCleanup(materials.stop)
        overlay = patch.object(baseline, "prepare_unsigned_rules", return_value={"fixture": "reviewed"})
        overlay.start()
        self.addCleanup(overlay.stop)
        inspection = patch.object(baseline, "inspect_ipa", return_value={"test_fixture": True})
        inspection.start()
        self.addCleanup(inspection.stop)
        device_inspection = patch.object(baseline, "inspect_unsigned_device", return_value={"test_fixture": True})
        device_inspection.start()
        self.addCleanup(device_inspection.stop)
        probe = patch.object(baseline, "capture_projection_launch")
        probe.start()
        self.addCleanup(probe.stop)

    def test_rejected_ipa_is_not_published_after_successful_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            source = root / "bazel-bin/Telegram/Telegram.ipa"
            source.parent.mkdir(parents=True)
            def fake_command(args, env, secrets):
                source.write_bytes(b"wrong-platform fixture")
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, "inspect_unsigned_device", side_effect=baseline.InvalidIPA("Wrong platform")) as inspection, patch.object(baseline, "publish_artifact") as publish:
                with self.assertRaisesRegex(baseline.InvalidSetup, "Wrong platform"):
                    baseline.build("unsigned-device", fixture(), 1)
                inspection.assert_called_once()
                publish.assert_not_called()
            self.assertFalse((root / "build/fork-baseline-unsigned-device").exists())

    def test_storage_diagnostics_precede_cleanup_and_suites_reuse_output_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            sequence = []
            scratch_paths = []
            output_roots = []
            def disk_usage(path):
                self.assertTrue(path.is_dir())
                scratch_paths.append(path)
                sequence.append("measure")
                return SimpleNamespace(total=1000, used=250, free=750)
            def fake_command(args, env, secrets):
                self.assertFalse(any(arg.startswith("--cacheDir") for arg in args))
                output_roots.append(next(arg for arg in args if arg.startswith("--bazelUserRoot=")))
                sequence.append("suite")
            def shutdown(*args, **kwargs):
                self.assertTrue(scratch_paths[-1].is_dir())
                sequence.append("shutdown")
            buffer = io.StringIO()
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline.shutil, "disk_usage", side_effect=disk_usage), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run", side_effect=shutdown), patch.object(baseline, "output", return_value="c" * 40), patch.object(baseline, "retain_test_evidence"), contextlib.redirect_stdout(buffer):
                baseline.build("simulator", fixture(), 1, integration_tests=True, tests_only=True)
            self.assertEqual(sequence, ["measure"] + ["suite"] * len(baseline.INTEGRATION_TEST_TARGETS) + ["measure", "shutdown"])
            self.assertEqual(len(set(output_roots)), 1)
            self.assertEqual(scratch_paths, [scratch_paths[0], scratch_paths[0]])
            self.assertFalse(scratch_paths[0].exists())
            self.assertIn("Build storage before: total_bytes=1000 used_bytes=250 free_bytes=750", buffer.getvalue())
            self.assertIn("Build storage after: total_bytes=1000 used_bytes=250 free_bytes=750", buffer.getvalue())
            self.assertNotIn(str(root), buffer.getvalue())

    def test_storage_measurement_errors_preserve_original_failure_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_failure = baseline.InvalidSetup("original build failure")
            generated = root / "build-input/configuration-repository"
            scratch_paths = []
            def disk_usage(path):
                self.assertTrue(path.is_dir())
                scratch_paths.append(path)
                raise OSError("sensitive diagnostic path")
            def fake_command(args, env, secrets):
                generated.mkdir(parents=True)
                (generated / "variables.bzl").write_text("private fixture")
                raise original_failure
            buffer = io.StringIO()
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline.shutil, "disk_usage", side_effect=disk_usage), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run") as shutdown, contextlib.redirect_stdout(buffer):
                with self.assertRaises(baseline.InvalidSetup) as caught:
                    baseline.build("unsigned-device", fixture(), 1)
                self.assertIs(caught.exception, original_failure)
                shutdown.assert_called_once()
            self.assertEqual(len(scratch_paths), 2)
            self.assertIn("Build storage before: unavailable", buffer.getvalue())
            self.assertIn("Build storage after: unavailable", buffer.getvalue())
            self.assertNotIn("sensitive", buffer.getvalue())
            self.assertFalse(generated.exists())
            self.assertTrue(all(not path.exists() for path in scratch_paths))

    def test_tests_only_retains_commit_bound_results_without_build_or_ipa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{"xcode":"27.1"}')
            invoked = []
            def fake_command(args, env, secrets):
                self.assertIn("test", args)
                invoked.append(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target=")))
            buffer = io.StringIO()
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, "output", return_value="c" * 40), patch.object(baseline, "publish_artifact") as publish, contextlib.redirect_stdout(buffer):
                baseline.build("simulator", fixture(), 1, integration_tests=True, tests_only=True)
                publish.assert_not_called()
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS))
            summary = json.loads((root / "build/fork-evidence/integration-tests.json").read_text())
            self.assertEqual(summary["source_commit"], "c" * 40)
            self.assertEqual(summary["toolchain"], {"xcode": "27.1"})
            self.assertEqual(summary["tests"], dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, "passed"))
            self.assertTrue(summary["tests_only"])
            self.assertFalse(summary["full_application_built"])
            self.assertFalse(summary["physical_device_verified"])
            self.assertFalse((root / "build/fork-baseline-simulator").exists())
            self.assertNotIn("build completed", buffer.getvalue())
            self.assertIn("No full application build or IPA", buffer.getvalue())
            self.assertTrue((root / "build/fork-evidence/prebuild-textformat/collection.json").exists())
            self.assertFalse((root / "build/fork-evidence/textformat").exists())

    def test_tests_only_failure_retains_all_suite_results_and_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            failure = baseline.InvalidSetup("injected suite failure")
            failed_target = baseline.INTEGRATION_TEST_TARGETS[1]
            invoked = []
            def fake_command(args, env, secrets):
                target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target="))
                invoked.append(target)
                if target == failed_target:
                    raise failure
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, "output", return_value="d" * 40):
                with self.assertRaises(baseline.InvalidSetup) as caught:
                    baseline.build("simulator", fixture(), 1, integration_tests=True, tests_only=True)
                self.assertIs(caught.exception, failure)
            summary = json.loads((root / "build/fork-evidence/integration-tests.json").read_text())
            expected = dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, "passed")
            expected[failed_target] = "failed"
            self.assertEqual(summary["tests"], expected)
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS))
            for target, outcome in expected.items():
                name = baseline.TEST_TARGET_PATHS[target][0]
                collected = json.loads((root / "build/fork-evidence" / ("prebuild-" + name) / "collection.json").read_text())
                self.assertEqual(collected["target"], target)
                self.assertEqual(collected["invocation"], outcome)
            self.assertFalse((root / "build/fork-baseline-simulator").exists())

    def test_tests_only_multiple_failures_preserve_failure_and_clean_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            first_failure = baseline.InvalidSetup("first suite failure")
            failed_targets = {baseline.INTEGRATION_TEST_TARGETS[0], baseline.INTEGRATION_TEST_TARGETS[-1]}
            invoked = []
            private_directories = []
            generated = root / "build-input/configuration-repository"
            def fake_command(args, env, secrets):
                self.assertIn("test", args)
                self.assertNotIn("build", args)
                self.assertNotIn("TELEGRAM_API_ID", env)
                self.assertNotIn("TELEGRAM_API_HASH", env)
                target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target="))
                invoked.append(target)
                configuration = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--configurationPath=")))
                private_directories.append(configuration.parent)
                generated.mkdir(parents=True, exist_ok=True)
                (generated / "variables.bzl").write_text("private test configuration")
                if target == baseline.INTEGRATION_TEST_TARGETS[0]:
                    raise first_failure
                if target in failed_targets:
                    raise baseline.InvalidSetup("later suite failure")
            buffer = io.StringIO()
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run") as shutdown, patch.object(baseline, "output", return_value="e" * 40), patch.object(baseline, "publish_artifact") as publish, contextlib.redirect_stdout(buffer):
                with self.assertRaises(baseline.InvalidSetup) as caught:
                    baseline.build("simulator", fixture(), 1, integration_tests=True, tests_only=True)
                self.assertIs(caught.exception, first_failure)
                shutdown.assert_called_once()
                publish.assert_not_called()
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS))
            summary = json.loads((root / "build/fork-evidence/integration-tests.json").read_text())
            expected = {target: "failed" if target in failed_targets else "passed" for target in baseline.INTEGRATION_TEST_TARGETS}
            self.assertEqual(summary["tests"], expected)
            self.assertFalse(summary["full_application_built"])
            self.assertFalse(summary["physical_device_verified"])
            self.assertNotIn("targets passed", buffer.getvalue())
            self.assertFalse(generated.exists())
            self.assertTrue(private_directories)
            self.assertTrue(all(not path.exists() for path in private_directories))
            self.assertFalse((root / "build/fork-baseline-simulator").exists())

    def test_tests_only_requires_simulator_and_integration_option_before_preflight(self):
        for mode, integration in (("simulator", False), ("unsigned-device", False), ("device", True)):
            with self.subTest(mode=mode, integration=integration), patch.object(baseline, "preflight") as preflight:
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build(mode, {}, 1, integration_tests=integration, tests_only=True)
                preflight.assert_not_called()

    def test_integration_suites_precede_full_build_and_manifest_records_actual_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            source = root / "bazel-bin/Telegram/Telegram.ipa"
            source.parent.mkdir(parents=True)
            invoked = []
            output_roots = []
            configuration_paths = []
            def fake_command(args, env, secrets):
                self.assertNotIn("TELEGRAM_API_ID", env)
                self.assertNotIn("TELEGRAM_API_HASH", env)
                self.assertFalse(any(arg.startswith("--cacheDir") for arg in args))
                output_roots.append(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--bazelUserRoot=")))
                configuration_paths.append(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--configurationPath=")))
                if "test" in args:
                    invoked.append(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target=")))
                else:
                    summary = json.loads((root / "build/fork-evidence/integration-tests.json").read_text())
                    self.assertEqual(summary['tests'], dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, 'passed'))
                    self.assertFalse(summary['tests_only'])
                    self.assertTrue(summary['full_application_build_requested'])
                    self.assertFalse(summary['full_application_built'])
                    invoked.append("full-build")
                    source.write_bytes(b"integration IPA")
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, "output", return_value="a" * 40), patch.object(baseline, "retain_test_evidence") as collect:
                with contextlib.redirect_stdout(io.StringIO()):
                    baseline.build("simulator", fixture(), 1, integration_tests=True)
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS) + ["full-build"])
            self.assertEqual(len(set(output_roots)), 1)
            self.assertEqual(len(set(configuration_paths)), 1)
            self.assertFalse(Path(output_roots[0]).parent.exists())
            self.assertFalse(Path(configuration_paths[0]).exists())
            self.assertEqual([call.args[2:] for call in collect.call_args_list],
                             [("passed", target) for target in baseline.INTEGRATION_TEST_TARGETS])
            self.assertTrue(all(call.kwargs['prebuild'] for call in collect.call_args_list))
            manifest = json.loads((root / "build/fork-baseline-simulator/manifest.json").read_text())
            self.assertEqual(manifest["tests"], dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, "passed"))
            self.assertEqual(manifest["source_commit"], "a" * 40)
            self.assertFalse(manifest["runtime_verified"])
            self.assertFalse(manifest["signature_verified"])

    def test_failed_integration_suite_retains_failure_and_stops_before_full_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'versions.json').write_text('{}')
            invoked = []
            failed_target = baseline.INTEGRATION_TEST_TARGETS[1]
            def fake_command(args, env, secrets):
                target = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--target="))
                invoked.append(target)
                if target == failed_target:
                    raise baseline.InvalidSetup("integration failure")
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, 'output', return_value='d' * 40), patch.object(baseline, "retain_test_evidence") as collect:
                with self.assertRaisesRegex(baseline.InvalidSetup, "integration failure"):
                    baseline.build("simulator", fixture(), 1, integration_tests=True)
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS[:2]))
            self.assertEqual(collect.call_args.args[2:], ("failed", failed_target))
            summary = json.loads((root / 'build/fork-evidence/integration-tests.json').read_text())
            self.assertEqual(summary['tests'][failed_target], 'failed')
            self.assertTrue(all(summary['tests'][target] == 'not_run' for target in baseline.INTEGRATION_TEST_TARGETS[2:]))
            self.assertFalse(summary['tests_only'])
            self.assertTrue(summary['full_application_build_requested'])
            self.assertFalse(summary['full_application_built'])
            self.assertFalse((root / "build/fork-baseline-simulator").exists())

    def test_combined_native_failure_survives_summary_write_failure_and_never_builds(self):
        failure = baseline.InvalidSetup('synthetic original native failure')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'preflight', return_value=Path('/bazel')), patch.object(baseline, 'run_redacted', side_effect=failure) as invoked, patch.object(baseline.subprocess, 'run'), patch.object(baseline, 'retain_test_evidence'), patch.object(baseline, 'retain_integration_test_summary', side_effect=OSError('synthetic storage failure')), patch.object(baseline, 'publish_artifact') as publish, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(baseline.InvalidSetup) as caught:
                    baseline.build('simulator', fixture(), 1, integration_tests=True)
                self.assertIs(caught.exception, failure)
                self.assertEqual(invoked.call_count, 1)
                self.assertIn('test', invoked.call_args.args[0])
                publish.assert_not_called()

    def test_full_app_compilation_failure_keeps_prior_native_results_and_cleans_private_root(self):
        failure = baseline.InvalidSetup('synthetic full app failure')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'versions.json').write_text('{}')
            invoked = []; private_roots = []
            def execute(args, env, secrets):
                private_roots.append(Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--bazelUserRoot='))))
                if 'test' in args:
                    invoked.append(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--target=')))
                else:
                    invoked.append('full-build')
                    raise failure
            with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'preflight', return_value=Path('/bazel')), patch.object(baseline, 'run_redacted', side_effect=execute), patch.object(baseline.subprocess, 'run'), patch.object(baseline, 'output', return_value='c' * 40), patch.object(baseline, 'retain_test_evidence'), patch.object(baseline, 'retain_bundle_diagnostics'), patch.object(baseline, 'publish_artifact') as publish, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(baseline.InvalidSetup) as caught:
                    baseline.build('simulator', fixture(), 1, integration_tests=True)
                self.assertIs(caught.exception, failure)
                publish.assert_not_called()
            self.assertEqual(invoked, list(baseline.INTEGRATION_TEST_TARGETS) + ['full-build'])
            summary = json.loads((root / 'build/fork-evidence/integration-tests.json').read_text())
            self.assertEqual(summary['tests'], dict.fromkeys(baseline.INTEGRATION_TEST_TARGETS, 'passed'))
            self.assertFalse(summary['full_application_built'])
            self.assertTrue(all(not path.parent.exists() for path in private_roots))

    def test_integration_option_rejects_device_mode_before_preflight(self):
        for mode in ("device", "unsigned-device"):
            with self.subTest(mode=mode), patch.object(baseline, "preflight") as preflight:
                with self.assertRaisesRegex(baseline.InvalidSetup, "require simulator"):
                    baseline.build(mode, {}, 1, integration_tests=True)
                preflight.assert_not_called()

    def test_shutdown_errors_preserve_build_outcome_and_cleanup(self):
        for failed in (False, True):
            for error_type in ("timeout", "oserror"):
                with self.subTest(failed=failed, error_type=error_type), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "versions.json").write_text('{}')
                    source = root / "bazel-bin/Telegram/Telegram.ipa"
                    source.parent.mkdir(parents=True)
                    generated = root / "build-input/configuration-repository"
                    private_directories = []
                    original_failure = baseline.InvalidSetup("original build failure")
                    shutdown_failure = (subprocess.TimeoutExpired(["sensitive-command-argument"], 60, output="sensitive-output")
                                        if error_type == "timeout" else OSError("sensitive-os-error"))
                    def fake_command(args, env, secrets):
                        config_path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--configurationPath=")))
                        private_directories.append(config_path.parent)
                        generated.mkdir(parents=True)
                        (generated / "variables.bzl").write_text("private configuration fixture")
                        if failed:
                            raise original_failure
                        source.write_bytes(b"fixture IPA")
                    buffer = io.StringIO()
                    with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run", side_effect=shutdown_failure) as shutdown, patch.object(baseline, "output", return_value="a" * 40), contextlib.redirect_stdout(buffer):
                        if failed:
                            with self.assertRaises(baseline.InvalidSetup) as caught:
                                baseline.build("unsigned-device", fixture(), 1)
                            self.assertIs(caught.exception, original_failure)
                        else:
                            baseline.build("unsigned-device", fixture(), 1)
                        shutdown.assert_called_once()
                    self.assertIn("Bazel shutdown did not complete", buffer.getvalue())
                    self.assertNotIn("sensitive-", buffer.getvalue())
                    self.assertFalse(generated.exists())
                    self.assertTrue(private_directories)
                    self.assertTrue(all(not path.exists() for path in private_directories))
                    self.assertEqual((root / "build/fork-baseline-unsigned-device/Telegram.ipa").exists(), not failed)

    def test_build_captures_ipa_before_test_changes_bazel_bin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "versions.json").write_text('{}')
            source = root / "bazel-bin/Telegram/Telegram.ipa"
            source.parent.mkdir(parents=True)
            def fake_command(args, env, secrets):
                source.write_bytes(b"build IPA" if "build" in args else b"different test configuration")
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"), patch.object(baseline, "output", return_value="a" * 40):
                with contextlib.redirect_stdout(io.StringIO()):
                    baseline.build("simulator", fixture(), 1)
            self.assertEqual((root / "build/fork-baseline-simulator/Telegram.ipa").read_bytes(), b"build IPA")

    def test_failed_test_does_not_publish_build_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bazel-bin/Telegram/Telegram.ipa"
            source.parent.mkdir(parents=True)
            def fake_command(args, env, secrets):
                if "test" in args:
                    bazel_root = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--bazelUserRoot=")))
                    logs = bazel_root / "output/testlogs/submodules/TextFormat/TextFormatTests"
                    logs.mkdir(parents=True)
                    (root / "bazel-testlogs").symlink_to(bazel_root / "output/testlogs", target_is_directory=True)
                    (logs / "test.log").write_text("failure " + fixture()["TELEGRAM_API_HASH"])
                    raise baseline.InvalidSetup("test failed")
                source.write_bytes(b"build IPA")
            with patch.object(baseline, "ROOT", root), patch.object(baseline, "preflight", return_value=Path('/bazel')), patch.object(baseline, "run_redacted", side_effect=fake_command), patch.object(baseline.subprocess, "run"):
                with self.assertRaises(baseline.InvalidSetup):
                    baseline.build("simulator", fixture(), 1)
            self.assertFalse((root / "build/fork-baseline-simulator").exists())
            evidence = root / "build/fork-evidence/textformat"
            self.assertEqual((evidence / "test.log").read_text(), "failure [REDACTED]")
            self.assertEqual(json.loads((evidence / "collection.json").read_text())["invocation"], "failed")
            self.assertFalse((root / "bazel-testlogs").exists())  # Private source was cleaned.

    def test_generated_config_private_and_cleanup_even_on_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(baseline, "ROOT", Path(directory)):
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "intentional"):
                with baseline.private_configuration(fixture()) as (temporary, path):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    generated = root / "build-input/configuration-repository"
                    generated.mkdir(parents=True)
                    (generated / "variables.bzl").write_text("sensitive")
                    raise RuntimeError("intentional")
            self.assertFalse(generated.exists())
            self.assertFalse(temporary.exists())

    def test_existing_config_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(baseline, "ROOT", Path(directory)):
            generated = Path(directory) / "build-input/configuration-repository"
            generated.mkdir(parents=True)
            with self.assertRaises(baseline.InvalidSetup):
                with baseline.private_configuration(fixture()):
                    pass
            self.assertTrue(generated.exists())

    def test_subprocess_output_redaction_and_failure(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(baseline.InvalidSetup):
                baseline.run_redacted([sys.executable, "-c", "print('private-value'); raise SystemExit(1)"], {}, ["private-value"])
        self.assertNotIn("private-value", buffer.getvalue())
        self.assertIn("[REDACTED]", buffer.getvalue())

    def test_manifest_hash_and_no_false_runtime_claim(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(baseline, "output", return_value="a" * 40):
            source = Path(directory) / "source.ipa"
            source.write_bytes(b"fixture IPA")
            destination = Path(directory) / "artifact"
            baseline.publish_artifact(source, destination, "unsigned-device", "org.livara.ayugram", 1)
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertFalse(manifest["runtime_verified"])
            self.assertFalse(manifest["signature_verified"])
            self.assertEqual(manifest["signing"], "disabled")
            self.assertEqual(manifest["integration_qualification"], "not_evaluated_by_this_job")
            self.assertEqual(manifest["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(manifest["tests"][baseline.TEST_TARGET], "not_run")
            with self.assertRaises(FileExistsError):
                baseline.publish_artifact(source, destination, "unsigned-device", "org.livara.ayugram", 2)

    def test_failed_copy_never_publishes_partial_artifact(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(baseline, "output", return_value="a" * 40):
            source = Path(directory) / "source.ipa"
            source.write_bytes(b"fixture")
            destination = Path(directory) / "artifact"
            def failed_copy(src, dest):
                Path(dest).write_bytes(b"partial")
                raise OSError("disk full")
            with patch.object(baseline.shutil, "copyfile", side_effect=failed_copy):
                with self.assertRaises(OSError):
                    baseline.publish_artifact(source, destination, "unsigned-device", "org.livara.ayugram", 1)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob(".fork-artifact-*")), [])


class TestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bazel_root = self.root / "private-bazel"
        self.logs = self.bazel_root / "output/testlogs/submodules/TextFormat/TextFormatTests"
        self.logs.mkdir(parents=True)
        (self.root / "bazel-testlogs").symlink_to(self.bazel_root / "output/testlogs", target_is_directory=True)
        self.destination = self.root / "build/fork-evidence/textformat"
        patcher = patch.object(baseline, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def collect(self):
        baseline.retain_test_evidence(self.bazel_root, [fixture()["TELEGRAM_API_HASH"], fixture()["TELEGRAM_API_ID"]], "passed")
        return json.loads((self.destination / "collection.json").read_text())

    def test_only_target_files_retained_with_both_secrets_redacted(self):
        value = fixture()
        (self.logs / "test.xml").write_text('<system-out>' + value["TELEGRAM_API_HASH"] + '</system-out>')
        (self.logs / "test.log").write_text('API ' + value["TELEGRAM_API_ID"])
        (self.logs / "credentials.json").write_text('must not be copied')
        summary = self.collect()
        self.assertEqual(summary["target"], baseline.TEST_TARGET)
        self.assertEqual(summary["files"], {"test.xml": "retained_redacted", "test.log": "retained_redacted"})
        self.assertEqual((self.destination / "test.xml").read_text(), '<system-out>[REDACTED]</system-out>')
        self.assertEqual((self.destination / "test.log").read_text(), 'API [REDACTED]')
        self.assertEqual({p.name for p in self.destination.iterdir()}, {"test.xml", "test.log", "collection.json"})

    def test_integration_evidence_kept_separate_for_each_allowlisted_target(self):
        secret = fixture()["TELEGRAM_API_HASH"]
        for target in baseline.INTEGRATION_TEST_TARGETS:
            evidence_name, components = baseline.TEST_TARGET_PATHS[target]
            logs = self.bazel_root / "output/testlogs"
            logs = logs.joinpath(*components)
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "test.log").write_text(target + " " + secret)
            baseline.retain_test_evidence(self.bazel_root, [secret], "passed", target)
            destination = self.root / "build/fork-evidence" / evidence_name
            self.assertEqual((destination / "test.log").read_text(), target + " [REDACTED]")
            self.assertEqual(json.loads((destination / "collection.json").read_text())["target"], target)

    def test_prebuild_evidence_does_not_hide_later_full_build_smoke_test(self):
        (self.logs / "test.log").write_text("first invocation")
        baseline.retain_test_evidence(self.bazel_root, [], "passed", prebuild=True)
        (self.logs / "test.log").write_text("second invocation")
        baseline.retain_test_evidence(self.bazel_root, [], "failed")
        prebuild = self.root / "build/fork-evidence/prebuild-textformat"
        self.assertEqual((prebuild / "test.log").read_text(), "first invocation")
        self.assertEqual((self.destination / "test.log").read_text(), "second invocation")
        self.assertEqual(json.loads((prebuild / "collection.json").read_text())["invocation"], "passed")
        self.assertEqual(json.loads((self.destination / "collection.json").read_text())["invocation"], "failed")

    def test_unknown_test_target_rejected_without_creating_output(self):
        for target in ("../../secrets", "//submodules/Other:OtherTests", "--test_arg=unsafe"):
            with self.subTest(target=target), self.assertRaisesRegex(baseline.InvalidSetup, "not allowlisted"):
                baseline.retain_test_evidence(self.bazel_root, [], "passed", target)
        self.assertFalse((self.root / "build").exists())

    def test_oversized_file_omitted_without_truncated_xml(self):
        (self.logs / "test.xml").write_bytes(b"x" * 65)
        with patch.object(baseline, "TEST_EVIDENCE_LIMIT", 64):
            summary = self.collect()
        self.assertEqual(summary["files"]["test.xml"], "omitted_size_limit")
        self.assertFalse((self.destination / "test.xml").exists())

    def test_convenience_symlink_outside_private_root_rejected(self):
        link = self.root / "bazel-testlogs"
        link.unlink()
        external = self.root / "outside/submodules/TextFormat/TextFormatTests"
        external.mkdir(parents=True)
        (external / "test.log").write_text('private data')
        link.symlink_to(self.root / "outside", target_is_directory=True)
        summary = self.collect()
        self.assertEqual(summary["files"]["test.log"], "unavailable_or_unsafe")
        self.assertFalse((self.destination / "test.log").exists())

    def test_redaction_expansion_is_also_bounded(self):
        (self.logs / "test.log").write_text(fixture()["TELEGRAM_API_ID"] * 9)
        with patch.object(baseline, "TEST_EVIDENCE_LIMIT", 64):
            summary = self.collect()
        self.assertEqual(summary["files"]["test.log"], "omitted_size_limit")
        self.assertFalse((self.destination / "test.log").exists())

    def test_target_file_symlink_rejected_even_within_private_root(self):
        private = self.bazel_root / "private.txt"
        private.write_text('do not expose')
        (self.logs / "test.log").symlink_to(private)
        summary = self.collect()
        self.assertEqual(summary["files"]["test.log"], "unavailable_or_unsafe")
        self.assertFalse((self.destination / "test.log").exists())

    def test_target_directory_symlink_rejected(self):
        self.logs.rmdir()
        external = self.root / "external"
        external.mkdir()
        (external / "test.log").write_text('do not expose')
        self.logs.symlink_to(external, target_is_directory=True)
        summary = self.collect()
        self.assertEqual(summary["files"]["test.log"], "unavailable_or_unsafe")

    def test_evidence_destination_symlink_rejected(self):
        external = self.root / "external"
        external.mkdir()
        (self.root / "build").symlink_to(external, target_is_directory=True)
        with self.assertRaises(baseline.InvalidSetup):
            self.collect()
        self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
