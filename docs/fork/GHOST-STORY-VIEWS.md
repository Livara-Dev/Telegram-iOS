# Experimental story-view receipt suppression

This increment adds an independent, default-off, per-account story-view receipt
setting. It is source-implemented and has authored integration tests; native test
results and full-application behavior are not yet verified. It is not full Ghost
Mode. Message-read receipts, online status, story reactions/replies, downloads,
other clients and server-derived activity remain outside its protection.

## Reference and scope

The [receipt-path audit](GHOST-RECEIPTS-AUDIT.md) compares the public Android
snapshot, October 2026 Desktop source and published AyuGram behavior. The available
Android implementation is from July 2023; October Android release notes do not
supply current story-receipt source. No Android, proprietary exteraGram or Desktop
implementation was translated or copied.

The inspected Desktop strategy skips its local story read marker as well as
outgoing receipts. This native implementation follows that behavior within the
iOS engine: a suppressed ordinary view does not advance `Stories.PeerState`,
inject a local read update, or append a synchronization operation. A suppressed
pinned/profile view does not increment its server view counter. A story can
therefore remain unread in the list after being opened.

Both paths enter through the existing `_internal_markStoryAsSeen` facade. The
production implementation uses the same typed producer and persistent worker
exercised by the integration tests. The final transport still calls Telegram's
existing `stories.readStories` or `stories.incrementStoryViews`; authentication,
MTProto, encryption and incoming server updates are unchanged.

## Persistent settings and queued work

`Account.localStoryPrivacy` stores a separate `ayugram.ghost.story-views.v1`
record in that account's Postbox. Existing activity-indicator preferences and
the chat's activity quick toggle keep their original meaning. The native Ghost
settings screen exposes a separate story switch and explains this independence.
Malformed, oversized or newer settings are preserved read-only and block the
covered story receipts. A missing record preserves upstream behavior.

Enabling suppression advances a monotonic generation in the same preferences
transaction. Every new queued ordinary receipt records its generation; legacy
queued operations decode as generation zero. The worker retires receipts whose
generation no longer matches. Thus enabling and then disabling suppression
before the worker runs does not release old queued receipts. A suppressed view
is never queued for later transmission. Generation overflow is rejected without
resetting or replacing the stored policy.

The durable consumer checks current preferences and operation identity. Its
coalescing logic retains the greatest story ID within a generation, replaces old
generations, and does not restart unchanged work on unrelated queue updates.
Completion removes only the original matching operation. Stopping a worker is
terminal for that instance, including callbacks already waiting on its queue.
The dispatch policy is checked again after queued delivery, and policy changes
cancel an in-flight subscription on a best-effort basis.

These boundaries cannot recall bytes already handed to Telegram. They are not
a transaction shared between Postbox and the server. A genuine read update from
another client still updates local story state. Later allowed reads use a
cumulative maximum ID and can mark earlier stories read on Telegram; disabling
suppression is not a promise that all previous views remain permanently hidden.
Downgrading to an older binary without this policy does not preserve protection.

## Verification and limitations

The 26 authored story-specific tests use the real on-disk Postbox schema, producer, synchronization
worker and replay path. Only the final transport is replaced by a typed recorder.
They cover enabled/disabled defaults, ordinary/pinned behavior, local read state,
per-account settings, legacy operations, generation persistence, rapid toggles,
queued work, malformed settings, coalescing, cancellation and server-read replay.
Reopening a database is not a physical crash or a second extension process test.

The following remain required before a working-application claim:

- Passing native tests for the exact published commit and a full app build.
- Actual ordinary/profile/pinned story views using controlled Telegram accounts,
  including reconnect, account switching, backgrounding and another client.
- Viewer-list and unread-state comparisons, plus reply/reaction behavior.
- Recorded native settings and story navigation, accessibility and device checks.
- Cross-process app/extension behavior and abrupt termination during queued work.

A warning before every story-opening route is not implemented. The audit lists
multiple constructors requiring a common opening gate; the receipt policy is
not dependent on a warning or a single avatar menu. No live visibility result,
perfect invisibility or API-policy compliance is claimed. The existing
[Telegram API-terms conflict](LICENSE-AND-SUPPLY-CHAIN.md#separate-api-contract-conflict)
remains unresolved. Existing [upstream telemetry](TELEMETRY-REVIEW.md) also remains
outside this narrow change.
