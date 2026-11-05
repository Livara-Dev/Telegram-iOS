# AyuGram native iOS feature matrix

Audit date: **2026-11-05**. Baseline statuses below refer to application source
`62546557807ee5d8ff468ea3d7d46ce1a090c8cf` and audited integration source
`73b52244d4fc7e82cb82970ed9657a499d3cda2c`, before the current dashboard/feature
implementation batch. A setting or compiled library is not marked fully working
without real application behavior and relevant test evidence. New source authored
in the current worktree needs a separate qualified build and UI recordings.

The [dated reference audit](fork/REFERENCE-AUDIT-2026-11-05.md) verifies the requested
**November 4, 2026 Android build 20261104**, based on exteraGram 13.1.1. Its changelog
references the exteraGram November 2 announcement. Public AyuGram4A source is
still July 2023 (`70131456`), Desktop is October 20, 2026 (`0f2a31bb`), and the docs
source is January 2025 (`42f5f32c`). This is an inventory of discoverable public
features, not a claim of complete corresponding source for the current Android
binary. No proprietary exteraGram components are copied.

## Current verification evidence

- Application source `62546557` produced the full unsigned ARM64 device build in
  [42031063759](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42031063759).
  Seven native suites executed **1,134 passing cases**; its original Projection
  host ran zero cases. That full run failed, so it is not an all-green release.
- The same Keychain/archive/playback modules passed **104 native cases** in
  [42027769348](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42027769348)
  and again at c484 in
  [42058341829](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42058341829).
  Counts: 16 PasscodeCore, 77 LocalMessageArchive, 11 encrypted playback. The deprecated
  API and video failures are repaired; neither remains a current open failure.
- [42075582797](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075582797)
  executed **13 native Projection cases, all passing**, at f48c9b04. Two startup
  stalls/reboots preceded success; stable first-attempt launch and real failed
  test-product capture were not established. This pass must not be described as
  a new full application qualification.
- [42075783146](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146)
  passed two actual authorization-screen launches in one fresh simulator, with
  a private selection explicitly seeded for the test. The app created private
  account metadata and retained the same directory across restart. This is not
  automatic-fallback, Telegram-login, message-preservation or physical-device proof.
  The new [main-app-only unsigned device artifact 13438212892](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42075783146/artifacts/13438212892)
  omits six extensions and preserves all 880 retained members from the original
  device package. It requires independent signing and device testing.
- The user's later report says the current IPA launches but remains visually
  similar to stock Telegram. That is user-reported startup evidence, not a
  demonstration of the hidden/default-off feature paths. No new feature batch
  or customized UI is claimed verified by the older startup recording.

## Core features and native integration

Reference column abbreviates the pinned public sources documented in the
[Android](fork/AYUGRAM-REFERENCE.md), [Desktop](fork/DESKTOP-REFERENCE.md) and
[November audit](fork/REFERENCE-AUDIT-2026-11-05.md). “Native-tested” describes the
named local tests; “source-wired” means a real call path exists but does not by
itself establish the authenticated running-app experience.

