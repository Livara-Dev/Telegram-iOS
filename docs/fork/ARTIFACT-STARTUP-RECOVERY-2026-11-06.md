# Retained-build startup recovery — 6 November 2026

Application source `5d241267735b3958c225681d173853568bab705d` compiled for
ARM64 devices and the simulator in [run 42279866706](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42279866706).
All 270 actual TextFormat cases passed. The subsequent simulator installation
command timed out while its direct child was still alive; shutdown and deletion
also timed out. No application launch, screenshot or video occurred. This run
does not reproduce a black screen, an authentication failure or an app crash.
Its simulator job remains failed and its original packaging job remains skipped.

Both compiled artifacts are preserved:

| Component | Original artifact | Outer ZIP SHA-256 |
| --- | --- | --- |
| Unsigned device, build 1001 | 13539444219 | `48a9c84a22cfffdc8c5ab5bdfdc2d60b3a84c1df1c1c34313b61594a0bd84cf9` |
| Simulator, build 1001 | 13542840666 | `89407b99ce5e76d3314dcc873950b754756e851eda760f063cb4ee02c4ba833b` |

The retained simulator IPA itself has SHA-256
`01f32ba769f876c20bed6090c54a8066d27f09cc73d75d890e7f57c435219e0a`.
Its original package checksums, source/license materials and 270 ordered native
test outcomes were independently verified. Archive/Core qualification remains
the separate source-5d run `42272821047`, with 129 Store and 835 Core passes.

## Recovery contract

The new `resume-qualified-device-delivery.yml` workflow reuses these exact
artifacts. It does not invoke Bazel, rebuild Telegram, fetch build dependencies,
use Telegram API secrets or sign a device application. A separate request on
`ci/qualified-device-startup-resume` starts one recovery after reviewed tooling
publication; ordinary code/documentation pushes do not dispatch it.

The macOS job verifies the failed original graph, immutable producer identities,
artifact hashes, source, license materials and TextFormat results. It then uses
the unchanged source-5d startup helpers on a fresh hosted runner with a private
CoreSimulator device set. Every simulator operation targets that set and its
exact owned UUID. Canonical temporary paths avoid macOS `/var` alias mismatches.
Only the small screenshot checker is compiled. Existing command deadlines,
code/resource checks and fresh-private-storage requirements remain enforced.

Two real controller-visible launches, nonblank screenshots, stable private
account-store identity across restart, a successful recording and exact owned
simulator deletion/absence are mandatory. A separate cleanup receipt closes the
older helper's ability to retain `passed: true` alongside cleanup errors.
Failed cleanup never authorizes packaging; backing files are not removed from
an unverified running simulator. The default simulator set remains untouched.

Only a successful new startup job can authorize the Linux copy-only packaging
job. Its manifest distinguishes the failed original simulator job, successful
compile step and new successful startup origin. It preserves device code bytes,
removes the same six extensions, retains complete native/TextFormat/GPL
evidence and writes SHA-256 checksums. The result remains unsigned and requires
independent Apple signing. Native recovery is pending until that workflow runs.

The underlying cause of the original CoreSimulator installation hang has not
been established. Private-set isolation is a controlled repair path requiring
native verification, not evidence of a particular Apple service defect.

The newer `d020bfd7` Core candidate separately passed 854 cases, including 16
recipient-expiry and three startup-order regressions. Its coordinator relocation
is **not present in the frozen source-5d binary**. Neither component tests nor
simulator startup establish live server feature behavior, production login,
SideStore signing, physical iPhone installation or complete release qualification.
