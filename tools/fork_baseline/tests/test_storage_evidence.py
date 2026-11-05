"""Storage controls disclose booleans and cannot overwrite an existing install."""
import json
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_evidence as storage


class StorageEvidenceTests(unittest.TestCase):
    def write(self, root, selection=None, preferences=None):
        path = root / 'preferences.plist'
        value = preferences if preferences is not None else {storage.STORAGE_SELECTION: selection}
        path.write_bytes(plistlib.dumps(value))
        return path

    def assertClosed(self, result):
        self.assertEqual(set(result), storage.STORAGE_FIELDS)
        self.assertTrue(all(type(value) is bool for value in result.values()))
        encoded = json.dumps(result)
        for private in ('group.org.livara.ayugram', 'private-sender', 'telegram-data', '/tmp/', storage.STORAGE_SELECTION):
            self.assertNotIn(private, encoded)

    def test_private_group_and_unrelated_preferences_have_only_precise_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = self.write(root, preferences={storage.STORAGE_SELECTION: {'version': 1, 'kind': 'private'},
                                                'account': 'private-sender'})
            private = storage.inspect_storage(path)
            self.assertClosed(private)
            self.assertTrue(storage.valid_private(private)); self.assertFalse(storage.valid_group(private))
            self.assertFalse(private['identifier_present'])
            path = self.write(root, {'version': 1, 'kind': 'group', 'identifier': 'group.' + storage.BUNDLE})
            group = storage.inspect_storage(path)
            self.assertClosed(group)
            self.assertTrue(storage.valid_group(group)); self.assertFalse(storage.valid_private(group))
            self.assertTrue(group['identifier_matches_expected_group'])

    def test_missing_link_directory_oversized_unreadable_and_malformed_are_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); path = root / 'preferences.plist'
            missing = storage.inspect_storage(path); self.assertClosed(missing)
            self.assertFalse(missing['file_exists'])
            path.symlink_to(root / 'absent')
            linked = storage.inspect_storage(path); self.assertClosed(linked)
            self.assertTrue(linked['file_exists']); self.assertTrue(linked['file_is_symlink'])
            self.assertFalse(linked['file_readable']); path.unlink()
            path.mkdir(); self.assertFalse(storage.inspect_storage(path)['file_is_regular']); path.rmdir()
            path.write_bytes(b'x' * (storage.MAX_PREFERENCES_BYTES + 1))
            oversized = storage.inspect_storage(path); self.assertClosed(oversized)
            self.assertTrue(oversized['file_oversized']); self.assertFalse(oversized['file_readable'])
            path.write_bytes(b'not a plist')
            with patch.object(storage.os, 'open', side_effect=PermissionError('never log this private path')):
                unreadable = storage.inspect_storage(path)
            self.assertFalse(unreadable['file_readable'])
            for invalid in (b'not a plist', b'<?xml version="1.0"?><plist><dict><x>'):
                path.write_bytes(invalid)
                malformed = storage.inspect_storage(path); self.assertClosed(malformed)
                self.assertTrue(malformed['file_readable']); self.assertFalse(malformed['plist_parsed'])

    def test_parse_shape_selection_type_version_kind_identifier_and_unknown_keys(self):
        cases = [
            ([], 'preferences_dictionary'),
            ({}, 'selection_present'),
            ({storage.STORAGE_SELECTION: 'private'}, 'selection_dictionary'),
            ({storage.STORAGE_SELECTION: {'version': True, 'kind': 'private'}}, 'version_integer'),
            ({storage.STORAGE_SELECTION: {'version': '1', 'kind': 'private'}}, 'version_integer'),
            ({storage.STORAGE_SELECTION: {'version': 2, 'kind': 'private'}}, 'version_is_one'),
            ({storage.STORAGE_SELECTION: {'version': 1, 'kind': 'other'}}, 'kind_private'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for preferences, false_field in cases:
                with self.subTest(field=false_field, preferences=preferences):
                    result = storage.inspect_storage(self.write(root, preferences=preferences))
                    self.assertClosed(result); self.assertFalse(result[false_field])
                    self.assertFalse(storage.valid_private(result)); self.assertFalse(storage.valid_group(result))
            for identifier in ('group.unexpected', 123, b'not text'):
                result = storage.inspect_storage(self.write(root, {'version': 1, 'kind': 'group', 'identifier': identifier}))
                self.assertTrue(result['identifier_present'])
                self.assertEqual(result['identifier_string'], type(identifier) is str)
                self.assertFalse(result['identifier_matches_expected_group']); self.assertFalse(storage.valid_group(result))
            private_identifier = storage.inspect_storage(self.write(root, {'version': 1, 'kind': 'private', 'identifier': ''}))
            self.assertTrue(private_identifier['identifier_present']); self.assertFalse(storage.valid_private(private_identifier))
            unknown = storage.inspect_storage(self.write(root, {'version': 1, 'kind': 'private', 'extra': 'private-sender'}))
            self.assertTrue(unknown['selection_unknown_keys']); self.assertFalse(storage.valid_private(unknown)); self.assertClosed(unknown)

    def test_validators_require_exact_boolean_fields_and_every_admission_condition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for selection, validator in (({'version': 1, 'kind': 'private'}, storage.valid_private),
                                         ({'version': 1, 'kind': 'group', 'identifier': 'group.' + storage.BUNDLE}, storage.valid_group)):
                valid = storage.inspect_storage(self.write(root, selection))
                self.assertTrue(validator(valid))
                for key in valid:
                    broken = dict(valid); broken[key] = not broken[key]
                    with self.subTest(key=key): self.assertFalse(validator(broken))
                    broken = dict(valid); broken[key] = int(broken[key])
                    self.assertFalse(validator(broken))
                self.assertFalse(validator({**valid, 'raw': 'do not expose'}))
                self.assertFalse(validator({key: value for key, value in valid.items() if key != 'kind_group'}))

    def test_parent_symlink_is_not_read_and_cannot_be_seeded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); actual = root / 'actual'; actual.mkdir()
            path = self.write(actual, {'version': 1, 'kind': 'private'})
            (root / 'link').symlink_to(actual, target_is_directory=True)
            result = storage.inspect_storage(root / 'link' / path.name)
            self.assertFalse(result['file_readable']); self.assertFalse(storage.valid_private(result))
            self.assertFalse(storage.seed_candidate_absent(root / 'link'))
            with self.assertRaisesRegex(ValueError, '^private_selection_seed_refused$'):
                storage.seed_private_selection(root / 'link')

    def test_seed_creates_only_public_selection_atomically_mode600_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertTrue(storage.seed_candidate_absent(root))
            self.assertTrue(storage.seed_private_selection(root))
            path = root / 'Library/Preferences' / (storage.BUNDLE + '.plist')
            original = path.read_bytes()
            self.assertEqual(plistlib.loads(original), {storage.STORAGE_SELECTION: {'version': 1, 'kind': 'private'}})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(storage.valid_private(storage.inspect_storage(path)))
            self.assertFalse(storage.seed_candidate_absent(root))
            with self.assertRaisesRegex(ValueError, '^private_selection_seed_refused$'): storage.seed_private_selection(root)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(storage.private_account_store_present(root))

    def test_seed_rejects_existing_marker_data_preferences_and_unsafe_ancestry(self):
        for relative, kind in (('Library/Preferences/' + storage.BUNDLE + '.plist', 'file'),
                               ('Library/Application Support/AyuGram/LaunchDiagnostics/latest.json', 'file'),
                               ('Library/Application Support/AyuGram/telegram-data', 'directory'),
                               ('Library', 'file'), ('Library/Preferences', 'link')):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); existing = root / relative
                existing.parent.mkdir(parents=True, exist_ok=True)
                if kind == 'directory': existing.mkdir()
                elif kind == 'link': existing.symlink_to(root, target_is_directory=True)
                else: existing.write_bytes(b'unchanged')
                self.assertFalse(storage.seed_candidate_absent(root))
                with self.assertRaisesRegex(ValueError, '^private_selection_seed_refused$'): storage.seed_private_selection(root)
                if kind == 'file': self.assertEqual(existing.read_bytes(), b'unchanged')
        for path in (Path('relative-data'), Path('/definitely-missing-storage-control')):
            self.assertFalse(storage.seed_candidate_absent(path))
            with self.assertRaisesRegex(ValueError, '^private_selection_seed_refused$'): storage.seed_private_selection(path)

    def test_account_store_requires_real_fixed_directory_and_reads_no_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            base = root / 'Library/Application Support/AyuGram'; base.mkdir(parents=True)
            (base / 'LaunchDiagnostics').mkdir()
            self.assertFalse(storage.private_account_store_present(root))
            target = base / 'telegram-data/accounts-metadata'; target.parent.mkdir()
            target.write_bytes(b'not a directory')
            self.assertFalse(storage.private_account_store_present(root)); target.unlink()
            target.symlink_to(base, target_is_directory=True)
            self.assertFalse(storage.private_account_store_present(root)); target.unlink()
            target.mkdir(); (target / 'account.db').write_bytes(b'never inspect me')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('database read forbidden')), \
                 patch.object(Path, 'iterdir', side_effect=AssertionError('database listing forbidden')):
                self.assertTrue(storage.private_account_store_present(root))
