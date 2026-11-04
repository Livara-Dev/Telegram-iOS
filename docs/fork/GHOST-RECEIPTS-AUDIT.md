# Ghost message and story receipt audit

Research date: 2026-11-04. Reviewed Telegram-iOS fork at `f295948ad9ba8fb1cb668dc0b645c486d1f2ec74`. This is source analysis only; no receipt implementation, server experiment, native runtime test, or benchmark was performed. No tracked source files were changed for this audit. Line ranges below refer to that checkout and may move after edits.

The subsequent story-only implementation is described in [GHOST-STORY-VIEWS.md](GHOST-STORY-VIEWS.md). The broader message-receipt design remains unimplemented.

## Recommendation

Implement **independent per-account story-view receipt suppression** as the next narrow Ghost increment. Cover both ordinary story reads and pinned/profile story view increments, including the durable operation consumer. Initially leave the local story read watermark unchanged when a view is suppressed. This preserves Telegram's canonical unread meaning and is consistent with the inspected current Desktop strategy, but the story ring/list can remain unread after viewing. Disclose that UI behavior.

Do not call this full Ghost Mode or message-read suppression. Message reads need a separate local/server read-state design before implementation: suppressing `messages.readHistory` alone does not cover the actual application, and existing validation intentionally retries when local read state is ahead of the server.

## Reference evidence and its limits

- Historical public Android: `/workspace/AyuGram4A`, `7013145676d36d82ee13c02a89f72097b7490dcd`, dated 2023-07-28. `TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java:307-396` intercepts typing, online status, and six read request types. Read suppression fabricates an affected-messages response with `pts=-1`, `pts_count=0`. This is **not** an implementation pattern to port to typed iOS RPCs.
- That hook covers `messages.readHistory`, `messages.readEncryptedHistory`, `messages.readDiscussion`, `messages.readMessageContents`, `channels.readHistory`, and `channels.readMessageContents`. It has no story receipt implementation in this inspected revision.
- Android `com/radolyn/ayugram/utils/AyuGhostUtils.java:21-62` explicitly distinguishes server reads and local reads. `AyuState.java:20-30` uses a static one-request read bypass; do not reproduce this global mutable bypass across iOS accounts. The old send hook at `ConnectionsManager.java:362-391` wraps send-message/media callbacks for optional read-after-send; this is not evidence of reliable current Android semantics.
- Current inspected Desktop: `/workspace/AyuGramDesktop`, `0f2a31bb420de98f331990432265b131b8d836b5`, dated 2026-10-20. `Telegram/SourceFiles/data/data_stories.cpp:1239-1266` returns before `bumpReadTill` when account story receipts are disabled. Additional guards exist at `1403-1429` (individual request), `1439-1460` (pending read dispatch), `1462-1480` (view increments), and `2314-2333` (shutdown behavior).
- Desktop message reads: `data/data_histories.cpp:696-720` cancels the timer and clears pending read watermarks when disabled. `apiwrap.cpp:1455-1533` separates local content state from network dispatch and permits mention/reaction passthrough only when not also unread media. `ayu/utils/telegram_helpers.cpp:413-467` provides explicit read and read-after-action behavior scoped to the session.
- Published semantics: `/workspace/AyuGramDocs/shared/ghost.md:7-63`, also retained live-page snapshot `/workspace/ayu-evidence/ghost.html`. Docs identify speech-to-text as an exception to message-read concealment, story reactions/replies as visible server-side interactions, and other clients as a source of online-state conflicts.
- `/workspace/ayu-evidence/evidence-manifest.json` and `/workspace/ayugram-expanded-audit.md:9-35` document that the only advertised Android branch is the historical `rewrite` branch, there are no reachable October 2026 Android commits/releases, and October build 20261005 is known through release notes. The October notes do not establish a new receipt implementation. Do not label historical code as current Android parity or inspect missing proprietary helpers as a shortcut.

## Current iOS outgoing paths

Paths in this section are relative to the iOS repository root.

