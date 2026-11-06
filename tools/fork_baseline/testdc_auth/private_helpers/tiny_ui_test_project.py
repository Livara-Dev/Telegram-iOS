# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Generate one Apple-only UI test for the independently retained renderer.

The project contains no application target or third-party dependency. Xcode
must generate its own XCTest runner and plan; writing these inputs does not
establish native compilation, visible UI, or authenticated account behavior.
"""
import os
from pathlib import Path
import plistlib


TARGET = 'AyuRetainedOptionsUITests'
TEST_BUNDLE = 'org.livara.ayugram.tests.retained-options-ui'
HOST_BUNDLE = 'org.livara.ayugram.tests.custome-options'
EXPECTED_CASE = 'AyuRetainedOptionsUITests.testRetainedCustomeOptionsRoot'

TEST_SOURCE = '''// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
import Foundation
import XCTest

// Account-free retained renderer only: no authentication or account bindings.
final class AyuRetainedOptionsUITests: XCTestCase {
    @MainActor
    func testRetainedCustomeOptionsRoot() {
        // Keep ordinary assertion failures alive through the fixed attachment.
        // A process crash or an unavailable screenshot is not a passing result.
        self.continueAfterFailure = true
        let app = XCUIApplication(bundleIdentifier: "org.livara.ayugram.tests.custome-options")
        defer {
            let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
            attachment.name = "retained-root"
            attachment.lifetime = .keepAlways
            self.add(attachment)
        }
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        XCTAssertEqual(app.state, .runningForeground)

        let navigation = app.navigationBars["Custome options"].firstMatch
        XCTAssertTrue(navigation.waitForExistence(timeout: 30))
        let title = navigation.staticTexts["Custome options"].firstMatch
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        XCTAssertEqual(title.label, "Custome options")

        let table = app.tables["ayugram.custom-options.list"].firstMatch
        XCTAssertTrue(table.waitForExistence(timeout: 10))
        XCTAssertTrue(table.isHittable)
        let archive = table.switches["ayugram.custom-options.toggle.archive"].firstMatch
        XCTAssertTrue(archive.waitForExistence(timeout: 10))
        XCTAssertTrue(archive.isEnabled)
        XCTAssertTrue(archive.isHittable)
    }
}
'''

PROJECT = '''// !$*UTF8*$!
// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
{
	archiveVersion = 1;
	classes = {};
	objectVersion = 56;
	objects = {
		110000000000000000000001 /* Test in Sources */ = {isa = PBXBuildFile; fileRef = 210000000000000000000001; };
		110000000000000000000002 /* Foundation in Frameworks */ = {isa = PBXBuildFile; fileRef = 210000000000000000000003; };
		110000000000000000000003 /* XCTest in Frameworks */ = {isa = PBXBuildFile; fileRef = 210000000000000000000004; };
		210000000000000000000001 /* Test source */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = AyuRetainedOptionsUITests.swift; sourceTree = "<group>"; };
		210000000000000000000002 /* Test Info */ = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>"; };
		210000000000000000000003 /* Foundation */ = {isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = Foundation.framework; path = System/Library/Frameworks/Foundation.framework; sourceTree = SDKROOT; };
		210000000000000000000004 /* XCTest */ = {isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = XCTest.framework; path = Platforms/iPhoneSimulator.platform/Developer/Library/Frameworks/XCTest.framework; sourceTree = DEVELOPER_DIR; };
		210000000000000000000005 /* Test product */ = {isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = AyuRetainedOptionsUITests.xctest; sourceTree = BUILT_PRODUCTS_DIR; };
		310000000000000000000001 /* Root */ = {isa = PBXGroup; children = (310000000000000000000002, 310000000000000000000003, 310000000000000000000004, ); sourceTree = "<group>"; };
		310000000000000000000002 /* Tests */ = {isa = PBXGroup; children = (210000000000000000000001, 210000000000000000000002, ); path = AyuRetainedOptionsUITests; sourceTree = "<group>"; };
		310000000000000000000003 /* Frameworks */ = {isa = PBXGroup; children = (210000000000000000000003, 210000000000000000000004, ); name = Frameworks; sourceTree = "<group>"; };
		310000000000000000000004 /* Products */ = {isa = PBXGroup; children = (210000000000000000000005, ); name = Products; sourceTree = "<group>"; };
		410000000000000000000001 /* Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (110000000000000000000001, ); runOnlyForDeploymentPostprocessing = 0; };
		410000000000000000000002 /* Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (110000000000000000000002, 110000000000000000000003, ); runOnlyForDeploymentPostprocessing = 0; };
		410000000000000000000003 /* Resources */ = {isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; };
		510000000000000000000001 /* UI tests */ = {
			isa = PBXNativeTarget; buildConfigurationList = 710000000000000000000002;
			buildPhases = (410000000000000000000001, 410000000000000000000002, 410000000000000000000003, );
			buildRules = (); dependencies = ();
			name = AyuRetainedOptionsUITests; productName = AyuRetainedOptionsUITests;
			productReference = 210000000000000000000005; productType = "com.apple.product-type.bundle.ui-testing";
		};
		610000000000000000000001 /* Project */ = {
			isa = PBXProject;
			attributes = {BuildIndependentTargetsInParallel = 0; LastUpgradeCheck = 2710; TargetAttributes = {510000000000000000000001 = {CreatedOnToolsVersion = 27.1; }; }; };
			buildConfigurationList = 710000000000000000000001; compatibilityVersion = "Xcode 14.0";
			developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base, );
			mainGroup = 310000000000000000000001; productRefGroup = 310000000000000000000004;
			projectDirPath = ""; projectRoot = ""; targets = (510000000000000000000001, );
		};
		710000000000000000000001 /* Project configurations */ = {isa = XCConfigurationList; buildConfigurations = (910000000000000000000001, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
		710000000000000000000002 /* Test configurations */ = {isa = XCConfigurationList; buildConfigurations = (910000000000000000000002, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
		910000000000000000000001 /* Project Debug */ = {
			isa = XCBuildConfiguration; name = Debug;
			buildSettings = {ARCHS = arm64; CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; CODE_SIGNING_ALLOWED = YES; CODE_SIGN_IDENTITY = "-"; CODE_SIGN_STYLE = Manual; CODE_SIGN_INJECT_BASE_ENTITLEMENTS = YES; DEBUG_INFORMATION_FORMAT = dwarf; ENABLE_TESTABILITY = YES; GCC_TREAT_WARNINGS_AS_ERRORS = YES; IPHONEOS_DEPLOYMENT_TARGET = 15.0; ONLY_ACTIVE_ARCH = YES; SDKROOT = iphonesimulator; SUPPORTED_PLATFORMS = iphonesimulator; SWIFT_VERSION = 5.0; SWIFT_TREAT_WARNINGS_AS_ERRORS = YES; TARGETED_DEVICE_FAMILY = 1; };
		};
		910000000000000000000002 /* Test Debug */ = {
			isa = XCBuildConfiguration; name = Debug;
			buildSettings = {ENABLE_TESTING_SEARCH_PATHS = YES; FRAMEWORK_SEARCH_PATHS = ("$(inherited)", "$(PLATFORM_DIR)/Developer/Library/Frameworks", ); GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = AyuRetainedOptionsUITests/Info.plist; LD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks", "@loader_path/Frameworks", ); PRODUCT_BUNDLE_IDENTIFIER = "org.livara.ayugram.tests.retained-options-ui"; PRODUCT_MODULE_NAME = AyuRetainedOptionsUITests; PRODUCT_NAME = "$(TARGET_NAME)"; USES_XCTRUNNER = YES; };
		};
	};
	rootObject = 610000000000000000000001;
}
'''

SCHEME = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- Created for the AyuGram iOS fork on 2026-11-06. -->
<Scheme LastUpgradeVersion="2710" version="1.3">
  <BuildAction parallelizeBuildables="NO" buildImplicitDependencies="NO">
    <BuildActionEntries>
      <BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="YES">
        <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="510000000000000000000001" BuildableName="AyuRetainedOptionsUITests.xctest" BlueprintName="AyuRetainedOptionsUITests" ReferencedContainer="container:AyuRetainedOptionsUITests.xcodeproj"/>
      </BuildActionEntry>
    </BuildActionEntries>
  </BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="NO" codeCoverageEnabled="NO" systemAttachmentLifetime="keepNever" userAttachmentLifetime="keepAlways">
    <Testables>
      <TestableReference skipped="NO" parallelizable="NO">
        <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="510000000000000000000001" BuildableName="AyuRetainedOptionsUITests.xctest" BlueprintName="AyuRetainedOptionsUITests" ReferencedContainer="container:AyuRetainedOptionsUITests.xcodeproj"/>
      </TestableReference>
    </Testables>
  </TestAction>
  <AnalyzeAction buildConfiguration="Debug"/>
</Scheme>
'''


def create_project(root):
    """Write four fixed input files under a new private canonical directory."""
    root = Path(root)
    try:
        valid = (root.is_absolute() and not root.exists() and not root.is_symlink()
                 and root.parent.is_dir() and root == root.resolve(strict=False)
                 and not any(path.is_symlink() for path in root.parents))
    except (OSError, RuntimeError):
        valid = False
    if not valid:
        raise ValueError('A fresh canonical project root under a real existing parent is required')
    root.mkdir(mode=0o700)
    for relative in (TARGET, TARGET + '.xcodeproj', TARGET + '.xcodeproj/xcshareddata',
                     TARGET + '.xcodeproj/xcshareddata/xcschemes'):
        (root / relative).mkdir(mode=0o700)
    info = {'CFBundleDevelopmentRegion': 'en', 'CFBundleExecutable': '$(EXECUTABLE_NAME)',
            'CFBundleIdentifier': '$(PRODUCT_BUNDLE_IDENTIFIER)', 'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundleName': '$(PRODUCT_NAME)', 'CFBundlePackageType': 'BNDL',
            'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator'], 'MinimumOSVersion': '15.0',
            'UIDeviceFamily': [1]}
    files = {TARGET + '/' + TARGET + '.swift': TEST_SOURCE.encode('utf-8'),
             TARGET + '/Info.plist': plistlib.dumps(info),
             TARGET + '.xcodeproj/project.pbxproj': PROJECT.encode('utf-8'),
             TARGET + '.xcodeproj/xcshareddata/xcschemes/' + TARGET + '.xcscheme': SCHEME.encode('utf-8')}
    for relative, data in files.items():
        descriptor = os.open(root / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return {'project': root / (TARGET + '.xcodeproj'), 'scheme': TARGET, 'target': TARGET,
            'test_bundle': TEST_BUNDLE, 'host_bundle': HOST_BUNDLE, 'expected_case': EXPECTED_CASE}
