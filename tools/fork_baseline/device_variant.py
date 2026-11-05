#!/usr/bin/env python3
"""Package a separately named unsigned device control after verified simulator startup.

This tool has no network access or signing step. Only the main app's PlugIns
members are removed; retained ZIP member bytes, identity and metadata are checked.
It does not establish device installation, authentication or feature correctness.
"""
import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from replay_artifact import (BUNDLE, DIGEST, REPOSITORY, ReplayError, coordinates,
                             digest_stream, prior_smoke_report, unsigned_device_inspection)


def digest(path):
    with Path(path).open('rb') as stream:
        return digest_stream(stream)


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 65536:
        raise ReplayError('invalid_variant_evidence')
    try:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict): raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise ReplayError('invalid_variant_evidence') from None


def validate_startup(device, simulator, proof, result):
    """Fail before creating a package unless the exact source's control passed."""
    for report, mode in ((device, 'unsigned-device'), (simulator, 'simulator')):
        if (report.get('schema') != 1 or report.get('mode') != mode or report.get('repository') != REPOSITORY
                or report.get('application_source_unchanged') is not True or report.get('source_compile_step_passed') is not True
                or report.get('physical_device_verified') is not False or report.get('release_qualified') is not False
                or not isinstance(report.get('ipa_sha256'), str) or not DIGEST.fullmatch(report['ipa_sha256'])
                or not isinstance(report.get('outer_sha256'), str) or not DIGEST.fullmatch(report['outer_sha256'])
                or type(report.get('artifact_id')) is not int or report['artifact_id'] <= 0):
            raise ReplayError('invalid_variant_provenance')
        coordinates(report.get('source_run'), report.get('source_commit'))
    if (device['source_commit'] != simulator['source_commit'] or device['source_run'] != simulator['source_run']
            or device.get('source_job_conclusion') != 'success'):
        raise ReplayError('variant_source_mismatch')
    if (proof.get('mode') != 'main_app_only' or proof.get('startup_variant') != 'main-app-only'
            or proof.get('main_app_only_control_passed') is not True or proof.get('passed') is not True or proof.get('phase') != 'complete'
            or proof.get('source_codesign_verified') is not True or proof.get('main_code_unchanged') is not True
            or proof.get('source_ipa_unchanged') is not True or proof.get('application_recompiled') is not False
            or proof.get('physical_device_verified') is not False
            or proof.get('source_simulator_ipa_sha256') != simulator['ipa_sha256']
            or type(proof.get('extensions_removed')) is not int or not 1 <= proof['extensions_removed'] <= 32):
        raise ReplayError('startup_variant_not_verified')
    checked = prior_smoke_report(result)
    if (checked.get('passed') is not True or checked.get('phase') != 'complete' or checked.get('failure') is not None
            or len(checked['launches']) != 2):
        raise ReplayError('startup_variant_not_verified')
    for attempt, launch in enumerate(checked['launches'], 1):
        if (launch.get('attempt') != attempt or launch.get('storage_kind') != 'private'
                or launch['marker']['stage'] not in {'authorizationVisible', 'conversationsVisible'}
                or launch['marker']['failure'] is not None or launch['screenshot']['nonblank'] is not True):
            raise ReplayError('startup_variant_not_verified')
    return device['source_commit'], device['source_run'], proof['extensions_removed']


def metadata(entry):
    return (entry.date_time, entry.compress_type, entry.comment, entry.extra,
            entry.external_attr, entry.internal_attr, entry.create_system)


