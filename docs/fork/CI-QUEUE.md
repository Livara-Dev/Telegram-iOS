# Qualification queue

The automatic build entry point is `ci/verify-latest`. Development branches and
PR updates do not automatically start hosted macOS work. Advance the verification
branch to the exact reviewed source batch once; do not create a throwaway commit
or retry a healthy run merely to get a different runner.

`archive-integration.yml` uses the global `ayugram-qualification` concurrency group
with `cancel-in-progress: false`. Its integration and unsigned device jobs may run
alongside each other on that one revision; the simulator app still requires all
integration suites to pass. A newer pending batch supersedes an older pending
batch while the active run is preserved. Standalone storage, compatibility and
legacy baseline workflows remain available through explicit manual dispatch.
These are diagnostic alternatives, not automatic duplicate runs.

## Observed queue failure on 2026-11-04

Job 126686204417 in run 41862050201 was canceled with no runner assigned and no
steps executed. Its public check annotation says:

> The job was not acquired by Runner of type hosted even after multiple attempts

The compatibility check 126685062470 in run 41861738456 also recorded:

> Due to capacity constraints, jobs targeting macOS arm64 runners may experience longer queue times.

These are hosted runner acquisition failures, not compiler errors, test passes,
missing signing profiles, or evidence of exhausted billing. The GitHub connector
can read runs/jobs and retry jobs but does not expose cancellation or check
annotations. These annotations were read from the public GitHub check endpoints;
no token or private credentials were exported. Active work is preserved.

Previously submitted runs retain their own workflow and concurrency settings;
changing YAML cannot move, cancel or reprioritize those existing jobs. Do not
claim the backlog was canceled or that GitHub guarantees FIFO priority. The new
policy prevents additional feature-push fan-out and limits subsequent submissions
to one active qualification plus its latest pending successor.

An unsigned device artifact is build output only. Its source commit, device
Mach-O platform and SHA-256 must match its manifest; same-commit integration,
independent signing and physical iPhone validation remain separate gates.
