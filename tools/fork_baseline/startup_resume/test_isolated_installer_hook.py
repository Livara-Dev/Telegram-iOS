#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Portable owned-process instrumentation tests; no CoreSimulator invocation."""
import contextlib
import json
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import isolated_startup as m
import installer_observation as observer

ID = '11111111-2222-3333-4444-555555555555'


class SmokeError(RuntimeError):
    def __init__(self, code, phase, return_code=None, timeout=False, **unused):
        super().__init__(code)
        self.code, self.phase = code, phase
        self.return_code, self.timeout = return_code, timeout


class Process:
    def __init__(self):
        self.pid = 12345
        self.returncode = None
        self.kills = 0
        self.stdout, self.stderr = object(), object()

    def poll(self): return self.returncode
    def wait(self, timeout=None): return self.returncode
    def kill(self):
        self.kills += 1
        self.returncode = -9


class HookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve(strict=True)
        self.output = self.base / 'replay'
        self.process, self.calls, self.observations = Process(), [], []
        self.clock = 100.0
        self.error = SmokeError('commandTimedOut', 'installApplication', timeout=True)
        self.elapsed = 120
        self.seen_wrapper = None
        self.command_finished = False
        self.smoke = SimpleNamespace(SmokeError=SmokeError)
        self.original_module = SimpleNamespace(PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError,
            Popen=self.popen)
        self.smoke.subprocess, self.smoke.command = self.original_module, self.command
        original = tempfile.mkdtemp
        with patch.object(m.tempfile, 'mkdtemp', lambda prefix: original(prefix=prefix, dir=self.base)):
            self.state = m.OwnedDeviceSet(self.smoke, self.output)
        self.state.identifier, self.state.name, self.state.created = ID, 'AYU_SMOKE_' + 'a' * 32, True
        device = self.state.root / ID
        device.mkdir(mode=0o700)
        (device / 'device.plist').write_bytes(plistlib.dumps({'UDID': ID, 'name': self.state.name,
            'deviceType': m.MODEL, 'runtime': m.RUNTIME}))
        app_parent = self.base / 'ayu-launch-smoke-test'
        app_parent.mkdir(mode=0o700)
        (app_parent / 'Telegram.app').mkdir(mode=0o700)
        self.state.checker = app_parent / 'screenshot-check'
        self.arguments = ['xcrun', 'simctl', 'install', ID, str(app_parent / 'Telegram.app')]
        self.fake_observer = SimpleNamespace(blank=observer.blank,
            validate_observation=observer.validate_observation, capture=self.capture)
        self.enable()

    def enable(self):
        self.state.installer_observer = self.fake_observer
        self.state.installer_observation = {**observer.blank(), 'state': 'notTriggered'}

    def popen(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        return self.process

    def capture(self, process, **kwargs):
        self.assertIs(process, self.process)
        self.assertEqual(process.kills, 0)
        self.assertEqual(kwargs, {'mapped_arguments': ['xcrun', 'simctl', '--set', str(self.state.root)]
            + self.arguments[2:], 'private_root': self.state.root, 'root_identity': self.state.root_identity,
            'identifier': ID, 'device_name': self.state.name, 'command_started': 100.0, 'timeout_seconds': 120})
        self.observations.append(kwargs)
        return observer.blank()

    def command(self, arguments, timeout=120, phase='extractApplication', compiler_log=None):
        self.assertEqual(timeout, 120)
        self.seen_wrapper = self.smoke.subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.clock += self.elapsed
        try:
            raise self.error
        finally:
            self.seen_wrapper.kill()
            self.command_finished = True

    def invoke(self, arguments=None, phase='installApplication'):
        with patch.object(m.time, 'monotonic', lambda: self.clock):
            with self.assertRaises(SmokeError) as captured:
                with m.adapters(self.smoke, self.state):
                    self.smoke.command(self.arguments if arguments is None else arguments, timeout=120, phase=phase)
            self.assertIs(captured.exception, self.error)
        self.assertIs(self.smoke.subprocess, self.original_module)
        self.assertEqual(self.smoke.command, self.command)
        self.assertIsNone(self.state.current)
        self.assertIsNone(self.state.install_started)

    def test_exact_timeout_observes_original_once_then_original_kill_and_failure(self):
        global_popen = subprocess.Popen
        self.invoke()
        self.assertEqual(len(self.observations), 1)
        self.assertEqual(self.process.kills, 1)
        self.assertIs(subprocess.Popen, global_popen)
        self.assertIs(self.seen_wrapper.stdout, self.process.stdout)
        self.assertEqual(self.seen_wrapper.pid, self.process.pid)
        self.assertTrue(self.command_finished)

    def test_default_disabled_returns_actual_object_and_never_observes(self):
        self.state.installer_observer = None
        self.invoke()
        self.assertIs(self.seen_wrapper, self.process)
        self.assertEqual(self.observations, [])
        self.assertEqual(self.process.kills, 1)

    def test_no_active_error_never_observes(self):
        with m.adapters(self.smoke, self.state):
            self.state.current = (self.arguments, 120, 'installApplication', None)
            self.state.install_started = 1.0
            wrapped = self.smoke.subprocess.Popen(self.arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with patch.object(m.time, 'monotonic', return_value=121): wrapped.kill()
        self.assertEqual(self.observations, [])
        self.assertEqual(self.process.kills, 1)

    def test_other_error_shapes_never_observe(self):
        errors = [SmokeError('commandOutputLimit', 'installApplication', timeout=True),
                  SmokeError('commandTimedOut', 'bootDevice', timeout=True),
                  SmokeError('commandTimedOut', 'installApplication', timeout=False),
                  SmokeError('commandTimedOut', 'installApplication', timeout=1),
                  SmokeError('commandTimedOut', 'installApplication', return_code=0, timeout=True)]
        for error in errors:
            with self.subTest(code=error.code, phase=error.phase, timeout=error.timeout, code_value=error.return_code):
                self.error, self.clock, self.process.returncode = error, 100.0, None
                self.invoke()
        self.assertEqual(self.observations, [])

    def test_before_original_deadline_never_observes(self):
        self.elapsed = 119.999
        self.invoke()
        self.assertEqual(self.observations, [])
        self.assertEqual(self.process.kills, 1)

    def test_child_already_exited_never_observes(self):
        self.process.returncode = 0
        self.invoke()
        self.assertEqual(self.observations, [])

    def test_noninstall_command_returns_original_and_never_observes(self):
        self.error = SmokeError('commandTimedOut', 'bootDevice', timeout=True)
        self.invoke(['xcrun', 'simctl', 'boot', ID], phase='bootDevice')
        self.assertIs(self.seen_wrapper, self.process)
        self.assertEqual(self.observations, [])

    def test_observer_errors_including_interruption_never_replace_failure_or_kill(self):
        for error in (ValueError('PRIVATE_CANARY'), KeyboardInterrupt()):
            with self.subTest(kind=type(error).__name__):
                self.clock, self.process.returncode = 100.0, None
                self.fake_observer.capture = lambda *args, **kwargs: (_ for _ in ()).throw(error)
                self.invoke()
                self.assertEqual(self.state.installer_observation, observer.blank())
        self.assertEqual(self.process.kills, 2)

    def test_invalid_observer_payload_is_not_retained(self):
        self.fake_observer.capture = lambda *args, **kwargs: {'PRIVATE_CANARY': 'private'}
        self.invoke()
        self.assertEqual(self.state.installer_observation, observer.blank())
        self.assertEqual(self.process.kills, 1)

    def test_repeated_kill_never_repeats_observer(self):
        self.invoke()
        try:
            raise self.error
        except SmokeError:
            self.seen_wrapper.kill()
        self.assertEqual(len(self.observations), 1)
        self.assertEqual(self.process.kills, 2)

    def test_context_changes_refuse_observation(self):
        original_command = self.command
        def changed_context(*args, **kwargs):
            self.state.install_started = None
            return original_command(*args, **kwargs)
        self.smoke.command = changed_context
        with patch.object(m.time, 'monotonic', lambda: self.clock), self.assertRaises(SmokeError) as error:
            with m.adapters(self.smoke, self.state): self.smoke.command(self.arguments, phase='installApplication')
        self.assertIs(error.exception, self.error)
        self.assertEqual(self.observations, [])
        self.assertEqual(self.process.kills, 1)

    def test_observation_receipt_atomic_regular_0600_and_closed(self):
        self.output.mkdir(mode=0o700)
        m.publish_installer_observation(self.state, self.output)
        receipt = self.output / 'installer-observation.json'
        info = receipt.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        self.assertEqual(observer.validate_observation(json.loads(receipt.read_bytes())), self.state.installer_observation)
        self.assertEqual([entry.name for entry in self.output.iterdir()], ['installer-observation.json'])
        self.assertNotIn(str(self.base), receipt.read_text())

    def test_existing_or_symlink_receipt_is_never_overwritten(self):
        self.output.mkdir(mode=0o700)
        sentinel = self.base / 'sentinel'
        sentinel.write_bytes(b'PRIVATE_SENTINEL')
        target = self.output / 'installer-observation.json'
        target.symlink_to(sentinel)
        with self.assertRaises(FileExistsError): m.publish_installer_observation(self.state, self.output)
        self.assertTrue(target.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'PRIVATE_SENTINEL')
        self.assertEqual([entry.name for entry in self.output.iterdir()], ['installer-observation.json'])

    def test_publication_failure_does_not_leak_partial_file(self):
        self.output.mkdir(mode=0o700)
        with patch.object(m.os, 'fsync', side_effect=OSError('private')):
            with self.assertRaises(OSError): m.publish_installer_observation(self.state, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_recorder_returns_original_process_not_installer_wrapper(self):
        with m.adapters(self.smoke, self.state):
            process = self.smoke.subprocess.Popen(['xcrun', 'simctl', 'io', ID, 'recordVideo',
                '--codec=h264', '--force', str(self.state.output / 'launch.mp4')],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertIs(process, self.process)
        self.assertEqual(self.observations, [])

    def run_failed_stock_replay(self, publication_error=False):
        ipa = self.base / 'Telegram.ipa'
        ipa.write_bytes(b'synthetic owned input')
        original_result, original_replay = b'{"stock-result":"unchanged"}', b'{"stock-replay":"unchanged"}'
        def replay(*args, **kwargs):
            target = self.output / 'main-app-only/launch-smoke'
            target.mkdir(mode=0o700, parents=True)
            (target / 'result.json').write_bytes(original_result)
            (self.output / 'replay.json').write_bytes(original_replay)
            raise self.error
        @contextlib.contextmanager
        def modules(*args):
            yield self.smoke, SimpleNamespace(replay=replay)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(m.platform, 'system', return_value='Darwin'))
            stack.enter_context(patch.object(m.platform, 'machine', return_value='arm64'))
            stack.enter_context(patch.object(m, 'digest', return_value=m.IPA_SHA256))
            stack.enter_context(patch.object(m, 'pinned_modules', modules))
            stack.enter_context(patch.object(m, 'OwnedDeviceSet', return_value=self.state))
            if publication_error:
                stack.enter_context(patch.object(m, 'publish_installer_observation', side_effect=OSError('PRIVATE_CANARY')))
            with self.assertRaises(SmokeError) as caught:
                m.run(ipa, self.output, self.base, installer_diagnostics=True)
        self.assertIs(caught.exception, self.error)
        self.assertEqual((self.output / 'main-app-only/launch-smoke/result.json').read_bytes(), original_result)
        self.assertEqual((self.output / 'replay.json').read_bytes(), original_replay)
        self.assertIs(json.loads((self.output / 'cleanup.json').read_bytes())['owned_device_deleted'], False)
        self.assertIs(self.smoke.subprocess, self.original_module)

    def test_run_preserves_original_failure_and_stock_reports_with_closed_not_triggered_receipt(self):
        self.run_failed_stock_replay()
        receipt = observer.validate_observation(json.loads((self.output / 'installer-observation.json').read_bytes()))
        self.assertEqual(receipt['state'], 'notTriggered')
        self.assertIs(receipt['startup_verified'], False)

    def test_receipt_publication_error_does_not_replace_stock_run_failure(self):
        self.run_failed_stock_replay(publication_error=True)
        self.assertFalse((self.output / 'installer-observation.json').exists())

    def test_cli_diagnostics_default_off_and_explicit_opt_in(self):
        arguments = ['isolated_startup.py', '--ipa', 'app.ipa', '--output', 'replay', '--source-root', 'source']
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(m.sys, 'argv', arguments + (['--installer-diagnostics'] if enabled else [])):
                with patch.object(m, 'run') as run, patch('builtins.print'):
                    self.assertEqual(m.main(), 0)
                    self.assertIs(run.call_args.kwargs['installer_diagnostics'], enabled)


if __name__ == '__main__':
    unittest.main()
