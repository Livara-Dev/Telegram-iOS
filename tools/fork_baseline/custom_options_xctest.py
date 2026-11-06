#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Compile one Apple-only UI driver for the exact retained synthetic renderer.

The Telegram application, retained host and production renderers are never
rebuilt. The one new UI case is separate from historical eight-plus-one tests.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import zlib
from xml.parsers.expat import ExpatError

import custom_options_replay as retained
import projection_replay as projection
import test_product_capture as products
import tiny_ui_test_project as driver
from installer_control import read_json, write_json

ROLE = 'retainedSyntheticSettingsNativeUI'
OUTPUT = retained.OUTPUT
MAX_PLAN = 2 * 1024**2
MAX_PNG = 16 * 1024**2
NATIVE_UI_TIMEOUT_SECONDS = 600


class DriverError(ValueError):
    pass


def redacted_driver_log(data, secrets, roots):
    """Remove known private prefixes even when attached to compiler flags."""
    text = data.decode('utf-8', errors='replace')
    for value in sorted({item for item in secrets if isinstance(item, str) and item}, key=len, reverse=True):
        text = text.replace(value, '<REDACTED_CREDENTIAL>')
    prefixes = set()
    for root in roots:
        path = Path(root)
        for candidate in (path, path.resolve(strict=False)):
            if candidate.is_absolute() and str(candidate) != '/':
                prefix = str(candidate); prefixes.add(prefix)
                # Darwin commonly reports the /var spelling even when the
                # private workspace was canonicalized to /private/var.
                if prefix.startswith(('/private/var/', '/private/tmp/')): prefixes.add(prefix[len('/private'):])
    for prefix in sorted(prefixes, key=len, reverse=True): text = text.replace(prefix, '<PRIVATE_ROOT>')
    selected = retained.redact_probe_text(text.encode('utf-8'), [])
    return selected.encode('utf-8')[:16384].decode('utf-8', errors='ignore')


def read_plan(path):
    try: value = plistlib.loads(products.read_small(path, MAX_PLAN))
    except (ValueError, TypeError, ExpatError, OverflowError): raise DriverError('ui_plan_invalid') from None
    if not isinstance(value, dict): raise DriverError('ui_plan_invalid')
    return value


def target(plan):
    if not isinstance(plan, dict): raise DriverError('ui_plan_invalid')
    metadata = plan.get('__xctestrun_metadata__', {})
    version = metadata.get('FormatVersion', 1) if isinstance(metadata, dict) else None
    if type(version) is not int or version not in {1, 2}: raise DriverError('ui_plan_unsupported')
    if version == 2:
        configurations = plan.get('TestConfigurations')
        if not isinstance(configurations, list) or len(configurations) != 1 or not isinstance(configurations[0], dict):
            raise DriverError('ui_plan_unsupported')
        targets = configurations[0].get('TestTargets')
    else: targets = [value for key, value in plan.items() if key != '__xctestrun_metadata__']
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict): raise DriverError('ui_plan_unsupported')
    result = targets[0]
    if result.get('IsUITestBundle') is not True or result.get('ProductModuleName') != driver.TARGET:
        raise DriverError('ui_plan_wrong_target')
    def unfiltered(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'OnlyTestIdentifiers', 'SkipTestIdentifiers'} and child: raise DriverError('ui_plan_filtered')
                if key == 'UseDestinationArtifacts' and child: raise DriverError('ui_plan_unsupported')
                unfiltered(child)
        elif isinstance(value, list):
            for child in value: unfiltered(child)
    unfiltered(plan)
    return result


def resolve_product(value, root, host=None):
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value: raise DriverError('ui_product_path_invalid')
    if '__TESTHOST__' in value:
        if host is None: raise DriverError('ui_product_path_invalid')
        value = value.replace('__TESTHOST__', str(host))
    value = value.replace('__TESTROOT__', str(root))
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or re.search(r'__[A-Z][A-Z0-9_]*__', value): raise DriverError('ui_product_path_invalid')
    try: relative = path.relative_to(root).as_posix()
    except ValueError: raise DriverError('ui_product_path_invalid') from None
    return products.fixed_path(root, relative)


