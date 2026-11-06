"""Historical Projection replay must not become an arbitrary artifact executor."""
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
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


def metadata(size=100, digest=None):
    artifact = {'id': replay.ARTIFACT, 'name': 'archive-integration-tests-' + replay.SOURCE,
                'expired': False, 'size_in_bytes': size, 'digest': 'sha256:' + (digest or replay.OUTER_SHA256),
                'workflow_run': {'id': replay.RUN, 'head_sha': replay.SOURCE, 'repository_id': 7, 'head_repository_id': 7}}
    run = {'id': replay.RUN, 'head_sha': replay.SOURCE, 'status': 'completed', 'path': replay.artifacts.WORKFLOW,
           'repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY},
           'head_repository': {'id': 7, 'full_name': replay.artifacts.REPOSITORY}}
    return run, artifact


def api_for(run, artifact):
    return lambda endpoint: {'total_count': 1, 'artifacts': [artifact]} if '/artifacts?' in endpoint else run


def captured(root):
    source = root / 'source'; source.mkdir(); fixture(source)
    (source / products.APP / 'ProjectionTestHost').write_bytes(macho(b'host'))
    (source / products.APP / 'PlugIns' / products.TEST / 'ChatLocalTextFilterProjectionTests').write_bytes(macho(b'test', kind=8))
    destination = root / 'capture'; report = products.capture_products(source, destination)
    hashes = {**report['files'], 'capture.json': replay.digest(destination / 'capture.json')}
    manifest = {'schema_version': 1, 'source_commit': replay.SOURCE, 'test_target': products.LABEL,
                'toolchain': replay.PINS, 'application_recompiled_for_replay': False,
                'diagnostic_products_not_test_pass': True, 'physical_device_verified': False, 'files': hashes}
    (destination / 'manifest.json').write_text(json.dumps(manifest))
    sums = {**hashes, 'manifest.json': replay.digest(destination / 'manifest.json')}
    (destination / 'SHA256SUMS').write_text(''.join(h + '  ' + n + '\n' for n, h in sorted(sums.items())))
    return destination


def archive(root, mutate=None):
    source = captured(root)
    if mutate: mutate(source)
    target = root / 'artifact.zip'
    with zipfile.ZipFile(target, 'w') as zipped:
        for path in source.rglob('*'):
            if path.is_file(): zipped.write(path, products.CAPTURE + '/' + str(path.relative_to(source)))
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
        with patch.dict(os.environ, {'GH_TOKEN': fake_token, 'GITHUB_TOKEN': 'fixture-other-' + 'only', 'TELEGRAM_API_ID': '999', hash_key: fake_hash, 'SESSION_TOKEN': 'fixture-session-' + 'only', 'AUTHORIZATION': 'fixture-bearer-' + 'only', 'PATH': os.environ['PATH']}):
            child = replay.child_environment()
            for key in ['GH_TOKEN', 'GITHUB_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'SESSION_TOKEN', 'AUTHORIZATION']: self.assertNotIn(key, child)
            with replay.clean_environment(): self.assertEqual(dict(os.environ), child)
            self.assertEqual(os.environ['GH_TOKEN'], fake_token)
            status, data = replay.native_command([sys.executable, '-c', 'import os;print(os.getenv("GH_TOKEN", "absent"))'])
            self.assertEqual(status['status'], 'completed'); self.assertEqual(data.strip(), b'absent')

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

    def test_one_test_invocation_and_owned_device_cleanup_after_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run, calls = self.runner(failure='xctest')
            result = replay.execute(root, root, root, run, lambda *args: None)
            self.assertFalse(result['passed']); self.assertTrue(result['owned_device_cleanup_completed'])
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
