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
    "Tests/CustomeOptionsHost/", "Telegram/Telegram-iOS/AyuGram.icon/",
    "submodules/TelegramCore/Sources/LocalAppearance/", "submodules/TelegramCore/Tests/LocalClientCosmetics",
    "docs/fork/", "tools/fork_audit/", "tools/fork_baseline/", "submodules/LocalMessageArchive/",
    "submodules/TelegramCore/Sources/LocalArchive/", "submodules/TelegramCore/Tests/LocalArchive",
    "submodules/TelegramCore/Sources/LocalPrivacy/", "submodules/TelegramCore/Tests/LocalGhost", "submodules/TelegramCore/Tests/LocalTelemetry",
    "submodules/TelegramCore/Sources/LocalMessageFilter/", "submodules/TelegramCore/Tests/LocalMessageFilter",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Tests/",
    "submodules/TelegramUI/Components/Chat/ChatMessageLocalFilterBubbleContentNode/",
)
ADDED_FILES = {
    'submodules/TelegramCore/Sources/Utils/SafePeerId.swift',
    'submodules/SettingsUI/Tests/LocalArchivePeerReferenceTests.swift',
    'docs/AYUGRAM_INLINE_HISTORY.md',
    'submodules/SettingsUI/Tests/LocalArchiveInlineHistoryStatusTests.swift',
    'submodules/SettingsUI/Sources/LocalFolderAppearanceController.swift',
    'submodules/TelegramCore/Tests/LocalFolderAppearanceTests.swift',
    'submodules/SettingsUI/Sources/LocalArchiveMediaPreview.swift',
    'submodules/SettingsUI/Tests/LocalArchiveMediaPreviewTests.swift',

    'submodules/SettingsUI/Tests/LocalArchiveTransferLifecycleTests.swift',
    'submodules/SettingsUI/Sources/LocalArchiveTransferController.swift',
    'submodules/SettingsUI/Sources/LocalClientCosmeticsController.swift',
    'submodules/SettingsUI/Sources/LocalClientNavigation.swift',
    'submodules/SettingsUI/Sources/LocalArchivePresentation.swift',
    'submodules/SettingsUI/Sources/LocalArchivePresentationController.swift',
    'submodules/SettingsUI/Tests/LocalClientNavigationTests.swift',
    'submodules/SettingsUI/Tests/LocalArchivePresentationTests.swift',
    'submodules/TelegramCore/Tests/LocalStoryOpeningWarningTests.swift',

    "submodules/SettingsUI/Sources/CustomeOptionsAccountController.swift",
    "submodules/SettingsUI/Sources/CustomeOptionsCatalog.swift",
    "submodules/SettingsUI/Sources/CustomeOptionsController.swift",
    "submodules/SettingsUI/Sources/LocalPrivacyAdsSettingsController.swift",
    "submodules/SettingsUI/Sources/LocalTimedMediaInformationController.swift",
    "submodules/SettingsUI/Tests/CustomeOptionsCatalogTests.swift",
    "submodules/TelegramCore/Tests/LocalPrivacyAdsTests.swift",
    "Tests/NativeSecurityHost/BUILD",
    "Tests/NativeSecurityHost/NativeSecurityTestHost.swift",
    "docs/SIDESTORE_INSTALL.md",
    "submodules/PasscodeCore/Sources/ApplicationStorage.swift",
    "submodules/PasscodeCore/Tests/PasscodeCoreTests/ApplicationStorageTests.swift",
    "submodules/PasscodeCore/Tests/PasscodeCoreTests/PrivateInstallationKeychainTests.swift",
    "submodules/TelegramUI/Sources/LocalLaunchDiagnostics.swift",
    ".github/workflows/archive-integration.yml",
    ".github/workflows/archive-compatibility.yml",
    ".github/workflows/archive-tests.yml",
    ".github/workflows/fork-baseline.yml", "COPYING",
    "docs/AYUGRAM_FEATURE_MATRIX.md", "docs/WINDOWS_INSTALL.md", "docs/SECURITY_ASSESSMENT.md",
    "submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift",
    "submodules/SettingsUI/Tests/LocalArchiveGalleryTests.swift",
    "submodules/SettingsUI/Sources/LocalGhostSettingsController.swift",
    "submodules/SettingsUI/Sources/LocalMessageFilterSettingsController.swift",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatLocalTextFilterProjection.swift",
}
# Explicit integration seams only. Everything else in the upstream tree, including
# MTProto, authentication methods, cryptography, Postbox, dependency manifests and
# gitlinks, stays pinned. An allowlist is a scope tripwire, not a semantic audit of
# every change within these files; reviewers must inspect their Git diffs.
REVIEWED_MODIFICATIONS = {
    'submodules/TelegramUI/Components/Chat/ChatMessageDateAndStatusNode/Sources/ChatMessageDateAndStatusNode.swift': 'verified saved-edit presentation and a date-only archive action preserving reaction hit regions and imported tooltips',
    'submodules/TelegramUI/Components/Chat/ChatMessageTextBubbleContentNode/Sources/ChatMessageTextBubbleContentNode.swift': 'foreground visible-cell saved-edit query with account/version identity and cancellation; no archived content enters Telegram messages',
    'submodules/ChatListUI/Sources/ChatListFilterPresetListController.swift': 'account-local folder title/color projection; canonical filters and Premium limits remain unchanged',
    'submodules/TelegramUI/Components/ChatList/ChatListFilterTabContainerNode/BUILD': 'direct dependency on the existing pinned SwiftSignalKit for account preference observation',
    'submodules/TelegramUI/Components/ChatList/ChatListFilterTabContainerNode/Sources/ChatListFilterTabContainerNode.swift': 'observe immutable account context and project local folder tags/colors without rewriting canonical titles or order',
    'submodules/SettingsUI/Sources/Themes/ThemeSettingsController.swift': 'explicit per-account bundled-icon UI permission, real Premium unchanged, validated OS icon completion',
    'submodules/TelegramUI/Components/Chat/ChatSearchNavigationContentNode/Sources/ChatSearchNavigationContentNode.swift': 'accessible local-archive search action with reserved native search-field geometry',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoProfileItems.swift': 'chat-scoped separate local saved-media and deleted-history destinations',
    'submodules/TelegramUI/Sources/ChatControllerNode.swift': 'hand the current native search query to local encrypted archive search',
    'submodules/TelegramUI/Components/Stories/StoryContainerScreen/Sources/StoryContainerScreen.swift': 'central account-policy confirmation before viewer subscriptions or story-view receipts',
    'submodules/TelegramCore/Sources/State/PendingMessageManager.swift': 'observe actual mapped send acknowledgement for a separately bounded explicit read task',
    'submodules/TelegramCore/Sources/PendingMessages/EnqueueMessage.swift': 'capture bounded account/peer/max-cloud-id intent before eligible local enqueue',

    'submodules/RMIntro/Sources/platform/ios/RMIntroViewController.m': 'fork-specific displayed title and Telegram API attribution without changing authorization callbacks',
    'Telegram/Telegram-iOS/ar.lproj/InfoPlist.strings': 'preserve AyuGram display identity under Arabic localization',
    'Telegram/Telegram-iOS/ko.lproj/InfoPlist.strings': 'preserve AyuGram display identity under Korean localization',
    'submodules/SettingsUI/Sources/Search/SettingsSearchableItems.swift': 'index the actual Custome options categories in native Settings search',
    'submodules/TelegramCore/Sources/State/AccountViewTracker.swift': 'ordinary view-count privacy and original-request-scoped snapshots before unsupported-media refresh replaces a body',
    'submodules/TelegramCore/Sources/State/ManagedAccountPresence.swift': 'observe account policy before online refresh and bounded foreground offline attempts',
    'submodules/TelegramCore/Sources/State/ManagedConsumePersonalMessagesActions.swift': 'preserve local content consumption and mandatory expiry while gating ordinary outgoing receipts',
    'submodules/TelegramCore/Sources/State/ManagedSynchronizeConsumeMessageContentsOperations.swift': 'final ordinary-content receipt policy check without fabricated network results',
    'submodules/TelegramCore/Sources/State/ManagedSynchronizeMarkAllUnseenPersonalMessagesOperations.swift': 'do not enqueue all-unseen receipts under ordinary read privacy',
    'submodules/TelegramCore/Sources/State/SynchronizePeerReadState.swift': 'account-local outgoing read synchronization decision with canonical local transitions retained',
    'submodules/TelegramCore/Sources/TelegramEngine/Messages/ApplyMaxReadIndexInteractively.swift': 'local read updates preserved independently of ordinary cloud receipt dispatch',
    'submodules/TelegramCore/Sources/TelegramEngine/Messages/MarkAllChatsAsRead.swift': 'account-scoped receipt policy at read-all producer',
    'submodules/TelegramCore/Sources/TelegramEngine/Messages/ReplyThreadHistory.swift': 'account-local receipt policy and snapshots before ordinary fetched reply-thread body replacements',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoScreen.swift': 'dedicated top-level customization action identifier',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoScreenSettingsActions.swift': 'route top-level entry to the real account-bound dashboard',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoSettingsItems.swift': 'prominent separate Custome options Settings section',

    "Telegram/Telegram-iOS/InfoBazel.plist": "stable fork bundle identity for signing-aware container selection",
    "submodules/PasscodeCore/BUILD": "wire native storage and Keychain isolation regression tests",
    "submodules/sqlcipher/BUILD": "enable the existing progress callback API for bounded archive cancellation; all crypto/version flags preserved",
    "submodules/sqlcipher/Package.swift": "keep the same progress callback API in SwiftPM; no dependency or crypto configuration change",
    "submodules/PasscodeCore/Sources/Passcode.swift": "explicit private installation Keychain namespace without weakening existing shared mode",
    "submodules/WalletContext/Sources/WalletSecurity.swift": "isolate private-installation wallet Keychain services from legacy shared items",
    "submodules/WalletContext/Sources/WalletStorage.swift": "retain upstream wallet namespace except for isolated private installation",
    "submodules/TelegramCore/Sources/State/Fetch.swift": "pass an optional, disabled statistics collector through existing fetch routing",
    "submodules/TelegramCore/Sources/State/FetchSecretFileResource.swift": "preserve secret-file fetch behavior with optional statistics collection",
    "submodules/Postbox/Sources/Postbox.swift": "optional selected-row removal and exact existing-body replacement observers; unrelated callers retain nil behavior",
    "submodules/Postbox/Sources/MessageHistoryTable.swift": "observe selected removals and real merged replacements before old bodies/media references change",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/DeleteMessages.swift": "explicit authorized cloud-history and moderation removals feed bounded durable capture before body deletion",
    "submodules/TelegramCore/Sources/State/HistoryViewStateValidation.swift": "capture before existing validation replacements/removals without changing the upstream decisions",
    "submodules/TelegramCore/Sources/State/ForumChannelState.swift": "route ordinary forum message upserts through the opt-in existing-body observer",
    "submodules/TelegramCore/Sources/State/Holes.swift": "observe ordinary cloud history/dialog/reference fetch replacements while leaving local view refresh unchanged",
    "submodules/TelegramCore/Sources/State/ResetState.swift": "preserve previous observed bodies when a dialog reset upserts server messages",
    "submodules/TelegramCore/Sources/State/ManagedSynchronizePinnedChatsOperations.swift": "observe pinned-dialog server-message replacements without changing synchronization",
    "submodules/TelegramCore/Sources/TelegramEngine/Feed/FeedPageStorage.swift": "capture before the existing feed server-body merge result is stored; do not enable the feed",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/LoadMessagesIfNecessary.swift": "observe replacements in ordinary server message-load storage",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/SparseMessageList.swift": "observe server calendar-page body replacements without changing page results",
    "submodules/TelegramCore/Sources/TelegramEngine/Peers/ChatListFiltering.swift": "observe replaced dialog bodies and atomically prune local appearance for removed folder IDs; server payloads remain canonical",
    "submodules/TelegramCore/Sources/TelegramEngine/Peers/UpdateCachedPeerData.swift": "capture cached full-channel minimum-ID range removal and defer only queued media unlink",
    "submodules/TelegramCore/Sources/TelegramEngine/Messages/SearchMessages.swift": "preserve previous body during media-reference refetch only for the original ordinary cloud request identity",
    'submodules/TelegramCore/Sources/TelegramEngine/Calls/RateCall.swift': 'remove automatic diagnostic RPC and file upload sinks while preserving explicit rating',
    'submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift': 'disable optional viewport and listening analytics facades',
    'submodules/TelegramUI/Sources/MediaManager.swift': 'stop collecting optional music-listening analytics',
    'submodules/TelegramUI/Sources/AppDelegate.swift': 'remove AppCenter startup; fail-visible signing-aware storage initialization and bounded local diagnostics',
    'submodules/TelegramUI/BUILD': 'remove optional AppCenter dependencies',
    'submodules/TelegramCallsUI/Sources/CallRatingController.swift': 'explicit rating without diagnostic attachments or support-account messages',
    'submodules/TelegramCallsUI/Sources/CallFeedbackController.swift': 'remove diagnostic attachment controls',

    'submodules/TelegramCore/Sources/State/SynchronizeAppLogEventsOperation.swift': 'disable optional app-event producers',
    'submodules/TelegramCore/Sources/State/ManagedSynchronizeAppLogEventsOperations.swift': 'retire legacy app-event records locally without Network access',
    'submodules/TelegramCore/Sources/State/AccountTaskManager.swift': 'start local legacy telemetry cleanup',
    'submodules/TelegramCore/Sources/Network/NetworkTelemetry.swift': 'permanently disable production telemetry recording',
    'submodules/TelegramCore/Tests/NetworkTelemetryTests.swift': 'verify remote configuration cannot enable production recording',

    "submodules/SettingsUI/BUILD": "link native archive media reader, gallery and actual date/status-node tests; use the installed iOS 27.0 test runtime",
    "build-system/Make/tests/test_postbox_import_check.py": "measure tokenizer CPU budget independently of hosted runner descheduling",
    "submodules/Postbox/BUILD": "pin simulator test runner to installed iOS 27.0",
    "submodules/TextFormat/BUILD": "pin simulator test runner to installed iOS 27.0",
    "submodules/TelegramUI/Components/Chat/ChatMessageBubbleItemNode/BUILD": "link the local text-filter placeholder component",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/BUILD": "isolated local text-filter projection test target and host",
    "submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatHistoryEntry.swift": "presentation-only filter state preserving row identity",
    "submodules/TelegramUI/Components/ChatControllerInteraction/Sources/ChatControllerInteraction.swift": "controller-scoped temporary reveal state and separate read-only archive timeline action",
    "submodules/TelegramUI/Sources/ChatHistoryListNode.swift": "off-main literal projection and removal of optional viewport analytics",
    "submodules/TelegramUI/Sources/ChatInterfaceStateContextMenus.swift": "create local literal rule from eligible message text",
    "submodules/TelegramUI/Components/Chat/ChatMessageBubbleItemNode/Sources/ChatMessageBubbleItemNode.swift": "render local text placeholder and guard hidden-message actions",
    "submodules/TelegramUI/Components/Chat/ChatMessageItemView/Sources/ChatMessageItemView.swift": "redact hidden message accessibility data",
    "Telegram/BUILD": "fork display/icon/base-bundle metadata and isolated test-host string resources",
    "submodules/TelegramCore/BUILD": "link the local archive and its integration tests",
    "submodules/TelegramCore/Tests/EngineMessageReplyQuoteTests.swift": "repair stale rich-quote fixture to use the production rich initializer",
    "submodules/TelegramCore/Sources/Account/Account.swift": "account-owned archive lifecycle",
    "submodules/TelegramCore/Sources/Account/AccountManager.swift": "register archive outbox decoder",
    "submodules/TelegramCore/Sources/State/AccountStateManagementUtils.swift": "capture before server delete/edit and real existing-body replacement during reset/difference upserts",
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
                reviewed_local_import = (path == "build-system/Make/tests/test_postbox_import_check.py" and top == "PostboxImportCheck")
                if top and top not in sys.stdlib_module_names and top not in local_modules and not reviewed_local_import:
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
