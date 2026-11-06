"""Bounded, closed-schema diagnostics for the synthetic projection host only."""
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
from projection_crash import capture as capture_crashes
from custom_options_smoke import EXPORT_ENVIRONMENT_KEYS, redact_export_stderr
from launch_smoke import COMMAND_OUTPUT_LIMIT, POST_EXIT_DRAIN_SECONDS

BUNDLE = 'org.livara.ayugram.tests.native-security.projection'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE_NAME = 'BAZEL_TEST_iPhone 17_27.0'
REPLAY_PROFILE = 'replay-iphone15pro'
REPLAY_DEVICE_NAME = 'AYU_REPLAY_iPhone 15 Pro_27.0'
REPLAY_DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
IDENTITY_ROW_COUNT_LIMIT = 128
MARKER = 'Library/Caches/AyuGramNativeTestHost/projection-lifecycle.json'
STAGES = {'didFinish', 'sceneVisible'}
MARKER_STATES = {'observed', 'absent', 'unsafe', 'invalid', 'unavailable'}
BOOTSTRAP_PROCESSES = {'testmanagerd', 'runningboardd', 'xctest', 'launchd_sim'}
BOOTSTRAP_IDENTITIES = (BUNDLE, 'ProjectionTestHost', 'ChatLocalTextFilterProjectionTests')
BOOTSTRAP_PREDICATE = (
    '(process == "testmanagerd" OR process == "runningboardd" OR process == "xctest" OR process == "launchd_sim") AND '
    '(eventMessage CONTAINS[c] "org.livara.ayugram.tests.native-security.projection" OR '
    'eventMessage CONTAINS[c] "ProjectionTestHost" OR eventMessage CONTAINS[c] "ChatLocalTextFilterProjectionTests")'
)


