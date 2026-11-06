#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Draft: derive a native XCTest plan from a tiny Apple-only control project.

All generated plans, project products and signing inputs stay private. Neither
the historical application nor the captured 41-case test module is recompiled.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
import time
import uuid

import projection_replay as projection
import replay_artifact as artifacts
import simulator_probe
import test_product_capture as products
import tiny_xcode_project as tiny
import bootstrap_sample as bootstrap
from installer_control import read_json

OUTPUT = 'build/fork-evidence/modern-projection-plan'
SOURCE = projection.CURRENT_SOURCE
RUNTIME = projection.RUNTIME
DEVICE_TYPE = projection.DEVICE_TYPE
MAX_PLAN = 1024 * 1024
CONTROL_APP = 'AyuProjectionControl'
CONTROL_TEST = 'AyuProjectionControlTests'
CONTROL_APP_ID = products.BUNDLE
CONTROL_TEST_ID = tiny.TEST_BUNDLE
CONTROL_CASE = CONTROL_TEST + '.testRunsInApplicationHost'
MAX_PRIVATE = 65536
MAX_OWNED_CONTAINERS = 512
NATIVE_CONTROL_TIMEOUT_SECONDS = 600
NATIVE_PROJECTION_TIMEOUT_SECONDS = 600
NATIVE_DESTINATION_TIMEOUT_SECONDS = 600
GROUP_ROLES = ('CoreSimulatorService', 'SimulatorTrampoline', 'simctl', 'launchd_sim', 'xcodebuild', CONTROL_APP, 'other')
KERNEL_PATH = (
    "import ctypes,os,sys;"
    "lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True);"
    "lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];"
    "lib.proc_pidpath.restype=ctypes.c_int;"
    "buf=ctypes.create_string_buffer(4096);"
    "size=lib.proc_pidpath(int(sys.argv[1]),buf,4096);"
    "os.write(1,buf.value if 0<size<4096 else b'')"
)
MANIFEST_SHA256 = '6c8b70b9f78dedb309f4b48ee69cc9ca5112b63712e9fad71cfac716bc6dc965'
CAPTURE_SHA256 = 'fc9b625a66995db1f0219b49db657992e06c62491f54f62f8917be0778dbaaf8'
CAPTURED_LANGUAGES = ('ar', 'be', 'ca', 'de', 'en', 'es', 'fa', 'fr', 'id', 'it', 'ko', 'ms', 'nl', 'pl', 'pt', 'ru', 'tr', 'uk', 'uz')


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
    """Rebind the validated Apple plan without changing its observed path model."""
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


def bind_alternate_plan(generated, plan_root, alternate, destination):
    """Preserve Xcode's host path model and replace only the tested product."""
    version, original, control_app, control_test, kind = generated_products(generated, plan_root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    host = destination / control_app.relative_to(plan_root)
    host.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _copy_bundle(alternate, host)
    test = host / 'PlugIns' / products.TEST
    info = plistlib.loads(private_bytes(test / 'Info.plist'))
    if info.get('CFBundleExecutable') != 'ChatLocalTextFilterProjectionTests' or not isinstance(info.get('CFBundleIdentifier'), str):
        raise PlanError('alternate_test_identity_mismatch')
    def transformed(value):
        if isinstance(value, str):
            return value.replace(str(plan_root), str(destination)).replace(CONTROL_TEST_ID, info['CFBundleIdentifier']).replace(CONTROL_TEST, 'ChatLocalTextFilterProjectionTests')
        if isinstance(value, list): return [transformed(item) for item in value]
        if isinstance(value, dict): return {transformed(key): transformed(item) for key, item in value.items()}
        return value
    bound = transformed(copy.deepcopy(generated)); _, runner = target(bound)
    runner['TestHostPath'] = str(host / CONTROL_APP) if kind == 'executable' else str(host)
    runner['TestBundlePath'] = '__TESTHOST__/PlugIns/' + products.TEST
    if 'TestHostBundleIdentifier' in runner: runner['TestHostBundleIdentifier'] = products.BUNDLE
    paths = []
    for value in original.get('DependentProductPaths', []):
        old = resolved_path(value, plan_root, control_app)
        if old == control_test: new = test
        elif old == control_test / CONTROL_TEST: new = test / 'ChatLocalTextFilterProjectionTests'
        else:
            try: new = host / old.relative_to(control_app)
            except ValueError: raise PlanError('unrecognized_alternate_dependent_product') from None
        if not new.exists(): raise PlanError('alternate_dependent_product_missing')
        paths.append(str(new))
    if 'DependentProductPaths' in runner: runner['DependentProductPaths'] = paths
    if (runner.get('ProductModuleName') != 'ChatLocalTextFilterProjectionTests'
            or resolved_path(runner['TestBundlePath'], destination, host) != test
            or projection.snapshot_tree(host) != projection.snapshot_tree(alternate)):
        raise PlanError('alternate_bound_product_changed')
    path = destination / 'alternate.xctestrun'; path.write_bytes(plistlib.dumps(bound)); path.chmod(0o600)
    return path, host, {'harness': 'xcodeUIKitProjection', 'different_harness': True,
        'original_bazel_host_executed': False, 'original_plan_executed': False, 'modern_plan_executed': False,
        'apple_format_version': version, 'apple_host_path_kind': kind, 'captured_test_bytes_unchanged': True,
        'host_executable_name_preserved': True, 'selection_filters_added': False}


def swift_support_available(raw):
    """Inspect only the two SDK libraries used by the unchanged Swift bundle."""
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= 4096: raise ValueError()
        text = raw.decode('utf-8').strip()
        if '\n' in text or '\0' in text: raise ValueError()
        platform = Path(text)
        if not platform.is_absolute() or platform.name != 'iPhoneSimulator.platform' or '..' in platform.parts:
            raise ValueError()
        platform = platform.resolve(strict=True)
        paths = {'xctest_framework': platform / 'Developer/Library/Frameworks/XCTest.framework/XCTest',
                 'swift_xctest_support': platform / 'Developer/usr/lib/libXCTestSwiftSupport.dylib'}
        result = {}
        for name, path in paths.items():
            actual = path.resolve(strict=True)
            result[name] = actual.is_relative_to(platform) and actual.is_file() and 0 < actual.stat().st_size <= products.MAX_FILE_BYTES
        return {'source': 'pinnedXcodeSimulatorSDK', 'both_available': all(result.values()), **result}
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError):
        return {'source': 'pinnedXcodeSimulatorSDK', 'both_available': False, 'xctest_framework': False, 'swift_xctest_support': False}


