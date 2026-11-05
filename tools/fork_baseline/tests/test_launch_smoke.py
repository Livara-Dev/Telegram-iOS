import io
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
from ipa_inspect import InvalidIPA
from launch_smoke import extract_app, read_stage


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
