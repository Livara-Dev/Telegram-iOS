"""Distribution is atomic and carries only fixed, exact-source license materials."""
import hashlib
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artifact_licensing as licensing
import baseline

PROJECT = Path(__file__).resolve().parents[3]
SOURCE = 'c' * 40


class PublisherLicensingTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'docs/fork').mkdir(parents=True)
        for name in ('COPYING', 'docs/fork/LICENSE-NOTICE.md'):
            (root / name).write_bytes((PROJECT / name).read_bytes())
        (root / 'versions.json').write_text('{"xcode":"27.1"}')
        ipa = root / 'source.ipa'; ipa.write_bytes(b'synthetic unchanged IPA')
        return ipa

    def publish(self, root, ipa, destination):
        with patch.object(baseline, 'ROOT', root), patch.object(baseline, 'output', return_value=SOURCE):
            baseline.publish_artifact(ipa, destination, 'unsigned-device', 'org.livara.ayugram', 1)

    def test_exact_source_materials_and_all_hashes_are_published_without_other_repo_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ipa = self.fixture(root); original = ipa.read_bytes()
            (root / 'credentials.json').write_text('synthetic private file must not be copied')
            destination = root / 'published'; self.publish(root, ipa, destination)
            expected = {'Telegram.ipa', 'manifest.json', 'SHA256SUMS', *licensing.MATERIAL_NAMES}
            self.assertEqual({path.name for path in destination.iterdir()}, expected)
            self.assertEqual((destination / 'Telegram.ipa').read_bytes(), original)
            self.assertEqual(ipa.read_bytes(), original)
            self.assertEqual((destination / 'COPYING').read_bytes(), (PROJECT / 'COPYING').read_bytes())
            self.assertEqual((destination / 'LICENSE-NOTICE.md').read_bytes(), (PROJECT / 'docs/fork/LICENSE-NOTICE.md').read_bytes())
            materials = licensing.read_materials(destination, SOURCE)
            metadata = json.loads((destination / 'manifest.json').read_text())
            self.assertEqual(metadata['license_materials'], licensing.validate_materials(materials, SOURCE))
            sums = dict(line.split('  ', 1)[::-1] for line in (destination / 'SHA256SUMS').read_text().splitlines())
            self.assertEqual(set(sums), expected - {'SHA256SUMS'})
            for name, digest in sums.items():
                self.assertEqual(hashlib.sha256((destination / name).read_bytes()).hexdigest(), digest)
            self.assertIn(SOURCE, (destination / 'CORRESPONDING-SOURCE.md').read_text())
            self.assertFalse(metadata['runtime_verified'])
            self.assertFalse(metadata['signature_verified'])

    def test_missing_symlink_and_nonregular_required_materials_fail_before_publication(self):
        for kind in ('missing-copying', 'missing-notice', 'copying-link', 'notice-link', 'docs-link', 'notice-fifo'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); ipa = self.fixture(root)
                copying = root / 'COPYING'; notice = root / 'docs/fork/LICENSE-NOTICE.md'
                if kind == 'missing-copying': copying.unlink()
                if kind == 'missing-notice': notice.unlink()
                if kind in {'copying-link', 'notice-link'}:
                    path = copying if kind == 'copying-link' else notice
                    private = root / 'private-material'; path.rename(private); path.symlink_to(private)
                if kind == 'docs-link':
                    (root / 'docs').rename(root / 'real-docs'); (root / 'docs').symlink_to(root / 'real-docs', target_is_directory=True)
                if kind == 'notice-fifo': notice.unlink(); os.mkfifo(notice)
                with self.assertRaises(baseline.InvalidSetup): self.publish(root, ipa, root / 'published')
                self.assertFalse((root / 'published').exists())
                self.assertEqual(list(root.glob('.fork-artifact-*')), [])

    def test_oversize_or_private_notice_is_rejected_without_copying_or_disclosing_it(self):
        for private in (b'x' * 65537, ('-----BEGIN ' + 'PRIVATE KEY-----\nsynthetic-private-value').encode(),
                        ('api_' + 'hash = "' + '1234567890abcdef' * 2 + '"').encode()):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); ipa = self.fixture(root)
                (root / 'docs/fork/LICENSE-NOTICE.md').write_bytes(private)
                with self.assertRaises(baseline.InvalidSetup) as raised:
                    self.publish(root, ipa, root / 'published')
                self.assertNotIn('synthetic-private-value', str(raised.exception))
                self.assertFalse((root / 'published').exists())
                self.assertEqual(list(root.glob('.fork-artifact-*')), [])

    def test_failed_material_copy_removes_hidden_partial_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ipa = self.fixture(root)
            def fail_after_first(materials, staging):
                (staging / 'COPYING').write_bytes(materials['COPYING'])
                raise OSError('synthetic disk-full failure')
            with patch.object(baseline, 'write_materials', side_effect=fail_after_first):
                with self.assertRaises(OSError): self.publish(root, ipa, root / 'published')
            self.assertFalse((root / 'published').exists())
            self.assertEqual(list(root.glob('.fork-artifact-*')), [])


if __name__ == '__main__': unittest.main()
