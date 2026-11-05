# Inline saved-edit history

This increment connects an existing ordinary text/caption bubble's date row to
the encrypted archive. It is source-wired and has native tests authored; it is
not visually qualified or physically verified until the latest application is
built, exercised and recorded.

An actual `EditedMessageAttribute`, including a hidden server edited label,
makes a currently visible bubble eligible for a local existence lookup. The
account-owned coordinator reads at most one snapshot through its indexed
message timeline query. A successful saved-edit result shows the account's
custom Edited label and a history arrow. Tapping the date opens the existing
native, message-specific chronological archive screen. A normal Telegram edited
label remains when there is no saved prior version. Nothing from the archive is
turned into an `EngineMessage`, Postbox row, outgoing payload or server request.

The read lease is limited to foreground visibility and uses the existing-only
database-open path when capture is paused. It never enables capture or creates
an absent archive/key. Cell identity includes its account object, message id
and stable version. Reuse, background, invisibility and locked/unavailable
storage cancel lookups, clear local presentation and reject late deliveries.
Ready events are debounced for 250 ms. Unedited incoming messages perform no
lookups. Writes invalidate positive proof because quota or age pruning can
remove a previously saved version. An account-local metadata revision also
invalidates successful snapshot deletion, import, purge and actual pruning
during ordinary reads. The store reports a metadata-only callback after either
ordinary or transfer transaction commits with changed `sqlite3_total_changes64`;
the coordinator binds it serially to the active account/store/epoch. This also
covers a timer boundary crossed inside a read after its pre-read retention
check. No-op pruning and rolled-back transactions do not notify, so querying a
marker cannot create an invalidation loop. The observer is per open store, not
a cross-process database notification service; production supplementary
processes must continue to leave this archive worker to the main account owner.
Media bytes are never queried by this marker.
This is a bounded design, not a measured scrolling/battery benchmark.

Current eligibility rejects Secret Chats, non-cloud/scheduled messages, timers,
view-once/expired content, copy-protected messages/chats, rich content, service
actions, imported-message date tooltips, previews, hidden filtered messages and
album entries. Current chat/sender/bot exclusions also suppress the local link.
The first increment covers the text-bubble date row, including supported
captions. Media-only status nodes and album history are pending a dedicated
multi-message action. Their absence is not a platform impossibility.

Deleted bubbles are also pending. A safe follow-up needs a separate local
history entry and renderer with its own stable identity and explicit archive
actions. It must not be a fabricated canonical message. The deleted row can
offer local timeline/media/delete actions only; it must be excluded from read
advancement, reply/forward/pin/send, Telegram selection, notification counts and
canonical media indexing. The history list's numerous exhaustive message
switches, scroll anchors, grouping and rotated-list backends must be audited
before adding that entry. The current native chat archive/search/profile routes
remain available while this separate projection is implemented.

## Required native/visual qualification

The Core native suite contains source-injected lifecycle/debounce/pruning tests
and a production server replacement → durable Postbox outbox → SQLCipher →
inline-loader scenario, including paused capture, lock/unlock and deletion of
the last saved version. A second real integration test advances an injected
archive clock between the pre-read check and the actual media read and proves
its internal age pruning clears an active marker. Store tests cover commit
notification order, no-op reads/duplicate writes, rollback, internal media-read
pruning, account scoping, observer removal and close. Existing transfer tests
also assert committed import notification, no-op duplicate import and no
notification after conflict or cancellation following a real BLOB write. These
are local integration tests, not live Telegram RPC or phone evidence. The
SettingsUI suite exercises the actual status node's independent date-text
accessibility action and clears it on reuse without absorbing reaction buttons
or replacing an imported-message tooltip.

Before recording, the expected full-app behavior is:

1. In a normal chat with an eligible saved edit, the date row gains the custom
   label/history arrow after the asynchronous lookup. Its timestamp, send/read
   checks and reaction row retain their native layout. A missing archive/version
   keeps Telegram's existing appearance. No loading spinner or empty row is
   inserted into history.
2. Tap and VoiceOver's “Open local edit history” action open the correct account,
   peer and message timeline. Going back preserves the chat's scroll position.
3. Record incoming/outgoing, single-line/multiline, RTL, light/dark, largest text,
   a 32-grapheme custom label, a photo caption and a reaction row. There must be
   no clipped date/checkmark, overlapping text, hidden reaction action or
   unintentional gap. A long label may use the existing date text truncation.
4. Record scrolling a reused cell into/out of visibility, account switching,
   background/foreground and lock/unlock. A local marker must never transfer to
   another message/account. Normal Telegram edited state remains independent.
5. Remove the last saved version and enforce a quota/retention prune, return to
   the conversation and verify the local marker disappears. Timer-protected,
   copy-protected, imported, album and preview cases must not gain this action.

Use `simctl io recordVideo` on an owned disposable simulator, then
`tools/animation_sheet.py` with original `ffprobe` timestamps as required by
`AGENTS.md`. Inspect and cite original frame indices/times before claiming
layout/interaction is visually verified. No such recording has been produced
for this increment in the Linux workspace.