| Feature | Android / Desktop implementation or reference | Native iOS equivalent / code boundary | Baseline status and limitation |
| --- | --- | --- | --- |
| Deleted text in private chats, groups and channels | Android `AyuMessagesController`; Desktop `processMessageDelete` | `LocalArchiveCapture`, durable Postbox outbox, account worker and SQLCipher store | Native local-pipeline tests pass; opt-in, available cloud content only; live sender-deletion scenario unverified |
| Edits and all observed previous versions | Android `MessagesStorage`/edited DAO; Desktop edition hook | Capture before server replay/own-edit acknowledgement; version timeline | Native local integration passes; unsupported rich content and unseen history are not recovered |
| Deletion/edit timestamps | Both reference databases record revisions/deletion metadata | Observed deletion time and server edit time stored separately | Native storage/integration tests pass; observation time is not proof of server deletion time |
| Photos | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Generated JPEG exercised through local capture/cache cleanup; source supports completed cloud images |
| Videos | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Local byte-pipeline fixtures plus 11 real encrypted H264 loader cases; live incoming-video capture unverified |
| GIFs/animations | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Classified separately as animation; byte-path fixtures do not prove every GIF decoder |
| Voice messages | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Voice metadata and completed cached bytes supported; live voice playback/capture unverified |
| Round/video messages | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Separate videoMessage classification; real view-once round/voice content excluded |
| Documents | Android saved attachments; Desktop persistent archive is text-only | Encrypted archive-owned completed bytes and metadata | Streaming file integration includes a 20 MiB synthetic document; live received-document workflow unverified |
| Incomplete or missing downloads | Android may request downloads before/after deletion | Store descriptive metadata and report unavailable bytes; never claim preservation from metadata alone | Partial: only complete locally cached eligible resources; no promised redownload after deletion |
| Independent media cache | Android separate attachment folder; Desktop DB plus memory | Archive-owned encrypted media outside MediaBox cache | Native local cache-clear/reopen tests pass; physical cache-clear scenario still unverified |
| Crash-safe handoff and replay | Neither reference DB alone proves cross-store atomicity | Durable ordered Postbox capture operations and idempotent archive receipts | Native local rollback/replay tests pass; two SQLite stores are not one atomic commit |
| Account isolation | Android user/dialog keys; Desktop user/dialog/topic keys | Account directory, Keychain identity and archive DB identity | Native isolation tests pass; SideStore/team/profile migration and physical lock still unverified |
| Retention age and storage limits | Android cleanup notes; no current public implementation | Transactional retention with bounded payload budget and native presets | Native tests pass; payload accounting is not full physical-disk quota; baseline default 32 MiB |
| Chat/group/channel exclusions | October Android release claim; historical Java bot gate | Persistent excludedPeerIds policy before capture | Policy is implemented/tested; baseline has only chat-specific control, broad picker pending |
| Sender/user exclusions | Required product behavior; modern Android internals unavailable | Persistent excludedSenderIds; fail closed when sender unknown | Core policy implemented/tested; baseline scope-management UI incomplete |
| Bot exclusions | Android saveForBots; Desktop saveForBots | Account-local excludeBots policy | Native tests pass; baseline default excludes bots |
| Deleted-only screen and search | Android View Deleted; Desktop history section | Native paged archive screen and encrypted indexed local queries | Source-wired/compiled; local tests pass; authenticated UI recordings pending |
| History timeline | Android AyuMessageHistory; Desktop history UI | Native original/edited/deleted version screen | Source-wired; real conversation navigation and recorded UI pending |
| Media gallery | October Android profile-media claim; Desktop persistent media absent | Native encrypted gallery with type filters and per-chat queries | Source-wired; gallery helper tests pass; actual authenticated gallery UI unverified |
| Archive management and explicit deletion | Reference dialog clearing; Android DB tools | Native usage counters, retention controls and generation-aware purge | Core tests pass; exact physical allocation/cryptographic erasure not claimed |
| Deleted hits in standard chat search | October Android release claim | Merge explicitly local archive hits without uploading archived text/index | Absent in baseline |
| Deleted media in standard chat profile | October Android release claim | Explicit archive media route/tab scoped to the current chat | Standalone chat-scoped gallery exists; standard profile integration absent in baseline |
| Inline deleted/edited indicators | Android customizable marks; Desktop semi-transparent/mark rendering | Native stable archive projection and tap-to-history behavior | Absent in baseline; no reconstruction into outgoing/server message queues |
| Custom deleted/edited labels | Android mark preferences; Desktop deletedMark/editedMark | Bounded local marker preference and renderer | Absent in baseline |
| Per-chat archive clearing | Desktop clear dialog/topic; Android database controls | Account/chat-scoped explicit purge with replay generation safety | Store/coordinator controls exist; per-chat UI completeness unverified |
| Account removal and logout retention | References differ; no general permanence proof | Keep policy explicit and separate from ordinary media-cache clearing | Not guaranteed: upstream account-directory cleanup can remove the contained archive |
| History after removal from a group | Android cache retention claim; Desktop DeletedDialog schema incomplete | Retain already archived eligible content only | Previously archived data remains while account directory exists; full removed-group history recovery absent |
| Encrypted import/export | October Android import/export options; encryption not established | New versioned authenticated local-file format with bounded parsing and account mapping | Absent in baseline; no plaintext fallback or export of Telegram sessions |
| Optional encrypted synchronization | Historical Android AyuSync transport; Desktop AyuSync name is forwarding helper | Separate protocol/security review, explicit user enablement, local-only default | Not implemented: no reviewed compatible E2E archive protocol/backend source |
| Timed/self-destructing photos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Timed/self-destructing videos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| View-once photos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| View-once videos | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Disappearing voice messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Disappearing round/video messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Cloud auto-delete timer messages | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Secret Chat retention | Reference advertises retention; current API terms §1.4 prohibits defeating expiry | Preserve normal expiry; do not bypass encryption, one-shot access or copy protection | Unsupported in archive baseline; autoremove/autoclear/expired/secret content excluded; no verified contractual exception |
| Message read suppression | Historical Android request interception; Desktop typed history/content gates | Native operation policy preserving local state and canonical queues | Absent in baseline; API-contract conflict; new candidate requires its own tests |
| Story view suppression | Current docs and Desktop story gates | Account-scoped producer/queue generation checks in LocalStoryPrivacy | 26 native local story tests passed in625; reactions/replies and other clients can reveal views |
| Typing suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Sticker-selection suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Voice/round recording suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Upload progress suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Game activity suppression | Android typing hook; Desktop api_send_progress switch | Per-account LocalGhostSettings and typed outgoing activity gate | 15 native local activity tests pass across categories; user must enable; already sent/server-derived activity remains visible |
| Online suppression | Android updateStatus rewrite; Desktop presence gate | Gate explicit native presence requests without changing transport/authentication | Absent in baseline; API-contract conflict; sending/reactions/other clients may still reveal activity |
| Automatic offline attempt | Desktop worker timer; Android preference | Bounded explicit offline operation after unavoidable activity | Absent in baseline; no guaranteed background timer or invisibility |
| Read on interaction | Android read after send; Desktop multiple explicit actions | Scoped, documented one-shot permission after chosen user actions | Absent in baseline; intentionally reveals reading when enabled |
| Ghost scheduled sending | Docs/ Desktop media-dependent delayed scheduling | Reuse real Telegram scheduling; preserve explicit schedules and failures | Absent; reference documents unreliability; no hidden fallback or invisibility claim |
| Warning before opening stories | Android docs; Desktop window controller | Cancelable pre-navigation warning before receipt-producing story work | Absent in baseline |
| Per-account Ghost preferences | Desktop account/global settings; old Android global SharedPreferences | Postbox-backed account activity/story preferences | Implemented locally; native tests pass; full privacy-mode state not implemented |
| Quick Ghost toggle and active status | Android drawer; Desktop drawer/tray/context controls | Visible aggregate account control with exact protection list | Baseline lists activity/story state in nested settings; chat quick toggle absent |
| Global/per-chat literal filters | References use regex, which also supports plain text | Bounded literal evaluator and per-account persisted rules | 18 native core and13 native projection tests passed; incoming ordinary text scope only |
| Case-sensitive/insensitive matching | Android regex flags; Desktop flags | Deterministic literal case choice | Implemented/tested for literal rules; not equivalent to all Java regex behavior |
| Global-filter exclusions | Android shared exclusions; Desktop DB exclusions | Skip globals in selected peers while keeping explicit chat rules | Core implemented/tested; broad scope picker incomplete in baseline |
| Create rule from a message | Android selected text; Desktop context edit filter | Native rule editor prefilled with message excerpt | Source-wired; not proof of precise selected-substring behavior or authenticated UI |
| Temporary reveal | Desktop revealed-dialog set | Account/chat-local presentation reveal with clear state | Source-wired/tested projection path; UI frame verification pending |
| Regular expressions | Android Java regex; Desktop ICU regex | Bounded supported syntax or independently reviewed engine, never arbitrary main-thread backtracking | Absent in baseline; no misleading regex toggle |
| Media/button-type filters | References append metadata tags to regex input | Structured local predicates over media/button metadata | Absent in baseline |
| Hide blocked users locally | Android docs; Desktop sender/via-bot/forwarded checks | Explicit local presentation rule distinct from server block state | Absent in baseline |
| Local shadow bans | Desktop shadowBanIds | Account-local sender exclusions that do not alter server content | Absent as a display filter; archive sender-exclusion policy is a different feature |
| Inverse matching, albums and previews | Desktop reversed filter and Android album/dialog behavior | Define group semantics, ordinary previews, notifications and reveal | Baseline excludes media/forwarded/grouped/large emoji/rich/secret/expiring messages |
| Remote filter import and paste export | Android allowlisted hosts; Desktop dpaste publishing | Prefer explicit local files; network import needs separate validation/consent | Not implemented; no background rules download or archive paste upload |
| Sponsored-message hiding | Android disableAds; Desktop disableAds | Local display policy, separate from transport and telemetry | Absent in baseline; API terms §3.3 conflict must be disclosed if implemented |
| Proxy sponsor/promotional elements | Android docs; Desktop advertising controls | Local presentation only, without changing proxy authentication/networking | Absent in baseline |
| Local Premium cosmetics | Docs distinguish client visuals from server limits | Independent appearance preferences; never fake paid server entitlement | Absent as an AyuGram feature in baseline; ordinary upstream appearance remains |
| Local folder colors/tags | October Android claim; Desktop uses server tag API in inspected path | Account-local presentation metadata only | Absent; do not use messages.toggleDialogFilterTags as proof of local-only behavior |
| Emoji/profile/reply colors and icons | Android documentation | Local visual overlays only where renderers support them | Absent as local overrides; server Premium settings remain entitlement-gated |
| Native light/dark themes and text scaling | References appearance; upstream iOS already supplies native controls | Reuse native theme/accessibility settings | Upstream source exists; not proof of newly customized AyuGram UI |
| Alternative app icons | Desktop picker; Android exteraGram behavior | Own bundled artwork and native alternate-icon API | Upstream icon code exists; new AyuGram artwork/eligibility controls absent in baseline |
| Prominent searchable Custome options | New user requirement; Desktop searchable builder; October Android redesign | One native top-level dashboard with 11 categories and real account controls | Absent in shipped baseline; active implementation is not yet validated |
| Settings reset | Reference preferences resets | Explicit category/account reset without deleting archive content unless separately confirmed | Only individual existing policy operations; combined dashboard reset absent in baseline |
| Restricted forwarding / AyuForward | Android download/reupload; Desktop forward reconstruction | Respect copy protection and server permissions; normal forwarding stays upstream | No bypass; copy-protected archive capture excluded; no automatic archive upload |
| Peek online / temporary privacy edits | Android docs modifies exceptions and may use another account | No automatic account privacy mutation or secret cross-account lookup | Not implemented; may expose own presence and requires many server requests |
| Screenshots / app-switcher / streamer mode | Android FLAG_SECURE; Desktop capture exclusion | Only public iOS capture-state/app-switcher redaction controls where available | Desktop/Android mechanisms not portable; no platform-security bypass or perfect screenshot prevention |
| Message shots | Desktop selection screenshot editor | Native explicitly requested rendered export, with privacy review | Not implemented; archive views have no implicit share/export pipeline |
| Own API identity and telemetry removal | Reference advertises official keys and Firebase; Desktop crash setting | Own credentials from Secrets; existing reviewed containment; no archive networking | Source containment implemented; runtime zero-telemetry/privacy audit remains unverified |
| Performance and iPhone15 Pro | No relevant native benchmark supplied by references | Measure scroll frames, startup, memory, disk, battery and async work | No physical iPhone benchmark; host/unit timing is not a device performance claim |

