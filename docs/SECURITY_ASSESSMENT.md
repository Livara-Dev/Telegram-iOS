# Preliminary security assessment

Date: 2026-11-04. This is a source-based threat assessment and a list of acceptance requirements, **not a finding that the app is secure**. The reviewed snapshots are Telegram-iOS `279f89e4d4f63f17227bf877dc3d8990f22ecd58` and AyuGram4A `7013145676d36d82ee13c02a89f72097b7490dcd`. The companion architecture and license audits contain source references. No successful full iOS application build, signed-device test, penetration test, cryptographic review or network privacy verification is established by this document.

## Additional source review at `f295948a`

The [expanded telemetry inventory](fork/TELEMETRY-REVIEW.md) identifies additional
active channel-reading metrics and music-listening reports, independent of
AppCenter and the generic app-log queue. It also distinguishes automatic call
and soft-auth-reset diagnostics from local crash storage and user-triggered
exports. No telemetry-removal implementation or runtime non-disclosure test is
claimed by that inventory. Disabling one analytics SDK is insufficient.

The [AyuSync protocol review](fork/AYUSYNC-REVIEW.md) found readable JSON in the
available historical Android transport, no active archive replication or E2E
key lifecycle, and unavailable backend source. The Desktop namespace with the
same name is a forwarding helper. No sync client or archive upload was added.

## Assets and trust boundaries

Protect Telegram authorization/session material; message text, metadata and media; archive/history records; archive encryption and recovery keys; sync credentials; filter rules; signing keys and CI secrets. Retained versions and deleted messages are especially sensitive because another party may reasonably expect them to have disappeared.

1. **Telegram service → existing Telegram networking → update transaction.** Preserve authentication, generated API types, MTProto engines, encryption, update ordering and ordinary canonical deletion/edit behavior. Capture only eligible locally observed content; do not rehydrate deleted messages into live upload/forwarding queues.
2. **Canonical Postbox → separate archive store.** Copy the minimum approved representation through a narrow local interface. The archive is not a Telegram session store. Account identity, peer identity, namespace and message ID must all participate in isolation; account logout and explicit archive retention must have defined behavior.
3. **Archive store → UI, media decoder, extensions, clipboard/share sheet.** Every output can disclose retained content. Normal chat search, notifications, widgets, Spotlight, Siri, previews and share extensions must not expose the archive accidentally. Treat message text, markup, media and filter expressions as untrusted input.
4. **Archive → optional export/backup/sync.** Default is local-only. Each external destination requires explicit user configuration and clear data scope. No developer-operated service, relay, telemetry endpoint or fallback receives archive content or keys. A user-selected storage provider is still an external trust boundary; client-side encryption should prevent it reading payloads, while sizes/timing/object identifiers may remain visible.
5. **Source/dependencies → hosted macOS CI → artifact → Windows sideload tool → iOS device.** A compromised dependency, workflow, CI action or signing tool can replace the client or steal credentials. An artifact hash identifies bytes; it does not establish trustworthy behavior. Signing establishes an identity, not application safety.

## Risk register

“Observed” means supported by inspected source or official published terms. All controls below remain proposed until their stated evidence exists.

