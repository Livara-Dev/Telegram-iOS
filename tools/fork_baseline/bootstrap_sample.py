# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Private, bounded observations of one still-running synthetic XCTest command.

Only the caller's known Xcode PID may be sampled. Other approved process roles
contribute relationship booleans, never identities, arguments, or stack data.
"""
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import time

import simulator_probe

command = simulator_probe.diagnostic_command
BUNDLE = simulator_probe.BUNDLE
MAX_BYTES = 65536
MAX_PS_ROWS = 4096
MAX_SELECTED_ROWS = 64
OVERALL_SECONDS = 18
ROLES = ('xcodebuild', 'ProjectionTestHost', 'testmanagerd', 'CoreSimulatorService')
XCODE_PATHS = frozenset({'/usr/bin/xcodebuild',
    '/Applications/Xcode_27.1.app/Contents/Developer/usr/bin/xcodebuild'})
STATUSES = frozenset({'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'})
WAIT_SYMBOLS = {
    ('mach_msg_trap', 'libsystem_kernel.dylib'): 'mach_message_wait',
    ('mach_msg2_trap', 'libsystem_kernel.dylib'): 'mach_message_wait',
    ('_xpc_connection_send_message_with_reply_sync', 'libxpc.dylib'): 'xpc_sync_wait',
    ('xpc_connection_send_message_with_reply_sync', 'libxpc.dylib'): 'xpc_sync_wait',
    ('_dispatch_semaphore_wait_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('_dispatch_group_wait_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('_dispatch_sync_f_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('__ulock_wait', 'libsystem_kernel.dylib'): 'ulock_wait',
    ('__ulock_wait2', 'libsystem_kernel.dylib'): 'ulock_wait',
}
WAIT_CATEGORIES = ('mach_message_wait', 'xpc_sync_wait', 'dispatch_wait', 'ulock_wait')
SAMPLE_EXEC_WRAPPER = (
    "import os,resource,sys;"
    "resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536));"
    "os.execv('/usr/bin/sample',['/usr/bin/sample',sys.argv[1],'3','10','-file',sys.argv[2]])"
)


def _metadata(value):
    """Do not carry command stderr, arbitrary fields, or unbounded numbers."""
    value = value if isinstance(value, dict) else {}
    state = value.get('status')
    state = state if isinstance(state, str) and state in STATUSES else 'unavailable'
    code = value.get('exit_code')
    code = code if type(code) is int and -(2**31) <= code < 2**31 else None
    result = {'status': state, 'exit_code': code, 'timeout': state == 'timedOut',
              'output_limit': state == 'outputLimit'}
    for field in ('stdout_bytes', 'stderr_bytes'):
        number = value.get(field)
        result[field] = number if type(number) is int and 0 <= number <= MAX_BYTES else 0
    return result


def _run(arguments, secrets, timeout, deadline):
    # diagnostic_command may spend two seconds retiring its direct child.
    budget = min(timeout, deadline - time.monotonic() - 2)
    if budget <= 0:
        return _metadata({'status': 'timedOut'}), b''
    try:
        status, data = command(arguments, secrets, timeout=budget)
        status = _metadata(status)
        if not isinstance(data, bytes) or len(data) > MAX_BYTES:
            return _metadata({'status': 'outputLimit'}), b''
        return status, data
    except Exception:
        return _metadata({}), b''


def _alive(predicate):
    try:
        result = predicate()
        return result if type(result) is bool else None
    except Exception:
        return None


def _require_alive(report, predicate):
    alive = _alive(predicate)
    if alive is True:
        return True
    report['state'] = 'childExited' if alive is False else 'unavailable'
    return False


def _completed(status):
    return status['status'] == 'completed' and status['exit_code'] == 0


def _empty_roles():
    return {role: {'observed': False, 'shared_owned_group': False, 'parent_is_owned_child': False}
            for role in ROLES}


def _role(path):
    if path in XCODE_PATHS:
        return 'xcodebuild'
    name = PurePosixPath(path).name
    if name == 'com.apple.CoreSimulator.CoreSimulatorService':
        return 'CoreSimulatorService'
    if name in {'ProjectionTestHost', 'testmanagerd', 'CoreSimulatorService'}:
        return name
    return None


def _process_snapshot(data, pid, pgid, uid):
    result = {'state': 'invalidPayload', 'owned_xcodebuild_verified': False, 'roles': _empty_roles()}
    try:
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
            return result
        lines = data.decode('utf-8').splitlines()
        if not 1 <= len(lines) <= MAX_PS_ROWS:
            return result
        selected, child, seen = [], [], set()
        for line in lines:
            if not line.strip():
                continue
            fields = line.split(None, 4)
            if len(fields) != 5 or any(re.fullmatch(r'[0-9]{1,10}', item) is None for item in fields[:4]):
                return result
            current, parent, group, owner = map(int, fields[:4])
            if (current > 2**31 - 1 or parent > 2**31 - 1 or group > 2**31 - 1
                    or owner > 2**32 - 2 or current in seen):
                return result
            seen.add(current)
            if owner != uid:
                continue
            role = _role(fields[4].strip())
            if current == pid:
                child.append((group, role))
            if role == 'xcodebuild' and current != pid:
                continue
            if role is not None:
                selected.append((role, parent, group))
                if len(selected) > MAX_SELECTED_ROWS:
                    return result
        if child != [(pgid, 'xcodebuild')]:
            result['state'] = 'ownershipUnverified'
            return result
        roles = result['roles']
        for role, parent, group in selected:
            roles[role]['observed'] = True
            roles[role]['shared_owned_group'] |= group == pgid
            roles[role]['parent_is_owned_child'] |= parent == pid
        result.update(state='observed', owned_xcodebuild_verified=True)
        return result
    except (UnicodeError, ValueError, TypeError, OverflowError):
        return result


def parse_sample(data, secrets):
    """Recognize literal main-thread wait symbols, never publish a stack."""
    invalid = {'state': 'invalidPayload', 'wait_categories': {category: 0 for category in WAIT_CATEGORIES},
               'main_thread_observed': False, 'complete_stacks_verified': False}
    try:
        if not isinstance(data, bytes) or not 0 < len(data) < MAX_BYTES:
            return invalid
        lines = data.decode('utf-8').splitlines()
        graph = next(index for index, line in enumerate(lines) if line.strip() == 'Call graph:')
        images = next(index for index, line in enumerate(lines) if line.strip() == 'Binary Images:')
        if graph >= images:
            return invalid
        thread = re.compile(r'^\s*(?:[+!:|]\s*)*\d+\s+Thread_\d+\b(.*)$')
        frame = re.compile(r'^\s*(?:[+!:|]\s*)*(\d{1,6})\s+([A-Za-z_][A-Za-z_0-9]*)\s+'
                           r'\(in ([A-Za-z0-9_.+-]+)\)(?:\s+\+\s+\d+)?(?:\s+\[0x[0-9a-fA-F]+\])?\s*$')
        main, mains = False, 0
        counts = {category: 0 for category in WAIT_CATEGORIES}
        for line in lines[graph + 1:images]:
            match = thread.fullmatch(line)
            if match:
                main = re.fullmatch(r'\s+DispatchQueue_\d+: com\.apple\.main-thread\s+\(serial\)\s*', match[1]) is not None
                mains += int(main)
                continue
            if re.search(r'\bThread_', line):
                return invalid
            if not main:
                continue
            match = frame.fullmatch(line)
            if match:
                category = WAIT_SYMBOLS.get((match[2], match[3]))
                if category is not None:
                    counts[category] = min(100000, counts[category] + int(match[1]))
        if mains != 1:
            return invalid
        return {'state': 'parsed', 'wait_categories': counts, 'main_thread_observed': True,
                'complete_stacks_verified': False}
    except (UnicodeError, ValueError, TypeError, StopIteration, OverflowError):
        return invalid


def _sample_file(path, uid):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != uid
                or info.st_nlink != 1 or not 0 < info.st_size < MAX_BYTES):
            return None
        data = os.read(descriptor, MAX_BYTES + 1)
        return data if len(data) == info.st_size else None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def capture(pid, pgid, identifier, secrets, is_alive):
    """Observe one owned Xcode child without changing command/test outcomes."""
    report = {'diagnostic_only': True, 'state': 'unavailable',
              'process_snapshot': {'state': 'notAttempted', 'owned_xcodebuild_verified': False, 'roles': _empty_roles()},
              'installed_host': {'state': 'notAttempted'}, 'sample': {'state': 'notAttempted'}}
    try:
        if (type(pid) is not int or not 0 < pid < 2**31 or type(pgid) is not int or pgid != pid
                or not isinstance(identifier, str)
                or re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier) is None
                or not callable(is_alive)):
            report['state'] = 'invalidRequest'
            return report
        uid, deadline = os.getuid(), time.monotonic() + OVERALL_SECONDS
        if not _require_alive(report, is_alive):
            return report
        status, data = _run(['/bin/ps', '-A', '-o', 'pid=,ppid=,pgid=,uid=,comm='], secrets, 3, deadline)
        report['process_snapshot'] = snapshot = _process_snapshot(data, pid, pgid, uid) if _completed(status) else {
            'state': 'unavailable', 'owned_xcodebuild_verified': False, 'roles': _empty_roles()}
        snapshot['command'] = status
        if not snapshot['owned_xcodebuild_verified']:
            report['state'] = 'ownershipUnverified'
            return report
        if not _require_alive(report, is_alive):
            return report
        status, _ = _run(['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'app'], secrets, 3, deadline)
        report['installed_host'] = {'state': 'available' if _completed(status) else 'unavailable',
                                    'command': status, 'path_verified': False}
        if not _require_alive(report, is_alive):
            return report
        with tempfile.TemporaryDirectory(prefix='ayu-bootstrap-sample-') as private:
            directory = Path(private); directory.chmod(0o700)
            path = directory / 'sample.txt'
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            status, _ = _run([sys.executable, '-c', SAMPLE_EXEC_WRAPPER, str(pid), str(path)], secrets, 10, deadline)
            selection = {'state': 'unavailable', 'wait_categories': {category: 0 for category in WAIT_CATEGORIES},
                         'main_thread_observed': False, 'complete_stacks_verified': False}
            if _completed(status):
                data = _sample_file(path, uid)
                if data is not None:
                    selection = parse_sample(data, secrets)
            report['sample'] = {'state': 'observed' if selection['state'] == 'parsed' else 'unavailable',
                                'command': status, 'selection': selection}
        report['state'] = 'observed' if report['sample']['state'] == 'observed' else 'partial'
        return report
    except Exception:
        report['state'] = 'unavailable'
        return report
