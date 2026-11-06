#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Draft: derive a native XCTest plan from a tiny Apple-only control project.

All generated plans, project products and signing inputs stay private. Neither
the historical application nor the captured 41-case test module is recompiled.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import tempfile
import time
import uuid

import projection_replay as projection
import replay_artifact as artifacts
import simulator_probe
import test_product_capture as products
import tiny_xcode_project as tiny
from installer_control import read_json

OUTPUT = 'build/fork-evidence/modern-projection-plan'
SOURCE = projection.CURRENT_SOURCE
RUNTIME = projection.RUNTIME
DEVICE_TYPE = projection.DEVICE_TYPE
MAX_PLAN = 1024 * 1024
CONTROL_APP = 'AyuProjectionControl'
CONTROL_TEST = 'AyuProjectionControlTests'
CONTROL_APP_ID = 'org.livara.ayugram.tests.modern-projection-control'
CONTROL_TEST_ID = CONTROL_APP_ID + 'Tests'
CONTROL_CASE = CONTROL_TEST + '.testRunsInApplicationHost'
CONTROL_DEBUG_ENTITLEMENT = 'com.apple.security.get-task-allow'
MANIFEST_SHA256 = '6c8b70b9f78dedb309f4b48ee69cc9ca5112b63712e9fad71cfac716bc6dc965'
CAPTURE_SHA256 = 'fc9b625a66995db1f0219b49db657992e06c62491f54f62f8917be0778dbaaf8'


class PlanError(ValueError):
    pass


def _read_plan(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_PLAN:
        raise PlanError('invalid_apple_plan')
    try:
        value = plistlib.loads(path.read_bytes())
    except Exception:
        raise PlanError('invalid_apple_plan') from None
    if not isinstance(value, dict): raise PlanError('invalid_apple_plan')
    return value


def target(plan):
    """Recognize either Apple's one-target v1 or one-configuration v2 plan."""
    metadata = plan.get('__xctestrun_metadata__', {})
    if not isinstance(metadata, dict): raise PlanError('unsupported_apple_plan')
    version = metadata.get('FormatVersion', 1)
    if type(version) is not int or version not in {1, 2}: raise PlanError('unsupported_apple_plan')
    if version == 2:
        configurations = plan.get('TestConfigurations')
        if not isinstance(configurations, list) or len(configurations) != 1 or not isinstance(configurations[0], dict):
            raise PlanError('unsupported_apple_plan')
        targets = configurations[0].get('TestTargets')
        if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
            raise PlanError('unsupported_apple_plan')
        result = targets[0]
    else:
        targets = [value for key, value in plan.items() if key != '__xctestrun_metadata__']
        if len(targets) != 1 or not isinstance(targets[0], dict): raise PlanError('unsupported_apple_plan')
        result = targets[0]
    # Apple's documented app-hosted examples can omit this optional key. The
    # real product identities/path model and the native main-bundle control
    # establish hosting; an explicitly false/invalid key still fails closed.
    if result.get('IsUITestBundle') is True or ('IsAppHostedTestBundle' in result and result['IsAppHostedTestBundle'] is not True):
        raise PlanError('control_is_not_application_hosted')
    def filters(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'OnlyTestIdentifiers', 'SkipTestIdentifiers'} and child:
                    raise PlanError('test_selection_is_not_unfiltered')
                filters(child)
        elif isinstance(value, list):
            for child in value: filters(child)
    filters(plan)
    return version, result


def resolved_path(value, plan_root, host=None):
    if not isinstance(value, str) or not value or '\0' in value or '\\' in value:
        raise PlanError('invalid_apple_product_path')
    # Validate plan tokens before inserting approved filesystem paths. A valid
    # temporary/build root can itself contain double underscores.
    if any(token not in {'__TESTHOST__', '__TESTROOT__'} for token in re.findall(r'__[A-Za-z][A-Za-z0-9_]*?__', value)):
        raise PlanError('unsupported_apple_product_placeholder')
    if '__TESTHOST__' in value:
        if host is None: raise PlanError('invalid_apple_product_path')
        value = value.replace('__TESTHOST__', str(host))
    value = value.replace('__TESTROOT__', str(plan_root))
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or any(item.is_symlink() for item in (path, *path.parents)):
        raise PlanError('invalid_apple_product_path')
    try: path.relative_to(plan_root)
    except ValueError: raise PlanError('apple_product_outside_build_root') from None
    return path


def generated_products(plan, plan_root):
    version, runner = target(plan)
    if runner.get('ProductModuleName') != CONTROL_TEST:
        raise PlanError('unexpected_control_module')
    host_path = resolved_path(runner.get('TestHostPath'), plan_root)
    if host_path.is_file() and host_path.name == CONTROL_APP and host_path.parent.name == CONTROL_APP + '.app':
        host, kind = host_path.parent, 'executable'
    elif host_path.is_dir() and host_path.name == CONTROL_APP + '.app':
        host, kind = host_path, 'bundle'
    else: raise PlanError('unknown_apple_host_path_semantics')
    bundle = resolved_path(runner.get('TestBundlePath'), plan_root, host)
    if not bundle.is_dir() or bundle.name != CONTROL_TEST + '.xctest':
        raise PlanError('invalid_control_test_bundle')
    for root, identifier, executable in ((host, CONTROL_APP_ID, CONTROL_APP), (bundle, CONTROL_TEST_ID, CONTROL_TEST)):
        info = plistlib.loads(products.read_small(products.fixed_path(root, 'Info.plist')))
        if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != identifier or
                info.get('CFBundleExecutable') != executable): raise PlanError('control_product_identity_mismatch')
        binary = products.fixed_path(root, executable)
        with binary.open('rb') as stream:
            if not products.simulator_macho(lambda offset, count: (stream.seek(offset), stream.read(count))[1], binary.stat().st_size):
                raise PlanError('control_not_arm64_simulator')
    if 'TestHostBundleIdentifier' in runner and runner['TestHostBundleIdentifier'] != CONTROL_APP_ID:
        raise PlanError('control_host_identifier_mismatch')
    return version, runner, host, bundle, kind


