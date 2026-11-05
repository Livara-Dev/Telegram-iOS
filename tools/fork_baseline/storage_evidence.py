"""Closed storage diagnostics for disposable, freshly created simulator controls.

No preference contents, paths, identifiers or account database contents are
returned. Seeding is a test admission control, never migration or user recovery.
"""
import os
from pathlib import Path
import plistlib
import stat
import uuid
from xml.parsers.expat import ExpatError

BUNDLE = 'org.livara.ayugram'
STORAGE_SELECTION = 'org.livara.ayugram.startup-storage.v1'
STORAGE_FIELDS = frozenset({
    'file_exists', 'file_is_symlink', 'file_is_regular', 'file_oversized', 'file_readable',
    'plist_parsed', 'preferences_dictionary', 'selection_present', 'selection_dictionary',
    'version_integer', 'version_is_one', 'kind_private', 'kind_group', 'identifier_present',
    'identifier_string', 'identifier_matches_expected_group', 'selection_unknown_keys',
})
MAX_PREFERENCES_BYTES = 1024 * 1024
_PREFERENCES = Path('Library/Preferences') / (BUNDLE + '.plist')
_MARKER = Path('Library/Application Support/AyuGram/LaunchDiagnostics/latest.json')
_TELEGRAM_DATA = Path('Library/Application Support/AyuGram/telegram-data')
_PRIVATE_ACCOUNTS = _TELEGRAM_DATA / 'accounts-metadata'


def _no_symlink_parents(path):
    for parent in path.parents:
        mode = parent.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode): return False
    return True


def inspect_storage(path):
    result = dict.fromkeys(STORAGE_FIELDS, False)
    try:
        path = Path(path)
        observed = path.lstat()
        result['file_exists'] = True
        result['file_is_symlink'] = stat.S_ISLNK(observed.st_mode)
        result['file_is_regular'] = stat.S_ISREG(observed.st_mode)
        result['file_oversized'] = observed.st_size > MAX_PREFERENCES_BYTES
        if (not result['file_is_regular'] or result['file_is_symlink'] or result['file_oversized']
                or not _no_symlink_parents(path)):
            return result
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
                return result
            data = stream.read(MAX_PREFERENCES_BYTES + 1)
            result['file_readable'] = True
        if len(data) > MAX_PREFERENCES_BYTES:
            result['file_oversized'] = True
            return result
        try:
            preferences = plistlib.loads(data)
        except (ValueError, TypeError, OverflowError, ExpatError):
            return result
        result['plist_parsed'] = True
        result['preferences_dictionary'] = isinstance(preferences, dict)
        if not result['preferences_dictionary']: return result
        result['selection_present'] = STORAGE_SELECTION in preferences
        if not result['selection_present']: return result
        selection = preferences[STORAGE_SELECTION]
        result['selection_dictionary'] = isinstance(selection, dict)
        if not result['selection_dictionary']: return result
        result['version_integer'] = type(selection.get('version')) is int
        result['version_is_one'] = result['version_integer'] and selection['version'] == 1
        result['kind_private'] = type(selection.get('kind')) is str and selection['kind'] == 'private'
        result['kind_group'] = type(selection.get('kind')) is str and selection['kind'] == 'group'
        result['identifier_present'] = 'identifier' in selection
        result['identifier_string'] = type(selection.get('identifier')) is str
        result['identifier_matches_expected_group'] = (result['identifier_string']
                                                       and selection['identifier'] == 'group.' + BUNDLE)
        result['selection_unknown_keys'] = bool(set(selection) - {'version', 'kind', 'identifier'})
    except (OSError, ValueError, TypeError, RuntimeError):
        pass
    return result


def _valid(evidence, group):
    if not isinstance(evidence, dict) or set(evidence) != STORAGE_FIELDS or any(type(v) is not bool for v in evidence.values()):
        return False
    expected = dict.fromkeys(STORAGE_FIELDS, False)
    for field in ('file_exists', 'file_is_regular', 'file_readable', 'plist_parsed', 'preferences_dictionary',
                  'selection_present', 'selection_dictionary', 'version_integer', 'version_is_one'):
        expected[field] = True
    expected['kind_group' if group else 'kind_private'] = True
    if group:
        for field in ('identifier_present', 'identifier_string', 'identifier_matches_expected_group'):
            expected[field] = True
    return evidence == expected


def valid_private(evidence):
    return _valid(evidence, False)


def valid_group(evidence):
    return _valid(evidence, True)


def _data_root(data):
    root = Path(data)
    if (not root.is_absolute() or root.resolve(strict=True) != root or not _no_symlink_parents(root)
            or not stat.S_ISDIR(root.lstat().st_mode) or root.is_symlink()):
        raise ValueError('private_selection_seed_refused')
    return root


def _exists_safe(root, relative):
    """Check fixed-path ancestry without listing or reading any database."""
    current = root
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(mode): raise ValueError('private_selection_seed_refused')
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError('private_selection_seed_refused')
    return True


def seed_candidate_absent(data):
    try:
        root = _data_root(data)
        return not any(_exists_safe(root, relative) for relative in (_PREFERENCES, _MARKER, _TELEGRAM_DATA))
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def seed_private_selection(data):
    """Atomically seed only a brand-new owned simulator; never overwrite state."""
    descriptor = None
    temporary = '.startup-private-' + uuid.uuid4().hex
    try:
        root = _data_root(data)
        if not seed_candidate_absent(root): raise ValueError('private_selection_seed_refused')
        preferences = root
        for component in _PREFERENCES.parent.parts:
            preferences = preferences / component
            try:
                preferences.mkdir(mode=0o700)
            except FileExistsError:
                pass
            if preferences.is_symlink() or not stat.S_ISDIR(preferences.lstat().st_mode):
                raise ValueError('private_selection_seed_refused')
        descriptor = os.open(preferences, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        # Recheck the admission markers after directory creation. Exclusive hard
        # linking publishes a complete file atomically and cannot overwrite one.
        if not seed_candidate_absent(root): raise ValueError('private_selection_seed_refused')
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0),
                                  0o600, dir_fd=descriptor)
        with os.fdopen(file_descriptor, 'wb') as target:
            target.write(plistlib.dumps({STORAGE_SELECTION: {'version': 1, 'kind': 'private'}}))
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, _PREFERENCES.name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ValueError('private_selection_seed_refused') from None
    finally:
        if descriptor is not None:
            try: os.unlink(temporary, dir_fd=descriptor)
            except OSError: pass
            try: os.close(descriptor)
            except OSError: pass


def private_account_store_identity(data):
    """For local comparison only; never serialize filesystem identity values."""
    try:
        root = _data_root(data)
        if not _exists_safe(root, _PRIVATE_ACCOUNTS): return None
        observed = (root / _PRIVATE_ACCOUNTS).lstat()
        if not stat.S_ISDIR(observed.st_mode): return None
        return observed.st_dev, observed.st_ino
    except (OSError, ValueError, TypeError, RuntimeError):
        return None


def private_account_store_present(data):
    return private_account_store_identity(data) is not None
