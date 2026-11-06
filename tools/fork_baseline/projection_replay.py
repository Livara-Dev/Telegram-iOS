#!/usr/bin/env python3
"""Replay one pinned historical synthetic Projection test artifact without Bazel.

This diagnostic never qualifies a new application revision or publishes products,
raw XCTest results, account data, API credentials, or a device installation claim.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from xml.parsers.expat import ExpatError

import replay_artifact as artifacts
import test_product_capture as products
import simulator_probe
import bootstrap_sample as bootstrap_sampling
from custom_options_smoke import EXPORT_ENVIRONMENT_KEYS
from launch_smoke import POST_EXIT_DRAIN_SECONDS
from startup_replay import main_code_digest

SOURCE = '17033b099458c258236b5a01411ab6efb45b37b6'
RUN = 42134690056
ARTIFACT = 13470438745
OUTER_SHA256 = 'f11ce130a5d209355ccbe0a37731e27af0db9504fce65f1a8eff644edd5581fd'
CURRENT_SOURCE = 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'
CURRENT_RUN = 42160468600
CURRENT_ARTIFACT = 13482555818
CURRENT_OUTER_SHA256 = 'b1994fd195dc85097d8984c0eeb97508ccc60747f847de883636f137ce409f29'
PINS = {'app': '13.1.2', 'xcode': '27.1', 'deploy_xcode': '27.1', 'macos': '27',
        'bazel': '9.2.0:dd466352a3e4d3581b8898740ee1ff208866ccbe25f8d367c5dcb950219587e6'}
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
DEVICE_NAME = 'AYU_REPLAY_iPhone 15 Pro_27.0'
DEVICE_PROFILE = 'replay-iphone15pro'
OUTPUT = 'build/fork-evidence/projection-replay'
MAX_OUTPUT = 1024 * 1024
BOOTSTRAP_SAMPLE_AFTER_SECONDS = 90
BOOTSTRAP_SAMPLE_LATE_AFTER_SECONDS = 480
BOOTSTRAP_SAMPLE_JOIN_SECONDS = 20
BOOTSTRAP_SAMPLE_OUTPUT_LIMIT = 64 * 1024
POST_EXIT_DIAGNOSTIC_SECONDS = 3
POST_EXIT_DIAGNOSTIC_OUTPUT_LIMIT = 8 * 1024
POST_EXIT_GROUP_ROLES = ('CoreSimulatorService', 'SimulatorTrampoline', 'simctl',
                         'launchd_sim', 'xcodebuild', 'AyuProjectionControl', 'other')
HOST_LOG_PROCESSES = frozenset({'xcodebuild', 'CoreSimulatorService', 'testmanagerd',
                                'runningboardd', 'xctest', 'launchd_sim'})
HOST_LOG_IDENTITIES = simulator_probe.BOOTSTRAP_IDENTITIES
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


def pinned(run, source):
    run, source = artifacts.coordinates(run, source)
    if (run, source) == (RUN, SOURCE):
        return {'run': run, 'source': source, 'artifact': ARTIFACT, 'digest': OUTER_SHA256,
                'name': 'archive-integration-tests-' + source, 'prefix': products.CAPTURE + '/'}
    if (run, source) == (CURRENT_RUN, CURRENT_SOURCE):
        return {'run': run, 'source': source, 'artifact': CURRENT_ARTIFACT, 'digest': CURRENT_OUTER_SHA256,
                'name': 'archive-integration-' + source, 'prefix': 'fork-evidence/' + products.CAPTURE + '/'}
    raise artifacts.ReplayError('unsupported_projection_replay_source')


def origin(run, source, api=artifacts.gh_json):
    specification = pinned(run, source)
    run, source = specification['run'], specification['source']
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
    selected = [a for a in listing['artifacts'] if isinstance(a, dict) and a.get('name') == specification['name']]
    if len(selected) != 1:
        raise artifacts.ReplayError('projection_artifact_not_unique')
    artifact = selected[0]; provenance = artifact.get('workflow_run')
    if (type(artifact.get('id')) is not int or artifact['id'] != specification['artifact'] or artifact.get('expired') is not False or
            type(artifact.get('size_in_bytes')) is not int or not 0 < artifact['size_in_bytes'] <= artifacts.MAX_ZIP_BYTES or
            artifact.get('digest') != 'sha256:' + specification['digest'] or not isinstance(provenance, dict) or
            provenance.get('id') != run or provenance.get('head_sha') != source or
            provenance.get('repository_id') != repo['id'] or provenance.get('head_repository_id') != repo['id']):
        raise artifacts.ReplayError('projection_artifact_provenance_mismatch')
    return artifact


def digest(path):
    with Path(path).open('rb') as stream:
        return artifacts.digest_stream(stream)


def validate_products(root, source=SOURCE):
    if source not in {SOURCE, CURRENT_SOURCE}: raise artifacts.ReplayError('unsupported_projection_replay_source')
    root = Path(root)
    manifest = json.loads(products.read_small(products.fixed_path(root, 'manifest.json')))
    capture = json.loads(products.read_small(products.fixed_path(root, 'capture.json')))
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'source_commit', 'test_target', 'toolchain',
            'application_recompiled_for_replay', 'diagnostic_products_not_test_pass', 'physical_device_verified', 'files'} or
            manifest.get('schema_version') != 1 or manifest.get('source_commit') != source or
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


def extract(archive_path, artifact, destination, source=SOURCE):
    specification = pinned(CURRENT_RUN if source == CURRENT_SOURCE else RUN, source)
    archive_path, destination = Path(archive_path), Path(destination)
    if (archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size != artifact['size_in_bytes'] or
            archive_path.stat().st_size > artifacts.MAX_ZIP_BYTES or digest(archive_path) != specification['digest']):
        raise artifacts.ReplayError('projection_outer_digest_mismatch')
    if destination.exists() or destination.is_symlink():
        raise artifacts.ReplayError('projection_destination_not_fresh')
    prefix = specification['prefix']
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
            with zipped.open(entry) as source_stream, target.open('xb') as output:
                count = 0
                while chunk := source_stream.read(1024 * 1024):
                    count += len(chunk)
                    if count > entry.file_size: raise artifacts.ReplayError('projection_archive_size_mismatch')
                    output.write(chunk)
            if count != entry.file_size: raise artifacts.ReplayError('projection_archive_size_mismatch')
            target.chmod(0o600)
    return validate_products(destination, source)


def verify_current_checkout(root):
    """Only tooling/docs may differ from the exact current captured source."""
    artifacts.verify_checkout(root, CURRENT_SOURCE)
    expected = set()
    directory = 'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Tests/'
    for filename in ('ChatLocalTextFilterProjectionTests.swift', 'ChatLocalArchiveProjectionTests.swift', 'ChatLocalArchiveRowRendererTests.swift'):
        data = artifacts.git(root, 'show', CURRENT_SOURCE + ':' + directory + filename)
        if not 0 < len(data) <= 1024**2: raise artifacts.ReplayError('projection_expected_source_invalid')
        text = data.decode('utf-8')
        classes = re.findall(r'^final class (\w+): XCTestCase', text, re.M)
        methods = re.findall(r'^\s+func (test\w+)\(', text, re.M)
        if len(classes) != 1 or len(methods) != len(set(methods)):
            raise artifacts.ReplayError('projection_expected_source_invalid')
        expected.update(classes[0] + '.' + method for method in methods)
    if expected != EXPECTED: raise artifacts.ReplayError('projection_expected_cases_changed')


def snapshot_tree(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir(): raise artifacts.ReplayError('projection_unsafe_control_tree')
    result, total = {}, 0
    for path in root.rglob('*'):
        mode = path.lstat().st_mode
        name = str(path.relative_to(root)); products.relative_name(name)
        if stat.S_ISDIR(mode): result[name] = ('directory', stat.S_IMODE(mode), None)
        elif stat.S_ISREG(mode):
            size = path.stat().st_size; total += size
            if not 0 <= size <= products.MAX_FILE_BYTES or total > products.MAX_TOTAL_BYTES:
                raise artifacts.ReplayError('projection_control_limit')
            result[name] = ('file', stat.S_IMODE(mode), digest(path))
        else: raise artifacts.ReplayError('projection_unsafe_control_tree')
        if len(result) > 2 * products.MAX_FILES: raise artifacts.ReplayError('projection_control_limit')
    return result


def _host_signature(app):
    """Read only the fixed host's bounded embedded signing metadata."""
    app = Path(app)
    info = plistlib.loads(products.read_small(products.fixed_path(app, 'Info.plist')))
    if not isinstance(info, dict) or info.get('CFBundleIdentifier') != products.BUNDLE or info.get('CFBundleExecutable') != 'ProjectionTestHost':
        raise artifacts.ReplayError('projection_control_identity_mismatch')
    code_digest = main_code_digest(app)
    path = products.fixed_path(app, 'ProjectionTestHost')
    with path.open('rb') as stream:
        header = stream.read(32); count, length = struct.unpack_from('<II', header, 16)
        if not 1 <= count <= 4096 or not count * 8 <= length <= 1024**2:
            raise artifacts.ReplayError('projection_invalid_signature')
        commands = stream.read(length); offset, selected = 0, None
        for _ in range(count):
            code, size = struct.unpack_from('<II', commands, offset)
            if size < 8 or size % 8 or offset + size > len(commands): raise artifacts.ReplayError('projection_invalid_signature')
            if code == 0x1D:
                if selected is not None or size != 16: raise artifacts.ReplayError('projection_invalid_signature')
                selected = struct.unpack_from('<II', commands, offset + 8)
            offset += size
        if offset != length or selected is None: raise artifacts.ReplayError('projection_invalid_signature')
        start, size = selected
        if not 12 <= size <= 1024**2 or start + size > path.stat().st_size:
            raise artifacts.ReplayError('projection_invalid_signature')
        stream.seek(start); signature = stream.read(size)
    magic, size, count = struct.unpack_from('>III', signature)
    if magic != 0xFADE0CC0 or not 12 + count * 8 <= size <= len(signature) or not 1 <= count <= 32:
        raise artifacts.ReplayError('projection_invalid_signature')
    blobs, ranges = {}, []
    for index in range(count):
        slot, offset = struct.unpack_from('>II', signature, 12 + index * 8)
        if slot in blobs or not 12 + count * 8 <= offset <= size - 8:
            raise artifacts.ReplayError('projection_invalid_signature')
        _, length = struct.unpack_from('>II', signature, offset)
        if length < 8 or offset + length > size or any(offset < end and begin < offset + length for begin, end in ranges):
            raise artifacts.ReplayError('projection_invalid_signature')
        blobs[slot] = signature[offset:offset + length]; ranges.append((offset, offset + length))
    cd = blobs.get(0, b'')
    if len(cd) < 44 or struct.unpack_from('>I', cd)[0] != 0xFADE0C02:
        raise artifacts.ReplayError('projection_invalid_signature')
    flags, hash_offset, identifier_offset, specials, code_slots, code_limit = struct.unpack_from('>6I', cd, 12)
    hash_size, hash_type, _, page = struct.unpack_from('>4B', cd, 36)
    if (hash_type != 2 or hash_size != 32 or not 1 <= page <= 20 or not 7 <= specials <= 32
            or hash_offset - specials * hash_size < 44 or hash_offset + code_slots * hash_size > len(cd)
            or not 44 <= identifier_offset < hash_offset - specials * hash_size or code_limit != start):
        raise artifacts.ReplayError('projection_invalid_signature')
    identifier = cd[identifier_offset:hash_offset - specials * hash_size].split(b'\0', 1)[0].decode('utf-8')
    if identifier != products.BUNDLE: raise artifacts.ReplayError('projection_control_identity_mismatch')
    if any(slot not in blobs for slot in (2, 5, 7)):
        raise artifacts.ReplayError('projection_invalid_signature')
    for slot, payload in ((1, products.read_small(products.fixed_path(app, 'Info.plist'))),
                          (2, blobs[2]), (3, products.read_small(products.fixed_path(app, '_CodeSignature/CodeResources'))),
                          (5, blobs[5]), (7, blobs[7])):
        if hashlib.sha256(payload).digest() != cd[hash_offset - slot * 32:hash_offset - (slot - 1) * 32]:
            raise artifacts.ReplayError('projection_signature_metadata_mismatch')
    entitlements = plistlib.loads(blobs[5][8:])
    if not isinstance(entitlements, dict): raise artifacts.ReplayError('projection_invalid_entitlements')
    return {'code_digest': code_digest, 'identifier': identifier, 'flags': flags, 'requirements': blobs[2],
            'entitlements': entitlements, 'entitlements_xml': blobs[5][8:], 'der_entitlements': blobs[7]}


