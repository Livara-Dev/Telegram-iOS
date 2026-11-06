# SPDX-License-Identifier: GPL-2.0-or-later
# Created for AyuGram iOS on 2026-11-06.
"""Portable contracts; no fresh XCTest or macOS UI result is inferred here."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import custom_options_replay as replay

ROOT = TOOLS.parents[1]
HEAD = 'd' * 40
IDENTIFIER = '11111111-2222-3333-4444-555555555555'
CONTAINER = '99999999-8888-7777-6666-555555555555'


def device_tree(home, name='AYU_OPTIONS_ROOT_fixture'):
    device = home / 'Library/Developer/CoreSimulator/Devices' / IDENTIFIER
    device.mkdir(parents=True)
    (device / 'device.plist').write_bytes(plistlib.dumps({'UDID': IDENTIFIER, 'name': name,
        'deviceType': replay.DEVICE_TYPE, 'runtime': replay.RUNTIME}))
    candidate = device / 'data/Containers/Bundle/Application' / CONTAINER / 'CustomeOptionsHost.app'
    candidate.parent.mkdir(parents=True)
    return device, candidate


def metadata():
    return {'CFBundleIdentifier': replay.BUNDLE, 'CFBundleExecutable': 'CustomeOptionsHost', 'CFBundleVersion': '1',
        'CFBundleShortVersionString': '1.0', 'CFBundleSupportedPlatforms': ['iPhoneSimulator'], 'MinimumOSVersion': '15.0',
        'UIDeviceFamily': [1], 'CFBundleDisplayName': 'AyuGram Renderer Test', 'DTXcode': '2710', 'DTXcodeBuild': '27A9275',
        'DTPlatformVersion': '27.1'}


def fixture(root):
    for name in replay.NATIVE_SOURCES:
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((ROOT / name).read_bytes())
    buffer = io.BytesIO(); prefix = 'Payload/CustomeOptionsHost.app/'
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr(prefix, b'')
        z.writestr(prefix + 'Info.plist', plistlib.dumps(metadata()))
        z.writestr(prefix + 'CustomeOptionsHost', struct.pack('<8I', 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0) +
            struct.pack('<6I', 0x32, 24, 7, 0xf0000, 0x1b0100, 0))
        z.writestr(prefix + 'PkgInfo', b'APPL????')
        z.writestr(prefix + '_CodeSignature/CodeResources', b'synthetic-not-a-native-signature')
    ipa = buffer.getvalue(); ipa_sha = hashlib.sha256(ipa).hexdigest()
    result = {'profile': 'synthetic_production_renderer', 'source_commit': replay.SOURCE, 'passed': True, 'phase': 'complete',
        'logged_in_settings_verified': False, 'physical_device_verified': False, 'full_application_built': False,
        'authentication_codes_used': False, 'account_or_network_client_created': False, 'device_model': 'iPhone 15 Pro',
        'simulator_runtime': '27.0', 'renderer_sources': replay.RENDERER_SOURCES, 'simulator_host_sha256': ipa_sha,
        'tests': [{'target': '//Tests/CustomeOptionsHost:CustomeOptionsControllerTests', 'executed': 8, 'passed': 8, 'failed': 0, 'skipped': 0, 'owned_simulator_verified': True},
                  {'target': '//Tests/CustomeOptionsHost:CustomeOptionsInteractionTests', 'executed': 1, 'passed': 1, 'failed': 0, 'skipped': 0, 'owned_simulator_verified': True}]}
    members = {replay.IPA_MEMBER: ipa, 'custom-options-ui/result.json': json.dumps(result).encode()}
    members['custom-options-ui/SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name.removeprefix('custom-options-ui/') + '\n' for name, data in members.items()).encode()
    members['SHA256SUMS'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n' for name, data in members.items()).encode()
    archive = root / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name, data in members.items(): z.writestr(name, data)
    outer_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    artifact = {'id': replay.ARTIFACT, 'name': 'custom-options-ui-' + replay.SOURCE, 'digest': 'sha256:' + outer_sha,
        'expired': False, 'size_in_bytes': archive.stat().st_size,
        'workflow_run': {'id': replay.RUN, 'head_sha': replay.SOURCE, 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': replay.RUN, 'head_sha': replay.SOURCE, 'path': replay.artifacts.WORKFLOW, 'status': 'completed', 'conclusion': 'success',
        'repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY}, 'head_repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY}}
    jobs = {'total_count': 1, 'jobs': [{'id': 22, 'run_id': replay.RUN, 'name': 'custom-options-ui', 'status': 'completed', 'conclusion': 'success',
        'steps': [{'name': replay.COMPILE_STEP, 'status': 'completed', 'conclusion': 'success'}]}]}
    def api(endpoint):
        if '/jobs?' in endpoint: return jobs
        if '/artifacts?' in endpoint: return {'total_count': 1, 'artifacts': [artifact]}
        return run
    def fetch(identifier, target):
        if identifier != replay.ARTIFACT: raise AssertionError('unfixed artifact')
        target.write_bytes(archive.read_bytes())
    return artifact, run, jobs, api, fetch, outer_sha, ipa_sha


def complete(data=b''):
    return {'status': 'completed', 'exit_code': 0, 'timeout': False, 'output_limit': False, 'captured_bytes': len(data)}, data


def self_controls(kind=None):
    def observation(regions):
        expected = any(x['kind'] == 'expected' for x in regions)
        return {'state': 'completed', 'expected_title_seen': expected,
            'expected_title_in_navigation_region': any(x['kind'] == 'expected' and x['rect'][1] + x['rect'][3] / 2 > .8 for x in regions),
            'known_variant_seen': any(x['kind'] == 'variant' for x in regions),
            'recognized_observations': len(regions), 'known_title_regions': regions, 'passed': False,
            'cpu_compute_selected': True, 'error': None}
    expected = {'kind': 'expected', 'rect': [.2, .86, .35, .06]}
    controls = {'positive': observation([expected]),
        'wrong': observation([{'kind': 'negative', 'rect': [.2, .86, .35, .06]}]), 'blank': observation([])}
    if kind == 'selfSpelling': controls['positive'] = observation([{'kind': 'variant', 'rect': [.2, .86, .35, .06]}])
    if kind == 'selfOrientation': controls['positive'] = observation([{'kind': 'expected', 'rect': [.2, .05, .35, .06]}])
    if kind == 'selfWrong': controls['wrong'] = observation([expected])
    if kind == 'selfBlank': controls['blank'] = observation([expected])
    if kind == 'selfVision': controls['positive'].update(state='visionUnavailable', expected_title_seen=False,
        expected_title_in_navigation_region=False, recognized_observations=0, known_title_regions=[], error={'domain': 'Vision', 'code': 9})
    for name, control in controls.items():
        control['passed'] = control['state'] == 'completed' and (control['expected_title_in_navigation_region'] if name == 'positive' else not control['expected_title_seen'])
    return {'self_test_passed': all(x['passed'] for x in controls.values()), 'cases': 3, 'controls': controls}


class Native:
    def __init__(self, root, kind=None, filesystem=False):
        self.root = root; self.kind = kind; self.calls = []; self.device_name = None; self.app = None
        self.filesystem = filesystem; self.home = root / 'owned-home'
        self.installed = root / 'owned-installed/CustomeOptionsHost.app'
    def __call__(self, args, timeout=30):
        self.calls.append((args, timeout))
        if args[0] == '/usr/bin/codesign':
            if self.kind == 'signature': return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}, b''
            if self.kind == 'installedSignature' and args[-1] == str(self.installed): return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}, b''
        elif args[:2] == ['xcodebuild', '-version']: return complete(b'Xcode 27.1\nBuild version 27A9275\n')
        elif args[:2] == ['xcrun', 'swiftc']: Path(args[-1]).write_bytes(b'synthetic helper')
        elif len(args) == 3 and args[1] == '--self-test':
            Path(args[2]).write_bytes(b'synthetic known-word PNG, never a real screenshot')
            controls = self_controls(self.kind)
            if self.kind == 'selfMalformed': controls['raw_ocr'] = 'PRIVATE-CANARY'
            if self.kind == 'selfCrashed': return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': 18}, b'{"verified":false}'
            if self.kind == 'largePositive':
                with Path(args[2]).open('ab') as file: file.truncate(1024**2 + 1)
            data = json.dumps(controls).encode()
            if not controls['self_test_passed'] or self.kind in ('selfMalformed', 'selfMismatch', 'selfTimeout'):
                if self.kind == 'selfMismatch': data = json.dumps(self_controls('selfSpelling')).encode()
                if self.kind == 'selfTimeout': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': len(data)}, data
                if self.kind == 'selfMismatch': return complete(data)
                return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': len(data)}, data
            return complete(data)
        elif args[:4] == ['xcrun', 'simctl', 'list', 'runtimes']:
            return complete(json.dumps({'runtimes': [{'identifier': replay.RUNTIME, 'isAvailable': True}]}).encode())
        elif args[:4] == ['xcrun', 'simctl', 'list', '--json']:
            self.device_query = args
            return complete(json.dumps({'devicetypes': [{'identifier': replay.DEVICE_TYPE}]}).encode())
        elif args[:3] == ['xcrun', 'simctl', 'create']:
            self.device_name = args[3]; return complete(IDENTIFIER.encode())
        elif args[:3] == ['xcrun', 'simctl', 'bootstatus'] and self.kind in ('149', '149wrong'):
            return {'status': 'commandFailed', 'exit_code': 149, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}, b''
        elif args[:4] == ['xcrun', 'simctl', 'list', 'devices']:
            if args == ['xcrun', 'simctl', 'list', 'devices', '--json']:
                if self.kind == 'cleanupTimedOut': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
                if self.kind == 'cleanupMalformed': return complete(b'{"devices":')
                return complete(json.dumps({'devices': {replay.RUNTIME: [{'udid': IDENTIFIER.lower()}] if self.kind == 'cleanup' else []}}).encode())
            return complete(json.dumps({'devices': {replay.RUNTIME: [{'udid': IDENTIFIER, 'name': self.device_name,
                'deviceTypeIdentifier': replay.DEVICE_TYPE, 'state': 'Booted' if self.kind == '149' else 'Shutdown'}]}}).encode())
        elif args[:3] == ['xcrun', 'simctl', 'install']:
            self.app = Path(args[-1])
            if self.kind == 'install': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
            if self.filesystem:
                device, self.installed = device_tree(self.home, self.device_name)
                if self.kind == 'wrongOwnedDevice':
                    path = device / 'device.plist'; value = plistlib.loads(path.read_bytes()); value['deviceType'] += '.wrong'
                    path.write_bytes(plistlib.dumps(value))
                if self.kind == 'unknownSchema': (device / 'device.plist').write_bytes(plistlib.dumps({'UDID': IDENTIFIER}))
            self.installed.parent.mkdir(parents=True, exist_ok=True); shutil.copytree(self.app, self.installed)
            if self.kind == 'installed': (self.installed / 'CustomeOptionsHost').write_bytes(b'wrong executable')
            if self.kind == 'installedXML': (self.installed / 'Info.plist').write_bytes(b'<plist><dict>PRIVATE-CANARY</broken>')
            if self.kind == 'extraFile': (self.installed / 'UnexpectedExecutable').write_bytes(b'PRIVATE-CANARY')
            if self.kind == 'extraDir': (self.installed / 'UnexpectedDirectory').mkdir()
            if self.kind == 'extraSymlink': (self.installed / 'UnexpectedLink').symlink_to(self.app / 'CustomeOptionsHost')
        elif args[:3] == ['xcrun', 'simctl', 'get_app_container']:
            if self.filesystem and self.kind != 'unknownSchema': raise AssertionError('Attested owned filesystem must not wait on optional API lookup')
            if timeout != 5: raise AssertionError('API fallback must be bounded to five seconds')
            return complete(str(self.installed).encode())
        elif args[:3] == ['xcrun', 'simctl', 'io'] and args[4] == 'screenshot': Path(args[-1]).write_bytes(b'synthetic PNG checked by mocked native Vision')
        elif len(args) == 3 and args[0].endswith('visible-root-check') and args[1] == '--metadata':
            return complete(b'{"width":1179,"height":2556}')
        elif len(args) == 2 and args[0].endswith('visible-root-check'):
            if self.kind == 'mutate': (self.root / replay.OUTPUT / 'CustomeOptionsHost-simulator.ipa').write_bytes(b'changed')
            return complete(json.dumps({'root_title_visible': self.kind != 'title', 'width': 1179, 'height': 2556}).encode())
        elif args[:3] == ['xcrun', 'simctl', 'shutdown'] and self.kind in ('cleanup', 'shutdownOnly'):
            return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
        elif args[:3] == ['xcrun', 'simctl', 'delete'] and self.kind == 'deleteFailed':
            return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}, b''
        return complete()
    def popen(self, args, **kwargs):
        if set(kwargs['env']) - replay.child_environment().keys(): raise AssertionError('unbounded child environment')
        for key in ('GH_TOKEN', 'TELEGRAM_API_HASH', 'DYLD_INSERT_LIBRARIES'):
            if key in kwargs['env']: raise AssertionError('credential inherited')
        if not kwargs.get('start_new_session'): raise AssertionError('unowned recorder')
        self.asserted_pipe_capture = kwargs['stdout'] == replay.subprocess.PIPE and kwargs['stderr'] == replay.subprocess.PIPE
        if not self.asserted_pipe_capture: raise AssertionError('both recorder pipes must be private bounded readers')
        if self.kind == 'recordSpawn': raise OSError('PRIVATE-CANARY /Users/private/recording')
        pipes = {name: os.pipe() for name in ('stdout', 'stderr')}
        data = {'stdout': b'', 'stderr': b'Recording started'}
        if self.kind == 'recordStdout': data = {'stdout': b'Recording started', 'stderr': b''}
        if self.kind == 'recordEarly': data = {'stdout': b'recorder failed /Users/private PRIVATE-CANARY', 'stderr': b'11111111-2222-3333-4444-555555555555'}
        if self.kind == 'recordLimit': data = {'stdout': b'x' * 32768, 'stderr': b''}
        def writer():
            for name, (_, descriptor) in pipes.items():
                try:
                    for start in range(0, len(data[name]), 4096): os.write(descriptor, data[name][start:start + 4096])
                except OSError: pass
                finally: os.close(descriptor)
        threading.Thread(target=writer, daemon=True).start()
        Path(args[-1]).write_bytes(b'x' * 2048)
        if self.kind == 'largeVideo':
            with Path(args[-1]).open('ab') as file: file.truncate(32 * 1024**2 + 1)
        class Recorder:
            pid = 987654321
            def __init__(self, kind):
                self.kind = kind; self.code = 1 if kind == 'recordEarly' else None
                self.stdout = os.fdopen(pipes['stdout'][0], 'rb', buffering=0)
                self.stderr = os.fdopen(pipes['stderr'][0], 'rb', buffering=0)
            def poll(self): return self.code
            def send_signal(self, value):
                if self.kind != 'recordStop': self.code = 0
            def kill(self): self.code = -9
            def wait(self, timeout):
                if self.kind == 'recordStop' and self.code is None: raise replay.subprocess.TimeoutExpired('synthetic', timeout)
                self.code = 0 if self.code is None else self.code
                return self.code
        return Recorder(self.kind)


class RetainedSettingsReplayTests(unittest.TestCase):
    def launch_arguments(self):
        return ['xcrun', 'simctl', 'launch', IDENTIFIER, replay.BUNDLE,
                '-AppleLanguages', '(en)', '-AppleLocale', 'en_US']

    def test_simctl_adapter_strips_and_restores_environment_without_routing_other_commands(self):
        variables = {'PATH': os.environ.get('PATH', ''), 'HOME': os.environ.get('HOME', ''),
                     'GH_' + 'TOKEN': 'test-only', 'TELEGRAM_' + 'API_HASH': 'test-only',
                     'DYLD_INSERT_LIBRARIES': 'PRIVATE-CANARY', 'UNRELATED_PRIVATE_VALUE': 'PRIVATE-CANARY'}
        with patch.dict(os.environ, variables, clear=True):
            original = dict(os.environ)
            for fails in (False, True):
                def observed(arguments, timeout, phase):
                    self.assertEqual(arguments, self.launch_arguments()); self.assertEqual(timeout, 7)
                    self.assertEqual(phase, 'launchApplication')
                    self.assertEqual(dict(os.environ), replay.child_environment())
                    self.assertFalse(any(k in os.environ for k in variables if k not in ('PATH', 'HOME')))
                    if fails: raise replay.launch_smoke.SmokeError('commandTimedOut', phase, timeout=True)
                    return 'retained stdout'
                with patch.object(replay.launch_smoke, 'command', side_effect=observed):
                    status, data = replay.ui_native_command(self.launch_arguments(), timeout=7)
                self.assertEqual(dict(os.environ), original)
                self.assertEqual(status['captured_bytes_scope'], 'returnedStdoutPayloadBytes')
                self.assertEqual(status['supervisor'], 'launchSmokeCommand')
                self.assertEqual(data, b'' if fails else b'retained stdout')
            sentinel = ({'status': 'commandFailed'}, b'{"self_test_passed":false}')
            with patch.object(replay, 'native_command', return_value=sentinel) as native, \
                    patch.object(replay.launch_smoke, 'command') as startup:
                self.assertEqual(replay.ui_native_command(['/private/fixed-checker', '--self-test'], timeout=9), sentinel)
                native.assert_called_once_with(['/private/fixed-checker', '--self-test'], timeout=9); startup.assert_not_called()

    def test_simctl_adapter_preserves_closed_failure_categories_without_false_output_counts(self):
        for code, state, result, timeout in (('commandTimedOut', 'timedOut', None, True),
                                            ('commandFailed', 'commandFailed', 149, False),
                                            ('commandOutputLimit', 'outputLimit', 0, False),
                                            ('commandUnavailable', 'unavailable', None, False)):
            with self.subTest(code=code):
                error = replay.launch_smoke.SmokeError(code, 'launchApplication', result, timeout,
                    [{'domain': 'FBSOpenApplicationServiceErrorDomain', 'code': 3}])
                with patch.object(replay.launch_smoke, 'command', side_effect=error):
                    status, data = replay.ui_native_command(self.launch_arguments())
                self.assertEqual(status['status'], state); self.assertEqual(status['failure'], error.evidence())
                self.assertEqual(status['exit_code'], result); self.assertEqual(status['timeout'], timeout)
                self.assertEqual(status['output_limit'], code == 'commandOutputLimit')
                self.assertEqual(status['captured_bytes'], 0); self.assertEqual(data, b'')
                replay.validate_simctl_status(status, self.launch_arguments())
        for error in (OSError('PRIVATE-CANARY'), ValueError('PRIVATE-CANARY'),
                      UnicodeDecodeError('utf-8', b'PRIVATE-CANARY\xff', 14, 15, 'PRIVATE-CANARY')):
            with self.subTest(error=type(error).__name__), patch.object(replay.launch_smoke, 'command', side_effect=error):
                status, data = replay.ui_native_command(self.launch_arguments())
                self.assertEqual(status['status'], 'unavailable'); self.assertEqual(data, b'')
                replay.validate_simctl_status(status, self.launch_arguments())
                self.assertNotIn('PRIVATE-CANARY', json.dumps(status))

    def test_simctl_adapter_rejects_unexpected_operations_and_failure_metadata(self):
        for arguments in (['xcrun', 'simctl', 'erase', 'all'], ['xcrun', 'simctl', 'shutdown', 'booted'],
                          ['xcrun', 'simctl', 'launch', IDENTIFIER, 'unrelated.bundle'],
                          ['xcrun', 'simctl', 'io', IDENTIFIER, 'recordVideo', '/private/output.mp4']):
            with self.subTest(arguments=arguments), patch.object(replay.launch_smoke, 'command') as startup:
                with self.assertRaisesRegex(replay.ReplayError, '^settings_simulator_command_not_allowed$'):
                    replay.ui_native_command(arguments)
                startup.assert_not_called()
        error = replay.launch_smoke.SmokeError('commandFailed', 'launchApplication', 1)
        with patch.object(replay.launch_smoke, 'command', side_effect=error):
            status, _ = replay.ui_native_command(self.launch_arguments())
        for key, value in (('captured_bytes_scope', 'PRIVATE-CANARY'), ('failure', {**error.evidence(), 'private': 'PRIVATE-CANARY'})):
            with self.subTest(key=key), self.assertRaisesRegex(replay.ReplayError, '^settings_command_evidence_invalid$'):
                replay.validate_simctl_status({**status, key: value}, self.launch_arguments())

    def test_simctl_adapter_reuses_bounded_post_exit_drain_without_killing_live_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); heartbeat = root / 'heartbeat'
            worker = ('import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); '
                      'exec("while True:\\n p.write_text(str(time.monotonic_ns()))\\n time.sleep(0.01)")')
            leader = ('import os,pathlib,subprocess,sys,time; p=pathlib.Path(sys.argv[1]); '
                      'subprocess.Popen([sys.executable,"-c",' + repr(worker) + ',str(p)]); '
                      'exec("for _ in range(200):\\n if p.exists(): break\\n time.sleep(0.01)"); '
                      'sys.stdout.write("parent-output"); sys.stdout.flush(); os._exit(0)')
            original_popen, owned = subprocess.Popen, []
            def isolated(arguments, **options):
                self.assertEqual(arguments, self.launch_arguments())
                child = original_popen([sys.executable, '-c', leader, str(heartbeat)], start_new_session=True, **options)
                owned.append(child); return child
            try:
                with patch.object(replay.launch_smoke.subprocess, 'Popen', side_effect=isolated):
                    started = time.monotonic(); status, data = replay.ui_native_command(self.launch_arguments(), timeout=2)
                self.assertEqual(status['status'], 'completed'); self.assertEqual(data, b'parent-output')
                self.assertLess(time.monotonic() - started, 1.5)
                before = heartbeat.read_text(); deadline = time.monotonic() + .5
                while heartbeat.read_text() == before and time.monotonic() < deadline: time.sleep(.01)
                self.assertNotEqual(heartbeat.read_text(), before)
                replay.validate_simctl_status(status, self.launch_arguments())
            finally:
                for child in owned:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait(timeout=2)

    def test_complete_replay_accepts_only_closed_simctl_supervisor_evidence(self):
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root)
            def startup(arguments, timeout, phase):
                status, data = native(arguments, timeout=timeout)
                self.assertEqual(status['status'], 'completed')
                self.assertEqual(phase, replay.simctl_phase(arguments))
                return data.decode().strip()
            with patch.object(replay.launch_smoke, 'command', side_effect=startup), patch.object(replay, 'native_command', side_effect=native):
                report = replay.execute(root=root, command=replay.ui_native_command, popen=native.popen, pause=lambda _: None)
            self.assertTrue(report['passed'])
            for entry in report['commands']:
                if 'supervisor' in entry:
                    self.assertEqual(entry['captured_bytes_scope'], 'returnedStdoutPayloadBytes')
                    self.assertIsNone(entry['failure'])
            self.assertTrue(any('supervisor' in entry for entry in report['commands']))

    @contextlib.contextmanager
    def prepared(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); values = fixture(root)
            with patch.object(replay, 'OUTER_SHA256', values[5]), patch.object(replay, 'IPA_SHA256', values[6]), \
                 patch.object(replay.artifacts, 'verify_checkout', return_value=(HEAD, 2)) as guard:
                proof = replay.prepare(root=root, api=values[3], fetch=values[4])
                yield root, root / replay.OUTPUT, proof, guard

    def test_prepare_retains_fixed_origin_five_source_hashes_and_historical_not_fresh_tests(self):
        with self.prepared() as (root, output, proof, guard):
            guard.assert_called_once_with(root, replay.CURRENT_SOURCE)
            self.assertEqual(proof['source_commit'], replay.SOURCE)
            self.assertEqual(proof['native_source_hashes'], replay.NATIVE_SOURCES)
            self.assertEqual(len(proof['native_source_hashes']), 5)
            self.assertEqual(proof['new_xctest_cases_executed'], 0)
            self.assertEqual(proof['historical_controller_cases_passed'], 8)
            self.assertEqual(proof['historical_interaction_cases_passed'], 1)
            self.assertEqual(proof['inner_references_verified'], 5)
            self.assertFalse(proof['release_qualified']); self.assertFalse(proof['logged_in_settings_verified'])
            self.assertEqual(set(proof), replay.PROOF_FIELDS)
            self.assertEqual(sorted(p.name for p in output.iterdir()), ['CustomeOptionsHost-simulator.ipa', 'verification.json'])

    def test_self_control_schema_accepts_only_fixed_known_regions_and_consistent_booleans(self):
        for kind in (None, 'selfSpelling', 'selfOrientation', 'selfWrong', 'selfBlank', 'selfVision'):
            with self.subTest(kind=kind):
                value = self_controls(kind); parsed = replay.self_control_report(json.dumps(value).encode())
                self.assertEqual(parsed, value); self.assertEqual(parsed['self_test_passed'], kind is None)
        changes = [lambda v: v.update(raw_ocr='PRIVATE-CANARY'),
            lambda v: v.update(self_test_passed=False), lambda v: v.update(cases=True),
            lambda v: v['controls']['positive'].update(expected_title_in_navigation_region=False),
            lambda v: v['controls']['positive']['known_title_regions'][0].update(kind='PRIVATE-CANARY'),
            lambda v: v['controls']['positive']['known_title_regions'][0].update(rect=[.2, float('nan'), .3, .1]),
            lambda v: v['controls']['positive']['known_title_regions'][0].update(rect=[.2, .9, .3, .2]),
            lambda v: v['controls']['positive'].update(recognized_observations=0),
            lambda v: v['controls']['positive'].update(state='visionUnavailable'),
            lambda v: v['controls']['positive'].update(error={'domain': 'PRIVATE-CANARY', 'code': 9}),
            lambda v: v['controls']['positive'].update(error={'domain': 'Vision', 'code': 2**40}),
            lambda v: v['controls']['positive'].update(cpu_compute_selected=False)]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = self_controls(); change(value)
                with self.assertRaises(replay.ReplayError): replay.self_control_report(json.dumps(value).encode())
        with self.assertRaises(replay.ReplayError): replay.self_control_report(b'{"cases":3,"cases":3}')

    def test_current_guard_and_renderer_identity_precede_download(self):
        for kind in ('checkout', 'renderer', 'host'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); values = fixture(root); api = Mock(); fetch = Mock()
                if kind != 'checkout':
                    name = list(replay.NATIVE_SOURCES)[0 if kind == 'renderer' else 4]; (root / name).write_text('changed production source')
                with patch.object(replay.artifacts, 'verify_checkout', side_effect=replay.artifacts.ReplayError('changed') if kind == 'checkout' else None,
                                  return_value=(HEAD, 2)):
                    with self.assertRaises(ValueError): replay.prepare(root=root, api=api, fetch=fetch)
                api.assert_not_called(); fetch.assert_not_called()

    def test_fixed_origin_refuses_wrong_run_source_repository_compile_id_and_hash(self):
        for kind in ('run', 'source', 'repository', 'compile', 'job', 'artifact', 'hash', 'artifactrun'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); values = fixture(root); artifact, run, jobs = values[:3]
                if kind == 'run': run['id'] += 1
                if kind == 'source': run['head_sha'] = replay.CURRENT_SOURCE
                if kind == 'repository': run['head_repository']['id'] += 1
                if kind == 'compile': jobs['jobs'][0]['steps'][0]['conclusion'] = 'failure'
                if kind == 'job': jobs['jobs'][0]['conclusion'] = 'failure'
                if kind == 'artifact': artifact['id'] += 1
                if kind == 'hash': artifact['digest'] = 'sha256:' + '0' * 64
                if kind == 'artifactrun': artifact['workflow_run']['id'] += 1
                with patch.object(replay, 'OUTER_SHA256', values[5]):
                    with self.assertRaises(replay.ReplayError): replay.select(values[3])

    def test_download_and_independent_inner_checksum_validation_reject_tampering(self):
        for kind in ('outer', 'inner'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); values = fixture(root); archive = root / 'source.zip'
                if kind == 'outer': archive.write_bytes(b'corrupt download')
                else:
                    with zipfile.ZipFile(archive) as z: members = {n: z.read(n) for n in z.namelist()}
                    members['custom-options-ui/result.json'] += b' '
                    with zipfile.ZipFile(archive, 'w') as z:
                        for name, data in members.items(): z.writestr(name, data)
                    values[0]['size_in_bytes'] = archive.stat().st_size
                outer = hashlib.sha256(archive.read_bytes()).hexdigest() if kind == 'inner' else values[5]
                with patch.object(replay, 'OUTER_SHA256', outer):
                    with self.assertRaises(replay.ReplayError): replay.verify_archive(archive, values[0], root / 'output')

    def test_exact_five_entry_host_extraction_uses_renderer_bundle_not_telegram_bundle(self):
        with self.prepared() as (_, output, _, _), tempfile.TemporaryDirectory() as directory:
            destination = Path(directory).resolve() / 'CustomeOptionsHost.app'
            replay.extract_host(output / 'CustomeOptionsHost-simulator.ipa', destination)
            self.assertEqual(replay.checked_metadata(destination)['CFBundleIdentifier'], replay.BUNDLE)
            self.assertNotEqual(replay.BUNDLE, replay.artifacts.BUNDLE)
            self.assertEqual(len([p for p in destination.rglob('*') if p.is_file()]), 4)

    def test_owned_filesystem_locator_requires_exact_fresh_device_identity(self):
        for key in (None, 'UDID', 'name', 'deviceType', 'runtime'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve(); device, candidate = device_tree(home); candidate.mkdir()
                if key:
                    path = device / 'device.plist'; value = plistlib.loads(path.read_bytes()); value[key] += '-wrong'
                    path.write_bytes(plistlib.dumps(value))
                result, proof = replay.owned_installed_bundle(IDENTIFIER, 'AYU_OPTIONS_ROOT_fixture', home=home)
                if key:
                    self.assertIsNone(result); self.assertEqual(proof['state'], 'rejected')
                    self.assertEqual(proof['reason'], 'ownedDeviceIdentityMismatch')
                else:
                    self.assertEqual(result, candidate); self.assertEqual(proof['state'], 'candidateLocated')
                    self.assertTrue(proof['device_identity_verified']); self.assertTrue(proof['unique_candidate_verified'])
                self.assertNotIn(str(home), json.dumps(proof))

    def test_owned_locator_rejects_symbolic_links_wrong_owner_multiple_candidates_and_container_overflow(self):
        for kind in ('deviceLink', 'metadataLink', 'appLink', 'owner', 'multiple', 'overflow', 'fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve(); device, candidate = device_tree(home); candidate.mkdir()
                if kind == 'deviceLink':
                    actual = home / 'actual-device'; device.rename(actual); device.symlink_to(actual, target_is_directory=True)
                if kind == 'metadataLink':
                    metadata = device / 'device.plist'; actual = home / 'actual.plist'; metadata.rename(actual); metadata.symlink_to(actual)
                if kind == 'appLink':
                    actual = home / 'Actual.app'; candidate.rename(actual); candidate.symlink_to(actual, target_is_directory=True)
                if kind == 'multiple': (candidate.parent.parent / 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE/CustomeOptionsHost.app').mkdir(parents=True)
                if kind == 'overflow':
                    for i in range(33): (candidate.parent.parent / (f'{i:08X}-0000-0000-0000-000000000001')).mkdir()
                if kind == 'fifo': (device / 'device.plist').unlink(); os.mkfifo(device / 'device.plist')
                result, proof = replay.owned_installed_bundle(IDENTIFIER, 'AYU_OPTIONS_ROOT_fixture', home=home,
                    uid=os.getuid() + 1 if kind == 'owner' else os.getuid())
                self.assertIsNone(result); self.assertEqual(proof['state'], 'rejected')

    def test_owned_filesystem_candidate_still_requires_complete_bytes_metadata_and_installed_signature(self):
        for kind in (None, 'installed', 'installedXML', 'extraFile', 'extraSymlink', 'installedSignature', 'wrongOwnedDevice'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind, filesystem=True)
                with patch.object(replay.Path, 'home', return_value=native.home):
                    if kind:
                        with self.assertRaises(ValueError): replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                        report = json.loads((output / 'result.json').read_text()); self.assertFalse(report['passed'])
                        self.assertFalse(report['installed_host_verified'])
                        self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'launch'] for a, _ in native.calls))
                        self.assertIsNotNone(report['installed_host_locator']['reason'])
                    else:
                        report = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                        self.assertTrue(report['passed']); self.assertTrue(report['installed_host_verified'])
                        self.assertEqual(report['installed_host_locator']['origin'], 'ownedDeviceFilesystem')
                        self.assertEqual(report['installed_host_locator']['state'], 'attested')
                    self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'get_app_container'] for a, _ in native.calls))
                    self.assertNotIn(str(root), (output / 'result.json').read_text())

    def test_unknown_private_device_schema_falls_back_only_to_bounded_own_api_and_full_attestation(self):
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'unknownSchema', filesystem=True)
            with patch.object(replay.Path, 'home', return_value=native.home):
                report = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
            self.assertTrue(report['passed']); self.assertEqual(report['installed_host_locator']['origin'], 'simctl')
            self.assertEqual(report['installed_host_locator']['filesystem']['reason'], 'deviceMetadataUnsupported')
            self.assertTrue(report['installed_host_verified'])
            lookups = [(a, t) for a, t in native.calls if a[:3] == ['xcrun', 'simctl', 'get_app_container']]
            self.assertEqual(lookups, [(['xcrun', 'simctl', 'get_app_container', IDENTIFIER, replay.BUNDLE, 'app'], 5)])

    def test_cleanup_requires_delete_success_and_unfiltered_uuid_absence_without_faking_shutdown(self):
        for kind in ('shutdownOnly', 'cleanup', 'cleanupMalformed', 'cleanupTimedOut', 'deleteFailed'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                if kind == 'shutdownOnly':
                    report = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                    self.assertTrue(report['passed']); self.assertTrue(report['cleanup_passed'])
                    self.assertEqual(report['cleanup_results'], {'shutdown_completed': False, 'delete_command_completed': True, 'owned_uuid_absent': True})
                else:
                    with self.assertRaisesRegex(replay.ReplayError, '^settings_cleanup_not_verified$'):
                        replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                    report = json.loads((output / 'result.json').read_text()); self.assertFalse(report['passed']); self.assertFalse(report['cleanup_passed'])
                    self.assertEqual(report['cleanup_results']['owned_uuid_absent'], False if kind == 'cleanup' else None)
                inventories = [a for a, _ in native.calls if a[:4] == ['xcrun', 'simctl', 'list', 'devices']]
                self.assertEqual(inventories, [] if kind == 'deleteFailed' else [['xcrun', 'simctl', 'list', 'devices', '--json']])
        self.assertIsNone(replay.owned_uuid_absent(b'{"devices": {"runtime": [null]}}', IDENTIFIER))
        self.assertFalse(replay.owned_uuid_absent(json.dumps({'devices': {'runtime': [{'udid': IDENTIFIER.lower()}]}}).encode(), IDENTIFIER))
        for data in (b'{"devices":{"runtime":[{"udid":"' + IDENTIFIER.encode() + b'"}],"runtime":[]}}',
                     b'{"devices":{"runtime":[{"udid":"' + IDENTIFIER.encode() + b'"}]},"devices":{}}'):
            self.assertIsNone(replay.owned_uuid_absent(data, IDENTIFIER))

    def test_wrong_prepared_source_stale_checkout_or_fresh_test_claim_blocks_commands(self):
        for key, value in [('source_commit', replay.CURRENT_SOURCE), ('artifact_id', 1), ('replay_checkout_commit', 'e' * 40),
                           ('new_xctest_cases_executed', 9), ('logged_in_settings_verified', True), ('native_source_hashes', {})]:
            with self.subTest(key=key), self.prepared() as (root, output, proof, _):
                proof[key] = value; (output / 'verification.json').write_text(json.dumps(proof)); command = Mock()
                with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=command)
                command.assert_not_called()

    def test_linux_replay_never_claims_or_attempts_a_simulator_launch(self):
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Linux'):
            command = Mock()
            with self.assertRaisesRegex(replay.ReplayError, '^macos_required$'): replay.execute(root=root, command=command)
            command.assert_not_called()

    def test_recorded_root_requires_signature_owned_install_attestation_title_and_cleanup(self):
        api_hash_key = 'TELEGRAM_' + 'API_HASH'
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'), \
             patch.dict(os.environ, {'GH_TOKEN': 'PRIVATE-CANARY', api_hash_key: 'PRIVATE-CANARY', 'DYLD_INSERT_LIBRARIES': 'PRIVATE-CANARY'}):
            native = Native(root)
            result = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
            self.assertTrue(result['passed']); self.assertTrue(result['source_codesign_verified'])
            self.assertTrue(result['installed_host_verified']); self.assertTrue(result['cleanup_passed'])
            self.assertTrue(result['root_observation']['root_title_visible'])
            self.assertTrue(result['root_title_automatically_verified'])
            self.assertTrue(result['title_checker_self_test']['self_test_passed'])
            self.assertEqual(result['new_xctest_cases_executed'], 0)
            for key in ('application_recompiled', 'renderer_recompiled', 'logged_in_settings_verified', 'physical_device_verified', 'release_qualified'):
                self.assertFalse(result[key])
            self.assertEqual(native.device_query, ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro'])
            commands = [a for a, _ in native.calls]
            self.assertEqual(sum(a[:3] == ['xcrun', 'simctl', 'install'] for a in commands), 1)
            self.assertEqual(sum(a[:3] == ['xcrun', 'simctl', 'launch'] for a in commands), 2)
            self.assertTrue(result['warmup_launch_completed'])
            self.assertFalse(result['recording_started_before_launch'])
            self.assertTrue(result['recording_started_before_recorded_launch'])
            self.assertTrue(result['recording_available'])
            self.assertEqual([a[2] for a in commands[-3:]], ['shutdown', 'delete', 'list'])
            text = (output / 'result.json').read_text()
            for private in ('PRIVATE-CANARY', str(root), IDENTIFIER): self.assertNotIn(private, text)

    def test_recording_readiness_is_accepted_from_stdout_as_well_as_stderr(self):
        with self.prepared() as (root, _, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'recordStdout')
            result = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
            self.assertTrue(result['passed']); self.assertTrue(result['recording_available'])
            self.assertGreater(result['recording_diagnostic']['stdout_bytes'], 0)
            self.assertEqual(result['recording_diagnostic']['stderr_bytes'], 0)
            self.assertEqual(result['recording_diagnostic']['state'], 'complete')

    def test_recorder_spawn_early_exit_pipe_cap_and_stop_failures_never_prevent_actual_launch_or_png(self):
        for kind in ('recordSpawn', 'recordEarly', 'recordLimit', 'recordStop'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), \
                 patch.object(replay.platform, 'system', return_value='Darwin'), patch.dict(os.environ, {'GH_TOKEN': 'PRIVATE-CANARY'}):
                native = Native(root, kind)
                with self.assertRaisesRegex(replay.ReplayError, '^settings_recording_failed_diagnostic_only$'):
                    replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                result = json.loads((output / 'result.json').read_text())
                self.assertFalse(result['passed']); self.assertFalse(result['recording_available'])
                self.assertTrue(result['warmup_launch_completed']); self.assertTrue(result['diagnostic_root_captured'])
                self.assertTrue(result['cleanup_passed']); self.assertTrue((output / 'root-light.png').is_file())
                self.assertFalse((output / 'root-light.mp4').exists())
                self.assertEqual(result['phase'], 'recordingFailedDiagnosticOnly')
                self.assertEqual(sum(a[:3] == ['xcrun', 'simctl', 'launch'] for a, _ in native.calls), 2)
                diagnostic = result['recording_diagnostic']
                self.assertLessEqual(diagnostic['stdout_bytes'] + diagnostic['stderr_bytes'], 16384)
                self.assertLessEqual(len(json.dumps(diagnostic['output'])), 16384)
                for private in ('PRIVATE-CANARY', '/Users/private', IDENTIFIER):
                    self.assertNotIn(private, (output / 'result.json').read_text())
                if kind == 'recordEarly': self.assertIn('recorder failed', diagnostic['output'])
                if kind == 'recordLimit': self.assertTrue(diagnostic['output_limit'])

    def test_combined_vision_and_recorder_failure_still_preserves_a_failed_actual_root_observation(self):
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'selfVision')
            def unavailable(*args, **kwargs): raise OSError('PRIVATE-CANARY')
            with self.assertRaisesRegex(replay.ReplayError, '^settings_recording_failed_diagnostic_only$'):
                replay.execute(root=root, command=native, popen=unavailable, pause=lambda _: None)
            result = json.loads((output / 'result.json').read_text())
            self.assertFalse(result['passed']); self.assertFalse(result['root_title_automatically_verified'])
            self.assertTrue(result['diagnostic_root_captured']); self.assertTrue(result['cleanup_passed'])
            self.assertEqual(result['title_checker_self_test']['controls']['positive']['error'], {'domain': 'Vision', 'code': 9})
            self.assertEqual(result['root_observation'], {'width': 1179, 'height': 2556})
            self.assertTrue((output / 'root-light.png').exists()); self.assertNotIn('PRIVATE-CANARY', (output / 'result.json').read_text())

    def test_failed_validated_self_controls_record_real_root_diagnostics_but_never_pass(self):
        for kind in ('selfSpelling', 'selfOrientation', 'selfWrong', 'selfBlank', 'selfVision'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                with self.assertRaisesRegex(replay.ReplayError, '^settings_self_test_failed_diagnostic_only$'):
                    replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                result = json.loads((output / 'result.json').read_text())
                self.assertFalse(result['passed']); self.assertFalse(result['title_checker_self_test']['self_test_passed'])
                self.assertFalse(result['root_title_automatically_verified'])
                self.assertTrue(result['diagnostic_root_captured']); self.assertTrue(result['installed_host_verified'])
                self.assertTrue(result['cleanup_passed']); self.assertEqual(result['recording_return_code'], 0)
                self.assertEqual(result['phase'], 'selfTestFailedDiagnosticOnly')
                self.assertNotIn('root_title_visible', result['root_observation'])
                self.assertEqual(result['root_observation'], {'width': 1179, 'height': 2556})
                self.assertEqual(result['new_xctest_cases_executed'], 0)
                for name in ('root-light.png', 'root-light.mp4', 'title-check-positive.png'): self.assertTrue((output / name).is_file())
                self.assertFalse(any(len(a) == 2 and a[0].endswith('visible-root-check') for a, _ in native.calls))
                self.assertEqual([a[2] for a, _ in native.calls[-3:]], ['shutdown', 'delete', 'list'])

    def test_self_control_invalid_payload_crash_timeout_or_status_mismatch_never_creates_simulator(self):
        for kind in ('selfMalformed', 'selfCrashed', 'selfMismatch', 'selfTimeout', 'largePositive'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'create'] for a, _ in native.calls))
                result = json.loads((output / 'result.json').read_text()); self.assertFalse(result['passed'])
                self.assertFalse(result['root_title_automatically_verified'])
                self.assertNotIn('PRIVATE-CANARY', (output / 'result.json').read_text())
                if kind == 'largePositive': self.assertFalse((output / 'title-check-positive.png').exists())

    def test_cli_returns_failure_after_validated_diagnostic_recording_and_cleanup(self):
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'selfSpelling'); capture = io.StringIO(); execute = replay.execute
            def invoke(destination):
                return execute(destination, root=root, command=native, popen=native.popen, pause=lambda _: None)
            with patch.object(replay, 'execute', side_effect=invoke), contextlib.redirect_stdout(capture):
                code = replay.main(['run', '--output', str(output)])
            self.assertEqual(code, 1)
            result = json.loads((output / 'result.json').read_text())
            self.assertFalse(result['passed']); self.assertFalse(result['root_title_automatically_verified'])
            self.assertTrue(result['diagnostic_root_captured']); self.assertTrue(result['cleanup_passed'])
            self.assertEqual(result['recording_return_code'], 0)
            self.assertTrue((output / 'root-light.png').exists()); self.assertTrue((output / 'root-light.mp4').exists())
            self.assertEqual(json.loads(capture.getvalue()), {'diagnostic_role': replay.ROLE, 'passed': False, 'error': 'settings_replay_rejected'})

    def test_signature_failure_prevents_owned_simulator_creation(self):
        with self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
            native = Native(root, 'signature')
            with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
            self.assertEqual(len(native.calls), 1)
            result = json.loads((output / 'result.json').read_text())
            self.assertFalse(result['passed']); self.assertFalse(result['source_codesign_verified'])

    def test_installed_mismatch_install_timeout_title_failure_cleanup_failure_and_mutation_fail_closed(self):
        for kind in ('installed', 'installedXML', 'extraFile', 'extraDir', 'extraSymlink', 'install', 'title', 'cleanup', 'mutate', 'largeVideo'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                result = json.loads((output / 'result.json').read_text()); self.assertFalse(result['passed'])
                self.assertEqual(result['new_xctest_cases_executed'], 0)
                self.assertEqual([args[2] for args, _ in native.calls[-3:]], ['shutdown', 'delete', 'list'])
                if kind in ('installed', 'installedXML', 'extraFile', 'extraDir', 'extraSymlink', 'install'):
                    self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'launch'] for a, _ in native.calls))
                if kind == 'install': self.assertEqual(result['phase'], 'installFrozenHost')
                if kind == 'cleanup': self.assertFalse(result['cleanup_passed'])
                if kind == 'largeVideo': self.assertFalse((output / 'root-light.mp4').exists())
                self.assertNotIn('PRIVATE-CANARY', (output / 'result.json').read_text())

    def test_149_requires_actual_owned_booted_model_not_just_exit_status(self):
        for kind in ('149', '149wrong'):
            with self.subTest(kind=kind), self.prepared() as (root, output, _, _), patch.object(replay.platform, 'system', return_value='Darwin'):
                native = Native(root, kind)
                if kind == '149':
                    result = replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                    self.assertTrue(result['owned_already_booted_state_verified'])
                else:
                    with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=native, popen=native.popen, pause=lambda _: None)
                    self.assertFalse(any(a[:3] == ['xcrun', 'simctl', 'install'] for a, _ in native.calls))

    def test_fixed_output_prevents_aliases_symlinks_and_accidental_overwrite(self):
        with self.prepared() as (root, output, _, _):
            for name in ('somewhere-else', root / 'elsewhere'):
                with self.subTest(name=name), self.assertRaises(replay.ReplayError): replay.execute(output=name, root=root)
            with self.assertRaises(replay.ReplayError): replay.prepare(root=root)
            (output / 'root-light.png').write_bytes(b'old evidence')
            with patch.object(replay.platform, 'system', return_value='Darwin'):
                command = Mock()
                with self.assertRaises(replay.ReplayError): replay.execute(root=root, command=command)
                command.assert_not_called()

    def test_native_failure_cli_emits_only_fixed_fields(self):
        capture = io.StringIO()
        with patch.object(replay, 'execute', side_effect=ValueError('PRIVATE-CANARY')), contextlib.redirect_stdout(capture):
            self.assertEqual(replay.main(['run']), 1)
        self.assertEqual(json.loads(capture.getvalue()), {'diagnostic_role': replay.ROLE, 'passed': False, 'error': 'settings_replay_rejected'})


if __name__ == '__main__': unittest.main()
