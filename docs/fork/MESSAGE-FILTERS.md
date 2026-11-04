# Experimental local text filters

This increment is under development. It is not verified in the running Telegram
application. Keep library tests, iOS integration tests, full application builds
and recorded UI interaction as separate acceptance gates.

## Reference and native scope

The inspected Android source is AyuGram4A
`7013145676d36d82ee13c02a89f72097b7490dcd` (July 2023). Its
`TMessagesProj/src/main/java/com/radolyn/ayugram/AyuFilter.java` compiles Java
regular expressions, uses substring `find()`, caches by dialog/message, and
shares decisions across album members. That cache does not include account or
content revision. The public source does not establish the implementation of
the October 2026 Android release. See [the release audit](EXPANDED-REFERENCE.md).

Desktop `0f2a31bb420de98f331990432265b131b8d836b5` (October 20, 2026),
`Telegram/SourceFiles/ayu/features/filters/filters_controller.cpp`, adds explicit
dialog/shared rules, shared-rule exclusions, blocked author handling and a
temporary reveal set. Its handling of forwarded authors, bots, outgoing
messages and direct conversations is a product policy, not protocol behavior
that should be copied implicitly. No Java or C++ implementation is translated
into Swift or imported into this feature.

The official [filter documentation](https://docs.ayugram.one/android/filters/)
describes Java regex, synthetic media/button tags, dialog preview filtering,
download suppression and remote rule import. This initial native increment
targets a smaller, testable scope:

- Account-specific literal rules with an explicit case-sensitivity choice.
- Global or single-chat scope, and chat exclusions from global rules.
- Native rule management and creation from message text.
- Ordinary text-bubble placeholders and temporary local reveal.

Media, albums, rich messages, arbitrary regular expressions, blocked-user
filtering, notification/dialog/search previews and remote rule import are not
included. No automatic spam classifier or advertised spam-detection accuracy
exists. Patterns are ordinary literal text: punctuation has no regex meaning.

## Persistence and evaluation constraints

Rules belong to the active account's existing encrypted Postbox preferences.
They do not change canonical messages, create server deletion/moderation
operations, or introduce a network endpoint. The master switch defaults off.
An unsupported or malformed saved schema remains read-only and must not be
overwritten by defaults.

Limit each account to 64 rules, each literal to 256 UTF-16 code units, and the
global-exclusion set to 4096 chats. Evaluate at most 32768 UTF-16 code units per
message; an oversized input is an explicit unsupported result, not a successful
scan of a silently truncated prefix. Evaluate immutable snapshots away from the
main queue and reject obsolete results. Do not use a process-global cache keyed
only by message or peer identifiers.

## Chat integration acceptance

A hidden message must keep its history index and row identity so an all-matching
window can still paginate. Hiding a text bubble must not replace its outer list
node with an incompatible class. A temporary reveal is presentation state for
the current chat/account lifetime, not a persistent rule change.

The placeholder must not leave the original text in an accessibility label or
offer hidden-message copy, reply, forwarding or selection actions. Opening a
different account must not reuse the reveal state or evaluation result. Repeated
hide/reveal, edits, rule changes, theme changes, scrolling and all-filtered pages
need recorded UI verification. Compilation alone does not satisfy these checks.

Filtering is a local display preference, not access control or deletion. The
original message remains in Telegram and Postbox, and is still available on
other devices and in surfaces outside this increment. Background translation,
read status, mentions, downloads and other existing processing do not implicitly
stop because a bubble is hidden. This feature does not extend Ghost Mode.

## Verification status

Implementation and tests are being prepared on `feature/message-filter-draft`.
No filter test pass, successful full application build, iPhone performance
measurement or working UI is claimed yet.
