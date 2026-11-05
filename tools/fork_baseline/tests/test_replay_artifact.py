"""Artifact replay accepts only the exact original simulator build and tooling changes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import replay_artifact as replay

SOURCE = 'c' * 40
RUN = 42031063759


def ipa_fixture(platform=7):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'Telegram',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator']}))
        archive.writestr('Payload/Telegram.app/Telegram', struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0) +
                         struct.pack('<6I', 0x32, 24, platform, 0x000f0000, 0x001b0100, 0))
    return data.getvalue()


def fixture(path, changes=None, extra=None, platform=7):
    ipa = ipa_fixture(platform)
    smoke = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
             'authentication_codes_used': False, 'launches': [], 'passed': False}
    names = {'fork-baseline-simulator/Telegram.ipa': ipa,
             'fork-baseline-simulator/manifest.json': json.dumps({'source_commit': SOURCE, 'mode': 'simulator',
                  'bundle_id': replay.BUNDLE, 'sha256': hashlib.sha256(ipa).hexdigest()}).encode(),
             'fork-evidence/launch-smoke/result.json': json.dumps(smoke).encode(),
             'fork-evidence/unneeded.log': b'not copied into replay evidence'}
    names['fork-baseline-simulator/SHA256SUMS'] = (hashlib.sha256(ipa).hexdigest() + '  Telegram.ipa\n').encode()
    names['fork-evidence/SHA256SUMS'] = ''.join(hashlib.sha256(names[n]).hexdigest() + '  ' + n.removeprefix('fork-evidence/') + '\n'
        for n in ('fork-evidence/launch-smoke/result.json', 'fork-evidence/unneeded.log')).encode()
    names.update(changes or {})
    with warnings.catch_warnings(), zipfile.ZipFile(path, 'w') as archive:
        warnings.simplefilter('ignore', UserWarning)
        for name, data in names.items(): archive.writestr(name, data)
        if extra: archive.writestr(*extra)
    artifact = {'id': 123, 'name': 'archive-integration-' + SOURCE, 'expired': False,
                'size_in_bytes': path.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(),
                'workflow_run': {'id': RUN, 'head_sha': SOURCE, 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': RUN, 'head_sha': SOURCE, 'path': replay.WORKFLOW,
           'repository': {'id': 7, 'full_name': replay.REPOSITORY}, 'head_repository': {'id': 7, 'full_name': replay.REPOSITORY}}
    jobs = {'total_count': 1, 'jobs': [{'id': 22, 'run_id': RUN, 'name': 'application-build',
            'status': 'completed', 'conclusion': 'failure', 'steps': [
                {'name': replay.COMPILE_STEP, 'status': 'completed', 'conclusion': 'success'},
                {'name': replay.SMOKE_STEP, 'status': 'completed', 'conclusion': 'failure'}]}]}
    return artifact, run, jobs


class API:
    def __init__(self, artifact, run, jobs):
        self.artifact, self.run, self.jobs = copy.deepcopy((artifact, run, jobs))
    def __call__(self, endpoint):
        if '/jobs?' in endpoint: return self.jobs
        if '/artifacts?' in endpoint: return {'total_count': 1, 'artifacts': [self.artifact]}
        return self.run


class ReplayArtifactTests(unittest.TestCase):
    def test_inputs_and_source_change_allowlist_fail_closed(self):
        self.assertEqual(replay.coordinates(str(RUN), SOURCE), (RUN, SOURCE))
        for run, source in [('0', SOURCE), ('-1', SOURCE), ('2/x', SOURCE), ('9' * 20, SOURCE), (str(RUN), 'HEAD')]:
            with self.assertRaises(replay.ReplayError): replay.coordinates(run, source)
        self.assertEqual(replay.validate_paths(['docs/report.md', 'tools/fork_baseline/replay_artifact.py', replay.WORKFLOW]), 3)
        for name in ['submodules/PasscodeCore/BUILD', 'MODULE.bazel', '.gitmodules', '.github/workflows/other.yml',
                     'docs/../Telegram/main.m', '/docs/report.md', 'docs\\report.md']:
            with self.subTest(path=name), self.assertRaises(replay.ReplayError): replay.validate_paths([name])

    def test_completed_compilation_can_be_replayed_after_failed_startup_without_qualification_claim(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, run, jobs = fixture(Path(d) / 'source.zip')
            selected, proof = replay.select_artifact(RUN, SOURCE, API(artifact, run, jobs))
            self.assertEqual(selected['id'], 123)
            self.assertTrue(proof['source_compile_step_passed'])
            self.assertEqual(proof['source_job_conclusion'], 'failure')
            self.assertFalse(proof['prior_smoke_passed'])

    def test_wrong_repository_source_artifact_or_compile_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, run, jobs = fixture(Path(d) / 'source.zip')
            for kind in ['run_sha', 'run_repo', 'artifact_sha', 'artifact_repo', 'artifact_name', 'compile_failure', 'job_running']:
                api = API(artifact, run, jobs)
                if kind == 'run_sha': api.run['head_sha'] = 'd' * 40
                if kind == 'run_repo': api.run['repository']['full_name'] = 'another/repository'
                if kind == 'artifact_sha': api.artifact['workflow_run']['head_sha'] = 'd' * 40
                if kind == 'artifact_repo': api.artifact['workflow_run']['repository_id'] = 99
                if kind == 'artifact_name': api.artifact['name'] = 'device-artifact'
                if kind == 'compile_failure': api.jobs['jobs'][0]['steps'][0]['conclusion'] = 'failure'
                if kind == 'job_running': api.jobs['jobs'][0]['status'] = 'in_progress'
                with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): replay.select_artifact(RUN, SOURCE, api)

    def test_all_checksums_verified_and_only_required_data_is_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root / 'source.zip'
            artifact, _, _ = fixture(archive)
            proof = replay.verify_extract(archive, artifact, SOURCE, root / 'output')
            self.assertEqual(proof['checksum_files_verified'], 2)
            self.assertEqual(proof['inner_payloads_verified'], 3)
            self.assertEqual(proof['main_executable']['mach_o_platform'], 7)
            self.assertEqual((root / 'output/Telegram.ipa').read_bytes(), ipa_fixture())
            self.assertFalse((root / 'output/fork-evidence/unneeded.log').exists())
            self.assertFalse((root / 'output/archive-evidence/unneeded.log').exists())
            prior = json.loads((root / 'output/archive-evidence/prior-launch-result.json').read_text())
            self.assertFalse(prior['passed'])

    def test_outer_and_inner_hash_failures_never_produce_verified_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root / 'source.zip'
            artifact, _, _ = fixture(archive)
            artifact['digest'] = 'sha256:' + '0' * 64
            with self.assertRaisesRegex(replay.ReplayError, 'artifact_digest_mismatch'):
                replay.verify_extract(archive, artifact, SOURCE, root / 'outer')
            self.assertFalse((root / 'outer').exists())
            artifact, _, _ = fixture(archive, {'fork-evidence/unneeded.log': b'corrupted despite valid outer zip digest'})
            with self.assertRaisesRegex(replay.ReplayError, 'inner_checksum_mismatch'):
                replay.verify_extract(archive, artifact, SOURCE, root / 'inner')
            self.assertFalse((root / 'inner').exists())

    def test_traversal_links_and_duplicate_members_are_rejected(self):
        link = zipfile.ZipInfo('fork-evidence/link'); link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in [('../escape', b'x'), ('/absolute', b'x'), (link, b'/etc/passwd'), ('fork-evidence/unneeded.log', b'duplicate')]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d); archive = root / 'source.zip'
                artifact, _, _ = fixture(archive, extra=extra)
                with self.assertRaises(replay.ReplayError): replay.verify_extract(archive, artifact, SOURCE, root / 'output')
                self.assertFalse((root / 'output').exists())

    def test_manifest_identity_and_real_binary_platform_are_both_required(self):
        for mode in ['unsigned-device', 'simulator']:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d); archive = root / 'source.zip'
                if mode == 'unsigned-device':
                    manifest = json.dumps({'source_commit': SOURCE, 'mode': mode, 'bundle_id': replay.BUNDLE, 'sha256': 'a' * 64}).encode()
                    artifact, _, _ = fixture(archive, {'fork-baseline-simulator/manifest.json': manifest})
                else:
                    artifact, _, _ = fixture(archive, platform=2)
                with self.assertRaises(ValueError): replay.verify_extract(archive, artifact, SOURCE, root / 'output')

    def test_prior_smoke_does_not_allow_free_form_diagnostic_content(self):
        value = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
                 'authentication_codes_used': False, 'launches': [], 'passed': False}
        self.assertEqual(replay.prior_smoke_report(value), value)
        value['message'] = 'private log data'
        with self.assertRaises(replay.ReplayError): replay.prior_smoke_report(value)

    def test_new_closed_phase_diagnostics_are_compatible_without_free_form_errors(self):
        value = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
                 'authentication_codes_used': False, 'launches': [], 'passed': False,
                 'phase': 'installApplication',
                 'failure': {'code': 'commandFailed', 'phase': 'installApplication', 'return_code': 1,
                             'timeout': False, 'system_errors': [{'domain': 'MIInstallerErrorDomain', 'code': -1}]},
                 'cleanup_failures': [{'code': 'commandTimedOut', 'phase': 'shutdownDevice', 'return_code': None,
                                       'timeout': True, 'system_errors': []}]}
        self.assertEqual(replay.prior_smoke_report(value), value)
        value['failure']['message'] = 'do not retain arbitrary command output'
        with self.assertRaises(replay.ReplayError): replay.prior_smoke_report(value)
        del value['failure']['message']
        value['failure']['system_errors'][0]['domain'] = 'private-path-or-unknown-domain'
        with self.assertRaises(replay.ReplayError): replay.prior_smoke_report(value)

    def test_partial_attempt_preserves_ready_private_evidence_only_at_closed_failure_phase(self):
        value = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
                 'authentication_codes_used': False, 'passed': False, 'expected_storage_kind': 'private',
                 'recording_return_code': 0, 'phase': 'checkScreenshot',
                 'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
                 'launches': [{'attempt': 1, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                               'storage_kind': 'private', 'seconds_to_marker': 3}],
                 'failure': {'code': 'commandTimedOut', 'phase': 'checkScreenshot', 'return_code': None,
                             'timeout': True, 'system_errors': []}}
        self.assertEqual(replay.prior_smoke_report(value), value)
        for kind in ('passed', 'phase', 'no_failure', 'storage', 'stage', 'null_screenshot', 'private_text'):
            changed = copy.deepcopy(value)
            if kind == 'passed': changed['passed'] = True
            if kind == 'phase': changed['phase'] = changed['failure']['phase'] = 'installApplication'
            if kind == 'no_failure': changed['failure'] = None
            if kind == 'storage': changed['launches'][0]['storage_kind'] = 'unverified'
            if kind == 'stage': changed['launches'][0]['marker']['stage'] = 'applicationEntered'
            if kind == 'null_screenshot': changed['launches'][0]['screenshot'] = None
            if kind == 'private_text': changed['screenshot_checker']['detail'] = '/private/path'
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): replay.prior_smoke_report(changed)

    def test_passing_evidence_requires_two_ready_nonblank_launches_and_expected_private_storage(self):
        value = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
                 'authentication_codes_used': False, 'passed': True, 'expected_storage_kind': 'private',
                 'recording_return_code': 0, 'phase': 'complete', 'failure': None,
                 'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
                 'launches': [{'attempt': n, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                               'storage_kind': 'private', 'seconds_to_marker': 3,
                               'screenshot': {'nonblank': True, 'width': 1206, 'height': 2622}} for n in (1, 2)]}
        self.assertEqual(replay.prior_smoke_report(value), value)
        for kind in ('one_launch', 'missing_screenshot', 'blank', 'stage', 'storage', 'return_code', 'control_cases'):
            changed = copy.deepcopy(value)
            if kind == 'one_launch': changed['launches'].pop()
            if kind == 'missing_screenshot': changed['launches'][1].pop('screenshot')
            if kind == 'blank': changed['launches'][1]['screenshot']['nonblank'] = False
            if kind == 'stage': changed['launches'][1]['marker']['stage'] = 'authorizationPrepared'
            if kind == 'storage': changed['launches'][1]['storage_kind'] = 'unverified'
            if kind == 'return_code': changed['recording_return_code'] = '/private/path'
            if kind == 'control_cases': changed['screenshot_checker']['self_test_cases'] = True
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): replay.prior_smoke_report(changed)

    def test_checkout_verifies_commits_worktree_and_forbids_gitlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def git(*args): return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q'); git('config', 'user.name', 'Replay test'); git('config', 'user.email', 'replay@example.invalid')
            (root / '.gitignore').write_text('build/\n')
            (root / 'app.swift').write_text('unchanged app')
            git('add', '.'); git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'synthetic source')
            source = git('rev-parse', 'HEAD')
            (root / 'docs').mkdir(); (root / 'docs/report.md').write_text('tooling evidence')
            git('add', '.'); git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'synthetic docs')
            head, count = replay.verify_checkout(root, source)
            self.assertEqual(head, git('rev-parse', 'HEAD')); self.assertEqual(count, 1)
            (root / 'app.swift').write_text('changed app')
            with self.assertRaisesRegex(replay.ReplayError, 'application_source_changed'): replay.verify_checkout(root, source)
            (root / 'app.swift').write_text('unchanged app')
            git('update-index', '--add', '--cacheinfo', '160000,' + source + ',docs/foreign')
            with self.assertRaisesRegex(replay.ReplayError, 'submodule_source_changed'): replay.verify_checkout(root, source)

    def test_replay_report_records_source_failure_and_never_marks_release_ready(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root / 'source.zip'
            artifact, run, jobs = fixture(archive)
            def fetch(identifier, destination):
                self.assertEqual(identifier, artifact['id']); destination.write_bytes(archive.read_bytes())
            with patch.object(replay, 'verify_checkout', return_value=('d' * 40, 2)):
                report = replay.replay(RUN, SOURCE, 'build/fork-evidence/startup-replay', root=root, api=API(artifact, run, jobs), fetch=fetch)
            self.assertEqual(report['source_job_conclusion'], 'failure')
            self.assertFalse(report['prior_smoke_passed'])
            self.assertFalse(report['release_qualified'])
            self.assertTrue(report['application_source_unchanged'])
            self.assertFalse(report['physical_device_verified'])
            self.assertTrue((root / 'build/fork-evidence/startup-replay/verification.json').is_file())
