#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reviewed bounded TestDC auth-only runner. No application build/download.

Native authentication is unverified until this exact reviewed scenario executes.
Only a newly constructed closed result JSON is exportable. Native command bytes,
XCTest activities and xcresult never leave the disposable private workspace.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid

EXECUTION_REVIEWED = True
REVIEW_PINS_SHA256 = '0ebcab09f8b105995f29f1596223e59e6941e068e9c2fba2dac0cd7dd7812a9c'
REVIEWED_STARTUP_RUN = 42204637743  # Root reviewed exact source/startup proof; auth result pending.
STARTUP_CONTROL_RUN = 42204637743
REVIEWED_EXECUTION_SCOPE = 'authOnly'  # One official-public-TestDC attempt after setup run 42297681349.
STARTUP_ARTIFACT = 13495439062
STARTUP_ARTIFACT_SHA256 = 'aefdac1cb0edc2dff994129577da44490a44045a8e5bcb0ec432fc5eac14aa79'
STARTUP_RESULT_SHA256 = '6e3a44f2f8b4222ed4164d4d590163cb38be1d4592c5b383ac0ca6d4700173d7'
STARTUP_PROOF_SHA256 = 'baa24592c7d25ff3471f4c48e32e5d1ee9a61f197caa72d8f674aa7eaef85cd2'
SOURCE = 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'
ARTIFACT = 13490005243
SOURCE_RUN = 42174474046
IPA_SHA256 = 'df7a313e263440d92f2ab503e7ee1c1afb328774f6d631b3d052169f2f2e8e10'
BUNDLE = 'org.livara.ayugram'
TARGET = 'OfficialTestDCAuthenticationSmoke'
CASE = TARGET + '.testOfficialPublicTestDCAuthenticationOnly'
TEST_BUNDLE = 'org.livara.ayugram.tests.public-testdc-auth-only'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
PREFIX = 'AYU_TESTDC_AUTH_STAGE:'
SUCCESS_TRACES = (
    ('welcomeObserved', 'reservedInputVerified', 'phoneSubmitted', 'authorizedRootInstalled'),
    ('welcomeObserved', 'reservedInputVerified', 'phoneSubmitted', 'publicCodeSubmitted', 'authorizedRootInstalled'),
)
STAGES = frozenset({'disabled', 'welcomeObserved', 'reservedInputVerified', 'phoneSubmitted',
    'publicCodeSubmitted', 'authorizedRootInstalled', 'rejectedBeforeSubmission', 'signupRequired',
    'passwordRequired', 'blockedOrUnexpected', 'timedOut'})
ERRORS = frozenset({'disabled', 'reviewGate', 'platform', 'inputIntegrity', 'artifactIdentity',
    'sourceIntegrity', 'helperIntegrity', 'commandFailure', 'commandTimeout', 'commandOutputLimit',
    'toolchain', 'driverCompile', 'driverPlan', 'driverIdentity', 'signature', 'simulatorOwnership',
    'simulatorUnavailable', 'containerOwnership', 'privateSeed', 'authEvidence', 'cleanup', 'internal'})
MAX_COMMAND_BYTES = 8 * 1024**2
PHASES = frozenset({'notStarted', 'toolchain', 'artifactExtraction', 'sourceSignature',
    'privateVariant', 'driverProject', 'driverCompile', 'driverProducts', 'runnerSignature',
    'planBind', 'runtimeInventory', 'simulatorCreate', 'boot', 'simulatorVerify', 'install',
    'dataContainer', 'appContainer', 'installedIntegrity', 'privateSeed', 'contacts',
    'sourceIntegrity', 'setupComplete', 'nativeAuthentication', 'authEvidence',
    'postAuthIntegrity', 'authComplete', 'cleanup'})
BUILTIN_EXCEPTION_KINDS = frozenset({'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'OverflowError', 'RuntimeError', 'OSError', 'FileNotFoundError',
    'PermissionError', 'NotADirectoryError', 'IsADirectoryError', 'TimeoutError', 'otherException'})
METADATA_FAILURE_CODES = frozenset({
    'invalid_owned_container_capability',
    'invalid_owned_container_request',
    'owned_application_identity_mismatch',
    'owned_container_capability_changed',
    'owned_container_inventory_changed',
    'owned_container_inventory_count_limit',
    'owned_container_inventory_deadline',
    'owned_container_metadata_changed',
    'owned_container_not_unique',
    'owned_device_identity_changed',
    'owned_device_identity_unverified',
    'private_file_changed',
    'private_file_unavailable',
    'unexpectedMetadataFailure',
    'unidentified_owned_container_metadata',
    'unsafe_owned_container',
    'unsafe_owned_container_metadata',
    'unsafe_private_file',
    'unsupported_owned_container_layout',
})
BASE = Path(__file__).resolve().parent


