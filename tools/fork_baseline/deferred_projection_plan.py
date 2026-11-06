#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Experimental app-hosted deferred loading of the unchanged c55 XCTest bundle.

This distinct harness never qualifies the application or the original Bazel
host. Every payload outcome must also exist in the actual native result bundle.
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
import struct
import sys
import tempfile
import time
import uuid

import modern_projection_plan as modern
import projection_replay as projection
import simulator_probe
import tiny_xcode_project as tiny

OUTPUT = modern.OUTPUT
SOURCE = projection.CURRENT_SOURCE
PROFILE = 'experimentalDeferredPayloadSuite'
WRAPPER = 'AyuDeferredProjectionLoaderTests.testExecutesRetainedProjectionBundleAfterActiveUIKit'
WRAPPER_SOURCE = 'deferred_projection_tests.swift'
WRAPPER_SHA256 = 'f1441814a16cc66289f70a2f3ac1fc710e5a833b6de7422d0cf0d0307f18335a'
PAYLOAD = 'AyuPrivatePayload/ChatLocalTextFilterProjectionTests.xctest'
EXPECTED = frozenset(projection.EXPECTED)
EVENT = re.compile(r"Test Case '-\[(?:[A-Za-z0-9_]+\.)*([A-Za-z0-9_]+) ([A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=$|\W|Test Case '-\[)")
COUNTS = {'expected_count': 41, 'discovered_count': 41, 'observer_started': 41, 'observer_passed': 41,
    'observer_failed': 0, 'observer_skipped': 0, 'observer_unknown': 0, 'observer_duplicates': 0,
    'observer_out_of_order': 0, 'run_execution_count': 41, 'run_test_case_count': 41, 'run_failure_count': 0,
    'run_total_failure_count': 0, 'run_unexpected_exception_count': 0, 'run_skip_count': 0, 'load_error_code': 0}
BOOLEANS = {'run_has_succeeded': True, 'run_has_been_skipped': False, 'checks_consistent': True,
    'nestedtestmanager_unverified': True, 'release_qualified': False}
STRINGS = {'profile': PROFILE, 'phase': 'completed', 'failure_reason': 'none', 'load_error_category': 'none'}
PHASES = {'wrapperEntered', 'hostActive', 'payloadValidated', 'payloadLoadStart', 'payloadLoaded',
          'suiteValidated', 'suiteRun', 'suiteFinished', 'completed', 'failed'}
FAILURES = {'none', 'hostIdentity', 'hostNotActive', 'payloadLayout', 'payloadMetadata', 'payloadLoad',
            'suiteShape', 'suiteIdentity', 'suiteCount', 'observedResults', 'suiteResults', 'diagnosticWrite'}
REPORT_RELATIVE = 'Library/Caches/ayu-deferred-projection-loader'
JSON_WRAPPER = (
    "import os,resource,sys;"
    "resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576));"
    "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);"
    "os.dup2(fd,1);os.close(fd);os.execv('/usr/bin/xcrun',['xcrun',*sys.argv[2:]])"
)


class DeferredError(ValueError):
    pass


PREFLIGHT_CODES = {
    projection.artifacts.ReplayError: frozenset({
        'application_source_changed', 'submodule_source_changed', 'checkout_provenance_unavailable',
        'projection_expected_source_invalid', 'projection_expected_cases_changed',
        'github_read_failed', 'github_metadata_too_large', 'github_metadata_unavailable',
        'github_artifact_download_failed', 'invalid_artifact_metadata', 'artifact_provenance_mismatch',
        'artifact_digest_mismatch', 'projection_product_hash_mismatch', 'projection_capture_hash_mismatch',
        'projection_product_inventory_mismatch'}),
    modern.PlanError: frozenset({'invalid_modern_plan_output', 'modern_plan_output_already_exists',
        'modern_fixed_capture_mismatch', 'unsafe_private_file', 'private_file_changed', 'private_file_unavailable'}),
    DeferredError: frozenset({'deferred_source_mismatch', 'deferred_output_not_fresh',
        'deferred_prepared_capture_mismatch', 'deferred_prepared_source_mismatch'}),
}


def preflight_failure(error, phase):
    """Only exact known classes/codes may survive; never stringify an error."""
    code = 'deferred_preflight_unavailable'
    allowed = PREFLIGHT_CODES.get(type(error), ())
    if allowed:
        arguments = error.args
        if (type(arguments) is tuple and len(arguments) == 1 and type(arguments[0]) is str
                and len(arguments[0]) <= 96 and arguments[0] in allowed):
            code = arguments[0]
    if type(phase) is not str or phase not in {'prepare', 'runPreflight'}: phase = 'preflight'
    return {'schema': 1, 'profile': PROFILE, 'role': PROFILE, 'phase': phase,
        'failure': code, 'passed': False, 'different_harness': True,
        'requested_source_commit': SOURCE, 'source_verification_passed': False,
        'application_recompiled': False, 'projection_tests_recompiled': False,
        'tiny_control_compiled': False, 'wrapper_compiled': False,
        'control': {'attempted': False, 'expected': 1}, 'projection': {'attempted': False, 'expected': 41},
        'release_qualified': False, 'physical_device_verified': False}