def strip_extensions(source, destination, expected_count):
    """Retain every member outside the sole main app's PlugIns, byte for byte."""
    unsigned_device_inspection(source)
    with zipfile.ZipFile(source) as original:
        infos = [entry.filename for entry in original.infolist()
                 if len(PurePosixPath(entry.filename).parts) == 3 and entry.filename.endswith('.app/Info.plist')]
        if len(infos) != 1: raise ReplayError('invalid_main_bundle')
        prefix = infos[0][:-len('Info.plist')] + 'PlugIns/'
        if prefix[:-1] in original.namelist(): raise ReplayError('extension_boundary_mismatch')
        removed = [entry for entry in original.infolist() if entry.filename.startswith(prefix)]
        children = {entry.filename[len(prefix):].split('/', 1)[0] for entry in removed if entry.filename != prefix}
        if (len(children) != expected_count or any(not name.endswith('.appex') for name in children)
                or any(prefix + name + '/Info.plist' not in original.namelist() for name in children)):
            raise ReplayError('extension_boundary_mismatch')
        retained = [entry for entry in original.infolist() if not entry.filename.startswith(prefix)]
        with zipfile.ZipFile(destination, 'x') as output:
            output.comment = original.comment
            for entry in retained:
                # ZipFile.open mutates header offsets on a ZipInfo. Keep the
                # source's descriptors intact for the independent comparison.
                with original.open(entry) as source_file, output.open(copy.copy(entry), 'w') as target:
                    while chunk := source_file.read(1024 * 1024): target.write(chunk)
        with zipfile.ZipFile(destination) as output:
            if output.namelist() != [entry.filename for entry in retained] or output.comment != original.comment:
                raise ReplayError('retained_members_changed')
            for entry in retained:
                retained_entry = output.getinfo(entry.filename)
                with original.open(entry) as first, output.open(retained_entry) as second:
                    if digest_stream(first) != digest_stream(second) or metadata(entry) != metadata(retained_entry):
                        raise ReplayError('retained_members_changed')
    inspection = unsigned_device_inspection(destination)
    return {'extensions_removed': len(children), 'retained_entries': len(retained),
            'retained_member_bytes_unchanged': True, 'retained_metadata_unchanged': True,
            'main_code_unchanged': True, 'framework_files_unchanged': True, 'main_executable': inspection}


def package(ipa, verification, simulator_verification, startup_proof, startup_result, output):
    ipa, output = Path(ipa), Path(output)
    device, simulator = read_json(verification), read_json(simulator_verification)
    proof, result = read_json(startup_proof), read_json(startup_result)
    source, run, extension_count = validate_startup(device, simulator, proof, result)
    if ipa.is_symlink() or not ipa.is_file() or digest(ipa) != device['ipa_sha256']:
        raise ReplayError('device_ipa_digest_mismatch')
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ReplayError('invalid_variant_output')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.main-app-only-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'package'; staging.mkdir(mode=0o700)
        filename = 'AyuGram-' + source[:8] + '-main-app-only-unsigned-device.ipa'
        report = strip_extensions(ipa, staging / filename, extension_count)
        if digest(ipa) != device['ipa_sha256']: raise ReplayError('source_package_changed')
        manifest = {'schema': 1, 'mode': 'unsigned-device', 'variant': 'main-app-only', 'bundle_id': BUNDLE,
                    'source_commit': source, 'source_run': run, 'source_artifact_id': device['artifact_id'],
                    'source_ipa_sha256': device['ipa_sha256'], 'source_outer_sha256': device['outer_sha256'],
                    'source_simulator_ipa_sha256': simulator['ipa_sha256'], 'startup_proof_sha256': digest(startup_proof),
                    'startup_result_sha256': digest(startup_result), 'sha256': digest(staging / filename),
                    'filename': filename, 'signing': 'disabled', 'installable_signed_ipa': False,
                    'signature_verified': False, 'runtime_verified': False, 'physical_device_verified': False,
                    'simulator_variant_startup_verified': True, 'application_recompiled': False,
                    'source_ipa_unchanged': True, 'build_number_changed': False,
                    'extensions_available': False, 'release_qualified': False, **report}
        (staging / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        (staging / 'SHA256SUMS').write_text(''.join(digest(staging / name) + '  ' + name + '\n'
                                                   for name in (filename, 'manifest.json')))
        staging.rename(output)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ipa', 'verification', 'simulator-verification', 'startup-proof', 'startup-result', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = package(args.ipa, args.verification, args.simulator_verification, args.startup_proof,
                         args.startup_result, args.output)
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({'error': 'device_variant_not_published', 'physical_device_verified': False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