| Priority | Risk and current evidence | Required control / evidence before a release claim |
| --- | --- | --- |
| High | **Unwanted diagnostic disclosure. Observed:** upstream iOS has server-enabled network telemetry and app-log uploads (`NetworkTelemetryReporter.swift:169–195`, `ManagedSynchronizeAppLogEventsOperations.swift:73`, `Account.swift:1611,1674`); AppCenter can start conditionally (`AppDelegate.swift:1632–1638`). AyuGram includes Crashlytics and sync code. | Separate privacy-hardening patch; prohibit archive data in logs, analytics, diagnostics and crash metadata. Test telemetry on/off remote configurations, authenticated/pre-auth paths, old queued events and call diagnostics. Exercise archive canaries and verify absence in outbound payloads and logs. Setting an AppCenter ID empty is insufficient. |
| High | **Protocol/state corruption. Design risk:** edits and deletes are applied during ordered replay; suppressing updates or fabricating protocol success can desynchronize the account. | Preserve normal update processing. Take eligible snapshots before deletion/overwrite. Test duplicate, delayed and gap-recovery updates, rollback, account switching and restart. Do not port AyuGram's fabricated RPC results. |
| High | **False atomicity with an independent database. Design risk:** a Postbox transaction and a separate SQLCipher connection do not form one atomic commit. | Specify a crash-safe handoff/outbox and idempotency scheme; account for ordering and deletion purge across both stores. Test crashes before/after each commit and during replay. Do not claim guaranteed capture or exactly-once history from two sequential writes. Avoid blocking the main thread or indefinitely blocking sync on archive failure. |
| High | **Key loss, disclosure or cross-account access. Design risk:** adding an encrypted database creates a new key lifecycle. | Generate independent per-account high-entropy keys using the platform CSPRNG. Store locally in an appropriately restricted Keychain item; never derive them from the API hash, account ID, PIN alone, Telegram auth key or signing certificate. Test wrong/missing keys, keychain lock, logout, account removal, reinstall, access-group changes and key rotation. Define recovery before enabling sync. |
| High | **Residual plaintext. Design risk:** encrypting the main database does not cover copied media, thumbnails, logs, temporary files, WAL/journals, backup snapshots or exports automatically. | Verify actual SQLCipher configuration plus file protection for every sidecar and media path. Set and test backup exclusion and bounded retention. No plaintext intermediate export. Test locked-device behavior, cache cleanup and crash leftovers. Ordinary file deletion cannot promise forensic erasure on flash; document the limits of purge and cryptographic erasure. |
| High | **Sync/export defeats the local privacy model. New external boundary.** | Off by default; explicit scope/destination; client-side authenticated encryption with reviewed platform/library primitives and a documented interoperable format. Keep keys out of provider metadata, URLs and logs. Validate HTTPS/certificate handling, redirects, size bounds, replay/version ordering, duplicate records, account separation and offline recovery. Never fall back to plaintext or a developer endpoint. No working E2EE sync is currently established. |
| High | **Retention after expected expiry. Observed:** API terms §1.4 prohibit preventing self-destructing content from disappearing. | A user toggle does not override Telegram's rules. Exclude Secret Chat/self-destruct/view-once/TTL content from the initial retention scope; preserve canonical expiry. Any proposed permitted exception needs an explicit rule and separate tests across archive, media, search, export and sync. Already exported copies cannot be reliably recalled. |
| High | **Ghost-mode limitations and API contract conflict. Observed:** §1.4 explicitly names read-status tampering/ghost mode and online/typing suppression. | Record this unresolved contract conflict. Do not label such behavior API-compliant, invisible or anonymous. Technical tests would have to cover every read/typing/presence path, queued work, reconnect, policy changes, background execution and other active sessions; they would not resolve the contract issue. |
| High | **Supply-chain and CI secret exposure. Observed:** mixed vendored code/submodules and downloaded toolchains; pinned inputs help integrity but have not been completely audited. | Require exact recursive gitlinks, immutable action commits, verified download hashes and locked dependencies. No unreviewed new dependencies or automatic repin. Hosted workflow uses minimal permissions and no secrets for untrusted PR execution. Keep signing/API configurations out of caches, logs and source/artifact bundles. Review the resolved dependency/license inventory and actual workflow before enabling signing. |
| High | **External sideload tool trust and entitlement changes. New boundary:** Sideloadly may re-sign or modify the IPA and handles Apple-account interaction outside this project. | Obtain the tool from its official distributor and independently verify its provenance; do not embed credentials or route Apple login through this app/CI. Test the re-signed artifact separately on a dedicated device/account. Verify nested signatures, bundle IDs, app groups, keychain groups and extension capabilities. Re-signing may change push/background behavior or access to prior data; do not promise equivalence with the CI artifact. |
| Medium | **Untrusted media or expensive filters cause crashes/DoS. Design risk:** remote content and regex can trigger costly parsing; AyuGram uses regex matching. | Reuse audited native rendering/decoding paths; restrict initial filter language and bound input/work/storage. Test malformed rules, pathological content, all-filtered pages, albums, edits, pagination and notification previews. Do not call arbitrary regex execution bounded without measured/enforced limits. |
| Medium | **Purge and sync resurrection. New multi-device risk:** an old device or backup can upload content deleted elsewhere. | Define tombstones, retention epochs, conflict rules, device revocation and restore semantics. Test offline devices, clocks moving backward, corrupted/stale backups and interrupted purge. State clearly which user-created exported copies lie outside app control. |
| Medium | **Usability/security mismatch. Design risk:** a hidden archive, confusing defaults or a “ghost” label can mislead users. | Native settings show retention, scope, local/external destinations, recovery consequences and feature limitations. Make export/purge explicit. Validate accessibility and actual device/simulator frames; model tests alone do not validate the interface. |

## Independent SQLCipher store: design constraints