## Native source map and advertised-control rule

- Archive policy/capture: `TelegramCore/Sources/LocalArchive/LocalArchiveSettings.swift`,
  `LocalArchiveCapture.swift`, `LocalArchiveCaptureOperation.swift`,
  `LocalArchiveCoordinator.swift`; encrypted store: `LocalMessageArchive`; native
  screens: `SettingsUI/Sources/LocalArchiveSettingsController.swift`.
- Activity/story privacy: `TelegramCore/Sources/LocalPrivacy/LocalGhostSettings.swift`,
  `LocalGhostContext.swift` and the story-privacy implementation; controller:
  `SettingsUI/Sources/LocalGhostSettingsController.swift`.
- Filters: `TelegramCore/Sources/LocalMessageFilter/` and
  `ChatHistoryEntry/ChatLocalTextFilterProjection`; controller:
  `SettingsUI/Sources/LocalMessageFilterSettingsController.swift`.
- Baseline entry points are nested in Data and Storage and chat-specific routes;
  archive/activity/filter enablement defaults off. The requested prominent
  `Custome options` dashboard is an explicit new requirement, not something
  established by existing library tests or the Welcome screen.

Every newly advertised control must name its persistent owner, behavior call
site and actual validation. An unsupported category may explain its limitation
but must not expose a switch that saves a value with no effect. Retention defaults,
account scope and reset behavior must be visible. No server Premium entitlement,
expired content access or third-party archive upload may be fabricated.