def control_cases(data):
    starts, outcomes, invalid = [], [], False
    text = data.decode('utf-8', errors='replace')
    pattern = re.compile(r"Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=$|[\s.(])")
    events = list(pattern.finditer(text))
    if [match.start() for match in events] != [match.start() for match in re.finditer(r"Test Case '", text)]: invalid = True
    for match in events:
        name = match[1] + '.' + match[2]
        if name != CONTROL_CASE: invalid = True
        if match[3] == 'started': starts.append(name)
        else:
            if starts != [CONTROL_CASE]: invalid = True
            outcomes.append((name, match[3]))
    passed = not invalid and starts == [CONTROL_CASE] and outcomes == [(CONTROL_CASE, 'passed')]
    return {'expected': 1, 'started': len(starts), 'passed': sum(item[1] == 'passed' for item in outcomes),
            'all_expected_passed': passed, 'unexpected_or_duplicate_events': invalid or len(starts) > 1 or len(outcomes) > 1}


def private_bytes(path, maximum=MAX_PRIVATE, allow_empty=False):
    """Read a bounded, owned regular file without following its final link."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or not (0 if allow_empty else 1) <= info.st_size <= maximum): raise PlanError('unsafe_private_file')
        data = os.read(descriptor, maximum + 1)
        if len(data) != info.st_size: raise PlanError('private_file_changed')
        return data
    except (OSError, ValueError):
        raise PlanError('private_file_unavailable') from None
    finally:
        if descriptor is not None: os.close(descriptor)


def entitlement_document(data):
    # The authoritative generated signed .xcent is kept distinct from the
    # *-Simulated.xcent section. No debugger key is copied from a failing host.
    try:
        if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_PRIVATE or b'\0' in data
                or data.count(b'<?xml') != 1 or data.count(b'</plist>') != 1):
            raise ValueError()
        value = plistlib.loads(data)
        count = 0
        def valid(item, depth=0):
            nonlocal count
            count += 1
            if count > 512 or depth > 8: return False
            if type(item) is dict:
                return len(item) <= 128 and all(type(key) is str and len(key) <= 1024 and valid(child, depth + 1) for key, child in item.items())
            if type(item) is list: return len(item) <= 128 and all(valid(child, depth + 1) for child in item)
            if type(item) is str: return len(item) <= 4096
            if type(item) is bytes: return len(item) <= 4096
            if type(item) is int: return -(2**63) <= item < 2**64
            return type(item) is bool
        if type(value) is not dict or not valid(value): raise ValueError()
        return value
    except Exception:
        raise PlanError('invalid_private_entitlements') from None


def typed_equal(first, second):
    if type(first) is not type(second): return False
    if type(first) is dict:
        return set(first) == set(second) and all(typed_equal(first[key], second[key]) for key in first)
    if type(first) is list:
        return len(first) == len(second) and all(typed_equal(left, right) for left, right in zip(first, second))
    return first == second


def actual_signing_inputs(raw, derived, app, bundle, report, policies=None):
    """Use actual Xcode CodeSign arguments, never a guessed .xcent basename."""
    roles = {'host': (app, CONTROL_APP_ID), 'test': (bundle, CONTROL_TEST_ID)}
    matches = {role: [] for role in roles}
    report['code_sign_log_scan_completed'] = False
    for role in roles:
        report[role]['code_sign_argv'] = {'matching_invocations': 0, 'verified': False,
            'entitlement_input_kind': 'unavailable', 'entitlement_name_kind': 'notSelected'}
    if not isinstance(raw, bytes) or not 0 < len(raw) <= projection.MAX_OUTPUT:
        raise PlanError('invalid_private_build_output')
    try: lines = raw.decode('utf-8', errors='strict').splitlines()
    except UnicodeError: raise PlanError('private_build_output_not_utf8') from None
    for line in lines:
        if '/usr/bin/codesign' not in line: continue
        if len(line) > 16384: raise PlanError('codesign_argv_limit')
        try: arguments = shlex.split(line)
        except ValueError: raise PlanError('codesign_argv_unparseable') from None
        if '/usr/bin/codesign' not in arguments: continue
        if arguments[0] != '/usr/bin/codesign': raise PlanError('unsupported_codesign_prefix')
        if not 3 <= len(arguments) <= 64 or arguments[0] != '/usr/bin/codesign':
            raise PlanError('codesign_argv_unparseable')
        for role, (product, _) in roles.items():
            if str(product) in arguments:
                matches[role].append(arguments)
                report[role]['code_sign_argv']['matching_invocations'] = len(matches[role])
                if arguments[-1] != str(product): raise PlanError('nonterminal_codesign_product')
    report['code_sign_log_scan_completed'] = True
    result = {}
    root = derived / 'Build/Intermediates.noindex'
    for role, (product, identifier) in roles.items():
        observation = report[role]['code_sign_argv']
        if len(matches[role]) != 1: raise PlanError('actual_codesign_invocation_not_unique')
        arguments = matches[role][0]; position, seen, values = 1, set(), {}
        while position < len(arguments) - 1:
            option = arguments[position]; position += 1
            if option in {'--force', '-f', '--generate-entitlement-der', '--timestamp=none'}:
                key = '--force' if option == '-f' else option
                if key in seen: raise PlanError('repeated_codesign_option')
                seen.add(key); continue
            key, separator, value = option.partition('=')
            key = {'-s': '--sign', '-i': '--identifier'}.get(key, key)
            if key not in {'--sign', '--entitlements', '--identifier', '--options'} or key in seen:
                raise PlanError('unsupported_codesign_option')
            seen.add(key)
            if not separator:
                if position >= len(arguments) - 1: raise PlanError('missing_codesign_option_value')
                value = arguments[position]; position += 1
            if not value: raise PlanError('missing_codesign_option_value')
            values[key] = value
        if values.get('--sign') != '-' or ('--identifier' in values and values['--identifier'] != identifier):
            raise PlanError('actual_codesign_identity_mismatch')
        if '--options' in values and values['--options'] not in {'runtime', '0x10000', '0x0'}:
            raise PlanError('unsupported_codesign_flags')
        policy = {'options': values.get('--options'), 'generate_der': '--generate-entitlement-der' in seen,
                  'timestamp_none': '--timestamp=none' in seen, 'identifier_explicit': '--identifier' in values}
        if '--entitlements' not in values:
            observation.update(verified=True, entitlement_input_kind='none', entitlement_name_kind='none')
            result[role] = None
            if policies is not None: policies[role] = {**policy, 'entitlements': None}
            continue
        path = Path(values['--entitlements'])
        observation['entitlement_input_kind'] = 'explicitFile'
        observation['entitlement_name_kind'] = ('simulatedNamedXcent' if path.name.endswith('-Simulated.xcent') else
            'xcent' if path.suffix == '.xcent' else 'other')
        if not path.is_absolute() or '..' in path.parts or '\\' in str(path) or '\0' in str(path):
            raise PlanError('unsafe_actual_entitlement_input')
        try: relative = path.relative_to(root)
        except ValueError: raise PlanError('actual_entitlement_input_outside_intermediates') from None
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink(): raise PlanError('unsafe_actual_entitlement_input')
            if ancestor == derived: break
        else: raise PlanError('unsafe_actual_entitlement_input')
        result[role] = entitlement_document(private_bytes(path))
        if policies is not None: policies[role] = {**policy, 'entitlements': result[role]}
        observation['verified'] = True
    return result


def compiler_log(raw, secrets):
    """Retain both compiler startup and final signing diagnostics within 16KiB."""
    if not isinstance(raw, bytes) or len(raw) > projection.MAX_OUTPUT: return '[compiler output unavailable]\n'
    text = raw.decode('utf-8', errors='replace')
    literals = sorted({value for value in secrets if isinstance(value, str) and value}, key=len, reverse=True)
    if literals:
        # One pass avoids reprocessing the replacement text as another secret.
        text = re.sub('|'.join(re.escape(value) for value in literals), '<REDACTED_CREDENTIAL>', text)
    encoded = text.encode()
    def selected(tail):
        size = min(32768, len(encoded))
        while True:
            data = encoded[-size:] if tail and size else encoded[:size]
            value = simulator_probe.redact_export_stderr(data, '<UNUSED_SYNTHETIC_RESULT>', '<UNUSED_PRIVATE_EXPORT>')
            # The existing redactor caps its prefix. Shrink toward the chosen
            # end until that cap did not discard the final signing records.
            if len(value) < 16384 or size == 0: break
            size //= 2
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            section = value[-middle:] if tail else value[:middle]
            if len(json.dumps(section, ensure_ascii=True).encode()) <= 7600: low = middle
            else: high = middle - 1
        return (value[-low:] if low else '') if tail else value[:low]
    return '[first compiler segment]\n' + selected(False) + '\n[last compiler segment]\n' + selected(True)


def verify_xcode_signing(derived, app, bundle, build_output, call, report):
    report['tiny_signing'] = {'mode': 'xcodeAdhocGeneratedEntitlements', 'host': {'verified': False}, 'test': {'verified': False}}
    policies = {}
    inputs = actual_signing_inputs(build_output, derived, app, bundle, report['tiny_signing'], policies)
    for role, product, expected_id in (('host', app, CONTROL_APP_ID), ('test', bundle, CONTROL_TEST_ID)):
        expected = inputs[role]
        state = report['tiny_signing'][role]
        state['generated_signed_entitlements_present'] = expected is not None
        # Verify Xcode's real identity as well as the resource/code seal.
        _, raw = call('verify_' + role + '_adhoc', ['/usr/bin/codesign', '--display', '--verbose=4', str(product)])
        lines = raw.decode('utf-8', errors='replace').splitlines()
        if (lines.count('Signature=adhoc') != 1 or lines.count('Identifier=' + expected_id) != 1
                or lines.count('TeamIdentifier=not set') != 1): raise PlanError('tiny_xcode_adhoc_identity_unverified')
        with tempfile.TemporaryDirectory(prefix='ayu-modern-entitlement-readback-') as directory:
            private = Path(directory).resolve(); private.chmod(0o700)
            readback = private / 'extracted-signature-entitlements.plist'
            state['readback'] = 'commandAttempted'
            status, _ = call('verify_' + role + '_entitlements', ['/usr/bin/codesign', '--display', '--entitlements',
                str(readback), '--xml', str(product)], required=False)
            if status.get('status') != 'completed' or status.get('exit_code') != 0:
                state['readback'] = 'commandFailed'; raise PlanError('tiny_xcode_entitlement_display_failed')
            if not readback.exists() and not readback.is_symlink():
                actual = None; state['readback'] = 'missingFile'
            else:
                try:
                    data = private_bytes(readback, allow_empty=expected in (None, {}))
                    actual = entitlement_document(data) if data else None
                    state['readback'] = 'parsedPrivateXML' if data else 'emptyRegularFile'
                except PlanError:
                    state['readback'] = 'unsafeOrInvalidFile'; raise
            if expected in (None, {}):
                if actual not in (None, {}): raise PlanError('tiny_unsigned_entitlement_input_unverified')
            elif actual is None or not typed_equal(expected, actual):
                state['readback'] = 'generatedSignatureMismatch'; raise PlanError('tiny_generated_signature_entitlements_mismatch')
            state['readback'] = 'verified'
        call('verify_' + role + '_signature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(product)])
        state['verified'] = True
    return policies['host']


def presentation_resources(app):
    names = ['PresentationStrings.data', *[language + '.lproj/Localizable.strings' for language in CAPTURED_LANGUAGES]]
    actual = {path.parent.name for path in app.glob('*.lproj/Localizable.strings')}
    if actual != {language + '.lproj' for language in CAPTURED_LANGUAGES}:
        raise PlanError('captured_localizations_changed')
    return {name: projection.digest(products.fixed_path(app, name)) for name in names}


def prepare_alternate_host(control_app, control_test, captured_app, destination, policy, call, report):
    """Clone the coherent Xcode-built UIKit host; never modify either source."""
    if (not isinstance(policy, dict) or set(policy) != {'options', 'generate_der', 'timestamp_none', 'identifier_explicit', 'entitlements'}
            or policy['options'] not in {None, 'runtime', '0x10000', '0x0'}
            or any(type(policy[key]) is not bool for key in ('generate_der', 'timestamp_none', 'identifier_explicit'))
            or policy['entitlements'] is not None and not isinstance(policy['entitlements'], dict)):
        raise PlanError('alternate_signing_policy_unverified')
    if policy['entitlements'] is not None: entitlement_document(plistlib.dumps(policy['entitlements']))
    original_control = projection.snapshot_tree(control_app)
    original_capture = projection.snapshot_tree(captured_app)
    captured_test = captured_app / 'PlugIns' / products.TEST
    expected_test = projection.snapshot_tree(captured_test)
    resources = presentation_resources(captured_app)
    info = plistlib.loads(private_bytes(control_app / 'Info.plist'))
    if (info.get('CFBundleIdentifier') != products.BUNDLE or info.get('CFBundleExecutable') != CONTROL_APP
            or info.get('AyuGramProjectionHostDiagnostics') is not True): raise PlanError('alternate_host_identity_not_built_coherently')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _copy_bundle(control_app, destination)
    tiny_test = destination / 'PlugIns' / (CONTROL_TEST + '.xctest')
    try: relative = control_test.relative_to(control_app)
    except ValueError: relative = None
    if relative is not None and relative != Path('PlugIns') / (CONTROL_TEST + '.xctest'):
        raise PlanError('unsupported_tiny_test_placement')
    if tiny_test.exists():
        if relative is None or tiny_test.is_symlink(): raise PlanError('unexpected_alternate_test_product')
        shutil.rmtree(tiny_test)
    plugins = destination / 'PlugIns'; plugins.mkdir(mode=0o700, exist_ok=True)
    if list(plugins.iterdir()): raise PlanError('unexpected_alternate_plugin')
    _copy_bundle(captured_test, plugins / products.TEST)
    for name in resources:
        target = destination / name
        if target.exists() or target.is_symlink(): raise PlanError('alternate_resource_collision')
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = products.fixed_path(captured_app, name); shutil.copy2(source, target)
        if target.stat().st_nlink != 1 or (source.stat().st_dev, source.stat().st_ino) == (target.stat().st_dev, target.stat().st_ino):
            raise PlanError('alternate_resource_hardlink')
    before_sign = projection.snapshot_tree(destination)
    original_code = projection.main_code_digest(control_app)
    if projection.main_code_digest(destination) != original_code: raise PlanError('alternate_host_code_changed')
    report.update(harness='xcodeUIKitProjection', different_harness=True, original_bazel_host_executed=False,
        host_identity_built_from_start=True, captured_test_tree_unchanged=False, captured_resources_unchanged=False,
        host_code_normalized_unchanged=False, strict_seal_verified=False, source_control_unchanged=False)
    arguments = ['/usr/bin/codesign', '--force', '--sign', '-']
    if policy['timestamp_none']: arguments.append('--timestamp=none')
    if policy['identifier_explicit']: arguments += ['--identifier', products.BUNDLE]
    if policy['options'] is not None: arguments += ['--options', policy['options']]
    if policy['generate_der']: arguments.append('--generate-entitlement-der')
    with tempfile.TemporaryDirectory(prefix='ayu-alternate-signing-') as directory:
        private = Path(directory).resolve(); private.chmod(0o700)
        if policy['entitlements'] is not None:
            path = private / 'validated-xcode-entitlements.plist'
            path.write_bytes(plistlib.dumps(policy['entitlements'])); path.chmod(0o600)
            arguments += ['--entitlements', str(path)]
        call('sign_alternate_host', [*arguments, str(destination)])
        _, identity = call('verify_alternate_adhoc', ['/usr/bin/codesign', '--display', '--verbose=4', str(destination)])
        lines = identity.decode('utf-8', errors='replace').splitlines()
        if lines.count('Signature=adhoc') != 1 or lines.count('Identifier=' + products.BUNDLE) != 1 or lines.count('TeamIdentifier=not set') != 1:
            raise PlanError('alternate_host_adhoc_unverified')
        extracted = private / 'actual-entitlements.plist'
        call('verify_alternate_entitlements', ['/usr/bin/codesign', '--display', '--entitlements', str(extracted), '--xml', str(destination)])
        actual = None
        if extracted.exists() or extracted.is_symlink():
            value = private_bytes(extracted, allow_empty=policy['entitlements'] in (None, {}))
            actual = entitlement_document(value) if value else None
        if policy['entitlements'] in (None, {}):
            if actual not in (None, {}): raise PlanError('alternate_host_entitlements_changed')
        elif actual is None or not typed_equal(policy['entitlements'], actual): raise PlanError('alternate_host_entitlements_changed')
    call('verify_alternate_signature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)])
    after = projection.snapshot_tree(destination)
    allowed = {CONTROL_APP, '_CodeSignature/CodeResources'}
    if (set(after) != set(before_sign) or any(after[name] != before_sign[name] for name in after if name not in allowed)
            or projection.main_code_digest(destination) != original_code): raise PlanError('alternate_signing_changed_code_or_resources')
    if projection.snapshot_tree(plugins / products.TEST) != expected_test: raise PlanError('alternate_test_tree_changed')
    if presentation_resources(destination) != resources: raise PlanError('alternate_strings_changed')
    if projection.snapshot_tree(control_app) != original_control or projection.snapshot_tree(captured_app) != original_capture:
        raise PlanError('alternate_source_products_changed')
    report.update(captured_test_tree_unchanged=True, captured_resources_unchanged=True,
        captured_localizations=19, captured_resource_files=20, host_code_normalized_unchanged=True,
        strict_seal_verified=True, source_control_unchanged=True, actual_xcode_signing_policy_applied=True)
    return destination


def control_inventory(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir(): raise PlanError('unsafe_control_inventory')
    result, total = {}, 0
    for path in root.rglob('*'):
        info = path.lstat(); name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode): result[name] = ('directory', None)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid():
            total += info.st_size
            if not 0 <= info.st_size <= 32 * 1024**2 or total > 64 * 1024**2: raise PlanError('control_inventory_limit')
            result[name] = ('file', projection.digest(path))
        else: raise PlanError('unsafe_control_inventory')
        if len(result) > 256: raise PlanError('control_inventory_limit')
    return result


def owned_data_root(identifier):
    if not isinstance(identifier, str) or str(uuid.UUID(identifier)).casefold() != identifier.casefold():
        raise PlanError('invalid_owned_control_uuid')
    base = (Path.home().resolve(strict=True) / 'Library/Developer/CoreSimulator/Devices').resolve(strict=True)
    device = base / identifier
    data = device / 'data'
    for path in (device, data):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid(): raise PlanError('unsafe_owned_control_device')
    return data


def owned_device_identity(identifier, name, *, home=None, uid=None):
    """Prove only identity from this new device's metadata, never readiness."""
    proof = {'source': 'ownedDeviceMetadata', 'state': 'unavailable', 'reason': 'metadataUnavailable',
             'identity_verified': False, 'readiness_asserted': False}
    try:
        user = os.getuid() if uid is None else uid
        if (not isinstance(identifier, str) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', identifier) is None
                or not isinstance(name, str) or not name or len(name) > 128 or type(user) is not int or not 0 <= user < 2**32 - 1):
            return {**proof, 'state': 'rejected', 'reason': 'invalidOwnedIdentity'}
        root = Path(home if home is not None else Path.home()).resolve(strict=True)
        device = root / 'Library/Developer/CoreSimulator/Devices' / identifier
        path = root
        for part in (None, *device.relative_to(root).parts):
            if part is not None: path = path / part
            try: info = path.lstat()
            except FileNotFoundError: return proof
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != user:
                return {**proof, 'state': 'rejected', 'reason': 'unsafeOwnedDevicePath'}
        metadata = device / 'device.plist'
        try: info = metadata.lstat()
        except FileNotFoundError: return proof
        if not stat.S_ISREG(info.st_mode) or info.st_uid != user or info.st_nlink != 1:
            return {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceMetadata'}
        if not 0 < info.st_size <= MAX_PRIVATE: return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        try: value = plistlib.loads(private_bytes(metadata))
        except Exception: return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        if not isinstance(value, dict): return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        expected = {'UDID': identifier, 'name': name, 'deviceType': DEVICE_TYPE, 'runtime': RUNTIME}
        # A known mismatch remains a rejection even when another field is
        # absent; partial metadata cannot downgrade it to an API fallback.
        for key, wanted in expected.items():
            if key not in value: continue
            observed = value[key]
            matches = (type(observed) is str and
                (observed.casefold() == wanted.casefold() if key == 'UDID' else observed == wanted))
            if not matches: return {**proof, 'state': 'rejected', 'reason': 'ownedDeviceIdentityMismatch'}
        if any(key not in value for key in expected): return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        return {**proof, 'state': 'verified', 'reason': None, 'identity_verified': True}
    except (OSError, ValueError, TypeError, RuntimeError):
        return proof


def verify_owned_device(raw, identifier, name):
    try:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PRIVATE: return False
        value = json.loads(raw)
        rows = [(runtime, row) for runtime, devices in value['devices'].items() for row in devices
                if isinstance(row, dict) and isinstance(row.get('udid'), str) and row['udid'].casefold() == identifier.casefold()]
        return (len(rows) == 1 and rows[0][0] == RUNTIME and rows[0][1].get('name') == name
                and rows[0][1].get('deviceTypeIdentifier') == DEVICE_TYPE and rows[0][1].get('state') == 'Booted'
                and rows[0][1].get('isAvailable') is True)
    except Exception:
        return False


def container_observations(identifier, *, deadline=None):
    """Observe the two owned roles independently; return paths only privately."""
    roles = {role: {'state': 'unavailable', 'reason': 'ownedDataRootUnavailable',
                    'verified_candidates': None, 'unidentified_records': None} for role in ('app', 'data')}
    found = {}
    def expired():
        try:
            return deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)
                                             or time.monotonic() >= deadline)
        except OverflowError:
            return True
    if expired():
        for record in roles.values(): record['reason'] = 'inventoryDeadline'
        return found, roles
    try: data_root = owned_data_root(identifier)
    except Exception: return found, roles
    for role, relative in (('app', 'Containers/Bundle/Application'), ('data', 'Containers/Data/Application')):
        reason = 'filesystemUnavailable'
        def within_budget():
            nonlocal reason
            if expired():
                reason = 'inventoryDeadline'; raise PlanError('container_inventory_deadline')
        try:
            within_budget()
            root = data_root
            missing = False
            for part in Path(relative).parts:
                within_budget()
                root = root / part
                try: info = root.lstat()
                except FileNotFoundError: missing = True; break
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                    reason = 'unsafeLayout'; raise PlanError('unsafe_container')
            if missing:
                within_budget()
                roles[role].update(state='absent', reason='containerLayoutAbsent', verified_candidates=0, unidentified_records=0); continue
            candidates = []
            with os.scandir(root) as entries:
                for entry in entries:
                    within_budget()
                    if len(candidates) == MAX_OWNED_CONTAINERS:
                        reason = 'inventoryLimit'; raise PlanError('container_limit')
                    candidates.append(Path(entry.path))
            matches, unidentified = [], 0
            for candidate in candidates:
                within_budget()
                info = candidate.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                    reason = 'unsafeLayout'; raise PlanError('unsafe_container')
                metadata = candidate / '.com.apple.mobile_container_manager.metadata.plist'
                if not metadata.exists() and not metadata.is_symlink(): unidentified += 1; continue
                try: value = plistlib.loads(private_bytes(metadata, 16384))
                except Exception:
                    reason = 'unsafeOrInvalidMetadata'; raise PlanError('invalid_container_metadata') from None
                within_budget()
                if (not isinstance(value, dict) or type(value.get('MCMMetadataIdentifier')) is not str
                        or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', value['MCMMetadataIdentifier']) is None):
                    unidentified += 1; continue
                if value['MCMMetadataIdentifier'] != CONTROL_APP_ID: continue
                selected = candidate if role == 'data' else candidate / (CONTROL_APP + '.app')
                info = selected.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                    reason = 'unsafeLayout'; raise PlanError('unsafe_container')
                if role == 'app':
                    value = plistlib.loads(private_bytes(selected / 'Info.plist'))
                    if (not isinstance(value, dict) or value.get('CFBundleIdentifier') != CONTROL_APP_ID
                            or value.get('CFBundleExecutable') != CONTROL_APP):
                        reason = 'applicationIdentityMismatch'; raise PlanError('invalid_container_identity')
                matches.append(selected)
            within_budget()
            state = 'ambiguous' if len(matches) > 1 else 'unavailable' if unidentified else 'unique' if matches else 'absent'
            roles[role].update(state=state, reason='metadataUnidentified' if unidentified and state != 'ambiguous' else None if matches else 'noMatchingContainer',
                               verified_candidates=len(matches), unidentified_records=unidentified)
            if state == 'unique': found[role] = matches[0]
        except Exception:
            roles[role].update(state='unavailable', reason=reason)
    return found, roles


def control_containers(identifier):
    """Strict compatibility wrapper: both independently verified roles required."""
    paths, roles = container_observations(identifier)
    if any(roles[role]['state'] != 'unique' for role in ('app', 'data')):
        raise PlanError('owned_control_container_not_unique')
    return paths


def control_marker(container, clear=False):
    try:
        container = Path(container)
        path = container / tiny.MARKER_RELATIVE
        for part in path.relative_to(container).parents:
            if (container / part).is_symlink(): return {'state': 'unsafe'}
        if not path.exists() and not path.is_symlink(): return {'state': 'absent'}
        data = private_bytes(path, 128)
        stage = data.decode('ascii')
        if stage not in tiny.STAGES or data != stage.encode(): return {'state': 'invalid'}
        if clear:
            path.unlink()
            return {'state': 'cleared'} if not path.exists() and not path.is_symlink() else {'state': 'unsafe'}
        return {'state': 'observed', 'stage': stage}
    except Exception:
        return {'state': 'unavailable'}


def owned_executable_digest(path):
    """Hash only the small owned host, never follow a link or read a FIFO."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or not 32 <= before.st_size <= 32 * 1024**2): raise ValueError()
        digest, total = hashlib.sha256(), 0
        while chunk := os.read(descriptor, min(256 * 1024, before.st_size - total + 1)):
            total += len(chunk)
            if total > before.st_size: raise ValueError()
            digest.update(chunk)
        after = os.fstat(descriptor)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if total != before.st_size or any(getattr(before, key) != getattr(after, key) for key in fields): raise ValueError()
        return digest.hexdigest()
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if descriptor is not None: os.close(descriptor)


def group_observation(pid, group, secrets):
    result = {'state': 'unavailable', 'group_verified': False, 'same_uid': None, 'counts': {role: 0 for role in GROUP_ROLES}}
    try:
        if type(pid) is not int or group != pid or not 0 < pid < 2**31: return result
        result['group_verified'] = True
        status, raw = simulator_probe.diagnostic_command(['/bin/ps', '-g', str(group), '-o',
            'pid=,ppid=,pgid=,uid=,ucomm='], secrets, timeout=.7)
        empty_evidence_known = (isinstance(status, dict) and type(status.get('stdout_bytes')) is int and
            status['stdout_bytes'] == 0 and type(status.get('stderr_bytes')) is int and status['stderr_bytes'] == 0)
        status = bootstrap._metadata(status)
        if status['status'] == 'commandFailed' and status['exit_code'] == 1 and raw == b'' and empty_evidence_known:
            result.update(state='observed', same_uid=True); return result
        if not bootstrap._completed(status) or not isinstance(raw, bytes) or len(raw) > 16384: return result
        rows = raw.decode('ascii').splitlines()
        if len(rows) > 128: return result
        counts, seen = dict(result['counts']), set()
        for row in rows:
            fields = row.split()
            if len(fields) != 5 or any(re.fullmatch(r'[0-9]{1,10}', value) is None for value in fields[:4]): return result
            current, parent, observed_group, owner = map(int, fields[:4])
            if not 0 < current < 2**31 or not 0 <= parent < 2**31 or current in seen: return result
            if observed_group != group or owner != os.getuid():
                result.update(state='ownershipUnverified', same_uid=False); return result
            seen.add(current); role = fields[4] if fields[4] in counts else 'other'; counts[role] += 1
        result.update(state='observed', same_uid=True, counts=counts)
    except Exception:
        pass
    return result


def live_control_observation(pid, group, identifier, expected, secrets, is_alive, device_name):
    """Inspect the exact owned control without relying on live simctl IPC."""
    report = {'diagnostic_only': True, 'state': 'unavailable', 'phase': 'checkChild', 'reason': 'notObserved',
              'callback_result_received': True, 'xcode_identity_verified': False,
              'owned_container_verified': False, 'installed_bytes_match': False, 'host_executable_bytes_match': False,
              'marker': {'state': 'unavailable', 'captured_while_child_alive': False},
              'containers': {}, 'xcode_process': {'parse_state': 'notAttempted', 'identity_verified': False},
              'host_process': {'state': 'notMeasured'}, 'sample': {'target': 'none', 'state': 'notAttempted'}}
    def child_alive():
        alive = bootstrap._alive(is_alive)
        if alive is not True:
            report['reason'] = 'childNotAlive' if alive is False else 'childLivenessUnknown'
        return alive is True
    try:
        deadline = time.monotonic() + 18
        if not child_alive(): return report
        selected_pid, target, app = None, 'none', None
        # Filesystem identity is independent of a responsive ps process. It
        # grants no permission to sample either an unknown PID or another app.
        report['phase'] = 'ownedFilesystem'
        report['device_identity'] = identity = owned_device_identity(identifier, device_name)
        if identity['identity_verified'] is True:
            containers, report['containers'] = container_observations(identifier, deadline=deadline)
            report['owned_container_verified'] = all(role in containers for role in ('app', 'data'))
            app = containers.get('app')
            if not child_alive(): return report
            if 'data' in containers:
                marker = control_marker(containers['data'])
                if not child_alive(): return report
                report['marker'] = {**marker, 'captured_while_child_alive': True}
            if app is not None:
                observed_digest = owned_executable_digest(app / CONTROL_APP)
                report['host_executable_bytes_match'] = observed_digest is not None and observed_digest == expected
        if not child_alive(): return report
        report['phase'] = 'verifyXcodeProcess'
        status, raw = bootstrap._run(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,uid=,ucomm='], secrets, 2, deadline)
        proof = bootstrap._process_snapshot(raw, pid, group, os.getuid()) if bootstrap._completed(status) else {}
        report['xcode_identity_verified'] = proof.get('owned_xcodebuild_verified') is True
        report['xcode_process'] = {'command': status, 'parse_state': proof.get('state', 'notParsed'),
                                   'identity_verified': report['xcode_identity_verified']}
        if report['xcode_identity_verified']: selected_pid, target = pid, 'xcodebuild'
        if not child_alive(): return report
        report['phase'] = 'selectHostProcess'
        try:
            if app is not None and report['host_executable_bytes_match']:
                status, raw = bootstrap._run(['/usr/bin/pgrep', '-x', '-u', str(os.getuid()), CONTROL_APP], secrets, 1, deadline)
                report['host_process'] = {'state': 'unavailable'}
                if bootstrap._completed(status) and re.fullmatch(rb'[0-9]{1,10}\n?', raw):
                    candidate = int(raw)
                    if 0 < candidate < 2**31:
                        path_status, path = bootstrap._run([sys.executable, '-c', KERNEL_PATH, str(candidate)], secrets, 1, deadline)
                        uid_status, uid = bootstrap._run(['/bin/ps', '-p', str(candidate), '-o', 'pid=,uid='], secrets, 1, deadline)
                        if (bootstrap._completed(path_status) and bootstrap._completed(uid_status)
                                and path == str(app / CONTROL_APP).encode()
                                and uid.split() == [str(candidate).encode(), str(os.getuid()).encode()]):
                            selected_pid, target = candidate, 'tinyControl'
                            report['host_process'] = {'state': 'verifiedOwnedPathAndUID'}
        except Exception:
            pass
        if not child_alive(): return report
        if selected_pid is None:
            report.update(state='partial' if report['containers'] else 'unavailable', reason='samplingTargetUnverified')
            return report
        report['phase'] = 'sampleVerifiedProcess'
        with tempfile.TemporaryDirectory(prefix='ayu-modern-live-sample-') as directory:
            root = Path(directory).resolve(); root.chmod(0o700); path = root / 'sample.txt'
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600); os.close(descriptor)
            status, _ = bootstrap._run([sys.executable, '-c', bootstrap.SAMPLE_EXEC_WRAPPER, str(selected_pid), str(path)], secrets, 10, deadline)
            selection = {'state': 'unavailable', 'wait_categories': {key: 0 for key in bootstrap.WAIT_CATEGORIES},
                         'main_thread_observed': False, 'complete_stacks_verified': False}
            if bootstrap._completed(status):
                data = bootstrap._sample_file(path, os.getuid())
                if data is not None: selection = bootstrap.parse_sample(data, secrets)
            if not child_alive(): return report
            report['sample'] = {'target': target, 'state': 'observed' if selection['state'] == 'parsed' else 'unavailable',
                                'command': status, 'selection': selection}
        report.update(state='observed' if report['sample']['state'] == 'observed' else 'partial',
                      phase='complete', reason='observed' if report['sample']['state'] == 'observed' else 'sampleUnavailable')
    except Exception:
        report['reason'] = 'observationUnavailable'
    return report


def public_live_observation(value):
    """Never export arbitrary callback strings, process IDs or filesystem paths."""
    value = value if isinstance(value, dict) else {}
    def choice(item, choices, fallback):
        return item if type(item) is str and item in choices else fallback
    result = {'diagnostic_only': True, 'state': choice(value.get('state'),
        {'unavailable', 'observed', 'partial', 'notTriggered'}, 'unavailable')}
    result['callback_result_received'] = value.get('callback_result_received') is True
    result['phase'] = choice(value.get('phase'), {'checkChild', 'ownedFilesystem', 'verifyXcodeProcess',
        'selectHostProcess', 'sampleVerifiedProcess', 'complete'}, 'nativeSupervisor')
    result['reason'] = choice(value.get('reason'), {'notObserved', 'childNotAlive', 'childLivenessUnknown', 'samplingTargetUnverified',
        'observed', 'sampleUnavailable', 'observationUnavailable', 'commandEndedBeforeTrigger', 'expectedCaseStarted',
        'ownedProcessGroupUnverified', 'diagnosticDidNotFinishBeforeDeadline', 'diagnosticOutputLimit',
        'diagnosticFailed', 'diagnosticWorkerUnavailable'}, 'unreported')
    for key in ('xcode_identity_verified', 'owned_container_verified', 'installed_bytes_match', 'host_executable_bytes_match'):
        result[key] = value.get(key) is True
    marker = value.get('marker', {}); marker = marker if isinstance(marker, dict) else {}
    state = choice(marker.get('state'), {'absent', 'observed', 'unsafe', 'invalid', 'unavailable'}, 'unavailable')
    result['marker'] = {'state': state, 'captured_while_child_alive': marker.get('captured_while_child_alive') is True}
    if state == 'observed' and marker.get('stage') in tiny.STAGES: result['marker']['stage'] = marker['stage']
    elif state == 'observed': result['marker']['state'] = 'unavailable'
    containers = value.get('containers', {}); containers = containers if isinstance(containers, dict) else {}
    result['containers'] = {}
    for role in ('app', 'data'):
        record = containers.get(role, {}); record = record if isinstance(record, dict) else {}
        selected = {'state': choice(record.get('state'), {'unique', 'absent', 'ambiguous', 'unavailable'}, 'unavailable'),
            'reason': choice(record.get('reason'), {'ownedDataRootUnavailable', 'containerLayoutAbsent', 'unsafeLayout',
                'inventoryLimit', 'inventoryDeadline', 'unsafeOrInvalidMetadata', 'applicationIdentityMismatch', 'metadataUnidentified',
                'noMatchingContainer', 'filesystemUnavailable'}, None)}
        for key in ('verified_candidates', 'unidentified_records'):
            count = record.get(key); selected[key] = count if type(count) is int and 0 <= count <= MAX_OWNED_CONTAINERS else None
        if ((selected['state'] == 'unique' and (selected['verified_candidates'] != 1 or selected['unidentified_records'] != 0))
                or selected['state'] == 'ambiguous' and not (type(selected['verified_candidates']) is int and selected['verified_candidates'] >= 2)
                or selected['state'] == 'absent' and (selected['verified_candidates'] != 0 or selected['unidentified_records'] != 0)):
            selected.update(state='unavailable', reason=None)
        result['containers'][role] = selected
    process = value.get('xcode_process', {}); process = process if isinstance(process, dict) else {}
    result['xcode_process'] = {'parse_state': choice(process.get('parse_state'),
        {'notAttempted', 'notParsed', 'observed', 'ownershipUnverified', 'invalidPayload'}, 'notAttempted'),
        'identity_verified': process.get('identity_verified') is True}
    if isinstance(process.get('command'), dict): result['xcode_process']['command'] = bootstrap._metadata(process['command'])
    host = value.get('host_process', {}); host = host if isinstance(host, dict) else {}
    result['host_process'] = {'state': choice(host.get('state'),
        {'notMeasured', 'unavailable', 'notObserved', 'verifiedOwnedPathAndUID'}, 'unavailable')}
    sample = value.get('sample', {}); sample = sample if isinstance(sample, dict) else {}
    result['sample'] = {'target': choice(sample.get('target'), {'none', 'xcodebuild', 'tinyControl'}, 'none'),
                        'state': choice(sample.get('state'), {'notAttempted', 'observed', 'unavailable'}, 'unavailable')}
    if isinstance(sample.get('command'), dict): result['sample']['command'] = bootstrap._metadata(sample['command'])
    selection = sample.get('selection', {}); selection = selection if isinstance(selection, dict) else {}
    counts = selection.get('wait_categories', {}); counts = counts if isinstance(counts, dict) else {}
    result['sample']['selection'] = {'main_thread_observed': selection.get('main_thread_observed') is True,
        'complete_stacks_verified': False, 'wait_categories': {key: counts.get(key) if type(counts.get(key)) is int and
            0 <= counts[key] <= 100000 else 0 for key in bootstrap.WAIT_CATEGORIES}}
    return result


def public_group_observation(value):
    try:
        if not isinstance(value, dict) or value.get('received_before_retirement') is not True: raise ValueError()
        content = {key: child for key, child in value.items() if key != 'received_before_retirement'}
        return {**projection._closed_post_exit_diagnostic(content), 'received_before_retirement': True}
    except Exception:
        return {'state': 'unavailable', 'received_before_retirement': False}


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
    report = {'schema': 3, 'source_commit': SOURCE, 'role': 'alternateXcodeUIKitHost',
              'application_recompiled': False, 'projection_tests_recompiled': False,
              'tiny_control_compiled': False, 'tiny_control_signing_verified': False,
              'tiny_control_entitlement_readback': 'notAttempted',
              'original_products_unchanged': False,
              'release_qualified': False, 'physical_device_verified': False, 'passed': False,
              'different_harness': True, 'original_bazel_host_executed': False, 'tiny_control_language': 'Swift',
              'native_control_timeout_seconds': NATIVE_CONTROL_TIMEOUT_SECONDS,
              'native_projection_timeout_seconds': NATIVE_PROJECTION_TIMEOUT_SECONDS,
              'native_destination_timeout_seconds': NATIVE_DESTINATION_TIMEOUT_SECONDS,
              'control': {'attempted': False, 'expected': 1}, 'projection': {'attempted': False, 'expected': 41},
              'commands': {}, 'command_timing': {}, 'post_exit_group_observations': {}, 'phase': 'preflight'}
    secrets = tuple(value for key, value in os.environ.items() if value and re.search(r'TOKEN|SECRET|AUTH|PASSWORD|API_ID|API_HASH|SESSION|PRIVATE_KEY', key, re.I))
    def call(label, args, timeout=30, required=True, groups=False, observer=None):
        options = {}
        if groups: options['post_exit_diagnostic'] = lambda pid, group: group_observation(pid, group, secrets)
        if observer is not None: options['bootstrap_diagnostic'] = observer
        command_started = time.monotonic()
        status, data = command(args, timeout=timeout, **options)
        elapsed = time.monotonic() - command_started
        report['command_timing'][label] = ({'state': 'measured', 'elapsed_ms': int(elapsed * 1000)}
            if type(elapsed) in (int, float) and 0 <= elapsed <= 86400 else {'state': 'unavailable'})
        if isinstance(status, dict):
            status = dict(status)
            if groups: report['post_exit_group_observations'][label] = public_group_observation(status.pop('post_exit_diagnostic', None))
            observation = status.pop('bootstrap_diagnostic', None) if observer is not None else None
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
        return ({**status, 'bootstrap_diagnostic': public_live_observation(observation)} if observer is not None else status), data
    used_identifiers = set()
    def suite(label, plan, expected, observed_app):
        identifier = None; owned = {'attempted': True, 'expected': expected, 'cleanup_completed': False,
            'xctest_command_attempted': False, 'tests_started': False, 'started': 0, 'passed': 0,
            'boot_requested': False, 'bootstatus_prerequisite_used': False,
            'xcode_waits_for_device_readiness': False, 'readiness_asserted': False}
        report[label] = owned
        try:
            name = projection.DEVICE_NAME if label == 'projection' else 'AYU_MODERN_CONTROL_' + uuid.uuid4().hex
            _, raw = call(label + '_create', ['xcrun', 'simctl', 'create', name, DEVICE_TYPE, RUNTIME], groups=True)
            candidate = raw.decode().strip()
            if str(uuid.UUID(candidate)).casefold() != candidate.casefold(): raise PlanError('modern_device_invalid')
            if candidate.casefold() in used_identifiers: raise PlanError('modern_owned_device_reused')
            used_identifiers.add(candidate.casefold())
            identifier = candidate
            call(label + '_boot_request', ['xcrun', 'simctl', 'boot', identifier], 30, groups=True)
            # The acknowledged request does not prove a completed boot. The
            # single bounded Xcode command owns readiness, install and launch.
            owned['boot_requested'] = True
            identity = owned_device_identity(identifier, name)
            owned['device_identity'] = identity
            if identity['state'] == 'rejected': raise PlanError('modern_owned_simulator_identity_rejected')
            if identity['identity_verified'] is not True:
                status, raw = call(label + '_owned_identity', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], 15, False)
                verified = status.get('status') == 'completed' and status.get('exit_code') == 0 and verify_owned_device(raw, identifier, name)
                owned['identity_fallback'] = {'source': 'boundedSimctlInventory', 'identity_verified': verified,
                                            'readiness_asserted': False}
                if not verified: raise PlanError('modern_owned_simulator_identity_unverified')
            owned.update(owned_simulator_identity_verified=True, manual_prewarm_performed=False,
                         xcode_manages_install_and_launch=True, gui_assisted=False)
            expected_executable = projection.digest(observed_app / CONTROL_APP)
            owned['host_profile'] = 'xcodeUIKitControl' if label == 'control' else 'xcodeUIKitProjection'
            observer = lambda pid, group, alive: live_control_observation(pid, group, identifier, expected_executable, secrets, alive, name)
            with tempfile.TemporaryDirectory(prefix='ayu-modern-xcresult-') as temporary:
                started_at = time.time()
                owned['xctest_command_attempted'] = True
                owned['xcode_waits_for_device_readiness'] = True
                status, raw = call(label + '_xctest', ['xcodebuild', 'test-without-building', '-xctestrun', str(plan),
                    '-destination', 'id=' + identifier, '-destination-timeout', str(NATIVE_DESTINATION_TIMEOUT_SECONDS), '-parallel-testing-enabled', 'NO',
                    '-derivedDataPath', str(Path(temporary) / 'derived'), '-resultBundlePath', str(Path(temporary) / 'result.xcresult'),
                    '-collect-test-diagnostics=never'], NATIVE_PROJECTION_TIMEOUT_SECONDS if label == 'projection' else NATIVE_CONTROL_TIMEOUT_SECONDS, False, observer=observer)
                if observer is not None: owned['live_bootstrap'] = status.pop('bootstrap_diagnostic')
                counts = control_cases(raw) if expected == 1 else projection.cases(raw)
                owned.update(counts)
                owned['tests_started'] = counts.get('started', 0) > 0
                owned['passed_own_cases'] = status.get('status') == 'completed' and status.get('exit_code') == 0 and counts['all_expected_passed']
                if not owned['passed_own_cases']:
                    owned['failure'] = 'tiny_hosted_control_not_passed' if label == 'control' else 'modern_projection_not_passed'
                projection.write(output / (label + '-test.log'), simulator_probe.redact_probe_text(raw, secrets), text=True)
                projection.write(output / (label + '-cases.json'), counts)
                if not owned['passed_own_cases']:
                    # The captured-host probe hardcodes a different executable.
                    # This alternate harness uses only its exact owned metadata.
                    owned['post_test_marker'] = {'state': 'unavailable', 'captured_after_xcode_exit': True}
                    owned['post_test_filesystem'] = {'state': 'unavailable', 'captured_after_xcode_exit': True}
                    try:
                        identity = owned_device_identity(identifier, name)
                        if identity['identity_verified'] is True:
                            containers, roles = container_observations(identifier)
                            selected = public_live_observation({'containers': roles})['containers']
                            owned['post_test_filesystem'] = {'state': 'observed', 'containers': selected, 'captured_after_xcode_exit': True}
                            if 'data' in containers:
                                owned['post_test_marker'] = {**control_marker(containers['data']), 'captured_after_xcode_exit': True}
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
            '-derivedDataPath', str(derived), 'CODE_SIGNING_ALLOWED=YES', 'CODE_SIGN_IDENTITY=-',
            'CODE_SIGN_STYLE=Manual', 'CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES',
            'DEVELOPMENT_TEAM=', 'PROVISIONING_PROFILE=', 'PROVISIONING_PROFILE_SPECIFIER='], 180, False)
        projection.write(output / 'control-build.log', compiler_log(raw, (*secrets, str(output))), text=True)
        if status.get('status') != 'completed' or status.get('exit_code') != 0: raise PlanError('tiny_control_compile_failed')
        report['tiny_control_compiled'] = True
        plans = list((derived / 'Build/Products').glob('*.xctestrun'))
        if len(plans) != 1: raise PlanError('apple_generated_plan_not_unique')
        generated = _read_plan(plans[0]); _, _, app, bundle, kind = generated_products(generated, plans[0].parent)
        report['apple_plan'] = {'host_path_kind': kind, 'format_version': target(generated)[0],
                                'host_bundle_identifier_present': 'TestHostBundleIdentifier' in target(generated)[1]}
        policy = verify_xcode_signing(derived, app, bundle, raw, call, report)
        report['tiny_control_entitlement_readback'] = 'matchedActualCodeSignInput'
        report['tiny_control_signing_verified'] = True
        control_source = projection.snapshot_tree(app)
        report['phase'] = 'testTinyControl'
        control_passed = suite('control', plans[0], 1, app)
        if report['control']['cleanup_completed'] is not True: raise PlanError('tiny_control_cleanup_unverified')
        if projection.snapshot_tree(original) != original_snapshot: raise PlanError('original_products_changed_before_projection')
        if projection.snapshot_tree(app) != control_source: raise PlanError('source_control_host_changed')
        _, platform = call('swift_sdk_platform', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-platform-path'])
        report['swift_xctest_dependencies'] = swift_support_available(platform)
        if not report['swift_xctest_dependencies']['both_available']: raise PlanError('pinned_swift_xctest_support_unavailable')
        report['phase'] = 'bindModernPlan'
        report['alternate_host'] = {}
        alternate = prepare_alternate_host(app, bundle, original / 'TEST_ROOT' / products.APP,
            output / 'private-alternate' / (CONTROL_APP + '.app'), policy, call, report['alternate_host'])
        plan, bound_host, metadata = bind_alternate_plan(generated, plans[0].parent, alternate, output / 'private-modern')
        report['configuration_change'] = metadata
        frozen = projection.snapshot_tree(output / 'private-modern')
        report['phase'] = 'testProjection'
        report['configuration_change']['modern_plan_executed'] = True
        projection_passed = suite('projection', plan, 41, bound_host)
        if projection.snapshot_tree(output / 'private-modern') != frozen:
            raise PlanError('modern_products_changed')
        if not control_passed: raise PlanError('tiny_hosted_control_not_passed')
        if not projection_passed: raise PlanError('modern_projection_not_passed')
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
