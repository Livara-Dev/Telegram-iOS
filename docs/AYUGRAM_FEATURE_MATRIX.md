# AyuGram for iOS feature matrix

Updated 2026-11-04. Active priority: [deleted-message/media preservation](fork/PRESERVATION-STATUS.md). Secondary feature development is paused. **No requested feature is verified in the running application yet.**
The current archive branch contains default-off TelegramCore/Postbox capture,
a durable per-account outbox, independent encrypted media storage, indexed text
search, streaming attachment reads and native archive screens. The gallery
increment adds an encrypted attachment index, paginated account/chat-scoped
browsing, type filters and message-history navigation. **The gallery, schema-7
streaming and AVFoundation playback changes still await native CI.**

The integration candidate additionally includes channel minimum-available range
capture and the reviewed telemetry-containment branch. Both require current
native qualification. Ordinary feature pushes no longer launch hosted builds;
[one verification queue](fork/CI-QUEUE.md) tests each submitted source batch.

Verified revisions must be kept separate from this source:

- `92f6d752`: 1,060 iOS XCTest cases passed, including 74 archive integration
  cases. These exercised actual local message-processing paths with a generated
  JPEG and synthetic bytes for other media types, cache removal and reopening.
- `115f83bf`: 533 TelegramCore and 270 TextFormat iOS cases passed, including
  streaming a 20 MiB document through real Postbox/cache cleanup. The archive
  test module failed Swift type inference (now corrected); the overall run failed.
  [Evidence](fork/evidence/archive-streaming-115f83bf.json).
- `9190be8c`: 64 archive, 532 TelegramCore and 270 TextFormat iOS cases passed.
  The Postbox runner timed out and the UI-hosted projection suite executed zero
  cases; the overall integration job failed. See
  [the exact result](fork/evidence/archive-integration-9190be8c.json).
- `e690da94`: 64 macOS archive tests and iOS module compilation passed. This
  predates streaming and the gallery; [evidence](fork/evidence/archive-tests-e690da94.json).
- The separate Ghost activity draft passed 15 worker/dispatch cases at
  `cd1effef`. Story tests and telemetry-removal tests on newer feature branches
  still need their own successful runs.

These are local integration tests, not verified live Telegram delivery,
physical-device protection or running-app UI. No full application build,
installable signed IPA, device installation or iPhone performance result is
claimed. Hosted workflows read the owner's API credentials from Secrets.
[Integration scope](fork/ARCHIVE-INTEGRATION.md) and
[preservation limits](fork/PRESERVATION-STATUS.md) remain release gates.

Evidence dates matter: the public Android `rewrite` source is July 2023,
official docs are January 2025, and the October 5, 2026 improvements are first-party
release-note claims without corresponding implementation in that public source.
Desktop source is pinned to October 20, 2026. See the
[expanded source and release audit](fork/EXPANDED-REFERENCE.md),
[historical Android code hooks](fork/AYUGRAM-REFERENCE.md),
[Desktop implementation evidence](fork/DESKTOP-REFERENCE.md),
[iOS integration seams](fork/IOS-ARCHITECTURE.md), and
[phase acceptance tests](fork/IMPLEMENTATION-PLAN.md).

## Requested functionality and observed reference behavior

`Not implemented` means no Swift feature or integration exists in this checkpoint.
`Policy conflict` additionally identifies a Telegram API-terms issue, not a
technical claim of impossibility. All entries require actual integration tests.

