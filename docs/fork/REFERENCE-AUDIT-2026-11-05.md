# AyuGram reference audit — 2026-11-05

This is a read-only source and first-party release audit, not Android/Desktop runtime verification or proof that every advertised behavior exists in the iOS client. It supplements the [feature matrix](../AYUGRAM_FEATURE_MATRIX.md). No Java-to-Swift translation, APK extraction, proprietary component import or new network service was used.

## Verified source and release boundaries

| Reference | Verified revision/date | What it establishes |
| --- | --- | --- |
| [AyuGram4A](https://github.com/AyuGram/AyuGram4A) | Only public branch `rewrite`, `7013145676d36d82ee13c02a89f72097b7490dcd`, commit 2023-07-28; repository push date 2023-09-11; no public tags or releases returned | Historical Java configuration, Room schema and processing hooks. A recent repository `updated_at` is not a recent source revision. It does not provide corresponding source for the November 2026 Android APK. |
| [AyuGram Desktop](https://github.com/AyuGram/AyuGramDesktop) | Default `dev`, `0f2a31bb420de98f331990432265b131b8d836b5`, 2026-10-20 | Current public C++/Qt reference. Its persistent archive mapper still omits media; schema columns alone do not prove media persistence. |
| [AyuGram documentation source](https://github.com/AyuGram/AyuGramDocs) | `42f5f32c6b05aab4a69a5f8d0bf7652e1cb11a9b`, 2025-01-08; MIT | Public documented feature contracts. The current website still contains these older descriptions; it is not a 2026 implementation snapshot. |
| [November Android changelog](https://t.me/AyuGramReleases/64) and [APK announcement](https://t.me/AyuGramReleases/65) | Both posted **2026-11-04 at 12:00:31 UTC**, build **20261104**, full/lite, based on **exteraGram 13.1.1** | First-party confirmation of the requested November release. Full includes plugin support; lite omits it. The changelog delegates to the exteraGram announcement below. The APKs were not downloaded, disassembled or run. |
| [Referenced exteraGram 13.1.1 announcement](https://t.me/exteraGram/194) | 2026-11-02 at 18:08:45 UTC | Release claims inherited by reference, not proof of AyuGram implementation details or permission to copy proprietary exteraGram code. |

The Android tree contains an explicit proprietary gitlink at `TMessagesProj/src/main/java/com/radolyn/ayugram/proprietary`, revision `a3b47ff3af30829cdec58d61d61ebc0a64546563`. That subtree was not imported. Public Java cannot honestly establish exhaustive binary parity with build 20261104. The matrix covers the discoverable public source/settings, all public documentation feature pages and the dated release announcements; undocumented/proprietary behavior remains unknown.

## November 2026 release delta and native equivalents

The announcement describes the following feature groups. These are reference claims, not completed iOS features. Native equivalents must use reviewed iOS/upstream implementations and keep archive text, authentication material and session keys out of external services.

| Reference capability | Native equivalent / limitation |
| --- | --- |
| Music playback with lyrics and moving artwork | Native media player presentation; artwork/lyrics providers need a separate opt-in network/privacy review. |
| Discover/install plugins | No arbitrary Python/native code loading added. New native features require a rebuilt, signed IPA. |
| Plugin code review | A scanner is not a security guarantee; no unverified plugin execution framework copied. |
| Prompts for sensitive plugin actions | No plugin code is granted account, Stars, gift or administration authority. |
| Python/Chaquopy/hook engine updates | Android-specific runtime; not a native iOS dependency. |
| Selected-channel feed categories | Upstream channel/feed UI can be evaluated independently; archive content stays local. |
| Currency/unit/time-zone input calculations | A local calculator is feasible; live exchange rates would introduce a reviewed network source. |
| Search by media and sender | Reuse native chat-search predicates; deleted results need a separate local archive query and clear provenance. |
| PDF viewing | Reuse existing document preview/Quick Look where supported. |
| Round-video camera controls | Use native iOS camera paths; do not port CameraX or exceed platform permissions. |
| Android predictive back | Use native iOS navigation gestures; Android APIs do not transfer. |
| Drag selection in menus | Native context-menu interaction, with accessibility and recorded gesture checks. |
| Material-style profile/seek/navigation presentation | Native themed controls and animations; no Android layout or proprietary assets copied. |
| Tablet pane resizing | An iPad layout concern; it does not establish iPhone 15 Pro behavior. |
| Owner/admin-only folders | Local folder predicates, without raising server-side folder limits. |
| Reaction editor redesign | Native editor presentation, preserving server entitlement checks. |
| Formatted online translation / media origin labels | Existing native translation can be reused under its own privacy rules; archived content is not automatically sent to translation providers. |
| Supporter badge redesign | Local attribution/artwork only, not a Telegram Premium entitlement. |
| General stability and memory work | No imported binary fixes or invented iOS benchmark results. Reproduce and test native failures separately. |

The October 5 Android announcement additionally introduced archive import/export options, deleted media in chat profiles, archive hits in normal chat search, archive exclusions, local folder colors/tags and login fixes. Those claims remain relevant requirements; the November post does not publish their implementation or erase those gaps. The March announcement covers a last-seen search widget, restricted private-chat forwarding and fixes to inline deletion/filter display. Desktop's public source gives a broader, independently inspectable presentation/settings inventory.

## Feature contracts that materially affect implementation

- [Ghost documentation](https://docs.ayugram.one/shared/ghost/) distinguishes message reads, story reads, presence, typing/activity, offline attempts, interaction reads and scheduling. Story replies/reactions and ordinary sends can reveal activity; another client can defeat presence suppression. Scheduled sending is documented as fragile, not an invisibility guarantee.
- [Saving documentation](https://docs.ayugram.one/android/saving/) describes Room storage, an independent attachment folder, a deleted-only screen and imperfect inline pagination. This is not proof of encrypted archive storage, complete media availability or crash-safe cross-store commits. Desktop's source mapper and [Desktop saving documentation](https://docs.ayugram.one/desktop/saving/) remain text-only for persistent archive media.
- [Filter documentation](https://docs.ayugram.one/android/filters/) describes shared/chat regex, exclusions, case matching, blocked users, selected-text rule creation, media/button tags and remote filter imports. Java regex compatibility and unrestricted backtracking are not safe default iOS execution requirements.
- [Other Android documentation](https://docs.ayugram.one/android/various/) includes deep links, debug actions, app-switcher behavior and Android-specific background services. Keep developer-only controls separate from normal user settings and do not implement insecure remote filter imports or identity spoofing.
- Android's advertised official API keys, Firebase telemetry and signature/package impersonation are explicitly excluded. The iOS app uses its own API credentials and Apple signing identity.

## Current API contract limits

The [Telegram API terms](https://core.telegram.org/api/terms), retrieved on this audit date, expressly disallow preventing self-destructing content from disappearing and changing read, online or typing behavior in §1.4. No verified contractual exception permitting retained received view-once/self-destructing media after expiry was found. A locally accessible file, user switch or consent of the client operator does not create such an exception. Keep those cases unsupported and remove any switch that claims they work. Ordinary, non-expiring cloud-media retention is a different code path and must be tested separately.

Ghost controls therefore have an unresolved API-contract conflict even when technically implemented and tested. §3.3 also prohibits interference with official sponsored messages. These are contract restrictions, not claims that local filtering or suppression is technically impossible. Do not describe conflicting controls as API-compliant or invisible.

§2 requires an independent API ID, prominent Telegram API ecosystem disclosure, a distinct application title and no official Telegram logo. Changing the display name and original artwork must preserve the established bundle identity and account-data location. Do not copy official credentials to imitate another application.

## Source, dependency and GPL boundaries

Android's root license is GPL-2.0; Desktop offers GPL-3.0-or-later with its documented OpenSSL exception. The public documentation is MIT. The Android proprietary gitlink and the latest binary/source gap remain unresolved corresponding-source boundaries. Behavioral study is not a license to copy proprietary components. Independent native Swift implementations preserve existing Telegram authentication, encryption and networking, and distribution must provide exact corresponding source, build scripts and notices for the delivered iOS binary. Existing detailed audits: [Android](AYUGRAM-REFERENCE.md), [Desktop](DESKTOP-REFERENCE.md), [license obligations](LICENSE-AND-SUPPLY-CHAIN.md).

No Android/Desktop binary, telemetry SDK, plugin engine, cloud archive synchronization, official API credential or third-party message-upload endpoint was imported during this audit.

## Evidence integrity

Local read-only snapshots are under `/workspace/ayu-evidence/reference-20261105/`. The public release feed hash was `3195b3b3fe7a806bd077fb49176f45a0df79622458d2ca43fa105b3751b2fdac`; the referenced exteraGram feed snapshot was `455b85b35409c0aadb71026b24502079ad7a46b7e332b83da750c3a4470c89d9`; the API-terms HTML was `d01de46e58b844513f35bac10c3a407d7392b2a4ab56dd91ab61fc0a01047c65`. These identify retrieved bytes, not signatures, runtime behavior or approval of the source. Website HTML may change without a feature change.
