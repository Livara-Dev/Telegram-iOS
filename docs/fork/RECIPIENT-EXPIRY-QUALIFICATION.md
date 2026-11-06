# Recipient local-consume and expiry-worker qualification

This candidate extends native test coverage of the recipient path. Its production comparison source is `5d241267735b3958c225681d173853568bab705d`. Native compilation and execution are pending; portable checks do not establish simulator, device or production behavior.

## Source boundary

The candidate adds one internal overload in `ManagedAutoremoveMessageOperations.swift` accepting a `globalTimeDifference` reader. The existing Network entry point delegates with `{ network.globalTimeDifference }`. The original worker body changes only that property access into the supplied reader call. Timestamp subscriptions, head selection/cancellation, signal composition, offset rounding, real `suspendAwareDelay`, and destructive transaction branches stay identical. Tests pass `{ 0 }` and use the real wall clock. They never call the remote-consume, expiry, delete or capture helpers as a substitute for the worker.

The seam is needed because `Network` is final and its initializer is fileprivate. It avoids a live Network/session dependency in otherwise local native tests. It adds no dependency, transport, encryption, storage or telemetry behavior.

All capture and storage implementations are byte-identical to `5d241267735b3958c225681d173853568bab705d`, including `Sources/LocalArchive`, `LocalMessageArchive/Sources`, `DeleteMessages.swift`, the local/remote consume functions, and Postbox. Account construction now moves its existing coordinator block ahead of managed deletion/expiry worker subscriptions. `recipient_expiry_request.py` permits exactly eleven candidate paths and verifies both the exact reader-only worker transformation and exact coordinator-block relocation against the pinned production source. The old block must be unique, its location and arguments unchanged, and every other Account byte identical. A neighbor file, unrelated Account change or additional worker-body edit is rejected.

The native run will execute the exact reviewed **candidate** commit. A separate request commit contains only `ci/recipient-expiry-request.json`; validation checks its single parent and request-only delta, then the job checks out the candidate before dependencies, configuration and tests. This is not an exact-source test of an already built `5d241267...` package: that package is not rebuilt here, and the candidate includes the worker dependency seam.

## Native evidence

`LocalArchiveRecipientExpiryWorkerTests` contains 16 cases:

- `testIncomingTimedPhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingTimedVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingTimedVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingTimedRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingViewOncePhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingViewOnceVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingViewOnceVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testIncomingViewOnceRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval`
- `testViewOnceWorkerCatchesUpFromActualLocallyArmedTimestamp`
- `testSecretTimedPhotoWorkerUnlinksCacheBeforeStoreCanCommit`
- `testSecretTimedVoiceWorkerUnlinksCacheBeforeStoreCanCommit`
- `testCloudTimedVoiceOptOutDoesNotPreventCanonicalExpiry`
- `testCloudViewOncePhotoOptOutDoesNotPreventCanonicalExpiry`
- `testSecretTimedPhotoOptOutDoesNotPreventCanonicalCacheRemoval`
- `testCloudViewOnceVoiceDoesNotTreatPartialCacheAsRetainedBytes`
- `testSecretTimedPhotoPartialCacheIsRemovedWithoutInventingArchivedBytes`

Eight cases cover incoming cloud timed/view-once photo, video, Opus voice and round video. Cloud fixtures use the received-media Autoclear timer shape. A catch-up case inspects a locally armed timestamp before the real worker subscribes. Two secret timed cases exercise the real autoremove worker's deletion and forced cache cleanup. Three opt-outs verify expiry/cleanup continues without capture. Two partial-source cases preserve honest unavailable-media state.

The JPEG, H.264 MP4, Opus/Ogg and H.264/AAC round-video constants are copied verbatim from the existing synthetic playback fixtures, with SHA-256 provenance in the file. They replace arbitrary AV bytes in this new suite. Format probing and full FFmpeg decoding succeeded during draft inspection; the native tests themselves assert exact byte preservation, not native playback.