| Feature | Android/reference implementation and evidence | Proposed iOS equivalent and limitations | iOS status |
| --- | --- | --- | --- |
| Suppress message read receipts | `shared/ghost.md:9`; historical Android `ConnectionsManager.java:327` covers history, encrypted history, discussion and content read methods. Docs name speech-to-text as an exception. | Inventory every read operation, including media content, threads, notifications/extensions and retries. Preserve local state independently and prove no prohibited RPC is emitted under each policy. Explain actions that still reveal reading. | Not implemented; API-policy conflict |
| Suppress story views | `shared/ghost.md:15`; current Desktop skips local marking and gates ordinary/pinned dispatch. Not in the 2023 Android source audit. | Independent per-account native policy; ordinary/pinned producer gates plus durable queue generations. Suppressed views remain locally unread; later allowed cumulative reads can reveal earlier stories. | [Source and 27 tests authored](fork/GHOST-STORY-VIEWS.md); native compile fix awaiting retest; app verification pending; API-policy conflict |
| Warn before opening stories | `shared/ghost.md:61`; cancellation must leave story unopened. | Gate story viewer startup before receipt work is queued; explicit cancellation test. | Not implemented |
| Suppress online status | `shared/ghost.md:21`; sending can still expose online presence. Historical request hook rewrites updateStatus offline. | Gate application-level presence operations, preserving transport/auth; document server inference and other-session limits. | Not implemented; API-policy conflict |
| Suppress typing, sticker choice, upload, recording indicators | `shared/ghost.md:27` lists typing/sticker/game; old Android drops both ordinary/encrypted typing constructors. | Per-account typed activity suppression before existing cloud/secret typing dispatch; preserve calls, emoji interactions and explicit draft stop. Already sent requests cannot be recalled. | 15 worker/dispatch tests passed at `cd1effef`; native controls authored; full application verification pending; API-policy conflict |
| Automatic offline | `shared/ghost.md:36` warns of online/offline oscillation with another active client. | Per-account policy and bounded scheduling; test no loop/flood, account switch, foreground transition and other-session effects. Cannot retroactively erase a visible online interval. | Not implemented; API-policy conflict |
| Read on interaction | `shared/ghost.md:44`; historical Android has mark-read-after-send plus explicit mark-read helpers. Docs do not define every interaction. | Specify exact actions (e.g. successful send/manual mark read); scoped one-shot permission, no global race-prone allow flag. Test failures and unrelated chats/accounts. | Not implemented |
| Scheduled sending for ghost use | `shared/ghost.md:48` describes 12-second text delay, dynamic media delay and network fragility. | Use existing Telegram scheduling capability, explicitly showing the delay. Respect unsupported destinations/media, cancellation and failures. No guarantee against server presence inference. | Not implemented; API-policy conflict |
| Independent deleted-message archive | `android/saving.md:10` describes separate Room storage; old source exposes snapshot/deletion hooks. | Store locally seen snapshots in a separately managed encrypted native store; canonical deletion still proceeds. Cannot recover unseen or already absent data. | 59 schema-5 storage/Keychain tests pass on macOS and iOS simulator at `aba9fae9`; new media integration and application verification pending |
| Edited-message timeline | Old Android records prior content before overwrite; `AyuMessageHistory` displays revisions. | Preserve original/current distinction, order, deduplication, same-timestamp edits, account namespace and rollback atomicity. | Server-replay and own-edit acknowledgement capture plus native timeline authored; application verification pending |
| Saved media | `android/saving.md:17`, `:56` describes auto-download or copying already downloaded files; October post 61 adds profile display. | First version should only retain already available files unless explicit later download policy is approved. Protect filenames/content; test reference counting, eviction, partial downloads and size limits. No promised retrieval after server deletion. | Bounded encrypted cached-byte storage and native image preview authored; storage tests passed, media capture/UI integration unverified |
| Per-chat exceptions and bot controls | October post 61 claims exceptions; `android/saving.md:60` describes disabling bot capture; old config has bot gate. | Define precedence: disabled feature, excluded chat, bot policy, then media/retention policy. Account-specific persistent rules. | Persistent account policy and bot toggle authored; scope-picker UI outstanding |
| Size/age limits and cleanup | Requested addition; January 2026 post 49 documents historical excessive storage/cleanup fixes. | Bounded archive, media quota and deterministic retention; test crash/restart, shared attachment deletion, disk-full and logout purge. | Transactional age and text/media-payload quota tested in storage module; presets through 1 GiB authored; quota measures logical payload, not physical disk allocation |
| Searchable deleted-only view | `android/saving.md:23`. | Dedicated archive query API; test paging, localization, escaped search input, account isolation and deleted-only results. | Native paginated view and encrypted indexed word/phrase search authored; schema-6 search tests and UI verification pending |
| Deleted results in normal chat search | October post 61. | Distinguish local archived results from Telegram results and prohibit upload of archive content/query index. Test deduplication and navigation to absent canonical message. | Not implemented |
| Deleted media in chat profile | October post 61. | Clearly marked local-only media collection; no false reference to server availability. | Account/chat-scoped native archive gallery authored; not embedded into the standard chat profile media section; native/UI validation pending |
| Inline deleted/edited markers and timeline integration | Historical Android customizable marks; docs acknowledge deleted messages can disappear between loaded batches (`android/saving.md:25`). | Merge with stable keys and deterministic pagination; preserve archive-only view as complete index. Test gap boundaries and all-deleted spans. | Not implemented |
| Archive dashboard | Requested addition; not established in inspected docs or October notes. | Local counts, bytes, retention policy, purge progress and encryption status; no fabricated completeness metric. | Snapshot/payload counters, retention and purge controls authored; physical/index overhead is additional and not measured |
| Encrypted export/import | Docs mention DB import/export deep links (`android/various.md:28`), October adds options, **neither establishes encryption**. | New authenticated versioned format, reviewed password KDF/key handling, resource limits, corruption/tamper tests, dry-run preview and account mapping. Reject unsafe paths/zip bombs and accidental plaintext exports. | Not implemented |
| Global/per-chat literal rules | Android and Desktop expose text matching through their regex filters; see the [native scope](fork/MESSAGE-FILTERS.md). | Bounded literal matching with per-account persistence, explicit case choice and global-chat exclusions. No regex syntax or network mutations. | 18 core tests passed at `6eba37ff`; native controls/placeholders authored; host/projection compile at `f295948a`, but simulator startup timed out before any projection case ran; bounded retry pending; UI verification pending |
| Global/per-chat regex rules | `android/filters/index.md:31`, `:41`, `:53`: shared rules, chat rules and shared-rule exclusions; Java regex. | Native rules need explicit grammar/compatibility, compiled-rule validation, bounded cost and content/rule-version cache keys. No automatic Java regex equivalence promise. | Not implemented |
| Hide blocked-user messages and activity | `android/filters/index.md:18` additionally names reactions, typing and member list. | Separate message presentation from membership truth. Cover reactions, notification previews, mentions, search and activity; do not alter server membership or moderation records. | Not implemented |
| Media/button/type filters | `android/filters/index.md:59` and `:61` expose synthetic `<button>` and `<type>` tags. | Prefer typed native predicates with a defined compatibility adapter; test media albums, captions and unknown types. | Not implemented |
| Create rule from a message | `android/filters/index.md:16`. | Open an editable, bounded literal excerpt from an eligible message; saving and enabling remain explicit. | Native editor/context-menu integration authored; runtime verification pending |
| Temporarily reveal filtered messages | Desktop source keeps an in-memory peer reveal set; see [reference review](fork/MESSAGE-FILTERS.md). | Chat-controller-scoped reveal, placeholder and menu controls; selection mode also shows originals. No persistent rule change. | Native text-bubble integration and projection tests authored; runtime verification pending |
| Disappeared / removed-group history | `android/index.md:32` says banned/kicked chats are kept in cache; earlier audit locates deletion hooks. | Define strictly local retention of content already available. No recovery of remote history or restored access to a removed group. Account permissions and metadata removal need tests. | Not implemented |
| Restricted-forwarding evaluation | `android/forward.md:8` describes automatic download/reupload without author; March 2026 post 52 expands to private chats. | Evaluate separately because re-upload changes attribution, network behavior and expected restrictions. Do not treat this as local archive access. No automatic forwarding from archives; any later send must be a deliberate user action and preserve provenance as designed. | Not implemented |
| Local folder colors/tags | October post 61. | Local presentation metadata only; server premium entitlement/limits stay authoritative. Test folder identity changes and account isolation. | Not implemented |
| Premium-like cosmetics | `shared/ghost.md:65` explicitly says local premium does not raise server limits. | Use independent local cosmetic settings; do not falsify entitlement responses or bypass authentication/payment architecture. | Not implemented |
| Themes, native settings and settings search | Android October redesign; Desktop April post 55 adds settings search; Desktop README includes enhanced appearance. | Reuse iOS native controls/theme/localization/accessibility; integrate all feature policies into a searchable settings registry and migrations. | Native themed archive, Ghost activity and filter controls authored; localization, settings search and recorded UI verification outstanding |
| Optional reviewed E2E sync | [Protocol review](fork/AYUSYNC-REVIEW.md): legacy Android sends readable read-state JSON; edited-upload loop is commented out. Backend source unavailable; Desktop AyuSync is synchronous forwarding, not this service. | Default off and absent from initial local-only release. Requires a separately reviewed authenticated encryption/key management design and explicit endpoint/identity choice. No developer-run service, telemetry, plaintext payloads or implicit archive sync. | Not implemented |