def _copy_bundle(source, destination):
    before = projection.snapshot_tree(source)
    shutil.copytree(source, destination, symlinks=False)
    if projection.snapshot_tree(destination) != before: raise PlanError('private_product_copy_changed')
    for name, value in before.items():
        if value[0] == 'file' and (source / name).stat().st_ino == (destination / name).stat().st_ino:
            raise PlanError('private_product_hardlink')


def bind_plan(generated, plan_root, captured_app, destination):
    """Rebind the proven Apple plan without changing its observed path model."""
    plan_root, captured_app, destination = Path(plan_root), Path(captured_app), Path(destination)
    version, original, app, bundle, kind = generated_products(generated, plan_root)
    captured_info = plistlib.loads(products.read_small(products.fixed_path(captured_app, 'Info.plist')))
    test = captured_app / 'PlugIns' / products.TEST
    test_info = plistlib.loads(products.read_small(products.fixed_path(test, 'Info.plist')))
    if captured_info.get('CFBundleIdentifier') != products.BUNDLE or captured_info.get('CFBundleExecutable') != 'ProjectionTestHost':
        raise PlanError('captured_identity_mismatch')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    host_relative = app.relative_to(plan_root).with_name(products.APP)
    new_host = destination / host_relative
    new_host.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _copy_bundle(captured_app, new_host)
    try:
        bundle.relative_to(app)
        new_bundle = new_host / 'PlugIns' / products.TEST
        sibling = False
    except ValueError:
        new_bundle = destination / bundle.relative_to(plan_root).with_name(products.TEST)
        new_bundle.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _copy_bundle(test, new_bundle)
        sibling = True
    replacements = [(str(plan_root), str(destination)), (CONTROL_TEST_ID, test_info['CFBundleIdentifier']),
                    (CONTROL_APP_ID, products.BUNDLE), (CONTROL_TEST, 'ChatLocalTextFilterProjectionTests'),
                    (CONTROL_APP, 'ProjectionTestHost')]
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    def replace(value):
        if isinstance(value, str):
            for old, new in replacements: value = value.replace(old, new)
            return value
        if isinstance(value, list): return [replace(item) for item in value]
        if isinstance(value, dict): return {replace(key): replace(item) for key, item in value.items()}
        return value
    bound = replace(copy.deepcopy(generated))
    _, runner = target(bound)
    expected_host = new_host / 'ProjectionTestHost' if kind == 'executable' else new_host
    if (resolved_path(runner.get('TestHostPath'), destination) != expected_host or
            resolved_path(runner.get('TestBundlePath'), destination, new_host) != new_bundle or
            runner.get('ProductModuleName') != 'ChatLocalTextFilterProjectionTests' or
            ('TestHostBundleIdentifier' in runner and runner['TestHostBundleIdentifier'] != products.BUNDLE)):
        raise PlanError('bound_plan_identity_mismatch')
    for path in runner.get('DependentProductPaths', []):
        resolved = resolved_path(path, destination, new_host)
        if not resolved.exists(): raise PlanError('bound_dependent_product_missing')
    if projection.snapshot_tree(new_host) != projection.snapshot_tree(captured_app):
        raise PlanError('bound_host_or_test_bytes_changed')
    path = destination / 'modern.xctestrun'
    path.write_bytes(plistlib.dumps(bound)); path.chmod(0o600)
    metadata = {'apple_format_version': version, 'apple_host_path_kind': kind,
                'apple_supplies_host_bundle_identifier': 'TestHostBundleIdentifier' in original,
                'apple_supplies_app_hosted_flag': 'IsAppHostedTestBundle' in original,
                'apple_supplies_dependent_products': 'DependentProductPaths' in original,
                'apple_empty_user_dyld_value': original.get('EnvironmentVariables', {}).get('DYLD_INSERT_LIBRARIES') == '',
                'test_bundle_has_separate_product_copy': sibling, 'host_and_test_bytes_unchanged': True,
                'original_plan_executed': False, 'modern_plan_executed': False}
    return path, metadata


