#!/usr/bin/env python3
"""Pinned-source and reviewed-feature scope guard; not a runtime security proof."""
import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

BASELINE = "279f89e4d4f63f17227bf877dc3d8990f22ecd58"
REFERENCE = "7013145676d36d82ee13c02a89f72097b7490dcd"
MANIFESTS = ("versions.json", ".gitmodules", "MODULE.bazel", "MODULE.bazel.lock", ".bazelrc")
ADDED_PREFIXES = (
    "docs/fork/", "tools/fork_audit/", "tools/fork_baseline/", "submodules/LocalMessageArchive/",
    "submodules/TelegramCore/Sources/LocalArchive/", "submodules/TelegramCore/Tests/LocalArchive",
    "submodules/TelegramCore/Sources/LocalPrivacy/", "submodules/TelegramCore/Tests/LocalGhost",
    "submodules/TelegramCore/Sources/LocalMessageFilter/", "submodules/TelegramCore/Tests/LocalMessageFilter",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Tests/",
    "submodules/TelegramUI/Components/Chat/ChatMessageLocalFilterBubbleContentNode/",
)
ADDED_FILES = {
    ".github/workflows/archive-integration.yml",
    ".github/workflows/archive-tests.yml",
    ".github/workflows/fork-baseline.yml", "COPYING",
    "docs/AYUGRAM_FEATURE_MATRIX.md", "docs/WINDOWS_INSTALL.md", "docs/SECURITY_ASSESSMENT.md",
    "submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift",
    "submodules/SettingsUI/Sources/LocalGhostSettingsController.swift",
    "submodules/SettingsUI/Sources/LocalMessageFilterSettingsController.swift",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatLocalTextFilterProjection.swift",
}
# Explicit integration seams only. Everything else in the upstream tree, including
# MTProto, authentication methods, cryptography, Postbox, dependency manifests and
# gitlinks, stays pinned. An allowlist is a scope tripwire, not a semantic audit of
# every change within these files; reviewers must inspect their Git diffs.
REVIEWED_MODIFICATIONS = {
    "submodules/TelegramUI/Components/Chat/ChatMessageBubbleItemNode/BUILD": "link the local text-filter placeholder component",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/BUILD": "isolated local text-filter projection test target and host",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatHistoryEntry.swift": "presentation-only filter state preserving row identity",
    "submodules/TelegramUI/Components/ChatControllerInteraction/Sources/ChatControllerInteraction.swift": "controller-scoped temporary reveal state",
    "submodules/TelegramUI/Sources/ChatHistoryListNode.swift": "off-main literal evaluation and identity-preserving projection",
    "submodules/TelegramUI/Sources/ChatInterfaceStateContextMenus.swift": "create local literal rule from eligible message text",
    "submodules/TelegramUI/Components/Chat/ChatMessageBubbleItemNode/Sources/ChatMessageBubbleItemNode.swift": "render local text placeholder and guard hidden-message actions",
    "submodules/TelegramUI/Components/Chat/ChatMessageItemView/Sources/ChatMessageItemView.swift": "redact hidden message accessibility data",
    "Telegram/BUILD": "allow the isolated filter projection test host to bundle existing string resources",
    "submodules/TelegramCore/BUILD": "link the local archive and its integration tests",
    "submodules/TelegramCore/Tests/EngineMessageReplyQuoteTests.swift": "repair stale rich-quote fixture to use the production rich initializer",
    "submodules/TelegramCore/Sources/Account/Account.swift": "account-owned archive lifecycle",
    "submodules/TelegramCore/Sources/Account/AccountManager.swift": "register archive outbox decoder",
    "submodules/TelegramCore/Sources/State/AccountStateManagementUtils.swift": "capture before server delete/edit",
    "submodules/TelegramCore/Sources/State/ManagedLocalInputActivities.swift": "per-account activity suppression before existing request dispatch",
    "submodules/TelegramCore/Sources/State/ManagedSynchronizeViewStoriesOperations.swift": "persisted story-receipt policy and generation checks before typed dispatch",
    "submodules/TelegramCore/Sources/SyncCore/SynchronizeViewStoriesOperation.swift": "backward-compatible story-receipt generation and bounded coalescing",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/Stories.swift": "gate ordinary and pinned story-view receipt production",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/DeleteMessagesInteractively.swift": "capture before user-initiated message deletion",
    "submodules/TelegramCore/Sources/PendingMessages/RequestEditMessage.swift": "capture before acknowledged own-edit replacement",
    "submodules/TelegramCore/Sources/SyncCore/SyncCore_Namespaces.swift": "reserve archive operation tag",
    "submodules/SettingsUI/Sources/Data and Storage/DataAndStorageSettingsController.swift": "native archive settings entry",
    "submodules/TelegramUI/Sources/ChatController.swift": "open this conversation's deleted-message archive",
    "submodules/TelegramUI/Sources/Chat/ChatControllerOpenMessageContextMenu.swift": "open this message's saved versions",
}
SECRET_SUFFIXES = {".p12", ".pfx", ".mobileprovision", ".key", ".pem", ".cer", ".keystore"}
PRIVATE_KEY = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
CREDENTIAL = re.compile(
    rb"[\"']?(?:TELEGRAM_API_HASH|api_hash|P12_PASSWORD|APPLE_CERTIFICATE_PASSWORD)[\"']?"
    rb"\s*[:=]\s*[\"']([^\"'\r\n]+)[\"']", re.IGNORECASE
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def tree(root, revision):
    entries = {}
    for record in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            entries[path.decode()] = (mode, kind, oid)
    return entries


def provenance(root, revision=BASELINE):
    entries = tree(root, revision)
    return {
        "schema": 1,
        "telegram_ios": {"url": "https://github.com/TelegramMessenger/Telegram-iOS", "commit": revision},
        "ayugram_reference": {"url": "https://github.com/AyuGram/AyuGram4A", "commit": REFERENCE},
        "build_manifest_sha256": {
            path: hashlib.sha256(git(root, "show", f"{revision}:{path}")).hexdigest()
            for path in MANIFESTS if path in entries
        },
        "gitlinks": {path: item[2] for path, item in sorted(entries.items()) if item[0] == "160000"},
        "scope": "Top-level pinned source inventory only; recursive dependency review and runtime privacy verification remain required.",
    }


def allowed_addition(path):
    return path in ADDED_FILES or path.startswith(ADDED_PREFIXES)


def changed_paths(root, revision):
    # Compare working files to the immutable base, including staged and committed changes.
    tracked = git(root, "diff", "--name-only", "--no-renames", "--ignore-submodules=dirty", "-z", revision, "--")
    staged = git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", revision, "--")
    committed = git(root, "diff", "--name-only", "--no-renames", "-z", revision, "HEAD", "--")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(set(p.decode() for p in (tracked + staged + committed + untracked).split(b"\0") if p))


def inspect_added_file(root, path):
    file = root / path
    if file.is_symlink():
        return [f"Added path must be a regular file: {path}"]
    if not file.exists():
        return []
    if not file.is_file():
        return [f"Added path must be a regular file: {path}"]
    return inspect_data(root, path, file.read_bytes())


def inspect_data(root, path, data):
    failures = []
    file = Path(path)
    if file.suffix.lower() in SECRET_SUFFIXES or file.name in {".env", "credentials.json"} or file.name.startswith(".env."):
        failures.append(f"Signing/credential file must not be committed: {path}")
    if PRIVATE_KEY.search(data):
        failures.append(f"Private-key marker found: {path}")
    for match in CREDENTIAL.finditer(data):
        value = match.group(1)
        # Only unmistakably synthetic values in test code are exempted; never print values.
        test_file = path.startswith(("tools/fork_audit/tests/", "tools/fork_baseline/tests/"))
        synthetic = test_file and value in {b"0" * 32, b"a" * 32, b"b" * 32, b"test-only"}
        placeholder = value.startswith((b"${", b"<")) or value in {b"", b"REQUIRED", b"REDACTED"}
        if not (synthetic or placeholder):
            failures.append(f"Literal credential assignment found: {path}")
    if file.suffix == ".py":
        try:
            parsed = ast.parse(data, filename=path)
        except (SyntaxError, UnicodeError):
            return failures + [f"Python source cannot be parsed: {path}"]
        local_modules = {p.stem for directory in (root / "tools/fork_audit", root / "tools/fork_baseline")
                         for p in directory.rglob("*.py")}
        for node in ast.walk(parsed):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in names:
                top = name.split(".")[0]
                if top and top not in sys.stdlib_module_names and top not in local_modules:
                    failures.append(f"Non-stdlib Python import requires review: {path}: {top}")
    return failures


def check(root, revision=BASELINE, verify_manifest=True):
    root = Path(root)
    failures = []
    entries = tree(root, revision)
    for path in changed_paths(root, revision):
        if path in entries:
            removed_legacy_workflow = path == ".github/workflows/build.yml" and not (root / path).exists()
            if path != ".gitignore" and not removed_legacy_workflow and path not in REVIEWED_MODIFICATIONS:
                failures.append(f"Upstream source differs from baseline: {path}")
        elif not allowed_addition(path):
            failures.append(f"Addition outside audit/build scope: {path}")
        failures.extend(inspect_added_file(root, path))
        for record in git(root, "ls-files", "--stage", "-z", "--", path).split(b"\0"):
            if record and record.split(b" ", 1)[0] not in {b"100644", b"100755"} and path not in entries:
                failures.append(f"Staged addition must be a regular file: {path}")
        # A safe worktree must not hide a secret already staged or committed.
        for source in (f":{path}", f"HEAD:{path}"):
            try:
                data = git(root, "show", source)
            except subprocess.CalledProcessError:
                continue
            failures.extend(inspect_data(root, path, data))
    if verify_manifest:
        manifest_path = root / "docs/fork/provenance.json"
        try:
            recorded = json.loads(manifest_path.read_text())
            if recorded != provenance(root, revision):
                failures.append("Provenance manifest does not match pinned source tree")
        except (OSError, ValueError):
            failures.append("Provenance manifest is missing or invalid")
    return sorted(set(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        failures = check(args.root)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Source guard could not inspect checkout ({type(error).__name__})", file=sys.stderr)
        return 2
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("PASS: pinned-source, reviewed-feature scope and credential checks; no runtime security claim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