def runner_macho(read_at, size):
    """Accept the stock runner's thin or universal ARM64 simulator slice.

    This is local to the newly generated Apple UI runner. Captured renderer and
    authored test-bundle validation remain strict thin ARM64 checks. No binary
    is thinned or rewritten, and strict codesign verification remains required.
    """
    try:
        if not 32 <= size <= products.MAX_FILE_BYTES: return False
        header = read_at(0, 32)
        if len(header) != 32: return False
        if header[:4] == b'\xcf\xfa\xed\xfe':
            return struct.unpack_from('<II', header, 4)[0] == 0x0100000C and struct.unpack_from('<I', header, 12)[0] == 2 and products.simulator_macho(read_at, size)
        formats = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
                   b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
        if header[:4] not in formats: return False
        endian, wide = formats[header[:4]]; count = struct.unpack_from(endian + 'I', header, 4)[0]
        entry_size = 32 if wide else 20; table_end = 8 + count * entry_size
        if not 1 <= count <= 16 or table_end > size: return False
        table = read_at(8, count * entry_size)
        if len(table) != count * entry_size: return False
        slices, identities, arm64 = [], set(), []
        for index in range(count):
            item = struct.unpack_from(endian + ('IIQQII' if wide else 'IIIII'), table, index * entry_size)
            cpu, subtype, offset, length, alignment = item[:5]
            if wide and item[5] != 0: return False
            if (not 0 <= alignment <= 30 or offset < table_end or length < 32 or
                    offset > size or length > size - offset or offset % (1 << alignment) or (cpu, subtype) in identities): return False
            identities.add((cpu, subtype)); slices.append((offset, offset + length))
            inner = read_at(offset, 32)
            if (len(inner) != 32 or inner[:4] != b'\xcf\xfa\xed\xfe' or
                    struct.unpack_from('<II', inner, 4) != (cpu, subtype) or struct.unpack_from('<I', inner, 12)[0] != 2): return False
            if cpu == 0x0100000C: arm64.append((offset, length))
        ordered = sorted(slices)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])) or len(arm64) != 1: return False
        offset, length = arm64[0]
        def read_slice(start, amount):
            return read_at(offset + start, amount) if 0 <= start <= length and 0 <= amount <= length - start else b''
        return products.simulator_macho(read_slice, length)
    except (OSError, ValueError, TypeError, struct.error, OverflowError): return False


def generated_products(plan, root):
    root = Path(root); item = target(plan)
    app = resolve_product(item.get('TestHostPath'), root)
    if not app.is_dir() or not app.name.endswith('-Runner.app'): raise DriverError('ui_runner_layout_unsupported')
    bundle = resolve_product(item.get('TestBundlePath'), root, app)
    if bundle != app / 'PlugIns' / (driver.TARGET + '.xctest') or not bundle.is_dir():
        raise DriverError('ui_runner_layout_unsupported')
    for directory, identifier, kind in ((app, driver.TEST_BUNDLE + '.xctrunner', 2), (bundle, driver.TEST_BUNDLE, 8)):
        info = read_plan(products.fixed_path(directory, 'Info.plist'))
        executable = info.get('CFBundleExecutable') if isinstance(info, dict) else None
        if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != identifier or
                not isinstance(executable, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', executable) or
                info.get('CFBundleSupportedPlatforms') != ['iPhoneSimulator']):
            raise DriverError('ui_product_identity_mismatch')
        code = products.fixed_path(directory, executable)
        with code.open('rb') as stream:
            read_at = lambda offset, count: (stream.seek(offset), stream.read(count))[1]
            if kind == 2: valid = runner_macho(read_at, code.stat().st_size)
            else:
                header = read_at(0, 32)
                valid = len(header) == 32 and struct.unpack_from('<I', header, 12)[0] == 8 and products.simulator_macho(read_at, code.stat().st_size)
            if not valid:
                raise DriverError('ui_product_not_arm64_simulator')
    if 'TestHostBundleIdentifier' in item and item['TestHostBundleIdentifier'] != driver.TEST_BUNDLE + '.xctrunner':
        raise DriverError('ui_runner_identity_mismatch')
    return app, bundle