def redact_probe_text(data, secrets):
    # Reuse the existing bounded CI-tool redactor. Explicit caller credentials
    # may have been removed from this process's environment before diagnostics.
    text = data[:COMMAND_OUTPUT_LIMIT].decode('utf-8', errors='replace')
    for value in sorted((value for value in secrets if isinstance(value, str) and value), key=len, reverse=True):
        text = text.replace(value, '<REDACTED_CREDENTIAL>')
    text = redact_export_stderr(text.encode('utf-8'), '<UNUSED_SYNTHETIC_RESULT>', '<UNUSED_PRIVATE_EXPORT>')
    # JSON escaping can expand Unicode/control characters. Bound the retained
    # representation, not only Python character count or raw pipe input.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(text[:middle], ensure_ascii=True)) <= 16 * 1024:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def diagnostic_command(arguments, secrets, timeout=20):
    """Bound the fixed read-only probes; never change test outcomes.

    Stdout remains transient for closed-schema log selection. Only redacted
    stderr and fixed status/counts can be retained on cap/timeout/parse failure.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 20:
        raise ValueError('Invalid diagnostic timeout')
    process = None
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    status, code = 'unavailable', None
    deadline = time.monotonic() + timeout
    try:
        child = {key: value for key, value in os.environ.items() if key in EXPORT_ENVIRONMENT_KEYS}
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            drain_deadline = None
            while selector.get_map():
                code = process.poll()
                now = time.monotonic()
                if code is not None and drain_deadline is None:
                    drain_deadline = min(deadline, now + POST_EXIT_DRAIN_SECONDS)
                remaining = (drain_deadline if drain_deadline is not None else deadline) - now
                if remaining <= 0:
                    if process.poll() is None: status = 'timedOut'
                    break
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = COMMAND_OUTPUT_LIMIT - sum(len(value) for value in captured.values())
                    captured[key.data].extend(chunk[:room])
                    if len(chunk) > room:
                        status = 'outputLimit'
                        break
                if status == 'outputLimit': break
        if status not in {'timedOut', 'outputLimit'}:
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
                status = 'completed' if code == 0 else 'commandFailed'
            except subprocess.TimeoutExpired:
                status, code = 'timedOut', process.poll()
    except OSError:
        status = 'unavailable'
    finally:
        if process is not None:
            if process.poll() is None:
                try: process.kill()
                except OSError: pass
            try: process.wait(timeout=2)
            except subprocess.SubprocessError: pass
            for pipe in (process.stdout, process.stderr):
                if pipe is not None: pipe.close()
    return ({'status': status, 'exit_code': code, 'timeout': status == 'timedOut',
             'output_limit': status == 'outputLimit', 'stdout_bytes': len(captured['stdout']),
             'stderr_bytes': len(captured['stderr']), 'stderr': redact_probe_text(bytes(captured['stderr']), secrets)},
            bytes(captured['stdout']))


def bootstrap_events(data, secrets):
    """Keep only fixed synthetic-host messages from the approved processes."""
    try:
        if len(data) > COMMAND_OUTPUT_LIMIT: raise ValueError('Log input limit')
        records = json.loads(data)
        if not isinstance(records, list): raise ValueError('Log payload must be an array')
        events, omitted, retained_bytes = [], max(0, len(records) - 128), 2
        for record in records[:128]:
            if not isinstance(record, dict): continue
            image = record.get('processImagePath')
            process = Path(image).name if isinstance(image, str) else record.get('process')
            message = record.get('eventMessage')
            if (process not in BOOTSTRAP_PROCESSES or not isinstance(message, str) or
                    not any(identity.casefold() in message.casefold() for identity in BOOTSTRAP_IDENTITIES)):
                continue
            if len(events) == 32:
                omitted += 1
                continue
            event = {'process': process, 'message': redact_probe_text(message.encode('utf-8'), secrets)}
            if record.get('messageType') in {'Default', 'Info', 'Debug', 'Error', 'Fault'}:
                event['message_type'] = record['messageType']
            stamp = record.get('timestamp')
            if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:[+-]\d{4})?', stamp):
                event['timestamp'] = stamp
            count = len(json.dumps(event, ensure_ascii=True)) + 2
            # Leave room for the independently capped stderr plus fixed status
            # fields within the 64 KiB per-command retained evidence budget.
            if retained_bytes + count > 44 * 1024:
                omitted += 1
                continue
            events.append(event)
            retained_bytes += count
        return {'state': 'parsed', 'events': events, 'records_seen': min(len(records), 128), 'records_omitted': omitted}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {'state': 'invalidPayload', 'events': []}


def read_marker(container):
    if container is None:
        return {'state': 'unavailable'}
    try:
        approved_root = Path(container)
        if approved_root.is_symlink():
            return {'state': 'unsafe'}
        # simctl approved this data root. Canonicalize OS aliases above it
        # (notably macOS /var -> /private/var), then enforce the boundary only
        # on the fixed marker descendants. Never follow a link inside it.
        path = approved_root.resolve(strict=True)
        if not path.is_dir():
            return {'state': 'unavailable'}
        for component in Path(MARKER).parts:
            path = path / component
            if path.is_symlink():
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


def capture(root, secrets, started_at=None, *, owned_identifier=None, profile=None):
    root = Path(root)
    destination = root / 'build/fork-evidence'
    report = {'probe_only_not_test_result': True, 'bundle': BUNDLE, 'commands': {}}
    replay = owned_identifier is not None or profile is not None
    valid_replay = (isinstance(profile, str) and profile == REPLAY_PROFILE and
                    isinstance(owned_identifier, str) and
                    re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', owned_identifier))
    if replay:
        report.update(profile=REPLAY_PROFILE, device_type=REPLAY_DEVICE_TYPE,
                      runtime=RUNTIME, device_identity={'state': 'unknown', 'owned_uuid_rows': None,
                          'owned_uuid_rows_capped': None, 'name_matches': None, 'device_type_matches': None,
                          'runtime_matches': None, 'booted': None},
                      marker_availability={'before_standalone_launch': None, 'after_standalone_launch': None})
    try:
        if (root / 'build').is_symlink() or destination.is_symlink():
            return
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        def write_report():
            descriptor = os.open(destination / 'projection-launch-probe.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                json.dump(report, file, indent=2)
                file.write('\n')
        if replay and not valid_replay:
            write_report()
            return
        if replay:
            secrets = [*secrets, owned_identifier, owned_identifier.lower(), owned_identifier.upper()]
        # Snapshot the failing XCTest host before a standalone launch changes
        # lifecycle state or generates an unrelated later crash report.
        report['crash_summary_before_standalone_launch'] = capture_crashes(started_at)
        def command(name, args):
            try:
                result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
                report['commands'][name] = {'exit_code': result.returncode}
                return result.returncode, result.stdout[:65536].decode('utf-8', errors='replace')
            except (OSError, subprocess.SubprocessError):
                report['commands'][name] = {'state': 'unavailable'}
                return None, ''
        inventory, data = diagnostic_command(['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], secrets, timeout=10)
        # Keep the bounded/redacted failure metadata even when CoreSimulator
        # inventory cannot complete. No raw device inventory is published.
        report['commands']['devices'] = inventory
        if inventory['status'] == 'completed' and inventory['exit_code'] == 0:
            try:
                if replay and (not isinstance(data, bytes) or len(data) > COMMAND_OUTPUT_LIMIT):
                    raise ValueError('Invalid device inventory size')
                payload = json.loads(data)
                devices = payload.get('devices') if isinstance(payload, dict) else None
                if replay:
                    if not isinstance(devices, dict): raise ValueError('Invalid device inventory')
                    owned_rows = []
                    for runtime, values in devices.items():
                        if not isinstance(runtime, str) or not isinstance(values, list):
                            raise ValueError('Invalid device inventory')
                        for device in values:
                            if not isinstance(device, dict) or not isinstance(device.get('udid'), str):
                                raise ValueError('Invalid device row')
                            if device['udid'].casefold() == owned_identifier.casefold():
                                owned_rows.append((runtime, device))
                    identity = report['device_identity']
                    # A row count describes this bounded snapshot. Missing or
                    # ambiguous rows cannot establish the device's identity.
                    identity.update(owned_uuid_rows=min(len(owned_rows), IDENTITY_ROW_COUNT_LIMIT),
                                    owned_uuid_rows_capped=len(owned_rows) > IDENTITY_ROW_COUNT_LIMIT)
                    candidates = []
                    if len(owned_rows) == 1:
                        runtime, device = owned_rows[0]
                        identity.update(name_matches=device.get('name') == REPLAY_DEVICE_NAME,
                                        device_type_matches=device.get('deviceTypeIdentifier') == REPLAY_DEVICE_TYPE,
                                        runtime_matches=runtime == RUNTIME, booted=device.get('state') == 'Booted')
                        if all(identity[field] for field in ('name_matches', 'device_type_matches', 'runtime_matches', 'booted')):
                            candidates = [device]
                else:
                    values = devices.get(RUNTIME, []) if isinstance(devices, dict) else None
                    if not isinstance(values, list): raise ValueError('Invalid device inventory')
                    candidates = [d for d in values if isinstance(d, dict)
                                  and d.get('name') == DEVICE_NAME and d.get('state') == 'Booted']
            except (ValueError, TypeError, UnicodeError, RecursionError):
                candidates = []
                inventory['payload_state'] = 'invalid'
            report['commands']['devices']['matching_booted_devices'] = len(candidates)
            if (len(candidates) == 1 and isinstance(candidates[0].get('udid'), str)
                    and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', candidates[0]['udid'])):
                identifier = owned_identifier if replay else candidates[0]['udid']
                if replay:
                    report['device_identity']['state'] = 'verified'
                # Installation/path validation can fail during XCTest bootstrap.
                # Read selected logs for the verified owned device first so
                # those failures do not suppress the most useful evidence.
                diagnostics = report['diagnostics'] = {}
                log, stdout = diagnostic_command(['xcrun', 'simctl', 'spawn', identifier, 'log', 'show',
                    '--style', 'json', '--last', '15m', '--info', '--debug', '--predicate', BOOTSTRAP_PREDICATE], secrets)
                diagnostics['test_bootstrap_log'] = log
                report['commands']['test_bootstrap_log'] = {'exit_code': log['exit_code']}
                report['bootstrap_events_before_standalone_launch'] = (
                    {'state': 'unavailable', 'reason': log['status'], 'events': []}
                    if log['status'] in {'outputLimit', 'timedOut', 'unavailable'} else bootstrap_events(stdout, secrets))
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
                    if replay:
                        marker_state = report['marker_before_standalone_launch'].get('state')
                        if marker_state in MARKER_STATES:
                            report['marker_availability']['before_standalone_launch'] = marker_state
                    signature, _ = diagnostic_command(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', container], secrets, timeout=10)
                    diagnostics['installed_signature'] = signature
                    report['commands']['installed_signature'] = {'exit_code': signature['exit_code']}
                    # Strict sealing can differ after XCTest bundle injection;
                    # neither this check nor standalone launch proves test entry.
                    launch_code, _ = command('standalone_host_launch', ['xcrun', 'simctl', 'launch', '--terminate-running-process', identifier, BUNDLE])
                    marker = read_marker(data_path)
                    deadline = time.monotonic() + 2
                    while launch_code == 0 and data_path is not None and marker.get('stage') != 'sceneVisible' and time.monotonic() < deadline:
                        time.sleep(0.1)
                        marker = read_marker(data_path)
                    report['marker_after_standalone_launch'] = marker
                    if replay and marker.get('state') in MARKER_STATES:
                        report['marker_availability']['after_standalone_launch'] = marker['state']
        write_report()
    except (OSError, ValueError, TypeError, KeyError):
        # Diagnostic collection never replaces the original compiler/test failure.
        return
