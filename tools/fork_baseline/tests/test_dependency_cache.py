import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dependency_cache as cache
import baseline


class PublicDependencyCacheTests(unittest.TestCase):
    def put(self, root, data, digest=None):
        digest = digest or hashlib.sha256(data).hexdigest()
        path = root / 'content_addressable/sha256' / digest / 'file'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return digest, path

    def fixture(self, root):
        temp = root / 'runner'; temp.mkdir()
        repo = root / 'repo'; repo.mkdir()
        for name in ('MODULE.bazel', 'MODULE.bazel.lock', 'versions.json'):
            (repo / name).write_text('{}')
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'Livara-Dev/Telegram-iOS',
               'GITHUB_REF': 'refs/heads/ci/verify-latest', 'GITHUB_EVENT_NAME': 'push',
               'RUNNER_TEMP': str(temp), 'GITHUB_ENV': str(root / 'env')}
        subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
        self.commit_pins(repo)
        return env, repo

    def commit_pins(self, repo):
        subprocess.run(['git', 'add', 'MODULE.bazel', 'MODULE.bazel.lock', 'versions.json'], cwd=repo, check=True)
        subprocess.run(['git', '-c', 'user.name=Public cache test', '-c', 'user.email=cache-test@example.invalid',
                        'commit', '-q', '--no-gpg-sign', '-m', 'Test public dependency pins'], cwd=repo, check=True)

    def test_private_configuration_and_build_products_never_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / 'active', root / 'export'
            public, _ = self.put(source, b'pinned public dependency')
            private, _ = self.put(source, b'private API constants, keys, sessions and archived media')
            (source / 'configuration.json').write_text('private credentials')
            result = cache.copy_pinned(source, dest, {public})
            self.assertEqual(result, {'files': 1, 'bytes': len(b'pinned public dependency')})
            self.assertTrue((dest / 'content_addressable/sha256' / public / 'file').exists())
            self.assertFalse((dest / 'content_addressable/sha256' / private).exists())
            self.assertFalse((dest / 'configuration.json').exists())

    def test_restored_corrupt_bytes_and_symlinks_are_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / 'cache', root / 'live'
            good, blob = self.put(source, b'known public bytes')
            blob.write_bytes(b'poisoned cache bytes')
            outside = root / 'private'; outside.write_bytes(b'private')
            symlink_digest = hashlib.sha256(b'private').hexdigest()
            folder = source / 'content_addressable/sha256' / symlink_digest
            folder.mkdir(); (folder / 'file').symlink_to(outside)
            result = cache.copy_pinned(source, dest, {good, symlink_digest})
            self.assertEqual(result['files'], 0)
            self.assertEqual(list(dest.iterdir()), [])

    def test_symlinked_intermediate_cas_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, outside = root / 'cache', root / 'outside'
            source.mkdir(); outside.mkdir()
            (source / 'content_addressable').symlink_to(outside, target_is_directory=True)
            (outside / 'sha256').mkdir()
            with self.assertRaises(ValueError):
                cache.copy_pinned(source, root / 'live', set())

    def test_only_empty_canonical_markers_survive_with_a_verified_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / 'cache', root / 'live'
            digest, blob = self.put(source, b'public')
            valid = 'id-' + '1' * 64
            (blob.parent / valid).touch()
            (blob.parent / ('id-' + '2' * 64)).write_bytes(b'secret')
            (blob.parent / 'arbitrary-session').write_bytes(b'secret')
            cache.copy_pinned(source, dest, {digest})
            self.assertEqual(sorted(p.name for p in (dest / 'content_addressable/sha256' / digest).iterdir()), ['file', valid])

    def test_verified_locked_source_metadata_admits_archive_and_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, dest = root / 'cache', root / 'live'
            archive = b'public module archive'
            patch = b'public module patch'
            integrity = lambda b: 'sha256-' + base64.b64encode(hashlib.sha256(b).digest()).decode()
            metadata = json.dumps({'url': 'https://github.com/public/dependency/archive/source.tar.gz',
                                   'integrity': integrity(archive), 'patches': {'a.patch': integrity(patch)}}).encode()
            pin, _ = self.put(source, metadata)
            self.put(source, archive); self.put(source, patch)
            result = cache.copy_pinned(source, dest, {pin})
            self.assertEqual(result['files'], 3)
            # The same metadata without a checked-in hash cannot authorize blobs.
            self.assertEqual(cache.copy_pinned(source, root / 'empty', set())['files'], 0)

    def test_prepare_revalidates_cache_and_export_only_uses_public_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); env, repo = self.fixture(root)
            base = cache.cache_root(env)
            data = b'public toolchain'
            digest, _ = self.put(base / 'portable', data)
            (repo / 'MODULE.bazel').write_text('sha256 = "' + digest + '"')
            self.commit_pins(repo)
            restored = cache.prepare(env, repo)
            self.assertEqual(restored['files'], 1)
            env['FORK_BASELINE_PUBLIC_CACHE'] = str(base / 'live')
            self.assertEqual(cache.active_path(env), base / 'live')
            self.put(base / 'live', b'unpinned private generated configuration')
            exported = cache.export(env, repo)
            self.assertEqual(exported['files'], 1)
            self.assertEqual((base / 'portable/content_addressable/sha256' / digest / 'file').read_bytes(), data)
            self.assertIn('FORK_BASELINE_PUBLIC_CACHE=', (root / 'env').read_text())

    def test_untrusted_workflows_and_path_overrides_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            env, _ = self.fixture(Path(directory))
            for key, value in [('GITHUB_REPOSITORY', 'attacker/fork'), ('GITHUB_REF', 'refs/pull/1/merge'),
                               ('GITHUB_EVENT_NAME', 'pull_request'), ('GITHUB_ACTIONS', 'false')]:
                altered = dict(env, **{key: value})
                with self.subTest(key=key), self.assertRaises(ValueError):
                    cache.cache_root(altered)
            env['FORK_BASELINE_PUBLIC_CACHE'] = env['RUNNER_TEMP']
            with self.assertRaises(ValueError): cache.active_path(env)
            self.assertIsNone(cache.active_path({}))

    def test_proxy_keeps_action_cache_disabled_and_owns_repository_cache_flag(self):
        command = baseline.proxy_arguments(Path('/bazel'), Path('/repo'), 'unsigned-device',
            ['build', '--repository_cache=/untrusted', '--repository_cache', '/second', '//Telegram:Telegram'],
            repository_cache=Path('/verified-public-downloads'))
        self.assertEqual([v for v in command if v.startswith('--repository_cache=')], ['--repository_cache=/verified-public-downloads'])
        self.assertNotIn('/second', command)
        self.assertIn('--disk_cache=', command)
        self.assertIn('--remote_cache=', command)
        self.assertIn('--features=disable_legacy_signing', command)

    def test_generated_worktree_changes_cannot_authorize_private_cache_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            env, repo = self.fixture(Path(directory))
            digest = hashlib.sha256(b'private configuration').hexdigest()
            (repo / 'MODULE.bazel').write_text('sha256 = "' + digest + '"')
            self.assertNotIn(digest, cache.pinned_hashes(repo))
