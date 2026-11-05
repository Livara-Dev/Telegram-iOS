"""The actual Bazel plist, rather than an unused source plist, owns identity."""
import ast
import io
from pathlib import Path
import plistlib
import sys
import unittest
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from branding import inspect_branding

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = 'org.livara.ayugram'


def active_metadata():
    tree = ast.parse((ROOT / 'Telegram/BUILD').read_text())
    info = None
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id != 'plist_fragment':
            continue
        fields = {item.arg: item.value for item in call.keywords}
        if not isinstance(fields.get('name'), ast.Constant) or fields['name'].value != 'TelegramInfoPlist':
            continue
        template = fields['template']
        assert isinstance(template, ast.Call) and isinstance(template.func, ast.Attribute)
        assert template.func.attr == 'format' and isinstance(template.func.value, ast.Constant)
        text = template.func.value.value.format(telegram_bundle_id=BUNDLE)
        info = plistlib.loads(('<plist version="1.0"><dict>' + text + '</dict></plist>').encode())
        break
    if info is None: raise AssertionError('Active TelegramInfoPlist fragment was not found')
    # rules_apple supplies CFBundleExecutable from the application target; that
    # generated field is intentionally absent from the source plist fragment.
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == 'ios_application':
                fields = {item.arg: item.value for item in call.keywords}
                if isinstance(fields.get('name'), ast.Constant) and fields['name'].value == 'Telegram':
                    info['CFBundleExecutable'] = ast.literal_eval(fields.get('executable_name', fields['name']))
                    return info
    raise AssertionError('Main application target was not found')


class BrandingTests(unittest.TestCase):
    def package(self, info):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('Payload/Telegram.app/Info.plist', plistlib.dumps(info))
        buffer.seek(0)
        return buffer

    def test_compiled_identity_accepts_active_bazel_metadata(self):
        result = inspect_branding(self.package(active_metadata()), BUNDLE)
        self.assertTrue(result['base_bundle_metadata_verified'])
        self.assertFalse(result['physical_device_verified'])

    def test_each_missing_or_changed_identity_is_rejected(self):
        original = active_metadata()
        for field in ('CFBundleDisplayName', 'CFBundleIdentifier', 'AyuGramBaseBundleIdentifier', 'CFBundleExecutable'):
            for replacement in (None, 'incorrect'):
                with self.subTest(field=field, replacement=replacement):
                    info = dict(original)
                    if replacement is None: info.pop(field, None)
                    else: info[field] = replacement
                    with self.assertRaises(ValueError): inspect_branding(self.package(info), BUNDLE)

    def test_default_icon_has_only_local_original_vector_layers(self):
        import json
        path = ROOT / 'Telegram/Telegram-iOS/AyuGram.icon'
        config = json.loads((path / 'icon.json').read_text())
        layers = [layer for group in config['groups'] for layer in group['layers']]
        self.assertEqual({layer['image-name'] for layer in layers}, {'Ghost.svg', 'Halo.svg'})
        for layer in layers:
            tree = ET.parse(path / 'Assets' / layer['image-name'])
            self.assertNotIn('script', {n.tag.rsplit('}', 1)[-1] for n in tree.iter()})
            for node in tree.iter():
                self.assertFalse(any(key.endswith('href') for key in node.attrib))
        self.assertIn('composer_icon_folders = ["AyuGram"]', (ROOT / 'Telegram/BUILD').read_text())
