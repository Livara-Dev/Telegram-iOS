# Experimental local text filters

This increment has source code and automated tests prepared for native CI. It is not verified in the running Telegram
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

## Implemented boundary awaiting native verification

`TelegramCore/Sources/LocalMessageFilter` supplies the preferences facade and
pure evaluator. `SettingsUI/LocalMessageFilterSettingsController.swift` supplies
native rule management; a message-menu action opens an editable excerpt rather
than saving or enabling automatically. Case-insensitive matching uses
`en_US_POSIX`; Foundation canonical Unicode equivalence applies, without a
separate diacritic-insensitive option.

`ChatHistoryEntry/ChatLocalTextFilterProjection.swift` retains original message
objects, row count, identity and indexes. Its eligibility allowlist limits this
increment to ordinary incoming cloud text without media, reply/forward/group,
expiry, rich content or unknown attributes. The live history integration also
excludes protected chats, special subjects and tagged views. Large/animated
emoji keep their existing rendering classes.

`ChatHistoryListNode.swift` evaluates immutable snapshots on a dedicated queue,
discards obsolete results, and retains pending initial/scroll metadata for the
same location while evaluation is superseded. This asynchronous integration
still requires live scrolling/reconnection regression tests. The new content
node replaces only the inside of the existing ordinary text bubble. Original
text is removed immediately rather than faded over the hidden placeholder.
Accessibility, context, reaction, share, swipe and selection entry points have
explicit hidden-state guards.

Temporary reveal belongs to `ChatControllerInteraction` and therefore to that
chat controller, not a global peer-ID set. A placeholder or chat menu can reveal
all matching text; the menu can hide it again. **Selection mode shows originals**
so Telegram's existing selection toolbar does not operate on hidden text. An
already selected row also stays visible if the selection-mode flag is stale.
This is intentional, not a persistent exception to the rules.

## Verification status

The branch is `feature/message-filter-draft`, [PR #4](https://github.com/Livara-Dev/Telegram-iOS/pull/4).
There are 18 real-Postbox core tests and 12 production projection tests. The
projection target has a separate test-only UIKit host bundling the existing
Telegram presentation strings; it creates no Telegram account or connection.
The hosted integration workflow runs the Postbox, archive, TelegramCore and
TextFormat suites before the projection target. The tests-only job collects all
suite results even after one fails; every suite must pass before the full app
build becomes eligible. Core tests therefore do not depend on UI host startup.

Portable tooling, source-scope/credential and Postbox-import checks are separate
from native verification. At `6eba37ff`, the first four targets passed all 1,011 XCTest cases, including
18 filter-core, 15 Ghost and 56 archive integration cases. The fifth target
failed to build because the test host lacked `CFBundleVersion`; **zero projection
tests ran**. [Recorded evidence](evidence/filter-integration-6eba37ff.json) separates
the passed cases from that build failure. At `f295948a`, the corrected host and
projection module compiled and packaged, but hosted simulator startup stalled.
Bazel's 300-second timeout interrupted the first of the pinned runner's three
startup attempts. No actual XCTest case executed, and the former wrapper did
not attempt the remaining suites after that failure. [Recorded timeout evidence](evidence/filter-integration-f295948a.json)
includes the verified artifact digest. The retry gives this host a bounded
900-second allowance and lets the tests-only job collect every fixed suite's
result before returning failure. Any failing suite still blocks application
builds. No successful full application build, projection-test pass, iPhone
performance measurement or working UI is claimed by this source checkpoint. Consult PR #4 for revision-specific CI evidence. No synthetic
benchmark is represented as an iPhone 15 Pro measurement.
