# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline guards for a separately labeled, unqualified retained-device export.

Synthetic metadata and tiny archives test rejection behavior only. The optional
cached-evidence test reads an already downloaded failure artifact; it never
downloads an IPA, contacts CI, launches an app, or establishes qualification.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('owned_unqualified_export_tests', ROOT / 'export_unqualified.py')
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def write_tree(root, files):
    root.mkdir(parents=True)
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (root / 'SHA256SUMS').write_bytes(''.join(sha(value) + '  ' + name + '\n'
            for name, value in sorted(files.items())).encode())


def valid_request():
    return {'schema': 1, 'source_commit': exporter.SOURCE, 'original_run': exporter.RUN,
            'failed_startup_run': exporter.FAILED_RUN, 'nonce': 'owned-offline-fixture-v1'}


def environment():
    return {'GITHUB_REPOSITORY': 'Livara-Dev/Telegram-iOS', 'GITHUB_EVENT_NAME': 'push',
            'GITHUB_REF': 'refs/heads/ci/unqualified-device-export', 'GITHUB_RUN_ATTEMPT': '1'}


def failed_run():
    repo = {'full_name': 'Livara-Dev/Telegram-iOS', 'id': 1445292587}
    return {'id': exporter.FAILED_RUN, 'head_sha': exporter.FROZEN,
            'path': '.github/workflows/resume-qualified-device-delivery.yml',
            'head_branch': 'ci/qualified-device-startup-resume', 'event': 'push',
            'status': 'completed', 'conclusion': 'failure', 'run_attempt': 1,
            'repository': repo.copy(), 'head_repository': repo.copy()}


def failed_jobs():
    contract = json.loads((ROOT / 'failed-startup-step-contract.json').read_text())
    jobs = []
    for name, identity, conclusion in (('retained-startup', 128196956832, 'failure'),
                                       ('package', 128200001807, 'skipped')):
        jobs.append({'id': identity, 'name': name, 'run_id': exporter.FAILED_RUN,
                     'head_sha': exporter.FROZEN, 'head_branch': 'ci/qualified-device-startup-resume',
                     'run_attempt': 1, 'status': 'completed', 'conclusion': conclusion,
                     'steps': [{'number': number, 'name': step, 'conclusion': outcome, 'status': 'completed'}
                               for number, step, outcome in contract[name]]})
    return {'total_count': len(jobs), 'jobs': jobs}


def failed_artifacts():
    return {'total_count': 1, 'artifacts': [{
        'id': exporter.FAILED_ARTIFACT, 'name': 'startup-resume-5d241267-42279866706',
        'expired': False, 'size_in_bytes': 5652, 'digest': 'sha256:' + exporter.FAILED_SHA,
        'workflow_run': {'id': exporter.FAILED_RUN, 'head_sha': exporter.FROZEN,
                         'repository_id': 1445292587, 'head_repository_id': 1445292587}}]}


def device_fixture():
    manifest = {'sha256': 'c' * 64, 'license_materials': {'COPYING': {'sha256': 'd' * 64, 'size_bytes': 4}}}
    manifest_sha = 'b' * 64
    proof = {'schema': 1, 'repository': 'Livara-Dev/Telegram-iOS', 'source_commit': exporter.SOURCE,
             'source_run': exporter.RUN, 'source_run_attempt': 1,
             'workflow_commit': '2f68bca8027980250bf8f9b0328bb1e063fc55c1',
             'workflow_dispatch_commit': '2f68bca8027980250bf8f9b0328bb1e063fc55c1',
             'source_workflow': '.github/workflows/unsigned-device-delivery.yml',
             'source_build_job': 'unsigned-device-build', 'source_compile_step': 'Compile unsigned device package',
             'source_build_number': 1001, 'mode': 'unsigned-device',
             'source_manifest_sha256': manifest_sha, 'source_ipa_sha256': manifest['sha256'],
             'license_materials': manifest['license_materials'], 'application_source_unchanged': True,
             'source_license_materials_present': True, 'physical_device_verified': False, 'release_qualified': False,
             'qualification_sha256': 'e' * 64}
    return proof, manifest, manifest_sha


class FrozenHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen_root = Path(os.environ.get('UNQUALIFIED_EXPORT_FROZEN_ROOT', 'frozen')).resolve()
        # Private synthetic tests can bypass only the checkout HEAD assertion.
        # The actual export CLI always uses the production True default.
        verify_head = os.environ.get('UNQUALIFIED_EXPORT_TEST_SKIP_FROZEN_HEAD') != '1'
        cls.modules = exporter.load_modules(cls.frozen_root, verify_head=verify_head)


class RequestTests(unittest.TestCase):
    def test_fixed_request_accepts_only_a_bounded_non_authority_nonce(self):
        for nonce in ('a', '0', 'a' * 80, 'owned-offline-fixture-v2'):
            value = {**valid_request(), 'nonce': nonce}
            self.assertEqual(exporter.request(encode(value), environment()), value)

    def test_request_requires_exact_push_repository_branch_and_first_attempt(self):
        for key, wrong in (('GITHUB_REPOSITORY', 'someone-else/Telegram-iOS'),
                           ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                           ('GITHUB_REF', 'refs/heads/main'),
                           ('GITHUB_REF', 'refs/pull/1/merge'),
                           ('GITHUB_RUN_ATTEMPT', '2')):
            with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError):
                exporter.request(encode(valid_request()), {**environment(), key: wrong})

    def test_request_rejects_unknown_missing_typed_and_retargeted_fields(self):
        valid = valid_request()
        changes = ({'schema': True}, {'schema': '1'}, {'source_commit': 'a' * 40},
                   {'original_run': str(exporter.RUN)}, {'original_run': exporter.RUN + 1},
                   {'failed_startup_run': True}, {'failed_startup_run': exporter.FAILED_RUN + 1},
                   {'extra': 'SYNTHETIC_PRIVATE_CANARY'}, {'nonce': ''}, {'nonce': 'A'},
                   {'nonce': 'a' * 81}, {'nonce': '../file'}, {'nonce': 'a\n'}, {'nonce': 1})
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                exporter.request(encode({**valid, **changed}), environment())
        for key in valid:
            value = valid.copy(); del value[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                exporter.request(encode(value), environment())

    def test_request_rejects_duplicate_json_keys_nonfinite_values_and_oversize(self):
        raw = encode(valid_request()).rstrip()[:-1] + b', "schema": 1}'
        for value in (raw, b'{"nonce":NaN}', b'[]', b'{}' + b' ' * 2048, b'\xff'):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                exporter.request(value, environment())


class FailedGraphTests(unittest.TestCase):
    def test_exact_completed_failure_contract_is_accepted_without_promotion(self):
        receipt = exporter.failed_graph(failed_run(), failed_jobs())
        self.assertIsInstance(receipt, dict)
        self.assertNotIn(True, [receipt.get('startup_verified'), receipt.get('runtime_verified'),
                               receipt.get('release_qualified')])

    def test_run_must_be_exact_completed_original_repository_and_frozen_tooling(self):
        for key, wrong in (('id', exporter.FAILED_RUN + 1), ('head_sha', 'a' * 40),
                           ('path', '.github/workflows/other.yml'), ('event', 'workflow_dispatch'),
                           ('head_branch', 'main'), ('status', 'in_progress'),
                           ('conclusion', 'success'), ('run_attempt', 2), ('run_attempt', True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                exporter.failed_graph({**failed_run(), key: wrong}, failed_jobs())
        for key in ('repository', 'head_repository'):
            for wrong in ({'id': 1445292587, 'full_name': 'unrelated/repo'},
                          {'id': True, 'full_name': 'Livara-Dev/Telegram-iOS'}):
                with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError):
                    exporter.failed_graph({**failed_run(), key: wrong}, failed_jobs())

    def test_exact_two_job_identity_origin_and_conclusions_are_required(self):
        changes = (('id', 1), ('run_id', exporter.FAILED_RUN + 1), ('head_sha', 'a' * 40),
                   ('head_branch', 'main'), ('run_attempt', 2), ('run_attempt', True),
                   ('status', 'queued'), ('name', 'unrecognized'), ('conclusion', 'success'))
        for key, wrong in changes:
            value = failed_jobs(); value['jobs'][0][key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                exporter.failed_graph(failed_run(), value)
        for conclusion in ('success', 'failure'):
            value = failed_jobs(); value['jobs'][1]['conclusion'] = conclusion
            with self.subTest(package=conclusion), self.assertRaises(ValueError):
                exporter.failed_graph(failed_run(), value)
        for value in ({'total_count': 1, 'jobs': failed_jobs()['jobs']},
                      {'total_count': 3, 'jobs': failed_jobs()['jobs'] + [copy.deepcopy(failed_jobs()['jobs'][0])]},
                      {'total_count': 1, 'jobs': failed_jobs()['jobs'][:1]}):
            with self.subTest(inventory=value['total_count']), self.assertRaises(ValueError):
                exporter.failed_graph(failed_run(), value)

    def test_step_names_numbers_statuses_and_outcomes_are_not_inferred_from_job_failure(self):
        for key, wrong in (('name', 'different failure'), ('number', 999),
                           ('status', 'in_progress'), ('conclusion', 'success')):
            value = failed_jobs(); value['jobs'][0]['steps'][7][key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                exporter.failed_graph(failed_run(), value)
        value = failed_jobs(); value['jobs'][0]['steps'].pop()
        with self.assertRaises(ValueError): exporter.failed_graph(failed_run(), value)

    def test_all_graph_numeric_coordinates_reject_equal_float_and_boolean_lookalikes(self):
        run_paths = (('id',), ('run_attempt',), ('repository', 'id'), ('head_repository', 'id'))
        listing_paths = (('total_count',), ('jobs', 0, 'id'), ('jobs', 0, 'run_id'),
                         ('jobs', 0, 'run_attempt'), ('jobs', 0, 'steps', 0, 'number'),
                         ('jobs', 1, 'id'), ('jobs', 1, 'run_id'), ('jobs', 1, 'run_attempt'))
        for kind, paths in (('run', run_paths), ('listing', listing_paths)):
            for path in paths:
                for replacement in (float, lambda _: True):
                    run, listing = failed_run(), failed_jobs()
                    owner = run if kind == 'run' else listing
                    for part in path[:-1]: owner = owner[part]
                    owner[path[-1]] = replacement(owner[path[-1]])
                    with self.subTest(kind=kind, path=path, value=owner[path[-1]]), self.assertRaises(ValueError):
                        exporter.failed_graph(run, listing)
        value = failed_jobs(); value['jobs'][1]['steps'] = [{'number': 1, 'name': 'unexpected',
                                                          'status': 'completed', 'conclusion': 'success'}]
        with self.assertRaises(ValueError): exporter.failed_graph(failed_run(), value)


class FailedArtifactTests(unittest.TestCase):
    def test_failure_artifact_requires_fixed_id_digest_run_source_and_repository(self):
        valid = failed_artifacts()
        self.assertEqual(exporter.failed_artifact(valid), valid['artifacts'][0])
        for key, wrong in (('id', exporter.FAILED_ARTIFACT + 1), ('id', True),
                           ('name', 'other'), ('expired', True), ('expired', 0),
                           ('size_in_bytes', 0), ('size_in_bytes', True), ('size_in_bytes', 1024**3),
                           ('digest', 'sha256:' + 'a' * 64)):
            value = copy.deepcopy(valid); value['artifacts'][0][key] = wrong
            with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError):
                exporter.failed_artifact(value)
        for key, wrong in (('id', exporter.FAILED_RUN + 1), ('head_sha', 'a' * 40),
                           ('repository_id', 1), ('head_repository_id', 1)):
            value = copy.deepcopy(valid); value['artifacts'][0]['workflow_run'][key] = wrong
            with self.subTest(origin=key), self.assertRaises(ValueError):
                exporter.failed_artifact(value)

    def test_failure_artifact_rejects_missing_duplicate_and_partial_inventory(self):
        valid = failed_artifacts()
        for value in ({'total_count': 0, 'artifacts': []},
                      {'total_count': 2, 'artifacts': valid['artifacts'] * 2},
                      {'total_count': 2, 'artifacts': valid['artifacts']}):
            with self.subTest(count=value['total_count']), self.assertRaises(ValueError):
                exporter.failed_artifact(value)

    def test_artifact_numeric_coordinates_reject_equal_floats_and_booleans(self):
        paths = (('total_count',), ('artifacts', 0, 'id'), ('artifacts', 0, 'size_in_bytes'),
                 ('artifacts', 0, 'workflow_run', 'id'), ('artifacts', 0, 'workflow_run', 'repository_id'),
                 ('artifacts', 0, 'workflow_run', 'head_repository_id'))
        for path in paths:
            for replacement in (float, lambda _: True):
                value = failed_artifacts(); owner = value
                for part in path[:-1]: owner = owner[part]
                owner[path[-1]] = replacement(owner[path[-1]])
                with self.subTest(path=path, value=owner[path[-1]]), self.assertRaises(ValueError):
                    exporter.failed_artifact(value)


class DeviceProofTests(unittest.TestCase):
    def test_device_proof_binds_separate_build_origins_manifest_ipa_licenses_and_build_number(self):
        proof, manifest, manifest_sha = device_fixture()
        self.assertEqual(exporter.device_proof(proof, manifest, manifest_sha), proof)
        for key, wrong in (('schema', True), ('repository', 'other/repo'), ('source_commit', 'a' * 40),
                           ('source_run', exporter.RUN + 1), ('source_run_attempt', True),
                           ('workflow_commit', exporter.SOURCE), ('workflow_dispatch_commit', exporter.FROZEN),
                           ('source_workflow', '.github/workflows/other.yml'), ('source_build_job', 'application-build'),
                           ('source_compile_step', 'unrelated'), ('source_build_number', '1001'),
                           ('source_build_number', 1002), ('mode', 'simulator'),
                           ('source_manifest_sha256', 'a' * 64), ('source_ipa_sha256', 'a' * 64),
                           ('license_materials', {}), ('application_source_unchanged', False),
                           ('source_license_materials_present', False), ('physical_device_verified', True),
                           ('release_qualified', True)):
            with self.subTest(key=key, wrong=wrong), self.assertRaises(ValueError):
                exporter.device_proof({**proof, key: wrong}, manifest, manifest_sha)

    def test_device_proof_rejects_missing_fields_and_invalid_native_receipt_hash(self):
        proof, manifest, manifest_sha = device_fixture()
        for key in proof:
            changed = proof.copy(); del changed[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                exporter.device_proof(changed, manifest, manifest_sha)
        for wrong in ('', 'e' * 63, 'E' * 64, '../path', None, 42):
            with self.subTest(qualification_hash=wrong), self.assertRaises(ValueError):
                exporter.device_proof({**proof, 'qualification_sha256': wrong}, manifest, manifest_sha)
        for changed, changed_sha in (({**manifest, 'sha256': 'a' * 64}, manifest_sha),
                                     ({**manifest, 'license_materials': {}}, manifest_sha),
                                     (manifest, 'a' * 64)):
            with self.subTest(manifest=changed, hash=changed_sha), self.assertRaises(ValueError):
                exporter.device_proof(proof, changed, changed_sha)


class ArchiveAndTreeTests(FrozenHelpers):
    def test_archive_outer_digest_must_match_before_any_member_is_exposed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); path = write_zip(root / 'fixture.zip', {'file': b'owned'})
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                with self.modules['static'].verified_archive(path, '0' * 64):
                    self.fail('digest mismatch exposed the archive')
            with self.modules['static'].verified_archive(path, sha(original)) as archive:
                self.assertEqual(archive.read('file'), b'owned')
            self.assertEqual(path.read_bytes(), original)

    def test_archive_rejects_traversal_duplicates_symlinks_and_special_unix_types(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for label, members in (('traversal', [('../outside', b'x')]),
                                   ('absolute', [('/outside', b'x')]),
                                   ('duplicate', [('same', b'x'), ('same', b'y')]),
                                   ('symlink', [(stat.S_IFLNK | 0o777, b'target')]),
                                   ('fifo', [(stat.S_IFIFO | 0o600, b'')])):
                path = root / (label + '.zip')
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(path, 'w') as archive:
                        for name, data in members:
                            if isinstance(name, int):
                                info = zipfile.ZipInfo('special'); info.create_system = 3; info.external_attr = name << 16
                                archive.writestr(info, data)
                            else: archive.writestr(name, data)
                with self.subTest(label=label), self.assertRaises(ValueError):
                    with self.modules['static'].verified_archive(path, sha(path.read_bytes())):
                        self.fail('unsafe member exposed the archive')
            self.assertFalse((root.parent / 'outside').exists())

    def test_checksum_tree_requires_complete_exact_nested_regular_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); root = base / 'evidence'
            write_tree(root, {'nested/result.json': b'owned result', 'source.json': b'owned source'})
            before = tree_bytes(root)
            self.modules['static'].checksum_tree(root)
            self.assertEqual(tree_bytes(root), before)
            (root / 'orphan').write_bytes(b'orphan')
            with self.assertRaises(ValueError): self.modules['static'].checksum_tree(root)
            (root / 'orphan').unlink()
            (root / 'source.json').write_bytes(b'corrupt')
            with self.assertRaises(ValueError): self.modules['static'].checksum_tree(root)
            (root / 'source.json').write_bytes(before['source.json'])
            (root / 'nested/result.json').unlink()
            with self.assertRaises(ValueError): self.modules['static'].checksum_tree(root)

    def test_checksum_tree_rejects_symlink_and_traversing_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); root = base / 'evidence'
            write_tree(root, {'data': b'owned'}); (base / 'target').write_bytes(b'owned')
            (root / 'data').unlink(); (root / 'data').symlink_to(base / 'target')
            with self.assertRaises(ValueError): self.modules['static'].checksum_tree(root)
            (root / 'data').unlink(); (root / 'data').write_bytes(b'owned')
            (root / 'SHA256SUMS').write_text(sha(b'owned') + '  ../target\n')
            with self.assertRaises(ValueError): self.modules['static'].checksum_tree(root)


class CachedFailedEvidenceTests(FrozenHelpers):
    def test_exact_cached_failed_startup_is_accepted_and_preserved_without_qualification(self):
        selected = os.environ.get('UNQUALIFIED_EXPORT_CACHED_STARTUP')
        if not selected:
            self.skipTest('optional already-downloaded failed-startup artifact was not supplied')
        path = Path(selected).resolve(strict=True)
        before = path.read_bytes()
        self.assertEqual(sha(before), exporter.FAILED_SHA)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary).resolve() / 'failure'
            summary = exporter.failed_evidence(path, destination, self.modules)
            self.assertIsInstance(summary, dict)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(tree_bytes(destination), {name: archive.read(name) for name in archive.namelist()})
            self.modules['static'].checksum_tree(destination)
            self.assertNotIn(True, [summary.get('startup_verified'), summary.get('runtime_verified'),
                                   summary.get('physical_device_verified'), summary.get('release_qualified')])
        self.assertEqual(path.read_bytes(), before)


class SyntheticExportFlowTests(FrozenHelpers):
    def test_small_export_retains_bytes_measures_counts_and_never_promotes_failed_startup(self):
        # This is deliberately not an application. Static binary inspection,
        # artifact identity, GPL providers, and failure provider are mocked;
        # checksums, baseline metadata, branding, and six-extension stripping
        # use the actual frozen routines. It establishes export plumbing only.
        origins, static, helpers = self.modules['origins'], self.modules['static'], self.modules['helpers']
        contract = json.loads(Path(origins.__file__).with_name('original-step-contract.json').read_text())
        repo = {'full_name': origins.REPOSITORY, 'id': origins.REPOSITORY_ID}
        original_run = {'id': origins.RUN, 'head_sha': origins.TOOLING, 'path': origins.WORKFLOW,
                        'head_branch': origins.BRANCH, 'event': 'push', 'status': 'completed',
                        'conclusion': 'failure', 'run_attempt': 1, 'repository': repo, 'head_repository': repo}
        original_jobs = {'total_count': 4, 'jobs': []}
        for name, identity, outcome in (('qualification', origins.QUALIFICATION_JOB, 'success'),
                ('unsigned-device-build', origins.DEVICE_JOB, 'success'),
                ('application-build', origins.SIMULATOR_JOB, 'failure'), ('package', origins.PACKAGE_JOB, 'skipped')):
            original_jobs['jobs'].append({'id': identity, 'name': name, 'run_id': origins.RUN,
                'head_sha': origins.TOOLING, 'head_branch': origins.BRANCH, 'run_attempt': 1,
                'status': 'completed', 'conclusion': outcome,
                'steps': [{'number': number, 'name': step, 'status': 'completed', 'conclusion': conclusion}
                          for number, step, conclusion in contract[name]]})
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            data = io.BytesIO()
            retained = {'Payload/Telegram.app/Info.plist': plistlib.dumps({
                'CFBundleIdentifier': 'org.livara.ayugram', 'AyuGramBaseBundleIdentifier': 'org.livara.ayugram',
                'CFBundleDisplayName': 'AyuGram', 'CFBundleExecutable': 'Telegram', 'CFBundleVersion': '1001'}),
                'Payload/Telegram.app/Telegram': b'SYNTHETIC NOT MACH-O: owned main executable',
                'Payload/Telegram.app/Frameworks/Owned.framework/Owned': b'SYNTHETIC owned framework'}
            with zipfile.ZipFile(data, 'w') as archive:
                for name, value in retained.items(): archive.writestr(name, value)
                for number in range(6):
                    prefix = 'Payload/Telegram.app/PlugIns/Owned' + str(number) + '.appex/'
                    archive.writestr(prefix + 'Info.plist', plistlib.dumps({'fixture_only': True}))
                    archive.writestr(prefix + 'Owned', b'SYNTHETIC extension')
            ipa_bytes = data.getvalue()
            licensing = helpers['artifact_licensing']
            materials = {name: ('SYNTHETIC GPL PROVIDER FIXTURE ONLY: ' + name).encode()
                         for name in ('COPYING', 'LICENSE-NOTICE.md', 'CORRESPONDING-SOURCE.md')}
            licenses = {name: {'sha256': sha(value), 'size_bytes': len(value)}
                        for name, value in materials.items()}
            manifest = {'source_commit': exporter.SOURCE, 'mode': 'unsigned-device',
                'bundle_id': 'org.livara.ayugram', 'build_number': 1001, 'sha256': sha(ipa_bytes),
                'toolchain': static.native_validation.PINS, 'runtime_verified': False, 'signature_verified': False,
                'signing': 'disabled', 'license_materials': licenses, 'synthetic_test_fixture_only': True}
            manifest_bytes = encode(manifest)
            proof, _, _ = device_fixture()
            proof.update(source_manifest_sha256=sha(manifest_bytes), source_ipa_sha256=manifest['sha256'],
                         license_materials=licenses, synthetic_test_fixture_only=True)
            files = {**materials, 'Telegram.ipa': ipa_bytes, 'manifest.json': manifest_bytes}
            files['SHA256SUMS'] = ''.join(sha(value) + '  ' + name + '\n'
                                         for name, value in sorted(files.items())).encode()
            members = {'fork-baseline-unsigned-device/' + name: value for name, value in files.items()}
            members['fork-evidence/build-verification.json'] = encode(proof)
            device_zip = write_zip(base / 'device.zip', members)
            startup_zip = write_zip(base / 'synthetic-failure.zip', {'SYNTHETIC-ONLY': b'not actual startup evidence'})
            device = {'id': 13539444219, 'size_in_bytes': 75971522, 'digest': 'sha256:' + sha(device_zip.read_bytes()),
                      'workflow_run': {'id': exporter.RUN, 'repository_id': 1445292587, 'head_repository_id': 1445292587}}
            before = {path: path.read_bytes() for path in (device_zip, startup_zip)}
            output = base / 'output'

            def synthetic_failure(path, destination, modules):
                self.assertEqual(path, startup_zip)
                write_tree(destination, {'synthetic-only.json': encode({'startup_verified': False,
                           'synthetic_test_fixture_only': True})})
                return {'startup_verified': False, 'synthetic_test_fixture_only': True}

            def synthetic_material_writer(values, destination):
                for name, value in values.items(): (destination / name).write_bytes(value)

            inspection = {'all_macho_platforms': 2, 'all_bundled_dependencies_resolved': True,
                          'signing': 'disabled', 'synthetic_test_fixture_only': True}
            with mock.patch.object(origins, 'artifact', return_value=device), \
                 mock.patch.object(exporter, 'failed_evidence', side_effect=synthetic_failure) as failure_provider, \
                 mock.patch.object(licensing, 'read_materials', return_value=materials), \
                 mock.patch.object(licensing, 'validate_materials', return_value=licenses), \
                 mock.patch.object(licensing, 'write_materials', side_effect=synthetic_material_writer), \
                 mock.patch.object(helpers['device_variant'], 'unsigned_device_inspection', return_value={'synthetic_test_fixture_only': True}), \
                 mock.patch.object(helpers['ipa_device_inspect'], 'inspect_unsigned_device', return_value=inspection):
                report = exporter.export(device_zip, startup_zip, original_run, original_jobs, {},
                    failed_run(), failed_jobs(), failed_artifacts(), self.modules, output, 'f' * 40)
            failure_provider.assert_called_once()
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            self.assertEqual(report['status'], 'not_runtime_qualified')
            self.assertEqual(report['qualification_status'], 'not_runtime_qualified')
            self.assertEqual(report['retained_entries'], len(retained))
            self.assertEqual(report['extensions_removed'], 6)
            self.assertEqual(report['source_device_ipa_sha256'], sha(ipa_bytes))
            self.assertEqual(report['sha256'], sha((output / exporter.IPA_NAME).read_bytes()))
            for key in ('application_recompiled', 'build_number_changed', 'runtime_verified',
                        'simulator_variant_startup_verified', 'physical_device_verified', 'telegram_authentication_verified',
                        'device_installation_verified', 'sidestore_installation_verified', 'automatic_storage_fallback_verified',
                        'signature_verified', 'installable_signed_ipa', 'release_qualified',
                        'external_native_qualification_reference_reverified'):
                self.assertIs(report[key], False, key)
            self.assertEqual(report['source_run_conclusion'], 'failure')
            self.assertEqual(report['failed_startup_conclusion'], 'failure')
            self.assertEqual(report['source_device_job_conclusion'], 'success')
            with zipfile.ZipFile(output / exporter.IPA_NAME) as archive:
                self.assertEqual({name: archive.read(name) for name in archive.namelist()}, retained)
            for name, value in materials.items(): self.assertEqual((output / name).read_bytes(), value)
            self.assertEqual((output / 'evidence/original-device-manifest.json').read_bytes(), manifest_bytes)
            self.assertEqual((output / 'evidence/original-device-build-proof.json').read_bytes(), encode(proof))
            summary = json.loads((output / 'evidence/origin-summary.json').read_bytes())
            self.assertEqual(summary['original_delivery']['simulator_job_conclusion'], 'failure')
            self.assertIs(summary['failed_startup']['startup_verified'], False)
            self.assertIs(summary['external_native_qualification_reference']['referenced_bytes_included'], False)
            self.assertIs(summary['external_native_qualification_reference']['reference_target_reverified'], False)
            static.checksum_tree(output)
            static.checksum_tree(output / 'evidence/failed-startup')
            self.assertEqual(list(base.glob('.unqualified-export-*')), [])


if __name__ == '__main__':
    unittest.main()
