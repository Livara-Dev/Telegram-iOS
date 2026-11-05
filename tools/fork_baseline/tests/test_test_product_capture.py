"""Only relocatable synthetic Projection products may survive the test runner."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_product_capture as capture


def fixture(root):
    app = root / capture.APP
    test = app / 'PlugIns' / capture.TEST
    test.mkdir(parents=True)
    (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': capture.BUNDLE, 'CFBundleExecutable': 'ProjectionTestHost'}))
    (app / 'ProjectionTestHost').write_bytes(b'synthetic arm64 host executable')
    (app / 'ProjectionTestHost').chmod(0o700)
    (test / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'ChatLocalTextFilterProjectionTests'}))
    (test / 'ChatLocalTextFilterProjectionTests').write_bytes(b'synthetic XCTest executable')
    (test / 'ChatLocalTextFilterProjectionTests').chmod(0o700)
    plan = {'Runner': {'ProductModuleName': 'ChatLocalTextFilterProjectionTests',
                      'TestHostPath': '__TESTROOT__/' + capture.APP,
                      'TestBundlePath': '__TESTROOT__/' + capture.APP + '/PlugIns/' + capture.TEST,
                      'IsAppHostedTestBundle': True,
                      'TestingEnvironmentVariables': {
                          'XCInjectBundleInto': '__TESTHOST__/ProjectionTestHost',
                          'DYLD_FRAMEWORK_PATH': '__TESTROOT__:__PLATFORMS__/iPhoneSimulator.platform/Developer/Library/Frameworks:__PLATFORMS__/iPhoneSimulator.platform/Developer/Library/PrivateFrameworks',
                          'DYLD_INSERT_LIBRARIES': '__PLATFORMS__/iPhoneSimulator.platform/Developer/usr/lib/libXCTestBundleInject.dylib',
                          'DYLD_LIBRARY_PATH': '__TESTROOT__:__PLATFORMS__/iPhoneSimulator.platform/Developer/usr/lib:',
                          'GH_TOKEN': 'secret-fixture-token'},
                      'EnvironmentVariables': {'GH_TOKEN': 'secret-fixture-token', 'XML_OUTPUT_FILE': '/private/account-path', 'DYLD_INSERT_LIBRARIES': ''},
                      'CommandLineArguments': ['secret-fixture-token'], 'UnrelatedPrivateField': '/private/account-path'}}
    (root / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
    (root / 'launch_options.json').write_text('secret-fixture-token')
    return plan


def outputs(root):
    private = root / 'private-bazel'
    logs = private / 'testlogs'; logs.mkdir(parents=True)
    (root / 'bazel-testlogs').symlink_to(logs, target_is_directory=True)
    directory = logs / 'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/ChatLocalTextFilterProjectionTests/test.outputs'
    directory.mkdir(parents=True)
    return private, directory


def macho(payload, kind=2, platform=7):
    count, commands_size = (3, 120) if kind == 2 else (3, 128) if kind == 6 else (2, 96)
    size = 32 + commands_size + len(payload)
    segment = struct.pack('<II16s4Q4I', 0x19, 72, b'__TEXT', 0, size, 0, size, 5, 5, 0, 0)
    build = struct.pack('<6I', 0x32, 24, platform, 0xF0000, 0x1B0100, 0)
    extra = struct.pack('<IIQQ', 0x80000028, 24, 32 + commands_size, 0) if kind == 2 else struct.pack('<6I', 0xD, 32, 24, 0, 0, 0) + b'X\0' + b'\0' * 6 if kind == 6 else b''
    return struct.pack('<8I', 0xFEEDFACF, 0x0100000C, 0, kind, count, commands_size, 0, 0) + segment + build + extra + payload


class TestProductCaptureTests(unittest.TestCase):
    def test_capture_keeps_products_unchanged_and_plan_uses_only_closed_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'TEST_ROOT'; source.mkdir()
            fixture(source)
            original = (source / capture.APP / 'ProjectionTestHost').read_bytes()
            destination = root / 'captured'
            report = capture.capture_products(source, destination)
            self.assertTrue(report['diagnostic_products_not_test_pass'])
            self.assertEqual((destination / 'TEST_ROOT' / capture.APP / 'ProjectionTestHost').read_bytes(), original)
            plan = plistlib.loads((destination / 'TEST_ROOT/test.xctestrun').read_bytes())
            self.assertEqual(plan['Runner']['EnvironmentVariables'], {'DYLD_INSERT_LIBRARIES': ''})
            for text in ['secret-fixture-token', 'GH_TOKEN', '/private/account-path', str(root), 'CommandLineArguments']:
                self.assertNotIn(text, json.dumps(plan))
                self.assertNotIn(text, (destination / 'capture.json').read_text())
            self.assertFalse((destination / 'launch_options.json').exists())
            self.assertTrue((destination / 'TEST_ROOT' / capture.APP / 'ProjectionTestHost').stat().st_mode & 0o111)
            for name, digest in report['files'].items():
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)

    def test_absolute_source_paths_are_movable_only_after_inside_root_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'TEST_ROOT'; source.mkdir()
            plan = fixture(source)
            for key in ('TestHostPath', 'TestBundlePath'):
                plan['Runner'][key] = plan['Runner'][key].replace('__TESTROOT__', str(source))
            (source / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
            self.assertTrue(capture.sanitize_plan(source)['Runner']['TestHostPath'].startswith('__TESTROOT__/'))
            plan['Runner']['TestHostPath'] = str(root / capture.APP)
            (source / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
            with self.assertRaises(capture.InvalidProducts): capture.sanitize_plan(source)

    def test_identity_injection_and_external_plan_paths_are_rejected(self):
        for field, bad in [('TestHostPath', '__TESTROOT__/../outside.app'), ('TestBundlePath', '/tmp/private.xctest'), ('ProductModuleName', 'OtherTests'), ('IsAppHostedTestBundle', False)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); plan = fixture(root); plan['Runner'][field] = bad
                (root / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
                with self.assertRaises(capture.InvalidProducts): capture.sanitize_plan(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan = fixture(root)
            plan['Runner']['TestingEnvironmentVariables']['DYLD_INSERT_LIBRARIES'] = '/private/libSecret.dylib'
            (root / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
            with self.assertRaises(capture.InvalidProducts): capture.sanitize_plan(root)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root)
            (root / capture.APP / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.other.account', 'CFBundleExecutable': 'ProjectionTestHost'}))
            with self.assertRaises(capture.InvalidProducts): capture.sanitize_plan(root)

    def test_absolute_plan_os_ancestor_alias_is_allowed_but_internal_link_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); actual = base / 'actual'; actual.mkdir()
            source = actual / 'TEST_ROOT'; source.mkdir(); plan = fixture(source)
            alias = base / 'os-alias'; alias.symlink_to(actual, target_is_directory=True)
            for key in ('TestHostPath', 'TestBundlePath'):
                plan['Runner'][key] = plan['Runner'][key].replace('__TESTROOT__', str(alias / 'TEST_ROOT'))
            (source / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
            self.assertEqual(capture.sanitize_plan(source)['Runner']['TestHostPath'], '__TESTROOT__/' + capture.APP)
            (source / 'host-link').symlink_to(source / capture.APP, target_is_directory=True)
            plan['Runner']['TestHostPath'] = str(source / 'host-link')
            (source / 'test.xctestrun').write_bytes(plistlib.dumps(plan))
            with self.assertRaises(capture.InvalidProducts): capture.sanitize_plan(source)

    def test_symlink_or_runtime_data_inside_products_cannot_be_captured(self):
        for name in ('configuration.json', 'history.sqlite', 'signing.key', 'authorization.session', 'access.token', 'client.authkey', 'session.json', 'Library/cache.data'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'source'; source.mkdir(); fixture(source)
                bad = source / capture.APP / name; bad.parent.mkdir(exist_ok=True, parents=True); bad.write_text('private-fixture')
                with self.assertRaises(capture.InvalidProducts): capture.capture_products(source, root / 'captured')
                self.assertFalse((root / 'captured').exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir(); fixture(source)
            (source / capture.APP / 'linked').symlink_to(source / 'launch_options.json')
            with self.assertRaises(capture.InvalidProducts): capture.capture_products(source, root / 'captured')
            self.assertFalse((root / 'captured').exists())

    def test_post_action_finds_only_fixed_runner_products_and_never_copies_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); tmp = root / 'temp'; out = root / 'outputs'; out.mkdir()
            source = tmp / 'test_runner_work_dir.123456/TEST_ROOT'; source.mkdir(parents=True); fixture(source)
            env = {'TEST_TMPDIR': str(tmp), 'TEST_UNDECLARED_OUTPUTS_DIR': str(out), 'TEST_EXIT_CODE': '12', 'GH_TOKEN': 'secret-fixture-token'}
            self.assertEqual(capture.post_action(env)['state'], 'captured')
            self.assertTrue((out / capture.CAPTURE / 'TEST_ROOT/test.xctestrun').is_file())
            self.assertFalse(any(path.name == 'launch_options.json' for path in out.rglob('*')))
            self.assertNotIn('secret-fixture-token', (out / capture.CAPTURE / 'capture.json').read_text())

    def test_success_and_missing_or_ambiguous_sources_do_not_change_status(self):
        self.assertEqual(capture.post_action({'TEST_EXIT_CODE': '0'})['state'], 'notNeeded')
        self.assertEqual(capture.post_action({'TEST_EXIT_CODE': '12'})['state'], 'unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); out = root / 'out'; out.mkdir()
            for number in (1, 2):
                source = root / f'test_runner_work_dir.{number}/TEST_ROOT'; source.mkdir(parents=True); fixture(source)
            report = capture.post_action({'TEST_TMPDIR': str(root), 'TEST_UNDECLARED_OUTPUTS_DIR': str(out), 'TEST_EXIT_CODE': '12'})
            self.assertEqual(report['state'], 'unavailable')
            self.assertEqual(list(out.iterdir()), [])
        script = Path(capture.__file__).with_name('projection_test_capture.sh')
        result = subprocess.run(['bash', str(script)], env={'PATH': os.environ['PATH'], 'TEST_EXIT_CODE': '12'}, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0)

    def test_direct_and_zipped_bazel_outputs_retain_verified_hashes_and_provenance(self):
        for zipped in (False, True):
            with self.subTest(zipped=zipped), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); private, out = outputs(root)
                source = root / 'source'; source.mkdir(); fixture(source)
                captured = out / capture.CAPTURE; capture.capture_products(source, captured)
                if zipped:
                    with zipfile.ZipFile(out / 'outputs.zip', 'w') as archive:
                        for path in captured.rglob('*'):
                            if path.is_file(): archive.write(path, str(path.relative_to(out)))
                    import shutil
                    shutil.rmtree(captured)
                report = capture.retain_captured(root, private, 'a' * 40, {'xcode': '27.1'}, secrets=['not-present-fixture'])
                self.assertEqual(report['state'], 'retained')
                destination = root / 'build/fork-evidence' / capture.CAPTURE
                manifest = json.loads((destination / 'manifest.json').read_text())
                self.assertEqual(manifest['source_commit'], 'a' * 40)
                self.assertEqual(manifest['test_target'], capture.LABEL)
                self.assertEqual(manifest['toolchain'], {'xcode': '27.1'})
                self.assertFalse(manifest['physical_device_verified'])
                for line in (destination / 'SHA256SUMS').read_text().splitlines():
                    digest, name = line.split('  ', 1)
                    self.assertEqual(digest, hashlib.sha256((destination / name).read_bytes()).hexdigest())

    def test_tampered_products_credentials_extra_files_and_escaped_logs_are_rejected(self):
        for failure in ('hash', 'secret', 'extra', 'logs'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); private, out = outputs(root)
                source = root / 'source'; source.mkdir(); fixture(source)
                captured = out / capture.CAPTURE; capture.capture_products(source, captured)
                if failure == 'hash': (captured / 'TEST_ROOT' / capture.APP / 'ProjectionTestHost').write_bytes(b'tampered executable')
                if failure == 'secret': (captured / 'TEST_ROOT' / capture.APP / 'ProjectionTestHost').write_bytes(b'private-fixture-secret')
                if failure == 'extra': (captured / 'unapproved.json').write_text('{}')
                if failure == 'logs':
                    (root / 'bazel-testlogs').unlink(); (root / 'bazel-testlogs').symlink_to(source)
                with self.assertRaises((capture.InvalidProducts, FileNotFoundError)):
                    capture.retain_captured(root, private, 'a' * 40, {}, secrets=['private-fixture-secret'])
                self.assertFalse((root / 'build/fork-evidence' / capture.CAPTURE).exists())

    def test_post_action_is_fixed_testonly_and_cannot_replace_original_exit_code(self):
        root = Path(__file__).resolve().parents[3]
        build = (root / 'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/BUILD').read_text()
        runner = re.search(r'ios_test_runner\(\n\s+name = "ChatLocalTextFilterProjectionTestRunner",(.*?)\n\)', build, re.S).group(1)
        # Bazel rejects a non-test runner depending on a testonly post action
        # before compilation, so both sides of this edge must be testonly.
        self.assertIn('testonly = True', runner)
        self.assertIn('post_action = "//tools/fork_baseline:projection_test_capture"', runner)
        self.assertIn('post_action_determines_exit_code = False', runner)
        helper = (root / 'tools/fork_baseline/BUILD').read_text()
        self.assertIn('testonly = True', helper)
        self.assertIn('@rules_shell//shell:sh_binary.bzl', helper)
        self.assertNotIn('rules_python', helper)

    def test_capture_limits_leave_no_partial_published_directory(self):
        for constant, value in [('MAX_FILES', 2), ('MAX_TOTAL_BYTES', 4), ('MAX_FILE_BYTES', 4)]:
            with self.subTest(constant=constant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'source'; source.mkdir(); fixture(source)
                with patch.object(capture, constant, value), self.assertRaises(capture.InvalidProducts):
                    capture.capture_products(source, root / 'captured')
                self.assertFalse((root / 'captured').exists())

    def test_zip_traversal_links_and_duplicate_entries_fail_closed(self):
        for kind in ('traversal', 'link', 'duplicate'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); private, out = outputs(root)
                with zipfile.ZipFile(out / 'outputs.zip', 'w') as archive:
                    entry = zipfile.ZipInfo(capture.CAPTURE + '/file.json')
                    if kind == 'traversal': entry.filename = capture.CAPTURE + '/../private.json'
                    if kind == 'link': entry.external_attr = (0o120777 << 16)
                    archive.writestr(entry, '{}')
                    if kind == 'duplicate':
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter('ignore', UserWarning)
                            archive.writestr(entry, '{}')
                with self.assertRaises(capture.InvalidProducts):
                    capture.retain_captured(root, private, 'a' * 40, {})
                self.assertFalse((root / 'build/fork-evidence' / capture.CAPTURE).exists())

    def test_only_structurally_valid_arm64_simulator_code_exempts_application_config(self):
        api = ['1234567', 'a' * 32]
        payload = ('application configuration ' + ' '.join(api)).encode()
        for kind in (2, 6, 8):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'source'; data = macho(payload, kind=kind); source.write_bytes(data)
                capture.copy_file(source, root / 'copied', application_credentials=api, code_candidate=True)
                self.assertEqual((root / 'copied').read_bytes(), data)
        bad_header = bytearray(macho(payload)); struct.pack_into('<I', bad_header, 20, 0x7FFFFFFF)
        fat = struct.pack('>7I', 0xCAFEBABE, 1, 0x0100000C, 0, 32, len(macho(payload)), 3) + b'\0' * 4 + macho(payload)
        for data in (b'not-code' + payload, b'\xcf\xfa\xed\xfe' + payload, bytes(bad_header), macho(payload, platform=2), fat):
            with self.subTest(data=data[:8]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'source'; source.write_bytes(data)
                with self.assertRaises(capture.InvalidProducts):
                    capture.copy_file(source, root / 'copied', application_credentials=api, code_candidate=True)

    def test_runtime_secret_still_rejected_in_valid_code_and_api_text_stays_blocked(self):
        api, token = ['1234567', 'a' * 32], 'runtime-secret-fixture-token'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.write_bytes(macho((api[0] + token).encode()))
            with self.assertRaises(capture.InvalidProducts):
                capture.copy_file(source, root / 'blocked', application_credentials=api, secrets=[token], code_candidate=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.write_bytes(macho(api[0].encode()))
            with self.assertRaises(capture.InvalidProducts):
                capture.copy_file(source, root / 'blocked', application_credentials=api, code_candidate=False)

    def test_direct_and_zip_retain_api_config_only_in_verified_product_code(self):
        for zipped in (False, True):
            for plaintext in (False, True):
                with self.subTest(zipped=zipped, plaintext=plaintext), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); private, out = outputs(root)
                    source = root / 'source'; source.mkdir(); fixture(source)
                    api = ['1234567', 'a' * 32]
                    binary = macho(('public application config ' + ' '.join(api)).encode())
                    (source / capture.APP / 'ProjectionTestHost').write_bytes(binary)
                    if plaintext: (source / capture.APP / 'Localizable.strings').write_text(api[1])
                    captured = out / capture.CAPTURE; capture.capture_products(source, captured)
                    if zipped:
                        with zipfile.ZipFile(out / 'outputs.zip', 'w') as archive:
                            for path in captured.rglob('*'):
                                if path.is_file(): archive.write(path, str(path.relative_to(out)))
                        import shutil
                        shutil.rmtree(captured)
                    if plaintext:
                        with self.assertRaises(capture.InvalidProducts):
                            capture.retain_captured(root, private, 'b' * 40, {}, application_credentials=api)
                    else:
                        result = capture.retain_captured(root, private, 'b' * 40, {}, application_credentials=api)
                        self.assertEqual(result['state'], 'retained')
                        retained = root / 'build/fork-evidence' / capture.CAPTURE
                        self.assertEqual((retained / 'TEST_ROOT' / capture.APP / 'ProjectionTestHost').read_bytes(), binary)
                        self.assertNotIn(api[1], (retained / 'manifest.json').read_text())
