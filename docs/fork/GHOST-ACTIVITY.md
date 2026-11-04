# Experimental outgoing activity controls

Status: source implementation and tests are being prepared. This document does
not record a passing build, a completed settings UI, or verified behavior in a
running Telegram application. Results must identify the tested commit and remain
separate from the archive module's earlier test results.

This increment covers five outgoing activity categories, with settings stored
independently in each account's Postbox. The master switch defaults **off**. Each
category defaults to suppression when the user enables the master switch, and
can be configured independently:

| Category | Existing Telegram-iOS activity cases |
| --- | --- |
| Typing | `typingText`, including the existing Secret Chat typing request |
| Sticker selection | `choosingSticker` |
| Recording | `recordingVoice`, `recordingInstantVideo` |
| Upload progress | `uploadingFile`, `uploadingPhoto`, `uploadingVideo`, `uploadingInstantVideo` |
| Gameplay | `playingGame` |

The controls suppress activity indicators, not message sending, recording, media
upload, or gameplay itself. Calls and `speakingInGroupCall`, emoji interactions
and their seen actions, cancellation actions, and the explicit action that stops
an incoming live draft retain their existing behavior. These preserved actions
can disclose activity. There is no claim of full Ghost Mode or invisibility.

## Reference evidence and differences

The reference implementations were inspected, not executed. Source dates matter:
the official public Android `rewrite` branch at
[`7013145676d36d82ee13c02a89f72097b7490dcd`](https://github.com/AyuGram/AyuGram4A/tree/7013145676d36d82ee13c02a89f72097b7490dcd)
is from July 2023; Desktop at
[`0f2a31bb420de98f331990432265b131b8d836b5`](https://github.com/AyuGram/AyuGramDesktop/tree/0f2a31bb420de98f331990432265b131b8d836b5)
is from October 20, 2026. The official documentation snapshot is
`42f5f32c6b05aab4a69a5f8d0bf7652e1cb11a9b` and was retrieved on
November 4, 2026. See the [expanded audit](EXPANDED-REFERENCE.md) for provenance
and the October 2026 Android release-note improvements. Those notes do not
provide corresponding public Android source or establish new activity-control
semantics; the older code must not be represented as the October implementation.

| Reference | Observed implementation | Native decision |
| --- | --- | --- |
| Android `AyuConfig.java:69–78,125–138` | One shared `ayuconfig` preferences file; `sendUploadProgress` controls typing/upload together. The aggregate Ghost toggle also changes read and online behavior. | Store an account-local policy with five explicit categories. The activity switch does not change read or presence behavior. |
| Android `org/telegram/tgnet/ConnectionsManager.java:302–350` | A generic request hook drops both `messages.setTyping` and `messages.setEncryptedTyping` without invoking their completion callback. It also rewrites presence requests and fabricates a read-response object. | Gate the existing typed activity operation. Preserve generic networking, authentication, encryption, response types and protocol acknowledgements. No fabricated server responses. |
| Desktop `api/api_send_progress.cpp:114–162` | The single `sendUploadProgress` gate precedes an action switch covering typing, recording, uploads, location/contact selection, gameplay, group-call speaking and sticker selection. | The narrower native list above is intentional. Preserve calls and explicit interactions; do not silently inherit Desktop's broader suppression. |
| Desktop `ayu/ayu_settings.h:85–177,625,715` | Distinct account Ghost settings can be selected, but global Ghost settings are the default. | Each iOS account owns its policy. No global fallback that silently changes another account. |
| Desktop `api/api_updates.cpp:1002` and `ayu/ayu_worker.cpp:33–86` | Presence gate plus a worker that considers accounts for offline requests every three seconds. | Presence and automatic offline are deferred. A repeating Desktop timer cannot guarantee execution while iOS is suspended. |
| Official [Ghost documentation](https://docs.ayugram.one/shared/ghost/) | Lists typing/sticker/game suppression; warns that sending can reveal online presence, reactions/replies can mark stories viewed, automatic offline can oscillate against another client, and scheduled sending is unreliable on poor networks. | Expose only implemented protections. Do not describe activity suppression or delayed sending as a guarantee against server-derived visibility. |

Android paths above abbreviate `TMessagesProj/src/main/java/`; its configuration
class is under `com/radolyn/ayugram/`. Desktop paths abbreviate
`Telegram/SourceFiles/`. The detailed [Android](AYUGRAM-REFERENCE.md) and
[Desktop](DESKTOP-REFERENCE.md) audits record the other Ghost paths.

No Android Java, Desktop C++, proprietary exteraGram component, or missing
Android proprietary helper is copied or translated into this increment. Desktop
is GPLv3-or-later with an OpenSSL exception; its code is not being imported under
an assumed license compatibility. See the
[license and API-policy review](LICENSE-AND-SUPPLY-CHAIN.md): the published
Telegram API terms explicitly conflict with suppressing typing/read/presence
status. Local feature flags and source availability do not resolve that service
policy conflict or establish distribution clearance.

## Native integration boundary

`LocalPrivacy/LocalGhostSettings.swift` and `LocalGhostContext.swift` live in
`submodules/TelegramCore/Sources/`. They persist a versioned preference in the
account's existing Postbox; they hold no credentials or new network endpoint.
Missing preferences mean the master switch is off. Malformed or newer,
unsupported preferences block the five covered activity categories and remain
read-only, rather than overwriting a potentially stricter policy with defaults.
Preserved call/emoji/cancellation behavior stays outside this fallback.

`State/ManagedLocalInputActivities.swift` observes the account preference along
with outgoing activities. It waits for the stored preference before dispatch,
serializes activity/policy changes, and disposes work for newly suppressed
categories. Suppressing the first activity in a space must not promote a lower
priority activity from the same list. The operation checks the stored policy
again inside its Postbox transaction before preparing the typed request.

The production sender maps that request to the original Telegram
`messages.setTyping` or `messages.setEncryptedTyping` API. Existing self-chat,
peer-presence, channel and Secret Chat eligibility checks remain in place. The
existing `State/TypingDraftStop.swift` action is separate and remains unchanged.
This change adds no request transport, authentication proxy, telemetry producer,
archive upload, dependency or cross-account preference store.

These activities are transient work, not a new persistent operation log. Enabling
suppression disposes queued/current work where the existing signal permits it;
it cannot recall a request that has already crossed the dispatch boundary or
retract an indicator already received by Telegram. A toggle cannot serve as an
atomic revocation of previous network activity. Remote indicators can persist
until Telegram expires them. Server behavior and another active client can
continue to reveal account activity.

## Verification contract

The intended automated integration tests use real Postbox preferences and the
actual activity worker with an injected **typed request sender**. They can check
which operation reaches the send boundary and whether it is disposed; they do
not contact Telegram or prove what another user sees. Relevant cases include:

- Default behavior and every category/variant, including Secret Chat typing,
  thread identity, upload progress and preserved call/emoji/cancellation actions.
- Per-account persistence and isolation, unknown/malformed settings, and no
  activity sent before the stored enabled policy loads.
- Enabling suppression during activity, queued policy changes, disposal,
  re-enabling categories and unchanged activity priority/peer eligibility.
- No fabricated protocol response, new server connection or mutation of
  authentication, session keys or generic networking.

Passing those tests would establish this local boundary's behavior at the tested
commit. A simulator compilation establishes only compilation. Neither result
establishes a working installed application or effective remote privacy. Full
application builds, native settings/navigation tests, account switching,
foreground/background and reconnect tests, and controlled two-client checks of
visible indicators remain separate gates. UI appearance requires recorded
simulator frames under this repository's visual-review rules; iPhone behavior,
latency and battery usage require device measurements.

## Boundaries for later Ghost increments

**Presence:** `State/ManagedAccountPresence.swift` is the current sender of
`account.updateStatus`. Add a separate per-account policy there rather than
changing `Account.shouldKeepOnlinePresence`. The original signal also controls
`messages.receivedQueue(maxQts:)` in `AccountStateManager.swift` and
`Network.isUserOnline` transport keepalive timing in `Account.swift`. Suppressing
those protocol/transport consumers would change unrelated Telegram behavior.

The presence worker starts with `wasOnline = false`; merely mapping foreground
presence to `false` does not send an initial offline request. A later implementation
must handle enable/start/reconnect explicitly, invalidate the existing online
refresh timer, and handle an already-dispatched online request without claiming
it was recalled. Successful message-delivery callbacks in
`PendingMessageManager.swift` are a candidate account-local trigger for a bounded
offline attempt. An empty pending-message queue also occurs after cancellation
or failure and is not evidence of delivery. The existing per-peer delivery-event
subscriptions do not provide a complete account-wide trigger. Debouncing,
bounded retries, suspension and another client overriding status require tests;
continuous three-second polling is not an iOS background execution guarantee.

**Read receipts:** `ManagedSynchronizePeerReadStates.swift` retries an operation
after either completion or failure while its persisted read operation remains.
Returning an empty completed signal to suppress a request without resolving
that operation can produce an immediate loop. In `SynchronizePeerReadState.swift`,
validation also returns retry when the local read marker is ahead of the server
and the server still reports unread messages, precisely the state a local-only
read can create. A future design needs an explicit local acknowledgement or
durable deferred-policy model, with retry/convergence tests. It must not invent
successful Telegram responses or corrupt pts/qts state.

Read coverage also includes direct `readDiscussion` calls in
`ApplyMaxReadIndexInteractively.swift` and `ReplyThreadHistory.swift`, bulk
history reads in `MarkAllChatsAsRead.swift`, and content/mention/Secret Chat read
paths. A gate in the read-state worker alone is incomplete. Story handling has
both queued `stories.readStories` operations and direct pinned-story
`stories.incrementStoryViews` calls in `Stories.swift`; replies and reactions
can still disclose a view. Read-on-interaction, story warnings and scheduled
sending each need their own tested behavior and remain outside this increment.