## Additional requirements and explicit gaps

| Feature | Android/reference evidence | Proposed iOS equivalent and limitations | iOS status |
| --- | --- | --- | --- |
| Quick Ghost toggle and active-protection indication | Historical aggregate ghost setting; Desktop toggle; see reference audit | Per-account effective policy, chat toolbar control, individual capability labels; no perfect-invisibility badge | Activity-only toggle/status in avatar menu and native settings authored; full-app/UI verification pending |
| Account-specific settings and archive isolation | Android stores user ID with archive rows; global caches require caution | Separate encrypted DB/key per account; account-scoped local settings and query APIs | DB isolation, per-account preferences and real-Postbox worker tests passed at `387177fd`; actual Keychain/device tests pending |
| Sender, group, channel and bot exclusions | Historical bot gate and October save-exception release note; exact modern precedence unavailable | Persist explicit scope/precedence; test sender-vs-chat conflicts and transitions | Capture policy and account persistence authored; dedicated exclusion picker absent |
| Cache-clear independence | Docs describe Room storage and saved files separate from usual history | Independent protected archive directory with archive-owned media; verify normal cache purge leaves it intact | Dedicated account archive path authored; actual MediaBox cache-clear integration test passed at `387177fd`; physical-device verification pending |
| Edit/deletion timestamps and migration | Old edit data stores entity creation time; modern schema unavailable | Distinguish observed deletion time from server edit time, idempotent schema versions and rollback recovery | Schema 1–5 storage tests passed; 56 earlier text-capture integration cases passed at `387177fd`; new media/live/device verification pending |
| Configurable disappearing-media retention | Reference advertises retention; API terms forbid defeating expiry | Keep TTL/view-once/Secret Chat capture excluded; no security-control bypass | Not implemented; policy-limited |
| Case sensitivity, selected-text and spam rules | Old AyuFilter regex flags and find(); current docs rule creation | Literal predicates with fixed-locale case matching; no generic spam classifier | Case-sensitive/insensitive literals and editable message excerpt authored; regex and classifier absent; native verification pending |
| Native dark/light, smooth animations, settings search | Upstream native iOS supports appearance; Android redesign and Desktop search reference | Reuse themes and native components; settings registry, VoiceOver/Dynamic Type, frame-recorded UI tests | Native themed archive settings authored; recorded UI frames and settings search absent |
| Client-side Premium cosmetics | October local folder colors/tags; docs distinguish server limits | Local appearance metadata only, never alter entitlement checks | Not implemented |
| Photos/video/documents/voice archive | Android saved attachment path; Desktop archive currently text-only | Independent encrypted BLOBs and pending outbox, bounded cached files; no guaranteed redownload after deletion | [Source implemented](fork/PRESERVATION-STATUS.md): completed cached bytes, schema-7 streaming up to 512 MiB/file; earlier small-file integration passed at `92f6d752`, streaming and app validation pending |
| Encrypted database and Keychain keys | Historical Android Room and Desktop SQLite do not establish encryption | Independent SQLCipher, per-account random keys, Keychain this-device-only, Data Protection and backup exclusion | 59 SQLCipher/Keychain tests pass on macOS and iOS simulator at `aba9fae9`; Keychain mocked; actual device checks pending |
| Import/export and optional sync | October import/export options; E2E format/protocol unverified | Authenticated versioned encrypted files; sync only after protocol review and explicit user endpoint choice | Not implemented; [legacy protocol review](fork/AYUSYNC-REVIEW.md) found no E2E archive format and unavailable backend source |
| No telemetry / no archive upload | Both upstreams contain telemetry; Android sync exists | Separate reviewed telemetry-removal diff and request-canary tests, no archive networking capability | Reviewed source containment is integrated into the candidate; [native tests and runtime connection audit remain pending](fork/TELEMETRY-CONTAINMENT.md) |
| iPhone 15 Pro performance | No relevant iOS measurements available | Measure cold start, scroll frames, RSS, DB p95, disk pressure and battery with fixed datasets | Not measured |

