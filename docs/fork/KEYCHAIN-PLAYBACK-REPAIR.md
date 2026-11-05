# Keychain and encrypted playback repair qualification

Run [42008457157](https://github.com/Livara-Dev/Telegram-iOS/actions/runs/42008457157)
built source `32ee28da`. Both complete-application jobs stopped compiling
`Passcode.swift:127`: `kSecUseAuthenticationUIFail` is deprecated and the module
correctly treats warnings as errors. No startup smoke or new IPA was produced.

The repair uses the documented public replacement: a fresh `LAContext` with
`interactionNotAllowed = true` passed through `kSecUseAuthenticationContext`.
The same deprecated archive Keychain option and test expectations are updated.
Access groups, service namespaces, non-sync flags, Data Protection and failure
semantics are preserved. Ordinary interactive passcode reads are unchanged;
compiler warnings have not been suppressed.

The native controls distinguish playback from storage. The protected synthetic
MP4 decoded, and byte-for-byte archive reopening passed. Both the encrypted and
in-memory custom loader failed with AVFoundation -11800 / POSIX 1 after exactly
one metadata request and zero byte-range requests. These results do not identify
a SQLCipher or fixture corruption problem.

The playback candidate gives each asset an opaque random URL so failed loads and
different accounts cannot share a resource cache identity. Completed local media
also advertises `isEntireLengthAvailableOnDemand` on supported iOS versions.
The `.forbidAll` external-reference restriction and exact-URL delegate guard stay
in place. No private API, HTTP listener, external upload or plaintext-media
fallback is introduced. Native decode tests, including failure followed by two
different valid movies, must pass before this is called a working repair.

## Focused validation before any full build

The existing qualification workflow accepts `validation_scope: focused` through
manual dispatch. On `integration/ayu-current` it runs only PasscodeCore, archive
storage and archive playback tests. Device and simulator application builds are
skipped even if `unsigned_device` is true. The evidence explicitly identifies the
three requested targets and does not claim full integration or an IPA.

Only after this same source passes the focused native tests should it advance to
`ci/verify-latest`, whose push event retains normal full qualification. Active
jobs are preserved. This avoids repeating full builds to diagnose a small repair.
A successful device compilation still requires the independent real-app startup
smoke and physical SideStore testing before claiming the black-screen issue is
resolved on iPhone.
