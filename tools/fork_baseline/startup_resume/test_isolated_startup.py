"""Portable adversarial routing/ownership tests; never invoke CoreSimulator."""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('isolated_startup', Path(__file__).with_name('isolated_startup.py'))
isolated = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isolated)
IDENTIFIER = '11111111-2222-3333-4444-555555555555'
OTHER = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
CONTAINER = '12345678-1234-1234-1234-123456789012'


class FakeSmokeError(RuntimeError):
    def __init__(self, code, phase, **unused):
        self.code, self.phase = code, phase
        super().__init__(code)


def successful_reports():
    proof = {'passed': True, 'phase': 'complete', 'private_control_only': True,
        'private_mode_control': 'explicitFreshPinnedSelection', 'main_app_only_control_passed': True,
        'source_codesign_verified': True, 'main_code_unchanged': True, 'source_ipa_unchanged': True,
        'application_recompiled': False, 'source_simulator_ipa_sha256': isolated.IPA_SHA256,
        'physical_device_verified': False, 'automatic_control_run': False,
        'automatic_fallback_verified': False, 'automatic_group_selection_observed': False}
    result = {'passed': True, 'phase': 'complete', 'failure': None, 'cleanup_failures': [],
        'storage_test_setup': 'freshPinnedPrivate', 'private_selection_seeded_before_launch': True,
        'private_account_store_absent_before_launch': True, 'physical_device_verified': False,
        'authentication_codes_used': False, 'recording_return_code': 0, 'simulator_device_type': isolated.MODEL,
        'simulator_runtime': isolated.RUNTIME,
        'screenshot_checker': {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3},
        'launches': [{'attempt': index, 'storage_kind': 'private', 'private_account_store_present': True,
            'private_account_store_stable_after_restart': True,
            'marker': {'stage': 'authorizationVisible', 'failure': None},
            'screenshot': {'nonblank': True}} for index in (1, 2)]}
    cleanup = {'schema': 1, 'owned_device_deleted': True,
               'owned_uuid_absence_verified': True, 'exact_owned_device_only': True}
    return proof, result, cleanup


class IsolatedStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Match the workflow's canonical RUNNER_TEMP -> TMPDIR handoff before
        # fresh Python startup. Do not relax the adapter's owned-path checks.
        self.base = Path(self.temporary.name).resolve(strict=True)
        self.output = self.base / 'replay'
        self.calls = []
        self.command_calls = []
        self.mode = None
        self.smoke = types.SimpleNamespace(__file__=str(self.base / 'tools/launch_smoke.py'), SmokeError=FakeSmokeError)
        self.process_module = types.SimpleNamespace(PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired, SubprocessError=subprocess.SubprocessError, Popen=self.popen)
        self.smoke.subprocess = self.process_module
        def command(arguments, timeout=120, phase='extractApplication', compiler_log=None):
            self.command_calls.append((list(arguments), timeout, phase))
            result = self.smoke.subprocess.Popen(arguments, stdout=self.smoke.subprocess.PIPE,
                                                stderr=self.smoke.subprocess.PIPE)
            return result.output
        self.smoke.command = command
        original = tempfile.mkdtemp
        with patch.object(isolated.tempfile, 'mkdtemp', lambda prefix: original(prefix=prefix, dir=self.base)):
            self.state = isolated.OwnedDeviceSet(self.smoke, self.output)
        self.default = self.base / 'default-device-set' / OTHER
        self.default.mkdir(parents=True)
        (self.default / 'untouched').write_bytes(b'owned sentinel; never a simctl target')

    def popen(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if args[:2] != ['xcrun', 'simctl']:
            return types.SimpleNamespace(output='{}')
        self.assertEqual(args[2:4], ['--set', str(self.state.root)])
        action = args[4]
        if action == 'create':
            device = self.state.root / IDENTIFIER
            device.mkdir()
            (device / 'device.plist').write_bytes(plistlib.dumps({'UDID': IDENTIFIER,
                'name': args[5], 'deviceType': isolated.MODEL, 'runtime': isolated.RUNTIME}))
            return types.SimpleNamespace(output=IDENTIFIER)
        if action == 'get_app_container':
            return types.SimpleNamespace(output=str(self.container()))
        if action == 'delete':
            if self.mode == 'deleteTimeout': raise FakeSmokeError('commandTimedOut', 'deleteDevice')
            if self.mode != 'deleteLeavesDirectory': shutil.rmtree(self.state.root / IDENTIFIER)
            if self.mode == 'unknownDevice': (self.state.root/OTHER).mkdir()
            if self.mode == 'unknownDirectory': (self.state.root/'unexpected').mkdir()
            if self.mode == 'symlink': (self.state.root/'device_set.plist').symlink_to(self.default/'untouched')
            if self.mode == 'hardlink': os.link(self.default/'untouched',self.state.root/'device_set.plist')
            if self.mode == 'unknownFile': (self.state.root/'unexpected').write_bytes(b'unknown')
            if self.mode == 'fifo': os.mkfifo(self.state.root/'device_set.plist')
            if self.mode == 'bookkeeping': (self.state.root/'device_set.plist').write_bytes(plistlib.dumps({'unchanged':'bookkeeping'}))
        self.assertNotEqual(action,'list','No extra CoreSimulator IPC after successful deletion')
        return types.SimpleNamespace(output='')

    def container(self):
        path = self.state.root / IDENTIFIER / 'data/Containers/Data/Application' / CONTAINER
        path.mkdir(parents=True, exist_ok=True)
        (path / '.com.apple.mobile_container_manager.metadata.plist').write_bytes(plistlib.dumps({
            'MCMMetadataIdentifier': isolated.BUNDLE, 'MCMMetadataUUID': CONTAINER}))
        return path

    def create(self):
        return self.smoke.command(['xcrun', 'simctl', 'create', 'AYU_SMOKE_' + 'a' * 32,
                                  isolated.MODEL, isolated.RUNTIME], phase='createDevice')

    def test_private_set_is_unique_owned_empty_0700_and_never_auto_removed(self):
        self.assertEqual(self.state.root.stat().st_uid, os.getuid())
        self.assertEqual(self.state.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(self.state.root.iterdir()), [])
        with isolated.adapters(self.smoke, self.state): self.create()
        self.assertTrue((self.state.root / IDENTIFIER).is_dir())
        self.assertEqual((self.default / 'untouched').read_bytes(), b'owned sentinel; never a simctl target')

    def test_all_simctl_command_shapes_and_recorder_use_owned_set_and_timeouts(self):
        temporary = self.base / 'ayu-launch-smoke-test'; temporary.mkdir(mode=0o700)
        app = temporary / 'Telegram.app'; app.mkdir()
        checker = temporary / 'screenshot-check'
        with isolated.adapters(self.smoke, self.state):
            self.smoke.command(['xcrun', 'swiftc', '-O', '-framework', 'CoreGraphics', '-framework', 'ImageIO',
                str(Path(self.smoke.__file__).with_name('screenshot_check.swift')), '-o', str(checker)],
                timeout=300, phase='compileScreenshotChecker', compiler_log=self.state.output / 'checker-compiler.log')
            self.smoke.command([str(checker), '--self-test'], timeout=30, phase='testScreenshotChecker')
            self.create()
            self.smoke.command(['xcrun', 'simctl', 'boot', IDENTIFIER], phase='bootDevice')
            self.smoke.command(['xcrun', 'simctl', 'bootstatus', IDENTIFIER, '-b'], timeout=180, phase='waitForBoot')
            self.smoke.command(['xcrun', 'simctl', 'install', IDENTIFIER, str(app)], phase='installApplication')
            self.smoke.subprocess.Popen(['xcrun', 'simctl', 'io', IDENTIFIER, 'recordVideo', '--codec=h264', '--force',
                str(self.state.output / 'launch.mp4')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.smoke.command(['xcrun', 'simctl', 'get_app_container', IDENTIFIER, isolated.BUNDLE, 'data'], phase='getContainer')
            for action in ('launch', 'terminate'):
                self.smoke.command(['xcrun', 'simctl', action, IDENTIFIER, isolated.BUNDLE], phase='launchApplication')
            self.smoke.command(['xcrun', 'simctl', 'io', IDENTIFIER, 'screenshot', str(self.state.output / 'launch-1.png')], phase='captureScreenshot')
            self.smoke.command([str(checker), str(self.state.output / 'launch-1.png')], timeout=30, phase='checkScreenshot')
            self.smoke.command(['xcrun', 'simctl', 'shutdown', IDENTIFIER], timeout=30, phase='shutdownDevice')
            self.smoke.command(['xcrun', 'simctl', 'delete', IDENTIFIER], timeout=30, phase='deleteDevice')
        self.assertEqual(self.state.proof(), successful_reports()[2])
        self.assertTrue(self.state.root.is_dir())  # No rmtree by production adapter.
        self.assertEqual([t for a,t,p in self.command_calls if p=='installApplication'], [120])
        self.assertEqual([t for a,t,p in self.command_calls if p=='deleteDevice'], [30])
        self.assertTrue(all(a[2:4]==['--set',str(self.state.root)] for a,k in self.calls if a[:2]==['xcrun','simctl']))

    def test_wrong_uuid_aliases_global_actions_and_timeout_increase_rejected_before_spawn(self):
        with isolated.adapters(self.smoke, self.state):
            self.create()
            count = len(self.calls)
            commands = [(['xcrun','simctl','shutdown',value],30,'shutdownDevice') for value in ('all','booted',OTHER)]
            commands += [(['xcrun','simctl','erase',IDENTIFIER],120,'bootDevice'),
                (['xcrun','simctl','delete','unavailable'],30,'deleteDevice'),
                (['xcrun','simctl','--set',str(self.default),'delete',IDENTIFIER],30,'deleteDevice'),
                (['xcrun','simctl','boot',IDENTIFIER],121,'bootDevice'),
                (['xcrun','simctl','list','devices','--json'],30,'deleteDevice'),
                (['killall','Simulator'],120,'bootDevice')]
            for args, timeout, phase in commands:
                with self.subTest(args=args), self.assertRaises(FakeSmokeError):
                    self.smoke.command(args, timeout=timeout, phase=phase)
            self.assertEqual(len(self.calls), count)
        self.assertTrue((self.default / 'untouched').is_file())
        self.assertFalse(self.state.proof()['exact_owned_device_only'])

    def test_unvalidated_direct_popen_or_recorder_escape_is_rejected(self):
        with isolated.adapters(self.smoke, self.state):
            self.create(); count=len(self.calls)
            for args in (['xcrun','simctl','delete',IDENTIFIER], ['xcrun','simctl','io',OTHER,'recordVideo',
                '--codec=h264','--force',str(self.state.output/'launch.mp4')],
                ['xcrun','simctl','io',IDENTIFIER,'recordVideo','--codec=h264','--force','/tmp/escape.mp4']):
                with self.subTest(args=args), self.assertRaises(FakeSmokeError):
                    self.smoke.subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(len(self.calls),count)

    def test_device_plist_identity_is_rechecked_before_boot_install_and_cleanup(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); metadata=self.state.root/IDENTIFIER/'device.plist'; original=metadata.read_bytes()
            for field in ('UDID','name','deviceType','runtime'):
                data=plistlib.loads(original); data[field]='wrong'; metadata.write_bytes(plistlib.dumps(data))
                for action,phase,timeout in [('boot','bootDevice',120),('shutdown','shutdownDevice',30),('delete','deleteDevice',30)]:
                    with self.subTest(field=field,action=action),self.assertRaises(FakeSmokeError):
                        self.smoke.command(['xcrun','simctl',action,IDENTIFIER],timeout=timeout,phase=phase)
                metadata.write_bytes(original)
        self.assertTrue((self.state.root/IDENTIFIER).is_dir())

    def test_other_uuid_in_private_set_prevents_destructive_action(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); (self.state.root/OTHER).mkdir(); count=len(self.calls)
            with self.assertRaises(FakeSmokeError): self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
            self.assertEqual(len(self.calls),count)
        self.assertTrue((self.state.root/IDENTIFIER).is_dir())

    def test_symlink_and_wrong_uid_device_metadata_fail_closed(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); metadata=self.state.root/IDENTIFIER/'device.plist'
            outside=self.base/'foreign.plist'; metadata.rename(outside); metadata.symlink_to(outside)
            with self.assertRaises(FakeSmokeError): self.smoke.command(['xcrun','simctl','boot',IDENTIFIER],phase='bootDevice')
            metadata.unlink(); outside.rename(metadata); self.state.uid+=1
            with self.assertRaises(FakeSmokeError): self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')

    def test_root_replacement_or_permissions_change_refuses_cleanup(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); root=self.state.root; root.chmod(0o755)
            with self.assertRaises(FakeSmokeError): self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
            root.chmod(0o700); backup=root.with_name(root.name+'-old'); root.rename(backup); root.mkdir(mode=0o700)
            with self.assertRaises(FakeSmokeError): self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
            self.assertTrue((backup/IDENTIFIER).is_dir())

    def test_container_requires_exact_owned_data_layout_and_bundle_metadata(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); container=self.container(); self.assertEqual(self.state.verify_container(str(container)),str(container))
            for path in (self.default,self.state.root/OTHER/'data',container/'nested',Path('/tmp')):
                with self.subTest(path=path),self.assertRaises((isolated.IsolationError,ValueError,OSError)):
                    self.state.verify_container(str(path))
            metadata=container/'.com.apple.mobile_container_manager.metadata.plist'
            for data in ({'MCMMetadataIdentifier':'another.bundle'}, {'MCMMetadataIdentifier':isolated.BUNDLE,'MCMMetadataUUID':OTHER}):
                metadata.write_bytes(plistlib.dumps(data))
                with self.assertRaises(isolated.IsolationError):self.state.verify_container(str(container))

    def test_container_symlink_is_not_followed(self):
        with isolated.adapters(self.smoke,self.state):
            self.create(); container=self.container(); backup=self.base/'container-copy'; container.rename(backup); container.symlink_to(backup)
            with self.assertRaises(isolated.IsolationError):self.state.verify_container(str(container))

    def test_delete_timeout_cannot_claim_cleanup_or_remove_backing_files(self):
        self.mode='deleteTimeout'
        with isolated.adapters(self.smoke,self.state):
            self.create()
            with self.assertRaises(FakeSmokeError):self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
        self.assertFalse(self.state.deleted);self.assertFalse(self.state.absence)
        self.assertTrue((self.state.root/IDENTIFIER).exists())

    def test_delete_exit_zero_without_filesystem_absence_is_not_cleanup_pass(self):
        self.mode='deleteLeavesDirectory'
        with isolated.adapters(self.smoke,self.state):
            self.create()
            with self.assertRaises(FakeSmokeError):self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
        self.assertTrue(self.state.deleted);self.assertFalse(self.state.absence)

    def test_unsafe_or_nonempty_private_inventory_is_not_absence_proof(self):
        for mode in ('unknownDevice','unknownDirectory','symlink','hardlink','unknownFile','fifo'):
            with self.subTest(mode=mode):
                self.mode=mode
                # Each subcase requires its own newly created state/set.
                self.state=isolated.OwnedDeviceSet(self.smoke,self.output)
                self.addCleanup(shutil.rmtree,self.state.root,True)
                with isolated.adapters(self.smoke,self.state):
                    self.create()
                    with self.assertRaises(FakeSmokeError):self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
                self.assertFalse(self.state.absence)

    def test_regular_owned_bookkeeping_after_delete_allows_complete_absence_proof(self):
        self.mode='bookkeeping'
        with isolated.adapters(self.smoke,self.state):
            self.create();self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
        self.assertTrue(self.state.absence)

    def test_plist_hardlinks_and_changes_during_read_are_rejected(self):
        path=self.base/'metadata.plist';path.write_bytes(plistlib.dumps({'owner':'fixture'}))
        link=self.base/'linked';os.link(path,link)
        with self.assertRaises(isolated.IsolationError):isolated.owned_plist(path,os.getuid())
        link.unlink();original_read=os.read
        def mutated(descriptor,count):
            value=original_read(descriptor,count);path.write_bytes(plistlib.dumps({'owner':'changed'}));return value
        with patch.object(isolated.os,'read',mutated),self.assertRaises(isolated.IsolationError):
            isolated.owned_plist(path,os.getuid())

    def test_recording_exit_and_both_original_images_and_video_are_required(self):
        proof,result,cleanup=successful_reports()
        for value in (None,False,1,-9):
            with self.subTest(value=value),self.assertRaises(isolated.IsolationError):
                isolated.verify_success(proof,dict(result,recording_return_code=value),cleanup)
        folder=self.output/'main-app-only/launch-smoke';folder.mkdir(parents=True)
        files=[folder/name for name in ('launch-1.png','launch-2.png','launch.mp4')]
        for path in files:path.write_bytes(b'fixture-only original media stand-in')
        isolated.verify_visual_files(self.output,os.getuid())
        for path in files:
            data=path.read_bytes();path.unlink()
            with self.subTest(missing=path.name),self.assertRaises(OSError):
                isolated.verify_visual_files(self.output,os.getuid())
            path.write_bytes(b'')
            with self.assertRaises(isolated.IsolationError):isolated.verify_visual_files(self.output,os.getuid())
            path.unlink();path.symlink_to(self.default/'untouched')
            with self.assertRaises(isolated.IsolationError):isolated.verify_visual_files(self.output,os.getuid())
            path.unlink();os.link(self.default/'untouched',path)
            with self.assertRaises(isolated.IsolationError):isolated.verify_visual_files(self.output,os.getuid())
            path.unlink();path.write_bytes(data)

    def test_local_proxy_and_command_restore_on_exception_without_global_mutation(self):
        original_command=self.smoke.command; original_module=self.smoke.subprocess; global_popen=subprocess.Popen
        with self.assertRaises(RuntimeError):
            with isolated.adapters(self.smoke,self.state):
                self.assertIs(subprocess.Popen,global_popen)
                self.assertIsNot(self.smoke.subprocess,original_module)
                raise RuntimeError('synthetic failure')
        self.assertIs(self.smoke.command,original_command);self.assertIs(self.smoke.subprocess,original_module)
        self.assertIs(subprocess.Popen,global_popen)
        with isolated.adapters(self.smoke,self.state):pass

    def test_closed_cleanup_contract_has_no_identifiers_or_paths(self):
        with isolated.adapters(self.smoke,self.state):
            self.create();self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
        text=json.dumps(self.state.proof())
        self.assertEqual(set(self.state.proof()),{'schema','owned_device_deleted','owned_uuid_absence_verified','exact_owned_device_only'})
        self.assertNotIn(IDENTIFIER,text);self.assertNotIn(str(self.state.root),text)

    def test_stock_pass_with_cleanup_failures_or_unverified_delete_is_rejected(self):
        proof,result,cleanup=successful_reports();isolated.verify_success(proof,result,cleanup)
        for key in ('owned_device_deleted','owned_uuid_absence_verified','exact_owned_device_only'):
            for value in (False,1,'true'):
                with self.subTest(key=key,value=value),self.assertRaises(isolated.IsolationError):
                    isolated.verify_success(proof,result,dict(cleanup,**{key:value}))
        result['cleanup_failures']=[{'code':'commandTimedOut','phase':'shutdownDevice'}]
        with self.assertRaises(isolated.IsolationError):isolated.verify_success(proof,result,cleanup)

    def test_success_requires_two_real_closed_private_launches_and_unchanged_source(self):
        proof,result,cleanup=successful_reports()
        variants=[]
        for field,value in [('passed',False),('main_code_unchanged',False),('source_ipa_unchanged',False),('source_simulator_ipa_sha256','0'*64),('private_control_only',False)]:
            variants.append((dict(proof,**{field:value}),result))
        variants.extend((proof,dict(result,launches=result['launches'][:n])) for n in (0,1))
        changed=copy.deepcopy(result);changed['launches'][1]['private_account_store_stable_after_restart']=False;variants.append((proof,changed))
        for p,r in variants:
            with self.assertRaises(isolated.IsolationError):isolated.verify_success(p,r,cleanup)

    def test_source_pins_are_exact_and_changed_helper_cannot_import(self):
        source=self.base/'source';directory=source/'tools/fork_baseline';directory.mkdir(parents=True)
        for name in isolated.PINS:(directory/name).write_text('raise AssertionError("must not execute")\n')
        with patch.object(isolated.subprocess,'check_output',side_effect=[(isolated.SOURCE+'\n').encode(),b'']):
            with self.assertRaises(isolated.IsolationError):
                with isolated.pinned_modules(source):pass
        self.assertEqual(isolated.PINS['launch_smoke.py'],'c9efc0fc8e15642c684abf9ff44ba81760ccaa64ed9c93ff8e60b6218f17b6ef')

    def run_fake_replay(self, mode=None, raise_from_helper=False):
        self.mode=mode
        ipa=self.base/'fixture.ipa';ipa.write_bytes(b'owned synthetic artifact, not an actual app')
        saved={}
        def replay(ipa_argument,output,startup_variant,private_control_only):
            self.assertEqual(startup_variant,'main-app-only');self.assertIs(private_control_only,True)
            proof,result,_=successful_reports()
            folder=output/'main-app-only/launch-smoke';folder.mkdir(parents=True)
            self.create()
            try:self.smoke.command(['xcrun','simctl','delete',IDENTIFIER],timeout=30,phase='deleteDevice')
            except FakeSmokeError:result['cleanup_failures']=[{'code':'commandTimedOut','phase':'deleteDevice'}]
            if not result['cleanup_failures']:result.pop('cleanup_failures')
            saved['result_bytes']=json.dumps(result,indent=2).encode()
            (folder/'result.json').write_bytes(saved['result_bytes'])
            for name in ('launch-1.png','launch-2.png','launch.mp4'):(folder/name).write_bytes(b'owned synthetic media fixture')
            if raise_from_helper:raise FakeSmokeError('commandTimedOut','installApplication')
            return proof
        fake=types.SimpleNamespace(replay=replay,prior_smoke_report=lambda value:value)
        @contextlib.contextmanager
        def modules(root):yield self.smoke,fake
        with patch.object(isolated.platform,'system',return_value='Darwin'),patch.object(isolated.platform,'machine',return_value='arm64'),\
             patch.object(isolated,'IPA_SHA256',hashlib.sha256(ipa.read_bytes()).hexdigest()),\
             patch.object(isolated,'pinned_modules',modules),patch.object(isolated,'OwnedDeviceSet',return_value=self.state):
            try:result=isolated.run(ipa,self.output,self.base)
            finally:
                self.assertEqual((self.state.output/'result.json').read_bytes(),saved['result_bytes'])
        return result

    def test_run_preserves_stock_success_bytes_and_writes_actual_cleanup_receipt(self):
        self.run_fake_replay()
        cleanup=json.loads((self.output/'cleanup.json').read_text())
        self.assertEqual(cleanup,successful_reports()[2])
        self.assertNotIn('cleanup_failures',json.loads((self.state.output/'result.json').read_text()))
        self.assertIs(self.smoke.subprocess,self.process_module)

    def test_run_raises_even_if_stock_claims_pass_after_cleanup_timeout(self):
        with self.assertRaises(isolated.IsolationError):self.run_fake_replay(mode='deleteTimeout')
        cleanup=json.loads((self.output/'cleanup.json').read_text())
        self.assertIs(cleanup['owned_device_deleted'],False)
        self.assertIs(cleanup['owned_uuid_absence_verified'],False)
        self.assertTrue((self.state.root/IDENTIFIER).is_dir())
        self.assertIs(self.smoke.subprocess,self.process_module)

    def test_run_restores_adapter_and_writes_receipt_when_stock_helper_raises(self):
        with self.assertRaises(FakeSmokeError):self.run_fake_replay(raise_from_helper=True)
        self.assertTrue((self.output/'cleanup.json').is_file())
        self.assertIs(self.smoke.subprocess,self.process_module)

    def test_raw_temporary_root_alias_rejects_before_helpers_and_canonical_root_succeeds(self):
        alias=self.base/'temporary-root-alias';alias.symlink_to(self.base,target_is_directory=True)
        ipa=self.base/'fixture.ipa';ipa.write_bytes(b'owned synthetic artifact, not an actual app')
        with patch.object(isolated.platform,'system',return_value='Darwin'),\
             patch.object(isolated.platform,'machine',return_value='arm64'),\
             patch.object(isolated,'IPA_SHA256',hashlib.sha256(ipa.read_bytes()).hexdigest()),\
             patch.object(isolated,'pinned_modules') as modules:
            with self.assertRaises(isolated.IsolationError):
                isolated.run(alias/'fixture.ipa',alias/'replay',self.base)
            modules.assert_not_called()
        self.assertEqual(alias.resolve(strict=True),self.base)
        self.assertFalse(self.output.exists())
        # Fresh-process canonical TMPDIR admission gives the owned fixture its
        # real root; this exercises the unchanged adapter's positive run path.
        self.run_fake_replay()
        self.assertEqual(json.loads((self.output/'cleanup.json').read_text()),successful_reports()[2])


if __name__=='__main__':unittest.main()