## Storage library and experimental integration

`submodules/LocalMessageArchive` is a native Swift module using the existing
vendored SQLCipher library and Security.framework. Its integration now uses TelegramCore hooks and an account worker. Postbox,
authentication and networking implementations remain unchanged. Capture is disabled
by default; application verification is pending. An API unit test is
not proof of automatic capture, safe cache clearing or on-device key protection.

The draft stores supplied text/entity snapshots and bounded completed cloud-media
bytes with observed deletion/edit time, server edit time, account/peer/message
identity and replay keys. It includes bounded queries, exclusions, transactional
batches, age/text/media-payload retention and fail-closed schema/key handling.
The Keychain tests inject Security responses; they do not establish actual
entitlement or locked-device behavior. Export/import and synchronization remain absent; indexed archive search and its native field await validation. Media copying runs on a utility queue after a
durable outbox record. Global deletion defers eager cache unlink only for
successfully queued eligible resources; a concurrent cache purge can still win.
The new media handoff and native preview require their own integration evidence.

The earlier `1b7dfc3b` integration passed the real Postbox, LocalMessageArchive,
TelegramCore and TextFormat iOS simulator targets in
[run 41811248732](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41811248732).
This covers that revision's 32 archive integration cases. The later suite has
56 cases, including local deletion/edit acknowledgements, rich-content gaps,
MediaBox cache clearing and byte preservation of incompatible preferences;
all 56 passed at `387177fd` in the revision-specific evidence above. Full-app/UI execution remains
unverified. Existing incompatible settings are read-only, never silently
replaced with older defaults.

