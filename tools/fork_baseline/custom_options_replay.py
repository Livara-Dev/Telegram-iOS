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
import math
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import signal
import selectors
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from xml.parsers.expat import ExpatError

import replay_artifact as artifacts
import launch_smoke
from custom_options_smoke import BUNDLE, DEVICE_TYPE, RUNTIME
from simulator_probe import redact_probe_text
from installer_control import read_json, write_json
from ipa_inspect import inspect_ipa
from projection_replay import child_environment, clean_environment, native_command

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
import CoreML
enum CheckError: Error { case invalid, cpuUnavailable }
func closedError(_ error: Error) -> [String: Any] {
    if let local = error as? CheckError {
        switch local { case .invalid: return ["domain": "checker", "code": 0]
        case .cpuUnavailable: return ["domain": "checker", "code": 1] }
    }
    let value = error as NSError
    let domain = value.domain == VNErrorDomain ? "Vision" : value.domain == MLModelErrorDomain ? "CoreML" : "other"
    return ["domain": domain, "code": domain != "other" && value.code >= Int(Int32.min) && value.code <= Int(Int32.max) ? value.code as Any : NSNull()]
}
func normalized(_ text: String) -> String {
    text.lowercased().filter { $0.isLetter || $0.isNumber }
}
func recognize(_ image: CGImage, cpuConfigured: inout Bool) throws -> [VNRecognizedTextObservation] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    // Apple's customWords contract requires language correction to be enabled.
    // Both acceptance and the negative controls still require the exact title.
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    // Guide the model toward the actual spelling; acceptance remains exact.
    request.customWords = ["Custome"]
    // Public macOS 14+ API; usesCPUOnly is deprecated. Select only a CPU that
    // Vision itself lists as supported, rather than assuming a VM has Metal.
    let supported = try request.supportedComputeStageDevices
    guard !supported.isEmpty else { throw CheckError.cpuUnavailable }
    for (stage, devices) in supported {
        guard let cpu = devices.first(where: { if case .cpu = $0 { return true }; return false }) else { throw CheckError.cpuUnavailable }
        request.setComputeDevice(cpu, for: stage)
    }
    cpuConfigured = true
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return request.results ?? []
}
func titleVisible(_ image: CGImage) throws -> Bool {
    var cpuConfigured = false
    return try recognize(image, cpuConfigured: &cpuConfigured).contains { observation in
        guard observation.boundingBox.midY > 0.80,
              let text = observation.topCandidates(1).first?.string else { return false }
        return normalized(text) == "customeoptions"
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
func writeSynthetic(_ image: CGImage, path: String) throws {
    let url = URL(fileURLWithPath: path)
    guard url.lastPathComponent == "title-check-positive.png",
          !FileManager.default.fileExists(atPath: path),
          let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else { throw CheckError.invalid }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { throw CheckError.invalid }
}
func syntheticControl(_ text: String, positive: Bool, imagePath: String?) -> [String: Any] {
    var result: [String: Any] = ["state": "imageUnavailable", "expected_title_seen": false,
        "expected_title_in_navigation_region": false, "known_variant_seen": false,
        "recognized_observations": 0, "known_title_regions": [[String: Any]](), "passed": false,
        "cpu_compute_selected": false, "error": NSNull()]
    var cpuConfigured = false
    do {
        let image = try synthetic(text)
        if let imagePath { try writeSynthetic(image, path: imagePath) }
        result["state"] = "visionUnavailable"
        let observations = try recognize(image, cpuConfigured: &cpuConfigured)
        guard observations.count <= 128 else { throw CheckError.invalid }
        var expected = false, navigation = false, variant = false
        var regions: [[String: Any]] = []
        for observation in observations {
            guard let text = observation.topCandidates(1).first?.string else { continue }
            let word = normalized(text)
            let kind: String
            switch word {
            case "customeoptions": kind = "expected"; expected = true; navigation = navigation || observation.boundingBox.midY > 0.80
            case "customoptions": kind = "variant"; variant = true
            case "anotherscreen": kind = "negative"
            default: continue // Never serialize arbitrary model-recognized text.
            }
            guard regions.count < 16 else { throw CheckError.invalid }
            let rect = observation.boundingBox
            regions.append(["kind": kind, "rect": [rect.minX, rect.minY, rect.width, rect.height].map { Double($0) }])
        }
        result = ["state": "completed", "expected_title_seen": expected,
            "expected_title_in_navigation_region": navigation, "known_variant_seen": variant,
            "recognized_observations": observations.count, "known_title_regions": regions,
            "passed": positive ? navigation : !expected, "cpu_compute_selected": cpuConfigured, "error": NSNull()]
    } catch { result["cpu_compute_selected"] = cpuConfigured; result["error"] = closedError(error) }
    return result
}
do {
    var result: [String: Any]
    var succeeded = true
    if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--self-test" {
        let correct = syntheticControl("Custome options", positive: true, imagePath: CommandLine.arguments[2])
        let wrong = syntheticControl("Another screen", positive: false, imagePath: nil)
        let blank = syntheticControl("", positive: false, imagePath: nil)
        succeeded = [correct, wrong, blank].allSatisfy { $0["passed"] as? Bool == true }
        result = ["self_test_passed": succeeded, "cases": 3,
            "controls": ["positive": correct, "wrong": wrong, "blank": blank]]
    } else {
        let metadataOnly = CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--metadata"
        guard CommandLine.arguments.count == 2 || metadataOnly else { throw CheckError.invalid }
        let url = URL(fileURLWithPath: CommandLine.arguments[metadataOnly ? 2 : 1])
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
        result = ["width": width, "height": height]
        if !metadataOnly { result["root_title_visible"] = try titleVisible(image) }
    }
    let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    guard let text = String(data: data, encoding: .utf8) else { throw CheckError.invalid }
    print(text)
    if !succeeded { exit(1) }
} catch { print("{\"verified\":false}"); exit(1) }
'''


class ReplayError(ValueError):
    pass


def simctl_phase(arguments):
    """Allow only this retained replay's fixed simulator operations."""
    def identifier(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', value) is not None
    if arguments in (['xcrun', 'simctl', 'list', 'runtimes', '--json'],
                     ['xcrun', 'simctl', 'list', '--json', 'devicetypes', 'iPhone 15 Pro'],
                     ['xcrun', 'simctl', 'list', 'devices', 'available', '--json'],
                     ['xcrun', 'simctl', 'list', 'devices', '--json']): return 'unknownPhase'
    if (len(arguments) == 6 and arguments[:3] == ['xcrun', 'simctl', 'create'] and
            re.fullmatch(r'AYU_OPTIONS_ROOT_[0-9a-f]{32}', arguments[3]) and
            arguments[4:] == [DEVICE_TYPE, RUNTIME]): return 'createDevice'
    if len(arguments) == 5 and arguments[:3] == ['xcrun', 'simctl', 'bootstatus'] and identifier(arguments[3]) and arguments[4] == '-b': return 'waitForBoot'
    if (len(arguments) == 5 and arguments[:3] == ['xcrun', 'simctl', 'install'] and
            identifier(arguments[3]) and Path(arguments[4]).is_absolute()): return 'installApplication'
    if (len(arguments) == 6 and arguments[:3] == ['xcrun', 'simctl', 'get_app_container'] and
            identifier(arguments[3]) and arguments[4:] == [BUNDLE, 'app']): return 'getContainer'
    if arguments[:3] == ['xcrun', 'simctl', 'launch']:
        tail = arguments[3:]
        if len(tail) == 6 and identifier(tail[0]) and tail[1:] == [BUNDLE, '-AppleLanguages', '(en)', '-AppleLocale', 'en_US']: return 'launchApplication'
    if (len(arguments) == 6 and arguments[:3] == ['xcrun', 'simctl', 'io'] and identifier(arguments[3]) and
            arguments[4] == 'screenshot' and Path(arguments[5]).is_absolute()): return 'captureScreenshot'
    if len(arguments) == 4 and arguments[:2] == ['xcrun', 'simctl'] and identifier(arguments[3]):
        if arguments[2] == 'boot': return 'bootDevice'
        if arguments[2] == 'shutdown': return 'shutdownDevice'
        if arguments[2] == 'delete': return 'deleteDevice'
    raise ReplayError('settings_simulator_command_not_allowed')


SIMCTL_FAILURE_STATES = {'commandTimedOut': 'timedOut', 'commandFailed': 'commandFailed',
                         'commandUnavailable': 'unavailable', 'commandOutputLimit': 'outputLimit'}
SIMCTL_STATUS_FIELDS = {'supervisor', 'captured_bytes_scope', 'failure'}


def ui_native_command(arguments, timeout=30):
    """Use the exercised startup supervisor only for the fixed simctl calls.

    That helper returns stripped stdout, not merged raw output. On failure it
    retains only closed SmokeError evidence, so zero returned bytes must never
    be read as proof that the child emitted no data. Other commands keep their
    existing supervisor, including the checker self-test's expected exit1 JSON.
    """
    if arguments[:2] != ['xcrun', 'simctl']: return native_command(arguments, timeout=timeout)
    phase = simctl_phase(arguments)
    status = {'status': 'unavailable', 'exit_code': None, 'timeout': False, 'output_limit': False,
              'captured_bytes': 0, 'supervisor': 'launchSmokeCommand',
              'captured_bytes_scope': 'returnedStdoutPayloadBytes', 'failure': None}
    try:
        with clean_environment():
            text = launch_smoke.command(arguments, timeout=timeout, phase=phase)
        if not isinstance(text, str): raise TypeError('Invalid supervisor output')
        data = text.encode('utf-8')
        if len(data) > launch_smoke.COMMAND_OUTPUT_LIMIT: raise launch_smoke.SmokeError('commandOutputLimit', phase)
        status.update(status='completed', exit_code=0, captured_bytes=len(data))
        return status, data
    except launch_smoke.SmokeError as error:
        failure = error.evidence()
        status.update(status=SIMCTL_FAILURE_STATES.get(error.code, 'unavailable'), exit_code=error.return_code,
                      timeout=error.timeout, output_limit=error.code == 'commandOutputLimit', failure=failure)
    except (OSError, ValueError, RuntimeError, TypeError, subprocess.SubprocessError):
        status['failure'] = launch_smoke.SmokeError('commandUnavailable', phase).evidence()
    return status, b''


def validate_simctl_status(status, arguments):
    """Do not publish arbitrary metadata from a command adapter."""
    failure = status.get('failure')
    if (arguments[:2] != ['xcrun', 'simctl'] or status.get('supervisor') != 'launchSmokeCommand' or
            status.get('captured_bytes_scope') != 'returnedStdoutPayloadBytes' or
            type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= launch_smoke.COMMAND_OUTPUT_LIMIT):
        raise ReplayError('settings_command_evidence_invalid')
    if failure is None:
        if status['status'] != 'completed' or status['exit_code'] != 0 or status['timeout'] or status['output_limit']:
            raise ReplayError('settings_command_evidence_invalid')
        return
    if (not isinstance(failure, dict) or set(failure) != {'code', 'phase', 'return_code', 'timeout', 'system_errors'} or
            failure.get('code') not in SIMCTL_FAILURE_STATES or failure.get('phase') != simctl_phase(arguments) or
            type(failure.get('timeout')) is not bool or not isinstance(failure.get('system_errors'), list)):
        raise ReplayError('settings_command_evidence_invalid')
    selected = launch_smoke.SmokeError(failure['code'], failure['phase'], failure['return_code'],
                                     failure['timeout'], failure['system_errors']).evidence()
    if (selected != failure or status['status'] != SIMCTL_FAILURE_STATES[failure['code']] or
            status['exit_code'] != failure['return_code'] or status['timeout'] != failure['timeout'] or
            status['timeout'] != (failure['code'] == 'commandTimedOut') or
            status['output_limit'] != (failure['code'] == 'commandOutputLimit') or status['captured_bytes'] != 0):
        raise ReplayError('settings_command_evidence_invalid')


def self_control_report(data):
    """Only fixed synthetic-case booleans/known regions may authorize observation."""
    if not isinstance(data, bytes) or not 0 < len(data) <= 16384:
        raise ReplayError('settings_checker_invalid')
    def unique(items):
        value = {}
        for key, item in items:
            if key in value: raise ReplayError('settings_checker_invalid')
            value[key] = item
        return value
    try: value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError): raise ReplayError('settings_checker_invalid') from None
    if (not isinstance(value, dict) or set(value) != {'self_test_passed', 'cases', 'controls'} or
            type(value.get('self_test_passed')) is not bool or type(value.get('cases')) is not int or value['cases'] != 3 or
            not isinstance(value.get('controls'), dict) or set(value['controls']) != {'positive', 'wrong', 'blank'}):
        raise ReplayError('settings_checker_invalid')
    fields = {'state', 'expected_title_seen', 'expected_title_in_navigation_region', 'known_variant_seen',
              'recognized_observations', 'known_title_regions', 'passed', 'cpu_compute_selected', 'error'}
    for name, control in value['controls'].items():
        if (not isinstance(control, dict) or set(control) != fields or
                not isinstance(control.get('state'), str) or control['state'] not in {'completed', 'imageUnavailable', 'visionUnavailable'} or
                any(type(control.get(k)) is not bool for k in ('expected_title_seen', 'expected_title_in_navigation_region', 'known_variant_seen', 'passed', 'cpu_compute_selected')) or
                type(control.get('recognized_observations')) is not int or not 0 <= control['recognized_observations'] <= 128 or
                not isinstance(control.get('known_title_regions'), list) or len(control['known_title_regions']) > 16):
            raise ReplayError('settings_checker_invalid')
        error = control['error']
        if error is not None and (not isinstance(error, dict) or set(error) != {'domain', 'code'} or
                error.get('domain') not in ('Vision', 'CoreML', 'checker', 'other') or
                (error.get('domain') == 'other' and error.get('code') is not None) or
                (error.get('domain') != 'other' and (type(error.get('code')) is not int or not -(2**31) <= error['code'] < 2**31))):
            raise ReplayError('settings_checker_invalid')
        expected = navigation = variant = False
        for region in control['known_title_regions']:
            if (not isinstance(region, dict) or set(region) != {'kind', 'rect'} or
                    not isinstance(region.get('kind'), str) or region['kind'] not in {'expected', 'variant', 'negative'} or
                    not isinstance(region.get('rect'), list) or len(region['rect']) != 4 or
                    any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in region['rect'])):
                raise ReplayError('settings_checker_invalid')
            x, y, width, height = region['rect']
            if width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
                raise ReplayError('settings_checker_invalid')
            if region['kind'] == 'expected': expected = True; navigation = navigation or y + height / 2 > .80
            if region['kind'] == 'variant': variant = True
        completed = control['state'] == 'completed'
        if (control['expected_title_seen'] != expected or control['expected_title_in_navigation_region'] != navigation or
                control['known_variant_seen'] != variant or control['recognized_observations'] < len(control['known_title_regions']) or
                control['passed'] != (completed and (navigation if name == 'positive' else not expected)) or
                (completed and (error is not None or control['cpu_compute_selected'] is not True)) or
                (not completed and (error is None or control['recognized_observations'] or control['known_title_regions']))):
            raise ReplayError('settings_checker_invalid')
    if value['self_test_passed'] != all(c['passed'] for c in value['controls'].values()):
        raise ReplayError('settings_checker_invalid')
    return value


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


def owned_installed_bundle(identifier, name, *, home=None, uid=None):
    """Inspect only this invocation's new device; unknown layouts grant no path."""
    proof = {'state': 'unavailable', 'reason': 'deviceSetUnavailable', 'device_identity_verified': False,
             'unique_candidate_verified': False}
    try:
        if not isinstance(identifier, str) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', identifier) is None:
            return None, {**proof, 'state': 'rejected', 'reason': 'invalidOwnedIdentifier'}
        user = os.getuid() if uid is None else uid
        root = Path(home if home is not None else Path.home()).resolve(strict=True)
        device = root / 'Library/Developer/CoreSimulator/Devices' / identifier
        path = root
        for part in device.relative_to(root).parts:
            path = path / part
            if path.is_symlink(): return None, {**proof, 'state': 'rejected', 'reason': 'symbolicLink'}
            if not path.is_dir(): return None, proof
        if device.stat().st_uid != user: return None, {**proof, 'state': 'rejected', 'reason': 'ownerMismatch'}
        metadata = device / 'device.plist'
        if metadata.is_symlink(): return None, {**proof, 'state': 'rejected', 'reason': 'symbolicLink'}
        if not metadata.exists(): return None, {**proof, 'reason': 'deviceMetadataUnavailable'}
        info = metadata.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != user:
            return None, {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceMetadata'}
        if not 0 < info.st_size <= 65536: return None, {**proof, 'reason': 'deviceMetadataUnsupported'}
        descriptor = os.open(metadata, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != user or not 0 < opened.st_size <= 65536:
                return None, {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceMetadata'}
            raw = os.read(descriptor, 65537)
        finally: os.close(descriptor)
        if len(raw) != opened.st_size: return None, {**proof, 'reason': 'deviceMetadataUnavailable'}
        try: value = plistlib.loads(raw)
        except (ValueError, TypeError, ExpatError, OverflowError): return None, {**proof, 'reason': 'deviceMetadataUnsupported'}
        if not isinstance(value, dict) or any(not isinstance(value.get(k), str) for k in ('UDID', 'name', 'deviceType', 'runtime')):
            return None, {**proof, 'reason': 'deviceMetadataUnsupported'}
        if (value['UDID'].casefold() != identifier.casefold() or value['name'] != name or
                value['deviceType'] != DEVICE_TYPE or value['runtime'] != RUNTIME):
            return None, {**proof, 'state': 'rejected', 'reason': 'ownedDeviceIdentityMismatch'}
        proof['device_identity_verified'] = True
        application = device
        for part in ('data', 'Containers', 'Bundle', 'Application'):
            application = application / part
            if application.is_symlink(): return None, {**proof, 'state': 'rejected', 'reason': 'symbolicLink'}
            if not application.is_dir(): return None, {**proof, 'reason': 'applicationLayoutUnavailable'}
            if application.stat().st_uid != user: return None, {**proof, 'state': 'rejected', 'reason': 'ownerMismatch'}
        candidates = []
        for index, container in enumerate(application.iterdir()):
            if index >= 32: return None, {**proof, 'state': 'rejected', 'reason': 'containerLimitExceeded'}
            if container.is_symlink(): return None, {**proof, 'state': 'rejected', 'reason': 'symbolicLink'}
            if (not container.is_dir() or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', container.name) is None):
                return None, {**proof, 'reason': 'applicationLayoutUnsupported'}
            if container.stat().st_uid != user: return None, {**proof, 'state': 'rejected', 'reason': 'ownerMismatch'}
            candidate = container / 'CustomeOptionsHost.app'
            if candidate.is_symlink(): return None, {**proof, 'state': 'rejected', 'reason': 'symbolicLink'}
            if candidate.exists():
                if not candidate.is_dir() or candidate.stat().st_uid != user:
                    return None, {**proof, 'state': 'rejected', 'reason': 'unsafeApplicationBundle'}
                candidates.append(candidate)
        if len(candidates) > 1: return None, {**proof, 'state': 'rejected', 'reason': 'ambiguousApplicationBundle'}
        if not candidates: return None, {**proof, 'reason': 'applicationBundleUnavailable'}
        return candidates[0], {**proof, 'state': 'candidateLocated', 'reason': None, 'unique_candidate_verified': True}
    except (OSError, ValueError, TypeError, RuntimeError):
        return None, {**proof, 'reason': 'filesystemUnavailable'}


def owned_uuid_absent(data, identifier):
    """Only a valid unfiltered inventory may establish final device absence."""
    if not isinstance(data, bytes) or not 0 < len(data) <= 65536: return None
    def unique(items):
        value = {}
        for key, item in items:
            if key in value: raise ValueError('Ambiguous device inventory')
            value[key] = item
        return value
    try:
        payload = json.loads(data, object_pairs_hook=unique)
        devices = payload.get('devices') if isinstance(payload, dict) else None
        if not isinstance(devices, dict): return None
        matches, count = 0, 0
        for runtime, rows in devices.items():
            if not isinstance(runtime, str) or not isinstance(rows, list): return None
            for row in rows:
                count += 1
                if count > 1024 or not isinstance(row, dict) or not isinstance(row.get('udid'), str): return None
                if re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', row['udid']) is None: return None
                matches += row['udid'].casefold() == identifier.casefold()
        return matches == 0
    except (ValueError, TypeError, UnicodeError, RecursionError): return None


class Recording:
    """The one fixed simctl recorder, with bounded private copies of both pipes."""
    def __init__(self, identifier, video, popen, pause):
        self.identifier, self.video, self.popen, self.pause = identifier, Path(video), popen, pause
        self.process = self.reader = None
        self.stop = threading.Event(); self.lock = threading.Lock()
        self.data = {'stdout': bytearray(), 'stderr': bytearray()}
        self.state, self.ready, self.limited = 'notAttempted', False, False
        self.exit_code = None

    def _kill(self):
        if self.process is None: return
        try: os.killpg(self.process.pid, signal.SIGKILL)
        except OSError:
            if self.process.poll() is None:
                try: self.process.kill()
                except OSError: pass

    def _drain(self):
        try:
            with selectors.DefaultSelector() as selector:
                for name in self.data: selector.register(getattr(self.process, name), selectors.EVENT_READ, name)
                while selector.get_map() and not self.stop.is_set():
                    for key, _ in selector.select(.05):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk: selector.unregister(key.fileobj); continue
                        with self.lock:
                            room = 16384 - sum(len(x) for x in self.data.values())
                            self.data[key.data].extend(chunk[:room])
                            self.ready = self.ready or any(b'Recording started' in x for x in self.data.values())
                            if self.ready and self.state == 'starting': self.state = 'ready'
                            if len(chunk) > room: self.limited = True; self.state = 'outputLimit'
                        if self.limited: self._kill(); return
        except (OSError, ValueError, RuntimeError): self.state = 'readerUnavailable'

    def start(self):
        """Request recording without making readiness a prerequisite to launch."""
        self.state = 'starting'
        try:
            self.process = self.popen(['xcrun', 'simctl', 'io', self.identifier, 'recordVideo', '--codec=h264', '--force', str(self.video)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_environment(), start_new_session=True)
            self.reader = threading.Thread(target=self._drain, name='fixed-settings-recording', daemon=True); self.reader.start()
            return True
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.state = 'unavailable'; self._kill(); return False

    def finish(self):
        complete = False
        if self.process is not None:
            try:
                if not self.limited and self.state in {'starting', 'ready'} and self.process.poll() is None:
                    self.process.send_signal(signal.SIGINT); self.exit_code = self.process.wait(timeout=30)
                    complete = self.exit_code == 0
                else:
                    self.exit_code = self.process.poll()
                    if self.state in {'starting', 'ready'}: self.state = 'endedEarly'
            except (OSError, ValueError, subprocess.SubprocessError): self.state = 'stopFailed'
            finally:
                self._kill()
                try: self.process.wait(timeout=2)
                except (OSError, subprocess.SubprocessError): pass
                if self.reader is not None:
                    self.reader.join(timeout=1)
                    self.stop.set(); self.reader.join(timeout=1)
                    if self.reader.is_alive(): complete = False; self.state = 'readerUnavailable'
                for name in self.data:
                    stream = getattr(self.process, name, None)
                    if stream is not None: stream.close()
        complete = (complete and not self.limited and self.state in {'starting', 'ready'} and not self.video.is_symlink()
                    and self.video.is_file() and 1024 <= self.video.stat().st_size <= 32 * 1024**2)
        if complete:
            self.video.chmod(0o600); self.state = 'complete'
        else:
            if self.state == 'ready': self.state = 'invalidVideo'
            self.video.unlink(missing_ok=True)
        return complete

    def diagnostic(self):
        with self.lock:
            raw = b'\n'.join(name.encode() + b':\n' + bytes(value) for name, value in self.data.items())
            counts = {name + '_bytes': len(value) for name, value in self.data.items()}
        return {'state': self.state, 'readiness_observed': self.ready, 'output_limit': self.limited,
                'exit_code': self.exit_code, **counts, 'output': redact_probe_text(raw, [])}


def execute(output=OUTPUT, root=None, command=ui_native_command, pause=time.sleep, popen=subprocess.Popen):
    root, output = location(output, root); head, count = checkout(root)
    validate_proof(read_json(output / 'verification.json'), head, count)
    if platform.system() != 'Darwin': raise ReplayError('macos_required')
    ipa = output / 'CustomeOptionsHost-simulator.ipa'
    if ipa.is_symlink() or not ipa.is_file() or digest(ipa) != IPA_SHA256: raise ReplayError('settings_host_digest_mismatch')
    if any((output / name).exists() or (output / name).is_symlink() for name in ('result.json', 'root-light.png', 'root-light.mp4', 'title-check-positive.png')):
        raise ReplayError('settings_replay_already_exists')
    inspect_ipa(ipa, 'simulator', BUNDLE)
    report = {'schema': 1, 'diagnostic_role': ROLE, 'source_commit': SOURCE, 'current_application_source_commit': CURRENT_SOURCE,
              'device_model': 'iPhone 15 Pro', 'runtime': '27.0', 'passed': False, 'phase': 'preflight',
              'new_xctest_cases_executed': 0, 'historical_controller_cases_passed': 8, 'historical_interaction_cases_passed': 1,
              'application_recompiled': False, 'renderer_recompiled': False, 'logged_in_settings_verified': False,
              'physical_device_verified': False, 'release_qualified': False, 'visual_review_required': True,
              'commands': [], 'cleanup_passed': False, 'source_codesign_verified': False, 'installed_host_verified': False,
              'root_title_automatically_verified': False, 'diagnostic_root_captured': False,
              'warmup_launch_completed': False, 'recording_available': False, 'recording_started_before_launch': False,
              'recording_readiness_observed_before_launch': False,
              'recording_requested_before_launch': False, 'launch_command_completed': False, 'launch_rpc_failure': None,
              'cleanup_results': {'shutdown_completed': None, 'delete_command_completed': None, 'owned_uuid_absent': None}}
    identifier, recorder = None, None
    def call(label, args, timeout=30, self_test=False):
        report['phase'] = label
        status, data = command(args, timeout=timeout)
        fields = {'status', 'exit_code', 'timeout', 'output_limit', 'captured_bytes'}
        if (not isinstance(status, dict) or set(status) not in (fields, fields | SIMCTL_STATUS_FIELDS) or
                status.get('status') not in {'completed', 'commandFailed', 'timedOut', 'outputLimit', 'unavailable'} or
                type(status.get('timeout')) is not bool or type(status.get('output_limit')) is not bool or
                type(status.get('captured_bytes')) is not int or not 0 <= status['captured_bytes'] <= 1024**2 or
                not isinstance(data, bytes) or len(data) != status['captured_bytes'] or
                not (status.get('exit_code') is None or type(status['exit_code']) is int)):
            raise ReplayError('settings_command_evidence_invalid')
        if set(status) != fields: validate_simctl_status(status, args)
        report['commands'].append({'command': label, **status})
        if self_test:
            if (label != 'testTitleChecker' or args != [str(checker), '--self-test', str(output / 'title-check-positive.png')] or
                    status['timeout'] or status['output_limit'] or
                    (status['status'], status['exit_code']) not in {('completed', 0), ('commandFailed', 1)}):
                raise ReplayError('settings_native_command_failed')
            selected = self_control_report(data)
            if selected['self_test_passed'] != (status['status'] == 'completed' and status['exit_code'] == 0):
                raise ReplayError('settings_checker_invalid')
            report['title_checker_self_test'] = selected
            return data
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
            call('compileTitleChecker', ['xcrun', 'swiftc', '-O', '-warnings-as-errors', '-target', 'arm64-apple-macos14.0',
                '-framework', 'CoreML', '-framework', 'Vision', '-framework', 'CoreText',
                '-framework', 'ImageIO', str(source), '-o', str(checker)], timeout=120)
            if checker.is_symlink() or not checker.is_file() or not 0 < checker.stat().st_size <= 20 * 1024**2: raise ReplayError('settings_checker_invalid')
            call('testTitleChecker', [str(checker), '--self-test', str(output / 'title-check-positive.png')], self_test=True)
            controls_passed = report['title_checker_self_test']['self_test_passed']
            report['title_checker_self_test_cases_passed'] = sum(c['passed'] for c in report['title_checker_self_test']['controls'].values())
            positive = output / 'title-check-positive.png'
            if report['title_checker_self_test']['controls']['positive']['state'] == 'imageUnavailable':
                positive.unlink(missing_ok=True)
                report['synthetic_positive_image_recorded'] = False
            else:
                if positive.is_symlink() or not positive.is_file() or not 0 < positive.stat().st_size <= 1024**2:
                    raise ReplayError('settings_synthetic_image_invalid')
                positive.chmod(0o600); report['synthetic_positive_image_recorded'] = True
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
                # Match the exercised startup lifecycle. bootstatus -b can also
                # request boot; this explicit request is not a root-cause claim.
                call('requestOwnedBoot', ['xcrun', 'simctl', 'boot', identifier], timeout=120)
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
                report['phase'] = 'locateInstalledHost'
                installed, filesystem = owned_installed_bundle(identifier, device_name)
                locator = report['installed_host_locator'] = {'origin': 'ownedDeviceFilesystem' if installed is not None else 'notLocated',
                    'state': 'unverified', 'reason': None, 'filesystem': filesystem}
                if filesystem['state'] == 'rejected':
                    locator.update(state='rejected', reason=filesystem['reason'])
                    raise ReplayError('settings_installed_host_invalid')
                if installed is None:
                    try: installed = Path(call('locateInstalledHost', ['xcrun', 'simctl', 'get_app_container', identifier, BUNDLE, 'app'], timeout=5).decode().strip())
                    except (ValueError, RuntimeError):
                        locator.update(reason='apiLookupUnavailable')
                        raise
                    locator['origin'] = 'simctl'
                if not installed.is_absolute() or any(p.is_symlink() for p in (installed, *installed.parents)):
                    locator.update(state='rejected', reason='unsafeInstalledPath')
                    raise ReplayError('settings_installed_host_invalid')
                try: checked_metadata(installed)
                except (OSError, ValueError):
                    locator.update(state='rejected', reason='installedMetadataMismatch')
                    raise
                try: installed_snapshot = host_snapshot(installed)
                except (OSError, ValueError):
                    locator.update(state='rejected', reason='installedInventoryMismatch')
                    raise
                if installed_snapshot != snapshot:
                    locator.update(state='rejected', reason='installedInventoryMismatch')
                    raise ReplayError('settings_installed_host_invalid')
                try: call('verifyInstalledSignature', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed)])
                except (ValueError, RuntimeError):
                    locator.update(state='rejected', reason='installedSignatureUnverified')
                    raise
                locator.update(state='attested', reason=None)
                report['installed_host_verified'] = True
                video = output / 'root-light.mp4'
                recorder = Recording(identifier, video, popen, pause)
                report['phase'] = 'startRecording'
                report['recording_requested_before_launch'] = recorder.start()
                # Snapshot readiness now; a later sentinel must never rewrite
                # the evidence about what was known before this one launch.
                ready_before_launch = recorder.ready
                report['recording_readiness_observed_before_launch'] = ready_before_launch
                report['recording_started_before_launch'] = ready_before_launch
                report['recording_started_before_recorded_launch'] = ready_before_launch
                # Match the exercised startup ordering: recording is requested
                # before launch, and its readiness does not gate that launch.
                try:
                    call('launchRoot', ['xcrun', 'simctl', 'launch', identifier, BUNDLE,
                        '-AppleLanguages', '(en)', '-AppleLocale', 'en_US'])
                    report['launch_command_completed'] = True
                except ReplayError as error:
                    # Only an already validated failure of this exact command
                    # permits diagnostic capture. Malformed evidence and all
                    # integrity/preflight failures still stop immediately.
                    previous = report['commands'][-1]
                    if (str(error) != 'settings_native_command_failed' or previous['command'] != 'launchRoot' or
                            previous['status'] == 'completed'): raise
                    report['launch_rpc_failure'] = dict(previous)
                pause(2)
                screenshot = output / 'root-light.png'
                call('captureRoot', ['xcrun', 'simctl', 'io', identifier, 'screenshot', str(screenshot)])
                if screenshot.is_symlink() or not screenshot.is_file() or not 0 < screenshot.stat().st_size <= 16 * 1024**2: raise ReplayError('settings_screenshot_invalid')
                screenshot.chmod(0o600)
                if controls_passed:
                    observed = json.loads(call('recognizeRoot', [str(checker), str(screenshot)]))
                else:
                    observed = json.loads(call('inspectDiagnosticRoot', [str(checker), '--metadata', str(screenshot)]))
                fields = {'width', 'height'} | ({'root_title_visible'} if controls_passed else set())
                if (not isinstance(observed, dict) or set(observed) != fields or
                        (controls_passed and type(observed.get('root_title_visible')) is not bool) or
                        type(observed.get('width')) is not int or not 1000 <= observed['width'] <= 1500 or
                        type(observed.get('height')) is not int or not 2000 <= observed['height'] <= 3000):
                    raise ReplayError('settings_visible_root_not_verified')
                report['root_observation'] = observed
                report['root_title_automatically_verified'] = report['launch_command_completed'] and controls_passed and observed['root_title_visible']
                report['diagnostic_root_captured'] = True
            finally:
                original_phase = report['phase']
                if recorder is not None:
                    report['phase'] = 'stopRecording'
                    try: report['recording_available'] = recorder.finish()
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                        report['recording_available'] = False
                        recorder.state = 'stopFailed'; recorder._kill()
                    report['recording_return_code'] = recorder.exit_code
                    report['recording_diagnostic'] = recorder.diagnostic()
                    recorder = None
                cleanup = report['cleanup_results']
                for action in ('shutdown', 'delete'):
                    field = 'shutdown_completed' if action == 'shutdown' else 'delete_command_completed'
                    try:
                        call(action + 'OwnedSimulator', ['xcrun', 'simctl', action, identifier], timeout=30)
                        cleanup[field] = True
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): cleanup[field] = False
                if cleanup['delete_command_completed']:
                    try:
                        inventory = call('verifyOwnedDeletion', ['xcrun', 'simctl', 'list', 'devices', '--json'], timeout=10)
                        cleanup['owned_uuid_absent'] = owned_uuid_absent(inventory, identifier)
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError): pass
                report['cleanup_passed'] = cleanup['delete_command_completed'] is True and cleanup['owned_uuid_absent'] is True
                report['phase'] = original_phase
            if not report['cleanup_passed']:
                report['phase'] = 'cleanupOwnedSimulator'
                raise ReplayError('settings_cleanup_not_verified')
            if digest(ipa) != IPA_SHA256 or host_snapshot(app) != snapshot: raise ReplayError('settings_source_host_changed')
            if report['launch_rpc_failure'] is not None:
                report['phase'] = 'launchFailedDiagnosticOnly'
                raise ReplayError('settings_launch_failed_diagnostic_only')
            if not report['recording_available']:
                report['phase'] = 'recordingFailedDiagnosticOnly'
                raise ReplayError('settings_recording_failed_diagnostic_only')
            if not controls_passed:
                report['phase'] = 'selfTestFailedDiagnosticOnly'
                raise ReplayError('settings_self_test_failed_diagnostic_only')
            if not report['root_title_automatically_verified']:
                report['phase'] = 'visibleRootNotVerified'
                raise ReplayError('settings_visible_root_not_verified')
            report.update(passed=True, phase='complete')
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
        report['passed'] = False
        raise
    finally:
        if not report['passed']:
            for name, limit in (('root-light.png', 16 * 1024**2), ('root-light.mp4', 32 * 1024**2), ('title-check-positive.png', 1024**2)):
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
