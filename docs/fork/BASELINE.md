# Baseline execution record and runbook

## GitHub continuation

The user authorized the actual fork `Livara-Dev/Telegram-iOS`. Both prepared
commits were published to `fork/00-feasibility-baseline` as `e94b5a8d` and
`ee6fa54a`; remote trees match the verified local trees exactly. The first
[Actions run](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41791930344)
failed at workflow validation, before any runner started: `runner.temp` was not
an allowed context in `jobs.baseline.defaults.run.working-directory`. The workflow
now uses a relative checkout directory and checks API Secrets before the expensive
toolchain/runtime setup. The earlier discovery record below is historical.

The follow-up commit `a7b9d5ff` started a hosted runner successfully in
[run 41791996708](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41791996708).
The code is reviewable in [draft PR 1](https://github.com/Livara-Dev/Telegram-iOS/pull/1).
Test report retention now copies only bounded, credential-redacted `test.xml` and
`test.log` files for the explicit target before the private Bazel root is removed,
including when tests fail. Missing/unsafe/oversized files are recorded as omissions.

Run 41791996708 completed the recursive checkout on macOS 27.0.1 and uploaded
diagnostic artifacts. It then failed a portable test that compared a canonical
Bazel path against an unresolved `/var` alias; macOS resolves that alias through
`/private/var`. The test now expects the canonical path and exercises a directory
symlink explicitly. No application compile ran in that job. Superseded branch
runs are now canceled when a new fix is pushed, avoiding redundant cold builds.

[Run 41792257376](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/41792257376)
passed all 95 portable checks, API Secrets validation, pinned toolchain verification
and iOS 26.5 runtime installation. Its build invocation stopped at the upstream Bazel
version probe, before application compilation: the wrapper prepended startup
options to `--version`. The wrapper now preserves the executable's exact version
fast path; binary hash/version verification remains enabled.

## Current result

The current checkpoint is **not a working iOS baseline**. The local executor is
Linux and cannot execute the Apple SDK build. The preflight was run here and
correctly exited nonzero with:

```text
Baseline blocked: An Apple Silicon macOS runner is required; Linux cannot build or sign this iOS app
```

No application compile, simulator launch, account login, iOS unit/integration test,
signed build, iPhone installation or performance benchmark has passed in this
session. No IPA/download URL exists. Portable Python tooling tests and source
integrity checks are recorded separately below.

Executed on Linux: **96 passing portable Python tests** (30 build-tooling,
22 source-guard, 44 upstream build-system tests), plus passing source integrity
and `git diff --check`. [Raw output](evidence/portable-tests.txt) and
[machine-readable record](evidence/verification.json) retain the earlier 94-test
checkpoint; hosted run 41792257376 additionally exercises the macOS path regression.
The latest local test also verifies that a Bazel shutdown timeout or OS error
preserves the build result, still cleans private configuration and emits only a
fixed warning without exception details. Mocked
build orchestration tests are not application compilation or iOS feature tests.

## Prepared GitHub-hosted pipeline

The workflow is [fork-baseline.yml](../../.github/workflows/fork-baseline.yml).
It runs on pushes to main/master/the baseline branch and by manual dispatch. It
uses GitHub-hosted `xcode-27`, not a personal Mac. The image advertises the pinned
Xcode 27.1 and macOS 27; actual versions are checked at runtime. The image is
mutable/public preview, so its exact observed version is written to evidence.
See [hosted-runner evidence](HOSTED-RUNNER.md) for simulator and resource limits.

The runner checks out the exact event SHA and recursively pinned submodules,
downloads Bazel 9.2.0 over HTTPS and verifies the `versions.json` SHA-256 before
execution, and attempts Apple's iOS 26.5 simulator download. Existing tests require
an iPhone 17 on that runtime. Failure to acquire it is a test blocker, not a reason
to skip tests or claim a build passed. Upstream Make.py builds the full Telegram
target and runs `//submodules/TextFormat:TextFormatTests` explicitly; the default
AllTests suite currently references a dangling target. The watch application is
not embedded in this baseline (`--embedWatchApp` is not enabled); no watch build
or watch runtime validation is claimed.

The workflow overrides the two relative submodule URLs in the checkout's local
Git configuration to their original TelegramMessenger repositories. Otherwise
cloning the fork would incorrectly look for `Livara-Dev/rlottie` and
`Livara-Dev/tgcalls`. The source `.gitmodules` and pinned gitlinks stay unchanged.

No runtime dependency has been added to the application. The pipeline relies on
upstream locked build dependencies and one official immutable-pinned artifact
upload action (`actions/upload-artifact` v4.6.2,
`ea165f8d65b6e75b540449e92b4886f43607fa02`; tag resolved 2026-11-04).
This is integrity/provenance evidence, not a completed supply-chain vulnerability
audit. The underlying watch SwiftPM resolution gap remains documented.

The workflow uploads explicit build-output/evidence directories, never the source
checkout, private build configuration, keychains or signing directories. Logs
passing through the build wrapper redact API ID/hash values. Successful packages
carry SHA-256 and a manifest stating simulator, unsigned device or unverified
signed-device status. Failed jobs may publish only diagnostics. Artifacts expire
after seven days. No GitHub release, App Store or TestFlight upload is configured.

## Account discovery and required setup

Connected GitHub login: `Livara-Dev`. The connector's installed/public searches
found `Livara-OS` and `Livara-Chat`, neither the requested iOS fork. The latter's
README explicitly calls it a product/documentation showcase. A direct lookup of
`Livara-Dev/AyuGram-iOS` returned 404 on 2026-11-04. No unrelated repository was
modified. The available connector exposes commit/tree/ref writes but no repository
creation/fork endpoint, no Secrets-management endpoint and no new-workflow-dispatch
endpoint. These are tool capability limits, not an approval rejection.

Subsequent installation enumeration found the user's new fork under
`Livara-Dev/Telegram-iOS`, with its master at the exact pinned upstream commit.
The user explicitly authorized that destination. Repository creation and access
are resolved; no rename is required. The connector writes commits and updates
the feature branch with a non-force lease. Master remains unchanged.

Required **repository Secrets**: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, obtained
from the owner's registered Telegram application. No invented/demo credentials
are accepted. The chosen bundle ID is `org.livara.ayugram`; its Apple registration
availability is unverified. Signing additionally needs the owner's Apple team,
certificate/private key and matching provisioning profiles for all enabled
extensions, supplied outside Git. The default hosted workflow requests no signing
keys and cannot produce an independently signed installable artifact by itself.

## Local verification commands

```sh
python3 -m unittest discover -s tools/fork_baseline/tests -v
python3 -m unittest discover -s tools/fork_audit/tests -v
python3 -m unittest discover -s build-system/Make/tests
python3 tools/fork_audit/check.py
git diff --check
```

The source guard verifies the audit checkpoint against the immutable official
tree, build-manifest hashes, and added-file secret/import checks. It intentionally
rejects application-source changes in this stage. Later feature commits must
explicitly evolve its reviewed scope without discarding authentication, crypto,
dependency and telemetry regression protection. It is not a runtime security test.

For the actual Mac build commands and private-input lifecycle, see
[the wrapper runbook](../../tools/fork_baseline/README.md). Windows installation
and Sideloadly limitations are in [WINDOWS_INSTALL.md](../WINDOWS_INSTALL.md).

## Acceptance still required

1. Successful hosted checkout/toolchain/runtime preparation and cold full build.
2. Passing explicit iOS test target with preserved logs; add Postbox/Core targets
   as archive implementation begins. No empty/omitted test suite counts as a pass.
3. Telemetry-hardening diff tested before real-account smoke checks.
4. Signed-device build with verified nested signatures/profiles/entitlements,
   install/launch and login using the owner's registered app.
5. Exact corresponding source and notices paired with the final artifact.