def retain_prepare_failure(output, root, report, initially_fresh):
    """Use only the fixed private output; preserve any preexisting evidence."""
    if not initially_fresh: return False
    directories = []
    try:
        root, output = modern.location(output, root)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        parent = os.open(root.anchor, flags); directories.append(parent)
        for part in root.parts[1:]:
            parent = os.open(part, flags, dir_fd=parent); directories.append(parent)
            if not stat.S_ISDIR(os.fstat(parent).st_mode): return False
        if os.fstat(parent).st_uid != os.getuid(): return False
        # Hold every directory while descending. An ancestor swapped after
        # validation cannot redirect the exclusive report creation elsewhere.
        for part in output.relative_to(root).parts:
            try: child = os.open(part, flags, dir_fd=parent)
            except FileNotFoundError:
                os.mkdir(part, mode=0o700, dir_fd=parent)
                child = os.open(part, flags, dir_fd=parent)
            directories.append(child); parent = child
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid(): return False
        data = json.dumps(report, sort_keys=True).encode('ascii') + b'\n'
        if len(data) > 4096: return False
        descriptor = os.open('result.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'wb') as stream: stream.write(data)
        return True
    except Exception:
        return False
    finally:
        for descriptor in reversed(directories):
            try: os.close(descriptor)
            except OSError: pass


def source_bytes(root):
    value = modern.private_bytes(Path(root) / 'tools/fork_baseline' / WRAPPER_SOURCE)
    if hashlib.sha256(value).hexdigest() != WRAPPER_SHA256: raise DeferredError('deferred_source_mismatch')
    return value


def verification(original, proof):
    return {**modern.verification(original, proof), 'deferred_payload_profile': {
        'schema': 1, 'profile': PROFILE, 'wrapper_source_sha256': WRAPPER_SHA256,
        'expected_payload_cases': 41, 'expected_wrapper_cases': 1, 'requires_native_xcresult_cases': 42}}


def prepare(output=OUTPUT, root=None, api=projection.artifacts.gh_json, fetch=projection.artifacts.download):
    root, destination = modern.location(output, root)
    projection.verify_current_checkout(root)
    wrapper = source_bytes(root)
    modern.prepare(output, root, api, fetch)
    original = destination / 'private-products'
    proof = projection.validate_products(original, SOURCE)
    expected = verification(original, proof)
    # Only this newly created private report is replaced; downloaded inputs are
    # never rewritten and the wrapper source has an independent fixed digest.
    (destination / 'verification.json').unlink()
    projection.write(destination / 'verification.json', expected)
    modern_path = destination / 'private-wrapper-source.swift'
    with modern_path.open('xb') as stream: stream.write(wrapper)
    modern_path.chmod(0o600)
    return expected


def summary_matches(value):
    if not isinstance(value, dict) or set(value) != {'schema'} | set(COUNTS) | set(BOOLEANS) | set(STRINGS): return False
    return (type(value.get('schema')) is int and value['schema'] == 1
        and all(type(value.get(key)) is int and value[key] == wanted for key, wanted in COUNTS.items())
        and all(type(value.get(key)) is bool and value[key] == wanted for key, wanted in BOOLEANS.items())
        and all(type(value.get(key)) is str and value[key] == wanted for key, wanted in STRINGS.items()))


def closed_summary(value):
    """Read one fixed synthetic report, never NSError descriptions or paths."""
    if not isinstance(value, dict) or set(value) != {'schema'} | set(COUNTS) | set(BOOLEANS) | set(STRINGS):
        raise DeferredError('deferred_summary_shape_invalid')
    if type(value['schema']) is not int or value['schema'] != 1 or value.get('profile') != PROFILE:
        raise DeferredError('deferred_summary_identity_invalid')
    result = {'schema': 1, 'profile': PROFILE}
    for key in COUNTS:
        number = value[key]
        if type(number) is not int or not (-2**31 <= number < 2**31 if key == 'load_error_code' else 0 <= number <= 100000):
            raise DeferredError('deferred_summary_counter_invalid')
        result[key] = number
    for key in BOOLEANS:
        if type(value[key]) is not bool: raise DeferredError('deferred_summary_boolean_invalid')
        result[key] = value[key]
    for key, allowed in (('phase', PHASES), ('failure_reason', FAILURES),
                         ('load_error_category', {'none', 'cocoa', 'posix', 'osstatus', 'other'})):
        if type(value[key]) is not str or value[key] not in allowed: raise DeferredError('deferred_summary_state_invalid')
        result[key] = value[key]
    if result['release_qualified'] is not False or result['nestedtestmanager_unverified'] is not True:
        raise DeferredError('deferred_summary_scope_invalid')
    return result


def progress(container):
    if container is None: return {'state': 'unavailable'}
    try:
        directory = Path(container)
        for part in (None, *Path(REPORT_RELATIVE).parts):
            if part is not None: directory = directory / part
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise DeferredError('deferred_progress_directory_unsafe')
        for path in (directory / 'stage', directory / 'summary.json'):
            try: path.lstat()
            except FileNotFoundError: return {'state': 'absent'}
        stage = modern.private_bytes(directory / 'stage', 64).decode('ascii')
        if stage not in PHASES: raise DeferredError('deferred_stage_invalid')
        summary = closed_summary(_bounded_xcresult_json(modern.private_bytes(directory / 'summary.json', 16384))[0])
        if summary['phase'] != stage: return {'state': 'incomplete', 'phase': stage}
        return {'state': 'observed', 'phase': stage, 'summary': summary}
    except FileNotFoundError:
        return {'state': 'absent'}
    except Exception:
        return {'state': 'unavailable'}


def public_progress(value):
    if not isinstance(value, dict): return {'state': 'unavailable'}
    state = value.get('state')
    if type(state) is not str or state not in {'unavailable', 'absent', 'incomplete', 'observed'}:
        return {'state': 'unavailable'}
    if state in {'unavailable', 'absent'}: return {'state': state}
    phase = value.get('phase')
    if type(phase) is not str or phase not in PHASES: return {'state': 'unavailable'}
    if state == 'incomplete': return {'state': state, 'phase': phase}
    try: return {'state': 'observed', 'phase': phase, 'summary': closed_summary(value.get('summary'))}
    except DeferredError: return {'state': 'unavailable'}


def live_observation(pid, group, identifier, expected, secrets, alive, name):
    # The deferred phase is the diagnostic priority. Use the same18-second
    # budget and low-level owned-process proofs as the existing observer.
    deadline = time.monotonic() + 18
    result = {'diagnostic_only': True, 'state': 'unavailable', 'phase': 'ownedFilesystem', 'reason': 'notObserved',
        'callback_result_received': True, 'deferred_progress': {'state': 'unavailable'}, 'containers': {},
        'marker': {'state': 'unavailable', 'captured_while_child_alive': False},
        'xcode_identity_verified': False, 'host_executable_bytes_match': False,
        'host_process': {'state': 'notMeasured'}, 'sample': {'state': 'notAttempted', 'target': 'none'}}
    def running():
        state = modern.bootstrap._alive(alive)
        if state is not True:
            result['reason'] = 'childNotAlive' if state is False else 'childLivenessUnknown'
            return False
        if time.monotonic() >= deadline:
            result['reason'] = 'observerDeadline'; return False
        return True
    try:
        if not running(): return result
        paths = {}
        if modern.owned_device_identity(identifier, name)['identity_verified'] is True:
            paths, result['containers'] = modern.container_observations(identifier, deadline=deadline)
            result['owned_container_verified'] = all(role in paths for role in ('app', 'data'))
            candidate = progress(paths.get('data'))
            marker = modern.control_marker(paths['data']) if 'data' in paths else {'state': 'unavailable'}
            if not running(): return result
            result.update(deferred_progress=candidate, deferred_progress_captured_while_child_alive=True,
                marker={**marker, 'captured_while_child_alive': 'data' in paths})
            if 'app' in paths:
                result['host_executable_bytes_match'] = modern.owned_executable_digest(paths['app'] / modern.CONTROL_APP) == expected
        if not running(): return result
        result['phase'] = 'verifyXcodeProcess'
        status, raw = modern.bootstrap._run(['/bin/ps', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,uid=,ucomm='], secrets, 2, deadline)
        proof = modern.bootstrap._process_snapshot(raw, pid, group, os.getuid()) if modern.bootstrap._completed(status) else {}
        result['xcode_identity_verified'] = proof.get('owned_xcodebuild_verified') is True
        result['xcode_process'] = {'command': status, 'parse_state': proof.get('state', 'notParsed'), 'identity_verified': result['xcode_identity_verified']}
        selected, target = (pid, 'xcodebuild') if result['xcode_identity_verified'] else (None, 'none')
        if not running(): return result
        result['phase'] = 'selectHostProcess'
        if 'app' in paths and result['host_executable_bytes_match']:
            status, raw = modern.bootstrap._run(['/usr/bin/pgrep', '-x', '-u', str(os.getuid()), modern.CONTROL_APP], secrets, 1, deadline)
            result['host_process'] = {'state': 'unavailable'}
            if modern.bootstrap._completed(status) and re.fullmatch(rb'[0-9]{1,10}\n?', raw):
                candidate = int(raw)
                if 0 < candidate < 2**31:
                    path_status, path = modern.bootstrap._run([sys.executable, '-c', modern.KERNEL_PATH, str(candidate)], secrets, 1, deadline)
                    uid_status, uid = modern.bootstrap._run(['/bin/ps', '-p', str(candidate), '-o', 'pid=,uid='], secrets, 1, deadline)
                    if (modern.bootstrap._completed(path_status) and modern.bootstrap._completed(uid_status)
                            and path == str(paths['app'] / modern.CONTROL_APP).encode()
                            and uid.split() == [str(candidate).encode(), str(os.getuid()).encode()]):
                        selected, target = candidate, 'tinyControl'; result['host_process'] = {'state': 'verifiedOwnedPathAndUID'}
        if not running(): return result
        if selected is None:
            result.update(state='partial', reason='samplingTargetUnverified'); return result
        result['phase'] = 'sampleVerifiedProcess'
        with tempfile.TemporaryDirectory(prefix='ayu-deferred-sample-') as temporary:
            private = Path(temporary).resolve(); private.chmod(0o700); path = private / 'sample.txt'
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600); os.close(descriptor)
            status, _ = modern.bootstrap._run([sys.executable, '-c', modern.bootstrap.SAMPLE_EXEC_WRAPPER, str(selected), str(path)], secrets, 10, deadline)
            selection = {'state': 'unavailable', 'wait_categories': {key: 0 for key in modern.bootstrap.WAIT_CATEGORIES},
                         'main_thread_observed': False, 'complete_stacks_verified': False}
            if modern.bootstrap._completed(status):
                data = modern.bootstrap._sample_file(path, os.getuid())
                if data is not None: selection = modern.bootstrap.parse_sample(data, secrets)
            if not running(): return result
            result['sample'] = {'target': target, 'state': 'observed' if selection['state'] == 'parsed' else 'unavailable',
                'command': status, 'selection': selection}
        result.update(state='observed' if selection['state'] == 'parsed' else 'partial', phase='complete',
                      reason='observed' if selection['state'] == 'parsed' else 'sampleUnavailable')
    except Exception:
        result['reason'] = 'observationUnavailable'
    return result


def public_live(value):
    result = modern.public_live_observation(value)
    value = value if isinstance(value, dict) else {}
    if value.get('reason') == 'observerDeadline': result['reason'] = 'observerDeadline'
    result['deferred_progress'] = public_progress(value.get('deferred_progress'))
    result['deferred_progress_captured_while_child_alive'] = value.get('deferred_progress_captured_while_child_alive') is True
    return result


def verify_control_project(path):
    with tempfile.TemporaryDirectory(prefix='ayu-deferred-project-source-') as temporary:
        expected = Path(temporary).resolve() / 'project'
        tiny.create_project(expected)
        if projection.snapshot_tree(path) != projection.snapshot_tree(expected):
            raise DeferredError('deferred_control_project_changed')


def cases(data):
    """A distinct strict42 profile; the legacy41 parser is unchanged."""
    if not isinstance(data, bytes) or len(data) > projection.MAX_OUTPUT: raise DeferredError('deferred_log_limit')
    text = data.decode('utf-8', errors='replace')
    allowed = EXPECTED | {WRAPPER}
    starts, outcomes, offsets = set(), {}, set()
    unknown = duplicates = invalid_order = nesting = 0
    for event in EVENT.finditer(text):
        offsets.add(event.start()); key = event[1] + '.' + event[2]; state = event[3]
        if key not in allowed: unknown += 1; continue
        if state == 'started':
            if key in starts: duplicates += 1
            starts.add(key)
            if key != WRAPPER and (WRAPPER not in starts or WRAPPER in outcomes): nesting += 1
        else:
            if key in outcomes: duplicates += 1
            if key not in starts: invalid_order += 1
            outcomes.setdefault(key, state)
            if key == WRAPPER and not EXPECTED <= outcomes.keys(): nesting += 1
    unknown += sum(match.start() not in offsets for match in re.finditer("Test Case '", text))
    return {'expected': 41, 'expected_wrapper_cases': 1, 'expected_event_cases': 42,
        'started': len(EXPECTED & starts), 'passed': sum(outcomes.get(key) == 'passed' for key in EXPECTED),
        'failed': sum(outcomes.get(key) == 'failed' for key in EXPECTED),
        'skipped': sum(outcomes.get(key) == 'skipped' for key in EXPECTED),
        'crashed': sum(outcomes.get(key) == 'crashed' for key in EXPECTED),
        'wrapper_started': WRAPPER in starts, 'wrapper_passed': outcomes.get(WRAPPER) == 'passed',
        'missing': sorted(EXPECTED - outcomes.keys()), 'unknown_event_count': unknown,
        'duplicate_event_count': duplicates, 'invalid_case_event_order': invalid_order,
        'invalid_wrapper_nesting_count': nesting,
        'all_expected_passed': not (unknown or duplicates or invalid_order or nesting)
            and len(starts) == len(outcomes) == 42 and all(outcomes.get(key) == 'passed' for key in allowed)}


def create_wrapper_project(root, wrapper):
    """Generate a test-only Xcode target; there is no second app build target."""
    root = Path(root)
    if root.exists() or not root.parent.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise DeferredError('deferred_project_location_invalid')
    if hashlib.sha256(wrapper).hexdigest() != WRAPPER_SHA256: raise DeferredError('deferred_source_mismatch')
    root.mkdir(mode=0o700)
    for name in ('Tests', 'DeferredProjection.xcodeproj', 'DeferredProjection.xcodeproj/xcshareddata',
                 'DeferredProjection.xcodeproj/xcshareddata/xcschemes'):
        (root / name).mkdir(mode=0o700)
    project = '''// !$*UTF8*$!
{ archiveVersion = 1; classes = {}; objectVersion = 56; objects = {
 A01 = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = wrapper.swift; sourceTree = "<group>"; };
 A02 = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>"; };
 A03 = {isa = PBXFileReference; explicitFileType = wrapper.cfbundle; path = AyuProjectionControlTests.xctest; sourceTree = BUILT_PRODUCTS_DIR; };
 B01 = {isa = PBXBuildFile; fileRef = A01; };
 C01 = {isa = PBXGroup; children = (C02, C03, ); sourceTree = "<group>"; };
 C02 = {isa = PBXGroup; children = (A01, A02, ); path = Tests; sourceTree = "<group>"; };
 C03 = {isa = PBXGroup; children = (A03, ); name = Products; sourceTree = "<group>"; };
 D01 = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (B01, ); runOnlyForDeploymentPostprocessing = 0; };
 D02 = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
 E01 = {isa = PBXNativeTarget; buildConfigurationList = F02; buildPhases = (D01, D02, ); buildRules = (); dependencies = ();
   name = AyuProjectionControlTests; productName = AyuProjectionControlTests; productReference = A03; productType = "com.apple.product-type.bundle.unit-test"; };
 E02 = {isa = PBXProject; attributes = {BuildIndependentTargetsInParallel = 0; LastUpgradeCheck = 2710; }; buildConfigurationList = F01;
   compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base, );
   mainGroup = C01; productRefGroup = C03; projectDirPath = ""; projectRoot = ""; targets = (E01, ); };
 F01 = {isa = XCConfigurationList; buildConfigurations = (G01, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
 F02 = {isa = XCConfigurationList; buildConfigurations = (G02, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
 G01 = {isa = XCBuildConfiguration; name = Debug; buildSettings = {ARCHS = arm64; CLANG_ENABLE_MODULES = YES; CODE_SIGNING_ALLOWED = YES;
   CODE_SIGN_IDENTITY = "-"; CODE_SIGN_STYLE = Manual; CODE_SIGN_INJECT_BASE_ENTITLEMENTS = YES; DEBUG_INFORMATION_FORMAT = dwarf;
   ENABLE_TESTABILITY = YES; IPHONEOS_DEPLOYMENT_TARGET = 15.0; ONLY_ACTIVE_ARCH = YES; SDKROOT = iphonesimulator;
   SUPPORTED_PLATFORMS = iphonesimulator; TARGETED_DEVICE_FAMILY = 1; }; };
 G02 = {isa = XCBuildConfiguration; name = Debug; buildSettings = {GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = Tests/Info.plist;
   PRODUCT_BUNDLE_IDENTIFIER = "org.livara.ayugram.tests.modern-projection-controlTests"; PRODUCT_NAME = "$(TARGET_NAME)";
   SWIFT_VERSION = 5.0; BUNDLE_LOADER = "$(TEST_HOST)";
   FRAMEWORK_SEARCH_PATHS = ("$(inherited)", "$(PLATFORM_DIR)/Developer/Library/Frameworks", );
   LD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks", "@loader_path/Frameworks", ); }; };
 }; rootObject = E02; }
'''
    scheme = '''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2710" version="1.3">
 <BuildAction parallelizeBuildables="NO" buildImplicitDependencies="NO"><BuildActionEntries>
  <BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="NO">
   <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="E01" BuildableName="AyuProjectionControlTests.xctest" BlueprintName="AyuProjectionControlTests" ReferencedContainer="container:DeferredProjection.xcodeproj"/>
  </BuildActionEntry></BuildActionEntries></BuildAction>
 <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES" codeCoverageEnabled="NO">
  <Testables><TestableReference skipped="NO" parallelizable="NO"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="E01" BuildableName="AyuProjectionControlTests.xctest" BlueprintName="AyuProjectionControlTests" ReferencedContainer="container:DeferredProjection.xcodeproj"/></TestableReference></Testables>
 </TestAction>
</Scheme>
'''
    # PBX object IDs are Xcode's conventional 24 hexadecimal characters.
    identifiers = {name: f'{index:024X}' for index, name in enumerate(
        ('A01', 'A02', 'A03', 'B01', 'C01', 'C02', 'C03', 'D01', 'D02', 'E01', 'E02', 'F01', 'F02', 'G01', 'G02'), 1)}
    pattern = r'\b(?:' + '|'.join(identifiers) + r')\b'
    project = re.sub(pattern, lambda match: identifiers[match[0]], project)
    scheme = re.sub(pattern, lambda match: identifiers[match[0]], scheme)
    values = {'Tests/wrapper.swift': wrapper, 'Tests/Info.plist': plistlib.dumps(tiny._info('BNDL')),
        'DeferredProjection.xcodeproj/project.pbxproj': project.encode(),
        'DeferredProjection.xcodeproj/xcshareddata/xcschemes/DeferredProjection.xcscheme': scheme.encode()}
    for name, value in values.items():
        with (root / name).open('xb') as stream: stream.write(value)
        (root / name).chmod(0o600)


def byte_tree(root):
    return {name: (value[0], value[2]) for name, value in projection.snapshot_tree(root).items()}


def sign_host(destination, policy, call):
    if (not isinstance(policy, dict) or set(policy) != {'options', 'generate_der', 'timestamp_none', 'identifier_explicit', 'entitlements'}
            or policy['options'] not in {None, 'runtime', '0x10000', '0x0'}
            or any(type(policy[key]) is not bool for key in ('generate_der', 'timestamp_none', 'identifier_explicit'))
            or policy['entitlements'] is not None and not isinstance(policy['entitlements'], dict)):
        raise DeferredError('deferred_signing_policy_unverified')
    args = ['/usr/bin/codesign', '--force', '--sign', '-']
    if policy['timestamp_none']: args.append('--timestamp=none')
    if policy['identifier_explicit']: args += ['--identifier', modern.products.BUNDLE]
    if policy['options'] is not None: args += ['--options', policy['options']]
    if policy['generate_der']: args.append('--generate-entitlement-der')
    with tempfile.TemporaryDirectory(prefix='ayu-deferred-signature-') as temporary:
        private = Path(temporary).resolve(); private.chmod(0o700)
        if policy['entitlements'] is not None:
            modern.entitlement_document(plistlib.dumps(policy['entitlements']))
            path = private / 'entitlements.plist'; path.write_bytes(plistlib.dumps(policy['entitlements'])); path.chmod(0o600)
            args += ['--entitlements', str(path)]
        call('sign_deferred_host', [*args, str(destination)])
        _, raw = call('verify_deferred_host_identity', ['/usr/bin/codesign', '--display', '--verbose=4', str(destination)])
        lines = raw.decode('utf-8', errors='replace').splitlines()
        if (lines.count('Signature=adhoc') != 1 or lines.count('Identifier=' + modern.products.BUNDLE) != 1
                or lines.count('TeamIdentifier=not set') != 1): raise DeferredError('deferred_adhoc_identity_unverified')
        extracted = private / 'actual-entitlements.plist'
        call('verify_deferred_host_entitlements', ['/usr/bin/codesign', '--display', '--entitlements', str(extracted), '--xml', str(destination)])
        actual = None
        if extracted.exists() or extracted.is_symlink():
            data = modern.private_bytes(extracted, allow_empty=policy['entitlements'] in (None, {}))
            actual = modern.entitlement_document(data) if data else None
        if policy['entitlements'] in (None, {}):
            if actual not in (None, {}): raise DeferredError('deferred_host_rights_changed')
        elif actual is None or not modern.typed_equal(actual, policy['entitlements']): raise DeferredError('deferred_host_rights_changed')
    call('verify_deferred_host_seal', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(destination)])


def stage_host(control, wrapper, captured, destination, policy, call):
    sources = [(path, projection.snapshot_tree(path)) for path in (control, wrapper, captured)]
    modern._copy_bundle(control, destination)
    plugins = destination / 'PlugIns'; plugins.mkdir(mode=0o700, exist_ok=True)
    old = plugins / (modern.CONTROL_TEST + '.xctest')
    if old.exists():
        if old.is_symlink(): raise DeferredError('deferred_unsafe_old_plugin')
        shutil.rmtree(old)
    if list(plugins.iterdir()): raise DeferredError('deferred_unexpected_plugin')
    modern._copy_bundle(wrapper, old)
    payload = destination / PAYLOAD; payload.parent.mkdir(mode=0o700)
    original_payload = captured / 'PlugIns' / modern.products.TEST
    modern._copy_bundle(original_payload, payload)
    resources = modern.presentation_resources(captured)
    for name, wanted in resources.items():
        path = destination / name
        if path.exists() or path.is_symlink(): raise DeferredError('deferred_resource_collision')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(captured / name, path)
        if projection.digest(path) != wanted or path.stat().st_nlink != 1: raise DeferredError('deferred_resource_copy_changed')
    before = projection.snapshot_tree(destination); original_code = projection.main_code_digest(control)
    if projection.main_code_digest(destination) != original_code: raise DeferredError('deferred_host_code_changed')
    call('verify_payload_seal', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(payload)])
    call('verify_wrapper_seal', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(old)])
    sign_host(destination, policy, call)
    after = projection.snapshot_tree(destination)
    if (set(after) != set(before) or any(after[key] != before[key] for key in before
            if key not in {modern.CONTROL_APP, '_CodeSignature/CodeResources'})
            or projection.main_code_digest(destination) != original_code): raise DeferredError('deferred_sealing_changed_code_or_resources')
    if projection.snapshot_tree(payload) != projection.snapshot_tree(original_payload): raise DeferredError('deferred_payload_changed')
    if projection.snapshot_tree(old) != projection.snapshot_tree(wrapper): raise DeferredError('deferred_wrapper_changed')
    if any(projection.snapshot_tree(path) != snapshot for path, snapshot in sources): raise DeferredError('deferred_original_products_changed')
    return {'original_control_unchanged': True, 'wrapper_tree_unchanged': True, 'payload_tree_unchanged': True,
        'host_code_normalized_unchanged': True, 'main_resource_files': len(resources), 'main_localizations': 19,
        'strict_host_wrapper_payload_seals_verified': True, 'payload_outside_plugins': True}


