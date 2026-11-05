import io
import json
import os
from pathlib import Path
import plistlib
import signal
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
            path = Path(d).resolve() / 'preferences.plist'
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
    def inherited_pipe_command(self, exit_code=0, noisy_descendant=False):
        # Each test owns a new process group. Kill only that group's synthetic
        # grandchild afterward; production must never kill simulator services.
        original_popen, owned = subprocess.Popen, []
        def isolated(arguments, **kwargs):
            child = original_popen(arguments, start_new_session=True, **kwargs)
            owned.append(child)
            return child
        descendant = 'import time; time.sleep(5)'
        if noisy_descendant:
            descendant = ('import os,sys,time; parent=int(sys.argv[1]); '
                          'exec("while os.getppid() == parent: time.sleep(0.005)"); '
                          'sys.stdout.write("x" * 70000); sys.stdout.flush(); time.sleep(5)')
        code = ('import os,subprocess,sys; '
                'subprocess.Popen([sys.executable,"-c",' + repr(descendant) + ',str(os.getpid())]); '
                'sys.stdout.write("parent-output"); sys.stdout.flush(); '
                'sys.stderr.write("domain=MIInstallerErrorDomain, code=13"); sys.stderr.flush(); '
                'os._exit(' + str(exit_code) + ')')
        try:
            with mock.patch.object(launch.subprocess, 'Popen', side_effect=isolated):
                return command([sys.executable, '-c', code], timeout=2, phase='installApplication')
        finally:
            for child in owned:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass

    def test_exited_success_with_inherited_open_pipes_returns_without_false_timeout(self):
        self.assertEqual(self.inherited_pipe_command(), 'parent-output')

    def test_exited_failure_with_inherited_open_pipes_preserves_status_and_safe_errors(self):
        with self.assertRaises(SmokeError) as caught:
            self.inherited_pipe_command(exit_code=65)
        self.assertEqual(caught.exception.code, 'commandFailed')
        self.assertEqual(caught.exception.return_code, 65)
        self.assertFalse(caught.exception.timeout)
        self.assertEqual(caught.exception.system_errors, [{'domain': 'MIInstallerErrorDomain', 'code': 13}])

    def test_noisy_inherited_pipe_still_enforces_output_limit_after_parent_exit(self):
        with self.assertRaises(SmokeError) as caught:
            self.inherited_pipe_command(noisy_descendant=True)
        self.assertEqual(caught.exception.code, 'commandOutputLimit')
        self.assertEqual(caught.exception.return_code, 0)
        self.assertFalse(caught.exception.timeout)

    def test_live_child_with_closed_pipes_still_times_out(self):
        code = 'import os,time; os.close(1); os.close(2); time.sleep(2)'
        with self.assertRaises(SmokeError) as caught:
            command([sys.executable, '-c', code], timeout=0.05, phase='installApplication')
        self.assertEqual(caught.exception.code, 'commandTimedOut')
        self.assertIsNone(caught.exception.return_code)
        self.assertTrue(caught.exception.timeout)

    def test_checker_is_compiled_once_and_native_controls_run_before_any_device(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def fake(arguments, timeout=120, phase=None, compiler_log=None):
                calls.append((arguments, timeout, phase))
                if phase == 'compileScreenshotChecker':
                    Path(arguments[-1]).write_bytes(b'synthetic executable')
                    return ''
                return json.dumps({'self_test_passed': True, 'cases': 3})
            with mock.patch.object(launch, 'command', side_effect=fake):
                checker = launch.prepare_screenshot_checker(d)
            self.assertEqual(calls[0][0][:3], ['xcrun', 'swiftc', '-O'])
            self.assertEqual(calls[0][1:], (300, 'compileScreenshotChecker'))
            self.assertEqual(calls[1], ([str(checker), '--self-test'], 30, 'testScreenshotChecker'))
            self.assertEqual(len(calls), 2)

    def test_checker_native_control_failures_are_closed_and_stop_before_boot(self):
        for result in ({'self_test_passed': False, 'cases': 3}, {'self_test_passed': True, 'cases': 2},
                       {'self_test_passed': True, 'cases': 3, 'path': 'must-not-retain'}):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as d:
                def fake(arguments, timeout=120, phase=None, compiler_log=None):
                    if phase == 'compileScreenshotChecker':
                        Path(arguments[-1]).write_bytes(b'synthetic executable'); return ''
                    return json.dumps(result)
                with mock.patch.object(launch, 'command', side_effect=fake), self.assertRaises(SmokeError) as caught:
                    launch.prepare_screenshot_checker(d)
                self.assertEqual(caught.exception.phase, 'testScreenshotChecker')
                self.assertEqual(caught.exception.code, 'invalidScreenshotEvidence')
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(launch.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(launch, 'extract_app', return_value=Path(d) / 'App.app'), \
                 mock.patch.object(launch, 'prepare_screenshot_checker', side_effect=SmokeError('commandTimedOut', 'compileScreenshotChecker', timeout=True)), \
                 mock.patch.object(launch, 'command') as commands:
                with self.assertRaises(SmokeError): launch.smoke(Path('source.ipa'), Path(d) / 'evidence')
            commands.assert_not_called()
            report = json.loads((Path(d) / 'evidence/result.json').read_text())
            self.assertEqual(report['phase'], 'compileScreenshotChecker')
            self.assertFalse(report['passed'])
            self.assertEqual(report['launches'], [])

    def test_runtime_checker_failure_preserves_observed_marker_and_private_selection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); output = root / 'evidence'; checker = root / 'compiled-checker'
            calls = []
            def fake(arguments, timeout=120, phase=None):
                calls.append((arguments, timeout, phase))
                if phase == 'createDevice': return '00000000-0000-0000-0000-000000000001'
                if phase == 'getContainer': return str(root / 'fresh-data')
                if phase == 'checkScreenshot':
                    partial = json.loads((output / 'result.json').read_text())
                    self.assertEqual(partial['launches'][0]['marker'], {'stage': 'authorizationVisible', 'failure': None})
                    self.assertEqual(partial['launches'][0]['storage_kind'], 'private')
                    self.assertNotIn('screenshot', partial['launches'][0])
                    self.assertEqual(arguments[0], str(checker))
                    self.assertEqual(timeout, 30)
                    raise SmokeError('commandTimedOut', phase, timeout=True)
                return ''
            recorder = mock.Mock(); recorder.poll.return_value = 0
            with mock.patch.object(launch.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(launch, 'extract_app', return_value=root / 'App.app'), \
                 mock.patch.object(launch, 'prepare_screenshot_checker', return_value=checker), \
                 mock.patch.object(launch, 'command', side_effect=fake), \
                 mock.patch.object(launch.subprocess, 'Popen', return_value=recorder), \
                 mock.patch.object(launch, 'read_stage', return_value={'stage': 'authorizationVisible', 'failure': None}), \
                 mock.patch.object(launch, 'read_storage_kind', return_value='private') as storage, \
                 mock.patch.object(launch, 'private_account_store_identity', return_value=(1, 2)), \
                 mock.patch.object(launch.time, 'sleep'):
                with self.assertRaises(SmokeError): launch.smoke(Path('source.ipa'), output, expected_storage_kind='private')
            storage.assert_called_once()
            result = json.loads((output / 'result.json').read_text())
            self.assertEqual(result['phase'], 'checkScreenshot')
            self.assertEqual(result['launches'][0]['storage_kind'], 'private')
            self.assertEqual(result['launches'][0]['marker']['stage'], 'authorizationVisible')
            self.assertFalse(result['passed'])
            import replay_artifact
            self.assertEqual(replay_artifact.prior_smoke_report(result), result)

    def test_two_launches_use_compiled_checker_with_ready_and_private_assertions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); checker = root / 'compiled-checker'; calls = []
            def fake(arguments, timeout=120, phase=None):
                calls.append((arguments, timeout, phase))
                if phase == 'createDevice': return '00000000-0000-0000-0000-000000000001'
                if phase == 'getContainer': return str(root / 'fresh-data')
                if phase == 'checkScreenshot': return json.dumps({'nonblank': True, 'width': 1206, 'height': 2622})
                return ''
            recorder = mock.Mock(); recorder.poll.return_value = 0
            with mock.patch.object(launch.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(launch, 'extract_app', return_value=root / 'App.app'), \
                 mock.patch.object(launch, 'prepare_screenshot_checker', return_value=checker) as prepare, \
                 mock.patch.object(launch, 'command', side_effect=fake), \
                 mock.patch.object(launch.subprocess, 'Popen', return_value=recorder), \
                 mock.patch.object(launch, 'read_stage', return_value={'stage': 'authorizationVisible', 'failure': None}), \
                 mock.patch.object(launch, 'read_storage_kind', return_value='private') as storage, \
                 mock.patch.object(launch, 'private_account_store_identity', return_value=(1, 2)), \
                 mock.patch.object(launch.time, 'sleep'):
                result = launch.smoke(Path('source.ipa'), root / 'evidence', expected_storage_kind='private')
            prepare.assert_called_once()
            self.assertEqual(storage.call_count, 2)
            checks = [entry for entry in calls if entry[2] == 'checkScreenshot']
            self.assertEqual(len(checks), 2)
            self.assertTrue(all(args[0] == str(checker) and timeout == 30 for args, timeout, _ in checks))
            self.assertTrue(result['passed'])
            import replay_artifact
            self.assertEqual(replay_artifact.prior_smoke_report(result), result)

    def test_fresh_pinned_control_seeds_once_before_launch_and_requires_app_created_account_store(self):
        for store_mode in ('stable', 'missing', 'replaced'):
            creates_store, persists = store_mode != 'missing', store_mode == 'stable'
            with self.subTest(store_mode=store_mode), tempfile.TemporaryDirectory() as d:
                root = Path(d).resolve(); data = root / 'fresh-data'; data.mkdir()
                launches = []
                def fake(arguments, timeout=120, phase=None):
                    if phase == 'createDevice': return '00000000-0000-0000-0000-000000000001'
                    if phase == 'getContainer': return str(data)
                    if phase == 'launchApplication' and 'launch' in arguments:
                        preferences = data / 'Library/Preferences' / (launch.BUNDLE + '.plist')
                        self.assertEqual(read_storage_kind(preferences), 'private')
                        launches.append(True)
                        if creates_store:
                            directory = data / 'Library/Application Support/AyuGram/telegram-data/accounts-metadata'
                            if store_mode == 'replaced' and len(launches) == 2:
                                directory.rename(directory.parent / 'previous-account-store')
                            directory.mkdir(parents=True, exist_ok=True)
                    if phase == 'checkScreenshot': return json.dumps({'nonblank': True, 'width': 1206, 'height': 2622})
                    return ''
                recorder = mock.Mock(); recorder.poll.return_value = 0
                with mock.patch.object(launch.platform, 'system', return_value='Darwin'), \
                     mock.patch.object(launch, 'extract_app', return_value=root / 'App.app'), \
                     mock.patch.object(launch, 'prepare_screenshot_checker', return_value=root / 'checker'), \
                     mock.patch.object(launch, 'command', side_effect=fake), \
                     mock.patch.object(launch.subprocess, 'Popen', return_value=recorder), \
                     mock.patch.object(launch, 'read_stage', return_value={'stage': 'authorizationVisible', 'failure': None}), \
                     mock.patch.object(launch, 'seed_private_selection', wraps=launch.seed_private_selection) as seed, \
                     mock.patch.object(launch.time, 'sleep'):
                    if persists:
                        result = launch.smoke(Path('source.ipa'), root / 'evidence', 'private', seed_private_storage=True)
                    else:
                        with self.assertRaises(SmokeError) as caught:
                            launch.smoke(Path('source.ipa'), root / 'evidence', 'private', seed_private_storage=True)
                        self.assertEqual(caught.exception.phase, 'checkPrivateAccountStore')
                        result = json.loads((root / 'evidence/result.json').read_text())
                seed.assert_called_once_with(data)
                self.assertEqual(len(launches), 2 if creates_store else 1)
                self.assertEqual(result['passed'], persists)
                self.assertEqual(result['storage_test_setup'], 'freshPinnedPrivate')
                self.assertTrue(result['private_selection_seeded_before_launch'])
                self.assertTrue(result['private_account_store_absent_before_launch'])
                self.assertEqual(result['launches'][0]['private_account_store_present'], creates_store)
                if creates_store:
                    self.assertEqual(result['launches'][1]['private_account_store_stable_after_restart'], persists)
                self.assertNotIn('st_ino', json.dumps(result))
                self.assertNotIn('st_dev', json.dumps(result))
                import replay_artifact
                self.assertEqual(replay_artifact.prior_smoke_report(result), result)

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

    def test_fixed_public_compiler_failure_retains_bounded_redacted_diagnostics(self):
        original_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve(); home = root / 'private-home-sentinel'; temporary = root / 'private-temp-sentinel'
            log = root / 'checker-compiler.log'
            source = Path(launch.__file__).with_name('screenshot_check.swift')
            text = (str(source) + ':17:3: error: synthetic syntax failure\n' +
                    str(home / 'Library/cache') + ': note: module cache\n' +
                    str(temporary / 'module.swift') + ': note: temporary file\n' +
                    'GH_TOKEN=credential-sentinel-must-not-appear\n' +
                    'TELEGRAM_API_HASH=api-sentinel-must-not-appear\n' + 'x' * 70000)
            code = 'import sys; sys.stderr.write(' + repr(text) + '); sys.stderr.flush(); sys.exit(1)'
            def fake_popen(arguments, **kwargs):
                return original_popen([sys.executable, '-c', code], **kwargs)
            arguments = ['xcrun', 'swiftc', '-O', '-framework', 'CoreGraphics', '-framework', 'ImageIO',
                         str(source), '-o', str(temporary / 'checker')]
            with mock.patch.object(launch.subprocess, 'Popen', side_effect=fake_popen), \
                 mock.patch.object(launch.Path, 'home', return_value=home), \
                 mock.patch.object(launch.tempfile, 'gettempdir', return_value=str(root)), self.assertRaises(SmokeError):
                command(arguments, phase='compileScreenshotChecker', compiler_log=log)
            output = log.read_text()
            self.assertLessEqual(log.stat().st_size, launch.COMMAND_OUTPUT_LIMIT)
            self.assertIn('<SOURCE_ROOT>/tools/fork_baseline/screenshot_check.swift:17:3: error:', output)
            self.assertIn('synthetic syntax failure', output)
            self.assertIn('<HOME_ROOT>', output)
            self.assertIn('<CHECKER_TEMP>', output)
            for secret in (str(root), 'private-home-sentinel', 'private-temp-sentinel',
                           'credential-sentinel-must-not-appear', 'api-sentinel-must-not-appear'):
                self.assertNotIn(secret, output)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)

    def test_raw_simctl_errors_cannot_be_opted_into_compiler_logging(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(launch.subprocess, 'Popen') as process:
            log = Path(d) / 'checker-compiler.log'
            with self.assertRaises(ValueError):
                command(['xcrun', 'simctl', 'install', 'private'], phase='installApplication', compiler_log=log)
            process.assert_not_called()
            self.assertFalse(log.exists())

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
                     mock.patch.object(launch, 'prepare_screenshot_checker', return_value=Path(d) / 'compiled-checker'), \
                     mock.patch.object(launch, 'command', side_effect=fake):
                    with self.assertRaises(SmokeError): launch.smoke(Path('source.ipa'), output)
                value = json.loads((output / 'result.json').read_text())
                self.assertEqual(value['phase'], phase)
                self.assertEqual(value['failure']['code'], code)
                self.assertEqual(value['failure']['return_code'], status)
                self.assertEqual(value['failure']['timeout'], timed_out)
                self.assertFalse(value['passed'])
                self.assertEqual(value['launches'], [])
