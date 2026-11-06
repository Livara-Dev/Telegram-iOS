"""Historical Projection replay must not become an arbitrary artifact executor."""
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import projection_replay as replay
import test_product_capture as products
from test_test_product_capture import fixture, macho


def metadata(size=100, digest=None, current=False):
    specification = replay.pinned(replay.CURRENT_RUN, replay.CURRENT_SOURCE) if current else replay.pinned(replay.RUN, replay.SOURCE)
    artifact = {'id': specification['artifact'], 'name': specification['name'],
                'expired': False, 'size_in_bytes': size, 'digest': 'sha256:' + (digest or specification['digest']),
                'workflow_run': {'id': specification['run'], 'head_sha': specification['source'], 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': specification['run'], 'head_sha': specification['source'], 'status': 'completed', 'path': replay.artifacts.WORKFLOW,
           'repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY},
           'head_repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY}}
    return run, artifact


def api_for(run, artifact):
    return lambda endpoint: {'total_count': 1, 'artifacts': [artifact]} if '/artifacts?' in endpoint else run


def captured(root, current=False):
    source = root / 'source'; source.mkdir(); fixture(source)
    (source / products.APP / 'ProjectionTestHost').write_bytes(macho(b'host'))
    (source / products.APP / 'PlugIns' / products.TEST / 'ChatLocalTextFilterProjectionTests').write_bytes(macho(b'test', kind=8))
    destination = root / 'capture'; report = products.capture_products(source, destination)
    hashes = {**report['files'], 'capture.json': replay.digest(destination / 'capture.json')}
    manifest = {'schema_version': 1, 'source_commit': replay.CURRENT_SOURCE if current else replay.SOURCE, 'test_target': products.LABEL,
                'toolchain': replay.PINS, 'application_recompiled_for_replay': False,
                'diagnostic_products_not_test_pass': True, 'physical_device_verified': False, 'files': hashes}
    (destination / 'manifest.json').write_text(json.dumps(manifest))
    sums = {**hashes, 'manifest.json': replay.digest(destination / 'manifest.json')}
    (destination / 'SHA256SUMS').write_text(''.join(h + '  ' + n + '\n' for n, h in sorted(sums.items())))
    return destination


def archive(root, mutate=None, current=False):
    source = captured(root, current)
    if mutate: mutate(source)
    target = root / 'artifact.zip'
    with zipfile.ZipFile(target, 'w') as zipped:
        for path in source.rglob('*'):
            if path.is_file(): zipped.write(path, ('fork-evidence/' if current else '') + products.CAPTURE + '/' + str(path.relative_to(source)))
        zipped.writestr('unrelated/private.log', 'must never be extracted')
    return target


def log(outcome='passed', keys=None):
    return ''.join("Test Case '-[Module." + key.replace('.', ' ') + "]' started.\nTest Case '-[Module." + key.replace('.', ' ') + "]' " + outcome + ' (0.001 seconds).\n' for key in sorted(keys if keys is not None else replay.EXPECTED)).encode()


class ProjectionReplayTests(unittest.TestCase):
    def test_only_pinned_historical_origin_is_supported(self):
        run, artifact = metadata()
        self.assertEqual(replay.origin(replay.RUN, replay.SOURCE, api_for(run, artifact)), artifact)
        for badrun, badsource in [(0, replay.SOURCE), (replay.RUN + 1, replay.SOURCE), (replay.RUN, 'a' * 40)]:
            with self.subTest(run=badrun, source=badsource), self.assertRaises(ValueError): replay.origin(badrun, badsource, api_for(run, artifact))

    def test_second_fixed_current_pair_uses_exact_final_artifact_and_never_mixes_sources(self):
        run, artifact = metadata(current=True)
        self.assertEqual(replay.origin(replay.CURRENT_RUN, replay.CURRENT_SOURCE, api_for(run, artifact)), artifact)
        self.assertEqual(artifact['name'], 'archive-integration-' + replay.CURRENT_SOURCE)
        for run_id, source in [(replay.RUN, replay.CURRENT_SOURCE), (replay.CURRENT_RUN, replay.SOURCE), (replay.CURRENT_RUN + 1, replay.CURRENT_SOURCE)]:
            with self.subTest(run_id=run_id), self.assertRaises(ValueError): replay.origin(run_id, source, api_for(run, artifact))
        artifact['id'] = replay.ARTIFACT
        with self.assertRaises(ValueError): replay.origin(replay.CURRENT_RUN, replay.CURRENT_SOURCE, api_for(run, artifact))

    def test_current_product_prefix_and_manifest_source_are_verified_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); zipped = archive(root, current=True)
            actual = replay.digest(zipped); _, artifact = metadata(zipped.stat().st_size, actual, current=True)
            with patch.object(replay, 'CURRENT_OUTER_SHA256', actual):
                result = replay.extract(zipped, artifact, root / 'output', replay.CURRENT_SOURCE)
                self.assertTrue(result['sanitized_plan_verified'])
                with self.assertRaises(ValueError): replay.validate_products(root / 'output', replay.SOURCE)
                with self.assertRaises(ValueError): replay.extract(zipped, artifact, root / 'wrong-prefix', replay.SOURCE)

    def test_current_checkout_and_exact_method_set_are_required_before_artifact_access(self):
        payloads = {}
        for name in ('ChatLocalTextFilterProjectionTests', 'ChatLocalArchiveProjectionTests', 'ChatLocalArchiveRowRendererTests'):
            methods = sorted(key.split('.', 1)[1] for key in replay.EXPECTED if key.startswith(name + '.'))
            payloads[name + '.swift'] = ('final class ' + name + ': XCTestCase {\n' + ''.join('    func ' + method + '() {}\n' for method in methods) + '}').encode()
        def source(root, *args):
            self.assertEqual(args[0], 'show'); self.assertTrue(args[1].startswith(replay.CURRENT_SOURCE + ':'))
            return payloads[args[1].rsplit('/', 1)[1]]
        with patch.object(replay.artifacts, 'verify_checkout') as checkout, patch.object(replay.artifacts, 'git', side_effect=source):
            replay.verify_current_checkout(Path('.'))
            checkout.assert_called_once_with(Path('.'), replay.CURRENT_SOURCE)
            payloads['ChatLocalArchiveRowRendererTests.swift'] += b'\n    func testUnexpected() {}\n'
            with self.assertRaisesRegex(ValueError, 'projection_expected_cases_changed'): replay.verify_current_checkout(Path('.'))
        with tempfile.TemporaryDirectory() as directory:
            # Match replay's canonical root before constructing its fixed
            # output path (Darwin may expose /var as /private/var).
            root = Path(directory).resolve()
            with patch.object(replay, 'verify_current_checkout', side_effect=replay.artifacts.ReplayError('application_source_changed')) as guard, patch.object(replay, 'origin') as origin:
                with self.assertRaisesRegex(ValueError, 'application_source_changed'):
                    replay.replay(replay.CURRENT_RUN, replay.CURRENT_SOURCE, root / replay.OUTPUT, root=root)
                guard.assert_called_once_with(root.resolve()); origin.assert_not_called()
                self.assertFalse((root / replay.OUTPUT).exists())

    def test_reseal_is_never_allowed_for_historical_pair_or_unknown_control(self):
        for run_id, source, control in [(replay.RUN, replay.SOURCE, 'resealed-host'),
                                        (replay.CURRENT_RUN, replay.CURRENT_SOURCE, 'anything')]:
            with self.subTest(control=control), patch.object(replay, 'origin') as origin:
                with self.assertRaisesRegex(ValueError, 'projection_invalid_host_control'):
                    replay.replay(run_id, source, Path(replay.OUTPUT), host_control=control)
                origin.assert_not_called()

    def test_current_original_arm_keeps_immutable_provenance_and_never_qualifies_new_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); zipped = archive(root, current=True); actual = replay.digest(zipped)
            run_meta, artifact = metadata(zipped.stat().st_size, actual, current=True)
            run, calls = self.runner()
            def fetch(identifier, target):
                self.assertEqual(identifier, replay.CURRENT_ARTIFACT); Path(target).write_bytes(zipped.read_bytes())
            with patch.object(replay, 'CURRENT_OUTER_SHA256', actual), patch.object(replay, 'verify_current_checkout') as guard:
                result = replay.replay(replay.CURRENT_RUN, replay.CURRENT_SOURCE, root / replay.OUTPUT, root,
                    api_for(run_meta, artifact), fetch, run, lambda *args: None)
            guard.assert_called_once_with(root)
            self.assertTrue(result['passed']); self.assertTrue(result['source_products_unchanged'])
            self.assertTrue(result['control_products_unchanged_after_execution'])
            self.assertEqual(result['source_commit'], replay.CURRENT_SOURCE); self.assertEqual(result['host_signing_control'], 'original')
            for flag in ('new_checkout_qualified', 'release_qualified', 'full_application_built', 'physical_device_verified'):
                self.assertFalse(result[flag])
            self.assertEqual(sum('test-without-building' in arguments for arguments in calls), 1)

    def test_actual_installed_code_attestation_precedes_standalone_and_rejects_all_observed_mismatches(self):
        for mode in ('unchanged', 'testBytes', 'identifier', 'executable', 'malformedXML', 'missingInfo', 'invalidPath', 'unavailable'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); capture = captured(root); installed = root / 'Installed.app'
                shutil.copytree(capture / 'TEST_ROOT' / products.APP, installed)
                if mode == 'testBytes': (installed / 'PlugIns' / products.TEST / 'ChatLocalTextFilterProjectionTests').write_bytes(b'replaced code')
                elif mode in {'identifier', 'executable'}:
                    path = installed / 'Info.plist'; info = plistlib.loads(path.read_bytes())
                    info['CFBundleIdentifier' if mode == 'identifier' else 'CFBundleExecutable'] = 'private-installed-canary'
                    path.write_bytes(plistlib.dumps(info))
                elif mode == 'malformedXML': (installed / 'Info.plist').write_bytes(b'<?xml version="1.0"?><plist><dict><key>private-malformed-canary</key>')
                elif mode == 'missingInfo': (installed / 'Info.plist').unlink()
                base, calls = self.runner(); order = []
                def run(arguments, timeout=30):
                    if 'get_app_container' in arguments:
                        order.append('installed'); self.assertEqual(timeout, 10)
                        self.assertEqual(arguments[-2:], [products.BUNDLE, 'app'])
                        return ({'status': 'commandFailed', 'exit_code': 2}, b'') if mode == 'unavailable' else ({'status': 'completed', 'exit_code': 0}, b'private-invalid-path' if mode == 'invalidPath' else str(installed).encode())
                    return base(arguments, timeout)
                def probe(*args): order.append('standaloneProbe')
                with patch.object(replay, 'host_bootstrap_diagnostic', return_value={'state': 'unavailable'}):
                    result = replay.execute(capture, root, root, run, probe, attest_control=True)
                self.assertEqual(order, ['installed', 'standaloneProbe'])
                self.assertEqual(result['installed_control_attestation']['state'], 'matched' if mode == 'unchanged' else 'unavailable' if mode == 'unavailable' else 'mismatch')
                self.assertEqual(result['passed'], mode in {'unchanged', 'unavailable'})
                self.assertNotIn('private-installed-canary', json.dumps(result)); self.assertNotIn('private-malformed-canary', json.dumps(result))
                self.assertEqual(result['case_counts']['passed'], 41)
                self.assertTrue(result['owned_device_cleanup_completed'])

    def test_post_execution_snapshot_errors_never_leave_a_successful_replay_result(self):
        real_snapshot = replay.snapshot_tree
        for target in ('control', 'products'):
            for error_type in (OSError, ValueError):
                with self.subTest(target=target, error_type=error_type), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve(); zipped = archive(root, current=True); actual = replay.digest(zipped)
                    run_meta, artifact = metadata(zipped.stat().st_size, actual, current=True)
                    completed = False
                    def execution(*args, **kwargs):
                        nonlocal completed
                        completed = True
                        return {'passed': True, 'case_counts': {'started': 41, 'passed': 41}, 'owned_device_cleanup_completed': True}
                    def snapshot(path):
                        if completed and Path(path).name == target: raise error_type('private-post-execution-canary')
                        return real_snapshot(path)
                    def fetch(identifier, path): Path(path).write_bytes(zipped.read_bytes())
                    with patch.object(replay, 'CURRENT_OUTER_SHA256', actual), patch.object(replay, 'verify_current_checkout'), patch.object(replay, 'execute', side_effect=execution), patch.object(replay, 'snapshot_tree', side_effect=snapshot):
                        result = replay.replay(replay.CURRENT_RUN, replay.CURRENT_SOURCE, root / replay.OUTPUT, root,
                            api_for(run_meta, artifact), fetch)
                    self.assertFalse(result['passed']); self.assertEqual(result['failure'], 'projection_artifact_verification_failed')
                    self.assertEqual(result['case_counts']['passed'], 41)
                    self.assertNotIn('private-post-execution-canary', json.dumps(result))
                    self.assertFalse(json.loads((root / replay.OUTPUT / 'result.json').read_text())['passed'])

    def test_repository_workflow_head_and_artifact_provenance_fail_closed(self):
        paths = [('run', 'head_sha'), ('run', 'path'), ('run', 'status'), ('artifact', 'id'), ('artifact', 'digest'), ('artifact', 'expired')]
        for kind, key in paths:
            run, artifact = metadata(); target = run if kind == 'run' else artifact; target[key] = None
            with self.subTest(kind=kind, key=key), self.assertRaises(ValueError): replay.origin(replay.RUN, replay.SOURCE, api_for(run, artifact))
        for field in ('repository', 'head_repository'):
            run, artifact = metadata(); run[field]['full_name'] = 'Other/Repository'
            with self.subTest(field=field), self.assertRaises(ValueError): replay.origin(replay.RUN, replay.SOURCE, api_for(run, artifact))
        run, artifact = metadata(); artifact['workflow_run']['repository_id'] = 8
        with self.assertRaises(ValueError): replay.origin(replay.RUN, replay.SOURCE, api_for(run, artifact))

    def test_only_product_subtree_extracts_and_known_binaries_restore_execute_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); zipped = archive(root); _, artifact = metadata(zipped.stat().st_size, replay.digest(zipped))
            with patch.object(replay, 'OUTER_SHA256', replay.digest(zipped)):
                result = replay.extract(zipped, artifact, root / 'private-products')
            self.assertTrue(result['sanitized_plan_verified'])
            self.assertFalse((root / 'private-products/unrelated').exists())
            for path in (root / 'private-products').rglob('*'):
                if path.is_file(): self.assertEqual(bool(path.stat().st_mode & 0o111), str(path.relative_to(root / 'private-products')) in replay.EXECUTABLES)

    def test_outer_digest_and_size_checked_before_any_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); zipped = archive(root); _, artifact = metadata(zipped.stat().st_size)
            with self.assertRaises(ValueError): replay.extract(zipped, artifact, root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_manifest_source_toolchain_capture_hashes_and_inventory_are_verified(self):
        for mutation in ('source', 'toolchain', 'hash', 'extra', 'capture'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = captured(Path(directory))
                if mutation in {'source', 'toolchain', 'hash'}:
                    p = root / 'manifest.json'; m = json.loads(p.read_text())
                    if mutation == 'source': m['source_commit'] = 'a' * 40
                    elif mutation == 'toolchain': m['toolchain'] = {**replay.PINS, 'xcode': '99'}
                    else: m['files']['capture.json'] = '0' * 64
                    p.write_text(json.dumps(m))
                elif mutation == 'capture': (root / 'capture.json').write_text('{}')
                else: (root / 'TEST_ROOT/private.sqlite3').write_text('forbidden')
                with self.assertRaises(ValueError): replay.validate_products(root)

    def test_unsafe_zip_names_links_duplicates_and_nonproduct_files_are_rejected(self):
        for name, mode in [('projection-test-products/../outside', stat.S_IFREG | 0o600),
                           ('projection-test-products/TEST_ROOT/ProjectionTestHost.app/link', stat.S_IFLNK | 0o777),
                           ('projection-test-products/TEST_ROOT/ProjectionTestHost.app/private.db', stat.S_IFREG | 0o600),
                           ('projection-test-products/manifest.json', stat.S_IFREG | 0o600)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); zipped = archive(root)
                with zipfile.ZipFile(zipped, 'a') as z:
                    info = zipfile.ZipInfo(name); info.external_attr = mode << 16; z.writestr(info, b'bad')
                _, artifact = metadata(zipped.stat().st_size, replay.digest(zipped))
                with patch.object(replay, 'OUTER_SHA256', replay.digest(zipped)), self.assertRaises(ValueError): replay.extract(zipped, artifact, root / 'out')

    def test_plan_with_added_environment_arguments_or_foreign_host_is_not_sanitized(self):
        for key, value in [('CommandLineArguments', ['-anything']), ('EnvironmentVariables', {'GH_TOKEN': 'credential'}),
                           ('TestHostPath', '__TESTROOT__/Other.app')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                root = captured(Path(directory)); plan = root / 'TEST_ROOT/test.xctestrun'; data = plistlib.loads(plan.read_bytes()); data['Runner'][key] = value; plan.write_bytes(plistlib.dumps(data))
                # Recompute hashes to reach semantic validation independently of
                # the already pinned outer digest/inventory security boundary.
                self.refresh(root)
                with self.assertRaises((ValueError, OSError)): replay.validate_products(root)

    @staticmethod
    def refresh(root):
        capture = json.loads((root / 'capture.json').read_text())
        capture['files'] = {name: replay.digest(root / name) for name in capture['files']}
        capture['total_product_bytes'] = sum((root / name).stat().st_size for name in capture['files'] if name != 'TEST_ROOT/test.xctestrun')
        (root / 'capture.json').write_text(json.dumps(capture))
        manifest = json.loads((root / 'manifest.json').read_text()); manifest['files'] = {**capture['files'], 'capture.json': replay.digest(root / 'capture.json')}
        (root / 'manifest.json').write_text(json.dumps(manifest))
        (root / 'SHA256SUMS').write_text(''.join(h + '  ' + n + '\n' for n, h in sorted({**manifest['files'], 'manifest.json': replay.digest(root / 'manifest.json')}.items())))

    def test_device_macho_cannot_be_executed_as_simulator_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = captured(Path(directory)); binary = root / next(iter(replay.EXECUTABLES)); binary.write_bytes(macho(b'wrong', platform=2)); self.refresh(root)
            with self.assertRaisesRegex(replay.artifacts.ReplayError, '^projection_not_arm64_simulator_code$'): replay.validate_products(root)

    def test_all_41_exact_outcomes_required_without_skip_duplicate_unknown_or_crash(self):
        self.assertEqual(len(replay.EXPECTED), 41)
        self.assertTrue(replay.cases(log())['all_expected_passed'])
        for data in [log(keys=sorted(replay.EXPECTED)[1:]), log('skipped'), log('failed'), log() + log(keys=[next(iter(replay.EXPECTED))]),
                     log() + b"Test Case '-[Module.OtherTests testUnknown]' passed (0.1 seconds).\n", log().replace(b"' passed", b"' crashed")]:
            with self.subTest(data=data[:80]): self.assertFalse(replay.cases(data)['all_expected_passed'])

    def test_native_children_receive_no_auth_api_or_session_environment(self):
        hash_key = 'TELEGRAM_' + 'API_HASH'
        fake_token = 'fixture-github-' + 'only'
        fake_hash = 'fixture-api-' + 'only'
        with patch.dict(os.environ, {'GH_TOKEN': fake_token, 'GITHUB_TOKEN': 'fixture-other-' + 'only', 'TELEGRAM_API_ID': '999', hash_key: fake_hash, 'SESSION_TOKEN': 'fixture-session-' + 'only', 'AUTHORIZATION': 'fixture-bearer-' + 'only', 'NSUnbufferedIO': 'NO', 'PATH': os.environ['PATH']}):
            child = replay.child_environment()
            self.assertEqual(child['NSUnbufferedIO'], 'YES')
            for key in ['GH_TOKEN', 'GITHUB_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'SESSION_TOKEN', 'AUTHORIZATION']: self.assertNotIn(key, child)
            with replay.clean_environment(): self.assertEqual(dict(os.environ), child)
            self.assertEqual(os.environ['GH_TOKEN'], fake_token)
            status, data = replay.native_command([sys.executable, '-c', 'import os;print(os.getenv("GH_TOKEN", "absent"),os.getenv("NSUnbufferedIO", "missing"))'])
            self.assertEqual(status['status'], 'completed'); self.assertEqual(data.strip(), b'absent YES')

    def test_native_command_output_cap_and_timeout_do_not_hang(self):
        with patch.object(replay, 'MAX_OUTPUT', 1024):
            status, data = replay.native_command([sys.executable, '-c', 'print("x"*5000)'], timeout=2)
            self.assertEqual(status['status'], 'outputLimit'); self.assertEqual(len(data), 1024)
        status, _ = replay.native_command([sys.executable, '-c', 'import time;time.sleep(5)'], timeout=.1)
        self.assertTrue(status['timeout'])

    def runner(self, failure=None, version='27.1', sdk='27.1', boot149=False):
        calls = []; device = '12345678-1234-1234-1234-123456789ABC'
        def run(args, timeout=30):
            calls.append(args)
            result = {'status': 'completed', 'exit_code': 0, 'timeout': False, 'output_limit': False, 'captured_bytes': 0}; data = b''
            if args == ['xcodebuild', '-version']: data = ('Xcode ' + version + '\nBuild version 27A9275').encode()
            elif '--show-sdk-version' in args: data = sdk.encode()
            elif 'runtimes' in args: data = json.dumps({'runtimes': [{'identifier': replay.RUNTIME, 'version': '27.0', 'isAvailable': True}]}).encode()
            elif 'devices' in args:
                data = json.dumps({'devices': {replay.RUNTIME: [{'name': replay.DEVICE_NAME, 'udid': device, 'state': 'Booted'}] if boot149 and any('bootstatus' in a for a in calls) else []}}).encode()
            elif 'create' in args: data = device.encode()
            elif 'get_app_container' in args: result.update(status='commandFailed', exit_code=2)
            elif 'bootstatus' in args and boot149: result.update(status='commandFailed', exit_code=149)
            elif 'test-without-building' in args:
                data = log()
                if failure == 'xctest': result.update(status='timedOut', exit_code=None, timeout=True); data = b'no case entry'
            return result, data
        return run, calls

    def test_sdk_mismatch_prevents_device_and_test_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run, calls = self.runner(sdk='99')
            result = replay.execute(root, root, root, run, lambda *args: None)
            self.assertFalse(result['passed']); self.assertFalse(any('create' in a for a in calls))

    def test_first_launch_readiness_is_read_only_bounded_and_precedes_device_creation(self):
        original = replay.native_command
        secret = 'first-launch-private-sentinel'
        for mode in ('ready', 'failed', 'timedOut'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); base, calls = self.runner()
                def run(arguments, timeout=30):
                    if arguments == ['xcodebuild', '-checkFirstLaunchStatus']:
                        self.assertEqual(timeout, 30)
                        calls.append(arguments)
                        code = 'import sys,time;sys.stdout.write(' + repr(secret + ' /private/first-launch-secret/path') + ');sys.stdout.flush();'
                        code += 'time.sleep(10)' if mode == 'timedOut' else 'sys.exit(' + ('0' if mode == 'ready' else '1') + ')'
                        return original([sys.executable, '-c', code], timeout=.1 if mode == 'timedOut' else 2)
                    return base(arguments, timeout)
                with patch.dict(os.environ, {'SESSION_TOKEN': secret}), patch.object(replay, 'host_bootstrap_diagnostic', return_value={'state': 'unavailable'}):
                    result = replay.execute(root, root, root, run, lambda *args: None)
                self.assertNotIn(secret, json.dumps(result)); self.assertNotIn('/private/first-launch-secret', json.dumps(result))
                self.assertIn('<REDACTED_CREDENTIAL>', result['diagnostics']['first_launch']['output'])
                self.assertLessEqual(len(json.dumps(result['diagnostics']['first_launch'])), 17 * 1024)
                self.assertEqual(result['commands']['first_launch']['status'], {'ready': 'completed', 'failed': 'commandFailed', 'timedOut': 'timedOut'}[mode])
                self.assertFalse(any('-runFirstLaunch' in args for args in calls))
                if mode == 'ready':
                    self.assertTrue(result['passed'])
                    self.assertLess(calls.index(['xcodebuild', '-checkFirstLaunchStatus']), next(i for i, args in enumerate(calls) if 'create' in args))
                else:
                    self.assertFalse(result['passed']); self.assertFalse(result['xctest_command_attempted']); self.assertFalse(result['tests_started'])
                    self.assertFalse(any('create' in args or 'test-without-building' in args for args in calls))
                    self.assertEqual(result['failure'], 'projection_first_launch_not_ready')

    def test_host_log_selection_rechecks_process_and_owned_identity_then_redacts_selected_fields(self):
        owned = '12345678-1234-1234-1234-123456789ABC'
        other = '87654321-4321-4321-4321-CBA987654321'
        secret = 'host-private-sentinel'
        records = [{'processImagePath': '/private/image-path/CoreSimulatorService',
            'eventMessage': 'device ' + owned.lower() + ' failed ' + secret + ' /private/host-secret/path',
            'timestamp': '2026-11-06 12:00:00.123456+0000', 'messageType': 'Error',
            'environment': secret, 'pid': 123, 'unselected': 'private extra field'}]
        records += [{'process': 'xcodebuild', 'eventMessage': 'prepare ' + identity} for identity in replay.HOST_LOG_IDENTITIES]
        records += [
            {'process': 'unapproved', 'eventMessage': replay.HOST_LOG_IDENTITIES[0] + ' dropped'},
            {'process': 'CoreSimulatorService', 'eventMessage': 'other device ' + other},
            {'process': 'xcodebuild', 'eventMessage': 'unrelated error'},
            {'process': 'CoreSimulatorService', 'eventMessage': 'not exact ' + owned + 'ABC'},
        ]
        result = replay.host_log_events(json.dumps(records).encode(), [secret], owned)
        self.assertEqual(result['state'], 'parsed'); self.assertEqual(len(result['events']), 4)
        self.assertEqual(result['events'][0]['process'], 'CoreSimulatorService')
        self.assertEqual(result['events'][0]['message_type'], 'Error')
        self.assertIn('<REDACTED_ID>', result['events'][0]['message'])
        self.assertIn('<REDACTED_CREDENTIAL>', result['events'][0]['message'])
        text = json.dumps(result)
        for private in (owned, owned.lower(), other, secret, '/private/', 'unrelated error', 'not exact', 'dropped', 'unselected', 'environment'):
            self.assertNotIn(private, text)
        self.assertEqual(set(result['events'][0]), {'process', 'message', 'timestamp', 'message_type'})

    def test_host_log_predicate_rejects_unowned_or_injected_identifiers_before_starting_any_command(self):
        owned = '12345678-1234-1234-1234-123456789ABC'
        predicate = replay.host_log_predicate(owned)
        self.assertEqual(replay.HOST_LOG_PROCESSES, {'xcodebuild', 'CoreSimulatorService', 'testmanagerd', 'runningboardd', 'xctest', 'launchd_sim'})
        self.assertEqual(predicate.count('process == '), 6)
        self.assertIn(' AND ', predicate)
        for identity in (*replay.HOST_LOG_IDENTITIES, owned): self.assertIn('eventMessage CONTAINS[c] "' + identity + '"', predicate)
        for identifier in (None, '', 'other device', owned + '" OR TRUE OR "', owned + '0', '{' + owned + '}'):
            with self.subTest(identifier=identifier), patch.object(replay.simulator_probe, 'diagnostic_command') as command:
                with self.assertRaises(ValueError): replay.host_bootstrap_diagnostic(identifier, [])
                with self.assertRaises(ValueError): replay.host_log_events(b'[]', [], identifier)
                command.assert_not_called()

    def test_host_log_limits_and_failures_never_publish_raw_records(self):
        owned = '12345678-1234-1234-1234-123456789ABC'
        small = {'process': 'xcodebuild', 'eventMessage': 'ProjectionTestHost ready'}
        result = replay.host_log_events(json.dumps([small] * 200).encode(), [], owned)
        self.assertEqual(result['records_seen'], 128); self.assertEqual(len(result['events']), 32)
        self.assertEqual(result['records_omitted'], 168)
        large = {'process': 'xcodebuild', 'eventMessage': 'ProjectionTestHost ' + 'é' * 7000}
        result = replay.host_log_events(json.dumps([large] * 4, ensure_ascii=False).encode(), [], owned)
        self.assertEqual(result['state'], 'parsed'); self.assertGreater(result['records_omitted'], 0)
        self.assertLessEqual(len(json.dumps(result)), 44 * 1024)
        for state, data in [('timedOut', b'[{"private":"raw sentinel"'), ('outputLimit', b'raw sentinel' * 8000),
                            ('commandFailed', b'raw sentinel'), ('completed', b'[{"private":"raw sentinel"')]:
            with self.subTest(state=state):
                status = {'status': state, 'exit_code': 0 if state == 'completed' else None,
                    'timeout': state == 'timedOut', 'output_limit': state == 'outputLimit',
                    'stdout_bytes': min(len(data), 65536), 'stderr_bytes': 1,
                    'stderr': replay.simulator_probe.redact_probe_text(('secret ' * 10000).encode(), ['secret'])}
                with patch.object(replay.simulator_probe, 'diagnostic_command', return_value=(status, data)) as command:
                    diagnostic = replay.host_bootstrap_diagnostic(owned, ['secret'])
                command.assert_called_once_with(['/usr/bin/log', 'show', '--style', 'json', '--last', '15m',
                    '--info', '--debug', '--predicate', replay.host_log_predicate(owned)], ['secret'])
                self.assertEqual(diagnostic['selection']['events'], [])
                self.assertEqual(diagnostic['selection']['state'], 'invalidPayload' if state == 'completed' else 'unavailable')
                self.assertNotIn('raw sentinel', json.dumps(diagnostic)); self.assertLessEqual(len(json.dumps(diagnostic)), 65536)
        self.assertEqual(replay.host_log_events(b'[]' + b' ' * 65536, [], owned)['state'], 'invalidPayload')

    def test_inventory_timeout_still_collects_host_logs_before_cleanup_without_changing_zero_case_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base, calls = self.runner(failure='xctest'); order = []
            def run(arguments, timeout=30):
                if 'test-without-building' in arguments: self.assertEqual(timeout, 300)
                if 'shutdown' in arguments or 'delete' in arguments: order.append(arguments[2])
                return base(arguments, timeout)
            def probe(destination, secrets, started_at):
                order.append('inventoryTimedOut')
                target = destination / 'build/fork-evidence'; target.mkdir(parents=True)
                (target / 'projection-launch-probe.json').write_text(json.dumps({'probe_only_not_test_result': True,
                    'commands': {'devices': {'status': 'timedOut', 'stdout_bytes': 0, 'stderr_bytes': 0}}}))
            def host_log(arguments, secrets, timeout=20):
                order.append('hostLog')
                self.assertEqual(arguments[:2], ['/usr/bin/log', 'show']); self.assertEqual(timeout, 20)
                data = json.dumps([{'process': 'CoreSimulatorService', 'eventMessage': 'device 12345678-1234-1234-1234-123456789ABC service unavailable'}]).encode()
                return {'status': 'completed', 'exit_code': 0, 'stderr': ''}, data
            with patch.object(replay.simulator_probe, 'diagnostic_command', side_effect=host_log):
                result = replay.execute(root, root, root, run, probe)
            self.assertEqual(order, ['hostLog', 'inventoryTimedOut', 'shutdown', 'delete'])
            self.assertFalse(result['passed']); self.assertFalse(result['tests_started']); self.assertTrue(result['xctest_command_attempted'])
            self.assertEqual(result['case_counts']['started'], 0); self.assertEqual(len(result['case_counts']['missing']), 41)
            self.assertEqual(result['assertion_retries'], 0); self.assertTrue(result['owned_device_cleanup_completed'])
            self.assertEqual(sum('test-without-building' in args for args in calls), 1)
            self.assertEqual(len(result['diagnostics']['host_bootstrap_log']['selection']['events']), 1)

    def test_only_expected_case_entry_sets_tests_started_and_failed_or_partial_run_never_passes(self):
        key = next(iter(replay.EXPECTED))
        single_start = ("Test Case '-[Module." + key.replace('.', ' ') + "]' started.\n").encode()
        for mode, data in [('failedCommand', b''), ('crashed', log('crashed')), ('startedOnly', single_start),
                           ('unknownOnly', b"Test Case '-[OtherTests testUnknown]' started.\n")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); base, calls = self.runner()
                def run(arguments, timeout=30):
                    status, stdout = base(arguments, timeout)
                    if 'test-without-building' in arguments:
                        status.update(status='commandFailed', exit_code=65)
                        stdout = data
                    return status, stdout
                with patch.object(replay, 'host_bootstrap_diagnostic', return_value={'state': 'unavailable'}):
                    result = replay.execute(root, root, root, run, lambda *args: None)
                self.assertTrue(result['xctest_command_attempted']); self.assertFalse(result['passed'])
                self.assertEqual(result['tests_started'], mode in {'crashed', 'startedOnly'})
                self.assertEqual(result['case_counts']['started'], {'crashed': 41, 'startedOnly': 1}.get(mode, 0))
                self.assertTrue(result['owned_device_cleanup_completed'])

    def test_one_test_invocation_and_owned_device_cleanup_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run, calls = self.runner(failure='xctest')
            result = replay.execute(root, root, root, run, lambda *args: None)
            self.assertFalse(result['passed']); self.assertTrue(result['owned_device_cleanup_completed'])
            self.assertTrue(result['xctest_command_attempted']); self.assertFalse(result['tests_started'])
            self.assertEqual(result['case_counts']['started'], 0)
            self.assertEqual(sum('test-without-building' in a for a in calls), 1)
            invocation = next(args for args in calls if 'test-without-building' in args)
            # Xcode 27 rejects -test-iterations 1; no repetition option means
            # its normal single execution, without assertion retries.
            for flag in ('-test-iterations', '-retry-tests-on-failure', '-run-tests-until-failure'):
                self.assertNotIn(flag, invocation)
            self.assertEqual(invocation[invocation.index('-parallel-testing-enabled') + 1], 'NO')
            self.assertTrue(any('shutdown' in a for a in calls)); self.assertTrue(any('delete' in a for a in calls))
            self.assertFalse((root / 'test.xcresult').exists())
            self.assertEqual(json.loads((root / 'case-results.json').read_text())['passed'], 0)

    def test_success_counts_are_historical_diagnostic_and_149_needs_exact_fresh_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run, calls = self.runner(boot149=True)
            result = replay.execute(root, root, root, run, lambda *args: None)
            self.assertTrue(result['passed']); self.assertTrue(result['already_booted_state_verified'])
            self.assertTrue(result['xctest_command_attempted']); self.assertTrue(result['tests_started'])
            self.assertEqual(result['case_counts']['started'], 41)
            self.assertEqual(result['case_counts']['passed'], 41)
            self.assertEqual(sum('test-without-building' in a for a in calls), 1)

    def test_only_fixed_fresh_output_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output in [root / 'elsewhere', root / replay.OUTPUT / '..']:
                with self.assertRaises(ValueError): replay.replay(replay.RUN, replay.SOURCE, output, root=root)

    def test_case_outcome_before_start_or_crash_cannot_be_a_pass(self):
        data = log(); lines = data.splitlines(); lines[0], lines[1] = lines[1], lines[0]
        result = replay.cases(b'\n'.join(lines))
        self.assertFalse(result['all_expected_passed']); self.assertTrue(result['invalid_case_event_order'])
        result = replay.cases(log('crashed'))
        self.assertFalse(result['all_expected_passed']); self.assertEqual(result['crashed'], 41)

    def test_boot_timeout_and_unverified_149_never_reach_tests_but_cleanup_runs(self):
        for mode in ('timeout', 'wrong_name'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); base, calls = self.runner(boot149=True)
                def run(args, timeout=30):
                    status, data = base(args, timeout)
                    if 'bootstatus' in args and mode == 'timeout': status.update(status='timedOut', timeout=True, exit_code=None)
                    if 'devices' in args and mode == 'wrong_name': data = data.replace(replay.DEVICE_NAME.encode(), b'Other device')
                    return status, data
                result = replay.execute(root, root, root, run, lambda *args: None)
                self.assertFalse(result['passed']); self.assertFalse(result['tests_started'])
                self.assertTrue(result['owned_device_cleanup_completed'])
                self.assertFalse(any('test-without-building' in a for a in calls))

    def test_cleanup_survives_probe_exception_and_child_failure_overrides_all_passed_cases(self):
        for mode in ('probe', 'exit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); base, calls = self.runner()
                def run(args, timeout=30):
                    status, data = base(args, timeout)
                    if mode == 'exit' and 'test-without-building' in args: status.update(status='commandFailed', exit_code=65)
                    return status, data
                def probe(*args):
                    if mode == 'probe': raise RuntimeError('must not be published')
                result = replay.execute(root, root, root, run, probe)
                self.assertFalse(result['passed']); self.assertTrue(result['owned_device_cleanup_completed'])
                self.assertNotIn('must not be published', json.dumps(result))

    def test_end_to_end_mocked_replay_publishes_only_bounded_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            # Match the production root before forming the fixed output path;
            # macOS may return /var while its canonical root is /private/var.
            root = Path(directory).resolve(); zipped = archive(root); digest = replay.digest(zipped); run_meta, artifact = metadata(zipped.stat().st_size, digest)
            run, calls = self.runner()
            def fetch(identifier, destination):
                self.assertEqual(identifier, replay.ARTIFACT); Path(destination).write_bytes(zipped.read_bytes())
            with patch.object(replay, 'OUTER_SHA256', digest):
                result = replay.replay(replay.RUN, replay.SOURCE, root / replay.OUTPUT, root, api_for(run_meta, artifact), fetch, run, lambda *args: None)
            self.assertTrue(result['passed']); self.assertTrue(result['diagnostic_replay_only'])
            self.assertFalse(result['application_recompiled_for_replay']); self.assertFalse(result['new_checkout_qualified'])
            self.assertFalse(result['physical_device_verified']); output = root / replay.OUTPUT
            self.assertEqual({p.name for p in output.iterdir()}, {'result.json', 'case-results.json', 'test.log', 'SHA256SUMS'})
            self.assertLessEqual((output / 'test.log').stat().st_size, 16 * 1024)
            for p in output.iterdir(): self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_native_command_retires_owned_descendant_after_leader_exits_and_pipe_stays_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); heartbeat = root / 'heartbeat'; group = root / 'group'
            child = ('import os,time,pathlib;'
                     'pathlib.Path(' + repr(str(group)) + ').write_text(str(os.getpgrp()));'
                     'f=open(' + repr(str(heartbeat)) + ',"ab",buffering=0)\n'
                     'while True:\n f.write(b"x");time.sleep(.02)\n')
            leader = ('import subprocess,sys,time,pathlib;'
                      'subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);'
                      'p=pathlib.Path(' + repr(str(heartbeat)) + ')\n'
                      'for _ in range(100):\n'
                      ' if p.exists() and p.stat().st_size: break\n'
                      ' time.sleep(.01)\n')
            try:
                status, _ = replay.native_command([sys.executable, '-c', leader], timeout=3)
                self.assertEqual(status['status'], 'completed')
                self.assertTrue(group.is_file()); self.assertGreater(heartbeat.stat().st_size, 0)
                size = heartbeat.stat().st_size; time.sleep(.15)
                self.assertEqual(heartbeat.stat().st_size, size, 'owned descendant continued after the bounded drain')
            finally:
                if group.is_file():
                    try: os.killpg(int(group.read_text()), signal.SIGKILL)
                    except ProcessLookupError: pass


class ProjectionHostControlTests(unittest.TestCase):
    """A private seal control may change no XCTest code, data, or launch plan."""

    @staticmethod
    def signed_host(info, resources, flags=2, requirements=None, entitlements=None,
                    der_entitlements=None, identifier=None, body=b'\xc0\x03\x5f\xd6'):
        import struct
        pack = struct.pack
        code_size = signature_size = 4096
        commands = [
            pack('<6I', 0x32, 24, 7, 0xF0000, 0x1B0100, 0),
            pack('<II16s4Q4I', 0x19, 72, b'__TEXT', 0, code_size, 0, code_size, 5, 5, 0, 0),
            pack('<II16s4Q4I', 0x19, 72, b'__LINKEDIT', code_size, signature_size,
                 code_size, signature_size, 1, 1, 0, 0),
            pack('<4I', 0x1D, 16, code_size, signature_size),
            pack('<IIQQ', 0x80000028, 24, 256, 0),
        ]
        command_bytes = b''.join(commands)
        code = pack('<8I', 0xFEEDFACF, 0x0100000C, 0, 2, len(commands),
                    len(command_bytes), 0x200085, 0) + command_bytes
        code = code.ljust(256, b'\0') + body
        code = code.ljust(code_size, b'\0')
        if requirements is None:
            requirements = pack('>3I', 0xFADE0C01, 12, 0)
        if entitlements is None:
            entitlements = {'com.apple.security.get-task-allow': True}
        xml = plistlib.dumps(entitlements)
        xml_blob = pack('>2I', 0xFADE7171, 8 + len(xml)) + xml
        if der_entitlements is None:
            der_entitlements = bytes.fromhex(
                '702d020101b02830260c21636f6d2e6170706c652e73656375726974792e'
                '6765742d7461736b2d616c6c6f770101') + (
                    b'\xff' if entitlements.get('com.apple.security.get-task-allow') else b'\0')
        der_blob = pack('>2I', 0xFADE7172, 8 + len(der_entitlements)) + der_entitlements
        identity = (identifier or products.BUNDLE).encode() + b'\0'
        special_count = 7
        hash_offset = 88 + len(identity) + special_count * 32
        payloads = {1: info, 2: requirements, 3: resources, 5: xml_blob, 7: der_blob}
        hashes = b''.join(hashlib.sha256(payloads[slot]).digest() if slot in payloads else b'\0' * 32
                          for slot in range(special_count, 0, -1))
        cd_length = hash_offset + 32
        directory = pack('>9I4B4I4Q', 0xFADE0C02, cd_length, 0x20400, flags,
                         hash_offset, 88, special_count, 1, code_size,
                         32, 2, 0, 12, 0, 0, 0, 0, 0, 0, code_size, 1)
        directory += identity + hashes + hashlib.sha256(code).digest()
        blobs = [(0, directory), (2, requirements), (5, xml_blob), (7, der_blob),
                 (0x10000, pack('>2I', 0xFADE0B01, 8))]
        offset = 12 + len(blobs) * 8
        index, contents = [], []
        for slot, blob in blobs:
            index.append(pack('>2I', slot, offset)); contents.append(blob); offset += len(blob)
        signature = pack('>3I', 0xFADE0CC0, offset, len(blobs)) + b''.join(index + contents)
        if len(signature) > signature_size:
            raise AssertionError('The synthetic embedded signature exceeded its reserved region')
        return code + signature.ljust(signature_size, b'\0')

    @staticmethod
    def refresh_capture(root):
        capture_path, manifest_path = root / 'capture.json', root / 'manifest.json'
        capture_report, manifest = json.loads(capture_path.read_bytes()), json.loads(manifest_path.read_bytes())
        captured_files = {str(path.relative_to(root)): replay.digest(path)
                          for path in (root / 'TEST_ROOT').rglob('*') if path.is_file()}
        capture_report['files'] = captured_files
        capture_report['total_product_bytes'] = sum((root / name).stat().st_size for name in captured_files
                                                   if name != 'TEST_ROOT/test.xctestrun')
        capture_path.write_text(json.dumps(capture_report))
        manifest['source_commit'] = replay.CURRENT_SOURCE
        manifest['files'] = {**captured_files, 'capture.json': replay.digest(capture_path)}
        manifest_path.write_text(json.dumps(manifest))
        checksums = {**manifest['files'], 'manifest.json': replay.digest(manifest_path)}
        (root / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n'
                                               for name, value in sorted(checksums.items())))

    def control_fixture(self, root):
        original = captured(root)
        app = original / 'TEST_ROOT' / products.APP
        test = app / 'PlugIns' / products.TEST
        (test / 'fixture-resource').write_bytes(b'unchanged synthetic XCTest resource')
        (test / 'fixture-resource').chmod(0o600)
        (test / '_CodeSignature').mkdir(mode=0o700)
        (test / '_CodeSignature/CodeResources').write_bytes(plistlib.dumps({'files2': {}}))
        metadata = plistlib.loads((app / 'Info.plist').read_bytes())
        metadata['CFBundleSupportedPlatforms'] = ['iPhoneSimulator']
        (app / 'Info.plist').write_bytes(plistlib.dumps(metadata))
        (app / '_CodeSignature').mkdir(mode=0o700)
        seal = app / '_CodeSignature/CodeResources'
        seal.write_bytes(plistlib.dumps({'files2': {}, 'fixture': 'original outer seal'}))
        (app / 'ProjectionTestHost').write_bytes(self.signed_host((app / 'Info.plist').read_bytes(), seal.read_bytes()))
        (app / 'ProjectionTestHost').chmod(0o700)
        self.refresh_capture(original)
        return original

    def signer(self, mutation=None, failure=None):
        import struct
        calls = []
        verification_count = 0
        secret = 'private-control-diagnostic-canary'

        def command(arguments, secrets, timeout=20):
            nonlocal verification_count
            calls.append(list(arguments))
            self.assertEqual(timeout, 20)
            self.assertEqual(arguments[0], '/usr/bin/codesign')
            app = Path(arguments[-1])
            self.assertEqual(app.name, products.APP)
            if '--force' not in arguments:
                self.assertEqual(arguments, ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
                verification_count += 1
                failed = verification_count == 1 or failure == 'verify'
            else:
                self.assertEqual(arguments[:7], ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
                    '--preserve-metadata=identifier,requirements,flags', '--entitlements'])
                self.assertEqual(len(arguments), 9)
                self.assertNotIn('--deep', arguments)
                entitlements = Path(arguments[7])
                self.assertFalse(entitlements.is_relative_to(app))
                self.assertEqual(stat.S_IMODE(entitlements.stat().st_mode), 0o600)
                self.assertEqual(plistlib.loads(entitlements.read_bytes()), {'com.apple.security.get-task-allow': True})
                failed = failure == 'sign'
                if not failed:
                    seal = app / '_CodeSignature/CodeResources'
                    seal.write_bytes(plistlib.dumps({'files2': {}, 'fixture': 'resealed outer seal'}))
                    options = {}
                    if mutation == 'body': options['body'] = b'\0\0\0\0'
                    elif mutation == 'entitlements': options['entitlements'] = {'com.apple.security.get-task-allow': False}
                    elif mutation == 'flags': options['flags'] = 0x10002
                    elif mutation == 'identifier': options['identifier'] = products.BUNDLE + '.changed'
                    elif mutation == 'requirements':
                        options['requirements'] = struct.pack('>5I', 0xFADE0C01, 32, 1, 3, 20) + struct.pack('>3I', 0xFADE0C00, 12, 1)
                    elif mutation == 'der': options['der_entitlements'] = b'\x70\x00'
                    (app / 'ProjectionTestHost').write_bytes(self.signed_host((app / 'Info.plist').read_bytes(), seal.read_bytes(), **options))
                    test = app / 'PlugIns' / products.TEST
                    if mutation == 'test_resource': (test / 'fixture-resource').write_bytes(b'tampered test resource')
                    elif mutation == 'test_binary': (test / 'ChatLocalTextFilterProjectionTests').write_bytes(b'tampered XCTest code')
                    elif mutation == 'test_seal': (test / '_CodeSignature/CodeResources').write_bytes(b'tampered test signature')
                    elif mutation == 'plan': (app.parent / 'test.xctestrun').write_bytes(b'tampered launch plan')
                    elif mutation == 'mode': (test / 'fixture-resource').chmod(0o644)
                    elif mutation == 'extra_file': (app / 'signer-added-file').write_bytes(b'unapproved resource')
            status = {'status': 'commandFailed' if failed else 'completed', 'exit_code': 1 if failed else 0,
                      'timeout': False, 'output_limit': False, 'stdout_bytes': 100, 'stderr_bytes': 0, 'stderr': ''}
            return status, (secret + ' /private/control-fixture/diagnostic').encode()
        return command, calls, secret

    def test_signed_fixture_and_original_control_preserve_every_file_mode_and_independent_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = self.control_fixture(root)
            replay.validate_products(original, replay.CURRENT_SOURCE)
            signature = replay.host_signature(original / 'TEST_ROOT' / products.APP)
            self.assertEqual(signature['identifier'], products.BUNDLE)
            self.assertEqual(signature['flags'], 2)
            self.assertEqual(signature['entitlements'], {'com.apple.security.get-task-allow': True})
            snapshot = replay.snapshot_tree(original)
            with patch.object(replay.simulator_probe, 'diagnostic_command', side_effect=AssertionError('original control must not sign')) as command:
                report = replay.prepare_control(original, root / 'original-arm', 'original', {}, [], command)
                command.assert_not_called()
            self.assertEqual(report, {'mode': 'original', 'source_products_unchanged': True,
                'independent_private_copy': True, 'signing_modified_for_diagnostic': False})
            self.assertEqual(replay.snapshot_tree(original), snapshot)
            self.assertEqual(replay.snapshot_tree(root / 'original-arm'), snapshot)
            for name, (kind, _, _) in snapshot.items():
                if kind == 'file':
                    first, second = (original / name).stat(), (root / 'original-arm' / name).stat()
                    self.assertNotEqual((first.st_dev, first.st_ino), (second.st_dev, second.st_ino))

    def test_resealed_control_changes_only_host_signature_and_outer_seal_with_original_products_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = self.control_fixture(root)
            before = replay.snapshot_tree(original); diagnostics = {}
            command, calls, secret = self.signer()
            report = replay.prepare_control(original, root / 'resealed-arm', 'resealed-host', diagnostics, [secret], command)
            self.assertTrue(report['signing_modified_for_diagnostic'])
            for field in ('source_products_unchanged', 'independent_private_copy', 'host_code_unchanged',
                          'test_tree_unchanged', 'entitlements_preserved', 'requirements_preserved',
                          'identifier_and_flags_preserved', 'strict_seal_verified'):
                self.assertTrue(report[field])
            self.assertEqual(replay.snapshot_tree(original), before)
            after = replay.snapshot_tree(root / 'resealed-arm')
            changed = {name for name in before if after[name] != before[name]}
            self.assertEqual(changed, {'TEST_ROOT/' + products.APP + '/ProjectionTestHost',
                'TEST_ROOT/' + products.APP + '/_CodeSignature/CodeResources'})
            self.assertEqual(sum('--force' in args for args in calls), 1)
            self.assertEqual(len(calls), 3)
            text = json.dumps(diagnostics)
            self.assertNotIn(secret, text); self.assertNotIn('/private/control-fixture', text)
            self.assertIn('<REDACTED_CREDENTIAL>', text)

    def test_resealed_control_rejects_code_test_plan_mode_and_signing_identity_tampering(self):
        mutations = ('body', 'test_resource', 'test_binary', 'test_seal', 'plan', 'mode', 'extra_file',
                     'entitlements', 'flags', 'identifier', 'requirements', 'der')
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); original = self.control_fixture(root)
                before = replay.snapshot_tree(original)
                command, calls, secret = self.signer(mutation=mutation)
                with self.assertRaises(ValueError):
                    replay.prepare_control(original, root / 'tampered-arm', 'resealed-host', {}, [secret], command)
                self.assertEqual(replay.snapshot_tree(original), before)
                self.assertEqual(sum('--force' in args for args in calls), 1)
                self.assertEqual(len(calls), 2, 'tampered products must fail before final verification')

    def test_sign_or_strict_verification_failure_blocks_control_without_retries(self):
        for failure in ('sign', 'verify'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); original = self.control_fixture(root)
                before = replay.snapshot_tree(original); diagnostics = {}
                command, calls, secret = self.signer(failure=failure)
                with self.assertRaises(ValueError):
                    replay.prepare_control(original, root / 'failed-arm', 'resealed-host', diagnostics, [secret], command)
                self.assertEqual(replay.snapshot_tree(original), before)
                self.assertEqual(sum('--force' in args for args in calls), 1)
                self.assertEqual(len(calls), 2 if failure == 'sign' else 3)
                self.assertNotIn(secret, json.dumps(diagnostics))

    def test_existing_destination_unknown_control_and_symlink_source_are_rejected_before_signing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = self.control_fixture(root)
            existing = root / 'existing'; existing.mkdir(); (existing / 'keep').write_bytes(b'keep')
            linked = root / 'linked'; linked.symlink_to(original, target_is_directory=True)
            for source, destination, mode in ((original, existing, 'resealed-host'),
                                               (original, root / 'unknown', 'arbitrary-control'),
                                               (linked, root / 'linked-arm', 'resealed-host')):
                with self.subTest(mode=mode, source=source), patch.object(replay.simulator_probe, 'diagnostic_command') as command:
                    with self.assertRaises(ValueError): replay.prepare_control(source, destination, mode, {}, [], command)
                    command.assert_not_called()
            self.assertEqual((existing / 'keep').read_bytes(), b'keep')

    def test_reseal_requires_a_known_failing_source_seal_before_the_signer_can_run(self):
        for state, code in (('completed', 0), ('commandFailed', 65), ('timedOut', None),
                            ('outputLimit', None), ('unavailable', None)):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); original = self.control_fixture(root)
                before = replay.snapshot_tree(original)
                status = {'status': state, 'exit_code': code, 'timeout': state == 'timedOut',
                          'output_limit': state == 'outputLimit', 'stdout_bytes': 0, 'stderr_bytes': 0, 'stderr': ''}
                with patch.object(replay.simulator_probe, 'diagnostic_command', return_value=(status, b'')) as command:
                    with self.assertRaisesRegex(ValueError, 'source_seal_contrast_unavailable'):
                        replay.prepare_control(original, root / 'unproven-arm', 'resealed-host', {}, [], command)
                command.assert_called_once()
                self.assertNotIn('--force', command.call_args.args[0])
                self.assertEqual(replay.snapshot_tree(original), before)

    def test_historical_capture_cannot_be_resealed_as_a_current_source_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); original = self.control_fixture(root)
            manifest = json.loads((original / 'manifest.json').read_bytes())
            manifest['source_commit'] = replay.SOURCE
            (original / 'manifest.json').write_text(json.dumps(manifest))
            checksums = {**manifest['files'], 'manifest.json': replay.digest(original / 'manifest.json')}
            (original / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n'
                                                       for name, value in sorted(checksums.items())))
            replay.validate_products(original, replay.SOURCE)
            with patch.object(replay.simulator_probe, 'diagnostic_command') as command:
                with self.assertRaisesRegex(ValueError, 'reseal_requires_current_source'):
                    replay.prepare_control(original, root / 'historical-arm', 'resealed-host', {}, [], command)
            command.assert_not_called()
