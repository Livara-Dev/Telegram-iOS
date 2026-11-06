# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Ownership and privacy boundaries for the bounded live bootstrap diagnostic."""
import json
import os
from pathlib import Path
import resource
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap_sample as bootstrap


PID = 43120
UID = os.getuid()
IDENTIFIER = '11111111-2222-3333-4444-555555555555'
BUNDLE = 'org.livara.ayugram.tests.native-security.projection'
SECRET = 'private-bootstrap-fixture-secret'
XCODEBUILD = '/Applications/Xcode_27.1.app/Contents/Developer/usr/bin/xcodebuild'
PS = ['/bin/ps', '-p', str(PID), '-o', 'pid=,ppid=,pgid=,uid=,ucomm=']
CATEGORIES = {'mach_message_wait', 'xpc_sync_wait', 'dispatch_wait', 'ulock_wait'}
SAMPLE = b'''Analysis of sampling xcodebuild (pid 43120) every 10 milliseconds
Process: xcodebuild [43120]
Path: /private/bootstrap-fixture/xcodebuild
Call graph:
  30 Thread_123   DispatchQueue_1: com.apple.main-thread  (serial)
    + 10 mach_msg2_trap (in libsystem_kernel.dylib) + 8 [0xabcdef]
    + 10 _xpc_connection_send_message_with_reply_sync (in libxpc.dylib) + 4
    + 10 _dispatch_semaphore_wait_slow (in libdispatch.dylib) + 4
    + 10 __ulock_wait2 (in libsystem_kernel.dylib) + 4
Binary Images:
private-bootstrap-fixture-secret
'''


def sample_frames(frames, other_threads=''):
    return ('Call graph:\n' + other_threads +
            '  30 Thread_123   DispatchQueue_1: com.apple.main-thread  (serial)\n' +
            frames + '\nBinary Images:\n/private/' + SECRET + '\n').encode()


def status(data=b'', state='completed'):
    return {'status': state, 'exit_code': 0 if state == 'completed' else None,
            'timeout': state == 'timedOut', 'output_limit': state == 'outputLimit',
            'stdout_bytes': len(data), 'stderr_bytes': len(SECRET), 'stderr': SECRET,
            'private_detail': '/private/bootstrap-fixture/' + SECRET}


def processes(owned='xcodebuild', owned_uid=UID, owned_group=PID, owned_pid=PID):
    return ('%d 40000 %d %d %s\n' % (owned_pid, owned_group, owned_uid, owned)).encode()