Ordinary flat text, message entities and bounded cloud-media descriptors/bytes are the current capture format. Structured
rich messages keep their content outside the flat text field and are unsupported:
eligible deletion/change events increment the missed-capture count instead of
storing a misleading empty record. Minimum-available channel range capture is now authored with real replay tests;
other history-clear, history-validation, group-removal and extension mutation
paths remain uncovered. This is not a
complete or lossless message-history engine.

The separate archive workflow runs native host XCTest against the real vendored
SQLCipher implementation without Telegram secrets. The first execution, [run
41794260200](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41794260200),
compiled successfully but failed 17 storage tests at database open. Eleven mocked
Keychain tests and two unsafe-path tests passed. The physical-path fix in `86fcf189` passes [run 41794635275](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41794635275): **31 native tests** (20 database, 11 mocked Keychain) with zero failures. The module's iOS simulator variant also compiles. These are not iOS runtime or Telegram integration tests. [Machine-readable evidence](fork/evidence/archive-tests-86fcf189.json) records the exact commit, results and artifact digest. Device Data Protection, crash recovery across the
two stores, physical storage quota and iPhone performance remain unverified.

The schema-2 library increment adds usage counters, bounded indexed
retention cleanup and a transactional v1-to-v2 migration. Commit `9bfc4c87` passed 39 native macOS tests and iOS simulator compilation in
[run 41795912590](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795912590).
Seven added tests cover migration, rollback and counter consistency across two
live connections with sequential operations. The same 10,000-record benchmark
measured 0.340 ms mean append latency versus 29.648 ms in the earlier hosted run;
these are separate host measurements, not iPhone results. See
[performance observations](fork/PERFORMANCE.md) and
[revision-specific evidence](fork/evidence/archive-tests-9bfc4c87.json).

The schema-3 draft adds the local half of that purge protocol: required capture
generations, transactional generation advances and idempotent purge retries.
Commit `c326364b` passed 46 native macOS tests and iOS simulator compilation in
[run 41796701379](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41796701379).
The new tests cover the local generation boundary, rollback, retry and migration.
Those earlier results do not cover the newly authored Postbox coordinator or prove
application-level purge safety. [Exact evidence](fork/evidence/archive-tests-c326364b.json) records the
commit, test counts, benchmark samples and downloadable artifact identity.

