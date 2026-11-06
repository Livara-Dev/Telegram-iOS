#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One fixed, explicitly runtime-unqualified export of retained unsigned device bytes.

No build, signing, simulator execution, qualified gate override or Actions write.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

SOURCE = '5d241267735b3958c225681d173853568bab705d'
RUN = 42279866706
FROZEN = '51728f5ee077af13ade2619d2b8d45b55f2ef80b'
FAILED_RUN = 42306472313
FAILED_ARTIFACT = 13546133462
FAILED_SHA = 'ca16bac9c27fdfdc639517144f5a0157dc659c11ef6eca8c6daefadbb688659a'
FAILED_SIZE = 5652
REPOSITORY = 'Livara-Dev/Telegram-iOS'
BRANCH = 'ci/unqualified-device-export'
REQUEST_PATH = 'ci/unqualified-device-export-request.json'
WORKFLOW = '.github/workflows/diagnostic-device-export.yml'
IPA_NAME = 'AyuGram-5d241267-build1001-main-app-only-UNQUALIFIED-unsigned.ipa'
FAILED_FILES = {'SHA256SUMS', 'cleanup.json', 'installer-observation.json', 'sdk-bootstrap.json',
                'replay.json', 'source-build-origin.json', 'original-failure/replay.json',
                'original-failure/result.json', 'main-app-only/launch-smoke/result.json',
                'main-app-only/launch-smoke/checker-compiler.log'}


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def digest(path):
    require(Path(path).is_file() and not Path(path).is_symlink(), 'regularFileRequired')
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def document(raw, maximum=1024*1024):
    require(isinstance(raw, bytes) and 0 < len(raw) <= maximum, 'documentBounds')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicateJsonKey')
            result[key] = value
        return result
    def reject_constant(_):
        raise Rejected('nonfiniteJson')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise Rejected('invalidJson') from None
    require(isinstance(result, dict), 'objectRequired')
    return result


def request(raw, environment):
    require(environment.get('GITHUB_REPOSITORY') == REPOSITORY
            and environment.get('GITHUB_EVENT_NAME') == 'push'
            and environment.get('GITHUB_REF') == 'refs/heads/' + BRANCH
            and environment.get('GITHUB_RUN_ATTEMPT') == '1', 'requestOrigin')
    value = document(raw, 1024)
    require(set(value) == {'schema', 'source_commit', 'original_run', 'failed_startup_run', 'nonce'}
            and type(value['schema']) is int and value['schema'] == 1
            and value['source_commit'] == SOURCE
            and type(value['original_run']) is int and value['original_run'] == RUN
            and type(value['failed_startup_run']) is int and value['failed_startup_run'] == FAILED_RUN
            and isinstance(value['nonce'], str)
            and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', value['nonce']), 'requestSchema')
    return value


def load_modules(frozen_root, verify_head=True):
    """Load only the immutable schema-1 reader, never a later diagnostic schema."""
    root = Path(frozen_root).resolve(strict=True)
    if verify_head:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                                       stderr=subprocess.DEVNULL).strip()
        status = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'],
                                         cwd=root, text=True, stderr=subprocess.DEVNULL)
        require(head == FROZEN and not status, 'frozenReaderOrigin')
    helper_root = root / 'tools/fork_baseline'
    resume_root = helper_root / 'startup_resume'
    sys.path[:0] = [str(resume_root), str(helper_root)]
    names = {'origins': 'resume_origins', 'static': 'resume_package', 'startup': 'resume_startup',
             'replay': 'replay_artifact'}
    modules = {key: importlib.import_module(name) for key, name in names.items()}
    # Refuse an already-loaded module from any competing checkout.
    for key, module in modules.items():
        expected = resume_root if key != 'replay' else helper_root
        require(Path(module.__file__).resolve().parent == expected.resolve(), 'readerModuleOrigin')
    modules['helpers'] = {name: importlib.import_module(name) for name in
                         ('artifact_licensing', 'device_variant', 'ipa_inspect', 'ipa_device_inspect', 'branding')}
    for module in modules['helpers'].values():
        require(Path(module.__file__).resolve().parent == helper_root.resolve(), 'staticModuleOrigin')
    return modules


