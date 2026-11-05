#!/usr/bin/env python3
"""Replay the published simulator app without App Groups; never compile app code.

The original package is tested first. If it already lacks App Groups, that
private-storage control is sufficient and no duplicate variant is run. Otherwise
only the extracted main app is re-signed ad hoc. Entitlements and codesign output
stay in a private temporary directory, never in retained evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import stat
import subprocess
import tempfile
import zipfile

from ipa_inspect import inspect_ipa
from launch_smoke import BUNDLE, extract_app, smoke

GROUPS = 'com.apple.security.application-groups'
LIMIT = 1024 * 1024


class ReplayError(RuntimeError):
    pass


def parse_entitlements(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= LIMIT:
        raise ReplayError('invalid_entitlements')
    try:
        value = plistlib.loads(data)
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ValueError()
        for key in (GROUPS, 'keychain-access-groups'):
            if key in value and (not isinstance(value[key], list) or
                                 not all(isinstance(item, str) and item for item in value[key])):
                raise ValueError()
        if 'application-identifier' in value and not isinstance(value['application-identifier'], str):
            raise ValueError()
        plistlib.dumps(value)  # Reject unsupported/null property-list values.
        return value
    except (ValueError, TypeError, OverflowError):
        raise ReplayError('invalid_entitlements') from None


def filtered_entitlements(value):
    try:
        checked = parse_entitlements(plistlib.dumps(value))
    except (ValueError, TypeError, OverflowError):
        raise ReplayError('invalid_entitlements') from None
    checked.pop(GROUPS, None)
    return checked


def private_command(arguments, directory, label, run=subprocess.run):
    output, errors = Path(directory) / (label + '.out'), Path(directory) / (label + '.err')
    try:
        with output.open('xb') as stdout, errors.open('xb') as stderr:
            output.chmod(0o600); errors.chmod(0o600)
            result = run(arguments, stdout=stdout, stderr=stderr, timeout=30, check=False)
        if result.returncode or output.stat().st_size > LIMIT or errors.stat().st_size > LIMIT:
            raise ReplayError('codesign_failed')
        return output.read_bytes()
    except subprocess.SubprocessError:
        raise ReplayError('codesign_failed') from None


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024): result.update(chunk)
    return result.hexdigest()


def framework_snapshot(app):
    root = Path(app) / 'Frameworks'
    if not root.exists(): return {}
    if root.is_symlink() or not root.is_dir(): raise ReplayError('invalid_frameworks')
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ReplayError('invalid_frameworks')
        if stat.S_ISREG(mode): result[str(path.relative_to(root))] = digest(path)
    return result


def resign_main(app, directory, run=subprocess.run):
    app = Path(app)
    if app.is_symlink() or not app.is_dir() or (app / 'Info.plist').is_symlink():
        raise ReplayError('invalid_main_bundle')
    try:
        if (app / 'Info.plist').stat().st_size > LIMIT:
            raise ValueError()
        info = plistlib.loads((app / 'Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != BUNDLE or info.get('CFBundleSupportedPlatforms') != ['iPhoneSimulator']:
            raise ValueError()
        executable = info.get('CFBundleExecutable')
        if (not isinstance(executable, str) or executable in {'.', '..'} or
                '/' in executable or '\\' in executable or
                (app / executable).is_symlink() or not (app / executable).is_file()):
            raise ValueError()
    except (ValueError, AttributeError):
        raise ReplayError('invalid_main_bundle') from None
    before_info = digest(app / 'Info.plist')
    frameworks = framework_snapshot(app)
    original = parse_entitlements(private_command(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)], directory, 'original-entitlements', run))
    filtered = filtered_entitlements(original)
    entitlements = Path(directory) / 'filtered-entitlements.plist'
    with entitlements.open('xb') as out:
        entitlements.chmod(0o600)
        out.write(plistlib.dumps(filtered))
    private_command(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
                     '--preserve-metadata=identifier,requirements,flags', '--entitlements', str(entitlements), str(app)], directory, 'resign-main', run)
    observed = parse_entitlements(private_command(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)], directory, 'resigned-entitlements', run))
    if observed != filtered or GROUPS in observed:
        raise ReplayError('resigned_entitlements_mismatch')
    if digest(app / 'Info.plist') != before_info or framework_snapshot(app) != frameworks:
        raise ReplayError('non_target_bundle_data_changed')
    private_command(['/usr/bin/codesign', '--verify', '--strict', str(app)], directory, 'verify-main', run)
    return {'app_group_entitlement_present_before': GROUPS in original,
            'app_group_entitlement_absent_after': True, 'other_entitlements_preserved': True,
            'framework_files_unchanged': len(frameworks), 'main_bundle_resigned_ad_hoc': True}


def repack_app(app, destination):
    app = Path(app)
    if app.is_symlink() or not app.is_dir(): raise ReplayError('invalid_main_bundle')
    total = 0
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app.rglob('*')):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ReplayError('unsafe_repack_entry')
            if stat.S_ISREG(mode):
                total += path.stat().st_size
                if total > 2 * 1024**3: raise ReplayError('repack_limit_exceeded')
                archive.write(path, 'Payload/Telegram.app/' + path.relative_to(app).as_posix())
    inspect_ipa(destination, 'simulator', BUNDLE)


def replay(ipa, output):
    if platform.system() != 'Darwin': raise ReplayError('macos_simulator_required')
    ipa, output = Path(ipa), Path(output)
    if ipa.is_symlink() or not ipa.is_file() or output.is_symlink(): raise ReplayError('invalid_replay_paths')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'mode': 'without_app_groups', 'physical_device_verified': False,
              'application_recompiled': False, 'passed': False, 'normal_control_passed': False,
              'no_app_groups_control_attempted': False, 'source_codesign_verified': False,
              'phase': 'extractApplication'}
    try:
        inspect_ipa(ipa, 'simulator', BUNDLE)
        report['source_simulator_ipa_sha256'] = digest(ipa)
        with tempfile.TemporaryDirectory(prefix='ayu-startup-replay-') as directory:
            app = extract_app(ipa, Path(directory) / 'Telegram.app')
            report['phase'] = 'verifyCodeSignature'
            private_command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                            directory, 'verify-source')
            report['source_codesign_verified'] = True
            report['phase'] = 'inspectEntitlements'
            source_entitlements = parse_entitlements(private_command(
                ['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)],
                directory, 'source-entitlements'))
            has_groups = GROUPS in source_entitlements
            report['app_group_entitlement_present_before'] = has_groups
            report['phase'] = 'normalControl'
            normal = smoke(ipa, output / 'normal/launch-smoke',
                           expected_storage_kind=None if has_groups else 'private')
            if not normal.get('passed'): raise ReplayError('normal_control_failed')
            report['normal_control_passed'] = True
            if has_groups:
                report['phase'] = 'withoutAppGroupsControl'
                report['no_app_groups_control_attempted'] = True
                report.update(resign_main(app, directory))
                repacked = Path(directory) / 'without-app-groups-simulator.ipa'
                repack_app(app, repacked)
                launched = smoke(repacked, output / 'no-app-groups/launch-smoke', expected_storage_kind='private')
                report['passed'] = launched['passed']
                report['app_group_control'] = 'strippedVariant'
            else:
                # The published app already exercises the desired capability
                # absence. Re-signing it and repeating identical tests adds no
                # independent evidence and risks obscuring the original failure.
                report['app_group_control'] = 'coveredByNormal'
                report['main_bundle_resigned_ad_hoc'] = False
                report['app_group_entitlement_absent_after'] = True
                report['passed'] = True
            if digest(ipa) != report['source_simulator_ipa_sha256']:
                raise ReplayError('source_package_changed')
            report['source_ipa_unchanged'] = True
            report['phase'] = 'complete'
    finally:
        (output / 'replay.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    try: result = replay(arguments.ipa, arguments.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({'mode': 'without_app_groups', 'passed': False, 'error': 'startup_replay_failed', 'physical_device_verified': False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