def control_cases(data):
    starts, outcomes, invalid = [], [], False
    pattern = re.compile(r"^Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?:[ .(].*)?$", re.M)
    for match in pattern.finditer(data.decode('utf-8', errors='replace')):
        name = match[1] + '.' + match[2]
        if name != CONTROL_CASE: invalid = True
        if match[3] == 'started': starts.append(name)
        else:
            if starts != [CONTROL_CASE]: invalid = True
            outcomes.append((name, match[3]))
    passed = not invalid and starts == [CONTROL_CASE] and outcomes == [(CONTROL_CASE, 'passed')]
    return {'expected': 1, 'started': len(starts), 'passed': sum(item[1] == 'passed' for item in outcomes),
            'all_expected_passed': passed, 'unexpected_or_duplicate_events': invalid or len(starts) > 1 or len(outcomes) > 1}


def _control_debug_entitlement_status(encoded):
    """Check only XML read from codesign's private --xml output file."""
    try:
        if not isinstance(encoded, bytes) or not 0 < len(encoded) <= 65536 or b'\0' in encoded:
            return 'nonXML'
        if encoded.count(b'<?xml') != 1 or encoded.count(b'</plist>') != 1:
            return 'nonXML'
        value = plistlib.loads(encoded)
        if isinstance(value, dict) and set(value) == {CONTROL_DEBUG_ENTITLEMENT} and value[CONTROL_DEBUG_ENTITLEMENT] is True:
            return 'verified'
        return 'unexpectedEntitlements'
    except Exception:
        return 'nonXML'


def control_debug_entitlements(encoded):
    return _control_debug_entitlement_status(encoded) == 'verified'