def bind_plan(generated, root, host, destination):
    _, runner, control, control_test, kind = modern.generated_products(generated, root)
    destination.mkdir(mode=0o700)
    installed = destination / control.relative_to(root); installed.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    modern._copy_bundle(host, installed)
    result = copy.deepcopy(generated); _, bound = modern.target(result)
    bound['TestHostPath'] = str(installed / modern.CONTROL_APP) if kind == 'executable' else str(installed)
    bound['TestBundlePath'] = '__TESTHOST__/PlugIns/' + modern.CONTROL_TEST + '.xctest'
    paths = []
    for value in runner.get('DependentProductPaths', []):
        original = modern.resolved_path(value, root, control)
        if original == control_test: new = installed / 'PlugIns' / control_test.name
        elif original == control_test / modern.CONTROL_TEST: new = installed / 'PlugIns' / control_test.name / modern.CONTROL_TEST
        else:
            try: new = installed / original.relative_to(control)
            except ValueError: raise DeferredError('deferred_unknown_dependent_product') from None
        if not new.exists(): raise DeferredError('deferred_dependent_product_missing')
        paths.append(str(new))
    if 'DependentProductPaths' in bound: bound['DependentProductPaths'] = paths
    if (bound.get('ProductModuleName') != modern.CONTROL_TEST
            or ('TestHostBundleIdentifier' in bound and bound['TestHostBundleIdentifier'] != modern.CONTROL_APP_ID)
            or modern.resolved_path(bound['TestBundlePath'], destination, installed) != installed / 'PlugIns' / (modern.CONTROL_TEST + '.xctest')):
        raise DeferredError('deferred_plan_identity_changed')
    path = destination / 'deferred.xctestrun'; path.write_bytes(plistlib.dumps(result)); path.chmod(0o600)
    return path, installed