Tests start a real Postbox, MediaBox, coordinator and SQLCipher archive. An earlier real Store operation is gated while local consumption and the real timer/worker execute. Before release, opted cases inspect the pending original capture. Secret cases also assert that canonical complete/partial/meta cache paths are already removed, without test-side cache deletion. After release, tests observe a new writing state, wait for ready, verify a fresh empty outbox, the matching archive event and exact bytes. They close/reopen both databases with capture off, then verify bytes and canonical expired/deleted state again.

Cloud expiry does not unlink its cache. Those cases call the standard MediaBox removal API after commit and confirm all cache paths disappear before reopening. They qualify durable persistence after normal clearing, not survival of eviction before source acquisition. Secret tests qualify the existing in-process descriptor handoff across actual worker-origin cleanup. Neither path promises recovery of already expired/unreceived/incomplete content, an evicted source, an expired lease, process death before commit, or capacity/storage failure.

Do not infer completion from initial ready or a history-revision bump. The suite installs a writing observer before consumption, waits for canonical expiry while Store is blocked, then releases the Store gate before waiting for new writing/ready. The coordinator queues its own retention operation before writing, so waiting for writing before gate release would deadlock.

## One explicitly requested Core run

`.github/workflows/recipient-expiry-tests.yml` listens only for pushes to `test/recipient-expiry-worker` that change the exact request path. The request file is intentionally absent from this candidate. Publish this preparation only on `fix/archive-startup-order`, which does not match that workflow's branch gate. There is no manual/full-build dispatch input.

Deleting the old request also matches a push path filter. Do not advance the eligible test branch to an intermediate request-absent candidate: that would start an unnecessary job which fails its missing-request guard. After review and the preceding native run's completion, create the separate request-only child commit, then advance `test/recipient-expiry-worker` directly from its old request head to the new request head in one push. The intermediate candidate can be present in history without ever becoming the eligible branch tip. No new request or push is created by this local preparation.

After review, a separate metadata-only child commit may add a request with exactly these fields:

| Field | Required value |
| --- | --- |
| `schemaVersion` | integer `1` |
| `candidate_sha` | exact reviewed candidate commit, also the request commit's sole parent |
| `production_source_sha` | `5d241267735b3958c225681d173853568bab705d` |
| `validation_scope` | `core-only` |
| `nonce` | safe bounded request identifier |

The validator rejects duplicate/unknown keys, wrong events/repository/branch, wrong ancestry, broader source changes and non-regular request files. `--check-worktree` checks the exact source delta and requires the request absent. Before a candidate commit exists, it reports a null candidate SHA rather than labelling uncommitted tests as the original production commit.

There is one `xcode-27` job, using the pinned Xcode 27.1 toolchain/runtime checks and the existing command:

```sh
python3 tools/fork_baseline/baseline.py simulator --integration-tests --tests-only --core-only-tests
```

That command selects only `//submodules/TelegramCore:TelegramCoreTests`; tests-only skips full application build and packaging. The workflow has no device/UI/package job, matrix, publishing permission or application artifact. It shares the existing Core concurrency group, with active-run cancellation off. Checkout credentials are ephemeral and confined to fetch. API Secrets enter only configuration/native build steps; values are validated without disclosure and the existing baseline runner redacts retained output. This branch does not restore or save dependency caches: the existing cache trust gate remains restricted to `ci/verify-latest`. Upstream dependency checksum verification and the pinned Bazel digest check remain unchanged. Private Bazel roots/configuration are not cached or uploaded.

The artifact contains the validated request/candidate hashes, bounded redacted `prebuild-telegram-core/test.xml`, test log and collection status, integration summary, toolchain/portable/native text logs, and checksums. A future combined-candidate pass claim requires auditing all 16 recipient and 3 startup test identities and outcomes from that exact candidate's native evidence. Missing XML, runner/bootstrap failure, zero discovered tests or merely compiled fixtures do not qualify the suite.

## Verification boundary

