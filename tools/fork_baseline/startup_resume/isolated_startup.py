#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Artifact-only, private-device-set adapter for the immutable 5d startup helper.

No application rebuild, default simulator mutation, broad process termination,
or private-set filesystem deletion. Native qualification remains required.
"""
import argparse
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time

SOURCE = '5d241267735b3958c225681d173853568bab705d'
IPA_SHA256 = '01f32ba769f876c20bed6090c54a8066d27f09cc73d75d890e7f57c435219e0a'
PINS = {
    'launch_smoke.py': 'c9efc0fc8e15642c684abf9ff44ba81760ccaa64ed9c93ff8e60b6218f17b6ef',
    'startup_replay.py': '28390769e9fe460702383f3265c1802d1e6259ba7024f28c77ce3b9f5ef14802',
}
MODULES = ('ipa_inspect', 'ipa_device_inspect', 'storage_evidence', 'artifact_licensing',
           'launch_smoke', 'replay_artifact', 'startup_replay')
UUID = re.compile(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}')
BUNDLE = 'org.livara.ayugram'
MODEL = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
_LOCK = threading.Lock()


class IsolationError(RuntimeError):
    def __init__(self):
        super().__init__('isolated_startup_verification_failed')


def require(value):
    if not value:
        raise IsolationError()


def unique_object(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def owned_directory(path, uid):
    info = Path(path).lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid)
    return info


def owned_plist(path, uid):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1
            and 0 < before.st_size <= 65536)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        require(file_identity(opened) == file_identity(before))
        require(stat.S_ISREG(opened.st_mode) and opened.st_uid == uid and opened.st_nlink == 1
                and 0 < opened.st_size <= 65536)
        data = os.read(descriptor, 65537)
        require(len(data) == opened.st_size)
        require(file_identity(os.fstat(descriptor)) == file_identity(opened))
        require(file_identity(path.lstat()) == file_identity(opened))
    finally:
        os.close(descriptor)
    value = plistlib.loads(data)
    require(isinstance(value, dict))
    return value


def file_identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class OwnedDeviceSet:
    def __init__(self, smoke, output):
        self.smoke = smoke
        self.output = Path(output).resolve() / 'main-app-only' / 'launch-smoke'
        self.uid = os.getuid()
        self.thread = threading.get_ident()
        # Deliberately not TemporaryDirectory: on failure an active VM must not
        # lose its backing files. Hosted-runner teardown owns any remaining set.
        created = Path(tempfile.mkdtemp(prefix='ayu-isolated-startup-'))
        require(not created.is_symlink())
        self.root = created.resolve(strict=True)
        info = owned_directory(self.root, self.uid)
        require(stat.S_IMODE(info.st_mode) == 0o700 and not any(self.root.iterdir()))
        self.root_identity = (info.st_dev, info.st_ino)
        self.identifier = None
        self.name = None
        self.device_identity = None
        self.checker = None
        self.created = False
        self.deleted = False
        self.absence = False
        self.routing_valid = True
        self.current = None
        self.original_command = None
        self.original_subprocess = None
        self.installer_observer = None
        self.installer_observation = None
        self.install_started = None

    def verify_root(self):
        require(threading.get_ident() == self.thread)
        info = owned_directory(self.root, self.uid)
        require((info.st_dev, info.st_ino) == self.root_identity and stat.S_IMODE(info.st_mode) == 0o700)

    def uuid_entries(self):
        self.verify_root()
        result, count = [], 0
        deadline = time.monotonic() + 2.0
        with os.scandir(self.root) as entries:
            for entry in entries:
                count += 1
                require(count <= 1024 and time.monotonic() < deadline)
                if UUID.fullmatch(entry.name):
                    result.append(Path(entry.path))
        require(time.monotonic() < deadline)
        return result

    def verify_device(self):
        self.verify_root()
        require(self.created and self.identifier is not None and not self.deleted)
        entries = self.uuid_entries()
        require(len(entries) == 1 and entries[0].name == self.identifier)
        device = self.root / self.identifier
        info = owned_directory(device, self.uid)
        if self.device_identity is not None:
            require((info.st_dev, info.st_ino) == self.device_identity)
        value = owned_plist(device / 'device.plist', self.uid)
        require(all(isinstance(value.get(key), str) for key in ('UDID', 'name', 'deviceType', 'runtime')))
        require(value['UDID'].casefold() == self.identifier.casefold() and value['name'] == self.name
                and value['deviceType'] == MODEL and value['runtime'] == RUNTIME)
        self.device_identity = (info.st_dev, info.st_ino)

    def verify_container(self, value):
        self.verify_device()
        require(isinstance(value, str) and '\n' not in value and '\x00' not in value)
        path = Path(value)
        require(path.is_absolute())
        relative = path.relative_to(self.root)
        parts = relative.parts
        require(len(parts) == 6 and parts[:5] == (self.identifier, 'data', 'Containers', 'Data', 'Application')
                and UUID.fullmatch(parts[5]) is not None)
        current = self.root
        for part in parts:
            current = current / part
            owned_directory(current, self.uid)  # lstat forbids links throughout.
        require(path.resolve(strict=True) == path)
        metadata = owned_plist(path / '.com.apple.mobile_container_manager.metadata.plist', self.uid)
        require(metadata.get('MCMMetadataIdentifier') == BUNDLE)
        if 'MCMMetadataUUID' in metadata:
            require(isinstance(metadata['MCMMetadataUUID'], str)
                    and metadata['MCMMetadataUUID'].casefold() == parts[5].casefold())
        return value

    def check_arguments(self, arguments, timeout, phase, compiler_log):
        require(isinstance(arguments, list) and all(isinstance(x, str) and 0 < len(x) < 4096
                and '\x00' not in x and '\n' not in x for x in arguments))
        require('--set' not in arguments and '--device-set' not in arguments)
        if arguments[:2] != ['xcrun', 'simctl']:
            if phase == 'compileScreenshotChecker':
                source = str(Path(self.smoke.__file__).with_name('screenshot_check.swift'))
                require(len(arguments) == 10 and arguments[:-1] ==
                    ['xcrun', 'swiftc', '-O', '-framework', 'CoreGraphics', '-framework', 'ImageIO', source, '-o'])
                target = Path(arguments[-1])
                require(target.is_absolute() and target.name == 'screenshot-check'
                        and target.parent.name.startswith('ayu-launch-smoke-') and timeout == 300)
                owned_directory(target.parent, self.uid)
                # Apple's normal TMPDIR may traverse /var -> /private/var. The
                # newly created leaf itself must be an owned real directory.
                require(compiler_log == self.output / 'checker-compiler.log')
                self.checker = target
            elif phase == 'testScreenshotChecker':
                require(self.checker is not None and arguments == [str(self.checker), '--self-test']
                        and timeout == 30 and compiler_log is None)
            elif phase == 'checkScreenshot':
                require(self.checker is not None and len(arguments) == 2 and arguments[0] == str(self.checker)
                        and arguments[1] in {str(self.output / 'launch-1.png'), str(self.output / 'launch-2.png')}
                        and timeout == 30 and compiler_log is None)
            else:
                raise IsolationError()
            return
        require(compiler_log is None)
        action = arguments[2] if len(arguments) > 2 else None
        if action == 'create':
            self.verify_root()
            require(not self.created and self.identifier is None and not any(self.root.iterdir()))
            require(len(arguments) == 6 and re.fullmatch(r'AYU_SMOKE_[0-9a-f]{32}', arguments[3])
                    and arguments[4:] == [MODEL, RUNTIME] and phase == 'createDevice' and timeout == 120)
            self.name = arguments[3]
            return
        self.verify_device()
        require(len(arguments) >= 4 and arguments[3] == self.identifier)
        tail = arguments[4:]
        if action == 'boot':
            require(tail == [] and phase == 'bootDevice' and timeout == 120)
        elif action == 'bootstatus':
            require(tail == ['-b'] and phase == 'waitForBoot' and timeout == 180)
        elif action == 'install':
            require(self.checker is not None and tail == [str(self.checker.parent / 'Telegram.app')]
                    and phase == 'installApplication' and timeout == 120)
            owned_directory(self.checker.parent / 'Telegram.app', self.uid)
        elif action in {'launch', 'terminate'}:
            require(tail == [BUNDLE] and phase == 'launchApplication' and timeout == 120)
        elif action == 'get_app_container':
            require(tail == [BUNDLE, 'data'] and phase == 'getContainer' and timeout == 120)
        elif action == 'io':
            require(len(tail) == 2 and tail[0] == 'screenshot'
                    and tail[1] in {str(self.output / 'launch-1.png'), str(self.output / 'launch-2.png')}
                    and phase == 'captureScreenshot' and timeout == 120)
        elif action in {'shutdown', 'delete'}:
            require(tail == [] and phase == {'shutdown': 'shutdownDevice', 'delete': 'deleteDevice'}[action]
                    and timeout == 30)
        else:
            raise IsolationError()

    def map_popen(self, arguments, kwargs):
        require(threading.get_ident() == self.thread and isinstance(arguments, list))
        if self.current is not None:
            require(arguments == self.current[0] and kwargs ==
                {'stdout': self.original_subprocess.PIPE, 'stderr': self.original_subprocess.PIPE})
            self.check_arguments(arguments, *self.current[1:])
        else:
            self.verify_device()
            require(arguments == ['xcrun', 'simctl', 'io', self.identifier, 'recordVideo',
                                   '--codec=h264', '--force', str(self.output / 'launch.mp4')])
            require(kwargs == {'stdout': self.original_subprocess.DEVNULL, 'stderr': self.original_subprocess.DEVNULL})
        if arguments[:2] == ['xcrun', 'simctl']:
            return arguments[:2] + ['--set', str(self.root)] + arguments[2:]
        return arguments

    def call_original(self, arguments, timeout, phase, compiler_log=None):
        require(self.current is None)
        self.current = (arguments, timeout, phase, compiler_log)
        if self.installer_observer is not None and arguments[:3] == ['xcrun', 'simctl', 'install']:
            self.install_started = time.monotonic()
        try:
            return self.original_command(arguments, timeout=timeout, phase=phase, compiler_log=compiler_log)
        finally:
            self.current = None
            self.install_started = None

    def verify_absence(self):
        self.verify_root()
        require(self.deleted and not os.path.lexists(self.root / self.identifier))
        # Actual delete exit0 plus a complete bounded filesystem inventory of our
        # fresh singleton set. Another simctl IPC after delete is unnecessary.
        # No directory, link, special file, hardlink or unknown name is admitted.
        deadline = time.monotonic() + 2.0
        allowed = {'device_set.plist', 'device_set.plist.lock', '.device_set.plist.lock'}
        def inventory():
            result = {}
            with os.scandir(self.root) as entries:
                for entry in entries:
                    require(len(result) < 1024 and time.monotonic() < deadline and entry.name in allowed)
                    value = entry.stat(follow_symlinks=False)
                    require(stat.S_ISREG(value.st_mode) and value.st_uid == self.uid
                            and value.st_nlink == 1 and 0 <= value.st_size <= 1024 * 1024)
                    result[entry.name] = file_identity(value)
            require(time.monotonic() < deadline)
            return result
        observed = inventory()
        require(observed == inventory())
        self.verify_root()
        require(not os.path.lexists(self.root / self.identifier))
        self.absence = True

    def command(self, arguments, timeout=120, phase='extractApplication', compiler_log=None):
        try:
            self.check_arguments(arguments, timeout, phase, compiler_log)
            answer = self.call_original(arguments, timeout, phase, compiler_log)
            if arguments[:3] == ['xcrun', 'simctl', 'create']:
                require(isinstance(answer, str) and UUID.fullmatch(answer) is not None)
                self.identifier = answer
                self.created = True
                self.verify_device()
            elif arguments[:3] == ['xcrun', 'simctl', 'get_app_container']:
                return self.verify_container(answer)
            elif arguments[:3] == ['xcrun', 'simctl', 'delete']:
                self.deleted = True  # The unchanged command returned actual exit 0.
                self.verify_absence()
            return answer
        except self.smoke.SmokeError:
            raise
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, OverflowError):
            self.routing_valid = False
            raise self.smoke.SmokeError('startupPreparationFailed', phase) from None

    def proof(self):
        return {'schema': 1, 'owned_device_deleted': self.deleted,
                'owned_uuid_absence_verified': self.absence,
                'exact_owned_device_only': self.created and self.routing_valid}


class OwnedInstallerProcess:
    """Only observe the verified install child's original timeout/kill boundary.

    The observation module owns its <=12s diagnostic worker budget. No filesystem
    observation/publication is added here, and the original kill always executes.
    """
    def __init__(self, state, process, mapped):
        self.state, self.process = state, process
        self.mapped = tuple(mapped)
        self.started = state.install_started
        self.attempted = False

    def __getattr__(self, name):
        return getattr(self.process, name)

    def kill(self):
        try:
            if self.attempted:
                return
            self.attempted = True
            error = sys.exc_info()[1]
            state = self.state
            # All checks use already-established in-memory context. Never open
            # paths or scan processes in the stock command's kill/finally block.
            if not (isinstance(error, state.smoke.SmokeError)
                    and error.code == 'commandTimedOut' and error.phase == 'installApplication'
                    and error.timeout is True and error.return_code is None
                    and state.current is not None and state.current[1:] == (120, 'installApplication', None)
                    and tuple(state.current[0]) == ('xcrun', 'simctl', 'install', state.identifier,
                                                  str(state.checker.parent / 'Telegram.app'))
                    and self.mapped == ('xcrun', 'simctl', '--set', str(state.root), 'install',
                                        state.identifier, str(state.checker.parent / 'Telegram.app'))
                    and self.started is not None and self.started == state.install_started
                    and time.monotonic() - self.started >= 120 and self.process.poll() is None):
                return
            state.installer_observation = state.installer_observer.blank()
            value = state.installer_observer.capture(self.process, mapped_arguments=list(self.mapped),
                private_root=state.root, root_identity=state.root_identity, identifier=state.identifier,
                device_name=state.name, command_started=self.started, timeout_seconds=120)
            state.installer_observation = state.installer_observer.validate_observation(value)
        except BaseException:
            # Diagnostic failures must not replace the already-pending stock
            # SmokeError (including interrupted observation/publication).
            pass
        finally:
            self.process.kill()


class LocalSubprocessProxy:
    def __init__(self, state):
        self.state = state

    def __getattr__(self, name):
        require(name in {'PIPE', 'DEVNULL', 'TimeoutExpired', 'SubprocessError'})
        return getattr(self.state.original_subprocess, name)

    def Popen(self, arguments, **kwargs):
        try:
            mapped = self.state.map_popen(arguments, kwargs)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            self.state.routing_valid = False
            phase = self.state.current[2] if self.state.current is not None else 'startRecording'
            raise self.state.smoke.SmokeError('startupPreparationFailed', phase) from None
        process = self.state.original_subprocess.Popen(mapped, **kwargs)
        if (self.state.installer_observer is not None and self.state.current is not None
                and arguments[:3] == ['xcrun', 'simctl', 'install']):
            return OwnedInstallerProcess(self.state, process, mapped)
        return process


@contextlib.contextmanager
def adapters(smoke, state):
    require(_LOCK.acquire(blocking=False))
    original_command, original_subprocess = smoke.command, smoke.subprocess
    try:
        state.original_command, state.original_subprocess = original_command, original_subprocess
        smoke.command = state.command
        smoke.subprocess = LocalSubprocessProxy(state)
        yield
    finally:
        smoke.command, smoke.subprocess = original_command, original_subprocess
        _LOCK.release()


@contextlib.contextmanager
def pinned_modules(source_root):
    require(threading.active_count() == 1)
    root = Path(source_root).resolve(strict=True)
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, stderr=subprocess.DEVNULL).decode().strip() == SOURCE)
    require(not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root, stderr=subprocess.DEVNULL).strip())
    directory = root / 'tools/fork_baseline'
    for name, expected in PINS.items():
        path = directory / name
        require(not path.is_symlink() and digest(path) == expected)
    # All imports below come from the exact clean application source, with its
    # reviewed helper pins. Restore module/path state even after an exception.
    saved = {name: sys.modules.get(name) for name in MODULES}
    old_path = list(sys.path)
    old_bytecode = sys.dont_write_bytecode
    try:
        for name in MODULES:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(directory))
        sys.dont_write_bytecode = True
        smoke = importlib.import_module('launch_smoke')
        replay = importlib.import_module('startup_replay')
        require(smoke.BUNDLE == BUNDLE and smoke.SIMULATOR_DEVICE_TYPE == MODEL and smoke.SIMULATOR_RUNTIME == RUNTIME)
        yield smoke, replay
    finally:
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def verify_success(replay, result, cleanup):
    require(type(cleanup.get('schema')) is int and all(type(cleanup.get(key)) is bool for key in
            ('owned_device_deleted', 'owned_uuid_absence_verified', 'exact_owned_device_only')))
    require(cleanup == {'schema': 1, 'owned_device_deleted': True,
                        'owned_uuid_absence_verified': True, 'exact_owned_device_only': True})
    require(replay.get('passed') is True and replay.get('phase') == 'complete'
            and replay.get('private_control_only') is True and replay.get('private_mode_control') == 'explicitFreshPinnedSelection'
            and replay.get('main_app_only_control_passed') is True and replay.get('source_codesign_verified') is True
            and replay.get('main_code_unchanged') is True and replay.get('source_ipa_unchanged') is True
            and replay.get('application_recompiled') is False
            and replay.get('physical_device_verified') is False and replay.get('automatic_control_run') is False
            and replay.get('automatic_fallback_verified') is False and replay.get('automatic_group_selection_observed') is False
            and replay.get('source_simulator_ipa_sha256') == IPA_SHA256)
    require(result.get('passed') is True and result.get('phase') == 'complete' and result.get('failure') is None
            and result.get('cleanup_failures', []) == [] and result.get('storage_test_setup') == 'freshPinnedPrivate'
            and result.get('private_selection_seeded_before_launch') is True
            and result.get('private_account_store_absent_before_launch') is True
            and result.get('physical_device_verified') is False and result.get('authentication_codes_used') is False
            and result.get('simulator_device_type') == MODEL and result.get('simulator_runtime') == RUNTIME
            and type(result.get('recording_return_code')) is int and result['recording_return_code'] == 0
            and result.get('screenshot_checker') == {'compiled': True, 'self_test_passed': True, 'self_test_cases': 3})
    launches = result.get('launches')
    require(isinstance(launches, list) and len(launches) == 2)
    for index, launch in enumerate(launches, 1):
        require(isinstance(launch, dict) and launch.get('attempt') == index and launch.get('storage_kind') == 'private'
                and launch.get('private_account_store_present') is True
                and (index == 1 or launch.get('private_account_store_stable_after_restart') is True)
                and launch.get('marker', {}).get('stage') in {'authorizationVisible', 'conversationsVisible'}
                and launch.get('marker', {}).get('failure') is None and launch.get('screenshot', {}).get('nonblank') is True)


def verify_visual_files(output, uid):
    output = Path(output)
    current = output
    owned_directory(current, uid)
    for name in ('main-app-only', 'launch-smoke'):
        current = current / name
        owned_directory(current, uid)
    for name, maximum in (('launch-1.png', 32 * 1024**2), ('launch-2.png', 32 * 1024**2),
                          ('launch.mp4', 128 * 1024**2)):
        info = (current / name).lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_nlink == 1
                and 0 < info.st_size <= maximum)


def publish_installer_observation(state, output):
    """Publish only the validated closed receipt, after the stock helper unwinds.

    Atomic no-replace linking prevents a partial or overwritten public receipt.
    No raw sample, identifier, path, or arbitrary exception is published.
    """
    if state.installer_observer is None:
        return
    value = state.installer_observer.validate_observation(state.installer_observation)
    data = (json.dumps(value, sort_keys=True) + '\n').encode('utf-8')
    require(0 < len(data) <= 65536)
    owned_directory(output, state.uid)
    descriptor, name = tempfile.mkstemp(prefix='.installer-observation-', dir=output)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output / 'installer-observation.json', follow_symlinks=False)
    finally:
        temporary.unlink()


def run(ipa, output, source_root, installer_diagnostics=False):
    require(type(installer_diagnostics) is bool)
    observer = importlib.import_module('installer_observation') if installer_diagnostics else None
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64')
    ipa, output = Path(ipa).resolve(strict=True), Path(output).absolute()
    require(ipa.is_file() and digest(ipa) == IPA_SHA256 and not os.path.lexists(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    require(output.parent.resolve(strict=True) == output.parent)
    with pinned_modules(source_root) as (smoke, replay):
        state = OwnedDeviceSet(smoke, output)
        if observer is not None:
            state.installer_observer = observer
            state.installer_observation = observer.validate_observation({**observer.blank(), 'state': 'notTriggered'})
        try:
            with adapters(smoke, state):
                report = replay.replay(ipa, output, startup_variant='main-app-only', private_control_only=True)
            raw = json.loads((output / 'main-app-only/launch-smoke/result.json').read_bytes(), object_pairs_hook=unique_object)
            result = replay.prior_smoke_report(raw)
            verify_success(report, result, state.proof())
            verify_visual_files(output, state.uid)
            require(digest(ipa) == IPA_SHA256)
            # Preserve both stock result/replay bytes. cleanup.json is the separate
            # closed proof; no optional empty failure field is invented here.
            return report
        finally:
            # Never delete a set or backing files after a failed VM operation.
            # The only simulator cleanup is the stock exact-UUID shutdown/delete
            # routed through this private set and independently verified above.
            require(not output.is_symlink())
            output.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (output / 'cleanup.json').open('x') as stream:
                json.dump(state.proof(), stream, sort_keys=True)
                stream.write('\n')
            try:
                publish_installer_observation(state, output)
            except BaseException:
                # Optional diagnostic publication never replaces stock failure.
                # The publisher separately validates any receipt that exists.
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--installer-diagnostics', action='store_true',
                        help='Observe only an owned install timeout; never retries or extends install.')
    args = parser.parse_args()
    try:
        run(args.ipa, args.output, args.source_root, installer_diagnostics=args.installer_diagnostics)
    except Exception:
        print('Isolated startup verification failed; no startup or cleanup pass recorded.', file=sys.stderr)
        return 1
    print('Isolated artifact-only startup and owned-device cleanup verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
