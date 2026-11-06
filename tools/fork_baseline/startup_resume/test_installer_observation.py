# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import socket
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import installer_observation as m

ID = '12345678-1234-1234-1234-123456789ABC'
NAME = 'AYU_SMOKE_' + 'a' * 32


def frames(*stages):
    output=io.BytesIO();writer=m.FrameWriter(output.write);value=m.blank()
    for stage in stages:writer.emit(value,stage,complete=stage=='complete')
    return output.getvalue()


def sample(pid=123, command='simctl'):
    return (f'Process: {command} [{pid}]\nPath: /PRIVATE_SYNTHETIC_CANARY\nCall graph:\n'
            '  30 Thread_111 DispatchQueue_1: com.apple.main-thread (serial)\n'
            '  + 20 mach_msg2_trap (in libsystem_kernel.dylib) + 1 [0x123]\n'
            '  + 10 copyfile (in libcopyfile.dylib)\n'
            '  + 99 PRIVATE_SYNTHETIC_CANARY (in PrivateImage)\n'
            '  10 Thread_222 DispatchQueue_2: background (serial)\n'
            '  + 99 _dispatch_group_wait_slow (in libdispatch.dylib)\n'
            'Binary Images:\n'
            '  0x1 - 0x2 com.apple.CoreSimulator (1) <ABC> /PRIVATE_SYNTHETIC_CANARY\n'
            '  0x3 - 0x4 libcopyfile.dylib (1) <DEF> /usr/lib/libcopyfile.dylib\n').encode()


