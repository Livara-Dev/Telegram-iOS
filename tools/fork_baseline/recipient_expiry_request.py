#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06.
"""Validate one explicit recipient-expiry request; never build, publish or mutate Git."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SOURCE = "5d241267735b3958c225681d173853568bab705d"
REPOSITORY = "Livara-Dev/Telegram-iOS"
BRANCH = "refs/heads/test/recipient-expiry-worker"
REQUEST = "ci/recipient-expiry-request.json"
WORKER = "submodules/TelegramCore/Sources/State/ManagedAutoremoveMessageOperations.swift"
CANDIDATE_PATHS = frozenset({
    ".github/workflows/recipient-expiry-tests.yml",
    "docs/fork/RECIPIENT-EXPIRY-QUALIFICATION.md",
    WORKER,
    "submodules/TelegramCore/Tests/LocalArchiveRecipientExpiryWorkerTests.swift",
    "submodules/TelegramCore/Tests/LocalArchiveRecipientMediaFixtures.swift",
    "tools/fork_audit/check.py",
    "tools/fork_audit/tests/test_check.py",
    "tools/fork_baseline/recipient_expiry_request.py",
    "tools/fork_baseline/tests/test_recipient_expiry_request.py",
})
WORKER_SIGNATURE = b"func managedAutoremoveMessageOperations(network: Network, postbox: Postbox, isRemove: Bool) -> Signal<Void, NoError> {\n"
WORKER_WRAPPER = WORKER_SIGNATURE + b"""    return managedAutoremoveMessageOperations(postbox: postbox, isRemove: isRemove,
        globalTimeDifference: { network.globalTimeDifference })
}