## Complete public Desktop preference inventory

The following **109 distinct fields** are defined in the pinned
[`ayu_settings.h`](https://github.com/AyuGram/AyuGramDesktop/blob/0f2a31bb420de98f331990432265b131b8d836b5/Telegram/SourceFiles/ayu/ayu_settings.h).
Reactive `Value`/`Changes` accessors and raw cloud-theme credential/ID fields are
excluded as implementation duplicates, not omitted user features. Definitions
alone are not Desktop runtime verification. Composite filter records, shadow-ban
sets, message-shot cloud themes, translators and forwarding are covered above.
None of these flags should be counted as implemented iOS features merely because
a corresponding settings row can be drawn.

| Desktop option / source line | Android reference correspondence | Proposed native iOS equivalent | Baseline iOS status |
| --- | --- | --- | --- |
| `sendReadMessages` (89) | sendReadPackets | Native read operation gates | Absent |
| `sendReadStories` (90) | Not established in public historical Android; docs/release may differ | Account story operation gates | 26 native local story cases pass |
| `sendOnlinePackets` (91) | sendOnlinePackets | Explicit presence gate | Absent |
| `sendUploadProgress` (92) | sendUploadProgress | Typed activity-category policy | 15 native local activity cases cover categories |
| `sendOfflinePacketAfterOnline` (93) | sendOfflinePacketAfterOnline | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `markReadAfterAction` (94) | markReadAfterSend (narrower) | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `useScheduledMessages` (95) | useScheduledMessages | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `sendWithoutSound` (96) | Not established in public historical Android; docs/release may differ | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `suggestGhostModeBeforeViewingStory` (98) | Not established in public historical Android; docs/release may differ | Account privacy/send policy; see core table | Absent; server and API-contract limits apply |
| `isGhostModeActive` (99) | Not established in public historical Android; docs/release may differ | Display exact active capabilities | Activity/story status only; full mode absent |
| `sendReadMessagesLocked` (102) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendReadStoriesLocked` (103) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendOnlinePacketsLocked` (104) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendUploadProgressLocked` (105) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `sendOfflinePacketAfterOnlineLocked` (106) | Not established in public historical Android; docs/release may differ | Local aggregate-toggle lock preference | Absent |
| `showBackground` (186) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showDate` (187) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showReactions` (188) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showHeaderDecorations` (189) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `showColorfulReplies` (190) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `revealSpoilers` (191) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `embeddedThemeType` (192) | Not established in public historical Android; docs/release may differ | Native message-shot rendering preference | Absent |
| `useGlobalGhostMode` (257) | Not established in public historical Android; docs/release may differ | Prefer independent account policy | Per-account activity/story controls; global aggregation absent |
| `saveDeletedMessages` (270) | saveDeletedMessages | Account archive policy/outbox | Local integration tested; live app proof pending |
| `saveMessagesHistory` (271) | saveMessagesHistory | Pre-overwrite capture/timeline | Local integration tested; UI proof pending |
| `saveForBots` (272) | saveForBots | Account excludeBots | Implemented/tested; default excludes bots |
| `filtersEnabled` (273) | regexFiltersEnabled | Account local filter master switch | Core/projection tested; limited incoming text |
| `filtersEnabledInChats` (274) | regexFiltersInChats | Scoped rules and exclusions | Implemented/tested for literals |
| `hideFromBlocked` (275) | Not established in public historical Android; docs/release may differ | Account-local sender presentation predicate | Absent |
| `semiTransparentDeletedMessages` (276) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableAds` (277) | disableAds | Explicit local sponsor presentation policy | Absent; API terms §3.3 conflict |
| `disableStories` (278) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableCustomBackgrounds` (279) | Not established in public historical Android; docs/release may differ | Native wallpaper selection/disable | Upstream settings exist; Ayu-specific override absent |
| `hidePremiumStatuses` (280) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showOnlyAddedEmojisAndStickers` (281) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `collapseSimilarChannels` (282) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideSimilarChannels` (283) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `messageBubbleRadius` (284) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableOpenLinkWarning` (285) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `wideMultiplier` (286) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `spoofWebviewAsAndroid` (287) | Not established in public historical Android; docs/release may differ | Preserve truthful native webview identity | Not copied |
| `increaseWebviewHeight` (288) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `increaseWebviewWidth` (289) | Not established in public historical Android; docs/release may differ | Native iPhone/iPad/webview layout, platform-specific | Desktop geometry is not portable; custom override absent |
| `materialSwitches` (290) | Not established in public historical Android; docs/release may differ | Native themed switches | Upstream UIKit equivalent, not Material3 port |
| `removeMessageTail` (291) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableNotificationsDelay` (292) | Not established in public historical Android; docs/release may differ | Review native notification/OS limits | Not portable as a guaranteed background effect |
| `localPremium` (293) | localPremium | Independent local cosmetics only | Absent; no server entitlement changes |
| `showChannelReactions` (294) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `showGroupReactions` (295) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `showPrivateChatReactions` (296) | Not established in public historical Android; docs/release may differ | Local reaction presentation only | No Ayu override; server entitlement/permissions preserved |
| `appIcon` (297) | Not established in public historical Android; docs/release may differ | Own bundled alternate icons | Upstream native API/source; Ayu artwork absent |
| `simpleQuotesAndReplies` (298) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideFastShare` (299) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `replaceBottomInfoWithIcons` (300) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `deletedMark` (301) | deletedMarkText | Bounded local marker/rendering | Absent |
| `editedMark` (302) | editedMarkText | Bounded local marker/rendering | Absent |
| `unlimitedRecentStickers` (303) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showReactionsPanelInContextMenu` (304) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showViewsPanelInContextMenu` (305) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showHideMessageInContextMenu` (306) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showUserMessagesInContextMenu` (307) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showMessageDetailsInContextMenu` (308) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showRepeatMessageInContextMenu` (309) | Not established in public historical Android; docs/release may differ | Native context-menu presentation preference | Absent as configurable Ayu option |
| `showAddFilterInContextMenu` (310) | Not established in public historical Android; docs/release may differ | Native local rule editor route | Message excerpt route exists; exact selection/UI unverified |
| `showAttachButtonInMessageField` (311) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showCommandsButtonInMessageField` (312) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showEmojiButtonInMessageField` (313) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showMicrophoneButtonInMessageField` (314) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAutoDeleteButtonInMessageField` (315) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showGiftButtonInMessageField` (316) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAiEditorButtonInMessageField` (317) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showAttachPopup` (318) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showEmojiPopup` (319) | Not established in public historical Android; docs/release may differ | Native composer presentation preference | Existing upstream affordance; Ayu visibility setting absent |
| `showMyProfileInDrawer` (320) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showBotsInDrawer` (321) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNewGroupInDrawer` (322) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNewChannelInDrawer` (323) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showContactsInDrawer` (324) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showCallsInDrawer` (325) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showSavedMessagesInDrawer` (326) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showLReadToggleInDrawer` (327) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showSReadToggleInDrawer` (328) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showNightModeToggleInDrawer` (329) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showGhostToggleInDrawer` (330) | showGhostToggleInDrawer | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showStreamerToggleInDrawer` (331) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showGhostToggleInTray` (332) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `showStreamerToggleInTray` (333) | Not established in public historical Android; docs/release may differ | Native navigation/quick control; no desktop tray on iPhone | Absent as configurable Ayu option |
| `monoFont` (334) | Not established in public historical Android; docs/release may differ | Explicit local code-font option | Absent as Ayu customization |
| `hideNotificationCounters` (335) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideNotificationBadge` (336) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `hideAllChatsFolder` (337) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `channelBottomButton` (338) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `quickAdminShortcuts` (339) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `disableGreetingSticker` (340) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showPeerId` (341) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showMessageSeconds` (342) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `showMessageShot` (343) | Not established in public historical Android; docs/release may differ | User-requested native rendered export | Absent |
| `filterZalgo` (344) | Not established in public historical Android; docs/release may differ | Bounded Unicode presentation sanitizer | Absent |
| `stickerConfirmation` (345) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `gifConfirmation` (346) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `voiceConfirmation` (347) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `roundConfirmation` (348) | Not established in public historical Android; docs/release may differ | Explicit native pre-send confirmation | Absent as Ayu preference |
| `translationProvider` (349) | Not established in public historical Android; docs/release may differ | Upstream native translation; optional provider review | No new third-party provider imported |
| `adaptiveCoverColor` (350) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `improveLinkPreviews` (351) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `crashReporting` (352) | Not established in public historical Android; docs/release may differ | No added collector/upload toggle | Reviewed containment exists; runtime audit pending |
| `avatarCorners` (353) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `singleCornerRadius` (354) | Not established in public historical Android; docs/release may differ | Independent native presentation preference | Not ported as an Ayu setting in baseline |
| `streamerMode` (355) | Not established in public historical Android; docs/release may differ | Public capture-state redaction where supported | Absent; no perfect iOS screenshot-prevention guarantee |

## Historical Android configuration inventory

These **28 persisted boolean fields** come from
[`AyuConfig.java`](https://github.com/AyuGram/AyuGram4A/blob/7013145676d36d82ee13c02a89f72097b7490dcd/TMessagesProj/src/main/java/com/radolyn/ayugram/AyuConfig.java).
They are not a source listing for build 20261104. String preferences additionally
include deleted/edited markers, regex patterns and AyuSync URL/token; those are
covered explicitly above. No token or key value is copied here.

| Historical Android field | iOS equivalent / exclusion |
| --- | --- |
| `sendReadPackets` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendOnlinePackets` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendOfflinePacketAfterOnline` | Native typed account privacy policy; see the matching core feature and its limits |
| `sendUploadProgress` | Native typed account privacy policy; see the matching core feature and its limits |
| `useScheduledMessages` | Native typed account privacy policy; see the matching core feature and its limits |
| `saveDeletedMessages` | Account archive policy and capture pipeline; exact controls described above |
| `saveMessagesHistory` | Account archive policy and capture pipeline; exact controls described above |
| `saveMedia` | Independent encrypted completed-media retention, eligible cloud resources only |
| `saveMediaInPrivateChats` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPublicChannels` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPrivateChannels` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPublicGroups` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveMediaInPrivateGroups` | Account/chat capture exclusions and media policy; no exact per-chat-kind equivalent confirmed |
| `saveFormatting` | Flat text entities retained; unsupported structured rich content reported as missed |
| `saveReactions` | No general reaction-history archive parity established |
| `saveForBots` | Account-local excludeBots (inverted semantics) |
| `markReadAfterSend` | Native typed account privacy policy; see the matching core feature and its limits |
| `keepAliveService` | Android service/process UI is not an iOS feature; respect background execution |
| `disableAds` | Local display policy only; API terms §3.3 conflict; baseline absent |
| `localPremium` | Local cosmetics only; no server Premium entitlement |
| `regexFiltersEnabled` | Current baseline supports bounded literals, not Java regex compatibility |
| `regexFiltersInChats` | Current baseline supports bounded literals, not Java regex compatibility |
| `regexFiltersCaseInsensitive` | Current baseline supports bounded literals, not Java regex compatibility |
| `showGhostToggleInDrawer` | Native chat/settings quick control required; no Android drawer copied |
| `showKillButtonInDrawer` | Android service/process UI is not an iOS feature; respect background execution |
| `syncEnabled` | No unreviewed AyuSync transport; local-only storage |
| `useSecureConnection` | No unreviewed AyuSync transport; local-only storage |
| `WALMode` | Reviewed archive SQLCipher/WAL policy, not an unverified debug switch |

## Other documented features, platform boundaries and release delta

| Public reference behavior | Native equivalent / baseline status |
| --- | --- |
| Ayu settings/archive/filter/import deep links | Route only to real supported native screens; unavailable in baseline. No unauthenticated arbitrary remote import. |
| Open profile by numeric ID | Use existing peer/access-hash privacy rules; no invented server access; dedicated Ayu route absent. |
| Register URL scheme | Preserve iOS bundle/link configuration and user choice; desktop OS registration mechanism is inapplicable. |
| Search-bar last-seen widget | Local UI using already allowed status; no inferred/private presence access; absent. |
| Show content in app switcher | Native lifecycle redaction where public APIs allow it; Android FLAG_SECURE behavior is not portable. |
| Debug download buttons, alert reset and statistics | Developer-only diagnostics; not production feature parity or verification. |
| Android FCM/keepalive/vendor FPS behavior | Use valid Apple capabilities and iOS constraints; no Android identity spoofing, permanent daemon or promised refresh rate. |
| New November release capabilities | All19 delegated release groups and native proposals are individually listed in the [dated November audit](fork/REFERENCE-AUDIT-2026-11-05.md#november-2026-release-delta-and-native-equivalents). Current Android corresponding source remains unavailable. |

## Security, licensing and acceptance boundaries

Use the owner's API credentials from GitHub Secrets. Keep Telegram authentication,
encryption and transport architecture; no custom proxies, session uploads,
developer archive service or unverified runtime-code loader. Retain exact GPL
corresponding source/build scripts/notices and distinct app branding. See
[license and supply-chain audit](fork/LICENSE-AND-SUPPLY-CHAIN.md),
[security assessment](SECURITY_ASSESSMENT.md), [preservation limits](fork/PRESERVATION-STATUS.md),
[Windows installation](WINDOWS_INSTALL.md) and [SideStore evidence](SIDESTORE_INSTALL.md).

API terms §1.4 prohibits preventing self-destructing content from disappearing and
altering read/online/typing behavior; §3.3 addresses sponsored messages. An opt-in
switch does not establish a contractual exception. Keep timed/view-once retention
unsupported unless a real permissible scope is identified; explain conflicts for
experimental controls rather than falsely claiming compliance. Rooted/jailbroken
access, encryption bypass and server-side restriction bypass are excluded.

The independent archive survives normal cache clearing, not arbitrary parent
account-directory removal. Keychain/profile changes, account migration, physical
storage/locked-device behavior, interrupted upgrades and actual logged-in media
capture remain acceptance gates. Visual changes require recordings and frame
review; compilation and unit tests alone do not establish their appearance.
