"""Closed renderer evidence and source/CI contracts; no native result invented."""
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custom_options_ui as profile
import custom_options_smoke as visual

ROOT = Path(__file__).resolve().parents[3]


class CustomOptionsContracts(unittest.TestCase):
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

    def test_existing_simulator_is_not_reused_or_deleted(self):
        devices = {'devices': {visual.RUNTIME: [{'name': visual.DEVICE_NAME, 'udid': 'not-ours'}]}}
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', return_value=json.dumps(devices)) as command:
            with self.assertRaises(visual.InvalidVisualEvidence):
                with visual.DisposableRendererSimulator(Path(directory)): pass
            self.assertEqual(command.call_count, 1)

    def test_disposable_lifecycle_only_shuts_down_and_deletes_created_identity(self):
        identifier = '11111111-2222-3333-4444-555555555555'
        replies = iter([json.dumps({'devices': {visual.RUNTIME: []}}), identifier, '', '', '', ''])
        with tempfile.TemporaryDirectory() as directory, patch.object(visual, 'command', side_effect=lambda *args, **kwargs: next(replies)) as command:
            with visual.DisposableRendererSimulator(Path(directory)) as simulator:
                self.assertEqual(simulator.identifier, identifier)
            calls = [call.args[0] for call in command.call_args_list]
            self.assertEqual(calls[-2], ['xcrun', 'simctl', 'shutdown', identifier])
            self.assertEqual(calls[-1], ['xcrun', 'simctl', 'delete', identifier])
            self.assertIn(visual.DEVICE_NAME, calls[1])
            self.assertNotIn('erase', ' '.join(' '.join(call) for call in calls))

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
            def execute(args, env, secrets):
                self.assertNotIn('TELEGRAM_API_ID', env)
                self.assertNotIn('TELEGRAM_API_HASH', env)
                self.assertNotIn('GH_TOKEN', env)
                self.assertNotIn('ACTIONS_RUNTIME_TOKEN', env)
                target = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--target=')); calls.append(target)
                if target == profile.APP_TARGET:
                    original.parent.mkdir(parents=True); original.write_bytes(b'exact renderer IPA')
                else:
                    copied = root / 'build/fork-evidence/custom-options-ui/CustomeOptionsHost-simulator.ipa'
                    self.assertEqual(copied.read_bytes(), b'exact renderer IPA')
                    original.write_bytes(b'stale repointed Bazel product')
            simulator = SimpleNamespace(identifier='11111111-2222-3333-4444-555555555555',
                start_recording=lambda: None, stop_recording=lambda: None, recording_started=True, recording_return_code=0)
            context = unittest.mock.MagicMock(); context.__enter__.return_value = simulator
            def retained(bazel, label, destination, secrets, identifier):
                self.assertEqual(identifier, simulator.identifier)
                destination.mkdir(parents=True)
                (destination / 'test.log').write_text('synthetic source-contract fixture')
                return {'target': label, 'owned_simulator_verified': True}
            env = {'TELEGRAM_API_ID': '1234567', 'TELEGRAM_API_HASH': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'IOS_BUNDLE_ID': 'org.livara.ayugram'}
            env.update(GH_TOKEN='synthetic-ci-token', ACTIONS_RUNTIME_TOKEN='synthetic-actions-token')
            with patch.object(profile, 'ROOT', root), patch.object(profile.baseline, 'ROOT', root), patch.object(profile.baseline, 'preflight', return_value=Path('/bazel')), patch.object(profile, 'validate_runtime'), patch.object(profile.baseline, 'output', return_value='c' * 40), patch.object(profile.baseline, 'run_redacted', side_effect=execute), patch.object(profile, 'inspect_ipa', return_value={'mach_o_platform': 7}), patch.object(profile, 'DisposableRendererSimulator', return_value=context), patch.object(profile, 'retain_native_logs', side_effect=retained), patch.object(profile, 'log_directory', return_value=root), patch.object(profile, 'frames_from_log', return_value=[]), patch.object(profile, 'export_screenshots', return_value={}), patch.object(profile, 'command', return_value=''), patch.object(profile.subprocess, 'run'):
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


if __name__ == '__main__': unittest.main()
