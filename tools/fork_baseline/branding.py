"""Verify the active fork metadata in a compiled main IPA, without printing it."""
import argparse
import json
from pathlib import Path
import plistlib
import re
import stat
import zipfile


def inspect_branding(ipa, bundle_id):
    with zipfile.ZipFile(ipa) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate package entries')
        candidates = [n for n in names if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist', n)]
        if len(candidates) != 1:
            raise ValueError('Expected one main application')
        entry = archive.getinfo(candidates[0])
        if entry.file_size > 1024 * 1024 or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}:
            raise ValueError('Invalid main metadata')
        info = plistlib.loads(archive.read(entry))
        if (not isinstance(info, dict) or info.get('CFBundleIdentifier') != bundle_id or
                info.get('AyuGramBaseBundleIdentifier') != bundle_id or
                info.get('CFBundleDisplayName') != 'AyuGram' or info.get('CFBundleExecutable') != 'Telegram'):
            raise ValueError('Compiled fork branding or stable identity is missing')
    return {'display_name_verified': True, 'stable_bundle_identity_verified': True,
            'base_bundle_metadata_verified': True, 'executable_identity_preserved': True,
            'runtime_verified': False, 'physical_device_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipa', required=True, type=Path)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.ipa.is_symlink() or args.output.exists() or args.output.is_symlink():
        raise SystemExit('Invalid branding evidence paths')
    try:
        report = inspect_branding(args.ipa, args.bundle)
    except (OSError, ValueError, TypeError, zipfile.BadZipFile):
        raise SystemExit('Compiled fork identity verification failed') from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print('Compiled AyuGram name and stable application identity verified.')


if __name__ == '__main__':
    main()
