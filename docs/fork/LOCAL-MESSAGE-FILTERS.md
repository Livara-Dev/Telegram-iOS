# Account-local message filters

The current source adds explicit sender hiding and inverse rules to the existing
bounded text, expression and media-type filters. These additions are not native
qualified yet: this workspace is Linux, and no compilation, authenticated chat
interaction, visual review or physical-device result is claimed here.

## Reference and scope

The inspected AyuGram Desktop `features/filters/filters_controller.cpp` uses
`ReversiblePattern.reversed` to invert a successful regex result and consults
`AyuSettings::shadowBanIds` when filtering senders. The native implementation
reuses Telegram-iOS account preferences, its existing background history queue
and the existing reversible hidden-message placeholder. It does not copy ICU or
translate Android Java. Desktop forwarded-author and via-bot filtering, grouped
media, dialog previews and notifications are outside this implementation.

Only ordinary incoming cloud messages already eligible for the native local
filter projection can be hidden. Secret chats, protected messages, TTL/view-once
attributes, unknown or rich metadata, forwarded messages, grouped media and
non-generic bubbles remain visible. The projection rechecks that boundary even
if an obsolete or otherwise inappropriate match reaches it.

## Sender hiding

Open **Create Text Filter…** or **Create Media Filter…** from a supported message,
then switch **Hide This Sender Locally**. That preference saves immediately,
independently of the draft text/media rule. Filtering must also be enabled for
the account. Only a known user/channel author can be added; sender filters do
not infer a missing author, via-bot identity or forwarded identity.

The **Locally Hidden Senders** section in Message Filters lists stored identities
using local cached names when available. Selecting a sender offers removal of
that local filter. No block/unblock RPC or peer-fetch request is made. Removing
a sender filter does not override other matching rules. Global-chat exclusions
also exempt chats from sender hiding; explicit per-chat text/media rules still
apply there. Archive sender exclusions are separate preferences.

The set is account-local, limited to 1,024 encoded peer identities and included
in the existing 128 KiB preference-document bound. Decoding validates raw high
identifier bits before constructing `PeerId`, whose invariant assertions are
unsuitable for untrusted preference values. Only positive CloudUser/CloudChannel
identities are accepted.

Rule scopes and chat exceptions also validate encoded identifier bits before
native peer-name lookup. Every safely decodable scope namespace and the legacy
empty sentinel retain their identity; malformed scopes make the saved policy
read-only instead of being silently discarded or reaching a `PeerId` assertion.

## Inverse semantics

**Invert This Rule** changes a proven non-match into a hiding predicate:

| Rule | Hidden | Kept visible by this rule |
| --- | --- | --- |
| Inverse literal/expression | Eligible nonempty text/caption that does not match | A match, empty text, oversized text or exhausted expression budget |
| Inverse media type | Other recognized, eligible media types | Selected media type, text-only messages, unknown classification |

Rules remain OR predicates. An inverse rule is not an allowlist; a different
rule or a sender filter can still hide its apparent exception. Case-sensitive
and fixed-locale case-insensitive behavior are unchanged. All expressions share
the existing finite-state execution budget per message; inversion never turns a
work-limit or unsupported result into a hiding decision. Independent media or
sender predicates do not scan the text body and may apply to a large caption.

## Persistence and verification

Schema version 3 reads versions 1 and 2 with their original semantics and does
not rewrite the saved bytes on read. Only an explicit mutation writes version
3. Older versions may not contain sender hiding or inverse semantics; unsupported
versions and value-invalid policies in the supported shape stay read-only,
including reset, with original bytes retained. Unsupported versions are rejected
before their fields or collections are decoded.
Every ordinary mutation preserves unrelated sender/rule/exclusion preferences.
Disabling filtering preserves its saved policy. Explicit filter reset clears it.
The matching path does not mutate canonical messages, media, server preferences,
archive records or Telegram operations.

Matching mode and media type are explicit keyed Int32 primitives. Encoding a
raw enum generically invokes Postbox's unsupported single-value container and
was the actual native host crash in run 42114628197. The corrected rule codec
preserves the intended numeric representation and legacy field omission; it
rejects unknown raw values without rewriting saved preferences. Standard JSON
round-trips alone do not test this boundary.
The inherited adapted decoder is not a complete structural validator: a
same-version raw preference with a wrong binary collection shape (for example
integer elements instead of keyed rule objects) can still trap. Normal rule
mutations cannot produce that shape. This repair does not alter the global
Postbox adapter or certify arbitrary corrupt binary preference data.

Fifteen authored Core tests cover on-disk reopen, account isolation, cached
block separation, canonical cleanup, every mutation, limits, malformed/future
policies, legacy-byte preservation, Unicode/case matching, expression work
limits, input bounds, inverse media truth tables and scope/OR behavior. Three
additional authored projection tests exercise the production snapshot bridge,
sender identity, reveal and protected/timed/grouped/unknown metadata boundaries.
Native execution is pending; portable guard tests are not a substitute.
Three additional authored codec cases use real Postbox preferences/reopen to
cover all media kinds, expression/inverse rules, version-2 numeric wire bytes,
and unknown values in versions 1, 2 and 3. Their native execution is pending.

A native visual recording should show the message-prefilled editor saving its
sender switch independently of Save; an inverse rule surviving editor reopen;
the sender appearing by name in the account's list; a confirmed local unhide;
the correct global-chat exception; and temporary reveal preserving the original
message. Light/dark layouts must keep all switch labels and scope explanations
readable. Review timestamped simulator frames before calling this UI verified.
