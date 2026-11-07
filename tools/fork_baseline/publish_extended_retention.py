#!/usr/bin/env python3
"""Apply one checksum-bound source review and publish only its integration branch.

The request contains an ordinary unified source diff. It cannot change workflows,
credentials, dependency versions or unlisted files. This tool never starts an iOS build.
"""
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

REPOSITORY = 'Livara-Dev/Telegram-iOS'
BRANCH = 'integration/ayu-current'
REQUEST = 'tools/fork_baseline/extended-retention-request.json'
PREFIXES = ('submodules/LocalMessageArchive/', 'submodules/TelegramCore/Sources/LocalArchive/',
            'submodules/TelegramCore/Tests/LocalArchive', 'submodules/SettingsUI/Sources/LocalArchive',
            'tools/fork_baseline/', 'docs/fork/')
FILES = {'tools/fork_audit/check.py',
         'submodules/TelegramCore/Sources/TelegramEngine/Messages/DeleteMessages.swift',
         'submodules/TelegramCore/Sources/State/ManagedAutoremoveMessageOperations.swift',
         'submodules/TelegramCore/Sources/TelegramEngine/Messages/MarkMessageContentAsConsumedInteractively.swift',
         'submodules/SettingsUI/Sources/LocalTimedMediaInformationController.swift',
         'submodules/SettingsUI/Sources/CustomeOptionsCatalog.swift'}
# Exact reviewed refinement seams; credentials, authentication and dependency versions remain out of scope.
FILES.update({
    'Telegram/BUILD',
    'Telegram/Telegram-iOS/ar.lproj/InfoPlist.strings',
    'Telegram/Telegram-iOS/ko.lproj/InfoPlist.strings',
    'submodules/SettingsUI/BUILD',
    'submodules/SettingsUI/Tests/LocalArchiveMediaPreviewTests.swift',
    'Tests/CustomeOptionsHost/CustomeOptionsControllerTests.swift',
    'Tests/CustomeOptionsHost/CustomeOptionsInteractionTests.swift',
    'submodules/AccountUtils/Sources/AccountUtils.swift',
    'submodules/SettingsUI/Sources/CustomeOptionsController.swift',
    'submodules/SettingsUI/Sources/DeleteAccountOptionsController.swift',
    'submodules/SettingsUI/Sources/LocalClientCosmeticsController.swift',
    'submodules/SettingsUI/Sources/LocalGhostSettingsController.swift',
    'submodules/SettingsUI/Sources/LocalMessageFilterSettingsController.swift',
    'submodules/SettingsUI/Sources/LogoutOptionsController.swift',
    'submodules/SettingsUI/Tests/CustomeOptionsCatalogTests.swift',
    'submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift',
    'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatHistoryEntry.swift',
    'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatLocalArchiveProjection.swift',
    'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Tests/ChatLocalArchiveProjectionTests.swift',
    'submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Tests/ChatLocalArchiveRowRendererTests.swift',
    'submodules/TelegramUI/Components/Chat/ChatMessageItemImpl/Sources/ChatLocalArchiveItem.swift',
    'submodules/TelegramUI/Components/Chat/ChatMessageItemImpl/Sources/ChatLocalArchiveRowView.swift',
    'submodules/TelegramUI/Components/ChatControllerInteraction/Sources/ChatControllerInteraction.swift',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoScreenSettingsActions.swift',
    'submodules/TelegramUI/Sources/ChatController.swift',
    'submodules/TelegramUI/Sources/ChatHistoryListNode.swift',
    'submodules/SettingsUI/Sources/CustomeOptionsAccountController.swift',
    'submodules/SettingsUI/Sources/Search/SettingsSearchableItems.swift',
    'submodules/SettingsUI/Tests/LocalArchiveExtrasTests.swift',
    'submodules/TelegramCore/Sources/LocalAppearance/LocalClientCosmeticsSettings.swift',
    'submodules/TelegramCore/Tests/LocalClientCosmeticsTests.swift',
    'submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoSettingsItems.swift',
})
MAX_PATCH = 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *arguments, env=None):
    return subprocess.check_output(['git', *arguments], cwd=root, env=env, stderr=subprocess.DEVNULL, timeout=180).decode().strip()


def checked_file(root, name):
    parts = PurePosixPath(name)
    if (not isinstance(name, str) or not name or parts.is_absolute() or str(parts) != name
            or any(part in ('', '.', '..', '.git') for part in parts.parts)
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
            or name == REQUEST or name.startswith('tools/fork_baseline/extended-retention-payload/')
            or name == 'tools/fork_baseline/publish_extended_retention.py'
            or not (name in FILES or name.startswith(PREFIXES))):
        raise ValueError('Source path is outside the reviewed scope')
    target = root / name
    if any(item.is_symlink() for item in [target, *target.parents]):
        raise ValueError('Source path contains a symbolic link')
    return target


