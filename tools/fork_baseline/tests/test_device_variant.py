"""Device variants must preserve content and remain gated on real startup evidence."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import stat
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import device_variant as variant
import replay_artifact as replay
from test_replay_artifact import API

SOURCE, RUN = 'c' * 40, 42031063759


def macho(platform=2, signature=False, filetype=2):
    commands = struct.pack('<6I', 0x32, 24, platform, 0x000f0000, 0x001b0100, 0)
    if signature: commands += struct.pack('<4I', 0x1d, 16, 0, 0)
    return struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, filetype, 2 if signature else 1,
                       len(commands), 0, 0) + commands


def ipa(path, extra=None):
    files = {'Payload/Telegram.app/Info.plist': plistlib.dumps({
        'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'Telegram',
        'CFBundleSupportedPlatforms': ['iPhoneOS'], 'CFBundleVersion': '54'}),
        'Payload/Telegram.app/Telegram': macho(),
        'Payload/Telegram.app/Frameworks/Media.framework/Media': macho(filetype=6),
        'Payload/Telegram.app/Frameworks/Media.framework/Info.plist': b'framework metadata',
        'Payload/Telegram.app/Assets.car': b'preserved application resource',
        'Payload/Telegram.app/Other/PlugIns/keep.txt': b'not the main PlugIns directory'}
    for n in range(6):
        base = 'Payload/Telegram.app/PlugIns/Extension' + str(n) + '.appex/'
        files[base + 'Info.plist'] = plistlib.dumps({'CFBundleIdentifier': 'test.extension.' + str(n)})
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
    simulator = {**common, 'mode': 'simulator', 'ipa_sha256': 'a' * 64}
    proof = {'mode': 'main_app_only', 'startup_variant': 'main-app-only', 'passed': True, 'phase': 'complete',
             'main_app_only_control_passed': True,
             'source_codesign_verified': True, 'main_code_unchanged': True, 'source_ipa_unchanged': True,
             'application_recompiled': False, 'physical_device_verified': False, 'extensions_removed': 6,
             'source_simulator_ipa_sha256': 'a' * 64}
    result = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False, 'authentication_codes_used': False,
              'expected_storage_kind': 'private', 'recording_return_code': 0,
              'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
              'passed': True, 'phase': 'complete', 'launches': [
                  {'attempt': n, 'marker': {'stage': 'authorizationVisible', 'failure': None},
                   'screenshot': {'nonblank': True, 'width': 1206, 'height': 2622},
                   'seconds_to_marker': 4, 'storage_kind': 'private'} for n in (1, 2)]}
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
