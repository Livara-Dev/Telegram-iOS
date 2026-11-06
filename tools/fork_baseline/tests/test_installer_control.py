# SPDX-License-Identifier: GPL-2.0-or-later
# Created for AyuGram iOS on 2026-11-06.
"""The fixed historical comparison cannot qualify or substitute for current code."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import installer_control as control
import replay_artifact as artifacts
from launch_smoke import SmokeError

HEAD = 'd' * 40


def fixture(root, manifest_source=control.HISTORICAL_SOURCE, bad_inner=False):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': artifacts.BUNDLE, 'CFBundleExecutable': 'Telegram',
            'CFBundleVersion': '54', 'CFBundleSupportedPlatforms': ['iPhoneSimulator']}))
        z.writestr('Payload/Telegram.app/Telegram', struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0) +
                   struct.pack('<6I', 0x32, 24, 7, 0xf0000, 0x1b0100, 0))
    ipa = data.getvalue(); ipa_sha = hashlib.sha256(ipa).hexdigest()
    prefix = 'fork-baseline-simulator/'
    manifest = {'source_commit': manifest_source, 'mode': 'simulator', 'bundle_id': artifacts.BUNDLE,
                'build_number': 54, 'sha256': ipa_sha}
    archive = root / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(prefix + 'Telegram.ipa', ipa)
        z.writestr(prefix + 'manifest.json', json.dumps(manifest))
        z.writestr(prefix + 'SHA256SUMS', ('0' * 64 if bad_inner else ipa_sha) + '  Telegram.ipa\n')
    outer_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    artifact = {'id': control.HISTORICAL_ARTIFACT, 'name': 'archive-integration-' + control.HISTORICAL_SOURCE,
                'expired': False, 'size_in_bytes': archive.stat().st_size, 'digest': 'sha256:' + outer_sha,
                'workflow_run': {'id': control.HISTORICAL_RUN, 'head_sha': control.HISTORICAL_SOURCE,
                                 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': control.HISTORICAL_RUN, 'head_sha': control.HISTORICAL_SOURCE, 'path': artifacts.WORKFLOW,
           'repository': {'id': 7, 'full_name': artifacts.REPOSITORY},
           'head_repository': {'id': 7, 'full_name': artifacts.REPOSITORY}}
    jobs = {'total_count': 1, 'jobs': [{'id': 22, 'run_id': control.HISTORICAL_RUN, 'name': 'application-build',
            'status': 'completed', 'conclusion': 'failure', 'steps': [
                {'name': artifacts.COMPILE_STEP, 'status': 'completed', 'conclusion': 'success'}]}]}
    def api(endpoint):
        if '/jobs?' in endpoint: return jobs
        if '/artifacts?' in endpoint: return {'total_count': 1, 'artifacts': [artifact]}
        return run
    def fetch(identifier, target):
        if identifier != control.HISTORICAL_ARTIFACT: raise AssertionError('wrong historical artifact')
        target.write_bytes(archive.read_bytes())
    return artifact, run, jobs, api, fetch, outer_sha, ipa_sha


def native_result(passed, error=None):
    result = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
              'authentication_codes_used': False, 'launches': [], 'passed': passed,
              'storage_test_setup': 'freshPinnedPrivate', 'expected_storage_kind': 'private',
              'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
              'phase': 'complete' if passed else error.phase, 'failure': None if passed else error.evidence()}
    if passed:
        result.update(private_selection_seeded_before_launch=True, private_account_store_absent_before_launch=True)
        for attempt in (1, 2):
            launch = {'attempt': attempt, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                      'seconds_to_marker': 0.2, 'storage_kind': 'private', 'private_account_store_present': True,
                      'screenshot': {'width': 1206, 'height': 2622, 'nonblank': True}}
            if attempt == 2: launch['private_account_store_stable_after_restart'] = True
            result['launches'].append(launch)
    return result


def write_native(output, passed, error=None, change=None):
    output.mkdir(parents=True)
    report = {'mode': 'main_app_only', 'startup_variant': 'main-app-only', 'private_control_only': True,
              'physical_device_verified': False, 'application_recompiled': False, 'normal_control_passed': False,
              'no_app_groups_control_attempted': False, 'app_group_entitlement_present_before': False,
              'automatic_fallback_verified': False, 'automatic_group_selection_observed': False,
              'automatic_control_run': False, 'automatic_storage_kind': 'notObserved',
              'source_codesign_verified': True, 'main_code_unchanged': True, 'main_bundle_resigned_ad_hoc': True,
              'other_entitlements_preserved': True, 'app_group_entitlement_absent_after': True,
              'pinned_private_control_attempted': True, 'private_mode_control': 'explicitFreshPinnedSelection',
              'source_simulator_ipa_sha256': control.IPA_SHA256, 'extensions_removed': 6,
              'framework_files_unchanged': 77, 'retained_resource_files_unchanged': 855,
              'passed': passed, 'phase': 'complete' if passed else 'explicitPrivateControl'}
    if passed: report.update(source_ipa_unchanged=True, main_app_only_control_passed=True)
    result = native_result(passed, error)
    if change: change(report, result)
    (output / 'replay.json').write_text(json.dumps(report))
    smoke = output / 'main-app-only/launch-smoke'; smoke.mkdir(parents=True)
    (smoke / 'result.json').write_text(json.dumps(result))
    return report


class InstallerControlTests(unittest.TestCase):
    @contextlib.contextmanager
    def prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            # Match the production fixed-path guard on Darwin's /var alias.
            root = Path(directory).resolve()
            values = fixture(root)
            with patch.object(control, 'OUTER_SHA256', values[5]), patch.object(control, 'IPA_SHA256', values[6]), \
                 patch.object(artifacts, 'verify_checkout', return_value=(HEAD, 2)) as guard, \
                 patch.dict(os.environ, {}, clear=True):
                proof = control.prepare(root=root, api=values[3], fetch=values[4])
                yield root, root / control.OUTPUT, proof, guard

    def test_prepare_checks_current_source_and_retains_distinct_historical_identity(self):
        with self.prepared() as (root, output, proof, guard):
            guard.assert_called_once_with(root, control.CURRENT_SOURCE)
            self.assertEqual(proof['source_commit'], control.HISTORICAL_SOURCE)
            self.assertEqual(proof['source_build_number'], 54)
            self.assertFalse(proof['historical_application_source_is_current'])
            self.assertFalse(proof['release_qualified'])
            self.assertFalse(proof['source_license_materials_present'])
            self.assertIsNone(proof['license_materials'])
            self.assertEqual(set(proof), control.PROOF_FIELDS)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual((output / 'verification.json').stat().st_mode & 0o777, 0o600)

    def test_source_guard_failure_precedes_all_authenticated_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            api, fetch = Mock(), Mock()
            with patch.object(artifacts, 'verify_checkout', side_effect=artifacts.ReplayError('application_source_changed')):
                with self.assertRaisesRegex(artifacts.ReplayError, '^application_source_changed$'):
                    control.prepare(root=Path(directory).resolve(), api=api, fetch=fetch)
            api.assert_not_called(); fetch.assert_not_called()

    def test_prepare_rejects_wrong_pinned_artifact_id_or_outer_digest_without_download(self):
        for field, value in [('id', control.HISTORICAL_ARTIFACT + 1), ('digest', 'sha256:' + '0' * 64)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); values = fixture(root); values[0][field] = value
                fetch = Mock()
                with patch.object(artifacts, 'verify_checkout', return_value=(HEAD, 2)), \
                     patch.object(control, 'OUTER_SHA256', values[5]):
                    with self.assertRaises(control.ControlError): control.prepare(root=root, api=values[3], fetch=fetch)
                fetch.assert_not_called()

    def test_existing_selector_rejects_wrong_origin_compile_and_repository(self):
        for kind in ('run', 'source', 'repository', 'artifactrun', 'compile', 'workflow'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); values = fixture(root); artifact, run, jobs = values[:3]
                if kind == 'run': run['id'] += 1
                if kind == 'source': run['head_sha'] = control.CURRENT_SOURCE
                if kind == 'repository': run['head_repository']['id'] += 1
                if kind == 'artifactrun': artifact['workflow_run']['id'] += 1
                if kind == 'compile': jobs['jobs'][0]['steps'][0]['conclusion'] = 'failure'
                if kind == 'workflow': run['path'] = artifacts.BASELINE_WORKFLOW
                fetch = Mock()
                with patch.object(artifacts, 'verify_checkout', return_value=(HEAD, 2)):
                    with self.assertRaises(artifacts.ReplayError): control.prepare(root=root, api=values[3], fetch=fetch)
                fetch.assert_not_called()

    def test_real_extract_verifies_download_inner_checksums_and_historical_manifest(self):
        for kind in ('download', 'inner', 'manifest'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                values = fixture(root, manifest_source=control.CURRENT_SOURCE if kind == 'manifest' else control.HISTORICAL_SOURCE,
                                 bad_inner=kind == 'inner')
                if kind == 'download': (root / 'source.zip').write_bytes(b'corrupted download')
                with patch.object(control, 'OUTER_SHA256', values[5]), patch.object(control, 'IPA_SHA256', values[6]), \
                     patch.object(artifacts, 'verify_checkout', return_value=(HEAD, 2)):
                    with self.assertRaises(artifacts.ReplayError): control.prepare(root=root, api=values[3], fetch=values[4])
                self.assertFalse((root / control.OUTPUT / 'verification.json').exists())

    def test_fixed_output_rejects_other_paths_and_symlinked_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); (root / 'build').symlink_to(root / 'elsewhere')
            for output in ('elsewhere', control.OUTPUT, root / 'other'):
                with self.subTest(output=output), patch.object(artifacts, 'verify_checkout') as guard:
                    with self.assertRaises(control.ControlError): control.prepare(output, root=root)
                    guard.assert_not_called()

    def test_runtime_rechecks_checkout_and_never_fetches_or_compiles(self):
        with self.prepared() as (root, output, _, guard), \
             patch.object(artifacts, 'gh_json', side_effect=AssertionError('network')), \
             patch.object(artifacts, 'download', side_effect=AssertionError('network')):
            def execute(ipa, destination, **kwargs):
                self.assertEqual(ipa, output / 'Telegram.ipa')
                self.assertEqual(kwargs, {'startup_variant': 'main-app-only', 'private_control_only': True})
                return write_native(destination, True)
            result = control.run_control(root=root, execute=execute)
            guard.assert_called_with(root, control.CURRENT_SOURCE)
            self.assertTrue(result['historical_passed']); self.assertTrue(result['current_arm_may_run'])
            self.assertFalse(result['release_qualified']); self.assertFalse(result['same_application_source'])
            self.assertFalse(result['comparison_establishes_causality'])
            self.assertEqual(result['planned_arm_order'], 'historicalThenCurrent')
            self.assertTrue(result['planned_same_host'])
            self.assertTrue(result['planned_fresh_owned_simulator_per_arm'])

    def test_prepared_wrong_role_source_run_hash_or_stale_checkout_never_executes(self):
        changes = [('diagnostic_role', 'currentQualification'), ('source_commit', control.CURRENT_SOURCE),
                   ('source_run', artifacts.BASELINE_SIMULATOR_RUN), ('artifact_id', 1),
                   ('outer_sha256', '0' * 64), ('ipa_sha256', '0' * 64), ('release_qualified', True),
                   ('replay_checkout_commit', 'e' * 40), ('current_application_source_commit', control.HISTORICAL_SOURCE),
                   ('source_job_conclusion', ['PRIVATE-CANARY']), ('schema', True)]
        for key, value in changes:
            with self.subTest(key=key), self.prepared() as (root, output, proof, _):
                proof[key] = value; (output / 'verification.json').write_text(json.dumps(proof))
                execute = Mock()
                with self.assertRaises(control.ControlError): control.run_control(root=root, execute=execute)
                execute.assert_not_called()

    def test_stale_source_guard_after_preparation_never_executes(self):
        with self.prepared() as (root, _, _, guard):
            guard.side_effect = artifacts.ReplayError('application_source_changed'); execute = Mock()
            with self.assertRaises(artifacts.ReplayError): control.run_control(root=root, execute=execute)
            execute.assert_not_called()

    def test_matching_expected_smoke_failure_stays_failed_and_allows_current_arm(self):
        with self.prepared() as (root, output, _, _):
            error = SmokeError('commandTimedOut', 'installApplication', timeout=True)
            def execute(ipa, destination, **kwargs):
                write_native(destination, False, error); raise error
            result = control.run_control(root=root, execute=execute)
            self.assertEqual(result['status'], 'expectedSmokeFailure')
            self.assertFalse(result['historical_passed']); self.assertTrue(result['current_arm_may_run'])
            self.assertEqual(result['failure'], error.evidence()); self.assertEqual(result['launches'], 0)
            self.assertFalse(json.loads((output / 'replay-controls/replay.json').read_text())['passed'])

    def test_mismatched_unstructured_cleanup_or_preflight_failures_cannot_continue(self):
        for kind in ('failure', 'raw-field', 'cleanup', 'signature', 'preflight', 'no-result'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _):
                error = SmokeError('commandTimedOut', 'compileScreenshotChecker' if kind == 'preflight' else 'installApplication', timeout=True)
                def execute(ipa, destination, **kwargs):
                    def change(report, result):
                        if kind == 'failure': result['failure']['timeout'] = False
                        if kind == 'raw-field': result['private-canary'] = 'PRIVATE-CANARY'
                        if kind == 'cleanup': result['cleanup_failures'] = [SmokeError('commandTimedOut', 'deleteDevice', timeout=True).evidence()]
                        if kind == 'signature': report['source_codesign_verified'] = False
                    if kind != 'no-result': write_native(destination, False, error, change)
                    raise error
                with self.assertRaises((control.ControlError, artifacts.ReplayError)): control.run_control(root=root, execute=execute)
                rejected = json.loads((output / 'comparison.json').read_text())
                self.assertFalse(rejected['current_arm_may_run']); self.assertFalse(rejected['historical_passed'])
                self.assertNotIn('PRIVATE-CANARY', (output / 'comparison.json').read_text())

    def test_unexpected_error_never_becomes_a_historical_runtime_failure(self):
        for error in (OSError('PRIVATE-CANARY'), ValueError('PRIVATE-CANARY'), startup_error()):
            with self.subTest(error=type(error).__name__), self.prepared() as (root, output, _, _):
                with self.assertRaises(type(error)): control.run_control(root=root, execute=Mock(side_effect=error))
                result = json.loads((output / 'comparison.json').read_text())
                self.assertEqual(result['status'], 'controlRejected'); self.assertFalse(result['current_arm_may_run'])
                self.assertNotIn('PRIVATE-CANARY', (output / 'comparison.json').read_text())

    def test_post_execution_ipa_mutation_or_return_mismatch_cannot_qualify_control(self):
        for kind in ('ipa', 'return'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _):
                def execute(ipa, destination, **kwargs):
                    report = write_native(destination, True)
                    if kind == 'ipa': ipa.write_bytes(b'changed package')
                    else: report['passed'] = False
                    return report
                with self.assertRaises(control.ControlError): control.run_control(root=root, execute=execute)
                result = json.loads((output / 'comparison.json').read_text())
                self.assertFalse(result['historical_passed']); self.assertFalse(result['current_arm_may_run'])

    def test_credentials_or_wrong_ipa_and_manifest_are_rejected_before_native_execution(self):
        for kind in ('credentials', 'ipa', 'manifest'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _):
                if kind == 'credentials': os.environ['GH_TOKEN'] = 'PRIVATE-CANARY'
                if kind == 'ipa': (output / 'Telegram.ipa').write_bytes(b'current or altered IPA')
                if kind == 'manifest':
                    path = output / 'archive-evidence/manifest.json'; value = json.loads(path.read_text())
                    value['source_commit'] = control.CURRENT_SOURCE; path.write_text(json.dumps(value))
                execute = Mock()
                with self.assertRaises(control.ControlError): control.run_control(root=root, execute=execute)
                execute.assert_not_called()

    def test_duplicate_or_unknown_proof_fields_are_not_accepted(self):
        for kind in ('duplicate', 'unknown'):
            with self.subTest(kind=kind), self.prepared() as (root, output, proof, _):
                path = output / 'verification.json'
                if kind == 'duplicate': path.write_text('{"schema":1,' + path.read_text()[1:])
                else: proof['private-canary'] = 'PRIVATE-CANARY'; path.write_text(json.dumps(proof))
                execute = Mock()
                with self.assertRaises(control.ControlError): control.run_control(root=root, execute=execute)
                execute.assert_not_called()

    def test_cli_unexpected_errors_print_only_fixed_error_fields(self):
        output = io.StringIO()
        with patch.object(control, 'run_control', side_effect=ValueError('PRIVATE-CANARY')), contextlib.redirect_stdout(output):
            self.assertEqual(control.main(['run']), 1)
        self.assertNotIn('PRIVATE-CANARY', output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), {'diagnostic_role': control.ROLE, 'verified': False,
                                                       'error': 'historical_control_rejected'})


def startup_error():
    return control.startup_replay.ReplayError('PRIVATE-CANARY')


if __name__ == '__main__':
    unittest.main()