Reusing the repository's SQLCipher implementation avoids adding a second database dependency, but does not automatically inherit Postbox's account lifecycle or encryption settings. The existing `SqliteValueBox.swift:494–498` expects a 32-byte key and 16-byte salt; these details are evidence of the existing implementation, not a complete recipe for a new store. Inspect the actual linked SQLCipher version and supported configuration, migration behavior and page authentication rather than copying undocumented raw-key formatting.

Choose a dedicated serial execution context, parameterized queries, a versioned schema and bounded records. An integrity error or unavailable key must not silently produce an empty replacement database or destructive migration. Keep schema migration and key rotation crash-safe; test damaged headers/pages, truncated files, disk-full conditions and abandoned temporary copies. SQLCipher database encryption does not by itself authenticate a multi-device sync protocol or encrypt detached media. Do not invent a cipher, nonce scheme or password KDF.

For a strictly local key, a `ThisDeviceOnly` Keychain accessibility class is a candidate, with the exact class selected against required background access and device-lock behavior. Making archive data unavailable until unlock is preferable to weakening protection merely to keep background capture running, but deferred capture behavior must be defined and tested. Synchronizable Keychain entries or recovery-key wrapping would cross another boundary and need explicit opt-in/design. Keychain persistence after uninstall and changed entitlements after re-signing require tests; neither behavior should be assumed safe.

If cross-device recovery is enabled, separate the recovery/wrapping secret from the database key and define how the new device is authenticated. A passphrase requires a reviewed, appropriately configured password KDF, salt and upgrade strategy. Never store the only decrypting secret alongside ciphertext at the same provider. Specify what happens when all devices and the recovery secret are lost. Do not represent password-protected exports as secure before testing tamper detection, wrong passwords, truncation and version compatibility.

## Export, backup and sync acceptance

Local-only mode must exclude archive files from automatic iCloud/device backup mechanisms as far as the platform permits, with actual restoration tests. Optional backup/sync is a separate opt-in mode; selecting a provider does not authorize upload of unrelated Telegram sessions, credentials or account state. A user-controlled server is not inherently trustworthy. Encrypt on-device before upload and keep decryption on user devices. Explicit plaintext export, if offered at all, must identify that exported files and their recipients are outside app retention/purge control and must not become the default backup path.

Use an archive-specific export schema rather than uploading a live database plus WAL. Snapshot consistently, include format/version and integrity metadata, and import into a staging area with size/type/path limits. Avoid insecure archive extraction paths and unbounded decompression. Test that imported data cannot enqueue Telegram sends, modify authorization state, inject credentials or cross accounts. Export consent must not enable ongoing sync implicitly.

## Hosted macOS build and Windows installation

GitHub-hosted macOS can be a build target only when its installed Xcode/SDK versions satisfy the pinned repository. This source pins Xcode 27.1 and macOS 27; hosted run 41792257376 verified both on the `xcode-27` image. Full compilation and device behavior remain unverified. Windows/Sideloadly can be an installation path, not an Xcode compilation substitute.

Keep unsigned build/testing and optional signing as distinct CI jobs/artifacts with explicit status. Secrets belong in the selected repository/environment, with protected signing access and no untrusted pull-request code allowed to consume them. The build must not download arbitrary “latest” signing tools or binary patches. Record source revision, dependency pins, toolchain versions and IPA hash; provide matching GPL corresponding source without secrets. Clean ephemeral signing keychains and generated configuration and restrict artifact retention/access. Telegram API identifiers embedded in a mobile binary are extractable; hiding them from logs does not make them a client authentication secret.

A Sideloadly re-sign changes the artifact; record its identity/hash separately when possible. Test first launch, login, multi-account state, database access, extensions, backup exclusions and upgrades on that installed form. Apple provisioning expiry or certificate revocation may prevent launch or refresh; never promise permanent installation. Neither hosted workflow configuration nor an unsigned IPA counts as a verified independently signed working application.

## Evidence required to close this assessment

The present evidence is source inspection and the official API/license pages captured by the license audit. Future releases need passing, named test results tied to a revision; a successful full build; device tests of the installed signing variant; reviewed dependency/source/license manifests; and runtime disclosure/retention checks using synthetic accounts and canary content. Report failed, skipped and unavailable checks separately. Until then every feature and control above remains unverified.


## Experimental application integration: verification pending

The new TelegramCore outbox, account worker and native archive screens are
described in [ARCHIVE-INTEGRATION.md](fork/ARCHIVE-INTEGRATION.md). They are
default off. Schema 4 introduces a durable ordered replay receipt that survives
retention. Commit `1b7dfc3b` passed 54 macOS tests (43 storage, 11 mocked Keychain)
and iOS module compilation; see [exact evidence](fork/evidence/archive-tests-1b7dfc3b.json).
The application hooks and real Postbox integration tests require their own
revision-specific results. Standalone library results do not validate them.