def host_signature(app):
    try:
        return _host_signature(app)
    except artifacts.ReplayError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, IndexError, OverflowError, struct.error, ExpatError):
        raise artifacts.ReplayError('projection_invalid_signature') from None


def prepare_control(original_root, destination, mode, diagnostics, secrets, command=simulator_probe.diagnostic_command):
    """Create one independent original/re-sealed arm; never rewrite provenance."""
    if mode not in {'original', 'resealed-host'}: raise artifacts.ReplayError('projection_invalid_host_control')
    original_root, destination = Path(original_root), Path(destination)
    if mode == 'resealed-host':
        manifest = json.loads(products.read_small(products.fixed_path(original_root, 'manifest.json')))
        if not isinstance(manifest, dict) or manifest.get('source_commit') != CURRENT_SOURCE:
            raise artifacts.ReplayError('projection_reseal_requires_current_source')
    if (destination.exists() or destination.is_symlink() or original_root.resolve() in destination.resolve().parents
            or destination.resolve() in original_root.resolve().parents):
        raise artifacts.ReplayError('projection_control_copy_not_fresh')
    original = snapshot_tree(original_root)
    products.sanitize_plan(original_root / 'TEST_ROOT')
    shutil.copytree(original_root, destination, symlinks=True, copy_function=shutil.copyfile)
    for name, (kind, permissions, _) in original.items():
        path = destination / name
        if path.is_symlink(): raise artifacts.ReplayError('projection_unsafe_control_tree')
        path.chmod(permissions)
        if kind == 'file' and (path.stat().st_dev, path.stat().st_ino) == ((original_root / name).stat().st_dev, (original_root / name).stat().st_ino):
            raise artifacts.ReplayError('projection_control_hardlink')
    if snapshot_tree(destination) != original: raise artifacts.ReplayError('projection_control_copy_changed')
    report = {'mode': mode, 'source_products_unchanged': True, 'independent_private_copy': True,
              'signing_modified_for_diagnostic': mode == 'resealed-host'}
    if mode == 'original': return report
    app = destination / 'TEST_ROOT' / products.APP
    before = host_signature(app)
    def run(name, arguments):
        status, stdout = command(arguments, secrets, timeout=20)
        diagnostics[name] = {**status, 'stdout': simulator_probe.redact_probe_text(stdout, secrets)}
        return status
    initial = run('source_seal', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    if initial['status'] != 'commandFailed' or initial['exit_code'] != 1:
        raise artifacts.ReplayError('projection_source_seal_contrast_unavailable')
    with tempfile.TemporaryDirectory(prefix='ayu-projection-entitlements-') as private:
        entitlements = Path(private) / 'original.plist'
        with entitlements.open('xb') as stream:
            entitlements.chmod(0o600); stream.write(before['entitlements_xml'])
        signed = run('reseal_host', ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
            '--preserve-metadata=identifier,requirements,flags', '--entitlements', str(entitlements), str(app)])
    if signed['status'] != 'completed' or signed['exit_code'] != 0:
        raise artifacts.ReplayError('projection_host_reseal_failed')
    after = host_signature(app)
    for field in ('code_digest', 'identifier', 'flags', 'requirements', 'entitlements', 'der_entitlements'):
        if after[field] != before[field]: raise artifacts.ReplayError('projection_host_reseal_changed_identity_or_code')
    changed = snapshot_tree(destination)
    allowed = {'TEST_ROOT/' + products.APP + '/ProjectionTestHost',
               'TEST_ROOT/' + products.APP + '/_CodeSignature/CodeResources'}
    if (set(changed) != set(original) or any(changed[name][:2] != original[name][:2] for name in original)
            or {name for name in original if changed[name] != original[name]} != allowed):
        raise artifacts.ReplayError('projection_host_reseal_changed_products')
    verified = run('resealed_verify', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    if verified['status'] != 'completed' or verified['exit_code'] != 0:
        raise artifacts.ReplayError('projection_resealed_host_not_verified')
    if snapshot_tree(original_root) != original: raise artifacts.ReplayError('projection_original_products_changed')
    report.update(host_code_unchanged=True, test_tree_unchanged=True, entitlements_preserved=True,
                  requirements_preserved=True, identifier_and_flags_preserved=True, strict_seal_verified=True)
    return report


def child_environment():
    child = {k: v for k, v in os.environ.items() if k in EXPORT_ENVIRONMENT_KEYS}
    # Match pinned xctestrunner's xcodebuild_test_executor.py. Piped Xcode
    # startup messages must not remain buffered until after a failed replay.
    child['NSUnbufferedIO'] = 'YES'
    return child


@contextmanager
def clean_environment():
    original = dict(os.environ)
    try:
        child = child_environment(); os.environ.clear(); os.environ.update(child)
        yield
    finally:
        os.environ.clear(); os.environ.update(original)


def _closed_post_exit_diagnostic(value):
    """Accept only role counts; process identities and arbitrary text stay private."""
    if not isinstance(value, dict) or set(value) != {'state', 'group_verified', 'same_uid', 'counts'}:
        raise ValueError('Invalid post-exit diagnostic selection')
    if (value['state'] not in {'observed', 'unavailable', 'ownershipUnverified'} or
            type(value['group_verified']) is not bool or
            (value['same_uid'] is not None and type(value['same_uid']) is not bool) or
            not isinstance(value['counts'], dict) or set(value['counts']) != set(POST_EXIT_GROUP_ROLES) or
            any(type(count) is not int or not 0 <= count <= 128 for count in value['counts'].values()) or
            sum(value['counts'].values()) > 128):
        raise ValueError('Invalid post-exit diagnostic selection')
    if value['state'] == 'observed':
        if value['group_verified'] is not True or value['same_uid'] is not True:
            raise ValueError('Unverified post-exit diagnostic selection')
    elif any(value['counts'].values()):
        raise ValueError('Unverified post-exit diagnostic counts')
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(encoded) > POST_EXIT_DIAGNOSTIC_OUTPUT_LIMIT:
        raise ValueError('Post-exit diagnostic output limit')
    return json.loads(encoded)


def native_command(arguments, timeout=30, bootstrap_diagnostic=None, post_exit_diagnostic=None,
                   bootstrap_after_seconds=None):
    """Bound subprocess lifetime/output, including inherited pipes after exit."""
    if bootstrap_after_seconds is not None and (
            type(bootstrap_after_seconds) is not int or bootstrap_after_seconds != 480 or
            not callable(bootstrap_diagnostic)):
        raise ValueError('Invalid bootstrap observation threshold')
    threshold = BOOTSTRAP_SAMPLE_AFTER_SECONDS if bootstrap_after_seconds is None else BOOTSTRAP_SAMPLE_LATE_AFTER_SECONDS
    planned_after_ms = int(threshold * 1000)
    process = None; data = bytearray(); code = None; state = 'unavailable'
    started = time.monotonic(); deadline = started + timeout
    observer = None; observation = []; observation_decided = False
    callback_entry = []
    verified_initial_group = None
    post_exit_observation = {'state': 'notTriggered', 'reason': 'commandDidNotSucceed',
                             'received_before_retirement': False}
    not_observed = {'state': 'notTriggered', 'reason': 'commandEndedBeforeTrigger'}
    def begin_observation():
        nonlocal observer, not_observed
        try:
            group = os.getpgid(process.pid)
            if group != process.pid: raise ValueError('Unexpected owned process group')
        except (OSError, ValueError):
            not_observed = {'state': 'unavailable', 'reason': 'ownedProcessGroupUnverified'}
            return
        def alive():
            return process.poll() is None and time.monotonic() < deadline
        def observe():
            try:
                # The process may exit or emit its first expected test event
                # between the parent's decision and this worker being scheduled.
                # Recheck only already captured events, never invoke afterward.
                if cases(bytes(data))['started']:
                    observation.append({'state': 'notTriggered', 'reason': 'expectedCaseStarted'})
                    return
                if not alive():
                    observation.append({'state': 'notTriggered', 'reason': 'commandEndedBeforeTrigger'})
                    return
                entered = time.monotonic()
                elapsed = entered - started
                if (not math.isfinite(elapsed) or not threshold <= elapsed <= 600 or entered >= deadline):
                    observation.append({'state': 'unavailable', 'reason': 'diagnosticClockUnverified'})
                    return
                callback_entry.append({'state': 'triggered', 'planned_after_ms': planned_after_ms,
                                       'actual_after_ms': int(elapsed * 1000)})
                selected = bootstrap_diagnostic(process.pid, group, alive)
                if not isinstance(selected, dict): raise ValueError('Invalid diagnostic selection')
                selected = dict(selected)
                # Trigger timing comes from this runner, not diagnostic text.
                selected.pop('trigger', None)
                encoded = json.dumps(selected, ensure_ascii=True, allow_nan=False).encode('ascii')
                if len(encoded) > BOOTSTRAP_SAMPLE_OUTPUT_LIMIT:
                    selected = {'state': 'unavailable', 'reason': 'diagnosticOutputLimit'}
                else:
                    selected = json.loads(encoded)
            except Exception:
                # An optional diagnostic cannot alter a test outcome, expose an
                # exception's private arguments, or bypass ordinary cleanup.
                selected = {'state': 'unavailable', 'reason': 'diagnosticFailed'}
            observation.append(selected)
        try:
            observer = threading.Thread(target=observe, name='projection-bootstrap-observer', daemon=True)
            observer.start()
        except RuntimeError:
            observer = None
            not_observed = {'state': 'unavailable', 'reason': 'diagnosticWorkerUnavailable'}
    def consider_observation(now, code):
        nonlocal observation_decided, not_observed
        if (bootstrap_diagnostic is not None and not observation_decided and code is None
                and now - started >= threshold):
            observation_decided = True
            if cases(bytes(data))['started']:
                not_observed = {'state': 'notTriggered', 'reason': 'expectedCaseStarted'}
            else:
                begin_observation()
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=child_environment(), start_new_session=True)
        if post_exit_diagnostic is not None:
            # Record ownership before poll()/wait() can reap a short-lived
            # leader. Never infer ownership from a group number after exit.
            try:
                candidate_group = os.getpgid(process.pid)
                if candidate_group == process.pid: verified_initial_group = candidate_group
            except (OSError, ValueError):
                pass
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ); drain = None
            while selector.get_map():
                code = process.poll(); now = time.monotonic()
                if code is not None and drain is None: drain = min(deadline, now + POST_EXIT_DRAIN_SECONDS)
                left = (drain if drain is not None else deadline) - now
                if left <= 0:
                    if code is None: state = 'timedOut'
                    break
                consider_observation(now, code)
                for key, _ in selector.select(min(left, .05)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk: selector.unregister(key.fileobj); continue
                    room = MAX_OUTPUT - len(data); data.extend(chunk[:room])
                    if len(chunk) > room: state = 'outputLimit'; break
                if state == 'outputLimit': break
        if state not in {'timedOut', 'outputLimit'}:
            # Closing stdout is not proof that XCTest exited. Keep the optional
            # single observation available even when a live child closes pipes.
            while bootstrap_diagnostic is not None and process.poll() is None:
                now = time.monotonic()
                if now >= deadline: break
                consider_observation(now, None)
                try: process.wait(timeout=min(.05, deadline - now))
                except subprocess.TimeoutExpired: pass
            try:
                code = process.wait(timeout=max(0, deadline - time.monotonic()))
                state = 'completed' if code == 0 else 'commandFailed'
            except subprocess.TimeoutExpired:
                state, code = 'timedOut', process.poll()
    except OSError:
        state = 'unavailable'
    finally:
        if process is not None:
            if observer is not None:
                # Diagnostics run while pipes drain. Waiting for their bounded
                # completion must never extend the original XCTest deadline.
                observer.join(timeout=min(BOOTSTRAP_SAMPLE_JOIN_SECONDS, max(0, deadline - time.monotonic())))
            if post_exit_diagnostic is not None and state == 'completed' and code == 0:
                if verified_initial_group is None:
                    post_exit_observation = {'state': 'unavailable', 'reason': 'ownedProcessGroupUnverified',
                                             'received_before_retirement': False}
                elif time.monotonic() >= deadline:
                    post_exit_observation = {'state': 'unavailable', 'reason': 'commandDeadlineReached',
                                             'received_before_retirement': False}
                else:
                    selected = []
                    def observe_retired_leader():
                        try:
                            value = _closed_post_exit_diagnostic(post_exit_diagnostic(process.pid, verified_initial_group))
                        except Exception:
                            value = {'state': 'unavailable', 'reason': 'diagnosticPayloadInvalid'}
                        selected.append(value)
                    try:
                        retired_observer = threading.Thread(target=observe_retired_leader,
                            name='projection-post-exit-observer', daemon=True)
                        retired_observer.start()
                        retired_observer.join(timeout=min(POST_EXIT_DIAGNOSTIC_SECONDS, max(0, deadline - time.monotonic())))
                        if selected and not retired_observer.is_alive():
                            post_exit_observation = {**selected[0], 'received_before_retirement': True}
                        else:
                            post_exit_observation = {'state': 'unavailable', 'reason': 'diagnosticDidNotFinishBeforeDeadline',
                                                     'received_before_retirement': False}
                    except RuntimeError:
                        post_exit_observation = {'state': 'unavailable', 'reason': 'diagnosticWorkerUnavailable',
                                                 'received_before_retirement': False}
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
    status = {'status': state, 'exit_code': code, 'timeout': state == 'timedOut',
              'output_limit': state == 'outputLimit', 'captured_bytes': len(data)}
    if bootstrap_diagnostic is not None:
        selected = (observation[0] if observation else
            {'state': 'unavailable', 'reason': 'diagnosticDidNotFinishBeforeDeadline'} if observer is not None else not_observed)
        trigger = (dict(callback_entry[0]) if callback_entry else {
            'state': 'notTriggered' if selected.get('state') == 'notTriggered' else 'unavailable',
            'planned_after_ms': planned_after_ms, 'actual_after_ms': None})
        status['bootstrap_diagnostic'] = {**selected, 'trigger': trigger}
    if post_exit_diagnostic is not None:
        status['post_exit_diagnostic'] = post_exit_observation
    return status, bytes(data)


def cases(data):
    started, outcomes, unknown, duplicates, invalid_order = set(), {}, False, False, False
    # Xcode may prefix events or merge separate writes onto one line. Match
    # each event without consuming its suffix, so no later failure can hide
    # behind an earlier start/pass. Unknown XCTest identities still reject.
    pattern = re.compile(r"Test Case '-\[(?:[A-Za-z0-9_]+\.)*([A-Za-z0-9_]+) ([A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=$|\W|Test Case '-\[)")
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
    return {'expected': len(EXPECTED), 'started': len(started), 'passed': sum(v == 'passed' for v in outcomes.values()),
            'failed': sum(v == 'failed' for v in outcomes.values()), 'crashed': sum(v == 'crashed' for v in outcomes.values()), 'skipped': sum(v == 'skipped' for v in outcomes.values()),
            'missing': missing, 'outcome_without_start': unstarted, 'duplicate_case_events': duplicates,
            'unexpected_case_events': unknown, 'invalid_case_event_order': invalid_order, 'cases': dict(sorted(outcomes.items())),
            'all_expected_passed': not (missing or unstarted or duplicates or unknown or invalid_order) and len(outcomes) == len(EXPECTED) and all(v == 'passed' for v in outcomes.values())}


def host_log_predicate(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identifier):
        raise ValueError('Invalid owned simulator identifier')
    processes = ' OR '.join('process == "' + name + '"' for name in sorted(HOST_LOG_PROCESSES))
    identities = ' OR '.join('eventMessage CONTAINS[c] "' + name + '"' for name in (*HOST_LOG_IDENTITIES, identifier))
    return '(' + processes + ') AND (' + identities + ')'


def host_log_events(data, secrets, identifier):
    """Select only this synthetic replay's host events, never raw records."""
    host_log_predicate(identifier)
    try:
        if len(data) > simulator_probe.COMMAND_OUTPUT_LIMIT: raise ValueError('Log input limit')
        records = json.loads(data)
        if not isinstance(records, list): raise ValueError('Log payload must be an array')
        events, omitted, retained_bytes = [], max(0, len(records) - 128), 2
        own_id = re.compile(r'(?<![A-Za-z0-9_-])' + re.escape(identifier) + r'(?![A-Za-z0-9_-])', re.I)
        for record in records[:128]:
            if not isinstance(record, dict): continue
            image = record.get('processImagePath')
            process = Path(image).name if isinstance(image, str) else record.get('process')
            message = record.get('eventMessage')
            if (process not in HOST_LOG_PROCESSES or not isinstance(message, str) or
                    not (any(identity.casefold() in message.casefold() for identity in HOST_LOG_IDENTITIES)
                         or own_id.search(message))):
                continue
            if len(events) == 32:
                omitted += 1
                continue
            event = {'process': process, 'message': simulator_probe.redact_probe_text(message.encode('utf-8'), secrets)}
            if record.get('messageType') in {'Default', 'Info', 'Debug', 'Error', 'Fault'}:
                event['message_type'] = record['messageType']
            stamp = record.get('timestamp')
            if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:[+-]\d{4})?', stamp):
                event['timestamp'] = stamp
            count = len(json.dumps(event, ensure_ascii=True)) + 2
            # Reserve room for the closed selection status/count fields too.
            if retained_bytes + count > 44 * 1024 - 512:
                omitted += 1
                continue
            events.append(event); retained_bytes += count
        return {'state': 'parsed', 'events': events, 'records_seen': min(len(records), 128), 'records_omitted': omitted}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {'state': 'invalidPayload', 'events': []}


