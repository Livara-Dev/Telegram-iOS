#!/usr/bin/env python3
"""Replay one pinned historical synthetic Projection test artifact without Bazel.

This diagnostic never qualifies a new application revision or publishes products,
raw XCTest results, account data, API credentials, or a device installation claim.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

import replay_artifact as artifacts
import test_product_capture as products
import simulator_probe
from custom_options_smoke import EXPORT_ENVIRONMENT_KEYS
from launch_smoke import POST_EXIT_DRAIN_SECONDS

SOURCE = '17033b099458c258236b5a01411ab6efb45b37b6'
RUN = 42134690056
ARTIFACT = 13470438745
OUTER_SHA256 = 'f11ce130a5d209355ccbe0a37731e27af0db9504fce65f1a8eff644edd5581fd'
PINS = {'app': '13.1.2', 'xcode': '27.1', 'deploy_xcode': '27.1', 'macos': '27',
        'bazel': '9.2.0:dd466352a3e4d3581b8898740ee1ff208866ccbe25f8d367c5dcb950219587e6'}
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-17'
DEVICE_NAME = 'BAZEL_TEST_iPhone 17_27.0'
OUTPUT = 'build/fork-evidence/projection-replay'
MAX_OUTPUT = 1024 * 1024
EXECUTABLES = {'TEST_ROOT/' + products.APP + '/ProjectionTestHost',
               'TEST_ROOT/' + products.APP + '/PlugIns/' + products.TEST + '/ChatLocalTextFilterProjectionTests'}
# Exact methods in the captured SOURCE, not a count inferred from product files.
EXPECTED = frozenset(('ChatLocalTextFilterProjectionTests.testProjectionRunsInIsolatedIOSHostWithPresentationResources',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryPhotoCanHideAndRevealWithoutChangingItsMedia',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryVideoCanHideAndRevealWithoutChangingItsMedia',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryGIFCanHideAndRevealWithoutChangingItsMedia',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryVoiceCanHideAndRevealWithoutConsumingIt',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryRoundVideoCanHideAndRevealWithoutConsumingIt',
 'ChatLocalTextFilterProjectionTests.testSingleOrdinaryDocumentCanHideAndRevealWithoutChangingItsMedia',
 'ChatLocalTextFilterProjectionTests.testMediaExpiryProtectionGroupingAndUnknownMetadataStayVisible',
 'ChatLocalTextFilterProjectionTests.testChangingAuthorOrMediaClassificationInvalidatesAQueuedDecision',
 'ChatLocalTextFilterProjectionTests.testProductionSnapshotBridgeAppliesLocalSenderIdentityAndAllowsReveal',
 'ChatLocalTextFilterProjectionTests.testInverseRulesAndSenderMatchesCannotHideIneligibleMetadata',
 'ChatLocalTextFilterProjectionTests.testInverseMediaProjectionUsesKnownTypesAndKeepsTextAndAllowedMediaVisible',
 'ChatLocalTextFilterProjectionTests.testStickerAndCustomEmojiNodesCannotBePromotedIntoFilterBubbles',
 'ChatLocalTextFilterProjectionTests.testOrdinaryIncomingCloudTextAndAllowedMetadataRemainEligible',
 'ChatLocalTextFilterProjectionTests.testUnsupportedContentIsNotHiddenEvenWhenSnapshotMatches',
 'ChatLocalTextFilterProjectionTests.testAllMatchedRowsKeepCountOrderIdentityAndIndexes',
 'ChatLocalTextFilterProjectionTests.testOnlyMatchedMessageChangesPresentation',
 'ChatLocalTextFilterProjectionTests.testRevealAndRehidePreserveCanonicalMessageAndPriorRows',
 'ChatLocalTextFilterProjectionTests.testRemovingMatchesClearsBothHiddenAndRevealedState',
 'ChatLocalTextFilterProjectionTests.testSelectionModeRevealsAllMatchesAndClearingSelectionAllowsRehide',
 'ChatLocalTextFilterProjectionTests.testSelectedMessageStaysVisibleEvenWithInconsistentSelectionModeFlag',
 'ChatLocalTextFilterProjectionTests.testStaleMessageVersionOrTextNeverInheritsPreviousMatch',
 'ChatLocalTextFilterProjectionTests.testSnapshotIdentityRejectsOtherPeerMessageAndNamespace',
 'ChatLocalTextFilterProjectionTests.testLargeAndAnimatedEmojiKeepTheirOriginalNodeClassification',
 'ChatLocalTextFilterProjectionTests.testAlbumAndNonMessageEntriesPassThroughInTheirOriginalOrder',
 'ChatLocalArchiveProjectionTests.testLocalRowsSortBetweenUnmodifiedCanonicalObjects',
 'ChatLocalArchiveProjectionTests.testCanonicalAndAlbumIdentitiesWinOverArchiveDuplicates',
 'ChatLocalArchiveProjectionTests.testRepeatedDeletedIdHasOneStableIdentityAndNewVersionUpdatesEquality',
 'ChatLocalArchiveProjectionTests.testArchiveIdentityCannotCollideWithCanonicalOrArbitraryAlbumBits',
 'ChatLocalArchiveProjectionTests.testOtherAccountChatPeerCannotAppear',
 'ChatLocalArchiveProjectionTests.testUnsafeIdentifiersAndTimestampsFailClosed',
 'ChatLocalArchiveProjectionTests.testRevokedBodyNeverReentersAReplayedTransition',
 'ChatLocalArchiveProjectionTests.testProtectedSelectionPreviewOrFilterGateDoesNotInsertAnyLocalRows',
 'ChatLocalArchiveProjectionTests.testOutputBoundDoesNotExpandWithLargeArchivePage',
 'ChatLocalArchiveProjectionTests.testTruncationNoticeSurvivesDeduplicationOfItsOriginalRow',
 'ChatLocalArchiveProjectionTests.testReverseHistoryMaintainsComparatorOrder',
 'ChatLocalArchiveProjectionTests.testOnlyCanonicalRowsCanSupplyNetworkPaginationAndReadAnchors',
 'ChatLocalArchiveRowRendererTests.testActualRendererExposesLocalMarkerTimestampAndMediaAvailabilityWithoutServerActions',
 'ChatLocalArchiveRowRendererTests.testReusedRendererCannotRestoreRevokedPreviousBody',
 'ChatLocalArchiveRowRendererTests.testLongAndMediaOnlyRowsHaveBoundedNonoverlappingLayoutOnPhoneWidths',
 'ChatLocalArchiveRowRendererTests.testRendererUsesRealLightAndDarkThemeColorsAndAlwaysClearsBoth'))


def origin(run, source, api=artifacts.gh_json):
    run, source = artifacts.coordinates(run, source)
    if (run, source) != (RUN, SOURCE):
        raise artifacts.ReplayError('unsupported_projection_replay_source')
    value = api(artifacts.BASE + '/actions/runs/' + str(run))
    if (not isinstance(value, dict) or value.get('id') != run or value.get('head_sha') != source or
            value.get('status') != 'completed' or not isinstance(value.get('path'), str) or
            value['path'].split('@', 1)[0] != artifacts.WORKFLOW):
        raise artifacts.ReplayError('projection_run_source_mismatch')
    repo, head = value.get('repository'), value.get('head_repository')
    if (not isinstance(repo, dict) or not isinstance(head, dict) or repo.get('full_name') != artifacts.REPOSITORY or
            head.get('full_name') != artifacts.REPOSITORY or type(repo.get('id')) is not int or
            repo['id'] <= 0 or type(head.get('id')) is not int or head['id'] != repo['id']):
        raise artifacts.ReplayError('projection_repository_mismatch')
    listing = api(artifacts.BASE + '/actions/runs/' + str(run) + '/artifacts?per_page=100')
    if (not isinstance(listing, dict) or type(listing.get('total_count')) is not int or
            not 0 <= listing['total_count'] <= 100 or not isinstance(listing.get('artifacts'), list)):
        raise artifacts.ReplayError('projection_artifact_metadata_invalid')
    selected = [a for a in listing['artifacts'] if isinstance(a, dict) and a.get('name') == 'archive-integration-tests-' + source]
    if len(selected) != 1:
        raise artifacts.ReplayError('projection_artifact_not_unique')
    artifact = selected[0]; provenance = artifact.get('workflow_run')
    if (type(artifact.get('id')) is not int or artifact['id'] != ARTIFACT or artifact.get('expired') is not False or
            type(artifact.get('size_in_bytes')) is not int or not 0 < artifact['size_in_bytes'] <= artifacts.MAX_ZIP_BYTES or
            artifact.get('digest') != 'sha256:' + OUTER_SHA256 or not isinstance(provenance, dict) or
            provenance.get('id') != run or provenance.get('head_sha') != source or
            provenance.get('repository_id') != repo['id'] or provenance.get('head_repository_id') != repo['id']):
        raise artifacts.ReplayError('projection_artifact_provenance_mismatch')
    return artifact


def digest(path):
    with Path(path).open('rb') as stream:
        return artifacts.digest_stream(stream)


def validate_products(root):
    root = Path(root)
    manifest = json.loads(products.read_small(products.fixed_path(root, 'manifest.json')))
    capture = json.loads(products.read_small(products.fixed_path(root, 'capture.json')))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'source_commit', 'test_target', 'toolchain',
            'application_recompiled_for_replay', 'diagnostic_products_not_test_pass', 'physical_device_verified', 'files'} or
            manifest.get('schema_version') != 1 or manifest.get('source_commit') != SOURCE or
            manifest.get('test_target') != products.LABEL or manifest.get('toolchain') != PINS or
            manifest.get('application_recompiled_for_replay') is not False or manifest.get('physical_device_verified') is not False or
            manifest.get('diagnostic_products_not_test_pass') is not True or not isinstance(manifest.get('files'), dict)):
        raise artifacts.ReplayError('projection_manifest_provenance_mismatch')
    if (not isinstance(capture, dict) or set(capture) != {'schema_version', 'target', 'synthetic_host_identity_validated',
            'xctest_plan_sanitized', 'diagnostic_products_not_test_pass', 'files', 'total_product_bytes'} or
            capture.get('schema_version') != 1 or capture.get('target') != products.LABEL or
            any(capture.get(k) is not True for k in ('synthetic_host_identity_validated', 'xctest_plan_sanitized', 'diagnostic_products_not_test_pass')) or
            not isinstance(capture.get('files'), dict) or type(capture.get('total_product_bytes')) is not int):
        raise artifacts.ReplayError('projection_capture_invalid')
    hashes = manifest['files']
    actual = {}
    for item in root.rglob('*'):
        if item.is_symlink() or (not item.is_dir() and not item.is_file()):
            raise artifacts.ReplayError('projection_nonregular_product')
        if item.is_file(): actual[str(item.relative_to(root))] = item
    if (not 0 < len(hashes) <= products.MAX_FILES + 1 or set(actual) != set(hashes) | {'manifest.json', 'SHA256SUMS'} or
            set(hashes) != set(capture['files']) | {'capture.json'}):
        raise artifacts.ReplayError('projection_product_inventory_mismatch')
    total = 0
    for name, expected in hashes.items():
        products.relative_name(name)
        if name != 'capture.json':
            if not name.startswith('TEST_ROOT/'):
                raise artifacts.ReplayError('projection_product_location_invalid')
            if name != 'TEST_ROOT/test.xctestrun':
                products.allowed_product(name[len('TEST_ROOT/'):])
                if not name.startswith('TEST_ROOT/' + products.APP + '/'):
                    raise artifacts.ReplayError('projection_product_location_invalid')
            if capture['files'].get(name) != expected:
                raise artifacts.ReplayError('projection_capture_hash_mismatch')
        if not isinstance(expected, str) or not artifacts.DIGEST.fullmatch(expected) or digest(actual[name]) != expected:
            raise artifacts.ReplayError('projection_product_hash_mismatch')
        total += actual[name].stat().st_size
        if total > products.MAX_TOTAL_BYTES:
            raise artifacts.ReplayError('projection_products_too_large')
    product_bytes = sum(actual[n].stat().st_size for n in capture['files'] if n != 'TEST_ROOT/test.xctestrun')
    if capture['total_product_bytes'] != product_bytes:
        raise artifacts.ReplayError('projection_capture_size_mismatch')
    checksum_lines = products.read_small(actual['SHA256SUMS']).decode('ascii').splitlines()
    checksums = {}
    for line in checksum_lines:
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in checksums:
            raise artifacts.ReplayError('projection_checksums_invalid')
        checksums[match[2]] = match[1]
    if checksums != {**hashes, 'manifest.json': digest(actual['manifest.json'])}:
        raise artifacts.ReplayError('projection_checksums_mismatch')
    plan = plistlib.loads(products.read_small(actual['TEST_ROOT/test.xctestrun']))
    if plan != products.sanitize_plan(root / 'TEST_ROOT'):
        raise artifacts.ReplayError('projection_plan_not_sanitized')
    for name in EXECUTABLES:
        path = actual[name]
        with path.open('rb') as binary:
            if not products.simulator_macho(lambda offset, count: (binary.seek(offset), binary.read(count))[1], path.stat().st_size):
                raise artifacts.ReplayError('projection_not_arm64_simulator_code')
        path.chmod(0o700)
    return {'product_files_verified': len(hashes), 'product_bytes_verified': product_bytes, 'sanitized_plan_verified': True}


def extract(archive_path, artifact, destination):
    archive_path, destination = Path(archive_path), Path(destination)
    if (archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size != artifact['size_in_bytes'] or
            archive_path.stat().st_size > artifacts.MAX_ZIP_BYTES or digest(archive_path) != OUTER_SHA256):
        raise artifacts.ReplayError('projection_outer_digest_mismatch')
    if destination.exists() or destination.is_symlink():
        raise artifacts.ReplayError('projection_destination_not_fresh')
    prefix = products.CAPTURE + '/'
    with zipfile.ZipFile(archive_path) as zipped:
        inventory = zipped.infolist()
        if len(inventory) > artifacts.MAX_FILES:
            raise artifacts.ReplayError('projection_archive_inventory_too_large')
        selected, seen, total = [], set(), 0
        for entry in inventory:
            artifacts.member_name(entry.filename, directory=entry.is_dir())
            if not entry.filename.startswith(prefix): continue
            name = entry.filename[len(prefix):].rstrip('/')
            products.relative_name(name)
            if name.casefold() in seen or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise artifacts.ReplayError('projection_unsafe_archive_member')
            seen.add(name.casefold())
            if entry.is_dir(): continue
            if (name not in {'manifest.json', 'capture.json', 'SHA256SUMS', 'TEST_ROOT/test.xctestrun'} and
                    not name.startswith('TEST_ROOT/' + products.APP + '/')):
                raise artifacts.ReplayError('projection_unexpected_archive_member')
            if name.startswith('TEST_ROOT/') and name != 'TEST_ROOT/test.xctestrun': products.allowed_product(name[10:])
            if not 0 <= entry.file_size <= products.MAX_FILE_BYTES:
                raise artifacts.ReplayError('projection_archive_member_too_large')
            total += entry.file_size; selected.append((entry, name))
        if not 0 < len(selected) <= products.MAX_FILES + 3 or total > products.MAX_TOTAL_BYTES:
            raise artifacts.ReplayError('projection_archive_products_too_large')
        destination.mkdir(mode=0o700)
        for entry, name in selected:
            target = destination / name; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with zipped.open(entry) as source, target.open('xb') as output:
                count = 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > entry.file_size: raise artifacts.ReplayError('projection_archive_size_mismatch')
                    output.write(chunk)
            if count != entry.file_size: raise artifacts.ReplayError('projection_archive_size_mismatch')
            target.chmod(0o600)
    return validate_products(destination)


def child_environment():
    return {k: v for k, v in os.environ.items() if k in EXPORT_ENVIRONMENT_KEYS}


@contextmanager
def clean_environment():
    original = dict(os.environ)
    try:
        child = child_environment(); os.environ.clear(); os.environ.update(child)
        yield
    finally:
        os.environ.clear(); os.environ.update(original)


def native_command(arguments, timeout=30):
    """Bound subprocess lifetime/output, including inherited pipes after exit."""
    process = None; data = bytearray(); code = None; state = 'unavailable'
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=child_environment(), start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ); drain = None
            while selector.get_map():
                code = process.poll(); now = time.monotonic()
                if code is not None and drain is None: drain = min(deadline, now + POST_EXIT_DRAIN_SECONDS)
                left = (drain if drain is not None else deadline) - now
                if left <= 0:
                    if code is None: state = 'timedOut'
                    break
                for key, _ in selector.select(min(left, .05)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk: selector.unregister(key.fileobj); continue
                    room = MAX_OUTPUT - len(data); data.extend(chunk[:room])
                    if len(chunk) > room: state = 'outputLimit'; break
                if state == 'outputLimit': break
        if state not in {'timedOut', 'outputLimit'}:
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
                state = 'completed' if code == 0 else 'commandFailed'
            except subprocess.TimeoutExpired:
                state, code = 'timedOut', process.poll()
    except OSError:
        state = 'unavailable'
    finally:
        if process is not None:
            # A leader can exit while a descendant still owns its stdout pipe.
            # start_new_session gives this invocation its own group: retire that
            # group after the bounded drain even when the leader already exited.
            try: os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                if process.poll() is None:
                    try: process.kill()
                    except OSError: pass
            try: process.wait(timeout=2)
            except subprocess.SubprocessError: pass
            if process.stdout is not None: process.stdout.close()
    return {'status': state, 'exit_code': code, 'timeout': state == 'timedOut',
            'output_limit': state == 'outputLimit', 'captured_bytes': len(data)}, bytes(data)


def cases(data):
    started, outcomes, unknown, duplicates, invalid_order = set(), {}, False, False, False
    pattern = re.compile(r"^Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?:[ .(].*)?$", re.M)
    for match in pattern.finditer(data.decode('utf-8', errors='replace')):
        key = match[1] + '.' + match[2]; status = match[3]
        if key not in EXPECTED: unknown = True; continue
        if status == 'started':
            if key in started: duplicates = True
            started.add(key)
        else:
            if key in outcomes: duplicates = True
            if key not in started: invalid_order = True
            outcomes[key] = status
    missing = sorted(set(EXPECTED) - set(outcomes)); unstarted = sorted(set(outcomes) - started)
    return {'expected': len(EXPECTED), 'passed': sum(v == 'passed' for v in outcomes.values()),
            'failed': sum(v == 'failed' for v in outcomes.values()), 'crashed': sum(v == 'crashed' for v in outcomes.values()), 'skipped': sum(v == 'skipped' for v in outcomes.values()),
            'missing': missing, 'outcome_without_start': unstarted, 'duplicate_case_events': duplicates,
            'unexpected_case_events': unknown, 'invalid_case_event_order': invalid_order, 'cases': dict(sorted(outcomes.items())),
            'all_expected_passed': not (missing or unstarted or duplicates or unknown or invalid_order) and len(outcomes) == len(EXPECTED) and all(v == 'passed' for v in outcomes.values())}


def execute(products_root, output, root, command=native_command, probe=simulator_probe.capture):
    output, root = Path(output), Path(root); commands = {}; identifier = None
    report = {'commands': commands, 'passed': False, 'tests_started': False, 'assertion_retries': 0}
    secrets = tuple(v for k, v in os.environ.items() if v and re.search(r'TOKEN|SECRET|PASSWORD|API_ID|API_HASH|AUTH|PRIVATE_KEY|SESSION', k, re.I))
    def call(name, arguments, timeout=30, required=True):
        status, stdout = command(arguments, timeout=timeout); commands[name] = status
        if required and status['status'] != 'completed': raise artifacts.ReplayError('projection_' + name + '_failed')
        return status, stdout
    try:
        _, version = call('xcode', ['xcodebuild', '-version'])
        _, sdk = call('sdk', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])
        if version.decode().splitlines()[0] != 'Xcode 27.1' or sdk.strip() != b'27.1':
            raise artifacts.ReplayError('projection_toolchain_mismatch')
        _, raw = call('runtimes', ['xcrun', 'simctl', 'list', 'runtimes', '--json'])
        runtimes = json.loads(raw)['runtimes']
        if len([r for r in runtimes if r.get('identifier') == RUNTIME and r.get('version') == '27.0' and r.get('isAvailable') is True]) != 1:
            raise artifacts.ReplayError('projection_runtime_unavailable')
        _, raw = call('devices', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'])
        if any(d.get('name') == DEVICE_NAME for ds in json.loads(raw)['devices'].values() for d in ds):
            raise artifacts.ReplayError('projection_device_name_already_exists')
        _, raw = call('create', ['xcrun', 'simctl', 'create', DEVICE_NAME, DEVICE_TYPE, RUNTIME])
        candidate = raw.decode().strip()
        if str(uuid.UUID(candidate)).upper() != candidate.upper(): raise artifacts.ReplayError('projection_device_identity_invalid')
        identifier = candidate
        status, _ = call('boot', ['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=240, required=False)
        if status['status'] != 'completed':
            if status['status'] != 'commandFailed' or status['exit_code'] != 149 or status['timeout']:
                raise artifacts.ReplayError('projection_boot_failed')
            _, raw = call('boot_state', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'], timeout=15)
            matching = [d for d in json.loads(raw)['devices'].get(RUNTIME, []) if d.get('udid') == identifier and d.get('name') == DEVICE_NAME and d.get('state') == 'Booted']
            if len(matching) != 1: raise artifacts.ReplayError('projection_boot_state_unverified')
            report['already_booted_state_verified'] = True
        with tempfile.TemporaryDirectory(prefix='ayu-projection-results-') as private:
            result = Path(private) / 'test.xcresult'
            report['tests_started'] = True
            started_at = time.time()
            status, data = call('xctest', ['xcodebuild', 'test-without-building', '-xctestrun', str(Path(products_root) / 'TEST_ROOT/test.xctestrun'),
                '-destination', 'id=' + identifier, '-destination-timeout', '30', '-derivedDataPath', str(Path(private) / 'derived'),
                '-resultBundlePath', str(result), '-parallel-testing-enabled', 'NO', '-collect-test-diagnostics=never'], timeout=300, required=False)
            outcome = cases(data); report['case_counts'] = {k: v for k, v in outcome.items() if k != 'cases'}
            write(output / 'case-results.json', outcome)
            text = simulator_probe.redact_probe_text(data, secrets)
            write(output / 'test.log', text, text=True)
            report['passed'] = status['status'] == 'completed' and outcome['all_expected_passed']
        # Reuse only the synthetic-host probe, with credentials absent from every
        # nested native command; raw XCResult and simulator files stay private.
        with tempfile.TemporaryDirectory(prefix='ayu-projection-probe-') as private:
            with clean_environment(): probe(Path(private), secrets, started_at)
            diagnostic = Path(private) / 'build/fork-evidence/projection-launch-probe.json'
            if diagnostic.is_file() and not diagnostic.is_symlink() and diagnostic.stat().st_size <= 256 * 1024:
                write(output / 'projection-launch-probe.json', json.loads(products.read_small(diagnostic, 256 * 1024)))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, IndexError, RuntimeError) as error:
        report['passed'] = False
        report['failure'] = str(error) if isinstance(error, artifacts.ReplayError) else 'projection_diagnostic_unavailable'
    finally:
        if identifier is not None:
            for operation in ('shutdown', 'delete'):
                try: call(operation, ['xcrun', 'simctl', operation, identifier], timeout=30, required=False)
                except (OSError, ValueError, TypeError): report['passed'] = False
            report['owned_device_cleanup_completed'] = all(commands.get(op, {}).get('status') == 'completed' for op in ('shutdown', 'delete'))
            if not report['owned_device_cleanup_completed']: report['passed'] = False
    return report


def write(path, value, text=False):
    with Path(path).open('x', encoding='utf-8') as file:
        Path(path).chmod(0o600)
        file.write(value if text else json.dumps(value, indent=2, sort_keys=True) + '\n')


def replay(run, source, output, root=None, api=artifacts.gh_json, fetch=artifacts.download, command=native_command, probe=simulator_probe.capture):
    root = Path(root or Path(__file__).resolve().parents[2]).resolve(strict=True)
    output = Path(output); output = output if output.is_absolute() else root / output
    if output != root / OUTPUT or any(x.is_symlink() for x in (output, *output.parents)) or output.exists():
        raise artifacts.ReplayError('projection_output_not_fresh_fixed_path')
    artifact = origin(run, source, api)
    output.mkdir(mode=0o700, parents=True)
    report = {'schema_version': 1, 'repository': artifacts.REPOSITORY, 'source_run': RUN, 'source_commit': SOURCE,
              'artifact_id': ARTIFACT, 'artifact_sha256': OUTER_SHA256, 'diagnostic_replay_only': True,
              'application_recompiled_for_replay': False, 'new_checkout_qualified': False,
              'full_application_built': False, 'physical_device_verified': False, 'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-projection-replay-') as private:
            archive = Path(private) / 'artifact.zip'; fetch(ARTIFACT, archive)
            verified = extract(archive, artifact, Path(private) / 'products')
            report.update(verified)
            report.update(execute(Path(private) / 'products', output, root, command, probe))
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile):
        report['failure'] = 'projection_artifact_verification_failed'
    write(output / 'result.json', report)
    hashes = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    write(output / 'SHA256SUMS', ''.join(value + '  ' + name + '\n' for name, value in sorted(hashes.items())), text=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True); parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try: report = replay(args.run, args.source, args.output)
    except artifacts.ReplayError as error:
        print(json.dumps({'passed': False, 'failure': str(error)})); return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print(json.dumps({'passed': False, 'failure': 'projection_replay_preparation_failed'})); return 1
    print(json.dumps({'passed': report['passed'], 'diagnostic_replay_only': True, 'source_commit': SOURCE}, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