| Path and source location | Outgoing operation / coupling |
| --- | --- |
| `submodules/TelegramCore/Sources/State/SynchronizePeerReadState.swift:250-358` | `messages.readEncryptedHistory` at 275, `channels.readHistory` at 293, `messages.readHistory` at 323. Optional `messages.markDialogUnread` follows. Cloud affected-message results feed real pts/ptsCount into the state manager. |
| `.../State/SynchronizePeerReadState.swift:363-463` | Push captures current state, checks it again after completion, and confirms the persisted operation. Validation is separate. Returning `.complete()` without deciding the persisted operation's fate can leave a restart loop. |
| `.../TelegramEngine/Messages/MarkAllChatsAsRead.swift:8-76` | Bulk action gets unread marks then directly calls channel/message `readHistory`, outside the normal peer synchronizer. |
| `.../TelegramEngine/Messages/ReplyThreadHistory.swift:365-530` | Thread interactive reads update local max/unread count, directly call `messages.readDiscussion` at 490 or `messages.readSavedHistory` at 481, and may revalidate using `getDiscussionMessage`. |
| `.../TelegramEngine/Messages/ApplyMaxReadIndexInteractively.swift:180-310` | Forum/mono-forum mark-read and unread toggles mutate thread counts and directly call `readDiscussion` at 222/258/300 or `readSavedHistory` at 226/262/304. |
| `.../State/ManagedSynchronizeConsumeMessageContentsOperations.swift:80-147` | Persistent content-consumption queue sends `messages.readMessageContents` at 114 or channel equivalent at 131; removes operation afterward. |
| `.../State/ManagedConsumePersonalMessagesActions.swift:250-388` | Mention consumption and reaction/poll read actions each have user/group and channel `readMessageContents` branches (256/291/325/368). Completion updates local consumed/pending flags and tags, so replacing network code without local finalization can strand state. |
| `.../State/ManagedSynchronizeMarkAllUnseenPersonalMessagesOperations.swift:120-170,290-330,390-430` | Bulk unseen-mention processing fetches unread mentions then calls content reads at 151/160; bulk reaction/poll actions call `messages.readReactions` at 309 and `messages.readPollVotes` at 406. These need explicitly defined scope rather than accidental inclusion. |
| `.../State/AccountViewTracker.swift:953-1010` | Visible live-location updates independently call user/group or channel `readMessageContents` at 975/979. |
| `.../State/AccountViewTracker.swift:706-750` | Channel message view refresh calls `messages.getMessagesViews(... increment: true)` at 731. This is an additional observable counter, distinct from a private read receipt. Current Desktop sets its increment from the message-read policy. |
| `.../TelegramEngine/Messages/MarkMessageContentAsConsumedInteractively.swift:6-150` | Marks media consumed, queues cloud content operations, and starts autoremove/autoclear countdowns. Secret chats enqueue typed `SecretChatOutgoingOperationContents.readMessagesContent` and advance secret-chat state. |
| `.../State/ManagedSecretChatOutgoingOperations.swift:150-170,1634-1658` | Sends the encrypted read-content service action, separate from `readEncryptedHistory`. Do not delete arbitrary secret-chat sequence entries or alter key/sequence handling. |
| `.../TelegramEngine/Messages/Stories.swift:2073-2115` | Pinned/profile case directly sends `stories.incrementStoryViews` at 2089. Ordinary case updates `Stories.PeerState.maxReadId` then enqueues `SynchronizeViewStoriesOperation` at 2105. |
| `.../State/ManagedSynchronizeViewStoriesOperations.swift:65-132` | Durable story queue consumer sends `stories.readStories` at 125 and removes the operation afterward. It runs independently of the visible story screen. |
| `.../SyncCore/SynchronizeViewStoriesOperation.swift:24-46` | Coalesces ordinary story receipts per peer to the greatest story id. A suppressed view must not silently remain here to dispatch later. |

Additional explicit interaction paths are outside an automatic-read suppression claim: `TelegramEngine/Messages/Stories.swift:3079` sends story reactions; `State/MessageReactions.swift:373` sends message reactions; `TelegramEngine/Messages/Transcription.swift:18-31` sends `messages.transcribeAudio`. Published docs warn that some such operations reveal reading through server behavior; suppressing a separate read request cannot undo that.

`messages.reportMessagesDelivery` is a delivery acknowledgement, not a read receipt. It must not be casually dropped under a broad name/string matcher. The same applies to unrelated `readFeaturedStickers` bookkeeping.

## Local unread state is not just an RPC side effect

