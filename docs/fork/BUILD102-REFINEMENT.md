# Build-102 refinement

Base application: `9d6b8f887dad20f072fdcc65ad4d1fab9bc7664f`.
Review checkout: `bb4bd6c3b25b648277df9ebe75e62d8f0485d4bb`.

## Source changes

- Compact AyuGram settings: native grouped rows, a small search bar, direct single-destination navigation and no long on-screen descriptions. Timed Media opens the persisted Message Saving controls. Required destructive/retention confirmations remain short.
- Local account allowance is 50 for both ordinary and Premium accounts. Settings, log-out and account-deletion alternatives use the same cap. At capacity, the client reports a local limit instead of selling Premium. This does not bypass login verification, rate limits or server-side limits. Fifty simultaneous live accounts have not been performance-tested.
- Retained files now have local photo previews and playable attachment actions inside distinct chat rows. Expired placeholders are replaced only by an available retained row; live messages and album entries remain canonical. Secret incoming/local namespaces and zero IDs remain distinct. Actual stored attachment slots are read from SQL, never guessed from a count.
- When an explicitly opted-in disappearing video, voice or round video is opened, the coordinator starts a native full-resource fetch and cache lease. Capture can wait asynchronously for completion for at most 120 seconds. There are at most eight concurrent preparations; each lease is released on storage commit, privacy change, lock, shutdown or the ten-minute deadline. Partial media is never marked saved. Reads and expiration still use the upstream transaction.
- Video metadata includes alternate playback resources; an alternate completed file is not rejected for having a different byte length from the primary rendition. The default quota for newly created archive preferences is 1 GiB. Existing user-configured quotas are preserved; existing 32 MiB installations must increase Message Saving → Storage limit to save larger files.

## Not implemented or not claimed

No SMS-payment/Premium bypass, identity spoofing, authentication-token extraction, code interception, fabricated receipt or altered server-entitlement check was added. Telegram chooses available login methods. Existing supported login paths are unchanged.

This is not recovery of media that was never downloaded or has already been erased. Full-fetch completion can still lose a race with server expiry, background suspension, low storage, manual cache deletion or termination. A selected quota and the existing 512 MiB per-file limit still apply. Downloads may consume additional mobile data when the option is enabled.

The inline projection remains bounded to forty saved rows per loaded history window. Forum/thread, copy-protected, filter-enabled and unsupported rich-content cases retain their current boundaries. Video/voice attachments open the local player; this is not a claim of identical Android waveform controls or full AyuGram parity.

## Validation

Local Linux checks: 35 changed Swift files parsed successfully before packaging; the production policy/resource-key check passed 2,586 host assertions. The full portable tooling suite ran 719 tests with six failures in pre-existing launch/projection timing harnesses. The audit suite passed 31 tests; build-wrapper tests passed 44; telemetry source checks passed. The six failing tests were not removed or weakened. Separate targeted refinement (9), row ordering (3), retained interaction (16) and retained source-fixture (38) checks passed (these overlap the full suite and must not be added to its count). These do not establish iOS type-checking or runtime success. New native regressions cover actual SQL slot identities and secret namespaces, alternate-video capture/cache removal/reopen, opt-in prefetch plans and exclusions, compact settings navigation, expired-placeholder replacement and retained attachment actions/revocation.

The frozen build-82 UI replay driver is preserved byte-for-byte in a named fixture. Fresh settings compilation uses the updated native interaction scenario and retains all twelve screenshot stages and eleven actual light/dark observations. Native execution, real simulator visual review and physical-iPhone acceptance are separate gates. Build 102's old test results do not qualify this source.

Do not delete or rename an existing installation's bundle/account container for testing. New IPAs must identify their exact source, retain source/license materials and state whether the device package is unsigned. Never relabel the build-102 executable as a rebuilt application.
