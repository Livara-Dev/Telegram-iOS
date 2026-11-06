"""Guard SQLCipher's public cancellation API; native link/runtime proof is separate."""
import hashlib
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
# Hash the reviewed files before removing their one omit option. Re-inserting
# only that exact line must reproduce them, so crypto flags, SDKs, dependencies,
# Package metadata and every other byte stay unchanged by this repair. The
# separately reviewed GPL notice is the only additional permitted metadata.
SURFACES = {
    'submodules/sqlcipher/BUILD': (
        b'        "-DSQLITE_OMIT_DECLTYPE",\n',
        b'        "-DSQLITE_OMIT_PROGRESS_CALLBACK",\n',
        '19577646f79403ae92be88f7ef07bded499fc0f40b163710a9c5b5f778ca16dd'),
    'submodules/sqlcipher/Package.swift': (
        b'                .define("SQLITE_OMIT_DECLTYPE", to: "1"),\n',
        b'                .define("SQLITE_OMIT_PROGRESS_CALLBACK", to: "1"),\n',
        'e87dcdf9fb0571d1bd7c2e46669766e9c0c92ee5b9c0f17eb0dfbe0c1050356c'),
}
NOTICE_TEXT = (
    'Modified for AyuGram iOS.\n',
    'Modification dates recorded in Git history: 2026-11-06.\n',
    "See this file's history for the corresponding changes.\n",
)


class ArchiveSQLiteABITests(unittest.TestCase):
    def assert_reviewed_surface(self, relative, data):
        anchor, omitted, original_hash = SURFACES[relative]
        prefix = b'// swift-tools-version:5.5\n' if relative.endswith('.swift') else b''
        marker = '// ' if prefix else '# '
        notice = ''.join(marker + line for line in NOTICE_TEXT).encode()
        # Remove exactly the audited dated notice at its fixed position. Never
        # strip arbitrary comments or replace the original crypto/build hash.
        self.assertTrue(data.startswith(prefix + notice))
        body = prefix + data[len(prefix + notice):]
        self.assertNotIn(b'SQLITE_OMIT_PROGRESS_CALLBACK', body)
        self.assertEqual(body.count(anchor), 1)
        restored = body.replace(anchor, anchor + omitted)
        self.assertEqual(hashlib.sha256(restored).hexdigest(), original_hash,
                         'Only the reviewed notice and cancellation ABI line may differ')

    def test_bazel_and_swiftpm_enable_progress_api_with_no_other_build_changes(self):
        self.assertEqual(set(SURFACES), {'submodules/sqlcipher/BUILD', 'submodules/sqlcipher/Package.swift'})
        for relative in SURFACES:
            with self.subTest(surface=relative):
                self.assert_reviewed_surface(relative, (ROOT / relative).read_bytes())

    def test_notice_metadata_cannot_hide_crypto_flags_other_changes_or_a_changed_notice(self):
        for relative in SURFACES:
            original = (ROOT / relative).read_bytes()
            self.assertEqual(original.count(b'SQLITE_HAS_CODEC'), 1)
            for changed in (original.replace(b'SQLITE_HAS_CODEC', b'SQLITE_WITHOUT_CODEC'),
                            original + b'\n', original.replace(b'2026-11-06', b'2026-11-07')):
                with self.subTest(surface=relative), self.assertRaises(AssertionError):
                    self.assert_reviewed_surface(relative, changed)

    def test_pinned_sqlcipher_declaration_and_definition_have_the_same_public_name(self):
        header = (ROOT / 'submodules/sqlcipher/PublicHeaders/sqlcipher/sqlite3.h').read_text()
        source = (ROOT / 'submodules/sqlcipher/Sources/sqlite3.c').read_text()
        self.assertRegex(header, r'SQLITE_API void sqlite3_progress_handler\(sqlite3\*, int, int\(\*\)\(void\*\), void\*\);')
        self.assertRegex(source, r'(?s)#ifndef SQLITE_OMIT_PROGRESS_CALLBACK\n/\*.*?SQLITE_API void sqlite3_progress_handler\(')
        self.assertIn('db->xProgress = xProgress;', source)
        self.assertIn('db->nProgressOps = (unsigned)nOps;', source)

    def test_archive_keeps_real_progress_cancellation_and_explicit_callback_cleanup(self):
        source = (ROOT / 'submodules/LocalMessageArchive/Sources/LocalMessageArchive/ArchiveTransferDatabase.swift').read_text()
        self.assertIn('import sqlcipher', source)
        install = source.split('static func installCancellation(', 1)[1].split('static func clearCancellation(', 1)[0]
        self.assertIn('sqlite3_progress_handler(database, 1000,', install)
        self.assertIn('.takeUnretainedValue().isCancelled ? 1 : 0', install)
        self.assertIn('Unmanaged.passUnretained(cancellation).toOpaque()', install)
        clear = source.split('static func clearCancellation(', 1)[1].split('static func openSnapshot(', 1)[0]
        self.assertIn('sqlite3_progress_handler(database, 0, nil, nil)', clear)
        self.assertNotRegex(source, r'(?m)^import (?:SQLite3|CSQLite)\b')
        self.assertNotIn('dlsym(', source)
        build = (ROOT / 'submodules/LocalMessageArchive/BUILD').read_text()
        self.assertIn('deps = ["//submodules/sqlcipher"]', build)
