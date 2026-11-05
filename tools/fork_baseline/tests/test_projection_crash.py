"""Raw crash reports must never escape the closed synthetic-host summary."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import projection_crash as crash


def fixture(**changes):
    value = {
        'procName': crash.PROCESS,
        'bundleInfo': {'CFBundleIdentifier': crash.BUNDLE},
        'exception': {'type': 'EXC_CRASH', 'signal': 'SIGABRT', 'rawCodes': [123456789]},
        'termination': {'namespace': 'DYLD', 'code': 1, 'indicator': 'Library missing',
                        'details': ['Library not loaded: /private/user-secret/SQLCipher.framework/SQLCipher',
                                    'Referenced from: /private/user-secret/ProjectionTestHost',
                                    'Symbol not found: _OBJC_CLASS_$_XCTestCase']},
        'pid': 123456789, 'incident': '1234-private-uuid', 'procPath': '/private/user-secret/app',
        'environment': {'GH_TOKEN': 'secret-fixture-token'},
    }
    value.update(changes)
    return value


class ProjectionCrashTests(unittest.TestCase):
    def test_metadata_plus_report_is_closed_and_retains_only_safe_dyld_names(self):
        data = (json.dumps({'app_name': crash.PROCESS, 'bundleID': crash.BUNDLE, 'timestamp': 'private-time'}) + '\n' + json.dumps(fixture())).encode()
        value = crash.summarize(data)
        self.assertEqual(value, {
            'synthetic_host_identity_verified': True, 'exception_type': 'EXC_CRASH',
            'signal': 'SIGABRT', 'termination_namespace': 'DYLD', 'termination_code': 1,
            'termination_indicator': 'missingLibrary', 'missing_library': 'SQLCipher',
            'missing_symbol': '_OBJC_CLASS_$_XCTestCase',
        })
        text = json.dumps(value)
        for secret in ('user-secret', 'secret-fixture-token', '123456789', 'private-uuid', 'private-time', 'GH_TOKEN', 'procPath'):
            self.assertNotIn(secret, text)

    def test_identity_must_match_without_contradictory_metadata(self):
        for value in (fixture(procName='Telegram'), fixture(bundleInfo={'CFBundleIdentifier': 'other.app'}), {}, {'termination': {'namespace': 'DYLD'}}):
            with self.subTest(value=value):
                self.assertIsNone(crash.summarize(json.dumps(value).encode()))
        self.assertIsNone(crash.summarize((json.dumps({'app_name': 'OtherHost'}) + '\n' + json.dumps(fixture())).encode()))
        self.assertIsNotNone(crash.summarize(json.dumps({'procName': crash.PROCESS}).encode()))

    def test_unknown_fields_and_unsafe_dyld_strings_are_omitted(self):
        value = fixture(exception={'type': 'secret-fixture-token', 'signal': '/private/path'},
                        termination={'namespace': 'DYLD', 'code': True, 'indicator': 'unknown secret',
                                     'details': ['Library not loaded: /private/data.db',
                                                 'Library not loaded: /private/Bad.framework/Other',
                                                 'Symbol not found: /private/secret',
                                                 'Symbol not found: _Safe secret-fixture-token']})
        self.assertEqual(crash.summarize(json.dumps(value).encode()), {'synthetic_host_identity_verified': True, 'termination_namespace': 'DYLD'})
        for unsafe in ('secret-fixture-token', 'DYLD /private/path', ['DYLD']):
            result = crash.summarize(json.dumps(fixture(termination={'namespace': unsafe, 'code': 1})).encode())
            self.assertNotIn('termination_namespace', result)
            self.assertNotIn('termination_code', result)

    def test_library_basename_and_symbol_are_read_only_from_dyld_fields(self):
        value = fixture(termination={'namespace': 'DYLD', 'code': 4},
                        asi={'dyld': ['Library not loaded: @rpath/libswiftXCTest.dylib\nSymbol not found: _$sKnownSymbol'],
                             'private': ['Symbol not found: _PrivateUserData']},
                        randomProse='Symbol not found: _PrivateUserData')
        result = crash.summarize(json.dumps(value).encode())
        self.assertEqual(result['missing_library'], 'libswiftXCTest.dylib')
        self.assertEqual(result['missing_symbol'], '_$sKnownSymbol')
        self.assertNotIn('PrivateUserData', json.dumps(result))

    def test_malformed_streams_are_ignored(self):
        for data in (b'', b'not-json', b'[]', b'{} {} {}', b'\xff', b'{"procName": 7}'):
            with self.subTest(data=data):
                self.assertIsNone(crash.summarize(data))

    def test_capture_filters_stale_unrelated_unsafe_large_and_malformed_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / 'current.ips'; current.write_text(json.dumps(fixture()))
            stale = root / 'stale.ips'; stale.write_text(json.dumps(fixture()))
            os.utime(current, (200, 200)); os.utime(stale, (50, 50))
            (root / 'other.ips').write_text(json.dumps(fixture(procName='OtherHost')))
            (root / 'link.ips').symlink_to(current)
            (root / 'large.ips').write_bytes(b'x' * (crash.MAX_FILE_BYTES + 1))
            (root / 'invalid.ips').write_text('not-json')
            (root / 'directory.ips').mkdir()
            value = crash.capture(100, root)
            self.assertEqual(value['state'], 'available')
            self.assertEqual(len(value['reports']), 1)
            text = json.dumps(value)
            self.assertNotIn(str(root), text)
            self.assertNotIn('current.ips', text)
            self.assertNotIn('secret-fixture-token', text)

    def test_capture_is_bounded_and_handles_missing_or_symlinked_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(8):
                (root / f'{index}.ips').write_text(json.dumps(fixture()))
            self.assertEqual(len(crash.capture(0, root)['reports']), crash.MAX_REPORTS)
            self.assertEqual(crash.capture(0, root / 'missing')['state'], 'unavailable')
            link = root / 'alias'; link.symlink_to(root, target_is_directory=True)
            self.assertEqual(crash.capture(0, link)['state'], 'unavailable')
            for invalid in (-1, float('nan'), True, '100'):
                self.assertEqual(crash.capture(invalid, root)['state'], 'unavailable')
