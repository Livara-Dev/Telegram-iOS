#!/usr/bin/env python3
"""Replay a published simulator app with bounded controls; never compile app code.

The original package is tested first. If it already lacks App Groups, that
private-storage control is sufficient and no duplicate variant is run. Otherwise
only the extracted main app is re-signed ad hoc. Entitlements and codesign output
stay in a private temporary directory, never in retained evidence.
The explicit main-app-only variant removes extensions and preserves entitlements;
it runs that control alone, without repeating a known original installation hang.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile

from ipa_inspect import inspect_ipa
from launch_smoke import BUNDLE, READY, SmokeError, extract_app, smoke
from replay_artifact import prior_smoke_report
from storage_evidence import valid_group

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


def resign_main(app, directory, run=subprocess.run, remove_groups=True):
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
    filtered = filtered_entitlements(original) if remove_groups else original
    entitlements = Path(directory) / 'filtered-entitlements.plist'
    with entitlements.open('xb') as out:
        entitlements.chmod(0o600)
        out.write(plistlib.dumps(filtered))
    private_command(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
                     '--preserve-metadata=identifier,requirements,flags', '--entitlements', str(entitlements), str(app)], directory, 'resign-main', run)
    observed = parse_entitlements(private_command(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(app)], directory, 'resigned-entitlements', run))
    if observed != filtered or (remove_groups and GROUPS in observed):
        raise ReplayError('resigned_entitlements_mismatch')
    if digest(app / 'Info.plist') != before_info or framework_snapshot(app) != frameworks:
        raise ReplayError('non_target_bundle_data_changed')
    private_command(['/usr/bin/codesign', '--verify', '--strict', str(app)], directory, 'verify-main', run)
    return {'app_group_entitlement_present_before': GROUPS in original,
            'app_group_entitlement_absent_after': GROUPS not in observed, 'other_entitlements_preserved': True,
            'framework_files_unchanged': len(frameworks), 'main_bundle_resigned_ad_hoc': True}


def main_code_digest(app):
    """Hash all executable bytes before its signature, normalizing signing sizes.

    Re-signing may resize the signature and its __LINKEDIT segment. No code,
    resource, signature offset, other load command, or pre-signature byte may
    change. A signed thin ARM64 simulator executable is required.
    """
    info = plistlib.loads((Path(app) / 'Info.plist').read_bytes())
    name = info.get('CFBundleExecutable')
    if not isinstance(name, str) or '/' in name or '\\' in name or name in {'.', '..'}:
        raise ReplayError('invalid_main_bundle')
    path = Path(app) / name
    if path.is_symlink() or not path.is_file() or not 32 <= path.stat().st_size <= 512 * 1024**2:
        raise ReplayError('invalid_main_executable')
    value = bytearray(path.read_bytes())
    magic, cpu, _, kind, count, size, _, _ = struct.unpack_from('<8I', value)
    if magic != 0xfeedfacf or cpu != 0x0100000c or kind != 2 or not 1 <= count <= 4096 or not count * 8 <= size <= min(1024**2, len(value) - 32):
        raise ReplayError('invalid_main_executable')
    offset, signature = 32, None
    for _ in range(count):
        if offset + 8 > 32 + size: raise ReplayError('invalid_main_executable')
        command, length = struct.unpack_from('<II', value, offset)
        if length < 8 or length % 8 or offset + length > 32 + size:
            raise ReplayError('invalid_main_executable')
        if command == 0x1d:  # LC_CODE_SIGNATURE
            if signature is not None or length != 16: raise ReplayError('invalid_main_executable')
            start, length_signature = struct.unpack_from('<II', value, offset + 8)
            if start < 32 + size or not 0 < length_signature <= len(value) - start:
                raise ReplayError('invalid_main_executable')
            signature = start
            value[offset + 12:offset + 16] = b'\0' * 4
        if command == 0x19:  # LC_SEGMENT_64; only signature-dependent sizes.
            if length < 72: raise ReplayError('invalid_main_executable')
            if bytes(value[offset + 8:offset + 24]).rstrip(b'\0') == b'__LINKEDIT':
                value[offset + 32:offset + 40] = b'\0' * 8
                value[offset + 48:offset + 56] = b'\0' * 8
        offset += length
    if offset != 32 + size or signature is None:
        raise ReplayError('invalid_main_executable')
    return hashlib.sha256(value[:signature]).hexdigest()


def main_app_only(app, directory, run=subprocess.run):
    """Remove only the immediate extension directory and re-seal the main app."""
    app = Path(app)
    plugins = app / 'PlugIns'
    if app.is_symlink() or not app.is_dir() or plugins.is_symlink() or not plugins.is_dir():
        raise ReplayError('invalid_extension_directory')
    children = list(plugins.iterdir())
    if not children or any(child.is_symlink() or not child.is_dir() or child.suffix != '.appex' for child in children):
        raise ReplayError('invalid_extension_directory')
    # Reject every unusual nested entry before recursive deletion.
    for path in plugins.rglob('*'):
        if not (stat.S_ISREG(path.lstat().st_mode) or stat.S_ISDIR(path.lstat().st_mode)):
            raise ReplayError('invalid_extension_directory')
    before_code, frameworks = main_code_digest(app), framework_snapshot(app)
    executable = plistlib.loads((app / 'Info.plist').read_bytes())['CFBundleExecutable']
    retained = {p.relative_to(app).as_posix(): digest(p) for p in app.rglob('*')
                if p.is_file() and not p.is_symlink() and 'PlugIns' != p.relative_to(app).parts[0]
                and '_CodeSignature' != p.relative_to(app).parts[0]
                and p.relative_to(app).as_posix() != executable}
    shutil.rmtree(plugins)
    result = resign_main(app, directory, run=run, remove_groups=False)
    if main_code_digest(app) != before_code or framework_snapshot(app) != frameworks:
        raise ReplayError('main_code_or_frameworks_changed')
    if any(not (app / path).is_file() or digest(app / path) != expected for path, expected in retained.items()):
        raise ReplayError('non_target_bundle_data_changed')
    private_command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], directory, 'verify-main-only', run)
    return {**result, 'extensions_removed': len(children), 'main_code_unchanged': True,
            'retained_resource_files_unchanged': len(retained), 'framework_files_unchanged': len(frameworks)}


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


def automatic_group_failure(error, result_path):
    """Only a valid observed group selection admits a fresh pinned control."""
    if not isinstance(error, SmokeError) or error.code != 'invalidStorageEvidence' or error.phase != 'checkStorageSelection':
        return False
    path = Path(result_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536: return False
    try:
        result = prior_smoke_report(json.loads(path.read_bytes()))
    except (OSError, ValueError, RuntimeError):
        return False
    failure = result.get('failure')
    if (result['passed'] or result.get('storage_test_setup') != 'freshAutomatic' or
            result.get('expected_storage_kind') != 'private' or result.get('phase') != 'checkStorageSelection' or
            not isinstance(failure, dict) or failure.get('code') != 'invalidStorageEvidence' or len(result['launches']) != 1):
        return False
    launch = result['launches'][0]
    return (launch['marker']['stage'] in READY and launch['marker']['failure'] is None and
            launch.get('storage_kind') == 'group' and valid_group(launch.get('storage_evidence')))


def replay(ipa, output, startup_variant='original'):
    if platform.system() != 'Darwin': raise ReplayError('macos_simulator_required')
    if startup_variant not in {'original', 'main-app-only'}: raise ReplayError('invalid_startup_variant')
    ipa, output = Path(ipa), Path(output)
    if ipa.is_symlink() or not ipa.is_file() or output.is_symlink(): raise ReplayError('invalid_replay_paths')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {'mode': 'main_app_only' if startup_variant == 'main-app-only' else 'without_app_groups', 'physical_device_verified': False,
              'startup_variant': startup_variant,
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
            if startup_variant == 'main-app-only':
                # Do not repeat the already-observed original installation hang.
                # Preserve all entitlements: this control varies extensions only.
                if has_groups: raise ReplayError('private_storage_control_requires_absent_app_groups')
                report['phase'] = 'prepareMainAppOnly'
                report.update(main_app_only(app, directory))
                repacked = Path(directory) / 'main-app-only-simulator.ipa'
                repack_app(app, repacked)
                report['phase'] = 'mainAppOnlyControl'
                report['automatic_fallback_verified'] = False
                report['automatic_group_selection_observed'] = False
                report['pinned_private_control_attempted'] = False
                report['private_mode_control'] = 'automaticSelection'
                try:
                    launched = smoke(repacked, output / 'main-app-only/launch-smoke', expected_storage_kind='private')
                    if not launched.get('passed'): raise ReplayError('main_app_only_control_failed')
                    report['automatic_storage_kind'] = 'private'
                    report['automatic_fallback_verified'] = True
                except SmokeError as error:
                    if not automatic_group_failure(error, output / 'main-app-only/launch-smoke/result.json'): raise
                    # The simulator can expose its embedded group even when the
                    # code signature lacks that capability. Preserve that result
                    # and vary only the saved selection in a separate new device.
                    (output / 'main-app-only').rename(output / 'main-app-only-automatic')
                    report['automatic_storage_kind'] = 'group'
                    report['automatic_group_selection_observed'] = True
                    report['pinned_private_control_attempted'] = True
                    report['private_mode_control'] = 'freshPinnedSelection'
                    report['phase'] = 'pinnedPrivateControl'
                    launched = smoke(repacked, output / 'main-app-only/launch-smoke',
                                     expected_storage_kind='private', seed_private_storage=True)
                if not launched.get('passed'): raise ReplayError('main_app_only_control_failed')
                report['main_app_only_control_passed'] = True
                if digest(ipa) != report['source_simulator_ipa_sha256']: raise ReplayError('source_package_changed')
                report['source_ipa_unchanged'] = True
                report['passed'] = True
                report['phase'] = 'complete'
                return report
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
    parser.add_argument('--startup-variant', choices=['original', 'main-app-only'], default='original')
    arguments = parser.parse_args()
    try: result = replay(arguments.ipa, arguments.output, arguments.startup_variant)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({'mode': 'main_app_only' if arguments.startup_variant == 'main-app-only' else 'without_app_groups',
                          'passed': False, 'error': 'startup_replay_failed', 'physical_device_verified': False}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