- `TelegramEngine/Messages/ApplyMaxReadIndexInteractively.swift:7-112`: interactive entry point calls `transaction.applyInteractiveReadMaxIndex`; then updates forum state, starts secret-chat text countdowns, or notifies the state manager of cloud incoming reads. The message-id variant at 32-45 is also used by notification/Siri flows and deliberately excludes secret chats.
- `submodules/Postbox/Sources/Postbox.swift:2371-2403`: one interactive mark can also advance an associated migrated peer and other message namespaces. Only gating the nominal input peer misses those effects.
- `submodules/Postbox/Sources/MessageHistoryTable.swift:713-750`: updates the real read-state table and enqueues durable `.Push(... thenSync:)`. Local badge change and remote work are coupled here.
- `TelegramEngine/Messages/InstallInteractiveReadMessagesAction.swift:6-156`: an open chat installs a store hook that marks new incoming messages, mentions, reactions, and poll votes as read, including forum counts. This remains active for messages arriving while the chat is open.
- `State/ManagedSynchronizePeerReadStates.swift:31-117`: watches the persisted synchronization table and restarts unresolved operations on completion/error. A no-op RPC is not by itself a safe terminal state.
- `State/SynchronizePeerReadState.swift:181-235`: `localReadMarkIsAheadOfServer` deliberately retries validation when a local watermark exceeds a server watermark with remaining unread items.
- `State/AccountStateManagementUtils.swift:4836-4869`: server `.ResetReadState` replay preserves the ahead local marker and schedules synchronization. Merely confirming one suppressed push will not stop future validation from recreating work.
- Story replay is different: `State/AccountStateManagementUtils.swift:5613-5625` merges a genuine server story read using the maximum local/server watermark. Leave incoming updates from other sessions intact; Ghost controls this client's outgoing behavior, not other clients.

Existing UI debug flag `skipReadHistory` appears at `TelegramUI/Sources/ChatHistoryListNode.swift:1136,2790,2874,3627,5079,5097` and in story contexts. It is shared UI state, misses persistent workers/direct engine callers/extensions, and is not a substitute for an account-scoped product policy.

## Story entry points and warning coverage

`TelegramUI/Components/Stories/StoryContainerScreen/Sources/StoryChatContent.swift` has four `markAsSeen` implementations: 1132-1136 (subscription), 1433-1441 (single story with `readGlobally`), 1832-1836 (profile/list, pinned=true), and 3096-3100 (another subscription content context). All reach the engine entry point, making it the appropriate canonical suppression seam.

A warning placed only on a chat avatar is incomplete. `StoryContainerScreen` is constructed from:

- `TelegramUI/Sources/ChatController.swift:5712` and chat `openStories` around 9741;
- `TelegramUI/Sources/OpenResolvedUrl.swift:1620`;
- `TelegramUI/Sources/OpenChatMessage.swift:68`;
- `TelegramUI/Components/Stories/StoryContainerScreen/Sources/OpenStories.swift:38,205`;
- the same component's `StoryItemSetContainerComponent.swift:5704`;
- `Components/PeerInfo/PeerInfoVisualMediaPaneNode/Sources/PeerInfoStoryPaneNode.swift:1931`;
- `Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoScreen.swift:4568` and `PeerInfoScreenOpenStories.swift:48`.

A future warning needs a common opening coordinator (or complete caller coverage), before viewer construction/mark-as-seen; cancellation must not open the story. It is distinct from the receipt gate and should not be used as its enforcement layer.

## Proposed story policy contract

1. Store an explicit `suppressStoryViews` choice per account; default off for the new feature. Existing activity preferences use `LocalPrivacy/LocalGhostSettings.swift` and key `ayugram.ghost.activity.v1`. Adding a required Codable field without migration would turn existing v1 settings into unsupported records. Use an intentional compatible schema migration or a separate versioned story-policy record, with tests preserving the five existing activity choices.
2. At the engine producer, read actual account preferences in the Postbox transaction. When suppressed, return a typed local `.suppressed` decision: no `maxReadId` mutation, no queued ordinary receipt, and no pinned increment. Do not synthesize an API response/pts. Keep incoming Telegram updates untouched.
3. At the durable worker, independently read the same policy before request dispatch. A queued receipt that was created **before enabling** but has not been dispatched must be retired locally while suppression is active, not parked for automatic sending on disable. The explicit disposition/operation removal must be crash-safe and must not create a tight retry loop. Local state already advanced by the earlier allowed view may remain advanced; do not roll back canonical state blindly.
4. Policy changes and request dispatch need a well-defined ordering. Recheck the latest account policy at the actual dispatch boundary, including after delayed peer lookup. Once a request has been handed to MTProto, cancellation cannot prove it was not delivered. Do not promise to recall already-dispatched/in-flight receipts. Pending workers and policy observers must not be duplicated on rapid toggles.
5. On disabling suppression, **do not replay suppressed views**. Only a subsequent allowed viewing action may enqueue a normal receipt. Ordinary receipts use a maximum story id, so later allowed reads may imply a cumulative watermark covering earlier ids; test actual Telegram behavior before promising permanent concealment of earlier views.
6. Unknown/malformed/future story-policy data must be read-only and suppress covered outgoing receipt work; there must be no synthetic initial default-open emission. Preserve unrelated activity flags. Decide/document how a fresh record differs from an undecodable existing record.
7. The durable consumer starts in `TelegramCore/Sources/Account/Account.swift:1523`, outside UI. Any application extension/account-opening process that can run it must consult persisted per-account policy. A process-local cached Boolean is not sufficient. Test app/extension workers sharing the same account store and ensure a missing in-memory UI controller does not bypass suppression.
8. Expose “story-view receipts suppressed; stories may stay unread locally.” Still warn that reactions, replies, and another connected client can expose viewing/activity. This feature does not suppress media downloads or create perfect invisibility.