def read_control_debug_entitlement_status(path):
    """Read the newly extracted signature evidence, never the signing input."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or not 0 < info.st_size <= 65536):
            return 'unsafeFile'
        # The enclosing fresh temporary directory is 0700 throughout extraction.
        os.fchmod(descriptor, 0o600)
        encoded = os.read(descriptor, 65537)
        if len(encoded) != info.st_size: return 'unsafeFile'
        return _control_debug_entitlement_status(encoded)
    except FileNotFoundError:
        return 'missingFile'
    except Exception:
        return 'unsafeFile'
    finally:
        if descriptor is not None:
            os.close(descriptor)


def location(output, root):
    root = Path(root or Path(__file__).resolve().parents[2]).resolve(strict=True)
    output = Path(output); output = output if output.is_absolute() else root / output
    if output != root / OUTPUT or any(item.is_symlink() for item in (output, *output.parents)):
        raise PlanError('invalid_modern_plan_output')
    return root, output


def prepare(output=OUTPUT, root=None, api=artifacts.gh_json, fetch=artifacts.download):
    root, output = location(output, root)
    projection.verify_current_checkout(root)
    if output.exists(): raise PlanError('modern_plan_output_already_exists')
    artifact = projection.origin(projection.CURRENT_RUN, SOURCE, api)
    output.mkdir(mode=0o700, parents=True)
    with tempfile.TemporaryDirectory(prefix='ayu-modern-plan-download-') as temporary:
        archive = Path(temporary) / 'artifact.zip'; fetch(projection.CURRENT_ARTIFACT, archive)
        proof = projection.extract(archive, artifact, output / 'private-products', SOURCE)
    tiny.create_project(output / 'private-project')
    report = verification(output / 'private-products', proof)
    projection.write(output / 'verification.json', report)
    return report


def verification(original, proof):
    # Phase separation must not turn a rewritten, self-consistent manifest
    # into an authorized replacement for the fixed downloaded c55 artifact.
    if (projection.digest(original / 'manifest.json') != MANIFEST_SHA256 or
            projection.digest(original / 'capture.json') != CAPTURE_SHA256):
        raise PlanError('modern_fixed_capture_mismatch')
    return {'schema': 1, 'source_commit': SOURCE, 'source_run': projection.CURRENT_RUN,
              'artifact_id': projection.CURRENT_ARTIFACT,
              'artifact_sha256': projection.CURRENT_OUTER_SHA256, 'expected_projection_cases': 41,
              'expected_control_cases': 1, 'application_recompiled': False, 'projection_tests_recompiled': False,
              'physical_device_verified': False, 'release_qualified': False,
              'manifest_sha256': MANIFEST_SHA256, 'capture_sha256': CAPTURE_SHA256, **proof}


def run(output=OUTPUT, root=None, command=projection.native_command, signing=simulator_probe.diagnostic_command,
        probe=simulator_probe.capture):
    root, output = location(output, root); projection.verify_current_checkout(root)
    original = output / 'private-products'
    verified = projection.validate_products(original, SOURCE)
    if read_json(output / 'verification.json') != verification(original, verified):
        raise PlanError('modern_prepared_capture_mismatch')
    original_snapshot = projection.snapshot_tree(original)
    report = {'schema': 1, 'source_commit': SOURCE, 'role': 'appleGeneratedXCTestPlanControl',
              'application_recompiled': False, 'projection_tests_recompiled': False,
              'tiny_control_compiled': False, 'tiny_control_debug_entitlement_verified': False,
              'tiny_control_debug_entitlement_readback': 'notAttempted',
              'original_products_unchanged': False,
              'release_qualified': False, 'physical_device_verified': False, 'passed': False,
              'control': {'attempted': False, 'expected': 1}, 'projection': {'attempted': False, 'expected': 41},
              'commands': {}, 'phase': 'preflight'}
    secrets = tuple(value for key, value in os.environ.items() if value and re.search(r'TOKEN|SECRET|AUTH|PASSWORD|API_ID|API_HASH|SESSION|PRIVATE_KEY', key, re.I))
    def call(label, args, timeout=30, required=True):
        status, data = command(args, timeout=timeout)
        if (not isinstance(status, dict) or set(status) != {'status', 'exit_code', 'timeout', 'output_limit', 'captured_bytes'} or
                status.get('status') not in {'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'} or
                type(status.get('timeout')) is not bool or type(status.get('output_limit')) is not bool or
                type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= 1024**2 or
                not isinstance(data, bytes) or len(data) != status['captured_bytes'] or
                not (status.get('exit_code') is None or type(status['exit_code']) is int)):
            raise PlanError('modern_command_evidence_invalid')
        report['commands'][label] = status
        if required and (status.get('status') != 'completed' or status.get('exit_code') != 0):
            raise PlanError('modern_' + label + '_failed')
        return status, data
    def suite(label, plan, expected):
        identifier = None; owned = {'attempted': True, 'expected': expected, 'cleanup_completed': False}
        report[label] = owned
        try:
            name = projection.DEVICE_NAME if label == 'projection' else 'AYU_MODERN_CONTROL_' + uuid.uuid4().hex
            _, raw = call(label + '_create', ['xcrun', 'simctl', 'create', name, DEVICE_TYPE, RUNTIME])
            candidate = raw.decode().strip()
            if str(uuid.UUID(candidate)).casefold() != candidate.casefold(): raise PlanError('modern_device_invalid')
            identifier = candidate
            status, _ = call(label + '_boot', ['xcrun', 'simctl', 'bootstatus', identifier, '-b'], 240, False)
            if status.get('status') != 'completed' or status.get('exit_code') != 0:
                if (status.get('status') != 'commandFailed' or status.get('exit_code') != 149 or
                        status.get('timeout') or status.get('output_limit')): raise PlanError('modern_boot_failed')
                _, raw = call(label + '_boot_state', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], 15)
                rows = [(runtime, row) for runtime, values in json.loads(raw)['devices'].items() for row in values
                        if isinstance(row, dict) and isinstance(row.get('udid'), str) and row['udid'].casefold() == identifier.casefold()]
                if (len(rows) != 1 or rows[0][0] != RUNTIME or rows[0][1].get('name') != name or
                        rows[0][1].get('deviceTypeIdentifier') != DEVICE_TYPE or rows[0][1].get('state') != 'Booted'):
                    raise PlanError('modern_owned_boot_unverified')
            with tempfile.TemporaryDirectory(prefix='ayu-modern-xcresult-') as temporary:
                started_at = time.time()
                status, raw = call(label + '_xctest', ['xcodebuild', 'test-without-building', '-xctestrun', str(plan),
                    '-destination', 'id=' + identifier, '-destination-timeout', '30', '-parallel-testing-enabled', 'NO',
                    '-derivedDataPath', str(Path(temporary) / 'derived'), '-resultBundlePath', str(Path(temporary) / 'result.xcresult'),
                    '-collect-test-diagnostics=never'], 300, False)
                counts = control_cases(raw) if expected == 1 else projection.cases(raw)
                owned.update(counts)
                owned['passed_own_cases'] = status.get('status') == 'completed' and status.get('exit_code') == 0 and counts['all_expected_passed']
                projection.write(output / (label + '-test.log'), simulator_probe.redact_probe_text(raw, secrets), text=True)
                projection.write(output / (label + '-cases.json'), counts)
                if label == 'projection' and not owned['passed_own_cases']:
                    # Preserve the failure's lifecycle boundary before cleanup.
                    # Any later standalone launch remains diagnostic, never an
                    # XCTest outcome and never a retry of these 41 assertions.
                    owned['post_failure_probe'] = {'state': 'unavailable', 'probe_only_not_test_result': True}
                    try:
                        with tempfile.TemporaryDirectory(prefix='ayu-modern-post-failure-') as private:
                            private_root = Path(private).resolve()
                            with projection.clean_environment():
                                probe(private_root, secrets, started_at, owned_identifier=identifier, profile=projection.DEVICE_PROFILE)
                            path = private_root / 'build/fork-evidence/projection-launch-probe.json'
                            if not path.is_symlink() and path.is_file() and path.stat().st_size <= 256 * 1024:
                                value = json.loads(products.read_small(path, 256 * 1024))
                                if isinstance(value, dict) and value.get('probe_only_not_test_result') is True:
                                    owned['post_failure_probe'] = value
                    except Exception:
                        pass
        finally:
            if identifier is not None:
                outcomes = []
                for operation in ('shutdown', 'delete'):
                    try:
                        status, _ = call(label + '_' + operation, ['xcrun', 'simctl', operation, identifier], 30, False)
                        outcomes.append(status.get('status') == 'completed' and status.get('exit_code') == 0)
                    except Exception: outcomes.append(False)
                owned['cleanup_completed'] = all(outcomes)
        return owned.get('passed_own_cases') is True and owned['cleanup_completed']
    try:
        if call('xcode', ['xcodebuild', '-version'])[1].splitlines()[:1] != [b'Xcode 27.1']:
            raise PlanError('modern_xcode_mismatch')
        if call('sdk', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])[1].strip() != b'27.1':
            raise PlanError('modern_sdk_mismatch')
        report['phase'] = 'buildTinyControl'
        derived = output / 'private-derived'
        status, raw = call('build_control', ['xcodebuild', 'build-for-testing', '-project',
            str(output / 'private-project/AyuProjectionControl.xcodeproj'), '-scheme', CONTROL_APP,
            '-configuration', 'Debug', '-sdk', 'iphonesimulator', '-destination', 'generic/platform=iOS Simulator',
            '-derivedDataPath', str(derived), 'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO'], 180, False)
        projection.write(output / 'control-build.log', simulator_probe.redact_probe_text(raw, secrets), text=True)
        if status.get('status') != 'completed' or status.get('exit_code') != 0: raise PlanError('tiny_control_compile_failed')
        report['tiny_control_compiled'] = True
        plans = list((derived / 'Build/Products').glob('*.xctestrun'))
        if len(plans) != 1: raise PlanError('apple_generated_plan_not_unique')
        generated = _read_plan(plans[0]); _, _, app, bundle, kind = generated_products(generated, plans[0].parent)
        report['apple_plan'] = {'host_path_kind': kind, 'format_version': target(generated)[0],
                                'host_bundle_identifier_present': 'TestHostBundleIdentifier' in target(generated)[1]}
        # Simulator hosted tests need the ordinary debug attach entitlement.
        # This exact key is present in the captured synthetic Projection host;
        # it is applied only to our newly compiled, account-free control app.
        entitlements = output / 'private-derived/control-debug-entitlements.plist'
        entitlements.write_bytes(plistlib.dumps({CONTROL_DEBUG_ENTITLEMENT: True})); entitlements.chmod(0o600)
        call('sign_control_test', ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(bundle)])
        call('sign_control_app', ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
            '--entitlements', str(entitlements), str(app)])
        # codesign's default display is an abstract representation, not XML.
        # Its documented file destination and --xml keep signature data apart
        # from Executable=.../warning diagnostics on merged stderr/stdout.
        with tempfile.TemporaryDirectory(prefix='ayu-modern-entitlement-readback-') as directory:
            private = Path(directory).resolve(); private.chmod(0o700)
            readback = private / 'extracted-signature-entitlements.plist'
            if readback.exists() or readback.is_symlink(): raise PlanError('unsafe_control_entitlement_readback')
            report['tiny_control_debug_entitlement_readback'] = 'commandAttempted'
            status, _ = call('verify_control_entitlements', ['/usr/bin/codesign', '--display', '--entitlements',
                str(readback), '--xml', str(app)], required=False)
            if status.get('status') != 'completed' or status.get('exit_code') != 0:
                report['tiny_control_debug_entitlement_readback'] = 'commandFailed'
                raise PlanError('modern_verify_control_entitlements_failed')
            report['tiny_control_debug_entitlement_readback'] = read_control_debug_entitlement_status(readback)
            if report['tiny_control_debug_entitlement_readback'] != 'verified':
                raise PlanError('tiny_control_debug_entitlement_unverified')
        report['tiny_control_debug_entitlement_verified'] = True
        call('verify_control_signature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
        report['phase'] = 'testTinyControl'
        if not suite('control', plans[0], 1): raise PlanError('tiny_hosted_control_not_passed')
        report['phase'] = 'bindModernPlan'
        controlled = output / 'private-resealed'
        report['signing_control'] = projection.prepare_control(original, controlled, 'resealed-host', {}, secrets, signing)
        plan, metadata = bind_plan(generated, plans[0].parent, controlled / 'TEST_ROOT' / products.APP, output / 'private-modern')
        report['configuration_change'] = metadata
        frozen = projection.snapshot_tree(output / 'private-modern')
        report['phase'] = 'testProjection'
        report['configuration_change']['modern_plan_executed'] = True
        if not suite('projection', plan, 41): raise PlanError('modern_projection_not_passed')
        if projection.snapshot_tree(output / 'private-modern') != frozen:
            raise PlanError('modern_products_changed')
        report['passed'] = True; report['phase'] = 'complete'
    except Exception as error:
        report['passed'] = False
        report['failure'] = str(error) if isinstance(error, PlanError) else 'modern_plan_control_unavailable'
    finally:
        try: report['original_products_unchanged'] = projection.snapshot_tree(original) == original_snapshot
        except Exception: report['original_products_unchanged'] = False
        if not report['original_products_unchanged']: report['passed'] = False
        projection.write(output / 'result.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run')); parser.add_argument('--output', default=OUTPUT, type=Path)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args(argv)
    try: report = prepare(args.output, args.root) if args.command == 'prepare' else run(args.output, args.root)
    except Exception:
        print(json.dumps({'passed': False, 'failure': 'modern_plan_preparation_failed'})); return 1
    print(json.dumps({'passed': report.get('passed', False), 'application_recompiled': False, 'projection_tests_recompiled': False}))
    return 0 if args.command == 'prepare' or report.get('passed') else 1


if __name__ == '__main__': raise SystemExit(main())
