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

ROOT = Path(__file__).resolve().parents[2]
TEST_TARGET = "//submodules/TextFormat:TextFormatTests"
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
        "team_id": team, "app_center_id": "0", "is_internal_build": "false",
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
        if not any("iOS-26-5" in runtime and any(d["name"] == "iPhone 17" for d in entries) for runtime, entries in devices.items()):
            raise InvalidSetup("TextFormatTestRunner requires an available iPhone 17 simulator on iOS 26.5")
    return bazel


def proxy_arguments(bazel, root, mode, arguments):
    # Bazel's --version fast path must be the sole argument. Prepending startup
    # flags makes the upstream get_bazel_version probe fail before any build.
    if arguments == ["--version"]:
        return [str(bazel), "--version"]
    command = [str(bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc", "--bazelrc=" + str(root / ".bazelrc")]
    # Make.py's --bazelArguments is currently unused. Inject only build/test flags
    # here, preserving startup arguments such as --output_user_root.
    for argument in arguments:
        command.append(argument)
        if argument in ("build", "test"):
            command.extend(["--lockfile_mode=error", "--remote_cache=", "--remote_executor=", "--bes_backend=", "--noremote_accept_cached", "--noremote_upload_local_results"])
            if mode in ("simulator", "unsigned-device"):
                command.append("--//Telegram:disableProvisioningProfiles=true")
            if mode == "unsigned-device":
                command.append("--features=disable_legacy_signing")
    return command


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


def retain_test_evidence(bazel_root, secrets, outcome):
    """Copy only this target's bounded, redacted text outputs before Bazel cleanup."""
    destination = ROOT / "build/fork-evidence/textformat"
    for directory in (ROOT / "build", destination.parent, destination):
        if directory.is_symlink():
            raise InvalidSetup("Test evidence destination must not contain symlinks")
    destination.mkdir(parents=True, exist_ok=False)
    summary = {"target": TEST_TARGET, "invocation": outcome, "files": {}}
    source_fd = None
    try:
        # bazel-testlogs itself is an expected convenience symlink. Its resolved
        # root must belong to this invocation's private Bazel directory.
        source_root = (ROOT / "bazel-testlogs").resolve(strict=True)
        if not source_root.is_relative_to(Path(bazel_root).resolve(strict=True)):
            raise InvalidSetup("Test log root escaped the private Bazel directory")
        source_fd = os.open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in ("submodules", "TextFormat", "TextFormatTests"):
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


def publish_artifact(source, destination, mode, bundle_id, build_number):
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
        "tests": {TEST_TARGET: "passed" if mode == "simulator" else "not_run"},
        "runtime_verified": False, "signature_verified": False,
        "signing": "disabled" if mode == "unsigned-device" else "not_verified",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Upload globs never see this hidden staging directory or a partial package.
    with tempfile.TemporaryDirectory(prefix=".fork-artifact-", dir=destination.parent) as directory:
        staging = Path(directory)
        shutil.copyfile(source, staging / "Telegram.ipa")
        (staging / "SHA256SUMS").write_text(digest + "  Telegram.ipa\n")
        (staging / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
        staging.rename(destination)


def build(mode, env, build_number):
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
        for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH"):
            child_env.pop(name, None)
        common = [sys.executable, "build-system/Make/Make.py", "--bazel=" + str(proxy), "--bazelUserRoot=" + str(temporary / "bazel-root"), "--cacheDir=" + str(temporary / "cache")]
        signing_arguments = ["--codesigningInformationPath=" + str(signing)] if signing else ["--xcodeManagedCodesigning"]
        base = ["--configurationPath=" + str(configuration_path)] + signing_arguments
        secret_values = (config["api_hash"], config["api_id"])
        try:
            run_redacted(common + ["build"] + base + ["--buildNumber=" + str(build_number), "--configuration=" + ("debug_sim_arm64" if mode == "simulator" else "release_arm64"), "--lock"], child_env, secret_values)
            # The test invocation may repoint bazel-bin at a different configuration.
            captured_ipa = temporary / "Telegram.ipa"
            shutil.copyfile(ROOT / "bazel-bin/Telegram/Telegram.ipa", captured_ipa)
            if mode == "simulator":
                test_outcome = "failed"
                try:
                    run_redacted(common + ["test"] + base + ["--target=" + TEST_TARGET], child_env, secret_values)
                    test_outcome = "passed"
                finally:
                    try:
                        retain_test_evidence(temporary / "bazel-root", secret_values, test_outcome)
                    except (OSError, InvalidSetup):
                        print("Test evidence could not be retained; console output remains available.", flush=True)
            publish_artifact(captured_ipa, destination, mode, config["bundle_id"], build_number)
        finally:
            try:
                subprocess.run([str(bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc", "--output_user_root=" + str(temporary / "bazel-root"), "shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
            except (OSError, subprocess.SubprocessError):
                # Cleanup must not replace the build/test outcome. Exceptions may
                # contain command arguments, paths or output; never relay them.
                print("Warning: Bazel shutdown did not complete; temporary build data cleanup will still run.", flush=True)
    print(f"Upstream {mode} build completed. IPA requires independent signature/runtime verification.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "bazel-proxy":
        command = proxy_arguments(Path(sys.argv[2]), ROOT, sys.argv[3], sys.argv[4:])
        os.execv(command[0], command)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "simulator", "unsigned-device", "device"))
    parser.add_argument("--build-number", type=int, default=1)
    args = parser.parse_args()
    try:
        if args.build_number <= 0:
            raise InvalidSetup("Build number must be positive")
        if args.mode == "preflight":
            preflight(os.environ)
            print("Host, Bazel integrity and recursive checkout preflight passed; no iOS build has run.")
        else:
            build(args.mode, os.environ, args.build_number)
    except (InvalidSetup, OSError, subprocess.SubprocessError) as exc:
        print("Baseline blocked: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
