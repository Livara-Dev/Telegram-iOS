"""Capture only the fixed synthetic Projection XCTest products for replay.

The supported rules_apple post_action calls this before its temporary TEST_ROOT
is deleted. It never copies simulator data, logs, launch_options, configurations,
or arbitrary environment values. A captured plan is a diagnostic, not a test pass.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import struct
import tempfile
import zipfile

LABEL = '//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:ChatLocalTextFilterProjectionTests'
BUNDLE = 'org.livara.ayugram.tests.native-security.projection'
APP = 'ProjectionTestHost.app'
TEST = 'ChatLocalTextFilterProjectionTests.xctest'
CAPTURE = 'projection-test-products'
MAX_FILES = 4096
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
FORBIDDEN_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.log', '.ips', '.pem', '.key', '.p12', '.pfx', '.mobileprovision', '.session', '.token', '.authkey'}
FORBIDDEN_NAMES = {'configuration.json', 'launch_options.json', 'build-configuration.json', '.ds_store',
                   'session.json', 'session.plist', 'auth.json', 'keychain.plist', 'tokens.json'}


class InvalidProducts(ValueError):
    pass


def relative_name(value):
    if not isinstance(value, str) or not value or '\\' in value or '\0' in value:
        raise InvalidProducts('Invalid product relative name')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' in path.parts or str(path) != value:
        raise InvalidProducts('Unsafe product relative name')
    if any(not re.fullmatch(r'[A-Za-z0-9_.+@ -]{1,180}', part) for part in path.parts):
        raise InvalidProducts('Unsupported product name')
    return path


def fixed_path(root, relative):
    root = Path(root)
    if root.is_symlink():
        raise InvalidProducts('Product root is a symlink')
    root = root.resolve(strict=True)
    current = root
    for part in relative_name(relative).parts:
        current = current / part
        if current.is_symlink():
            raise InvalidProducts('Product descendant is a symlink')
    if not current.resolve(strict=True).is_relative_to(root):
        raise InvalidProducts('Product escaped its root')
    return current


def read_small(path, limit=2 * 1024 * 1024):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise InvalidProducts('Invalid product metadata file')
        data = os.read(descriptor, limit + 1)
        if len(data) > limit:
            raise InvalidProducts('Product metadata exceeds limit')
        return data
    finally:
        os.close(descriptor)


def verify_binary(path):
    # Identity checks must not materialize a potentially large debug executable.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise InvalidProducts('Missing or oversized product executable')
    finally:
        os.close(descriptor)


def plan_path(value, root):
    if not isinstance(value, str):
        raise InvalidProducts('Missing XCTest product path')
    if value.startswith('__TESTROOT__/'):
        relative = value[len('__TESTROOT__/'):]
    else:
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise InvalidProducts('Unsupported XCTest product path')
        try:
            relative = str(path.resolve(strict=True).relative_to(root))
        except (OSError, ValueError, RuntimeError):
            raise InvalidProducts('XCTest path escaped copied root') from None
        # Accept OS aliases above the approved root (/var on macOS), while
        # rejecting a link at the root boundary or anywhere below it.
        for component in (path, *path.parents):
            if component.is_symlink():
                raise InvalidProducts('XCTest path contains a product symlink')
            if component.resolve(strict=True) == root:
                break
        else:
            raise InvalidProducts('XCTest path lacks the approved root')
    return fixed_path(root, relative), str(relative_name(relative))


def sanitize_plan(root):
    root = Path(root).resolve(strict=True)
    plan = plistlib.loads(read_small(fixed_path(root, 'test.xctestrun')))
    if not isinstance(plan, dict) or set(plan) != {'Runner'} or not isinstance(plan['Runner'], dict):
        raise InvalidProducts('Unexpected XCTest plan')
    runner = plan['Runner']
    host, host_relative = plan_path(runner.get('TestHostPath'), root)
    bundle, bundle_relative = plan_path(runner.get('TestBundlePath'), root)
    if host_relative != APP or bundle_relative != APP + '/PlugIns/' + TEST:
        raise InvalidProducts('Unexpected Projection products')
    if not host.is_dir() or not bundle.is_dir():
        raise InvalidProducts('Missing Projection products')
    host_info = plistlib.loads(read_small(fixed_path(root, APP + '/Info.plist')))
    test_info = plistlib.loads(read_small(fixed_path(root, APP + '/PlugIns/' + TEST + '/Info.plist')))
    if not isinstance(host_info, dict) or host_info.get('CFBundleIdentifier') != BUNDLE or host_info.get('CFBundleExecutable') != 'ProjectionTestHost':
        raise InvalidProducts('Unexpected host identity')
    if not isinstance(test_info, dict) or test_info.get('CFBundleExecutable') != 'ChatLocalTextFilterProjectionTests':
        raise InvalidProducts('Unexpected test bundle identity')
    verify_binary(fixed_path(root, APP + '/ProjectionTestHost'))
    verify_binary(fixed_path(root, APP + '/PlugIns/' + TEST + '/ChatLocalTextFilterProjectionTests'))
    module = runner.get('ProductModuleName')
    if module not in {'ChatLocalTextFilterProjectionTests', 'ChatLocalTextFilterProjectionTestsLib'} or runner.get('IsAppHostedTestBundle') is not True:
        raise InvalidProducts('Unexpected XCTest module or host mode')
    developer = '__PLATFORMS__/iPhoneSimulator.platform/Developer'
    testing = {
        'XCInjectBundleInto': '__TESTHOST__/ProjectionTestHost',
        'DYLD_FRAMEWORK_PATH': '__TESTROOT__:' + developer + '/Library/Frameworks:' + developer + '/Library/PrivateFrameworks',
        'DYLD_INSERT_LIBRARIES': developer + '/usr/lib/libXCTestBundleInject.dylib',
        'DYLD_LIBRARY_PATH': '__TESTROOT__:' + developer + '/usr/lib:',
    }
    # Preserve only the known runner injection contract, never arbitrary launch
    # environment/arguments, Bazel temp paths, API credentials or token values.
    original = runner.get('TestingEnvironmentVariables')
    if not isinstance(original, dict) or any(original.get(key) != value for key, value in testing.items()):
        raise InvalidProducts('Unexpected XCTest injection environment')
    environment = runner.get('EnvironmentVariables', {})
    if not isinstance(environment, dict):
        raise InvalidProducts('Unexpected XCTest environment shape')
    retained = {}
    if environment.get('DYLD_INSERT_LIBRARIES') == '':
        retained['DYLD_INSERT_LIBRARIES'] = ''
    clean = {'ProductModuleName': module, 'TestHostPath': '__TESTROOT__/' + host_relative,
             'TestBundlePath': '__TESTROOT__/' + bundle_relative, 'IsAppHostedTestBundle': True,
             'TestingEnvironmentVariables': testing}
    if retained:
        clean['EnvironmentVariables'] = retained
    return {'Runner': clean}


def allowed_product(relative):
    path = relative_name(relative)
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.lower() in FORBIDDEN_NAMES:
        raise InvalidProducts('Nonproduct file is forbidden')
    if any(part in {'Documents', 'Library', 'Caches', 'Accounts', 'account-data'} for part in path.parts):
        raise InvalidProducts('Runtime data is forbidden')
    return path


def code_product_name(relative):
    path = relative_name(relative)
    if relative in {'TEST_ROOT/' + APP + '/ProjectionTestHost',
                    'TEST_ROOT/' + APP + '/PlugIns/' + TEST + '/ChatLocalTextFilterProjectionTests'}:
        return True
    if not relative.startswith('TEST_ROOT/' + APP + '/') or 'Frameworks' not in path.parts:
        return False
    return (path.parent.name == path.name + '.framework' or
            path.parent.name == 'Frameworks' and re.fullmatch(r'lib[A-Za-z0-9_+.-]+\.dylib', path.name) is not None)


def simulator_macho(read_at, size):
    """Recognize bounded thin ARM64 simulator code, never just a magic prefix.

    Fat files are intentionally not exempt: the captured products target only
    ARM64. This is a format check, not a code-signature or runtime assertion.
    """
    try:
        header = read_at(0, 32)
        if len(header) != 32 or header[:4] != b'\xcf\xfa\xed\xfe':
            return False
        _, cpu, _, kind, count, command_bytes, _, _ = struct.unpack('<8I', header)
        if (cpu != 0x0100000C or kind not in {2, 6, 8} or not 1 <= count <= 4096 or
                not count * 8 <= command_bytes <= min(1024 * 1024, size - 32)):
            return False
        commands = read_at(32, command_bytes)
        if len(commands) != command_bytes:
            return False
        offset, platforms, executable_segments, entrypoints, dylib_ids = 0, [], 0, 0, 0
        for _ in range(count):
            if offset + 8 > len(commands):
                return False
            command, length = struct.unpack_from('<II', commands, offset)
            if length < 8 or length % 8 or offset + length > len(commands):
                return False
            if command == 0x32:  # LC_BUILD_VERSION
                if length < 24:
                    return False
                platforms.append(struct.unpack_from('<I', commands, offset + 8)[0])
            elif command == 0x19:  # LC_SEGMENT_64
                if length < 72:
                    return False
                segment = struct.unpack_from('<16s4Q4I', commands, offset + 8)
                _, _, _, file_offset, file_size, _, protection, sections, _ = segment
                if length != 72 + sections * 80 or file_offset > size or file_size > size - file_offset:
                    return False
                if protection & 4 and file_size:
                    executable_segments += 1
                for index in range(sections):
                    section = struct.unpack_from('<16s16s2Q8I', commands, offset + 72 + index * 80)
                    section_size, section_offset, flags = section[3], section[4], section[8]
                    if flags & 0xFF not in {1, 0xC, 0x12} and (section_offset < file_offset or section_offset > file_offset + file_size or section_size > file_offset + file_size - section_offset):
                        return False
            elif command == 0x80000028:  # LC_MAIN
                if length != 24 or struct.unpack_from('<Q', commands, offset + 8)[0] >= size:
                    return False
                entrypoints += 1
            elif command == 0xD:  # LC_ID_DYLIB
                if length < 24:
                    return False
                dylib_ids += 1
            offset += length
        return (offset == command_bytes and platforms == [7] and executable_segments > 0 and
                (kind != 2 or entrypoints == 1) and (kind != 6 or dylib_ids == 1))
    except (OSError, ValueError, TypeError, struct.error):
        return False


def credential_needles(secrets, application_credentials, exempt_api_config):
    values = tuple(secrets) + (() if exempt_api_config else tuple(application_credentials))
    return tuple(value.encode() if isinstance(value, str) else value for value in values if value)


def copy_file(source, destination, secrets=(), application_credentials=(), code_candidate=False):
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, tail, copied = hashlib.sha256(), b'', 0
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_FILE_BYTES:
            raise InvalidProducts('Nonregular or oversized product')
        exempt_api = code_candidate and simulator_macho(lambda offset, count: os.pread(descriptor, count, offset), info.st_size)
        needles = credential_needles(secrets, application_credentials, exempt_api)
        keep = max((len(value) for value in needles), default=1) - 1
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with destination.open('xb') as output:
            while data := os.read(descriptor, 1024 * 1024):
                copied += len(data)
                if copied > MAX_FILE_BYTES or any(value in tail + data for value in needles):
                    raise InvalidProducts('Product size or credential check failed')
                tail = (tail + data)[-keep:] if keep else b''
                digest.update(data); output.write(data)
        destination.chmod(0o700 if info.st_mode & 0o111 else 0o600)
        return copied, digest.hexdigest()
    finally:
        os.close(descriptor)


def capture_products(test_root, destination):
    root, destination = Path(test_root), Path(destination)
    if root.is_symlink() or destination.exists() or destination.is_symlink():
        raise InvalidProducts('Capture roots must be fresh regular directories')
    root = root.resolve(strict=True)
    clean = sanitize_plan(root)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise InvalidProducts('Capture destination is unsafe')
    with tempfile.TemporaryDirectory(prefix='.projection-capture-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'products'; stage.mkdir(mode=0o700)
        files, total = {}, 0
        host = fixed_path(root, APP)
        for directory, directories, names in os.walk(host, followlinks=False):
            for name in directories:
                if (Path(directory) / name).is_symlink():
                    raise InvalidProducts('Product directory symlink is forbidden')
            for name in names:
                source = Path(directory) / name
                relative = str(source.relative_to(root)); allowed_product(relative)
                source = fixed_path(root, relative)
                count, digest = copy_file(source, stage / 'TEST_ROOT' / relative)
                files['TEST_ROOT/' + relative] = digest; total += count
                if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise InvalidProducts('Product tree exceeds limit')
        plan_data = plistlib.dumps(clean, fmt=plistlib.FMT_XML, sort_keys=True)
        (stage / 'TEST_ROOT/test.xctestrun').write_bytes(plan_data)
        files['TEST_ROOT/test.xctestrun'] = hashlib.sha256(plan_data).hexdigest()
        report = {'schema_version': 1, 'target': LABEL, 'synthetic_host_identity_validated': True,
                  'xctest_plan_sanitized': True, 'diagnostic_products_not_test_pass': True,
                  'files': files, 'total_product_bytes': total}
        (stage / 'capture.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        stage.rename(destination)
    return report


def post_action(env):
    """Supported rules_apple post action, preserving every original test status."""
    report = {'state': 'unavailable', 'phase': 'validateInvocation', 'diagnostic_products_not_test_pass': True}
    try:
        code = env.get('TEST_EXIT_CODE', '')
        if not re.fullmatch(r'[0-9]{1,3}', code):
            return report
        if code == '0':
            return {**report, 'state': 'notNeeded', 'phase': 'complete'}
        report['phase'] = 'validateDirectories'
        root = Path(env['TEST_TMPDIR'])
        output = Path(env['TEST_UNDECLARED_OUTPUTS_DIR'])
        if not root.is_absolute() or not output.is_absolute() or root.is_symlink() or output.is_symlink():
            raise InvalidProducts('Invalid declared test directories')
        root, output = root.resolve(strict=True), output.resolve(strict=True)
        candidates = list(root.glob('test_runner_work_dir.*/TEST_ROOT'))
        report['phase'] = 'locateProducts'
        report['candidate_count'] = len(candidates)
        if len(candidates) > 16:
            raise InvalidProducts('Unexpected runner directory count')
        matching = []
        for candidate in candidates:
            if candidate.parent.is_symlink() or candidate.is_symlink():
                continue
            try:
                sanitize_plan(candidate)
                matching.append(candidate)
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if len(matching) != 1:
            raise InvalidProducts('Missing unique Projection test root')
        report['phase'] = 'captureProducts'
        capture_products(matching[0], output / CAPTURE)
        report['state'] = 'captured'
        report['phase'] = 'complete'
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        pass
    return report


def retain_captured(root, bazel_root, source_commit, toolchain, secrets=(), application_credentials=()):
    """Retain only this fixed undeclared-output subtree after Bazel completes."""
    root, bazel_root = Path(root), Path(bazel_root).resolve(strict=True)
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit) or not isinstance(toolchain, dict):
        raise InvalidProducts('Invalid capture provenance')
    destination = root / 'build/fork-evidence' / CAPTURE
    for path in (root / 'build', destination.parent, destination):
        if path.is_symlink():
            raise InvalidProducts('Unsafe evidence destination')
    if destination.exists():
        raise InvalidProducts('Product evidence already exists')
    logs = (root / 'bazel-testlogs').resolve(strict=True)
    if not logs.is_relative_to(bazel_root):
        raise InvalidProducts('Test logs escaped private Bazel root')
    outputs = fixed_path(logs, 'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/ChatLocalTextFilterProjectionTests/test.outputs')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.projection-retain-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'products'; stage.mkdir(mode=0o700)
        direct = outputs / CAPTURE
        archive = outputs / 'outputs.zip'
        if direct.is_dir() and not direct.is_symlink():
            count, total = 0, 0
            for directory, directories, names in os.walk(direct, followlinks=False):
                if any((Path(directory) / name).is_symlink() for name in directories):
                    raise InvalidProducts('Captured directory symlink')
                for name in names:
                    source = Path(directory) / name
                    relative = str(source.relative_to(direct)); relative_name(relative)
                    size, _ = copy_file(fixed_path(direct, relative), stage / relative, secrets=secrets,
                                        application_credentials=application_credentials,
                                        code_candidate=code_product_name(relative))
                    count += 1; total += size
                    if count > MAX_FILES + 2 or total > MAX_TOTAL_BYTES:
                        raise InvalidProducts('Captured products exceed retention limit')
        elif archive.is_file() and not archive.is_symlink():
            with zipfile.ZipFile(archive) as zipped:
                selected = [entry for entry in zipped.infolist() if entry.filename.startswith(CAPTURE + '/') and not entry.is_dir()]
                if not 0 < len(selected) <= MAX_FILES + 2 or sum(entry.file_size for entry in selected) > MAX_TOTAL_BYTES:
                    raise InvalidProducts('Invalid captured archive size')
                names = set()
                for entry in selected:
                    relative = entry.filename[len(CAPTURE) + 1:]; relative_name(relative)
                    if relative in names or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG} or entry.file_size > MAX_FILE_BYTES:
                        raise InvalidProducts('Unsafe captured archive entry')
                    names.add(relative)
                # Validate the entire inventory before extracting any member.
                for entry in selected:
                    relative = entry.filename[len(CAPTURE) + 1:]
                    target = stage / relative; target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with zipped.open(entry) as source, target.open('xb') as output:
                        copied, tail = 0, b''
                        def read_at(offset, count):
                            source.seek(offset)
                            return source.read(count)
                        exempt_api = code_product_name(relative) and simulator_macho(read_at, entry.file_size)
                        source.seek(0)
                        needles = credential_needles(secrets, application_credentials, exempt_api)
                        keep = max((len(value) for value in needles), default=1) - 1
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > MAX_FILE_BYTES or any(value in tail + chunk for value in needles):
                                raise InvalidProducts('Captured archive credential or size check failed')
                            tail = (tail + chunk)[-keep:] if keep else b''
                            output.write(chunk)
                    target.chmod(0o700 if entry.external_attr >> 16 & 0o111 else 0o600)
        else:
            raise InvalidProducts('Captured Projection products unavailable')
        report = json.loads(read_small(stage / 'capture.json'))
        if (not isinstance(report, dict) or report.get('schema_version') != 1 or report.get('target') != LABEL or
                report.get('synthetic_host_identity_validated') is not True or report.get('xctest_plan_sanitized') is not True or
                not isinstance(report.get('files'), dict)):
            raise InvalidProducts('Invalid product manifest')
        actual = {str(path.relative_to(stage)): path for path in stage.rglob('*') if path.is_file()}
        if set(actual) != set(report['files']) | {'capture.json'} or len(actual) > MAX_FILES + 2:
            raise InvalidProducts('Unexpected retained files')
        total = 0
        for name, expected in report['files'].items():
            relative_name(name)
            if not name.startswith('TEST_ROOT/'):
                raise InvalidProducts('Unexpected captured product location')
            if name != 'TEST_ROOT/test.xctestrun':
                allowed_product(name[len('TEST_ROOT/'):])
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise InvalidProducts('Invalid product digest')
            path = actual[name]; total += path.stat().st_size
            with path.open('rb') as file:
                digest = hashlib.sha256()
                while chunk := file.read(1024 * 1024): digest.update(chunk)
            if digest.hexdigest() != expected or total > MAX_TOTAL_BYTES:
                raise InvalidProducts('Captured product digest mismatch')
        sanitized = sanitize_plan(stage / 'TEST_ROOT')
        if plistlib.loads(read_small(stage / 'TEST_ROOT/test.xctestrun')) != sanitized:
            raise InvalidProducts('Captured XCTest plan changed')
        manifest = {'schema_version': 1, 'source_commit': source_commit, 'test_target': LABEL,
                    'toolchain': toolchain, 'application_recompiled_for_replay': False,
                    'diagnostic_products_not_test_pass': True, 'physical_device_verified': False,
                    'files': {**report['files'], 'capture.json': hashlib.sha256((stage / 'capture.json').read_bytes()).hexdigest()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        hashes = {**manifest['files'], 'manifest.json': hashlib.sha256((stage / 'manifest.json').read_bytes()).hexdigest()}
        (stage / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in sorted(hashes.items())))
        stage.rename(destination)
    return {'state': 'retained', 'file_count': len(hashes), 'diagnostic_products_not_test_pass': True}


if __name__ == '__main__':
    print(json.dumps(post_action(os.environ), sort_keys=True))
    # Diagnostics can never replace success or the original failure status.
