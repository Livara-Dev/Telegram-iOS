#!/usr/bin/env python3
"""Verify an existing simulator build for a diagnostic startup replay, without compiling.

Uses gh's normal read-only authentication. Never prints API errors, token values,
redirect URLs or arbitrary archive content. A replay is not release qualification.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tempfile
import zipfile

from ipa_inspect import inspect_ipa
from launch_smoke import PHASES, SMOKE_CODES, ERROR_DOMAINS

REPOSITORY = 'Livara-Dev/Telegram-iOS'
WORKFLOW = '.github/workflows/archive-integration.yml'
BUNDLE = 'org.livara.ayugram'
COMPILE_STEP = 'Build the full simulator app and run TextFormat regression tests'
SMOKE_STEP = 'Launch the real simulator application twice on a fresh device'
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')
MAX_ZIP_BYTES = 1024**3
MAX_CONTENT_BYTES = 2 * 1024**3
MAX_FILES = 20000
BASE = 'repos/' + REPOSITORY
MODES = {
    'simulator': {'job': 'application-build', 'compile': COMPILE_STEP, 'artifact': 'archive-integration-',
                  'prefix': 'fork-baseline-simulator', 'output': 'startup-replay'},
    'unsigned-device': {'job': 'unsigned-device-build', 'compile': 'Compile unsigned device package',
                        'artifact': 'archive-unsigned-device-', 'prefix': 'fork-baseline-unsigned-device',
                        'output': 'startup-replay-device'},
}


class ReplayError(ValueError):
    pass


def coordinates(run, source):
    value = str(run)
    if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) > 2**63 - 1:
        raise ReplayError('invalid_run_id')
    if not isinstance(source, str) or not SHA.fullmatch(source):
        raise ReplayError('invalid_source_commit')
    return int(value), source


def git(root, *args):
    try:
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise ReplayError('checkout_provenance_unavailable') from None


def validate_paths(paths):
    for path in paths:
        item = PurePosixPath(path)
        if (item.is_absolute() or '..' in item.parts or '\\' in path or
                not (path.startswith('tools/fork_baseline/') or path.startswith('docs/') or path == WORKFLOW)):
            raise ReplayError('application_source_changed')
    return len(set(paths))


def verify_checkout(root, source):
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if not SHA.fullmatch(head):
        raise ReplayError('checkout_provenance_unavailable')
    git(root, 'rev-parse', '--verify', source + '^{commit}')
    paths = []
    for arguments in ((source, 'HEAD'), ('HEAD',), ('--cached', 'HEAD')):
        data = git(root, 'diff', '--name-only', '--no-renames', '--ignore-submodules=none', '-z', *arguments, '--')
        paths.extend(value.decode('utf-8') for value in data.split(b'\0') if value)
        raw = git(root, 'diff', '--raw', '--no-renames', '--ignore-submodules=none', *arguments, '--')
        for line in raw.splitlines():
            modes = line.split(b' ', 2)[:2]
            if any(mode.lstrip(b':') == b'160000' for mode in modes):
                raise ReplayError('submodule_source_changed')
    paths.extend(value.decode('utf-8') for value in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if value)
    return head, validate_paths(paths)


def gh_json(endpoint):
    try:
        result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', endpoint],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        if result.returncode:
            raise ReplayError('github_read_failed')
        if len(result.stdout) > 4 * 1024**2:
            raise ReplayError('github_metadata_too_large')
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        raise ReplayError('github_metadata_unavailable') from None


def download(artifact_id, destination):
    try:
        with Path(destination).open('xb') as output:
            result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                BASE + '/actions/artifacts/' + str(artifact_id) + '/zip'],
                stdout=output, stderr=subprocess.PIPE, timeout=180, check=False)
        if result.returncode:
            raise ReplayError('github_artifact_download_failed')
    except (OSError, subprocess.SubprocessError):
        raise ReplayError('github_artifact_download_failed') from None


def select_artifact(run, source, api, mode='simulator'):
    if mode not in MODES: raise ReplayError('invalid_artifact_mode')
    specification = MODES[mode]
    value = api(BASE + '/actions/runs/' + str(run))
    if (not isinstance(value, dict) or value.get('id') != run or value.get('head_sha') != source or
            not isinstance(value.get('path'), str) or value['path'].split('@', 1)[0] != WORKFLOW or
            not isinstance(value.get('repository'), dict) or not isinstance(value.get('head_repository'), dict) or
            value['repository'].get('full_name') != REPOSITORY or value['head_repository'].get('full_name') != REPOSITORY):
        raise ReplayError('run_source_mismatch')
    repo_id = value['repository'].get('id')
    head_repo_id = value['head_repository'].get('id')
    if type(repo_id) is not int or repo_id != head_repo_id:
        raise ReplayError('run_repository_mismatch')
    jobs = api(BASE + '/actions/runs/' + str(run) + '/jobs?filter=latest&per_page=100')
    if not isinstance(jobs, dict) or not isinstance(jobs.get('jobs'), list) or type(jobs.get('total_count')) is not int or not 0 <= jobs['total_count'] <= 100:
        raise ReplayError('invalid_job_metadata')
    applications = [job for job in jobs['jobs'] if isinstance(job, dict) and job.get('name') == specification['job']]
    if len(applications) != 1:
        raise ReplayError('application_build_not_found')
    job = applications[0]
    if not isinstance(job.get('steps'), list):
        raise ReplayError('invalid_job_metadata')
    compiled = [step for step in job['steps'] if isinstance(step, dict) and step.get('name') == specification['compile']]
    if (job.get('run_id') != run or job.get('status') != 'completed' or type(job.get('id')) is not int or
            len(compiled) != 1 or compiled[0].get('status') != 'completed' or compiled[0].get('conclusion') != 'success'):
        raise ReplayError('application_compilation_not_verified')
    conclusion = job.get('conclusion')
    if not isinstance(conclusion, str) or conclusion not in {'success', 'failure', 'cancelled', 'timed_out', 'skipped', 'neutral', 'action_required'}:
        raise ReplayError('invalid_job_conclusion')
    if mode == 'unsigned-device' and conclusion != 'success':
        raise ReplayError('device_build_not_successful')
    smoke = [step for step in job['steps'] if isinstance(step, dict) and step.get('name') == SMOKE_STEP]
    prior_smoke_passed = len(smoke) == 1 and smoke[0].get('conclusion') == 'success'
    result = api(BASE + '/actions/runs/' + str(run) + '/artifacts?per_page=100')
    if not isinstance(result, dict) or not isinstance(result.get('artifacts'), list) or type(result.get('total_count')) is not int or not 0 <= result['total_count'] <= 100:
        raise ReplayError('invalid_artifact_metadata')
    artifacts = [a for a in result['artifacts'] if isinstance(a, dict) and a.get('name') == specification['artifact'] + source]
    if len(artifacts) != 1:
        raise ReplayError('simulator_artifact_not_unique')
    artifact = artifacts[0]
    provenance = artifact.get('workflow_run', {})
    if (not isinstance(provenance, dict) or type(artifact.get('id')) is not int or artifact['id'] <= 0 or artifact.get('expired') is not False or
            type(artifact.get('size_in_bytes')) is not int or not 0 < artifact['size_in_bytes'] <= MAX_ZIP_BYTES or
            not isinstance(artifact.get('digest'), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', artifact['digest']) or
            provenance.get('id') != run or provenance.get('head_sha') != source or
            provenance.get('repository_id') != repo_id or provenance.get('head_repository_id') != head_repo_id):
        raise ReplayError('artifact_provenance_mismatch')
    return artifact, {'application_build_job_id': job['id'], 'source_job_conclusion': conclusion,
                      'source_compile_step_passed': True, 'prior_smoke_passed': prior_smoke_passed}


def digest_stream(source):
    hasher = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        hasher.update(chunk)
    return hasher.hexdigest()


def member_name(value, directory=False):
    name = value[:-1] if directory and value.endswith('/') else value
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name or '\0' in name or path.as_posix() != name):
        raise ReplayError('unsafe_artifact_path')
    return name


def smoke_failure(value):
    if (not isinstance(value, dict) or set(value) != {'code', 'phase', 'return_code', 'timeout', 'system_errors'} or
            not isinstance(value['code'], str) or value['code'] not in SMOKE_CODES or
            not isinstance(value['phase'], str) or value['phase'] not in PHASES or type(value['timeout']) is not bool or
            not (value['return_code'] is None or type(value['return_code']) is int and -(2**31) <= value['return_code'] < 2**31) or
            not isinstance(value['system_errors'], list) or len(value['system_errors']) > 4):
        raise ReplayError('unexpected_prior_smoke_fields')
    for error in value['system_errors']:
        if (not isinstance(error, dict) or set(error) != {'domain', 'code'} or not isinstance(error['domain'], str) or
                error['domain'] not in ERROR_DOMAINS or type(error['code']) is not int or not -(2**31) <= error['code'] < 2**31):
            raise ReplayError('unexpected_prior_smoke_fields')


def prior_smoke_report(value):
    fields = {'mode', 'physical_device_verified', 'authentication_codes_used', 'launches', 'passed'}
    stages = {'applicationEntered', 'windowCreated', 'sceneAttached', 'storageReady', 'accountManagerReady',
              'sharedContextReady', 'authorizationPrepared', 'conversationsPrepared', 'authorizationVisible',
              'conversationsVisible', 'failed', 'notObserved'}
    failures = {None, 'storageUnavailable', 'keychainUnavailable', 'storageConfiguration', 'initializationUnavailable'}
    if (not isinstance(value, dict) or not fields.issubset(value) or not set(value).issubset(fields | {'phase', 'failure', 'cleanup_failures'}) or value['mode'] != 'fresh_simulator_launch' or
            value['physical_device_verified'] is not False or value['authentication_codes_used'] is not False or
            type(value['passed']) is not bool or not isinstance(value['launches'], list) or len(value['launches']) > 2):
        raise ReplayError('unexpected_prior_smoke_fields')
    if 'phase' in value and (not isinstance(value['phase'], str) or value['phase'] not in PHASES):
        raise ReplayError('unexpected_prior_smoke_fields')
    if value.get('failure') is not None:
        smoke_failure(value['failure'])
    cleanup = value.get('cleanup_failures', [])
    if not isinstance(cleanup, list) or len(cleanup) > 4:
        raise ReplayError('unexpected_prior_smoke_fields')
    for error in cleanup:
        smoke_failure(error)
    for launch in value['launches']:
        if (not isinstance(launch, dict) or not set(launch).issubset({'attempt', 'marker', 'screenshot', 'seconds_to_marker', 'storage_kind'}) or
                type(launch.get('attempt')) is not int or launch['attempt'] not in {1, 2}):
            raise ReplayError('unexpected_prior_smoke_fields')
        marker, screen = launch.get('marker'), launch.get('screenshot')
        if (not isinstance(marker, dict) or set(marker) != {'stage', 'failure'} or
                not isinstance(marker['stage'], str) or marker['stage'] not in stages or
                not (marker['failure'] is None or isinstance(marker['failure'], str) and marker['failure'] in failures) or
                not isinstance(screen, dict) or set(screen) != {'nonblank', 'width', 'height'} or type(screen['nonblank']) is not bool or
                any(type(screen[k]) is not int or not 0 < screen[k] <= 20000 for k in ('width', 'height'))):
            raise ReplayError('unexpected_prior_smoke_fields')
        seconds = launch.get('seconds_to_marker')
        if type(seconds) not in {int, float} or not math.isfinite(seconds) or not 0 <= seconds <= 3600:
            raise ReplayError('unexpected_prior_smoke_fields')
        if 'storage_kind' in launch and (not isinstance(launch['storage_kind'], str) or launch['storage_kind'] not in {'private', 'group', 'unverified'}):
            raise ReplayError('unexpected_prior_smoke_fields')
    return value


def unsigned_device_inspection(path):
    """Check every Mach-O, not just the main executable, without executing code."""
    inspection = inspect_ipa(path, 'unsigned-device', BUNDLE)
    count = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES or len({e.filename for e in entries}) != len(entries):
            raise ReplayError('invalid_ipa_entries')
        total = 0
        for entry in entries:
            name = member_name(entry.filename, directory=entry.is_dir())
            if (entry.flag_bits & 1 or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or '_CodeSignature' in PurePosixPath(name).parts or name.endswith('/embedded.mobileprovision')):
                raise ReplayError('device_ipa_not_unsigned')
            total += entry.file_size
            if entry.file_size > MAX_ZIP_BYTES or total > MAX_CONTENT_BYTES:
                raise ReplayError('ipa_content_limit_exceeded')
            if entry.is_dir(): continue
            with archive.open(entry) as stream:
                magic = stream.read(4)
                if magic != b'\xcf\xfa\xed\xfe':
                    if magic in {b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
                                 b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}:
                        raise ReplayError('unsupported_device_macho')
                    continue
                header = magic + stream.read(28)
                if len(header) != 32: raise ReplayError('invalid_device_macho')
                _, cpu, _, filetype, commands, size, _, _ = struct.unpack('<8I', header)
                if (cpu != 0x0100000c or filetype not in {2, 6, 8} or not 1 <= commands <= 4096 or
                        not commands * 8 <= size <= min(1024**2, entry.file_size - 32)):
                    raise ReplayError('invalid_device_macho')
                data = stream.read(size)
                if len(data) != size: raise ReplayError('invalid_device_macho')
                position, platform = 0, None
                for _ in range(commands):
                    if position + 8 > len(data): raise ReplayError('invalid_device_macho')
                    code, length = struct.unpack_from('<II', data, position)
                    if length < 8 or length % 8 or position + length > len(data):
                        raise ReplayError('invalid_device_macho')
                    if code == 0x1d: raise ReplayError('device_ipa_not_unsigned')  # LC_CODE_SIGNATURE
                    if code == 0x32:
                        if length < 24 or platform is not None: raise ReplayError('invalid_device_macho')
                        platform = struct.unpack_from('<I', data, position + 8)[0]
                    position += length
                if position != len(data) or platform != 2: raise ReplayError('device_macho_platform_mismatch')
                count += 1
    if not count: raise ReplayError('device_macho_missing')
    return {**inspection, 'unsigned_macho_count': count, 'all_macho_platforms': 2, 'signing': 'disabled'}


def verify_extract(archive_path, artifact, source, destination, mode='simulator'):
    if mode not in MODES: raise ReplayError('invalid_artifact_mode')
    archive_path, destination = Path(archive_path), Path(destination)
    if archive_path.stat().st_size != artifact['size_in_bytes']:
        raise ReplayError('artifact_size_mismatch')
    with archive_path.open('rb') as stream:
        outer_digest = digest_stream(stream)
    if 'sha256:' + outer_digest != artifact['digest']:
        raise ReplayError('artifact_digest_mismatch')
    if destination.exists() or destination.is_symlink():
        raise ReplayError('replay_output_already_exists')
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or len({e.filename for e in entries}) != len(entries):
                raise ReplayError('invalid_artifact_entries')
            total = 0
            for entry in entries:
                member_name(entry.filename, directory=entry.is_dir())
                if entry.flag_bits & 1 or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ReplayError('unsafe_artifact_entry_type')
                total += entry.file_size
                if entry.file_size > MAX_ZIP_BYTES or total > MAX_CONTENT_BYTES:
                    raise ReplayError('artifact_content_limit_exceeded')
            checksums, verified = [], set()
            names = {e.filename for e in entries if not e.is_dir()}
            for name in sorted(n for n in names if n.endswith('SHA256SUMS')):
                if archive.getinfo(name).file_size > 2 * 1024**2:
                    raise ReplayError('checksum_file_too_large')
                checksums.append(name)
                prefix = PurePosixPath(name).parent
                for line in archive.read(name).decode('utf-8').splitlines():
                    parts = line.split('  ', 1)
                    if len(parts) != 2 or not DIGEST.fullmatch(parts[0]):
                        raise ReplayError('invalid_inner_checksum')
                    relative = member_name(parts[1])
                    target = member_name((prefix / relative).as_posix())
                    if target not in names:
                        raise ReplayError('checksum_member_missing')
                    with archive.open(target) as stream:
                        if digest_stream(stream) != parts[0]:
                            raise ReplayError('inner_checksum_mismatch')
                    verified.add(target)
            ipa_name = MODES[mode]['prefix'] + '/Telegram.ipa'
            manifest_name = MODES[mode]['prefix'] + '/manifest.json'
            if not checksums or ipa_name not in verified or manifest_name not in names:
                raise ReplayError('required_artifact_evidence_missing')
            if archive.getinfo(manifest_name).file_size > 65536:
                raise ReplayError('manifest_too_large')
            manifest = json.loads(archive.read(manifest_name))
            if (not isinstance(manifest, dict) or manifest.get('source_commit') != source or manifest.get('mode') != mode or
                    manifest.get('bundle_id') != BUNDLE or not isinstance(manifest.get('sha256'), str) or not DIGEST.fullmatch(manifest['sha256'])):
                raise ReplayError('simulator_manifest_mismatch')
            if mode == 'unsigned-device' and (manifest.get('signing') != 'disabled' or manifest.get('runtime_verified') is not False
                                               or manifest.get('signature_verified') is not False):
                raise ReplayError('device_manifest_not_unsigned')
            destination.mkdir(parents=True, mode=0o700)
            ipa = destination / 'Telegram.ipa'
            with archive.open(ipa_name) as src, ipa.open('xb') as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
            ipa.chmod(0o600)
            with ipa.open('rb') as stream:
                if digest_stream(stream) != manifest['sha256']:
                    raise ReplayError('ipa_digest_mismatch')
            inspection = unsigned_device_inspection(ipa) if mode == 'unsigned-device' else inspect_ipa(ipa, mode, BUNDLE)
            evidence = destination / 'archive-evidence'
            evidence.mkdir(mode=0o700)
            (evidence / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            smoke_name = 'fork-evidence/launch-smoke/result.json'
            smoke_present = smoke_name in names
            if smoke_present:
                if archive.getinfo(smoke_name).file_size > 65536:
                    raise ReplayError('prior_smoke_evidence_too_large')
                original = prior_smoke_report(json.loads(archive.read(smoke_name)))
                (evidence / 'prior-launch-result.json').write_text(json.dumps(original, indent=2) + '\n')
            return {'outer_sha256': outer_digest, 'ipa_sha256': manifest['sha256'],
                    'checksum_files_verified': len(checksums), 'inner_payloads_verified': len(verified),
                    'prior_smoke_evidence_present': smoke_present, 'main_executable': inspection}
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError):
        raise ReplayError('invalid_simulator_artifact') from None


def replay(run, source, output, root=None, api=gh_json, fetch=download, mode='simulator'):
    if mode not in MODES: raise ReplayError('invalid_artifact_mode')
    run, source = coordinates(run, source)
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    expected = root / ('build/fork-evidence/' + MODES[mode]['output'])
    if output != expected or any(parent.is_symlink() for parent in (output, *output.parents)):
        raise ReplayError('invalid_replay_output_path')
    head, changed_count = verify_checkout(root, source)
    artifact, provenance = select_artifact(run, source, api, mode)
    with tempfile.TemporaryDirectory(prefix='ayu-replay-download-') as temporary:
        archive_path = Path(temporary) / 'simulator-artifact.zip'
        fetch(artifact['id'], archive_path)
        result = verify_extract(archive_path, artifact, source, output, mode)
    report = {'schema': 1, 'mode': mode, 'repository': REPOSITORY, 'source_run': run, 'source_commit': source,
              'replay_checkout_commit': head, 'artifact_id': artifact['id'], 'allowed_tooling_changes': changed_count,
              'application_source_unchanged': True, **provenance, **result,
              'diagnostic_replay_only': True, 'release_qualified': False, 'physical_device_verified': False}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=tuple(MODES), default='simulator')
    args = parser.parse_args(argv)
    try:
        report = replay(args.run, args.source, args.output, mode=args.mode)
    except ReplayError as error:
        print(json.dumps({'error': str(error), 'verified': False}, sort_keys=True))
        raise SystemExit(1) from None
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({'error': 'replay_verification_failed', 'verified': False}, sort_keys=True))
        raise SystemExit(1) from None
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