Preparing the source does not dispatch a GitHub job. Candidate publication and an explicit request are separate; publication alone is not native verification. Linux portable tooling/source/security checks are separate from native execution. Gallery/controller/player callbacks, actual delivery/API parsing, download completion, early close/natural end/background/suspension, OS kill and physical-device behavior remain outside these core tests. Existing API Terms conflict documentation remains unchanged; no policy approval is implied.

## Startup-order candidate checkpoint

The preceding recipient candidate `a9f97764d9a6e1ef0c576644e724762270d935d9` requested the 16 recipient-worker cases in [run 42291375322](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42291375322). Its expected Core inventory was 851 cases (835 earlier Core cases plus 16 authored recipient cases). The native test library failed compilation at `LocalArchiveRecipientExpiryWorkerTests.swift:101,119`: the local resource was declared as the broader `MediaResource`, while both Telegram media constructors require `TelegramMediaResource`. All three assigned concrete resources implement that narrower protocol. The correction changes only that local declaration; it adds no cast or production capture change. **Zero native cases executed** in the failed run. Artifact [13540804680](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42291375322/artifacts/13540804680) has outer SHA-256 `4f5006466264d9fc42000d8ea3d56c631d03ef4754f67f16e646e251a46bfb93`; the failure is not a timer assertion, media-preservation pass or application-build failure.

The new local `fix/archive-startup-order` candidate adds three authored `LocalArchiveStartupOrderingTests` cases, all **native PENDING**:

- `testCoordinatorSubscriptionBeforeRestoredDueWorkerPinsWhileStoreOpenIsPending`
- `testRestoredDueWorkerBeforeCoordinatorRecordsHonestMissingMedia`
- `testProtectedStoreFailureDoesNotDelayRestoredSecretCanonicalExpiry`

This addresses a concrete initialization ordering boundary. Account construction runs independently of the Postbox queue, so a restored already-due secret timer could previously execute before the later coordinator initialization. The proposed relocation registers the existing account-owned media source and subscribes its initial policy before the timestamp workers subscribe. Both initial views pass through the same serial Postbox queue; the initial preference callback applies policy synchronously before the timestamp callback can schedule its timer. No Store, Keychain, `.ready` status, network request or encrypted commit is awaited. Unsupported policy, unavailable protected files, exhausted bounds or key failure still permit mandatory canonical cleanup.

The new positive test holds Postbox before either initial view publishes, subscribes coordinator then the actual worker without a readiness wait, and releases the queue. Canonical forced cleanup must finish while Store-open completion is held; only the pre-unlink descriptor can later supply the exact JPEG to the encrypted archive. The old-order control requires honest missing media. A protection-error control requires normal cleanup; an identical key with distinct equal-size bytes in another MediaBox must remain unchanged. All synchronization uses explicit bounded acknowledgements, not relative sleeps. These fixtures exercise real coordinator/worker ordering but **do not instantiate the complete Account initializer**; Account wiring is independently source-reviewed and remains an application-runtime verification boundary.

The unchanged existing time-offset overload is reused; the new patch does not introduce another worker implementation. The combined candidate expects 854 Core method identities if all earlier tests remain present, but neither that arithmetic nor portable checks proves compilation or execution. No new request is included. The existing workflow/event/branch, fixed Core-only command, API credential scope, timeouts, concurrency and artifact gates remain unchanged. Publishing this local preparation on another branch cannot broaden those trigger permissions.

This preparation starts from the earlier request commit and removes only its request marker. Until a new candidate commit records that deletion, the strict worktree checker can still reject the inherited historical request in HEAD; that guard is deliberately not weakened. The final candidate must have the exact eleven-file production-relative scope, no request, and both exact transforms before a separate request-only child commit can qualify it.

The already built/frozen `5d241267` application is a separate source and does not contain the recipient seam or this startup relocation. Passing its earlier 964 Store/Core cases does not verify these new 19 authored tests or the changed initialization order. Any eventual candidate pass must retain its own source identity. Native compilation, authenticated application startup, physical installation and a replacement IPA for this new source remain unverified here.
