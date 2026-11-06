# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable structure and filesystem checks; these do not invoke or verify Xcode."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tiny_xcode_project as tiny


APP = 'AyuProjectionControl'
TEST = 'AyuProjectionControlTests'
APP_BUNDLE = 'org.livara.ayugram.tests.modern-projection-control'
TEST_BUNDLE = 'org.livara.ayugram.tests.modern-projection-controlTests'
ID = re.compile(r'[0-9A-Fa-f]{24}')
TOKEN = re.compile(r'\s+|/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|[{}()=;,]|[^\s{}()=;,"/]+(?:/[^\s{}()=;,"/]*)*', re.S)


def pbx_document(text):
    """Parse the generated OpenStep subset, rejecting gaps and duplicate keys."""
    tokens = []
    position = 0
    for match in TOKEN.finditer(text):
        if match.start() != position:
            raise AssertionError('Unrecognized PBX syntax')
        position = match.end()
        token = match.group()
        if token.isspace() or token.startswith(('//', '/*')):
            continue
        tokens.append(token)
    if position != len(text):
        raise AssertionError('Unrecognized PBX suffix')
    cursor = 0
    def take(expected=None):
        nonlocal cursor
        if cursor >= len(tokens):
            raise AssertionError('Unbalanced PBX document')
        token = tokens[cursor]
        cursor += 1
        if expected is not None and token != expected:
            raise AssertionError('Unexpected PBX token')
        return token
    def value():
        token = take()
        if token == '{':
            result = {}
            while cursor < len(tokens) and tokens[cursor] != '}':
                key = take()
                if key.startswith('"'):
                    key = json.loads(key)
                if key in result or key in '{}()=;,':
                    raise AssertionError('Duplicate or invalid PBX dictionary key')
                take('=')
                result[key] = value()
                take(';')
            take('}')
            return result
        if token == '(':
            result = []
            while cursor < len(tokens) and tokens[cursor] != ')':
                result.append(value())
                if tokens[cursor] == ')':
                    break
                take(',')
            take(')')
            return result
        if token in '}),;=':
            raise AssertionError('Unbalanced PBX value')
        return json.loads(token) if token.startswith('"') else token
    result = value()
    if cursor != len(tokens):
        raise AssertionError('Trailing PBX syntax')
    objects = result['objects']
    if not objects or any(not ID.fullmatch(identifier) for identifier in objects):
        raise AssertionError('Invalid PBX object identifier')
    references = {token for token in tokens if ID.fullmatch(token)}
    if references - objects.keys():
        raise AssertionError('Undefined PBX object reference')
    return result


class TinyXcodeProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'Generated'

    def generate(self):
        result = tiny.create_project(self.root)
        document = pbx_document((result['project'] / 'project.pbxproj').read_text())
        return result, document, document['objects']

    @staticmethod
    def snapshot(root):
        values = {}
        for path in root.rglob('*'):
            relative = str(path.relative_to(root))
            info = path.lstat()
            values[relative] = (info.st_mode, os.readlink(path) if path.is_symlink()
                                else path.read_bytes() if path.is_file() else None)
        return values

    def target_settings(self, document, objects, target):
        def settings(owner):
            configurations = objects[owner['buildConfigurationList']]['buildConfigurations']
            self.assertEqual(len(configurations), 1)
            configuration = objects[configurations[0]]
            self.assertEqual(configuration['name'], 'Debug')
            return configuration['buildSettings']
        return {**settings(objects[document['rootObject']]), **settings(target)}

    def test_exact_api_and_six_private_files_without_invoking_tools(self):
        with patch.object(subprocess, 'run') as run, patch.object(subprocess, 'Popen') as popen, \
                patch.object(os, 'system') as system:
            result = tiny.create_project(self.root)
        run.assert_not_called(); popen.assert_not_called(); system.assert_not_called()
        self.assertEqual(result, {'project': self.root / (APP + '.xcodeproj'), 'scheme': APP,
            'app_name': APP, 'test_name': TEST, 'app_bundle': APP_BUNDLE, 'test_bundle': TEST_BUNDLE,
            'expected_case': TEST + '.testRunsInApplicationHost'})
        expected = {APP + '/main.m', APP + '/Info.plist', TEST + '/' + TEST + '.m', TEST + '/Info.plist',
            APP + '.xcodeproj/project.pbxproj', APP + '.xcodeproj/xcshareddata/xcschemes/' + APP + '.xcscheme'}
        self.assertEqual({str(p.relative_to(self.root)) for p in self.root.rglob('*') if p.is_file()}, expected)
        for path in (self.root, *self.root.rglob('*')):
            self.assertFalse(path.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_pbx_is_balanced_has_defined_fixed_references_and_is_deterministic(self):
        _, document, objects = self.generate()
        self.assertEqual(document['archiveVersion'], '1')
        self.assertEqual(document['objectVersion'], '56')
        self.assertEqual(objects[document['rootObject']]['isa'], 'PBXProject')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        second = self.base / 'Second'
        tiny.create_project(second)
        after = {str(p.relative_to(second)): p.read_bytes() for p in second.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn(str(self.root), ''.join(data.decode() for data in before.values()))

    def test_pbx_parser_rejects_unbalanced_duplicate_and_undefined_object_references(self):
        _, _, _ = self.generate()
        text = (self.root / (APP + '.xcodeproj/project.pbxproj')).read_text()
        for bad in (text[:-2], text + '}', text.replace('archiveVersion = 1;', 'archiveVersion = 1; archiveVersion = 2;'),
                    text.replace('rootObject = 600000000000000000000001', 'rootObject = FFFFFFFFFFFFFFFFFFFFFFFF')):
            with self.subTest(prefix=bad[-40:]), self.assertRaises((AssertionError, IndexError)):
                pbx_document(bad)

    def test_two_targets_have_exact_sources_products_and_application_host_dependency(self):
        _, document, objects = self.generate()
        project = objects[document['rootObject']]
        targets = {objects[identifier]['name']: (identifier, objects[identifier]) for identifier in project['targets']}
        self.assertEqual(set(targets), {APP, TEST})
        app_id, app = targets[APP]; _, test = targets[TEST]
        self.assertEqual(app['productType'], 'com.apple.product-type.application')
        self.assertEqual(test['productType'], 'com.apple.product-type.bundle.unit-test')
        self.assertEqual(objects[app['productReference']]['path'], APP + '.app')
        self.assertEqual(objects[test['productReference']]['path'], TEST + '.xctest')
        for target, source_name in ((app, 'main.m'), (test, TEST + '.m')):
            phases = [objects[identifier] for identifier in target['buildPhases']]
            sources = [phase for phase in phases if phase['isa'] == 'PBXSourcesBuildPhase']
            self.assertEqual(len(sources), 1)
            self.assertEqual(len(sources[0]['files']), 1)
            source = objects[objects[sources[0]['files'][0]]['fileRef']]
            self.assertEqual(source['path'], source_name)
            self.assertEqual(target['buildRules'], [])
        self.assertEqual(app['dependencies'], [])
        self.assertEqual(len(test['dependencies']), 1)
        dependency = objects[test['dependencies'][0]]
        self.assertEqual(dependency['isa'], 'PBXTargetDependency')
        self.assertEqual(dependency['target'], app_id)
        proxy = objects[dependency['targetProxy']]
        self.assertEqual(proxy['remoteGlobalIDString'], app_id)
        self.assertEqual(proxy['containerPortal'], document['rootObject'])

    def test_debug_settings_are_arm64_simulator_adhoc_signed_and_test_is_hosted(self):
        _, document, objects = self.generate()
        targets = [value for value in objects.values() if value['isa'] == 'PBXNativeTarget']
        for configuration in (value for value in objects.values() if value['isa'] == 'XCBuildConfiguration'):
            for key in configuration['buildSettings']:
                self.assertNotIn(key, {'DEVELOPMENT_TEAM', 'CODE_SIGN_ENTITLEMENTS', 'CODE_SIGNING_REQUIRED'})
                self.assertFalse(key.startswith('PROVISIONING_PROFILE'))
        for target in targets:
            settings = self.target_settings(document, objects, target)
            for key, expected in {'ARCHS': 'arm64', 'SDKROOT': 'iphonesimulator',
                    'SUPPORTED_PLATFORMS': 'iphonesimulator', 'IPHONEOS_DEPLOYMENT_TARGET': '15.0',
                    'TARGETED_DEVICE_FAMILY': '1', 'CODE_SIGNING_ALLOWED': 'YES', 'CODE_SIGN_IDENTITY': '-',
                    'CODE_SIGN_STYLE': 'Manual', 'CODE_SIGN_INJECT_BASE_ENTITLEMENTS': 'YES'}.items():
                self.assertEqual(settings[key], expected)
            self.assertEqual(settings['PRODUCT_BUNDLE_IDENTIFIER'], APP_BUNDLE if target['name'] == APP else TEST_BUNDLE)
            self.assertEqual(settings['GENERATE_INFOPLIST_FILE'], 'NO')
            self.assertTrue((self.root / settings['INFOPLIST_FILE']).is_file())
            if target['name'] == TEST:
                self.assertEqual(settings['BUNDLE_LOADER'], '$(TEST_HOST)')
                self.assertEqual(settings['TEST_HOST'], '$(BUILT_PRODUCTS_DIR)/' + APP + '.app/' + APP)
                self.assertEqual(settings['TEST_TARGET_NAME'], APP)
                self.assertEqual(settings['FRAMEWORK_SEARCH_PATHS'],
                    ['$(inherited)', '$(PLATFORM_DIR)/Developer/Library/Frameworks'])

    def test_only_apple_frameworks_and_resolvable_local_file_references_without_external_dependencies(self):
        _, document, objects = self.generate()
        allowed = {'PBXBuildFile', 'PBXFileReference', 'PBXGroup', 'PBXSourcesBuildPhase', 'PBXFrameworksBuildPhase',
                   'PBXNativeTarget', 'PBXProject', 'XCConfigurationList', 'PBXContainerItemProxy', 'PBXTargetDependency', 'XCBuildConfiguration'}
        self.assertTrue({value['isa'] for value in objects.values()}.issubset(allowed))
        text = (self.root / (APP + '.xcodeproj/project.pbxproj')).read_text()
        for forbidden in ('shellScript', 'PBXShellScriptBuildPhase', 'XCRemoteSwiftPackageReference',
                          'XCSwiftPackageProductDependency', 'packageReferences', 'packageProductDependencies', 'https://', 'http://'):
            self.assertNotIn(forbidden, text)
        frameworks = {value['name']: value for value in objects.values()
                      if value['isa'] == 'PBXFileReference' and value.get('lastKnownFileType') == 'wrapper.framework'}
        self.assertEqual(set(frameworks), {'UIKit.framework', 'XCTest.framework'})
        for name, value in frameworks.items():
            self.assertEqual(value['sourceTree'], 'SDKROOT' if name == 'UIKit.framework' else 'DEVELOPER_DIR')
            self.assertEqual(value['path'], 'System/Library/Frameworks/UIKit.framework' if name == 'UIKit.framework'
                else 'Platforms/iPhoneSimulator.platform/Developer/Library/Frameworks/XCTest.framework')
            self.assertFalse(Path(value['path']).is_absolute())
            self.assertEqual(Path(value['path']).name, name)
        resolved = set()
        def walk(identifier, parent):
            value = objects[identifier]
            if value['isa'] == 'PBXGroup':
                group = parent / value.get('path', '')
                for child in value['children']:
                    walk(child, group)
            elif value.get('sourceTree') == '<group>':
                file = parent / value['path']
                self.assertTrue(file.is_file(), value['path'])
                resolved.add(str(file.relative_to(self.root)))
        walk(objects[document['rootObject']]['mainGroup'], self.root)
        self.assertEqual(resolved, {APP + '/main.m', APP + '/Info.plist', TEST + '/' + TEST + '.m', TEST + '/Info.plist'})
        linked = {}
        for target in (value for value in objects.values() if value['isa'] == 'PBXNativeTarget'):
            phases = [objects[identifier] for identifier in target['buildPhases']]
            framework_phase = next(phase for phase in phases if phase['isa'] == 'PBXFrameworksBuildPhase')
            linked[target['name']] = {objects[objects[identifier]['fileRef']]['name'] for identifier in framework_phase['files']}
        self.assertEqual(linked, {APP: {'UIKit.framework'}, TEST: {'UIKit.framework', 'XCTest.framework'}})

    def test_plists_are_valid_simulator_iphone_and_scene_manifest_matches_implementation(self):
        self.generate()
        app = plistlib.loads((self.root / APP / 'Info.plist').read_bytes())
        test = plistlib.loads((self.root / TEST / 'Info.plist').read_bytes())
        for value, package in ((app, 'APPL'), (test, 'BNDL')):
            self.assertEqual(value['CFBundlePackageType'], package)
            self.assertEqual(value['CFBundleIdentifier'], '$(PRODUCT_BUNDLE_IDENTIFIER)')
            self.assertEqual(value['CFBundleExecutable'], '$(EXECUTABLE_NAME)')
            self.assertEqual(value['CFBundleSupportedPlatforms'], ['iPhoneSimulator'])
            self.assertEqual(value['MinimumOSVersion'], '15.0')
            self.assertEqual(value['UIDeviceFamily'], [1])
        manifest = app['UIApplicationSceneManifest']
        self.assertFalse(manifest['UIApplicationSupportsMultipleScenes'])
        scenes = manifest['UISceneConfigurations']['UIWindowSceneSessionRoleApplication']
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0]['UISceneDelegateClassName'], APP + 'SceneDelegate')
        source = (self.root / APP / 'main.m').read_text()
        self.assertIn('@interface ' + APP + 'SceneDelegate : UIResponder <UIWindowSceneDelegate>', source)
        self.assertIn('configuration.delegateClass = [' + APP + 'SceneDelegate class]', source)
        self.assertIn('UIApplicationMain(', source)
        self.assertIn('[self.window makeKeyAndVisible]', source)

    def test_exactly_one_objective_c_test_checks_real_app_bundle_and_uiapplication(self):
        self.generate()
        source = (self.root / TEST / (TEST + '.m')).read_text()
        self.assertEqual(re.findall(r'-\s*\(void\)\s*(test\w+)\s*\{', source), ['testRunsInApplicationHost'])
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),
                         '2dfa08f8b3c3d87d4a2a23ac445760dccf075d7ecbe024dbccece611efbfb247')
        self.assertIn('@interface ' + TEST + ' : XCTestCase', source)
        self.assertIn('#import <XCTest/XCTest.h>', source)
        self.assertIn('#import <UIKit/UIKit.h>', source)
        self.assertRegex(source, r'XCTAssertEqualObjects\(\[NSBundle mainBundle\]\.bundleIdentifier,\s*@"' + re.escape(APP_BUNDLE) + r'"\)')
        self.assertIn('XCTAssertNotNil([UIApplication sharedApplication])', source)
        self.assertNotIn('XCTSkip', source)
        self.assertNotIn('XCTAssertTrue(YES)', source)
        for name in (APP + '/main.m', TEST + '/' + TEST + '.m'):
            text = (self.root / name).read_text()
            self.assertIn('SPDX-License-Identifier: GPL-2.0-or-later', text)
            self.assertIn('2026-11-06', text)

    def test_shared_scheme_tests_only_the_test_target_once_without_filters_or_environment(self):
        result, document, objects = self.generate()
        scheme = ET.parse(result['project'] / 'xcshareddata/xcschemes' / (APP + '.xcscheme')).getroot()
        action = scheme.find('TestAction')
        self.assertEqual(action.get('buildConfiguration'), 'Debug')
        testables = action.findall('Testables/TestableReference')
        self.assertEqual(len(testables), 1)
        self.assertEqual(testables[0].get('skipped'), 'NO')
        target = testables[0].find('BuildableReference')
        self.assertEqual(target.get('BlueprintName'), TEST)
        self.assertEqual(objects[target.get('BlueprintIdentifier')]['name'], TEST)
        self.assertEqual(target.get('ReferencedContainer'), 'container:' + APP + '.xcodeproj')
        for element in scheme.iter():
            self.assertNotIn(element.tag, {'OnlyTestIdentifiers', 'SkippedTests', 'SkipTestIdentifiers',
                'SelectedTests', 'EnvironmentVariables', 'EnvironmentVariable', 'CommandLineArguments', 'ExecutionAction'})
            if element.tag == 'BuildableReference':
                self.assertIn(element.get('BlueprintIdentifier'), objects)
        entries = scheme.findall('BuildAction/BuildActionEntries/BuildActionEntry')
        app_entries = [entry for entry in entries if entry.find('BuildableReference').get('BlueprintName') == APP]
        self.assertEqual(len(app_entries), 1)
        self.assertEqual(app_entries[0].get('buildForTesting'), 'YES')
        self.assertEqual(scheme.find('TestAction/MacroExpansion/BuildableReference').get('BlueprintName'), APP)

    def test_existing_empty_or_nonempty_root_is_rejected_without_overwriting(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                root = self.base / ('Populated' if populated else 'Empty')
                root.mkdir()
                if populated:
                    (root / 'sentinel').write_bytes(b'untouched input')
                before = self.snapshot(self.base)
                with self.assertRaises((OSError, ValueError)):
                    tiny.create_project(root)
                self.assertEqual(self.snapshot(self.base), before)
        self.generate()
        before = self.snapshot(self.root)
        with self.assertRaises((OSError, ValueError)):
            tiny.create_project(self.root)
        self.assertEqual(self.snapshot(self.root), before)

    def test_root_and_ancestor_links_are_rejected_without_touching_their_targets(self):
        real = self.base / 'Real'; real.mkdir()
        (real / 'sentinel').write_bytes(b'untouched link destination')
        alias = self.base / 'Alias'; alias.symlink_to(real, target_is_directory=True)
        dangling = self.base / 'Dangling'; dangling.symlink_to(self.base / 'Absent', target_is_directory=True)
        for root in (alias, alias / 'Fresh', dangling):
            with self.subTest(root=root.name):
                before = self.snapshot(self.base)
                with self.assertRaises((OSError, ValueError)):
                    tiny.create_project(root)
                self.assertEqual(self.snapshot(self.base), before)

    def test_relative_root_or_missing_parent_is_rejected_without_creating_anything(self):
        for root in (Path('relative-unapproved-project'), self.base / 'Missing' / 'Fresh'):
            with self.subTest(root=root.name):
                before = self.snapshot(self.base)
                with self.assertRaises((OSError, ValueError)):
                    tiny.create_project(root)
                self.assertEqual(self.snapshot(self.base), before)
                self.assertFalse(root.exists())


    def test_lifecycle_marker_has_fixed_private_path_and_only_three_ordered_call_sites(self):
        self.assertEqual(tiny.MARKER_RELATIVE, 'Library/Caches/ayu-modern-control/stage')
        self.assertEqual(tiny.STAGES, ('mainEntered', 'didFinish', 'sceneVisible'))
        self.generate()
        source = (self.root / APP / 'main.m').read_text()
        self.assertEqual(re.findall(r'AyuModernRecordStage\("([A-Za-z]+)"\)', source),
                         ['sceneVisible', 'didFinish', 'mainEntered'])
        main = source[source.index('int main('):]
        self.assertLess(main.index('@autoreleasepool'), main.index('AyuModernRecordStage("mainEntered")'))
        self.assertLess(main.index('AyuModernRecordStage("mainEntered")'), main.index('UIApplicationMain('))
        did_finish = source[source.index('didFinishLaunchingWithOptions:'):source.index('- (UISceneConfiguration *)')]
        self.assertLess(did_finish.index('AyuModernRecordStage("didFinish")'), did_finish.index('return YES;'))
        scene = source[source.index('@implementation AyuProjectionControlSceneDelegate'):source.index('@interface AyuProjectionControlAppDelegate')]
        self.assertLess(scene.index('[self.window makeKeyAndVisible]'), scene.index('AyuModernRecordStage("sceneVisible")'))
        wrapper = source[source.index('static void AyuModernRecordStage('):source.index('@interface AyuProjectionControlSceneDelegate')]
        self.assertIn('NSHomeDirectory()', wrapper)
        self.assertIn('stringByAppendingPathComponent:@"Library/Caches/ayu-modern-control"', wrapper)
        self.assertIn('AyuModernWriteStage([directory fileSystemRepresentation], stage)', wrapper)
        self.assertIn('@try', wrapper); self.assertIn('@catch (NSException *exception)', wrapper)
        for forbidden in ('getenv', 'environ', 'NSLog', 'NSURL', 'NSURLConnection', 'NSURLSession',
                          'account', 'keychain', 'NSUserDefaults', 'https://', 'http://'):
            self.assertNotIn(forbidden, source)
        self.assertNotRegex(source, r'\b(?:printf|fprintf|puts|perror)\s*\(')
        self.assertEqual(source.count('// BEGIN PORTABLE MARKER WRITER'), 1)
        self.assertEqual(source.count('// END PORTABLE MARKER WRITER'), 1)


class PortableMarkerWriterTests(unittest.TestCase):
    """Execute the exact C writer, independent of UIKit and any Apple tooling."""
    @classmethod
    def setUpClass(cls):
        compiler = next((path for name in ('cc', 'clang', 'gcc') if (path := shutil.which(name))), None)
        if compiler is None:
            raise unittest.SkipTest('A portable C compiler is unavailable')
        temporary = tempfile.TemporaryDirectory(prefix='ayu-marker-writer-test-')
        cls.addClassCleanup(temporary.cleanup)
        cls.build = Path(temporary.name).resolve()
        cls.source_path = Path(tiny.__file__)
        cls.original_source = cls.source_path.read_bytes()
        marker = re.search(r'// BEGIN PORTABLE MARKER WRITER\n(.*?)// END PORTABLE MARKER WRITER',
                           tiny.APP_SOURCE, re.S)
        if marker is None:
            raise AssertionError('Portable marker writer block is missing')
        headers = '\n'.join(re.findall(r'^#include <[^>]+>$', tiny.APP_SOURCE, re.M))
        harness = '#define _POSIX_C_SOURCE 200809L\n' + headers + r"""
static int AyuHarnessMode = 0;
static unsigned int AyuHarnessWriteCalls = 0;
static ssize_t AyuHarnessWrite(int descriptor, const void *bytes, size_t length) {
    if (AyuHarnessMode == 2) { errno = EIO; return -1; }
    if (AyuHarnessMode == 1 && AyuHarnessWriteCalls++ == 0) { errno = EINTR; return -1; }
    if (AyuHarnessMode == 1 && length > 1) { length = 1; }
    return write(descriptor, bytes, length);
}
#define write AyuHarnessWrite
""" + marker[1] + r"""
#undef write
int main(int argc, char *argv[]) {
    if (argc != 3 && argc != 4) { return 2; }
    if (argc == 4 && strcmp(argv[3], "short-eintr") == 0) { AyuHarnessMode = 1; }
    if (argc == 4 && strcmp(argv[3], "write-error") == 0) { AyuHarnessMode = 2; }
    const char *stage = argc == 4 && strcmp(argv[3], "null-stage") == 0 ? NULL : argv[2];
    AyuModernWriteStage(argv[1], stage);
    return 0;
}
"""
        code = cls.build / 'marker_writer_harness.c'
        code.write_text(harness)
        cls.executable = cls.build / 'marker_writer_harness'
        result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', str(code),
                                 '-o', str(cls.executable)], capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise AssertionError('Portable marker writer compilation failed:\n' + result.stderr.decode(errors='replace'))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ayu-marker-filesystem-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.directory = self.base / 'marker-directory'

    def invoke(self, stage, directory=None, mode=None):
        arguments = [str(self.executable), str(directory or self.directory), '' if stage is None else stage]
        if stage is None:
            mode = 'null-stage'
        if mode:
            arguments.append(mode)
        result = subprocess.run(arguments, capture_output=True, timeout=3, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b''); self.assertEqual(result.stderr, b'')
        self.assertEqual(self.source_path.read_bytes(), self.original_source)

    def assert_only_private_stage(self, stage):
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'stage'})
        path = self.directory / 'stage'
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.read_bytes(), stage.encode('ascii'))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(path.stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_writer_atomically_replaces_stage_with_private_file_and_keeps_open_reader_snapshot(self):
        self.invoke('mainEntered')
        self.assert_only_private_stage('mainEntered')
        marker = self.directory / 'stage'
        with marker.open('rb') as original:
            previous_inode = os.fstat(original.fileno()).st_ino
            self.directory.chmod(0o777)
            marker.chmod(0o666)
            self.invoke('didFinish')
            self.assertNotEqual(marker.stat().st_ino, previous_inode)
            self.assertEqual(original.read(), b'mainEntered')
        self.assert_only_private_stage('didFinish')
        self.invoke('sceneVisible')
        self.assert_only_private_stage('sceneVisible')

    def test_writer_ignores_invalid_empty_long_non_ascii_and_null_stages_without_mutation(self):
        invalid = ('', 'x', 'MAINENTERED', 'sceneVisible\n', '../stage', 'x' * 129,
                   'sceneVisible' + 'x' * 10000, '\u00e9', None)
        for stage in invalid:
            with self.subTest(stage=str(stage)[:20]):
                self.invoke(stage)
                self.assertFalse(self.directory.exists())
        self.invoke('mainEntered')
        original = TinyXcodeProjectTests.snapshot(self.base)
        for stage in invalid:
            with self.subTest(stage=str(stage)[:20]):
                self.invoke(stage)
                self.assertEqual(TinyXcodeProjectTests.snapshot(self.base), original)

    def test_writer_rejects_directory_symlink_file_and_fifo_without_touching_external_content(self):
        real = self.base / 'external'; real.mkdir(mode=0o755)
        sentinel = real / 'sentinel'; sentinel.write_bytes(b'untouched external content')
        self.directory.symlink_to(real, target_is_directory=True)
        before = TinyXcodeProjectTests.snapshot(self.base)
        self.invoke('sceneVisible')
        self.assertEqual(TinyXcodeProjectTests.snapshot(self.base), before)
        self.directory.unlink()
        self.directory.write_bytes(b'untouched non-directory')
        before = TinyXcodeProjectTests.snapshot(self.base)
        self.invoke('sceneVisible')
        self.assertEqual(TinyXcodeProjectTests.snapshot(self.base), before)
        self.directory.unlink()
        os.mkfifo(self.directory)
        before = TinyXcodeProjectTests.snapshot(self.base)
        self.invoke('sceneVisible')
        self.assertEqual(TinyXcodeProjectTests.snapshot(self.base), before)

    def test_writer_replaces_destination_symlink_or_hardlink_without_modifying_external_target(self):
        self.directory.mkdir(mode=0o700)
        target = self.base / 'external-sentinel'; target.write_bytes(b'untouched external target'); target.chmod(0o644)
        initial = (target.read_bytes(), target.stat().st_ino, target.stat().st_mode)
        marker = self.directory / 'stage'
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if marker.exists() or marker.is_symlink(): marker.unlink()
                if kind == 'symlink': marker.symlink_to(target)
                else: os.link(target, marker)
                self.invoke('didFinish')
                self.assert_only_private_stage('didFinish')
                self.assertEqual((target.read_bytes(), target.stat().st_ino, target.stat().st_mode), initial)
                self.assertEqual(target.stat().st_nlink, 1)

    def test_writer_handles_short_writes_and_eintr_before_publishing_complete_stage(self):
        self.invoke('sceneVisible', mode='short-eintr')
        self.assert_only_private_stage('sceneVisible')

    def test_writer_removes_unpublished_temporary_files_after_write_or_rename_failure(self):
        self.invoke('mainEntered')
        original = TinyXcodeProjectTests.snapshot(self.directory)
        self.invoke('sceneVisible', mode='write-error')
        self.assertEqual(TinyXcodeProjectTests.snapshot(self.directory), original)
        marker = self.directory / 'stage'; marker.unlink(); marker.mkdir()
        self.invoke('didFinish')
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'stage'})
        self.assertTrue(marker.is_dir())
        self.assertEqual(list(marker.iterdir()), [])



if __name__ == '__main__':
    unittest.main()
