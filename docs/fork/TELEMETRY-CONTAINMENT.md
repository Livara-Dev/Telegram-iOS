# Optional telemetry containment

This branch implements the reviewed automatic-telemetry removals from
[the inventory](TELEMETRY-REVIEW.md). It is not a verified telemetry-free release.
Native compilation, application regression tests and observed network behavior
remain pending. The archive branch and its running builds are preserved.

- App-event producer overloads and the sync trigger no longer enqueue data.
  The account task performs one atomic deletion of the owning Postbox's root
  peer/tag-18 legacy queue. Its API has no Network parameter. Other operation
  tags, non-root records and canonical messages are retained.
- Production network recording returns false for every remote configuration,
  DEBUG build, extension/supplementary combination and recording override.
  Direct low-level recorder/formatter tests remain available. The account no
  longer starts the reporting timer. The existing disabled-network branch
  handles its own telemetry-directory cleanup.
- The soft-auth-reset diagnostic callback and short-log uploader are removed.
  Network authentication, reconnection and reset handling remain at their
  existing protocol paths.
- Download throughput aggregation is not instantiated in either account setup
  path. This is separate from local data-usage counters and media transport.
- Viewport/listening collectors are not instantiated. Their typed reporting
  facades complete locally; they do not synthesize Telegram RPC responses.
- Automatic call debug/full-log sinks complete before reading, compressing or
  uploading files. Explicit user ratings still use the existing rating API.
  The feedback interface no longer offers diagnostic attachments or sends a
  second message/log to a hardcoded support account.
- AppCenter startup and optional linkage are removed; the generated AppCenter
  ID is empty. Pinned dependency manifests and cryptographic libraries are not
  altered by this branch.

The new source guard detects reintroduction of the known optional RPC sinks and
collectors. It is a tripwire, not proof about every network path. Real Postbox
tests cover both producer entry points, legacy batches larger than the former
50-record upload batch, preserving other data, repeat cleanup/reopen and account
isolation. The production recording decision has configuration/override matrix
tests. These native tests have been authored but have not run on this branch.
Portable tooling and source checks are the only validation completed locally.

Remaining review: user-directed debug-log sharing, legacy diagnostic prompts,
local call-log retention, update endpoints, binary dependency inspection and
runtime canary/endpoint observation. Normal Telegram transport, authentication
challenges, push services, explicit ratings, sponsored-message protocol behavior
and content opened by a user are distinct from the removed automatic analytics.
No claim of complete Ghost Mode or zero external connections follows from this
change. No authentication/session/archive material is sent by this patch.
