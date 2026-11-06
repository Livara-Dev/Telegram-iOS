# Baseline build tooling

This wrapper runs the official `Make.py` full application build. It does not implement
the requested messaging features. Python tests validate the tooling only; a successful
iOS build, on-device launch, and messaging tests remain separate evidence.

Use a fresh, isolated Apple Silicon macOS runner matching `versions.json` and a
complete recursive checkout. `prepare_host.py` provisions the checksum-pinned Bazel
executable; `baseline.py` verifies it again before execution. Simulator tests also need
the iPhone 17 / iOS 27.0 runtime pinned in `TextFormatTestRunner`.

Provide these environment variables through CI variables/secrets:

- `FORK_BASELINE_BAZEL_PATH`: absolute path to the verified executable.
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`: your own application from my.telegram.org.
- `IOS_BUNDLE_ID`: `org.livara.ayugram` for this fork.
- Signed device builds only: `APPLE_TEAM_ID`, `FORK_BASELINE_SIGNING_DIR`.

Commands from the repository root:

```sh
python3 -m unittest discover -s tools/fork_baseline/tests -v
python3 tools/fork_baseline/baseline.py preflight
python3 tools/fork_baseline/baseline.py simulator --build-number 1
python3 tools/fork_baseline/baseline.py unsigned-device --build-number 1
```

The simulator mode builds `debug_sim_arm64`, captures that IPA, then runs the explicit
`//submodules/TextFormat:TextFormatTests` target. It publishes the captured artifact only
after tests pass. The default upstream all-tests target has a known dangling target and
is not substituted for a successful test result. The unsigned device mode builds
`release_arm64` with provisioning and legacy bundle signing disabled. It is an unsigned
build artifact, not an installable independently signed release. Device mode does not
run simulator tests, and its manifest records `not_run`.

The Actions `native-only` scope runs all eight integration suites and the
account-free production options renderer in the same workflow, on separate
macOS runners. Neither job builds the complete messaging application. The
standalone `custom-options-ui` scope remains available for renderer diagnostics.
The run-level concurrency policy preserves an active qualification and keeps
only its latest pending successor. Submit one consolidated source revision;
do not dispatch a second identical scope while it is active or queued.
Explicit `custom-options-ui` diagnostics use a separate queue so a failed
renderer job can be investigated without stopping healthy native suites.
Use that scope only for a distinct diagnostic or repair; do not duplicate an
already-running renderer check. Its queue also preserves the active job.

`core-only` runs only the fixed `//submodules/TelegramCore:TelegramCoreTests`
bundle for account/archive/filter integration repairs. It builds neither the
complete application nor a device package and does not replace the full
eight-suite release gate. Its separate queue preserves an active full build,
so a verified Core failure can be repaired and tested while an older device
job finishes. Submit only one source revision for each distinct repair.

For the zero-case Projection startup failure in run `42134690056`, the existing
`probe` scope accepts `replay_run=42134690056` and
`replay_source=17033b099458c258236b5a01411ab6efb45b37b6`. This selects a bounded
replay of that exact retained synthetic test product instead of the ordinary
host API probes. It verifies the GitHub origin, artifact digest, captured files,
toolchain and sanitized plan, then runs the 41 expected cases once on a fresh
owned simulator. No Bazel compilation, Telegram API secrets, account data or raw
result-bundle upload is involved. Its separate source-scoped queue preserves
active qualification jobs. This historical diagnostic cannot qualify a later
application revision or turn a zero-case source run into a passing run.

After qualification passes, advancing `ci/verify-latest` starts the full
application/device workflow automatically. The simulator application job first
runs all eight suites and then reuses that invocation's private Bazel outputs
for the app build. Compiled outputs and generated API configuration are never
exported into the shared dependency cache.

Artifacts go to `build/fork-baseline-<mode>/`: `Telegram.ipa`, `SHA256SUMS`, and
`manifest.json`. Existing destination directories are rejected to prevent stale
artifacts being mistaken for a new build. The manifest records the commit, pins,
mode and test status. It never claims runtime or signature verification.

`baseline.py device` is available when signing assets exist. Provision a valid identity
in the ephemeral runner's keychain before invoking it. `FORK_BASELINE_SIGNING_DIR` must
be outside the checkout with `profiles/` and `certs/` directories containing your own
profiles for every enabled app/extension. The wrapper does not import keys, fetch a
signing repository, use Telegram's signing identity, publish a release, or verify the
resulting signature. Independent codesign, entitlement and device-install checks remain
required. No Apple team is needed in unsigned modes; `UNSIGNED00` is an explicit build
placeholder, and is rejected for signed device builds.

The wrapper uses only Python's standard library. It validates configuration strings
before upstream interpolation, redacts API values from relayed build output, creates
private temporary configuration/cache directories, disables remote build services and
external Bazel rc files, and cleans generated configuration and private Bazel state on
normal completion or exceptions. An abruptly terminated runner must be destroyed;
cleanup cannot be guaranteed after SIGKILL or host failure. The IPA necessarily embeds
the application's API configuration. Restrict artifact access as appropriate. Test
fixtures are synthetic, and no real credentials belong in this directory.

No new dependency is introduced. Submodule commit and Bazel checksum checks establish
integrity, not a complete security or license audit of upstream dependencies. Setting
`app_center_id=0` and disabling iCloud/Siri do not prove absence of all upstream telemetry.
The audit and release gates remain applicable.
