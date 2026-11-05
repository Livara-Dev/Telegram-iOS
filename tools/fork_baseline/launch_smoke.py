#!/usr/bin/env python3
"""Launch the built simulator app on a fresh disposable device, without an account.

Never resets an installed user's app. Evidence contains only closed startup
stages and synthetic first-launch screenshots/video; no authentication inputs.
"""
import argparse
import json
import os
import re
import selectors
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
STORAGE_SELECTION = 'org.livara.ayugram.startup-storage.v1'
PHASES = {'extractApplication', 'compileScreenshotChecker', 'testScreenshotChecker',
          'createDevice', 'bootDevice', 'waitForBoot',
          'installApplication', 'startRecording', 'launchApplication', 'getContainer',
          'waitForInterface', 'captureScreenshot', 'checkScreenshot', 'checkStorageSelection', 'complete',
          'shutdownDevice', 'deleteDevice', 'stopRecording', 'unknownPhase'}
SMOKE_CODES = {'commandTimedOut', 'commandFailed', 'commandUnavailable', 'commandOutputLimit',
               'invalidCommandOutput', 'interfaceNotVisible', 'packageExtractionFailed',
               'invalidLaunchMarker', 'invalidScreenshotEvidence', 'invalidStorageEvidence',
               'recordingUnavailable', 'recordingStopFailed', 'startupPreparationFailed'}


class SmokeError(RuntimeError):
    def __init__(self, code, phase, return_code=None, timeout=False, system_errors=None):
        code = code if isinstance(code, str) and code in SMOKE_CODES else 'startupPreparationFailed'
        super().__init__(code)
        self.code = code
        self.phase = phase if isinstance(phase, str) and phase in PHASES else 'unknownPhase'
        self.return_code = return_code if type(return_code) is int and -(2**31) <= return_code < 2**31 else None
        self.timeout = timeout is True
        self.system_errors = [item for item in (system_errors or []) if isinstance(item, dict)
                              and set(item) == {'domain', 'code'} and isinstance(item['domain'], str)
                              and item['domain'] in ERROR_DOMAINS
                              and type(item['code']) is int and -(2**31) <= item['code'] < 2**31][:4]

    def evidence(self):
        return {'code': self.code, 'phase': self.phase,
                'return_code': self.return_code, 'timeout': self.timeout, 'system_errors': self.system_errors}



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


ERROR_DOMAINS = {'CoreSimulator.SimError', 'com.apple.CoreSimulator.SimError',
                 'MIInstallerErrorDomain', 'IXUserPresentableErrorDomain',
                 'FBSOpenApplicationServiceErrorDomain'}
COMMAND_OUTPUT_LIMIT = 64 * 1024


def safe_system_errors(data):
    # Ignore all prose, paths, UUIDs and unknown domains. Retain at most four
    # known Apple error identifiers with numeric codes from bounded stderr.
    text = data[:COMMAND_OUTPUT_LIMIT].decode('utf-8', errors='replace')
    pattern = r'(?:domain=|Domain=)([A-Za-z0-9.]+)[, ]+(?:code=|Code=)(-?\d{1,10})(?!\d)'
    errors = []
    for domain, number in re.findall(pattern, text):
        value = int(number)
        item = {'domain': domain, 'code': value}
        if domain in ERROR_DOMAINS and -(2**31) <= value < 2**31 and item not in errors:
            errors.append(item)
        if len(errors) == 4: break
    return errors


def write_compiler_log(path, data, arguments):
    """Retain only bounded diagnostics from the fixed public host helper."""
    path = Path(path)
    if path.name != 'checker-compiler.log' or path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Invalid compiler diagnostics destination')
    text = data[:COMMAND_OUTPUT_LIMIT].decode('utf-8', errors='replace')
    prefixes = {}
    for root, label in ((Path(__file__).resolve().parents[2], '<SOURCE_ROOT>'),
                        (Path.home(), '<HOME_ROOT>'),
                        (Path(arguments[-1]).parent, '<CHECKER_TEMP>'),
                        (Path(tempfile.gettempdir()), '<TEMP_ROOT>')):
        for candidate in (root, root.resolve()):
            if candidate.is_absolute() and str(candidate) != '/': prefixes[str(candidate)] = label
    for prefix in sorted(prefixes, key=len, reverse=True):
        text = text.replace(prefix, prefixes[prefix])
    # The fixed helper has no credentials. Also fail closed on recognizable
    # credential assignments/tokens if a tool ever prints unexpected context.
    text = re.sub(r'(?im)^.*\b(?:GH_TOKEN|GITHUB_TOKEN|TELEGRAM_API_HASH|TELEGRAM_API_ID|PASSWORD|AUTHORIZATION|SESSION_TOKEN)\b\s*[:=].*$',
                  '<REDACTED_CREDENTIAL>', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b',
                  '<REDACTED_CREDENTIAL>', text)
    with path.open('wb') as output:
        path.chmod(0o600)
        output.write(text.encode('utf-8')[:COMMAND_OUTPUT_LIMIT])


