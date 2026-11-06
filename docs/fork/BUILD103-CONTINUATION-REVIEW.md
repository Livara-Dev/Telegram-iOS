# Build 103 continuation review

Application source: `b9d4f42433c52fb23673418e2daf4e6524d5e60f`.
Build workflow revision: `7ac7d68f29b750b92b28e980376323e1bc643f06`.
Build run: https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42297515524

The interrupted refinement publication completed before this review. It is not a replacement of build 102's executable or a claim that build 103 has finished. The source snapshot at `73114fad3fd3b42e64979c959bbdb4106f96de90` differs from the application source only in the source-snapshot workflow comment.

## Verified checks

The exact-source GitHub portable artifact `13541819081` has outer SHA-256 `55467eae11b0c96ee869d7acd9de3f926eb7211c5a64de30f471ec9fb31c28a3`. All of its internal SHA256SUMS entries were independently checked. Its results.json and actual logs report:

- Build/tooling tests: 719 passed.
- Source-audit tests: 31 passed.
- Build-wrapper tests: 44 passed.
- Source and telemetry guards: both exit 0.

Total: 794 portable test cases. These are not native iOS feature tests. The earlier refinement document's six local harness failures remain a historical local result; they did not recur in this exact-source GitHub portable run. No failing test was removed in this continuation.

The downloaded exact source was also checked locally: all 35 changed Swift files passed syntax parsing; the existing production policy/resource-key host harness passed 2,586 assertions; 9 refinement source-contract tests and 3 settings-order tests passed. The latter tests overlap the portable suite and must not be added to its total. Swift parsing does not establish iOS type correctness or successful linking.

## Reviewed user-facing scope

- Settings use compact grouped rows and a small search bar. Single-destination categories navigate directly. Long explanatory footers and the large header are removed; short destructive and retention confirmations remain.
- Archived attachments have local previews/actions in distinct in-chat rows. Expired placeholders are replaced only by a qualifying retained row. Live canonical messages are not overwritten. The loaded archive projection remains bounded to 40 rows, with explicit archive browsing for the rest; forum/thread and filter-enabled inline cases remain unsupported.
- Opted-in disappearing video, voice and round-video consumption starts a bounded full-file fetch and cache lease. Only complete local bytes can be saved. Alternate completed video resources are supported. This is not proof of a successful live two-client disappearing-media scenario.
- The local account allowance is 50. Verification, two-step authentication and server limits remain unchanged. Fifty real logged-in accounts have not been load-tested on a physical iPhone.
- No SMS/Premium payment bypass was implemented. Telegram's documented payment-required login response is server-selected. QR login requires approval by an already authenticated session; it is not a bypass or a guarantee that every account has a fee-free login route.

## Important upgrade behavior

Existing saved archive quotas are preserved. A build-102 installation with a 32 MiB quota is not silently migrated to 1 GiB. Larger videos require increasing Message Saving > Storage limit. New preferences default to 1 GiB; the existing 512 MiB per-file limit remains. Incomplete downloads, termination, expiry races and unavailable storage can still prevent capture.

## Not yet qualified by this review

At the last observed check, the unsigned-device, native-test and settings-UI jobs were still executing. No new IPA, successful native test result, simulator recording or screenshot was available from this run. A produced IPA must be checked for its actual source/build number and must not be described as fully tested merely because compilation succeeds.

The settings acceptance target is a compact search header, grouped single-line settings, no clipped or overlapping controls, working toggles and navigation, and actual light/dark rendering. This requires the new native recording and screenshots. Physical-device login, message retention, playback and 50-account behavior remain separate acceptance tests.

References: https://core.telegram.org/constructor/auth.sentCodePaymentRequired and https://core.telegram.org/api/qr-login .
