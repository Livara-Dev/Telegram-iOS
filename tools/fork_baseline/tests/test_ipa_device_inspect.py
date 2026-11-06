# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Exercise unsigned nested binaries and actual loader-relative ZIP closure."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ipa_device_inspect as device
from ipa_inspect import InvalidIPA

BUNDLE = 'org.livara.ayugram'
ROOT = 'Payload/Telegram.app'
A = ROOT + '/Frameworks/A.framework'
B = A + '/SubFrameworks/B.framework'


def string_command(code, value, minimum=24):
    data = value.encode() + b'\0'
    length = (minimum + len(data) + 7) // 8 * 8
    return struct.pack('<III', code, length, minimum) + b'\0' * (minimum - 12) + data + b'\0' * (length - minimum - len(data))


def macho(kind=2, platform=2, cpu=0x0100000C, rpaths=(), loads=(), commands=()):
    values = [struct.pack('<6I', 0x32, 24, platform, 0xF0000, 0x1B0100, 0)]
    values += [string_command(0x8000001C, path, 12) for path in rpaths]
    for item in loads:
        code, name = item if isinstance(item, tuple) else (0xC, item)
        values.append(string_command(code, name))
    values += list(commands)
    payload = b''.join(values)
    return struct.pack('<8I', 0xFEEDFACF, cpu, 0, kind, len(values), len(payload), 0, 0) + payload


def info(executable, identifier='fixture.framework'):
    return plistlib.dumps({'CFBundleIdentifier': identifier, 'CFBundleExecutable': executable,
                          'CFBundleSupportedPlatforms': ['iPhoneOS']})


def files():
    return {ROOT + '/Info.plist': info('Telegram', BUNDLE),
            ROOT + '/Telegram': macho(rpaths=('@executable_path/Frameworks',), loads=('@rpath/A.framework/A',)),
            A + '/Info.plist': info('A'), A + '/A': macho(6, rpaths=('@loader_path/SubFrameworks',), loads=('@rpath/B.framework/B',)),
            B + '/Info.plist': info('B'), B + '/B': macho(6, loads=('/System/Library/Frameworks/UIKit.framework/UIKit', '/usr/lib/libSystem.B.dylib')),
            ROOT + '/Resource.bundle/Info.plist': plistlib.dumps({'CFBundleName': 'DataOnly'}),
            ROOT + '/Resource.bundle/data': b'not executable private resource sentinel'}


def archive(path, values=None, extra=()):
    with warnings.catch_warnings(), zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
        warnings.simplefilter('ignore', UserWarning)
        for name, data in list((files() if values is None else values).items()) + list(extra):
            entry = zipfile.ZipInfo(name); entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            zipped.writestr(entry, data)
    return path


