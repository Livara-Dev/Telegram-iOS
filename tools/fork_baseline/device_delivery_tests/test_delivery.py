"""Fail-closed native qualification, origin and artifact validation, on any host."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HELPER = Path(__file__).resolve().parents[1] / 'device_delivery.py'
SPEC = importlib.util.spec_from_file_location('device_delivery', HELPER)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)
INVENTORY = json.loads(HELPER.with_name('device_delivery_inventory.json').read_text())
RUN = 42272821047


def fixture_files():
    summary = {'source_commit': delivery.SOURCE, 'validation_scope': 'archive-core', 'toolchain': delivery.PINS,
               'tests': {label: 'passed' for label in delivery.TARGETS.values()}, 'tests_only': True,
               'full_integration_suite_requested': False, 'full_application_build_requested': False,
               'full_application_built': False, 'physical_device_verified': False}
    files = {'integration-tests.json': json.dumps(summary).encode()}
    for directory, label in delivery.TARGETS.items():
        prefix = 'submodules/LocalMessageArchive/' if directory.endswith('local-message-archive') else 'submodules/TelegramCore/'
        names = [item['case'] for item in INVENTORY['new_cases'] if item['file'].startswith(prefix)]
        names += ['FixtureTests.testFiller' + str(n) for n in range(delivery.NATIVE_COUNTS[directory] - len(names))]
        events = []
        for name in names:
            class_name, method = name.split('.', 1)
            events += ["Test Case '-[module." + class_name + ' ' + method + "]' started.",
                       "Test Case '-[module." + class_name + ' ' + method + "]' passed (0.001 seconds)."]
        events += ["Test Suite 'All tests' passed at 2026-11-06.",
                   'Executed ' + str(len(names)) + ' tests, with 0 failures (0 unexpected) in 1 seconds']
        files[directory + '/test.log'] = ('\n'.join(events) + '\n').encode()
        files[directory + '/test.xml'] = b'<testsuites><testsuite tests="1" errors="0" failures="0"><testcase name="bazel-wrapper" status="run"/></testsuite></testsuites>'
        files[directory + '/collection.json'] = json.dumps({'target': label, 'invocation': 'passed',
             'files': {'test.xml': 'retained_redacted', 'test.log': 'retained_redacted'}}).encode()
    return files


def archive(path, files, additional=()):
    values = dict(files)
    values['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                  for name, data in sorted(files.items())).encode()
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in [*values.items(), *additional]:
            output.writestr(name, data)
    return path


def metadata():
    repo = {'full_name': delivery.REPOSITORY, 'id': 16}
    run = {'id': RUN, 'head_sha': delivery.SOURCE, 'path': delivery.WORKFLOW,
           'repository': repo, 'head_repository': repo, 'event': 'workflow_dispatch',
           'status': 'completed', 'conclusion': 'success', 'run_attempt': 1}
    job = {'id': 71, 'run_id': RUN, 'head_sha': delivery.SOURCE, 'name': 'integration-tests',
           'status': 'completed', 'conclusion': 'success', 'steps': [
               {'name': name, 'status': 'completed', 'conclusion': 'success'} for name in
               ('Run real iOS integration suites', 'Publish integration test evidence before the full app build')]}
    jobs = {'total_count': 2, 'jobs': [job, {'name': 'application-build', 'status': 'completed', 'conclusion': 'skipped'}]}
    artifact = {'id': 25, 'name': 'archive-integration-tests-' + delivery.SOURCE, 'size_in_bytes': 100,
                'digest': 'sha256:' + 'a' * 64, 'expired': False,
                'workflow_run': {'id': RUN, 'head_sha': delivery.SOURCE, 'repository_id': 16, 'head_repository_id': 16}}
    return run, jobs, {'total_count': 1, 'artifacts': [artifact]}


class NativeEvidenceTests(unittest.TestCase):
    def verify(self, files=None, additional=(), inventory=None):
        with tempfile.TemporaryDirectory() as temporary:
            return delivery.verify_evidence(archive(Path(temporary) / 'native.zip', fixture_files() if files is None else files, additional),
                                            delivery.SOURCE, INVENTORY if inventory is None else inventory)

    def test_accepts_native_passes_but_counts_xml_as_one_wrapper(self):
        reports, _ = self.verify()
        self.assertEqual([row['native_passed_cases'] for row in reports.values()], [129, 835])
        self.assertEqual([row['bazel_wrapper_cases'] for row in reports.values()], [1, 1])
        self.assertEqual(sum(len(row['added_cases_verified']) for row in reports.values()), 28)

    def test_rejects_positive_wrapper_when_no_native_tests_ran(self):
        files = fixture_files(); files['prebuild-telegram-core/test.log'] = b'Bazel wrapper passed; no native tests.\n'
        with self.assertRaises(delivery.Blocked): self.verify(files)

    def test_rejects_pass_without_start_start_without_pass_repeated_and_reversed_events(self):
        for kind in ('no_start', 'no_pass', 'duplicate_start', 'duplicate_pass', 'reversed', 'failed', 'skipped'):
            files = fixture_files(); key = 'prebuild-local-message-archive/test.log'
            lines = files[key].decode().splitlines()
            if kind == 'no_start': del lines[0]
            if kind == 'no_pass': del lines[1]
            if kind == 'duplicate_start': lines.insert(1, lines[0])
            if kind == 'duplicate_pass': lines.insert(2, lines[1])
            if kind == 'reversed': lines[0], lines[1] = lines[1], lines[0]
            if kind in ('failed', 'skipped'): lines[1] = lines[1].replace('passed', kind)
            files[key] = '\n'.join(lines).encode()
            with self.subTest(kind=kind), self.assertRaises(delivery.Blocked): self.verify(files)

    def test_rejects_missing_added_identity_even_with_same_native_count(self):
        files = fixture_files(); key = 'prebuild-local-message-archive/test.log'
        files[key] = files[key].replace(INVENTORY['new_cases'][0]['case'].split('.')[1].encode(), b'testDifferentMethod')
        with self.assertRaises(delivery.Blocked): self.verify(files)

    def test_rejects_wrong_source_full_scope_extra_target_and_truncated_suite(self):
        for kind in ('source', 'scope', 'extra', 'zero'):
            files = fixture_files(); summary = json.loads(files['integration-tests.json'])
            if kind == 'source': summary['source_commit'] = 'c' * 40
            if kind == 'scope': summary['validation_scope'] = 'full'
            if kind == 'extra': summary['tests']['//other:Tests'] = 'passed'
            if kind == 'zero': files['prebuild-telegram-core/test.log'] = files['prebuild-telegram-core/test.log'].replace(b'Executed 835', b'Executed 0')
            files['integration-tests.json'] = json.dumps(summary).encode()
            with self.subTest(kind=kind), self.assertRaises(delivery.Blocked): self.verify(files)

    def test_rejects_missing_redacted_evidence(self):
        files = fixture_files(); key = 'prebuild-telegram-core/collection.json'
        value = json.loads(files[key]); value['files']['test.xml'] = 'unavailable_or_unsafe'
        files[key] = json.dumps(value).encode()
        with self.assertRaises(delivery.Blocked): self.verify(files)

    def test_rejects_zip_traversal_and_symlink(self):
        link = zipfile.ZipInfo('host-link'); link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in ((('../escape', b'bad'),), ((link, b'target'),)):
            with self.subTest(extra=extra), self.assertRaises(delivery.Blocked): self.verify(additional=extra)

    def test_rejects_changed_xml_and_bad_hashes(self):
        for xml in (b'<testcase name="wrapper"><skipped/></testcase>', b'<!DOCTYPE x [<!ENTITY y "bad">]><testcase/>'):
            files = fixture_files(); files['prebuild-telegram-core/test.xml'] = xml
            with self.assertRaises(delivery.Blocked): self.verify(files)
        with tempfile.TemporaryDirectory() as temporary:
            path = archive(Path(temporary) / 'native.zip', fixture_files())
            with zipfile.ZipFile(path) as original:
                entries = {item.filename: original.read(item) for item in original.infolist()}
            entries['SHA256SUMS'] = b'0' * 64 + b'  integration-tests.json\n'
            with zipfile.ZipFile(path, 'w') as output:
                for name, data in entries.items(): output.writestr(name, data)
            with self.assertRaises(delivery.Blocked): delivery.verify_evidence(path, delivery.SOURCE, INVENTORY)


class MetadataTests(unittest.TestCase):
    def test_accepts_successful_exact_source_and_skipped_other_jobs(self):
        artifact, job = delivery.validate_metadata(*metadata(), RUN, delivery.SOURCE)
        self.assertEqual((artifact['id'], job), (25, 71))

    def test_rejects_pending_failure_wrong_repository_workflow_and_old_source(self):
        for kind in ('pending', 'failed', 'repo', 'workflow', 'source', 'artifact_source', 'extra_job', 'incomplete'):
            run, jobs, listing = copy.deepcopy(metadata())
            if kind == 'pending': run.update(status='in_progress', conclusion=None)
            if kind == 'failed': jobs['jobs'][0]['conclusion'] = 'failure'
            if kind == 'repo': run['repository']['full_name'] = 'other/project'
            if kind == 'workflow': run['path'] = '.github/workflows/fork-baseline.yml'
            if kind == 'source': run['head_sha'] = 'c' * 40
            if kind == 'artifact_source': listing['artifacts'][0]['workflow_run']['head_sha'] = 'c' * 40
            if kind == 'extra_job': jobs['jobs'][1]['conclusion'] = 'success'
            if kind == 'incomplete': jobs['total_count'] = 300
            with self.subTest(kind=kind), self.assertRaises(delivery.Blocked):
                delivery.validate_metadata(run, jobs, listing, RUN, delivery.SOURCE)

    def test_current_build_origins_use_tooling_sha_and_independent_app_sha(self):
        tooling = 'e' * 40
        run, _, _ = metadata(); run.update(head_sha=tooling, path=delivery.DELIVERY_WORKFLOW, run_attempt=2)
        job = {'id': 19, 'name': 'unsigned-device-build', 'head_sha': tooling, 'run_id': RUN,
               'status': 'completed', 'conclusion': 'success', 'steps': [
                   {'name': delivery.BUILD_MODES['unsigned-device'][1], 'status': 'completed', 'conclusion': 'success'}]}
        artifact = {'id': 30, 'name': 'device-delivery-device-' + delivery.SOURCE, 'expired': False,
                    'size_in_bytes': 200, 'digest': 'sha256:' + 'a' * 64,
                    'workflow_run': {'id': RUN, 'head_sha': tooling, 'repository_id': 16, 'head_repository_id': 16}}
        with patch.dict(os.environ, GITHUB_RUN_ID=str(RUN), GITHUB_RUN_ATTEMPT='2', GITHUB_SHA=tooling):
            selected, _ = delivery.current_artifacts('unsigned-device', run, {'total_count': 1, 'jobs': [job]},
                                                    {'total_count': 1, 'artifacts': [artifact]}, delivery.SOURCE)
            self.assertEqual(selected['id'], 30)
            bad = copy.deepcopy(run); bad['head_sha'] = delivery.SOURCE
            with self.assertRaises(delivery.Blocked):
                delivery.current_artifacts('unsigned-device', bad, {'total_count': 1, 'jobs': [job]},
                                           {'total_count': 1, 'artifacts': [artifact]}, delivery.SOURCE)

    def test_json_duplicate_keys_rejected(self):
        with self.assertRaises(delivery.Blocked): delivery.document('{"source_commit":"a","source_commit":"b"}')


if __name__ == '__main__':
    unittest.main()
