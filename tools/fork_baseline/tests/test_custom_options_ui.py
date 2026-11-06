"""Closed renderer evidence and source/CI contracts; no native result invented."""
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import warnings
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custom_options_ui as profile
import custom_options_smoke as visual
import launch_smoke

ROOT = Path(__file__).resolve().parents[3]


class CustomOptionsContracts(unittest.TestCase):
    def test_attachment_export_prepares_private_directory_and_records_fixed_command_success(self):
        real_popen = visual.subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
            exported = root / 'exported'; diagnostic = root / 'attachment-export.json'
            def execute(arguments, **kwargs):
                self.assertEqual(arguments, ['xcrun', 'xcresulttool', 'export', 'attachments',
                    '--path', str(bundle), '--output-path', str(exported)])
                self.assertTrue(exported.is_dir())
                self.assertEqual(exported.stat().st_mode & 0o777, 0o700)
                self.assertLessEqual(set(kwargs['env']), visual.EXPORT_ENVIRONMENT_KEYS)
                for key in ('TELEGRAM_API_HASH', 'TELEGRAM_API_ID', 'GITHUB_TOKEN', 'DYLD_INSERT_LIBRARIES'):
                    self.assertNotIn(key, kwargs['env'])
                return real_popen([sys.executable, '-c', "import sys;sys.stderr.write('Export complete\\n');print('stdout is never retained')"], **kwargs)
            api_hash_key = 'TELEGRAM_' + 'API_HASH'
            with patch.dict(os.environ, {api_hash_key: 'synthetic-api-secret', 'TELEGRAM_API_ID': '1234567',
                    'GITHUB_TOKEN': 'synthetic-ci-secret', 'DYLD_INSERT_LIBRARIES': 'private-injection'}), patch.object(visual.subprocess, 'Popen', side_effect=execute):
                visual.export_attachments(bundle, exported, diagnostic)
            report = json.loads(diagnostic.read_text())
            self.assertEqual(report['phase'], 'exportAttachments')
            self.assertEqual(report['status'], 'success')
            self.assertEqual(report['return_code'], 0)
            self.assertFalse(report['timeout'])
            self.assertNotIn('stdout is never retained', diagnostic.read_text())
            self.assertGreater(report['stdout_bytes'], 0)
            self.assertEqual(diagnostic.stat().st_mode & 0o777, 0o600)

    def test_attachment_export_failure_retains_usage_and_redacts_credentials_paths_and_identifiers(self):
        real_popen = visual.subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
            exported = root / 'exported'; diagnostic = root / 'attachment-export.json'
            secret = 'synthetic-secret-value-not-for-artifacts'
            api_hash_key = 'TELEGRAM_' + 'API_HASH'
            token = 'github_pat_' + 'Z' * 30
            identifier = '11111111-2222-3333-4444-555555555555'
            stderr = ('USAGE: xcresulttool export attachments --path <path> --output-path <output-path>\n'
                + 'Error: output directory ' + str(exported) + ' could not be used\n'
                + api_hash_key + '=' + secret + '\n' + 'token ' + token + '\n'
                + '/Users/private/Library/' + identifier + ' https://example.invalid/private?q=private\n'
                + 'person@example.invalid\n' + '-----BEGIN ' + 'PRIVATE KEY-----\nprivate-key-material\n-----END ' + 'PRIVATE KEY-----\n')
            def execute(arguments, **kwargs):
                return real_popen([sys.executable, '-c', 'import sys;sys.stderr.write(' + repr(stderr) + ');sys.exit(64)'], **kwargs)
            with patch.dict(os.environ, {api_hash_key: secret}), patch.object(visual.subprocess, 'Popen', side_effect=execute):
                with self.assertRaises(launch_smoke.SmokeError) as raised:
                    visual.export_attachments(bundle, exported, diagnostic)
            self.assertEqual(raised.exception.evidence()['phase'], 'exportAttachments')
            self.assertEqual(raised.exception.return_code, 64)
            text = diagnostic.read_text(); report = json.loads(text)
            self.assertEqual(report['status'], 'commandFailed')
            self.assertEqual(report['return_code'], 64)
            self.assertIn('USAGE: xcresulttool export attachments', report['stderr'])
            self.assertIn('<PRIVATE_EXPORT>', report['stderr'])
            for private in (secret, token, identifier, str(root), '/Users/private', 'example.invalid', 'private-key-material'):
                self.assertNotIn(private, text)
            self.assertLessEqual(len(report['stderr']), 16384)

    def test_attachment_export_pipe_cap_and_timeout_fail_closed_with_diagnostics(self):
        real_popen = visual.subprocess.Popen
        cases = [("import os;os.write(2,b'x'*131072)", 5, 'commandOutputLimit'),
                 ('import time;time.sleep(5)', .05, 'commandTimedOut')]
        for script, timeout, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
                diagnostic = root / 'attachment-export.json'
                def execute(arguments, **kwargs):
                    return real_popen([sys.executable, '-c', script], **kwargs)
                with patch.object(visual.subprocess, 'Popen', side_effect=execute):
                    with self.assertRaises(launch_smoke.SmokeError) as raised:
                        visual.export_attachments(bundle, root / 'exported', diagnostic, timeout=timeout)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(raised.exception.phase, 'exportAttachments')
                report = json.loads(diagnostic.read_text())
                self.assertEqual(report['status'], expected)
                self.assertEqual(report['timeout'], expected == 'commandTimedOut')
                self.assertLessEqual(report['stderr_bytes'], launch_smoke.COMMAND_OUTPUT_LIMIT)
                self.assertLessEqual(len(report['stderr']), 16384)

    def test_attachment_export_rejects_symlink_roots_and_existing_diagnostics_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
            bundle_link = root / 'result-link'; bundle_link.symlink_to(bundle, target_is_directory=True)
            export_link = root / 'export-link'; export_link.symlink_to(bundle, target_is_directory=True)
            log = root / 'already.json'; log.write_text('keep original')
            for source, target, diagnostic in ((bundle_link, root / 'exported', root / 'new.json'),
                    (bundle, export_link, root / 'new.json'), (bundle, root / 'exported', log)):
                with self.subTest(source=source.name, target=target.name), patch.object(visual.subprocess, 'Popen') as process:
                    with self.assertRaises(visual.InvalidVisualEvidence):
                        visual.export_attachments(source, target, diagnostic)
                    process.assert_not_called()
            self.assertEqual(log.read_text(), 'keep original')

    def test_attachment_export_combined_pipe_limit_rejects_two_individually_small_streams(self):
        real_popen = visual.subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
            diagnostic = root / 'attachment-export.json'
            def execute(arguments, **kwargs):
                return real_popen([sys.executable, '-c', "import os;os.write(1,b'a'*40000);os.write(2,b'b'*40000)"], **kwargs)
            with patch.object(visual.subprocess, 'Popen', side_effect=execute):
                with self.assertRaises(launch_smoke.SmokeError) as raised:
                    visual.export_attachments(bundle, root / 'exported', diagnostic)
            self.assertEqual(raised.exception.code, 'commandOutputLimit')
            report = json.loads(diagnostic.read_text())
            self.assertEqual(report['total_bytes'], launch_smoke.COMMAND_OUTPUT_LIMIT)
            self.assertEqual(report['total_bytes'], report['stdout_bytes'] + report['stderr_bytes'])
            self.assertLess(report['stdout_bytes'], launch_smoke.COMMAND_OUTPUT_LIMIT)
            self.assertLess(report['stderr_bytes'], launch_smoke.COMMAND_OUTPUT_LIMIT)
            self.assertLessEqual(len(report['stderr']), 16384)

    def test_attachment_export_diagnostic_failure_preserves_original_command_failure(self):
        real_popen = visual.subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'test.xcresult'; bundle.mkdir()
            def execute(arguments, **kwargs):
                return real_popen([sys.executable, '-c', 'import sys;sys.exit(64)'], **kwargs)
            with patch.object(visual.subprocess, 'Popen', side_effect=execute), patch.object(visual.json, 'dump', side_effect=OSError('synthetic disk-full diagnostic failure')):
                with self.assertRaises(launch_smoke.SmokeError) as raised:
                    visual.export_attachments(bundle, root / 'exported', root / 'attachment-export.json')
            self.assertEqual(raised.exception.code, 'commandFailed')
            self.assertEqual(raised.exception.phase, 'exportAttachments')
            self.assertEqual(raised.exception.return_code, 64)

    def test_export_success_does_not_accept_missing_required_screenshot_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / 'outputs'; outputs.mkdir()
            (outputs / 'test.xcresult').mkdir()
            def exported(bundle, destination, diagnostic):
                destination.mkdir()
                (destination / 'manifest.json').write_text('[]')
                diagnostic.write_text('{"status":"success"}')
            with patch.object(visual, 'export_attachments', side_effect=exported), patch.object(visual, 'prepare_screenshot_checker') as checker:
                with self.assertRaises(visual.InvalidVisualEvidence):
                    visual.export_screenshots(outputs, root / 'screenshots')
                checker.assert_not_called()
            self.assertTrue((root / 'screenshots/attachment-export.json').is_file())

    def test_large_unrelated_inventory_uses_supported_fixed_search_without_raising_output_cap(self):
        unrelated = [{'name': 'Unrelated model ' + str(index), 'identifier': 'unrelated.' + str(index),
                      'description': 'public hardware metadata ' * 10} for index in range(500)]
        inventory = json.dumps({'devicetypes': unrelated + [{'identifier': visual.DEVICE_TYPE}]}).encode()
        self.assertGreater(len(inventory), 64 * 1024)
        self.assertEqual(launch_smoke.COMMAND_OUTPUT_LIMIT, 64 * 1024)
        expected = ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro']
        def limited_command(args):
            if args == ['xcrun', 'simctl', 'list', 'runtimes', '--json']:
                return json.dumps({'runtimes': [{'identifier': visual.RUNTIME, 'isAvailable': True}]})
            if args == expected:
                return json.dumps({'devicetypes': [{'identifier': visual.DEVICE_TYPE}, {'identifier': visual.DEVICE_TYPE + '-Max'}]})
            raise launch_smoke.SmokeError('commandOutputLimit', 'extractApplication')
        with patch.object(visual, 'command', side_effect=limited_command) as command:
            visual.validate_runtime()
            self.assertEqual(command.call_count, 2)
            self.assertEqual(command.call_args_list[-1].args[0], expected)

    def test_filtered_device_inventory_requires_one_exact_identifier(self):
        for identifiers in ([visual.DEVICE_TYPE + '-Max'], [visual.DEVICE_TYPE, visual.DEVICE_TYPE], []):
            with self.subTest(identifiers=identifiers), patch.object(visual, 'command', side_effect=[
                    json.dumps({'runtimes': [{'identifier': visual.RUNTIME, 'isAvailable': True}]}),
                    json.dumps({'devicetypes': [{'identifier': identifier} for identifier in identifiers]})]):
                with self.assertRaises(visual.InvalidVisualEvidence): visual.validate_runtime()

    def test_renderer_is_exactly_three_production_sources_without_account_binding(self):
        build = (ROOT / 'submodules/SettingsUI/BUILD').read_text()
        group = re.search(r'filegroup\(\n\s+name = "CustomeOptionsRendererSources",(.*?)\n\)', build, re.S).group(1)
        names = re.findall(r'"(Sources/[^\"]+\.swift)"', group)
        self.assertEqual(names, ['Sources/CustomeOptionsCatalog.swift', 'Sources/CustomeOptionsController.swift', 'Sources/LocalClientCosmeticsController.swift'])
        host = (ROOT / 'Tests/CustomeOptionsHost/BUILD').read_text()
        renderer = re.search(r'swift_library\(\n\s+name = "CustomeOptionsRenderer",(.*?)\n\)', host, re.S).group(1)
        self.assertIn('//submodules/SettingsUI:CustomeOptionsRendererSources', renderer)
        for forbidden in ('//submodules/SettingsUI:SettingsUI', '//submodules/AccountContext', '//submodules/TelegramCore', '//submodules/TelegramPresentationData'):
            self.assertNotIn(forbidden, host)
        for source in profile.RENDERER_SOURCES:
            imports = set(re.findall(r'^import (\w+)', (ROOT / source).read_text(), re.M))
            self.assertLessEqual(imports, {'Foundation', 'UIKit', 'Display', 'SwiftSignalKit'})

    def test_host_and_tests_use_real_controls_and_public_ui_automation(self):
        unit = (ROOT / 'Tests/CustomeOptionsHost/CustomeOptionsControllerTests.swift').read_text()
        interaction = (ROOT / 'Tests/CustomeOptionsHost/CustomeOptionsInteractionTests.swift').read_text()
        fixture = (ROOT / 'Tests/CustomeOptionsHost/CustomeOptionsFixture.swift').read_text()
        self.assertIn('sendActions(for: .valueChanged)', unit)
        self.assertIn('search.delegate?.searchBar?', unit)
        self.assertIn('table.delegate?.tableView?', unit)
        self.assertEqual(len(re.findall(r'func test\w+\(', unit)), profile.EXPECTED_CASES[profile.UNIT_TARGET][1])
        self.assertIn('XCUIApplication()', interaction)
        for action in ('Cancel', 'Turn features off', 'OK'):
            self.assertIn('buttons["' + action + '"].tap()', interaction)
        self.assertIn('XCTAttachment(screenshot: XCUIScreen.main.screenshot())', interaction)
        self.assertIn('attachment.lifetime = .keepAlways', interaction)
        self.assertIn('UINavigationController(rootViewController:', fixture)
        self.assertIn('pushViewController(controller, animated: true)', fixture)
        for text in (unit, interaction, fixture):
            self.assertNotIn('value(forKey:', text)
            self.assertNotIn('performSelector', text)
            self.assertNotIn('URLSession', text)
        self.assertIn('Synthetic · resets', fixture)
        self.assertIn('No Telegram account', fixture)

    def test_fixed_target_and_device_contract_does_not_offer_arbitrary_cli_targets(self):
        self.assertEqual(profile.APP_TARGET, '//Tests/CustomeOptionsHost:CustomeOptionsHost')
        self.assertEqual(set(profile.EXPECTED_CASES), {profile.UNIT_TARGET, profile.UI_TARGET})
        host = (ROOT / 'Tests/CustomeOptionsHost/BUILD').read_text()
        self.assertIn('device_type = "iPhone 15 Pro"', host)
        self.assertIn('os_version = "27.0"', host)
        self.assertIn('testonly = True', host)
        source = Path(profile.__file__).read_text()
        self.assertNotIn('argparse', source)
        self.assertIn('logged_in_settings_verified', source)
        self.assertIn('physical_device_verified', source)
        self.assertIn('full_application_built', source)
        self.assertLess(source.index('shutil.copyfile(ROOT / "bazel-bin') if 'shutil.copyfile(ROOT / "bazel-bin' in source else source.index("shutil.copyfile(ROOT / 'bazel-bin"), source.index("['--target=' + UNIT_TARGET]"))

    def test_closed_actual_frame_evidence_rejects_missing_duplicate_or_private_fields(self):
        values = [{'stage': stage, 'window': [0, 0, 393, 852], 'table': [0, 88, 393, 720]} for stage in visual.STAGES]
        log = '\n'.join('AYU_OPTIONS_FRAME:' + json.dumps(value) for value in values)
        self.assertEqual(len(visual.frames_from_log(log)), len(visual.STAGES))
        for invalid in (values[:-1], values + [values[0]], [dict(value, account='private') for value in values],
                        [dict(value, window=[0, 0, float('inf'), 852]) for value in values]):
            with self.assertRaises((visual.InvalidVisualEvidence, ValueError)):
                visual.frames_from_log('\n'.join('AYU_OPTIONS_FRAME:' + json.dumps(value) for value in invalid))

    def test_dark_stage_labels_cannot_substitute_for_observed_dark_traits_and_background(self):
        values = []
        for stage in visual.STAGES:
            if stage == 'reset-prompt': continue
            dark = not stage.endswith('-light')
            values.append({'stage': stage, 'window_dark': dark, 'table_dark': dark, 'background_dark': dark,
                'table_found': True, 'background_sampled': True})
        encode = lambda items: '\n'.join('AYU_OPTIONS_APPEARANCE:' + json.dumps(value) for value in items)
        self.assertEqual(len(visual.appearances_from_log(encode(values))), 11)
        invalid = [dict(value) for value in values]
        next(value for value in invalid if value['stage'] == 'root-dark')['background_dark'] = False
        for case in (invalid, values[:-1], values + [values[0]], [dict(value, account='private') for value in values],
                     [dict(value, table_found=1) for value in values]):
            with self.assertRaises(visual.InvalidVisualEvidence): visual.appearances_from_log(encode(case))

    def test_attachment_export_mapping_is_fixed_and_rejects_traversal_or_missing_states(self):
        values = [{'exportedFileName': 'opaque-' + str(i) + '.png', 'suggestedHumanReadableName': 'ayu-' + stage + '_0_fixture.png'} for i, stage in enumerate(visual.STAGES)]
        self.assertEqual(set(visual.attachment_mapping([{'attachments': values}])), set(visual.STAGES))
        configured = [dict(value, suggestedHumanReadableName='Screenshot.png', configuredName='ayu-' + stage) for value, stage in zip(values, visual.STAGES)]
        self.assertEqual(set(visual.attachment_mapping(configured)), set(visual.STAGES))
        for invalid in (values[:-1], values + [values[0]], [dict(value, exportedFileName='../private.png') for value in values]):
            with self.assertRaises(visual.InvalidVisualEvidence): visual.attachment_mapping(invalid)
        contradictory = [dict(value) for value in values]
        contradictory[0]['configuredName'] = 'ayu-' + visual.STAGES[1]
        with self.assertRaises(visual.InvalidVisualEvidence): visual.attachment_mapping(contradictory)

    def test_raw_result_extraction_copies_only_fixed_subtree_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / 'outputs'; outputs.mkdir(); private = root / 'private'; private.mkdir()
            with zipfile.ZipFile(outputs / 'outputs.zip', 'w') as archive:
                archive.writestr('test.xcresult/Info.plist', b'synthetic-result')
                archive.writestr('unrelated/private.log', b'must-not-copy')
            result = visual.extract_result_bundle(outputs, private)
            self.assertEqual((result / 'Info.plist').read_bytes(), b'synthetic-result')
            self.assertFalse((private / 'unrelated').exists())
        for name, mode in [('test.xcresult/../private.log', 0o100600), ('test.xcresult/link', 0o120777)]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); outputs = root / 'outputs'; outputs.mkdir(); private = root / 'private'; private.mkdir()
                with zipfile.ZipFile(outputs / 'outputs.zip', 'w') as archive:
                    info = zipfile.ZipInfo(name); info.external_attr = mode << 16; archive.writestr(info, b'x')
                with self.assertRaises(visual.InvalidVisualEvidence): visual.extract_result_bundle(outputs, private)

    def test_readonly_direct_result_is_copied_writable_without_modifying_original_bytes_or_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / 'outputs'; outputs.mkdir()
            source = outputs / 'test.xcresult'; source.mkdir()
            data = source / 'Data'; data.mkdir()
            database = data / 'database.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE fixture(value TEXT)')
                connection.execute("INSERT INTO fixture VALUES ('original result')")
            (source / 'Info.plist').write_bytes(b'synthetic result metadata')
            for path in (database, source / 'Info.plist'): path.chmod(0o444)
            data.chmod(0o555); source.chmod(0o555)
            originals = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in (database, source / 'Info.plist')}
            original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in (source, data)}
            private = root / 'private'; private.mkdir(mode=0o700)
            old_umask = os.umask(0)
            try: copied = visual.extract_result_bundle(outputs, private)
            finally: os.umask(old_umask)
            self.assertEqual(copied, private / 'test.xcresult')
            self.assertNotEqual(copied, source)
            renamed = copied / 'Data/moved.sqlite3'
            (copied / 'Data/database.sqlite3').rename(renamed)
            with sqlite3.connect(renamed) as connection:
                connection.execute("UPDATE fixture SET value='exporter converted copy'")
            for path in (copied, copied / 'Data'): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
            for path in (renamed, copied / 'Info.plist'): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for path, (content, mode) in originals.items():
                self.assertEqual(path.read_bytes(), content)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            for path, mode in original_modes.items(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            self.assertFalse((source / 'Data/moved.sqlite3').exists())
            self.assertFalse((copied / '_CodeSignature').exists(), 'only real result members are copied')
            # Restore fixture permissions only after proving production did not.
            source.chmod(0o700); data.chmod(0o700)

    def test_direct_result_rejects_linked_nonregular_or_unsafe_members_without_following_them(self):
        for kind in ('root_link', 'file_link', 'directory_link', 'fifo', 'backslash'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); outputs = root / 'outputs'; outputs.mkdir()
                private = root / 'private'; private.mkdir()
                external = root / 'external'; external.mkdir(); (external / 'private-data').write_bytes(b'must-not-copy')
                source = outputs / 'test.xcresult'
                if kind == 'root_link': source.symlink_to(external, target_is_directory=True)
                else:
                    source.mkdir()
                    if kind == 'file_link': (source / 'result').symlink_to(external / 'private-data')
                    if kind == 'directory_link': (source / 'result').symlink_to(external, target_is_directory=True)
                    if kind == 'fifo': os.mkfifo(source / 'result')
                    if kind == 'backslash': (source / 'unsafe\\member').write_bytes(b'bad')
                with self.assertRaises(visual.InvalidVisualEvidence): visual.extract_result_bundle(outputs, private)
                self.assertFalse(list(private.rglob('private-data')))

    def test_direct_result_enforces_existing_member_total_and_per_file_budgets(self):
        self.assertEqual((visual.RESULT_BUNDLE_MAX_MEMBERS, visual.RESULT_BUNDLE_MAX_BYTES, visual.RESULT_BUNDLE_MAX_FILE_BYTES),
                         (20000, 2 * 1024**3, 512 * 1024**2))
        cases = [('RESULT_BUNDLE_MAX_MEMBERS', 2), ('RESULT_BUNDLE_MAX_BYTES', 5), ('RESULT_BUNDLE_MAX_FILE_BYTES', 2)]
        for limit, value in cases:
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); outputs = root / 'outputs'; source = outputs / 'test.xcresult/Data'
                source.mkdir(parents=True); (source / 'a').write_bytes(b'aaa'); (source / 'b').write_bytes(b'bbb')
                private = root / 'private'; private.mkdir()
                with patch.object(visual, limit, value), self.assertRaises(visual.InvalidVisualEvidence):
                    visual.extract_result_bundle(outputs, private)
                self.assertEqual((source / 'a').read_bytes(), b'aaa')

    def test_zip_result_is_also_private_writable_and_rejects_duplicate_or_unsafe_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / 'outputs'; outputs.mkdir(); private = root / 'private'; private.mkdir()
            with zipfile.ZipFile(outputs / 'outputs.zip', 'w') as archive:
                info = zipfile.ZipInfo('test.xcresult/Data/database.sqlite3'); info.external_attr = 0o100444 << 16
                archive.writestr(info, b'synthetic sqlite payload')
            original = (outputs / 'outputs.zip').read_bytes()
            old_umask = os.umask(0)
            try: copied = visual.extract_result_bundle(outputs, private)
            finally: os.umask(old_umask)
            (copied / 'Data/database.sqlite3').rename(copied / 'Data/converted.sqlite3')
            self.assertEqual(stat.S_IMODE((copied / 'Data/converted.sqlite3').stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((copied / 'Data').stat().st_mode), 0o700)
            self.assertEqual((outputs / 'outputs.zip').read_bytes(), original)
        cases = [
            [('test.xcresult/a', 0o100600), ('test.xcresult/a', 0o100600)],
            [('test.xcresult/A', 0o100600), ('test.xcresult/a', 0o100600)],
            [('test.xcresult/Data/', 0o040700), ('test.xcresult/Data/', 0o040700)],
            [('test.xcresult/a', 0o100600), ('test.xcresult/a/child', 0o100600)],
            [('test.xcresult//private', 0o100600)],
            [('test.xcresult/./private', 0o100600)],
            [('test.xcresult/../private', 0o100600)],
            [('test.xcresult/back\\slash', 0o100600)],
            [('test.xcresult/nonregular', 0o010600)],
        ]
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); outputs = root / 'outputs'; outputs.mkdir(); private = root / 'private'; private.mkdir()
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    with zipfile.ZipFile(outputs / 'outputs.zip', 'w') as archive:
                        for name, mode in members:
                            info = zipfile.ZipInfo(name); info.external_attr = mode << 16; archive.writestr(info, b'x')
                with self.assertRaises(visual.InvalidVisualEvidence): visual.extract_result_bundle(outputs, private)

    def test_zipped_result_obeys_same_limits_and_never_overwrites_existing_destination(self):
        for limit, value in [('RESULT_BUNDLE_MAX_MEMBERS', 2), ('RESULT_BUNDLE_MAX_BYTES', 5), ('RESULT_BUNDLE_MAX_FILE_BYTES', 2)]:
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); outputs = root / 'outputs'; outputs.mkdir(); private = root / 'private'; private.mkdir()
                with zipfile.ZipFile(outputs / 'outputs.zip', 'w') as archive:
                    archive.writestr('test.xcresult/Data/a', b'aaa'); archive.writestr('test.xcresult/Data/b', b'bbb')
                with patch.object(visual, limit, value), self.assertRaises(visual.InvalidVisualEvidence):
                    visual.extract_result_bundle(outputs, private)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); outputs = root / 'outputs'; (outputs / 'test.xcresult').mkdir(parents=True)
            private = root / 'private'; existing = private / 'test.xcresult'; existing.mkdir(parents=True)
            (existing / 'keep').write_bytes(b'untouched')
            with self.assertRaises(visual.InvalidVisualEvidence): visual.extract_result_bundle(outputs, private)
            self.assertEqual((existing / 'keep').read_bytes(), b'untouched')

    def test_existing_simulator_is_not_reused_or_deleted(self):
        devices = {'devices': {visual.RUNTIME: [{'name': visual.DEVICE_NAME, 'udid': 'not-ours'}]}}
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', return_value=json.dumps(devices)) as command:
            with self.assertRaises(visual.InvalidVisualEvidence):
                with visual.DisposableRendererSimulator(Path(directory)): pass
            self.assertEqual(command.call_count, 1)

    def test_disposable_lifecycle_only_shuts_down_and_deletes_created_identity(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        replies = iter([json.dumps({'devices': {visual.RUNTIME: []}}), identifier, '', '', ''])
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=lambda *args, **kwargs: next(replies)) as command:
            with visual.DisposableRendererSimulator(Path(directory)) as simulator:
                self.assertEqual(simulator.identifier, identifier)
            calls = [call.args[0] for call in command.call_args_list]
            self.assertEqual(calls[-2], ['xcrun', 'simctl', 'shutdown', identifier])
            self.assertEqual(calls[-1], ['xcrun', 'simctl', 'delete', identifier])
            self.assertEqual(calls[1], ['xcrun', 'simctl', 'create', visual.DEVICE_NAME, visual.DEVICE_TYPE, visual.RUNTIME])
            self.assertNotIn('erase', ' '.join(' '.join(call) for call in calls))
            path = Path(directory) / 'boot-attempts.json'
            report = json.loads(path.read_text())
            self.assertTrue(report['boot_evidence_only'])
            self.assertFalse(report['native_tests_started'])
            self.assertTrue(report['first_attempt_successful'])
            self.assertFalse(report['recovered_on_retry'])
            self.assertTrue(report['ready'])
            self.assertEqual(len(report['attempts']), 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(identifier, path.read_text())

    def test_owned_boot_timeout_restarts_once_without_changing_runtime_or_wait_bound(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        personal = '99999999-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
        waits = 0
        def execute(arguments, **kwargs):
            nonlocal waits
            if arguments[2] == 'list':
                return json.dumps({'devices': {visual.RUNTIME: [{'name': 'Personal simulator', 'udid': personal}]}})
            if arguments[2] == 'create': return identifier
            if arguments[2] == 'bootstatus':
                waits += 1
                self.assertEqual(kwargs, {'timeout': 240, 'phase': 'waitForBoot'})
                if waits == 1: raise original
            if arguments[2] == 'shutdown' and waits == 1:
                first = json.loads((Path(directory) / 'boot-attempts.json').read_text())
                self.assertEqual(first['attempts'][0]['failure'], original.evidence())
                self.assertFalse(first['ready'])
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=execute) as command:
            with visual.DisposableRendererSimulator(Path(directory)) as simulator:
                self.assertEqual(simulator.identifier, identifier)
                report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
                self.assertFalse(report['first_attempt_successful'])
                self.assertTrue(report['recovered_on_retry']); self.assertTrue(report['ready'])
                self.assertFalse(report['native_tests_started'])
                self.assertEqual(report['attempts'][0]['failure'], original.evidence())
                self.assertEqual([attempt['status'] for attempt in report['attempts']], ['failed', 'ready'])
            calls = [call.args[0] for call in command.call_args_list]
            operations = [call[2] for call in calls]
            self.assertEqual(operations, ['list', 'create', 'bootstatus', 'shutdown', 'bootstatus', 'shutdown', 'delete'])
            self.assertTrue(all(call[3] == identifier for call in calls if call[2] in {'boot', 'bootstatus', 'shutdown', 'delete'}))
            self.assertNotIn(personal, json.dumps(calls))
            self.assertEqual(operations.count('create'), 1)
            self.assertNotIn('erase', operations)

    def test_failed_restart_or_second_wait_preserves_original_failure_and_cleans_only_owned_device(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        for failing_operation in ('shutdown', 'bootstatus'):
            with self.subTest(failing_operation=failing_operation), tempfile.TemporaryDirectory() as directory:
                original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
                second = launch_smoke.SmokeError('commandFailed', {'shutdown': 'shutdownDevice', 'bootstatus': 'waitForBoot'}[failing_operation], return_code=7)
                counts = {}
                def execute(arguments, **kwargs):
                    operation = arguments[2]; counts[operation] = counts.get(operation, 0) + 1
                    if operation == 'list': return json.dumps({'devices': {visual.RUNTIME: []}})
                    if operation == 'create': return identifier
                    if operation == 'bootstatus' and counts[operation] == 1: raise original
                    if operation == failing_operation and counts[operation] == (1 if operation == 'shutdown' else 2): raise second
                    return ''
                with patch.object(visual, 'command', side_effect=execute) as command:
                    with self.assertRaises(launch_smoke.SmokeError) as raised:
                        with visual.DisposableRendererSimulator(Path(directory)): self.fail('tests must not start')
                self.assertIs(raised.exception, original)
                self.assertLessEqual(counts.get('bootstatus', 0), 2)
                self.assertEqual(counts.get('boot', 0), 0)
                report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
                self.assertFalse(report['ready']); self.assertFalse(report['recovered_on_retry'])
                self.assertEqual([attempt['failure'] for attempt in report['attempts']], [original.evidence(), second.evidence()])
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(calls[-2:], [['xcrun', 'simctl', 'shutdown', identifier], ['xcrun', 'simctl', 'delete', identifier]])

    def test_non_wait_boot_failure_and_test_assertion_failure_are_never_retried(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        for fail_before_wait in (False, True):
            with self.subTest(fail_before_wait=fail_before_wait), tempfile.TemporaryDirectory() as directory:
                original = launch_smoke.SmokeError('commandFailed', 'bootDevice', return_code=9) if fail_before_wait else AssertionError('native assertion must not be hidden')
                def execute(arguments, **kwargs):
                    if arguments[2] == 'list': return json.dumps({'devices': {visual.RUNTIME: []}})
                    if arguments[2] == 'create': return identifier
                    if arguments[2] == 'bootstatus' and fail_before_wait: raise original
                    return ''
                with patch.object(visual, 'command', side_effect=execute) as command:
                    with self.assertRaises(type(original)) as raised:
                        with visual.DisposableRendererSimulator(Path(directory)):
                            raise original
                self.assertIs(raised.exception, original)
                operations = [call.args[0][2] for call in command.call_args_list]
                self.assertEqual(operations.count('boot'), 0)
                self.assertEqual(operations.count('bootstatus'), 1)
                self.assertEqual(operations[-2:], ['shutdown', 'delete'])

    def test_boot_evidence_write_failure_cannot_replace_original_wait_failure_or_overwrite_existing_file(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'boot-attempts.json'; path.write_text('keep existing private report')
            def execute(arguments, **kwargs):
                if arguments[2] == 'list': return json.dumps({'devices': {visual.RUNTIME: []}})
                if arguments[2] == 'create': return identifier
                if arguments[2] == 'bootstatus': raise original
                return ''
            with patch.object(visual, 'command', side_effect=execute) as command:
                with self.assertRaises(launch_smoke.SmokeError) as raised:
                    with visual.DisposableRendererSimulator(Path(directory)): pass
            self.assertIs(raised.exception, original)
            self.assertEqual(path.read_text(), 'keep existing private report')
            operations = [call.args[0][2] for call in command.call_args_list]
            self.assertEqual(operations.count('boot'), 0)
            self.assertEqual(operations.count('bootstatus'), 1)

    def test_boot_evidence_is_closed_and_cannot_store_exception_text_identifiers_or_secrets(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
        original.args = ('private-path /Users/private secret=' + identifier,)
        def execute(arguments, **kwargs):
            if arguments[2] == 'list': return json.dumps({'devices': {visual.RUNTIME: []}})
            if arguments[2] == 'create': return identifier
            if arguments[2] == 'bootstatus': raise original
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=execute):
            with self.assertRaises(launch_smoke.SmokeError):
                with visual.DisposableRendererSimulator(Path(directory)): pass
            path = Path(directory) / 'boot-attempts.json'
            report = json.loads(path.read_text())
            self.assertEqual(set(report), {'boot_evidence_only', 'native_tests_started', 'device_model', 'runtime',
                'restart_retry_limit', 'boot_status_timeout_seconds', 'first_attempt_successful', 'recovered_on_retry', 'ready', 'attempts'})
            self.assertNotIn(identifier, path.read_text())
            self.assertNotIn('/Users/private', path.read_text())
            self.assertNotIn('secret=', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(Path(directory).glob('.renderer-boot-*')))

    def test_bootstatus_149_requires_fresh_exact_owned_booted_state_and_retains_original_command_failure(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandFailed', 'waitForBoot', return_code=149)
        inventory_count = 0
        def execute(arguments, **kwargs):
            nonlocal inventory_count
            if arguments[2] == 'list':
                inventory_count += 1
                if inventory_count == 1: return json.dumps({'devices': {visual.RUNTIME: []}})
                self.assertEqual(kwargs, {'timeout': 15, 'phase': 'waitForBoot'})
                return json.dumps({'devices': {visual.RUNTIME: [{'udid': identifier, 'name': visual.DEVICE_NAME, 'state': 'Booted'}]}})
            if arguments[2] == 'create': return identifier
            if arguments[2] == 'bootstatus': raise original
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=execute) as command:
            with visual.DisposableRendererSimulator(Path(directory)):
                report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
                self.assertTrue(report['ready'])
                self.assertFalse(report['first_attempt_successful'])
                self.assertFalse(report['recovered_on_retry'])
                self.assertTrue(report['attempts'][0]['accepted_already_booted'])
                self.assertTrue(report['attempts'][0]['owned_booted_state_verified'])
                self.assertEqual(report['attempts'][0]['bootstatus_failure'], original.evidence())
            operations = [call.args[0][2] for call in command.call_args_list]
            self.assertEqual(operations, ['list', 'create', 'bootstatus', 'list', 'shutdown', 'delete'])
            self.assertNotIn('boot', operations)

    def test_bootstatus_149_does_not_accept_other_wrong_runtime_nonbooted_or_malformed_device_states(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        other = '99999999-2222-3333-4444-555555555555'
        owned = {'udid': identifier, 'name': visual.DEVICE_NAME, 'state': 'Booted'}
        inventories = [
            {'devices': {visual.RUNTIME: [dict(owned, udid=other)]}},
            {'devices': {visual.RUNTIME + '-other': [owned]}},
            {'devices': {visual.RUNTIME: [dict(owned, state='Booting')]}},
            {'devices': {visual.RUNTIME: [dict(owned, name='Personal simulator')]}},
            {'devices': {visual.RUNTIME: [owned, dict(owned, state='Shutdown')]}},
            {'devices': {visual.RUNTIME: []}},
            {'devices': {visual.RUNTIME: 'malformed collection'}},
            'unparsed private payload',
        ]
        for inventory in inventories:
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as directory:
                original = launch_smoke.SmokeError('commandFailed', 'waitForBoot', return_code=149)
                count = 0
                def execute(arguments, **kwargs):
                    nonlocal count
                    if arguments[2] == 'list':
                        count += 1
                        if count == 1: return json.dumps({'devices': {visual.RUNTIME: []}})
                        return json.dumps(inventory) if isinstance(inventory, dict) else inventory
                    if arguments[2] == 'create': return identifier
                    if arguments[2] == 'bootstatus': raise original
                    return ''
                with patch.object(visual, 'command', side_effect=execute) as command:
                    with self.assertRaises(launch_smoke.SmokeError) as raised:
                        with visual.DisposableRendererSimulator(Path(directory)): self.fail('unproven device must not be accepted')
                self.assertIs(raised.exception, original)
                report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
                self.assertFalse(report['ready']); self.assertEqual(len(report['attempts']), 2)
                self.assertTrue(all(not attempt.get('accepted_already_booted', False) for attempt in report['attempts']))
                for call in command.call_args_list:
                    arguments = call.args[0]
                    if arguments[2] in {'bootstatus', 'shutdown', 'delete'}: self.assertEqual(arguments[3], identifier)
                self.assertNotIn(other, (Path(directory) / 'boot-attempts.json').read_text())
                self.assertNotIn('unparsed private payload', (Path(directory) / 'boot-attempts.json').read_text())

    def test_bootstatus_149_state_query_timeout_is_not_substituted_for_original_failure(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandFailed', 'waitForBoot', return_code=149)
        query_error = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
        count = 0
        def execute(arguments, **kwargs):
            nonlocal count
            if arguments[2] == 'list':
                count += 1
                if count == 1: return json.dumps({'devices': {visual.RUNTIME: []}})
                raise query_error
            if arguments[2] == 'create': return identifier
            if arguments[2] == 'bootstatus': raise original
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=execute):
            with self.assertRaises(launch_smoke.SmokeError) as raised:
                with visual.DisposableRendererSimulator(Path(directory)): pass
            self.assertIs(raised.exception, original)
            report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
            self.assertFalse(report['ready'])
            self.assertTrue(all(attempt['state_query_failure'] == query_error.evidence() for attempt in report['attempts']))

    def test_bootstatus_timeout_never_uses_a_booted_state_fallback(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
        inventory_count = 0
        def execute(arguments, **kwargs):
            nonlocal inventory_count
            if arguments[2] == 'list':
                inventory_count += 1
                self.assertEqual(inventory_count, 1, 'timeouts must not ask for a permissive Booted fallback')
                return json.dumps({'devices': {visual.RUNTIME: []}})
            if arguments[2] == 'create': return identifier
            if arguments[2] == 'bootstatus': raise original
            return ''
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=execute) as command:
            with self.assertRaises(launch_smoke.SmokeError) as raised:
                with visual.DisposableRendererSimulator(Path(directory)): pass
            self.assertIs(raised.exception, original)
            report = json.loads((Path(directory) / 'boot-attempts.json').read_text())
            self.assertFalse(report['ready'])
            self.assertTrue(all('owned_booted_state_verified' not in attempt for attempt in report['attempts']))
            waits = [call for call in command.call_args_list if call.args[0][2] == 'bootstatus']
            self.assertEqual(len(waits), 2)
            self.assertTrue(all(call.kwargs['timeout'] == 240 for call in waits))

    def test_fixed_native_execution_evidence_requires_actual_cases_and_redacts_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); logs = root / 'logs'; logs.mkdir(); out = root / 'out'
            text = '\n'.join("Test Case '-[Fixture.CustomeOptionsControllerTests test" + str(i) + "]' passed" for i in range(8))
            identifier = '11111111-2222-3333-4444-555555555555'
            text += '\nxcodebuild test-without-building -destination id=' + identifier + ' -derivedDataPath fixture\n'
            (logs / 'test.log').write_text(text + '\nprivate-fixture-secret\n')
            (logs / 'test.xml').write_text('<testsuites/>')
            with patch.object(profile, 'log_directory', return_value=logs):
                result = profile.retain_native_logs(root, profile.UNIT_TARGET, out, ['private-fixture-secret'], identifier)
                self.assertEqual(result['passed'], 8)
                self.assertNotIn('private-fixture-secret', (out / 'test.log').read_text())
                (logs / 'test.log').write_text('Bazel passed but no actual XCTest cases')
                with self.assertRaises(visual.InvalidVisualEvidence): profile.retain_native_logs(root, profile.UNIT_TARGET, out, [], identifier)

    def test_wrapper_captures_ipa_before_tests_repoint_build_outputs_and_keeps_secrets_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in profile.RENDERER_SOURCES:
                source = root / name; source.parent.mkdir(parents=True, exist_ok=True); source.write_text('synthetic fixture source')
            original = root / 'bazel-bin/Tests/CustomeOptionsHost/CustomeOptionsHost.ipa'
            calls = []
            order = []
            def execute(args, env, secrets):
                self.assertEqual(order[0], 'bootReady', 'simulator must warm before any Bazel compilation')
                self.assertNotIn('TELEGRAM_API_ID', env)
                self.assertNotIn('TELEGRAM_API_HASH', env)
                self.assertNotIn('GH_TOKEN', env)
                self.assertNotIn('ACTIONS_RUNTIME_TOKEN', env)
                target = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--target=')); calls.append(target); order.append(target)
                if target == profile.APP_TARGET:
                    original.parent.mkdir(parents=True); original.write_bytes(b'exact renderer IPA')
                else:
                    copied = root / 'build/fork-evidence/custom-options-ui/CustomeOptionsHost-simulator.ipa'
                    self.assertEqual(copied.read_bytes(), b'exact renderer IPA')
                    original.write_bytes(b'stale repointed Bazel product')
            simulator = SimpleNamespace(identifier='11111111-2222-3333-4444-555555555555',
                start_recording=lambda: None, stop_recording=lambda: None, recording_started=True, recording_return_code=0)
            context = unittest.mock.MagicMock()
            def entered(): order.append('bootReady'); return simulator
            context.__enter__.side_effect = entered
            def retained(bazel, label, destination, secrets, identifier):
                self.assertEqual(identifier, simulator.identifier)
                destination.mkdir(parents=True)
                (destination / 'test.log').write_text('synthetic source-contract fixture')
                return {'target': label, 'owned_simulator_verified': True}
            env = {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'IOS_BUNDLE_ID': 'org.livara.ayugram'}
            env.update(GH_TOKEN='synthetic-ci-token', ACTIONS_RUNTIME_TOKEN='synthetic-actions-token')
            with patch.object(profile, 'ROOT', root), patch.object(profile.baseline, 'ROOT', root), patch.object(profile.baseline, 'preflight', return_value=Path('/bazel')), patch.object(profile, 'validate_runtime'), patch.object(profile.baseline, 'output', return_value='c' * 40), patch.object(profile.baseline, 'run_redacted', side_effect=execute), patch.object(profile, 'inspect_ipa', return_value={'mach_o_platform': 7}), patch.object(profile, 'DisposableRendererSimulator', return_value=context), patch.object(profile, 'retain_native_logs', side_effect=retained), patch.object(profile, 'log_directory', return_value=root), patch.object(profile, 'frames_from_log', return_value=[]), patch.object(profile, 'appearances_from_log', return_value=[]), patch.object(profile, 'export_screenshots', return_value={}), patch.object(profile, 'command', return_value=''), patch.object(profile.subprocess, 'run'):
                result = profile.run(env)
            self.assertEqual(calls, [profile.APP_TARGET, profile.UNIT_TARGET, profile.UI_TARGET])
            self.assertTrue(result['passed'])
            self.assertFalse(result['logged_in_settings_verified'])
            self.assertFalse(result['full_application_built'])
            self.assertFalse(result['physical_device_verified'])
            self.assertFalse((root / 'build-input/configuration-repository').exists())
            report = (root / 'build/fork-evidence/custom-options-ui/result.json').read_text()
            self.assertNotIn(env['TELEGRAM_API_HASH'], report)
            self.assertNotIn(simulator.identifier, report)

    def test_wrapper_attributes_boot_failure_before_any_native_tests_and_validates_runtime_first(self):
        for runtime_available in (False, True):
            with self.subTest(runtime_available=runtime_available), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in profile.RENDERER_SOURCES:
                    source = root / name; source.parent.mkdir(parents=True, exist_ok=True); source.write_text('synthetic renderer source')
                original = launch_smoke.SmokeError('commandTimedOut', 'waitForBoot', timeout=True)
                context = unittest.mock.MagicMock(); context.__enter__.side_effect = original
                def execute(arguments, env, secrets):
                    self.assertEqual(next(arg for arg in arguments if arg.startswith('--target=')), '--target=' + profile.APP_TARGET)
                    ipa = root / 'bazel-bin/Tests/CustomeOptionsHost/CustomeOptionsHost.ipa'
                    ipa.parent.mkdir(parents=True); ipa.write_bytes(b'synthetic renderer artifact')
                validation_error = visual.InvalidVisualEvidence('Fixed runtime unavailable')
                env = {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'IOS_BUNDLE_ID': 'org.livara.ayugram'}
                with patch.object(profile, 'ROOT', root), patch.object(profile.baseline, 'ROOT', root), patch.object(profile.baseline, 'preflight', return_value=Path('/bazel')), patch.object(profile.baseline, 'output', return_value='c' * 40), patch.object(profile, 'validate_runtime', side_effect=None if runtime_available else validation_error) as validate, patch.object(profile.baseline, 'run_redacted', side_effect=execute) as build, patch.object(profile, 'inspect_ipa', return_value={'mach_o_platform': 7}), patch.object(profile, 'DisposableRendererSimulator', return_value=context) as simulator, patch.object(profile.subprocess, 'run'):
                    with self.assertRaises(launch_smoke.SmokeError if runtime_available else visual.InvalidVisualEvidence) as raised:
                        profile.run(env)
                self.assertIs(raised.exception, original if runtime_available else validation_error)
                validate.assert_called_once_with()
                self.assertEqual(build.call_count, 0, 'a failed boot cannot waste a host compilation')
                self.assertEqual(simulator.call_count, 1 if runtime_available else 0)
                report = json.loads((root / 'build/fork-evidence/custom-options-ui/result.json').read_text())
                self.assertEqual(report['phase'], 'bootSimulator' if runtime_available else 'validateRendererRuntime')
                self.assertFalse(report['passed']); self.assertEqual(report['tests'], [])
                self.assertFalse(report['logged_in_settings_verified']); self.assertFalse(report['physical_device_verified'])
                if runtime_available: self.assertEqual(report['command_failure'], original.evidence())

    def test_wrapper_build_failure_after_successful_boot_cleans_simulator_and_keeps_build_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in profile.RENDERER_SOURCES:
                source = root / name; source.parent.mkdir(parents=True, exist_ok=True); source.write_text('synthetic renderer source')
            simulator = SimpleNamespace(identifier='11111111-2222-3333-4444-555555555555')
            context = unittest.mock.MagicMock()
            order = []
            def entered(): order.append('bootReady'); return simulator
            context.__enter__.side_effect = entered
            context.__exit__.side_effect = lambda *args: order.append('simulatorCleanup') or False
            original = profile.baseline.InvalidSetup('synthetic compiler failure')
            def execute(arguments, env, secrets):
                self.assertEqual(order, ['bootReady']); order.append('buildHost'); raise original
            env = {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'IOS_BUNDLE_ID': 'org.livara.ayugram'}
            with patch.object(profile, 'ROOT', root), patch.object(profile.baseline, 'ROOT', root), patch.object(profile.baseline, 'preflight', return_value=Path('/bazel')), patch.object(profile.baseline, 'output', return_value='c' * 40), patch.object(profile, 'validate_runtime'), patch.object(profile.baseline, 'run_redacted', side_effect=execute) as build, patch.object(profile, 'DisposableRendererSimulator', return_value=context), patch.object(profile.subprocess, 'run', side_effect=lambda *args, **kwargs: order.append('bazelShutdown')):
                with self.assertRaises(profile.baseline.InvalidSetup) as raised: profile.run(env)
            self.assertIs(raised.exception, original)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(order, ['bootReady', 'buildHost', 'simulatorCleanup', 'bazelShutdown'])
            report = json.loads((root / 'build/fork-evidence/custom-options-ui/result.json').read_text())
            self.assertEqual(report['phase'], 'buildHost')
            self.assertFalse(report['passed']); self.assertEqual(report['tests'], [])


if __name__ == '__main__': unittest.main()