## Tests required before calling it working

**Automated engine/worker tests using the production dispatch seam and a request recorder:**

1. Ordinary story suppression: zero `readStories`, unchanged local `maxReadId`, zero queued view operation. Repeat and switch story ids/peers.
2. Pinned/profile suppression: zero `incrementStoryViews`; permitted case sends the correct peer/id once through the actual engine branch.
3. Preexisting durable queue: enable before consumer starts; the operation retires with no RPC and no busy loop. Restart after the decision/removal boundary; it must not be sent later.
4. Toggle during delayed peer resolution / before dispatch: latest policy wins. Separate a request already dispatched before toggle, reporting it as in-flight rather than claiming suppression.
5. Disable after suppressed views: no replay; a fresh allowed view follows baseline behavior. Test max-id coalescing and lower-id revisits.
6. Unknown/future/corrupt settings: fail closed, preserve bytes/choices, no UI-default-open race. Fresh defaults preserve current behavior. Migration preserves prior Ghost activity flags.
7. Account isolation: same peer/story id in accounts A and B, A suppressed/B allowed; no cross-account pending operations, settings, or ephemeral overrides.
8. Crash/restart, network failure/retry, reconnect, background resume, and two store consumers: no delayed suppressed request, no queue leak, no spin. Distinguish SQLite transactional tests from a genuine second-process app-extension test.
9. Genuine server read update from another client advances local unread state normally while suppression remains active; no outgoing echo is manufactured.
10. Warning coordinator: every supported opening route presents before viewer creation; cancel/tap outside performs no open and no receipt; continue respects the engine policy.

**Native/manual validation with two controlled Telegram accounts and another client:** open ordinary/profile/pinned stories, navigate forward/backward, background/reconnect, rapidly toggle, send story reply/reaction, and read from a second client. Compare viewer lists and unread rings. Verify iPhone/simulator UI as available; source tests alone do not verify server visibility.

**Before adding message-read suppression:** test personal/group/channel/migrated chats, forum and mono-forum threads, bulk mark read, notification reply/read and Siri, incoming messages while chat open, mention/reaction/poll/live-location paths, content playback, empty/deleted/holey histories, and concurrent server resets. Assert both absence of prohibited requests and accurate local unread counts, pending-action completion, zero retry loops, and no later accidental upload of suppressed watermarks. Keep secret chats and disappearing/view-once media explicitly unsupported until their countdown/sequence invariants have dedicated tests. Optional read-on-interaction should be an explicit account+peer+namespace/thread+max-id authorization, not a global next-packet bypass.

## Message-read design blocker

There is no safe one-line message-RPC gate that preserves the current unread implementation. Two defensible approaches require deliberate implementation:

- Preserve canonical server read state and introduce a separate, persistent local-view watermark used for local unread presentation; or
- Keep local interactive advancement but represent suppressed remote watermarks explicitly, teaching the synchronization/replay/validation paths how to preserve local state without retrying or later uploading suppressed reads.

Either approach must cover migrated peers, forum counts, pending mentions/content, extensions, and real server updates. Do not reuse the deleted-message archive as the authoritative read-state store. Do not fabricate Telegram success responses, modify authentication/MTProto transport, or silently break secret-chat expiry to imitate the historical Android hook.
