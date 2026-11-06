# SPDX-License-Identifier: GPL-2.0-or-later
# Private frozen functions from the audited repository; no network or app launch entrypoint.
import hashlib, plistlib, stat, struct, subprocess, shutil, zipfile
from pathlib import Path, PurePosixPath
from ipa_inspect import inspect_ipa
BUNDLE = "org.livara.ayugram"
GROUPS = "com.apple.security.application-groups"
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


def extract_app(ipa, destination):
    inspect_ipa(ipa, 'simulator', BUNDLE)
    destination = Path(destination)
    with zipfile.ZipFile(ipa) as archive:
        infos = [n for n in archive.namelist() if n.startswith('Payload/') and n.endswith('.app/Info.plist') and n.count('/') == 2]
        if len(infos) != 1:
            raise ValueError('Expected one main application')
        prefix = infos[0][:-len('Info.plist')]
        total = 0
        for entry in archive.infolist():
            if not entry.filename.startswith(prefix):
                continue
            relative = PurePosixPath(entry.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename:
                raise ValueError('Unsafe bundle path')
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError('Unsupported bundle entry type')
            total += entry.file_size
            if total > 2 * 1024**3:
                raise ValueError('Bundle extraction limit exceeded')
            target = destination.joinpath(*relative.parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('xb') as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                target.chmod(0o700 if mode & 0o111 else 0o600)
        info = plistlib.loads((destination / 'Info.plist').read_bytes())
        binary = info['CFBundleExecutable']
        if not isinstance(binary, str) or '/' in binary or binary in {'.', '..'}:
            raise ValueError('Invalid application executable')
        (destination / binary).chmod(0o700)
    return destination
