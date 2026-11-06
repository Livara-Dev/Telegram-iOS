# SPDX-License-Identifier: GPL-2.0-or-later
# Private candidate portable fixtures, 2026-11-06. No network or native tools.
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auth_only_driver as d
import ci_gate as c


def request():
    return {'schema': 1, 'role': 'officialPublicTestDCAuthenticationOnly',
        'application_source': d.SOURCE, 'startup_control_run': d.STARTUP_CONTROL_RUN,
        'request_nonce': '12345678-1234-4234-8234-123456789012'}


def metadata():
    runs = []
    for number, head, workflow in ((d.SOURCE_RUN, d.SOURCE, '.github/workflows/fork-baseline.yml'),
            (d.STARTUP_CONTROL_RUN, c.STARTUP_TOOLING, '.github/workflows/archive-integration.yml')):
        runs.append({'id': number, 'head_sha': head, 'status': 'completed', 'path': workflow,
            'conclusion': 'success' if number == d.SOURCE_RUN else 'failure',
            'repository': {'full_name': c.REPOSITORY}, 'head_repository': {'full_name': c.REPOSITORY}})
    jobs = [{'id': number, 'run_id': run, 'status': 'completed', 'conclusion': 'success'}
        for number, run in ((c.SOURCE_JOB, d.SOURCE_RUN), (c.STARTUP_JOB, d.STARTUP_CONTROL_RUN))]
    jobs[0]['steps'] = [{'name': 'Build simulator baseline and run the explicit iOS test target', 'conclusion': 'success'}]
    artifacts = [{'id': number, 'expired': False, 'digest': 'sha256:' + digest,
        'workflow_run': {'id': run, 'head_sha': head}}
        for number, run, head, digest in ((d.ARTIFACT, d.SOURCE_RUN, d.SOURCE, c.SOURCE_ARCHIVE_SHA256),
            (d.STARTUP_ARTIFACT, d.STARTUP_CONTROL_RUN, c.STARTUP_TOOLING, d.STARTUP_ARTIFACT_SHA256))]
    return [runs[0], jobs[0], artifacts[0], runs[1], jobs[1], artifacts[1]]


class RequestTests(unittest.TestCase):
    def test_accepts_exact_push_request(self):
        c.request(request(), 'push', c.BRANCH)

    def test_rejects_wrong_event_and_branch(self):
        for event, ref in [('workflow_dispatch', c.BRANCH), ('push', 'refs/heads/ci/verify-latest'), ('pull_request', c.BRANCH)]:
            with self.subTest(event=event, ref=ref), self.assertRaises(d.Rejected):
                c.request(request(), event, ref)

    def test_rejects_unknown_request_field(self):
        value = request(); value['private_log'] = 'SYNTHETIC_CANARY'
        with self.assertRaises(d.Rejected): c.request(value, 'push', c.BRANCH)

    def test_rejects_wrong_source_or_run(self):
        for key, value in [('application_source', '0' * 40), ('startup_control_run', 1), ('startup_control_run', True), ('schema', True)]:
            row = request(); row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(d.Rejected): c.request(row, 'push', c.BRANCH)

    def test_rejects_placeholder_or_invalid_nonce(self):
        for nonce in ['00000000-0000-4000-8000-000000000000', 'not-a-uuid', '12345678-1234-1234-8234-123456789012']:
            row = request(); row['request_nonce'] = nonce
            with self.subTest(nonce=nonce), self.assertRaises(d.Rejected): c.request(row, 'push', c.BRANCH)


