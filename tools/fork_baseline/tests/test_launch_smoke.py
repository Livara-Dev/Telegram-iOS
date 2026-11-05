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
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipa_inspect import InvalidIPA
import launch_smoke as launch
from launch_smoke import STORAGE_SELECTION, SmokeError, command, extract_app, read_stage, read_storage_kind


def fixture(platform=7, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        z.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({
            'CFBundleIdentifier': 'org.livara.ayugram', 'CFBundleExecutable': 'Telegram',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator' if platform == 7 else 'iPhoneOS']}))
        binary = struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
        binary += struct.pack('<6I', 0x32, 24, platform, 0x000f0000, 0x001b0100, 0)
        z.writestr('Payload/Telegram.app/Telegram', binary)
        if extra is not None:
            z.writestr(*extra)
    output.seek(0)
    return output


class LaunchSmokeEvidenceTests(unittest.TestCase):
    def test_extraction_preserves_application_and_executable(self):
        with tempfile.TemporaryDirectory() as d:
            app = extract_app(fixture(), Path(d) / 'App.app')
            self.assertTrue((app / 'Telegram').stat().st_mode & 0o100)
            self.assertEqual(plistlib.loads((app / 'Info.plist').read_bytes())['CFBundleIdentifier'], 'org.livara.ayugram')

    def test_rejects_device_binary_before_simulator_install(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(InvalidIPA):
            extract_app(fixture(platform=2), Path(d) / 'App.app')

    def test_rejects_archive_path_escape_and_symlinks(self):
        link = zipfile.ZipInfo('Payload/Telegram.app/link')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in [('Payload/Telegram.app/../escaped', b'data'), (link, b'/etc/passwd')]:
            with self.subTest(extra=str(extra[0])), tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):
                    extract_app(fixture(extra=extra), Path(d) / 'App.app')
                self.assertFalse((Path(d) / 'escaped').exists())

    def test_marker_accepts_closed_schema_without_user_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'marker.json'
            p.write_text(json.dumps({'stage': 'authorizationVisible', 'failure': None}))
            self.assertEqual(read_stage(p)['stage'], 'authorizationVisible')
            p.write_text(json.dumps({'stage': 'authorizationVisible', 'failure': None, 'phone': 'private'}))
            with self.assertRaises(ValueError):
                read_stage(p)

    def test_marker_rejects_symlink_oversize_and_free_form_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'marker.json'
            for content in [' ' * 1025, json.dumps({'stage': '/private/path', 'failure': None})]:
                p.write_text(content)
                with self.assertRaises(ValueError):
                    read_stage(p)
            link = Path(d) / 'link'; link.symlink_to(p)
            with self.assertRaises(ValueError):
                read_stage(link)

    def test_private_storage_evidence_returns_only_closed_kind(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'preferences.plist'
            value = {STORAGE_SELECTION: {'version': 1, 'kind': 'private'}, 'unrelated': 'must-not-be-returned'}
            path.write_bytes(plistlib.dumps(value))
            self.assertEqual(read_storage_kind(path), 'private')

    def test_private_storage_requires_persisted_version_and_no_identifier(self):
        cases = [{}, {STORAGE_SELECTION: {'version': 1, 'kind': 'group', 'identifier': 'group.private'}},
                 {STORAGE_SELECTION: {'version': 1, 'kind': 'private', 'identifier': 'group.private'}},
                 {STORAGE_SELECTION: {'version': 2, 'kind': 'private'}},
                 {STORAGE_SELECTION: {'version': True, 'kind': 'private'}},
                 {STORAGE_SELECTION: {'version': 1, 'kind': 'private', 'unexpected': 'value'}}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'preferences.plist'
            for value in cases:
                path.write_bytes(plistlib.dumps(value))
                with self.subTest(value=value), self.assertRaises(ValueError):
                    read_storage_kind(path)

    def test_storage_evidence_rejects_symlink_and_oversize_preferences(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'preferences.plist'
            path.write_bytes(b'x' * (1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                read_storage_kind(path)
            link = Path(d) / 'link'; link.symlink_to(path)
            with self.assertRaises(ValueError):
                read_storage_kind(link)


class LaunchSmokeFailureEvidenceTests(unittest.TestCase):
    def test_failure_evidence_closes_unknown_fields_and_never_retains_prose(self):
        evidence = SmokeError('/private/detail', '/private/phase', return_code='private',
                              system_errors=[{'domain': '/private/domain', 'code': 1},
                                             {'domain': ['invalid'], 'code': 2}]).evidence()
        self.assertEqual(evidence['code'], 'startupPreparationFailed')
        self.assertEqual(evidence['phase'], 'unknownPhase')
        self.assertIsNone(evidence['return_code'])
        self.assertEqual(evidence['system_errors'], [])
        self.assertNotIn('/private', json.dumps(evidence))

    def test_command_timeout_is_classified_without_raw_arguments(self):
        with self.assertRaises(SmokeError) as caught:
            command([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=0.03, phase='waitForBoot')
        evidence = caught.exception.evidence()
        self.assertEqual(evidence['phase'], 'waitForBoot')
        self.assertTrue(evidence['timeout'])
        self.assertEqual(evidence['code'], 'commandTimedOut')
        self.assertNotIn('time.sleep', json.dumps(evidence))

    def test_nonzero_exit_retains_only_phase_status_and_allowlisted_error_codes(self):
        text = 'private path /secret/value domain=MIInstallerErrorDomain, code=13; domain=private.secret, code=99'
        code = 'import sys; sys.stderr.write(' + repr(text) + '); sys.exit(65)'
        with self.assertRaises(SmokeError) as caught:
            command([sys.executable, '-c', code], phase='installApplication')
        evidence = caught.exception.evidence()
        self.assertEqual(evidence['return_code'], 65)
        self.assertFalse(evidence['timeout'])
        self.assertEqual(evidence['system_errors'], [{'domain': 'MIInstallerErrorDomain', 'code': 13}])
        self.assertNotIn('/secret', json.dumps(evidence))
        self.assertNotIn('private.secret', json.dumps(evidence))

    def test_command_output_is_bounded_and_unknown_domains_are_ignored(self):
        with self.assertRaises(SmokeError) as caught:
            command([sys.executable, '-c', 'import sys; sys.stderr.write("x" * 70000)'], phase='createDevice')
        self.assertEqual(caught.exception.code, 'commandOutputLimit')
        errors = launch.safe_system_errors(b'Error Domain=IXUserPresentableErrorDomain Code=-1 private-data')
        self.assertEqual(errors, [{'domain': 'IXUserPresentableErrorDomain', 'code': -1}])

    def test_prelaunch_timeout_and_install_errors_persist_exact_phase(self):
        for phase, code, status, timed_out in [('createDevice', 'commandTimedOut', None, True),
                                              ('installApplication', 'commandFailed', 65, False)]:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as d:
                output = Path(d) / 'evidence'
                def fake(arguments, timeout=120, phase=None):
                    if phase == 'createDevice':
                        if phase == failing_phase: raise SmokeError(code, phase, return_code=status, timeout=timed_out)
                        return '00000000-0000-0000-0000-000000000001'
                    if phase == failing_phase: raise SmokeError(code, phase, return_code=status, timeout=timed_out)
                    return ''
                failing_phase = phase
                with mock.patch.object(launch.platform, 'system', return_value='Darwin'), \
                     mock.patch.object(launch, 'extract_app', return_value=Path(d) / 'App.app'), \
                     mock.patch.object(launch, 'command', side_effect=fake):
                    with self.assertRaises(SmokeError): launch.smoke(Path('source.ipa'), output)
                value = json.loads((output / 'result.json').read_text())
                self.assertEqual(value['phase'], phase)
                self.assertEqual(value['failure']['code'], code)
                self.assertEqual(value['failure']['return_code'], status)
                self.assertEqual(value['failure']['timeout'], timed_out)
                self.assertFalse(value['passed'])
                self.assertEqual(value['launches'], [])
