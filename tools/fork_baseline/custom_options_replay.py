#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created for AyuGram iOS on 2026-11-06.
"""Replay one retained synthetic settings host; never compile Telegram or its UI.

Historical XCTest evidence is kept separate from this recorded root-screen
observation. No account, Postbox, network client or physical device is exercised.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import signal
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
from xml.parsers.expat import ExpatError

import replay_artifact as artifacts
from custom_options_smoke import BUNDLE, DEVICE_TYPE, RUNTIME
from installer_control import read_json, write_json
from ipa_inspect import inspect_ipa
from projection_replay import child_environment, native_command

SOURCE = 'c5eafa23fa4aff7e05d588a9b2cd49cd539dcd1c'
CURRENT_SOURCE = 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'
RUN = 42147021107
ARTIFACT = 13471537351
OUTER_SHA256 = '3d69f82fd9bce9ce6f4e206cb689075a44cbeb0b1c239518cc3d0b87241fdb0b'
IPA_SHA256 = '1c295da5e2acbdd6d8eb974ab862547f7f5300633d5d95ddf87be862dde5ce0d'
OUTPUT = 'build/fork-evidence/custom-options-replay'
COMPILE_STEP = 'Build exact renderer host, run native controls and record real navigation'
RENDERER_SOURCES = {
    'submodules/SettingsUI/Sources/CustomeOptionsCatalog.swift': '109f346466e10b7f50cf519d78dfdca115093e3ba66c4c969a951c1fad12909d',
    'submodules/SettingsUI/Sources/CustomeOptionsController.swift': '337a95cc5a9fbd57128853f1b335f6024c4ed63f1a889f0e23dc7f4db502d0fa',
    'submodules/SettingsUI/Sources/LocalClientCosmeticsController.swift': '4f25c21ab8931ceb40ed714e30ebc47383aae16c884086b895e56157a366eb32'}
NATIVE_SOURCES = {**RENDERER_SOURCES,
    'Tests/CustomeOptionsHost/CustomeOptionsHost.swift': '66a287e6f795ec7384bd70d2f32b4cd0a7484ec74d2f2109766e91b80697462f',
    'Tests/CustomeOptionsHost/CustomeOptionsFixture.swift': 'c9f53f5fa108ccc5ff7d864567d90793e79a4dbcf72f33b6b5b64e7ec6c775a3'}
IPA_MEMBER = 'custom-options-ui/CustomeOptionsHost-simulator.ipa'
MAX_ARTIFACT = 128 * 1024**2
MAX_CONTENT = 192 * 1024**2
ROLE = 'retainedSyntheticSettingsRoot'
PROOF_FIELDS = {'schema', 'diagnostic_role', 'source_commit', 'source_run', 'artifact_id', 'outer_sha256',
                'ipa_sha256', 'current_application_source_commit', 'replay_checkout_commit', 'allowed_tooling_changes',
                'native_source_hashes', 'application_source_unchanged', 'source_job_id', 'inner_references_verified',
                'historical_controller_cases_passed', 'historical_interaction_cases_passed',
                'new_xctest_cases_executed', 'logged_in_settings_verified', 'physical_device_verified', 'release_qualified'}

# Host-only Apple Vision helper: publish booleans, never recognized strings.
# Its three synthetic controls test the same OCR/position path before install.
VISION_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import CoreGraphics
import CoreText
import ImageIO
import Vision
enum CheckError: Error { case invalid }
func titleVisible(_ image: CGImage) throws -> Bool {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).contains { observation in
        guard observation.boundingBox.midY > 0.80,
              let text = observation.topCandidates(1).first?.string else { return false }
        return text.lowercased().filter { $0.isLetter || $0.isNumber } == "customeoptions"
    }
}
func synthetic(_ text: String) throws -> CGImage {
    guard let context = CGContext(data: nil, width: 800, height: 600, bitsPerComponent: 8,
        bytesPerRow: 3200, space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { throw CheckError.invalid }
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: 800, height: 600))
    let attributes: [CFString: Any] = [kCTFontAttributeName: CTFontCreateWithName("Helvetica" as CFString, 36, nil),
                                     kCTForegroundColorAttributeName: CGColor(gray: 0, alpha: 1)]
    guard let string = CFAttributedStringCreate(nil, text as CFString, attributes as CFDictionary) else { throw CheckError.invalid }
    context.textPosition = CGPoint(x: 160, y: 520)
    CTLineDraw(CTLineCreateWithAttributedString(string), context)
    guard let image = context.makeImage() else { throw CheckError.invalid }
    return image
}
do {
    var result: [String: Any]
    if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--self-test" {
        let correct = try titleVisible(synthetic("Custome options"))
        let wrong = try titleVisible(synthetic("Another screen"))
        let blank = try titleVisible(synthetic(""))
        guard correct && !wrong && !blank else { throw CheckError.invalid }
        result = ["self_test_passed": true, "cases": 3]
    } else {
        guard CommandLine.arguments.count == 2 else { throw CheckError.invalid }
        let url = URL(fileURLWithPath: CommandLine.arguments[1])
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let bytes = values.fileSize, bytes > 0, bytes <= 16 * 1024 * 1024,
              let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(source) == 1,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, width <= 2048, height > 0, height <= 4096,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw CheckError.invalid }
        result = ["root_title_visible": try titleVisible(image), "width": width, "height": height]
    }
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    guard let text = String(data: data, encoding: .utf8) else { throw CheckError.invalid }
    print(text)
} catch { print("{\"verified\":false}"); exit(1) }
'''


class ReplayError(ValueError):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return artifacts.digest_stream(stream)


def location(output, root):
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    output = Path(output)
    if not output.is_absolute(): output = root / output
    if output != root / OUTPUT or any(p.is_symlink() for p in (output, *output.parents)):
        raise ReplayError('invalid_settings_replay_output')
    return root, output


def checkout(root):
    head, count = artifacts.verify_checkout(root, CURRENT_SOURCE)
    for name, expected in NATIVE_SOURCES.items():
        path = root / name
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or digest(path) != expected:
            raise ReplayError('settings_native_source_changed')
    return head, count


def select(api):
    run = api(artifacts.BASE + '/actions/runs/' + str(RUN))
    if (not isinstance(run, dict) or run.get('id') != RUN or run.get('head_sha') != SOURCE or
            not isinstance(run.get('path'), str) or run['path'].split('@', 1)[0] != artifacts.WORKFLOW or
            run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        raise ReplayError('settings_origin_mismatch')
    repo, head_repo = run.get('repository'), run.get('head_repository')
    if (not isinstance(repo, dict) or not isinstance(head_repo, dict) or repo.get('full_name') != artifacts.REPOSITORY or
            head_repo.get('full_name') != artifacts.REPOSITORY or type(repo.get('id')) is not int or repo['id'] != head_repo.get('id')):
        raise ReplayError('settings_repository_mismatch')
    jobs = api(artifacts.BASE + '/actions/runs/' + str(RUN) + '/jobs?filter=latest&per_page=100')
    if not isinstance(jobs, dict) or not isinstance(jobs.get('jobs'), list) or type(jobs.get('total_count')) is not int or not 0 <= jobs['total_count'] <= 100:
        raise ReplayError('settings_job_mismatch')
    found = [j for j in jobs['jobs'] if isinstance(j, dict) and j.get('name') == 'custom-options-ui']
    if len(found) != 1: raise ReplayError('settings_job_mismatch')
    job = found[0]; steps = job.get('steps')
    compiled = [s for s in steps if isinstance(s, dict) and s.get('name') == COMPILE_STEP] if isinstance(steps, list) else []
    if (job.get('run_id') != RUN or type(job.get('id')) is not int or job['id'] <= 0 or job.get('status') != 'completed' or
            job.get('conclusion') != 'success' or len(compiled) != 1 or compiled[0].get('status') != 'completed' or compiled[0].get('conclusion') != 'success'):
        raise ReplayError('settings_compilation_not_verified')
    response = api(artifacts.BASE + '/actions/runs/' + str(RUN) + '/artifacts?per_page=100')
    if not isinstance(response, dict) or not isinstance(response.get('artifacts'), list) or type(response.get('total_count')) is not int or not 0 <= response['total_count'] <= 100:
        raise ReplayError('settings_artifact_mismatch')
    found = [a for a in response['artifacts'] if isinstance(a, dict) and a.get('name') == 'custom-options-ui-' + SOURCE]
    if len(found) != 1: raise ReplayError('settings_artifact_mismatch')
    item = found[0]; origin = item.get('workflow_run')
    if (item.get('id') != ARTIFACT or item.get('digest') != 'sha256:' + OUTER_SHA256 or item.get('expired') is not False or
            type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= MAX_ARTIFACT or
            not isinstance(origin, dict) or origin.get('id') != RUN or origin.get('head_sha') != SOURCE or
            origin.get('repository_id') != repo['id'] or origin.get('head_repository_id') != repo['id']):
        raise ReplayError('settings_artifact_mismatch')
    return item, job['id']


def verify_archive(path, artifact, output):
    if Path(path).stat().st_size != artifact['size_in_bytes'] or digest(path) != OUTER_SHA256:
        raise ReplayError('settings_outer_digest_mismatch')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist(); names = {i.filename for i in entries}; total = 0
        if len(entries) > 100 or len(entries) != len(names): raise ReplayError('settings_archive_entries_invalid')
        for entry in entries:
            artifacts.member_name(entry.filename, directory=entry.is_dir())
            total += entry.file_size
            if (entry.flag_bits & 1 or entry.compress_type not in {0, 8} or
                    stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR} or
                    entry.file_size > MAX_ARTIFACT or total > MAX_CONTENT):
                raise ReplayError('settings_archive_entries_invalid')
        references, verified = 0, set()
        for name in ('SHA256SUMS', 'custom-options-ui/SHA256SUMS'):
            if name not in names or archive.getinfo(name).file_size > 65536: raise ReplayError('settings_checksums_missing')
            for line in archive.read(name).decode('utf-8').splitlines():
                parts = line.split('  ', 1)
                if len(parts) != 2 or not artifacts.DIGEST.fullmatch(parts[0]): raise ReplayError('settings_checksum_invalid')
                relative = artifacts.member_name(parts[1]); target = (PurePosixPath(name).parent / relative).as_posix()
                if target not in names: raise ReplayError('settings_checksum_missing')
                with archive.open(target) as stream:
                    if artifacts.digest_stream(stream) != parts[0]: raise ReplayError('settings_inner_digest_mismatch')
                references += 1; verified.add(target)
        if not {IPA_MEMBER, 'custom-options-ui/result.json'}.issubset(verified): raise ReplayError('settings_evidence_unverified')
        if archive.getinfo('custom-options-ui/result.json').file_size > 65536: raise ReplayError('settings_result_invalid')
        result = json.loads(archive.read('custom-options-ui/result.json'))
        tests = [{'target': '//Tests/CustomeOptionsHost:CustomeOptionsControllerTests', 'executed': 8, 'passed': 8, 'failed': 0, 'skipped': 0, 'owned_simulator_verified': True},
                 {'target': '//Tests/CustomeOptionsHost:CustomeOptionsInteractionTests', 'executed': 1, 'passed': 1, 'failed': 0, 'skipped': 0, 'owned_simulator_verified': True}]
        if (not isinstance(result, dict) or result.get('profile') != 'synthetic_production_renderer' or result.get('source_commit') != SOURCE or
                result.get('passed') is not True or result.get('phase') != 'complete' or result.get('tests') != tests or
                any(result.get(k) is not False for k in ('logged_in_settings_verified', 'physical_device_verified', 'full_application_built', 'authentication_codes_used', 'account_or_network_client_created')) or
                result.get('device_model') != 'iPhone 15 Pro' or result.get('simulator_runtime') != '27.0' or
                result.get('renderer_sources') != RENDERER_SOURCES or result.get('simulator_host_sha256') != IPA_SHA256):
            raise ReplayError('settings_result_invalid')
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        ipa = output / 'CustomeOptionsHost-simulator.ipa'
        with archive.open(IPA_MEMBER) as source, ipa.open('xb') as target:
            for chunk in iter(lambda: source.read(1024**2), b''): target.write(chunk)
        ipa.chmod(0o600)
        if digest(ipa) != IPA_SHA256: raise ReplayError('settings_host_digest_mismatch')
        inspect_ipa(ipa, 'simulator', BUNDLE)
        return references


def validate_proof(proof, head, count):
    expected = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': SOURCE, 'source_run': RUN, 'artifact_id': ARTIFACT,
        'outer_sha256': OUTER_SHA256, 'ipa_sha256': IPA_SHA256, 'current_application_source_commit': CURRENT_SOURCE,
        'replay_checkout_commit': head, 'allowed_tooling_changes': count, 'native_source_hashes': NATIVE_SOURCES,
        'application_source_unchanged': True, 'historical_controller_cases_passed': 8, 'historical_interaction_cases_passed': 1,
        'new_xctest_cases_executed': 0, 'logged_in_settings_verified': False, 'physical_device_verified': False, 'release_qualified': False}
    if (not isinstance(proof, dict) or set(proof) != PROOF_FIELDS or
            any(proof.get(k) != v or type(proof[k]) is not type(v) for k, v in expected.items()) or
            type(proof.get('source_job_id')) is not int or proof['source_job_id'] <= 0 or
            type(proof.get('inner_references_verified')) is not int or proof['inner_references_verified'] <= 0):
        raise ReplayError('settings_prepared_proof_mismatch')
    return proof


def prepare(output=OUTPUT, root=None, api=artifacts.gh_json, fetch=artifacts.download):
    root, output = location(output, root); head, count = checkout(root)
    if output.exists(): raise ReplayError('settings_output_already_exists')
    artifact, job = select(api)
    with tempfile.TemporaryDirectory(prefix='ayu-options-download-') as directory:
        path = Path(directory) / 'artifact.zip'; fetch(ARTIFACT, path)
        references = verify_archive(path, artifact, output)
    proof = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': SOURCE, 'source_run': RUN, 'artifact_id': ARTIFACT,
        'outer_sha256': OUTER_SHA256, 'ipa_sha256': IPA_SHA256, 'current_application_source_commit': CURRENT_SOURCE,
        'replay_checkout_commit': head, 'allowed_tooling_changes': count, 'native_source_hashes': NATIVE_SOURCES,
        'application_source_unchanged': True, 'source_job_id': job, 'inner_references_verified': references,
        'historical_controller_cases_passed': 8, 'historical_interaction_cases_passed': 1, 'new_xctest_cases_executed': 0,
        'logged_in_settings_verified': False, 'physical_device_verified': False, 'release_qualified': False}
    validate_proof(proof, head, count); write_json(output / 'verification.json', proof)
    return proof


def checked_metadata(app):
    path = Path(app) / 'Info.plist'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536: raise ReplayError('settings_metadata_invalid')
    try: info = plistlib.loads(path.read_bytes())
    except (ValueError, TypeError, ExpatError, OverflowError): raise ReplayError('settings_metadata_invalid') from None
    expected = {'CFBundleIdentifier': BUNDLE, 'CFBundleExecutable': 'CustomeOptionsHost', 'CFBundleVersion': '1',
                'CFBundleShortVersionString': '1.0', 'CFBundleSupportedPlatforms': ['iPhoneSimulator'],
                'MinimumOSVersion': '15.0', 'UIDeviceFamily': [1], 'CFBundleDisplayName': 'AyuGram Renderer Test',
                'DTXcode': '2710', 'DTXcodeBuild': '27A9275', 'DTPlatformVersion': '27.1'}
    if not isinstance(info, dict) or any(info.get(k) != v for k, v in expected.items()): raise ReplayError('settings_metadata_invalid')
    return info


def extract_host(ipa, destination):
    """The retained host has one executable and exactly these sealed resources."""
    destination = Path(destination)
    prefix = 'Payload/CustomeOptionsHost.app/'
    allowed = {prefix, prefix + 'CustomeOptionsHost', prefix + 'Info.plist', prefix + 'PkgInfo',
               prefix + '_CodeSignature/CodeResources'}
    with zipfile.ZipFile(ipa) as archive:
        entries = archive.infolist()
        if len(entries) != len(allowed) or {e.filename for e in entries} != allowed:
            raise ReplayError('settings_host_members_invalid')
        destination.mkdir(mode=0o700, exist_ok=False)
        for entry in entries:
            mode = entry.external_attr >> 16
            if (entry.flag_bits & 1 or entry.compress_type not in {0, 8} or entry.file_size > 20 * 1024**2 or
                    stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ReplayError('settings_host_members_invalid')
            if entry.filename == prefix:
                if not entry.is_dir() or entry.file_size: raise ReplayError('settings_host_members_invalid')
                continue
            relative = entry.filename[len(prefix):]
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open('xb') as output:
                for chunk in iter(lambda: source.read(1024**2), b''): output.write(chunk)
            target.chmod(mode & 0o777 or 0o600)
    return destination


def host_snapshot(app):
    """Compare the complete bounded code/resource inventory, including folders."""
    app = Path(app)
    if app.is_symlink() or not app.is_dir(): raise ReplayError('settings_host_inventory_invalid')
    result, total = {}, 0
    for index, path in enumerate(app.rglob('*')):
        if index >= 32: raise ReplayError('settings_host_inventory_invalid')
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode): value = {'kind': 'directory'}
        elif stat.S_ISREG(mode):
            size = path.stat().st_size; total += size
            if size > 20 * 1024**2 or total > 20 * 1024**2: raise ReplayError('settings_host_inventory_invalid')
            value = {'kind': 'file', 'sha256': digest(path)}
        else: raise ReplayError('settings_host_inventory_invalid')
        result[path.relative_to(app).as_posix()] = value
    return result


def execute(output=OUTPUT, root=None, command=native_command, pause=time.sleep, popen=subprocess.Popen):
    root, output = location(output, root); head, count = checkout(root)
    validate_proof(read_json(output / 'verification.json'), head, count)
    if platform.system() != 'Darwin': raise ReplayError('macos_required')
    ipa = output / 'CustomeOptionsHost-simulator.ipa'
    if ipa.is_symlink() or not ipa.is_file() or digest(ipa) != IPA_SHA256: raise ReplayError('settings_host_digest_mismatch')
    if any((output / name).exists() or (output / name).is_symlink() for name in ('result.json', 'root-light.png', 'root-light.mp4')):
        raise ReplayError('settings_replay_already_exists')
    inspect_ipa(ipa, 'simulator', BUNDLE)
    report = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': SOURCE, 'current_application_source_commit': CURRENT_SOURCE,
              'device_model': 'iPhone 15 Pro', 'runtime': '27.0', 'passed': False, 'phase': 'preflight',
              'new_xctest_cases_executed': 0, 'historical_controller_cases_passed': 8, 'historical_interaction_cases_passed': 1,
              'application_recompiled': False, 'renderer_recompiled': False, 'logged_in_settings_verified': False,
              'physical_device_verified': False, 'release_qualified': False, 'visual_review_required': True,
              'commands': [], 'cleanup_passed': False, 'source_codesign_verified': False, 'installed_host_verified': False}
    identifier, recorder = None, None
    def call(label, args, timeout=30):
        report['phase'] = label
        status, data = command(args, timeout=timeout)
        if (not isinstance(status, dict) or set(status) != {'status', 'exit_code', 'timeout', 'output_limit', 'captured_bytes'} or
                status.get('status') not in {'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'} or
                type(status.get('timeout')) is not bool or type(status.get('output_limit')) is not bool or
                type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= 1024**2 or
                not isinstance(data, bytes) or len(data) != status['captured_bytes'] or
                not (status.get('exit_code') is None or type(status['exit_code']) is int)):
            raise ReplayError('settings_command_evidence_invalid')
        report['commands'].append({'command': label, **status})
        if status['status'] != 'completed' or status['exit_code'] != 0 or status['timeout'] or status['output_limit']:
            raise ReplayError('settings_native_command_failed')
        return data
    try:
        with tempfile.TemporaryDirectory(prefix='ayu-options-root-') as directory:
            private = Path(directory); private.chmod(0o700)
            app = extract_host(ipa, private / 'CustomeOptionsHost.app'); checked_metadata(app)
            snapshot = host_snapshot(app)
            call('verifySourceSignature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
            report['source_codesign_verified'] = True
            if call('verifyXcode', ['xcodebuild', '-version']).decode().strip() != 'Xcode 27.1\nBuild version 27A9275': raise ReplayError('settings_xcode_mismatch')
            source = private / 'visible-root.swift'; source.write_text(VISION_SOURCE)
            checker = private / 'visible-root-check'
            call('compileTitleChecker', ['xcrun', 'swiftc', '-O', '-warnings-as-errors', '-framework', 'Vision', '-framework', 'CoreText',
                '-framework', 'ImageIO', str(source), '-o', str(checker)], timeout=120)
            if checker.is_symlink() or not checker.is_file() or not 0 < checker.stat().st_size <= 20 * 1024**2: raise ReplayError('settings_checker_invalid')
            if json.loads(call('testTitleChecker', [str(checker), '--self-test'])) != {'self_test_passed': True, 'cases': 3}: raise ReplayError('settings_checker_invalid')
            report['title_checker_self_test_cases_passed'] = 3
            runtime = json.loads(call('verifyRuntime', ['xcrun', 'simctl', 'list', 'runtimes', '--json']))
            types = json.loads(call('verifyDeviceType', ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro']))
            if (not isinstance(runtime, dict) or not isinstance(types, dict) or
                    not isinstance(runtime.get('runtimes'), list) or not isinstance(types.get('devicetypes'), list) or
                    sum(isinstance(x, dict) and x.get('identifier') == RUNTIME and x.get('isAvailable') is True for x in runtime['runtimes']) != 1 or
                    sum(isinstance(x, dict) and x.get('identifier') == DEVICE_TYPE for x in types.get('devicetypes', [])) != 1): raise ReplayError('settings_device_unavailable')
            device_name = 'AYU_OPTIONS_ROOT_' + uuid.uuid4().hex
            value = call('createOwnedSimulator', ['xcrun', 'simctl', 'create', device_name, DEVICE_TYPE, RUNTIME]).decode().strip()
            try: uuid.UUID(value)
            except ValueError: raise ReplayError('settings_owned_device_invalid') from None
            identifier = value
            try:
                try:
                    call('bootOwnedSimulator', ['xcrun', 'simctl', 'bootstatus', identifier, '-b'], timeout=240)
                except ReplayError:
                    previous = report['commands'][-1]
                    if previous.get('status') != 'commandFailed' or previous.get('exit_code') != 149 or previous.get('timeout'):
                        raise
                    inventory = json.loads(call('verifyOwnedBootedState', ['xcrun', 'simctl', 'list', 'devices', 'available', '--json']))
                    if not isinstance(inventory, dict) or not isinstance(inventory.get('devices'), dict):
                        raise ReplayError('settings_owned_boot_not_verified')
                    rows = inventory['devices'].get(RUNTIME, [])
                    if not isinstance(rows, list): raise ReplayError('settings_owned_boot_not_verified')
                    matches = [row for row in rows if isinstance(row, dict) and isinstance(row.get('udid'), str)
                               and row['udid'].casefold() == identifier.casefold()]
                    # The exact UUID came from this invocation's create command.
                    if (len(matches) != 1 or matches[0].get('name') != device_name or
                            matches[0].get('deviceTypeIdentifier') != DEVICE_TYPE or matches[0].get('state') != 'Booted'):
                        raise ReplayError('settings_owned_boot_not_verified')
                    report['owned_already_booted_state_verified'] = True
                call('installFrozenHost', ['xcrun', 'simctl', 'install', identifier, str(app)], timeout=120)
                installed = Path(call('locateInstalledHost', ['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'app']).decode().strip())
                if not installed.is_absolute() or any(p.is_symlink() for p in (installed, *installed.parents)): raise ReplayError('settings_installed_host_invalid')
                checked_metadata(installed)
                if host_snapshot(installed) != snapshot: raise ReplayError('settings_installed_host_invalid')
                report['installed_host_verified'] = True
                video = output / 'root-light.mp4'
                with tempfile.TemporaryFile() as recording_log:
                    recorder = popen(['xcrun', 'simctl', 'io', identifier, 'recordVideo', '--codec=h264', '--force', str(video)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=recording_log, env=child_environment(), start_new_session=True)
                    started = time.monotonic()
                    while recorder.poll() is None and time.monotonic() - started < 15:
                        if b'Recording started' in os.pread(recording_log.fileno(), 8192, 0): break
                        pause(.1)
                    else: raise ReplayError('settings_recording_unavailable')
                    report['recording_started_before_launch'] = True
                    call('launchRoot', ['xcrun', 'simctl', 'launch', identifier, BUNDLE, '-AppleLanguages', '(en)', '-AppleLocale', 'en_US'])
                    pause(2)
                    screenshot = output / 'root-light.png'
                    call('captureRoot', ['xcrun', 'simctl', 'io', identifier, 'screenshot', str(screenshot)])
                    if screenshot.is_symlink() or not screenshot.is_file() or not 0 < screenshot.stat().st_size <= 16 * 1024**2: raise ReplayError('settings_screenshot_invalid')
                    screenshot.chmod(0o600)
                    observed = json.loads(call('recognizeRoot', [str(checker), str(screenshot)]))
                    if (not isinstance(observed, dict) or set(observed) != {'root_title_visible', 'width', 'height'} or
                            observed.get('root_title_visible') is not True or
                            type(observed.get('width')) is not int or not 1000 <= observed['width'] <= 1500 or
                            type(observed.get('height')) is not int or not 2000 <= observed['height'] <= 3000):
                        raise ReplayError('settings_visible_root_not_verified')
                    report['root_observation'] = observed
                    recorder.send_signal(signal.SIGINT)
                    code = recorder.wait(timeout=30)
                    try: os.killpg(recorder.pid, signal.SIGKILL)
                    except OSError: pass
                    recorder = None
                    if code != 0 or video.is_symlink() or not video.is_file() or not 1024 <= video.stat().st_size <= 32 * 1024**2:
                        raise ReplayError('settings_recording_invalid')
                    video.chmod(0o600); report['recording_return_code'] = 0
            finally:
                original_phase = report['phase']
                if recorder is not None:
                    try: os.killpg(recorder.pid, signal.SIGKILL)
                    except OSError: pass
                    try: recorder.wait(timeout=2)
                    except subprocess.SubprocessError: pass
                cleaned = True
                for action in ('shutdown', 'delete'):
                    try: call(action + 'OwnedSimulator', ['xcrun', 'simctl', action, identifier], timeout=30)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): cleaned = False
                report['cleanup_passed'] = cleaned
                report['phase'] = original_phase
            if not report['cleanup_passed']:
                report['phase'] = 'cleanupOwnedSimulator'
                raise ReplayError('settings_cleanup_not_verified')
            if digest(ipa) != IPA_SHA256 or host_snapshot(app) != snapshot: raise ReplayError('settings_source_host_changed')
            report.update(passed=True, phase='complete')
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
        report['passed'] = False
        raise
    finally:
        if not report['passed']:
            for name, limit in (('root-light.png', 16 * 1024**2), ('root-light.mp4', 32 * 1024**2)):
                path = output / name
                if path.is_symlink() or path.is_file() and path.stat().st_size > limit:
                    path.unlink()
                    report['unsafe_visual_removed'] = True
        write_json(output / 'result.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--output', type=Path, default=Path(OUTPUT)); args = parser.parse_args(argv)
    try: report = prepare(args.output) if args.command == 'prepare' else execute(args.output)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'diagnostic_role': ROLE, 'passed': False, 'error': 'settings_replay_rejected'})); return 1
    print(json.dumps(report, sort_keys=True)); return 0


if __name__ == '__main__': raise SystemExit(main())
