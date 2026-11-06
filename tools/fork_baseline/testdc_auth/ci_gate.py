#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Private candidate, created for review on 2026-11-06; never submitted to CI.
"""Closed request/provenance gate. Does not launch an app or obtain credentials."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid

import auth_only_driver as d

REPOSITORY = 'Livara-Dev/Telegram-iOS'
BRANCH = 'refs/heads/ci/testdc-auth-smoke'
SOURCE_JOB = 127749648753
STARTUP_JOB = 127848100056
STARTUP_TOOLING = '5def97361de4f2e238ea8473b1f6997d6de1081c'
SOURCE_ARCHIVE_SHA256 = 'f6df42768a6c9875a1227015ac6b1080ea5517ea77dff3ac34c54750dd8246ad'


def request(value, event, ref):
    keys = {'schema', 'role', 'application_source', 'startup_control_run', 'request_nonce'}
    d.require(event == 'push' and ref == BRANCH and isinstance(value, dict) and set(value) == keys)
    d.require(type(value['schema']) is int and value['schema'] == 1)
    d.require(value['role'] == 'officialPublicTestDCAuthenticationOnly' and value['application_source'] == d.SOURCE)
    d.require(type(value['startup_control_run']) is int and value['startup_control_run'] == d.STARTUP_CONTROL_RUN)
    nonce = value['request_nonce']
    d.require(type(nonce) is str)
    try:
        parsed = uuid.UUID(nonce)
    except ValueError:
        raise d.Rejected('inputIntegrity') from None
    d.require(str(parsed) == nonce and parsed.version == 4)
    d.require(nonce != '00000000-0000-4000-8000-000000000000')


def api(endpoint):
    # Token is scoped to this metadata-only process. Never print stderr/JSON.
    result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
        'repos/' + REPOSITORY + '/' + endpoint], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    d.require(result.returncode == 0 and len(result.stdout) <= 2 * 1024**2)
    return d.decode_json(result.stdout)


def metadata(source_run, source_job, source_artifact, startup_run, startup_job, startup_artifact):
    for run, number, head, workflow in ((source_run, d.SOURCE_RUN, d.SOURCE, '.github/workflows/fork-baseline.yml'),
            (startup_run, d.STARTUP_CONTROL_RUN, STARTUP_TOOLING, '.github/workflows/archive-integration.yml')):
        d.require(isinstance(run, dict) and run.get('id') == number and run.get('head_sha') == head
            and run.get('status') == 'completed' and run.get('path') == workflow)
        for field in ('repository', 'head_repository'):
            d.require(isinstance(run.get(field), dict) and run[field].get('full_name') == REPOSITORY)
    # The startup run also contained an unrelated failed Projection job. Bind the
    # successful startup job, not an invented all-green workflow conclusion.
    d.require(source_run.get('conclusion') == 'success')
    for job, number, run in ((source_job, SOURCE_JOB, d.SOURCE_RUN), (startup_job, STARTUP_JOB, d.STARTUP_CONTROL_RUN)):
        d.require(isinstance(job, dict) and job.get('id') == number and job.get('run_id') == run
            and job.get('status') == 'completed' and job.get('conclusion') == 'success')
    steps = source_job.get('steps')
    d.require(isinstance(steps, list) and sum(isinstance(step, dict)
        and step.get('name') == 'Build simulator baseline and run the explicit iOS test target'
        and step.get('conclusion') == 'success' for step in steps) == 1)
    for artifact, number, run, head, digest in (
            (source_artifact, d.ARTIFACT, d.SOURCE_RUN, d.SOURCE, SOURCE_ARCHIVE_SHA256),
            (startup_artifact, d.STARTUP_ARTIFACT, d.STARTUP_CONTROL_RUN, STARTUP_TOOLING, d.STARTUP_ARTIFACT_SHA256)):
        d.require(isinstance(artifact, dict) and artifact.get('id') == number and artifact.get('expired') is False
            and artifact.get('digest') == 'sha256:' + digest)
        origin = artifact.get('workflow_run')
        d.require(isinstance(origin, dict) and origin.get('id') == run and origin.get('head_sha') == head)


def prepare(source_directory, startup_directory, review_gate):
    source_directory, startup_directory = Path(source_directory), Path(startup_directory)
    d.validate_review(d.json_object(review_gate))
    ipa = source_directory / 'fork-baseline-simulator/Telegram.ipa'
    d.require(d.sha256(ipa) == d.IPA_SHA256)
    manifest = d.json_object(source_directory / 'fork-baseline-simulator/manifest.json')
    d.require(manifest.get('source_commit') == d.SOURCE and manifest.get('sha256') == d.IPA_SHA256
        and manifest.get('mode') == 'simulator' and manifest.get('bundle_id') == d.BUNDLE)
    launch = startup_directory / 'replay-controls/main-app-only/launch-smoke/result.json'
    proof = startup_directory / 'replay-controls/replay.json'
    d.require(d.sha256(launch) == d.STARTUP_RESULT_SHA256 and d.sha256(proof) == d.STARTUP_PROOF_SHA256)
    result, replay = d.json_object(launch), d.json_object(proof)
    d.require(result.get('passed') is True and result.get('private_selection_seeded_before_launch') is True
        and result.get('expected_storage_kind') == 'private' and result.get('cleanup_failures', []) == [])
    launches = result.get('launches')
    d.require(isinstance(launches, list) and len(launches) == 2)
    d.require(all(row.get('marker') == {'failure': None, 'stage': 'authorizationVisible'} for row in launches)
        and launches[1].get('private_account_store_stable_after_restart') is True)
    d.require(replay.get('source_codesign_verified') is True and replay.get('main_code_unchanged') is True
        and replay.get('private_control_only') is True and replay.get('private_mode_control') == 'explicitFreshPinnedSelection'
        and replay.get('source_simulator_ipa_sha256') == d.IPA_SHA256)
    return ipa


def exportable(value):
    expected = d.closed_result()
    d.require(isinstance(value, dict) and set(value) == set(expected))
    for key, template in expected.items():
        if type(template) is bool:
            d.require(type(value[key]) is bool)
            if key in {'application_recompiled', 'physical_device_verified', 'restart_persistence_verified'}:
                d.require(value[key] is False)
        elif key == 'failure':
            d.require(value[key] is None or value[key] in d.ERRORS)
        elif key == 'stages':
            d.require(isinstance(value[key], list) and len(value[key]) <= 5
                and all(type(item) is str and item in d.STAGES for item in value[key]))
        else:
            d.require(type(value[key]) is type(template) and value[key] == template)
    if value['passed']:
        d.require(value['failure'] is None and tuple(value['stages']) in d.SUCCESS_TRACES
            and all(value[field] for field in ('driver_compiled', 'signature_checks_passed',
                'private_selection_seeded', 'native_xctest_passed', 'authentication_completed', 'cleanup_passed')))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('admit', 'prepare', 'export'))
    parser.add_argument('--request', type=Path)
    parser.add_argument('--review-gate', type=Path)
    parser.add_argument('--source-artifact', type=Path)
    parser.add_argument('--startup-artifact', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'admit':
            request(d.json_object(args.request), os.environ.get('GITHUB_EVENT_NAME'), os.environ.get('GITHUB_REF'))
            d.validate_review(d.json_object(args.review_gate))
            metadata(api('actions/runs/' + str(d.SOURCE_RUN)), api('actions/jobs/' + str(SOURCE_JOB)),
                api('actions/artifacts/' + str(d.ARTIFACT)), api('actions/runs/' + str(d.STARTUP_CONTROL_RUN)),
                api('actions/jobs/' + str(STARTUP_JOB)), api('actions/artifacts/' + str(d.STARTUP_ARTIFACT)))
        elif args.mode == 'prepare':
            prepare(args.source_artifact, args.startup_artifact, args.review_gate)
        else:
            value = exportable(d.json_object(args.result))
            d.private_write(args.output, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode())
        print('{"state":"accepted"}')
        return 0
    except Exception:
        print('{"state":"rejected","failure":"closedAdmission"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
