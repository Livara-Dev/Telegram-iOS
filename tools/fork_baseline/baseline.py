#!/usr/bin/env python3
"""Private, fail-closed wrapper around the upstream Telegram iOS build.

No package installs, signing-key imports, uploads, or runtime code changes.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

from unsigned_rules import prepare_unsigned_rules
from ipa_inspect import inspect_ipa, InvalidIPA
from simulator_probe import capture as capture_projection_launch
from dependency_cache import active_path as public_dependency_cache

ROOT = Path(__file__).resolve().parents[2]
TEST_TARGET = "//submodules/TextFormat:TextFormatTests"
# Fixed labels and evidence paths: no user-provided target or path is accepted.
TEST_TARGET_PATHS = {
    "//submodules/PasscodeCore:PasscodeCoreTests": ("passcode-core", ("submodules", "PasscodeCore", "PasscodeCoreTests")),
    TEST_TARGET: ("textformat", ("submodules", "TextFormat", "TextFormatTests")),
    "//submodules/Postbox:PostboxTests": ("postbox", ("submodules", "Postbox", "PostboxTests")),
    "//submodules/LocalMessageArchive:LocalMessageArchiveTests": (
        "local-message-archive", ("submodules", "LocalMessageArchive", "LocalMessageArchiveTests")),
    "//submodules/LocalMessageArchive:LocalArchivePlaybackTests": (
        "local-archive-playback", ("submodules", "LocalMessageArchive", "LocalArchivePlaybackTests")),
    "//submodules/SettingsUI:SettingsUITests": (
        "settings-ui", ("submodules", "SettingsUI", "SettingsUITests")),
    "//submodules/TelegramCore:TelegramCoreTests": (
        "telegram-core", ("submodules", "TelegramCore", "TelegramCoreTests")),
    "//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests": (
        "local-text-filter-projection", ("submodules", "TelegramUI", "Components", "Chat",
                                         "ChatHistoryEntry", "ChatLocalTextFilterProjectionTests")),
}
# Collect core/capture results before the hosted UI suite. The tests-only job
# attempts all targets, and every target must pass before any app-build claim.
INTEGRATION_TEST_TARGETS = (
    "//submodules/PasscodeCore:PasscodeCoreTests",
    "//submodules/Postbox:PostboxTests",
    "//submodules/LocalMessageArchive:LocalMessageArchiveTests",
    "//submodules/TelegramCore:TelegramCoreTests",
    "//submodules/LocalMessageArchive:LocalArchivePlaybackTests",
    TEST_TARGET,
    "//submodules/SettingsUI:SettingsUITests",
    "//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests",
)
# This repair scope is deliberately fixed: no caller-supplied Bazel labels.
# A focused pass is not evidence of full integration or application compilation.
FOCUSED_TEST_TARGETS = (
    "//submodules/PasscodeCore:PasscodeCoreTests",
    "//submodules/LocalMessageArchive:LocalMessageArchiveTests",
    "//submodules/LocalMessageArchive:LocalArchivePlaybackTests",
)
# Re-test the shared native hosts alongside the isolated projection host.
# This is a targeted follow-up, not a full integration/application result.
PROJECTION_TEST_TARGETS = FOCUSED_TEST_TARGETS + (
    "//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests",
)
TEST_EVIDENCE_LIMIT = 2 * 1024 * 1024


class InvalidSetup(Exception):
    pass


def require(value, pattern, name):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise InvalidSetup(f"Missing or invalid {name}; value omitted")
    return value


def configuration(env, unsigned=False):
    api_id = require(env.get("TELEGRAM_API_ID"), r"[1-9][0-9]{0,9}", "TELEGRAM_API_ID")
    api_hash = require(env.get("TELEGRAM_API_HASH"), r"[0-9a-fA-F]{32}", "TELEGRAM_API_HASH")
    bundle = require(env.get("IOS_BUNDLE_ID"), r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+){2,}", "IOS_BUNDLE_ID")
    team = require(env.get("APPLE_TEAM_ID") or ("UNSIGNED00" if unsigned else None), r"[A-Z0-9]{10}", "APPLE_TEAM_ID")
    if not unsigned and team == "UNSIGNED00":
        raise InvalidSetup("A real Apple team is required for signed device builds")
    if int(api_id) > 2147483647:
        raise InvalidSetup("TELEGRAM_API_ID exceeds the Telegram API integer range")
    # Known public/demo application IDs are not evidence of application ownership.
    if api_id in {"1", "4", "6", "8", "9", "2040"}:
        raise InvalidSetup("Use your own Telegram API application, not a known public/demo ID")
    if bundle.lower().startswith(("ph.telegra.", "org.telegram.", "com.telegram.")):
        raise InvalidSetup("Use your own bundle namespace")
    if team == "C67CF9S4VU":
        raise InvalidSetup("Use your own Apple team")
    return {
        "bundle_id": bundle, "api_id": api_id, "api_hash": api_hash,
        "team_id": team, "app_center_id": "", "is_internal_build": "false",
        "is_appstore_build": "false", "appstore_id": "0",
        "app_specific_url_scheme": "forktg", "premium_iap_product_id": "",
        "enable_siri": False, "enable_icloud": False,
    }


def output(args, cwd=ROOT):
    try:
        return subprocess.check_output(args, cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InvalidSetup(f"Required tool/check failed: {Path(args[0]).name}") from exc


def check_submodules(status):
    if not status:
        raise InvalidSetup("No recursive submodule status; checkout is incomplete")
    # Keep leading spaces: git uses ' ', '-', '+', 'U' as meaningful status.
    if any(line[:1] != " " for line in status.splitlines()):
        raise InvalidSetup("Initialize every recursive submodule at its recorded commit; no divergence allowed")


def verify_bazel(path, pin):
    version, sep, digest = pin.partition(":")
    if not sep or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise InvalidSetup("versions.json must include the Bazel SHA-256 pin")
    path = Path(path).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise InvalidSetup("Provision the pinned executable in FORK_BASELINE_BAZEL_PATH")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise InvalidSetup("Bazel SHA-256 does not match versions.json")
    if output([str(path), "--version"]) != "bazel " + version:
        raise InvalidSetup("Bazel version does not match versions.json")
    return path


def preflight(env, simulator=False):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise InvalidSetup("An Apple Silicon macOS runner is required; Linux cannot build or sign this iOS app")
    pins = json.loads((ROOT / "versions.json").read_text())
    if output(["sw_vers", "-productVersion"]).split(".")[0] != str(pins["macos"]):
        raise InvalidSetup("macOS major version must match versions.json")
    if output(["xcodebuild", "-version"]).splitlines()[0] != "Xcode " + pins["xcode"]:
        raise InvalidSetup("Xcode must exactly match versions.json; no override is permitted")
    bazel = verify_bazel(env.get("FORK_BASELINE_BAZEL_PATH", ""), pins["bazel"])
    result = subprocess.run(["git", "submodule", "status", "--recursive"], cwd=ROOT, capture_output=True, text=True, check=True)
    check_submodules(result.stdout)
    output(["git", "submodule", "foreach", "--quiet", "--recursive", "git diff --quiet && git diff --cached --quiet"])
    if simulator:
        devices = json.loads(output(["xcrun", "simctl", "list", "devices", "available", "--json"]))["devices"]
        if not any("iOS-27-0" in runtime and any(d["name"] == "iPhone 17" for d in entries) for runtime, entries in devices.items()):
            raise InvalidSetup("The explicit iOS test runners require an available iPhone 17 simulator on iOS 27.0")
    return bazel


def proxy_arguments(bazel, root, mode, arguments, rules_override=None, repository_cache=None):
    if rules_override is not None and mode != "unsigned-device":
        raise InvalidSetup("Unsigned rules overlay is forbidden for signed or simulator builds")
    # Bazel's --version fast path must be the sole argument. Prepending startup
    # flags makes the upstream get_bazel_version probe fail before any build.
    if arguments == ["--version"]:
        return [str(bazel), "--version"]
    command = [str(bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc", "--bazelrc=" + str(root / ".bazelrc")]
    # Make.py's --bazelArguments is currently unused. Inject only build/test flags
    # here, preserving startup arguments such as --output_user_root.
    in_build_command = False
    skip_cache_value = False
    for argument in arguments:
        if skip_cache_value:
            skip_cache_value = False
            continue
        if in_build_command and argument in ("--disk_cache", "--repository_cache"):
            skip_cache_value = True
            continue
        if in_build_command and argument.startswith(("--disk_cache=", "--repository_cache=")):
            continue
        command.append(argument)
        if argument in ("build", "test"):
            in_build_command = True
            # This invocation already reuses its private output_user_root across
            # suites. A second, disposable disk cache retains redundant artifacts
            # on the same hosted runner and is never reused by a later job.
            command.extend(["--lockfile_mode=error", "--disk_cache=", "--remote_cache=", "--remote_executor=", "--bes_backend=", "--noremote_accept_cached", "--noremote_upload_local_results"])
            # This is a separate, sanitized public download cache. It never
            # contains compiled actions or generated API/signing configuration.
            command.append("--repository_cache=" + (str(repository_cache) if repository_cache is not None else ""))
            if argument == "test" and mode == "simulator":
                # Use the pinned rules_apple simulator creator. It waits for
                # simctl bootstatus and reuses the private runner's simulator,
                # rather than cold-booting a new device for every test target.
                command.extend(["--test_env=REUSE_GLOBAL_SIMULATOR=1", "--test_env=SIMULATOR_REUSE_SIMULATOR=1"])
            if mode in ("simulator", "unsigned-device"):
                command.append("--//Telegram:disableProvisioningProfiles=true")
            if mode == "unsigned-device":
                command.append("--features=disable_legacy_signing")
                if rules_override is not None:
                    command.extend(["--override_module=rules_apple=" + str(rules_override), "--features=ayugram.unsigned_device"])
    return command


def report_build_storage(path, phase):
    """Best-effort scratch-volume diagnostics containing no paths or file names."""
    try:
        usage = shutil.disk_usage(path)
        values = (usage.total, usage.used, usage.free)
        if any(type(value) is not int or not 0 <= value <= (1 << 64) - 1 for value in values):
            raise ValueError("Invalid disk usage counters")
        message = (f"Build storage {phase}: total_bytes={values[0]} "
                   f"used_bytes={values[1]} free_bytes={values[2]}")
    except (OSError, ValueError, TypeError, OverflowError):
        # A failed diagnostic must not replace the original build/test outcome,
        # and OS errors can contain paths or command arguments: do not print them.
        message = f"Build storage {phase}: unavailable"
    try:
        print(message, flush=True)
    except OSError:
        pass


@contextlib.contextmanager
def private_configuration(config):
    generated = [ROOT / "build-input/configuration-repository", ROOT / "build-input/configuration-repository-workdir"]
    if any(os.path.lexists(path) for path in generated):
        raise InvalidSetup("Use a fresh isolated checkout; existing generated signing/configuration data will not be overwritten")
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="fork-ios-") as directory:
            temporary = Path(directory)
            path = temporary / "configuration.json"
            path.write_text(json.dumps(config))
            yield temporary, path
    finally:
        for path in generated:
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        os.umask(old_umask)


def run_redacted(args, env, secrets):
    # Upstream compiler failures can echo generated constants. Never relay them raw.
    with subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace") as process:
        for line in process.stdout:
            for value in secrets:
                line = line.replace(value, "[REDACTED]")
            print(line, end="", flush=True)
        if process.wait():
            raise InvalidSetup("Upstream build/test failed; no successful baseline is recorded")


def retain_bundle_diagnostics(bazel_root, secrets):
    """Read-only diagnostics for the exact private build's two known bundles.

    Do not copy bundle contents, signing material, generated configuration, or
    process environment. Diagnostic failure must never mask the build failure.
    """
    destination = ROOT / "build/fork-evidence"
    try:
        for directory in (ROOT / "build", destination):
            if directory.is_symlink():
                return
        destination.mkdir(parents=True, exist_ok=True)
        report = {"read_only": True, "signature_or_installation_verified": False, "bundles": {}}
        try:
            private_root = Path(bazel_root).resolve(strict=True)
            app = (ROOT / "bazel-bin/Telegram/Telegram_archive-root/Payload/Telegram.app").resolve(strict=True)
            if not app.is_relative_to(private_root) or not app.is_dir():
                return
        except (OSError, RuntimeError):
            return
        for name, bundle, executable in (
            ("application", app, "Telegram"),
            ("ui_framework", app / "Frameworks/TelegramUIFramework.framework", "TelegramUIFramework"),
        ):
            entry = {"commands": {}}
            report["bundles"][name] = entry
            try:
                resolved = bundle.resolve(strict=True)
                binary = (resolved / executable).resolve(strict=True)
                if not resolved.is_relative_to(app) or not binary.is_relative_to(app):
                    entry["state"] = "unsafe_path"
                    continue
                info = binary.stat()
                if not stat.S_ISREG(info.st_mode):
                    entry["state"] = "not_regular"
                    continue
                entry.update(state="available", executable_bytes=info.st_size, executable_mode=stat.S_IMODE(info.st_mode))
            except (OSError, RuntimeError):
                entry["state"] = "unavailable"
                continue
            for label, command in (
                ("signature_details", ["/usr/bin/codesign", "--display", "--verbose=2", str(resolved)]),
                ("signature_check", ["/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(resolved)]),
                ("attribute_names", ["/usr/bin/xattr", str(resolved)]),
            ):
                try:
                    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            timeout=8, check=False)
                    diagnostic = result.stdout[:65536].decode("utf-8", errors="replace")
                    # Remove private credentials and machine-specific paths, even
                    # if an external tool unexpectedly echoes them in an error.
                    for value in secrets:
                        if value:
                            diagnostic = diagnostic.replace(value, "[REDACTED]")
                    for path in sorted({str(private_root), str(Path(bazel_root))}, key=len, reverse=True):
                        diagnostic = diagnostic.replace(path, "[PRIVATE_BUILD]")
                    for path in sorted({str(ROOT.resolve()), str(ROOT)}, key=len, reverse=True):
                        diagnostic = diagnostic.replace(path, "[SOURCE]")
                    entry["commands"][label] = {"exit_code": result.returncode, "output": diagnostic}
                except (OSError, subprocess.SubprocessError):
                    entry["commands"][label] = {"state": "unavailable"}
        # Exclusive creation avoids following a destination file symlink.
        with (destination / "bundle-diagnostics.json").open("x", encoding="utf-8") as target:
            target.write(json.dumps(report, indent=2) + "\n")
    except (OSError, ValueError, TypeError):
        pass


def retain_test_evidence(bazel_root, secrets, outcome, test_target=TEST_TARGET, prebuild=False):
    """Copy only this target's bounded, redacted text outputs before Bazel cleanup."""
    if test_target not in TEST_TARGET_PATHS:
        raise InvalidSetup("Test evidence target is not allowlisted")
    evidence_name, source_components = TEST_TARGET_PATHS[test_target]
    destination = ROOT / "build/fork-evidence" / (("prebuild-" if prebuild else "") + evidence_name)
    for directory in (ROOT / "build", destination.parent, destination):
        if directory.is_symlink():
            raise InvalidSetup("Test evidence destination must not contain symlinks")
    destination.mkdir(parents=True, exist_ok=False)
    summary = {"target": test_target, "invocation": outcome, "files": {}}
    source_fd = None
    try:
        # bazel-testlogs itself is an expected convenience symlink. Its resolved
        # root must belong to this invocation's private Bazel directory.
        source_root = (ROOT / "bazel-testlogs").resolve(strict=True)
        if not source_root.is_relative_to(Path(bazel_root).resolve(strict=True)):
            raise InvalidSetup("Test log root escaped the private Bazel directory")
        source_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in source_components:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
            os.close(source_fd)
            source_fd = child_fd
        for name in ("test.xml", "test.log"):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        summary["files"][name] = "omitted_nonregular"
                        continue
                    data = source.read(TEST_EVIDENCE_LIMIT + 1)
                if len(data) > TEST_EVIDENCE_LIMIT:
                    summary["files"][name] = "omitted_size_limit"
                    continue
                text = data.decode("utf-8", errors="replace")
                for value in secrets:
                    if value:
                        text = text.replace(value, "[REDACTED]")
                if len(text.encode("utf-8")) > TEST_EVIDENCE_LIMIT:
                    summary["files"][name] = "omitted_size_limit"
                    continue
                with (destination / name).open("x", encoding="utf-8") as target:
                    target.write(text)
                summary["files"][name] = "retained_redacted"
            except OSError:
                # Do not copy arbitrary file types or disclose filesystem details.
                summary["files"][name] = "unavailable_or_unsafe"
    except (OSError, RuntimeError, InvalidSetup):
        summary["files"] = {name: "unavailable_or_unsafe" for name in ("test.xml", "test.log")}
    finally:
        if source_fd is not None:
            os.close(source_fd)
    (destination / "collection.json").write_text(json.dumps(summary, indent=2) + "\n")