# Strict legacy xcresult parsing is defined below. It never exposes references,
# raw JSON, diagnostics or arbitrary test names in public reports.

MAX_XCRESULT_JSON_BYTES = 1024 * 1024
MAX_XCRESULT_DEPTH = 32
MAX_XCRESULT_NODES = 4096
PAYLOAD_CASES = EXPECTED
EXPECTED_XCRESULT_CASES = PAYLOAD_CASES | {WRAPPER}
PAYLOAD_MODULES = frozenset({'ChatLocalTextFilterProjectionTests', 'ChatLocalTextFilterProjectionTestsLib',
    'submodules_TelegramUI_Components_Chat_ChatHistoryEntry_ChatLocalTextFilterProjectionTestsLib'})
WRAPPER_MODULES = frozenset({'AyuProjectionControlTests'})


class InvalidXCResult(ValueError):
    """A fixed public error; never carry native source or identifier text."""


def _invalid():
    raise InvalidXCResult('invalid_xcresult_structure')


def _object_pairs(pairs):
    value = {}
    for key, child in pairs:
        if key in value: _invalid()
        value[key] = child
    return value


def _constant(_value):
    _invalid()


def _bounded_xcresult_json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_XCRESULT_JSON_BYTES:
        _invalid()
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object_pairs, parse_constant=_constant)
    stack, dictionaries, nodes = [(value, 0)], [], 0
    while stack:
        child, depth = stack.pop(); nodes += 1
        if depth > MAX_XCRESULT_DEPTH or nodes > MAX_XCRESULT_NODES: _invalid()
        if isinstance(child, dict):
            dictionaries.append(child)
            stack.extend((item, depth + 1) for item in child.values())
        elif isinstance(child, list):
            stack.extend((item, depth + 1) for item in child)
        elif isinstance(child, float) and not math.isfinite(child):
            _invalid()
    return value, dictionaries


def _typed(value, name):
    if (not isinstance(value, dict) or not isinstance(value.get('_type'), dict) or
            value['_type'].get('_name') != name):
        _invalid()
    return value


def _string(value, maximum=512):
    value = _typed(value, 'String')
    if set(value) != {'_type', '_value'} or not isinstance(value['_value'], str) or not 0 < len(value['_value']) <= maximum:
        _invalid()
    return value['_value']


def _array(value):
    value = _typed(value, 'Array')
    if set(value) != {'_type', '_values'} or not isinstance(value['_values'], list): _invalid()
    return value['_values']


