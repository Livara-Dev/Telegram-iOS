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
import struct
import sys
import tempfile
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


class Native:
    def __init__(self, root, kind=None):
        self.root = root; self.kind = kind; self.calls = []; self.device_name = None; self.app = None
        self.installed = root / 'owned-installed/CustomeOptionsHost.app'
    def __call__(self, args, timeout=30):
        self.calls.append((args, timeout))
        if args[0] == '/usr/bin/codesign':
            if self.kind == 'signature': return {'status': 'commandFailed', 'exit_code': 1, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}, b''
        elif args[:2] == ['xcodebuild', '-version']: return complete(b'Xcode 27.1\nBuild version 27A9275\n')
        elif args[:2] == ['xcrun', 'swiftc']: Path(args[-1]).write_bytes(b'synthetic helper')
        elif args[-1] == '--self-test': return complete(b'{"self_test_passed":true,"cases":3}')
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
            return complete(json.dumps({'devices': {replay.RUNTIME: [{'udid': IDENTIFIER, 'name': self.device_name,
                'deviceTypeIdentifier': replay.DEVICE_TYPE, 'state': 'Booted' if self.kind == '149' else 'Shutdown'}]}}).encode())
        elif args[:3] == ['xcrun', 'simctl', 'install']:
            self.app = Path(args[-1])
            if self.kind == 'install': return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
            self.installed.parent.mkdir(); shutil.copytree(self.app, self.installed)
            if self.kind == 'installed': (self.installed / 'CustomeOptionsHost').write_bytes(b'wrong executable')
            if self.kind == 'installedXML': (self.installed / 'Info.plist').write_bytes(b'<plist><dict>PRIVATE-CANARY</broken>')
            if self.kind == 'extraFile': (self.installed / 'UnexpectedExecutable').write_bytes(b'PRIVATE-CANARY')
            if self.kind == 'extraDir': (self.installed / 'UnexpectedDirectory').mkdir()
            if self.kind == 'extraSymlink': (self.installed / 'UnexpectedLink').symlink_to(self.app / 'CustomeOptionsHost')
        elif args[:3] == ['xcrun', 'simctl', 'get_app_container']: return complete(str(self.installed).encode())
        elif args[:3] == ['xcrun', 'simctl', 'io'] and args[4] == 'screenshot': Path(args[-1]).write_bytes(b'synthetic PNG checked by mocked native Vision')
        elif len(args) == 2 and args[0].endswith('visible-root-check'):
            if self.kind == 'mutate': (self.root / replay.OUTPUT / 'CustomeOptionsHost-simulator.ipa').write_bytes(b'changed')
            return complete(json.dumps({'root_title_visible': self.kind != 'title', 'width': 1179, 'height': 2556}).encode())
        elif args[:3] == ['xcrun', 'simctl', 'shutdown'] and self.kind == 'cleanup':
            return {'status': 'timedOut', 'exit_code': None, 'timeout': True, 'output_limit': False, 'captured_bytes': 0}, b''
        return complete()
    def popen(self, args, **kwargs):
        if set(kwargs['env']) - replay.child_environment().keys(): raise AssertionError('unbounded child environment')
        for key in ('GH_TOKEN', 'TELEGRAM_API_HASH', 'DYLD_INSERT_LIBRARIES'):
            if key in kwargs['env']: raise AssertionError('credential inherited')
        if not kwargs.get('start_new_session'): raise AssertionError('unowned recorder')
        kwargs['stderr'].write(b'Recording started'); kwargs['stderr'].flush()
        Path(args[-1]).write_bytes(b'x' * 2048)
        if self.kind == 'largeVideo':
            with Path(args[-1]).open('ab') as file: file.truncate(32 * 1024**2 + 1)
        class Recorder:
            pid = 987654321
            def poll(self): return None
            def send_signal(self, value): pass
            def wait(self, timeout): return 0
        return Recorder()


class RetainedSettingsReplayTests(unittest.TestCase):
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
            self.assertEqual(result['new_xctest_cases_executed'], 0)
            for key in ('application_recompiled', 'renderer_recompiled', 'logged_in_settings_verified', 'physical_device_verified', 'release_qualified'):
                self.assertFalse(result[key])
            self.assertEqual(native.device_query, ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro'])
            commands = [a for a, _ in native.calls]
            self.assertEqual(sum(a[:3] == ['xcrun', 'simctl', 'install'] for a in commands), 1)
            self.assertEqual(sum(a[:3] == ['xcrun', 'simctl', 'launch'] for a in commands), 1)
            self.assertEqual([a[2] for a in commands[-2:]], ['shutdown', 'delete'])
            text = (output / 'result.json').read_text()
            for private in ('PRIVATE-CANARY', str(root), IDENTIFIER): self.assertNotIn(private, text)

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
                self.assertEqual([args[2] for args, _ in native.calls[-2:]], ['shutdown', 'delete'])
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