The transient outbox is protected by Postbox's existing encryption/key policy,
not the archive's independent Keychain key or complete file protection. The
worker does not change Postbox commit-error handling. Release-mode disk-full
durability, abrupt termination, actual device lock behavior, notification
extensions and every deletion path remain unverified. An in-flight write can
finish after a settings change; purge uses a separate generation barrier.

The capture review found that user-initiated deletion and acknowledged edits
can consume prior content before server replay. Narrow hooks now cover those
paths; tests check duplicate replay and preservation of existing canonical
operations. They still need native CI verification. History revalidation,
range/history clearing and structured rich-content preservation remain gaps.
Unsupported rich content is reported through the missed-capture counter, not an
empty successful snapshot. Sender exclusions fail closed if author identity
is unavailable. No broad hook was added to expiry or cache-maintenance deletion.
Existing incompatible or malformed archive preferences are also read-only: the
worker stops and public mutations preserve their bytes instead of replacing
future exclusions or purge generations with defaults. This guard has four new
real-Postbox regressions awaiting native execution.

The earlier `1b7dfc3b` revision passed all four iOS simulator integration targets
in [run 41811248732](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41811248732).
This is evidence for that revision's local pipeline tests, not live Telegram
server behavior, later changes, device protection or full application operation.

The archive directory sits outside the media cache but inside the account's
root. Upstream account cleanup removes that directory on account removal;
actual cache-clear and logout/upgrade device tests remain outstanding. Orphaned
Keychain entries may remain: the adapter has no key-deletion API, and this is
not a cryptographic-erasure implementation. The native views intentionally have
no send/forward/export action. No archive network endpoint is added. Existing
upstream telemetry is still unresolved.

## Ghost activity draft: verification pending

The separate Ghost activity draft adds only application-level decisions before
existing `messages.setTyping` / `setEncryptedTyping` dispatch. Its disabled
default, account-specific persisted policy and read-only incompatible-settings
behavior passed all 15 real-Postbox worker/dispatch tests at `cd1effef`; see
[revision-specific evidence](fork/evidence/ghost-activity-cd1effef.json). This is
not live server or UI verification. The existing
Network and encryption implementations are unchanged. Calls, emoji interactions
and explicit live-draft stop remain outside suppression. Requests already sent,
other clients and server-derived activity remain observable. Read/story receipts
and presence are not suppressed. See [the precise boundary](fork/GHOST-ACTIVITY.md).

## Earlier standalone archive evidence (before integration)

The feature branch introduces `LocalMessageArchive` without altering Telegram's
application core. It uses only vendored SQLCipher, Foundation and Security, has no
networking or logging capability, and does not receive live messages yet. Its
Keychain adapter rejects missing existing-archive keys, inaccessible items,
malformed keys and weaker-than-requested protection. Security calls are mocked in
unit tests; real Keychain/entitlement and locked-device behavior need device tests.

Postbox sets a process-global SQLCipher plaintext-header default of 32 bytes.
The independent archive must explicitly select a fully encrypted header after
key initialization, and its tests must exercise coexistence with that default.
No process-global SQLCipher setting may be changed by the archive module.

This draft does not close the risks above. A Postbox outbox, purge/replay barrier,
protected archive-owned media, physical disk quota, corruption/disk-full recovery,
real device protection tests and measured performance remain necessary before
archive integration can be described as reliable. An already-open SQLCipher
connection can retain keys/pages in memory after device lock; Keychain access
control and file protection do not themselves close that connection. App lifecycle
integration must gate archive access and close/reopen safely on protected-data
transitions. Retaining a supplied snapshot in
a native unit test is not evidence of preserving a live deletion event.


Commit `86fcf189` passed 20 native macOS database tests and 11 mocked Keychain
tests in hosted run 41794635275; the iOS simulator module also compiled. Database
checks include encryption/header configuration, wrong-key rejection, account
isolation, transaction rollback, retention and unsafe paths. This is useful
storage-boundary evidence, not a cryptographic audit or proof of live capture,
iOS file protection, actual Keychain policy enforcement or no-telemetry behavior.