def host_bootstrap_diagnostic(identifier, secrets):
    # The caller has created and booted this exact fresh device. Host logging
    # remains available when simctl inventory or its service connection hangs.
    predicate = host_log_predicate(identifier)
    status, data = simulator_probe.diagnostic_command(['/usr/bin/log', 'show', '--style', 'json',
        '--last', '15m', '--info', '--debug', '--predicate', predicate], secrets)
    selected = (host_log_events(data, secrets, identifier) if status['status'] == 'completed'
                else {'state': 'unavailable', 'reason': status['status'], 'events': []})
    return {'command': status, 'selection': selected}


def execute(products_root, output, root, command=native_command, probe=simulator_probe.capture, attest_control=False,
            bootstrap_sample=False):
    output, root = Path(output), Path(root); commands = {}; identifier = None
    report = {'commands': commands, 'passed': False, 'tests_started': False,
              'device_type': DEVICE_TYPE, 'runtime': RUNTIME,
              'xctest_command_attempted': False, 'assertion_retries': 0, 'diagnostics': {}}
    secrets = tuple(v for k, v in os.environ.items() if v and re.search(r'TOKEN|SECRET|PASSWORD|API_ID|API_HASH|AUTH|PRIVATE_KEY|SESSION', k, re.I))
    def call(name, arguments, timeout=30, required=True):
        options = {'timeout': timeout}
        if name == 'xctest' and bootstrap_sample:
            options['bootstrap_diagnostic'] = lambda pid, group, alive: bootstrap_sampling.capture(pid, group, identifier, secrets, alive)
        status, stdout = command(arguments, **options)
        status = dict(status)
        if name == 'xctest' and bootstrap_sample:
            report['diagnostics']['bootstrap_sample'] = status.pop('bootstrap_diagnostic', {'state': 'unavailable'})
        commands[name] = status
        if required and status['status'] != 'completed': raise artifacts.ReplayError('projection_' + name + '_failed')
        return status, stdout
    def retain_host_log():
        if identifier is None or not report['xctest_command_attempted'] or 'host_bootstrap_log' in report['diagnostics']:
            return
        try:
            report['diagnostics']['host_bootstrap_log'] = host_bootstrap_diagnostic(identifier, secrets)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            report['diagnostics']['host_bootstrap_log'] = {'state': 'unavailable'}
    try:
        _, version = call('xcode', ['xcodebuild', '-version'])
        _, sdk = call('sdk', ['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'])
        if version.decode().splitlines()[0] != 'Xcode 27.1' or sdk.strip() != b'27.1':
            raise artifacts.ReplayError('projection_toolchain_mismatch')
        status, raw = call('first_launch', ['xcodebuild', '-checkFirstLaunchStatus'], timeout=30, required=False)
        report['diagnostics']['first_launch'] = {'output': simulator_probe.redact_probe_text(raw, secrets)}
        if status['status'] != 'completed':
            raise artifacts.ReplayError('projection_first_launch_not_ready')
        _, raw = call('runtimes', ['xcrun', 'simctl', 'list', 'runtimes', '--json'])
        runtimes = json.loads(raw)['runtimes']
        if len([r for r in runtimes if r.get('identifier') == RUNTIME and r.get('version') == '27.0' and r.get('isAvailable') is True]) != 1:
            raise artifacts.ReplayError('projection_runtime_unavailable')
        _, raw = call('device_types', ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro'])
        types = json.loads(raw)['devicetypes']
        matching_types = [item for item in types if isinstance(item, dict) and item.get('identifier') == DEVICE_TYPE] if isinstance(types, list) else []
        if len(matching_types) != 1 or matching_types[0].get('name') != 'iPhone 15 Pro':
            raise artifacts.ReplayError('projection_device_type_unavailable')
        _, raw = call('devices', ['xcrun', 'simctl', 'list', 'devices', DEVICE_NAME, '--json'])
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
            _, raw = call('boot_state', ['xcrun', 'simctl', 'list', 'devices', DEVICE_NAME, '--json'], timeout=15)
            matching = [(runtime, device) for runtime, devices in json.loads(raw)['devices'].items() for device in devices
                        if isinstance(device, dict) and isinstance(device.get('udid'), str)
                        and device['udid'].casefold() == identifier.casefold()]
            if (len(matching) != 1 or matching[0][0] != RUNTIME or matching[0][1].get('name') != DEVICE_NAME
                    or matching[0][1].get('state') != 'Booted' or matching[0][1].get('deviceTypeIdentifier') != DEVICE_TYPE):
                raise artifacts.ReplayError('projection_boot_state_unverified')
            report['already_booted_state_verified'] = True
        with tempfile.TemporaryDirectory(prefix='ayu-projection-results-') as private:
            result = Path(private) / 'test.xcresult'
            report['xctest_command_attempted'] = True
            started_at = time.time()
            status, data = call('xctest', ['xcodebuild', 'test-without-building', '-xctestrun', str(Path(products_root) / 'TEST_ROOT/test.xctestrun'),
                '-destination', 'id=' + identifier, '-destination-timeout', '30', '-derivedDataPath', str(Path(private) / 'derived'),
                '-resultBundlePath', str(result), '-parallel-testing-enabled', 'NO', '-collect-test-diagnostics=never'], timeout=300, required=False)
            outcome = cases(data); report['tests_started'] = outcome['started'] > 0
            report['case_counts'] = {k: v for k, v in outcome.items() if k != 'cases'}
            write(output / 'case-results.json', outcome)
            text = simulator_probe.redact_probe_text(data, secrets)
            write(output / 'test.log', text, text=True)
            report['passed'] = status['status'] == 'completed' and outcome['all_expected_passed']
        # Snapshot before the optional standalone host launch can add unrelated
        # later lifecycle events. Failure paths collect once in finally too.
        retain_host_log()
        if attest_control:
            installed = report['installed_control_attestation'] = {'state': 'unavailable'}
            status, raw = call('installed_control', ['xcrun', 'simctl', 'get_app_container', identifier, products.BUNDLE, 'app'],
                               timeout=10, required=False)
            if status['status'] == 'completed':
                # Successful lookup is observed evidence. Invalid/missing code
                # or metadata cannot be relabeled as an unavailable command.
                installed.update(state='mismatch', file_bytes_unchanged=False, synthetic_identity_verified=False)
                try:
                    if not 0 < len(raw) <= 4096 or b'\0' in raw: raise ValueError('Invalid container response')
                    path = Path(raw.decode().strip())
                    if not path.is_absolute() or path.suffix != '.app' or path.is_symlink() or not path.is_dir():
                        raise ValueError('Invalid container response')
                    info = plistlib.loads(products.read_small(products.fixed_path(path, 'Info.plist')))
                    if not isinstance(info, dict) or info.get('CFBundleIdentifier') != products.BUNDLE or info.get('CFBundleExecutable') != 'ProjectionTestHost':
                        raise ValueError('Unexpected installed identity')
                    installed['synthetic_identity_verified'] = True
                    actual = snapshot_tree(path)
                    expected = snapshot_tree(Path(products_root) / 'TEST_ROOT' / products.APP)
                    # Installation may normalize permissions. Attest actual
                    # member identity and bytes, never data-container files.
                    unchanged = {name: (value[0], value[2]) for name, value in actual.items()} == {name: (value[0], value[2]) for name, value in expected.items()}
                    installed.update(state='matched' if unchanged else 'mismatch', file_bytes_unchanged=unchanged)
                except (OSError, ValueError, TypeError, KeyError, RuntimeError, IndexError, OverflowError, ExpatError):
                    pass  # Fixed mismatch fields only; never emit metadata/paths.
                if installed['state'] != 'matched':
                    report['passed'] = False; report['failure'] = 'projection_installed_control_mismatch'
        # Reuse only the synthetic-host probe, with credentials absent from every
        # nested native command; raw XCResult and simulator files stay private.
        with tempfile.TemporaryDirectory(prefix='ayu-projection-probe-') as private:
            with clean_environment():
                probe(Path(private), secrets, started_at, owned_identifier=identifier, profile=DEVICE_PROFILE)
            diagnostic = Path(private) / 'build/fork-evidence/projection-launch-probe.json'
            if diagnostic.is_file() and not diagnostic.is_symlink() and diagnostic.stat().st_size <= 256 * 1024:
                write(output / 'projection-launch-probe.json', json.loads(products.read_small(diagnostic, 256 * 1024)))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, IndexError, RuntimeError) as error:
        report['passed'] = False
        report['failure'] = str(error) if isinstance(error, artifacts.ReplayError) else 'projection_diagnostic_unavailable'
    finally:
        if identifier is not None:
            retain_host_log()
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


