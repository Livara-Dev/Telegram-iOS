"""Fixed GPL/source materials for a paired build artifact, not legal clearance.

Only COPYING and the reviewed fork notice are read from the checkout. No source
archive, generated configuration, credentials, or unrelated file is collected.
"""
import hashlib
import os
from pathlib import Path
import re
import stat

MATERIAL_NAMES = ('COPYING', 'LICENSE-NOTICE.md', 'CORRESPONDING-SOURCE.md')
MAX_MATERIAL_BYTES = 64 * 1024
COPYING_SHA256 = 'b409e426c009da876fc30b51ac3d9d6fcb965fcad27b53710d549b290331a853'


class InvalidLicenseMaterials(ValueError):
    pass


def _commit(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{40}', value) is None:
        raise InvalidLicenseMaterials('A full immutable source commit is required')
    return value


def source_notice(source_commit):
    """Return an exact, credential-free source locator for the fixed fork."""
    source_commit = _commit(source_commit)
    return f'''# Corresponding source for this artifact

Source repository: https://github.com/Livara-Dev/Telegram-iOS
Exact source commit: `{source_commit}`
Source tree: https://github.com/Livara-Dev/Telegram-iOS/tree/{source_commit}

Retrieve the exact source and recursively pinned dependencies:

```sh
git clone --no-checkout https://github.com/Livara-Dev/Telegram-iOS.git
cd Telegram-iOS
git checkout --detach {source_commit}
git submodule init
git config submodule.submodules/rlottie/rlottie.url https://github.com/TelegramMessenger/rlottie.git
git config submodule.submodules/TgVoipWebrtc/tgcalls.url https://github.com/TelegramMessenger/tgcalls.git
git submodule update --init --recursive
```

Use the toolchain versions and Bazel checksum recorded in `versions.json` at
that commit. The application build uses `build-system/Make/Make.py` through the
reviewed wrapper `tools/fork_baseline/baseline.py`. Hosted macOS reproduction,
tests, artifact checksums, and startup checks are specified in
`.github/workflows/archive-integration.yml` at the same commit. Read the build
and Windows installation documentation under `docs/` before building/signing.

Provision your own registered Telegram API credentials securely for the build;
do not commit or publish them. Unsigned device compilation uses
`python3 tools/fork_baseline/baseline.py unsigned-device`. Simulator qualification
uses `python3 tools/fork_baseline/baseline.py simulator --integration-tests`.
A device package requires independent signing with your own authorized identity;
no Apple private keys, profiles, Telegram authentication/session data, or API
credential values are included in these materials.

The accompanying `COPYING` is the unmodified GNU GPL version 2 text. Preserve
`LICENSE-NOTICE.md`, upstream notices, component licenses, modifications, and
build/installation scripts when supplying corresponding source. Recipients must
actually be able to obtain the exact corresponding source through a
GPL-compliant route; this repository locator alone does not establish access or
satisfy every source-distribution obligation. A source bundle should remain
paired with this immutable revision and independently signed artifact.

Presence and hashes of these three materials are verified mechanically. This is
not a legal certification, a complete third-party license inventory, or a finding
that every distribution service's terms are compatible with the GPL.
'''.encode('utf-8')


def _text(data):
    if type(data) is not bytes or not 0 < len(data) <= MAX_MATERIAL_BYTES:
        raise InvalidLicenseMaterials('License material is missing or exceeds the fixed bounds')
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise InvalidLicenseMaterials('License material must be safe UTF-8 text') from None
    if any(ord(character) < 32 and character not in '\n\r\t' for character in text):
        raise InvalidLicenseMaterials('License material contains unsupported control characters')
    return text


def _notice_is_safe(text):
    # Do not normalize/redact a notice: reject it rather than silently conveying
    # changed licensing text. Fixture tests construct forbidden input at runtime.
    forbidden = (
        r'-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-----',
        r'\b(?:gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b',
        r'''(?im)["']?\b(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PRIVATE_KEY)[A-Z0-9_]*|(?:TELEGRAM_)?API_(?:HASH|ID)|AUTHORIZATION)["']?\s*[:=]\s*\S''',
        r'https?://[^\s/@:]+:[^\s/@]+@',
    )
    if any(re.search(pattern, text) for pattern in forbidden):
        raise InvalidLicenseMaterials('Credential or private-key content is forbidden in license material')


def validate_materials(materials, source_commit):
    """Validate all fixed bytes and return their closed size/hash metadata."""
    source_commit = _commit(source_commit)
    if not isinstance(materials, dict) or set(materials) != set(MATERIAL_NAMES):
        raise InvalidLicenseMaterials('Exactly the three fixed license materials are required')
    texts = {name: _text(materials[name]) for name in MATERIAL_NAMES}
    if hashlib.sha256(materials['COPYING']).hexdigest() != COPYING_SHA256:
        raise InvalidLicenseMaterials('COPYING does not match the reviewed unmodified GPL text')
    if not texts['LICENSE-NOTICE.md'].startswith('# License notice for the fork checkpoint\n'):
        raise InvalidLicenseMaterials('The reviewed fork license notice is required')
    _notice_is_safe(texts['LICENSE-NOTICE.md'])
    if materials['CORRESPONDING-SOURCE.md'] != source_notice(source_commit):
        raise InvalidLicenseMaterials('Corresponding-source notice does not match the exact source commit')
    return {name: {'sha256': hashlib.sha256(materials[name]).hexdigest(), 'size_bytes': len(materials[name])}
            for name in MATERIAL_NAMES}


def _root_fd(directory):
    root = Path(directory)
    if root.is_symlink():
        raise InvalidLicenseMaterials('License material directory must not be a symlink')
    # macOS /var and /tmp aliases above this approved root are legitimate.
    root = root.resolve(strict=True)
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read(root_descriptor, parts):
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            info = os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MATERIAL_BYTES:
                raise InvalidLicenseMaterials('Required license material must be a bounded regular file')
            data = os.read(child, MAX_MATERIAL_BYTES + 1)
            if len(data) != info.st_size:
                raise InvalidLicenseMaterials('Required license material could not be read completely')
        finally:
            os.close(child)
        _text(data)
        return data
    finally:
        os.close(descriptor)


def material_bytes(root, source_commit):
    """Read only the fixed checkout files and generate the exact source notice."""
    _commit(source_commit)
    descriptor = None
    try:
        descriptor = _root_fd(root)
        materials = {'COPYING': _read(descriptor, ('COPYING',)),
                     'LICENSE-NOTICE.md': _read(descriptor, ('docs', 'fork', 'LICENSE-NOTICE.md')),
                     'CORRESPONDING-SOURCE.md': source_notice(source_commit)}
        validate_materials(materials, source_commit)
        return materials
    except (OSError, RuntimeError, TypeError):
        raise InvalidLicenseMaterials('Required checkout license material is unavailable or unsafe') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_materials(directory, source_commit):
    """Read fixed root-level materials from an already verified source artifact."""
    _commit(source_commit)
    descriptor = None
    try:
        descriptor = _root_fd(directory)
        materials = {name: _read(descriptor, (name,)) for name in MATERIAL_NAMES}
        validate_materials(materials, source_commit)
        return materials
    except (OSError, RuntimeError, TypeError):
        raise InvalidLicenseMaterials('Required artifact license material is unavailable or unsafe') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_materials(materials, staging):
    """Write the validated fixed set exclusively; roll back this call on failure."""
    if not isinstance(materials, dict) or set(materials) != set(MATERIAL_NAMES):
        raise InvalidLicenseMaterials('Exactly the three fixed license materials are required')
    text = _text(materials['CORRESPONDING-SOURCE.md'])
    match = re.findall(r'^Exact source commit: `([0-9a-f]{40})`$', text, re.M)
    if len(match) != 1:
        raise InvalidLicenseMaterials('An exact corresponding-source notice is required')
    metadata = validate_materials(materials, match[0])
    descriptor, created, completed = None, [], False
    try:
        descriptor = _root_fd(staging)
        for name in MATERIAL_NAMES:
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise InvalidLicenseMaterials('License material destination already exists')
        for name in MATERIAL_NAMES:
            child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=descriptor)
            created.append(name)
            with os.fdopen(child, 'wb') as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(materials[name])
                output.flush()
                os.fsync(output.fileno())
        completed = True
        return metadata
    except (OSError, RuntimeError, TypeError):
        raise InvalidLicenseMaterials('License materials could not be written safely') from None
    finally:
        if descriptor is not None:
            # Returning after success preserves all three; exceptional paths
            # remove only the entries created exclusively by this invocation.
            if not completed:
                for name in created:
                    try:
                        os.unlink(name, dir_fd=descriptor)
                    except OSError:
                        pass
            os.close(descriptor)
