# Recipient local-consume and expiry-worker qualification

This candidate extends native test coverage of the recipient path. Its production comparison source is `5d241267735b3958c225681d173853568bab705d`. Native compilation and execution are pending; portable checks do not establish simulator, device or production behavior.

## Source boundary

The candidate adds one internal overload in `ManagedAutoremoveMessageOperations.swift` accepting a `globalTimeDifference` reader. The existing Network entry point delegates with `{ network.globalTimeDifference }`. The original worker body changes only that property access into the supplied reader call. Timestamp subscriptions, head selection/cancellation, signal composition, offset rounding, real `suspendAwareDelay`, and destructive transaction branches stay identical. Tests pass `{ 0 }` and use the real wall clock. They never call the remote-consume, expiry, delete or capture helpers as a substitute for the worker.

The seam is needed because `Network` is final and its initializer is fileprivate. It avoids a live Network/session dependency in otherwise local native tests. It adds no dependency, transport, encryption, storage or telemetry behavior.

All capture and storage implementations are byte-identical to `5d241267735b3958c225681d173853568bab705d`, including `Sources/LocalArchive`, `LocalMessageArchive/Sources`, `DeleteMessages.swift`, the local/remote consume functions, and Postbox. `recipient_expiry_request.py` permits exactly nine candidate paths and checks that the worker equals this deterministic minimal transformation of the production source. A neighbor file or an additional worker-body edit is rejected.

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

`.github/workflows/recipient-expiry-tests.yml` listens only for pushes to `test/recipient-expiry-worker` that change the exact request path. The request file is intentionally absent from this candidate. There is no automatic run from publishing these source files and no manual/full-build dispatch input.

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

The artifact contains the validated request/candidate hashes, bounded redacted `prebuild-telegram-core/test.xml`, test log and collection status, integration summary, toolchain/portable/native text logs, and checksums. A future pass claim requires auditing all 16 actual test identities and outcomes from that exact candidate's native evidence. Missing XML, runner/bootstrap failure, zero discovered tests or merely compiled fixtures do not qualify the suite.

## Verification boundary

No GitHub action, commit or request is created by preparing this change. Linux portable tooling/source/security checks are separate from native execution. Gallery/controller/player callbacks, actual delivery/API parsing, download completion, early close/natural end/background/suspension, OS kill and physical-device behavior remain outside these core tests. Existing API Terms conflict documentation remains unchanged; no policy approval is implied.
