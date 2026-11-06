#!/usr/bin/env python3
"""Manual device delivery gate and packaging; never dispatches or signs an app.

Install only on a reviewed tooling branch. The app checkout remains at the
successful archive-core run's exact head, independent of the tooling checkout.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

REPOSITORY = 'Livara-Dev/Telegram-iOS'
WORKFLOW = '.github/workflows/archive-integration.yml'
SOURCE = '5d241267735b3958c225681d173853568bab705d'
TARGETS = {
    'prebuild-local-message-archive': '//submodules/LocalMessageArchive:LocalMessageArchiveTests',
    'prebuild-telegram-core': '//submodules/TelegramCore:TelegramCoreTests',
}
NATIVE_COUNTS = {'prebuild-local-message-archive': 129, 'prebuild-telegram-core': 835}
PINS = {'app': '13.1.2', 'xcode': '27.1', 'deploy_xcode': '27.1',
        'bazel': '9.2.0:dd466352a3e4d3581b8898740ee1ff208866ccbe25f8d367c5dcb950219587e6', 'macos': '27'}
SHA = re.compile(r'[0-9a-f]{40}')
MAX_ARCHIVE = 64 * 1024**2
MAX_FILE = 2 * 1024**2
MAX_LOG = 16 * 1024**2
DELIVERY_WORKFLOW = '.github/workflows/unsigned-device-delivery.yml'
BUILD_MODES = {
    'unsigned-device': ('unsigned-device-build', 'Compile unsigned device package', 'device'),
    'simulator': ('application-build', 'Build the full simulator app and run TextFormat regression tests', 'simulator'),
}


class Blocked(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Blocked(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def document(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise Blocked('invalid_json') from None


def api(endpoint):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                             'repos/' + REPOSITORY + '/' + endpoint],
                            capture_output=True, timeout=30, check=False)
    require(result.returncode == 0 and len(result.stdout) <= 4 * MAX_FILE, 'github_metadata_read_failed')
    return document(result.stdout)


def validate_metadata(run, jobs, listing, run_id, expected_source):
    require(isinstance(run, dict) and run.get('id') == run_id and run.get('head_sha') == expected_source,
            'qualification_source_mismatch')
    for field in ('repository', 'head_repository'):
        require(isinstance(run.get(field), dict) and run[field].get('full_name') == REPOSITORY,
                'qualification_repository_mismatch')
    repo_id = run['repository'].get('id')
    require(type(repo_id) is int and repo_id == run['head_repository'].get('id'), 'qualification_repository_mismatch')
    require(run.get('path', '').split('@', 1)[0] == WORKFLOW and run.get('event') == 'workflow_dispatch'
            and run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
            'qualification_not_completed_successfully')
    require(isinstance(jobs, dict) and isinstance(jobs.get('jobs'), list)
            and jobs.get('total_count') == len(jobs['jobs']) and 0 < len(jobs['jobs']) <= 100,
            'qualification_jobs_incomplete')
    native = [job for job in jobs['jobs'] if job.get('name') == 'integration-tests']
    require(len(native) == 1, 'qualification_native_job_missing')
    job = native[0]
    require(type(job.get('id')) is int and job.get('run_id') == run_id and job.get('head_sha') == expected_source
            and job.get('status') == 'completed' and job.get('conclusion') == 'success', 'qualification_native_job_failed')
    for other in jobs['jobs']:
        require(other is job or (other.get('status') == 'completed' and other.get('conclusion') == 'skipped'),
                'qualification_scope_not_archive_core_only')
    steps = job.get('steps', [])
    for step_name in ('Run real iOS integration suites', 'Publish integration test evidence before the full app build'):
        selected = [step for step in steps if step.get('name') == step_name]
        require(len(selected) == 1 and selected[0].get('status') == 'completed'
                and selected[0].get('conclusion') == 'success', 'qualification_native_step_failed')
    require(isinstance(listing, dict) and isinstance(listing.get('artifacts'), list)
            and listing.get('total_count') == len(listing['artifacts']) and len(listing['artifacts']) <= 100,
            'qualification_artifacts_incomplete')
    artifacts = [item for item in listing['artifacts'] if item.get('name') == 'archive-integration-tests-' + expected_source]
    require(len(artifacts) == 1, 'qualification_artifact_not_unique')
    artifact = artifacts[0]
    origin = artifact.get('workflow_run', {})
    require(type(artifact.get('id')) is int and artifact['id'] > 0 and artifact.get('expired') is False
            and type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_ARCHIVE
            and re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', ''))
            and origin.get('id') == run_id and origin.get('head_sha') == expected_source
            and origin.get('repository_id') == repo_id and origin.get('head_repository_id') == repo_id,
            'qualification_artifact_origin_mismatch')
    return artifact, job['id']


def native_tests(xml, log):
    require(b'<!DOCTYPE' not in xml and b'<!ENTITY' not in xml, 'unsafe_native_xml')
    root = ET.fromstring(xml)
    cases = list(root.iter('testcase'))
    require(bool(cases), 'zero_bazel_test_cases')
    require(not any(list(root.iter(tag)) for tag in ('failure', 'error', 'skipped')), 'native_xml_not_passed')
    for suite in root.iter('testsuite'):
        for field in ('failures', 'errors', 'skipped'):
            require(suite.get(field, '0') == '0', 'native_xml_not_passed')
    text = log.decode('utf-8')
    events = re.findall(r"Test Case '-\[[^\]\s]+\.([A-Za-z0-9_]+) ([A-Za-z0-9_]+)\]' (started|passed|failed|skipped)", text)
    states = {}
    for class_name, method, event in events:
        name = class_name + '.' + method
        if event == 'started':
            require(name not in states, 'duplicate_or_misordered_native_event')
            states[name] = 'started'
        else:
            require(event == 'passed' and states.get(name) == 'started', 'native_case_failure_or_misordered_event')
            states[name] = 'passed'
    require(bool(states) and all(value == 'passed' for value in states.values()), 'native_case_failure_or_zero_cases')
    passes = collections.Counter({name: 1 for name in states})
    totals = re.findall(r'Executed (\d+) tests?, with (\d+) failures? \((\d+) unexpected\)', text)
    require(bool(totals) and all(failures == '0' and unexpected == '0' for _, failures, unexpected in totals)
            and int(totals[-1][0]) > 0 and int(totals[-1][0]) == len(states)
            and "Test Suite 'All tests' passed" in text, 'native_execution_count_unverified')
    require(all(count == 1 for count in passes.values()), 'duplicate_native_case_execution')
    return {'bazel_wrapper_cases': len(cases), 'native_passed_cases': len(states)}, passes


def verify_evidence(archive_path, source, inventory):
    require(inventory.get('application_source_commit') == source and inventory.get('new_case_count') == 28
            and len(inventory.get('new_cases', [])) == 28, 'new_case_inventory_mismatch')
    required = {'SHA256SUMS', 'integration-tests.json'}
    required.update(directory + '/' + name for directory in TARGETS for name in ('collection.json', 'test.xml', 'test.log'))
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        require(len(entries) <= 512 and len(set(names)) == len(names)
                and sum(item.file_size for item in entries) <= 256 * 1024**2, 'qualification_archive_bounds')
        for item in entries:
            path = PurePosixPath(item.filename)
            require(not path.is_absolute() and '..' not in path.parts and '\\' not in item.filename
                    and '\x00' not in item.filename and not stat.S_ISLNK(item.external_attr >> 16)
                    and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    and not item.flag_bits & 1 and item.file_size <= MAX_ARCHIVE, 'unsafe_qualification_archive')
        require(required.issubset(names), 'qualification_evidence_missing')
        evidence = {}
        for name in required:
            require(archive.getinfo(name).file_size <= MAX_FILE, 'qualification_evidence_too_large')
            evidence[name] = archive.read(name)
    checksums = {}
    for line in evidence['SHA256SUMS'].decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None and match[2] not in checksums, 'invalid_qualification_checksums')
        checksums[match[2]] = match[1]
    with zipfile.ZipFile(archive_path) as archive:
        require(set(checksums) == {item.filename for item in archive.infolist() if not item.is_dir()} - {'SHA256SUMS'},
                'qualification_checksum_inventory_mismatch')
        for name, expected in checksums.items():
            actual = hashlib.sha256()
            with archive.open(name) as stream:
                while chunk := stream.read(1024 * 1024):
                    actual.update(chunk)
            require(actual.hexdigest() == expected, 'qualification_evidence_digest_mismatch')
    for name, data in evidence.items():
        if name != 'SHA256SUMS':
            require(checksums.get(name) == hashlib.sha256(data).hexdigest(), 'qualification_evidence_digest_mismatch')
    summary = document(evidence['integration-tests.json'])
    require(summary.get('source_commit') == source and summary.get('validation_scope') == 'archive-core'
            and summary.get('toolchain') == PINS and summary.get('tests') == {label: 'passed' for label in TARGETS.values()}
            and summary.get('tests_only') is True and summary.get('full_integration_suite_requested') is False
            and summary.get('full_application_build_requested') is False and summary.get('full_application_built') is False
            and summary.get('physical_device_verified') is False, 'qualification_summary_mismatch')
    reports = {}
    added = set()
    for directory, label in TARGETS.items():
        collection = document(evidence[directory + '/collection.json'])
        require(collection == {'target': label, 'invocation': 'passed',
                               'files': {'test.xml': 'retained_redacted', 'test.log': 'retained_redacted'}},
                'native_evidence_collection_incomplete')
        report, passes = native_tests(evidence[directory + '/test.xml'], evidence[directory + '/test.log'])
        require(report['native_passed_cases'] == NATIVE_COUNTS[directory], 'native_suite_case_count_changed')
        prefix = 'submodules/LocalMessageArchive/' if directory == 'prebuild-local-message-archive' else 'submodules/TelegramCore/'
        expected = [item['case'] for item in inventory['new_cases'] if item['file'].startswith(prefix)]
        require(bool(expected) and all(passes[name] == 1 for name in expected), 'new_native_case_not_passed')
        added.update(expected)
        reports[label] = {**report, 'added_cases_verified': expected}
    require(len(added) == 28, 'new_native_case_inventory_incomplete')
    return reports, evidence


def gate(run_id, expected_source, inventory_path, output):
    require(type(run_id) is int and 0 < run_id < 2**63 and expected_source == SOURCE, 'invalid_qualification_coordinates')
    run = api('actions/runs/' + str(run_id))
    artifact, job_id = validate_metadata(run, api('actions/runs/' + str(run_id) + '/jobs?filter=latest&per_page=100'),
                                         api('actions/runs/' + str(run_id) + '/artifacts?per_page=100'), run_id, expected_source)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='qualification-') as temporary:
        archive = Path(temporary) / 'native-evidence.zip'
        with archive.open('xb') as stream:
            result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                     'repos/' + REPOSITORY + '/actions/artifacts/' + str(artifact['id']) + '/zip'],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=180, check=False)
        require(result.returncode == 0 and 0 < archive.stat().st_size <= MAX_ARCHIVE
                and digest(archive) == artifact['digest'][7:], 'qualification_archive_digest_mismatch')
        reports, evidence = verify_evidence(archive, run['head_sha'], document(inventory_path.read_bytes()))
    # Recheck status/latest attempt after download to avoid accepting a rerun in progress.
    fresh = api('actions/runs/' + str(run_id))
    require(fresh.get('status') == 'completed' and fresh.get('conclusion') == 'success'
            and fresh.get('head_sha') == run['head_sha'] and fresh.get('run_attempt') == run['run_attempt'],
            'qualification_changed_during_verification')
    report = {'schema': 1, 'repository': REPOSITORY, 'source_commit': run['head_sha'],
              'qualification_run': run_id, 'qualification_attempt': run['run_attempt'], 'qualification_job_id': job_id,
              'qualification_workflow': WORKFLOW, 'qualification_scope': 'archive-core',
              'qualification_artifact_id': artifact['id'], 'qualification_artifact_digest': artifact['digest'],
              'inner_checksum_references': len(evidence['SHA256SUMS'].splitlines()),
              'native_suites': reports, 'native_added_methods_passed': 28, 'native_net_added_methods': 27,
              'full_integration_verified': False, 'startup_verified': False, 'physical_device_verified': False}
    (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, data in evidence.items():
        destination = output / 'native-evidence' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        stream.write('source_sha=' + run['head_sha'] + '\n')
    print('Exact-source archive-core qualification verified; both native suites and 28 added methods passed.')


def source_clean(root, source):
    def git(*arguments):
        return subprocess.check_output(['git', *arguments], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
    require(git('rev-parse', 'HEAD') == source and not git('status', '--porcelain', '--untracked-files=no'),
            'source_checkout_changed')
    require(document((root / 'versions.json').read_bytes()) == PINS, 'source_toolchain_pins_changed')


def validate_build(root, qualification_dir, build_number, mode):
    qualification = document((qualification_dir / 'qualification.json').read_bytes())
    source = qualification.get('source_commit')
    require(source == SOURCE and qualification.get('native_added_methods_passed') == 28, 'qualification_record_invalid')
    source_clean(root, source)
    sys.path.insert(0, str(root / 'tools/fork_baseline'))
    from artifact_licensing import read_materials, validate_materials, write_materials
    from ipa_inspect import inspect_ipa
    from ipa_device_inspect import inspect_unsigned_device
    raw = root / ('build/fork-baseline-' + mode)
    ipa = raw / 'Telegram.ipa'
    manifest = document((raw / 'manifest.json').read_bytes())
    require(manifest.get('source_commit') == source and manifest.get('mode') == mode
            and manifest.get('bundle_id') == 'org.livara.ayugram' and manifest.get('build_number') == build_number
            and manifest.get('runtime_verified') is False
            and manifest.get('sha256') == digest(ipa) and manifest.get('toolchain') == PINS,
            'device_build_manifest_mismatch')
    if mode == 'unsigned-device':
        require(manifest.get('signing') == 'disabled', 'device_build_not_unsigned')
        inspect_unsigned_device(ipa, 'org.livara.ayugram')
    else:
        require(manifest.get('tests') == {'//submodules/TextFormat:TextFormatTests': 'passed'}, 'simulator_textformat_not_passed')
        inspect_ipa(ipa, 'simulator', 'org.livara.ayugram')
    materials = read_materials(raw, source)
    license_metadata = validate_materials(materials, source)
    require(manifest.get('license_materials') == license_metadata, 'device_source_materials_mismatch')
    return qualification, manifest, license_metadata


def verify_build(mode, root, qualification_dir, destination, build_number):
    require(os.environ.get('COMPILE_STEP_OUTCOME') == 'success', 'compile_step_not_successful')
    qualification, manifest, license_metadata = validate_build(root, qualification_dir, build_number, mode)
    tooling_sha = os.environ.get('GITHUB_SHA', '')
    workflow_sha = os.environ.get('GITHUB_WORKFLOW_SHA', tooling_sha)
    require(SHA.fullmatch(tooling_sha) and SHA.fullmatch(workflow_sha), 'tooling_origin_invalid')
    report = {'schema': 1, 'repository': REPOSITORY, 'source_commit': qualification['source_commit'],
              'workflow_commit': workflow_sha, 'workflow_dispatch_commit': tooling_sha,
              'workflow_ref': os.environ.get('GITHUB_WORKFLOW_REF'), 'source_run': int(os.environ['GITHUB_RUN_ID']),
              'source_run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']), 'mode': mode,
              'source_workflow': DELIVERY_WORKFLOW, 'source_build_job': BUILD_MODES[mode][0],
              'source_compile_step': BUILD_MODES[mode][1], 'source_build_number': build_number,
              'source_ipa_sha256': manifest['sha256'], 'source_manifest_sha256': digest(root / ('build/fork-baseline-' + mode) / 'manifest.json'),
              'qualification_sha256': digest(qualification_dir / 'qualification.json'),
              'application_source_unchanged': True, 'source_license_materials_present': True,
              'license_materials': license_metadata, 'physical_device_verified': False, 'release_qualified': False}
    if mode == 'simulator':
        from replay_artifact import prior_smoke_report
        proof = document((root / 'build/fork-evidence/startup-main-app-only/replay.json').read_bytes())
        result = prior_smoke_report(document((root / 'build/fork-evidence/startup-main-app-only/main-app-only/launch-smoke/result.json').read_bytes()))
        require(proof.get('passed') is True and proof.get('source_simulator_ipa_sha256') == manifest['sha256']
                and result.get('passed') is True, 'source_matched_startup_not_passed')
        report['startup_proof_sha256'] = digest(root / 'build/fork-evidence/startup-main-app-only/replay.json')
        report['startup_result_sha256'] = digest(root / 'build/fork-evidence/startup-main-app-only/main-app-only/launch-smoke/result.json')
    for name, maximum in (('host.json', MAX_FILE), ('host.log', MAX_FILE), ('source-guards.log', MAX_FILE),
                          ('branding.json', MAX_FILE), (mode + '.log', MAX_LOG)):
        path = root / 'build/fork-evidence' / name
        require(path.is_file() and not path.is_symlink(), 'build_evidence_missing')
        if path.stat().st_size > maximum:
            # Compiler output is already redacted by baseline; retain a bounded tail.
            with path.open('rb') as stream:
                stream.seek(-maximum, 2)
                tail = stream.read(maximum)
            path.write_bytes(b'[bounded tail; earlier output omitted]\n' + tail)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print('Build source, IPA, mandatory tests and license metadata checked; workflow origin recorded separately.')


def current_artifacts(mode, run, jobs, listing, source):
    require(run.get('id') == int(os.environ['GITHUB_RUN_ID'])
            and run.get('run_attempt') == int(os.environ['GITHUB_RUN_ATTEMPT'])
            and run.get('head_sha') == os.environ['GITHUB_SHA']
            and run.get('repository', {}).get('full_name') == REPOSITORY
            and run.get('head_repository', {}).get('full_name') == REPOSITORY
            and run.get('path', '').split('@', 1)[0] == DELIVERY_WORKFLOW
            and run.get('event') == 'workflow_dispatch', 'delivery_run_origin_mismatch')
    require(jobs.get('total_count') == len(jobs.get('jobs', [])) <= 100, 'delivery_job_list_incomplete')
    selected = [job for job in jobs['jobs'] if job.get('name') == BUILD_MODES[mode][0]]
    require(len(selected) == 1, 'delivery_build_job_missing')
    job = selected[0]
    require(type(job.get('id')) is int and job['id'] > 0 and job.get('run_id') == run['id'] and job.get('head_sha') == run['head_sha']
            and job.get('status') == 'completed' and job.get('conclusion') == 'success', 'delivery_build_job_not_successful')
    for name in (BUILD_MODES[mode][1],) + (('Verify source-matched main-app startup',) if mode == 'simulator' else ()):
        steps = [step for step in job.get('steps', []) if step.get('name') == name]
        require(len(steps) == 1 and steps[0].get('status') == 'completed' and steps[0].get('conclusion') == 'success',
                'delivery_build_step_not_successful')
    require(listing.get('total_count') == len(listing.get('artifacts', [])) <= 100, 'delivery_artifact_list_incomplete')
    artifacts = [item for item in listing['artifacts'] if item.get('name') == 'device-delivery-' + BUILD_MODES[mode][2] + '-' + source]
    require(len(artifacts) == 1, 'delivery_artifact_not_unique')
    artifact = artifacts[0]
    origin = artifact.get('workflow_run', {})
    require(artifact.get('expired') is False and type(artifact.get('id')) is int
            and type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= 1024**3
            and re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', ''))
            and origin.get('id') == run['id'] and origin.get('head_sha') == run['head_sha']
            and origin.get('repository_id') == run['repository'].get('id')
            and origin.get('head_repository_id') == run['head_repository'].get('id'), 'delivery_artifact_origin_mismatch')
    return artifact, job


def fetch_build(artifact, mode, destination):
    require(not destination.exists(), 'delivery_download_directory_exists')
    destination.mkdir(parents=True)
    raw_prefix = 'fork-baseline-' + mode + '/'
    materials = {'COPYING', 'LICENSE-NOTICE.md', 'CORRESPONDING-SOURCE.md'}
    raw_names = {raw_prefix + name for name in {'Telegram.ipa', 'manifest.json', 'SHA256SUMS'} | materials}
    evidence_names = {'fork-evidence/' + name for name in ('build-verification.json', 'host.json', 'host.log',
                     'source-guards.log', 'branding.json', mode + '.log')}
    startup_prefix = 'fork-evidence/startup-main-app-only/'
    if mode == 'simulator':
        evidence_names.update('fork-evidence/' + name for name in ('runtime.log', 'startup.log'))
        evidence_names.update('fork-evidence/textformat/' + name for name in ('collection.json', 'test.xml', 'test.log'))
        evidence_names.add(startup_prefix + 'replay.json')
        evidence_names.update(startup_prefix + 'main-app-only/launch-smoke/' + name for name in
                              ('result.json', 'launch-1.png', 'launch-2.png', 'checker-compiler.log', 'recording.log', 'launch.mp4'))
    else:
        evidence_names.add('fork-evidence/unsigned-rules.json')
    with tempfile.TemporaryDirectory(prefix='delivery-download-') as temporary:
        path = Path(temporary) / 'artifact.zip'
        with path.open('xb') as stream:
            result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                     'repos/' + REPOSITORY + '/actions/artifacts/' + str(artifact['id']) + '/zip'],
                                    stdout=stream, stderr=subprocess.PIPE, timeout=300, check=False)
        require(result.returncode == 0 and 0 < path.stat().st_size <= 1024**3
                and digest(path) == artifact['digest'][7:], 'delivery_artifact_digest_mismatch')
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            require(len(entries) <= 256 and len(set(names)) == len(names)
                    and sum(item.file_size for item in entries) <= 2 * 1024**3, 'delivery_archive_bounds')
            for item in entries:
                name = item.filename
                path_name = PurePosixPath(name)
                require(not path_name.is_absolute() and '..' not in path_name.parts and '\\' not in name
                        and '\x00' not in name and not stat.S_ISLNK(item.external_attr >> 16)
                        and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        and not item.flag_bits & 1, 'unsafe_delivery_archive')
                # Read only the fixed package and bounded, credential-free evidence.
                if name not in raw_names | evidence_names:
                    continue
                maximum = 1024**3 if name.endswith('/Telegram.ipa') else 32 * MAX_FILE if name.endswith('.mp4') else 20 * MAX_FILE if name.endswith('.log') else 8 * MAX_FILE
                require(item.file_size <= maximum and not item.is_dir(), 'delivery_member_bounds')
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as original, target.open('xb') as output:
                    shutil.copyfileobj(original, output, length=1024 * 1024)
            require(raw_names.issubset(names) and 'fork-evidence/build-verification.json' in names, 'delivery_members_missing')


def package(root, qualification_dir, device_root, simulator_root, destination, build_number):
    qualification = document((qualification_dir / 'qualification.json').read_bytes())
    source = qualification.get('source_commit')
    require(source == SOURCE and qualification.get('native_added_methods_passed') == 28, 'qualification_record_invalid')
    source_clean(root, source)
    sys.path.insert(0, str(root / 'tools/fork_baseline'))
    from device_variant import package as guarded_package
    from artifact_licensing import read_materials, validate_materials
    from ipa_inspect import inspect_ipa
    from ipa_device_inspect import inspect_unsigned_device
    run_id = int(os.environ['GITHUB_RUN_ID'])
    run = api('actions/runs/' + str(run_id))
    jobs = api('actions/runs/' + str(run_id) + '/jobs?filter=latest&per_page=100')
    listing = api('actions/runs/' + str(run_id) + '/artifacts?per_page=100')
    verifications = {}
    origins = {}
    for mode, folder in (('unsigned-device', device_root), ('simulator', simulator_root)):
        artifact, job = current_artifacts(mode, run, jobs, listing, source)
        fetch_build(artifact, mode, folder)
        raw = folder / ('fork-baseline-' + mode)
        proof = document((folder / 'fork-evidence/build-verification.json').read_bytes())
        manifest = document((raw / 'manifest.json').read_bytes())
        require(proof.get('schema') == 1 and proof.get('repository') == REPOSITORY and proof.get('mode') == mode
                and proof.get('source_commit') == source and proof.get('source_run') == run_id
                and proof.get('source_run_attempt') == run['run_attempt']
                and proof.get('workflow_dispatch_commit') == run['head_sha']
                and proof.get('workflow_commit') == os.environ['GITHUB_WORKFLOW_SHA']
                and proof.get('source_workflow') == DELIVERY_WORKFLOW
                and proof.get('source_build_job') == BUILD_MODES[mode][0]
                and proof.get('source_compile_step') == BUILD_MODES[mode][1]
                and proof.get('source_build_number') == build_number
                and proof.get('source_manifest_sha256') == digest(raw / 'manifest.json')
                and proof.get('source_ipa_sha256') == digest(raw / 'Telegram.ipa') == manifest.get('sha256')
                and proof.get('qualification_sha256') == digest(qualification_dir / 'qualification.json')
                and proof.get('application_source_unchanged') is True
                and proof.get('source_license_materials_present') is True
                and proof.get('physical_device_verified') is False and proof.get('release_qualified') is False,
                'delivery_build_provenance_mismatch')
        require(manifest.get('source_commit') == source and manifest.get('build_number') == build_number
                and manifest.get('mode') == mode and manifest.get('toolchain') == PINS, 'delivery_manifest_mismatch')
        license_metadata = validate_materials(read_materials(raw, source), source)
        require(proof.get('license_materials') == manifest.get('license_materials') == license_metadata,
                'delivery_license_materials_mismatch')
        if mode == 'unsigned-device':
            inspect_unsigned_device(raw / 'Telegram.ipa', 'org.livara.ayugram')
        else:
            inspect_ipa(raw / 'Telegram.ipa', 'simulator', 'org.livara.ayugram')
            require(manifest.get('tests') == {'//submodules/TextFormat:TextFormatTests': 'passed'}, 'simulator_textformat_not_passed')
        verification = {**proof, 'ipa_sha256': proof['source_ipa_sha256'], 'outer_sha256': artifact['digest'][7:],
                        'artifact_id': artifact['id'], 'source_job_conclusion': 'success', 'source_compile_step_passed': True}
        verification_path = folder / 'verification.json'
        verification_path.write_text(json.dumps(verification, indent=2) + '\n')
        verifications[mode] = verification_path
        origins[mode] = {'workflow': DELIVERY_WORKFLOW, 'workflow_commit': proof['workflow_commit'],
                         'dispatch_commit': proof['workflow_dispatch_commit'], 'app_source_commit': source,
                         'run_id': run_id, 'attempt': run['run_attempt'], 'job_id': job['id'], 'job_name': job['name'],
                         'compile_step': BUILD_MODES[mode][1], 'artifact_id': artifact['id'], 'artifact_digest': artifact['digest']}
    startup = simulator_root / 'fork-evidence/startup-main-app-only'
    sim_proof = document((simulator_root / 'fork-evidence/build-verification.json').read_bytes())
    require(sim_proof.get('startup_proof_sha256') == digest(startup / 'replay.json')
            and sim_proof.get('startup_result_sha256') == digest(startup / 'main-app-only/launch-smoke/result.json'),
            'delivery_startup_digest_mismatch')
    # Existing guard requires both exact-source builds and genuine simulator startup;
    # never use strip_extensions directly to bypass it.
    manifest = guarded_package(device_root / 'fork-baseline-unsigned-device/Telegram.ipa',
                               verifications['unsigned-device'], verifications['simulator'],
                               startup / 'replay.json', startup / 'main-app-only/launch-smoke/result.json', destination)
    # The existing guard's old serializer intentionally drops unknown workflow
    # names. After strict API/proof validation, restore the real new origins.
    manifest.update(source_device_workflow=DELIVERY_WORKFLOW, source_simulator_workflow=DELIVERY_WORKFLOW,
                    source_device_workflow_commit=origins['unsigned-device']['workflow_commit'],
                    source_simulator_workflow_commit=origins['simulator']['workflow_commit'],
                    workflow_dispatch_commit=os.environ['GITHUB_SHA'],
                    application_source_commit=source, startup_control='explicitFreshPinnedSelection',
                    automatic_storage_fallback_verified=False, telegram_authentication_verified=False)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    provenance = {'schema': 1, 'source_commit': source, 'workflow_commit': os.environ['GITHUB_WORKFLOW_SHA'],
                  'workflow_dispatch_commit': os.environ['GITHUB_SHA'], 'workflow': DELIVERY_WORKFLOW,
                  'qualification': qualification, 'build_origins': origins,
                  'existing_package_guard_used': 'tools/fork_baseline/device_variant.py:package',
                  'legacy_origin_serializer_supports_this_workflow': False,
                  'origin_note': 'After the existing guard passes, verified new workflow origins are recorded in the manifest and here.',
                  'startup_control': 'explicitFreshPinnedSelection', 'private_storage_selection_seeded': True,
                  'automatic_storage_fallback_verified': False, 'telegram_authentication_verified': False,
                  'runtime_verified': False, 'physical_device_verified': False, 'release_qualified': False}
    (destination / 'delivery-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    shutil.copytree(qualification_dir, destination / 'evidence/qualification')
    shutil.copytree(startup, destination / 'evidence/startup')
    for mode, folder in (('unsigned-device', device_root), ('simulator', simulator_root)):
        target = destination / 'evidence' / mode
        target.mkdir()
        for path in (folder / 'fork-evidence').glob('*'):
            if path.is_file():
                shutil.copyfile(path, target / path.name)
        shutil.copyfile(verifications[mode], target / 'verification.json')
    files = sorted(path for path in destination.rglob('*') if path.is_file() and path != destination / 'SHA256SUMS')
    (destination / 'SHA256SUMS').write_text(''.join(digest(path) + '  ' + str(path.relative_to(destination)) + '\n' for path in files))
    print('Source-matched simulator startup and unsigned device package passed the existing packaging guard; external signing and physical-device verification remain required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    gate_parser = sub.add_parser('gate')
    gate_parser.add_argument('--run', required=True, type=int)
    gate_parser.add_argument('--expected-source', required=True)
    gate_parser.add_argument('--inventory', required=True, type=Path)
    gate_parser.add_argument('--output', required=True, type=Path)
    verify = sub.add_parser('verify-build')
    verify.add_argument('--mode', choices=BUILD_MODES, required=True)
    verify.add_argument('--source-root', required=True, type=Path)
    verify.add_argument('--qualification', required=True, type=Path)
    verify.add_argument('--output', required=True, type=Path)
    verify.add_argument('--build-number', required=True, type=int)
    pack = sub.add_parser('package')
    pack.add_argument('--source-root', required=True, type=Path)
    pack.add_argument('--qualification', required=True, type=Path)
    pack.add_argument('--device-root', required=True, type=Path)
    pack.add_argument('--simulator-root', required=True, type=Path)
    pack.add_argument('--output', required=True, type=Path)
    pack.add_argument('--build-number', required=True, type=int)
    args = parser.parse_args()
    try:
        if args.command == 'gate':
            gate(args.run, args.expected_source, args.inventory, args.output)
        elif args.command == 'verify-build':
            verify_build(args.mode, args.source_root.resolve(), args.qualification.resolve(), args.output.resolve(), args.build_number)
        else:
            package(args.source_root.resolve(), args.qualification.resolve(), args.device_root.resolve(),
                    args.simulator_root.resolve(), args.output.resolve(), args.build_number)
    except (Blocked, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile):
        # Never relay raw exceptions, GitHub responses, redirect URLs or private paths.
        print('Device delivery blocked: qualification, source or package evidence failed validation.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
