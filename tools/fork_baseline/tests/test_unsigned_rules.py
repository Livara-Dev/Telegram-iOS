import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('unsigned_rules', Path(__file__).resolve().parents[1] / 'unsigned_rules.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UnsignedRulesTests(unittest.TestCase):
    def test_patch_requires_reviewed_digest_and_exact_site_count(self):
        source = ('def implementation():\n' + module.BEFORE + '\n                actions = actions,\n            ),\n        )\n').encode()
        digest = hashlib.sha256(source).hexdigest()
        result = module.patch_source(source, digest, 1).decode()
        self.assertIn('"ayugram.unsigned_device" not in platform_prerequisites.features', result)
        self.assertIn('partials.provisioning_profile_partial(', result)
        with self.assertRaises(ValueError):
            module.patch_source(source + b'# changed', digest, 1)
        with self.assertRaises(ValueError):
            module.patch_source(source, digest, 2)

    def test_private_copy_is_pinned_and_excludes_untracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'source'
            root.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            path = 'apple/internal/ios_rules.bzl'
            source = ('def implementation():\n' + module.BEFORE + '\n                actions = actions,\n            ),\n        )\n').encode()
            file = root / path
            file.parent.mkdir(parents=True)
            file.write_bytes(source)
            (root / 'MODULE.bazel').write_text('module(name="rules_apple")\n')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            pin = git('rev-parse', 'HEAD')
            (root / 'untracked.txt').write_text('must not be copied')
            digest = hashlib.sha256(source).hexdigest()
            with patch.object(module, 'PINNED_COMMIT', pin), patch.object(module, 'SOURCES', {path: (digest, 1)}):
                destination = Path(temp) / 'overlay'
                result = module.prepare_unsigned_rules(root, destination)
                self.assertFalse(result['signed_builds_affected'])
                self.assertEqual(result['source_commit'], pin)
                self.assertEqual(file.read_bytes(), source)
                self.assertFalse((destination / 'untracked.txt').exists())
                self.assertFalse((destination / '.git').exists())
                self.assertIn(module.AFTER, (destination / path).read_text())
                with self.assertRaises(ValueError):
                    module.prepare_unsigned_rules(root, destination)
                file.write_bytes(source + b'# tamper')
                with self.assertRaises(ValueError):
                    module.prepare_unsigned_rules(root, Path(temp) / 'tampered')
                file.write_bytes(source)
            with self.assertRaises(ValueError):
                module.prepare_unsigned_rules(root, Path(temp) / 'wrong-revision')
