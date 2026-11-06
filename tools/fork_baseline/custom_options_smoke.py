"""Bounded visual evidence for the synthetic production-renderer host only."""
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from launch_smoke import (command, prepare_screenshot_checker, SmokeError,
                          COMMAND_OUTPUT_LIMIT, POST_EXIT_DRAIN_SECONDS)

BUNDLE = 'org.livara.ayugram.tests.custome-options'
DEVICE_NAME = 'BAZEL_TEST_iPhone 15 Pro_27.0'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
STAGES = ('root-light', 'search-light', 'category-light', 'root-dark', 'search-dark',
          'category-dark', 'reset-prompt', 'reset-cancelled', 'reset-confirmed',
          'cosmetics-before', 'cosmetics-changed', 'cosmetics-reset')
EXPORT_ENVIRONMENT_KEYS = {'PATH', 'HOME', 'TMPDIR', 'DEVELOPER_DIR', 'SDKROOT',
                           'TOOLCHAINS', 'LANG', 'LC_ALL', 'LC_CTYPE', '__CF_USER_TEXT_ENCODING'}


class InvalidVisualEvidence(ValueError):
    pass


def redact_export_stderr(data, bundle, exported):
    """Only the fixed synthetic exporter can supply this bounded diagnostic."""
    text = data[:COMMAND_OUTPUT_LIMIT].decode('utf-8', errors='replace')
    # The exporter receives no account input. Still remove inherited CI secrets
    # before retaining its usage/errors; never retain its stdout or environment.
    secrets = [value for key, value in os.environ.items() if value and
               re.search(r'TOKEN|SECRET|PASSWORD|API_ID|API_HASH|AUTHORIZATION|PRIVATE_KEY', key, re.I)]
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, '<REDACTED_CREDENTIAL>')
    text = re.sub(r'-----BEGIN [^-\n]*PRIVATE KEY-----.*?(?:-----END [^-\n]*PRIVATE KEY-----|\Z)',
                  '<REDACTED_CREDENTIAL>', text, flags=re.S)
    text = re.sub(r'(?im)^.*\b(?:[A-Z_]*(?:TOKEN|SECRET|PASSWORD|API_HASH|API_ID|AUTHORIZATION|PRIVATE_KEY)[A-Z_]*)\b\s*[:=].*$',
                  '<REDACTED_CREDENTIAL>', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b',
                  '<REDACTED_CREDENTIAL>', text)
    for path, name in ((bundle, '<SYNTHETIC_RESULT>'), (exported, '<PRIVATE_EXPORT>')):
        text = text.replace(str(path), name)
    text = re.sub(r'https?://\S+', '<REDACTED_URL>', text)
    text = re.sub(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', '<REDACTED_ID>', text)
    text = re.sub(r'(?<![A-Za-z0-9])/(?:[^\s\x00-\x1f\"\'<>]+)', '<REDACTED_PATH>', text)
    text = re.sub(r'\b[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '<REDACTED_ADDRESS>', text)
    # Keep control characters out of logs and cap the final redacted JSON field.
    return ''.join(c if c in '\n\t' or ord(c) >= 32 else '?' for c in text)[:16384]


def export_attachments(bundle, exported, diagnostic, timeout=120):
    """Run only Apple's fixed export command, with the same strict pipe cap.

    The bounded diagnostic survives private xcresult cleanup. It does not claim
    that preparing the destination explains an earlier export failure.
    """
    bundle, exported, diagnostic = Path(bundle), Path(exported), Path(diagnostic)
    if (bundle.is_symlink() or not bundle.is_dir() or exported.is_symlink() or
            exported.parent.is_symlink() or not exported.parent.is_dir() or
            diagnostic.is_symlink() or diagnostic.parent.is_symlink() or
            not diagnostic.parent.is_dir() or diagnostic.name != 'attachment-export.json' or diagnostic.exists()):
        raise InvalidVisualEvidence('Unsafe attachment export destination')
    exported.mkdir(mode=0o700, exist_ok=False)
    arguments = ['xcrun', 'xcresulttool', 'export', 'attachments', '--path', str(bundle), '--output-path', str(exported)]
    process = None
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    error = None
    deadline = time.monotonic() + timeout
    try:
        child = {key: value for key, value in os.environ.items() if key in EXPORT_ENVIRONMENT_KEYS}
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child)
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
                    code = process.poll()
                    if code is not None: break
                    raise SmokeError('commandTimedOut', 'exportAttachments', return_code=code, timeout=True)
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = captured[key.data]
                    room = COMMAND_OUTPUT_LIMIT - sum(len(value) for value in captured.values())
                    target.extend(chunk[:room])
                    if len(chunk) > room:
                        raise SmokeError('commandOutputLimit', 'exportAttachments', return_code=process.poll())
        try: code = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SmokeError('commandTimedOut', 'exportAttachments', return_code=process.poll(), timeout=True) from None
        if code:
            raise SmokeError('commandFailed', 'exportAttachments', return_code=code)
    except SmokeError as failure:
        error = failure
        raise
    except OSError:
        error = SmokeError('commandUnavailable', 'exportAttachments')
        raise error from None
    finally:
        if process is not None:
            if process.poll() is None:
                try: process.kill()
                except OSError: pass
            try: process.wait(timeout=5)
            except subprocess.SubprocessError: pass
            if process.stdout is not None: process.stdout.close()
            if process.stderr is not None: process.stderr.close()
        value = {'command': 'xcresulttool.export.attachments', 'phase': 'exportAttachments',
                 'status': 'success' if error is None else error.code,
                 'return_code': 0 if error is None else error.return_code,
                 'timeout': False if error is None else error.timeout,
                 'stdout_bytes': len(captured['stdout']), 'stderr_bytes': len(captured['stderr']),
                 'total_bytes': sum(len(value) for value in captured.values()),
                 'stderr': redact_export_stderr(captured['stderr'], bundle, exported)}
        try:
            descriptor = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                json.dump(value, output, indent=2); output.write('\n')
        except (OSError, ValueError, TypeError):
            # Preserve the original closed command failure if diagnostics cannot
            # be written. A successful export still requires retained evidence.
            if error is None: raise


def validate_runtime():
    runtimes = json.loads(command(['xcrun', 'simctl', 'list', 'runtimes', '--json']))['runtimes']
    # simctl supports a case-insensitive description search after the type.
    # Restrict the public inventory at its source, keeping the shared 64 KiB
    # command cap intact. The search may include Pro Max, so verify exact IDs.
    types = json.loads(command(['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro']))['devicetypes']
    runtime_matches = [item for item in runtimes if item.get('identifier') == RUNTIME and item.get('isAvailable') is True]
    type_matches = [item for item in types if item.get('identifier') == DEVICE_TYPE]
    if len(runtime_matches) != 1 or len(type_matches) != 1:
        raise InvalidVisualEvidence('The fixed iPhone 15 Pro / iOS 27.0 profile is unavailable')


def frames_from_log(text):
    frames = {}
    for line in text.splitlines():
        if 'AYU_OPTIONS_FRAME:' not in line:
            continue
        value = json.loads(line.split('AYU_OPTIONS_FRAME:', 1)[1])
        if not isinstance(value, dict) or set(value) != {'stage', 'window', 'table'} or value['stage'] not in STAGES:
            raise InvalidVisualEvidence('Invalid closed UI frame fields')
        if value['stage'] in frames:
            raise InvalidVisualEvidence('Duplicate UI frame stage')
        for key in ('window', 'table'):
            rectangle = value[key]
            if (not isinstance(rectangle, list) or len(rectangle) != 4 or
                    any(type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 8192 for number in rectangle)):
                raise InvalidVisualEvidence('Invalid UI frame geometry')
        if value['window'][2] <= 0 or value['window'][3] <= 0:
            raise InvalidVisualEvidence('Missing application window frame')
        frames[value['stage']] = value
    if set(frames) != set(STAGES):
        raise InvalidVisualEvidence('Required actual UI frame stages are missing')
    return [frames[stage] for stage in STAGES]


def appearances_from_log(text):
    """Require actual sampled UIKit state, independently of frame-stage names."""
    fields = {'stage', 'window_dark', 'table_found', 'table_dark', 'background_sampled', 'background_dark'}
    stages = set(STAGES) - {'reset-prompt'}  # Modal alert covers its toolbar.
    observations = {}
    for line in text.splitlines():
        if 'AYU_OPTIONS_APPEARANCE:' not in line:
            continue
        value = json.loads(line.split('AYU_OPTIONS_APPEARANCE:', 1)[1])
        if (not isinstance(value, dict) or set(value) != fields or value.get('stage') not in stages or
                any(type(item) is not bool for key, item in value.items() if key != 'stage')):
            raise InvalidVisualEvidence('Invalid closed UIKit appearance observation')
        stage = value['stage']; dark = not stage.endswith('-light')
        if (stage in observations or not value['table_found'] or not value['background_sampled'] or
                any(value[key] != dark for key in ('window_dark', 'table_dark', 'background_dark'))):
            raise InvalidVisualEvidence('Rendered UIKit appearance does not match the required state')
        observations[stage] = value
    if set(observations) != stages:
        raise InvalidVisualEvidence('Required actual UIKit appearance observations are missing')
    return [observations[stage] for stage in STAGES if stage in stages]


def attachment_mapping(value):
    """Read only the public xcresulttool attachment-name mapping, never logs."""
    found, visited = {}, 0
    def walk(node):
        nonlocal visited
        visited += 1
        if visited > 20000:
            raise InvalidVisualEvidence('Attachment manifest exceeds limit')
        if isinstance(node, list):
            for child in node: walk(child)
        elif isinstance(node, dict):
            filename = node.get('exportedFileName')
            labels = [node.get('suggestedHumanReadableName'), node.get('configuredName')]
            if isinstance(filename, str):
                matches = {stage for label in labels if isinstance(label, str) for stage in STAGES
                           if re.match(r'^ayu-' + re.escape(stage) + r'(?:_|\.png$|$)', label)}
                if matches:
                    relative = PurePosixPath(filename)
                    if len(matches) != 1 or relative.is_absolute() or '..' in relative.parts or '\\' in filename:
                        raise InvalidVisualEvidence('Unsafe exported screenshot name')
                    stage = next(iter(matches))
                    if stage in found:
                        raise InvalidVisualEvidence('Duplicate screenshot stage')
                    found[stage] = str(relative)
            for child in node.values():
                if isinstance(child, (dict, list)): walk(child)
    walk(value)
    if set(found) != set(STAGES):
        raise InvalidVisualEvidence('Required screenshot attachments are missing')
    return found


def extract_result_bundle(outputs, temporary):
    """Use only the fixed test.xcresult subtree; never publish raw diagnostics."""
    outputs, temporary = Path(outputs), Path(temporary)
    if outputs.is_symlink():
        raise InvalidVisualEvidence('Result output root is a symlink')
    direct = outputs / 'test.xcresult'
    if direct.is_dir() and not direct.is_symlink():
        return direct
    archive = outputs / 'outputs.zip'
    if not archive.is_file() or archive.is_symlink():
        raise InvalidVisualEvidence('Native UI result bundle unavailable')
    destination = temporary / 'test.xcresult'
    count = total = 0
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            if not entry.filename.startswith('test.xcresult/'):
                continue
            relative = PurePosixPath(entry.filename[len('test.xcresult/'):])
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise InvalidVisualEvidence('Unsafe result bundle member')
            count += 1; total += entry.file_size
            if count > 20000 or total > 2 * 1024**3 or entry.file_size > 512 * 1024**2:
                raise InvalidVisualEvidence('Result bundle exceeds limit')
            path = destination.joinpath(*relative.parts)
            if entry.is_dir(): path.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with zipped.open(entry) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    if not destination.is_dir():
        raise InvalidVisualEvidence('Native UI result bundle was not retained')
    return destination


def export_screenshots(outputs, destination):
    destination = Path(destination)
    if destination.is_symlink() or destination.parent.is_symlink():
        raise InvalidVisualEvidence('Unsafe screenshot destination')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ayu-options-attachments-') as directory:
        temporary = Path(directory)
        bundle = extract_result_bundle(outputs, temporary)
        exported = temporary / 'exported'
        export_attachments(bundle, exported, destination / 'attachment-export.json')
        manifest = exported / 'manifest.json'
        if manifest.is_symlink() or not 0 < manifest.stat().st_size <= 2 * 1024**2:
            raise InvalidVisualEvidence('Invalid screenshot attachment manifest')
        mapping = attachment_mapping(json.loads(manifest.read_text()))
        checker = prepare_screenshot_checker(temporary, compiler_log=destination / 'checker-compiler.log')
        images = {}
        for stage, name in mapping.items():
            path = exported / name
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(exported.resolve(strict=True)) or not 0 < path.stat().st_size <= 20 * 1024**2:
                raise InvalidVisualEvidence('Invalid exported screenshot')
            with path.open('rb') as source:
                if source.read(8) != b'\x89PNG\r\n\x1a\n':
                    raise InvalidVisualEvidence('Screenshot is not a PNG')
            target = destination / (stage + '.png')
            shutil.copyfile(path, target)
            result = json.loads(command([str(checker), str(target)], phase='checkScreenshot', timeout=20))
            if (not isinstance(result, dict) or set(result) != {'width', 'height', 'nonblank'} or result['nonblank'] is not True or
                    any(type(result[key]) is not int or not 0 < result[key] <= 16384 for key in ('width', 'height'))):
                raise InvalidVisualEvidence('Screenshot is blank or invalid')
            images[stage] = result
        return images


class DisposableRendererSimulator:
    """Own one fresh test device matching the fixed Bazel runner, never a user sim."""
    def __init__(self, output):
        self.output = Path(output)
        self.identifier = None
        self.recorder = None
        self.recording_return_code = None
        self.recording_started = False
        self.recording_log = None
        self.boot_attempts = []
        self.boot_evidence_created = False

    def _write_boot_evidence(self):
        """A boot checkpoint, never a native-test or first-attempt success claim."""
        if self.output.is_symlink() or self.output.parent.is_symlink():
            raise InvalidVisualEvidence('Unsafe boot evidence destination')
        self.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = self.output / 'boot-attempts.json'
        if destination.is_symlink() or (destination.exists() and not self.boot_evidence_created):
            raise InvalidVisualEvidence('Boot evidence already exists or is unsafe')
        value = {'boot_evidence_only': True, 'native_tests_started': False,
                 'device_model': 'iPhone 15 Pro', 'runtime': RUNTIME,
                 'restart_retry_limit': 1, 'boot_status_timeout_seconds': 240,
                 'first_attempt_successful': bool(self.boot_attempts and self.boot_attempts[0]['status'] == 'ready'
                                                  and not self.boot_attempts[0].get('accepted_already_booted', False)),
                 'recovered_on_retry': len(self.boot_attempts) == 2 and self.boot_attempts[-1]['status'] == 'ready',
                 'ready': bool(self.boot_attempts and self.boot_attempts[-1]['status'] == 'ready'),
                 'attempts': self.boot_attempts}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', prefix='.renderer-boot-', suffix='.json', dir=self.output, delete=False) as target:
                temporary = Path(target.name)
                os.chmod(target.name, 0o600)
                json.dump(value, target, indent=2)
                target.write('\n')
            if self.boot_evidence_created:
                os.replace(temporary, destination)
            else:
                # An exclusive first publication never overwrites a preexisting
                # report, symlink or FIFO; subsequent updates are atomic.
                os.link(temporary, destination)
                self.boot_evidence_created = True
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _owned_device_is_booted(self, attempt):
        """149 alone is not readiness; prove the fresh owned identity again."""
        attempt['owned_booted_state_verified'] = False
        try:
            current = json.loads(command(['xcrun', 'simctl', 'list', 'devices', 'available', '--json'],
                                         timeout=15, phase='waitForBoot'))
            entries = current['devices'][RUNTIME]
            if not isinstance(entries, list): return False
            matches = [item for item in entries if isinstance(item, dict) and item.get('udid') == self.identifier]
            verified = len(matches) == 1 and matches[0].get('name') == DEVICE_NAME and matches[0].get('state') == 'Booted'
            attempt['owned_booted_state_verified'] = verified
            return verified
        except SmokeError as error:
            attempt['state_query_failure'] = error.evidence()
        except (KeyError, ValueError, TypeError, RecursionError):
            attempt['state_query_unavailable'] = True
        return False

    def _boot_attempt(self, restarting):
        attempt = {'attempt': len(self.boot_attempts) + 1, 'restart': restarting,
                   'status': 'starting', 'phase': 'shutdownDevice' if restarting else 'waitForBoot'}
        self.boot_attempts.append(attempt)
        try:
            if restarting:
                command(['xcrun', 'simctl', 'shutdown', self.identifier], timeout=30, phase='shutdownDevice')
            attempt['phase'] = 'waitForBoot'
            # Match the known-working native warm-up path. -b both boots and
            # monitors this new device; no separate boot request is needed.
            try:
                command(['xcrun', 'simctl', 'bootstatus', self.identifier, '-b'], timeout=240, phase='waitForBoot')
            except SmokeError as error:
                if error.code != 'commandFailed' or error.phase != 'waitForBoot' or error.return_code != 149 or error.timeout:
                    raise
                attempt['bootstatus_failure'] = error.evidence()
                if not self._owned_device_is_booted(attempt): raise
                attempt['accepted_already_booted'] = True
            attempt['status'] = 'ready'
        except SmokeError as error:
            attempt['status'] = 'failed'
            attempt['failure'] = error.evidence()
            raise
        except Exception:
            # No exception text, command output, identifiers or environment.
            attempt['status'] = 'unavailable'
            raise

    def __enter__(self):
        if self.identifier is not None or self.boot_attempts:
            raise InvalidVisualEvidence('Disposable simulator cannot be entered twice')
        devices = json.loads(command(['xcrun', 'simctl', 'list', 'devices', 'available', '--json']))['devices']
        if any(item.get('name') == DEVICE_NAME for item in devices.get(RUNTIME, [])):
            raise InvalidVisualEvidence('Renderer test requires a fresh runner without a matching device')
        identifier = command(['xcrun', 'simctl', 'create', DEVICE_NAME,
            DEVICE_TYPE, RUNTIME], phase='createDevice')
        try: uuid.UUID(identifier)
        except ValueError: raise InvalidVisualEvidence('Invalid disposable simulator identity') from None
        self.identifier = identifier
        try:
            try:
                self._boot_attempt(restarting=False)
            except SmokeError as original:
                # Retrying is confined to preparation of this new, empty device.
                # No test or assertion has run, and no other simulator is touched.
                try: self._write_boot_evidence()
                except (OSError, ValueError, TypeError): raise original from None
                if original.phase != 'waitForBoot': raise
                try:
                    self._boot_attempt(restarting=True)
                except Exception:
                    try: self._write_boot_evidence()
                    except (OSError, ValueError, TypeError): pass
                    raise original from None
            self._write_boot_evidence()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def start_recording(self):
        if self.identifier is None or self.recorder is not None:
            raise InvalidVisualEvidence('Invalid recording lifecycle')
        self.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.recording_log = tempfile.TemporaryFile()
        self.recorder = subprocess.Popen(['xcrun', 'simctl', 'io', self.identifier, 'recordVideo', '--codec=h264', '--force', str(self.output / 'interaction.mp4')], stdout=subprocess.DEVNULL, stderr=self.recording_log)
        deadline = time.monotonic() + 15
        while self.recorder.poll() is None and time.monotonic() < deadline:
            if b'Recording started' in os.pread(self.recording_log.fileno(), 8192, 0):
                self.recording_started = True
                return
            time.sleep(0.1)
        raise InvalidVisualEvidence('Native video recorder did not become ready')

    def stop_recording(self):
        if self.recorder is None:
            return
        recorder, self.recorder = self.recorder, None
        if recorder.poll() is not None:
            self.recording_return_code = recorder.returncode
            raise InvalidVisualEvidence('Recording ended before the UI scenario completed')
        recorder.send_signal(signal.SIGINT)
        try: self.recording_return_code = recorder.wait(timeout=30)
        except subprocess.TimeoutExpired:
            recorder.kill(); recorder.wait(timeout=10)
            raise InvalidVisualEvidence('Recording did not finalize') from None
        if self.recording_return_code != 0 or not (self.output / 'interaction.mp4').is_file() or (self.output / 'interaction.mp4').stat().st_size < 1024:
            raise InvalidVisualEvidence('Recording is unavailable or invalid')

    def __exit__(self, exception_type, exception, traceback):
        if self.recorder is not None:
            try: self.stop_recording()
            except (OSError, ValueError, RuntimeError): pass
        if self.identifier:
            for operation, phase in [('shutdown', 'shutdownDevice'), ('delete', 'deleteDevice')]:
                try: command(['xcrun', 'simctl', operation, self.identifier], timeout=30, phase=phase)
                except (OSError, RuntimeError, ValueError): pass
        self.identifier = None
        if self.recording_log is not None:
            self.recording_log.close()
            self.recording_log = None
