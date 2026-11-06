#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""One exact user-service refresh on an exclusive hosted CI session.

This does not erase simulator data, retry tests, or qualify an artifact.
Launchctl output stays private because it can contain the service environment.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re

from simulator_probe import diagnostic_command

REPOSITORY = 'Livara-Dev/Telegram-iOS'
SOURCE = 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'
LABEL = 'com.apple.CoreSimulator.CoreSimulatorService'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
JOBS = frozenset({'native-api-probe', 'startup-artifact-replay', 'custom-options-artifact-replay'})
REPORT_NAME = 'clean-simulator-service.json'
LIMIT = 65536
STATES = frozenset({'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'})
RUNTIME_COMMAND = ['xcrun', 'simctl', 'list', 'runtimes', '--json']


class RefreshError(ValueError):
    pass


def _status(value):
    value = value if isinstance(value, dict) else {}
    state = value.get('status')
    state = state if isinstance(state, str) and state in STATES else 'unavailable'
    code = value.get('exit_code')
    code = code if type(code) is int and -(2**31) <= code < 2**31 else None
    result = {'status': state, 'exit_code': code, 'timeout': state == 'timedOut',
              'output_limit': state == 'outputLimit'}
    for field in ('stdout_bytes', 'stderr_bytes'):
        number = value.get(field)
        result[field] = number if type(number) is int and 0 <= number <= LIMIT else 0
    return result


def _completed(status):
    return status['status'] == 'completed' and status['exit_code'] == 0


def registered_pid(data, target):
    """Parse only direct state/PID fields of one exact registered service.

    Apple's print format is not an API. Unknown indentation, duplicate direct
    fields, an unexpected target, or a partial record cannot authorize restart.
    No field values other than these integers leave this private parser.
    """
    try:
        if not isinstance(data, bytes) or not 0 < len(data) < LIMIT:
            return None
        lines = data.decode('utf-8').splitlines()
        if (not 3 <= len(lines) <= 4096 or lines[0] != target + ' = {' or lines[-1] != '}'
                or any(line.startswith(('user/', 'gui/', 'system/')) for line in lines[1:])):
            return None
        states, identifiers, depth = [], [], 1
        for index, line in enumerate(lines[1:], 1):
            if not line.strip():
                continue
            indentation = len(line) - len(line.lstrip('\t'))
            value = line[indentation:]
            if value == '}':
                if indentation != depth - 1:
                    return None
                depth -= 1
                if depth == 0 and index != len(lines) - 1:
                    return None
                continue
            if depth < 1 or indentation != depth or value.startswith(' '):
                return None
            if value.endswith(' = {') and not any(character in value[:-4] for character in '{}'):
                depth += 1
                continue
            if '{' in value or '}' in value:
                return None
            if depth == 1:
                if value.startswith('state = '): states.append(value[len('state = '):])
                if value.startswith('pid = '): identifiers.append(value[len('pid = '):])
        if depth != 0:
            return None
        if states != ['running'] or len(identifiers) != 1 or re.fullmatch(r'[1-9][0-9]{0,9}', identifiers[0]) is None:
            return None
        candidate = int(identifiers[0])
        return candidate if 0 < candidate < 2**31 else None
    except (UnicodeError, ValueError, TypeError, OverflowError):
        return None


def _kickstart_pid(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= 32:
        return None
    try:
        value = data.decode('ascii').strip()
        if re.fullmatch(r'[1-9][0-9]{0,9}', value) is None:
            return None
        number = int(value)
        return number if 0 < number < 2**31 else None
    except (UnicodeError, ValueError, OverflowError):
        return None


def runtime_available(data):
    try:
        if not isinstance(data, bytes) or not 0 < len(data) <= LIMIT:
            return False
        value = json.loads(data)
        runtimes = value.get('runtimes') if isinstance(value, dict) else None
        if not isinstance(runtimes, list):
            return False
        selected = [item for item in runtimes if isinstance(item, dict) and item.get('identifier') == RUNTIME]
        return len(selected) == 1 and selected[0].get('version') == '27.0' and selected[0].get('isAvailable') is True
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False


def _guard(environment, system, machine, identifier):
    if system != 'Darwin' or machine != 'arm64' or type(identifier) is not int or not 0 < identifier < 2**32 - 1:
        raise RefreshError('unsupportedHost')
    required = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': REPOSITORY, 'RUNNER_OS': 'macOS',
                'RUNNER_ENVIRONMENT': 'github-hosted', 'AYU_CLEAN_RUNNER_ENVIRONMENT': 'github-hosted',
                'AYU_CLEAN_SESSION': 'exclusive-cached-artifacts', 'AYU_CLEAN_SOURCE': SOURCE}
    if any(environment.get(key) != value for key, value in required.items()):
        raise RefreshError('exclusiveHostedSessionUnverified')
    job = environment.get('GITHUB_JOB')
    if not isinstance(job, str) or job not in JOBS or environment.get('AYU_CLEAN_JOB') != job:
        raise RefreshError('exclusiveHostedJobUnverified')
    run = environment.get('GITHUB_RUN_ID')
    if not isinstance(run, str) or re.fullmatch(r'[1-9][0-9]{0,18}', run) is None or int(run) > 2**63 - 1:
        raise RefreshError('exclusiveHostedRunUnverified')
    if any(environment.get(key) for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'CHECKOUT_TOKEN', 'TELEGRAM_API_ID', 'TELEGRAM_API_HASH')):
        raise RefreshError('executionCredentialsPresent')


