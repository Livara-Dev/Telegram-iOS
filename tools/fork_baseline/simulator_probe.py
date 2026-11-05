"""Bounded, closed-schema diagnostics for the synthetic projection host only."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

BUNDLE = 'org.livara.ayugram.tests.native-security.projection'
MARKER = 'Library/Caches/AyuGramNativeTestHost/projection-lifecycle.json'
STAGES = {'didFinish', 'sceneVisible'}


def read_marker(container):
    if container is None:
        return {'state': 'unavailable'}
    path = Path(container) / MARKER
    try:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            return {'state': 'unsafe'}
        # O_NONBLOCK also bounds unexpected FIFO/device paths before the regular
        # file check; the marker is synthetic and must never block collection.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 128:
                return {'state': 'invalid'}
            data = os.read(descriptor, 129)
        finally:
            os.close(descriptor)
        value = json.loads(data)
        if len(data) > 128 or not isinstance(value, dict) or set(value) != {'stage'} or not isinstance(value['stage'], str) or value['stage'] not in STAGES:
            return {'state': 'invalid'}
        return {'state': 'observed', 'stage': value['stage']}
    except FileNotFoundError:
        return {'state': 'absent'}
    except (OSError, ValueError, TypeError):
        return {'state': 'unavailable'}


def capture(root, secrets):
    # Kept for the existing caller contract. No raw command or marker payload is
    # copied, so redacting an open-ended diagnostic string is no longer needed.
    del secrets
    root = Path(root)
    destination = root / 'build/fork-evidence'
    report = {'probe_only_not_test_result': True, 'bundle': BUNDLE, 'commands': {}}
    try:
        if (root / 'build').is_symlink() or destination.is_symlink():
            return
        destination.mkdir(parents=True, exist_ok=True)
        def command(name, args):
            try:
                result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
                report['commands'][name] = {'exit_code': result.returncode}
                return result.returncode, result.stdout[:65536].decode('utf-8', errors='replace')
            except (OSError, subprocess.SubprocessError):
                report['commands'][name] = {'state': 'unavailable'}
                return None, ''
        code, data = command('devices', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'])
        if code == 0:
            devices = json.loads(data).get('devices', {})
            candidates = [d for runtime, values in devices.items() if runtime.endswith('iOS-27-0')
                          for d in values if d.get('name') == 'BAZEL_TEST_iPhone 17_27.0' and d.get('state') == 'Booted']
            report['commands']['devices']['matching_booted_devices'] = len(candidates)
            if len(candidates) == 1 and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', candidates[0].get('udid', '')):
                identifier = candidates[0]['udid']
                code, container = command('installed_host', ['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'app'])
                container = container.strip()
                if code == 0 and container and '\0' not in container and Path(container).is_absolute() and Path(container).suffix == '.app' and Path(container).is_dir() and not Path(container).is_symlink():
                    report['commands']['installed_host']['path_validated'] = True
                    data_code, data_container = command('host_data_container', ['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'data'])
                    data_container = data_container.strip()
                    valid_data = (data_code == 0 and data_container and '\0' not in data_container and
                                  Path(data_container).is_absolute() and Path(data_container).is_dir() and not Path(data_container).is_symlink())
                    data_path = Path(data_container) if valid_data else None
                    report['commands']['host_data_container']['path_validated'] = bool(valid_data)
                    # Capture XCTest's host state before a standalone launch can
                    # create/overwrite its marker and obscure the distinction.
                    report['marker_before_standalone_launch'] = read_marker(data_path)
                    command('installed_signature', ['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', container])
                    # Strict sealing can differ after XCTest bundle injection;
                    # neither this check nor standalone launch proves test entry.
                    launch_code, _ = command('standalone_host_launch', ['xcrun', 'simctl', 'launch', '--terminate-running-process', identifier, BUNDLE])
                    marker = read_marker(data_path)
                    deadline = time.monotonic() + 2
                    while launch_code == 0 and data_path is not None and marker.get('stage') != 'sceneVisible' and time.monotonic() < deadline:
                        time.sleep(0.1)
                        marker = read_marker(data_path)
                    report['marker_after_standalone_launch'] = marker
        with (destination / 'projection-launch-probe.json').open('x') as file:
            json.dump(report, file, indent=2)
            file.write('\n')
    except (OSError, ValueError, TypeError, KeyError):
        # Diagnostic collection never replaces the original compiler/test failure.
        return
