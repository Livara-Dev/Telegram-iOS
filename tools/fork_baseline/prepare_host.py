#!/usr/bin/env python3
"""Prepare only hash-pinned Bazel and the existing test's simulator on hosted macOS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def verified_download(url, destination, digest):
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Invalid SHA-256 pin")
    destination = Path(destination)
    temporary = destination.with_suffix(".download")
    created = False
    try:
        hasher = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=90) as response, temporary.open("xb") as target:
            created = True
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise ValueError("Bazel download failed SHA-256 verification")
        temporary.chmod(0o700)
        temporary.replace(destination)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def prepare_simulator():
    runtimes = json.loads(command("xcrun", "simctl", "list", "runtimes", "--json"))["runtimes"]
    runtime = next((r for r in runtimes if r.get("version") == "26.5"
                    and r.get("identifier", "").startswith("com.apple.CoreSimulator.SimRuntime.iOS-")
                    and r.get("isAvailable")), None)
    if runtime is None:
        raise RuntimeError("iOS 26.5 runtime is unavailable; existing iOS tests cannot run")
    devices = json.loads(command("xcrun", "simctl", "list", "devices", "available", "--json"))["devices"]
    if not any(d["name"] == "iPhone 17" for d in devices.get(runtime["identifier"], [])):
        command("xcrun", "simctl", "create", "iPhone 17", "com.apple.CoreSimulator.SimDeviceType.iPhone-17", runtime["identifier"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Requires an Apple Silicon macOS host")
    if args.simulator:
        prepare_simulator()
        return
    pins = json.loads((ROOT / "versions.json").read_text())
    os_version = command("sw_vers", "-productVersion")
    xcode = command("xcodebuild", "-version")
    evidence = ROOT / "build/fork-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "host.json").write_text(json.dumps({
        "commit": command("git", "rev-parse", "HEAD"),
        "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
        "macos": os_version, "xcode": xcode, "free_bytes": shutil.disk_usage(ROOT).free,
        "pins": pins, "status": "host inspected; build and feature behavior unverified",
    }, indent=2) + "\n")
    if os_version.split(".")[0] != str(pins["macos"]) or xcode.splitlines()[0] != "Xcode " + pins["xcode"]:
        raise RuntimeError("Hosted image no longer matches versions.json")
    version, digest = pins["bazel"].split(":")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("Invalid Bazel version pin")
    executable = "bazel-" + version + "-darwin-arm64"
    destination = Path(os.environ["RUNNER_TEMP"]) / executable
    verified_download("https://github.com/bazelbuild/bazel/releases/download/" + version + "/" + executable,
                      destination, digest)
    with open(os.environ["GITHUB_ENV"], "a") as target:
        target.write("FORK_BASELINE_BAZEL_PATH=" + str(destination) + "\n")
    print("Pinned host verified; Bazel downloaded and SHA-256 verified. iOS build not yet run.")


if __name__ == "__main__":
    main()