def _open_report(output):
    path = Path(output)
    if (path.name != REPORT_NAME or path.exists() or any(parent.is_symlink() for parent in (path, *path.parents))
            or not path.parent.is_dir()):
        raise RefreshError('unsafeReportDestination')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


def refresh(output, *, env=None, command=diagnostic_command, system=platform.system,
            machine=platform.machine, uid=os.getuid):
    """Perform at most one registered current-user service action; no retries."""
    descriptor = _open_report(output)  # Reserve the one invocation before any command.
    report = {'schema': 1, 'diagnostic_only': True, 'application_recompiled': False,
              'application_startup_verified': False, 'native_test_cases_verified': False,
              'release_qualified': False, 'physical_device_verified': False,
              'shared_user_service_scope': True, 'default_device_set_erased': False,
              'service_label': LABEL, 'runtime': RUNTIME, 'passed': False, 'failure': None,
              'restart_attempts': 0, 'registered_service_verified': False,
              'service_restart_confirmed': False, 'pid_changed': False,
              'post_restart_runtime_available': False, 'commands': {}}
    try:
        environment = dict(os.environ if env is None else env)
        identifier = uid()
        _guard(environment, system(), machine(), identifier)
        secrets = tuple(value for key, value in environment.items() if isinstance(value, str) and value
                        and re.search(r'TOKEN|SECRET|PASSWORD|API_ID|API_HASH|AUTH|PRIVATE_KEY|SESSION', key, re.I))
        def call(name, arguments, timeout):
            try:
                value, data = command(arguments, secrets, timeout=timeout)
                status = _status(value)
                if not isinstance(data, bytes) or len(data) > LIMIT:
                    status, data = _status({'status': 'outputLimit'}), b''
            except Exception:
                status, data = _status({}), b''
            report['commands'][name] = status
            return status, data if _completed(status) else b''
        status, version = call('xcode', ['xcodebuild', '-version'], 10)
        if not _completed(status) or version.splitlines()[:1] != [b'Xcode 27.1']:
            raise RefreshError('pinnedXcodeUnverified')
        # A read-only call can register the on-demand service. Its failure is
        # retained because refreshing an already-stuck service is the purpose.
        call('initial_readiness', RUNTIME_COMMAND, 10)
        target = 'user/' + str(identifier) + '/' + LABEL
        status, private = call('registered_service', ['/bin/launchctl', 'print', target], 5)
        before = registered_pid(private, target) if _completed(status) else None
        if before is None:
            raise RefreshError('registeredServiceUnverified')
        report['registered_service_verified'] = True
        report['restart_attempts'] = 1
        status, private = call('kickstart', ['/bin/launchctl', 'kickstart', '-kp', target], 10)
        after = _kickstart_pid(private) if _completed(status) else None
        if after is None:
            raise RefreshError('serviceRestartUnverified')
        if after == before:
            raise RefreshError('servicePidUnchanged')
        report['pid_changed'] = True
        status, private = call('restarted_service', ['/bin/launchctl', 'print', target], 5)
        if not _completed(status) or registered_pid(private, target) != after:
            raise RefreshError('restartedServiceIdentityUnverified')
        report['service_restart_confirmed'] = True
        status, private = call('readiness', RUNTIME_COMMAND, 10)
        if not _completed(status) or not runtime_available(private):
            raise RefreshError('runtimeReadinessUnverified')
        report['post_restart_runtime_available'] = True
        report['passed'] = True
    except RefreshError as error:
        report['failure'] = str(error)
    except Exception:
        report['failure'] = 'unexpectedRefreshFailure'
    finally:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write('\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = refresh(arguments.output)
    except (OSError, ValueError, TypeError, RuntimeError):
        print(json.dumps({'passed': False, 'failure': 'refreshPreparationFailed', 'diagnostic_only': True}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
