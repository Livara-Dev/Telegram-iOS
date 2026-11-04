# Archive performance observations

These are measurements of the standalone module on hosted macOS, not iPhone 15
Pro performance or Telegram app behavior. No cold-start, scrolling, memory or
battery benchmark has been run. Test completion alone is not a performance claim.

## Initial retention implementation

Commit `3f329930` passed 32 native tests and iOS simulator compilation in
[run 41795234429](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795234429).
The benchmark seeds 10,000 distinct synthetic text snapshots in batches of at
most 256, verifies they remain present, warms up one append, and measures ten
single appends with unique IDs. Timing uses a monotonic clock and includes the
asynchronous API, callback and XCTest expectation/wait overhead. There is no
timing threshold or variance gate. This run measured a mean of **29.648 ms**,
with samples ranging from **18.813 to 52.306 ms**.

The initial retention path scans the history with COUNT/SUM/window aggregation
for every batch. Transactional database counters and bounded oldest-first
removal are implemented in the schema-2 draft; no improvement is established until the same
benchmark and functional/migration tests run on the changed code.

[Raw measurements and artifact identity](evidence/archive-benchmark-before.json)
retain the exact samples and limitations. [Downloadable logs](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795234429/artifacts/13304637808)
expire on 2026-11-11. A different hosted run can have different scheduling and I/O
load; this small sample must not be extrapolated to a phone or used as a latency
guarantee.
