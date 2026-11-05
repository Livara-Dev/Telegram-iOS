#!/usr/bin/env python3
"""Materialize only the hash-pinned synthetic H.264 fixture, never user media."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DIGEST = 'f45c335a8a3c11931a15e98022bc39e4d4db517f6758357071ea8f0456abae30'


def fixture_bytes():
    source = (ROOT / 'submodules/LocalMessageArchive/Tests/LocalArchivePlaybackTests/ArchivePlaybackTests.swift').read_text()
    match = re.search(r'Data\(base64Encoded: "([A-Za-z0-9+/=]+)"\)', source)
    if match is None:
        raise ValueError('Pinned synthetic fixture is missing')
    data = base64.b64decode(match.group(1), validate=True)
    data += bytes([0, 2, 0, 8, 0x66, 0x72, 0x65, 0x65]) + bytes(128 * 1024)
    if hashlib.sha256(data).hexdigest() != DIGEST:
        raise ValueError('Synthetic fixture changed; review its provenance before probing')
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    payload = fixture_bytes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as target:
        target.write(payload)
    print(json.dumps({'synthetic_fixture': True, 'sha256': DIGEST, 'bytes': len(payload)}))
