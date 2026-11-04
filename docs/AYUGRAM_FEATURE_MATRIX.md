# AyuGram for iOS feature matrix

Updated 2026-11-04. **No requested feature is implemented or runtime-tested yet.**
The existing project is the official Telegram-iOS checkout plus the audited
baseline tooling. Its source remains unchanged. Portable tooling tests do not
prove iOS app behavior. Phase A remains blocked on running the hosted build with
an identified fork repository and the owner's API credentials.

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
| Suppress story views | `shared/ghost.md:15`; reactions/replies may mark a story viewed server-side. Not in the 2023 source audit. | Separate story policy from chat read policy. Test ordinary opening, next story, retry, background, reaction and reply; never claim invisible interaction. | Not implemented; API-policy conflict |
| Warn before opening stories | `shared/ghost.md:61`; cancellation must leave story unopened. | Gate story viewer startup before receipt work is queued; explicit cancellation test. | Not implemented |
| Suppress online status | `shared/ghost.md:21`; sending can still expose online presence. Historical request hook rewrites updateStatus offline. | Gate application-level presence operations, preserving transport/auth; document server inference and other-session limits. | Not implemented; API-policy conflict |
| Suppress typing, sticker choice, upload, recording indicators | `shared/ghost.md:27` lists typing/sticker/game; old Android drops both ordinary/encrypted typing constructors. | Cover all `SendMessageAction`-equivalent variants and secret-chat paths explicitly. Recording/upload enumeration is an iOS coverage obligation even when the docs do not enumerate it. | Not implemented; API-policy conflict |
| Automatic offline | `shared/ghost.md:36` warns of online/offline oscillation with another active client. | Per-account policy and bounded scheduling; test no loop/flood, account switch, foreground transition and other-session effects. Cannot retroactively erase a visible online interval. | Not implemented; API-policy conflict |
| Read on interaction | `shared/ghost.md:44`; historical Android has mark-read-after-send plus explicit mark-read helpers. Docs do not define every interaction. | Specify exact actions (e.g. successful send/manual mark read); scoped one-shot permission, no global race-prone allow flag. Test failures and unrelated chats/accounts. | Not implemented |
| Scheduled sending for ghost use | `shared/ghost.md:48` describes 12-second text delay, dynamic media delay and network fragility. | Use existing Telegram scheduling capability, explicitly showing the delay. Respect unsupported destinations/media, cancellation and failures. No guarantee against server presence inference. | Not implemented; API-policy conflict |
| Independent deleted-message archive | `android/saving.md:10` describes separate Room storage; old source exposes snapshot/deletion hooks. | Store locally seen snapshots in a separately managed encrypted native store; canonical deletion still proceeds. Cannot recover unseen or already absent data. | Not implemented |
| Edited-message timeline | Old Android records prior content before overwrite; `AyuMessageHistory` displays revisions. | Preserve original/current distinction, order, deduplication, same-timestamp edits, account namespace and rollback atomicity. | Not implemented |
| Saved media | `android/saving.md:17`, `:56` describes auto-download or copying already downloaded files; October post 61 adds profile display. | First version should only retain already available files unless explicit later download policy is approved. Protect filenames/content; test reference counting, eviction, partial downloads and size limits. No promised retrieval after server deletion. | Not implemented |
| Per-chat exceptions and bot controls | October post 61 claims exceptions; `android/saving.md:60` describes disabling bot capture; old config has bot gate. | Define precedence: disabled feature, excluded chat, bot policy, then media/retention policy. Account-specific persistent rules. | Not implemented |
| Size/age limits and cleanup | Requested addition; January 2026 post 49 documents historical excessive storage/cleanup fixes. | Bounded archive, media quota and deterministic retention; test crash/restart, shared attachment deletion, disk-full and logout purge. | Not implemented |
| Searchable deleted-only view | `android/saving.md:23`. | Dedicated archive query API; test paging, localization, escaped search input, account isolation and deleted-only results. | Not implemented |
| Deleted results in normal chat search | October post 61. | Distinguish local archived results from Telegram results and prohibit upload of archive content/query index. Test deduplication and navigation to absent canonical message. | Not implemented |
| Deleted media in chat profile | October post 61. | Clearly marked local-only media collection; no false reference to server availability. | Not implemented |
| Inline deleted/edited markers and timeline integration | Historical Android customizable marks; docs acknowledge deleted messages can disappear between loaded batches (`android/saving.md:25`). | Merge with stable keys and deterministic pagination; preserve archive-only view as complete index. Test gap boundaries and all-deleted spans. | Not implemented |
| Archive dashboard | Requested addition; not established in inspected docs or October notes. | Local counts, bytes, retention policy, purge progress and encryption status; no fabricated completeness metric. | Not implemented |
| Encrypted export/import | Docs mention DB import/export deep links (`android/various.md:28`), October adds options, **neither establishes encryption**. | New authenticated versioned format, reviewed password KDF/key handling, resource limits, corruption/tamper tests, dry-run preview and account mapping. Reject unsafe paths/zip bombs and accidental plaintext exports. | Not implemented |
| Global/per-chat regex rules | `android/filters/index.md:31`, `:41`, `:53`: shared rules, chat rules and shared-rule exclusions; Java regex. | Native rules need explicit grammar/compatibility, compiled-rule validation, bounded cost and content/rule-version cache keys. No automatic Java regex equivalence promise. | Not implemented |
| Hide blocked-user messages and activity | `android/filters/index.md:18` additionally names reactions, typing and member list. | Separate message presentation from membership truth. Cover reactions, notification previews, mentions, search and activity; do not alter server membership or moderation records. | Not implemented |
| Media/button/type filters | `android/filters/index.md:59` and `:61` expose synthetic `<button>` and `<type>` tags. | Prefer typed native predicates with a defined compatibility adapter; test media albums, captions and unknown types. | Not implemented |
| Create rule from a message | `android/filters/index.md:16`. | Escape literal text by default; preview scope/results before saving regex. | Not implemented |
| Temporarily reveal filtered messages | Official Desktop [post 55](https://t.me/AyuGramReleases/55), 2026-04-27, claims show/hide filtered messages in current chat. | Session-scoped reveal with visible state, no rule mutation and no unintended media download. Confirm Desktop source separately. | Not implemented |
| Disappeared / removed-group history | `android/index.md:32` says banned/kicked chats are kept in cache; earlier audit locates deletion hooks. | Define strictly local retention of content already available. No recovery of remote history or restored access to a removed group. Account permissions and metadata removal need tests. | Not implemented |
| Restricted-forwarding evaluation | `android/forward.md:8` describes automatic download/reupload without author; March 2026 post 52 expands to private chats. | Evaluate separately because re-upload changes attribution, network behavior and expected restrictions. Do not treat this as local archive access. No automatic forwarding from archives; any later send must be a deliberate user action and preserve provenance as designed. | Not implemented |
| Local folder colors/tags | October post 61. | Local presentation metadata only; server premium entitlement/limits stay authoritative. Test folder identity changes and account isolation. | Not implemented |
| Premium-like cosmetics | `shared/ghost.md:65` explicitly says local premium does not raise server limits. | Use independent local cosmetic settings; do not falsify entitlement responses or bypass authentication/payment architecture. | Not implemented |
| Themes, native settings and settings search | Android October redesign; Desktop April post 55 adds settings search; Desktop README includes enhanced appearance. | Reuse iOS native controls/theme/localization/accessibility; integrate all feature policies into a searchable settings registry and migrations. | Not implemented |
| Optional reviewed E2E sync | Old Android contains optional AyuSync REST/WebSocket integration; its edited-upload loop is commented out. Inspected docs do not establish E2E encryption. | Default off and absent from initial local-only release. Requires a separately reviewed authenticated encryption/key management design and explicit endpoint/identity choice. No developer-run service, telemetry, plaintext payloads or implicit archive sync. | Not implemented |

## Additional requirements and explicit gaps

| Feature | Android/reference evidence | Proposed iOS equivalent and limitations | iOS status |
| --- | --- | --- | --- |
| Quick Ghost toggle and active-protection indication | Historical aggregate ghost setting; Desktop toggle; see reference audit | Per-account effective policy, chat toolbar control, individual capability labels; no perfect-invisibility badge | Not implemented |
| Account-specific settings and archive isolation | Android stores user ID with archive rows; global caches require caution | Separate encrypted DB/key per account; account-scoped local settings and query APIs | Not implemented |
| Sender, group, channel and bot exclusions | Historical bot gate and October save-exception release note; exact modern precedence unavailable | Persist explicit scope/precedence; test sender-vs-chat conflicts and transitions | Not implemented |
| Cache-clear independence | Docs describe Room storage and saved files separate from usual history | Independent protected archive directory with archive-owned media; verify normal cache purge leaves it intact | Not implemented |
| Edit/deletion timestamps and migration | Old edit data stores entity creation time; modern schema unavailable | Distinguish observed deletion time from server edit time, idempotent schema versions and rollback recovery | Not implemented |
| Configurable disappearing-media retention | Reference advertises retention; API terms forbid defeating expiry | Keep TTL/view-once/Secret Chat capture excluded; no security-control bypass | Not implemented; policy-limited |
| Case sensitivity, selected-text and spam rules | Old AyuFilter regex flags and find(); current docs rule creation | Typed/literal predicates, explicit case behavior, opt-in bounded regex; no accuracy claim for generic spam detection | Not implemented |
| Native dark/light, smooth animations, settings search | Upstream native iOS supports appearance; Android redesign and Desktop search reference | Reuse themes and native components; settings registry, VoiceOver/Dynamic Type, frame-recorded UI tests | Upstream appearance only; fork settings not implemented |
| Client-side Premium cosmetics | October local folder colors/tags; docs distinguish server limits | Local appearance metadata only, never alter entitlement checks | Not implemented |
| Photos/video/documents/voice archive | Android saved attachment path; Desktop archive currently text-only | Protected archive-owned bytes plus typed metadata and missing/partial states; no guaranteed redownload after deletion | Not implemented |
| Encrypted database and Keychain keys | Historical Android Room and Desktop SQLite do not establish encryption | Independent SQLCipher, per-account random keys, Keychain this-device-only, Data Protection and backup exclusion | Not implemented |
| Import/export and optional sync | October import/export options; E2E format/protocol unverified | Authenticated versioned encrypted files; sync only after protocol review and explicit user endpoint choice | Not implemented; sync protocol not cleared |
| No telemetry / no archive upload | Both upstreams contain telemetry; Android sync exists | Separate reviewed telemetry-removal diff and request-canary tests, no archive networking capability | Not satisfied by unchanged upstream |
| iPhone 15 Pro performance | No relevant iOS measurements available | Measure cold start, scroll frames, RSS, DB p95, disk pressure and battery with fixed datasets | Not measured |

## Delivery status

| Deliverable | Status | Evidence / next gate |
| --- | --- | --- |
| Existing source retained | Complete for audit checkpoint | Source guard checks immutable upstream tree and dependency manifest; no auth/crypto/network source edits |
| Android/Desktop/October feature audit | Source/document review | Reference docs distinguish inspected code from release claims and missing proprietary source |
| Hosted macOS workflow | Written; not executed | `xcode-27`, exact toolchain guards, runtime install attempt, tests, SHA-256 and artifact publishing |
| Portable tooling tests | Executed locally | Final counts and commands in [baseline evidence](fork/BASELINE.md) |
| Working iOS baseline | Blocked, not demonstrated | No supplied GitHub fork/API credentials; local executor is Linux |
| Installable independently signed IPA | Not produced | Unsigned mode is separate; signing, all entitlements and device installation still need verification |
| GPL review | Obligations identified; incomplete release clearance | [license audit](fork/LICENSE-AND-SUPPLY-CHAIN.md); full dependency/source bundle required |
| Security verification | Source findings only | [risk register](SECURITY_ASSESSMENT.md); no runtime privacy attestation |

Update each feature row with a commit and concrete test evidence when implemented.
Do not promote an entry to working because a model test or compiler succeeds.

Desktop inspection confirms typed request suppression, per-chat/global filters
and temporary reveal. Its current persistent archive is text-only SQLite, not
proof of encrypted media retention; its `AyuSync` forwarding namespace is not an
end-to-end encrypted archive synchronization service. Desktop is GPLv3-or-later
with an OpenSSL exception, so copying its code would require a distinct license
compatibility decision. This checkpoint copies no Desktop implementation.
