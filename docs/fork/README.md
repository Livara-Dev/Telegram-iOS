# Native iOS fork: feasibility and baseline checkpoint

Status on 2026-11-04: **source audit and build tooling only; iOS build unverified**.
No requested feature is implemented or represented as working. No IPA has been
produced or signed. This checkpoint deliberately precedes changes to message behavior.

The base is the official TelegramMessenger/Telegram-iOS repository at
`279f89e4d4f63f17227bf877dc3d8990f22ecd58`; the behavior reference is
AyuGram/AyuGram4A `rewrite` at `7013145676d36d82ee13c02a89f72097b7490dcd`.
Revisions are frozen for this audit, rather than assuming future HEAD is equivalent.
See [provenance.json](provenance.json) for build-manifest hashes and gitlinks.

## Audit documents

- [iOS architecture and integration seams](IOS-ARCHITECTURE.md)
- [AyuGram behavior reference and limitations](AYUGRAM-REFERENCE.md)
- [License, dependency and distribution audit](LICENSE-AND-SUPPLY-CHAIN.md)
- [Implementation sequence and acceptance tests](IMPLEMENTATION-PLAN.md)
- [Baseline execution and evidence](BASELINE.md)
- [Complete requested feature status matrix](../AYUGRAM_FEATURE_MATRIX.md)
- [Desktop and October Android reference evidence](EXPANDED-REFERENCE.md)
- [Security assessment](../SECURITY_ASSESSMENT.md)

## Feasibility verdict

Local retention, edit history, local filtering and native settings are technically
feasible as incremental Swift changes within the existing Postbox/TelegramCore/
TelegramEngine/UI boundaries. They do not require replacing authentication,
MTProto, encryption, or transport. Nothing in this checkpoint translates Android
source or adds Android runtime dependencies.

The Android reference is incomplete: its README identifies omitted proprietary
message-history helpers. Its public call sites and schemas inform requirements,
not a verified implementation to copy. It includes Firebase telemetry and AyuSync;
neither belongs in the iOS fork.

The official iOS base itself contains telemetry, including server-configured network
reporting through `help.saveAppLog`. The unchanged baseline is therefore **not a
telemetry-free deliverable**, even with `app_center_id=0`. Before any real-account
acceptance or release, a separate diff must disable collection and submission,
including pre-auth, background, diagnostics and extension paths, with tests.

[Telegram API terms §1.4](https://core.telegram.org/api/terms) explicitly prohibit
ghost behavior (read, online and typing suppression) and preventing self-destructing
content from disappearing. A technical implementation cannot make that behavior
compliant. Retention must exclude expiring, view-once and secret-chat content in the
initial scope. Any broader retention policy needs its own terms/privacy review.
GPL modification rights and use of Telegram's service are separate questions.

## Current blockers

The provided workspace is Linux, with no Swift/Xcode/Apple SDK toolchain, Telegram
API credentials, Apple signing assets, or fork remote. The connected GitHub login
was discovered as `Livara-Dev`; `AyuGram-iOS` was not found and the exposed connector
cannot create/fork repositories. GitHub's hosted `xcode-27` image is compatible on
paper; its workflow has been prepared but not executed.
All iOS gitlinks were fetched at their recorded revisions. This is source readiness,
not a successful compilation. The upstream toolchain pins macOS 27, Xcode 27.1 and
Bazel 9.2.0; iOS tests additionally specify an iPhone 17 / iOS 26.5 simulator.

Supply-chain and license work remains before distribution: verify all transitive
dependencies and binary provenance, retain corresponding source/notices, resolve
the watch build's dependency lock gap, and review the distribution channel's terms.
Checksums establish integrity against the pin; they do not prove that code is safe.

The next executable milestone is a full simulator build plus explicit upstream
test target on the provisioned Mac, followed by telemetry removal and signed-device
validation. Missing tools and credentials must not be replaced with stock Telegram
credentials, fake signing, version overrides, or a claimed successful build.