def retain_integration_test_summary(test_results, targets=INTEGRATION_TEST_TARGETS):
    destination = ROOT / "build/fork-evidence"
    for directory in (ROOT / "build", destination):
        if directory.is_symlink():
            raise InvalidSetup("Test evidence destination must not contain symlinks")
    destination.mkdir(parents=True, exist_ok=True)
    summary = {
        "source_commit": output(["git", "rev-parse", "HEAD"]),
        "toolchain": json.loads((ROOT / "versions.json").read_text()),
        "tests": {target: test_results.get(target, "not_run") for target in targets},
        "validation_scope": "focused" if targets == FOCUSED_TEST_TARGETS else "projection" if targets == PROJECTION_TEST_TARGETS else "full",
        "full_integration_suite_requested": targets == INTEGRATION_TEST_TARGETS,
        "tests_only": True,
        "full_application_built": False,
        "physical_device_verified": False,
    }
    with (destination / "integration-tests.json").open("x", encoding="utf-8") as target:
        target.write(json.dumps(summary, indent=2) + "\n")


def publish_artifact(source, destination, mode, bundle_id, build_number, test_results=None, binary_report=None):
    if destination.exists():
        raise FileExistsError("Artifact destination already exists")
    hasher = hashlib.sha256()
    with source.open("rb") as payload:
        while chunk := payload.read(1024 * 1024):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    metadata = {
        "source_commit": output(["git", "rev-parse", "HEAD"]),
        "mode": mode, "bundle_id": bundle_id, "build_number": build_number,
        "sha256": digest, "toolchain": json.loads((ROOT / "versions.json").read_text()),
        "tests": (dict(test_results) if test_results is not None else
                  {TEST_TARGET: "passed" if mode == "simulator" else "not_run"}),
        "runtime_verified": False, "signature_verified": False,
        "integration_qualification": "not_evaluated_by_this_job" if mode == "unsigned-device" else "see_tests",
        "signing": "disabled" if mode == "unsigned-device" else "not_verified",
        "main_executable": binary_report,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Upload globs never see this hidden staging directory or a partial package.
    with tempfile.TemporaryDirectory(prefix=".fork-artifact-", dir=destination.parent) as directory:
        staging = Path(directory)
        shutil.copyfile(source, staging / "Telegram.ipa")
        (staging / "SHA256SUMS").write_text(digest + "  Telegram.ipa\n")
        (staging / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
        staging.rename(destination)


def build(mode, env, build_number, integration_tests=False, tests_only=False, focused_tests=False, projection_tests=False):
    if integration_tests and mode != "simulator":
        raise InvalidSetup("Integration tests require simulator mode")
    if tests_only and not (integration_tests and mode == "simulator"):
        raise InvalidSetup("Tests-only requires simulator mode with integration tests")
    if focused_tests and not (mode == "simulator" and integration_tests and tests_only):
        raise InvalidSetup("Focused tests require simulator --integration-tests --tests-only")
    if focused_tests and projection_tests:
        raise InvalidSetup("Focused and projection test scopes are mutually exclusive")
    if projection_tests and not (mode == "simulator" and integration_tests and tests_only):
        raise InvalidSetup("Projection tests require simulator --integration-tests --tests-only")
    targets = PROJECTION_TEST_TARGETS if projection_tests else FOCUSED_TEST_TARGETS if focused_tests else INTEGRATION_TEST_TARGETS
    config = configuration(env, unsigned=mode != "device")
    bazel = preflight(env, simulator=mode == "simulator")
    destination = ROOT / "build" / ("fork-baseline-" + mode)
    if destination.exists():
        raise InvalidSetup("Baseline artifact directory already exists; use a fresh checkout or move the previous artifacts")
    signing = None
    if mode == "device":
        signing = Path(env.get("FORK_BASELINE_SIGNING_DIR", "")).resolve()
        if ROOT in signing.parents or signing == ROOT or not (signing / "profiles").is_dir() or not (signing / "certs").is_dir():
            raise InvalidSetup("FORK_BASELINE_SIGNING_DIR must be outside this checkout and contain profiles/ and certs/")
        if not list((signing / "profiles").glob("*.mobileprovision")):
            raise InvalidSetup("Provide your own device provisioning profiles")
        identities = output(["security", "find-identity", "-v", "-p", "codesigning"])
        if "valid identities found" not in identities or "0 valid identities found" in identities:
            raise InvalidSetup("Provision your signing identity in the ephemeral runner keychain")
    with private_configuration(config) as (temporary, configuration_path):
        proxy = temporary / "bazel-proxy"
        proxy.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(Path(__file__).resolve()), "bazel-proxy", str(bazel), mode]) + ' "$@"\n')
        proxy.chmod(0o700)
        child_env = dict(env)
        child_env.pop("FORK_UNSIGNED_RULES_PATH", None)
        if mode == "unsigned-device":
            overlay = temporary / "unsigned-rules-apple"
            try:
                overlay_manifest = prepare_unsigned_rules(ROOT / "build-system/bazel-rules/rules_apple", overlay)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                raise InvalidSetup("Reviewed unsigned rules overlay could not be prepared") from error
            child_env["FORK_UNSIGNED_RULES_PATH"] = str(overlay)
            evidence = ROOT / "build/fork-evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "unsigned-rules.json").write_text(json.dumps(overlay_manifest, indent=2) + "\n")
        for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
            child_env.pop(name, None)
        common = [sys.executable, "build-system/Make/Make.py", "--bazel=" + str(proxy), "--bazelUserRoot=" + str(temporary / "bazel-root")]
        signing_arguments = ["--codesigningInformationPath=" + str(signing)] if signing else ["--xcodeManagedCodesigning"]
        base = ["--configurationPath=" + str(configuration_path)] + signing_arguments
        secret_values = (config["api_hash"], config["api_id"])
        test_results = {}

        def run_tests(targets, continue_after_failure=False):
            first_failure = None
            for target in targets:
                test_outcome = "failed"
                try:
                    run_redacted(common + ["test"] + base + ["--target=" + target], child_env, secret_values)
                    test_outcome = "passed"
                except InvalidSetup as error:
                    if target == "//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests":
                        capture_projection_launch(ROOT, secret_values)
                    if not continue_after_failure:
                        raise
                    # A failed target must not hide results for independent
                    # suites. Keep the first failure and re-raise only after
                    # every fixed target's invocation/evidence is attempted.
                    if first_failure is None:
                        first_failure = error
                finally:
                    test_results[target] = test_outcome
                    try:
                        if tests_only:
                            retain_test_evidence(temporary / "bazel-root", secret_values, test_outcome, target, prebuild=True)
                        else:
                            retain_test_evidence(temporary / "bazel-root", secret_values, test_outcome, target)
                    except (OSError, InvalidSetup):
                        print("Test evidence could not be retained; console output remains available.", flush=True)
            if first_failure is not None:
                raise first_failure

        try:
            report_build_storage(temporary, "before")
            # Run the real iOS integration suites first to surface errors before
            # the much larger full-application build. This is not a substitute
            # for that build; no package is published until both have succeeded.
            if integration_tests:
                try:
                    run_tests(targets, continue_after_failure=tests_only)
                finally:
                    if tests_only:
                        retain_integration_test_summary(test_results, targets=targets)
            if not tests_only:
                try:
                    run_redacted(common + ["build"] + base + ["--buildNumber=" + str(build_number), "--configuration=" + ("debug_sim_arm64" if mode == "simulator" else "release_arm64"), "--lock"], child_env, secret_values)
                except InvalidSetup:
                    retain_bundle_diagnostics(temporary / "bazel-root", secret_values)
                    raise
                # The test invocation may repoint bazel-bin at a different configuration.
                captured_ipa = temporary / "Telegram.ipa"
                shutil.copyfile(ROOT / "bazel-bin/Telegram/Telegram.ipa", captured_ipa)
                try:
                    binary_report = inspect_ipa(captured_ipa, mode, config["bundle_id"])
                except InvalidIPA as error:
                    raise InvalidSetup(str(error)) from error
                if mode == "simulator" and not integration_tests:
                    run_tests((TEST_TARGET,))
                publish_artifact(captured_ipa, destination, mode, config["bundle_id"], build_number,
                                 test_results if mode == "simulator" else None, binary_report=binary_report)
        finally:
            # Measure while build products and diagnostics still exist. Cleanup
            # would otherwise hide pressure at the point of a packaging failure.
            report_build_storage(temporary, "after")
            try:
                subprocess.run([str(bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc", "--output_user_root=" + str(temporary / "bazel-root"), "shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
            except (OSError, subprocess.SubprocessError):
                # Cleanup must not replace the build/test outcome. Exceptions may
                # contain command arguments, paths or output; never relay them.
                print("Warning: Bazel shutdown did not complete; temporary build data cleanup will still run.", flush=True)
    if tests_only:
        scope = "Projection and shared-host" if projection_tests else "Focused repair" if focused_tests else "Explicit iOS integration"
        print(scope + " test targets passed. No full application build or IPA was produced.")
    else:
        print(f"Upstream {mode} build completed. IPA requires independent signature/runtime verification.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "bazel-proxy":
        command = proxy_arguments(Path(sys.argv[2]), ROOT, sys.argv[3], sys.argv[4:], rules_override=os.environ.get("FORK_UNSIGNED_RULES_PATH"), repository_cache=public_dependency_cache(os.environ))
        os.execv(command[0], command)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "simulator", "unsigned-device", "device"))
    parser.add_argument("--build-number", type=int, default=1)
    parser.add_argument("--integration-tests", action="store_true",
                        help="Run the fixed Postbox/archive/TelegramCore/TextFormat iOS suites before the full simulator build")
    parser.add_argument("--tests-only", action="store_true",
                        help="With simulator --integration-tests, retain test evidence without building or publishing an app")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--focused-tests", action="store_true",
                       help="With simulator --integration-tests --tests-only, run only fixed PasscodeCore/archive/playback repair targets")
    scope.add_argument("--projection-tests", action="store_true",
                       help="With simulator --integration-tests --tests-only, run the fixed repair targets plus the isolated projection host")
    args = parser.parse_args()
    try:
        if args.build_number <= 0:
            raise InvalidSetup("Build number must be positive")
        if args.integration_tests and args.mode != "simulator":
            raise InvalidSetup("Integration tests require simulator mode")
        if args.tests_only and not (args.integration_tests and args.mode == "simulator"):
            raise InvalidSetup("Tests-only requires simulator mode with integration tests")
        if args.focused_tests and not (args.mode == "simulator" and args.integration_tests and args.tests_only):
            raise InvalidSetup("Focused tests require simulator --integration-tests --tests-only")
        if args.projection_tests and not (args.mode == "simulator" and args.integration_tests and args.tests_only):
            raise InvalidSetup("Projection tests require simulator --integration-tests --tests-only")
        if args.mode == "preflight":
            preflight(os.environ)
            print("Host, Bazel integrity and recursive checkout preflight passed; no iOS build has run.")
        else:
            build(args.mode, os.environ, args.build_number, integration_tests=args.integration_tests, tests_only=args.tests_only, focused_tests=args.focused_tests, projection_tests=args.projection_tests)
    except (InvalidSetup, OSError, subprocess.SubprocessError) as exc:
        print("Baseline blocked: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
