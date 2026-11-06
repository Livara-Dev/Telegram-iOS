# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable UI-project input checks; no native compilation or UI is exercised."""
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiny_ui_test_project as tiny
from test_tiny_xcode_project import pbx_document


class TinyUITestProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'Generated'

    def generate(self):
        result = tiny.create_project(self.root)
        document = pbx_document((result['project'] / 'project.pbxproj').read_text())
        return result, document, document['objects']

    def test_exact_fixed_api_and_four_private_files_without_running_tools(self):
        with patch.object(subprocess, 'run') as run, patch.object(subprocess, 'Popen') as popen, patch.object(os, 'system') as system:
            result = tiny.create_project(self.root)
        run.assert_not_called(); popen.assert_not_called(); system.assert_not_called()
        self.assertEqual(tiny.TARGET, 'AyuRetainedOptionsUITests')
        self.assertEqual(tiny.TEST_BUNDLE, 'org.livara.ayugram.tests.retained-options-ui')
        self.assertEqual(tiny.HOST_BUNDLE, 'org.livara.ayugram.tests.custome-options')
        self.assertEqual(tiny.EXPECTED_CASE, 'AyuRetainedOptionsUITests.testRetainedCustomeOptionsRoot')
        self.assertEqual(result, {'project': self.root / (tiny.TARGET + '.xcodeproj'), 'scheme': tiny.TARGET,
            'target': tiny.TARGET, 'test_bundle': tiny.TEST_BUNDLE, 'host_bundle': tiny.HOST_BUNDLE,
            'expected_case': tiny.EXPECTED_CASE})
        files = {tiny.TARGET + '/' + tiny.TARGET + '.swift', tiny.TARGET + '/Info.plist',
            tiny.TARGET + '.xcodeproj/project.pbxproj',
            tiny.TARGET + '.xcodeproj/xcshareddata/xcschemes/' + tiny.TARGET + '.xcscheme'}
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()}, files)
        for p in (self.root, *self.root.rglob('*')):
            self.assertFalse(p.is_symlink())
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600)

    def test_single_ui_bundle_has_no_application_target_or_build_dependency(self):
        _, document, objects = self.generate()
        project = objects[document['rootObject']]
        self.assertEqual(len(project['targets']), 1)
        target = objects[project['targets'][0]]
        self.assertEqual(target['name'], tiny.TARGET)
        self.assertEqual(target['productType'], 'com.apple.product-type.bundle.ui-testing')
        self.assertEqual(target['dependencies'], [])
        self.assertEqual(target['buildRules'], [])
        self.assertEqual(objects[target['productReference']]['path'], tiny.TARGET + '.xctest')
        self.assertFalse(any(o['isa'] in ('PBXTargetDependency', 'PBXContainerItemProxy') for o in objects.values()))
        phases = [objects[i] for i in target['buildPhases']]
        sources = [o for o in phases if o['isa'] == 'PBXSourcesBuildPhase']
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0]['files']), 1)
        reference = objects[objects[sources[0]['files'][0]]['fileRef']]
        self.assertEqual(reference['path'], tiny.TARGET + '.swift')
        for configuration in (o for o in objects.values() if o['isa'] == 'XCBuildConfiguration'):
            self.assertFalse({'TEST_TARGET_NAME', 'TEST_HOST', 'BUNDLE_LOADER', 'CODE_SIGN_ENTITLEMENTS'} & configuration['buildSettings'].keys())
        attributes = project['attributes']['TargetAttributes']
        self.assertEqual(len(attributes), 1)
        self.assertNotIn('TestTargetID', next(iter(attributes.values())))

    def test_project_pins_swift_warnings_arm64_simulator_and_stock_xcode_signing(self):
        _, document, objects = self.generate()
        project = objects[document['rootObject']]
        target = objects[project['targets'][0]]
        def configuration(owner):
            ids = objects[owner['buildConfigurationList']]['buildConfigurations']
            self.assertEqual(len(ids), 1)
            self.assertEqual(objects[ids[0]]['name'], 'Debug')
            return objects[ids[0]]['buildSettings']
        settings = {**configuration(project), **configuration(target)}
        for key, expected in {'ARCHS': 'arm64', 'SUPPORTED_PLATFORMS': 'iphonesimulator',
                'SDKROOT': 'iphonesimulator', 'IPHONEOS_DEPLOYMENT_TARGET': '15.0', 'ONLY_ACTIVE_ARCH': 'YES',
                'TARGETED_DEVICE_FAMILY': '1', 'SWIFT_VERSION': '5.0', 'SWIFT_TREAT_WARNINGS_AS_ERRORS': 'YES',
                'GCC_TREAT_WARNINGS_AS_ERRORS': 'YES', 'CODE_SIGNING_ALLOWED': 'YES', 'CODE_SIGN_IDENTITY': '-',
                'CODE_SIGN_STYLE': 'Manual', 'CODE_SIGN_INJECT_BASE_ENTITLEMENTS': 'YES', 'USES_XCTRUNNER': 'YES',
                'ENABLE_TESTING_SEARCH_PATHS': 'YES', 'GENERATE_INFOPLIST_FILE': 'NO',
                'PRODUCT_BUNDLE_IDENTIFIER': tiny.TEST_BUNDLE, 'PRODUCT_MODULE_NAME': tiny.TARGET}.items():
            self.assertEqual(settings[key], expected)
        self.assertEqual(project['attributes']['LastUpgradeCheck'], '2710')
        self.assertEqual(settings['FRAMEWORK_SEARCH_PATHS'], ['$(inherited)', '$(PLATFORM_DIR)/Developer/Library/Frameworks'])
        self.assertTrue((self.root / settings['INFOPLIST_FILE']).is_file())
        self.assertFalse(any(k.startswith(('PROVISIONING_PROFILE', 'DEVELOPMENT_TEAM')) for k in settings))

    def test_only_apple_frameworks_resolve_and_no_network_scripts_or_packages_exist(self):
        _, document, objects = self.generate()
        allowed = {'PBXBuildFile', 'PBXFileReference', 'PBXGroup', 'PBXSourcesBuildPhase', 'PBXFrameworksBuildPhase',
                   'PBXResourcesBuildPhase', 'PBXNativeTarget', 'PBXProject', 'XCConfigurationList', 'XCBuildConfiguration'}
        self.assertTrue({o['isa'] for o in objects.values()}.issubset(allowed))
        frameworks = {o.get('name'): o for o in objects.values() if o.get('lastKnownFileType') == 'wrapper.framework'}
        self.assertEqual(set(frameworks), {'Foundation.framework', 'XCTest.framework'})
        self.assertEqual(frameworks['Foundation.framework']['sourceTree'], 'SDKROOT')
        self.assertEqual(frameworks['Foundation.framework']['path'], 'System/Library/Frameworks/Foundation.framework')
        self.assertEqual(frameworks['XCTest.framework']['sourceTree'], 'DEVELOPER_DIR')
        self.assertEqual(frameworks['XCTest.framework']['path'], 'Platforms/iPhoneSimulator.platform/Developer/Library/Frameworks/XCTest.framework')
        text = tiny.PROJECT + tiny.SCHEME + tiny.TEST_SOURCE
        for word in ('PrivateFrameworks', 'WebDriverAgent', 'PBXShellScriptBuildPhase', 'shellScript',
                     'XCRemoteSwiftPackageReference', 'XCSwiftPackageProductDependency', 'packageReferences',
                     'packageProductDependencies', 'https://', 'http://', 'TelegramCore', 'CustomeOptionsRenderer'):
            self.assertNotIn(word, text)
        def walk(identifier, parent):
            o = objects[identifier]
            if o['isa'] == 'PBXGroup':
                for child in o['children']: walk(child, parent / o.get('path', ''))
            elif o.get('sourceTree') == '<group>': self.assertTrue((parent / o['path']).is_file())
        project_group = objects[document['rootObject']]['mainGroup']
        walk(project_group, self.root)
        self.assertIn(project_group, objects)

    def test_scheme_builds_and_tests_only_the_ui_bundle_without_implicit_app(self):
        _, document, objects = self.generate()
        scheme = ET.fromstring(tiny.SCHEME)
        self.assertEqual(scheme.attrib['LastUpgradeVersion'], '2710')
        build = scheme.find('BuildAction')
        self.assertEqual(build.attrib['buildImplicitDependencies'], 'NO')
        entries = build.findall('BuildActionEntries/BuildActionEntry')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].attrib['buildForTesting'], 'YES')
        self.assertEqual(entries[0].attrib['buildForRunning'], 'NO')
        refs = scheme.findall('.//BuildableReference')
        self.assertEqual(len(refs), 2)
        for ref in refs:
            self.assertEqual(ref.attrib['BlueprintName'], tiny.TARGET)
            self.assertEqual(ref.attrib['BuildableName'], tiny.TARGET + '.xctest')
            self.assertEqual(objects[ref.attrib['BlueprintIdentifier']]['isa'], 'PBXNativeTarget')
        action = scheme.find('TestAction')
        self.assertEqual(action.attrib['codeCoverageEnabled'], 'NO')
        self.assertEqual(action.attrib['shouldUseLaunchSchemeArgsEnv'], 'NO')
        self.assertEqual(action.attrib['systemAttachmentLifetime'], 'keepNever')
        self.assertEqual(action.attrib['userAttachmentLifetime'], 'keepAlways')
        self.assertEqual(len(action.findall('Testables/TestableReference')), 1)
        self.assertEqual(action.find('Testables/TestableReference').attrib['skipped'], 'NO')
        self.assertEqual(action.find('Testables/TestableReference').attrib['parallelizable'], 'NO')
        self.assertEqual(scheme.findall('.//MacroExpansion'), [])
        self.assertEqual(scheme.findall('.//EnvironmentVariables'), [])

    def test_one_native_case_requires_foreground_exact_title_table_and_live_archive_control(self):
        self.generate()
        source = (self.root / tiny.TARGET / (tiny.TARGET + '.swift')).read_text()
        self.assertEqual(re.findall(r'^import ([A-Za-z]+)$', source, re.M), ['Foundation', 'XCTest'])
        cases = re.findall(r'func (test\w+)\(', source)
        self.assertEqual(cases, [tiny.EXPECTED_CASE.split('.')[1]])
        self.assertIn('@MainActor\n    func ' + cases[0], source)
        self.assertIn('XCUIApplication(bundleIdentifier: "' + tiny.HOST_BUNDLE + '")', source)
        self.assertIn('app.activate()', source)
        self.assertIn('XCTAssertEqual(app.state, .runningForeground)', source)
        self.assertIn('navigationBars["Custome options"]', source)
        self.assertIn('XCTAssertEqual(title.label, "Custome options")', source)
        self.assertIn('tables["ayugram.custom-options.list"]', source)
        self.assertIn('table.switches["ayugram.custom-options.toggle.archive"]', source)
        self.assertIn('XCTAssertTrue(archive.isEnabled)', source)
        self.assertIn('XCTAssertTrue(archive.isHittable)', source)
        self.assertIn('defer {', source)
        self.assertIn('attachment.name = "retained-root"', source)
        self.assertIn('attachment.lifetime = .keepAlways', source)
        self.assertIn('XCTAttachment(screenshot: XCUIScreen.main.screenshot())', source)
        for forbidden in ('XCUIApplication()', '.tap()', '.typeText(', '.launch()', 'launchEnvironment',
                          'XCTSkip', 'Vision', 'NSWorkspace', 'ProcessInfo', 'FileManager', 'UserDefaults'):
            self.assertNotIn(forbidden, source)

    def test_info_is_a_test_bundle_not_an_application_or_account_container(self):
        self.generate()
        info = plistlib.loads((self.root / tiny.TARGET / 'Info.plist').read_bytes())
        self.assertEqual(info['CFBundlePackageType'], 'BNDL')
        self.assertEqual(info['CFBundleSupportedPlatforms'], ['iPhoneSimulator'])
        self.assertEqual(info['MinimumOSVersion'], '15.0')
        self.assertEqual(info['CFBundleIdentifier'], '$(PRODUCT_BUNDLE_IDENTIFIER)')
        self.assertFalse({'UIApplicationSceneManifest', 'UILaunchScreen', 'NSExtension', 'CFBundleURLTypes',
                          'NSAppTransportSecurity', 'LSApplicationQueriesSchemes'} & info.keys())

    def test_outputs_are_deterministic_and_contain_no_generation_path(self):
        self.generate()
        other = self.base / 'Second'
        tiny.create_project(other)
        def files(root): return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        original = files(self.root)
        self.assertEqual(original, files(other))
        self.assertTrue(all(str(self.base).encode() not in data for data in original.values()))

    def test_unsafe_or_preexisting_roots_never_replace_existing_content(self):
        existing = self.base / 'existing'; existing.mkdir(); marker = existing / 'keep'; marker.write_text('original')
        file = self.base / 'file'; file.write_text('original')
        link = self.base / 'linked'; link.symlink_to(existing, target_is_directory=True)
        broken = self.base / 'broken'; broken.symlink_to(self.base / 'absent')
        roots = [Path('relative'), existing, file, link, broken, link / 'child',
                 self.base / 'missing' / 'child', self.base / 'existing' / '..' / 'child']
        for root in roots:
            with self.subTest(root=str(root)), self.assertRaises(ValueError): tiny.create_project(root)
        self.assertEqual(marker.read_text(), 'original')
        self.assertEqual(file.read_text(), 'original')
        self.assertFalse((existing / 'child').exists())
        self.assertFalse((self.base / 'child').exists())


if __name__ == '__main__': unittest.main()
