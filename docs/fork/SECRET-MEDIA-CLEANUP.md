# Archive media handoff and canonical cache cleanup

Source review, 6 November 2026. This change is based on extended-retention source
`c2ddd3d5652801fdc8fa11d514f144e5e1116836`. Its native/device build jobs in
`42265584535` ended cancelled; they do not establish successful compilation,
tests or an IPA. The preserved c55 application and its evidence are separate.

## Problem and intended behavior

The extended-retention deletion hooks excluded captured secret resource IDs from
Telegram's normal forced cache removal. The account coordinator had no eventual
cleanup obligation. A named decrypted cache file could therefore remain after
capture success, rejection, disablement or archive purge. Removing the encrypted
archive did not remove that source file.

The repair preserves Telegram's deletion resource IDs and force flags. Where an
active authorized account can acquire an eligible completed file before unlink,
it hands an owned descriptor to the existing SQLCipher writer. A removed or
replaced pathname does not change the opened inode. Missing pins do not postpone
canonical secret cleanup or authorize reopening an unbounded URL.

The same source owner supports acquiring ordinary completed cloud media on the
account's utility queue before asynchronous storage submission. The public URL
storage entry point retains its existing worker-time opening contract. No
plaintext staging files, extra downloads, authentication proxy, new encryption
algorithm or third-party service are introduced.

## Ownership and limits

The weak registry binds a MediaBox to its authorized account and environment.
Leases bind the event, capture generation, media slot and resource identity.
Global descriptor/byte budgets and per-account limits apply; budgets remain held
until the actual owner releases the descriptor, including worker references.
Protected-data, policy, purge, expiry and disposal changes revoke queued sources.
Revocation never closes a descriptor underneath an active read.

The source-handoff limits are 64 descriptors / 256 MiB globally and 16 descriptors
per account, further bounded by its archive quota. Pre-unlink admission leaves
one descriptor slot free (63 globally / 15 per account) for queued ordinary
media. Sources expire after 60 seconds. A single file above the 256 MiB handoff
limit cannot be imported through this bridge even if a larger archive quota is
selected; the lower-level store's 512 MiB file limit does not override it.
Temporary admission pressure retries without discarding the outbox. Expired or
policy-denied sources become reported media gaps, rather than endless retries
or reopening a replacement pathname. Byte pressure and expiry can still lose
pending media; these bounds are not a performance benchmark or a lossless claim.

Streaming uses bounded reads and the existing encrypted transaction, generation,
duplicate-byte and retention checks. Revocation is cooperative: a copy past its
final validity check can still race with SQLite COMMIT. Generation/purge handling
is a separate serialized operation. This is not an atomic cancellation guarantee.

An open descriptor is not durable storage. Process termination after cache
unlink but before encrypted commit can still lose media. Missing/incomplete
downloads, inaccessible protected data, admission limits and storage failures
remain real boundaries. Never describe this change as lossless recovery or as
recovering content the client never received.

## Verification boundary

The native regressions use real Postbox, MediaBox and SQLCipher operations. They
cover canonical cleanup, missing owners, held writers, path replacement, source
revocation, account isolation and restart after commit. Standalone store cases
cover descriptor lifetime, cancellation, quota and generation behavior.

At this document's creation these native tests are **authored, not executed**.
Portable guards and source review are not Swift type-checking, application
integration or physical-device evidence. The manual `archive-core` CI scope
runs only the archive-store and TelegramCore suites; it produces no app or IPA.
A new device package requires separate successful compilation and signing.

Telegram API policy conflicts for optional extended retention remain documented
in [EXTENDED-RETENTION.md](EXTENDED-RETENTION.md). This cleanup correction does not
establish Telegram approval, production-server parity or permission to bypass
encryption, one-shot access or other server restrictions.