def bind_plan(generated, root, app, destination):
    """Preserve Apple's runner; bind only its target app to the frozen host."""
    runner, _ = generated_products(generated, root)
    retained.checked_metadata(app)
    result = copy.deepcopy(generated); item = target(result)
    if item.get('UITargetAppPath') not in (None, ''): raise DriverError('ui_generated_application_unexpected')
    item['UITargetAppPath'] = str(app)
    for key in ('UITargetAppBundleIdentifier', 'TargetApplicationBundleID'):
        if key in item:
            if item[key] not in ('', retained.BUNDLE): raise DriverError('ui_generated_application_unexpected')
            item[key] = retained.BUNDLE
    paths = item.get('DependentProductPaths', [])
    if not isinstance(paths, list) or len(paths) > 32: raise DriverError('ui_product_path_invalid')
    for value in paths: resolve_product(value, root, runner)
    item['DependentProductPaths'] = paths + [str(app)]
    destination = Path(destination)
    if destination.parent != root or destination.exists() or destination.is_symlink(): raise DriverError('ui_plan_destination_invalid')
    destination.write_bytes(plistlib.dumps(result)); destination.chmod(0o600)
    return result


def case_results(data):
    if not isinstance(data, bytes) or len(data) > 1024**2: raise DriverError('ui_case_log_invalid')
    expected = driver.EXPECTED_CASE; started, outcomes, unexpected, duplicate = set(), {}, False, False
    # Native logging can prefix or merge events. Do not silently discard a
    # later failed/extra case simply because it did not start a fresh line.
    pattern = r"Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=[ .(\r\n]|$)"
    text = data.decode('utf-8', errors='replace'); matches = list(re.finditer(pattern, text, re.M))
    unexpected = len(matches) != text.count("Test Case '-[")
    for match in matches:
        name, state = match[1] + '.' + match[2], match[3]
        if name != expected: unexpected = True; continue
        if state == 'started':
            duplicate = duplicate or name in started; started.add(name)
        else:
            duplicate = duplicate or name in outcomes or name not in started; outcomes[name] = state
    return {'expected': 1, 'started': len(started), 'executed': len(outcomes),
            'passed': int(outcomes.get(expected) == 'passed'), 'failed': int(outcomes.get(expected) in {'failed', 'crashed'}),
            'skipped': int(outcomes.get(expected) == 'skipped'), 'unexpected_case_events': unexpected,
            'duplicate_or_unstarted_outcome': duplicate, 'cases': outcomes,
            'all_expected_passed': started == {expected} and outcomes == {expected: 'passed'} and not unexpected and not duplicate}


