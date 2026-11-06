# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable, offline retained-package guards using owned synthetic fixtures.

The end-to-end fixture mocks native/static/startup verification providers. It
tests export plumbing and honest failed-job reporting, never real qualification.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import warnings
import zipfile

import resume_package as package
import resume_startup
import installer_observation


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def checksum_bytes(files):
    return ''.join(sha(value) + '  ' + name + '\n' for name, value in sorted(files.items())).encode()


def write_tree(root, files):
    root.mkdir(parents=True)
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (root / 'SHA256SUMS').write_bytes(checksum_bytes(files))


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def cleanup():
    return {'schema': 1, 'owned_device_deleted': True,
            'owned_uuid_absence_verified': True, 'exact_owned_device_only': True}


def closed_replay_fixture():
    return {'mode': 'main_app_only', 'physical_device_verified': False,
            'startup_variant': 'main-app-only', 'private_control_only': True,
            'application_recompiled': False, 'passed': True, 'normal_control_passed': False,
            'no_app_groups_control_attempted': False, 'source_codesign_verified': True,
            'phase': 'complete', 'simulator_device_type': 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro',
            'simulator_runtime': 'com.apple.CoreSimulator.SimRuntime.iOS-27-0',
            'source_simulator_ipa_sha256': package.SIM_IPA_SHA,
            'private_mode_control': 'explicitFreshPinnedSelection'}


class DeploymentLayoutTests(unittest.TestCase):
    def test_separate_process_import_resolves_general_helpers_and_inventory_from_parent_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve() / 'repo'
            general = repository / 'tools/fork_baseline'
            resume = general / 'startup_resume'
            resume.mkdir(parents=True)
            source = Path(package.__file__).resolve().parent
            for name in ('resume_package.py', 'resume_origins.py', 'original-step-contract.json'):
                shutil.copyfile(source / name, resume / name)
            shutil.copyfile(Path(package.native_validation.__file__), general / 'device_delivery.py')
            shutil.copyfile(package.TOOL_HELPERS / 'device_delivery_inventory.json',
                            general / 'device_delivery_inventory.json')
            environment = os.environ.copy()
            environment['PYTHONPATH'] = str(resume)
            environment['PYTHONDONTWRITEBYTECODE'] = '1'
            script = '''
import os
from pathlib import Path
import resume_package as package
resume = Path(package.__file__).resolve().parent
assert os.environ['PYTHONPATH'] == str(resume)
assert package.TOOL_HELPERS == resume.parent
assert Path(package.native_validation.__file__).resolve() == resume.parent / 'device_delivery.py'
assert (package.TOOL_HELPERS / 'device_delivery_inventory.json').is_file()
assert not (resume / 'device_delivery.py').exists()
assert not (resume / 'device_delivery_inventory.json').exists()
'''
            subprocess.run([sys.executable, '-c', script], cwd=repository, env=environment,
                           capture_output=True, text=True, timeout=30, check=True)


