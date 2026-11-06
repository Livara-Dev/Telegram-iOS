# Extended local retention — 6 November 2026

This source change adds two independent, default-off per-account options under
Settings → Custome options → Local Message Archive: **Include Secret Chats** and
**Include Timed / View-Once Content**. The archive itself must also be enabled.
Timed content in a secret chat requires both options. Existing account policies
retain the old exclusions unless explicitly changed. Disabling capture does not
delete already retained copies; use the archive purge control to remove them.

## Implemented source paths

The existing encrypted archive now accepts locally received secret-chat message
identities, including the valid zero ID in incoming/local secret namespaces.
Version-3 adapter metadata distinguishes secret/expiring/view-once snapshots;
older pending-capture adapters reject that metadata instead of misclassifying
it as ordinary cloud content. Existing committed library records remain opaque.

Capture is placed before common deletion, secret clear-history/range removal,
local media expiry, remote consumption expiry and server-fetched expiry
replacement. Nested extended-deletion observers deduplicate identical pending
snapshots. Canonical Telegram deletion, expiry, read receipts, networking,
authentication and session encryption remain unchanged. No Telegram session key,
media encryption key, IV or access hash is written into archive metadata.

A captured message and successfully preserved media are separate facts. Only
completed locally available MediaBox bytes are streamed into archive-owned
SQLCipher storage. Secret file identifiers and local file identifiers are
strictly validated. Archived bytes use the existing account key and existing
streaming media/backup code. Global archive browsing can display retained
records; this does not add secret-chat rows to canonical Telegram history.

## Limits that remain

This is not recovery of unseen, already erased, incomplete or never-downloaded
content. Secret chats from another app installation/session do not transfer.
The pending handoff remains bounded to 256 records / 8 MiB and can miss content
under pressure. Files are capped by the existing 512 MiB per-file limit and
archive quota (default 32 MiB). Manual cache removal, force termination, locked
storage, resource replacement and storage errors can interrupt capture.
Copy-protected content, service messages and unsupported rich content keep their
existing exclusions. No arbitrary plugin loading or AyuSync was added.

The preservation feature conflicts with Telegram API Terms §1.4's disappearance
requirement. It must not be described as approved by Telegram or API-compliant.
The options require explicit confirmation. Their presence is not a security
certification or a claim of invisible operation.

The archive settings list now sorts optional error, missed-capture and chat rows
before ItemList's strict ordering precondition. Three source-contract tests cover
all eight optional-row combinations; these do not establish rendered UI behavior.
The native catalog assertion follows the new default-off capability contract
without changing server-entitlement assertions.

## Evidence at authoring time

The exact production capture-policy model and extracted production resource-key
validator executed 2,586 host assertions on Linux. Swift parser checks passed;
these are not iOS type-checking, application compilation or runtime evidence.

Twenty-seven new native XCTest methods cover opt-in/default behavior, timed and
view-once photo/video/voice/round-video classification, secret namespaces/zero
IDs, metadata redaction, real local expiry and remote/server replacement,
clear-history/range handling, encrypted storage/cache cleanup/reopen and backup.
They are authored, not claimed executed by this document. Existing default-off
exclusion tests remain unchanged. Four-byte voice/video classification fixtures
are not proof of successful decoding; real decoder coverage is a separate suite.

The old build 82 and its 1,473 native passes do not qualify these changes.
A new exact-source IPA and same-source native test results must be collected
separately. Physical-device installation, authenticated two-client behavior,
visual recordings, low-storage and force-termination acceptance remain required.
No fully working, safe, lossless or complete parity claim is made.

## Reproduction

Run `python3 tools/fork_baseline/extended_retention_checks.py` for host-only
checks. On the pinned Apple Silicon toolchain, use the existing
`baseline.py unsigned-device` command and the fixed iOS integration suite.
The extended-retention workflow publishes a checksum-bound review only to
`integration/ayu-current` with a non-forced, concurrency-checked push. Device
compilation and native tests use that exact resulting source commit; neither
relabels or patches the old executable. Only public dependency downloads are
cached; no credentials, session data or private build outputs are cached.