def png_geometry(data):
    """Validate complete bounded screenshot PNG data, not only its header."""
    if not isinstance(data, bytes) or not 33 <= len(data) <= MAX_PNG or data[:8] != b'\x89PNG\r\n\x1a\n': raise DriverError('ui_attachment_invalid')
    offset, count, width, height, row_bytes = 8, 0, None, None, None
    decoder, raw, seen_data, data_ended, ended = zlib.decompressobj(), bytearray(), False, False, False
    try:
        while offset < len(data):
            count += 1
            if count > 4096 or offset + 12 > len(data): raise DriverError('ui_attachment_invalid')
            size = struct.unpack_from('>I', data, offset)[0]; kind = data[offset + 4:offset + 8]
            end = offset + 12 + size
            if end > len(data) or re.fullmatch(b'[A-Za-z]{4}', kind) is None: raise DriverError('ui_attachment_invalid')
            payload = data[offset + 8:offset + 8 + size]
            if zlib.crc32(kind + payload) != struct.unpack_from('>I', data, offset + 8 + size)[0]: raise DriverError('ui_attachment_invalid')
            if count == 1:
                if kind != b'IHDR' or size != 13: raise DriverError('ui_attachment_invalid')
                width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
                channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
                if not 1000 <= width <= 1500 or not 2000 <= height <= 3000 or depth != 8 or channels is None or (compression, filtering, interlace) != (0, 0, 0):
                    raise DriverError('ui_attachment_invalid')
                row_bytes = width * channels + 1
            elif kind == b'IHDR': raise DriverError('ui_attachment_invalid')
            elif kind == b'IDAT':
                if data_ended: raise DriverError('ui_attachment_invalid')
                seen_data = True
                raw.extend(decoder.decompress(payload, row_bytes * height + 1 - len(raw)))
                if len(raw) > row_bytes * height or decoder.unconsumed_tail or decoder.unused_data: raise DriverError('ui_attachment_invalid')
            elif kind == b'IEND':
                if size or end != len(data) or not seen_data: raise DriverError('ui_attachment_invalid')
                ended = True
            else:
                if seen_data: data_ended = True
                if not kind[0] & 32 and kind != b'PLTE': raise DriverError('ui_attachment_invalid')
                if kind == b'PLTE' and (seen_data or not 0 < size <= 768 or size % 3): raise DriverError('ui_attachment_invalid')
            offset = end
        if not ended or not decoder.eof or len(raw) != row_bytes * height or any(raw[index] > 4 for index in range(0, len(raw), row_bytes)):
            raise DriverError('ui_attachment_invalid')
    except (ValueError, TypeError, zlib.error, struct.error): raise DriverError('ui_attachment_invalid') from None
    return {'width': width, 'height': height}


def screenshot_attachment(exported):
    """Select one named synthetic XCTest PNG; no raw manifest is published."""
    exported = Path(exported)
    value = json.loads(products.read_small(products.fixed_path(exported, 'manifest.json'), MAX_PLAN))
    found, visited = [], 0
    def walk(node, depth=0):
        nonlocal visited
        visited += 1
        if visited > 20000 or depth > 32: raise DriverError('ui_attachment_manifest_invalid')
        if isinstance(node, dict):
            labels = (node.get('suggestedHumanReadableName'), node.get('configuredName'))
            if any(isinstance(label, str) and re.fullmatch(r'(?:retained-root|failed-root)(?:_[A-Za-z0-9_.-]+|\.png)?', label) for label in labels):
                name = node.get('exportedFileName')
                if not isinstance(name, str): raise DriverError('ui_attachment_manifest_invalid')
                found.append(products.fixed_path(exported, name))
            for child in node.values():
                if isinstance(child, (dict, list)): walk(child, depth + 1)
        elif isinstance(node, list):
            for child in node: walk(child, depth + 1)
    walk(value)
    if len(found) != 1: raise DriverError('ui_attachment_not_unique')
    path = found[0]
    if path.suffix.lower() != '.png' or not path.is_file() or not 33 <= path.stat().st_size <= MAX_PNG:
        raise DriverError('ui_attachment_invalid')
    return path, png_geometry(products.read_small(path, MAX_PNG))


