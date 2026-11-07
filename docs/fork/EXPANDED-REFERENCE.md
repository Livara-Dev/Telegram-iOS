# Expanded AyuGram feature-reference audit

Audit date: 2026-11-04. This is source and documentation analysis, not confirmation of a running Android, Desktop or iOS build. It supplements `AYUGRAM-REFERENCE.md`. No source files were modified, no application binaries were run, and no proprietary classes were reconstructed or translated.

## Evidence sets and their dates

| Evidence | Revision or dated source | What it establishes |
| --- | --- | --- |
| Official Android public source | [`AyuGram/AyuGram4A`](https://github.com/AyuGram/AyuGram4A), sole advertised branch `rewrite`, `7013145676d36d82ee13c02a89f72097b7490dcd`, author/commit date **2023-07-28** | Historical implementation details only. This is not the October 2026 Android release source. |
| Official Desktop source | [`AyuGram/AyuGramDesktop`](https://github.com/AyuGram/AyuGramDesktop), branch `dev`, `0f2a31bb420de98f331990432265b131b8d836b5`, **2026-10-20** | A substantially newer native C++/Qt implementation reference. Desktop behavior does not establish iOS feasibility or Android parity. |
| Official documentation source | [`AyuGram/AyuGramDocs`](https://github.com/AyuGram/AyuGramDocs), `42f5f32c6b05aab4a69a5f8d0bf7652e1cb11a9b`, **2025-01-08** | Published feature semantics and known limitations; some details are stale relative to 2026 releases. |
| Requested live documentation | [Android](https://docs.ayugram.one/android/), [Ghost](https://docs.ayugram.one/shared/ghost/), [Saving](https://docs.ayugram.one/android/saving/), retrieved **2026-11-04** | Readable current web pages. Retrieval date does not imply the content was authored or implemented in 2026. Android page HTTP Last-Modified was 2025-01-08. |
| Official Android October release notes | [AyuGramReleases/61](https://t.me/AyuGramReleases/61), **2026-10-05 20:02:21 UTC** | Maintainer claims for the newer release, not inspected implementation. |
| Official Android release identifier | [AyuGramReleases/62](https://t.me/AyuGramReleases/62), same timestamp, build **20261005**, based on **exteraGram v12.10.6** | Identifies the release and full/lite variants. Full includes plugin support. |

`git fetch --unshallow origin` completed for Android: 1,043 reachable commits. `git ls-remote --heads --tags origin` returned only `rewrite`, with no advertised tags. `git log --all --since=2026-10-01 --until=2026-11-01` returned no commits. GitHub's October commits endpoint and releases endpoint each returned `[]`. Consequently, **the October Android implementation and its corresponding source cannot be verified from this official public repository**. No fork or unrelated branch is silently substituted. The public root still references uninspected `AyuGram4A_proprietary` gitlink `a3b47ff3af30829cdec58d61d61ebc0a64546563`; root README asks builders to implement the two absent helpers.

The release channel was found through the official AyuGram public channel linked by the documentation. Raw requested-page HTML and release-channel HTML are saved under `evidence/`; parsed release text, timestamps and links are in `releases.json`. Only public pages were read. Initial Python urllib requests returned HTTP 403; ordinary `curl` subsequently fetched the pages successfully. No browser scripts or telemetry were executed.

## October 2026 Android improvements, exactly bounded

The English notes in post 61 list:

1. Minor redesign of settings.
2. New options for AyuGram database import/export.
3. Deleted photo/video/file display in the chat profile.
4. Search through deleted messages in regular chat search.
5. Exceptions for saving deleted messages.
6. Local folder colors and tags under local premium.
7. Fixes for some account-login issues.
8. Other bug fixes.

The release links exteraGram 12.9.0 and 12.10.6 notes for inherited changes. It does **not** specify an encrypted export format, at-rest encryption, exact exclusion precedence, media completeness, retention quotas, an archive dashboard, automatic cleanup guarantees, or a reviewed end-to-end encrypted sync design. Those are additional product/security requirements and must be implemented and verified independently. A release-note claim about login fixes is not permission to replace Telegram-iOS authentication or copy Android's official-key impersonation approach.

Useful earlier dated evidence: [post 49](https://t.me/AyuGramReleases/49), 2026-01-10, reports fixes for excessive storage consumption and saved-attachment cleanup; [post 52](https://t.me/AyuGramReleases/52), 2026-03-29, reports deleted-view/ghost/filter fixes and forwarding from copy-restricted private chats. This makes quota, cleanup, regression and migration tests concrete requirements, not hypothetical polish.

## Expanded feature matrix

Paths in the documentation column are relative to the pinned `AyuGramDocs` revision above. The historical Android code details and paths are in the first audit. “Requested addition” means useful and potentially feasible, but not established by the inspected reference. Every iOS feature below remains **unimplemented and untested** by this audit.

| Requested capability | Evidence and limitations | Native iOS implementation/acceptance implication |
| --- | --- | --- |
| Suppress message read receipts | `shared/ghost.md:9`; historical Android `ConnectionsManager.java:327` covers history, encrypted history, discussion and content read methods. Docs name speech-to-text as an exception. | Inventory every read operation, including media content, threads, notifications/extensions and retries. Preserve local state independently and prove no prohibited RPC is emitted under each policy. Explain actions that still reveal reading. |
| Suppress story views | `shared/ghost.md:15`; reactions/replies may mark a story viewed server-side. Not in the 2023 source audit. | Separate story policy from chat read policy. Test ordinary opening, next story, retry, background, reaction and reply; never claim invisible interaction. |
| Warn before opening stories | `shared/ghost.md:61`; cancellation must leave story unopened. | Gate story viewer startup before receipt work is queued; explicit cancellation test. |
| Suppress online status | `shared/ghost.md:21`; sending can still expose online presence. Historical request hook rewrites updateStatus offline. | Gate application-level presence operations, preserving transport/auth; document server inference and other-session limits. |
| Suppress typing, sticker choice, upload, recording indicators | `shared/ghost.md:27` lists typing/sticker/game; old Android drops both ordinary/encrypted typing constructors. | Cover all `SendMessageAction`-equivalent variants and secret-chat paths explicitly. Recording/upload enumeration is an iOS coverage obligation even when the docs do not enumerate it. |
| Automatic offline | `shared/ghost.md:36` warns of online/offline oscillation with another active client. | Per-account policy and bounded scheduling; test no loop/flood, account switch, foreground transition and other-session effects. Cannot retroactively erase a visible online interval. |
| Read on interaction | `shared/ghost.md:44`; historical Android has mark-read-after-send plus explicit mark-read helpers. Docs do not define every interaction. | Specify exact actions (e.g. successful send/manual mark read); scoped one-shot permission, no global race-prone allow flag. Test failures and unrelated chats/accounts. |
| Scheduled sending for ghost use | `shared/ghost.md:48` describes 12-second text delay, dynamic media delay and network fragility. | Use existing Telegram scheduling capability, explicitly showing the delay. Respect unsupported destinations/media, cancellation and failures. No guarantee against server presence inference. |
| Independent deleted-message archive | `android/saving.md:10` describes separate Room storage; old source exposes snapshot/deletion hooks. | Store locally seen snapshots in a separately managed encrypted native store; canonical deletion still proceeds. Cannot recover unseen or already absent data. |
| Edited-message timeline | Old Android records prior content before overwrite; `AyuMessageHistory` displays revisions. | Preserve original/current distinction, order, deduplication, same-timestamp edits, account namespace and rollback atomicity. |
| Saved media | `android/saving.md:17`, `:56` describes auto-download or copying already downloaded files; October post 61 adds profile display. | First version should only retain already available files unless explicit later download policy is approved. Protect filenames/content; test reference counting, eviction, partial downloads and size limits. No promised retrieval after server deletion. |
| Per-chat exceptions and bot controls | October post 61 claims exceptions; `android/saving.md:60` describes disabling bot capture; old config has bot gate. | Define precedence: disabled feature, excluded chat, bot policy, then media/retention policy. Account-specific persistent rules. |
| Size/age limits and cleanup | Requested addition; January 2026 post 49 documents historical excessive storage/cleanup fixes. | Bounded archive, media quota and deterministic retention; test crash/restart, shared attachment deletion, disk-full and logout purge. |
| Searchable deleted-only view | `android/saving.md:23`. | Dedicated archive query API; test paging, localization, escaped search input, account isolation and deleted-only results. |
| Deleted results in normal chat search | October post 61. | Distinguish local archived results from Telegram results and prohibit upload of archive content/query index. Test deduplication and navigation to absent canonical message. |
| Deleted media in chat profile | October post 61. | Clearly marked local-only media collection; no false reference to server availability. |
| Inline deleted/edited markers and timeline integration | Historical Android customizable marks; docs acknowledge deleted messages can disappear between loaded batches (`android/saving.md:25`). | Merge with stable keys and deterministic pagination; preserve archive-only view as complete index. Test gap boundaries and all-deleted spans. |
| Archive dashboard | Requested addition; not established in inspected docs or October notes. | Local counts, bytes, retention policy, purge progress and encryption status; no fabricated completeness metric. |
| Encrypted export/import | Docs mention DB import/export deep links (`android/various.md:28`), October adds options, **neither establishes encryption**. | New authenticated versioned format, reviewed password KDF/key handling, resource limits, corruption/tamper tests, dry-run preview and account mapping. Reject unsafe paths/zip bombs and accidental plaintext exports. |
| Global/per-chat regex rules | `android/filters/index.md:31`, `:41`, `:53`: shared rules, chat rules and shared-rule exclusions; Java regex. | Native rules need explicit grammar/compatibility, compiled-rule validation, bounded cost and content/rule-version cache keys. No automatic Java regex equivalence promise. |
| Hide blocked-user messages and activity | `android/filters/index.md:18` additionally names reactions, typing and member list. | Separate message presentation from membership truth. Cover reactions, notification previews, mentions, search and activity; do not alter server membership or moderation records. |
| Media/button/type filters | `android/filters/index.md:59` and `:61` expose synthetic `<button>` and `<type>` tags. | Prefer typed native predicates with a defined compatibility adapter; test media albums, captions and unknown types. |
| Create rule from a message | `android/filters/index.md:16`. | Escape literal text by default; preview scope/results before saving regex. |
| Temporarily reveal filtered messages | Official Desktop [post 55](https://t.me/AyuGramReleases/55), 2026-04-27, claims show/hide filtered messages in current chat. | Session-scoped reveal with visible state, no rule mutation and no unintended media download. Confirm Desktop source separately. |
| Disappeared / removed-group history | `android/index.md:32` says banned/kicked chats are kept in cache; earlier audit locates deletion hooks. | Define strictly local retention of content already available. No recovery of remote history or restored access to a removed group. Account permissions and metadata removal need tests. |
| Restricted-forwarding evaluation | `android/forward.md:8` describes automatic download/reupload without author; March 2026 post 52 expands to private chats. | Evaluate separately because re-upload changes attribution, network behavior and expected restrictions. Do not treat this as local archive access. No automatic forwarding from archives; any later send must be a deliberate user action and preserve provenance as designed. |
| Local folder colors/tags | October post 61. | Local presentation metadata only; server premium entitlement/limits stay authoritative. Test folder identity changes and account isolation. |
| Premium-like cosmetics | `shared/ghost.md:65` explicitly says local premium does not raise server limits. | Use independent local cosmetic settings; do not falsify entitlement responses or bypass authentication/payment architecture. |
| Themes, native settings and settings search | Android October redesign; Desktop April post 55 adds settings search; Desktop README includes enhanced appearance. | Reuse iOS native controls/theme/localization/accessibility; integrate all feature policies into a searchable settings registry and migrations. |
| Optional reviewed E2E sync | Old Android contains optional AyuSync REST/WebSocket integration; its edited-upload loop is commented out. Inspected docs do not establish E2E encryption. | Default off and absent from initial local-only release. Requires a separately reviewed authenticated encryption/key management design and explicit endpoint/identity choice. No developer-run service, telemetry, plaintext payloads or implicit archive sync. |

## Important architecture/privacy differences

The Android reference combines Java controllers and storage hooks with Room, SharedPreferences, exteraGram settings UI and native JNI Telegram networking. The Desktop reference uses C++/Qt, its own Ayu storage/controller layer and Telegram Desktop's operation/update types. Telegram-iOS needs native Swift/Objective-C integration with its existing Postbox/state-update, settings and API scheduling model. Neither Android nor Desktop source is a safe translation target.

Several reference behaviors conflict with the requested product constraints: Android documentation advertises official API keys, Firebase analytics/Crashlytics, public shared-media storage and downloads/reuploads for restricted forwarding. Documentation itself also runs Plausible according to `AyuGramDocs/retype.yml`; this is website configuration, not evidence about a mobile binary. Do not import any telemetry, external verification code, plugin execution engine, official-key impersonation, default third-party sync, or public-storage pattern into the iOS client.

An encrypted independent archive is a new security design; neither an unencrypted Room constructor nor the mere existence of a separate database meets it. iOS Data Protection alone and application-level encryption protect different conditions, so the design must state the threat model and lock/key behavior. Keychain access group changes, app-group extension access, backup exclusion, logout, device migration, and interrupted writes need tests before archive functionality is asserted.

## Audit boundaries and release gate

The October Android features are grounded in dated first-party release notes but cannot be matched to public corresponding source in the inspected official repository. Full Android October binary/source parity, encryption, and absence of telemetry are not verified. Do not distribute that Android binary or copy an unreviewed fork to fill the gap.

The completed Desktop audit is in [DESKTOP-REFERENCE.md](DESKTOP-REFERENCE.md) against its exact newer revision. Its code and tests should be used to discover edge cases, never as evidence that an iOS implementation already works. The native iOS baseline must compile and launch using the user's own Telegram API credentials before feature commits proceed. Every feature needs a separate reviewable diff, passing unit/integration/security tests, and explicit runtime limitations.

## Local evidence integrity

Retrieved HTML SHA-256:

- Android: `66496b01c8c032ce1d03e9f1a347b8dce11785364e570a3823527dc4256cd8cb`
- Ghost: `eaf9fa15c750b5c42e8802c8baa30e8826322296e3597d993816c64e5b27d401`
- Saving: `2ff452e96ad9efa8b4cebbaaaa20909f6ba0130f49ea8a8ecc4b7046f197dfcc`
- Release feed: `cdf92ff42da84e2f11a0cb5cee3e0b3f6d689479279b60e1734192c1a458b79d`

These are audit snapshots, not a signature or guarantee of upstream correctness. Future pages and release posts may be edited; retain the dated snapshot and pinned source revisions when making implementation decisions.
