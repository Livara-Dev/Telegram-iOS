# Local folder colors and tags

This is candidate source awaiting the current native tests, full application
compilation and recorded visual review. No physical-device verification is
claimed.

`Custome options → Appearance & Themes → Themes, icons & text size` opens a
native menu with the existing Telegram appearance screen and Local Folder Colors
& Tags. `Custome options → Chat Interface → Chat folders` opens the local folder
list. Its Manage Telegram Folders row keeps the upstream create/rename/reorder
screen available.

Each account has a separate Postbox preference document. An override has an
optional fixed palette color and an optional tag, bounded to 20 graphemes and
128 UTF-8 bytes. Input is bounded before Unicode segmentation; control and
directional-formatting characters are removed while emoji joiners are retained.
The document permits at most 100 overrides and 32 KiB encoded data. Incompatible
versions and value-invalid documents in the supported shape remain read-only and byte-identical; display falls back to
native appearance.

The on-disk schema is version 2: a sorted, homogeneous array of keyed records
containing an Int32 folder ID, optional Int32 color and string tag. The in-memory
API still uses a dictionary. Postbox's adapted Codable implementation does not
support synthesized raw-enum single-value containers, mixed integer/value
dictionary arrays, or enumerating dynamic string dictionary keys. Version 1 was
an unreleased source candidate that could not encode a nonempty folder document;
its bytes and future versions remain read-only, with no attempted rewrite.
This is not a complete validator for arbitrary corrupt Postbox binary shapes.
Within schema 2, replacing the records array with a nonempty primitive array or
keyed object still reaches an inherited assertion in the adapted decoder.
Normal application writes never produce those shapes; the repair does not
change Postbox's global codec or claim that malformed binary data is safe.

The tab container and native folder settings list project their canonical title
once. A local tag is appended after the existing title, retaining its existing
entity ranges and animation setting. A color changes the tab text and the
native folder list's existing color indicator. Folder IDs, order, membership,
server names, server colors and Premium checks remain canonical. The code never
calls Telegram's display-tags API or grants a server entitlement. Other clients
do not receive these overlays.

Tab containers bind an immutable account context, initialize with empty local
preferences, observe only that account and dispose their observation with the
container. A late callback cannot mutate a new account's surface. The local
editor compares the exact canonical filter captured by its visible list row in
the same Postbox transaction as saving. Removal, rename or a changed canonical
filter rejects a stale draft, including when UI callbacks have not run yet. An
editor that observes removal or a changed filter stays invalidated even if a
later queued update has the original ID again. It tells the user to reopen the
editor. The three production canonical
filter transitions prune metadata in the same transaction as folder removal,
including remote reconciliation; a reused folder ID cannot inherit the old
overlay. Renaming or reordering an existing folder retains its saved overlay.
Telegram exposes no immutable folder-creation identity: if a folder is removed
and recreated with an identical ID and byte-identical canonical fields without
the open editor observing the disappearance, these snapshots cannot distinguish
that replacement. This bounded cosmetic safeguard is not a durable generation
tracking system. Removing an individual override or
resetting all local overlays does not delete a native folder or change another
account, archive data or unrelated appearance preferences.

Twenty-one authored native tests use real encrypted Postbox fixtures and the same
production projection used by tabs/list. They cover persistence/reopen,
observation, account isolation, Unicode/document bounds, unsupported documents,
stale-editor writes, reset and local/server removal/recreation. An additional
race test rejects old canonical snapshots after rename and remove/recreate with
the same ID and different fields, then accepts the replacement's own snapshot.
A request invariance test compares canonical
preference bytes, folder order and actual serialized
`messages.updateDialogFilter` request bytes before/after local changes,
including a shared folder. These tests require a macOS qualification run; source
review and the Linux checks do not establish their result.
The codec regressions exercise every raw color, homogeneous records, duplicate
IDs, incompatible shapes, an explicitly stored empty document, restoration of
the last override and on-disk reopen using the real Postbox codec.

Recorded visual review must cover selected/unselected tabs, folder reordering,
long and emoji titles, RTL text, light/dark appearance, save/reset, switching
accounts, folder removal/recreation and native Premium folder limits. Expected
frames show one appended tag per title, consistent color/text/accessibility
labels, no clipping or duplicate title during layout, and a native layout after
reset. Use `simctl io recordVideo` and the repository's labelled frame workflow;
do not infer visual correctness from compilation or unit tests.
