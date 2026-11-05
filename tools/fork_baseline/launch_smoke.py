#!/usr/bin/env python3
"""Launch the built simulator app on a fresh disposable device, without an account.

Never resets an installed user's app. Evidence contains only closed startup
stages and synthetic first-launch screenshots/video; no authentication inputs.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import platform
import plistlib
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from ipa_inspect import inspect_ipa

BUNDLE = 'org.livara.ayugram'
READY = {'authorizationVisible', 'conversationsVisible'}
STAGES = READY | {'applicationEntered', 'windowCreated', 'sceneAttached', 'storageReady',
                  'accountManagerReady', 'sharedContextReady', 'authorizationPrepared',
                  'conversationsPrepared', 'failed'}
FAILURES = {'storageUnavailable', 'keychainUnavailable', 'storageConfiguration', 'initializationUnavailable'}
MARKER = 'Library/Application Support/AyuGram/LaunchDiagnostics/latest.json'


def extract_app(ipa, destination):
    inspect_ipa(ipa, 'simulator', BUNDLE)
    destination = Path(destination)
    with zipfile.ZipFile(ipa) as archive:
        infos = [n for n in archive.namelist() if n.startswith('Payload/') and n.endswith('.app/Info.plist') and n.count('/') == 2]
        if len(infos) != 1:
            raise ValueError('Expected one main application')
        prefix = infos[0][:-len('Info.plist')]
        total = 0
        for entry in archive.infolist():
            if not entry.filename.startswith(prefix):
                continue
            relative = PurePosixPath(entry.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename:
                raise ValueError('Unsafe bundle path')
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError('Unsupported bundle entry type')
            total += entry.file_size
            if total > 2 * 1024**3:
                raise ValueError('Bundle extraction limit exceeded')
            target = destination.joinpath(*relative.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('xb') as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                target.chmod(0o700 if mode & 0o111 else 0o600)
        info = plistlib.loads((destination / 'Info.plist').read_bytes())
        binary = info['CFBundleExecutable']
        if not isinstance(binary, str) or '/' in binary or binary in {'.', '..'}:
            raise ValueError('Invalid application executable')
        (destination / binary).chmod(0o700)
    return destination


def read_stage(path):
    # No diagnostic text, paths, tokens, user content or account IDs are retained.
    if path.is_symlink() or path.stat().st_size > 1024:
        raise ValueError('Invalid launch marker')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {'stage', 'failure'}:
        raise ValueError('Invalid launch marker fields')
    stage, failure = value['stage'], value['failure']
    if not isinstance(stage, str) or stage not in STAGES:
        raise ValueError('Invalid startup stage')
    if failure is not None and (not isinstance(failure, str) or failure not in FAILURES):
        raise ValueError('Invalid startup failure')
    return value


def command(arguments, timeout=120):
    return subprocess.check_output(arguments, stderr=subprocess.DEVNULL, text=True, timeout=timeout).strip()


def smoke(ipa, output):
    if platform.system() != 'Darwin':
        raise RuntimeError('A macOS host and the pinned simulator runtime are required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
              'authentication_codes_used': False, 'launches': [], 'passed': False}
    identifier, recorder = None, None
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-launch-smoke-') as temporary:
            app = extract_app(ipa, Path(temporary) / 'Telegram.app')
            identifier = command(['xcrun', 'simctl', 'create', 'AYU_SMOKE_' + uuid.uuid4().hex,
                                  'com.apple.CoreSimulator.SimDeviceType.iPhone-17',
                                  'com.apple.CoreSimulator.SimRuntime.iOS-27-0'])
            # Identifier is produced by simctl, never taken from user input.
            uuid.UUID(identifier)
            command(['xcrun', 'simctl', 'boot', identifier])
            command(['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=180)
            command(['xcrun', 'simctl', 'install', identifier, str(app)])
            with (output / 'recording.log').open('w') as recording_log:
                recorder = subprocess.Popen(['xcrun', 'simctl', 'io', identifier, 'recordVideo',
                                             '--codec=h264', '--force', str(output / 'launch.mp4')],
                                            stdout=recording_log, stderr=subprocess.STDOUT)
                for attempt in range(2):
                    if attempt:
                        command(['xcrun', 'simctl', 'terminate', identifier, BUNDLE])
                    data = Path(command(['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'data']))
                    marker = data / MARKER
                    # Only a diagnostics marker in this newly created device is reset.
                    marker.unlink(missing_ok=True)
                    command(['xcrun', 'simctl', 'launch', identifier, BUNDLE])
                    started = time.monotonic()
                    stage = {'stage': 'notObserved', 'failure': None}
                    while time.monotonic() - started < 60:
                        try:
                            stage = read_stage(marker)
                        except FileNotFoundError:
                            pass
                        if stage['failure'] is not None or stage['stage'] in READY:
                            break
                        time.sleep(0.5)
                    time.sleep(0.5)  # Allow the newly installed root's first display frame.
                    screenshot = output / ('launch-' + str(attempt + 1) + '.png')
                    command(['xcrun', 'simctl', 'io', identifier, 'screenshot', str(screenshot)])
                    visual = json.loads(command(['xcrun', 'swift', str(Path(__file__).with_name('screenshot_check.swift')), str(screenshot)]))
                    result = {'attempt': attempt + 1, 'marker': stage, 'screenshot': visual,
                              'seconds_to_marker': round(time.monotonic() - started, 2)}
                    report['launches'].append(result)
                    if stage['stage'] not in READY or stage['failure'] is not None or not visual['nonblank']:
                        raise RuntimeError('Startup did not reach visible application UI')
                report['passed'] = True
    finally:
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        if recorder is not None and recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
            try:
                recorder.wait(timeout=15)
            except subprocess.TimeoutExpired:
                recorder.kill()
                recorder.wait(timeout=5)
        if identifier is not None:
            for action in ('shutdown', 'delete'):
                try:
                    subprocess.run(['xcrun', 'simctl', action, identifier], capture_output=True, timeout=30)
                except subprocess.SubprocessError:
                    pass
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = smoke(args.ipa, args.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Startup smoke failed; inspect bounded local evidence.')
    print(json.dumps(result))
