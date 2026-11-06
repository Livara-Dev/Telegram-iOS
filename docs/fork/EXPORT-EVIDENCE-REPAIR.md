# Completed-export evidence repair

Prepared on 2026-11-06. This tooling performs no application build, execution,
signing, application-source change or IPA mutation. Tooling publication alone
does not trigger a repair; the explicit request must remain absent until the
original delivery is successful and its artifact IDs have been audited.

Original native artifact: run 42272821047, artifact 13533034081.
Required native ZIP: the verified archive-core artifact downloaded locally
SHA256: 7b67ad73d3338a48fb1e04b009db6c2650b9109302a573edd49c0ae458f2790c
It contains 12 checksum references plus SHA256SUMS. The final export currently
copies seven hashed files and SHA256SUMS, omitting FIVE hashed files:
  evidence/qualification/native-evidence/host.json
  evidence/qualification/native-evidence/host.log
  evidence/qualification/native-evidence/integration-tests.log
  evidence/qualification/native-evidence/portable-tests.log
  evidence/qualification/native-evidence/runtime.log

The verified simulator intermediate contains three further omitted files:
  fork-evidence/textformat/collection.json
  fork-evidence/textformat/test.log
  fork-evidence/textformat/test.xml
They belong in final evidence/simulator/textformat/ at the same names.

After the current run succeeds, audit and retain the original final export and
simulator artifact. A separate packaging-only Linux workflow is permitted;
another full application build or qualification pipeline is unnecessary.
The repair script requires the simulator ZIP hash recorded in the original
final provenance, validates its source/run/IPA/manifest/build-proof binding and
its passed nonzero TextFormat execution. It reuses the reviewed native gate to
validate both native suites (129/835) and all 28 added methods from the cached
native ZIP. It writes a NEW export directory, copies all original files
unchanged, adds only the eight evidence files and export-repair.json, and
regenerates the root SHA256SUMS. The existing nested native SHA256SUMS is copied
unchanged and becomes complete when the five missing files are restored.

Example AFTER final success and artifact audit (replace only local input paths):
  python3 tools/fork_baseline/export_repair/repair_export.py \
    --export /path/to/original-final-export \
    --native-zip /path/to/run422728-archive-core.zip \
    --simulator-zip /path/to/verified-device-delivery-simulator.zip \
    --output /path/to/new-repaired-export \
    --helper tools/fork_baseline/device_delivery.py \
    --inventory tools/fork_baseline/device_delivery_inventory.json

The original manifest, delivery-provenance, GPL materials, screenshots, and IPA
remain byte-for-byte identical. Check the IPA hash before/after independently.
Only the export ZIP/hash, root checksums, added evidence, and repair record differ.
No new source, startup, signing, physical-device or release claims are added.

Future exporter changes could copy the same already-validated native and
TextFormat evidence during the original export. That is separate from this
copy-only repair and does not require rebuilding an application.

## Corrected downloadable export

`.github/workflows/repair-qualified-device-export.yml` contains exactly one
Ubuntu 24.04 job. Publish reviewed repair tooling first on the explicit branch
`ci/qualified-device-export-repair`. Its four modules are in
`tools/fork_baseline/export_repair/`:
  repair_export.py
  publish_repaired_export.py
  test_repair_export.py
  test_publish_repaired_export.py
The tree must retain the reviewed existing `device_delivery.py` and
`device_delivery_inventory.json`. The request file is intentionally absent from
this preparation; neither qualification nor a repair is claimed executed here.

Only AFTER original run 42279866706 succeeds and actual final/simulator artifact
IDs are known, add ci/qualified-device-export-repair-request.json in a separate
reviewed commit. No request file is included in this tooling preparation. Its exact fields are:
  schema: integer 1
  delivery_run: integer 42279866706
  source_commit: string 5d241267735b3958c225681d173853568bab705d
  final_artifact_id: actual completed original final artifact ID (integer)
  simulator_artifact_id: actual completed original simulator artifact ID (integer)
No other fields or source/run are accepted. Request IDs must match the unique
expected artifact names in the fixed original run's read-only API inventory;
they cannot select arbitrary artifacts. Native artifact 13533034081 and its
full ZIP hash are pinned in code.

The wrapper requires the original delivery and native runs to be completed and
successful at their exact workflow/source origins, and all four original
delivery jobs successful. It pins original tooling SHA
2f68bca8027980250bf8f9b0328bb1e063fc55c1 separately from app SHA 5d241267....
It downloads exactly final+simulator+native artifacts via the normal GitHub
read-only Actions token. It checks ZIP digests, manifest/provenance bindings,
the original IPA's complete static unsigned ARM64/iPhoneOS inspection, native
129/835 and 28 added methods, and actual nonzero passed TextFormat execution.
After copy-only repair it repeats the IPA static check and records input IDs,
digests and separate repair origin in repair-publication.json. It refreshes
outer SHA256SUMS and uploads corrected-qualified-main-app-only-unsigned-device-
5d241267-42279866706, retained 14 days. The new export ZIP digest can differ;
the candidate IPA, original manifest/provenance and GPL material bytes cannot.
There is no Xcode/Bazel/app build command, app execution, Apple signing,
Telegram API Secrets, dependency cache, application revision change or CI dispatch call.

The new repair tooling is licensed under GPL-2.0-or-later; see the SPDX headers
and repository COPYING. Copyright (c) 2026 AyuGram iOS contributors.
