# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Bounded static unsigned-device checks; never a signing or runtime proof.

Resolve only Mach-O load commands against the actual IPA member paths. Apple
system-library availability, dynamic/dlopen names, provisioning and physical
startup remain unverified. No archive is extracted and no code is executed.
"""
import os
from pathlib import PurePosixPath
import plistlib
import posixpath
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from xml.parsers.expat import ExpatError

from ipa_inspect import InvalidIPA, inspect_ipa

MAX_ARCHIVE_BYTES = 1024**3
MAX_MEMBERS = 20000
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_FILE_BYTES = 512 * 1024**2
MAX_PLIST_BYTES = 1024**2
MAX_COMMAND_BYTES = 1024**2
MAX_IMAGES = 256
MAX_RPATHS = 256
MAX_STATES = 4096
MAX_EDGES = 20000
MAX_PATH_DEPTH = 64
MAX_LOAD_DEPTH = 64
MAX_PATH_BYTES = 4096
LOAD_COMMANDS = frozenset({0xC, 0x80000018, 0x8000001F, 0x80000023, 0x20})
OTHER_MACHO = frozenset({b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
                         b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'})


def _path(name, directory=False):
    if not isinstance(name, str): raise InvalidIPA('Invalid ZIP member path')
    value = name[:-1] if directory and name.endswith('/') else name
    parts = value.split('/')
    if (not value or len(value.encode('utf-8')) > MAX_PATH_BYTES or len(parts) > MAX_PATH_DEPTH
            or value.startswith('/') or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in {'', '.', '..'} for part in parts)):
        raise InvalidIPA('Unsafe ZIP member path')
    return value


def _key(name):
    # iOS/macOS member aliases must not overwrite one another on extraction.
    return unicodedata.normalize('NFC', name).casefold()


def _read(stream, count):
    data = stream.read(count)
    if len(data) != count: raise InvalidIPA('Truncated unsigned device executable')
    return data


def _string(commands, position, length, minimum):
    if length < minimum: raise InvalidIPA('Invalid dynamic-loader command')
    offset = struct.unpack_from('<I', commands, position + 8)[0]
    if not minimum <= offset < length: raise InvalidIPA('Invalid dynamic-loader string offset')
    raw = commands[position + offset:position + length]
    terminator = raw.find(b'\0')
    if not 0 < terminator <= MAX_PATH_BYTES: raise InvalidIPA('Invalid dynamic-loader string')
    value = raw[:terminator].decode('utf-8')
    if '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InvalidIPA('Unsafe dynamic-loader string')
    return value


def _macho(stream, size):
    magic = stream.read(4)
    if magic in OTHER_MACHO: raise InvalidIPA('Expected only thin ARM64 device Mach-O images')
    if magic != b'\xcf\xfa\xed\xfe': return None
    header = magic + _read(stream, 28)
    _, cpu, _, kind, count, length, _, _ = struct.unpack('<8I', header)
    if (cpu != 0x0100000C or kind not in {2, 6, 8} or not 1 <= count <= 4096
            or not count * 8 <= length <= min(MAX_COMMAND_BYTES, size - 32)):
        raise InvalidIPA('Invalid unsigned device Mach-O header')
    commands = _read(stream, length)
    position, platform, rpaths, loads = 0, None, [], []
    for _ in range(count):
        if position + 8 > len(commands): raise InvalidIPA('Truncated device load command')
        code, command_length = struct.unpack_from('<II', commands, position)
        if command_length < 8 or command_length % 8 or position + command_length > len(commands):
            raise InvalidIPA('Invalid device load command')
        if code == 0x1D: raise InvalidIPA('Device Mach-O contains a code signature')
        if code == 0x32:
            if command_length < 24 or platform is not None: raise InvalidIPA('Ambiguous device build platform')
            platform = struct.unpack_from('<I', commands, position + 8)[0]
            tools = struct.unpack_from('<I', commands, position + 20)[0]
            if command_length != 24 + tools * 8: raise InvalidIPA('Invalid device build-version command')
        elif code == 0x8000001C:
            rpaths.append(_string(commands, position, command_length, 12))
            if len(rpaths) > MAX_RPATHS: raise InvalidIPA('Device run-path limit exceeded')
        elif code in LOAD_COMMANDS:
            loads.append(_string(commands, position, command_length, 24))
        elif code == 0xD:
            _string(commands, position, command_length, 24)  # Validate LC_ID_DYLIB too.
        position += command_length
    if position != len(commands) or platform != 2:
        raise InvalidIPA('Embedded Mach-O is not explicitly iPhoneOS')
    return {'kind': kind, 'rpaths': tuple(rpaths), 'loads': tuple(loads)}


def _system(name):
    return name.startswith(('/System/Library/', '/usr/lib/'))


def _canonical_system(name):
    return _system(name) and posixpath.normpath(name) == name and not name.endswith('/')


def _inside(name, root):
    if name != root and not name.startswith(root + '/'):
        raise InvalidIPA('Dynamic-loader path escapes the application')
    return name


def _expand(name, loader, executable, root):
    for prefix, image in (('@loader_path', loader), ('@executable_path', executable)):
        if name == prefix or name.startswith(prefix + '/'):
            return _inside(posixpath.normpath(posixpath.dirname(image) + name[len(prefix):]), root)
    if name.startswith('/') and _canonical_system(name): return name
    raise InvalidIPA('Unsupported or unsafe dynamic-loader path')


def _closures(images, files, root):
    executions = [name for name, image in images.items() if image['kind'] == 2]
    if not executions: raise InvalidIPA('Device application has no executable')
    reached, states, edges, checks = set(), 0, 0, 0
    for executable in executions:
        visited = set()
        def walk(loader, inherited, active):
            nonlocal states, edges, checks
            if loader in active: return
            if len(active) >= MAX_LOAD_DEPTH: raise InvalidIPA('Dynamic-loader depth limit exceeded')
            image = images[loader]
            paths = tuple(dict.fromkeys([_expand(path, loader, executable, root) for path in image['rpaths']] + list(inherited)))
            if len(paths) > MAX_RPATHS: raise InvalidIPA('Inherited run-path limit exceeded')
            state = (loader, paths)
            if state in visited: return
            visited.add(state); reached.add(loader); states += 1
            if states > MAX_STATES: raise InvalidIPA('Dynamic-loader state limit exceeded')
            for dependency in image['loads']:
                checks += 1
                if checks > MAX_EDGES: raise InvalidIPA('Dynamic-loader dependency limit exceeded')
                if dependency.startswith('/'):
                    if not _canonical_system(dependency): raise InvalidIPA('Non-Apple absolute dynamic dependency')
                    continue
                if dependency.startswith('@rpath/'):
                    tail = dependency[len('@rpath/'):]
                    if not tail or tail.startswith('/'): raise InvalidIPA('Invalid run-path dependency')
                    candidates = []
                    for path in paths:
                        candidate = posixpath.normpath(path + '/' + tail)
                        if path.startswith('/'):
                            if not _canonical_system(candidate): raise InvalidIPA('Run-path dependency escapes system path')
                            # System availability is not observable in an IPA.
                            # Never let a system run-path excuse missing local code.
                            continue
                        candidates.append(_inside(candidate, root))
                else:
                    candidates = [_expand(dependency, loader, executable, root)]
                resolved = next((name for name in candidates if name in files), None)
                if resolved is None or resolved not in images or images[resolved]['kind'] != 6:
                    raise InvalidIPA('Missing or invalid bundled dynamic dependency')
                edges += 1
                walk(resolved, paths, active | {loader})
        walk(executable, (), frozenset())
    if reached != set(images):
        raise InvalidIPA('Bundled image has no verified executable loading context')
    return len(executions), edges


def _members(archive):
    entries = archive.infolist()
    if not 1 <= len(entries) <= MAX_MEMBERS: raise InvalidIPA('Device archive member limit exceeded')
    names, aliases, total = {}, set(), 0
    for entry in entries:
        name = _path(entry.filename, entry.is_dir())
        if entry.orig_filename != entry.filename or _key(name) in aliases:
            raise InvalidIPA('Duplicate or aliased device archive member')
        aliases.add(_key(name)); names[name] = entry
        kind = stat.S_IFMT(entry.external_attr >> 16)
        parts = PurePosixPath(name).parts
        if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise InvalidIPA('Unsupported device archive compression')
        if (entry.flag_bits & 1 or kind not in ({0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG})
                or any(_key(part) == '_codesignature' for part in parts)
                or _key(parts[-1]) in {'embedded.mobileprovision', 'embedded.provisionprofile'}):
            raise InvalidIPA('Device archive contains signing material or unsafe member type')
        if not 0 <= entry.file_size <= MAX_FILE_BYTES: raise InvalidIPA('Device archive member exceeds limit')
        total += entry.file_size
        if total > MAX_TOTAL_BYTES: raise InvalidIPA('Device archive content limit exceeded')
    files = {name for name, entry in names.items() if not entry.is_dir()}
    file_aliases = {_key(name) for name in files}
    for name in names:
        parts = name.split('/')
        if any(_key('/'.join(parts[:i])) in file_aliases for i in range(1, len(parts))):
            raise InvalidIPA('Conflicting file and directory members')
    roots = [name[:-len('/Info.plist')] for name in files if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', name)]
    if len(roots) != 1: raise InvalidIPA('Expected one device application root')
    return names, files, roots[0]


def _inspect(archive, primary, members):
    names, files, root = members
    images, declared = {}, {}
    for name in files:
        entry = names[name]
        with archive.open(entry) as stream: image = _macho(stream, entry.file_size)
        if image is not None:
            _inside(name, root)
            images[name] = image
            if len(images) > MAX_IMAGES: raise InvalidIPA('Device image limit exceeded')
        if name.endswith('/Info.plist'):
            if entry.file_size > MAX_PLIST_BYTES: raise InvalidIPA('Device bundle metadata exceeds limit')
            metadata = plistlib.loads(archive.read(entry))
            if not isinstance(metadata, dict): raise InvalidIPA('Invalid device bundle metadata')
            executable = metadata.get('CFBundleExecutable')
            directory = name[:-len('/Info.plist')]
            executable_bundle = directory.casefold().endswith(('.app', '.appex', '.framework'))
            if 'CFBundleExecutable' not in metadata and not executable_bundle: continue
            if not isinstance(executable, str) or executable in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.+-]+', executable):
                raise InvalidIPA('Invalid declared device executable')
            binary = directory + '/' + executable
            if binary not in files: raise InvalidIPA('Declared device executable is missing')
            declared[binary] = 6 if directory.casefold().endswith('.framework') else 2 if directory.casefold().endswith(('.app', '.appex')) else None
    for image in images:
        parts = image.split('/')[:-1]
        for index, part in enumerate(parts, 1):
            if part.casefold().endswith(('.app', '.appex', '.framework')):
                if '/'.join(parts[:index]) + '/Info.plist' not in files:
                    raise InvalidIPA('Executable bundle metadata is missing')
    for binary, expected in declared.items():
        if binary not in images or expected is not None and images[binary]['kind'] != expected:
            raise InvalidIPA('Declared executable is not the required device image')
    executions, edges = _closures(images, files, root)
    return {**primary, 'unsigned_macho_count': len(images), 'all_macho_platforms': 2, 'signing': 'disabled',
            'bundle_executables_checked': len(declared), 'executables_checked': executions,
            'framework_count': sum(image['kind'] == 6 for image in images.values()),
            'bundled_load_edges_checked': edges, 'all_bundled_dependencies_resolved': True,
            'system_library_availability_verified': False, 'dynamic_loading_verified': False,
            'signature_verified': False, 'runtime_verified': False, 'physical_device_verified': False}


def inspect_unsigned_device(path, bundle_id):
    """Inspect one bounded regular IPA using closed, credential-free errors."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE_BYTES:
                raise InvalidIPA('Invalid unsigned device archive size or type')
            with zipfile.ZipFile(stream) as archive:
                # Reject unsupported codecs and malformed metadata entries
                # before even the existing main-image inspector decompresses.
                members = _members(archive)
                primary = inspect_ipa(stream, 'unsigned-device', bundle_id)
                return _inspect(archive, primary, members)
    except InvalidIPA as error:
        # The older main-image inspector chains a KeyError that can contain a
        # member name. Keep its closed message without exposing that traceback.
        raise InvalidIPA(str(error)) from None
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError, KeyError, OverflowError,
            struct.error, zipfile.BadZipFile, zlib.error, ExpatError):
        raise InvalidIPA('Unreadable unsigned device archive') from None
