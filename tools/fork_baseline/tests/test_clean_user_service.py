# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable, injected tests; never execute a native service command."""
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clean_user_service as draft


class CleanUserServiceTests(unittest.TestCase):
    TARGET = 'user/501/com.apple.CoreSimulator.CoreSimulatorService'
    BEFORE = 21001
    AFTER = 21002
    SECRET = 'clean-draft-private-canary'
    PRIVATE_PATH = '/private/clean-draft-private-path/service.xpc'
    ENV = {
        'GITHUB_ACTIONS': 'true',
        'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_REPOSITORY': 'Livara-Dev/Telegram-iOS',
        'RUNNER_OS': 'macOS',
        'RUNNER_ENVIRONMENT': 'github-hosted',
        'AYU_CLEAN_RUNNER_ENVIRONMENT': 'github-hosted',
        'AYU_CLEAN_SESSION': 'exclusive-cached-artifacts',
        'GITHUB_JOB': 'native-api-probe',
        'AYU_CLEAN_JOB': 'native-api-probe',
        'AYU_CLEAN_SOURCE': 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9',
        'GITHUB_RUN_ID': '42190000000',
    }
    STATUS_FIELDS = {'status', 'exit_code', 'timeout', 'output_limit', 'stdout_bytes', 'stderr_bytes'}
    REPORT_FIELDS = {
        'schema', 'diagnostic_only', 'application_recompiled', 'application_startup_verified',
        'native_test_cases_verified', 'release_qualified', 'physical_device_verified',
        'shared_user_service_scope', 'default_device_set_erased', 'service_label', 'runtime',
        'passed', 'failure', 'restart_attempts', 'registered_service_verified',
        'service_restart_confirmed', 'pid_changed', 'post_restart_runtime_available', 'commands',
    }

    @classmethod
    def service(cls, pid=None, target=None):
        return ((target or cls.TARGET) + ' = {\n'
                '\tpath = ' + cls.PRIVATE_PATH + '\n'
                '\tstate = running\n'
                '\tenvironment = {\n\t\tPRIVATE_SECRET => ' + cls.SECRET + '\n\t}\n'
                '\tpid = ' + str(cls.BEFORE if pid is None else pid) + '\n}\n').encode()

    @staticmethod
    def runtimes(**changes):
        return json.dumps({'runtimes': [{'identifier': draft.RUNTIME, 'version': '27.0',
                                        'isAvailable': True, **changes}]}).encode()

    @classmethod
    def reply(cls, data=b'', status='completed', code=None):
        return ({'status': status, 'exit_code': (0 if status == 'completed' else 7) if code is None else code,
                 'timeout': status == 'timedOut', 'output_limit': status == 'outputLimit',
                 'stdout_bytes': len(data), 'stderr_bytes': 20,
                 'stderr': cls.SECRET + ' ' + cls.PRIVATE_PATH,
                 'arbitrary_private_field': cls.SECRET}, data)

    def runner(self, overrides=None):
        overrides = overrides or {}
        replies = [self.reply(b'Xcode 27.1\nBuild version 27B5045c\n'), self.reply(self.runtimes()),
                   self.reply(self.service()), self.reply(str(self.AFTER).encode() + b'\n'),
                   self.reply(self.service(self.AFTER)), self.reply(self.runtimes())]
        expected = [(['xcodebuild', '-version'], 10), (draft.RUNTIME_COMMAND, 10),
                    (['/bin/launchctl', 'print', self.TARGET], 5),
                    (['/bin/launchctl', 'kickstart', '-kp', self.TARGET], 10),
                    (['/bin/launchctl', 'print', self.TARGET], 5), (draft.RUNTIME_COMMAND, 10)]
        calls = []

        def command(arguments, secrets, timeout):
            index = len(calls)
            self.assertLess(index, len(expected), 'No command retry or fallback is permitted')
            self.assertEqual((arguments, timeout), expected[index])
            self.assertTrue(all(isinstance(value, str) for value in secrets))
            calls.append((list(arguments), timeout))
            value = overrides.get(index, replies[index])
            if isinstance(value, Exception):
                raise value
            return value
        return Mock(side_effect=command), calls

    def invoke(self, output, command, env=None, **host):
        return draft.refresh(output, env=dict(self.ENV if env is None else env), command=command,
                             system=Mock(return_value=host.get('system', 'Darwin')),
                             machine=Mock(return_value=host.get('machine', 'arm64')),
                             uid=Mock(return_value=host.get('uid', 501)))

    def read_report(self, output, report):
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        text = output.read_text()
        self.assertEqual(json.loads(text), report)
        self.assertEqual(set(report), self.REPORT_FIELDS)
        self.assertEqual(report['restart_attempts'] in (0, 1), True)
        for private in (self.SECRET, self.PRIVATE_PATH, self.TARGET, str(self.BEFORE), str(self.AFTER)):
            self.assertNotIn(private, text)
        for name, status in report['commands'].items():
            self.assertIn(name, {'xcode', 'initial_readiness', 'registered_service', 'kickstart', 'restarted_service', 'readiness'})
            self.assertEqual(set(status), self.STATUS_FIELDS)
            self.assertIn(status['status'], draft.STATES)
        for field in ('application_recompiled', 'application_startup_verified', 'native_test_cases_verified',
                      'release_qualified', 'physical_device_verified', 'default_device_set_erased'):
            self.assertIs(report[field], False)
        self.assertIs(report['diagnostic_only'], True)
        self.assertIs(report['shared_user_service_scope'], True)
        return text

    def test_one_exact_user_restart_requires_new_pid_post_proof_and_available_runtime(self):
        for job in ('native-api-probe', 'startup-artifact-replay'):
            with self.subTest(job=job), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME
                command, calls = self.runner()
                report = self.invoke(output, command, {**self.ENV, 'GITHUB_JOB': job, 'AYU_CLEAN_JOB': job})
                self.read_report(output, report)
                self.assertTrue(report['passed']); self.assertIsNone(report['failure'])
                self.assertEqual(len(calls), 6)
                self.assertEqual(report['restart_attempts'], 1)
                for field in ('registered_service_verified', 'service_restart_confirmed', 'pid_changed',
                              'post_restart_runtime_available'):
                    self.assertIs(report[field], True)
                self.assertEqual(sum('kickstart' in args for args, _ in calls), 1)
                self.assertFalse(any(any(word in {'sudo', 'bootout', 'killall', 'pkill', 'erase', 'delete', 'create'}
                                         for word in args) for args, _ in calls))

    def test_retained_settings_job_requires_its_exact_workflow_marker(self):
        for marker in ('custom-options-artifact-replay', 'native-api-probe', 'other-job'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME
                command, calls = self.runner()
                report = self.invoke(output, command, {**self.ENV,
                    'GITHUB_JOB': 'custom-options-artifact-replay', 'AYU_CLEAN_JOB': marker})
                self.read_report(output, report)
                expected = marker == 'custom-options-artifact-replay'
                self.assertEqual(report['passed'], expected)
                self.assertEqual(len(calls), 6 if expected else 0)

    def test_os_temporary_alias_is_canonicalized_before_strict_output_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            canonical = root / 'canonical'; canonical.mkdir()
            alias = root / 'alias'; alias.symlink_to(canonical, target_is_directory=True)
            with tempfile.TemporaryDirectory(dir=alias) as temporary:
                logical = Path(temporary) / draft.REPORT_NAME
                self.assertNotEqual(logical, logical.resolve())
                rejected = Mock(side_effect=AssertionError('Aliased output must precede commands'))
                with self.assertRaises(draft.RefreshError): self.invoke(logical, rejected)
                rejected.assert_not_called()
                command, calls = self.runner()
                report = self.invoke(logical.resolve(), command)
                self.assertTrue(report['passed']); self.assertEqual(len(calls), 6)
                self.read_report(logical.resolve(), report)

    def test_all_required_session_guards_reject_before_any_command(self):
        cases = []
        for key in self.ENV:
            missing = dict(self.ENV); missing.pop(key)
            cases.extend([missing, {**self.ENV, key: 'invalid-private-guard'}])
        cases.extend({**self.ENV, 'GITHUB_RUN_ID': value} for value in
                     ('0', '-1', '01', '1\n', str(2**63), 1, True, None))
        cases.extend({**self.ENV, key: self.SECRET} for key in
                     ('GH_TOKEN', 'GITHUB_TOKEN', 'CHECKOUT_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH'))
        for env in cases:
            with self.subTest(env=env), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME
                command = Mock(side_effect=AssertionError('Guard must precede commands'))
                report = self.invoke(output, command, env)
                command.assert_not_called(); self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertIsInstance(report['failure'], str)
                self.assertEqual(report['restart_attempts'], 0); self.assertEqual(report['commands'], {})

    def test_unsupported_or_uncertain_host_identity_rejects_before_commands(self):
        hosts = [{'system': 'Linux'}, {'system': None}, {'machine': 'x86_64'}, {'machine': None}]
        hosts.extend({'uid': value} for value in (0, -1, True, '501', None, 2**32 - 1))
        for host in hosts:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command = Mock()
                report = self.invoke(output, command, **host)
                command.assert_not_called(); self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(report['failure'], 'unsupportedHost')

    def test_initial_runtime_failure_is_retained_but_does_not_invent_recovery(self):
        for state in ('timedOut', 'outputLimit', 'commandFailed', 'unavailable'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME
                command, calls = self.runner({1: self.reply(self.SECRET.encode(), state)})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertTrue(report['passed']); self.assertEqual(len(calls), 6)
                self.assertEqual(report['commands']['initial_readiness']['status'], state)
                self.assertEqual(report['restart_attempts'], 1)

    def test_bad_xcode_proof_prevents_inventory_and_restart(self):
        replies = [self.reply(b'Xcode 27.0\n'), self.reply(b'prefix\nXcode 27.1\n'),
                   self.reply(b'Xcode 27.1\n', 'timedOut'), self.reply(b'Xcode 27.1\n', 'outputLimit'),
                   self.reply(b'Xcode 27.1\n', 'commandFailed'), RuntimeError(self.SECRET)]
        for value in replies:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({0: value})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(len(calls), 1)
                self.assertEqual(report['restart_attempts'], 0)
                self.assertEqual(report['failure'], 'pinnedXcodeUnverified')

    def test_service_parser_rejects_nested_duplicate_partial_and_foreign_proofs(self):
        valid = self.service()
        self.assertEqual(draft.registered_pid(valid, self.TARGET), self.BEFORE)
        nested_only = (self.TARGET + ' = {\n\tenvironment = {\n\t\tstate = running\n\t\tpid = 21001\n\t}\n}\n').encode()
        partial = (self.TARGET + ' = {\n\tstate = running\n\tpid = 21001\n\tenvironment = {\n}').encode()
        nested_wrong_indent = (self.TARGET + ' = {\n\tenvironment = {\n\tstate = running\n\tpid = 21001\n\t}\n}\n').encode()
        invalid = [b'', valid.decode(), b'\xff', valid[:-2], valid + b'private trailer',
                   self.service(target=self.TARGET.replace('501', '502')),
                   self.service(target=self.TARGET.replace('user/', 'gui/')),
                   self.service(target='system/' + draft.LABEL),
                   valid.replace(b'\tstate = running', b'\tstate = waiting'),
                   valid.replace(b'\tstate = running', b'\tstate = running\n\tstate = running'),
                   valid.replace(b'\tpid = 21001', b'\tpid = 21001\n\tpid = 21001'),
                   valid.replace(b'\tpid = 21001', b'\t\tpid = 21001'),
                   valid.replace(b'\tpid = 21001', b' pid = 21001'),
                   nested_only, partial, nested_wrong_indent, b'x' * draft.LIMIT,
                   (self.TARGET + ' = {\n' + '\n' * 4097 + '\tstate = running\n\tpid = 21001\n}\n').encode()]
        invalid.extend(self.service(pid=value) for value in ('0', '-1', '01', '1 2', '1\n\tpid = 2', str(2**31)))
        for data in invalid:
            with self.subTest(data=data):
                self.assertIsNone(draft.registered_pid(data, self.TARGET))

    def test_unverified_registration_never_restarts_or_uses_gui_fallback(self):
        values = [self.reply(self.service(target=self.TARGET.replace('501', '502'))),
                  self.reply(self.service(target=self.TARGET.replace('user/', 'gui/'))),
                  self.reply(self.service().replace(b'\tpid = 21001', b'\t\tpid = 21001')),
                  self.reply(self.service()[:-2]), self.reply(self.service(), 'timedOut'),
                  self.reply(self.service(), 'outputLimit'), self.reply(self.service(), 'commandFailed'),
                  self.reply(self.service(), 'unavailable'), self.reply(b'x' * (draft.LIMIT + 1)),
                  RuntimeError(self.SECRET)]
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({2: value})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(len(calls), 3)
                self.assertEqual(report['failure'], 'registeredServiceUnverified')
                self.assertEqual(report['restart_attempts'], 0)
                self.assertFalse(report['registered_service_verified'])

    def test_restart_failure_stale_or_malformed_pid_never_retries_or_passes(self):
        values = [self.reply(str(self.BEFORE).encode()), self.reply(b'0'), self.reply(b'01'),
                  self.reply(b'21002\n21003\n'), self.reply(b'PID 21002'), self.reply(b'1' * 33),
                  self.reply(str(self.AFTER).encode(), 'timedOut'),
                  self.reply(str(self.AFTER).encode(), 'outputLimit'),
                  self.reply(str(self.AFTER).encode(), 'commandFailed'), RuntimeError(self.SECRET)]
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({3: value})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(len(calls), 4)
                self.assertEqual(report['restart_attempts'], 1)
                self.assertFalse(report['service_restart_confirmed'])
                self.assertIn(report['failure'], {'servicePidUnchanged', 'serviceRestartUnverified'})

    def test_post_restart_identity_must_match_exact_returned_new_pid(self):
        values = [self.reply(self.service()), self.reply(self.service(self.AFTER + 1)),
                  self.reply(self.service(self.AFTER, self.TARGET.replace('user/', 'gui/'))),
                  self.reply(self.service(self.AFTER), 'timedOut'), self.reply(self.service(self.AFTER), 'outputLimit'),
                  self.reply(self.service(self.AFTER), 'commandFailed'), RuntimeError(self.SECRET)]
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({4: value})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(len(calls), 5)
                self.assertEqual(report['restart_attempts'], 1); self.assertTrue(report['pid_changed'])
                self.assertFalse(report['service_restart_confirmed'])
                self.assertEqual(report['failure'], 'restartedServiceIdentityUnverified')

    def test_final_runtime_requires_unique_exact_version_and_literal_availability(self):
        exact = {'identifier': draft.RUNTIME, 'version': '27.0', 'isAvailable': True}
        invalid = [b'', b'\xff', b'{}', b'[]', b'private runtime stdout',
                   self.runtimes(identifier=draft.RUNTIME + '-other'), self.runtimes(version='27.1'),
                   self.runtimes(isAvailable=False), self.runtimes(isAvailable=1),
                   json.dumps({'runtimes': [exact, exact]}).encode(),
                   json.dumps({'runtimes': [None, {'identifier': draft.RUNTIME}]}).encode(),
                   b'x' * (draft.LIMIT + 1)]
        self.assertTrue(draft.runtime_available(self.runtimes()))
        for data in invalid:
            with self.subTest(data=data):
                self.assertFalse(draft.runtime_available(data))
        values = [self.reply(data) for data in invalid]
        values.extend(self.reply(self.runtimes(), state) for state in ('timedOut', 'commandFailed', 'outputLimit', 'unavailable'))
        values.append(RuntimeError(self.SECRET))
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({5: value})
                report = self.invoke(output, command)
                self.read_report(output, report)
                self.assertFalse(report['passed']); self.assertEqual(len(calls), 6)
                self.assertTrue(report['service_restart_confirmed']); self.assertEqual(report['restart_attempts'], 1)
                self.assertFalse(report['post_restart_runtime_available'])
                self.assertEqual(report['failure'], 'runtimeReadinessUnverified')

    def test_closed_metadata_drops_raw_data_and_invalid_status_fields(self):
        metadata = {'status': 'not-public-' + self.SECRET, 'exit_code': True, 'stdout_bytes': 2**50,
                    'stderr_bytes': -1, 'stderr': self.SECRET, 'private_path': self.PRIVATE_PATH}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / draft.REPORT_NAME; command, calls = self.runner({0: (metadata, self.SECRET.encode())})
            report = self.invoke(output, command)
            self.read_report(output, report)
            self.assertEqual(len(calls), 1); self.assertFalse(report['passed'])
            self.assertEqual(report['commands']['xcode'], {'status': 'unavailable', 'exit_code': None,
                                                         'timeout': False, 'output_limit': False,
                                                         'stdout_bytes': 0, 'stderr_bytes': 0})

    def test_existing_wrong_basename_and_linked_destinations_block_before_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); existing = root / draft.REPORT_NAME; existing.write_text('preserve original')
            linked_parent = root / 'linked'; linked_parent.symlink_to(root, target_is_directory=True)
            direct_link_parent = root / 'direct'; direct_link_parent.mkdir()
            direct_link = direct_link_parent / draft.REPORT_NAME; direct_link.symlink_to(existing)
            outputs = [existing, root / 'other.json', linked_parent / draft.REPORT_NAME, direct_link,
                       root / 'absent' / draft.REPORT_NAME]
            for output in outputs:
                with self.subTest(output=output):
                    command = Mock()
                    with self.assertRaises((ValueError, OSError)):
                        self.invoke(output, command)
                    command.assert_not_called()
                    self.assertEqual(existing.read_text(), 'preserve original')

    def test_reserved_report_prevents_any_second_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / draft.REPORT_NAME; command, _ = self.runner()
            report = self.invoke(output, command); self.read_report(output, report)
            original = output.read_bytes(); second = Mock()
            with self.assertRaises((ValueError, OSError)):
                self.invoke(output, second)
            second.assert_not_called(); self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