class Rejected(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else 'internal'
        super().__init__(self.code)


def require(value, code='inputIntegrity'):
    if not value:
        raise Rejected(code)


def read_file(path, maximum=1024**2):
    path = Path(path)
    require(not path.is_symlink() and path.is_file())
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum)
        data = source.read(maximum + 1)
    require(len(data) <= maximum)
    return data


def sha256(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file())
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024**2):
            result.update(chunk)
    return result.hexdigest()


def decode_json(data):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    def reject_constant(_):
        raise Rejected('inputIntegrity')
    return json.loads(data, object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def json_object(path):
    value = decode_json(read_file(path))
    require(isinstance(value, dict))
    return value


def private_write(path, data):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(data)


def clean_environment():
    # Do not pass GitHub/API/signing credentials or injected loader/Python flags.
    environment = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'DEVELOPER_DIR') if key in os.environ}
    environment.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', NSUnbufferedIO='YES')
    return environment


class PrivateCommands:
    """Bound memory, child lifetime and pipe-drain time; never print command data."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.counter = 0

    def __call__(self, arguments, timeout=30, check=True):
        self.counter += 1
        path = self.directory / ('command-%04d.bin' % self.counter)
        data = bytearray()
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=clean_environment(), start_new_session=True)
        started = time.monotonic()
        exited_at = None
        failure = None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        os.set_blocking(process.stdout.fileno(), False)
        try:
            while selector.get_map():
                now = time.monotonic()
                if now - started > timeout:
                    failure = 'commandTimeout'
                    break
                if process.poll() is not None:
                    exited_at = exited_at or now
                    if now - exited_at > 0.25:
                        failure = 'commandTimeout'
                        break
                for key, _ in selector.select(min(0.1, max(0, timeout - (now - started)))):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        data.extend(chunk)
                        if len(data) > MAX_COMMAND_BYTES:
                            failure = 'commandOutputLimit'
                            break
                if failure:
                    break
            # EOF can precede normal process exit. Keep the original deadline;
            # do not kill a healthy child merely because its output pipe closed.
            if not failure and not selector.get_map() and process.poll() is None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    failure = 'commandTimeout'
                else:
                    try:
                        process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        failure = 'commandTimeout'
            if failure or process.poll() is None or selector.get_map():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            code = process.wait(timeout=5)
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        private_write(path, bytes(data[:MAX_COMMAND_BYTES]))
        if failure:
            raise Rejected(failure)
        if check and code:
            raise Rejected('commandFailure')
        return code, bytes(data)


def expected_review():
    return {'schema': 2, 'reviewed': True, 'public_testdc_auth_only': True,
        'startup_control_run': REVIEWED_STARTUP_RUN, 'application_source': SOURCE,
        'execution_scope': REVIEWED_EXECUTION_SCOPE,
        'simulator_source_run': SOURCE_RUN, 'simulator_artifact_id': ARTIFACT,
        'simulator_ipa_sha256': IPA_SHA256, 'startup_artifact_id': STARTUP_ARTIFACT,
        'startup_artifact_sha256': STARTUP_ARTIFACT_SHA256,
        'startup_result_sha256': STARTUP_RESULT_SHA256, 'startup_proof_sha256': STARTUP_PROOF_SHA256,
        'startup_authorization_launches': 2, 'startup_control_job_passed': True,
        'source_codesign_verified': True, 'main_code_unchanged': True,
        'private_selection_seeded': True, 'private_account_store_stable_after_restart': True,
        'projection_qualification_required': False,
        'driver_pins_sha256': REVIEW_PINS_SHA256,
        'driver_source_sha256': sha256(Path(__file__))}


def validate_review(gate):
    expected = expected_review()
    require(REVIEWED_STARTUP_RUN == STARTUP_CONTROL_RUN and isinstance(gate, dict) and gate == expected
        and all(type(gate[key]) is type(value) for key, value in expected.items()), 'reviewGate')


def validate_source(root, pins):
    root = Path(root).resolve(strict=True)
    expected = pins.get('application_sources')
    require(isinstance(expected, dict) and len(expected) == 11, 'sourceIntegrity')
    for relative, digest in expected.items():
        require(isinstance(relative, str) and relative.startswith('submodules/')
            and '..' not in Path(relative).parts and not Path(relative).is_absolute(), 'sourceIntegrity')
        path = root / relative
        require(path.resolve(strict=True).is_relative_to(root), 'sourceIntegrity')
        require(sha256(path) == digest, 'sourceIntegrity')
    return root


def validate_ci_sources(pins):
    expected = pins.get('ci_sources')
    require(isinstance(expected, dict) and set(expected) == {
        '.github/workflows/testdc-auth-smoke.yml',
        'tools/fork_baseline/testdc_auth/ci_gate.py'}, 'helperIntegrity')
    root = BASE.parents[2]
    for relative, digest in expected.items():
        require(sha256(root / relative) == digest, 'helperIntegrity')


def load_helpers(pins):
    directory = BASE / 'private_helpers'
    expected = pins.get('private_files')
    require(isinstance(expected, dict) and set(expected) == {
        'OfficialTestDCAuthenticationSmoke.swift', 'private_helpers/ipa_inspect.py',
        'private_helpers/storage_evidence.py', 'private_helpers/tiny_ui_test_project.py',
        'private_helpers/preserved_app.py', 'private_helpers/owned_simulator_metadata.py'}, 'helperIntegrity')
    for relative, digest in expected.items():
        require(sha256(BASE / relative) == digest, 'helperIntegrity')
    # The verified helper directory is private and modules are imported only here,
    # after the execution/review/platform gates. No helper CLI is called.
    names = ('ipa_inspect', 'storage_evidence', 'tiny_ui_test_project', 'preserved_app', 'owned_simulator_metadata')
    require(not any(name in sys.modules for name in names), 'helperIntegrity')
    result = {}
    for name in names:
        spec = importlib.util.spec_from_file_location(name, directory / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        result[name] = module
    return result


def validate_artifact(ipa, evidence):
    expected = {'schema': 1, 'repository': 'Livara-Dev/Telegram-iOS', 'source_commit': SOURCE,
        'source_run': SOURCE_RUN, 'artifact_id': ARTIFACT, 'ipa_sha256': IPA_SHA256,
        'bundle_id': BUNDLE, 'platform': 'iPhoneSimulator'}
    require(evidence == expected and sha256(ipa) == IPA_SHA256, 'artifactIdentity')


def parse_auth_evidence(data, exit_code):
    require(isinstance(data, bytes) and len(data) <= MAX_COMMAND_BYTES, 'authEvidence')
    text = data.decode('utf-8', errors='replace')
    stage_matches = list(re.finditer(re.escape(PREFIX) + r'([A-Za-z]+)(?=[\r\n]|$)', text))
    require(len(stage_matches) == text.count(PREFIX), 'authEvidence')
    stages = tuple(match[1] for match in stage_matches)
    require(all(stage in STAGES for stage in stages), 'authEvidence')
    pattern = r"Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=[ .(\r\n]|$)"
    cases = list(re.finditer(pattern, text))
    require(len(cases) == text.count("Test Case '-[") == 2, 'authEvidence')
    require([(m[1] + '.' + m[2], m[3]) for m in cases] == [(CASE, 'started'), (CASE, 'passed')], 'authEvidence')
    require(type(exit_code) is int and exit_code == 0 and stages in SUCCESS_TRACES, 'authEvidence')
    require(cases[0].end() < stage_matches[0].start() and stage_matches[-1].end() < cases[1].start(), 'authEvidence')
    return list(stages)


def snapshot_tree(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink())
    result = {}
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode))
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = sha256(path)
        require(len(result) <= 20000)
    return result


def create_project(template, destination, source):
    # Work on a private module instance; never mutate repository files.
    template.PROJECT = template.PROJECT.replace(template.TEST_BUNDLE, TEST_BUNDLE).replace(template.TARGET, TARGET)
    template.SCHEME = template.SCHEME.replace(template.TARGET, TARGET).replace('userAttachmentLifetime="keepAlways"', 'userAttachmentLifetime="keepNever"')
    template.TARGET = TARGET
    template.TEST_BUNDLE = TEST_BUNDLE
    template.TEST_SOURCE = source.decode('utf-8')
    require(template.SCHEME.count('AttachmentLifetime="keepNever"') == 2, 'driverPlan')
    require('com.apple.product-type.application' not in template.PROJECT, 'driverPlan')
    return template.create_project(destination)


def plan_target(plan):
    require(isinstance(plan, dict), 'driverPlan')
    metadata = plan.get('__xctestrun_metadata__', {})
    version = metadata.get('FormatVersion', 1) if isinstance(metadata, dict) else None
    require(type(version) is int and version in {1, 2}, 'driverPlan')
    if version == 2:
        configurations = plan.get('TestConfigurations')
        require(isinstance(configurations, list) and len(configurations) == 1 and isinstance(configurations[0], dict), 'driverPlan')
        targets = configurations[0].get('TestTargets')
    else:
        targets = [value for key, value in plan.items() if key != '__xctestrun_metadata__']
    require(isinstance(targets, list) and len(targets) == 1 and isinstance(targets[0], dict), 'driverPlan')
    target = targets[0]
    require(target.get('IsUITestBundle') is True and target.get('ProductModuleName') == TARGET, 'driverPlan')
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'OnlyTestIdentifiers', 'SkipTestIdentifiers', 'UseDestinationArtifacts'}:
                    require(not child, 'driverPlan')
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(plan)
    return target


def resolve_product(value, products, host=None):
    require(isinstance(value, str) and value and '\\' not in value and '\0' not in value, 'driverPlan')
    if '__TESTHOST__' in value:
        require(host is not None, 'driverPlan')
        value = value.replace('__TESTHOST__', str(host))
    value = value.replace('__TESTROOT__', str(products))
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts and not re.search(r'__[A-Z_]+__', value), 'driverPlan')
    require(path.is_relative_to(products) and path.resolve(strict=True) == path, 'driverPlan')
    return path


def simulator_binary(path, kind):
    data = read_file(path, 512 * 1024**2)
    def thin(value):
        if len(value) < 32:
            return False
        magic, cpu, _, filekind, count, size, _, _ = struct.unpack_from('<8I', value)
        if magic != 0xfeedfacf or cpu != 0x0100000c or filekind != kind or not 1 <= count <= 4096 or not count * 8 <= size <= len(value) - 32:
            return False
        cursor, platforms = 32, []
        for _ in range(count):
            if cursor + 8 > 32 + size:
                return False
            command, length = struct.unpack_from('<II', value, cursor)
            if length < 8 or length % 8 or cursor + length > 32 + size:
                return False
            if command == 0x32:
                if length < 24:
                    return False
                platforms.append(struct.unpack_from('<I', value, cursor + 8)[0])
            cursor += length
        return cursor == 32 + size and platforms == [7]
    if data[:4] == b'\xcf\xfa\xed\xfe':
        return thin(data)
    if kind != 2 or data[:4] not in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        return False
    wide = data[:4] == b'\xca\xfe\xba\xbf'
    count = struct.unpack_from('>I', data, 4)[0]
    entry_size = 32 if wide else 20
    if not 1 <= count <= 16 or 8 + count * entry_size > len(data):
        return False
    slices, arm64 = [], []
    for index in range(count):
        item = struct.unpack_from('>IIQQII' if wide else '>IIIII', data, 8 + index * entry_size)
        cpu, subtype, offset, size, alignment = item[:5]
        if alignment > 30 or offset % (1 << alignment) or offset < 8 + count * entry_size or size < 32 or offset + size > len(data) or (wide and item[5] != 0):
            return False
        if any(offset < end and start < offset + size for start, end in slices):
            return False
        slices.append((offset, offset + size))
        if cpu == 0x0100000c:
            arm64.append(thin(data[offset:offset + size]))
    return arm64 == [True]


def generated_products(plan, products):
    target = plan_target(plan)
    runner = resolve_product(target.get('TestHostPath'), products)
    bundle = resolve_product(target.get('TestBundlePath'), products, runner)
    require(runner.name.endswith('-Runner.app') and bundle == runner / 'PlugIns' / (TARGET + '.xctest'), 'driverIdentity')
    for directory, identifier, kind in ((runner, TEST_BUNDLE + '.xctrunner', 2), (bundle, TEST_BUNDLE, 8)):
        info = plistlib.loads(read_file(directory / 'Info.plist'))
        name = info.get('CFBundleExecutable')
        require(info.get('CFBundleIdentifier') == identifier and info.get('CFBundleSupportedPlatforms') == ['iPhoneSimulator']
            and isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name), 'driverIdentity')
        require(simulator_binary(directory / name, kind), 'driverIdentity')
    return runner, bundle


def bind_plan(plan, app, destination):
    result = copy.deepcopy(plan)
    target = plan_target(result)
    require(target.get('UITargetAppPath') in (None, ''), 'driverPlan')
    target['UITargetAppPath'] = str(app)
    for key in ('UITargetAppBundleIdentifier', 'TargetApplicationBundleID'):
        require(target.get(key) in (None, '', BUNDLE), 'driverPlan')
        target[key] = BUNDLE
    paths = target.get('DependentProductPaths', [])
    require(isinstance(paths, list) and len(paths) <= 32 and all(isinstance(p, str) for p in paths), 'driverPlan')
    target['DependentProductPaths'] = paths + [str(app)]
    environment = target.get('EnvironmentVariables', {})
    require(isinstance(environment, dict) and 'AYU_PRIVATE_TESTDC_AUTH_SMOKE' not in environment, 'driverPlan')
    environment['AYU_PRIVATE_TESTDC_AUTH_SMOKE'] = 'PUBLIC_TESTDC_AUTH_ONLY'
    target['EnvironmentVariables'] = environment
    target['SystemAttachmentLifetime'] = 'keepNever'
    target['UserAttachmentLifetime'] = 'keepNever'
    private_write(destination, plistlib.dumps(result))
    return result


def device_rows(data):
    value = decode_json(data)
    require(isinstance(value, dict) and isinstance(value.get('devices'), dict), 'simulatorOwnership')
    rows = []
    for runtime, devices in value['devices'].items():
        require(isinstance(runtime, str) and isinstance(devices, list), 'simulatorOwnership')
        for device in devices:
            require(isinstance(device, dict) and isinstance(device.get('udid'), str), 'simulatorOwnership')
            identifier = str(uuid.UUID(device['udid'])).upper()
            rows.append((identifier, runtime, device))
    require(len(rows) <= 1024 and len({row[0] for row in rows}) == len(rows), 'simulatorOwnership')
    return rows


class OwnedSimulator:
    def __init__(self, command, metadata_helper=None):
        self.command = command
        self.metadata_helper = metadata_helper
        self.name = 'AYU_TESTDC_AUTH_' + uuid.uuid4().hex
        self.identifier = None
        self.before = set()
        self.created = False

    def listing(self):
        return device_rows(self.command(['xcrun', 'simctl', 'list', 'devices', '--json'], timeout=15)[1])

    def recover(self):
        matches = [(identifier, runtime, row) for identifier, runtime, row in self.listing()
            if row.get('name') == self.name and identifier not in self.before]
        require(len(matches) <= 1, 'simulatorOwnership')
        if matches:
            identifier, runtime, row = matches[0]
            require(runtime == RUNTIME and row.get('deviceTypeIdentifier') == DEVICE, 'simulatorOwnership')
            self.identifier = identifier

    def create(self):
        self.before = {row[0] for row in self.listing()}
        self.created = True
        try:
            _, data = self.command(['xcrun', 'simctl', 'create', self.name, DEVICE, RUNTIME])
            returned = str(uuid.UUID(data.decode().strip())).upper()
            self.recover()
            require(self.identifier == returned, 'simulatorOwnership')
        except Exception:
            self.recover()
            raise
        return self.identifier

    def verify(self):
        require(self.created and self.identifier is not None and self.identifier not in self.before, 'simulatorOwnership')
        if self.metadata_helper is not None:
            proof = self.metadata_helper.owned_device_identity(self.identifier, self.name)
            require(proof.get('state') != 'rejected', 'simulatorOwnership')
            if proof.get('identity_verified') is True:
                return  # Identity only; bootstatus remains the readiness gate.
        matches = [row for row in self.listing() if row[0] == self.identifier]
        require(len(matches) == 1 and matches[0][1] == RUNTIME and matches[0][2].get('name') == self.name
            and matches[0][2].get('deviceTypeIdentifier') == DEVICE, 'simulatorOwnership')

    def cleanup(self):
        if not self.created:
            return True
        if self.identifier is None:
            self.recover()
        if self.identifier is None:
            return True  # Creation failed and the unique requested name is absent.
        self.verify()
        for arguments in (['xcrun', 'simctl', 'terminate', self.identifier, BUNDLE],
                          ['xcrun', 'simctl', 'shutdown', self.identifier]):
            try:
                self.command(arguments, timeout=60, check=False)
            except Exception:
                pass  # Still attempt deletion of only the verified owned UUID.
        self.command(['xcrun', 'simctl', 'delete', self.identifier], timeout=60)
        if self.metadata_helper is not None:
            proof = self.metadata_helper.owned_device_absence(self.identifier, self.name)
            require(proof.get('state') not in {'rejected', 'present'}, 'cleanup')
            if proof.get('absence_verified') is True:
                return True
        rows = self.listing()
        require(not any(row[0] == self.identifier or row[2].get('name') == self.name for row in rows), 'cleanup')
        return True


def owned_container(path, simulator, kind):
    path = Path(path)
    base = Path.home() / 'Library/Developer/CoreSimulator/Devices' / simulator.identifier / 'data/Containers'
    base = base.resolve(strict=True)
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_relative_to(base), 'containerOwnership')
    relative = path.relative_to(base).parts
    if kind == 'data':
        require(len(relative) == 3 and relative[:2] == ('Data', 'Application'), 'containerOwnership')
        uuid.UUID(relative[2])
    else:
        require(len(relative) == 4 and relative[:2] == ('Bundle', 'Application') and relative[3] == 'Telegram.app', 'containerOwnership')
        uuid.UUID(relative[2])
    require(path.is_dir() and not path.is_symlink(), 'containerOwnership')
    return path


def seed_fresh_private(data_path, storage_helper):
    # The caller must first bind this path to the just-created owned simulator.
    test_root = Path(data_path) / 'Library/Application Support/AyuGram/telegram-ui-tests-data'
    require(not test_root.exists() and not test_root.is_symlink(), 'privateSeed')
    require(storage_helper.seed_private_selection(data_path), 'privateSeed')
    preferences = Path(data_path) / 'Library/Preferences' / (BUNDLE + '.plist')
    require(storage_helper.valid_private(storage_helper.inspect_storage(preferences)), 'privateSeed')


def builtin_exception_kind(error):
    # Never inspect message/args/path/traceback. Only known builtin classes escape.
    kinds = {ValueError: 'ValueError', TypeError: 'TypeError', KeyError: 'KeyError',
        IndexError: 'IndexError', AttributeError: 'AttributeError', OverflowError: 'OverflowError',
        RuntimeError: 'RuntimeError', OSError: 'OSError', FileNotFoundError: 'FileNotFoundError',
        PermissionError: 'PermissionError', NotADirectoryError: 'NotADirectoryError',
        IsADirectoryError: 'IsADirectoryError', TimeoutError: 'TimeoutError'}
    return kinds.get(type(error), 'otherException')


def metadata_failure_code(error, helper):
    # Only the exact reviewed helper class can acknowledge a metadata failure.
    # Never format exceptions or inspect unknown args/messages/paths.
    if type(error) is not getattr(helper, 'PlanError', None):
        return None
    reason = getattr(error, 'code', None)
    return reason if type(reason) is str and reason in METADATA_FAILURE_CODES else None


def closed_result(setup_only=False):
    require(type(setup_only) is bool)
    scope = 'setupOnly' if setup_only else 'authOnly'
    return {'schema': 1, 'role': 'officialPublicTestDCPreAuthSetupOnly' if setup_only else 'officialPublicTestDCAuthenticationOnly',
        'execution_scope': scope, 'setup_completed': False, 'phase': 'notStarted',
        'exception_kind': None, 'metadata_failure': None, 'cleanup_failure': None, 'cleanup_exception_kind': None, 'source_commit': SOURCE,
        'source_run': SOURCE_RUN, 'artifact_id': ARTIFACT, 'ipa_sha256': IPA_SHA256,
        'startup_review_run': REVIEWED_STARTUP_RUN, 'application_recompiled': False,
        'physical_device_verified': False, 'restart_persistence_verified': False,
        'driver_compiled': False, 'signature_checks_passed': False, 'private_selection_seeded': False,
        'native_xctest_passed': False, 'authentication_completed': False,
        'stages': [], 'cleanup_passed': False, 'passed': False, 'failure': None}


def execute(ipa, artifact_evidence, source_root, gate, output, setup_only=False):
    report = closed_result(setup_only=setup_only)
    require(EXECUTION_REVIEWED, 'disabled')
    require(report['execution_scope'] == REVIEWED_EXECUTION_SCOPE, 'reviewGate')
    validate_review(json_object(gate))
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'platform')
    pins_data = read_file(BASE / 'driver-review-pins.json')
    require(hashlib.sha256(pins_data).hexdigest() == REVIEW_PINS_SHA256, 'helperIntegrity')
    pins = decode_json(pins_data)
    validate_ci_sources(pins)
    validate_source(source_root, pins)
    validate_artifact(ipa, json_object(artifact_evidence))
    helpers = load_helpers(pins)
    saved_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-private-testdc-auth-') as temporary:
            private = Path(temporary).resolve()
            private.chmod(0o700)
            command = PrivateCommands(private)
            simulator = OwnedSimulator(command, helpers['owned_simulator_metadata'])
            try:
                report['phase'] = 'toolchain'
                require(command(['xcodebuild', '-version'])[1].decode().strip() == 'Xcode 27.1\nBuild version 27A9275', 'toolchain')
                require(command(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])[1].strip() == b'27.1', 'toolchain')
                app_helpers = helpers['preserved_app']
                report['phase'] = 'artifactExtraction'
                app = app_helpers.extract_app(ipa, private / 'Telegram.app')
                report['phase'] = 'sourceSignature'
                command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
                # Same verified main-app-only control as retained c55 startup:
                # remove extensions/reseal only; preserve all application code,
                # resources, frameworks and non-App-Group entitlements.
                signing = private / 'signing'; signing.mkdir(mode=0o700)
                def signing_run(arguments, **kwargs):
                    return subprocess.run(arguments, env=clean_environment(), **kwargs)
                report['phase'] = 'privateVariant'
                proof = app_helpers.main_app_only(app, signing, run=signing_run)
                require(proof.get('main_code_unchanged') is True and proof.get('other_entitlements_preserved') is True, 'signature')
                app_snapshot = snapshot_tree(app)
                code_digest = app_helpers.main_code_digest(app)
                swift = read_file(BASE / 'OfficialTestDCAuthenticationSmoke.swift')
                report['phase'] = 'driverProject'
                project = create_project(helpers['tiny_ui_test_project'], private / 'driver', swift)
                project_snapshot = snapshot_tree(private / 'driver')
                derived = private / 'derived'
                report['phase'] = 'driverCompile'
                status, _ = command(['xcodebuild', 'build-for-testing', '-project', str(project['project']), '-scheme', TARGET,
                    '-configuration', 'Debug', '-sdk', 'iphonesimulator', '-destination', 'generic/platform=iOS Simulator',
                    '-derivedDataPath', str(derived), 'CODE_SIGNING_ALLOWED=YES', 'CODE_SIGN_IDENTITY=-', 'CODE_SIGN_STYLE=Manual',
                    'CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES', 'DEVELOPMENT_TEAM=', 'PROVISIONING_PROFILE=', 'PROVISIONING_PROFILE_SPECIFIER='], timeout=180, check=False)
                require(status == 0, 'driverCompile')
                report['driver_compiled'] = True
                require(snapshot_tree(private / 'driver') == project_snapshot, 'sourceIntegrity')
                report['phase'] = 'driverProducts'
                plans = list((derived / 'Build/Products').glob('*.xctestrun'))
                require(len(plans) == 1, 'driverPlan')
                generated = plistlib.loads(read_file(plans[0], 2 * 1024**2))
                runner, _ = generated_products(generated, plans[0].parent)
                report['phase'] = 'runnerSignature'
                command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(runner)])
                runner_snapshot = snapshot_tree(runner)
                report['signature_checks_passed'] = True
                plan_path = plans[0].parent / 'auth-only.xctestrun'
                report['phase'] = 'planBind'
                bind_plan(generated, app, plan_path)
                plan_digest = sha256(plan_path)
                report['phase'] = 'runtimeInventory'
                runtimes = decode_json(command(['xcrun', 'simctl', 'list', 'runtimes', '--json'])[1])
                require(sum(item.get('identifier') == RUNTIME and item.get('isAvailable') is True for item in runtimes.get('runtimes', [])) == 1, 'simulatorUnavailable')
                report['phase'] = 'simulatorCreate'
                identifier = simulator.create()
                report['phase'] = 'boot'
                command(['xcrun', 'simctl', 'boot', identifier], timeout=120)
                command(['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=240)
                report['phase'] = 'simulatorVerify'
                simulator.verify()
                report['phase'] = 'install'
                command(['xcrun', 'simctl', 'install', identifier, str(app)], timeout=120)
                metadata_helper = helpers['owned_simulator_metadata']
                report['phase'] = 'dataContainer'
                data_capability = metadata_helper.owned_container_capability(identifier, simulator.name, 'data', BUNDLE, 'Telegram')
                data_path = owned_container(data_capability.path, simulator, 'data')
                report['phase'] = 'appContainer'
                app_capability = metadata_helper.owned_container_capability(identifier, simulator.name, 'app', BUNDLE, 'Telegram')
                installed = owned_container(app_capability.path, simulator, 'app')
                report['phase'] = 'installedIntegrity'
                require(snapshot_tree(installed) == app_snapshot, 'sourceIntegrity')
                command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed)])
                report['phase'] = 'privateSeed'
                for capability, role in ((data_capability, 'data'), (app_capability, 'app')):
                    metadata_helper.revalidate_container_capability(capability, identifier, simulator.name, role, BUNDLE, 'Telegram')
                seed_fresh_private(data_path, helpers['storage_evidence'])
                for capability, role in ((data_capability, 'data'), (app_capability, 'app')):
                    metadata_helper.revalidate_container_capability(capability, identifier, simulator.name, role, BUNDLE, 'Telegram')
                report['private_selection_seeded'] = True
                report['phase'] = 'contacts'
                command(['xcrun', 'simctl', 'privacy', identifier, 'revoke', 'contacts', BUNDLE])
                report['phase'] = 'sourceIntegrity'
                require(snapshot_tree(app) == app_snapshot and snapshot_tree(runner) == runner_snapshot and sha256(plan_path) == plan_digest, 'sourceIntegrity')
                report['setup_completed'] = True
                report['phase'] = 'setupComplete'
                if not setup_only:
                    # One auth-only attempt. No recordings, screenshots, UI tree or
                    # account-container reads follow this call. Raw output is private.
                    report['phase'] = 'nativeAuthentication'
                    exit_code, data = command(['xcodebuild', 'test-without-building', '-xctestrun', str(plan_path),
                        '-destination', 'platform=iOS Simulator,id=' + identifier, '-parallel-testing-enabled', 'NO',
                        '-maximum-concurrent-test-simulator-destinations', '1', '-resultBundlePath', str(private / 'test.xcresult')], timeout=600, check=False)
                    report['phase'] = 'authEvidence'
                    report['stages'] = parse_auth_evidence(data, exit_code)
                    report['native_xctest_passed'] = True
                    report['authentication_completed'] = True
                    report['phase'] = 'postAuthIntegrity'
                    require(snapshot_tree(app) == app_snapshot and snapshot_tree(runner) == runner_snapshot
                        and snapshot_tree(private / 'driver') == project_snapshot and sha256(plan_path) == plan_digest
                        and sha256(ipa) == IPA_SHA256 and app_helpers.main_code_digest(app) == code_digest, 'sourceIntegrity')
                    command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
                    command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(runner)])
                    report['phase'] = 'authComplete'
            except Rejected as error:
                report['failure'] = error.code
            except Exception as error:
                report['failure'] = 'internal'
                report['exception_kind'] = builtin_exception_kind(error)
                report['metadata_failure'] = metadata_failure_code(error, helpers['owned_simulator_metadata'])
            finally:
                try:
                    report['cleanup_passed'] = simulator.cleanup()
                except Exception as error:
                    report['cleanup_passed'] = False
                    report['cleanup_failure'] = 'cleanup'
                    report['cleanup_exception_kind'] = builtin_exception_kind(error)
                    # Retain the first failure boundary; cleanup is independent
                    # evidence and must never erase the setup exception.
                    if report['failure'] is None:
                        report['phase'] = 'cleanup'
                        report['failure'] = 'cleanup'
                        report['exception_kind'] = report['cleanup_exception_kind']
            report['passed'] = not setup_only and report['failure'] is None and report['authentication_completed'] and report['native_xctest_passed'] and report['cleanup_passed']
        # Private commands/xcresult/entitlements disappear before export.
        private_write(output, (json.dumps(report, sort_keys=True, indent=2) + '\n').encode())
        return report
    finally:
        os.umask(saved_umask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--setup-only', action='store_true', help='Stop before any UI authentication test invocation')
    parser.add_argument('--ipa', type=Path)
    parser.add_argument('--artifact-evidence', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--review-gate', type=Path)
    parser.add_argument('--result', type=Path)
    arguments = parser.parse_args(argv)
    if not arguments.execute or not EXECUTION_REVIEWED:
        print('{"state":"disabled","authentication_attempted":false}')
        return 2
    try:
        require(all((arguments.ipa, arguments.artifact_evidence, arguments.source_root, arguments.review_gate, arguments.result)))
        result = execute(arguments.ipa, arguments.artifact_evidence, arguments.source_root, arguments.review_gate,
            arguments.result, setup_only=arguments.setup_only)
        setup_success = arguments.setup_only and result['execution_scope'] == 'setupOnly' \
            and result['setup_completed'] and result['cleanup_passed'] and result['failure'] is None \
            and not result['passed'] and not result['native_xctest_passed'] \
            and not result['authentication_completed'] and result['stages'] == []
        auth_success = not arguments.setup_only and result['passed']
        state = 'setupCompleted' if setup_success else ('passed' if auth_success else 'failed')
        print(json.dumps({'state': state, 'failure': result['failure'], 'execution_scope': result['execution_scope']}))
        return 0 if auth_success or setup_success else 1
    except Rejected as error:
        print(json.dumps({'state': 'rejected', 'failure': error.code}))
        return 2
    except Exception:
        print('{"state":"rejected","failure":"internal"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