class MetadataTests(unittest.TestCase):
    def test_accepts_real_scope_shape_without_claiming_whole_startup_run_success(self):
        rows = metadata()
        self.assertEqual(rows[3]['conclusion'], 'failure')
        c.metadata(*rows)

    def test_rejects_failed_or_running_actual_startup_job(self):
        for field, value in [('conclusion', 'failure'), ('status', 'in_progress')]:
            rows = metadata(); rows[4][field] = value
            with self.subTest(field=field), self.assertRaises(d.Rejected): c.metadata(*rows)

    def test_rejects_failed_or_unbuilt_source_job(self):
        for section, field, value in [(0, 'conclusion', 'failure'), (1, 'conclusion', 'failure')]:
            rows = metadata(); rows[section][field] = value
            with self.subTest(section=section), self.assertRaises(d.Rejected): c.metadata(*rows)
        rows = metadata(); rows[1]['steps'] = []
        with self.assertRaises(d.Rejected): c.metadata(*rows)

    def test_rejects_wrong_repository_head_workflow_or_job(self):
        for section, key, value in [(0, 'head_sha', '0' * 40), (3, 'head_sha', d.SOURCE),
                (0, 'path', '.github/workflows/other.yml'), (4, 'run_id', d.SOURCE_RUN),
                (3, 'head_repository', {'full_name': 'other/repo'})]:
            rows = metadata(); rows[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(d.Rejected): c.metadata(*rows)

    def test_rejects_expired_modified_or_wrong_origin_artifact(self):
        for section, key, value in [(2, 'expired', True), (5, 'digest', 'sha256:' + '0' * 64),
                (2, 'id', d.STARTUP_ARTIFACT), (5, 'workflow_run', {'id': d.SOURCE_RUN, 'head_sha': d.SOURCE})]:
            rows = metadata(); rows[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(d.Rejected): c.metadata(*rows)


class ResultTests(unittest.TestCase):
    def setUp(self):
        # Exercise auth-mode parser contracts independently of review defaults.
        review = patch.object(d, 'REVIEWED_EXECUTION_SCOPE', 'authOnly')
        review.start()
        self.addCleanup(review.stop)
    def test_accepts_closed_failure_without_copying_raw_data(self):
        result = d.closed_result(); result['failure'] = 'authEvidence'
        self.assertEqual(c.exportable(result), result)

    def test_rejects_unknown_field_and_unclosed_stage_or_failure(self):
        for key, value in [('raw_log', 'SYNTHETIC_PRIVATE'), ('stages', ['arbitraryPrivateValue']), ('failure', 'rawServerError')]:
            row = d.closed_result(); row[key] = value
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(row)

    def test_rejects_changed_source_and_bool_identity(self):
        for key, value in [('source_commit', '0' * 40), ('source_run', True), ('physical_device_verified', 1),
                ('physical_device_verified', True), ('restart_persistence_verified', True), ('application_recompiled', True)]:
            row = d.closed_result(); row[key] = value
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(row)

    def test_rejects_incomplete_or_failed_success(self):
        row = d.closed_result(); row['passed'] = True
        with self.assertRaises(d.Rejected): c.exportable(row)
        for key in ['cleanup_passed', 'authentication_completed', 'native_xctest_passed', 'signature_checks_passed']:
            row = self.success(); row[key] = False
            with self.subTest(key=key), self.assertRaises(d.Rejected): c.exportable(row)

    def success(self):
        row = d.closed_result()
        for key in ['passed', 'setup_completed', 'driver_compiled', 'signature_checks_passed', 'private_selection_seeded',
                'native_xctest_passed', 'authentication_completed', 'cleanup_passed']:
            row[key] = True
        row['stages'] = list(d.SUCCESS_TRACES[1])
        row['phase'] = 'authComplete'
        return row

    def test_accepts_exact_success_but_still_not_device_or_restart(self):
        row = self.success(); c.exportable(row)
        self.assertFalse(row['physical_device_verified'])
        self.assertFalse(row['restart_persistence_verified'])


class DownloadedEvidenceTests(unittest.TestCase):
    def fixture(self, root):
        source, startup = root / 'source', root / 'startup'
        (source / 'fork-baseline-simulator').mkdir(parents=True)
        (source / 'fork-baseline-simulator/Telegram.ipa').write_bytes(b'SYNTHETIC_IPA_NOT_AN_APP')
        digest = d.sha256(source / 'fork-baseline-simulator/Telegram.ipa')
        (source / 'fork-baseline-simulator/manifest.json').write_text(json.dumps({'source_commit': d.SOURCE,
            'sha256': digest, 'mode': 'simulator', 'bundle_id': d.BUNDLE}))
        launch = startup / 'replay-controls/main-app-only/launch-smoke/result.json'; launch.parent.mkdir(parents=True)
        launch.write_text(json.dumps({'passed': True, 'private_selection_seeded_before_launch': True,
            'expected_storage_kind': 'private', 'launches': [
                {'marker': {'failure': None, 'stage': 'authorizationVisible'}},
                {'marker': {'failure': None, 'stage': 'authorizationVisible'}, 'private_account_store_stable_after_restart': True}]}))
        proof = startup / 'replay-controls/replay.json'
        proof.write_text(json.dumps({'source_codesign_verified': True, 'main_code_unchanged': True,
            'private_control_only': True, 'private_mode_control': 'explicitFreshPinnedSelection',
            'source_simulator_ipa_sha256': digest}))
        return source, startup, launch, proof, digest

    def test_exact_synthetic_fixture_then_changed_launch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source, startup, launch, proof, digest = self.fixture(Path(temp))
            with patch.object(d, 'validate_review'), patch.object(d, 'IPA_SHA256', digest), \
                    patch.object(d, 'STARTUP_RESULT_SHA256', d.sha256(launch)), \
                    patch.object(d, 'STARTUP_PROOF_SHA256', d.sha256(proof)):
                # Mocked reviewer gate only; no native/auth functions are called.
                gate = Path(temp) / 'gate.json'; gate.write_text('{}')
                self.assertEqual(c.prepare(source, startup, gate), source / 'fork-baseline-simulator/Telegram.ipa')
                launch.write_text('{}')
                with self.assertRaises(d.Rejected): c.prepare(source, startup, gate)

    def test_gate_is_required_before_reading_downloads(self):
        with self.assertRaises(d.Rejected):
            c.prepare('/nonexistent', '/nonexistent', d.BASE / 'review-gate.DISABLED.json')


class CISourcePinsTests(unittest.TestCase):
    def test_current_ci_wrapper_and_workflow_pins_match(self):
        d.validate_ci_sources(d.json_object(d.BASE / 'driver-review-pins.json'))

    def test_modified_wrapper_or_workflow_pin_is_rejected(self):
        pins = d.json_object(d.BASE / 'driver-review-pins.json')
        for relative in pins['ci_sources']:
            changed = copy.deepcopy(pins); changed['ci_sources'][relative] = '0' * 64
            with self.subTest(relative=relative), self.assertRaises(d.Rejected): d.validate_ci_sources(changed)

    def test_missing_or_extra_ci_path_is_rejected(self):
        for mapping in [{}, {'unreviewed.py': '0' * 64}]:
            with self.subTest(mapping=mapping), self.assertRaises(d.Rejected): d.validate_ci_sources({'ci_sources': mapping})


class WorkflowTests(unittest.TestCase):
    def test_native_deadline_is_bounded_for_framework_bootstrap_without_widening_case_phases(self):
        import ast
        tree = ast.parse((d.BASE / 'auth_only_driver.py').read_text())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'command':
                words = [item.value for arg in node.args if isinstance(arg, ast.List)
                    for item in arg.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)]
                if 'test-without-building' in words:
                    timeout = [item.value.value for item in node.keywords if item.arg == 'timeout' and isinstance(item.value, ast.Constant)]
                    found.append(timeout)
        self.assertEqual(found, [[600]])
        swift = (d.BASE / 'OfficialTestDCAuthenticationSmoke.swift').read_text()
        self.assertIn('seconds: 45', swift)
        self.assertIn('seconds: 60', swift)
        self.assertIn('timeout: 30', swift)
        workflow = (Path(__file__).resolve().parents[3] / '.github/workflows/testdc-auth-smoke.yml').read_text()
        self.assertIn('timeout-minutes: 30', workflow)

    def test_workflow_is_request_scoped_and_uploads_only_closed_json(self):
        path = Path(__file__).resolve().parents[3] / '.github/workflows/testdc-auth-smoke.yml'
        text = path.read_text()
        self.assertIn('runs-on: xcode-27', text)
        self.assertIn('paths: [ci/testdc-auth-request.json]', text)
        self.assertIn('branches: [ci/testdc-auth-smoke]', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertEqual(d.REVIEWED_EXECUTION_SCOPE, 'authOnly')
        self.assertIn('auth_only_driver.py\" --execute \\', text)
        self.assertNotIn('--setup-only', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('bazel', text.lower())
        self.assertNotIn('Make.py', text)
        self.assertIn('unset GH_TOKEN GITHUB_TOKEN CHECKOUT_TOKEN TELEGRAM_API_ID TELEGRAM_API_HASH', text)
        self.assertEqual(text.count('persist-credentials: false'), 2)
        upload = text.split('name: Publish only the bounded auth-only result', 1)[1]
        self.assertIn('path: ${{ runner.temp }}/ayu-testdc-closed/public-result.json', upload)
        self.assertNotIn('*', upload)
        self.assertNotIn('xcresult', upload)


if __name__ == '__main__':
    unittest.main()