class CleanupAndChecksumTests(unittest.TestCase):
    def test_cleanup_requires_all_three_literal_true_fields_and_no_reported_cleanup_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            proof = root / 'cleanup.json'
            proof.write_bytes(encode(cleanup()))
            self.assertEqual(package.cleanup_proof(root, {'cleanup_failures': []}), cleanup())
            # Stock successful 5d results omit this optional field; retain their
            # exact bytes and require the independent three-part cleanup proof.
            self.assertEqual(package.cleanup_proof(root, {}), cleanup())
            for name in ('owned_device_deleted', 'owned_uuid_absence_verified', 'exact_owned_device_only'):
                for value in (False, None, 1, 'true'):
                    proof.write_bytes(encode({**cleanup(), name: value}))
                    with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                        package.cleanup_proof(root, {'cleanup_failures': []})
                missing = cleanup(); del missing[name]
                proof.write_bytes(encode(missing))
                with self.assertRaises(ValueError): package.cleanup_proof(root, {'cleanup_failures': []})
            for schema in (True, '1', 2):
                proof.write_bytes(encode({**cleanup(), 'schema': schema}))
                with self.subTest(schema=schema), self.assertRaises(ValueError):
                    package.cleanup_proof(root, {'cleanup_failures': []})
            proof.write_bytes(encode(cleanup()))
            for result in ({'cleanup_failures': None}, {'cleanup_failures': {}}, {'cleanup_failures': ()},
                           {'cleanup_failures': [{'phase': 'deleteDevice'}]}):
                with self.subTest(result=result), self.assertRaises(ValueError): package.cleanup_proof(root, result)

    def test_complete_nested_checksum_tree_passes_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'tree'
            files = {'fixture.txt': b'fixture only', 'nested/test.log': b'passed fixture'}
            write_tree(root, files)
            before = tree_bytes(root)
            self.assertEqual(package.checksum_tree(root), {name: sha(data) for name, data in files.items()})
            self.assertEqual(tree_bytes(root), before)

    def test_checksum_tree_rejects_orphan_reference_unlisted_file_and_corrupt_member(self):
        for kind in ('orphan', 'unlisted', 'corrupt'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve() / 'tree'
                write_tree(root, {'fixture.txt': b'original fixture'})
                if kind == 'orphan':
                    with (root / 'SHA256SUMS').open('ab') as stream:
                        stream.write(b'0' * 64 + b'  absent.log\n')
                elif kind == 'unlisted':
                    (root / 'unlisted.log').write_bytes(b'not inventoried')
                else:
                    (root / 'fixture.txt').write_bytes(b'changed')
                with self.assertRaises(package.Rejected): package.checksum_tree(root)

    def test_checksum_tree_rejects_symlink_files_directories_root_and_checksum(self):
        for kind in ('file', 'directory', 'root', 'checksums'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                root = base / 'tree'
                write_tree(root, {'fixture.txt': b'owned fixture'})
                inspected = root
                if kind == 'file':
                    (root / 'link').symlink_to(root / 'fixture.txt')
                elif kind == 'directory':
                    (root / 'link').symlink_to(base, target_is_directory=True)
                elif kind == 'root':
                    inspected = base / 'alias'; inspected.symlink_to(root, target_is_directory=True)
                else:
                    original = base / 'hashes'; original.write_bytes((root / 'SHA256SUMS').read_bytes())
                    (root / 'SHA256SUMS').unlink(); (root / 'SHA256SUMS').symlink_to(original)
                with self.assertRaises(package.Rejected): package.checksum_tree(inspected)

    def test_checksum_parser_rejects_duplicate_and_traversing_references(self):
        for names in (('same', 'same'), ('../escape',), ('/absolute',), ('a/../escape',), ('a\\escape',)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / 'SHA256SUMS'
                path.write_text(''.join('0' * 64 + '  ' + name + '\n' for name in names))
                with self.assertRaises(package.Rejected): package.checksums(path)


class ArchiveAndOriginTests(unittest.TestCase):
    def test_verified_archive_checks_outer_digest_before_exposing_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = write_zip(Path(temporary).resolve() / 'artifact.zip', {'fixture.log': b'fixture'})
            with package.verified_archive(path, package.digest(path)) as archive:
                self.assertEqual(archive.read('fixture.log'), b'fixture')
            with self.assertRaises(package.Rejected):
                with package.verified_archive(path, '0' * 64): self.fail('bad digest yielded')
            with self.assertRaises(package.Rejected):
                with package.verified_archive(path, package.digest(path), maximum=1): self.fail('oversize yielded')

    def test_verified_archive_rejects_traversal_duplicate_and_special_unix_members(self):
        for kind in ('../escape', '/absolute', 'a/../escape', 'a\\escape', 'duplicate', 'symlink', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / 'artifact.zip'
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(path, 'w') as archive:
                        if kind in ('symlink', 'fifo'):
                            info = zipfile.ZipInfo('special')
                            info.external_attr = ((stat.S_IFLNK if kind == 'symlink' else stat.S_IFIFO) | 0o600) << 16
                            archive.writestr(info, b'fixture')
                        else:
                            archive.writestr('same' if kind == 'duplicate' else kind, b'fixture')
                            if kind == 'duplicate': archive.writestr('same', b'again')
                with self.assertRaises(package.Rejected):
                    with package.verified_archive(path, package.digest(path)): self.fail('unsafe ZIP yielded')

    def test_producing_reports_keep_failed_simulator_job_and_successful_compile_distinct(self):
        manifest = {'sha256': 'a' * 64}
        licenses = {'fixture': {'sha256': 'b' * 64, 'synthetic_test_fixture_only': True}}
        artifact = {'id': 123, 'digest': 'sha256:' + 'c' * 64}
        for mode, expected in (('simulator', 'failure'), ('unsigned-device', 'success')):
            with self.subTest(mode=mode):
                report = package.producing_report(mode, manifest, licenses, artifact)
                self.assertEqual(report['source_job_conclusion'], expected)
                self.assertIs(report['source_compile_step_passed'], True)
                self.assertEqual(report['source_run'], package.origins.RUN)
                self.assertEqual(report['source_commit'], package.origins.SOURCE)
                self.assertFalse(report['physical_device_verified'])
                self.assertFalse(report['release_qualified'])

    def test_fresh_archive_tree_inventory_and_bytes_must_match_before_derivation(self):
        for difference in ('extra', 'missing', 'changed', 'wrong-outer-hash'):
            with self.subTest(difference=difference), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                fresh = base / 'fresh'
                write_tree(fresh, {'fixture.log': b'current fixture'})
                zipped = tree_bytes(fresh)
                if difference == 'extra': zipped['unexpected.log'] = b'extra'
                elif difference == 'missing': del zipped['fixture.log']
                elif difference == 'changed': zipped['fixture.log'] = b'changed fixture'
                startup_zip = write_zip(base / 'startup.zip', zipped)
                paths = {role: write_zip(base / (role + '.zip'), {'fixture.log': role.encode()})
                         for role in ('device', 'simulator', 'native')}
                artifacts = {role: {'id': i + 1, 'digest': 'sha256:' + package.digest(path)}
                             for i, (role, path) in enumerate(paths.items())}
                startup = {'artifact_sha256': '0' * 64 if difference == 'wrong-outer-hash' else package.digest(startup_zip)}
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(package.origins, 'original_graph', return_value={'synthetic': True}))
                    stack.enter_context(mock.patch.object(package.origins, 'validate_startup_origin', return_value=startup))
                    stack.enter_context(mock.patch.object(package.origins, 'artifact', side_effect=lambda role, _: artifacts[role]))
                    derive = stack.enter_context(mock.patch.object(package, 'load_helpers'))
                    with self.assertRaises(package.Rejected):
                        package.package(base, paths['device'], paths['simulator'], paths['native'], {}, {}, {}, {},
                                        fresh, startup, base / 'output', startup_archive=startup_zip)
                    derive.assert_not_called()
                    self.assertFalse((base / 'output').exists())

    def test_unknown_replay_proof_field_is_rejected_before_helpers_or_output_derivation(self):
        proof = closed_replay_fixture()
        # Establish that adding this field, rather than missing required fields,
        # causes the same closed-schema provider used by the Mac stager to fail.
        self.assertEqual(resume_startup.validate_replay_schema(proof), proof)
        proof['unreviewed_diagnostic_text'] = 'SYNTHETIC_CANARY_MUST_NOT_ENTER_EXPORT'
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            fresh = base / 'fresh'
            write_tree(fresh, {'replay.json': encode(proof)})
            before = tree_bytes(fresh)
            startup_zip = write_zip(base / 'startup.zip', before)
            paths = {role: write_zip(base / (role + '.zip'), {'fixture.log': role.encode()})
                     for role in ('device', 'simulator', 'native')}
            artifacts = {role: {'id': i + 1, 'digest': 'sha256:' + package.digest(path)}
                         for i, (role, path) in enumerate(paths.items())}
            startup = {'artifact_sha256': package.digest(startup_zip)}
            strip = mock.Mock()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(package.origins, 'original_graph', return_value={'synthetic': True}))
                stack.enter_context(mock.patch.object(package.origins, 'validate_startup_origin', return_value=startup))
                stack.enter_context(mock.patch.object(package.origins, 'artifact', side_effect=lambda role, _: artifacts[role]))
                derive = stack.enter_context(mock.patch.object(package, 'load_helpers', return_value={
                    'device_variant': types.SimpleNamespace(strip_extensions=strip)}))
                validator = stack.enter_context(mock.patch.object(resume_startup, 'validate_replay_schema',
                                                                 wraps=resume_startup.validate_replay_schema))
                with self.assertRaisesRegex(package.origins.Rejected, 'replayProofFields'):
                    package.package(base, paths['device'], paths['simulator'], paths['native'], {}, {}, {}, {},
                                    fresh, startup, base / 'output', startup_archive=startup_zip)
                validator.assert_called_once_with(proof)
                derive.assert_not_called()
                strip.assert_not_called()
            self.assertFalse((base / 'output').exists())
            self.assertEqual(tree_bytes(fresh), before)

    def test_linux_optional_diagnostic_unknown_fields_and_canaries_reject_before_derivation(self):
        valid = installer_observation.blank()
        self.assertEqual(installer_observation.validate_observation(valid), valid)
        changes = ({'private_canary': 'SYNTHETIC_PRIVATE_MUST_NOT_EXPORT'},
                   {'source': 'SYNTHETIC_CANARY_MUST_NOT_EXPORT'},
                   {'host': {**valid['host'], 'raw_path': '/SYNTHETIC_PRIVATE_MUST_NOT_EXPORT'}},
                   {'startup_verified': True})
        for changed in changes:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary).resolve()
                fresh = base / 'fresh'
                write_tree(fresh, {'replay.json': encode(closed_replay_fixture()),
                                  'installer-observation.json': encode({**valid, **changed})})
                before = tree_bytes(fresh)
                startup_zip = write_zip(base / 'startup.zip', before)
                paths = {role: write_zip(base / (role + '.zip'), {'fixture.log': role.encode()})
                         for role in ('device', 'simulator', 'native')}
                artifacts = {role: {'id': i + 1, 'digest': 'sha256:' + package.digest(path)}
                             for i, (role, path) in enumerate(paths.items())}
                startup = {'artifact_sha256': package.digest(startup_zip)}
                strip = mock.Mock()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(package.origins, 'original_graph', return_value={'synthetic': True}))
                    stack.enter_context(mock.patch.object(package.origins, 'validate_startup_origin', return_value=startup))
                    stack.enter_context(mock.patch.object(package.origins, 'artifact', side_effect=lambda role, _: artifacts[role]))
                    derive = stack.enter_context(mock.patch.object(package, 'load_helpers', return_value={
                        'device_variant': types.SimpleNamespace(strip_extensions=strip)}))
                    validator = stack.enter_context(mock.patch.object(resume_startup, 'validate_optional_public',
                                                                     wraps=resume_startup.validate_optional_public))
                    with self.assertRaisesRegex(ValueError, 'installer_observation_rejected'):
                        package.package(base, paths['device'], paths['simulator'], paths['native'], {}, {}, {}, {},
                                        fresh, startup, base / 'output', startup_archive=startup_zip)
                    validator.assert_called_once_with('installer-observation.json', fresh / 'installer-observation.json')
                    derive.assert_not_called()
                    strip.assert_not_called()
                self.assertFalse((base / 'output').exists())
                self.assertEqual(tree_bytes(fresh), before)

    def test_valid_optional_diagnostic_does_not_replace_required_stock_startup_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            fresh = base / 'fresh'
            diagnostic = installer_observation.blank()
            write_tree(fresh, {'replay.json': encode(closed_replay_fixture()),
                              'installer-observation.json': encode(diagnostic)})
            startup_zip = write_zip(base / 'startup.zip', tree_bytes(fresh))
            paths = {role: write_zip(base / (role + '.zip'), {'fixture.log': role.encode()})
                     for role in ('device', 'simulator', 'native')}
            artifacts = {role: {'id': i + 1, 'digest': 'sha256:' + package.digest(path)}
                         for i, (role, path) in enumerate(paths.items())}
            startup = {'artifact_sha256': package.digest(startup_zip)}
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(package.origins, 'original_graph', return_value={'synthetic': True}))
                stack.enter_context(mock.patch.object(package.origins, 'validate_startup_origin', return_value=startup))
                stack.enter_context(mock.patch.object(package.origins, 'artifact', side_effect=lambda role, _: artifacts[role]))
                derive = stack.enter_context(mock.patch.object(package, 'load_helpers'))
                validator = stack.enter_context(mock.patch.object(resume_startup, 'validate_optional_public',
                                                                 wraps=resume_startup.validate_optional_public))
                with self.assertRaisesRegex(package.Rejected, 'documentBounds'):
                    package.package(base, paths['device'], paths['simulator'], paths['native'], {}, {}, {}, {},
                                    fresh, startup, base / 'output', startup_archive=startup_zip)
                validator.assert_called_once_with('installer-observation.json', fresh / 'installer-observation.json')
                derive.assert_not_called()
            self.assertFalse(diagnostic['startup_verified'])
            self.assertFalse((base / 'output').exists())


