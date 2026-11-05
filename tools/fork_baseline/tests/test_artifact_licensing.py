"""Artifact license materials stay fixed, source-paired, private and fail closed."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifact_licensing as licensing

ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'c' * 40
COPYING = (ROOT / 'COPYING').read_bytes()
NOTICE = (ROOT / 'docs/fork/LICENSE-NOTICE.md').read_bytes()


def checkout(root):
    (root / 'docs/fork').mkdir(parents=True)
    (root / 'COPYING').write_bytes(COPYING)
    (root / 'docs/fork/LICENSE-NOTICE.md').write_bytes(NOTICE)
    return root


def materials():
    return {'COPYING': COPYING, 'LICENSE-NOTICE.md': NOTICE,
            'CORRESPONDING-SOURCE.md': licensing.source_notice(SOURCE)}


class ArtifactLicensingTests(unittest.TestCase):
    def test_exact_checkout_materials_and_source_revision_are_paired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = checkout(Path(directory))
            (root / 'private.key').write_bytes(b'never collect this unrelated file')
            value = licensing.material_bytes(root, SOURCE)
        self.assertEqual(tuple(value), licensing.MATERIAL_NAMES)
        self.assertEqual(value, materials())
        self.assertEqual(value['COPYING'], COPYING)
        self.assertEqual(value['LICENSE-NOTICE.md'], NOTICE)
        text = value['CORRESPONDING-SOURCE.md'].decode()
        self.assertIn('git checkout --detach ' + SOURCE, text)
        for instruction in ('versions.json', 'build-system/Make/Make.py', 'tools/fork_baseline/baseline.py',
                            'submodule.submodules/rlottie/rlottie.url https://github.com/TelegramMessenger/rlottie.git',
                            'submodule.submodules/TgVoipWebrtc/tgcalls.url https://github.com/TelegramMessenger/tgcalls.git',
                            'git submodule update --init --recursive', 'not a legal certification'):
            self.assertIn(instruction, text)
        self.assertNotIn('private.key', text)
        self.assertNotIn('TELEGRAM_API_HASH=', text)
        self.assertNotIn('Xcode_27', text)

    def test_validation_returns_only_fixed_size_and_hash_metadata(self):
        value = licensing.validate_materials(materials(), SOURCE)
        self.assertEqual(tuple(value), licensing.MATERIAL_NAMES)
        for name, content in materials().items():
            self.assertEqual(value[name], {'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content)})

    def test_missing_extra_or_nonbytes_materials_are_rejected(self):
        for name in licensing.MATERIAL_NAMES:
            value = materials(); del value[name]
            with self.subTest(missing=name), self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.validate_materials(value, SOURCE)
        value = materials(); value['credentials.json'] = b'not a license material'
        with self.assertRaises(licensing.InvalidLicenseMaterials):
            licensing.validate_materials(value, SOURCE)
        for content in ('not bytes', bytearray(NOTICE), b'', b'x' * (licensing.MAX_MATERIAL_BYTES + 1), b'\xff', NOTICE + b'\x00'):
            value = materials(); value['LICENSE-NOTICE.md'] = content
            with self.subTest(content_type=type(content).__name__), self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.validate_materials(value, SOURCE)

    def test_gpl_text_cannot_be_altered_or_replaced(self):
        for content in (COPYING + b'\n', b'GPL licensed', COPYING.replace(b'GNU GENERAL PUBLIC LICENSE', b'Changed license text here!')):
            value = materials(); value['COPYING'] = content
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.validate_materials(value, SOURCE)

    def test_source_notice_is_exact_and_rejects_wrong_commit_or_repository(self):
        for content in (licensing.source_notice('d' * 40), licensing.source_notice(SOURCE) + b'\n',
                        licensing.source_notice(SOURCE).replace(b'Livara-Dev/Telegram-iOS', b'Other/Repository')):
            value = materials(); value['CORRESPONDING-SOURCE.md'] = content
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.validate_materials(value, SOURCE)
        for source in ('c' * 39, 'c' * 41, 'C' * 40, 'main', '', None, 'x' * 40):
            with self.subTest(source=source), self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.source_notice(source)

    def test_notice_rejects_credentials_constructed_at_runtime(self):
        key_marker = '-----' + 'BEGIN ' + 'PRIVATE KEY' + '-----'
        pgp_marker = '-----' + 'BEGIN PGP ' + 'PRIVATE KEY BLOCK' + '-----'
        credentials = [key_marker, pgp_marker, 'gh' + 'p_' + 'Z' * 32, 'github' + '_pat_' + 'Z' * 32,
                       'TELEGRAM_' + 'API_HASH' + '=' + 'a' * 32,
                       '"API_' + 'ID": "1234567"', 'GITHUB_' + 'TOKEN = private-value',
                       'P12_' + 'PASSWORD: private-value', 'https://' + 'user:private-value' + '@example.invalid']
        for credential in credentials:
            value = materials(); value['LICENSE-NOTICE.md'] = NOTICE + ('\n' + credential).encode()
            with self.subTest(kind=credentials.index(credential)), self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.validate_materials(value, SOURCE)

    def test_missing_symlinked_or_nonregular_fixed_checkout_files_fail_closed(self):
        for relative, action in (
                ('COPYING', 'missing'), ('docs/fork/LICENSE-NOTICE.md', 'missing'),
                ('COPYING', 'symlink'), ('docs/fork/LICENSE-NOTICE.md', 'symlink'),
                ('COPYING', 'directory'), ('docs/fork/LICENSE-NOTICE.md', 'fifo')):
            with self.subTest(relative=relative, action=action), tempfile.TemporaryDirectory() as directory:
                root = checkout(Path(directory)); target = root / relative
                target.unlink()
                if action == 'symlink':
                    other = root / 'unrelated'; other.write_bytes(COPYING if relative == 'COPYING' else NOTICE)
                    target.symlink_to(other)
                elif action == 'directory':
                    target.mkdir()
                elif action == 'fifo':
                    os.mkfifo(target)
                with self.assertRaises(licensing.InvalidLicenseMaterials):
                    licensing.material_bytes(root, SOURCE)

    def test_docs_and_fork_directory_links_are_rejected(self):
        for component in ('docs', 'docs/fork'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                root = checkout(Path(directory)); target = root / component
                moved = root / 'moved'; target.rename(moved); target.symlink_to(moved, target_is_directory=True)
                with self.assertRaises(licensing.InvalidLicenseMaterials):
                    licensing.material_bytes(root, SOURCE)

    def test_oversize_checkout_notice_is_rejected_before_copying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = checkout(Path(directory))
            (root / 'docs/fork/LICENSE-NOTICE.md').write_bytes(b'x' * (licensing.MAX_MATERIAL_BYTES + 1))
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.material_bytes(root, SOURCE)

    def test_short_regular_notice_read_is_rejected_without_returning_partial_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = checkout(Path(directory))
            original = licensing.os.read
            def short_notice(descriptor, limit):
                if os.fstat(descriptor).st_size == len(NOTICE):
                    return NOTICE[:NOTICE.index(b'\n') + 1]
                return original(descriptor, limit)
            with patch.object(licensing.os, 'read', side_effect=short_notice):
                with self.assertRaises(licensing.InvalidLicenseMaterials):
                    licensing.material_bytes(root, SOURCE)

    def test_os_alias_above_root_is_valid_but_root_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); actual = base / 'actual'; actual.mkdir()
            root = checkout(actual / 'checkout')
            alias = base / 'os-alias'; alias.symlink_to(actual, target_is_directory=True)
            self.assertEqual(licensing.material_bytes(alias / 'checkout', SOURCE), materials())
            direct = base / 'root-link'; direct.symlink_to(root, target_is_directory=True)
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.material_bytes(direct, SOURCE)

    def test_write_and_read_use_original_three_bytes_and_private_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = licensing.write_materials(materials(), root)
            self.assertEqual(licensing.read_materials(root, SOURCE), materials())
            self.assertEqual(metadata, licensing.validate_materials(materials(), SOURCE))
            self.assertEqual(set(path.name for path in root.iterdir()), set(licensing.MATERIAL_NAMES))
            for name in licensing.MATERIAL_NAMES:
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)

    def test_read_rejects_missing_linked_or_wrong_source_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); licensing.write_materials(materials(), root)
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.read_materials(root, 'd' * 40)
            path = root / 'LICENSE-NOTICE.md'; path.unlink()
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.read_materials(root, SOURCE)
            other = root / 'private'; other.write_bytes(NOTICE); path.symlink_to(other)
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.read_materials(root, SOURCE)

    def test_write_validates_everything_before_creating_any_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); value = materials(); value['COPYING'] += b'changed'
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.write_materials(value, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_write_rejects_preexisting_or_symlinked_destinations_without_partial_files(self):
        for name in licensing.MATERIAL_NAMES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); (root / name).write_bytes(b'preserve existing')
                with self.assertRaises(licensing.InvalidLicenseMaterials):
                    licensing.write_materials(materials(), root)
                self.assertEqual([path.name for path in root.iterdir()], [name])
                self.assertEqual((root / name).read_bytes(), b'preserve existing')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); actual = base / 'actual'; actual.mkdir()
            alias = base / 'alias'; alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(licensing.InvalidLicenseMaterials):
                licensing.write_materials(materials(), alias)
            self.assertEqual(list(actual.iterdir()), [])

    def test_write_failure_rolls_back_only_new_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'keep.txt').write_text('keep')
            original = licensing.os.open
            def fail_last(path, *args, **kwargs):
                if path == 'CORRESPONDING-SOURCE.md':
                    raise OSError('synthetic write failure')
                return original(path, *args, **kwargs)
            with patch.object(licensing.os, 'open', side_effect=fail_last):
                with self.assertRaises(licensing.InvalidLicenseMaterials):
                    licensing.write_materials(materials(), root)
            self.assertEqual([path.name for path in root.iterdir()], ['keep.txt'])
