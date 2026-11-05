#!/usr/bin/env python3
"""Share only checked-in-hash-pinned public Bazel downloads, never action outputs.

The GitHub cache is not trusted. Import and export both copy regular CAS files
whose SHA-256 matches an allowlist from the checked-in dependency declarations.
Generated repositories, API configuration, build products and signing data do
not enter this directory. No remote service is contacted by this helper.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HEX = re.compile(r"[0-9a-f]{64}")
NAMESPACE = 'ayugram-public-dependencies-v1'


def _regular(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _directory(path):
    if path.is_symlink():
        raise ValueError('Dependency cache directory must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ValueError('Invalid dependency cache directory')
    return path


def _digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def cache_root(env):
    if (env.get('GITHUB_ACTIONS') != 'true' or
            env.get('GITHUB_REPOSITORY') != 'Livara-Dev/Telegram-iOS' or
            env.get('GITHUB_REF') != 'refs/heads/ci/verify-latest' or
            env.get('GITHUB_EVENT_NAME') not in {'push', 'workflow_dispatch'}):
        raise ValueError('Public dependency caching requires the trusted qualification branch')
    temporary = Path(env.get('RUNNER_TEMP', ''))
    if not temporary.is_absolute() or not temporary.is_dir():
        raise ValueError('A hosted runner temporary directory is required')
    return _directory(temporary.resolve() / NAMESPACE)


def active_path(env):
    requested = env.get('FORK_BASELINE_PUBLIC_CACHE')
    if not requested:
        return None
    expected = cache_root(env) / 'live'
    if Path(requested) != expected or expected.is_symlink() or not expected.is_dir():
        raise ValueError('Invalid public dependency cache path')
    return expected


def pinned_hashes(root):
    # Fixed, tracked public dependency inputs only. Never scan generated build
    # directories, environment variables or the generated API configuration.
    result = set()
    for name in ('MODULE.bazel', 'MODULE.bazel.lock', 'versions.json'):
        try:
            # Read immutable checkout objects, not a working file that a build
            # tool or a restored cache might have changed during compilation.
            content = subprocess.check_output(['git', 'show', 'HEAD:' + name],
                                              cwd=root, stderr=subprocess.DEVNULL, text=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Committed public dependency pin input is unavailable') from None
        result.update(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])', content))
    return result


def copy_pinned(source, destination, pins):
    """Reject restored/cache-written arbitrary files, links and corrupt blobs."""
    copied = 0
    byte_count = 0
    _directory(source)
    _directory(destination)
    source_cas = source / 'content_addressable' / 'sha256'
    if not source_cas.exists():
        return {'files': 0, 'bytes': 0}
    # Do not traverse symlinked intermediate directories.
    for parent in (source / 'content_addressable', source_cas):
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError('Invalid public dependency CAS layout')
    allowed = set(pins)
    # A lock-pinned source.json already in CAS can authorize its archive and
    # patches. Its metadata bytes must first match the checked-in lock hash.
    for digest in list(allowed):
        folder = source_cas / digest
        blob = folder / 'file'
        if folder.is_symlink() or not _regular(blob) or blob.stat().st_size > 256 * 1024:
            continue
        if _digest(blob) != digest:
            continue
        try:
            metadata = json.loads(blob.read_bytes())
            if not isinstance(metadata, dict) or not isinstance(metadata.get('url'), str):
                continue
            # Only the public BCR archive metadata shape is accepted. Digest
            # verification remains mandatory for every admitted archive.
            integrities = [metadata.get('integrity', '')]
            patches = metadata.get('patches', {})
            if isinstance(patches, dict):
                integrities.extend(patches.values())
            for integrity in integrities:
                if not isinstance(integrity, str) or not integrity.startswith('sha256-'):
                    continue
                value = base64.b64decode(integrity[7:], validate=True)
                if len(value) == 32:
                    allowed.add(value.hex())
        except (ValueError, UnicodeError, TypeError):
            continue
    for digest in sorted(allowed):
        folder = source_cas / digest
        blob = folder / 'file'
        if folder.is_symlink() or not _regular(blob) or _digest(blob) != digest:
            continue
        target = destination / 'content_addressable' / 'sha256' / digest
        _directory(target.parent.parent)
        _directory(target.parent)
        _directory(target)
        # Copy bytes; never retain hard links to the active build workspace.
        target_blob = target / 'file'
        if os.path.lexists(target_blob):
            raise ValueError('Dependency cache destination is not empty')
        shutil.copyfile(blob, target_blob, follow_symlinks=False)
        if not _regular(target_blob) or _digest(target_blob) != digest:
            target_blob.unlink()
            continue
        os.chmod(target_blob, 0o600)
        copied += 1
        byte_count += blob.stat().st_size
        # Bazel's optional canonical-ID markers contain no bytes. They are
        # needed for repository rules that use a nonempty canonical ID.
        for marker in folder.iterdir():
            if re.fullmatch(r'id-[0-9a-f]{64}', marker.name) and _regular(marker) and marker.stat().st_size == 0:
                (target / marker.name).touch(mode=0o600)
    return {'files': copied, 'bytes': byte_count}


def prepare(env, root=ROOT):
    base = cache_root(env)
    live = _directory(base / 'live')
    # Fresh hosted runners only: a prepared cache must not merge arbitrary
    # preexisting files into the active Bazel cache.
    if any(live.iterdir()):
        raise ValueError('Dependency cache is already active')
    result = copy_pinned(base / 'portable', live, pinned_hashes(root))
    with Path(env['GITHUB_ENV']).open('a') as output:
        output.write('FORK_BASELINE_PUBLIC_CACHE=' + str(live) + '\n')
    return result


def export(env, root=ROOT):
    base = cache_root(env)
    live = _directory(base / 'live')
    portable = _directory(base / 'portable')
    with tempfile.TemporaryDirectory(prefix='.public-cache-', dir=base) as staging:
        result = copy_pinned(live, Path(staging), pinned_hashes(root))
        # rmtree does not follow inner symlinks; the fixed root was validated.
        shutil.rmtree(portable)
        shutil.copytree(staging, portable, symlinks=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'export'))
    args = parser.parse_args()
    result = prepare(os.environ) if args.action == 'prepare' else export(os.environ)
    print(json.dumps({'public_dependency_cache': args.action, **result,
                      'compiled_outputs_cached': False, 'performance_measured': False}))


if __name__ == '__main__':
    main()