def strict_xcresult_root(raw):
    """Return one PRIVATE testsRef token from all legacy ActionRecords."""
    try:
        root, dictionaries = _bounded_xcresult_json(raw)
        _typed(root, 'ActionsInvocationRecord')
        references = []
        for action in _array(root.get('actions')):
            _typed(action, 'ActionRecord')
            result = _typed(action.get('actionResult'), 'ActionResult')
            if 'testsRef' in result:
                reference = _typed(result['testsRef'], 'Reference')
                token = _string(reference.get('id'), 256)
                if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_~+/=-]{0,255}', token) is None: _invalid()
                references.append(token)
        # A shadow/foreign testsRef must not be ignored alongside the real one.
        if len(references) != 1 or sum('testsRef' in value for value in dictionaries) != 1: _invalid()
        return references[0]
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise InvalidXCResult('xcresult_root_invalid') from None


def _canonical_xcresult_identity(identifier):
    if not isinstance(identifier, str) or not 0 < len(identifier) <= 512: return None
    match = re.fullmatch(r'(?:(?P<module>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<class>[A-Za-z_][A-Za-z0-9_]*)/'
                         r'(?P<method>test[A-Za-z0-9_]*)(?:\(\))?', identifier)
    if match is None: return None
    key = match['class'] + '.' + match['method']
    allowed = WRAPPER_MODULES if key == WRAPPER else PAYLOAD_MODULES
    if match['module'] is not None and match['module'] not in allowed: return None
    return key if key in EXPECTED_XCRESULT_CASES else None


def strict_xcresult_cases(raw):
    """Select all 41 payload and one wrapper terminal outcomes, never infer starts."""
    try:
        root, dictionaries = _bounded_xcresult_json(raw)
        _typed(root, 'ActionTestPlanRunSummaries')
        consumed, visited, records, group_failure = set(), set(), [], False
        def branch(owner, key):
            consumed.add((id(owner), key))
            return _array(owner.get(key))
        def walk(test):
            nonlocal group_failure
            if not isinstance(test, dict) or not isinstance(test.get('_type'), dict): _invalid()
            kind = test['_type'].get('_name'); visited.add(id(test))
            if kind == 'ActionTestSummaryGroup':
                if 'testStatus' in test and _string(test['testStatus']) != 'Success': group_failure = True
                for child in branch(test, 'subtests'): walk(child)
            elif kind in {'ActionTestMetadata', 'ActionTestSummary'}:
                if 'subtests' in test: _invalid()
                identifier, status = _string(test.get('identifier')), _string(test.get('testStatus'))
                records.append((_canonical_xcresult_identity(identifier), status))
            else:
                _invalid()
        for summary in branch(root, 'summaries'):
            _typed(summary, 'ActionTestPlanRunSummary')
            for testable in branch(summary, 'testableSummaries'):
                _typed(testable, 'ActionTestableSummary')
                for test in branch(testable, 'tests'): walk(test)
        for value in dictionaries:
            for key in {'summaries', 'testableSummaries', 'tests', 'subtests'} & value.keys():
                if (id(value), key) not in consumed: _invalid()
            kind = value.get('_type', {}).get('_name') if isinstance(value.get('_type'), dict) else None
            if (kind in {'ActionTestSummaryGroup', 'ActionTestMetadata', 'ActionTestSummary'} or 'testStatus' in value) and id(value) not in visited:
                _invalid()
        outcomes = {}
        for key, status in records:
            if key is not None: outcomes.setdefault(key, []).append(status)
        cases = {key: ('duplicate' if len(values) != 1 else
                      'passed' if values[0] == 'Success' else 'skipped' if values[0] == 'Skipped' else 'failed')
                 for key, values in sorted(outcomes.items())}
        missing = sorted(EXPECTED_XCRESULT_CASES - outcomes.keys())
        unknown = sum(key is None for key, _status in records)
        duplicate = any(len(values) != 1 for values in outcomes.values())
        passed = sum(status == 'passed' for status in cases.values())
        return {'schema': 1, 'expected': 42, 'case_records': len(records), 'known_case_records': sum(len(v) for v in outcomes.values()),
                'passed': passed, 'failed': sum(status == 'failed' for status in cases.values()),
                'skipped': sum(status == 'skipped' for status in cases.values()), 'missing': missing,
                'unknown_case_records': unknown, 'unexpected_case_events': unknown != 0,
                'duplicate_case_events': duplicate, 'nonpassing_group': group_failure,
                'wrapper_passed': cases.get(WRAPPER) == 'passed', 'payload_passed': sum(cases.get(k) == 'passed' for k in PAYLOAD_CASES),
                'cases': cases, 'all_expected_passed': len(records) == 42 and not (missing or unknown or duplicate or group_failure) and passed == 42}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise InvalidXCResult('xcresult_cases_invalid') from None


def wrapper_product(path):
    info = plistlib.loads(modern.private_bytes(path / 'Info.plist'))
    if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != modern.CONTROL_TEST_ID
            or info.get('CFBundleExecutable') != modern.CONTROL_TEST
            or info.get('CFBundleSupportedPlatforms') != ['iPhoneSimulator']):
        raise DeferredError('deferred_wrapper_identity_invalid')
    binary = path / modern.CONTROL_TEST
    with binary.open('rb') as stream:
        header = stream.read(32)
        if (len(header) != 32 or struct.unpack('<8I', header)[:4] != (0xfeedfacf, 0x100000c, 0, 8)
                or not modern.products.simulator_macho(lambda offset, count: (stream.seek(offset), stream.read(count))[1], binary.stat().st_size)):
            raise DeferredError('deferred_wrapper_platform_invalid')
    return path


def create_build_host(control, control_test, base_products, derived):
    """Move only an independent host copy into Xcode's required product root."""
    name = modern.CONTROL_TEST + '.xctest'
    if control.parent != base_products / 'Debug-iphonesimulator':
        raise DeferredError('deferred_baseline_host_layout_invalid')
    if control_test not in {control / 'PlugIns' / name, control.parent / name}:
        raise DeferredError('deferred_baseline_test_layout_invalid')
    products = derived / 'Build/Products'
    clone = products / control.relative_to(base_products)
    clone.parent.mkdir(mode=0o700, parents=True)
    modern._copy_bundle(control, clone)
    original = projection.snapshot_tree(control)
    plugin = clone / 'PlugIns' / name
    removed = False
    if plugin.exists() or plugin.is_symlink():
        if plugin.is_symlink() or not plugin.is_dir():
            raise DeferredError('deferred_old_control_plugin_mismatch')
        info = plistlib.loads(modern.private_bytes(plugin / 'Info.plist'))
        if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != modern.CONTROL_TEST_ID
                or info.get('CFBundleExecutable') != modern.CONTROL_TEST
                or projection.snapshot_tree(plugin) != projection.snapshot_tree(control_test)):
            raise DeferredError('deferred_old_control_plugin_mismatch')
        shutil.rmtree(plugin); removed = True
    plugins = clone / 'PlugIns'
    if plugins.exists() or plugins.is_symlink():
        if plugins.is_symlink() or not plugins.is_dir() or list(plugins.iterdir()):
            raise DeferredError('deferred_build_extra_plugin')
    else: plugins.mkdir(mode=0o700)
    return {'host': clone, 'source': control, 'source_snapshot': original,
        'before': projection.snapshot_tree(clone), 'code': projection.main_code_digest(control),
        'products': products, 'old_plugin_removed': removed,
        'baseline_test_layout': 'hostedPlugIns' if control_test.parent == control / 'PlugIns' else 'sibling'}


def wrapper_location(raw, context):
    """Use actual Xcode settings, while accepting only the two fixed layouts."""
    try: value, _ = _bounded_xcresult_json(raw)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise DeferredError('deferred_wrapper_settings_json_invalid') from None
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise DeferredError('deferred_wrapper_settings_shape_invalid')
    value = value[0]
    if value.get('target') != modern.CONTROL_TEST or not isinstance(value.get('buildSettings'), dict):
        raise DeferredError('deferred_wrapper_settings_target_invalid')
    settings = value['buildSettings']; host = context['host']; executable = str(host / modern.CONTROL_APP)
    if (settings.get('TEST_HOST') != executable or settings.get('BUNDLE_LOADER') != executable
            or settings.get('FULL_PRODUCT_NAME') != modern.CONTROL_TEST + '.xctest'):
        raise DeferredError('deferred_wrapper_settings_identity_invalid')
    directory = settings.get('TARGET_BUILD_DIR')
    if (type(directory) is not str or not directory or '\0' in directory or '\\' in directory
            or '..' in Path(directory).parts or not Path(directory).is_absolute()
            or str(Path(directory)) != directory):
        raise DeferredError('deferred_wrapper_settings_path_invalid')
    product = Path(directory) / settings['FULL_PRODUCT_NAME']
    possible = {host / 'PlugIns' / (modern.CONTROL_TEST + '.xctest'), host.parent / (modern.CONTROL_TEST + '.xctest')}
    if product not in possible or any(path.is_symlink() for path in (product, *product.parents)):
        raise DeferredError('deferred_wrapper_settings_path_invalid')
    if 'CODESIGNING_FOLDER_PATH' in settings and settings['CODESIGNING_FOLDER_PATH'] != str(product):
        raise DeferredError('deferred_wrapper_settings_signing_path_invalid')
    if any(path.exists() or path.is_symlink() for path in possible):
        raise DeferredError('deferred_wrapper_product_not_fresh')
    context['product'] = product
    return product


