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
removal replace that path in schema 2; the measured result follows below.

[Raw measurements and artifact identity](evidence/archive-benchmark-before.json)
retain the exact samples and limitations. [Downloadable logs](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795234429/artifacts/13304637808)
expire on 2026-11-11. A different hosted run can have different scheduling and I/O
load; this small sample must not be extrapolated to a phone or used as a latency
guarantee.


## Schema-2 result

Commit `9bfc4c87` passed 39 native tests and iOS simulator compilation in
[run 41795912590](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795912590).
The unchanged workload measured a mean of **0.340 ms**, with ten samples ranging
from **0.310 to 0.393 ms**. Migration, rollback, two-live-connection counter
consistency and partial/multiple-chunk retention tests also passed.

This supports an improvement for this synthetic append workload. The runs used
separate hosted machines, so the comparison is not a controlled phone benchmark
or a general speedup guarantee. Large atomic purges, initial migrations, physical
disk consumption and simultaneous writer contention still require measurement.

[Exact samples and artifact metadata](evidence/archive-tests-9bfc4c87.json) and
[downloadable logs](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41795912590/artifacts/13304894197)
record the result. The logs expire on 2026-11-11.
