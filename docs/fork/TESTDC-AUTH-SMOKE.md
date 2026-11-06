# Retained application TestDC authentication smoke

Reviewed on 6 November 2026. **The current workflow is a pre-auth setup-only diagnostic. It cannot invoke the UI authentication test.** The tiny runner compiled and passed signature checks in [run 42284204421](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42284204421), but setup then failed with the closed `internal` category before private seeding completed. Source ordering places the authentication test afterward, so it was not invoked. Cleanup reported true; this alone does not prove a simulator was created or installed. The exact setup exception is unknown. No authentication or physical-device success is claimed. This tooling changes no Telegram application source and does not qualify the newer archive application.

## Current setup-only diagnostic

The workflow hardcodes `--setup-only`. The reviewed execution scope is temporarily `setupOnly`; an `authOnly` call is rejected before subprocesses. After the same source/signature, fresh-owner, install, private-seed, contacts and integrity checks, setup-only execution stops before `xcodebuild test-without-building` or app authentication. No seed/container ownership requirement was relaxed and no speculative Xcode-selection fix was made.

The result explicitly records `execution_scope: setupOnly` and `setup_completed`. Even a completed setup keeps `passed`, `native_xctest_passed` and `authentication_completed` false, with no auth stages. A green diagnostic job means only setup completed and cleanup succeeded; it is not a login pass. The closed `phase` identifies boundaries such as plan binding, runtime inventory, simulator creation/boot, install, data/app container checks, installed integrity, private seeding, contacts and final integrity. `exception_kind` contains only an exact allowlisted builtin category or `otherException`; messages, arguments, paths and tracebacks are never exported. The existing request remains unchanged until a separate reviewed diagnostic request is published.

The following sections describe the retained-app provenance and the dormant bounded auth scenario. Authentication remains blocked by the current setup-only review scope until a concrete setup repair and a separate internal review permit it.

## Exact source and evidence

The smoke reuses the existing full simulator application at `c55e19c4f5cbe0d730499035d0a0ef712f6903a9`, source run `42174474046`, artifact `13490005243`, IPA SHA-256 `df7a313e263440d92f2ab503e7ee1c1afb328774f6d631b3d052169f2f2e8e10`. A device IPA or account-free Settings host cannot substitute for it. The eleven relevant application source files, five Swift/helper files and two CI files are hash-bound by the reviewed manifest; the final driver hash is separately bound by its receipt.

The precondition is the actually passed startup job `127848100056` from run `42204637743`, not the unrelated Projection suite. It binds startup artifact `13495439062`, its exact replay/launch proofs, source signing/code invariance and two actual authorization-screen launches with explicitly seeded fresh private storage. The historical overall run contained a failed Projection job; the metadata gate requires only the independently successful startup job and does not relabel the overall run as green. The earlier control did not verify automatic fallback, authentication, physical installation or new archive source.

Root reviewed the bounded source, startup evidence, authoritative GitHub run/job/artifact metadata and independent security/state review before enabling the fixed scenario. The reviewed receipt is an internal correctness gate within the user's authorized authentication testing; it is not a demand for another user approval or credentials. No successful 41-case Projection result is fabricated or required for this independent auth scenario.

## Trigger and native execution

The workflow triggers only when `ci/testdc-auth-request.json` changes on `ci/testdc-auth-smoke`. Reviewed tooling is published before a separate request commit with a fresh UUIDv4 nonce. The actual request is deliberately absent from the tooling change. This does not run on `ci/verify-latest` or require the user to dispatch it manually.

It uses the proven `xcode-27` hosted runner, checks Xcode 27.1 / build 27A9275 and simulator SDK 27.1, and fails closed on drift. It compiles only a tiny Apple XCTest UI target; there is no Bazel or Telegram rebuild. Its own queue has `cancel-in-progress: false` and does not replace healthy Projection or application builds.

The driver creates one uniquely owned fresh iPhone 15 Pro / iOS 27.0 simulator, verifies the exact simulator IPA, removes only extensions from its private copy and ad-hoc re-seals the main app while preserving code/frameworks and other entitlements. It verifies the installed app, seeds explicit fresh private storage and denies contacts. The stock `--ui-test` flag creates a new `.test` authentication environment and wipes its test subtree on every launch. It must never run against a personal installation or be described as restart-persistence testing.