def verify_build_host(context, product=None, require_product=False):
    host, before = context['host'], context['before']
    if product is None: product = context.get('product')
    after = projection.snapshot_tree(host)
    name = modern.CONTROL_TEST + '.xctest'; prefix = 'PlugIns/' + name
    def protected(tree):
        return {key: value for key, value in tree.items() if key not in
            {modern.CONTROL_APP, '_CodeSignature', '_CodeSignature/CodeResources'}
            and key != prefix and not key.startswith(prefix + '/')}
    if (protected(after) != protected(before) or after.get(modern.CONTROL_APP, ())[:2] != before[modern.CONTROL_APP][:2]
            or any(after.get(key, ())[:2] != before[key][:2]
                for key in ('_CodeSignature', '_CodeSignature/CodeResources') if key in before)
            or projection.main_code_digest(host) != context['code']
            or projection.snapshot_tree(context['source']) != context['source_snapshot']):
        raise DeferredError('deferred_build_host_changed')
    plugins = host / 'PlugIns'
    if plugins.is_symlink() or not plugins.is_dir() or any(path.name != name for path in plugins.iterdir()):
        raise DeferredError('deferred_build_extra_plugin')
    present = [path for path in (host / 'PlugIns' / name, host.parent / name) if path.exists() or path.is_symlink()]
    if any(path.is_symlink() or not path.is_dir() for path in present): raise DeferredError('deferred_build_wrapper_unsafe')
    if require_product and (product is None or product not in present): raise DeferredError('deferred_built_wrapper_missing')
    if product is not None and product in present:
        if any(byte_tree(path) != byte_tree(product) for path in present): raise DeferredError('deferred_duplicate_wrapper_mismatch')
    return {'state': 'verified', 'independent_host_copy_verified': True,
        'old_control_plugin_removed': context['old_plugin_removed'], 'baseline_test_layout': context['baseline_test_layout'],
        'nonplugin_files_and_modes_unchanged': True, 'normalized_host_code_unchanged': True,
        'root_seal_bytes_may_change': True, 'original_control_unchanged': True, 'wrapper_product_copies': len(present)}


