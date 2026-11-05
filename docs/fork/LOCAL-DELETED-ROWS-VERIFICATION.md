# Local deleted-message rows: candidate and verification boundary

This increment implements a distinct `ChatHistoryEntry.LocalArchiveEntry` and
`ChatLocalArchiveItem`, rather than inserting archived data into Telegram's
`Message`, `EngineMessage`, Postbox history, network updates or media cache.
Native compilation, the newly authored tests, and application rendering have
not yet been verified for this increment. It must remain **candidate / unverified**
in the feature matrix until the corresponding qualification evidence exists.

The encrypted query is tied to the current account, a proven ordinary cloud
peer and the loaded chat viewport plus Postbox's prefetch window. It returns
at most 40 distinct deleted IDs with 2,048-code-point excerpts. An additional
row probe explicitly marks more available history. SQL excludes configured
authors before returning bodies; an unknown author is hidden when sender
exclusions are configured. The existing message index and an approximately
200k-VM-instruction progress guard bound pathological version histories. A
work-budget failure returns no partial page. Full versions and media remain in
the independent archive, reachable by tapping the local card.

The [executed host SQL evidence](evidence/deleted-row-sql-host.json) records the
exact query, source and vendored-engine hashes, compiler flags, synthetic cases,
planner output and interruption recovery. It ran the vendored SQLite 3.53.4
amalgamation on Linux **without codec or Apple flags**. It does not establish
encryption, Swift compilation, native integration, scrolling or battery results.

The rows deduplicate against live messages and album members. They have no
reply, forward, send, pin, selection, media-download, view-count, translation or
read-receipt action. Their only callback opens the existing read-only archive
timeline in the current chat/account. Canonical pagination, adjacent-message
navigation and unread/restoration fallbacks skip the local entry type. Local
cards have no shared date header or calendar action; both the original sent
time and locally observed deletion time are shown inside the card. This can
split a day's floating date-header run and still requires visual review.

All retained transition and item values share a revocable body token. Lock,
background, account/chat visibility changes, selection, policy changes and
committed archive changes revoke old bodies and synchronously clear rendered
labels, accessibility text and tap callbacks. Finite retention has one active
chat-level timer at the earliest age cutoff; permanent retention has no timer.
The timer is cancelled on exit/disposal, uses overflow-safe arithmetic, and
fails closed if the clock goes backwards. Very large finite policies use one
annual checkpoint to avoid overflowing DispatchTime's nanoseconds. This is
bounded plaintext lifetime, not a claim of forensic Swift-string zeroization.

Inline rows are excluded from secret chats, scheduled/quick-reply/custom views,
previews, copy-protected peers, forums/monoforums and all reply/thread views.
The existing capture engine excludes timed/view-once/expired/protected content;
this increment does not add a way around those controls. Inline archive rows
are disabled while local message filtering is enabled, so a separate local
row cannot bypass a live-message filter. Explicit archive browsing remains
available. This limitation must be reported until an archive-aware filter
projection has its own tests. There are no inline media thumbnails or playback
resources: the card reports saved/unavailable media counts and opens the
protected archive viewer. No plaintext media file is created.

New native tests cover the encrypted query's bounds, isolation, retention,
latest-version selection and interrupt recovery; production loader invalidation
and timers; distinct-entry identity/dedup/pagination; and the **exact production
UIKit renderer** through a source filegroup in the existing projection target.
A real Postbox interactive-delete/edit fixture feeds the durable outbox and
SQLCipher, clears media through `MediaBox.removeCachedResources`, reopens
Postbox and the archive with capture paused, and queries the same loader. It
also verifies that browsing an absent archive does not create a database or
request an encryption key from the provider. These are authored tests, not yet passing-test claims. Sender
deletion replay and other message types retain their separate existing tests.

## Required application and frame review

Before calling the visual integration verified, record these expected results
on a fresh disposable CI simulator using the production chat renderer and a
separate authorized test account for live Telegram delivery. Never seed fake
authenticated state or reuse a user's account data to manufacture this proof.

1. In a normal private chat, group and non-forum channel, receive a message,
   edit it, then delete it from the sender client. The live message disappears
   and exactly one clearly marked local card occupies its chronological place.
   Unchanged neighboring bubbles retain their positions and identities. Tapping
   the card opens saved original/edited/deleted versions in sequence.
2. Repeat with an available cached photo, video, GIF, voice note and document,
   plus an incomplete download. The card reports captured versus unavailable
   media accurately. Cache cleanup and app restart retain captured bytes; no
   card pretends to have recovered unseen or uncaptured bytes.
3. Verify the local card at the earliest/latest viewport edges, in an otherwise
   empty chat, during pagination and after an archive contains more than 40
   matching IDs. Local IDs must not cause remote history loads or read/view
   actions. Canonical date headers and audio next/previous navigation still work.
4. Record light/dark themes, long RTL/emoji captions, media-only cards, rotation
   and narrow widths. Text must fit the card, metadata must remain readable,
   and no date-header overlap, flipped card, clipping or blank row is acceptable.
5. Record account switching, passcode/device lock, background/foreground,
   retention expiration, archive deletion and filter/selection activation.
   Protected body text, accessibility text and tap actions disappear together;
   no old-account or departing-transition copy may remain visible.

Use `xcrun simctl io recordVideo` at real speed with still time before and after
each action. Read timestamps with `ffprobe`, generate indexed/timestamped
contact sheets with `tools/animation_sheet.py`, inspect every relevant frame,
and cite frame/time ranges for layout and motion observations as required by
`AGENTS.md`. No such recording has yet been made for these new local rows.
UIKit layout tests alone do not satisfy this requirement. Physical iPhone and
SideStore installation remain separate evidence requirements.
