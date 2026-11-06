"""Device variants must preserve content and remain gated on real startup evidence."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import re
import stat
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifact_licensing
import device_variant as variant
import replay_artifact as replay
from test_replay_artifact import API

SOURCE, RUN = 'c' * 40, 42031063759


def macho(platform=2, signature=False, filetype=2, media=False):
    commands = struct.pack('<6I', 0x32, 24, platform, 0x000f0000, 0x001b0100, 0)
    count = 1
    if media:
        for code, header_size, text in (
                (0x8000001c, 12, '@executable_path/Frameworks'),
                (0xc, 24, '@rpath/Media.framework/Media')):
            payload = text.encode() + b'\0'
            length = (header_size + len(payload) + 7) & ~7
            command = struct.pack('<III', code, length, header_size)
            command += b'\0' * (header_size - len(command))
            commands += command + payload + b'\0' * (length - header_size - len(payload))
            count += 1
    if signature: commands += struct.pack('<4I', 0x1d, 16, 0, 0)
    return struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, filetype, count + int(signature),
                       len(commands), 0, 0) + commands


def ipa(path, extra=None):
    files = {'Payload/Telegram.app/Info.plist': plistlib.dumps({
        'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'Telegram',
        'CFBundleSupportedPlatforms': ['iPhoneOS'], 'CFBundleVersion': '54'}),
        'Payload/Telegram.app/Telegram': macho(media=True),
        'Payload/Telegram.app/Frameworks/Media.framework/Media': macho(filetype=6),
        'Payload/Telegram.app/Frameworks/Media.framework/Info.plist': plistlib.dumps({
            'CFBundleIdentifier': 'test.framework.media', 'CFBundleExecutable': 'Media'}),
        'Payload/Telegram.app/Assets.car': b'preserved application resource',
        'Payload/Telegram.app/Other/PlugIns/keep.txt': b'not the main PlugIns directory'}
    for n in range(6):
        base = 'Payload/Telegram.app/PlugIns/Extension' + str(n) + '.appex/'
        files[base + 'Info.plist'] = plistlib.dumps({
            'CFBundleIdentifier': 'test.extension.' + str(n), 'CFBundleExecutable': 'Executable'})
        files[base + 'Executable'] = macho()
    files.update(extra or {})
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b'preserved archive comment'
        for name in sorted(files):
            data = files[name]
            info = zipfile.ZipInfo(name, (2026, 11, 5, 1, 2, 4))
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            info.comment = b'preserved entry comment'
            archive.writestr(info, data)
    return path


def evidence(device_digest='d' * 64):
    common = {'schema': 1, 'repository': replay.REPOSITORY, 'source_run': RUN, 'source_commit': SOURCE,
              'artifact_id': 12, 'outer_sha256': 'e' * 64, 'application_source_unchanged': True,
              'source_compile_step_passed': True, 'physical_device_verified': False, 'release_qualified': False}
    device = {**common, 'mode': 'unsigned-device', 'ipa_sha256': device_digest, 'source_job_conclusion': 'success'}
    materials = artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], SOURCE)
    device['source_license_materials_present'] = True
    device['license_materials'] = artifact_licensing.validate_materials(materials, SOURCE)
    simulator = {**common, 'mode': 'simulator', 'ipa_sha256': 'a' * 64}
    proof = {'mode': 'main_app_only', 'startup_variant': 'main-app-only', 'passed': True, 'phase': 'complete',
             'private_mode_control': 'automaticSelection', 'automatic_fallback_verified': True,
             'private_control_only': False, 'automatic_control_run': True,
             'pinned_private_control_attempted': False, 'automatic_group_selection_observed': False,
             'main_app_only_control_passed': True,
             'source_codesign_verified': True, 'main_code_unchanged': True, 'source_ipa_unchanged': True,
             'application_recompiled': False, 'physical_device_verified': False, 'extensions_removed': 6,
             'source_simulator_ipa_sha256': 'a' * 64}
    result = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False, 'authentication_codes_used': False,
              'expected_storage_kind': 'private', 'recording_return_code': 0,
              'storage_test_setup': 'freshAutomatic',
              'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
              'passed': True, 'phase': 'complete', 'launches': [
                  {'attempt': n, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                   'screenshot': {'nonblank': True, 'width': 1206, 'height': 2622},
                   'seconds_to_marker': 4, 'storage_kind': 'private', 'private_account_store_present': True,
                   **({'private_account_store_stable_after_restart': True} if n == 2 else {})} for n in (1, 2)]}
    return device, simulator, proof, result


def paired_evidence(device_digest='d' * 64):
    device, simulator, proof, result = evidence(device_digest)
    for report in (device, simulator):
        report['source_commit'] = replay.PAIRED_SOURCE
        report['source_license_materials_present'] = True
        report['license_materials'] = artifact_licensing.validate_materials(
            artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], replay.PAIRED_SOURCE), replay.PAIRED_SOURCE)
    device.update(source_run=replay.PAIRED_DEVICE_RUN, source_workflow=replay.WORKFLOW,
                  source_build_job='unsigned-device-build', source_compile_step=replay.MODES['unsigned-device']['compile'],
                  source_build_number=82, artifact_id=82)
    simulator.update(source_run=replay.BASELINE_SIMULATOR_RUN, source_workflow=replay.BASELINE_WORKFLOW,
                     source_build_job='baseline', source_compile_step=replay.BASELINE_COMPILE_STEP,
                     source_job_conclusion='success', source_build_number=1, artifact_id=83)
    return device, simulator, proof, result


def artifact_fixture(path):
    payload = path.parent / 'device.ipa'; ipa(payload)
    data = payload.read_bytes()
    names = {'fork-baseline-unsigned-device/Telegram.ipa': data,
             'fork-baseline-unsigned-device/manifest.json': json.dumps({
                 'source_commit': SOURCE, 'mode': 'unsigned-device', 'bundle_id': replay.BUNDLE,
                 'sha256': hashlib.sha256(data).hexdigest(), 'signing': 'disabled',
                 'signature_verified': False, 'runtime_verified': False}).encode()}
    names['fork-baseline-unsigned-device/SHA256SUMS'] = (
        hashlib.sha256(data).hexdigest() + '  Telegram.ipa\n').encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in names.items(): archive.writestr(name, value)
    artifact = {'id': 123, 'name': 'archive-unsigned-device-' + SOURCE, 'expired': False,
                'size_in_bytes': path.stat().st_size, 'digest': 'sha256:' + variant.digest(path),
                'workflow_run': {'id': RUN, 'head_sha': SOURCE, 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': RUN, 'head_sha': SOURCE, 'path': replay.WORKFLOW,
           'repository': {'id': 7, 'full_name': replay.REPOSITORY},
           'head_repository': {'id': 7, 'full_name': replay.REPOSITORY}}
    jobs = {'total_count': 1, 'jobs': [{'id': 33, 'run_id': RUN, 'name': 'unsigned-device-build',
            'status': 'completed', 'conclusion': 'success', 'steps': [
                {'name': 'Compile unsigned device package', 'status': 'completed', 'conclusion': 'success'}]}]}
    return artifact, run, jobs


class DeviceVariantTests(unittest.TestCase):
    def test_only_exact_reviewed_cross_run_pair_and_verified_origins_can_supply_startup_control(self):
        reports = paired_evidence()
        self.assertEqual(variant.validate_startup(*reports), (replay.PAIRED_SOURCE, replay.PAIRED_DEVICE_RUN, 6))
        # Historical same-run controls are deliberately unchanged.
        self.assertEqual(variant.validate_startup(*evidence()), (SOURCE, RUN, 6))
        for kind in ('device_run', 'simulator_run', 'other_source', 'reverse', 'same_known_simulator_run',
                     'device_workflow', 'simulator_workflow', 'device_job', 'simulator_job',
                     'device_compile', 'simulator_compile', 'simulator_failed', 'device_failed',
                     'device_licenses', 'simulator_licenses', 'simulator_unverified'):
            device, simulator, proof, result = copy.deepcopy(reports)
            if kind == 'device_run': device['source_run'] += 1
            elif kind == 'simulator_run': simulator['source_run'] += 1
            elif kind == 'other_source': device['source_commit'] = simulator['source_commit'] = 'd' * 40
            elif kind == 'reverse': device['source_run'], simulator['source_run'] = simulator['source_run'], device['source_run']
            elif kind == 'same_known_simulator_run': device['source_run'] = simulator['source_run']
            elif kind == 'device_workflow': device['source_workflow'] = replay.BASELINE_WORKFLOW
            elif kind == 'simulator_workflow': simulator['source_workflow'] = replay.WORKFLOW
            elif kind == 'device_job': device['source_build_job'] = 'baseline'
            elif kind == 'simulator_job': simulator['source_build_job'] = 'application-build'
            elif kind == 'device_compile': device['source_compile_step'] = replay.BASELINE_COMPILE_STEP
            elif kind == 'simulator_compile': simulator['source_compile_step'] = replay.COMPILE_STEP
            elif kind == 'simulator_failed': simulator['source_job_conclusion'] = 'failure'
            elif kind == 'device_failed': device['source_job_conclusion'] = 'failure'
            elif kind == 'device_licenses': device['source_license_materials_present'] = False
            elif kind == 'simulator_licenses': simulator['source_license_materials_present'] = False
            else: simulator['source_compile_step_passed'] = False
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError):
                variant.validate_startup(device, simulator, proof, result)

    def test_paired_package_records_both_actual_origins_and_build_numbers_without_relabelling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = ipa(root / 'source.ipa', extra={'Payload/Telegram.app/Info.plist': plistlib.dumps({
                'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'Telegram',
                'CFBundleSupportedPlatforms': ['iPhoneOS'], 'CFBundleVersion': '82'})})
            reports = paired_evidence(variant.digest(source)); paths = []
            for index, value in enumerate(reports):
                path = root / (str(index) + '.json'); path.write_text(json.dumps(value)); paths.append(path)
            materials = artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], replay.PAIRED_SOURCE)
            artifact_licensing.write_materials(materials, root)
            report = variant.package(source, *paths, root / 'published')
            self.assertEqual(report['source_run'], replay.PAIRED_DEVICE_RUN)
            self.assertEqual(report['source_device_run'], replay.PAIRED_DEVICE_RUN)
            self.assertEqual(report['source_simulator_run'], replay.BASELINE_SIMULATOR_RUN)
            self.assertEqual(report['source_commit'], replay.PAIRED_SOURCE)
            self.assertEqual(report['source_device_artifact_id'], 82); self.assertEqual(report['source_simulator_artifact_id'], 83)
            self.assertEqual(report['source_device_build_number'], 82); self.assertEqual(report['source_simulator_build_number'], 1)
            self.assertEqual(report['source_device_workflow'], replay.WORKFLOW)
            self.assertEqual(report['source_simulator_workflow'], replay.BASELINE_WORKFLOW)
            self.assertTrue(report['cross_run_startup_control'])
            self.assertFalse(report['build_number_changed']); self.assertFalse(report['application_recompiled'])
            self.assertFalse(report['runtime_verified']); self.assertFalse(report['physical_device_verified'])
            self.assertFalse(report['installable_signed_ipa']); self.assertFalse(report['release_qualified'])
            with zipfile.ZipFile(source) as before, zipfile.ZipFile(root / 'published' / report['filename']) as after:
                self.assertEqual(before.read('Payload/Telegram.app/Info.plist'), after.read('Payload/Telegram.app/Info.plist'))
            for name in artifact_licensing.MATERIAL_NAMES:
                self.assertEqual((root / 'published' / name).read_bytes(), materials[name])

    def test_device_mode_requires_exact_successful_job_and_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            artifact, run, jobs = artifact_fixture(Path(d) / 'source.zip')
            selected, report = replay.select_artifact(RUN, SOURCE, API(artifact, run, jobs), 'unsigned-device')
            self.assertEqual(selected['id'], 123)
            self.assertTrue(report['source_compile_step_passed'])
            for field in ('job', 'step', 'conclusion', 'artifact', 'source'):
                api = API(artifact, run, jobs)
                if field == 'job': api.jobs['jobs'][0]['name'] = 'application-build'
                if field == 'step': api.jobs['jobs'][0]['steps'][0]['name'] = replay.COMPILE_STEP
                if field == 'conclusion': api.jobs['jobs'][0]['conclusion'] = 'failure'
                if field == 'artifact': api.artifact['name'] = 'archive-integration-' + SOURCE
                if field == 'source': api.artifact['workflow_run']['head_sha'] = 'f' * 40
                with self.subTest(field=field), self.assertRaises(replay.ReplayError):
                    replay.select_artifact(RUN, SOURCE, api, 'unsigned-device')

    def test_device_extract_verifies_outer_inner_manifest_and_all_macho(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root / 'source.zip'
            artifact, _, _ = artifact_fixture(archive)
            result = replay.verify_extract(archive, artifact, SOURCE, root / 'verified', 'unsigned-device')
            self.assertEqual(result['main_executable']['unsigned_macho_count'], 8)
            self.assertEqual(result['main_executable']['all_macho_platforms'], 2)
            self.assertEqual((root / 'verified/Telegram.ipa').read_bytes(), (root / 'device.ipa').read_bytes())
            artifact['digest'] = 'sha256:' + '0' * 64
            with self.assertRaisesRegex(replay.ReplayError, 'artifact_digest_mismatch'):
                replay.verify_extract(archive, artifact, SOURCE, root / 'invalid', 'unsigned-device')
            self.assertFalse((root / 'invalid').exists())

    def test_every_embedded_binary_must_be_unsigned_arm64_device(self):
        for extra in ({'Payload/Telegram.app/Frameworks/Bad.framework/Bad': macho(signature=True, filetype=6)},
                      {'Payload/Telegram.app/Frameworks/Bad.framework/Bad': macho(platform=7, filetype=6)},
                      {'Payload/Telegram.app/embedded.mobileprovision': b'profile'},
                      {'Payload/Telegram.app/_CodeSignature/CodeResources': b'seal'}):
            with self.subTest(extra=list(extra)), tempfile.TemporaryDirectory() as d:
                source = ipa(Path(d) / 'device.ipa', extra)
                with self.assertRaises(replay.ReplayError): replay.unsigned_device_inspection(source)

    def test_only_main_plugins_are_removed_all_retained_bytes_and_metadata_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = ipa(root / 'source.ipa'); before = source.read_bytes()
            report = variant.strip_extensions(source, root / 'variant.ipa', 6)
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(report['extensions_removed'], 6)
            self.assertEqual(report['main_executable']['unsigned_macho_count'], 2)
            self.assertTrue(report['retained_member_bytes_unchanged'])
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(root / 'variant.ipa') as changed:
                self.assertNotIn('Payload/Telegram.app/PlugIns/Extension0.appex/Info.plist', changed.namelist())
                self.assertIn('Payload/Telegram.app/Other/PlugIns/keep.txt', changed.namelist())
                for name in changed.namelist():
                    self.assertEqual(original.read(name), changed.read(name))
                    self.assertEqual(variant.metadata(original.getinfo(name)), variant.metadata(changed.getinfo(name)))

    def test_boundary_count_or_unexpected_plugins_content_fails_before_output(self):
        for count, extra in ((5, None), (6, {'Payload/Telegram.app/PlugIns/unexpected.txt': b'cannot remove arbitrarily'})):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d); source = ipa(root / 'source.ipa', extra)
                with self.assertRaisesRegex(replay.ReplayError, 'extension_boundary_mismatch'):
                    variant.strip_extensions(source, root / 'variant.ipa', count)
                self.assertFalse((root / 'variant.ipa').exists())

    def test_device_variant_cannot_relabel_the_observed_simulator_model(self):
        values = evidence()
        profile = {'simulator_device_type': 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro',
                   'simulator_runtime': 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'}
        values[2].update(profile); values[3].update(profile)
        self.assertEqual(variant.validate_startup(*values), (SOURCE, RUN, 6))
        for kind in ('proof_missing', 'result_missing', 'different_model', 'invalid_model', 'different_runtime'):
            changed = copy.deepcopy(values); proof, result = changed[2:]
            if kind == 'proof_missing': proof.pop('simulator_device_type')
            elif kind == 'result_missing': result.pop('simulator_runtime')
            elif kind == 'different_model': proof['simulator_device_type'] = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17'
            elif kind == 'invalid_model': proof['simulator_device_type'] = result['simulator_device_type'] = 'unknown-model'
            else: proof['simulator_runtime'] = 'com.apple.CoreSimulator.SimRuntime.iOS-26-0'
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError):
                variant.validate_startup(*changed)

    def test_gate_rejects_failure_wrong_source_missing_launch_private_storage_or_blank_ui(self):
        device, simulator, proof, result = evidence()
        self.assertEqual(variant.validate_startup(device, simulator, proof, result), (SOURCE, RUN, 6))
        for kind in ('failed', 'source', 'digest', 'codesign', 'main_code', 'one_launch', 'storage', 'blank', 'stage', 'unchecked_screenshot_tool'):
            values = copy.deepcopy((device, simulator, proof, result)); dv, sm, pr, rs = values
            if kind == 'failed': pr['passed'] = False
            if kind == 'source': sm['source_commit'] = 'b' * 40
            if kind == 'digest': pr['source_simulator_ipa_sha256'] = 'b' * 64
            if kind == 'codesign': pr['source_codesign_verified'] = False
            if kind == 'main_code': pr['main_code_unchanged'] = False
            if kind == 'one_launch': rs['launches'].pop()
            if kind == 'storage': rs['launches'][1]['storage_kind'] = 'group'
            if kind == 'blank': rs['launches'][0]['screenshot']['nonblank'] = False
            if kind == 'stage': rs['launches'][0]['marker']['stage'] = 'storageReady'
            if kind == 'unchecked_screenshot_tool': rs.pop('screenshot_checker')
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): variant.validate_startup(*values)

    def test_seeded_private_control_is_explicit_and_requires_real_private_store_on_both_launches(self):
        device, simulator, proof, result = evidence()
        proof.update(private_mode_control='freshPinnedSelection', automatic_fallback_verified=False,
                     pinned_private_control_attempted=True, automatic_group_selection_observed=True)
        result.update(storage_test_setup='freshPinnedPrivate', private_selection_seeded_before_launch=True,
                      private_account_store_absent_before_launch=True)
        self.assertEqual(variant.validate_startup(device, simulator, proof, result), (SOURCE, RUN, 6))
        for kind in ('claimed_automatic', 'not_seeded', 'preexisting_store', 'missing_actual_store', 'replaced_store', 'no_group_observation'):
            values = copy.deepcopy((device, simulator, proof, result)); _, _, pr, rs = values
            if kind == 'claimed_automatic': pr['automatic_fallback_verified'] = True
            if kind == 'not_seeded': rs['private_selection_seeded_before_launch'] = False
            if kind == 'preexisting_store': rs['private_account_store_absent_before_launch'] = False
            if kind == 'missing_actual_store': rs['launches'][1]['private_account_store_present'] = False
            if kind == 'replaced_store': rs['launches'][1]['private_account_store_stable_after_restart'] = False
            if kind == 'no_group_observation': pr['automatic_group_selection_observed'] = False
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): variant.validate_startup(*values)

    def test_standalone_private_control_does_not_claim_an_automatic_group_observation(self):
        device, simulator, proof, result = evidence()
        proof.update(private_mode_control='explicitFreshPinnedSelection', private_control_only=True,
                     automatic_control_run=False, automatic_fallback_verified=False,
                     pinned_private_control_attempted=True, automatic_group_selection_observed=False)
        result.update(storage_test_setup='freshPinnedPrivate', private_selection_seeded_before_launch=True,
                      private_account_store_absent_before_launch=True)
        self.assertEqual(variant.validate_startup(device, simulator, proof, result), (SOURCE, RUN, 6))
        for kind in ('automatic_run', 'automatic_observation', 'automatic_fallback', 'wrong_flag', 'missing_store', 'replaced_store'):
            values = copy.deepcopy((device, simulator, proof, result)); _, _, pr, rs = values
            if kind == 'automatic_run': pr['automatic_control_run'] = True
            if kind == 'automatic_observation': pr['automatic_group_selection_observed'] = True
            if kind == 'automatic_fallback': pr['automatic_fallback_verified'] = True
            if kind == 'wrong_flag': pr['private_control_only'] = False
            if kind == 'missing_store': rs['launches'][0]['private_account_store_present'] = False
            if kind == 'replaced_store': rs['launches'][1]['private_account_store_stable_after_restart'] = False
            with self.subTest(kind=kind), self.assertRaises(replay.ReplayError): variant.validate_startup(*values)

    def test_package_is_atomic_distinct_unsigned_and_published_only_after_startup_passes(self):
        with tempfile.TemporaryDirectory() as d:
            # macOS /var is an alias of /private/var. Use the canonical fixture
            # root, keeping production's symlink-ancestor rejection intact.
            root = Path(d).resolve(); source = ipa(root / 'source.ipa')
            reports = evidence(variant.digest(source)); paths = []
            for n, value in enumerate(reports):
                path = root / (str(n) + '.json'); path.write_text(json.dumps(value)); paths.append(path)
            bad = copy.deepcopy(reports[2]); bad['passed'] = False
            paths[2].write_text(json.dumps(bad))
            with self.assertRaises(replay.ReplayError): variant.package(source, *paths, root / 'blocked')
            self.assertFalse((root / 'blocked').exists())
            paths[2].write_text(json.dumps(reports[2]))
            materials = artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], SOURCE)
            artifact_licensing.write_materials(materials, root)
            report = variant.package(source, *paths, root / 'published')
            self.assertIn('main-app-only-unsigned-device.ipa', report['filename'])
            self.assertEqual(report['signing'], 'disabled')
            self.assertFalse(report['physical_device_verified'])
            self.assertFalse(report['installable_signed_ipa'])
            self.assertFalse(report['runtime_verified'])
            self.assertTrue(report['simulator_variant_startup_verified'])
            self.assertEqual(variant.digest(source), report['source_ipa_sha256'])
            sums = (root / 'published/SHA256SUMS').read_text()
            self.assertIn(variant.digest(root / 'published' / report['filename']), sums)
            self.assertIn(variant.digest(root / 'published/manifest.json'), sums)
            for name, value in materials.items():
                self.assertEqual((root / 'published' / name).read_bytes(), value)
                self.assertIn(hashlib.sha256(value).hexdigest() + '  ' + name, sums)
            # Exercise the real workflow's upload glob against the packaged
            # files, including a canary that must never accompany the IPA.
            (root / 'published/private-diagnostic.log').write_text('not for distribution')
            workflow = (Path(__file__).resolve().parents[3] / '.github/workflows/archive-integration.yml').read_text()
            step = re.search(r'(?ms)^      - name: Publish separate unsigned main-app-only device package\n(.*?)(?=^      - name: |\Z)', workflow)
            self.assertIsNotNone(step)
            prefix = '${{ github.workspace }}/fork-source/build/fork-evidence/startup-main-app-only-device/'
            patterns = [line.strip().removeprefix(prefix) for line in step.group(1).splitlines()
                        if line.strip().startswith(prefix)]
            uploaded = {path.name: path.read_bytes() for pattern in patterns
                        for path in (root / 'published').glob(pattern) if path.is_file()}
            checksums = dict(line.split('  ', 1)[::-1] for line in sums.splitlines())
            self.assertEqual(set(uploaded), {'SHA256SUMS', *checksums})
            self.assertNotIn('private-diagnostic.log', uploaded)
            for name, digest in checksums.items():
                self.assertEqual(hashlib.sha256(uploaded[name]).hexdigest(), digest)

    def test_output_symlink_or_symlink_ancestor_is_rejected_without_publishing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); source = ipa(root / 'source.ipa')
            reports = evidence(variant.digest(source)); paths = []
            for n, value in enumerate(reports):
                path = root / (str(n) + '.json'); path.write_text(json.dumps(value)); paths.append(path)
            target = root / 'actual'; target.mkdir()
            alias = root / 'alias'; alias.symlink_to(target, target_is_directory=True)
            for output in (alias, alias / 'published'):
                with self.subTest(ancestor=output != alias), self.assertRaisesRegex(replay.ReplayError, 'invalid_variant_output'):
                    variant.package(source, *paths, output)
            self.assertEqual(list(target.iterdir()), [])

    def test_new_distribution_rejects_missing_partial_symlink_tampered_or_unverified_source_materials(self):
        for kind in ('missing', 'partial', 'symlink', 'tampered', 'legacy-proof', 'wrong-proof'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); source = ipa(root / 'source.ipa'); before = source.read_bytes()
                reports = evidence(variant.digest(source)); paths = []
                if kind != 'missing':
                    materials = artifact_licensing.material_bytes(Path(__file__).resolve().parents[3], SOURCE)
                    artifact_licensing.write_materials(materials, root)
                    path = root / 'LICENSE-NOTICE.md'
                    if kind == 'partial': path.unlink()
                    if kind == 'symlink': path.rename(root / 'private-notice'); path.symlink_to(root / 'private-notice')
                    if kind == 'tampered': (root / 'COPYING').write_bytes(b'not the verified license')
                    if kind == 'legacy-proof': reports[0]['source_license_materials_present'] = False
                    if kind == 'wrong-proof': reports[0]['license_materials']['COPYING']['sha256'] = '0' * 64
                for index, value in enumerate(reports):
                    path = root / (str(index) + '.json'); path.write_text(json.dumps(value)); paths.append(path)
                with self.assertRaises(replay.ReplayError): variant.package(source, *paths, root / 'blocked')
                self.assertFalse((root / 'blocked').exists())
                self.assertEqual(list(root.glob('.main-app-only-*')), [])
                self.assertEqual(source.read_bytes(), before)