def verify_wrapper_signing(derived, wrapper, raw, call):
    """Validate this role against its own actual Xcode intermediates root."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= projection.MAX_OUTPUT:
        raise DeferredError('deferred_wrapper_signing_log_invalid')
    matches = []
    try:
        for line in raw.decode('utf-8', errors='strict').splitlines():
            if '/usr/bin/codesign' not in line: continue
            if len(line) > 16384: raise DeferredError('deferred_wrapper_signing_argv_invalid')
            arguments = shlex.split(line)
            if '/usr/bin/codesign' not in arguments: continue
            if not 3 <= len(arguments) <= 64 or arguments[0] != '/usr/bin/codesign':
                raise DeferredError('deferred_wrapper_signing_argv_invalid')
            if str(wrapper) in arguments:
                if arguments[-1] != str(wrapper): raise DeferredError('deferred_wrapper_signing_argv_invalid')
                matches.append(arguments)
    except (UnicodeError, ValueError):
        raise DeferredError('deferred_wrapper_signing_argv_invalid') from None
    if len(matches) != 1: raise DeferredError('deferred_wrapper_signing_not_unique')
    arguments = matches[0]; position, seen, values = 1, set(), {}
    while position < len(arguments) - 1:
        option = arguments[position]; position += 1
        if option in {'--force', '-f', '--generate-entitlement-der', '--timestamp=none'}:
            key = '--force' if option == '-f' else option
            if key in seen: raise DeferredError('deferred_wrapper_signing_option_invalid')
            seen.add(key); continue
        key, separator, value = option.partition('=')
        key = {'-s': '--sign', '-i': '--identifier'}.get(key, key)
        if key not in {'--sign', '--entitlements', '--identifier', '--options'} or key in seen:
            raise DeferredError('deferred_wrapper_signing_option_invalid')
        seen.add(key)
        if not separator:
            if position >= len(arguments) - 1: raise DeferredError('deferred_wrapper_signing_option_invalid')
            value = arguments[position]; position += 1
        if not value: raise DeferredError('deferred_wrapper_signing_option_invalid')
        values[key] = value
    if (values.get('--sign') != '-' or ('--identifier' in values and values['--identifier'] != modern.CONTROL_TEST_ID)
            or values.get('--options') not in {None, 'runtime', '0x10000', '0x0'}):
        raise DeferredError('deferred_wrapper_signing_identity_invalid')
    expected = None
    if '--entitlements' in values:
        path = Path(values['--entitlements']); root = derived / 'Build/Intermediates.noindex'
        if not path.is_absolute() or '..' in path.parts or '\\' in str(path) or '\0' in str(path):
            raise DeferredError('deferred_wrapper_entitlement_path_invalid')
        try: path.relative_to(root)
        except ValueError: raise DeferredError('deferred_wrapper_entitlement_path_invalid') from None
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink(): raise DeferredError('deferred_wrapper_entitlement_path_invalid')
            if ancestor == derived: break
        else: raise DeferredError('deferred_wrapper_entitlement_path_invalid')
        expected = modern.entitlement_document(modern.private_bytes(path))
    _, raw = call('wrapper_verify_identity', ['/usr/bin/codesign', '--display', '--verbose=4', str(wrapper)])
    lines = raw.decode('utf-8', errors='replace').splitlines()
    if (lines.count('Signature=adhoc') != 1 or lines.count('Identifier=' + modern.CONTROL_TEST_ID) != 1
            or lines.count('TeamIdentifier=not set') != 1): raise DeferredError('deferred_wrapper_adhoc_unverified')
    with tempfile.TemporaryDirectory(prefix='ayu-deferred-wrapper-rights-') as temporary:
        private = Path(temporary).resolve(); private.chmod(0o700); extracted = private / 'entitlements.plist'
        call('wrapper_verify_entitlements', ['/usr/bin/codesign', '--display', '--entitlements', str(extracted), '--xml', str(wrapper)])
        actual = None
        if extracted.exists() or extracted.is_symlink():
            data = modern.private_bytes(extracted, allow_empty=expected in (None, {}))
            actual = modern.entitlement_document(data) if data else None
        if expected in (None, {}):
            if actual not in (None, {}): raise DeferredError('deferred_wrapper_rights_changed')
        elif actual is None or not modern.typed_equal(expected, actual): raise DeferredError('deferred_wrapper_rights_changed')
    call('wrapper_verify_seal', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(wrapper)])
    return {'mode': 'actualXcodeWrapperCodeSign', 'matching_invocations': 1, 'adhoc_identity_verified': True,
        'actual_input_kind': 'explicitFile' if '--entitlements' in values else 'none',
        'generated_entitlements_present': expected is not None, 'typed_signature_readback_matched': True,
        'strict_seal_verified': True}


def run(output=OUTPUT, root=None, command=projection.native_command):
    root, output = modern.location(output, root)
    projection.verify_current_checkout(root)
    wrapper_source = source_bytes(root)
    if any((output / name).exists() or (output / name).is_symlink() for name in
           ('result.json', 'control-build.log', 'private-derived', 'private-wrapper-derived', 'private-wrapper-project', 'private-deferred', 'private-deferred-host')):
        raise DeferredError('deferred_output_not_fresh')
    original = output / 'private-products'
    verified = projection.validate_products(original, SOURCE)
    if _bounded_xcresult_json(modern.private_bytes(output / 'verification.json'))[0] != verification(original, verified):
        raise DeferredError('deferred_prepared_capture_mismatch')
    if modern.private_bytes(output / 'private-wrapper-source.swift') != wrapper_source:
        raise DeferredError('deferred_prepared_source_mismatch')
    original_snapshot = projection.snapshot_tree(original)
    report = {'schema': 1, 'source_commit': SOURCE, 'role': PROFILE, 'profile': PROFILE,
        'different_harness': True, 'original_bazel_host_executed': False,
        'application_recompiled': False, 'projection_tests_recompiled': False,
        'tiny_control_compiled': False, 'wrapper_compiled': False, 'tiny_control_signing_verified': False,
        'passed': False, 'release_qualified': False, 'physical_device_verified': False,
        'nested_testmanager_equivalence_verified': False, 'actual_native_xcresult_verified': False,
        'native_xcresult_case_accounting_verified': False,
        'native_control_timeout_seconds': 600, 'native_projection_timeout_seconds': 600,
        'native_destination_timeout_seconds': 600, 'native_projection_observation_after_seconds': 480,
        'wrapper_source_sha256': WRAPPER_SHA256, 'original_products_unchanged': False,
        'control': {'attempted': False, 'expected': 1}, 'projection': {'attempted': False, 'expected': 41},
        'commands': {}, 'command_timing': {}, 'post_exit_group_observations': {}, 'phase': 'preflight'}
    secrets = tuple(value for key, value in os.environ.items() if value and
        re.search(r'TOKEN|SECRET|AUTH|PASSWORD|API_ID|API_HASH|SESSION|PRIVATE_KEY', key, re.I))
    compiler = []
    immutable = []
    build_host = None
    used = set()
    def call(label, args, timeout=30, required=True, groups=False, observer=None, late=False):
        options = {}
        if groups: options['post_exit_diagnostic'] = lambda pid, group: modern.group_observation(pid, group, secrets)
        if observer is not None: options['bootstrap_diagnostic'] = observer
        if late:
            if label != 'projection_xctest' or observer is None: raise DeferredError('deferred_invalid_late_observation')
            options['bootstrap_after_seconds'] = 480
        began = time.monotonic()
        status, data = command(args, timeout=timeout, **options)
        elapsed = time.monotonic() - began
        report['command_timing'][label] = ({'state': 'measured', 'elapsed_ms': int(elapsed * 1000)}
            if type(elapsed) in (int, float) and 0 <= elapsed <= 86400 else {'state': 'unavailable'})
        observation = None
        if isinstance(status, dict):
            status = dict(status)
            if groups: report['post_exit_group_observations'][label] = modern.public_group_observation(status.pop('post_exit_diagnostic', None))
            if observer is not None: observation = public_live(status.pop('bootstrap_diagnostic', None))
        if (not isinstance(status, dict) or set(status) != {'status', 'exit_code', 'timeout', 'output_limit', 'captured_bytes'}
                or status.get('status') not in {'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'}
                or type(status.get('timeout')) is not bool or type(status.get('output_limit')) is not bool
                or type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= projection.MAX_OUTPUT
                or not isinstance(data, bytes) or len(data) != status['captured_bytes']
                or not (status.get('exit_code') is None or type(status['exit_code']) is int)):
            raise DeferredError('deferred_command_evidence_invalid')
        report['commands'][label] = status
        if required and (status['status'] != 'completed' or status['exit_code'] != 0):
            raise DeferredError('deferred_' + label + '_failed')
        return ({**status, 'bootstrap_diagnostic': observation} if observer is not None else status), data
    def xcresult(result):
        with tempfile.TemporaryDirectory(prefix='ayu-deferred-xcresult-json-') as temporary:
            private = Path(temporary).resolve(); private.chmod(0o700)
            def query(label, ref=None):
                path = private / (label + '.json')
                args = ['xcresulttool', 'get', '--format', 'json', '--path', str(result), '--legacy']
                if ref is not None: args += ['--id', ref]
                call('xcresult_' + label, [sys.executable, '-c', JSON_WRAPPER, str(path), *args], 20)
                return modern.private_bytes(path, MAX_XCRESULT_JSON_BYTES)
            reference = strict_xcresult_root(query('root'))
            return strict_xcresult_cases(query('cases', reference))
    def suite(label, plan, host):
        identifier = None
        owned = {'attempted': True, 'expected': 1 if label == 'control' else 41,
            'xctest_command_attempted': False, 'tests_started': False, 'started': 0, 'passed': 0,
            'cleanup_completed': False, 'boot_requested': False, 'bootstatus_prerequisite_used': False,
            'xcode_waits_for_device_readiness': False, 'readiness_asserted': False}
        report[label] = owned
        try:
            name = 'AYU_DEFERRED_' + label.upper() + '_' + uuid.uuid4().hex
            _, raw = call(label + '_create', ['xcrun', 'simctl', 'create', name, modern.DEVICE_TYPE, modern.RUNTIME], groups=True)
            candidate = raw.decode().strip()
            if str(uuid.UUID(candidate)).casefold() != candidate.casefold() or candidate.casefold() in used:
                raise DeferredError('deferred_owned_device_invalid')
            used.add(candidate.casefold()); identifier = candidate
            call(label + '_boot_request', ['xcrun', 'simctl', 'boot', identifier], 30, groups=True)
            owned['boot_requested'] = True
            identity = modern.owned_device_identity(identifier, name); owned['device_identity'] = identity
            if identity['state'] == 'rejected': raise DeferredError('deferred_owned_identity_rejected')
            if identity['identity_verified'] is not True:
                status, data = call(label + '_owned_identity', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], 15, False)
                if not (status['status'] == 'completed' and status['exit_code'] == 0 and modern.verify_owned_device(data, identifier, name)):
                    raise DeferredError('deferred_owned_identity_unverified')
            expected_code = projection.digest(host / modern.CONTROL_APP)
            observer = lambda pid, group, alive: live_observation(pid, group, identifier, expected_code, secrets, alive, name)
            with tempfile.TemporaryDirectory(prefix='ayu-deferred-result-') as temporary:
                result = Path(temporary) / 'test.xcresult'
                owned.update(xctest_command_attempted=True, xcode_waits_for_device_readiness=True,
                    manual_prewarm_performed=False, xcode_manages_install_and_launch=True)
                status, raw = call(label + '_xctest', ['xcodebuild', 'test-without-building', '-xctestrun', str(plan),
                    '-destination', 'id=' + identifier, '-destination-timeout', '600', '-parallel-testing-enabled', 'NO',
                    '-derivedDataPath', str(Path(temporary) / 'derived'), '-resultBundlePath', str(result),
                    '-collect-test-diagnostics=never'], 600, False, observer=observer, late=label == 'projection')
                owned['live_bootstrap'] = status.pop('bootstrap_diagnostic')
                counts = modern.control_cases(raw) if label == 'control' else cases(raw)
                owned.update(counts); owned['tests_started'] = counts['started'] > 0
                projection.write(output / (label + '-test.log'), simulator_probe.redact_probe_text(raw, secrets), text=True)
                passed = status['status'] == 'completed' and status['exit_code'] == 0 and counts['all_expected_passed']
                if label == 'projection':
                    owned['xcresult'] = {'state': 'unavailable', 'all_expected_passed': False}
                    try: owned['xcresult'] = {'state': 'parsed', **xcresult(result)}
                    except (DeferredError, InvalidXCResult) as error: owned['xcresult']['reason'] = str(error)
                    except Exception: owned['xcresult']['reason'] = 'xcresult_unavailable'
                    owned['post_test_filesystem'] = {'state': 'unavailable', 'captured_after_xcode_exit': True}
                    owned['deferred_progress'] = {'state': 'unavailable'}
                    try:
                        if modern.owned_device_identity(identifier, name)['identity_verified'] is True:
                            paths, roles = modern.container_observations(identifier)
                            owned['post_test_filesystem'] = {'state': 'observed', 'containers': modern.public_live_observation({'containers': roles})['containers'],
                                'captured_after_xcode_exit': True}
                            owned['deferred_progress'] = public_progress(progress(paths.get('data')))
                            owned['installed_bytes_match'] = ('app' in paths and byte_tree(paths['app']) == byte_tree(host))
                    except Exception:
                        owned['installed_bytes_match'] = False
                    progress_value = owned['deferred_progress']
                    counters = (progress_value.get('state') == 'observed' and summary_matches(progress_value.get('summary')))
                    owned['actual_run_counters_matched'] = counters
                    passed = passed and counters and owned.get('installed_bytes_match') is True and owned['xcresult']['all_expected_passed'] is True
                    report['actual_native_xcresult_verified'] = owned['xcresult']['all_expected_passed'] is True
                owned['passed_own_cases'] = passed
                if not passed: owned['failure'] = 'control_not_passed' if label == 'control' else 'payload_not_passed'
                projection.write(output / (label + '-cases.json'), {**counts,
                    **({'xcresult': owned['xcresult'], 'actual_run_counters_matched': owned['actual_run_counters_matched']} if label == 'projection' else {})})
        finally:
            if identifier is not None:
                outcomes = []
                for operation in ('shutdown', 'delete'):
                    try:
                        status, _ = call(label + '_' + operation, ['xcrun', 'simctl', operation, identifier], 30, False)
                        outcomes.append(status['status'] == 'completed' and status['exit_code'] == 0)
                    except Exception: outcomes.append(False)
                owned['cleanup_completed'] = all(outcomes)
        return owned.get('passed_own_cases') is True and owned['cleanup_completed']
    try:
        if call('xcode', ['xcodebuild', '-version'])[1].splitlines()[:1] != [b'Xcode 27.1']: raise DeferredError('deferred_xcode_mismatch')
        if call('sdk', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])[1].strip() != b'27.1': raise DeferredError('deferred_sdk_mismatch')
        verify_control_project(output / 'private-project')
        report['tiny_project_source_verified'] = True
        report['phase'] = 'buildBaselineControl'
        derived = output / 'private-derived'
        signing = ['CODE_SIGNING_ALLOWED=YES', 'CODE_SIGN_IDENTITY=-', 'CODE_SIGN_STYLE=Manual',
            'CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES', 'DEVELOPMENT_TEAM=', 'PROVISIONING_PROFILE=', 'PROVISIONING_PROFILE_SPECIFIER=']
        status, baseline_log = call('build_control', ['xcodebuild', 'build-for-testing', '-project',
            str(output / 'private-project/AyuProjectionControl.xcodeproj'), '-scheme', modern.CONTROL_APP,
            '-configuration', 'Debug', '-sdk', 'iphonesimulator', '-destination', 'generic/platform=iOS Simulator',
            '-derivedDataPath', str(derived), *signing], 180, False)
        compiler.append(baseline_log)
        if status['status'] != 'completed' or status['exit_code'] != 0: raise DeferredError('deferred_control_compile_failed')
        report['tiny_control_compiled'] = True
        plans = list((derived / 'Build/Products').glob('*.xctestrun'))
        if len(plans) != 1: raise DeferredError('deferred_baseline_plan_not_unique')
        generated = modern._read_plan(plans[0]); _, _, control, control_test, _ = modern.generated_products(generated, plans[0].parent)
        policy = modern.verify_xcode_signing(derived, control, control_test, baseline_log, call, report)
        report['tiny_control_signing_verified'] = True
        immutable.append((control, projection.snapshot_tree(control)))
        immutable.append((control_test, projection.snapshot_tree(control_test)))
        _, platform = call('swift_sdk_platform', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-platform-path'])
        report['swift_xctest_dependencies'] = modern.swift_support_available(platform)
        if not report['swift_xctest_dependencies']['both_available']: raise DeferredError('deferred_swift_support_unavailable')
        report['phase'] = 'buildWrapperOnly'
        project = output / 'private-wrapper-project'; create_wrapper_project(project, wrapper_source)
        wrapper_derived = output / 'private-wrapper-derived'
        build_host = create_build_host(control, control_test, plans[0].parent, wrapper_derived)
        host_executable = str(build_host['host'] / modern.CONTROL_APP)
        # Compile this target only. build-for-testing resolves the scheme's
        # test host as a project target, but this diagnostic deliberately uses
        # the separately built, validated host without another app target.
        wrapper_args = ['-project', str(project / 'DeferredProjection.xcodeproj'),
            '-target', modern.CONTROL_TEST, '-configuration', 'Debug', '-sdk', 'iphonesimulator',
            'SYMROOT=' + str(wrapper_derived / 'Build/Products'),
            'OBJROOT=' + str(wrapper_derived / 'Build/Intermediates.noindex'),
            'TEST_HOST=' + host_executable, 'BUNDLE_LOADER=' + host_executable, *signing]
        with tempfile.TemporaryDirectory(prefix='ayu-deferred-build-settings-') as temporary:
            private = Path(temporary).resolve(); private.chmod(0o700); path = private / 'settings.json'
            status, settings_log = call('wrapper_build_settings', [sys.executable, '-c', JSON_WRAPPER, str(path),
                'xcodebuild', '-showBuildSettings', '-json', *wrapper_args], 20, False)
            compiler.append(settings_log)
            if status['status'] != 'completed' or status['exit_code'] != 0:
                raise DeferredError('deferred_wrapper_build_settings_failed')
            wrapper_path = wrapper_location(modern.private_bytes(path, MAX_XCRESULT_JSON_BYTES), build_host)
        report['wrapper_product_layout'] = 'hostedPlugIns' if wrapper_path.parent == build_host['host'] / 'PlugIns' else 'sibling'
        status, wrapper_log = call('build_wrapper', ['xcodebuild', 'build', *wrapper_args], 180, False)
        compiler.append(wrapper_log)
        report['wrapper_build_host'] = verify_build_host(build_host, wrapper_path,
            require_product=status['status'] == 'completed' and status['exit_code'] == 0)
        if status['status'] != 'completed' or status['exit_code'] != 0: raise DeferredError('deferred_wrapper_compile_failed')
        report['wrapper_compiled'] = True
        wrapper = wrapper_product(wrapper_path)
        immutable.append((wrapper, projection.snapshot_tree(wrapper)))
        report['wrapper_signing'] = verify_wrapper_signing(wrapper_derived, wrapper, wrapper_log, call)
        if any(projection.snapshot_tree(path) != snapshot for path, snapshot in immutable): raise DeferredError('deferred_original_products_changed')
        report['phase'] = 'stageDeferredHost'
        host = output / 'private-deferred-host' / (modern.CONTROL_APP + '.app'); host.parent.mkdir(mode=0o700)
        report['deferred_host'] = stage_host(control, wrapper, original / 'TEST_ROOT' / modern.products.APP, host, policy, call)
        plan, executing = bind_plan(generated, plans[0].parent, host, output / 'private-deferred')
        frozen = projection.snapshot_tree(output / 'private-deferred')
        # Compile and validate both products before paying for a cold device.
        # Actual control failure does not suppress the independent payload run;
        # unverified setup, cleanup or immutability remains a hard safety gate.
        report['phase'] = 'testBaselineControl'
        control_passed = suite('control', plans[0], control)
        if report['control']['cleanup_completed'] is not True: raise DeferredError('deferred_control_cleanup_unverified')
        if any(projection.snapshot_tree(path) != snapshot for path, snapshot in immutable): raise DeferredError('deferred_control_changed')
        report['phase'] = 'testDeferredPayload'
        payload_passed = suite('projection', plan, executing)
        if projection.snapshot_tree(output / 'private-deferred') != frozen: raise DeferredError('deferred_execution_products_changed')
        if not control_passed: raise DeferredError('deferred_control_not_passed')
        if not payload_passed: raise DeferredError('deferred_payload_not_passed')
        report['passed'] = True; report['phase'] = 'complete'
    except Exception as error:
        report['passed'] = False
        report['failure'] = str(error) if isinstance(error, DeferredError) else 'deferred_harness_unavailable'
    finally:
        if build_host is not None:
            try: report['wrapper_build_host'] = verify_build_host(build_host)
            except Exception:
                report['wrapper_build_host'] = {'state': 'unavailable', 'integrity_verified': False}
                report['passed'] = False
        try: report['original_products_unchanged'] = projection.snapshot_tree(original) == original_snapshot
        except Exception: report['original_products_unchanged'] = False
        try: report['original_built_products_unchanged'] = all(projection.snapshot_tree(path) == snapshot for path, snapshot in immutable)
        except Exception: report['original_built_products_unchanged'] = False
        if not report['original_products_unchanged'] or not report['original_built_products_unchanged']: report['passed'] = False
        report['native_xcresult_case_accounting_verified'] = report['passed'] and report['actual_native_xcresult_verified'] is True
        if compiler:
            projection.write(output / 'control-build.log', '\n'.join(modern.compiler_log(data, (*secrets, str(output))) for data in compiler), text=True)
        projection.write(output / 'result.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run')); parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args(argv)
    initially_fresh = False
    if args.command == 'prepare':
        try:
            _, candidate = modern.location(args.output, args.root)
            initially_fresh = not candidate.exists() and not candidate.is_symlink()
        except Exception: pass
    try: result = prepare(args.output, args.root) if args.command == 'prepare' else run(args.output, args.root)
    except Exception as error:
        result = preflight_failure(error, 'prepare' if args.command == 'prepare' else 'runPreflight')
        retained = args.command == 'prepare' and retain_prepare_failure(args.output, args.root, result, initially_fresh)
        print(json.dumps({**result, 'failure_report_retained': retained}, sort_keys=True)); return 1
    print(json.dumps({'passed': result.get('passed', False), 'different_harness': True, 'release_qualified': False}))
    return 0 if args.command == 'prepare' or result.get('passed') else 1


if __name__ == '__main__': raise SystemExit(main())
