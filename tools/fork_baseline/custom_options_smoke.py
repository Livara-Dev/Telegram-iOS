"""Bounded visual evidence for the synthetic production-renderer host only."""
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from launch_smoke import command, prepare_screenshot_checker, SmokeError

BUNDLE = 'org.livara.ayugram.tests.custome-options'
DEVICE_NAME = 'BAZEL_TEST_iPhone 15 Pro_27.0'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
STAGES = ('root-light', 'search-light', 'category-light', 'root-dark', 'search-dark',
          'category-dark', 'reset-prompt', 'reset-cancelled', 'reset-confirmed',
          'cosmetics-before', 'cosmetics-changed', 'cosmetics-reset')


class InvalidVisualEvidence(ValueError):
    pass


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
    destination = Path(destination); destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ayu-options-attachments-') as directory:
        temporary = Path(directory)
        bundle = extract_result_bundle(outputs, temporary)
        exported = temporary / 'exported'
        command(['xcrun', 'xcresulttool', 'export', 'attachments', '--path', str(bundle), '--output-path', str(exported)], timeout=120)
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

    def __enter__(self):
        devices = json.loads(command(['xcrun', 'simctl', 'list', 'devices', 'available', '--json']))['devices']
        if any(item.get('name') == DEVICE_NAME for item in devices.get(RUNTIME, [])):
            raise InvalidVisualEvidence('Renderer test requires a fresh runner without a matching device')
        identifier = command(['xcrun', 'simctl', 'create', DEVICE_NAME,
            DEVICE_TYPE, RUNTIME], phase='createDevice')
        try: uuid.UUID(identifier)
        except ValueError: raise InvalidVisualEvidence('Invalid disposable simulator identity') from None
        self.identifier = identifier
        try:
            command(['xcrun', 'simctl', 'boot', identifier], phase='bootDevice')
            command(['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=240, phase='waitForBoot')
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