def command(arguments, timeout=120, phase='extractApplication', compiler_log=None):
    if phase not in PHASES:
        raise ValueError('Unknown startup command phase')
    if compiler_log is not None:
        public_compiler = ['xcrun', 'swiftc', '-O', '-framework', 'CoreGraphics', '-framework', 'ImageIO',
                           str(Path(__file__).with_name('screenshot_check.swift')), '-o']
        if phase != 'compileScreenshotChecker' or arguments[:-1] != public_compiler or len(arguments) != 10:
            raise ValueError('Only the fixed public checker compiler can retain diagnostics')
    process = None
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SmokeError('commandTimedOut', phase, timeout=True,
                                     system_errors=safe_system_errors(captured['stderr']))
                for key, _ in selector.select(min(0.2, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = captured[key.data]
                    room = COMMAND_OUTPUT_LIMIT - len(target)
                    target.extend(chunk[:room])
                    if len(chunk) > room:
                        raise SmokeError('commandOutputLimit', phase,
                                         system_errors=safe_system_errors(captured['stderr']))
        try:
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SmokeError('commandTimedOut', phase, timeout=True,
                             system_errors=safe_system_errors(captured['stderr'])) from None
        if code:
            raise SmokeError('commandFailed', phase, return_code=code,
                             system_errors=safe_system_errors(captured['stderr']))
        return captured['stdout'].decode('utf-8').strip()
    except OSError:
        raise SmokeError('commandUnavailable', phase) from None
    finally:
        if process is not None:
            if process.poll() is None:
                try: process.kill()
                except OSError: pass
            try: process.wait(timeout=5)
            except subprocess.SubprocessError: pass
            if process.stdout is not None: process.stdout.close()
            if process.stderr is not None: process.stderr.close()
        if compiler_log is not None:
            write_compiler_log(compiler_log, captured['stderr'], arguments)


def read_storage_kind(path, expected='private'):
    """Read fresh-app preferences without retaining any other key or value."""
    if expected != 'private' or path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid storage selection evidence')
    preferences = plistlib.loads(path.read_bytes())
    if not isinstance(preferences, dict):
        raise ValueError('Invalid storage selection evidence')
    selection = preferences.get(STORAGE_SELECTION)
    if (not isinstance(selection, dict) or set(selection) != {'version', 'kind'} or
            type(selection.get('version')) is not int or selection['version'] != 1 or
            selection.get('kind') != expected):
        raise ValueError('Expected private storage was not observed')
    return 'private'


def prepare_screenshot_checker(directory, compiler_log=None):
    """Compile once before any simulator boot; run native synthetic controls."""
    binary = Path(directory) / 'screenshot-check'
    command(['xcrun', 'swiftc', '-O', '-framework', 'CoreGraphics', '-framework', 'ImageIO',
             str(Path(__file__).with_name('screenshot_check.swift')), '-o', str(binary)],
            timeout=300, phase='compileScreenshotChecker', compiler_log=compiler_log)
    if binary.is_symlink() or not binary.is_file() or not 0 < binary.stat().st_size <= 20 * 1024**2:
        raise SmokeError('invalidScreenshotEvidence', 'compileScreenshotChecker')
    try:
        result = json.loads(command([str(binary), '--self-test'], timeout=30, phase='testScreenshotChecker'))
    except ValueError:
        raise SmokeError('invalidScreenshotEvidence', 'testScreenshotChecker') from None
    if (not isinstance(result, dict) or set(result) != {'self_test_passed', 'cases'} or
            result['self_test_passed'] is not True or type(result['cases']) is not int or result['cases'] != 3):
        raise SmokeError('invalidScreenshotEvidence', 'testScreenshotChecker')
    return binary


def persist_report(output, report):
    """Persist only the closed evidence schema, including incomplete attempts."""
    temporary = Path(output) / '.result.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(Path(output) / 'result.json')


def smoke(ipa, output, expected_storage_kind=None):
    if expected_storage_kind not in {None, 'private'}:
        raise ValueError('Unsupported storage selection expectation')
    if platform.system() != 'Darwin':
        raise RuntimeError('A macOS host and the pinned simulator runtime are required')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'mode': 'fresh_simulator_launch', 'physical_device_verified': False,
              'authentication_codes_used': False, 'launches': [], 'passed': False,
              'phase': 'extractApplication', 'failure': None}
    if expected_storage_kind is not None:
        report['expected_storage_kind'] = expected_storage_kind
    identifier, recorder = None, None
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-launch-smoke-') as temporary:
            app = extract_app(ipa, Path(temporary) / 'Telegram.app')
            report['phase'] = 'compileScreenshotChecker'
            checker = prepare_screenshot_checker(temporary, compiler_log=output / 'checker-compiler.log')
            report['screenshot_checker'] = {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3}
            report['phase'] = 'createDevice'
            identifier = command(['xcrun', 'simctl', 'create', 'AYU_SMOKE_' + uuid.uuid4().hex,
                                  'com.apple.CoreSimulator.SimDeviceType.iPhone-17',
                                  'com.apple.CoreSimulator.SimRuntime.iOS-27-0'], phase='createDevice')
            # Identifier is produced by simctl, never taken from user input.
            try:
                uuid.UUID(identifier)
            except ValueError:
                identifier = None
                raise SmokeError('invalidCommandOutput', 'createDevice') from None
            report['phase'] = 'bootDevice'
            command(['xcrun', 'simctl', 'boot', identifier], phase='bootDevice')
            report['phase'] = 'waitForBoot'
            command(['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=180, phase='waitForBoot')
            report['phase'] = 'installApplication'
            command(['xcrun', 'simctl', 'install', identifier, str(app)], phase='installApplication')
            report['phase'] = 'startRecording'
            with (output / 'recording.log').open('w') as recording_log:
                recording_log.write('{"state":"requested"}\n')
                recorder = subprocess.Popen(['xcrun', 'simctl', 'io', identifier, 'recordVideo',
                                             '--codec=h264', '--force', str(output / 'launch.mp4')],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for attempt in range(2):
                    if attempt:
                        report['phase'] = 'launchApplication'
                        command(['xcrun', 'simctl', 'terminate', identifier, BUNDLE], phase='launchApplication')
                    report['phase'] = 'getContainer'
                    data = Path(command(['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'data'], phase='getContainer'))
                    if not data.is_absolute():
                        raise SmokeError('invalidCommandOutput', 'getContainer')
                    marker = data / MARKER
                    # Only a diagnostics marker in this newly created device is reset.
                    marker.unlink(missing_ok=True)
                    report['phase'] = 'launchApplication'
                    command(['xcrun', 'simctl', 'launch', identifier, BUNDLE], phase='launchApplication')
                    report['phase'] = 'waitForInterface'
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
                    result = {'attempt': attempt + 1, 'marker': stage,
                              'seconds_to_marker': round(time.monotonic() - started, 2)}
                    report['launches'].append(result)
                    persist_report(output, report)
                    if stage['stage'] not in READY or stage['failure'] is not None:
                        raise SmokeError('interfaceNotVisible', 'waitForInterface')
                    if expected_storage_kind is not None:
                        report['phase'] = 'checkStorageSelection'
                        result['storage_kind'] = 'unverified'
                        preferences = data / 'Library/Preferences' / (BUNDLE + '.plist')
                        result['storage_kind'] = read_storage_kind(preferences, expected_storage_kind)
                        persist_report(output, report)
                    time.sleep(0.5)  # Allow the newly installed root's first display frame.
                    screenshot = output / ('launch-' + str(attempt + 1) + '.png')
                    report['phase'] = 'captureScreenshot'
                    command(['xcrun', 'simctl', 'io', identifier, 'screenshot', str(screenshot)], phase='captureScreenshot')
                    report['phase'] = 'checkScreenshot'
                    visual = json.loads(command([str(checker), str(screenshot)], timeout=30, phase='checkScreenshot'))
                    if (not isinstance(visual, dict) or set(visual) != {'width', 'height', 'nonblank'} or
                            type(visual.get('nonblank')) is not bool or
                            any(type(visual.get(key)) is not int or not 0 < visual[key] <= 16384 for key in ('width', 'height'))):
                        raise SmokeError('invalidScreenshotEvidence', 'checkScreenshot')
                    result['screenshot'] = visual
                    persist_report(output, report)
                    if not visual['nonblank']:
                        raise SmokeError('interfaceNotVisible', 'waitForInterface')
                report['passed'] = True
                report['phase'] = 'complete'
    except SmokeError as error:
        report['phase'] = error.phase
        report['failure'] = error.evidence()
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        codes = {'extractApplication': 'packageExtractionFailed',
                 'waitForInterface': 'invalidLaunchMarker',
                 'checkScreenshot': 'invalidScreenshotEvidence',
                 'checkStorageSelection': 'invalidStorageEvidence',
                 'startRecording': 'recordingUnavailable'}
        error = SmokeError(codes.get(report['phase'], 'startupPreparationFailed'), report['phase'])
        report['failure'] = error.evidence()
        raise error from None
    finally:
        try:
            if recorder is not None and recorder.poll() is None:
                recorder.send_signal(signal.SIGINT)
                try:
                    recorder.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    recorder.kill()
                    recorder.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            report.setdefault('cleanup_failures', []).append(
                SmokeError('recordingStopFailed', 'stopRecording').evidence())
        if recorder is not None:
            report['recording_return_code'] = recorder.poll()
        if identifier is not None:
            for action, phase in (('shutdown', 'shutdownDevice'), ('delete', 'deleteDevice')):
                try:
                    command(['xcrun', 'simctl', action, identifier], timeout=30, phase=phase)
                except SmokeError as error:
                    report.setdefault('cleanup_failures', []).append(error.evidence())
        persist_report(output, report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = smoke(args.ipa, args.output)
    except SmokeError as error:
        print(json.dumps({'mode': 'fresh_simulator_launch', 'passed': False,
                          'phase': error.phase, 'failure': error.evidence(),
                          'physical_device_verified': False}))
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Startup smoke failed; inspect bounded local evidence.')
    print(json.dumps(result))
