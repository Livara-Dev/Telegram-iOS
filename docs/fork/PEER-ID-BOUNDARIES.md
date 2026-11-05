# Untrusted local peer identifiers

Telegram's `PeerId(Int64)` initializer enforces invariants with assertions. An
arbitrary signed 64-bit database or preference value is not necessarily a valid
encoded Telegram peer. Raw conversion before validation can crash a native
settings/archive screen even though SQL storage and Codable decoding succeeded.

`TelegramCore/Sources/Utils/SafePeerId.swift` checks the encoded identifier high
bits before invoking that initializer. It preserves all safely decodable
namespaces, signed legacy Secret Chat identifiers and the legacy empty-peer
sentinel. It rejects impossible high bits, negative raw encodings and the
noncanonical empty-peer alias. This is a shared representation check; each
feature separately defines which peer/message namespaces it may use.

Filter scopes and both archive exclusion sets use this boundary. Existing
malformed/future policies remain read-only with their original preference bytes;
they are not rewritten with a guessed peer or silently dropped exclusion.
Archive exclusions are limited to 4,096 entries per set and preference documents
to 128 KiB. Invalid exclusion mutations leave a valid policy unchanged. Existing
fixed-width Postbox serialization and valid preference schemas remain intact.

Generic `LocalMessageArchive` databases and encrypted imports retain their
existing `Int64` identity semantics. `localArchiveDisplayPeerId` and
`localArchiveDisplayMessageId` return optional native lookup identities. They
allow only positive ordinary cloud peers/messages. A row with unknown or invalid
metadata still opens its raw, local archive timeline and retained media; its
chat name uses the existing unavailable label. It cannot construct a live
Postbox message lookup. Name resolution uses local engine data, without an RPC.

The durable capture outbox also validates its encoded peer before constructing
`MessageId`. A malformed item retains the original raw value when encoded,
cannot form an archive capture, remains unacknowledged and yields the existing
invalid-pending-capture status. It is never silently rewritten into a valid
message identity or discarded.

Ten added native methods cover shared decoding, archive-display eligibility,
unknown generic records, maximal policies and oversized preference entries;
malformed persisted exclusions through every coordinator mutation and restart;
an actual malformed outbox decoder/worker/restart scenario; and the native
archive reference used by live-version lookup. The test fixture first proves a
valid decoded capture succeeds before corrupting only its peer field. Maximum
identity fixtures now use real encoded 56-bit peer identifiers instead of
arbitrary `Int64.max` values.

These native tests are authored and await macOS execution. Linux source guards
do not establish iOS compilation, rendered UI behavior or device verification.
