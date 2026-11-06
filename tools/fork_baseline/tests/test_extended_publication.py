import base64
import difflib
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publish_extended_retention as publish


class ExtendedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '--allow-empty', '-qm', 'owned fixture'], cwd=self.root, check=True)
        self.name = 'docs/fork/owned-fixture.md'
        self.content = 'Synthetic local source fixture.\n'

    def request(self, name=None, after=None):
        name = name or self.name
        patch = ''.join(difflib.unified_diff([], self.content.splitlines(keepends=True), fromfile='/dev/null', tofile='b/' + name)).encode()
        return {'schema': 1, 'repository': publish.REPOSITORY, 'branch': publish.BRANCH,
                'patch_gzip_base64': base64.b64encode(gzip.compress(patch, mtime=0)).decode(),
                'patch_sha256': hashlib.sha256(patch).hexdigest(),
                'files': [{'path': name, 'before_sha256': None,
                           'after_sha256': after or hashlib.sha256(self.content.encode()).hexdigest()}]}

    def test_exact_new_source_is_staged_without_commit_or_push(self):
        before = publish.git(self.root, 'rev-parse', 'HEAD')
        self.assertEqual(publish.apply_request(self.root, self.request()), [self.name])
        self.assertEqual((self.root / self.name).read_text(), self.content)
        self.assertEqual(publish.git(self.root, 'rev-parse', 'HEAD'), before)

    def test_foreign_repository_branch_and_unknown_schema_reject_before_write(self):
        for field, value in [('schema', 2), ('repository', 'other/repo'), ('branch', 'master')]:
            with self.subTest(field=field):
                request = self.request(); request[field] = value
                with self.assertRaises(ValueError): publish.apply_request(self.root, request)
                self.assertFalse((self.root / self.name).exists())

    def test_unsafe_workflow_credentials_dependency_and_self_mutations_reject(self):
        for name in ['../escape', '/absolute', '.github/workflows/other.yml', '.git/config',
                     'MODULE.bazel', '.env', publish.REQUEST,
                     'tools/fork_baseline/publish_extended_retention.py', 'docs//fork/bad.md']:
            with self.subTest(name=name):
                with self.assertRaises(ValueError): publish.apply_request(self.root, self.request(name=name))

    def test_checksum_mismatch_and_duplicate_manifest_reject(self):
        request = self.request(); request['patch_sha256'] = '0' * 64
        with self.assertRaises(ValueError): publish.apply_request(self.root, request)
        request = self.request(); request['files'] *= 2
        with self.assertRaises(ValueError): publish.apply_request(self.root, request)
        self.assertFalse((self.root / self.name).exists())

    def test_existing_changed_source_and_dirty_checkout_reject(self):
        (self.root / 'owned.txt').write_text('do not overwrite')
        with self.assertRaises(ValueError): publish.apply_request(self.root, self.request())
        self.assertEqual((self.root / 'owned.txt').read_text(), 'do not overwrite')

    def test_final_hash_mismatch_never_creates_a_commit(self):
        before = publish.git(self.root, 'rev-parse', 'HEAD')
        with self.assertRaises(ValueError): publish.apply_request(self.root, self.request(after='0' * 64))
        self.assertEqual(publish.git(self.root, 'rev-parse', 'HEAD'), before)

    def test_patch_cannot_hide_a_second_unlisted_file(self):
        request = self.request()
        raw = gzip.decompress(base64.b64decode(request['patch_gzip_base64']))
        raw += b'--- /dev/null\n+++ b/docs/fork/extra.md\n@@ -0,0 +1 @@\n+extra\n'
        request['patch_gzip_base64'] = base64.b64encode(gzip.compress(raw)).decode()
        request['patch_sha256'] = hashlib.sha256(raw).hexdigest()
        before = publish.git(self.root, 'rev-parse', 'HEAD')
        with self.assertRaises(ValueError): publish.apply_request(self.root, request)
        self.assertEqual(publish.git(self.root, 'rev-parse', 'HEAD'), before)

    def test_oversize_expansion_rejects_before_applying(self):
        request = self.request(); raw = b'a' * (publish.MAX_PATCH + 1)
        request['patch_gzip_base64'] = base64.b64encode(gzip.compress(raw)).decode()
        request['patch_sha256'] = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(ValueError): publish.apply_request(self.root, request)
        self.assertFalse((self.root / self.name).exists())
