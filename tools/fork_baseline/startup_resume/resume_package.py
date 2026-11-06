# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline packaging of retained 5d builds after a genuinely new startup pass.

No network, app build, signing, environment-derived compile outcomes or CI writes.
The original simulator job remains failed; its compile step remains successful.
"""
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import resume_origins as origins

# Deployed beside the other resume modules under fork_baseline/startup_resume;
# the already reviewed general validators and inventory live one level above.
TOOL_HELPERS = Path(__file__).resolve().parent
if not (TOOL_HELPERS / 'device_delivery.py').is_file():
    TOOL_HELPERS = TOOL_HELPERS.parent
sys.path.insert(0, str(TOOL_HELPERS))
import device_delivery as native_validation

BUILD = 1001
SIM_IPA_SHA = '01f32ba769f876c20bed6090c54a8066d27f09cc73d75d890e7f57c435219e0a'
FIXED_MATERIALS = {'COPYING', 'LICENSE-NOTICE.md', 'CORRESPONDING-SOURCE.md'}
BASELINE_FILES = FIXED_MATERIALS | {'Telegram.ipa', 'manifest.json', 'SHA256SUMS'}
MAX_IPA = 2 * 1024**3
MAX_TEXT = 2 * 1024**2


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def digest(path):
    require(not Path(path).is_symlink() and Path(path).is_file(), 'regularFileRequired')
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def document(path, maximum=MAX_TEXT):
    path = Path(path)
    require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= maximum,
            'documentBounds')
    return origins.document(path.read_bytes(), maximum)


def checksums(path):
    require(Path(path).stat().st_size <= MAX_TEXT, 'checksumBounds')
    values = {}
    for line in Path(path).read_text(encoding='ascii').splitlines():
        fields = line.split('  ', 1)
        require(len(fields) == 2 and len(fields[0]) == 64
                and all(c in '0123456789abcdef' for c in fields[0]), 'checksumFormat')
        name = fields[1]
        p = PurePosixPath(name)
        require(name and not p.is_absolute() and '..' not in p.parts and '\\' not in name
                and p.as_posix() == name and name not in values, 'checksumPath')
        values[name] = fields[0]
    return values


def checksum_tree(root):
    """Require complete inventories; do not preserve orphaned checksum names."""
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'checksumRoot')
    files = []
    for path in root.rglob('*'):
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), 'unsafeTreeEntry')
        if path.is_file():
            files.append(path)
    require(len(files) <= 256, 'treeFileBounds')
    values = checksums(root / 'SHA256SUMS')
    require(set(values) == {p.relative_to(root).as_posix() for p in files if p != root / 'SHA256SUMS'},
            'checksumInventoryIncomplete')
    for name, expected in values.items():
        require(digest(root / name) == expected, 'checksumMismatch')
    return values


@contextlib.contextmanager
def verified_archive(path, expected, maximum=1024**3):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= maximum
            and digest(path) == expected, 'artifactDigestOrBounds')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        require(0 < len(entries) <= 512 and len(names) == len(set(names))
                and sum(e.file_size for e in entries) <= 4 * 1024**3, 'archiveInventory')
        for entry in entries:
            name = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
            p = PurePosixPath(name)
            require(name and not p.is_absolute() and '..' not in p.parts and '\\' not in name
                    and p.as_posix() == name and not stat.S_ISLNK(entry.external_attr >> 16)
                    and stat.S_IFMT(entry.external_attr >> 16) in {0, stat.S_IFREG, stat.S_IFDIR}
                    and not entry.flag_bits & 1
                    and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    and entry.file_size <= MAX_IPA, 'unsafeArchiveEntry')
        yield archive


def copy_member(archive, name, destination, maximum=MAX_TEXT):
    entry = archive.getinfo(name)
    require(not entry.is_dir() and 0 < entry.file_size <= maximum, 'memberBounds')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists() and not destination.is_symlink(), 'memberAlreadyExists')
    with archive.open(entry) as original, destination.open('xb') as output:
        shutil.copyfileobj(original, output, 1024 * 1024)


def load_helpers(source_root, expected_tooling):
    source_root = Path(source_root).resolve(strict=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source_root,
                                   stderr=subprocess.DEVNULL, text=True).strip()
    # This is reviewed packaging tooling, not another application checkout/build.
    # Mac replay separately loads its immutable 5d runtime helper checkout.
    require(head == expected_tooling and len(head) == 40, 'packagingToolingOrigin')
    sys.path.insert(0, str(source_root / 'tools/fork_baseline'))
    return {name: importlib.import_module(name) for name in
            ('artifact_licensing', 'device_variant', 'ipa_inspect', 'ipa_device_inspect', 'branding')}


def baseline(archive, mode, destination, helpers):
    prefix = 'fork-baseline-' + mode + '/'
    for name in BASELINE_FILES:
        copy_member(archive, prefix + name, destination / name, MAX_IPA if name == 'Telegram.ipa' else MAX_TEXT)
    require(set(checksum_tree(destination)) == BASELINE_FILES - {'SHA256SUMS'}, 'baselineChecksumInventory')
    manifest = document(destination / 'manifest.json')
    require(manifest.get('source_commit') == origins.SOURCE and manifest.get('mode') == mode
            and manifest.get('bundle_id') == 'org.livara.ayugram' and manifest.get('build_number') == BUILD
            and manifest.get('sha256') == digest(destination / 'Telegram.ipa')
            and manifest.get('toolchain') == native_validation.PINS
            and manifest.get('runtime_verified') is False and manifest.get('signature_verified') is False,
            'baselineManifest')
    with zipfile.ZipFile(destination / 'Telegram.ipa') as ipa:
        entries = [e for e in ipa.infolist() if len(PurePosixPath(e.filename).parts) == 3
                   and e.filename.endswith('.app/Info.plist')]
        require(len(entries) == 1 and entries[0].file_size <= MAX_TEXT, 'mainBundleMetadata')
        info = plistlib.loads(ipa.read(entries[0]))
        require(info.get('CFBundleVersion') == str(BUILD), 'actualBuildNumberMismatch')
    license_module = helpers['artifact_licensing']
    materials = license_module.read_materials(destination, origins.SOURCE)
    license_metadata = license_module.validate_materials(materials, origins.SOURCE)
    require(manifest.get('license_materials') == license_metadata, 'baselineLicenseMaterials')
    if mode == 'simulator':
        require(manifest['sha256'] == SIM_IPA_SHA and manifest.get('tests') == {
                    '//submodules/TextFormat:TextFormatTests': 'passed'}, 'simulatorCompileManifest')
        helpers['ipa_inspect'].inspect_ipa(destination / 'Telegram.ipa', 'simulator', 'org.livara.ayugram')
    else:
        require(manifest.get('signing') == 'disabled', 'deviceMustRemainUnsigned')
        helpers['ipa_device_inspect'].inspect_unsigned_device(destination / 'Telegram.ipa', 'org.livara.ayugram')
    helpers['branding'].inspect_branding(destination / 'Telegram.ipa', 'org.livara.ayugram')
    return manifest, materials, license_metadata


def cleanup_proof(startup_root, result):
    proof = document(Path(startup_root) / 'cleanup.json')
    require(type(proof.get('schema')) is int and proof['schema'] == 1
            and all(proof.get(name) is True for name in
                    ('owned_device_deleted', 'owned_uuid_absence_verified', 'exact_owned_device_only')),
            'freshOwnedSimulatorCleanupNotVerified')
    # Preserve stock 5d result bytes: success omits this optional error list.
    # Actual owned-device cleanup is independently mandatory above.
    require(result.get('cleanup_failures', []) == [], 'freshLaunchCleanupFailed')
    return proof


def producing_report(mode, manifest, license_metadata, artifact):
    """Compatibility input to the strict startup guard; not an old build proof."""
    device = mode == 'unsigned-device'
    return {'schema': 1, 'repository': origins.REPOSITORY, 'source_run': origins.RUN,
            'source_commit': origins.SOURCE, 'mode': mode, 'artifact_id': artifact['id'],
            'outer_sha256': artifact['digest'][7:], 'ipa_sha256': manifest['sha256'],
            'application_source_unchanged': True, 'source_compile_step_passed': True,
            'source_job_conclusion': 'success' if device else 'failure',
            'source_workflow': origins.WORKFLOW,
            'source_build_job': 'unsigned-device-build' if device else 'application-build',
            'source_compile_step': origins.DEVICE_COMPILE if device else origins.SIM_COMPILE,
            'source_build_number': BUILD, 'source_license_materials_present': True,
            'license_materials': license_metadata, 'physical_device_verified': False, 'release_qualified': False}


def package(source_root, device_zip, simulator_zip, native_zip, original_run, original_jobs,
            original_artifacts, native_artifacts, startup_root, startup_origin, output,
            *, startup_archive=None, inventory_path=None):
    """Create a new candidate from fixed retained bytes and verified fresh startup.

    The caller derives startup_origin from fresh GitHub metadata through
    resume_origins.startup_origin. A receipt is retained public evidence, not an
    independent substitute for that producing-job/API verification.
    """
    original_receipt = origins.original_graph(original_run, original_jobs)
    origins.validate_startup_origin(startup_origin)
    artifacts = {role: origins.artifact(role, native_artifacts if role == 'native' else original_artifacts)
                 for role in ('device', 'simulator', 'native')}
    for role, path in (('device', device_zip), ('simulator', simulator_zip), ('native', native_zip)):
        with verified_archive(path, artifacts[role]['digest'][7:], 64 * 1024**2 if role == 'native' else 1024**3):
            pass
    startup_root, output = Path(startup_root).resolve(strict=True), Path(output)
    require(not output.exists() and not output.is_symlink()
            and not any(p.is_symlink() for p in output.parents), 'outputMustBeNew')
    require(startup_root.is_dir(), 'startupRootRequired')
    fresh_files = checksum_tree(startup_root)
    require(len(fresh_files) <= 128 and sum((startup_root / n).stat().st_size for n in fresh_files) <= 512 * 1024**2,
            'freshEvidenceBounds')
    require(startup_archive is not None, 'freshStartupArchiveRequired')
    with verified_archive(startup_archive, startup_origin['artifact_sha256'], 128 * 1024**2) as archive:
        names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
        require(names == set(fresh_files) | {'SHA256SUMS'}, 'freshArchiveTreeMismatch')
        for name in names:
            value = hashlib.sha256()
            with archive.open(name) as stream:
                while chunk := stream.read(1024 * 1024):
                    value.update(chunk)
            require(value.hexdigest() == digest(startup_root / name), 'freshArchiveFileMismatch')
    replay = document(startup_root / 'replay.json')
    # The public Mac stager checks this too. Recheck before Linux copies any
    # source-provided proof fields into the downloadable export.
    from resume_startup import validate_replay_schema
    validate_replay_schema(replay)
    result = document(startup_root / 'main-app-only/launch-smoke/result.json')
    cleanup = cleanup_proof(startup_root, result)
    source_build_origin = document(startup_root / 'source-build-origin.json')
    origins.validate_source_build_origin(source_build_origin)
    require(replay.get('source_simulator_ipa_sha256') == SIM_IPA_SHA
            and replay.get('private_mode_control') == 'explicitFreshPinnedSelection'
            and replay.get('private_control_only') is True, 'freshStartupSourceOrMode')
    for name in ('launch-1.png', 'launch-2.png', 'launch.mp4'):
        path = startup_root / 'main-app-only/launch-smoke' / name
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 256 * 1024**2,
                'freshVisualEvidenceMissing')
    helpers = load_helpers(source_root, startup_origin['resume_tooling_commit'])
    inventory_path = Path(inventory_path) if inventory_path else TOOL_HELPERS / 'device_delivery_inventory.json'
    native_report, _ = native_validation.verify_evidence(Path(native_zip), origins.SOURCE,
                                                        document(inventory_path))
    require(sum(row['native_passed_cases'] for row in native_report.values()) == 964
            and sum(len(row['added_cases_verified']) for row in native_report.values()) == 28,
            'nativeQualificationIncomplete')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.retained-package-', dir=output.parent) as temporary:
        work = Path(temporary)
        roots = {mode: work / mode for mode in ('unsigned-device', 'simulator')}
        with verified_archive(device_zip, artifacts['device']['digest'][7:]) as archive:
            device, materials, licenses = baseline(archive, 'unsigned-device', roots['unsigned-device'], helpers)
        with verified_archive(simulator_zip, artifacts['simulator']['digest'][7:]) as archive:
            simulator, _, sim_licenses = baseline(archive, 'simulator', roots['simulator'], helpers)
            for name in ('collection.json', 'test.xml', 'test.log'):
                copy_member(archive, 'fork-evidence/textformat/' + name, work / 'textformat' / name)
            for name in ('replay.json', 'main-app-only/launch-smoke/result.json'):
                copy_member(archive, 'fork-evidence/startup-main-app-only/' + name, work / 'original-startup' / name)
        require(source_build_origin['source_manifest_sha256'] == digest(roots['simulator'] / 'manifest.json')
                and source_build_origin['source_ipa_sha256'] == simulator['sha256'], 'retainedBuildReceiptMismatch')
        origins.original_failure((work / 'original-startup/replay.json').read_bytes(),
                                 (work / 'original-startup/main-app-only/launch-smoke/result.json').read_bytes())
        collection = document(work / 'textformat/collection.json')
        require(collection == {'target': '//submodules/TextFormat:TextFormatTests', 'invocation': 'passed',
                               'files': {'test.xml': 'retained_redacted', 'test.log': 'retained_redacted'}},
                'textFormatCollection')
        textformat, _ = native_validation.native_tests((work / 'textformat/test.xml').read_bytes(),
                                                      (work / 'textformat/test.log').read_bytes())
        require(textformat['native_passed_cases'] == 270, 'textFormatMustHave270Passes')
        # Both actual producing jobs are represented honestly. The simulator's
        # compile passed, while its original startup/job failed.
        device_report = producing_report('unsigned-device', device, licenses, artifacts['device'])
        simulator_report = producing_report('simulator', simulator, sim_licenses, artifacts['simulator'])
        guard_source, guard_run, extensions = helpers['device_variant'].validate_startup(
            device_report, simulator_report, replay, result)
        require((guard_source, guard_run, extensions) == (origins.SOURCE, origins.RUN, 6), 'startupGuardMismatch')
        staged = work / 'export'; staged.mkdir()
        filename = 'AyuGram-' + origins.SOURCE[:8] + '-build1001-main-app-only-unsigned-device.ipa'
        variant = helpers['device_variant'].strip_extensions(roots['unsigned-device'] / 'Telegram.ipa', staged / filename, 6)
        require(digest(roots['unsigned-device'] / 'Telegram.ipa') == device['sha256'], 'retainedDeviceIpaChanged')
        helpers['branding'].inspect_branding(staged / filename, 'org.livara.ayugram')
        helpers['artifact_licensing'].write_materials(materials, staged)
        evidence = staged / 'evidence'; evidence.mkdir()
        with verified_archive(native_zip, artifacts['native']['digest'][7:], 64 * 1024**2) as archive:
            for entry in archive.infolist():
                if not entry.is_dir():
                    copy_member(archive, entry.filename, evidence / 'native' / entry.filename)
        checksum_tree(evidence / 'native')
        shutil.copytree(work / 'textformat', evidence / 'textformat')
        shutil.copytree(work / 'original-startup', evidence / 'original-startup-failure')
        shutil.copytree(startup_root, evidence / 'fresh-startup')
        checksum_tree(evidence / 'fresh-startup')
        # Preserve useful raw manifests, not their checksum files with references
        # to original IPAs that are intentionally absent from this export.
        for role, mode in (('device', 'unsigned-device'), ('simulator', 'simulator')):
            shutil.copyfile(roots[mode] / 'manifest.json', evidence / ('original-' + role + '-manifest.json'))
        manifest = {'schema': 2, 'repository': origins.REPOSITORY, 'application_source_commit': origins.SOURCE,
                    'source_commit': origins.SOURCE, 'source_run': origins.RUN, 'build_number': BUILD,
                    'source_tooling_commit': origins.TOOLING, 'source_workflow': origins.WORKFLOW,
                    'original_delivery': original_receipt, 'fresh_startup_origin': startup_origin,
                    'source_build_origin': source_build_origin,
                    'original_device_artifact': {'id': artifacts['device']['id'], 'sha256': artifacts['device']['digest'][7:]},
                    'original_simulator_artifact': {'id': artifacts['simulator']['id'], 'sha256': artifacts['simulator']['digest'][7:]},
                    'source_device_ipa_sha256': device['sha256'], 'source_simulator_ipa_sha256': simulator['sha256'],
                    'filename': filename, 'sha256': digest(staged / filename), 'mode': 'unsigned-device',
                    'variant': 'main-app-only', 'bundle_id': 'org.livara.ayugram', 'signing': 'disabled',
                    'native_qualification_run': origins.NATIVE_RUN, 'native_suites': native_report,
                    'native_added_method_identities_passed': 28, 'native_net_added_methods': 27,
                    'textformat': textformat, 'startup_control': 'explicitFreshPinnedSelection',
                    'simulator_variant_startup_verified': True, 'owned_simulator_cleanup_verified': True,
                    'private_selection_seeded': True, 'automatic_storage_fallback_verified': False,
                    'telegram_authentication_verified': False, 'application_recompiled': False,
                    'runtime_verified': False, 'signature_verified': False, 'physical_device_verified': False,
                    'installable_signed_ipa': False, 'release_qualified': False, 'candidate_only': True,
                    'license_materials': licenses, **variant}
        (staged / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        (staged / 'producing-origins.json').write_text(json.dumps({
            'original': original_receipt, 'fresh_startup': startup_origin,
            'device': device_report, 'simulator': simulator_report, 'cleanup': cleanup}, indent=2) + '\n')
        files = sorted(p for p in staged.rglob('*') if p.is_file())
        (staged / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.relative_to(staged).as_posix() + '\n' for p in files))
        checksum_tree(staged)
        # The exported IPA is final. Later evidence/export repair must copy these
        # exact bytes and this hash; no re-signing or executable change occurs.
        staged.rename(output)
    return manifest