class ObservationTests(unittest.TestCase):
    def test_schema_accepts_empty_diagnostic_without_qualification(self):
        value = m.validate_observation(m.blank())
        self.assertIs(value['startup_verified'], False)
        self.assertIs(value['original_failure_preserved'], True)

    def test_every_unknown_object_key_is_rejected(self):
        paths = [(), ('identity',), ('tool',), ('sample',), ('sample', 'wait_counts'),
                 ('sample', 'image_family_present'), ('containers',), ('containers', 'app'),
                 ('containers', 'data'), ('host',)]
        for path in paths:
            value = m.blank(); target = value
            for item in path: target = target[item]
            target['private_canary'] = 'SYNTHETIC_PRIVATE'
            with self.subTest(path=path), self.assertRaises(ValueError): m.validate_observation(value)

    def test_exact_count_types_and_nonfinite_json_are_rejected(self):
        for bad in (False, 1.0, -1, 100001):
            value = m.blank(); value['sample']['wait_counts']['file_io'] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError): m.validate_observation(value)
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(ValueError): m.strict_json(data)

    def test_overrun_and_late_exit_are_honest_diagnostic_only(self):
        value = m.blank(); value.update(post_deadline_elapsed_ms=12001, budget_exceeded=True,
                                        post_observer_installer_exit_code=0)
        m.validate_observation(value)
        self.assertIs(value['startup_verified'], False)
        value['budget_exceeded'] = False
        with self.assertRaises(ValueError): m.validate_observation(value)

    def test_only_known_pid_parent_uid_and_command_are_accepted(self):
        self.assertEqual(m.process_identity(b'123 45 501 simctl\n', 123, 45, 501), 'simctl')
        for raw in (b'124 45 501 simctl\n', b'123 44 501 simctl\n', b'123 45 0 simctl\n',
                    b'123 45 501 bash\n', b'123 45 501 simctl\n124 45 501 simctl\n', b'not metadata'):
            with self.subTest(raw=raw): self.assertIsNone(m.process_identity(raw, 123, 45, 501))
        self.assertEqual(m.process_identity(b'123 45 501 bash\n',123,45,501,shell_wrapper_verified=True),
                         'verifiedShellWrapper')

    def test_sample_binds_header_pid_and_emits_only_fixed_main_thread_categories(self):
        value = m.parse_sample(sample(), 123, 'simctl')
        self.assertEqual(value['state'], 'parsed')
        self.assertEqual(value['wait_counts']['mach_message_wait'], 20)
        self.assertEqual(value['wait_counts']['copy_io'], 10)
        self.assertEqual(value['wait_counts']['dispatch_wait'], 0)
        self.assertTrue(value['image_family_present']['CoreSimulator'])
        self.assertNotIn('PRIVATE_SYNTHETIC_CANARY', json.dumps(value))
        self.assertEqual(m.parse_sample(sample(), 124, 'simctl')['state'], 'rejected')

    def test_sample_truncation_duplicate_graph_thread_and_header_fail_closed(self):
        for raw in (sample()[:50], sample()+b'Call graph:\n', sample().replace(b'Process: simctl [123]',b'Process: simctl [123]\nProcess: simctl [123]'),
                    sample().replace(b'Thread_111', b'Thread_BAD'), b'x'*m.MAX_BYTES):
            with self.subTest(raw=raw[:60]): self.assertEqual(m.parse_sample(raw,123,'simctl')['state'],'rejected')

    def test_shell_wrapper_sample_requires_same_verified_family(self):
        self.assertEqual(m.parse_sample(sample(command='bash'),123,'verifiedShellWrapper')['state'],'parsed')
        self.assertEqual(m.parse_sample(sample(command='bash'),123,'simctl')['state'],'rejected')
        value = m.blank(); value['identity'].update(state='verified',fixed_argv_context_verified=True,
            parent_and_uid_verified=True,command_family='verifiedShellWrapper')
        with self.assertRaises(ValueError): m.validate_observation(value)

    def test_minimal_child_environment_has_no_tokens(self):
        with mock.patch.dict(os.environ, {'GH_TOKEN':'PRIVATE','ACTIONS_RUNTIME_TOKEN':'PRIVATE','HOME':'/tmp','PATH':'/usr/bin'}, clear=True):
            self.assertEqual(m.clean_environment(), {'HOME':'/tmp','PATH':'/usr/bin'})

    def test_user_owned_fixed_path_does_not_authorize_shell_wrapper(self):
        info=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=501)
        with mock.patch.object(m,'command',return_value=('completed',(str(m.APPLE_SIMCTL)+'\n').encode())),\
             mock.patch.object(Path,'lstat',return_value=info),mock.patch.object(os,'getuid',return_value=501),\
             mock.patch.object(m.shutil,'which',return_value='/usr/bin/xcrun'),mock.patch.object(Path,'resolve',lambda self,**kwargs:self):
            result=m.tool_context()
        self.assertEqual(result['state'],'rejected');self.assertFalse(result['run_first_launch_wrapper_observed'])

    def test_shadowed_xcrun_does_not_bind_tool_observation_to_actual_installer(self):
        with mock.patch.object(m.shutil,'which',return_value='/private/shadow/xcrun'),\
             mock.patch.object(Path,'resolve',lambda self,**kwargs:self),mock.patch.object(m,'command') as command:
            result=m.tool_context()
        self.assertFalse(result['xcrun_lookup_is_usr_bin']);self.assertEqual(result['state'],'rejected')
        command.assert_not_called()

    def test_fixed_root_admin_applications_parent_allows_normal_platform_modes(self):
        for mode in (0o755,0o775):
            m.trusted_apple_ancestor(Path('/Applications'),SimpleNamespace(st_mode=stat.S_IFDIR|mode,st_uid=0,st_gid=80))

    def test_applications_parent_rejects_wrong_owner_group_world_write_and_links(self):
        for mode,uid,gid in ((0o775,501,80),(0o775,0,20),(0o777,0,80),(0o1775,0,80),(0o755,0,0)):
            with self.subTest(mode=mode,uid=uid,gid=gid),self.assertRaises(ValueError):
                m.trusted_apple_ancestor(Path('/Applications'),SimpleNamespace(st_mode=stat.S_IFDIR|mode,st_uid=uid,st_gid=gid))
        with self.assertRaises(ValueError):
            m.trusted_apple_ancestor(Path('/Applications'),SimpleNamespace(st_mode=stat.S_IFLNK|0o777,st_uid=0,st_gid=80))

    def test_admin_group_write_exception_does_not_extend_to_xcode_or_other_ancestors(self):
        for path in ('/Applications/Xcode_27.1.app','/Applications/Xcode_27.1.app/Contents','/OtherApplications'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                m.trusted_apple_ancestor(Path(path),SimpleNamespace(st_mode=stat.S_IFDIR|0o775,st_uid=0,st_gid=80))

    def test_worker_group_is_retired_before_any_leader_poll_or_wait(self):
        read_in,write_in=os.pipe();read_out,write_out=os.pipe()
        os.write(write_out,frames('sourceAppMetadata','complete'));os.close(write_out)
        process=mock.Mock(pid=123,stdin=os.fdopen(write_in,'wb',buffering=0),stdout=os.fdopen(read_out,'rb',buffering=0))
        process.poll.side_effect=AssertionError('Leader must remain unreaped')
        events=[]
        process.wait.side_effect=lambda **kwargs:events.append('wait') or -9
        try:
            with mock.patch.object(m.subprocess,'Popen',return_value=process) as spawn,\
                 mock.patch.object(m.os,'killpg',side_effect=lambda pid,sig:events.append(('kill',pid,sig))):
                state,data=m.worker_command(b'{"private":"request"}',.5)
            self.assertEqual(state,'payloadReceived');value=m.strict_json(data)
            self.assertEqual(value['worker_status'],'completeFrame');self.assertEqual(value['worker_frames_received'],2)
            self.assertTrue(value['own_group_signal_sent']);self.assertTrue(value['direct_worker_reaped'])
            self.assertEqual(events,[('kill',123,m.signal.SIGKILL),'wait'])
            process.poll.assert_not_called();self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        finally:os.close(read_in)

    def test_worker_does_not_reap_exited_leader_when_descendant_keeps_pipe_open(self):
        read_in,write_in=os.pipe();read_out,write_out=os.pipe()
        # The writing endpoint models an inherited descendant descriptor. No
        # payload/EOF arrives, so the owned group must still be retired first.
        process=mock.Mock(pid=123,stdin=os.fdopen(write_in,'wb',buffering=0),stdout=os.fdopen(read_out,'rb',buffering=0))
        process.poll.side_effect=AssertionError('Would lose unreaped identity')
        events=[];process.wait.side_effect=lambda **kwargs:events.append('wait') or 0
        try:
            with mock.patch.object(m.subprocess,'Popen',return_value=process),\
                 mock.patch.object(m.os,'killpg',side_effect=lambda pid,sig:events.append('kill')):
                state,data=m.worker_command(b'{}',.01)
            self.assertEqual(state,'timedOut');self.assertEqual(events,['kill','wait'])
            self.assertEqual(m.strict_json(data)['worker_status'],'timedOut')
            process.poll.assert_not_called()
        finally:os.close(read_in);os.close(write_out)

    def test_group_retirement_failure_never_delivers_observed_payload(self):
        read_in,write_in=os.pipe();read_out,write_out=os.pipe()
        os.write(write_out,b'{}\n');os.close(write_out)
        process=mock.Mock(pid=123,stdin=os.fdopen(write_in,'wb',buffering=0),stdout=os.fdopen(read_out,'rb',buffering=0))
        process.wait.side_effect=m.subprocess.TimeoutExpired('private',.25)
        try:
            with mock.patch.object(m.subprocess,'Popen',return_value=process),mock.patch.object(m.os,'killpg'):
                state,data=m.worker_command(b'{}',.5)
                self.assertEqual(state,'retirementUnverified');self.assertFalse(m.strict_json(data)['direct_worker_reaped'])
        finally:os.close(read_in)

    def test_request_pipe_is_nonblocking_and_handles_partial_delivery(self):
        read_end,write_end=os.pipe();stream=os.fdopen(write_end,'wb',buffering=0)
        try:
            with mock.patch.object(m.os,'write',side_effect=[BlockingIOError(),2,2]) as write:
                m.send_request(stream,b'1234',time.monotonic()+.5)
            self.assertFalse(os.get_blocking(stream.fileno()))
            self.assertEqual([call.args[1] for call in write.call_args_list],[b'1234',b'1234',b'34'])
        finally:stream.close();os.close(read_end)

    @unittest.skipUnless(hasattr(os,'fork') and hasattr(socket,'AF_UNIX'),'POSIX synthetic process fixture')
    def test_actual_exited_leader_and_inherited_pipe_descendant_are_retired(self):
        # The synthetic descendant keeps both a pipe and an AF_UNIX connection.
        # EOF proves actual retirement; no native app or process search is used.
        with tempfile.TemporaryDirectory(prefix='ayu-group-fixture-') as temporary:
            root=Path(temporary).resolve();address=root/'s';script=root/'worker.py'
            script.write_text('import os,socket,select\n'
                's=socket.socket(socket.AF_UNIX);s.connect('+repr(str(address))+')\n'
                'p=os.fork()\n'
                'if p:\n s.close();os._exit(0)\n'
                's.sendall(b"R")\nselect.select([s],[],[],8)\ns.close()\nos._exit(0)\n')
            server=socket.socket(socket.AF_UNIX);server.bind(str(address));server.listen(1);server.settimeout(4)
            ready,eof=threading.Event(),threading.Event();errors=[]
            def observe():
                try:
                    connection,_=server.accept()
                    with connection:
                        connection.settimeout(4)
                        if connection.recv(1)==b'R':ready.set()
                        if connection.recv(1)==b'':eof.set()
                except Exception as error:errors.append(type(error).__name__)
            thread=threading.Thread(target=observe);thread.start()
            try:
                with mock.patch.object(m,'SCRIPT',str(script)):
                    state,data=m.worker_command(b'{}',2)
                self.assertEqual(state,'timedOut');self.assertEqual(m.strict_json(data)['worker_status'],'timedOut')
                self.assertTrue(ready.wait(1),'Synthetic descendant did not start')
                self.assertTrue(eof.wait(1),'Owned diagnostic descendant survived group retirement')
                thread.join(1);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            finally:server.close();thread.join(5)

    def test_closed_progress_is_retained_and_does_not_stop_on_first_frame(self):
        reader=m.FrameReader();reader.feed(frames('sourceAppMetadata','ownedDeviceMetadata'))
        self.assertFalse(reader.complete);self.assertEqual(reader.count,2)
        self.assertEqual(reader.latest['last_progress_stage'],'ownedDeviceMetadata')
        self.assertEqual(reader.latest['worker_status'],'inProgress')

    def test_fragmented_progress_and_complete_are_validated_as_one_protocol(self):
        data=frames(*m.PROGRESS_STAGES[1:]);reader=m.FrameReader()
        for index in range(0,len(data),7):reader.feed(data[index:index+7])
        self.assertTrue(reader.complete);self.assertEqual(reader.count,8)
        self.assertEqual(reader.latest['last_progress_stage'],'complete')

    def test_later_unknown_private_frame_never_replaces_last_valid_progress(self):
        reader=m.FrameReader();reader.feed(frames('sourceAppMetadata'))
        bad=json.loads(frames('sourceAppMetadata').decode());bad['observation']['private_canary']='PRIVATE_SECRET'
        with self.assertRaises(ValueError):reader.feed(json.dumps(bad).encode()+b'\n')
        self.assertEqual(reader.count,1);self.assertNotIn('PRIVATE_SECRET',json.dumps(reader.latest))

    def test_frame_limit_stage_order_and_raw_trailing_payload_fail_closed(self):
        for data in (frames('sourceAppMetadata','sourceAppMetadata'),
                     frames('ownedDeviceMetadata'),frames('sourceAppMetadata','complete')+b'PRIVATE_CANARY'):
            reader=m.FrameReader()
            with self.subTest(data=data[:40]),self.assertRaises(ValueError):reader.feed(data)
            self.assertNotIn('PRIVATE_CANARY',json.dumps(reader.latest))
        reader=m.FrameReader()
        with self.assertRaises(ValueError):reader.feed(b'x'*(m.MAX_BYTES+1))

    def test_timeout_after_progress_returns_last_valid_stage_with_retirement_facts(self):
        read_in,write_in=os.pipe();read_out,write_out=os.pipe()
        os.write(write_out,frames('sourceAppMetadata','ownedDeviceMetadata'))
        process=mock.Mock(pid=123,stdin=os.fdopen(write_in,'wb',buffering=0),stdout=os.fdopen(read_out,'rb',buffering=0))
        process.poll.side_effect=AssertionError('Must not reap before group retirement');process.wait.return_value=-9
        try:
            with mock.patch.object(m.subprocess,'Popen',return_value=process),mock.patch.object(m.os,'killpg'):
                state,data=m.worker_command(b'{}',.01)
            value=m.strict_json(data);self.assertEqual(state,'timedOut')
            self.assertEqual(value['last_progress_stage'],'ownedDeviceMetadata')
            self.assertEqual(value['worker_frames_received'],2);self.assertEqual(value['state'],'partial')
            self.assertTrue(value['own_group_signal_sent']);self.assertTrue(value['direct_worker_reaped'])
        finally:os.close(read_in);os.close(write_out)


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve(); self.root.chmod(0o700)
        self.device = self.root / ID; self.device.mkdir(mode=0o700)
        self.write_plist(self.device/'device.plist', {'UDID':ID,'name':NAME,'deviceType':m.MODEL,'runtime':m.RUNTIME})
        info = self.root.stat()
        self.request = {'private_root':str(self.root),'identifier':ID,'device_name':NAME,
            'root_identity':[info.st_dev,info.st_ino],'source_device':info.st_dev,'executable_size':3}

    def tearDown(self): self.temp.cleanup()

    def write_plist(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(plistlib.dumps(value)); path.chmod(0o600)

    def container(self, role='app', *, name='00000000-0000-0000-0000-000000000001', bundle=m.BUNDLE):
        root = self.device/'data/Containers'/('Bundle' if role=='app' else 'Data')/'Application'/name
        root.mkdir(parents=True,mode=0o700)
        self.write_plist(root/'.com.apple.mobile_container_manager.metadata.plist', {'MCMMetadataIdentifier':bundle})
        if role=='app':
            app=root/'Telegram.app'; app.mkdir(mode=0o700)
            self.write_plist(app/'Info.plist', {'CFBundleIdentifier':bundle,'CFBundleExecutable':'Telegram','CFBundleVersion':'1001'})
            (app/'Telegram').write_bytes(b'abc'); (app/'Telegram').chmod(0o600)
        return root

    def get(self):
        with mock.patch.object(m, 'command', return_value=('unavailable',b'')):
            return m.snapshot(self.request)

    def test_complete_owned_metadata_and_volume_only_no_account_contents(self):
        app=self.container(); data=self.container('data')
        secret=data/'Documents/session'; secret.parent.mkdir(); secret.write_text('PRIVATE_SECRET')
        value=self.get()
        self.assertEqual(value['containers']['app']['state'],'unique')
        self.assertTrue(value['containers']['app']['executable_size_equal'])
        self.assertEqual(value['containers']['data']['state'],'unique')
        self.assertNotIn('PRIVATE_SECRET',json.dumps(value))
        self.assertFalse(value['containers']['installed_bytes_attested'])
        self.assertTrue(value['host']['same_volume'])

    def test_unknown_record_prevents_unique_or_absent(self):
        app=self.container(); unknown=app.parent/'00000000-0000-0000-0000-000000000002'; unknown.mkdir(mode=0o700)
        value=self.get()['containers']['app']
        self.assertEqual(value['state'],'unavailable'); self.assertEqual(value['unidentified_records'],1)

    def test_duplicate_bundle_is_ambiguous(self):
        self.container(); self.container(name='00000000-0000-0000-0000-000000000002')
        self.assertEqual(self.get()['containers']['app']['state'],'ambiguous')

    def test_symlink_metadata_and_writable_directory_never_count_as_installed(self):
        app=self.container(); metadata=app/'.com.apple.mobile_container_manager.metadata.plist'
        target=self.root/'target'; metadata.rename(target); metadata.symlink_to(target)
        self.assertEqual(self.get()['containers']['app']['state'],'unavailable')
        metadata.unlink(); target.rename(metadata); app.chmod(0o777)
        self.assertEqual(self.get()['containers']['app']['state'],'unavailable')

    def test_hardlinked_or_oversize_info_rejected(self):
        app=self.container(); info=app/'Telegram.app/Info.plist'; alias=self.root/'alias'; os.link(info,alias)
        self.assertEqual(self.get()['containers']['app']['state'],'unavailable')
        alias.unlink(); info.write_bytes(b'x'*(m.MAX_BYTES+1))
        self.assertEqual(self.get()['containers']['app']['state'],'unavailable')

    def test_foreign_bundle_not_inspected_and_missing_role_is_absent(self):
        app=self.container(bundle='org.synthetic.foreign')
        (app/'Telegram.app/Info.plist').unlink()
        value=self.get()
        self.assertEqual(value['containers']['app']['state'],'absent')
        self.assertEqual(value['containers']['data']['state'],'absent')

    def test_root_identity_or_uuid_mismatch_rejects_entire_snapshot(self):
        self.request['root_identity'][1]+=1
        self.assertEqual(self.get()['containers']['snapshot_state'],'unavailable')

    def test_snapshot_directory_entry_cap_is_fail_closed(self):
        app=self.container()
        for i in range(257): (app.parent/f'{i:08x}-1111-1111-1111-111111111111').mkdir(mode=0o700)
        self.assertEqual(self.get()['containers']['app']['state'],'unavailable')


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name).resolve()
        self.root=self.base/'device-set'; self.root.mkdir(mode=0o700)
        device=self.root/ID;device.mkdir(mode=0o700)
        (device/'device.plist').write_bytes(plistlib.dumps({'UDID':ID,'name':NAME,'deviceType':m.MODEL,'runtime':m.RUNTIME}))
        self.app=self.base/'ayu-launch-smoke-test/Telegram.app'; self.app.mkdir(mode=0o700,parents=True); (self.app/'Telegram').write_bytes(b'abc')
        self.process=mock.Mock(pid=123,returncode=None); self.process.poll.return_value=None
        self.kwargs={'mapped_arguments':['xcrun','simctl','--set',str(self.root),'install',ID,str(self.app)],
            'private_root':self.root,'root_identity':(self.root.stat().st_dev,self.root.stat().st_ino),
            'identifier':ID,'device_name':NAME,'command_started':time.monotonic()-121}

    def tearDown(self): self.temp.cleanup()

    def test_capture_uses_only_fixed_worker_not_installer_signal(self):
        with mock.patch.object(m,'worker_command',return_value=('payloadReceived',json.dumps(m.blank()).encode())) as call:
            result=m.capture(self.process,**self.kwargs)
        self.assertEqual(len(call.call_args_list),1)
        request=json.loads(call.call_args.args[0]);self.assertEqual(request['pid'],123)
        self.assertLessEqual(len(call.call_args.args[0]),4096)
        self.process.kill.assert_not_called(); self.assertFalse(result['startup_verified'])

    def test_predeadline_dead_child_wrong_argv_and_exception_never_spawn(self):
        for change in ({'command_started':time.monotonic()}, {'timeout_seconds':121},
                       {'mapped_arguments':['xcrun','simctl','install',ID,str(self.app)]}):
            with mock.patch.object(m,'worker_command') as call:
                result=m.capture(self.process,**{**self.kwargs,**change})
                call.assert_not_called(); self.assertEqual(result['state'],'unavailable')
        self.process.poll.return_value=0
        with mock.patch.object(m,'worker_command') as call:
            m.capture(self.process,**self.kwargs); call.assert_not_called()

    def test_worker_bad_schema_or_exception_does_not_escape_or_publish_raw_output(self):
        for output in (('payloadReceived',b'{"private":"CANARY"}'), ('timedOut',b'PRIVATE_CANARY')):
            with mock.patch.object(m,'worker_command',return_value=output): value=m.capture(self.process,**self.kwargs)
            self.assertNotIn('CANARY',json.dumps(value)); self.assertEqual(value['state'],'unavailable')
        with mock.patch.object(m,'worker_command',side_effect=RuntimeError('PRIVATE_CANARY')):
            self.assertEqual(m.capture(self.process,**self.kwargs)['state'],'unavailable')

    def test_late_installer_exit_zero_is_recorded_without_success(self):
        self.process.poll.side_effect=[None,0,0]; self.process.returncode=0
        with mock.patch.object(m,'worker_command',return_value=('payloadReceived',json.dumps(m.blank()).encode())):
            result=m.capture(self.process,**self.kwargs)
        self.assertEqual(result['state'],'childExited'); self.assertEqual(result['post_observer_installer_exit_code'],0)
        self.assertFalse(result['startup_verified'])

    def test_late_parent_liveness_failure_does_not_discard_valid_progress(self):
        snapshot=m.blank();snapshot.update(state='partial',worker_status='timedOut',
            last_progress_stage='sourceAppMetadata',worker_frames_received=1)
        self.process.poll.side_effect=[None,RuntimeError('PRIVATE_POLL_ERROR'),None]
        with mock.patch.object(m,'worker_command',return_value=('timedOut',json.dumps(snapshot).encode())):
            value=m.capture(self.process,**self.kwargs)
        self.assertEqual(value['last_progress_stage'],'sourceAppMetadata')
        self.assertEqual(value['worker_frames_received'],1);self.assertEqual(value['worker_status'],'timedOut')
        self.assertNotIn('PRIVATE_POLL_ERROR',json.dumps(value));self.assertFalse(value['startup_verified'])

    def test_parent_capture_performs_no_path_io_or_recursive_temporary_cleanup(self):
        with mock.patch.object(Path,'resolve',side_effect=AssertionError('Parent FS resolution')) as resolve,\
             mock.patch.object(Path,'lstat',side_effect=AssertionError('Parent FS metadata')) as lstat,\
             mock.patch.object(m.tempfile,'TemporaryDirectory',side_effect=AssertionError('Parent recursive cleanup')) as temporary,\
             mock.patch.object(m,'worker_command',return_value=('payloadReceived',json.dumps(m.blank()).encode())) as worker:
            result=m.capture(self.process,**self.kwargs)
        resolve.assert_not_called();lstat.assert_not_called();temporary.assert_not_called();worker.assert_called_once()
        self.assertEqual(result['state'],'unavailable')
        self.assertTrue(result['original_failure_preserved'])

    def test_replaced_sample_inode_cannot_be_parsed(self):
        private=self.base/'observe';private.mkdir(mode=0o700)
        request={'pid':123,'parent':45,'uid':os.getuid(),'observation_root':str(private),'request_path':str(private/'request'),
                 'private_root':str(self.root),'identifier':ID,'device_name':NAME,
                 'root_identity':list(self.kwargs['root_identity'])}
        def command(args,*a,**kw):
            if args[0]=='/bin/ps':return 'completed',f'123 45 {os.getuid()} simctl\n'.encode()
            if args[:3]==[m.sys.executable,'-c',m.SAMPLE_EXEC]:
                path=Path(args[-1]);replacement=private/'replacement';replacement.write_bytes(sample());replacement.chmod(0o600)
                replacement.replace(path);return 'completed',b''
            return 'unavailable',b''
        with mock.patch.object(m,'tool_context',return_value=m.blank()['tool']),mock.patch.object(m,'command',side_effect=command):
            result=m.worker_inner(request)
        self.assertEqual(result['sample']['state'],'rejected');self.assertFalse((private/'sample.txt').exists())

    def test_actual_module_worker_protocol_completes_without_simulator_or_sampling(self):
        # Real SCRIPT/transport/metadata traversal; this synthetic child has the
        # wrong command identity, so no sample or native application can run.
        child=m.subprocess.Popen([m.sys.executable,'-c','import time;time.sleep(30)'],
            stdin=m.subprocess.DEVNULL,stdout=m.subprocess.DEVNULL,stderr=m.subprocess.DEVNULL,
            env=m.clean_environment())
        request={'pid':child.pid,'parent':os.getpid(),'uid':os.getuid(),'private_root':str(self.root),
                 'identifier':ID,'device_name':NAME,'root_identity':list(self.kwargs['root_identity']),
                 'app_path':str(self.app)}
        try:
            state,raw=m.worker_command(json.dumps(request).encode(),10)
            value=m.validate_observation(m.strict_json(raw))
            self.assertEqual(state,'payloadReceived')
            self.assertEqual(value['worker_status'],'completeFrame')
            self.assertEqual(value['last_progress_stage'],'complete')
            self.assertGreaterEqual(value['worker_frames_received'],6)
            self.assertLessEqual(value['worker_frames_received'],8)
            self.assertEqual(value['identity']['state'],'rejected')
            self.assertEqual(value['sample']['state'],'notAttempted')
            self.assertTrue(value['direct_worker_reaped'])
            self.assertFalse(value['startup_verified']);self.assertIsNone(child.poll())
            for private in (str(self.base),ID,NAME,'"pid"','"uid"'):
                self.assertNotIn(private,raw.decode())
        finally:
            child.terminate();child.wait(timeout=5)


if __name__ == '__main__': unittest.main()
