"""Guard SQLCipher's public cancellation API; native link/runtime proof is separate."""
import hashlib
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
# Hash the reviewed files before removing their one omit option. Re-inserting
# only that exact line must reproduce them, so crypto flags, SDKs, dependencies,
# Package metadata and every other byte stay unchanged by this repair.
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


class ArchiveSQLiteABITests(unittest.TestCase):
    def test_bazel_and_swiftpm_enable_progress_api_with_no_other_build_changes(self):
        self.assertEqual(set(SURFACES), {'submodules/sqlcipher/BUILD', 'submodules/sqlcipher/Package.swift'})
        for relative, (anchor, omitted, original_hash) in SURFACES.items():
            with self.subTest(surface=relative):
                data = (ROOT / relative).read_bytes()
                self.assertNotIn(b'SQLITE_OMIT_PROGRESS_CALLBACK', data)
                self.assertEqual(data.count(anchor), 1)
                restored = data.replace(anchor, anchor + omitted)
                self.assertEqual(hashlib.sha256(restored).hexdigest(), original_hash,
                                 'The cancellation ABI repair must preserve all other build bytes')

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
