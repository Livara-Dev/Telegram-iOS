#!/usr/bin/env python3
"""Execute exact production policy/resource-key Swift on the host, not an iOS app."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

def run():
    compiler = shutil.which("swiftc")
    if compiler is None:
        raise RuntimeError("Swift compiler unavailable; checks were not executed")
    source = (ROOT / "submodules/TelegramCore/Sources/LocalArchive/LocalArchiveCapture.swift").read_text()
    begin = source.index("func localArchiveSafeResourceKey(")
    end = source.index("\nfunc localArchiveMessageIdentityIsSupported(", begin)
    with tempfile.TemporaryDirectory(prefix="ayu-host-policy-") as temporary:
        directory = Path(temporary)
        resource = directory / "ResourceKey.swift"
        resource.write_text("import Foundation\n" + source[begin:end] + "\n")
        program = directory / "policy-checks"
        subprocess.run([compiler, "-O", str(ROOT / "submodules/LocalMessageArchive/Sources/LocalMessageArchive/ArchiveModels.swift"),
            str(resource), str(ROOT / "tools/fork_baseline/ExtendedRetentionHostChecks.swift"), "-o", str(program)],
            check=True, timeout=90)
        completed = subprocess.run([str(program)], check=True, capture_output=True, text=True, timeout=30)
        report = json.loads(completed.stdout)
        if report.get("passed") is not True or report.get("assertions", 0) < 1000:
            raise RuntimeError("Incomplete host policy result")
        report.update(native_application_built=False, native_feature_tests_executed=0,
            scope="production ArchiveModels and extracted production resource-key function on host")
        print(json.dumps(report, sort_keys=True))

if __name__ == "__main__":
    run()
