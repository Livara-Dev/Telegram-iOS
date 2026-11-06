# SPDX-License-Identifier: GPL-2.0-or-later
# Frozen identity verifier reused for the fork on 2026-11-06.
# Source: tools/fork_baseline/modern_projection_plan.py; exact function bodies.
import os
from pathlib import Path
import plistlib
import re
import stat
import time

MAX_PRIVATE = 65536
DEVICE_TYPE = 'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'
RUNTIME = 'com.apple.CoreSimulator.SimRuntime.iOS-27-0'


class PlanError(RuntimeError):
    pass


def private_bytes(path, maximum=MAX_PRIVATE, allow_empty=False):
    """Read a bounded, owned regular file without following its final link."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or not (0 if allow_empty else 1) <= info.st_size <= maximum): raise PlanError('unsafe_private_file')
        data = os.read(descriptor, maximum + 1)
        if len(data) != info.st_size: raise PlanError('private_file_changed')
        return data
    except (OSError, ValueError):
        raise PlanError('private_file_unavailable') from None
    finally:
        if descriptor is not None: os.close(descriptor)


def owned_device_identity(identifier, name, *, home=None, uid=None):
    """Prove only identity from this new device's metadata, never readiness."""
    proof = {'source': 'ownedDeviceMetadata', 'state': 'unavailable', 'reason': 'metadataUnavailable',
             'identity_verified': False, 'readiness_asserted': False}
    try:
        user = os.getuid() if uid is None else uid
        if (not isinstance(identifier, str) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', identifier) is None
                or not isinstance(name, str) or not name or len(name) > 128 or type(user) is not int or not 0 <= user < 2**32 - 1):
            return {**proof, 'state': 'rejected', 'reason': 'invalidOwnedIdentity'}
        root = Path(home if home is not None else Path.home()).resolve(strict=True)
        device = root / 'Library/Developer/CoreSimulator/Devices' / identifier
        path = root
        for part in (None, *device.relative_to(root).parts):
            if part is not None: path = path / part
            try: info = path.lstat()
            except FileNotFoundError: return proof
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != user:
                return {**proof, 'state': 'rejected', 'reason': 'unsafeOwnedDevicePath'}
        metadata = device / 'device.plist'
        try: info = metadata.lstat()
        except FileNotFoundError: return proof
        if not stat.S_ISREG(info.st_mode) or info.st_uid != user or info.st_nlink != 1:
            return {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceMetadata'}
        if not 0 < info.st_size <= MAX_PRIVATE: return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        try: value = plistlib.loads(private_bytes(metadata))
        except Exception: return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        if not isinstance(value, dict): return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        expected = {'UDID': identifier, 'name': name, 'deviceType': DEVICE_TYPE, 'runtime': RUNTIME}
        # A known mismatch remains a rejection even when another field is
        # absent; partial metadata cannot downgrade it to an API fallback.
        for key, wanted in expected.items():
            if key not in value: continue
            observed = value[key]
            matches = (type(observed) is str and
                (observed.casefold() == wanted.casefold() if key == 'UDID' else observed == wanted))
            if not matches: return {**proof, 'state': 'rejected', 'reason': 'ownedDeviceIdentityMismatch'}
        if any(key not in value for key in expected): return {**proof, 'reason': 'unsupportedDeviceMetadata'}
        return {**proof, 'state': 'verified', 'reason': None, 'identity_verified': True}
    except (OSError, ValueError, TypeError, RuntimeError):
        return proof



def owned_device_absence(identifier, name, *, home=None):
    """Prove UUID AND unique-name absence from a complete bounded metadata scan.

    No readiness or other-device ownership is asserted, and no file is changed.
    Unknown/unreadable metadata never becomes absence. The caller still requires
    the exact owned simctl delete to succeed before using this proof.
    """
    proof = {'source': 'ownedDeviceMetadataScan', 'state': 'unavailable',
             'reason': 'metadataUnavailable', 'absence_verified': False}
    try:
        if (not isinstance(identifier, str) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', identifier) is None
                or not isinstance(name, str) or not name or len(name) > 128):
            return {**proof, 'state': 'rejected', 'reason': 'invalidOwnedIdentity'}
        user = os.getuid()
        root = Path(home if home is not None else Path.home()).resolve(strict=True)
        devices = root / 'Library/Developer/CoreSimulator/Devices'
        cursor = root
        for part in (None, *devices.relative_to(root).parts):
            if part is not None: cursor = cursor / part
            info = cursor.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != user:
                return {**proof, 'state': 'rejected', 'reason': 'unsafeOwnedDevicePath'}
        initial = devices.lstat()
        initial_identity = (initial.st_dev, initial.st_ino, initial.st_mtime_ns, initial.st_ctime_ns)
        deadline = time.monotonic() + 2
        observed = []
        entries = 0
        with os.scandir(devices) as listing:
            for entry in listing:
                entries += 1
                if entries > 1024 or time.monotonic() > deadline:
                    return {**proof, 'reason': 'metadataScanLimit'}
                path = devices / entry.name
                info = path.lstat()
                if entry.name.casefold() == identifier.casefold():
                    return {**proof, 'state': 'present', 'reason': 'ownedUUIDPresent'}
                if stat.S_ISLNK(info.st_mode) or info.st_uid != user:
                    return {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceEntry'}
                if stat.S_ISREG(info.st_mode):
                    if re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', entry.name) is not None:
                        return {**proof, 'reason': 'unsupportedDeviceEntry'}
                    continue  # Non-UUID device-set bookkeeping files are not devices.
                if not stat.S_ISDIR(info.st_mode) or re.fullmatch(r'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}', entry.name) is None:
                    return {**proof, 'reason': 'unsupportedDeviceEntry'}
                metadata = path / 'device.plist'
                before = metadata.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_uid != user or before.st_nlink != 1:
                    return {**proof, 'state': 'rejected', 'reason': 'unsafeDeviceMetadata'}
                value = plistlib.loads(private_bytes(metadata))
                if not isinstance(value, dict):
                    return {**proof, 'reason': 'unsupportedDeviceMetadata'}
                if value.get('name') == name:
                    return {**proof, 'state': 'present', 'reason': 'ownedNamePresent'}
                if not all(type(value.get(key)) is str and 0 < len(value[key]) <= 256
                        for key in ('UDID', 'name', 'deviceType', 'runtime')):
                    return {**proof, 'reason': 'unsupportedDeviceMetadata'}
                if value['UDID'].casefold() != entry.name.casefold():
                    return {**proof, 'state': 'rejected', 'reason': 'deviceIdentityMismatch'}
                after = metadata.lstat()
                identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    return {**proof, 'reason': 'deviceMetadataChanged'}
                observed.append((metadata, identity))
        if time.monotonic() > deadline:
            return {**proof, 'reason': 'metadataScanLimit'}
        for metadata, identity in observed:
            if time.monotonic() > deadline:
                return {**proof, 'reason': 'metadataScanLimit'}
            current = metadata.lstat()
            if (not stat.S_ISREG(current.st_mode) or current.st_uid != user or current.st_nlink != 1
                    or identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)):
                return {**proof, 'reason': 'deviceMetadataChanged'}
            if time.monotonic() > deadline:
                return {**proof, 'reason': 'metadataScanLimit'}
        final = devices.lstat()
        if (not stat.S_ISDIR(final.st_mode) or final.st_uid != user
                or initial_identity != (final.st_dev, final.st_ino, final.st_mtime_ns, final.st_ctime_ns)):
            return {**proof, 'reason': 'deviceSetChanged'}
        if time.monotonic() > deadline:
            return {**proof, 'reason': 'metadataScanLimit'}
        return {**proof, 'state': 'verified', 'reason': None, 'absence_verified': True}
    except (OSError, ValueError, TypeError, RuntimeError, PlanError):
        return proof
