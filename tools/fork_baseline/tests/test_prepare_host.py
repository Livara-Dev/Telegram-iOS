import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_host


class VerifiedDownloadTests(unittest.TestCase):
    def test_correct_digest_installs_executable(self):
        data = b"synthetic tool fixture"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bazel"
            with patch("prepare_host.urllib.request.urlopen", return_value=io.BytesIO(data)):
                prepare_host.verified_download("https://example.invalid/tool", target, hashlib.sha256(data).hexdigest())
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertFalse(target.with_suffix(".download").exists())

    def test_tamper_cannot_replace_existing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bazel"
            target.write_bytes(b"existing verified fixture")
            with patch("prepare_host.urllib.request.urlopen", return_value=io.BytesIO(b"tampered")):
                with self.assertRaises(ValueError):
                    prepare_host.verified_download("https://example.invalid/tool", target, "0" * 64)
            self.assertEqual(target.read_bytes(), b"existing verified fixture")
            self.assertFalse(target.with_suffix(".download").exists())

    def test_invalid_pin_never_downloads(self):
        with patch("prepare_host.urllib.request.urlopen") as download:
            with self.assertRaises(ValueError):
                prepare_host.verified_download("https://example.invalid/tool", "unused", "not-a-hash")
            download.assert_not_called()

    def test_existing_staging_file_is_not_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bazel"
            staged = target.with_suffix(".download")
            staged.write_bytes(b"another operation")
            with patch("prepare_host.urllib.request.urlopen", return_value=io.BytesIO(b"test")):
                with self.assertRaises(FileExistsError):
                    prepare_host.verified_download("https://example.invalid/tool", target, "0" * 64)
            self.assertEqual(staged.read_bytes(), b"another operation")


if __name__ == "__main__":
    unittest.main()