def synthetic_ipa(mode):
    """A ZIP metadata fixture, not a real application or signed runtime proof."""
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({
            'CFBundleVersion': '1001', 'CFBundleIdentifier': 'org.livara.ayugram',
            'AyuGramBaseBundleIdentifier': 'org.livara.ayugram', 'CFBundleDisplayName': 'AyuGram',
            'CFBundleExecutable': 'Telegram', 'CFBundleSupportedPlatforms': ['iPhoneSimulator' if mode == 'simulator' else 'iPhoneOS']}))
        archive.writestr('Payload/Telegram.app/Telegram', b'NOT REAL MACH-O: SYNTHETIC PLUMBING FIXTURE ' + mode.encode())
    return data.getvalue()


class SyntheticPackageFlowTests(unittest.TestCase):
    def test_full_copy_plumbing_preserves_original_failure_manifests_and_complete_evidence(self):
        # All verification providers below are explicit mocks. This test does
        # not assert actual compilation, native-test, startup, or signing success.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            materials = {name: ('SYNTHETIC LICENSE FIXTURE ONLY ' + name).encode() for name in package.FIXED_MATERIALS}
            licenses = {name: {'sha256': sha(data), 'size_bytes': len(data)} for name, data in materials.items()}
            manifests, raw = {}, {}
            for mode in ('unsigned-device', 'simulator'):
                ipa = synthetic_ipa(mode)
                manifest = {'source_commit': package.origins.SOURCE, 'mode': mode,
                            'bundle_id': 'org.livara.ayugram', 'build_number': package.BUILD,
                            'sha256': sha(ipa), 'toolchain': package.native_validation.PINS,
                            'runtime_verified': False, 'signature_verified': False,
                            'signing': 'disabled' if mode == 'unsigned-device' else 'not_verified',
                            'license_materials': licenses, 'synthetic_test_fixture_only': True,
                            'tests': {'//submodules/TextFormat:TextFormatTests': 'passed'} if mode == 'simulator' else {}}
                raw[mode] = {**materials, 'Telegram.ipa': ipa, 'manifest.json': encode(manifest)}
                raw[mode]['SHA256SUMS'] = checksum_bytes(raw[mode])
                manifests[mode] = manifest
            original_replay = b'{"synthetic_old_startup_failed":true}\n'
            original_result = b'{"synthetic_old_cleanup_failed":true}\n'
            sim_members = {'fork-baseline-simulator/' + n: v for n, v in raw['simulator'].items()}
            textformat = {'collection.json': encode({'target': '//submodules/TextFormat:TextFormatTests',
                'invocation': 'passed', 'files': {'test.xml': 'retained_redacted', 'test.log': 'retained_redacted'}}),
                'test.xml': b'<testsuite tests="1"><testcase name="fixture"/></testsuite>',
                'test.log': b'SYNTHETIC mocked native verification provider; not real qualification\n'}
            sim_members.update({'fork-evidence/textformat/' + n: v for n, v in textformat.items()})
            sim_members.update({'fork-evidence/startup-main-app-only/replay.json': original_replay,
                               'fork-evidence/startup-main-app-only/main-app-only/launch-smoke/result.json': original_result})
            paths = {'device': write_zip(base / 'device.zip', {'fork-baseline-unsigned-device/' + n: v for n, v in raw['unsigned-device'].items()}),
                     'simulator': write_zip(base / 'simulator.zip', sim_members)}
            native = {'synthetic-native-fixture.log': b'NOT REAL NATIVE QUALIFICATION'}
            native['SHA256SUMS'] = checksum_bytes(native)
            paths['native'] = write_zip(base / 'native.zip', native)
            artifact_records = {role: {'id': i + 1, 'digest': 'sha256:' + package.digest(path)}
                                for i, (role, path) in enumerate(paths.items())}
            fresh = base / 'fresh'
            source_receipt = {'source_manifest_sha256': sha(raw['simulator']['manifest.json']),
                              'source_ipa_sha256': manifests['simulator']['sha256'], 'synthetic_test_fixture_only': True}
            fresh_files = {'replay.json': encode({'source_simulator_ipa_sha256': manifests['simulator']['sha256'],
                            'private_mode_control': 'explicitFreshPinnedSelection', 'private_control_only': True}),
                           'cleanup.json': encode(cleanup()), 'source-build-origin.json': encode(source_receipt),
                           'main-app-only/launch-smoke/result.json': encode({'cleanup_failures': [], 'synthetic_test_fixture_only': True})}
            fresh_files['installer-observation.json'] = encode(installer_observation.blank())
            fresh_files.update({'main-app-only/launch-smoke/' + name: b'SYNTHETIC VISUAL FIXTURE ONLY'
                                for name in ('launch-1.png', 'launch-2.png', 'launch.mp4')})
            write_tree(fresh, fresh_files)
            startup_zip = write_zip(base / 'startup.zip', tree_bytes(fresh))
            startup_origin = {'artifact_sha256': package.digest(startup_zip), 'resume_tooling_commit': 'e' * 40,
                              'synthetic_test_fixture_only': True}
            inventory_path = base / 'inventory.json'; inventory_path.write_bytes(b'{"synthetic_test_fixture_only":true}')
            receipt = {'original_conclusion': 'failure', 'simulator_job_conclusion': 'failure',
                       'device_job_conclusion': 'success', 'simulator_compile_step_passed': True,
                       'original_package_conclusion': 'skipped', 'synthetic_test_fixture_only': True}
            native_report = {'fixture-archive': {'native_passed_cases': 129, 'added_cases_verified': ['fixture' + str(i) for i in range(10)],
                                               'synthetic_test_fixture_only': True},
                             'fixture-core': {'native_passed_cases': 835, 'added_cases_verified': ['fixture' + str(i) for i in range(18)],
                                              'synthetic_test_fixture_only': True}}

            def write_materials(values, destination):
                for name, value in values.items(): (destination / name).write_bytes(value)

            def strip_fixture(source, destination, count):
                self.assertEqual(count, 6)
                destination.write_bytes(source.read_bytes())
                return {'extensions_removed': 6, 'main_code_unchanged': True,
                        'synthetic_test_fixture_only': True}

            validate_guard = mock.Mock(return_value=(package.origins.SOURCE, package.origins.RUN, 6))
            # The full plumbing fixture's proof is intentionally synthetic; its
            # schema provider is mocked here and exercised for real above.
            validate_replay = mock.Mock(side_effect=lambda value: value)
            validate_optional = mock.Mock(wraps=resume_startup.validate_optional_public)
            helpers = {'artifact_licensing': types.SimpleNamespace(
                           read_materials=mock.Mock(return_value=materials), validate_materials=mock.Mock(return_value=licenses),
                           write_materials=write_materials),
                       'ipa_inspect': types.SimpleNamespace(inspect_ipa=mock.Mock()),
                       'ipa_device_inspect': types.SimpleNamespace(inspect_unsigned_device=mock.Mock()),
                       'branding': types.SimpleNamespace(inspect_branding=mock.Mock()),
                       'device_variant': types.SimpleNamespace(validate_startup=validate_guard, strip_extensions=strip_fixture)}
            before = {role: package.digest(path) for role, path in paths.items()}
            fresh_before = tree_bytes(fresh)
            output = base / 'output'
            with contextlib.ExitStack() as stack:
                for owner, name, replacement in (
                    (package.origins, 'original_graph', mock.Mock(return_value=receipt)),
                    (package.origins, 'validate_startup_origin', mock.Mock(return_value=startup_origin)),
                    (package.origins, 'artifact', mock.Mock(side_effect=lambda role, _: artifact_records[role])),
                    (package.origins, 'validate_source_build_origin', mock.Mock(return_value=source_receipt)),
                    (package.origins, 'original_failure', mock.Mock(return_value={'synthetic_test_fixture_only': True})),
                    (package, 'load_helpers', mock.Mock(return_value=helpers)),
                    (resume_startup, 'validate_replay_schema', validate_replay),
                    (resume_startup, 'validate_optional_public', validate_optional),
                    (package.native_validation, 'verify_evidence', mock.Mock(return_value=(native_report, {}))),
                    (package.native_validation, 'native_tests', mock.Mock(return_value=({'native_passed_cases': 270,
                                                                                       'synthetic_test_fixture_only': True}, {}))),
                ): stack.enter_context(mock.patch.object(owner, name, replacement))
                stack.enter_context(mock.patch.object(package, 'SIM_IPA_SHA', manifests['simulator']['sha256']))
                result = package.package(base, paths['device'], paths['simulator'], paths['native'], {}, {}, {}, {},
                                         fresh, startup_origin, output, startup_archive=startup_zip,
                                         inventory_path=inventory_path)
            self.assertEqual({role: package.digest(path) for role, path in paths.items()}, before)
            validate_replay.assert_called_once_with(json.loads(fresh_files['replay.json']))
            validate_optional.assert_called_once_with('installer-observation.json', fresh / 'installer-observation.json')
            self.assertEqual(tree_bytes(fresh), fresh_before)
            self.assertEqual(result['original_delivery']['simulator_job_conclusion'], 'failure')
            self.assertTrue(result['original_delivery']['simulator_compile_step_passed'])
            self.assertFalse(result['application_recompiled'])
            self.assertFalse(result['physical_device_verified'])
            self.assertFalse(result['runtime_verified'])
            self.assertFalse(package.document(output / 'evidence/fresh-startup/installer-observation.json')['startup_verified'])
            self.assertEqual((output / 'evidence/fresh-startup/installer-observation.json').read_bytes(),
                             fresh_files['installer-observation.json'])
            self.assertTrue(result['candidate_only'])
            produced = package.document(output / 'producing-origins.json')
            self.assertEqual(produced['simulator']['source_job_conclusion'], 'failure')
            self.assertTrue(produced['simulator']['source_compile_step_passed'])
            self.assertEqual(produced['device']['source_job_conclusion'], 'success')
            self.assertEqual((output / 'evidence/original-device-manifest.json').read_bytes(), raw['unsigned-device']['manifest.json'])
            self.assertEqual((output / 'evidence/original-simulator-manifest.json').read_bytes(), raw['simulator']['manifest.json'])
            self.assertEqual((output / 'evidence/original-startup-failure/replay.json').read_bytes(), original_replay)
            for name, value in textformat.items(): self.assertEqual((output / 'evidence/textformat' / name).read_bytes(), value)
            self.assertEqual(result['sha256'], package.digest(output / result['filename']))
            package.checksum_tree(output)
            package.checksum_tree(output / 'evidence/native')
            package.checksum_tree(output / 'evidence/fresh-startup')
            device_report, sim_report, _, _ = validate_guard.call_args.args
            self.assertEqual(device_report['source_job_conclusion'], 'success')
            self.assertEqual(sim_report['source_job_conclusion'], 'failure')
            self.assertEqual(list(base.glob('.retained-package-*')), [])


if __name__ == '__main__':
    unittest.main()
