#!/usr/bin/env python3
"""Build/test/record only the real production options renderer in a synthetic host.

No Telegram account, authentication, account binding, production-app installation,
or physical iPhone behavior is verified by this profile.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import zipfile

import baseline
from ipa_inspect import inspect_ipa
from launch_smoke import SmokeError
from custom_options_smoke import (BUNDLE, STAGES, DisposableRendererSimulator, export_screenshots,
                                  frames_from_log, appearances_from_log, InvalidVisualEvidence, command)
from custom_options_smoke import validate_runtime

ROOT = Path(__file__).resolve().parents[2]
APP_TARGET = '//Tests/CustomeOptionsHost:CustomeOptionsHost'
UNIT_TARGET = '//Tests/CustomeOptionsHost:CustomeOptionsControllerTests'
UI_TARGET = '//Tests/CustomeOptionsHost:CustomeOptionsInteractionTests'
RENDERER_SOURCES = ('submodules/SettingsUI/Sources/CustomeOptionsCatalog.swift',
                    'submodules/SettingsUI/Sources/CustomeOptionsController.swift',
                    'submodules/SettingsUI/Sources/LocalClientCosmeticsController.swift')
EXPECTED_CASES = {UNIT_TARGET: ('CustomeOptionsControllerTests', 8), UI_TARGET: ('CustomeOptionsInteractionTests', 1)}


def log_directory(bazel_root, label):
    if label not in EXPECTED_CASES:
        raise InvalidVisualEvidence('Native test label is not fixed')
    root = (ROOT / 'bazel-testlogs').resolve(strict=True)
    if not root.is_relative_to(Path(bazel_root).resolve(strict=True)):
        raise InvalidVisualEvidence('Native logs escaped the private build root')
    target = root / 'Tests/CustomeOptionsHost' / label.split(':')[1]
    for path in (target.parent.parent, target.parent, target):
        if path.is_symlink(): raise InvalidVisualEvidence('Native log directory is a symlink')
    return target


def retain_native_logs(bazel_root, label, output, secrets, expected_device):
    directory = log_directory(bazel_root, label)
    output = Path(output); output.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw_test_log = ''
    for name in ('test.log', 'test.xml'):
        path = directory / name
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 2 * 1024**2:
                raise InvalidVisualEvidence('Native log exceeds limits')
            data = os.read(descriptor, 2 * 1024**2 + 1)
        finally: os.close(descriptor)
        text = data.decode('utf-8', errors='replace')
        if name == 'test.log': raw_test_log = text
        for secret in secrets:
            if secret: text = text.replace(secret, '[REDACTED]')
        (output / name).write_text(text)
    text = (output / 'test.log').read_text()
    if (not isinstance(expected_device, str) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', expected_device) is None or
            re.search(r'-destination\s+id=' + re.escape(expected_device) + r'(?:\s|$)', raw_test_log) is None):
        raise InvalidVisualEvidence('Native suite did not use the owned disposable simulator')
    class_name, expected = EXPECTED_CASES[label]
    passed = re.findall(r"Test Case '([^'\n]+)' passed", text)
    failed = re.findall(r"Test Case '([^'\n]+)' (?:failed|skipped)", text)
    if len(set(passed)) != expected or len(passed) != expected or failed or any(class_name not in name for name in passed):
        raise InvalidVisualEvidence('Native case execution does not match the fixed suite')
    return {'target': label, 'executed': expected, 'passed': expected, 'failed': 0, 'skipped': 0, 'owned_simulator_verified': True}


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def run(env):
    config = baseline.configuration(env, unsigned=True)
    # Reuse the host/pin/submodule checks, then validate our own fixed iPhone 15
    # profile rather than creating or booting the baseline iPhone 17 device.
    bazel = baseline.preflight(env, simulator=False)
    evidence = ROOT / 'build/fork-evidence/custom-options-ui'
    for path in (ROOT / 'build', evidence.parent, evidence):
        if path.is_symlink(): raise InvalidVisualEvidence('Unsafe renderer evidence destination')
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    report = {'profile': 'synthetic_production_renderer', 'source_commit': baseline.output(['git', 'rev-parse', 'HEAD']),
              'logged_in_settings_verified': False, 'physical_device_verified': False,
              'full_application_built': False, 'authentication_codes_used': False,
              'account_or_network_client_created': False, 'passed': False, 'phase': 'buildHost',
              'device_model': 'iPhone 15 Pro', 'simulator_runtime': '27.0',
              'renderer_sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in RENDERER_SOURCES},
              'tests': [], 'visual_review_required': True}
    secrets = (config['api_id'], config['api_hash'])
    try:
        report['phase'] = 'validateRendererRuntime'
        validate_runtime()
        report['phase'] = 'buildHost'
        with baseline.private_configuration(config) as (temporary, configuration):
            proxy = temporary / 'bazel-proxy'
            proxy.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(ROOT / 'tools/fork_baseline/baseline.py'), 'bazel-proxy', str(bazel), 'simulator']) + ' "$@"\n')
            proxy.chmod(0o700)
            child = dict(env)
            for key in ('TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'FORK_UNSIGNED_RULES_PATH',
                        'GH_TOKEN', 'GITHUB_TOKEN', 'CHECKOUT_TOKEN', 'ACTIONS_RUNTIME_TOKEN',
                        'P12_PASSWORD', 'APPLE_CERTIFICATE_PASSWORD', 'APPLE_KEYCHAIN_PASSWORD'):
                child.pop(key, None)
            common = [sys.executable, 'build-system/Make/Make.py', '--bazel=' + str(proxy), '--bazelUserRoot=' + str(temporary / 'bazel-root')]
            base = ['--configurationPath=' + str(configuration), '--xcodeManagedCodesigning']
            try:
                baseline.run_redacted(common + ['build'] + base + ['--buildNumber=1', '--configuration=debug_sim_arm64', '--target=' + APP_TARGET], child, secrets)
                # Capture this exact IPA before any test can repoint bazel-bin.
                ipa = evidence / 'CustomeOptionsHost-simulator.ipa'
                shutil.copyfile(ROOT / 'bazel-bin/Tests/CustomeOptionsHost/CustomeOptionsHost.ipa', ipa)
                report['simulator_host'] = inspect_ipa(ipa, 'simulator', BUNDLE)
                report['simulator_host_sha256'] = digest_file(ipa)
                report['phase'] = 'bootSimulator'
                with DisposableRendererSimulator(evidence) as simulator:
                    report['phase'] = 'nativeControllerTests'
                    try: baseline.run_redacted(common + ['test'] + base + ['--target=' + UNIT_TARGET], child, secrets)
                    finally:
                        report['tests'].append(retain_native_logs(temporary / 'bazel-root', UNIT_TARGET, evidence / 'unit-tests', secrets, simulator.identifier))
                    # This is exclusively our fresh synthetic device. Remove the
                    # injected unit-test app before the UI runner installs it.
                    command(['xcrun', 'simctl', 'uninstall', simulator.identifier, BUNDLE], phase='installApplication')
                    report['phase'] = 'nativeInteractionTests'
                    simulator.start_recording()
                    try:
                        baseline.run_redacted(common + ['test'] + base + ['--target=' + UI_TARGET], child, secrets)
                    finally:
                        try: simulator.stop_recording()
                        finally: report['tests'].append(retain_native_logs(temporary / 'bazel-root', UI_TARGET, evidence / 'interaction-tests', secrets, simulator.identifier))
                    report['recording_return_code'] = simulator.recording_return_code
                    report['recording_started_before_ui_scenario'] = simulator.recording_started
                    report['frames'] = frames_from_log((evidence / 'interaction-tests/test.log').read_text())
                    report['appearances'] = appearances_from_log((evidence / 'interaction-tests/test.log').read_text())
                    report['phase'] = 'exportActualScreenshots'
                    outputs = log_directory(temporary / 'bazel-root', UI_TARGET) / 'test.outputs'
                    report['screenshots'] = export_screenshots(outputs, evidence / 'screenshots')
                report['passed'] = True
                report['phase'] = 'complete'
            finally:
                try: subprocess.run([str(bazel), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc', '--output_user_root=' + str(temporary / 'bazel-root'), 'shutdown'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
                except (OSError, subprocess.SubprocessError): pass
    except SmokeError as error:
        report['command_failure'] = error.evidence()
        raise
    except baseline.InvalidSetup:
        report['failure_kind'] = 'nativeBuildOrTestFailed'
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, zipfile.BadZipFile):
        report['failure_kind'] = 'visualOrNativeEvidenceUnavailable'
        raise
    finally:
        (evidence / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        files = sorted(path for path in evidence.rglob('*') if path.is_file() and path.name != 'SHA256SUMS')
        (evidence / 'SHA256SUMS').write_text(''.join(digest_file(path) + '  ' + str(path.relative_to(evidence)) + '\n' for path in files))
    return report


if __name__ == '__main__':
    try:
        result = run(os.environ)
        print(json.dumps({'profile': result['profile'], 'passed': result['passed'], 'logged_in_settings_verified': False}))
    except baseline.InvalidSetup as error:
        print('Renderer qualification blocked: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError):
        print('Renderer qualification failed; bounded evidence is retained.', file=sys.stderr)
        raise SystemExit(1)