def graph_numbers(run, listing):
    """JSON numeric lookalikes must never satisfy immutable Actions coordinates."""
    require(isinstance(run, dict) and all(type(run.get(k)) is int for k in ('id', 'run_attempt')),
            'runNumericIdentity')
    for key in ('repository', 'head_repository'):
        require(isinstance(run.get(key), dict) and type(run[key].get('id')) is int, 'repositoryNumericIdentity')
    require(isinstance(listing, dict) and type(listing.get('total_count')) is int
            and isinstance(listing.get('jobs'), list), 'jobsNumericInventory')
    for job in listing['jobs']:
        require(isinstance(job, dict) and all(type(job.get(k)) is int for k in ('id', 'run_id', 'run_attempt'))
                and isinstance(job.get('steps'), list), 'jobNumericIdentity')
        require(all(isinstance(step, dict) and type(step.get('number')) is int for step in job['steps']),
                'stepNumericIdentity')


def artifact_numbers(value):
    require(isinstance(value, dict) and all(type(value.get(k)) is int for k in ('id', 'size_in_bytes'))
            and isinstance(value.get('workflow_run'), dict), 'artifactNumericIdentity')
    require(all(type(value['workflow_run'].get(k)) is int for k in
                ('id', 'repository_id', 'head_repository_id')), 'artifactRunNumericIdentity')


def failed_graph(run, listing):
    graph_numbers(run, listing)
    require(isinstance(run, dict) and type(run.get('id')) is int and run['id'] == FAILED_RUN
            and run.get('head_sha') == FROZEN
            and run.get('path') == '.github/workflows/resume-qualified-device-delivery.yml'
            and run.get('head_branch') == 'ci/qualified-device-startup-resume'
            and run.get('event') == 'push' and run.get('status') == 'completed'
            and run.get('conclusion') == 'failure' and type(run.get('run_attempt')) is int
            and run['run_attempt'] == 1, 'failedRunOrigin')
    for name in ('repository', 'head_repository'):
        value = run.get(name, {})
        require(isinstance(value, dict) and value.get('full_name') == REPOSITORY and type(value.get('id')) is int
                and value['id'] == 1445292587, 'failedRepositoryOrigin')
    require(isinstance(listing, dict) and isinstance(listing.get('jobs'), list)
            and type(listing.get('total_count')) is int and listing['total_count'] == len(listing['jobs']) == 2,
            'failedJobInventory')
    rows = {job.get('name'): job for job in listing['jobs']}
    require(set(rows) == {'retained-startup', 'package'}, 'failedJobNames')
    expected = {'retained-startup': (128196956832, 'failure'), 'package': (128200001807, 'skipped')}
    contract = document(Path(__file__).with_name('failed-startup-step-contract.json').read_bytes())
    for name, job in rows.items():
        identity, outcome = expected[name]
        require(type(job.get('id')) is int and job['id'] == identity and job.get('run_id') == FAILED_RUN
                and job.get('head_sha') == FROZEN and job.get('head_branch') == 'ci/qualified-device-startup-resume'
                and type(job.get('run_attempt')) is int and job['run_attempt'] == 1
                and job.get('status') == 'completed' and job.get('conclusion') == outcome
                and isinstance(job.get('steps'), list)
                and all(step.get('status') == 'completed' and type(step.get('number')) is int for step in job['steps'])
                and [[s['number'], s.get('name'), s.get('conclusion')] for s in job['steps']] == contract[name],
                'failedJobOrStepOrigin')
    return {'schema': 1, 'run': FAILED_RUN, 'attempt': 1, 'conclusion': 'failure',
            'tooling_commit': FROZEN, 'workflow': run['path'], 'startup_job': 128196956832,
            'startup_job_conclusion': 'failure', 'package_job': 128200001807,
            'package_job_conclusion': 'skipped', 'admission_steps_passed': True,
            'startup_verified': False, 'application_recompiled': False}


def failed_artifact(listing):
    require(isinstance(listing, dict) and isinstance(listing.get('artifacts'), list)
            and type(listing.get('total_count')) is int
            and listing['total_count'] == len(listing['artifacts']) <= 100, 'failedArtifactInventory')
    matches = [value for value in listing['artifacts'] if value.get('name') == 'startup-resume-5d241267-42279866706']
    require(len(matches) == 1, 'failedArtifactAmbiguous')
    item = matches[0]
    artifact_numbers(item)
    origin = item['workflow_run']
    require(type(item.get('id')) is int and item['id'] == FAILED_ARTIFACT and item.get('expired') is False
            and type(item.get('size_in_bytes')) is int and item['size_in_bytes'] == FAILED_SIZE
            and item.get('digest') == 'sha256:' + FAILED_SHA and origin.get('id') == FAILED_RUN
            and origin.get('head_sha') == FROZEN and origin.get('repository_id') == 1445292587
            and origin.get('head_repository_id') == 1445292587, 'failedArtifactOrigin')
    return item


