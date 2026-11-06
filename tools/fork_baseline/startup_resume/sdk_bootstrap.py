#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Selected Apple SDK setup; schema 2 closed inspection reasons.
"""Initialize selected Xcode components and publish closed readiness facts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
import time

SOURCE = '5d241267735b3958c225681d173853568bab705d'
DEVELOPER = '/Applications/Xcode_27.1.app/Contents/Developer'
XCODEBUILD = DEVELOPER + '/usr/bin/xcodebuild'
SIMCTL = Path(DEVELOPER + '/usr/bin/simctl')
VERSION = 'Xcode 27.1\nBuild version 27A9275'
STAGES = {'notStarted', 'verifyHost', 'verifyVersion', 'checkBefore', 'initialize', 'checkAfter', 'inspectTool', 'complete'}
FAILURES = {'host', 'version', 'commandTimedOut', 'commandUnavailable', 'commandFailed', 'outputLimit', 'notReady', 'toolIdentity', 'internal'}
TOOL_REJECTION_REASONS = frozenset({'lookupMismatch', 'pathMismatch', 'type', 'owner', 'mode',
                                  'links', 'size', 'unstable', 'read', 'format', 'commandUnavailable'})


class Rejected(ValueError):
    def __init__(self, code):
        self.code = code if code in FAILURES else 'internal'
        super().__init__('Apple SDK readiness verification failed')


def require(value, code='internal'):
    if not value:
        raise Rejected(code)


def blank_tool():
    return {'state': 'unavailable', 'rejection_reason': None, 'xcrun_lookup_is_usr_bin': False,
            'selected_simctl_path_verified': False, 'root_owned_nonwritable': False,
            'stable_bytes_verified': False, 'file_kind': 'unavailable',
            'wrapper_mentions_first_launch': False}


def blank():
    return {'schema': 2, 'role': 'selectedAppleSDKInitialization', 'source': SOURCE,
            'application_recompiled': False, 'physical_device_verified': False,
            'stage': 'notStarted', 'failure': None, 'success': False,
            'host_verified': False, 'version_verified': False,
            'before_completed': False, 'before_exit_code': None,
            'initialization_attempted': False, 'initialization_completed': False,
            'initialization_exit_code': None, 'after_completed': False,
            'after_exit_code': None, 'tool_before': blank_tool(), 'tool_after': blank_tool()}


def validate_report(value):
    template = blank()
    require(type(value) is dict and set(value) == set(template))
    for key in ('schema', 'role', 'source', 'application_recompiled', 'physical_device_verified'):
        require(type(value[key]) is type(template[key]) and value[key] == template[key])
    require(type(value['stage']) is str and value['stage'] in STAGES)
    require(value['failure'] is None or type(value['failure']) is str and value['failure'] in FAILURES)
    for key, item in template.items():
        if type(item) is bool:
            require(type(value[key]) is bool)
    for phase, completed in (('before', 'before_completed'), ('initialization', 'initialization_completed'), ('after', 'after_completed')):
        code = value[phase + '_exit_code']
        require(code is None or type(code) is int and -(2**31) <= code < 2**31)
        require(value[completed] == (code is not None))
    if value['initialization_completed']:
        require(value['initialization_attempted'])
    for name in ('tool_before', 'tool_after'):
        tool = value[name]; schema = blank_tool()
        require(type(tool) is dict and set(tool) == set(schema))
        require(type(tool['state']) is str and tool['state'] in {'unavailable', 'observed', 'rejected'})
        require(type(tool['file_kind']) is str and tool['file_kind'] in {'unavailable', 'machO', 'shellWrapper'})
        reason = tool['rejection_reason']
        if tool['state'] == 'rejected':
            require(type(reason) is str and reason in TOOL_REJECTION_REASONS)
        else:
            require(reason is None)
        for key, item in schema.items():
            if type(item) is bool: require(type(tool[key]) is bool)
        if tool['state'] == 'observed':
            require(tool['selected_simctl_path_verified'] and tool['root_owned_nonwritable']
                    and tool['stable_bytes_verified'] and tool['file_kind'] != 'unavailable')
        if tool['wrapper_mentions_first_launch']:
            require(tool['state'] == 'observed' and tool['file_kind'] == 'shellWrapper')
    if value['success']:
        require(value['stage'] == 'complete' and value['failure'] is None
                and value['host_verified'] and value['version_verified']
                and value['initialization_attempted'] and value['initialization_completed']
                and value['initialization_exit_code'] == 0
                and value['after_completed'] and value['after_exit_code'] == 0)
    return value


def clean_environment():
    result = {key: os.environ[key] for key in ('HOME', 'PATH', 'TMPDIR') if key in os.environ}
    result['DEVELOPER_DIR'] = DEVELOPER
    return result


def private_command(arguments, timeout, directory, label):
    """Wait on the direct child, never on daemon-inherited pipe EOF."""
    path = Path(directory) / (label + '.private-log')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    process = None
    try:
        with os.fdopen(descriptor, 'wb') as output:
            process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, env=clean_environment())
            deadline = time.monotonic() + timeout
            # Cooperative output monitoring, not RLIMIT_FSIZE: that process-wide
            # limit could also prevent Apple's SDK component files being written.
            # A producer can overshoot between checks; retained reads stay capped.
            while True:
                require(path.stat().st_size <= 1024 * 1024, 'outputLimit')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Rejected('commandTimedOut')
                try:
                    code = process.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
        require(path.stat().st_size <= 1024 * 1024, 'outputLimit')
        with path.open('rb') as output:
            data = output.read(1024 * 1024 + 1)
        require(len(data) <= 1024 * 1024, 'outputLimit')
        return code, data
    except OSError:
        raise Rejected('commandUnavailable') from None
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass


def file_identity(value):
    return tuple(getattr(value, key) for key in ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def inspect_tool(runner, directory, label):
    result = blank_tool()
    reason = 'lookupMismatch'
    try:
        launcher = shutil.which('xcrun')
        result['xcrun_lookup_is_usr_bin'] = launcher is not None and Path(launcher).resolve(strict=True) == Path('/usr/bin/xcrun')
        # Preserve the existing advisory lookup flag and absolute runner path.
        # Reasons label the first rejected guard, never paths or exception text.
        reason = 'commandUnavailable'
        code, raw = runner(['/usr/bin/xcrun', '--find', 'simctl'], 15, directory, label)
        require(code == 0)
        reason = 'size'
        require(0 < len(raw) <= 4096)
        reason = 'pathMismatch'
        require(raw.decode('utf-8').strip() == str(SIMCTL))
        reason = 'read'
        before = SIMCTL.lstat()
        reason = 'type'; require(stat.S_ISREG(before.st_mode))
        reason = 'owner'; require(before.st_uid == 0)
        reason = 'mode'; require(not before.st_mode & 0o022)
        reason = 'links'; require(before.st_nlink == 1)
        reason = 'size'; require(0 < before.st_size <= 32 * 1024**2)
        reason = 'read'
        descriptor = os.open(SIMCTL, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            reason = 'unstable'; require(file_identity(opened) == file_identity(before))
            data = bytearray(); fingerprint = hashlib.sha256()
            while len(data) < before.st_size:
                reason = 'read'
                chunk = os.read(descriptor, min(1024**2, before.st_size - len(data)))
                require(chunk); data.extend(chunk); fingerprint.update(chunk)
            reason = 'read'
            after, current = os.fstat(descriptor), SIMCTL.lstat()
            reason = 'unstable'
            require(file_identity(after) == file_identity(before)
                    and file_identity(current) == file_identity(before))
            reason = 'format'
            require(len(fingerprint.digest()) == 32)  # Private fingerprint only.
        finally:
            try: os.close(descriptor)
            except OSError:
                reason = 'read'
                raise
        result.update(selected_simctl_path_verified=True, root_owned_nonwritable=True, stable_bytes_verified=True)
        reason = 'format'
        if data[:4] in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf'):
            result.update(state='observed', file_kind='machO')
        elif len(data) <= 65536 and bytes(data).splitlines()[0] in (b'#!/bin/bash', b'#!/bin/sh'):
            result.update(state='observed', file_kind='shellWrapper', wrapper_mentions_first_launch=b'-runFirstLaunch' in data)
        else: require(False)
    except (OSError, ValueError, TypeError, UnicodeError, IndexError):
        result.update(state='rejected', rejection_reason=reason)
    return result


def initialize(output, *, runner=private_command, probe=inspect_tool, host=None):
    report = blank()
    try:
        report['stage'] = 'verifyHost'
        host = host or (platform.system(), platform.machine(), platform.release().split('.')[0])
        require(host == ('Darwin', 'arm64', '27'), 'host')
        report['host_verified'] = True
        with tempfile.TemporaryDirectory(prefix='ayu-sdk-bootstrap-') as temporary:
            private = Path(temporary).resolve(strict=True)
            report['stage'] = 'verifyVersion'
            code, raw = runner([XCODEBUILD, '-version'], 30, private, 'version')
            require(code == 0 and raw.decode('utf-8').strip() == VERSION, 'version')
            code, raw = runner(['/usr/bin/xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], 30, private, 'sdk')
            require(code == 0 and raw.decode('utf-8').strip() == '27.1', 'version')
            report['version_verified'] = True
            report['tool_before'] = probe(runner, private, 'tool-before')
            report['stage'] = 'checkBefore'
            code, _ = runner([XCODEBUILD, '-checkFirstLaunchStatus'], 30, private, 'before')
            report.update(before_completed=True, before_exit_code=code)
            report['stage'] = 'initialize'; report['initialization_attempted'] = True
            code, _ = runner(['/usr/bin/sudo', '-n', XCODEBUILD, '-runFirstLaunch'], 300, private, 'initialize')
            report.update(initialization_completed=True, initialization_exit_code=code)
            require(code == 0, 'commandFailed')
            report['stage'] = 'checkAfter'
            code, _ = runner([XCODEBUILD, '-checkFirstLaunchStatus'], 30, private, 'after')
            report.update(after_completed=True, after_exit_code=code)
            require(code == 0, 'notReady')
            report['stage'] = 'inspectTool'; report['tool_after'] = probe(runner, private, 'tool-after')
            # File inspection is diagnostic only. Missing/unsupported metadata
            # must not be relabelled as verified, nor negate actual readiness.
            report.update(stage='complete', success=True)
    except Rejected as error:
        report['failure'] = error.code
    except Exception:
        report['failure'] = 'internal'
    validate_report(report)
    output = Path(output).absolute()
    require(output.parent.resolve(strict=True) == output.parent and not output.is_symlink())
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(report, target, sort_keys=True, indent=2); target.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = initialize(args.output)
    except Exception:
        print('Apple SDK readiness evidence unavailable.', file=__import__('sys').stderr)
        return 1
    print('Selected Apple SDK initialized.' if result['success'] else 'Selected Apple SDK readiness not verified.')
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
