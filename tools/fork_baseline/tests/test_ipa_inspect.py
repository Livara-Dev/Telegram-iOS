import io
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ipa_inspect import inspect_ipa, InvalidIPA


def executable(platform=2):
    return struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0) + struct.pack('<6I', 0x32, 24, platform, 0x000f0000, 0x001b0100, 0)


class IPAInspectionTests(unittest.TestCase):
    def fixture(self, binary=None, platform='iPhoneOS', identity='org.livara.ayugram'):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps({'CFBundleIdentifier': identity, 'CFBundleExecutable': 'Telegram', 'CFBundleSupportedPlatforms': [platform]}))
            archive.writestr('Payload/Telegram.app/Telegram', executable() if binary is None else binary)
        stream.seek(0)
        return stream

    def test_device_and_simulator_distinguished_by_binary_and_metadata(self):
        device = inspect_ipa(self.fixture(), 'unsigned-device', 'org.livara.ayugram')
        self.assertEqual(device['mach_o_platform'], 2)
        self.assertFalse(device['signature_verified'])
        simulator = inspect_ipa(self.fixture(executable(7), 'iPhoneSimulator'), 'simulator', 'org.livara.ayugram')
        self.assertEqual(simulator['mach_o_platform'], 7)
        for fixture in (self.fixture(executable(7)), self.fixture(platform='iPhoneSimulator')):
            with self.assertRaises(InvalidIPA):
                inspect_ipa(fixture, 'unsigned-device', 'org.livara.ayugram')

    def test_wrong_bundle_identity_and_unknown_mode_rejected(self):
        for mode, identity in [('invalid', 'org.livara.ayugram'), ('device', 'org.other.app')]:
            with self.assertRaises(InvalidIPA):
                inspect_ipa(self.fixture(), mode, identity)

    def test_truncation_missing_build_command_and_wrong_cpu_rejected(self):
        wrong_cpu = bytearray(executable()); struct.pack_into('<I', wrong_cpu, 4, 7)
        missing_platform = bytearray(executable()); struct.pack_into('<I', missing_platform, 32, 1)
        for binary in [b'not Mach-O', executable()[:-1], bytes(wrong_cpu), bytes(missing_platform)]:
            with self.assertRaises(InvalidIPA):
                inspect_ipa(self.fixture(binary), 'unsigned-device', 'org.livara.ayugram')

    def test_fat_slice_offset_checked_and_arm64_platform_validated(self):
        header = struct.pack('>7I', 0xcafebabe, 1, 0x0100000c, 0, 32, len(executable()), 3)
        self.assertEqual(inspect_ipa(self.fixture(header + b'\0' * 4 + executable()), 'device', 'org.livara.ayugram')['cpu'], 'arm64')
        with self.assertRaises(InvalidIPA):
            inspect_ipa(self.fixture(header + b'\0' * 4), 'device', 'org.livara.ayugram')

    def test_invalid_load_command_length_rejected(self):
        binary = bytearray(executable()); struct.pack_into('<I', binary, 36, 23)
        with self.assertRaises(InvalidIPA):
            inspect_ipa(self.fixture(binary), 'device', 'org.livara.ayugram')
