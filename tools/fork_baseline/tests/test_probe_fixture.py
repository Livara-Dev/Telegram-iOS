import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_probe_fixture as probe


class ProbeFixtureTests(unittest.TestCase):
    def test_only_pinned_synthetic_bytes_can_be_materialized(self):
        data = probe.fixture_bytes()
        self.assertEqual(len(data), 132769)
        self.assertEqual(hashlib.sha256(data).hexdigest(), probe.DIGEST)

    def test_changed_or_missing_source_never_becomes_decoder_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'submodules/LocalMessageArchive/Tests/LocalArchivePlaybackTests/ArchivePlaybackTests.swift'
            path.parent.mkdir(parents=True)
            with patch.object(probe, 'ROOT', root):
                for content in ['missing fixture', 'Data(base64Encoded: "AA==")']:
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        probe.fixture_bytes()
