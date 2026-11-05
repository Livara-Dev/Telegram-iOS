# License, API terms and dependency audit

Audit date: 2026-11-04. Source inspection only; no legal certification, dependency vulnerability clearance, runtime privacy attestation, or successful iOS build is claimed.

## Sources and scope

- TelegramMessenger/Telegram-iOS at `279f89e4d4f63f17227bf877dc3d8990f22ecd58`.
- AyuGram/AyuGram4A (`rewrite`) at `7013145676d36d82ee13c02a89f72097b7490dcd`.
- Official Telegram [source listing](https://telegram.org/apps#telegram-for-ios), retrieved 2026-11-04, explicitly labels the linked official iOS repository **“GNU GPL v. 2 or later.”** Local evidence snapshot `evidence/telegram-apps-license-evidence.html.gz`, SHA-256 `bc03e6ee1c6b800b2f884e4ac8b7aef0cf3c4e66e79b1a977628f67210702e59`.
- Official [API terms](https://core.telegram.org/api/terms), retrieved 2026-11-04. Local evidence snapshot `evidence/telegram-api-terms-evidence.html.gz`, SHA-256 `5941d93f52004cf26e2dfc3b3b1a3351aaf74a8e6362774e50f575338c0ae82d`.

Paths below are relative to the named repository. Line references are to these pinned revisions. Submodule source audit and dependency resolution are incomplete; the parent working session is initializing submodules concurrently.

## License findings

| Component | Evidence | Finding |
| --- | --- | --- |
| Telegram iOS application | Official source listing above; `README.md:8–12` | Officially identified as GPL v2 or later; requires own API ID, unofficial branding, no standard Telegram logo, and publishing modified source. |
| iOS checkout root | `git ls-tree HEAD` and tracked license filename inspection | No root LICENSE/COPYING text is present at the pinned commit. This is a packaging gap, not evidence that the app is unlicensed. Add a faithful GPL text and a provenance notice before distributing the fork. |
| AyuGram | `LICENSE:1–7`; Android `MessageObject.java:1–7` and `ChatActivity.java:1–7` | Root license text is GPLv2. Inherited Telegram source explicitly grants v2 or later. Do not infer that every AyuGram-authored contribution grants a later-version option solely from the generic GPL template appendix. GPLv2 is the conservative common basis when reusing its code. |
| AyuGram-authored code | `TMessagesProj/src/main/java/com/radolyn/ayugram/AyuFilter.java:1–8`; `messages/AyuMessagesController.java:1–8`; `README.md:92–96` | Copyright @Radolyn, 2023; requests original-author credit. Preserve applicable notices if copying or adapting expression. Reference behavior with original Swift implementation as requested; automatic translation would not remove licensing obligations. |
| FFmpeg | iOS `submodules/ffmpeg/Sources/FFMpeg/ffmpeg-8.1.2/LICENSE.md:3–11,61–73` | Generally LGPL2.1-or-later, with optional GPL parts and other notices. Final selected build configuration controls applicable terms; inventory linked files and retain required notices. |
| BoringSSL | iOS `third-party/boringssl/LICENSE:1–15` | Mixed OpenSSL/SSLeay, ISC and third-party terms. Do not represent all vendored code as GPL. Review GPL compatibility/exceptions for linked versions as part of the final dependency inventory. |

### Distribution obligations to implement

These items describe a GPLv2 distribution approach; they are not a statement that all release obligations have already been met.

1. Preserve copyright, license and no-warranty notices and accompany distributions with the license (`AyuGram4A/LICENSE:79–85`, §1).
2. Mark modified files prominently with change notices and dates, and license the derivative application as a whole under the applicable GPL terms (`LICENSE:90–101`, §2). Keep feature commits and a change log, but do not assume commit history alone replaces file notices.
3. For each distributed IPA, provide the complete corresponding source for that exact binary, including local modifications, generated-source inputs, interface definitions, build/install scripts, exact dependency versions and their required source. A GitHub source ZIP omitting submodules is insufficient (`LICENSE:134–170`, §3).
4. Prefer accompanying source from the same release location as the IPA, with revision/dependency lock metadata and build instructions. A §3(b) written source offer is an alternative, but creates at least a three-year obligation to any third party; do not make such an offer casually.
5. Maintain a third-party notice/license bundle. Generate an SBOM from the resolved build graph; include the source or appropriate offers and relinking material where required by the actual licenses. No complete SBOM or binary-link audit exists from this inspection.
6. Preserve recipient redistribution/modification rights; do not add an incompatible EULA or other restrictions (`LICENSE:189–208`, §§6–7).
7. Keep private signing keys, personal certificates/passwords and account credentials outside source publications. Document how a recipient obtains their own API credentials and independently signs their build. Credential separation must not make corresponding source incomplete.

### Apple distribution and signing

Independently signing an IPA does not itself resolve GPL compliance, and GPLv2 does not itself require publishing the distributor's Apple private signing key. Provide the scripts and instructions needed for recipients to rebuild and sign under their own identities. The final App Store/TestFlight/ad hoc/enterprise distribution route has different contractual and technical restrictions. Review its actual terms against GPL §§6–7 before distribution; do not claim that any Apple channel is automatically compatible or automatically prohibited. Selecting GPLv3 instead would introduce additional provisions, including potential installation-information requirements; it is not a substitute for reviewing compatibility across the source inventory.

## Separate API contract conflict

API terms §1.4 explicitly forbid “preventing self-destructing content from disappearing,” preventing correct last-seen/online status, tampering with message read status (the terms explicitly name **ghost mode**) and preventing typing status from being sent/displayed. Therefore the requested read/online/typing suppression conflicts with the currently published Telegram API terms. GPL permission to modify source and permission to operate against Telegram's service are separate questions. Record this as a release feasibility issue; do not label ghost mode API-compliant because it is technically implementable. Exclude secret-chat and self-destructing content from an initial archive design, and review ordinary deleted-message retention separately rather than assuming §1.4 explicitly names every ordinary deletion case.

API terms §2.1 and iOS `README.md:8` require the application's own `api_id`. AyuGram `README.md:28,105–108` advertises official keys and provides an example using them; those must not become this fork's defaults. Credentials embedded in mobile clients should not be treated as an unextractable security secret, but must still remain out of public CI logs and source configuration.

## Dependency integrity findings

- iOS `MODULE.bazel:14–28,231–253` pins SHA-256 for WASI SDK, CMake, Meson, Ninja and FlatBuffers archives. Rust toolchain archives have an explicit hash table (`MODULE.bazel:38–72`). Bazel itself has a version/hash pin in `versions.json`; `build-system/Make/BazelLocation.py:100–123` checks it before using the downloaded binary. These are useful integrity controls, not proof the code is safe or that provenance has been independently authenticated.
- The root `MODULE.bazel.lock` is tracked. Rust crates use dedicated Cargo lockfiles (`MODULE.bazel:104–119,131–179`). Some wallet specs use version ranges (`:84,87,90–94,100`), so the resolved lockfiles and a no-repin CI policy matter. Do not permit an automatic repin in a release build.
- `.gitmodules:2–40` names 13 first-level submodules including upstream projects and forks. Resolve relative URLs against the official upstream, require the exact root gitlinks, and verify recursively. Do not replace unavailable pinned commits with current branches to obtain a green build. A transient checkout mismatch was observed during concurrent initialization; final validation belongs in the baseline preflight.
- Git hashes and download checksums provide integrity against the recorded inputs, not maintainer identity verification or vulnerability clearance. Record the audited revision and license for every linked dependency before claiming the user's “no unverified dependencies” condition is met. No new application dependency is needed for the proposed feature architecture.
- AyuGram is a behavior reference only. Its Gradle wrapper properties pin Gradle 7.5.1 but have no `distributionSha256Sum`; no tracked Gradle dependency-verification metadata or dependency lockfile was found. `TMessagesProj/build.gradle:71` includes a binary AAR. None of these Android dependencies should enter the iOS fork.

## Telemetry and off-device history findings

- AyuGram explicitly documents Crashlytics (`README.md:49–50`), and `TMessagesProj/build.gradle:24,29–35` includes Google measurement/Firebase analytics/crash reporting components.
- AyuGram documents server synchronization of message history/read states (`README.md:46,78–84`); its Android build adds WebSocket/OkHttp dependencies (`TMessagesProj/build.gradle:67–69`). Do not port AyuSync, remote history transport, crash reporting or analytics into this local-only design.
- Unmodified iOS upstream is **not established telemetry-free**. `submodules/TelegramUI/Sources/AppDelegate.swift:1632–1638` conditionally starts AppCenter crashes when linked/configured. `submodules/TelegramCore/Sources/Network/NetworkTelemetryReporter.swift:169–195` uses server configuration to enable network reports through `help.saveAppLog`; `Sources/State/ManagedSynchronizeAppLogEventsOperations.swift:73` sends app-log events. An empty AppCenter ID alone does not satisfy “no telemetry.”
- These findings require a separate, reviewable privacy-hardening patch and runtime tests before any compliant release claim. Preserve authentication, cryptography and required protocol networking; disable optional telemetry producers/upload paths rather than altering MTProto transport. Audit call diagnostics and other app-log producers as well. Archived content must never enter live message objects used for forwarding/upload, analytics, crash logs, backups or extensions that can transmit it.

## Release verification still open

- Original build artifacts now mechanically include the unmodified GPL text as
  `COPYING`, the reviewed `LICENSE-NOTICE.md`, and `CORRESPONDING-SOURCE.md` with
  the exact public repository/commit and reproducible build/source retrieval
  instructions. Their bytes, sizes and SHA-256 values are paired with the IPA
  manifest. Newly published main-app-only derivatives preserve the original
  three verified files and fail closed if they are missing or altered. Older
  artifacts without these files remain usable for explicitly scoped diagnostic
  replay, not newly derived distribution through that publisher.
- These checks establish material presence and integrity, not complete GPL
  compliance or legal clearance. Recipients must actually have access to all
  corresponding source, notices and build/installation materials for the exact
  artifact. The full third-party notice/source inventory and distribution-channel
  review remain open. No private credentials or generated configuration are
  collected into the three source/license materials.
- Exact recursive submodule and resolved dependency verification plus source availability review.
- Privacy hardening and runtime network/telemetry tests.
- Distribution-channel contract review and resolution of the explicit ghost-mode API-terms conflict.
- macOS build, actual tests, signing identity/entitlement verification and corresponding-source bundle for the resulting IPA.
