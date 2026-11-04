"""Read-only platform checks for built IPAs; never a signature or runtime proof."""
import plistlib
import re
import stat
import struct
import zipfile


class InvalidIPA(ValueError):
    pass


def _read(stream, count):
    value = stream.read(count)
    if len(value) != count:
        raise InvalidIPA("Truncated executable")
    return value


def _thin_platform(stream, offset, size):
    if size < 32:
        raise InvalidIPA("Invalid executable size")
    stream.seek(offset)
    header = _read(stream, 32)
    if header[:4] != b"\xcf\xfa\xed\xfe":
        raise InvalidIPA("Expected a 64-bit Mach-O executable")
    _, cpu, _, filetype, count, command_bytes, _, _ = struct.unpack("<8I", header)
    if cpu != 0x0100000C or filetype != 2 or not 1 <= count <= 4096 or not count * 8 <= command_bytes <= min(1024 * 1024, size - 32):
        raise InvalidIPA("Invalid ARM64 executable header")
    commands = _read(stream, command_bytes)
    position, platform = 0, None
    for _ in range(count):
        if position + 8 > len(commands):
            raise InvalidIPA("Truncated Mach-O load command")
        code, length = struct.unpack_from("<II", commands, position)
        if length < 8 or length % 8 or position + length > len(commands):
            raise InvalidIPA("Invalid Mach-O load command")
        if code == 0x32:  # LC_BUILD_VERSION, emitted by the pinned Apple linker.
            if length < 24 or platform is not None:
                raise InvalidIPA("Ambiguous Mach-O platform")
            platform = struct.unpack_from("<I", commands, position + 8)[0]
        position += length
    if position != len(commands) or platform is None:
        raise InvalidIPA("Missing explicit Mach-O build platform")
    return platform


def _platform(stream, size):
    magic = _read(stream, 4)
    if magic == b"\xcf\xfa\xed\xfe":
        return _thin_platform(stream, 0, size)
    formats = {b"\xca\xfe\xba\xbe": (">", False), b"\xbe\xba\xfe\xca": ("<", False),
               b"\xca\xfe\xba\xbf": (">", True), b"\xbf\xba\xfe\xca": ("<", True)}
    if magic not in formats:
        raise InvalidIPA("Unrecognized executable format")
    endian, wide = formats[magic]
    count = struct.unpack(endian + "I", _read(stream, 4))[0]
    if not 1 <= count <= 16:
        raise InvalidIPA("Invalid universal executable count")
    entries = []
    entry_format = endian + ("IIQQII" if wide else "IIIII")
    for _ in range(count):
        cpu, _, offset, length, *_ = struct.unpack(entry_format, _read(stream, struct.calcsize(entry_format)))
        if offset < 8 + count * struct.calcsize(entry_format) or length < 32 or offset > size or length > size - offset:
            raise InvalidIPA("Universal executable slice exceeds file")
        entries.append((cpu, offset, length))
    arm64 = [(offset, length) for cpu, offset, length in entries if cpu == 0x0100000C]
    if len(arm64) != 1:
        raise InvalidIPA("Expected one ARM64 application slice")
    return _thin_platform(stream, *arm64[0])


def inspect_ipa(path, mode, bundle_id):
    expected_platform = 7 if mode == "simulator" else 2
    expected_sdk = "iPhoneSimulator" if mode == "simulator" else "iPhoneOS"
    if mode not in {"simulator", "unsigned-device", "device"}:
        raise InvalidIPA("Unknown artifact mode")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise InvalidIPA("Duplicate ZIP entries")
            infos = [name for name in names if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
            if len(infos) != 1:
                raise InvalidIPA("Expected one top-level iOS application")
            info_entry = archive.getinfo(infos[0])
            if info_entry.file_size > 1024 * 1024 or stat.S_IFMT(info_entry.external_attr >> 16) not in {0, stat.S_IFREG}:
                raise InvalidIPA("Invalid application metadata entry")
            info = plistlib.loads(archive.read(info_entry))
            if not isinstance(info, dict):
                raise InvalidIPA("Application metadata must be a dictionary")
            executable = info.get("CFBundleExecutable")
            if info.get("CFBundleIdentifier") != bundle_id or info.get("CFBundleSupportedPlatforms") != [expected_sdk] or not isinstance(executable, str) or executable in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", executable):
                raise InvalidIPA("Bundle identity or SDK does not match requested artifact")
            entry = archive.getinfo(infos[0][:-len("Info.plist")] + executable)
            if entry.file_size > 512 * 1024 * 1024 or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}:
                raise InvalidIPA("Invalid executable entry")
            with archive.open(entry) as stream:
                platform = _platform(stream, entry.file_size)
            if platform != expected_platform:
                raise InvalidIPA("Executable is built for a different platform")
            return {"cpu": "arm64", "mach_o_platform": platform, "supported_platform": expected_sdk,
                    "bundle_id_verified": True, "signature_verified": False, "runtime_verified": False}
    except (zipfile.BadZipFile, KeyError, plistlib.InvalidFileException, TypeError, struct.error) as error:
        raise InvalidIPA("Unreadable application archive") from error