Schema-2 commit `9bfc4c87` subsequently passed 39 native macOS tests and iOS
simulator compilation. Additional checks cover atomic v1 migration and rollback,
invalid legacy sizes/account identities, immutable-event triggers, consistent
counters across two live connections, and bounded retention boundaries. The
connection test uses sequential operations; simultaneous writer/migration races
and real I/O failures are not established by these results. Evidence is in
[fork/evidence/archive-tests-9bfc4c87.json](fork/evidence/archive-tests-9bfc4c87.json).


Schema-3 commit `c326364b` passed 46 native macOS tests and iOS simulator
compilation. Its storage-side generation barrier rejects stale/future batches,
advances purge generations atomically, and makes same-generation retries preserve
newer records. Tests cover migration, reopening, sequential live handles, a FIFO
writer/purge boundary and an injected SQL failure after deletion. These are not
process-termination, disk-full or live Postbox handoff tests; the application-level
purge/capture protocol remains unimplemented. The 11 Keychain tests still use
injected Security responses. See [exact evidence](fork/evidence/archive-tests-c326364b.json).


## Local literal filter draft

The filter increment adds only local preferences and presentation decisions.
There is no remote rule import/export, arbitrary regex engine, moderation RPC or
new endpoint. Rules, identifiers, exclusions, serialized preferences and evaluated
text have explicit bounds; malformed/newer preferences remain read-only. Matching
runs off-main with revision checks; all original Postbox messages remain intact.

The first UI scope is deliberately ordinary incoming cloud text. Dedicated
placeholder content and accessibility/interaction guards are source-implemented;
selection mode reveals originals to avoid invisible toolbar actions. Native
compilation, VoiceOver, hide/reveal, scroll anchoring and other real UI behavior
remain unverified. Notification/search/dialog previews, translation, read status
and other processing remain outside the display filter. It is not access control.
See [the implementation boundary and pending tests](fork/MESSAGE-FILTERS.md).

Archive revision `387177fd` has since passed all 978 iOS XCTest cases, including
56 local Postbox capture/replay/coordinator tests and the MediaBox cache-clear
regression. [Retained evidence](fork/evidence/archive-integration-387177fd.json)
records the exact revision and artifact digest. These local integration results
do not close the physical-device, disk-full/process-termination, UI, telemetry,
network-canary or distribution risks in this assessment.


At filter revision `6eba37ff`, 18 filter-core tests and the inherited Ghost/archive
regressions passed. The projection test host failed packaging before any of its
12 tests executed because its Info.plist lacked a bundle version. The explicit
version-field fix is a test-host change and still needs a new native run. It does
not establish that the UI or projection has passed. The next run executes the
new host first so setup failures are visible sooner; all other regressions remain
required before the application build.

At filter revision `f295948a`, the projection host and Swift test module compiled,
but hosted simulator startup exhausted Bazel's 300-second timeout before any
XCTest case began. The synthetic one-case error in Bazel XML is a harness result,
not an executed projection test. [Verified evidence](fork/evidence/filter-integration-f295948a.json)
records zero actual cases; other suites were not run by the former fail-fast
wrapper. The bounded runner allowance and tests-only evidence continuation do
not turn failures into success or bypass the full-application gate.

## Story-view receipt draft

The [story-only increment](fork/GHOST-STORY-VIEWS.md) adds a separate default-off
per-account preference and a durable generation to ordinary queued story
receipts. Its producer gates both ordinary and pinned views; suppressed views
do not advance the local story read marker. The queue consumer rejects obsolete
generations and rechecks persisted policy at dispatch. Its tests are authored;
no native result, live server visibility check or complete application behavior
is established yet. In-flight requests, other clients, later cumulative reads,
reactions and replies remain observable. Message-read suppression is a separate
unimplemented feature; [the read-state audit](fork/GHOST-RECEIPTS-AUDIT.md) explains
why a generic RPC no-op would break persistent synchronization assumptions.


## Bounded local media-copy draft

Schema 5 source adds event-linked encrypted media BLOBs and quota/deletion
accounting. The account worker reads only completed local cloud-resource paths
with bounded size and no-follow file opens, before acknowledging its outbox
item. It performs no download and writes no plaintext media temporary file.
This code and its new native tests await execution; cache removal before the
file is opened, incomplete downloads, physical termination/disk pressure and
real Keychain protection remain unverified. Do not claim lossless retention.

The global-delete handoff defers eager cache unlink only after successful
creation of an eligible durable outbox entry. This does not create a plaintext
archive or change timed/protected-content handling. It leaves the existing cache
file under Telegram's normal cleanup policy until the asynchronous reader runs.
A simultaneous cache purge can still win; proactive capture remains pending.
New real-replay regressions are awaiting native execution.