class UnsignedDeviceInspectionTests(unittest.TestCase):
    def inspect(self, values=None, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            path = archive(Path(directory) / 'device.ipa', values, extra)
            return device.inspect_unsigned_device(path, BUNDLE)

    def test_real_loader_relative_and_inherited_run_paths_resolve_without_extracting_or_mutating(self):
        with tempfile.TemporaryDirectory() as directory:
            path = archive(Path(directory) / 'device.ipa'); before = hashlib.sha256(path.read_bytes()).digest()
            with patch.object(zipfile.ZipFile, 'extract', side_effect=AssertionError('must not extract')), patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('must not extract')):
                result = device.inspect_unsigned_device(path, BUNDLE)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
            self.assertEqual(result['unsigned_macho_count'], 3); self.assertEqual(result['framework_count'], 2)
            self.assertEqual(result['bundle_executables_checked'], 3); self.assertEqual(result['executables_checked'], 1)
            self.assertEqual(result['bundled_load_edges_checked'], 2); self.assertTrue(result['all_bundled_dependencies_resolved'])
            self.assertEqual(result['all_macho_platforms'], 2); self.assertEqual(result['signing'], 'disabled')
            for flag in ('runtime_verified', 'signature_verified', 'physical_device_verified', 'dynamic_loading_verified', 'system_library_availability_verified'):
                self.assertFalse(result[flag])
            self.assertNotIn('private resource sentinel', json.dumps(result))
        value = files(); value[B + '/B'] = macho(6)
        destination = ROOT + '/Frameworks/B.framework'
        value[destination + '/Info.plist'] = value.pop(B + '/Info.plist'); value[destination + '/B'] = value.pop(B + '/B')
        value[A + '/A'] = macho(6, loads=('@rpath/B.framework/B',))
        self.assertEqual(self.inspect(value)['unsigned_macho_count'], 3, 'dependency must inherit the real executable run-path')

    def test_each_extension_uses_its_own_executable_path_and_can_share_app_frameworks_explicitly(self):
        value = files(); extension = ROOT + '/PlugIns/Share.appex'
        value[extension + '/Info.plist'] = info('Share', 'fixture.share')
        value[extension + '/Share'] = macho(rpaths=('@executable_path/../../Frameworks',), loads=('@rpath/A.framework/A',))
        result = self.inspect(value)
        self.assertEqual(result['executables_checked'], 2); self.assertEqual(result['unsigned_macho_count'], 4)
        value[extension + '/Share'] = macho(rpaths=('@executable_path/Frameworks',), loads=('@rpath/A.framework/A',))
        with self.assertRaisesRegex(InvalidIPA, 'Missing or invalid bundled dynamic dependency'): self.inspect(value)

    def test_nested_simulator_wrong_cpu_fat_signed_or_malformed_image_rejected(self):
        for bad in [macho(6, platform=7), macho(6, cpu=7), b'\xca\xfe\xba\xbe' + b'\0' * 60,
                    macho(6, commands=(struct.pack('<4I', 0x1D, 16, 0, 0),)), macho(6)[:-1], macho(kind=1)]:
            with self.subTest(binary=bad[:12]):
                value = files(); value[B + '/B'] = bad
                with self.assertRaises(InvalidIPA): self.inspect(value)
        value = files(); value[ROOT + '/hidden.dat'] = macho(platform=7)
        with self.assertRaises(InvalidIPA): self.inspect(value)

    def test_any_signature_profile_or_symlink_including_nested_members_is_rejected(self):
        for name in [ROOT + '/_CodeSignature/CodeResources', B + '/_CodeSignature/CodeResources',
                     ROOT + '/embedded.mobileprovision', B + '/embedded.provisionprofile',
                     ROOT + '/_codesignature/CodeResources', B + '/_CODESIGNATURE/CodeResources',
                     ROOT + '/Embedded.MobileProvision', B + '/EMBEDDED.PROVISIONPROFILE']:
            with self.subTest(name=name), self.assertRaises(InvalidIPA): self.inspect(extra=[(name, b'signing material')])
        with tempfile.TemporaryDirectory() as directory:
            path = archive(Path(directory) / 'device.ipa')
            with zipfile.ZipFile(path, 'a') as zipped:
                entry = zipfile.ZipInfo(ROOT + '/linked'); entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                zipped.writestr(entry, 'other resource')
            with self.assertRaises(InvalidIPA): device.inspect_unsigned_device(path, BUNDLE)
            link = Path(directory) / 'source-link.ipa'; link.symlink_to(path)
            with self.assertRaises(InvalidIPA): device.inspect_unsigned_device(link, BUNDLE)
            fifo = Path(directory) / 'source-fifo'; os.mkfifo(fifo)
            with self.assertRaises(InvalidIPA): device.inspect_unsigned_device(fifo, BUNDLE)

    def test_every_declared_executable_must_exist_and_be_correct_kind_of_macho(self):
        for mode in ('missing', 'notMacho', 'wrongKind', 'frameworkMissingName', 'resourceBundleMissingBinary', 'unsafeName'):
            with self.subTest(mode=mode):
                value = files()
                if mode == 'missing': value.pop(B + '/B')
                elif mode == 'notMacho': value[B + '/B'] = b'not code'
                elif mode == 'wrongKind': value[B + '/B'] = macho(kind=2)
                elif mode == 'frameworkMissingName': value[B + '/Info.plist'] = plistlib.dumps({'CFBundleName': 'B'})
                elif mode == 'resourceBundleMissingBinary': value[ROOT + '/Resource.bundle/Info.plist'] = info('Missing')
                else: value[B + '/Info.plist'] = info('../other')
                with self.assertRaises(InvalidIPA): self.inspect(value)

    def test_code_bearing_app_extension_and_framework_require_real_bundle_metadata(self):
        value = files(); value.pop(B + '/Info.plist')
        with self.assertRaisesRegex(InvalidIPA, 'Executable bundle metadata is missing'): self.inspect(value)
        for suffix in ('.app', '.appex', '.FRAMEWORK'):
            with self.subTest(suffix=suffix):
                value = files(); value[ROOT + '/Missing' + suffix + '/Executable'] = macho(6 if suffix.casefold() == '.framework' else 2)
                with self.assertRaisesRegex(InvalidIPA, 'Executable bundle metadata is missing'): self.inspect(value)

    def test_referenced_mh_bundle_without_metadata_cannot_satisfy_a_dylib_load(self):
        value = files(); value.pop(B + '/Info.plist'); value[B + '/B'] = macho(kind=8)
        with self.assertRaises(InvalidIPA): self.inspect(value)
        # The type regression must also fail when no framework metadata rule
        # masks it: MH_BUNDLE is valid code, but is not an LC_LOAD_* dylib.
        value = {ROOT + '/Info.plist': info('Telegram', BUNDLE),
                 ROOT + '/Telegram': macho(loads=('@loader_path/Data.bundle/Plugin',)),
                 ROOT + '/Data.bundle/Plugin': macho(kind=8)}
        with self.assertRaisesRegex(InvalidIPA, 'Missing or invalid bundled dynamic dependency'): self.inspect(value)

    def test_missing_local_dependencies_never_pass_via_basename_system_rpath_or_weak_link(self):
        for mode in ('absent', 'otherDirectory', 'systemRpath', 'weak'):
            with self.subTest(mode=mode):
                value = files(); value.pop(B + '/Info.plist'); value.pop(B + '/B')
                if mode == 'otherDirectory':
                    value[ROOT + '/Unrelated/B.framework/Info.plist'] = info('B'); value[ROOT + '/Unrelated/B.framework/B'] = macho(6)
                elif mode == 'systemRpath': value[A + '/A'] = macho(6, rpaths=('/usr/lib/swift',), loads=('@rpath/B.framework/B',))
                elif mode == 'weak': value[A + '/A'] = macho(6, rpaths=('@loader_path/SubFrameworks',), loads=((0x80000018, '@rpath/B.framework/B'),))
                with self.assertRaisesRegex(InvalidIPA, 'Missing or invalid bundled dynamic dependency'): self.inspect(value)

    def test_direct_loader_paths_reexport_upward_and_lazy_dependencies_are_all_checked(self):
        for code in device.LOAD_COMMANDS:
            with self.subTest(code=code):
                value = files(); value[A + '/A'] = macho(6, loads=((code, '@loader_path/SubFrameworks/B.framework/B'),))
                self.assertTrue(self.inspect(value)['all_bundled_dependencies_resolved'])
                value[A + '/A'] = macho(6, loads=((code, '@loader_path/Missing.framework/Missing'),))
                with self.assertRaises(InvalidIPA): self.inspect(value)

    def test_apple_absolute_libraries_are_structural_only_and_path_escapes_fail(self):
        value = files(); value[B + '/B'] = macho(6, loads=((0x80000018, '/System/Library/Frameworks/Unknown.framework/Unknown'),))
        self.assertFalse(self.inspect(value)['system_library_availability_verified'])
        for dependency in ('/tmp/private-framework', '/System/Library/../../private-framework',
                           '@loader_path/../../../../../../outside', '@rpath/../../../../../../outside',
                           'B.framework/B', '@unknown/B', '@rpath//B.framework/B'):
            with self.subTest(dependency=dependency):
                value = files(); value[B + '/B'] = macho(6, loads=(dependency,))
                with self.assertRaises(InvalidIPA): self.inspect(value)
        for path in ('/private/build-frameworks', '@loader_path/../../../../../../outside', '@rpath/unsupported'):
            with self.subTest(path=path):
                value = files(); value[B + '/B'] = macho(6, rpaths=(path,))
                with self.assertRaises(InvalidIPA): self.inspect(value)

    def test_zip_unsafe_alias_duplicate_and_file_parent_conflicts_are_rejected(self):
        for name in [ROOT + '/../escape', ROOT + '//double', ROOT + '/./same', '/absolute', ROOT + '/back\\slash',
                     ROOT + '/bad\x01name', ROOT + '/Info.plist', ROOT + '/info.PLIST']:
            with self.subTest(name=name), self.assertRaises(InvalidIPA): self.inspect(extra=[(name, b'private canary')])
        with self.assertRaises(InvalidIPA): self.inspect(extra=[(ROOT + '/é', b'one'), (ROOT + '/e\u0301', b'two')])
        with self.assertRaises(InvalidIPA): self.inspect(extra=[(ROOT + '/Conflict', b'file'), (ROOT + '/Conflict/child', b'data')])

    def test_orphan_library_has_no_invented_loading_context_and_dependency_cycles_are_bounded(self):
        value = files(); value[ROOT + '/Orphan.dylib'] = macho(6)
        with self.assertRaisesRegex(InvalidIPA, 'no verified executable loading context'): self.inspect(value)
        value = files(); value[B + '/B'] = macho(6, loads=('@loader_path/../../A',))
        self.assertEqual(self.inspect(value)['unsigned_macho_count'], 3)

    def test_archive_metadata_image_and_closure_budgets_fail_closed(self):
        value = files()
        for constant, limit in [('MAX_MEMBERS', len(value) - 1), ('MAX_TOTAL_BYTES', 1), ('MAX_FILE_BYTES', 1),
                                ('MAX_PLIST_BYTES', 1), ('MAX_IMAGES', 2), ('MAX_COMMAND_BYTES', 24),
                                ('MAX_RPATHS', 1), ('MAX_STATES', 1), ('MAX_EDGES', 1), ('MAX_LOAD_DEPTH', 1),
                                ('MAX_PATH_BYTES', 12), ('MAX_PATH_DEPTH', 2), ('MAX_ARCHIVE_BYTES', 1)]:
            with self.subTest(constant=constant), patch.object(device, constant, limit), self.assertRaises(InvalidIPA): self.inspect(value)
        value = files(); value[B + '/B'] = macho(6, loads=('/usr/lib/libSystem.B.dylib',) * 8)
        with patch.object(device, 'MAX_EDGES', 5), self.assertRaises(InvalidIPA):
            self.inspect(value)  # Absolute system loads must share the work cap.

    def test_invalid_command_sizes_offsets_platforms_and_unterminated_strings_are_rejected(self):
        bad_length = bytearray(macho(6)); struct.pack_into('<I', bad_length, 36, 23)
        count_overflow = bytearray(macho(6)); struct.pack_into('<I', count_overflow, 16, 4097)
        bad_offset = bytearray(string_command(0xC, '@loader_path/B')); struct.pack_into('<I', bad_offset, 8, 12)
        unterminated = struct.pack('<III', 0xC, 32, 24) + b'\0' * 12 + b'ABCDEFGH'
        for binary in [bytes(bad_length), bytes(count_overflow), macho(6, commands=(bytes(bad_offset),)),
                       macho(6, commands=(unterminated,)), macho(6, commands=(struct.pack('<6I', 0x32, 24, 2, 0, 0, 0),))]:
            with self.subTest(binary=binary[:36]):
                value = files(); value[B + '/B'] = binary
                with self.assertRaises(InvalidIPA): self.inspect(value)

    def test_errors_are_closed_and_never_include_private_paths_or_member_names(self):
        with tempfile.TemporaryDirectory(prefix='private-ipa-canary-') as directory:
            path = archive(Path(directory) / 'private-artifact-canary.ipa', extra=[(ROOT + '/private-member-canary/../escape', b'data')])
            with self.assertRaises(InvalidIPA) as raised: device.inspect_unsigned_device(path, BUNDLE)
            text = str(raised.exception)
            for private in ('private-ipa-canary', 'private-artifact-canary', 'private-member-canary', str(path)):
                self.assertNotIn(private, text)
            value = files(); value[ROOT + '/Info.plist'] = info('private-executable-canary', BUNDLE)
            archive(path, value)
            try:
                device.inspect_unsigned_device(path, BUNDLE)
                self.fail('missing declared main executable must fail')
            except InvalidIPA as error:
                self.assertIsNone(error.__cause__)
                self.assertNotIn('private-executable-canary', ''.join(traceback.format_exception(error)))

    def test_corrupt_main_deflate_is_a_closed_invalid_ipa_without_raw_codec_traceback(self):
        with tempfile.TemporaryDirectory(prefix='private-codec-canary-') as directory:
            path = archive(Path(directory) / 'private-codec-canary.ipa')
            with zipfile.ZipFile(path) as zipped: entry = zipped.getinfo(ROOT + '/Info.plist')
            data = bytearray(path.read_bytes()); start = entry.header_offset
            name_length, extra_length = struct.unpack_from('<HH', data, start + 26)
            start += 30 + name_length + extra_length
            data[start:start + 4] = b'\xff' * 4
            path.write_bytes(data)
            try:
                device.inspect_unsigned_device(path, BUNDLE)
                self.fail('malformed compressed metadata must fail')
            except InvalidIPA as error:
                self.assertEqual(str(error), 'Unreadable unsigned device archive')
                self.assertIsNone(error.__cause__)
                text = ''.join(traceback.format_exception(error))
                self.assertNotIn('private-codec-canary', text); self.assertNotIn('zlib.error', text)

    def test_unsupported_codec_is_rejected_before_any_main_metadata_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unsupported.ipa'
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_BZIP2) as zipped:
                for name, data in files().items(): zipped.writestr(name, data)
            with patch.object(device, 'inspect_ipa', side_effect=AssertionError('must reject before main read')) as main:
                with self.assertRaisesRegex(InvalidIPA, 'Unsupported device archive compression'):
                    device.inspect_unsigned_device(path, BUNDLE)
                main.assert_not_called()

    def test_malformed_main_and_nested_xml_plists_have_closed_errors_without_raw_decoder_tracebacks(self):
        for member in (ROOT + '/Info.plist', B + '/Info.plist'):
            with self.subTest(member=member), tempfile.TemporaryDirectory(prefix='private-xml-canary-') as directory:
                value = files()
                value[member] = b'<?xml version="1.0"?><plist><dict><key>private-xml-member-canary</key>'
                path = archive(Path(directory) / 'private-xml-file-canary.ipa', value)
                try:
                    device.inspect_unsigned_device(path, BUNDLE)
                    self.fail('malformed XML bundle metadata must fail')
                except InvalidIPA as error:
                    self.assertEqual(str(error), 'Unreadable unsigned device archive')
                    self.assertIsNone(error.__cause__)
                    text = ''.join(traceback.format_exception(error))
                    for private in ('private-xml-canary', 'private-xml-member-canary', 'private-xml-file-canary', 'ExpatError'):
                        self.assertNotIn(private, text)

    def test_out_of_range_binary_plist_date_is_a_closed_archive_error(self):
        for member in (ROOT + '/Info.plist', B + '/Info.plist'):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                metadata = plistlib.loads(info('Telegram', BUNDLE) if member.startswith(ROOT + '/Info.plist') else info('B'))
                metadata['private-date-canary'] = datetime.datetime(2001, 1, 2)
                payload = plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY)
                encoded = b'\x33' + struct.pack('>d', 86400.0)
                self.assertEqual(payload.count(encoded), 1)
                payload = payload.replace(encoded, b'\x33' + struct.pack('>d', 1e300))
                # Python versions may wrap the date decoder's OverflowError in
                # InvalidFileException. Both must remain closed at our API.
                with self.assertRaises((OverflowError, plistlib.InvalidFileException)): plistlib.loads(payload)
                value = files(); value[member] = payload
                path = archive(Path(directory) / 'invalid-date.ipa', value)
                try:
                    device.inspect_unsigned_device(path, BUNDLE)
                    self.fail('unrepresentable metadata date must fail')
                except InvalidIPA as error:
                    self.assertIn(str(error), {'Unreadable unsigned device archive', 'Unreadable application archive'})
                    self.assertIsNone(error.__cause__)
                    text = ''.join(traceback.format_exception(error))
                    self.assertNotIn('private-date-canary', text); self.assertNotIn('OverflowError', text)