def replay(run, source, output, root=None, api=artifacts.gh_json, fetch=artifacts.download, command=native_command, probe=simulator_probe.capture,
           host_control='original', control_command=simulator_probe.diagnostic_command, bootstrap_sample=False):
    specification = pinned(run, source)
    source = specification['source']
    if host_control not in {'original', 'resealed-host'} or (host_control != 'original' and source != CURRENT_SOURCE):
        raise artifacts.ReplayError('projection_invalid_host_control')
    if type(bootstrap_sample) is not bool or (bootstrap_sample and source != CURRENT_SOURCE):
        raise artifacts.ReplayError('projection_invalid_bootstrap_sample')
    root = Path(root or Path(__file__).resolve().parents[2]).resolve(strict=True)
    output = Path(output); output = output if output.is_absolute() else root / output
    if output != root / OUTPUT or any(x.is_symlink() for x in (output, *output.parents)) or output.exists():
        raise artifacts.ReplayError('projection_output_not_fresh_fixed_path')
    if source == CURRENT_SOURCE: verify_current_checkout(root)
    artifact = origin(run, source, api)
    output.mkdir(mode=0o700, parents=True)
    report = {'schema_version': 1, 'repository': artifacts.REPOSITORY, 'source_run': specification['run'], 'source_commit': source,
              'artifact_id': specification['artifact'], 'artifact_sha256': specification['digest'], 'diagnostic_replay_only': True,
              'application_recompiled_for_replay': False, 'new_checkout_qualified': False,
              'full_application_built': False, 'physical_device_verified': False, 'release_qualified': False,
              'host_signing_control': host_control, 'bootstrap_sample_enabled': bootstrap_sample, 'passed': False}
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-projection-replay-') as private:
            archive = Path(private) / 'artifact.zip'; fetch(specification['artifact'], archive)
            original = Path(private) / 'products'
            verified = extract(archive, artifact, original, source)
            report.update(verified)
            if source == CURRENT_SOURCE:
                original_snapshot = snapshot_tree(original)
                secrets = tuple(v for k, v in os.environ.items() if v and re.search(r'TOKEN|SECRET|PASSWORD|API_ID|API_HASH|AUTH|PRIVATE_KEY|SESSION', k, re.I))
                diagnostics = report['host_control_diagnostics'] = {}
                try:
                    controlled = Path(private) / 'control'
                    report['host_control_attestation'] = prepare_control(original, controlled, host_control, diagnostics, secrets, control_command)
                    control_snapshot = snapshot_tree(controlled)
                    report['expected_case_source_verified'] = True
                    report.update(execute(controlled, output, root, command, probe, attest_control=True,
                                          bootstrap_sample=bootstrap_sample))
                    control_unchanged = snapshot_tree(controlled) == control_snapshot
                    report['control_products_unchanged_after_execution'] = control_unchanged
                    if not control_unchanged:
                        report['passed'] = False
                        report['failure'] = 'projection_control_changed_during_execution'
                finally:
                    unchanged = snapshot_tree(original) == original_snapshot
                    report['source_products_unchanged'] = unchanged
                    if not unchanged:
                        report['passed'] = False
                        report['failure'] = 'projection_original_products_changed'
            else:
                report.update(execute(original, output, root, command, probe))
    except artifacts.ReplayError as error:
        report['passed'] = False; report['failure'] = str(error)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, IndexError, struct.error, zipfile.BadZipFile, ExpatError):
        report['passed'] = False
        report['failure'] = 'projection_artifact_verification_failed'
    write(output / 'result.json', report)
    hashes = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    write(output / 'SHA256SUMS', ''.join(value + '  ' + name + '\n' for name, value in sorted(hashes.items())), text=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True); parser.add_argument('--source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host-control', choices=['original', 'resealed-host'], default='original')
    parser.add_argument('--bootstrap-sample', action='store_true')
    args = parser.parse_args(argv)
    try: report = replay(args.run, args.source, args.output, host_control=args.host_control, bootstrap_sample=args.bootstrap_sample)
    except artifacts.ReplayError as error:
        print(json.dumps({'passed': False, 'failure': str(error)})); return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        print(json.dumps({'passed': False, 'failure': 'projection_replay_preparation_failed'})); return 1
    print(json.dumps({'passed': report['passed'], 'diagnostic_replay_only': True, 'source_commit': report['source_commit'],
                      'host_signing_control': report['host_signing_control']}, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