## Delivery status

| Deliverable | Status | Evidence / next gate |
| --- | --- | --- |
| Existing source retained | Preserved with explicit feature changes | Source guard pins dependencies and all upstream files except named capture/lifecycle/UI seams; allowlisted diffs require review |
| Android/Desktop/October feature audit | Source/document review | Reference docs distinguish inspected code from release claims and missing proprietary source |
| Hosted macOS workflow | Published; baseline not passed | [PR #5](https://github.com/Livara-Dev/Telegram-iOS/pull/5) adds independent integration-gated simulator/device jobs, checksums and artifacts; active earlier runs retain their original workflow |
| Portable tooling tests | Executed locally | Final counts and commands in [baseline evidence](fork/BASELINE.md) |
| Working iOS baseline | Not demonstrated | Core iOS suites pass on documented revisions; full Ghost source compiled but final framework signing failed. See [build failures](fork/BUILD-FAILURES.md). |
| Installable independently signed IPA | Not produced | Unsigned mode is separate; signing, all entitlements and device installation still need verification |
| GPL review | Obligations identified; incomplete release clearance | [license audit](fork/LICENSE-AND-SUPPLY-CHAIN.md); full dependency/source bundle required |
| Standalone archive module | 59 macOS and 59 iOS simulator tests pass; module compiles for iOS | Schema-5 revision `aba9fae9`, runs 41838526587 and 41838526609; adapter/replay compilation failed separately and awaits retest |
| Security verification | Source review plus storage boundary tests | [risk register](SECURITY_ASSESSMENT.md); no runtime privacy attestation |

Update each feature row with a commit and concrete test evidence when implemented.
Do not promote an entry to working because a model test or compiler succeeds.

## Ghost activity draft

`feature/ghost-activity-draft` is based on the archive work and limits its first
increment to typing, sticker selection, recording, uploads and game activity.
It uses per-account Postbox settings, a disabled master switch by default, a
serialized activity worker and a fresh persisted-policy check before dispatch.
Unknown settings remain read-only and block only the covered activity categories.
The 15 authored tests exercise real preferences and the actual worker/resolver
with a typed request recorder; they do not contact Telegram servers. Native
settings and an account-wide chat-menu toggle expose which categories are
configured. Message-read receipts, online presence, automatic offline and
read-on-interaction remain unimplemented. The subsequent story-only increment
has separate settings and is not covered by the activity-test results. See [scope, reference evidence and
verification limits](fork/GHOST-ACTIVITY.md). No full Ghost Mode claim is made.

Desktop inspection confirms typed request suppression, per-chat/global filters
and temporary reveal. Its current persistent archive is text-only SQLite, not
proof of encrypted media retention; its `AyuSync` forwarding namespace is not an
end-to-end encrypted archive synchronization service. Desktop is GPLv3-or-later
with an OpenSSL exception, so copying its code would require a distinct license
compatibility decision. This checkpoint copies no Desktop implementation.

Archive follow-up: global-delete replay now defers eager cache removal for
successfully queued eligible media, and media retention eviction is reported
as unavailable. Real replay tests are authored for global deletion and a media replacement
followed by deletion; native execution is pending. This does not extend support
to timed/view-once media or guarantee capture before a simultaneous cache purge.

### Large attachment follow-up (native verification pending)

The archive coordinator now submits completed cloud files to chunked SQLCipher
storage (512 MiB source cap, configured quota, 64 KiB buffer), instead of reading
an entire attachment into memory. Schema 7 migrates existing media rows and
usage accounting transactionally. Tests cover a 20 MiB source, range reads,
rollback, retry, account isolation and real Postbox/cache restart. These new
cases are awaiting macOS CI. Full video/document playback and a verified app
build remain open; this is not an additional parity-complete feature claim.

The native playback follow-up adds an AVFoundation reader over encrypted archive
ranges, plus native video/voice-row entry points and lock/background cleanup.
Its generated H.264 decode test and cancellation tests await native CI. Ogg voice,
animated playback, document viewing, a grid gallery and UI/device verification
remain open. Source existence does not establish codec or UI parity.