The one fixed identity and five-X public code follow [Telegram's official Test Accounts documentation](https://core.telegram.org/api/auth#test-accounts). The reserved prefix alone does not select TestDC; the unchanged app's test-environment path does. Input uses normal native UI and unchanged Telegram authentication. There is no personal number, private OTP access, session injection, custom authentication proxy or bypass.

Signup, password/2FA, terms, unexpected routes, flood/error states and timeouts stop the attempt. There are no resends, number cycling, retries, signup submissions, messages, conversation navigation or remote logout/delete/reset. The `--delete-test-account` path is never used. The ordinary app may automatically synchronize or render public sandbox dialogs before the test observes the genuine authorized root and terminates; this is not a fresh/exclusive-account or zero-background-sync claim.

## Deadlines and success criteria

Only the outer `xcodebuild test-without-building` deadline is 600 seconds. Compilation remains bounded to 180 seconds, fresh boot preparation to 240 seconds, and the Swift Welcome 30-second / route 45-second / sign-in 60-second budgets remain unchanged. The job is bounded to 30 minutes. Earlier unrelated framework/UI commands took 167 seconds including a 23-second case, 401 seconds and 347 seconds; these justify framework headroom, not a claim that this unrun auth scenario failed at 180 seconds or passed at 600 seconds.

Success requires native exit 0, exactly one started/passed expected XCTest, a legal ordered fixed-stage trace ending in the real `AyuLaunch.conversationsVisible` window marker, source/code/runner/signature checks, and successful deletion with the owned simulator UUID/name proved absent. Cleanup attempts termination and shutdown even after native failure; those best-effort actions are not independently required to succeed. A password branch's internal `.loggedIn` enum is not used as authorization evidence. Cleanup failure prevents success. Cleanup proves the owned UUID/name, not a global absence of unobserved simulator clones; any observed clone requires a concrete follow-up before a complete cleanup claim.

## Credential and output boundary

GitHub token access is scoped to checkout, metadata and retained-artifact download steps. Checkouts do not persist credentials. Native child processes receive an allowlisted environment; GitHub/API credential variables are unset before that step. No repository Secrets are required or read by this workflow. The app uses its existing compiled API identity without extracting private values.

Native stdout/stderr, Xcode activity, automatic attachments, `.xcresult`, entitlements and app/session material stay private and are removed before export. Both attachment lifetimes are `keepNever`; this does not guarantee that the frameworks never take transient snapshots. No screenshots, video, UI trees, raw or filtered auth logs, account databases, Keychain, `.tempkey`, session tokens or crash diagnostics are uploaded. The only upload is freshly constructed, closed-schema JSON containing fixed source/run identifiers, enums and booleans. Its validator rejects unknown/raw fields and true physical-device, restart-persistence or application-recompiled claims. Job/process termination before cleanup cannot yield a passing report; the hosted machine is disposable and no session directory is cached.

## Verification boundary

The tiny runner compiled and passed signature checks in the first run; its UI test did not execute. The setup-only diagnostic is not yet run at this checkpoint. Code-field accessibility/value/focus at 1×1 / alpha 0.01, the failing setup boundary, generated-plan binding, reinstall preserving the seed, and the server's acceptance/public-account state remain native uncertainties. They must fail honestly without weakening the auth or source checks. A resulting pass can establish only “this retained c55 application reached the genuine authorized root using official public TestDC authentication on a simulator.” It does not establish production DC login, the new archive source, archive/Ghost/filter behavior, durable restart, SideStore installation, physical iPhone success or performance.

Portable tests run without contacting TestDC or invoking Apple tools:

```text
python3 -m unittest discover -s tools/fork_baseline/testdc_auth -q
```

The 107 portable cases include the existing disabled-state and enabled-missing-arguments checks, plus mocked full setup execution. Injected synthetic failures exercise every requested setup boundary without leaking exception content or invoking the authentication command. Setup completion cannot become an auth pass, publication must match the reviewed execution scope, and cleanup failure is not green. These fixtures neither run Apple tools nor diagnose the actual unknown exception. No fixture enables or executes native authentication.