def apply_request(root, request):
    root = Path(root).resolve()
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Source checkout must be clean')
    if request.get('schema') != 1 or request.get('repository') != REPOSITORY or request.get('branch') != BRANCH:
        raise ValueError('Unexpected request authority')
    rows = request.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError('Unexpected file inventory')
    expected = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'before_sha256', 'after_sha256'}:
            raise ValueError('Invalid source manifest')
        name = row['path']
        file = checked_file(root, name)
        if name in expected:
            raise ValueError('Duplicate source path')
        for key in ('before_sha256', 'after_sha256'):
            if key == 'before_sha256' and row[key] is None:
                continue
            if not isinstance(row[key], str) or not re.fullmatch(r'[0-9a-f]{64}', row[key]):
                raise ValueError('Invalid source checksum')
        if row['before_sha256'] is None:
            if file.exists():
                raise ValueError('A new source path already exists')
        elif not file.is_file() or file.stat().st_size > 2 * MAX_PATCH or digest(file.read_bytes()) != row['before_sha256']:
            raise ValueError('Source changed since review; refusing overwrite')
        expected[name] = row['after_sha256']
    encoded = request.get('patch_gzip_base64')
    if not isinstance(encoded, str) or len(encoded) > 2 * MAX_PATCH:
        raise ValueError('Invalid patch size')
    compressed = base64.b64decode(encoded, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode='rb') as stream:
        patch = stream.read(MAX_PATCH + 1)
    if len(patch) > MAX_PATCH or digest(patch) != request.get('patch_sha256'):
        raise ValueError('Patch checksum or size mismatch')
    patch.decode('utf-8', errors='strict')
    with tempfile.TemporaryDirectory(prefix='ayu-reviewed-source-') as temporary:
        file = Path(temporary) / 'reviewed.patch'
        file.write_bytes(patch)
        git(root, 'apply', '--check', '--index', '--whitespace=error-all', str(file))
        git(root, 'apply', '--index', '--whitespace=error-all', str(file))
    staged = set(filter(None, git(root, 'diff', '--cached', '--name-only', '--no-renames').splitlines()))
    if staged != set(expected):
        raise ValueError('Patch changed files outside its manifest')
    for name, checksum in expected.items():
        file = checked_file(root, name)
        if not file.is_file() or file.stat().st_size > 2 * MAX_PATCH or digest(file.read_bytes()) != checksum:
            raise ValueError('Patched source checksum mismatch')
        record = git(root, 'ls-files', '--stage', '--', name)
        if not record.startswith('100644 '):
            raise ValueError('Patched source must be a non-executable regular file')
    git(root, 'diff', '--cached', '--check')
    return sorted(expected)


def resolve_payload(root, request):
    if 'patch_gzip_base64' in request:
        return request
    rows = request.get('patch_chunks')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError('Invalid source payload chunks')
    pieces = []
    for index, row in enumerate(rows):
        name = 'tools/fork_baseline/extended-retention-payload/' + str(index).zfill(2) + '.txt'
        if not isinstance(row, dict) or set(row) != {'path', 'sha256'} or row['path'] != name:
            raise ValueError('Unexpected payload path')
        file = root / name
        if any(item.is_symlink() for item in [file, *file.parents]) or not file.is_file() or file.stat().st_size > 16384:
            raise ValueError('Unsafe payload chunk')
        data = file.read_bytes()
        if digest(data) != row['sha256']:
            raise ValueError('Payload chunk checksum mismatch')
        text = data.decode('ascii').rstrip('\n')
        if not re.fullmatch(r'[A-Za-z0-9+/=]+', text):
            raise ValueError('Invalid payload encoding')
        pieces.append(text)
    return {**request, 'patch_gzip_base64': ''.join(pieces)}


def main():
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH:
        raise ValueError('Publication is restricted to the requested repository and branch')
    root = Path.cwd()
    parent = os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[0-9a-f]{40}', parent) or git(root, 'rev-parse', 'HEAD') != parent:
        raise ValueError('Checkout does not match the workflow revision')
    if git(root, 'remote', 'get-url', 'origin') != 'https://github.com/' + REPOSITORY + '.git':
        raise ValueError('Unexpected publication origin')
    path = root / REQUEST
    if path.is_symlink() or path.stat().st_size > 2 * MAX_PATCH:
        raise ValueError('Invalid request file')
    request = resolve_payload(root, json.loads(path.read_text()))
    names = apply_request(root, request)
    private = os.environ.copy()
    token = private.pop('PUBLISH_TOKEN')
    private.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode())
    remote = git(root, 'ls-remote', '--heads', 'origin', 'refs/heads/' + BRANCH, env=private).split()
    if len(remote) != 2 or remote[0] != parent:
        raise ValueError('Integration branch advanced; refusing concurrent overwrite')
    git(root, '-c', 'user.name=AyuGram source build', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
        'commit', '-m', 'Add original-media Photos and Files export, enhanced gallery and More Features settings')
    source = git(root, 'rev-parse', 'HEAD')
    git(root, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH, env=private)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('source_sha=' + source + '\n')
    print(json.dumps({'source_commit': source, 'changed_files': len(names),
                      'application_built': False, 'native_tests_executed': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
