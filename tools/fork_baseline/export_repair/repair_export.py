#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2026 AyuGram iOS contributors
# Added: 2026-11-06
"""Private, offline evidence export repair. Never builds, signs, or writes an IPA.

Read a completed, audited delivery export and its existing simulator intermediate.
Create a separate export with complete native and TextFormat evidence. No network.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

SOURCE = '5d241267735b3958c225681d173853568bab705d'
DELIVERY_RUN = 42279866706
NATIVE_RUN = 42272821047
NATIVE_ARTIFACT = 13533034081
NATIVE_ZIP_SHA256 = '7b67ad73d3338a48fb1e04b009db6c2650b9109302a573edd49c0ae458f2790c'
NATIVE_ADDITIONS = ('host.json', 'host.log', 'integration-tests.log', 'portable-tests.log', 'runtime.log')
TEXTFORMAT_FILES = ('collection.json', 'test.xml', 'test.log')
TEXTFORMAT_TARGET = '//submodules/TextFormat:TextFormatTests'
IPA_NAME = 'AyuGram-5d241267-main-app-only-unsigned-device.ipa'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return stream_digest(stream)


def stream_digest(stream):
    result = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        result.update(chunk)
    return result.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    require(isinstance(name, str) and name and not path.is_absolute() and '..' not in path.parts
            and '\\' not in name and str(path) == name
            and all(ord(c) >= 32 and ord(c) != 127 for c in name), 'unsafe relative evidence path')
    return name


def inventory(root):
    require(root.is_dir() and not root.is_symlink(), 'export root must be a regular directory')
    result = {}
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'symlinks are forbidden in an export')
        if path.is_dir():
            continue
        require(stat.S_ISREG(path.stat().st_mode), 'nonregular export member')
        result[safe_name(path.relative_to(root).as_posix())] = path
    return result


def checksums(root):
    files = inventory(root)
    require('SHA256SUMS' in files, 'missing export checksums')
    rows = {}
    for line in files['SHA256SUMS'].read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None and match[2] not in rows, 'invalid or duplicate checksum row')
        rows[safe_name(match[2])] = match[1]
    require(set(rows) == set(files) - {'SHA256SUMS'}, 'export checksum inventory is incomplete')
    require(all(digest(files[name]) == value for name, value in rows.items()), 'export checksum mismatch')
    return files


def zip_members(archive):
    entries = archive.infolist()
    require(len(entries) <= 512 and len({i.filename for i in entries}) == len(entries)
            and sum(i.file_size for i in entries) <= 2 * 1024**3, 'archive limits exceeded')
    for item in entries:
        safe_name(item.filename.rstrip('/') if item.is_dir() else item.filename)
        require(not stat.S_ISLNK(item.external_attr >> 16) and not item.flag_bits & 1
                and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'unsafe archive member')
    return {item.filename: item for item in entries}


def bounded_read(archive, name, maximum=2 * 1024**2):
    item = archive.getinfo(name)
    require(not item.is_dir() and item.file_size <= maximum, 'evidence exceeds fixed bounds')
    return archive.read(item)


def copy_only_export(root, additions, record, output):
    """Preserve every original byte; only add files and regenerate root checksums."""
    files = checksums(root)
    require(IPA_NAME in files, 'expected candidate IPA missing')
    ipa_sha = digest(files[IPA_NAME])
    manifest = json.loads(files['manifest.json'].read_bytes())
    require(manifest.get('sha256') == ipa_sha, 'candidate IPA does not match its manifest')
    require(not output.exists() and not output.is_symlink()
            and output.resolve() not in root.resolve().parents
            and root.resolve() not in output.resolve().parents, 'output must be a new separate export')
    for name, data in additions.items():
        safe_name(name)
        require(name not in files and name != 'SHA256SUMS' and not name.lower().endswith('.ipa')
                and isinstance(data, bytes), 'repair may only add evidence files')
    original_hashes = {name: digest(path) for name, path in files.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.export-repair-', dir=output.parent) as temporary:
        staging = Path(temporary) / 'delivery'
        shutil.copytree(root, staging)
        for name, data in additions.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
        require(all(digest(staging / name) == value for name, value in original_hashes.items()),
                'original export bytes changed while copying')
        record = {**record, 'schema': 1, 'ipa_sha256_before': ipa_sha, 'ipa_sha256_after': digest(staging / IPA_NAME),
                  'ipa_bytes_unchanged': True, 'original_files_unchanged': True,
                  'original_export_checksums_sha256': original_hashes['SHA256SUMS'],
                  'added_paths': sorted(additions), 'application_rebuilt': False, 'ci_dispatched': False}
        with (staging / 'export-repair.json').open('x') as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write('\n')
        rows = inventory(staging)
        (staging / 'SHA256SUMS').write_text(''.join(digest(rows[name]) + '  ' + name + '\n'
                                                  for name in sorted(rows) if name != 'SHA256SUMS'))
        checksums(staging)
        require(digest(staging / IPA_NAME) == ipa_sha and digest(root / IPA_NAME) == ipa_sha,
                'candidate IPA changed')
        staging.rename(output)
    return record


def repair(root, native_zip, simulator_zip, output, helper_path, inventory_path):
    files = checksums(root)
    spec = importlib.util.spec_from_file_location('reviewed_delivery_helper', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    manifest = helper.document(files['manifest.json'].read_bytes())
    provenance = helper.document(files['delivery-provenance.json'].read_bytes())
    qualification = helper.document(files['evidence/qualification/qualification.json'].read_bytes())
    require(manifest.get('source_commit') == SOURCE and manifest.get('source_run') == DELIVERY_RUN
            and manifest.get('filename') == IPA_NAME and manifest.get('signing') == 'disabled'
            and manifest.get('physical_device_verified') is False, 'unexpected final candidate origin')
    require(qualification.get('source_commit') == SOURCE and qualification.get('qualification_run') == NATIVE_RUN
            and qualification.get('qualification_artifact_id') == NATIVE_ARTIFACT
            and qualification.get('qualification_artifact_digest') == 'sha256:' + NATIVE_ZIP_SHA256,
            'unexpected native qualification origin')
    require(digest(native_zip) == NATIVE_ZIP_SHA256, 'cached native ZIP digest differs')
    reports, _ = helper.verify_evidence(native_zip, SOURCE, helper.document(inventory_path.read_bytes()))
    require(reports == qualification.get('native_suites'), 'native qualification reports differ')
    additions = {}
    native_prefix = 'evidence/qualification/native-evidence/'
    with zipfile.ZipFile(native_zip) as archive:
        zip_members(archive)
        for name in archive.namelist():
            target = native_prefix + name
            data = bounded_read(archive, name, 16 * 1024**2)
            if target in files:
                require(files[target].read_bytes() == data, 'retained native evidence differs from original')
            else:
                require(name in NATIVE_ADDITIONS, 'unexpected omitted native evidence')
                additions[target] = data
    require(set(additions) == {native_prefix + n for n in NATIVE_ADDITIONS}, 'native omission set differs')
    origin = provenance['build_origins']['simulator']
    require(origin.get('app_source_commit') == SOURCE and origin.get('run_id') == DELIVERY_RUN
            and origin.get('artifact_id') == manifest.get('source_simulator_artifact_id')
            and origin.get('artifact_digest') == 'sha256:' + manifest['source_simulator_outer_sha256']
            and digest(simulator_zip) == manifest['source_simulator_outer_sha256'], 'simulator artifact origin/digest mismatch')
    with zipfile.ZipFile(simulator_zip) as archive:
        zip_members(archive)
        sim_manifest_bytes = bounded_read(archive, 'fork-baseline-simulator/manifest.json')
        sim_manifest = helper.document(sim_manifest_bytes)
        proof_bytes = bounded_read(archive, 'fork-evidence/build-verification.json')
        proof = helper.document(proof_bytes)
        require(sim_manifest.get('source_commit') == SOURCE and sim_manifest.get('mode') == 'simulator'
                and sim_manifest.get('tests') == {TEXTFORMAT_TARGET: 'passed'}
                and sim_manifest.get('sha256') == manifest['source_simulator_ipa_sha256']
                and proof.get('source_run') == DELIVERY_RUN and proof.get('source_commit') == SOURCE
                and proof.get('source_manifest_sha256') == hashlib.sha256(sim_manifest_bytes).hexdigest()
                and proof.get('workflow_commit') == origin.get('workflow_commit')
                and files['evidence/simulator/build-verification.json'].read_bytes() == proof_bytes,
                'source-matched simulator evidence mismatch')
        with archive.open('fork-baseline-simulator/Telegram.ipa') as stream:
            require(stream_digest(stream) == sim_manifest['sha256'], 'simulator IPA digest mismatch')
        textformat = {name: bounded_read(archive, 'fork-evidence/textformat/' + name) for name in TEXTFORMAT_FILES}
        require(helper.document(textformat['collection.json']) == {
            'target': TEXTFORMAT_TARGET, 'invocation': 'passed',
            'files': {'test.xml': 'retained_redacted', 'test.log': 'retained_redacted'}},
            'TextFormat collection was not passed and retained redacted')
        textformat_report, _ = helper.native_tests(textformat['test.xml'], textformat['test.log'])
        additions.update({'evidence/simulator/textformat/' + name: data for name, data in textformat.items()})
    record = {'application_source_commit': SOURCE, 'delivery_run': DELIVERY_RUN,
              'native_run': NATIVE_RUN, 'native_artifact_id': NATIVE_ARTIFACT,
              'native_zip_sha256': NATIVE_ZIP_SHA256, 'native_inner_references': 12,
              'simulator_artifact_id': origin['artifact_id'], 'simulator_zip_sha256': digest(simulator_zip),
              'textformat': textformat_report, 'purpose': 'Complete selected export evidence only'}
    result = copy_only_export(root, additions, record, output)
    checksums(output / 'evidence/qualification/native-evidence')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('export', 'native-zip', 'simulator-zip', 'output', 'helper', 'inventory'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = repair(args.export, args.native_zip, args.simulator_zip, args.output, args.helper, args.inventory)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
