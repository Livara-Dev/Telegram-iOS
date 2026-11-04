# Audit-stage source guard

Run with Python 3.10 or later, from the repository root:

```sh
python3 -m unittest discover -s tools/fork_audit/tests -v
python3 tools/fork_audit/check.py
```

The guard compares the working source tree (including staged, committed and
untracked additions) with the recorded Telegram-iOS commit. At this stage, only
audit/build tooling, documentation, the GPL text, `.gitignore` changes and removal
of the inherited release workflow are allowed. App, authentication, networking,
encryption and dependency source changes fail. The provenance document records
top-level Git submodule commits and SHA-256 hashes of the build manifests.

Added files are checked for signing-file extensions, private-key markers, literal
credential assignments and direct Python imports outside the standard library or
these local tools. Diagnostics never print matched secret values. Only the
explicit synthetic credential values listed in the implementation are exempted,
and only within test files. Ignored local build outputs are outside this check;
the build runner must remain isolated and discard its private workspace/cache.

This is a scoped regression check, **not** a comprehensive secret scanner,
dependency vulnerability assessment, recursive source completeness proof, or
proof that the app emits no telemetry. Git commit/hash equality establishes
integrity relative to the recorded source, not that a dependency is trustworthy.
Recursive submodule readiness is checked by the build preflight. Each later
feature needs a separately reviewed revision to this audit-stage change policy;
do not broaden the allowlist merely to make a build green.