class BootstrapSampleTests(unittest.TestCase):
    def assert_private(self, value):
        text = json.dumps(value, sort_keys=True)
        for forbidden in (SECRET, IDENTIFIER, str(PID), '/private/', XCODEBUILD,
                          'mach_msg2_trap', 'private_detail', 'stderr"',
                          'private-argument-canary', 'unrelated-private-canary'):
            self.assertNotIn(forbidden, text)
        self.assertLessEqual(len(text.encode()), 16 * 1024)

    def run_capture(self, ps=None, sample=SAMPLE, sample_state='completed',
                    sample_file='regular', container_state='completed', alive=lambda: True):
        calls, sample_paths = [], []

        def command(arguments, secrets, timeout=20):
            self.assertEqual(tuple(secrets), (SECRET,))
            calls.append((list(arguments), timeout))
            if arguments == PS:
                data = processes() if ps is None else ps
                return status(data), data
            if arguments == ['xcrun', 'simctl', 'get_app_container', IDENTIFIER, BUNDLE, 'app']:
                self.assertEqual(timeout, 3)
                data = b'/private/bootstrap-fixture/ProjectionTestHost.app\n'
                return status(data, container_state), data
            self.assertEqual(arguments[:3], [sys.executable, '-c', bootstrap.SAMPLE_EXEC_WRAPPER])
            self.assertEqual(arguments[3], str(PID))
            self.assertEqual(timeout, 10)
            path = Path(arguments[4]); sample_paths.append(path)
            self.assertTrue(path.is_absolute())
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            if sample_file == 'regular':
                path.write_bytes(sample)
                path.chmod(0o600)
            elif sample_file == 'directory':
                if path.exists(): path.unlink()
                path.mkdir()
            elif sample_file == 'symlink':
                if path.exists(): path.unlink()
                target = path.parent / 'outside-sample-fixture'
                target.write_bytes(SAMPLE)
                path.symlink_to(target)
            elif sample_file == 'hardlink':
                if path.exists(): path.unlink()
                target = path.parent / 'shared-sample-fixture'
                target.write_bytes(SAMPLE); target.chmod(0o600)
                os.link(target, path)
            elif sample_file == 'public':
                path.write_bytes(sample); path.chmod(0o644)
            elif sample_file == 'missing':
                path.unlink(missing_ok=True)
            else:
                raise AssertionError('Unknown sample fixture')
            # A plausible stdout trace must never substitute for the private file.
            return status(SAMPLE, sample_state), SAMPLE

        with patch.object(bootstrap.os, 'getuid', return_value=UID), patch.object(bootstrap, 'command', side_effect=command):
            result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], alive)
        for path in sample_paths:
            self.assertFalse(path.exists(), 'private raw sample was not removed')
            self.assertFalse(path.parent.exists(), 'private sample workspace was not removed')
        return result, calls

    def test_live_owned_command_is_verified_before_container_lookup_and_private_sampling(self):
        result, calls = self.run_capture()
        self.assertTrue(result['diagnostic_only'])
        self.assertEqual([call[1] for call in calls], [3, 3, 10])
        self.assertEqual(calls[0][0], PS)
        self.assertEqual(calls[1][0], ['xcrun', 'simctl', 'get_app_container', IDENTIFIER, BUNDLE, 'app'])
        self.assertTrue(result['process_snapshot']['owned_xcodebuild_verified'])
        self.assertEqual(result['installed_host']['state'], 'available')
        self.assertEqual(result['sample']['state'], 'observed')
        selected = result['sample']['selection']
        self.assertEqual(selected['state'], 'parsed')
        self.assertLessEqual(set(selected['wait_categories']), CATEGORIES)
        self.assertTrue(all(selected['wait_categories'].get(name, 0) > 0 for name in CATEGORIES))
        self.assert_private(result)

    def test_only_known_child_is_queried_and_other_roles_remain_explicitly_unmeasured(self):
        result, calls = self.run_capture()
        roles = result['process_snapshot']['roles']
        self.assertEqual(set(roles), {'xcodebuild', 'ProjectionTestHost', 'testmanagerd', 'CoreSimulatorService'})
        for role in ('ProjectionTestHost', 'testmanagerd', 'CoreSimulatorService'):
            self.assertEqual(roles[role], {'state': 'notMeasured', 'observed': None,
                                         'shared_owned_group': None, 'parent_is_owned_child': None})
        self.assertEqual(roles['xcodebuild'], {'state': 'observed', 'observed': True,
                        'shared_owned_group': True, 'parent_is_owned_child': None})
        self.assertEqual(calls[0][0], ['/bin/ps', '-p', str(PID), '-o', 'pid=,ppid=,pgid=,uid=,ucomm='])
        self.assertFalse(any('-A' in arguments or '-e' in arguments for arguments, _ in calls))
        self.assert_private(result)

    def test_pid_group_identifier_and_boolean_inputs_fail_closed_before_any_command(self):
        for pid, group, identifier in [(0, 0, IDENTIFIER), (-1, -1, IDENTIFIER), (True, True, IDENTIFIER),
                                       (PID, PID + 1, IDENTIFIER), (str(PID), PID, IDENTIFIER),
                                       (PID, PID, 'private-invalid-identifier'), (PID, PID, None)]:
            with self.subTest(pid=pid, group=group, identifier=identifier), patch.object(bootstrap, 'command') as command:
                result = bootstrap.capture(pid, group, identifier, [SECRET], lambda: True)
                command.assert_not_called()
                self.assertNotEqual(result['sample']['state'], 'observed')
                self.assert_private(result)

    def test_dead_owned_child_never_runs_process_inventory_or_sampler(self):
        with patch.object(bootstrap, 'command') as command:
            result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], lambda: False)
        command.assert_not_called()
        self.assertEqual(result['sample']['state'], 'notAttempted')
        self.assert_private(result)

    def test_unknown_or_raising_liveness_callback_is_unavailable_without_commands(self):
        def unavailable():
            raise OSError('/private/' + SECRET)
        for alive in (lambda: None, unavailable):
            with self.subTest(alive=alive), patch.object(bootstrap, 'command') as command:
                result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], alive)
            command.assert_not_called()
            self.assertEqual(result['state'], 'unavailable')
            self.assertEqual(result['sample']['state'], 'notAttempted')
            self.assert_private(result)

    def test_wrong_pid_uid_group_kernel_name_or_extra_rows_never_authorizes_sampling(self):
        inventories = [b'', processes(owned_uid=UID + 1), processes(owned_group=PID + 1),
                       processes(owned_pid=PID + 1), processes(owned='not-xcodebuild'),
                       processes(owned='/usr/bin/xcodebuild'), processes(owned=XCODEBUILD),
                       processes(owned='xcodebuild private-argument-canary'),
                       processes() + processes(), processes() + b'\n',
                       processes() + ('%d 1 %d %d testmanagerd\n' % (PID + 1, PID, UID)).encode(),
                       b'private invalid process inventory\n']
        for ps in inventories:
            with self.subTest(ps=ps):
                result, calls = self.run_capture(ps=ps)
                self.assertEqual(len(calls), 1)
                self.assertFalse(result['process_snapshot']['owned_xcodebuild_verified'])
                for role in result['process_snapshot']['roles'].values():
                    self.assertEqual(role['state'], 'notMeasured'); self.assertIsNone(role['observed'])
                    self.assertIsNone(role['shared_owned_group']); self.assertIsNone(role['parent_is_owned_child'])
                self.assertEqual(result['sample']['state'], 'notAttempted')
                self.assert_private(result)

    def test_child_exit_after_inventory_or_container_lookup_prevents_the_next_probe(self):
        for stop_after in (1, 2):
            with self.subTest(stop_after=stop_after):
                count = 0
                def alive():
                    nonlocal count
                    count += 1
                    return count <= stop_after
                result, calls = self.run_capture(alive=alive)
                self.assertEqual(len(calls), stop_after)
                self.assertEqual(result['sample']['state'], 'notAttempted')
                self.assert_private(result)

    def test_inventory_failure_output_cap_and_unavailable_command_do_not_authorize_sample(self):
        for state in ('timedOut', 'outputLimit', 'commandFailed', 'unavailable'):
            with self.subTest(state=state), patch.object(bootstrap.os, 'getuid', return_value=UID), patch.object(bootstrap, 'command', return_value=(status(processes(), state), processes())) as command:
                result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], lambda: True)
                self.assertEqual(command.call_count, 1)
                self.assertFalse(result['process_snapshot']['owned_xcodebuild_verified'])
                self.assertTrue(all(role['observed'] is None for role in result['process_snapshot']['roles'].values()))
                self.assertEqual(result['sample']['state'], 'notAttempted')
                self.assert_private(result)

    def test_owned_process_byte_numeric_and_exact_row_bounds_fail_closed_before_further_commands(self):
        crowded = processes() + b'1 1 1 0 unrelated-private-canary\n' * 100
        fixtures = [processes() + b'x' * 65537, crowded, processes(owned_pid=2**31),
                    processes(owned_group=2**31), processes(owned_uid=2**32),
                    processes().replace(b'40000', str(2**31).encode()),
                    processes().replace(b'40000', b'-1'), processes().replace(b'40000', b'00000000000')]
        for ps in fixtures:
            with self.subTest(size=len(ps)):
                result, calls = self.run_capture(ps=ps)
                self.assertEqual(len(calls), 1)
                self.assertFalse(result['process_snapshot']['owned_xcodebuild_verified'])
                self.assertEqual(result['sample']['state'], 'notAttempted')
                self.assert_private(result)

    def test_remaining_deadline_clips_container_budget_and_prevents_a_late_sampler(self):
        calls = []
        def command(arguments, secrets, timeout=20):
            calls.append((arguments, timeout))
            if arguments == PS: return status(processes()), processes()
            self.assertEqual(arguments, ['xcrun', 'simctl', 'get_app_container', IDENTIFIER, BUNDLE, 'app'])
            return status(), b''
        with patch.object(bootstrap.os, 'getuid', return_value=UID), patch.object(bootstrap.time, 'monotonic', side_effect=[0, 0, 14, 17]), patch.object(bootstrap, 'command', side_effect=command):
            result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], lambda: True)
        self.assertEqual([timeout for _, timeout in calls], [3, 2])
        self.assertEqual(result['sample']['state'], 'unavailable')
        self.assertEqual(result['sample']['command']['status'], 'timedOut')
        self.assert_private(result)

    def test_sample_wrapper_limits_file_size_before_execing_only_the_fixed_sampler(self):
        private = '/private/bootstrap-fixture/sample.txt'
        with patch.object(resource, 'setrlimit') as limit, patch.object(os, 'execv') as execute, patch.object(sys, 'argv', ['bootstrap-wrapper', str(PID), private]):
            exec(bootstrap.SAMPLE_EXEC_WRAPPER, {})
        limit.assert_called_once_with(resource.RLIMIT_FSIZE, (65536, 65536))
        execute.assert_called_once_with('/usr/bin/sample', ['/usr/bin/sample', str(PID), '3', '10', '-file', private])

    def test_container_lookup_failure_is_not_a_reason_to_skip_owned_child_sample(self):
        result, calls = self.run_capture(container_state='timedOut')
        self.assertEqual(len(calls), 3)
        self.assertEqual(result['installed_host']['state'], 'unavailable')
        self.assertEqual(result['sample']['state'], 'observed')
        self.assert_private(result)

    def test_sampler_failure_cannot_publish_valid_stdout_or_private_trace_categories(self):
        for state in ('timedOut', 'outputLimit', 'commandFailed', 'unavailable'):
            with self.subTest(state=state):
                result, _ = self.run_capture(sample_state=state)
                self.assertEqual(result['sample']['state'], 'unavailable')
                self.assertNotEqual(result['sample']['selection']['state'], 'parsed')
                self.assert_private(result)

    def test_private_sample_must_be_new_regular_nonempty_and_strictly_below_cap(self):
        for mode, data in [('missing', SAMPLE), ('symlink', SAMPLE), ('hardlink', SAMPLE), ('public', SAMPLE),
                           ('directory', SAMPLE), ('regular', b''),
                           ('regular', SAMPLE + b'x' * (64 * 1024 - len(SAMPLE))),
                           ('regular', SAMPLE + b'x' * (64 * 1024 + 1 - len(SAMPLE)))]:
            with self.subTest(mode=mode, size=len(data)):
                result, _ = self.run_capture(sample_file=mode, sample=data)
                self.assertEqual(result['sample']['state'], 'unavailable')
                self.assertNotEqual(result['sample']['selection']['state'], 'parsed')
                self.assert_private(result)

    def test_sample_parser_keeps_only_whitelisted_literal_categories(self):
        symbols = {'mach_message_wait': ('libsystem_kernel.dylib', ('mach_msg_trap', 'mach_msg2_trap')),
                   'xpc_sync_wait': ('libxpc.dylib', ('_xpc_connection_send_message_with_reply_sync', 'xpc_connection_send_message_with_reply_sync')),
                   'dispatch_wait': ('libdispatch.dylib', ('_dispatch_semaphore_wait_slow', '_dispatch_group_wait_slow', '_dispatch_sync_f_slow')),
                   'ulock_wait': ('libsystem_kernel.dylib', ('__ulock_wait', '__ulock_wait2'))}
        for category, (module, names) in symbols.items():
            for name in names:
                with self.subTest(symbol=name):
                    data = sample_frames('    + 10 ' + name + '  (in ' + module + ') + 8 [0xabcdef]')
                    result = bootstrap.parse_sample(data, [SECRET])
                    self.assertEqual(result['state'], 'parsed')
                    self.assertTrue(result['main_thread_observed'])
                    self.assertFalse(result['complete_stacks_verified'])
                    self.assertEqual(result['wait_categories'].get(category, 0), 10)
                    self.assertLessEqual(set(result['wait_categories']), CATEGORIES)
                    self.assertTrue(all(type(count) is int and count >= 0 for count in result['wait_categories'].values()))
                    self.assert_private(result)

    def test_sample_parser_does_not_promote_unknown_or_substring_symbols_to_wait_evidence(self):
        data = sample_frames('    + 10 private_stack_function (in private.dylib)\n'
                             '    + 10 not_mach_msg_trap (in libsystem_kernel.dylib)\n'
                             '    + 10 mach_msg_trap_private (in libsystem_kernel.dylib)\n'
                             '    + 10 _dispatch_semaphore_wait_slow_extra (in libdispatch.dylib)\n'
                             '    + 10 __ulock_wait2_private (in libsystem_kernel.dylib)')
        result = bootstrap.parse_sample(data, [SECRET])
        self.assertLessEqual(set(result['wait_categories']), CATEGORIES)
        self.assertFalse(any(result['wait_categories'].values()))
        self.assert_private(result)

    def test_parser_requires_one_main_thread_and_never_counts_other_threads_or_module_lookalikes(self):
        main = '    + 4 mach_msg2_trap  (in libsystem_kernel.dylib) + 8 [0x1234]'
        other = ('  90 Thread_456\n'
                 '    + 90 _dispatch_group_wait_slow  (in libdispatch.dylib) + 8 [0x1234]\n')
        result = bootstrap.parse_sample(sample_frames(main, other), [SECRET])
        self.assertEqual(result['state'], 'parsed')
        self.assertEqual(result['wait_categories']['mach_message_wait'], 4)
        self.assertEqual(result['wait_categories']['dispatch_wait'], 0)
        wrong_module = bootstrap.parse_sample(sample_frames('    + 30 mach_msg2_trap  (in private-canary.dylib) + 8'), [SECRET])
        self.assertFalse(any(wrong_module['wait_categories'].values()))
        for data in [main.encode(), b'Call graph:\n' + main.encode() + b'\nBinary Images:\n',
                     sample_frames(main).replace(b'Binary Images:', b'Private Images:'),
                     sample_frames(main + '\n  30 Thread_789   DispatchQueue_1: com.apple.main-thread  (serial)\n' + main),
                     sample_frames(main + '\n  malformed Thread_bad\n'
                                   '    + 90 _dispatch_group_wait_slow  (in libdispatch.dylib) + 8')]:
            with self.subTest(data=data):
                result = bootstrap.parse_sample(data, [SECRET])
                self.assertEqual(result['state'], 'invalidPayload')
                self.assertFalse(any(result['wait_categories'].values()))
                self.assert_private(result)

    def test_parser_caps_weighted_counts_without_retaining_frame_text(self):
        data = sample_frames('    + 90000 mach_msg2_trap  (in libsystem_kernel.dylib) + 8\n'
                             '    + 90000 mach_msg_trap  (in libsystem_kernel.dylib) + 8')
        result = bootstrap.parse_sample(data, [SECRET])
        self.assertEqual(result['state'], 'parsed')
        self.assertEqual(result['wait_categories']['mach_message_wait'], 100000)
        self.assert_private(result)

    def test_parser_input_caps_and_command_exceptions_are_closed_diagnostics(self):
        result = bootstrap.parse_sample(SAMPLE + b'x' * (64 * 1024 + 1), [SECRET])
        self.assertNotEqual(result['state'], 'parsed')
        self.assert_private(result)
        with patch.object(bootstrap, 'command', side_effect=OSError('/private/' + SECRET)):
            result = bootstrap.capture(PID, PID, IDENTIFIER, [SECRET], lambda: True)
        self.assertNotEqual(result['sample']['state'], 'observed')
        self.assert_private(result)


if __name__ == '__main__':
    unittest.main()