def execute(output=OUTPUT, root=None, command=retained.ui_native_command, popen=subprocess.Popen):
    root, output = retained.location(output, root); head, count = retained.checkout(root)
    retained.validate_proof(read_json(output / 'verification.json'), head, count)
    if platform.system() != 'Darwin': raise DriverError('macos_required')
    ipa = output / 'CustomeOptionsHost-simulator.ipa'
    if ipa.is_symlink() or not ipa.is_file() or retained.digest(ipa) != retained.IPA_SHA256: raise DriverError('ui_source_digest_mismatch')
    names = ('result.json', 'root-light.png', 'root-light.mp4', 'xctest.log', 'case-results.json', 'driver-compiler.log')
    if any((output / name).exists() or (output / name).is_symlink() for name in names): raise DriverError('ui_replay_already_exists')
    retained.inspect_ipa(ipa, 'simulator', retained.BUNDLE)
    report = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': retained.SOURCE,
        'current_application_source_commit': retained.CURRENT_SOURCE, 'historical_controller_cases_passed': 8,
        'historical_interaction_cases_passed': 1, 'new_xctest_cases_executed': 0, 'expected_new_cases': 1,
        'application_recompiled': False, 'renderer_recompiled': False, 'logged_in_settings_verified': False,
        'physical_device_verified': False, 'release_qualified': False, 'visual_review_required': True,
        'native_ui_timeout_seconds': NATIVE_UI_TIMEOUT_SECONDS,
        'device_model': 'iPhone 15 Pro', 'runtime': '27.0', 'passed': False, 'phase': 'preflight', 'commands': [],
        'source_codesign_verified': False, 'installed_host_verified': False, 'post_installed_host_verified': False,
        'installation_strategy': 'appleXcodeUITestRunner', 'installed_host_pre_execution_verified': False,
        'source_host_unchanged': False, 'driver_compiled': False, 'driver_codesign_verified': False,
        'root_title_automatically_verified': False, 'diagnostic_root_captured': False, 'recording_available': False,
        'recording_requested_before_launch': False, 'recording_readiness_observed_before_launch': False,
        'cleanup_passed': False, 'cleanup_results': {'shutdown_completed': None, 'delete_command_completed': None, 'owned_uuid_absent': None}}
    identifier = recorder = None
    secrets = [value for key, value in os.environ.items() if len(value) >= 6 and re.search(r'TOKEN|SECRET|PASSWORD|API_HASH|API_ID', key, re.I)]
    def call(label, arguments, timeout=30, require=True):
        report['phase'] = label; started = time.monotonic()
        status, data = command(arguments, timeout=timeout)
        elapsed = (time.monotonic() - started) * 1000
        elapsed_ms = int(elapsed) if 0 <= elapsed <= 86400000 else None
        fields = {'status', 'exit_code', 'timeout', 'output_limit', 'captured_bytes'}
        if (not isinstance(status, dict) or set(status) not in (fields, fields | retained.SIMCTL_STATUS_FIELDS) or
                status.get('status') not in {'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'} or
                type(status.get('timeout')) is not bool or type(status.get('output_limit')) is not bool or
                type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= 1024**2 or
                not isinstance(data, bytes) or len(data) != status['captured_bytes'] or
                not (status.get('exit_code') is None or type(status['exit_code']) is int)):
            raise DriverError('ui_command_evidence_invalid')
        if set(status) != fields: retained.validate_simctl_status(status, arguments)
        report['commands'].append({'command': label, **status, 'elapsed_ms': elapsed_ms})
        okay = status['status'] == 'completed' and status['exit_code'] == 0 and not status['timeout'] and not status['output_limit']
        if require and not okay: raise DriverError('ui_native_command_failed')
        return okay, data
    def log(name, data):
        text = redacted_driver_log(data, secrets, (root, output, private, Path.home(), Path(tempfile.gettempdir())))
        path = output / name; path.write_text(text); path.chmod(0o600)
    def attest(name, label):
        installed, proof = retained.owned_installed_bundle(identifier, name)
        report[label + '_locator'] = proof
        if proof['state'] == 'rejected': raise DriverError('ui_installed_host_invalid')
        if installed is None:
            installed = Path(call(label + '_locate', ['xcrun', 'simctl', 'get_app_container', identifier, retained.BUNDLE, 'app'], 5)[1].decode().strip())
        if not installed.is_absolute() or any(p.is_symlink() for p in (installed, *installed.parents)): raise DriverError('ui_installed_host_invalid')
        retained.checked_metadata(installed)
        if retained.host_snapshot(installed) != original: raise DriverError('ui_installed_host_invalid')
        call(label + '_signature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed)])
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-options-xctest-') as directory:
            private = Path(directory).resolve(); private.chmod(0o700)
            app = retained.extract_host(ipa, private / 'CustomeOptionsHost.app'); retained.checked_metadata(app)
            original = retained.host_snapshot(app)
            call('sourceSignature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
            report['source_codesign_verified'] = True
            if call('verifyXcode', ['xcodebuild', '-version'])[1].decode().strip() != 'Xcode 27.1\nBuild version 27A9275': raise DriverError('ui_xcode_mismatch')
            if call('verifySDK', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])[1].strip() != b'27.1': raise DriverError('ui_sdk_mismatch')
            project = driver.create_project(private / 'driver')
            report['driver_source_sha256'] = hashlib.sha256(driver.TEST_SOURCE.encode('utf-8')).hexdigest()
            derived = private / 'derived'
            okay, data = call('compileDriver', ['xcodebuild', 'build-for-testing', '-project', str(project['project']), '-scheme', driver.TARGET,
                '-configuration', 'Debug', '-sdk', 'iphonesimulator', '-destination', 'generic/platform=iOS Simulator',
                '-derivedDataPath', str(derived), 'CODE_SIGNING_ALLOWED=YES', 'CODE_SIGN_IDENTITY=-', 'CODE_SIGN_STYLE=Manual',
                'CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES', 'DEVELOPMENT_TEAM=', 'PROVISIONING_PROFILE=', 'PROVISIONING_PROFILE_SPECIFIER='], 180, False)
            log('driver-compiler.log', data)
            if not okay: raise DriverError('ui_driver_compile_failed')
            report['driver_compiled'] = True
            plans = list((derived / 'Build/Products').glob('*.xctestrun'))
            if len(plans) != 1 or plans[0].is_symlink(): raise DriverError('ui_generated_plan_not_unique')
            generated = read_plan(plans[0]); runner, _ = generated_products(generated, plans[0].parent)
            call('driverSignature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(runner)])
            report['driver_codesign_verified'] = True; runner_snapshot = projection.snapshot_tree(runner)
            # Keep __TESTROOT__ identical to the generated plan's directory.
            plan = plans[0].parent / 'retained.xctestrun'; bind_plan(generated, plans[0].parent, app, plan)
            runtime = json.loads(call('verifyRuntime', ['xcrun', 'simctl', 'list', 'runtimes', '--json'])[1])
            types = json.loads(call('verifyDeviceType', ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro'])[1])
            if (not isinstance(runtime, dict) or not isinstance(runtime.get('runtimes'), list) or
                    not isinstance(types, dict) or not isinstance(types.get('devicetypes'), list) or
                    sum(isinstance(x, dict) and x.get('identifier') == retained.RUNTIME and x.get('isAvailable') is True for x in runtime['runtimes']) != 1 or
                    sum(isinstance(x, dict) and x.get('identifier') == retained.DEVICE_TYPE for x in types['devicetypes']) != 1):
                raise DriverError('ui_device_unavailable')
            name = 'AYU_OPTIONS_ROOT_' + uuid.uuid4().hex
            identifier = call('createOwnedSimulator', ['xcrun', 'simctl', 'create', name, retained.DEVICE_TYPE, retained.RUNTIME])[1].decode().strip()
            try:
                if str(uuid.UUID(identifier)).casefold() != identifier.casefold(): raise ValueError()
            except ValueError: identifier = None; raise DriverError('ui_owned_uuid_invalid') from None
            try:
                call('requestOwnedBoot', ['xcrun', 'simctl', 'boot', identifier], 120)
                call('waitForOwnedBoot', ['xcrun', 'simctl', 'bootstatus', identifier, '-b'], 240)
                # Apple owns installation of the exact signature-checked app
                # bound by UITargetAppPath/DependentProductPaths. There is no
                # independent pre-execution installed-app verification claim.
                recorder = retained.Recording(identifier, output / 'root-light.mp4', popen, lambda _: None)
                report['phase'] = 'requestRecording'; report['recording_requested_before_launch'] = recorder.start()
                report['recording_readiness_observed_before_launch'] = recorder.ready
                result_bundle = private / 'test.xcresult'
                okay, data = call('nativeUITest', ['xcodebuild', 'test-without-building', '-xctestrun', str(plan),
                    '-destination', 'platform=iOS Simulator,id=' + identifier, '-parallel-testing-enabled', 'NO',
                    '-maximum-concurrent-test-simulator-destinations', '1', '-resultBundlePath', str(result_bundle)], NATIVE_UI_TIMEOUT_SECONDS, False)
                log('xctest.log', data); cases = case_results(data); write_json(output / 'case-results.json', cases)
                report['new_xctest_cases_executed'] = cases['executed']; report['native_ui_case_passed'] = okay and cases['all_expected_passed']
                exported = private / 'attachments'; exported.mkdir(mode=0o700)
                exported_okay, _ = call('exportUIAttachment', ['xcrun', 'xcresulttool', 'export', 'attachments', '--path', str(result_bundle), '--output-path', str(exported)], 120, False)
                try:
                    if not exported_okay: raise DriverError('ui_attachment_unavailable')
                    screenshot, geometry = screenshot_attachment(exported)
                    shutil.copyfile(screenshot, output / 'root-light.png'); (output / 'root-light.png').chmod(0o600)
                    report['root_observation'] = geometry; report['screenshot_origin'] = 'xctestAttachment'
                    report['diagnostic_root_captured'] = True
                except (OSError, ValueError, RuntimeError):
                    captured, _ = call('captureDiagnosticRoot', ['xcrun', 'simctl', 'io', identifier, 'screenshot', str(output / 'root-light.png')], 30, False)
                    if captured and (output / 'root-light.png').is_file() and not (output / 'root-light.png').is_symlink() and 0 < (output / 'root-light.png').stat().st_size <= MAX_PNG:
                        (output / 'root-light.png').chmod(0o600); report['diagnostic_root_captured'] = True
                        report['screenshot_origin'] = 'ownedSimulatorDiagnostic'
                attest(name, 'postInstalled')
                report['installed_host_verified'] = report['post_installed_host_verified'] = True
            finally:
                phase = report['phase']
                if recorder is not None:
                    try: report['recording_available'] = recorder.finish()
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): recorder._kill()
                    report['recording_return_code'] = recorder.exit_code; report['recording_diagnostic'] = recorder.diagnostic()
                    recorder = None
                for action, field in (('shutdown', 'shutdown_completed'), ('delete', 'delete_command_completed')):
                    try: report['cleanup_results'][field] = call(action + 'OwnedSimulator', ['xcrun', 'simctl', action, identifier], 30)[0]
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): report['cleanup_results'][field] = False
                if report['cleanup_results']['delete_command_completed']:
                    try: report['cleanup_results']['owned_uuid_absent'] = retained.owned_uuid_absent(call('verifyOwnedDeletion', ['xcrun', 'simctl', 'list', 'devices', '--json'], 10)[1], identifier)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): pass
                report['cleanup_passed'] = report['cleanup_results']['delete_command_completed'] is True and report['cleanup_results']['owned_uuid_absent'] is True
                report['phase'] = phase
            if retained.digest(ipa) != retained.IPA_SHA256 or retained.host_snapshot(app) != original or projection.snapshot_tree(runner) != runner_snapshot:
                raise DriverError('ui_products_changed')
            report['source_host_unchanged'] = True
            if not all(report.get(key) is True for key in ('native_ui_case_passed', 'installed_host_verified', 'post_installed_host_verified', 'cleanup_passed', 'recording_available')) or report.get('screenshot_origin') != 'xctestAttachment':
                raise DriverError('ui_native_observation_not_qualified')
            report['root_title_automatically_verified'] = True; report.update(passed=True, phase='complete')
    finally:
        for name, limit in (('root-light.png', MAX_PNG), ('root-light.mp4', 32 * 1024**2)):
            path = output / name
            if path.is_symlink() or path.is_file() and path.stat().st_size > limit: path.unlink()
        write_json(output / 'result.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('command', choices=('run',))
    parser.add_argument('--output', type=Path, default=Path(OUTPUT)); args = parser.parse_args(argv)
    try: result = execute(args.output)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'diagnostic_role': ROLE, 'passed': False, 'error': 'native_ui_replay_rejected'})); return 1
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == '__main__': raise SystemExit(main())