// Modified 2026-11-06: the real worker needs only the network time offset. Keeping that reader
// injectable lets native tests drive its actual timestamp view and timer without
// constructing an unrelated live Network/session or calling expiry helpers.
func managedAutoremoveMessageOperations(postbox: Postbox, isRemove: Bool,
                                        globalTimeDifference: @escaping () -> Double) -> Signal<Void, NoError> {
"""


class RequestError(ValueError):
    pass


def git(root, *arguments):
    try:
        return subprocess.check_output(["git", "-C", str(root), *arguments], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RequestError("Git source verification failed") from error


def sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RequestError("Invalid source identifier")
    return value


def paths(data):
    return {item.decode("utf-8") for item in data.split(b"\0") if item}


def expected_worker(original):
    needle = b"subscriber.putNext(network.globalTimeDifference)"
    if original.count(WORKER_SIGNATURE) != 1 or original.count(needle) != 2:
        raise RequestError("Pinned worker source shape changed")
    # The second network read belongs to story expiration and stays unchanged.
    return original.replace(WORKER_SIGNATURE, WORKER_WRAPPER, 1).replace(
        needle, b"subscriber.putNext(globalTimeDifference())", 1)


def require_candidate_paths(changed):
    if changed != CANDIDATE_PATHS:
        raise RequestError("Candidate changes differ from the exact reviewed nine-file scope")


def regular_tree_file(root, revision, name):
    entry = git(root, "ls-tree", "-z", revision, "--", name).split(b"\0")[0]
    if not entry.startswith(b"100644 blob ") or entry.split(b"\t", 1)[-1] != name.encode():
        raise RequestError("Reviewed source must be a regular non-executable file")
    return git(root, "show", revision + ":" + name)


def validate_candidate(root, candidate):
    candidate = sha(candidate)
    if git(root, "cat-file", "-t", candidate).strip() != b"commit":
        raise RequestError("Candidate must identify a commit")
    require_candidate_paths(paths(git(root, "diff", "--name-only", "--no-renames", "-z", PRODUCTION_SOURCE, candidate, "--")))
    for name in CANDIDATE_PATHS:
        regular_tree_file(root, candidate, name)
    original = regular_tree_file(root, PRODUCTION_SOURCE, WORKER)
    if regular_tree_file(root, candidate, WORKER) != expected_worker(original):
        raise RequestError("Worker differs from the exact reader-only seam")


def check_worktree(root):
    request = Path(root) / REQUEST
    if request.exists() or request.is_symlink():
        raise RequestError("Request must remain absent while preparing the candidate")
    changed = set()
    for arguments in (
        ("diff", "--name-only", "--no-renames", "-z", PRODUCTION_SOURCE, "--"),
        ("diff", "--cached", "--name-only", "--no-renames", "-z", PRODUCTION_SOURCE, "--"),
        ("diff", "--name-only", "--no-renames", "-z", PRODUCTION_SOURCE, "HEAD", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        changed.update(paths(git(root, *arguments)))
    require_candidate_paths(changed)
    for name in CANDIDATE_PATHS:
        file = Path(root) / name
        linked_parent = any((Path(root) / parent).is_symlink() for parent in Path(name).parents if str(parent) != ".")
        if not file.is_file() or file.is_symlink() or linked_parent or file.stat().st_mode & 0o111:
            raise RequestError("Reviewed worktree source must be a regular file")
    if (Path(root) / WORKER).read_bytes() != expected_worker(regular_tree_file(root, PRODUCTION_SOURCE, WORKER)):
        raise RequestError("Worker differs from the exact reader-only seam")
    clean = not git(root, "status", "--porcelain", "--untracked-files=all").strip()
    candidate = git(root, "rev-parse", "HEAD").decode().strip() if clean else None
    return report(candidate, None)


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RequestError("Request contains duplicate fields")
        result[key] = value
    return result


def parse_request(data):
    if len(data) > 4096:
        raise RequestError("Request exceeds the bounded schema")
    try:
        value = json.loads(data, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise RequestError("Request is not valid JSON") from error
    expected = {"schemaVersion", "candidate_sha", "production_source_sha", "validation_scope", "nonce"}
    if not isinstance(value, dict) or set(value) != expected:
        raise RequestError("Request fields do not match the exact schema")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise RequestError("Unsupported request schema")
    sha(value["candidate_sha"])
    if value["production_source_sha"] != PRODUCTION_SOURCE or value["validation_scope"] != "core-only":
        raise RequestError("Request source or validation scope is not authorized")
    if not isinstance(value["nonce"], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value["nonce"]) is None:
        raise RequestError("Request nonce must be a bounded safe identifier")
    return value


def report(candidate, request):
    return {"candidate_sha": candidate, "production_source_sha": PRODUCTION_SOURCE,
            "request_sha": request, "validation_scope": "core-only"}


def validate_request(root, environment):
    if (environment.get("GITHUB_REPOSITORY") != REPOSITORY or environment.get("GITHUB_REF") != BRANCH
            or environment.get("GITHUB_EVENT_NAME") != "push"):
        raise RequestError("Request requires the exact repository, branch and push event")
    request_sha = sha(environment.get("GITHUB_SHA"))
    if git(root, "rev-parse", "HEAD").decode().strip() != request_sha:
        raise RequestError("Checkout does not match the request commit")
    value = parse_request(regular_tree_file(root, request_sha, REQUEST))
    candidate = value["candidate_sha"]
    parents = git(root, "show", "-s", "--format=%P", request_sha).decode().strip().split()
    if parents != [candidate]:
        raise RequestError("Request commit must directly follow the exact candidate")
    if paths(git(root, "diff", "--name-only", "--no-renames", "-z", candidate, request_sha, "--")) != {REQUEST}:
        raise RequestError("Request commit must change only the request file")
    validate_candidate(root, candidate)
    return report(candidate, request_sha)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-worktree", action="store_true")
    options = parser.parse_args(arguments)
    try:
        result = check_worktree(ROOT) if options.check_worktree else validate_request(ROOT, os.environ)
    except (RequestError, OSError, UnicodeError):
        print("Recipient-expiry request verification failed; no build authorized.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