def device_proof(proof, manifest, manifest_sha):
    expected = {'schema': 1, 'repository': REPOSITORY, 'source_commit': SOURCE, 'source_run': RUN,
                'source_run_attempt': 1, 'workflow_commit': '2f68bca8027980250bf8f9b0328bb1e063fc55c1',
                'workflow_dispatch_commit': '2f68bca8027980250bf8f9b0328bb1e063fc55c1',
                'source_workflow': '.github/workflows/unsigned-device-delivery.yml',
                'source_build_job': 'unsigned-device-build', 'source_compile_step': 'Compile unsigned device package',
                'source_build_number': 1001, 'mode': 'unsigned-device',
                'source_manifest_sha256': manifest_sha, 'source_ipa_sha256': manifest.get('sha256'),
                'license_materials': manifest.get('license_materials'), 'application_source_unchanged': True,
                'source_license_materials_present': True, 'physical_device_verified': False, 'release_qualified': False}
    require(isinstance(proof, dict) and all(type(proof.get(k)) is type(v) and proof.get(k) == v
                                          for k, v in expected.items()), 'deviceBuildProof')
    require(isinstance(proof.get('qualification_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', proof['qualification_sha256']), 'deviceQualificationReferenceFormat')
    return proof


def failed_evidence(startup_zip, destination, modules):
    static, startup, origins = modules['static'], modules['startup'], modules['origins']
    destination = Path(destination)
    require(not destination.exists(), 'failedEvidenceDestinationExists')
    with static.verified_archive(startup_zip, FAILED_SHA, 65536) as archive:
        require(set(archive.namelist()) == FAILED_FILES, 'failedEvidenceInventory')
        destination.mkdir(mode=0o700)
        for name in sorted(FAILED_FILES):
            # The known compiler log is empty; copy_member intentionally rejects empty files.
            entry = archive.getinfo(name)
            require(entry.file_size <= 65536 and not entry.is_dir(), 'failedEvidenceBounds')
            target = destination / name; target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o600)
    static.checksum_tree(destination)
    read = lambda name: document((destination / name).read_bytes(), 65536)
    proof, result = read('replay.json'), read('main-app-only/launch-smoke/result.json')
    startup.validate_replay_schema(proof)
    modules['replay'].prior_smoke_report(result)
    cleanup = read('cleanup.json')
    startup.valid_cleanup(cleanup, False)
    origins.validate_source_build_origin(read('source-build-origin.json'))
    origins.original_failure((destination / 'original-failure/replay.json').read_bytes(),
                             (destination / 'original-failure/result.json').read_bytes())
    for name in startup.OPTIONAL_PUBLIC:
        startup.validate_optional_public(name, destination / name)
    require(proof.get('passed') is False and proof.get('application_recompiled') is False
            and proof.get('source_simulator_ipa_sha256') == origins.SIMULATOR_IPA_SHA256
            and result.get('passed') is False and result.get('launches') == []
            and result.get('phase') == 'installApplication'
            and result.get('failure') == {'code': 'commandTimedOut', 'phase': 'installApplication',
                 'return_code': None, 'timeout': True, 'system_errors': []}
            and [v.get('phase') for v in result.get('cleanup_failures', [])] == ['shutdownDevice'],
            'failedEvidenceNotTheFixedInstallTimeout')
    return {'run': FAILED_RUN, 'artifact_id': FAILED_ARTIFACT, 'artifact_sha256': FAILED_SHA,
            'result_sha256': digest(destination / 'main-app-only/launch-smoke/result.json'),
            'replay_sha256': digest(destination / 'replay.json'), 'startup_verified': False,
            'install_passed': False, 'launch_attempts': 0, 'failure_phase': 'installApplication',
            'failure_code': 'commandTimedOut', 'stock_cleanup_failed_phases': ['shutdownDevice'],
            'owned_device_cleanup': cleanup, 'visual_evidence_available': False,
            'root_cause_established': False}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def export(device_zip, startup_zip, original_run, original_jobs, original_artifacts,
           failed_run, failed_jobs, failed_artifacts, modules, output, export_tooling):
    """Offline derive-once operation; caller already fetched immutable API metadata."""
    origins, static, helpers = modules['origins'], modules['static'], modules['helpers']
    graph_numbers(original_run, original_jobs)
    original = origins.original_graph(original_run, original_jobs)
    failed = failed_graph(failed_run, failed_jobs)
    device = origins.artifact('device', original_artifacts)
    artifact_numbers(device)
    require(device.get('size_in_bytes') == 75971522, 'deviceArtifactSize')
    failed_artifact(failed_artifacts)
    require(isinstance(export_tooling, str) and re.fullmatch(r'[0-9a-f]{40}', export_tooling), 'exportToolingCommit')
    output = Path(output)
    require(not output.exists() and not output.is_symlink()
            and all(not p.is_symlink() for p in output.parents), 'outputMustBeNew')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.unqualified-export-', dir=output.parent) as temp:
        temp = Path(temp).resolve()
        raw = temp / 'original-device'
        with static.verified_archive(device_zip, device['digest'][7:]) as archive:
            manifest, materials, licenses = static.baseline(archive, 'unsigned-device', raw, helpers)
            static.copy_member(archive, 'fork-evidence/build-verification.json', temp / 'device-proof.json')
        proof = static.document(temp / 'device-proof.json')
        device_proof(proof, manifest, static.digest(raw / 'manifest.json'))
        staged = temp / 'export'; staged.mkdir(mode=0o700)
        evidence = staged / 'evidence'; evidence.mkdir()
        failure = failed_evidence(startup_zip, evidence / 'failed-startup', modules)
        source_ipa = raw / 'Telegram.ipa'; source_sha = static.digest(source_ipa)
        variant = helpers['device_variant'].strip_extensions(source_ipa, staged / IPA_NAME, 6)
        inspection = helpers['ipa_device_inspect'].inspect_unsigned_device(staged / IPA_NAME, 'org.livara.ayugram')
        branding = helpers['branding'].inspect_branding(staged / IPA_NAME, 'org.livara.ayugram')
        require(variant.get('extensions_removed') == 6 and variant.get('retained_member_bytes_unchanged') is True
                and variant.get('retained_metadata_unchanged') is True
                and inspection.get('all_macho_platforms') == 2
                and inspection.get('all_bundled_dependencies_resolved') is True
                and inspection.get('signing') == 'disabled'
                and static.digest(source_ipa) == source_sha == manifest['sha256'], 'staticDerivationNotVerified')
        # Counts come from this actual artifact, not from the historical c55 package.
        with zipfile.ZipFile(staged / IPA_NAME) as ipa:
            measured_entries = len(ipa.infolist())
        require(type(variant.get('retained_entries')) is int and variant['retained_entries'] == measured_entries,
                'retainedEntryMeasurement')
        helpers['artifact_licensing'].write_materials(materials, staged)
        shutil.copyfile(raw / 'manifest.json', evidence / 'original-device-manifest.json')
        shutil.copyfile(temp / 'device-proof.json', evidence / 'original-device-build-proof.json')
        summary = {'schema': 1, 'original_delivery': original, 'failed_startup': failed,
                   'failed_startup_result': failure, 'original_device_artifact': {
                       'id': device['id'], 'sha256': device['digest'][7:], 'size_in_bytes': device['size_in_bytes']},
                   'inherited_simulator_textformat_receipt': {'preserved': True,
                       'referenced_raw_simulator_and_textformat_inputs_included': False,
                       'reference_targets_reverified': False},
                   'external_native_qualification_reference': {'sha256': proof['qualification_sha256'],
                       'referenced_bytes_included': False, 'reference_target_reverified': False},
                   'export_workflow': WORKFLOW, 'export_tooling_commit': export_tooling,
                   'frozen_schema_reader_commit': FROZEN, 'application_source_commit': SOURCE}
        write_json(evidence / 'origin-summary.json', summary)
        report = {'schema': 1, 'repository': REPOSITORY, 'qualification_status': 'not_runtime_qualified',
                  'status': 'not_runtime_qualified',
                  'source_commit': SOURCE, 'application_source_commit': SOURCE, 'build_number': 1001,
                  'filename': IPA_NAME, 'sha256': static.digest(staged / IPA_NAME),
                  'mode': 'unsigned-device', 'variant': 'main-app-only', 'bundle_id': 'org.livara.ayugram',
                  'source_run': RUN, 'source_run_conclusion': 'failure', 'source_device_job': origins.DEVICE_JOB,
                  'source_device_job_conclusion': 'success', 'source_device_compile_step_passed': True,
                  'source_device_workflow': origins.WORKFLOW, 'source_device_compile_step': origins.DEVICE_COMPILE,
                  'source_device_artifact_id': device['id'], 'source_device_outer_sha256': device['digest'][7:],
                  'source_device_ipa_sha256': source_sha, 'source_tooling_commit': origins.TOOLING,
                  'failed_startup_run': FAILED_RUN, 'failed_startup_conclusion': 'failure',
                  'failed_startup_artifact_id': FAILED_ARTIFACT, 'failed_startup_artifact_sha256': FAILED_SHA,
                  'export_workflow': WORKFLOW, 'export_tooling_commit': export_tooling,
                  'frozen_schema_reader_commit': FROZEN, 'signing': 'disabled', 'candidate_only': True,
                  'application_recompiled': False, 'source_ipa_unchanged': True, 'build_number_changed': False,
                  'runtime_verified': False, 'simulator_variant_startup_verified': False,
                  'physical_device_verified': False, 'telegram_authentication_verified': False,
                  'device_installation_verified': False, 'sidestore_installation_verified': False,
                  'automatic_storage_fallback_verified': False, 'signature_verified': False,
                  'installable_signed_ipa': False, 'release_qualified': False,
                  'extensions_available': False, 'license_materials': licenses,
                  'external_native_qualification_reference_reverified': False,
                  'static_inspection': inspection, 'branding_inspection': branding, **variant}
        write_json(staged / 'manifest.json', report)
        files = sorted(p for p in staged.rglob('*') if p.is_file())
        (staged / 'SHA256SUMS').write_text(''.join(static.digest(p) + '  ' + p.relative_to(staged).as_posix()
                                                   + '\n' for p in files), encoding='ascii')
        static.checksum_tree(staged)
        staged.rename(output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'frozen-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(args.request.is_file() and not args.request.is_symlink() and args.request.stat().st_size <= 1024,
                'requestFile')
        request(args.request.read_bytes(), os.environ)
        tooling = os.environ.get('GITHUB_SHA', '')
        require(re.fullmatch(r'[0-9a-f]{40}', tooling) and os.environ.get('GITHUB_WORKFLOW_SHA') == tooling,
                'currentWorkflowOrigin')
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[3],
                                       text=True, stderr=subprocess.DEVNULL).strip()
        require(head == tooling, 'currentToolingCheckout')
        modules = load_modules(args.frozen_root)
        api = modules['startup'].api
        original_run = api('actions/runs/' + str(RUN))
        original_jobs = api('actions/runs/' + str(RUN) + '/jobs?filter=latest&per_page=100')
        graph_numbers(original_run, original_jobs)
        modules['origins'].original_graph(original_run, original_jobs)
        original_artifacts = api('actions/runs/' + str(RUN) + '/artifacts?per_page=100')
        device = modules['origins'].artifact('device', original_artifacts)
        artifact_numbers(device)
        require(device.get('size_in_bytes') == 75971522, 'deviceArtifactSize')
        failed_run = api('actions/runs/' + str(FAILED_RUN))
        failed_jobs = api('actions/runs/' + str(FAILED_RUN) + '/jobs?filter=latest&per_page=100')
        failed_graph(failed_run, failed_jobs)
        failed_artifacts = api('actions/runs/' + str(FAILED_RUN) + '/artifacts?per_page=100')
        failed = failed_artifact(failed_artifacts)
        with tempfile.TemporaryDirectory(prefix='unqualified-device-inputs-') as temp:
            temp = Path(temp).resolve()
            device_zip, startup_zip = temp / 'device.zip', temp / 'failed-startup.zip'
            modules['startup'].download(device, device_zip)
            modules['startup'].download(failed, startup_zip)
            report = export(device_zip, startup_zip, original_run, original_jobs, original_artifacts,
                            failed_run, failed_jobs, failed_artifacts, modules, args.output, tooling)
        print(json.dumps({'qualification_status': report['qualification_status'], 'sha256': report['sha256'],
                          'source_commit': SOURCE, 'application_recompiled': False, 'physical_device_verified': False}))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile):
        # No token, URL, arbitrary input, archive member or raw subprocess output in errors.
        print(json.dumps({'error': 'unqualified_export_rejected', 'startup_verified': False,
                          'physical_device_verified': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
