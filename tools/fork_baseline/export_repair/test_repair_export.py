# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2026 AyuGram iOS contributors
# Added: 2026-11-06
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import repair_export as repair


class CopyOnlyTests(unittest.TestCase):
    def fixture(self, root):
        root.mkdir()
        ipa = b'fixture IPA bytes must survive evidence repair\x00\xff'
        (root / repair.IPA_NAME).write_bytes(ipa)
        (root / 'manifest.json').write_text(json.dumps({'sha256': hashlib.sha256(ipa).hexdigest()}))
        (root / 'delivery-provenance.json').write_text('{"preserve":"original provenance"}\n')
        (root / 'SHA256SUMS').write_text(''.join(repair.digest(p) + '  ' + p.name + '\n'
                                                 for p in sorted(root.iterdir())))
        return ipa

    def test_adds_nested_evidence_without_mutating_any_original_file_or_ipa(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            original = base / 'original'
            ipa = self.fixture(original)
            before = {p.name: p.read_bytes() for p in original.iterdir()}
            output = base / 'repaired'
            report = repair.copy_only_export(original, {'evidence/simulator/textformat/test.log': b'passed\n'}, {}, output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in original.iterdir()})
            self.assertEqual((output / repair.IPA_NAME).read_bytes(), ipa)
            for name, data in before.items():
                if name != 'SHA256SUMS':
                    self.assertEqual((output / name).read_bytes(), data)
            self.assertEqual(report['ipa_sha256_before'], report['ipa_sha256_after'])
            self.assertTrue(report['ipa_bytes_unchanged'])
            self.assertFalse(report['application_rebuilt'])
            repair.checksums(output)

    def test_rejects_replacing_ipa_original_metadata_or_traversing_output(self):
        for name in (repair.IPA_NAME, 'manifest.json', 'another.ipa', '../escape', '/absolute', 'a/../escape'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                original = base / 'original'
                self.fixture(original)
                with self.assertRaises(ValueError):
                    repair.copy_only_export(original, {name: b'bad'}, {}, base / 'repaired')
                self.assertFalse((base / 'repaired').exists())

    def test_rejects_corrupt_original_checksums_and_symlinks(self):
        for corruption in ('bytes', 'symlink'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                original = base / 'original'
                self.fixture(original)
                if corruption == 'bytes':
                    (original / repair.IPA_NAME).write_bytes(b'changed')
                else:
                    (original / 'link').symlink_to(original / repair.IPA_NAME)
                with self.assertRaises(ValueError):
                    repair.copy_only_export(original, {}, {}, base / 'repaired')
                self.assertFalse((base / 'repaired').exists())


if __name__ == '__main__':
    unittest.main()
