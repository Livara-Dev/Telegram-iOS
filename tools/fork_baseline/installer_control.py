#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for AyuGram iOS on 2026-11-06.
"""One pinned historical installer comparison; never current app qualification.

Preparation alone uses authenticated artifact reads. Execution reuses the
unchanged startup control on its own fresh simulator, without downloading or
compiling. Historical failure remains failure even when the current arm may run.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import replay_artifact as artifact_replay
import startup_replay
from launch_smoke import SmokeError

CURRENT_SOURCE = 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'
HISTORICAL_SOURCE = '62546557807ee5d8ff468ea3d7d46ce1a090c8cf'
HISTORICAL_RUN = 42031063759
HISTORICAL_ARTIFACT = 13426711346
OUTER_SHA256 = '0d5421b8fc15b1338140779eede69f85c24c2bd17d6d4c9674b0805bd343b0e5'
IPA_SHA256 = '8ed00f94e2b72bf4ae239c1ea3060b00445107f677a370d6dd59bb3edc2df4c4'
OUTPUT = 'build/fork-evidence/startup-replay-historical'
ROLE = 'historicalInstallerComparison'
LIMIT = 65536
PROVENANCE_FIELDS = {'application_build_job_id', 'source_job_conclusion', 'source_compile_step_passed',
                     'prior_smoke_passed', 'source_workflow', 'source_build_job', 'source_compile_step'}
EXTRACTION_FIELDS = {'outer_sha256', 'ipa_sha256', 'source_build_number', 'checksum_files_verified',
                     'inner_payloads_verified', 'source_license_materials_present', 'license_materials',
                     'prior_smoke_evidence_present', 'main_executable'}
PROOF_FIELDS = {'schema', 'diagnostic_role', 'repository', 'source_run', 'source_commit', 'artifact_id',
                'current_application_source_commit', 'current_application_source_unchanged',
                'historical_application_source_is_current', 'replay_checkout_commit', 'allowed_tooling_changes',
                'diagnostic_replay_only', 'release_qualified', 'physical_device_verified'} | PROVENANCE_FIELDS | EXTRACTION_FIELDS
RUNTIME_PHASES = {'installApplication', 'startRecording', 'launchApplication', 'getContainer',
                  'waitForInterface', 'captureScreenshot', 'checkScreenshot', 'checkStorageSelection',
                  'seedPrivateStorage', 'checkPrivateAccountStore'}
RUNTIME_CODES = {'commandTimedOut', 'commandFailed', 'commandUnavailable', 'commandOutputLimit',
                 'invalidCommandOutput', 'interfaceNotVisible', 'invalidLaunchMarker',
                 'invalidScreenshotEvidence', 'invalidStorageEvidence', 'recordingUnavailable'}


class ControlError(ValueError):
    pass


def location(output, root):
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    output = Path(output)
    if not output.is_absolute():
        output = root / output
    if output != root / OUTPUT or any(p.is_symlink() for p in (output, *output.parents)):
        raise ControlError('invalid_historical_output_path')
    return root, output


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= LIMIT:
        raise ControlError('invalid_historical_evidence')
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ControlError('invalid_historical_evidence')
            value[key] = item
        return value
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError):
        raise ControlError('invalid_historical_evidence') from None


def write_json(path, value):
    with Path(path).open('x') as stream:
        Path(path).chmod(0o600)
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')


def validate_proof(proof, head, changed):
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS or type(proof.get('schema')) is not int or proof['schema'] != 1 or
            proof.get('diagnostic_role') != ROLE or proof.get('repository') != artifact_replay.REPOSITORY or
            proof.get('source_run') != HISTORICAL_RUN or proof.get('source_commit') != HISTORICAL_SOURCE or
            proof.get('artifact_id') != HISTORICAL_ARTIFACT or proof.get('outer_sha256') != OUTER_SHA256 or
            proof.get('ipa_sha256') != IPA_SHA256 or proof.get('source_build_number') != 54 or
            proof.get('current_application_source_commit') != CURRENT_SOURCE or
            proof.get('current_application_source_unchanged') is not True or
            proof.get('historical_application_source_is_current') is not False or
            proof.get('replay_checkout_commit') != head or proof.get('allowed_tooling_changes') != changed or
            proof.get('diagnostic_replay_only') is not True or proof.get('release_qualified') is not False or
            proof.get('physical_device_verified') is not False or
            proof.get('source_workflow') != artifact_replay.WORKFLOW or
            proof.get('source_build_job') != 'application-build' or
            proof.get('source_compile_step') != artifact_replay.COMPILE_STEP or
            proof.get('source_compile_step_passed') is not True or
            not isinstance(proof.get('source_job_conclusion'), str) or
            proof['source_job_conclusion'] not in {'success', 'failure', 'cancelled', 'timed_out', 'skipped', 'neutral', 'action_required'} or
            type(proof.get('application_build_job_id')) is not int or proof['application_build_job_id'] <= 0 or
            type(proof.get('prior_smoke_passed')) is not bool or
            type(proof.get('prior_smoke_evidence_present')) is not bool or
            any(type(proof.get(key)) is not int or proof[key] <= 0 for key in ('checksum_files_verified', 'inner_payloads_verified')) or
            proof.get('source_license_materials_present') is not False or proof.get('license_materials') is not None or
            proof.get('main_executable') != {'cpu': 'arm64', 'mach_o_platform': 7, 'supported_platform': 'iPhoneSimulator',
                'bundle_id_verified': True, 'signature_verified': False, 'runtime_verified': False}):
        raise ControlError('historical_provenance_mismatch')
    return proof


def prepare(output=OUTPUT, root=None, api=artifact_replay.gh_json, fetch=artifact_replay.download):
    root, output = location(output, root)
    head, changed = artifact_replay.verify_checkout(root, CURRENT_SOURCE)
    if output.exists():
        raise ControlError('historical_output_already_exists')
    artifact, provenance = artifact_replay.select_artifact(HISTORICAL_RUN, HISTORICAL_SOURCE, api)
    if artifact['id'] != HISTORICAL_ARTIFACT or artifact['digest'] != 'sha256:' + OUTER_SHA256:
        raise ControlError('historical_artifact_mismatch')
    with tempfile.TemporaryDirectory(prefix='ayu-historical-download-') as directory:
        archive = Path(directory) / 'historical.zip'
        fetch(HISTORICAL_ARTIFACT, archive)
        extracted = artifact_replay.verify_extract(archive, artifact, HISTORICAL_SOURCE, output)
    proof = {'schema': 1, 'diagnostic_role': ROLE, 'repository': artifact_replay.REPOSITORY,
             'source_run': HISTORICAL_RUN, 'source_commit': HISTORICAL_SOURCE, 'artifact_id': HISTORICAL_ARTIFACT,
             'current_application_source_commit': CURRENT_SOURCE, 'current_application_source_unchanged': True,
             'historical_application_source_is_current': False, 'replay_checkout_commit': head,
             'allowed_tooling_changes': changed, 'diagnostic_replay_only': True,
             'release_qualified': False, 'physical_device_verified': False, **provenance, **extracted}
    validate_proof(proof, head, changed)
    write_json(output / 'verification.json', proof)
    return proof


def checked_control(output, expected_pass):
    control = read_json(output / 'replay-controls/replay.json')
    required_true = ('source_codesign_verified', 'main_code_unchanged', 'main_bundle_resigned_ad_hoc',
                     'other_entitlements_preserved', 'app_group_entitlement_absent_after', 'pinned_private_control_attempted')
    required_false = ('physical_device_verified', 'application_recompiled', 'normal_control_passed',
                      'no_app_groups_control_attempted', 'app_group_entitlement_present_before',
                      'automatic_fallback_verified', 'automatic_group_selection_observed', 'automatic_control_run')
    if (not isinstance(control, dict) or any(control.get(key) is not True for key in required_true) or
            any(control.get(key) is not False for key in required_false) or control.get('passed') is not expected_pass or
            control.get('mode') != 'main_app_only' or control.get('startup_variant') != 'main-app-only' or
            control.get('private_control_only') is not True or control.get('private_mode_control') != 'explicitFreshPinnedSelection' or
            control.get('automatic_storage_kind') != 'notObserved' or control.get('source_simulator_ipa_sha256') != IPA_SHA256 or
            control.get('extensions_removed') != 6 or control.get('framework_files_unchanged') != 77 or
            control.get('retained_resource_files_unchanged') != 855 or
            control.get('phase') != ('complete' if expected_pass else 'explicitPrivateControl') or
            (expected_pass and (control.get('source_ipa_unchanged') is not True or control.get('main_app_only_control_passed') is not True))):
        raise ControlError('historical_control_not_verified')
    result = artifact_replay.prior_smoke_report(read_json(output / 'replay-controls/main-app-only/launch-smoke/result.json'))
    if (result['passed'] is not expected_pass or result.get('storage_test_setup') != 'freshPinnedPrivate' or
            result.get('expected_storage_kind') != 'private' or result.get('cleanup_failures', []) or
            result.get('screenshot_checker') != {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3}):
        raise ControlError('historical_smoke_not_verified')
    return result


def run_control(output=OUTPUT, root=None, execute=startup_replay.replay):
    root, output = location(output, root)
    head, changed = artifact_replay.verify_checkout(root, CURRENT_SOURCE)
    validate_proof(read_json(output / 'verification.json'), head, changed)
    # This stage has no authenticated reads; never inherit the preparation token
    # into codesign, simctl or the synthetic application.
    if any(os.environ.get(key) for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'CHECKOUT_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH')):
        raise ControlError('historical_execution_credentials_present')
    ipa = output / 'Telegram.ipa'
    if ipa.is_symlink() or not ipa.is_file() or startup_replay.digest(ipa) != IPA_SHA256:
        raise ControlError('historical_ipa_mismatch')
    artifact_replay.inspect_ipa(ipa, 'simulator', artifact_replay.BUNDLE)
    manifest = read_json(output / 'archive-evidence/manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('source_commit') != HISTORICAL_SOURCE or
            manifest.get('mode') != 'simulator' or manifest.get('bundle_id') != artifact_replay.BUNDLE or
            manifest.get('sha256') != IPA_SHA256 or manifest.get('build_number') != 54):
        raise ControlError('historical_manifest_mismatch')
    report = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': HISTORICAL_SOURCE,
              'source_run': HISTORICAL_RUN, 'artifact_id': HISTORICAL_ARTIFACT, 'source_build_number': 54,
              'ipa_sha256': IPA_SHA256, 'current_application_source_commit': CURRENT_SOURCE,
              'same_application_source': False, 'application_recompiled': False, 'release_qualified': False,
              'physical_device_verified': False, 'comparison_establishes_causality': False,
              'planned_arm_order': 'historicalThenCurrent', 'planned_same_host': True,
              'planned_fresh_owned_simulator_per_arm': True,
              'historical_passed': False, 'current_arm_may_run': False, 'status': 'notCompleted'}
    try:
        try:
            returned = execute(ipa, output / 'replay-controls', startup_variant='main-app-only', private_control_only=True)
        except SmokeError as error:
            if error.phase not in RUNTIME_PHASES or error.code not in RUNTIME_CODES:
                raise ControlError('unexpected_historical_smoke_failure') from None
            result = checked_control(output, False)
            if result.get('failure') != error.evidence() or result.get('phase') != error.phase:
                raise ControlError('historical_failure_mismatch') from None
            report.update(status='expectedSmokeFailure', failure=error.evidence(), launches=len(result['launches']))
        else:
            if not isinstance(returned, dict) or returned != read_json(output / 'replay-controls/replay.json'):
                raise ControlError('historical_return_mismatch')
            result = checked_control(output, True)
            report.update(status='historicalPassed', historical_passed=True, launches=len(result['launches']))
        if startup_replay.digest(ipa) != IPA_SHA256:
            raise ControlError('historical_ipa_changed')
        report['current_arm_may_run'] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Fail closed after any unexpected error, including post-test checks.
        report.update(status='controlRejected', current_arm_may_run=False, historical_passed=False)
        raise
    finally:
        write_json(output / 'comparison.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--output', type=Path, default=Path(OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = prepare(args.output) if args.command == 'prepare' else run_control(args.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({'diagnostic_role': ROLE, 'verified': False, 'error': 'historical_control_rejected'}))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
