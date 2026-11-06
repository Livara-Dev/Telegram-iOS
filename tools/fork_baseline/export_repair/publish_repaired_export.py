#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2026 AyuGram iOS contributors
# Added: 2026-11-06
"""One fixed-run, packaging-only export repair; no build, signing, or app execution.

Only read GitHub metadata/artifacts, statically inspect an existing IPA, copy
evidence, and write a new export. The request must be published after success.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import repair_export as repair

REPOSITORY = 'Livara-Dev/Telegram-iOS'
ORIGINAL_TOOL_SHA = '2f68bca8027980250bf8f9b0328bb1e063fc55c1'
ORIGINAL_WORKFLOW = '.github/workflows/unsigned-device-delivery.yml'
NATIVE_WORKFLOW = '.github/workflows/archive-integration.yml'
REPAIR_BRANCH = 'ci/qualified-device-export-repair'
REPAIR_REF = 'refs/heads/' + REPAIR_BRANCH
REPAIR_WORKFLOW = '.github/workflows/repair-qualified-device-export.yml'
REQUEST_PATH = 'ci/qualified-device-export-repair-request.json'
ARTIFACT_NAMES = {
    'final': 'qualified-main-app-only-unsigned-device-' + repair.SOURCE,
    'simulator': 'device-delivery-simulator-' + repair.SOURCE,
    'native': 'archive-integration-tests-' + repair.SOURCE,
}
SHA256 = re.compile(r'[0-9a-f]{64}')


def document(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            repair.require(key not in result, 'duplicate JSON field')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def request(path, event, ref, repository):
    repair.require(event == 'push' and ref == REPAIR_REF and repository == REPOSITORY,
                   'unsupported repair trigger')
    repair.require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 1024,
                   'missing or unsafe explicit repair request')
    value = document(path.read_bytes())
    repair.require(isinstance(value, dict) and set(value) == {
        'schema', 'delivery_run', 'source_commit', 'final_artifact_id', 'simulator_artifact_id'}
        and type(value['schema']) is int and value['schema'] == 1
        and type(value['delivery_run']) is int and value['delivery_run'] == repair.DELIVERY_RUN
        and value['source_commit'] == repair.SOURCE
        and all(type(value[k]) is int and 0 < value[k] < 2**63 for k in ('final_artifact_id', 'simulator_artifact_id'))
        and value['final_artifact_id'] != value['simulator_artifact_id'], 'invalid fixed-run repair request')
    return value


def api(endpoint):
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                             'repos/' + REPOSITORY + '/' + endpoint],
                            capture_output=True, timeout=30, check=False)
    repair.require(result.returncode == 0 and len(result.stdout) <= 8 * 1024**2,
                   'GitHub metadata read failed')
    return document(result.stdout)


def completed_run(run, native=False):
    run_id = repair.NATIVE_RUN if native else repair.DELIVERY_RUN
    source = repair.SOURCE if native else ORIGINAL_TOOL_SHA
    workflow = NATIVE_WORKFLOW if native else ORIGINAL_WORKFLOW
    repair.require(isinstance(run, dict) and run.get('id') == run_id and run.get('head_sha') == source
                   and run.get('path', '').split('@', 1)[0] == workflow
                   and run.get('status') == 'completed' and run.get('conclusion') == 'success'
                   and type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
                   'original run has not completed successfully at the pinned origin')
    for key in ('repository', 'head_repository'):
        repair.require(isinstance(run.get(key), dict) and run[key].get('full_name') == REPOSITORY
                       and type(run[key].get('id')) is int, 'original repository mismatch')
    repair.require(run['repository']['id'] == run['head_repository']['id'], 'fork-origin run forbidden')
    repair.require((native and run.get('event') == 'workflow_dispatch') or
                   (not native and run.get('event') == 'push' and run.get('head_branch') == 'ci/qualified-device-delivery'),
                   'original trigger origin mismatch')


def select_artifact(role, listing, run, requested_id):
    repair.require(isinstance(listing, dict) and isinstance(listing.get('artifacts'), list)
                   and listing.get('total_count') == len(listing['artifacts']) <= 100,
                   'incomplete artifact inventory')
    rows = [a for a in listing['artifacts'] if a.get('name') == ARTIFACT_NAMES[role]]
    repair.require(len(rows) == 1, 'artifact name is missing or ambiguous')
    artifact = rows[0]
    origin = artifact.get('workflow_run', {})
    maximum = 64 * 1024**2 if role == 'native' else 1024**3
    repair.require(type(artifact.get('id')) is int and artifact['id'] == requested_id
                   and artifact.get('expired') is False
                   and type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= maximum
                   and re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', ''))
                   and origin.get('id') == run['id'] and origin.get('head_sha') == run['head_sha']
                   and origin.get('repository_id') == run['repository']['id']
                   and origin.get('head_repository_id') == run['head_repository']['id'],
                   'artifact ID, digest or immutable origin mismatch')
    if role == 'native':
        repair.require(artifact['digest'] == 'sha256:' + repair.NATIVE_ZIP_SHA256,
                       'native artifact digest is not the audited original')
    return artifact


def successful_delivery_jobs(jobs):
    repair.require(isinstance(jobs, dict) and isinstance(jobs.get('jobs'), list)
                   and jobs.get('total_count') == len(jobs['jobs']) == 4,
                   'original delivery job inventory differs')
    rows = {job.get('name'): job for job in jobs['jobs']}
    repair.require(set(rows) == {'qualification', 'unsigned-device-build', 'application-build', 'package'},
                   'original delivery jobs differ')
    for job in rows.values():
        repair.require(job.get('run_id') == repair.DELIVERY_RUN and job.get('head_sha') == ORIGINAL_TOOL_SHA
                       and job.get('status') == 'completed' and job.get('conclusion') == 'success',
                       'original delivery job did not complete successfully')
    repair.require(rows['unsigned-device-build'].get('id') == 128105899974
                   and rows['application-build'].get('id') == 128105900214, 'original build job identity differs')


def download(artifact, destination):
    with destination.open('xb') as output:
        result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                 'repos/' + REPOSITORY + '/actions/artifacts/' + str(artifact['id']) + '/zip'],
                                stdout=output, stderr=subprocess.PIPE, timeout=600, check=False)
    repair.require(result.returncode == 0 and 0 < destination.stat().st_size <= 1024**3
                   and repair.digest(destination) == artifact['digest'][7:], 'artifact download digest mismatch')


def extract_final(path, destination):
    with zipfile.ZipFile(path) as archive:
        entries = repair.zip_members(archive)
        aliases = set()
        destination.mkdir()
        for name, entry in entries.items():
            key = name.rstrip('/').casefold()
            repair.require(key not in aliases and entry.orig_filename == entry.filename, 'aliased export member')
            aliases.add(key)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            repair.require(kind in ({0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG}),
                           'nonregular original export member')
            if entry.is_dir():
                (destination / name).mkdir(parents=True, exist_ok=True)
                continue
            maximum = 1024**3 if name == repair.IPA_NAME else 64 * 1024**2 if name.endswith('.mp4') else 40 * 1024**2
            repair.require(entry.file_size <= maximum and (not name.lower().endswith('.ipa') or name == repair.IPA_NAME),
                           'unexpected application or oversized original export member')
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    repair.checksums(destination)


def run(request_file, output, delivery_helper, inventory):
    wanted = request(request_file, os.environ.get('GITHUB_EVENT_NAME'), os.environ.get('GITHUB_REF'),
                     os.environ.get('GITHUB_REPOSITORY'))
    repair.require(not output.exists(), 'repair output must be a new export')
    delivery = api('actions/runs/' + str(repair.DELIVERY_RUN))
    native = api('actions/runs/' + str(repair.NATIVE_RUN))
    completed_run(delivery)
    completed_run(native, native=True)
    successful_delivery_jobs(api('actions/runs/' + str(repair.DELIVERY_RUN) + '/jobs?filter=latest&per_page=100'))
    listing = api('actions/runs/' + str(repair.DELIVERY_RUN) + '/artifacts?per_page=100')
    artifacts = {
        'final': select_artifact('final', listing, delivery, wanted['final_artifact_id']),
        'simulator': select_artifact('simulator', listing, delivery, wanted['simulator_artifact_id']),
        'native': select_artifact('native', api('actions/runs/' + str(repair.NATIVE_RUN) + '/artifacts?per_page=100'),
                                  native, repair.NATIVE_ARTIFACT),
    }
    with tempfile.TemporaryDirectory(prefix='fixed-run-export-repair-') as temporary:
        work = Path(temporary)
        for role, artifact in artifacts.items():
            download(artifact, work / (role + '.zip'))
        original = work / 'original-export'
        extract_final(work / 'final.zip', original)
        provenance = document((original / 'delivery-provenance.json').read_bytes())
        manifest = document((original / 'manifest.json').read_bytes())
        repair.require(provenance.get('source_commit') == repair.SOURCE
                       and provenance.get('workflow_commit') == ORIGINAL_TOOL_SHA
                       and provenance.get('workflow_dispatch_commit') == ORIGINAL_TOOL_SHA
                       and provenance.get('workflow') == ORIGINAL_WORKFLOW
                       and provenance.get('delivery_event') == 'push'
                       and provenance.get('delivery_branch') == 'ci/qualified-device-delivery'
                       and manifest.get('source_commit') == repair.SOURCE
                       and manifest.get('source_device_workflow_commit') == ORIGINAL_TOOL_SHA
                       and manifest.get('source_simulator_workflow_commit') == ORIGINAL_TOOL_SHA,
                       'completed export workflow/application origin mismatch')
        for role, job_id in (('unsigned-device', 128105899974), ('simulator', 128105900214)):
            origin = provenance['build_origins'][role]
            repair.require(origin.get('workflow_commit') == ORIGINAL_TOOL_SHA
                           and origin.get('app_source_commit') == repair.SOURCE
                           and origin.get('run_id') == repair.DELIVERY_RUN and origin.get('job_id') == job_id,
                           'completed export build origin mismatch')
        repair.require(provenance['build_origins']['simulator']['artifact_id'] == artifacts['simulator']['id']
                       and provenance['build_origins']['simulator']['artifact_digest'] == artifacts['simulator']['digest'],
                       'simulator provenance is not the selected original intermediate')
        sys.path.insert(0, str(delivery_helper.parent.resolve()))
        from ipa_device_inspect import inspect_unsigned_device
        observed = inspect_unsigned_device(original / repair.IPA_NAME, 'org.livara.ayugram')
        repair.require(observed == manifest.get('main_executable'), 'original static IPA report differs')
        result = repair.repair(original, work / 'native.zip', work / 'simulator.zip', output, delivery_helper, inventory)
        repair.require(inspect_unsigned_device(output / repair.IPA_NAME, 'org.livara.ayugram') == observed,
                       'static IPA report changed during copy-only export repair')
        fresh = api('actions/runs/' + str(repair.DELIVERY_RUN))
        completed_run(fresh)
        repair.require(fresh['run_attempt'] == delivery['run_attempt'], 'original delivery reran during export repair')
    # A separate repair receipt records publication origin without changing the
    # preserved app manifest or delivery provenance.
    receipt = {'schema': 1, 'purpose': 'Packaging-only evidence export repair',
               'original_run': repair.DELIVERY_RUN, 'original_tooling_commit': ORIGINAL_TOOL_SHA,
               'application_source_commit': repair.SOURCE,
               'repair_run': os.environ.get('GITHUB_RUN_ID'), 'repair_workflow_commit': os.environ.get('GITHUB_WORKFLOW_SHA'),
               'input_artifacts': {role: {'id': value['id'], 'digest': value['digest']} for role, value in artifacts.items()},
               'ipa_sha256_unchanged': result['ipa_sha256_after'], 'application_built': False,
               'application_executed': False, 'application_signed': False}
    (output / 'repair-publication.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    files = repair.inventory(output)
    (output / 'SHA256SUMS').write_text(''.join(repair.digest(files[name]) + '  ' + name + '\n'
                                            for name in sorted(files) if name != 'SHA256SUMS'))
    repair.checksums(output)
    print('Fixed-run export evidence repaired; candidate IPA hash and static report unchanged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'output', 'delivery-helper', 'inventory'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.request, args.output, args.delivery_helper, args.inventory)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print('Export repair blocked: completed-run origin, artifact or copy-only evidence validation failed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
