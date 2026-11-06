#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Closed, private observations of the one owned timed-out installer child.

No observation changes an install result or grants startup qualification. The
parent supplies only the exact Popen it already owns; no process search occurs.
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

SOURCE = '5d241267735b3958c225681d173853568bab705d'
IPA_SHA = '01f32ba769f876c20bed6090c54a8066d27f09cc73d75d890e7f57c435219e0a'
BUNDLE = 'org.livara.ayugram'
MODEL = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
MAX_BYTES = 65536
BUDGET_SECONDS = 12
UUID = re.compile(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}')
FAMILIES = ('mach_message_wait', 'xpc_sync_wait', 'dispatch_wait', 'lock_wait', 'file_io', 'copy_io', 'child_process_wait')
IMAGES = ('CoreSimulator', 'MobileInstallation', 'libcopyfile.dylib')
SYMBOLS = {
    ('mach_msg_trap', 'libsystem_kernel.dylib'): 'mach_message_wait',
    ('mach_msg2_trap', 'libsystem_kernel.dylib'): 'mach_message_wait',
    ('_xpc_connection_send_message_with_reply_sync', 'libxpc.dylib'): 'xpc_sync_wait',
    ('xpc_connection_send_message_with_reply_sync', 'libxpc.dylib'): 'xpc_sync_wait',
    ('_dispatch_semaphore_wait_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('_dispatch_group_wait_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('_dispatch_sync_f_slow', 'libdispatch.dylib'): 'dispatch_wait',
    ('__ulock_wait', 'libsystem_kernel.dylib'): 'lock_wait',
    ('__ulock_wait2', 'libsystem_kernel.dylib'): 'lock_wait',
    **{(symbol, 'libsystem_kernel.dylib'): 'file_io' for symbol in
       ('read', '__read_nocancel', 'pread', 'write', '__write_nocancel', 'pwrite', 'fsync')},
    ('copyfile', 'libcopyfile.dylib'): 'copy_io',
    ('fcopyfile', 'libcopyfile.dylib'): 'copy_io',
    ('__wait4', 'libsystem_kernel.dylib'): 'child_process_wait',
    ('wait4', 'libsystem_kernel.dylib'): 'child_process_wait',
}
SAMPLE_EXEC = ("import os,resource,sys;"
               "resource.setrlimit(resource.RLIMIT_FSIZE,(65536,65536));"
               "os.execv('/usr/bin/sample',['/usr/bin/sample',sys.argv[1],'3','10','-file',sys.argv[2]])")
APPLE_SIMCTL = Path('/Applications/Xcode_27.1.app/Contents/Developer/usr/bin/simctl')
SCRIPT = str(Path(__file__).absolute())


def require(value):
    if not value:
        raise ValueError('installer_observation_rejected')


def unique(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def strict_json(data):
    require(isinstance(data, bytes) and 0 < len(data) <= MAX_BYTES)
    value = json.loads(data, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    require(type(value) is dict)
    return value


def blank():
    return {
        'schema': 1, 'diagnostic_only': True, 'source': SOURCE,
        'source_simulator_ipa_sha256': IPA_SHA, 'attempt': 1, 'phase': 'installApplication',
        'state': 'unavailable', 'original_deadline_seconds': 120,
        'post_deadline_budget_seconds': BUDGET_SECONDS, 'post_deadline_elapsed_ms': 0,
        'budget_exceeded': False, 'post_observer_installer_exit_code': None,
        'raw_sample_removal_verified': False,
        'original_failure_preserved': True, 'startup_verified': False,
        'tool': {'state': 'notAttempted', 'file_kind': 'unavailable', 'fixed_apple_path_verified': False,
                 'xcrun_lookup_is_usr_bin': False,
                 'stable_tool_bytes_verified': False, 'run_first_launch_wrapper_observed': False,
                 'signature_verified': False},
        'identity': {'state': 'notAttempted', 'fixed_argv_context_verified': False,
                     'parent_and_uid_verified': False, 'command_family': 'none'},
        'sample': {'state': 'notAttempted', 'main_thread_observed': False,
                   'complete_stacks_verified': False, 'wait_counts': dict.fromkeys(FAMILIES, 0),
                   'image_family_present': dict.fromkeys(IMAGES, False)},
        'containers': {'snapshot_state': 'notAttempted', 'captured_while_child_alive': False,
                       'installed_bytes_attested': False, 'installed_signature_attested': False,
                       'app': {'state': 'notAttempted', 'verified_matches': 0, 'unidentified_records': 0,
                               'info_identity_verified': False, 'executable_size_equal': False},
                       'data': {'state': 'notAttempted', 'verified_matches': 0, 'unidentified_records': 0}},
        'host': {'state': 'notAttempted', 'same_volume': None, 'private_set_available_bytes': None,
                 'private_set_free_bytes': None, 'physical_memory_bytes': None,
                 'page_size_bytes': None, 'free_pages': None, 'active_pages': None,
                 'wired_pages': None, 'compressed_pages': None},
    }


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def integer(value, maximum, minimum=0):
    require(type(value) is int and minimum <= value <= maximum)


def boolean(value):
    require(type(value) is bool)


def validate_observation(value):
    """Pure fail-closed public schema; never publish arbitrary tool content."""
    base = blank(); keys(value, base)
    for key in ('schema', 'diagnostic_only', 'source', 'source_simulator_ipa_sha256', 'attempt',
                'phase', 'original_deadline_seconds', 'post_deadline_budget_seconds',
                'original_failure_preserved', 'startup_verified'):
        require(type(value[key]) is type(base[key]) and value[key] == base[key])
    require(value['state'] in {'notTriggered', 'captured', 'partial', 'unavailable', 'childExited'})
    integer(value['post_deadline_elapsed_ms'], 2**63 - 1)
    boolean(value['budget_exceeded'])
    boolean(value['raw_sample_removal_verified'])
    require(value['budget_exceeded'] == (value['post_deadline_elapsed_ms'] > BUDGET_SECONDS * 1000))
    if value['post_observer_installer_exit_code'] is not None:
        integer(value['post_observer_installer_exit_code'], 2**31 - 1, -(2**31))
    tool = value['tool']; keys(tool, base['tool'])
    require(tool['state'] in {'notAttempted', 'observed', 'unavailable', 'rejected'})
    require(tool['file_kind'] in {'unavailable', 'machO', 'shellWrapper'})
    for key in ('fixed_apple_path_verified','xcrun_lookup_is_usr_bin','stable_tool_bytes_verified','run_first_launch_wrapper_observed','signature_verified'):
        boolean(tool[key])
    if tool['run_first_launch_wrapper_observed']:
        require(tool['state'] == 'observed' and tool['file_kind'] == 'shellWrapper'
                and tool['fixed_apple_path_verified'] and tool['xcrun_lookup_is_usr_bin'] and tool['stable_tool_bytes_verified'])
    identity = value['identity']; keys(identity, base['identity'])
    require(identity['state'] in {'notAttempted', 'verified', 'unavailable', 'rejected', 'childExited'})
    boolean(identity['fixed_argv_context_verified']); boolean(identity['parent_and_uid_verified'])
    require(identity['command_family'] in {'none', 'simctl', 'toolWrapper', 'verifiedShellWrapper'})
    require((identity['state'] == 'verified') == (identity['parent_and_uid_verified'] is True))
    if identity['state'] == 'verified':
        require(identity['fixed_argv_context_verified'] and identity['command_family'] != 'none')
    if identity['command_family'] == 'verifiedShellWrapper': require(tool['run_first_launch_wrapper_observed'])
    sample = value['sample']; keys(sample, base['sample'])
    require(sample['state'] in {'notAttempted', 'parsed', 'unavailable', 'rejected', 'childExited'})
    boolean(sample['main_thread_observed']); require(sample['complete_stacks_verified'] is False)
    keys(sample['wait_counts'], FAMILIES); keys(sample['image_family_present'], IMAGES)
    for number in sample['wait_counts'].values(): integer(number, 100000)
    for flag in sample['image_family_present'].values(): boolean(flag)
    if sample['state'] == 'parsed': require(identity['state'] == 'verified' and sample['main_thread_observed'])
    else: require(not sample['main_thread_observed'] and not any(sample['wait_counts'].values())
                  and not any(sample['image_family_present'].values()))
    containers = value['containers']; keys(containers, base['containers'])
    require(containers['snapshot_state'] in {'notAttempted', 'completed', 'timedOut', 'unavailable'})
    boolean(containers['captured_while_child_alive'])
    require(containers['installed_bytes_attested'] is False and containers['installed_signature_attested'] is False)
    for role in ('app', 'data'):
        item = containers[role]; keys(item, base['containers'][role])
        require(item['state'] in {'notAttempted', 'unique', 'absent', 'ambiguous', 'unavailable'})
        integer(item['verified_matches'], 256); integer(item['unidentified_records'], 256)
        if item['state'] == 'unique': require(item['verified_matches'] == 1 and item['unidentified_records'] == 0)
        if item['state'] == 'absent': require(item['verified_matches'] == 0 and item['unidentified_records'] == 0)
        if item['state'] == 'ambiguous': require(item['verified_matches'] > 1)
        if role == 'app':
            boolean(item['info_identity_verified']); boolean(item['executable_size_equal'])
            if item['state'] != 'unique': require(not item['info_identity_verified'] and not item['executable_size_equal'])
    if containers['captured_while_child_alive']: require(containers['snapshot_state'] == 'completed')
    host = value['host']; keys(host, base['host'])
    require(host['state'] in {'notAttempted', 'measured', 'unavailable'})
    if host['same_volume'] is not None: boolean(host['same_volume'])
    for key, number in host.items():
        if key not in {'state', 'same_volume'} and number is not None:
            integer(number, 2**20 if key == 'page_size_bytes' else 2**63 - 1, 1 if key == 'page_size_bytes' else 0)
    if value['state'] == 'notTriggered': require(value == {**blank(), 'state': 'notTriggered'})
    return value


def clean_environment():
    result = {key: os.environ[key] for key in ('HOME', 'PATH', 'TMPDIR', 'DEVELOPER_DIR') if key in os.environ}
    require('PATH' in result and 'HOME' in result)
    return result


def command(arguments, timeout, *, max_bytes=MAX_BYTES):
    """Worker-only command; its supervising group supplies the hard outer cap."""
    process, captured, state = None, bytearray(), 'unavailable'
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=clean_environment())
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            drain = None
            while selector.get_map():
                now = time.monotonic()
                if process.poll() is not None and drain is None: drain = min(deadline, now + .05)
                remaining = min(deadline, drain if drain is not None else deadline) - now
                if remaining <= 0:
                    state = 'timedOut' if process.poll() is None else 'completed' if process.returncode == 0 else 'unavailable'
                    break
                for key, _ in selector.select(min(.02, remaining)):
                    data = os.read(key.fileobj.fileno(), min(4096, max_bytes + 1 - len(captured)))
                    if not data: selector.unregister(key.fileobj); continue
                    captured.extend(data)
                    if len(captured) > max_bytes: return 'unavailable', b''
            else:
                remaining = max(0, deadline - time.monotonic())
                try: process.wait(timeout=remaining)
                except subprocess.TimeoutExpired: state = 'timedOut'
                else: state = 'completed' if process.returncode == 0 else 'unavailable'
        return state, bytes(captured) if state == 'completed' else b''
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'unavailable', b''
    finally:
        if process is not None:
            try:
                if process.poll() is None: process.kill()
            except (OSError, ProcessLookupError): pass
            try: process.wait(timeout=.25)
            except subprocess.SubprocessError: pass
            if process.stdout is not None: process.stdout.close()


def worker_command(request, timeout):
    """Never reap the new group leader until its exact group has been retired.

    Its one newline-terminated closed JSON payload is a diagnostic protocol,
    not a subprocess exit or application pass. An unreaped child PID cannot be
    reused as an unrelated process-group ID, even when its leader exits early.
    """
    process, captured, state = None, bytearray(), 'unavailable'
    deadline = time.monotonic() + timeout
    try:
        require(isinstance(request,bytes) and 0<len(request)<=4096)
        process = subprocess.Popen([sys.executable,SCRIPT,'--worker'],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=clean_environment(),start_new_session=True)
        # Darwin need not have Linux's PIPE_BUF value. Never block on request
        # delivery if a newly spawned worker has not reached its stdin reader.
        send_request(process.stdin,request,deadline);process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:state='timedOut';break
                for key,_ in selector.select(min(.02,remaining)):
                    data=os.read(key.fileobj.fileno(),min(4096,MAX_BYTES+1-len(captured)))
                    if not data:selector.unregister(key.fileobj);continue
                    captured.extend(data)
                    if len(captured)>MAX_BYTES:state='unavailable';break
                    if captured.endswith(b'\n'):
                        state='payloadReceived';break
                if state=='payloadReceived' or len(captured)>MAX_BYTES:break
    except (OSError,ValueError,subprocess.SubprocessError):state='unavailable'
    finally:
        if process is not None:
            # No poll/wait happened above. Even an exited leader is unreaped and
            # its PID is reserved, so this can only target our created group.
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            except OSError:state='unavailable'
            try:process.wait(timeout=.25)
            except subprocess.SubprocessError:state='retirementUnverified'
            for stream in (process.stdin,process.stdout):
                if stream is not None and not stream.closed:stream.close()
    return state,bytes(captured) if state=='payloadReceived' else b''


def send_request(stream,data,deadline):
    descriptor=stream.fileno();os.set_blocking(descriptor,False);offset=0
    with selectors.DefaultSelector() as selector:
        selector.register(stream,selectors.EVENT_WRITE)
        while offset<len(data):
            remaining=deadline-time.monotonic();require(remaining>0)
            if not selector.select(min(.02,remaining)):continue
            try:written=os.write(descriptor,data[offset:])
            except BlockingIOError:continue
            require(written>0);offset+=written


def process_identity(data, pid, parent, uid, *, shell_wrapper_verified=False):
    try:
        lines = data.decode('ascii').splitlines(); require(len(lines) == 1)
        fields = lines[0].split(); require(len(fields) == 4)
        require(all(re.fullmatch(r'[0-9]{1,10}', item) for item in fields[:3]))
        require(tuple(map(int, fields[:3])) == (pid, parent, uid))
        require(fields[3] in {'simctl', 'xcrun'} or (shell_wrapper_verified is True and fields[3] == 'bash'))
        return {'simctl': 'simctl', 'xcrun': 'toolWrapper', 'bash': 'verifiedShellWrapper'}[fields[3]]
    except (ValueError, TypeError, UnicodeError): return None


def tool_context():
    """Observe only the selected fixed Apple tool; never publish its raw file."""
    result = blank()['tool']; result['state'] = 'unavailable'
    try:
        found=shutil.which('xcrun')
        require(found is not None and Path(found).resolve(strict=True)==Path('/usr/bin/xcrun').resolve(strict=True))
        result['xcrun_lookup_is_usr_bin']=True
        status, raw = command(['/usr/bin/xcrun', '--find', 'simctl'], .5, max_bytes=4096)
        if status != 'completed': return result
        require(raw.decode('utf-8').strip() == str(APPLE_SIMCTL)
                and len(raw.decode('utf-8').splitlines()) == 1)
        current = Path('/');ancestors=[]
        for part in APPLE_SIMCTL.parts[1:-1]:
            current /= part
            info = current.lstat()
            trusted_apple_ancestor(current,info);ancestors.append((current,identity(info)))
        before = APPLE_SIMCTL.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and not before.st_mode & 0o022 and before.st_nlink == 1 and 0 < before.st_size <= 32 * 1024**2)
        descriptor = os.open(APPLE_SIMCTL, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            require(identity(os.fstat(descriptor)) == identity(before))
            data = bytearray(); digest = hashlib.sha256()
            while len(data) < before.st_size:
                chunk = os.read(descriptor, min(1024**2, before.st_size - len(data)))
                require(chunk); data.extend(chunk); digest.update(chunk)
            require(identity(os.fstat(descriptor)) == identity(before)
                    and identity(APPLE_SIMCTL.lstat()) == identity(before))
            require(all(identity(path.lstat())==expected for path,expected in ancestors))
            # Computed only privately; neither digest, path nor file text exits.
            require(len(digest.digest()) == 32)
        finally: os.close(descriptor)
        result.update(state='observed', fixed_apple_path_verified=True, stable_tool_bytes_verified=True)
        if bytes(data[:4]) in {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                               b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf'}:
            result['file_kind'] = 'machO'
        elif len(data) <= MAX_BYTES and bytes(data).splitlines()[0] == b'#!/bin/bash':
            result['file_kind'] = 'shellWrapper'
            result['run_first_launch_wrapper_observed'] = (
                re.search(rb'(?:\$\{DEVELOPER_DIR\}|\$DEVELOPER_DIR)/usr/bin/xcodebuild["\x27]?\s+-runFirstLaunch\b', data) is not None
                and b'/Library/Developer/PrivateFrameworks/CoreSimulator.framework/' in data
                and re.search(rb'\bexec\s+[^\n]*Resources/bin/simctl', data) is not None)
        else: result['state'] = 'rejected'
        status, _ = command(['/usr/bin/codesign', '--verify', '--strict', str(APPLE_SIMCTL)], .5, max_bytes=4096)
        result['signature_verified'] = status == 'completed'
        return result
    except (OSError, ValueError, UnicodeError, TypeError):
        result['state'] = 'rejected'; return result


def trusted_apple_ancestor(path,info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==0)
    if Path(path)==Path('/Applications'):
        # macOS admin group is root-equivalent for installed application trust.
        # This exact platform parent is the sole group-write allowance.
        require(info.st_gid==80 and stat.S_IMODE(info.st_mode) in {0o755,0o775})
    else:require(not info.st_mode&0o022)


def parse_sample(data, pid, expected_family):
    result = blank()['sample']; result['state'] = 'rejected'
    try:
        require(isinstance(data, bytes) and 0 < len(data) < MAX_BYTES)
        lines = data.decode('utf-8').splitlines()
        headers = [line for line in lines if line.startswith('Process:')]
        require(len(headers) == 1)
        name = {'simctl': 'simctl', 'toolWrapper': 'xcrun', 'verifiedShellWrapper': 'bash'}[expected_family]
        require(re.fullmatch(r'Process:\s+' + name + r'\s+\[' + str(pid) + r'\]\s*', headers[0]))
        require(sum(line.strip() == 'Call graph:' for line in lines) == 1
                and sum(line.strip() == 'Binary Images:' for line in lines) == 1)
        graph = next(i for i, line in enumerate(lines) if line.strip() == 'Call graph:')
        images = next(i for i, line in enumerate(lines) if line.strip() == 'Binary Images:')
        require(graph < images)
        thread = re.compile(r'^\s*(?:[+!:|]\s*)*\d+\s+Thread_\d+\b(.*)$')
        frame = re.compile(r'^\s*(?:[+!:|]\s*)*(\d{1,6})\s+([A-Za-z_][A-Za-z_0-9]*)\s+'
                           r'\(in ([A-Za-z0-9_.+-]+)\)(?:\s+\+\s+\d+)?(?:\s+\[0x[0-9a-fA-F]+\])?\s*$')
        counts = dict.fromkeys(FAMILIES, 0); main, mains = False, 0
        for line in lines[graph+1:images]:
            match = thread.fullmatch(line)
            if match:
                main = re.fullmatch(r'\s+DispatchQueue_\d+: com\.apple\.main-thread\s+\(serial\)\s*', match[1]) is not None
                mains += int(main); continue
            require(not re.search(r'\bThread_', line))
            match = frame.fullmatch(line)
            if main and match and (match[2], match[3]) in SYMBOLS:
                family = SYMBOLS[match[2], match[3]]
                counts[family] = min(100000, counts[family] + int(match[1]))
        require(mains == 1)
        present = dict.fromkeys(IMAGES, False)
        image_line = re.compile(r'^\s*0x[0-9a-fA-F]+\s+-\s+0x[0-9a-fA-F]+\s+([A-Za-z0-9_.+-]+)\s+.*$')
        known_images = {'CoreSimulator': 'CoreSimulator', 'com.apple.CoreSimulator': 'CoreSimulator',
                        'MobileInstallation': 'MobileInstallation', 'com.apple.MobileInstallation': 'MobileInstallation',
                        'libcopyfile.dylib': 'libcopyfile.dylib'}
        for line in lines[images+1:]:
            match = image_line.fullmatch(line)
            if match and match[1] in known_images: present[known_images[match[1]]] = True
        return {'state': 'parsed', 'main_thread_observed': True, 'complete_stacks_verified': False,
                'wait_counts': counts, 'image_family_present': present}
    except (ValueError, TypeError, KeyError, UnicodeError, StopIteration): return result


def identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def safe_info(info, *, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.getuid() and not info.st_mode & 0o022)
    if not directory: require(info.st_nlink == 1)


def directory_at(parent, name):
    require(name not in {'', '.', '..'} and '/' not in name)
    before = os.stat(name, dir_fd=parent, follow_symlinks=False); safe_info(before, directory=True)
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try: require(identity(os.fstat(descriptor)) == identity(before))
    except Exception: os.close(descriptor); raise
    return descriptor


def plist_at(parent, name):
    require('/' not in name)
    before = os.stat(name, dir_fd=parent, follow_symlinks=False); safe_info(before)
    require(0 < before.st_size <= MAX_BYTES)
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        require(identity(os.fstat(descriptor)) == identity(before))
        data = os.read(descriptor, MAX_BYTES + 1)
        require(len(data) == before.st_size and identity(os.fstat(descriptor)) == identity(before)
                and identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(before))
    finally: os.close(descriptor)
    value = plistlib.loads(data); require(type(value) is dict)
    return value


def role_inventory(device, role, executable_size):
    result = blank()['containers'][role]; result['state'] = 'unavailable'
    descriptors = []
    try:
        parent = device
        for name in ('data', 'Containers', 'Bundle' if role == 'app' else 'Data', 'Application'):
            try: parent = directory_at(parent, name)
            except FileNotFoundError:
                result['state'] = 'absent'; return result
            descriptors.append(parent)
        before = identity(os.fstat(parent))
        names = os.listdir(parent); require(len(names) <= 256 and len(set(names)) == len(names))
        matches, unknown, info_ok, size_equal = 0, 0, False, False
        for name in names:
            require(UUID.fullmatch(name) is not None)
            candidate = directory_at(parent, name)
            try:
                try: metadata = plist_at(candidate, '.com.apple.mobile_container_manager.metadata.plist')
                except FileNotFoundError: unknown += 1; continue
                bundle = metadata.get('MCMMetadataIdentifier')
                require(type(bundle) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', bundle))
                if bundle != BUNDLE: continue
                matches += 1
                if role == 'app':
                    app = directory_at(candidate, 'Telegram.app')
                    try:
                        info = plist_at(app, 'Info.plist')
                        require(info.get('CFBundleIdentifier') == BUNDLE and info.get('CFBundleExecutable') == 'Telegram'
                                and str(info.get('CFBundleVersion')) == '1001')
                        executable = os.stat('Telegram', dir_fd=app, follow_symlinks=False); safe_info(executable)
                        info_ok, size_equal = True, executable.st_size == executable_size
                    finally: os.close(app)
            finally: os.close(candidate)
        require(identity(os.fstat(parent)) == before and set(os.listdir(parent)) == set(names))
        state = 'ambiguous' if matches > 1 else 'unavailable' if unknown else 'unique' if matches else 'absent'
        result.update(state=state, verified_matches=matches, unidentified_records=unknown)
        if role == 'app' and state == 'unique': result.update(info_identity_verified=info_ok, executable_size_equal=size_equal)
        return result
    except (OSError, ValueError, TypeError, plistlib.InvalidFileException): return result
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)


@contextlib.contextmanager
def owned_device(request):
    root = device = None
    try:
        path = Path(request['private_root'])
        require(path.is_absolute() and path.resolve(strict=True) == path)
        root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(root); safe_info(info, directory=True)
        require(stat.S_IMODE(info.st_mode) == 0o700 and [info.st_dev, info.st_ino] == request['root_identity'])
        names = os.listdir(root); require(len(names) <= 1024)
        require([name for name in names if UUID.fullmatch(name)] == [request['identifier']])
        device = directory_at(root, request['identifier'])
        value = plist_at(device, 'device.plist')
        require(value.get('UDID') == request['identifier'] and value.get('name') == request['device_name']
                and value.get('deviceType') == MODEL and value.get('runtime') == RUNTIME)
        yield root,device
    finally:
        if device is not None:os.close(device)
        if root is not None:os.close(root)


def snapshot(request):
    result = {'containers': blank()['containers'], 'host': blank()['host']}
    try:
        with owned_device(request) as (root,device):
            return snapshot_owned(request,root,device,result)
    except (OSError,ValueError,TypeError,UnicodeError,plistlib.InvalidFileException):
        result['containers']['snapshot_state']='unavailable';result['host']['state']='unavailable'
        return result


def snapshot_owned(request,root,device,result):
    try:
        for role in ('app', 'data'):
            result['containers'][role] = role_inventory(device, role, request['executable_size'])
        result['containers']['snapshot_state'] = 'completed'
        volume = os.fstatvfs(root)
        result['host'].update(state='measured', same_volume=os.fstat(root).st_dev == request['source_device'],
            private_set_available_bytes=volume.f_bavail * volume.f_frsize,
            private_set_free_bytes=volume.f_bfree * volume.f_frsize)
        status, raw = command(['/usr/sbin/sysctl', '-n', 'hw.memsize'], .2, max_bytes=32)
        if status == 'completed' and re.fullmatch(rb'[0-9]{1,20}\n?', raw):
            result['host']['physical_memory_bytes'] = int(raw)
        status, raw = command(['/usr/bin/vm_stat'], .2, max_bytes=4096)
        if status == 'completed':
            text = raw.decode('ascii')
            header = re.search(r'page size of ([0-9]{1,7}) bytes', text)
            if header: result['host']['page_size_bytes'] = int(header[1])
            for label, key in (('Pages free', 'free_pages'), ('Pages active', 'active_pages'),
                               ('Pages wired down', 'wired_pages'), ('Pages occupied by compressor', 'compressed_pages')):
                match = re.search(r'^' + label + r':\s+([0-9]{1,20})\.\s*$', text, re.MULTILINE)
                if match: result['host'][key] = int(match[1])
        return result
    except (OSError, ValueError, TypeError, UnicodeError, plistlib.InvalidFileException):
        result['containers']['snapshot_state'] = 'unavailable'; result['host']['state'] = 'unavailable'
        return result


def worker(request):
    app=Path(request['app_path']);require(app.is_absolute() and app.resolve(strict=True)==app
        and app.name=='Telegram.app' and app.parent.name.startswith('ayu-launch-smoke-'))
    safe_info(app.lstat(),directory=True);safe_info(app.parent.lstat(),directory=True)
    executable=(app/'Telegram').lstat();safe_info(executable)
    require(0<executable.st_size<=2**31-1)
    request={**request,'source_device':executable.st_dev,'executable_size':executable.st_size}
    with tempfile.TemporaryDirectory(prefix='ayu-installer-observation-') as temporary:
        private=Path(temporary).resolve();private.chmod(0o700)
        request={**request,'observation_root':str(private)}
        # This worker may hang in a filesystem syscall; its parent has no such
        # work and retires only this new process group within the outer budget.
        request_path=private/'request.json'
        with request_path.open('x') as stream:
            request_path.chmod(0o600);json.dump(request,stream)
        return worker_inner({**request,'request_path':str(request_path)})


def worker_inner(request):
    result = blank(); result['identity']['fixed_argv_context_verified'] = True
    deadline = time.monotonic() + 10
    try:
        with owned_device(request):pass
    except (OSError,ValueError,TypeError,plistlib.InvalidFileException):
        result['identity']['state']='rejected';return validate_observation(result)
    result['tool'] = tool_context()
    status, raw = command(['/bin/ps', '-p', str(request['pid']), '-o', 'pid=,ppid=,uid=,ucomm='], .8, max_bytes=4096)
    family = process_identity(raw, request['pid'], request['parent'], request['uid'],
        shell_wrapper_verified=result['tool']['run_first_launch_wrapper_observed']) if status == 'completed' else None
    if family is None:
        result['identity']['state'] = 'unavailable' if status != 'completed' else 'rejected'
    else:
        result['identity'].update(state='verified', parent_and_uid_verified=True, command_family=family)
        private = Path(request['observation_root'])
        sample = private / 'sample.txt'
        descriptor = os.open(sample, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created=os.fstat(descriptor)
        os.close(descriptor)
        status, _ = command([sys.executable, '-c', SAMPLE_EXEC, str(request['pid']), str(sample)], 5, max_bytes=4096)
        result['sample']['state'] = 'unavailable'
        try:
            if status == 'completed':
                descriptor = os.open(sample, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(descriptor); safe_info(info)
                    require((info.st_dev,info.st_ino,info.st_uid,info.st_mode,info.st_nlink)==
                            (created.st_dev,created.st_ino,created.st_uid,created.st_mode,created.st_nlink)
                            and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size < MAX_BYTES)
                    data = os.read(descriptor, MAX_BYTES + 1)
                    require(len(data) == info.st_size and identity(os.fstat(descriptor)) == identity(info))
                    result['sample'] = parse_sample(data, request['pid'], family)
                finally: os.close(descriptor)
        except (OSError,ValueError,TypeError):
            result['sample']=blank()['sample'];result['sample']['state']='rejected'
        finally:
            sample.unlink(missing_ok=True)
            result['raw_sample_removal_verified']=not os.path.lexists(sample)
    if time.monotonic() < deadline - 2.5:
        status, raw = command([sys.executable, str(Path(__file__).resolve()), '--snapshot', str(request['request_path'])], 1.75,
                              max_bytes=16384)
        if status == 'completed':
            value = strict_json(raw); require(set(value) == {'containers', 'host'})
            result.update(value)
        else: result['containers']['snapshot_state'] = 'timedOut' if status == 'timedOut' else 'unavailable'
    result['state'] = 'captured' if result['sample']['state'] == 'parsed' else 'partial'
    return validate_observation(result)


def capture(process, *, mapped_arguments, private_root, root_identity, identifier, device_name, command_started, timeout_seconds=120):
    """Called only by the stock timeout's kill hook; never signal the installer."""
    started = time.monotonic(); result = blank()
    try:
        require(type(timeout_seconds) is int and timeout_seconds == 120 and type(command_started) in (int, float)
                and math.isfinite(command_started) and started - command_started >= 120)
        require(type(process.pid) is int and 0 < process.pid < 2**31 and process.poll() is None)
        root = Path(private_root);require(root.is_absolute() and '..' not in root.parts)
        require(type(root_identity) in (tuple,list) and len(root_identity)==2)
        for item in root_identity:integer(item,2**63-1)
        require(type(identifier) is str and UUID.fullmatch(identifier) and type(device_name) is str
                and re.fullmatch(r'AYU_SMOKE_[0-9a-f]{32}', device_name))
        require(type(mapped_arguments) is list and len(mapped_arguments) == 7
                and mapped_arguments[:6] == ['xcrun', 'simctl', '--set', str(root), 'install', identifier])
        app = Path(mapped_arguments[-1]); require(app.name == 'Telegram.app' and app.is_absolute()
                and '..' not in app.parts)
        request={'pid':process.pid,'parent':os.getpid(),'uid':os.getuid(),'private_root':str(root),
                 'identifier':identifier,'device_name':device_name,'root_identity':list(root_identity),'app_path':str(app)}
        remaining=BUDGET_SECONDS-(time.monotonic()-started)-.75
        if remaining>0:
            status,raw=worker_command(json.dumps(request).encode(),remaining)
            if status=='payloadReceived':result=validate_observation(strict_json(raw))
        if process.poll() is None and result['containers']['snapshot_state']=='completed':
            result['containers']['captured_while_child_alive']=True
        elif process.returncode is not None:result['state']='childExited'
    except Exception:
        # Do not emit exception text or change the caller's pending SmokeError.
        result = blank()
    elapsed = round((time.monotonic() - started) * 1000)
    result['post_deadline_elapsed_ms'] = elapsed
    result['budget_exceeded'] = elapsed > BUDGET_SECONDS * 1000
    try: code = process.poll()
    except Exception: code = None
    result['post_observer_installer_exit_code'] = code if type(code) is int and -(2**31) <= code < 2**31 else None
    return validate_observation(result)


def private_request(path):
    path = Path(path); require(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat(); safe_info(info); require(stat.S_IMODE(info.st_mode) == 0o600)
    parent = path.parent.lstat(); safe_info(parent, directory=True); require(stat.S_IMODE(parent.st_mode) == 0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(identity(os.fstat(descriptor)) == identity(info) and 0 < info.st_size <= 8192)
        data = os.read(descriptor, 8193)
        require(len(data) == info.st_size and identity(os.fstat(descriptor)) == identity(info)
                and identity(path.lstat()) == identity(info))
    finally: os.close(descriptor)
    value = strict_json(data)
    keys(value, {'pid','parent','uid','private_root','identifier','device_name','root_identity','app_path',
                 'source_device','executable_size','observation_root'})
    require(value['observation_root'] == str(path.parent))
    return validate_request(value,snapshot_request=True)


def validate_request(value,*,snapshot_request=False):
    expected={'pid','parent','uid','private_root','identifier','device_name','root_identity','app_path'}
    if snapshot_request:expected|={'source_device','executable_size','observation_root'}
    keys(value,expected)
    for key in ('pid', 'parent'): integer(value[key], 2**31 - 1, 1)
    integer(value['uid'], 2**32 - 2); require(value['uid'] == os.getuid())
    require(type(value['identifier']) is str and UUID.fullmatch(value['identifier'])
            and type(value['device_name']) is str and re.fullmatch(r'AYU_SMOKE_[0-9a-f]{32}', value['device_name']))
    require(type(value['root_identity']) is list and len(value['root_identity']) == 2)
    for item in value['root_identity']: integer(item, 2**63 - 1)
    if snapshot_request:
        integer(value['source_device'], 2**63 - 1); integer(value['executable_size'], 2**31 - 1, 1)
    require(type(value['private_root']) is str and 0 < len(value['private_root']) < 4096
            and Path(value['private_root']).is_absolute() and not any(ord(c) < 32 for c in value['private_root']))
    require(type(value['app_path']) is str and 0<len(value['app_path'])<4096 and Path(value['app_path']).is_absolute()
            and not any(ord(c)<32 for c in value['app_path']))
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true'); parser.add_argument('--snapshot',type=Path)
    args = parser.parse_args()
    try:
        require(args.worker != (args.snapshot is not None))
        request=validate_request(strict_json(sys.stdin.buffer.read(4097))) if args.worker else private_request(args.snapshot)
        result=worker(request) if args.worker else snapshot(request)
        data = json.dumps(result, sort_keys=True).encode(); require(len(data) <= MAX_BYTES)
        sys.stdout.buffer.write(data + b'\n'); return 0
    except Exception:
        return 1


if __name__ == '__main__':
    sys.exit(main())
