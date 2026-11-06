# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Write a tiny, unsigned UIKit/XCTest control project without invoking Xcode.

This draft supplies input for a future Xcode-generated test plan. Writing the
project does not establish that it builds, launches, or executes a test.
"""
import os
from pathlib import Path
import plistlib


APP_NAME = 'AyuProjectionControl'
TEST_NAME = 'AyuProjectionControlTests'
APP_BUNDLE = 'org.livara.ayugram.tests.modern-projection-control'
TEST_BUNDLE = 'org.livara.ayugram.tests.modern-projection-controlTests'
EXPECTED_CASE = 'AyuProjectionControlTests.testRunsInApplicationHost'

APP_SOURCE = '''// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
#import <UIKit/UIKit.h>

@interface AyuProjectionControlSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

@implementation AyuProjectionControlSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session
      options:(UISceneConnectionOptions *)connectionOptions {
    if (![scene isKindOfClass:[UIWindowScene class]]) {
        return;
    }
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    UIViewController *controller = [[UIViewController alloc] init];
    controller.view.backgroundColor = [UIColor systemBackgroundColor];
    UILabel *label = [[UILabel alloc] init];
    label.text = @"Projection control";
    label.textColor = [UIColor labelColor];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    [controller.view addSubview:label];
    [NSLayoutConstraint activateConstraints:@[
        [label.centerXAnchor constraintEqualToAnchor:controller.view.centerXAnchor],
        [label.centerYAnchor constraintEqualToAnchor:controller.view.centerYAnchor]
    ]];
    self.window.rootViewController = controller;
    [self.window makeKeyAndVisible];
}
@end

@interface AyuProjectionControlAppDelegate : UIResponder <UIApplicationDelegate>
@end

@implementation AyuProjectionControlAppDelegate
- (BOOL)application:(UIApplication *)application
        didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    return YES;
}
- (UISceneConfiguration *)application:(UIApplication *)application
        configurationForConnectingSceneSession:(UISceneSession *)connectingSceneSession
        options:(UISceneConnectionOptions *)options {
    UISceneConfiguration *configuration = [[UISceneConfiguration alloc]
        initWithName:@"Default Configuration" sessionRole:connectingSceneSession.role];
    configuration.delegateClass = [AyuProjectionControlSceneDelegate class];
    return configuration;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil,
            NSStringFromClass([AyuProjectionControlAppDelegate class]));
    }
}
'''

TEST_SOURCE = '''// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
#import <XCTest/XCTest.h>
#import <UIKit/UIKit.h>

@interface AyuProjectionControlTests : XCTestCase
@end

@implementation AyuProjectionControlTests
- (void)testRunsInApplicationHost {
    XCTAssertEqualObjects([NSBundle mainBundle].bundleIdentifier,
        @"org.livara.ayugram.tests.modern-projection-control");
    XCTAssertNotNil([UIApplication sharedApplication]);
}
@end
'''

PROJECT = '''// !$*UTF8*$!
// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
{
	archiveVersion = 1;
	classes = {};
	objectVersion = 56;
	objects = {
		100000000000000000000001 /* main.m in Sources */ = {isa = PBXBuildFile; fileRef = 200000000000000000000001 /* main.m */; };
		100000000000000000000002 /* Tests.m in Sources */ = {isa = PBXBuildFile; fileRef = 200000000000000000000002 /* Tests.m */; };
		100000000000000000000003 /* UIKit.framework in Frameworks */ = {isa = PBXBuildFile; fileRef = 200000000000000000000003 /* UIKit.framework */; };
		100000000000000000000004 /* UIKit.framework in Frameworks */ = {isa = PBXBuildFile; fileRef = 200000000000000000000003 /* UIKit.framework */; };
		100000000000000000000005 /* XCTest.framework in Frameworks */ = {isa = PBXBuildFile; fileRef = 200000000000000000000004 /* XCTest.framework */; };
		200000000000000000000001 /* main.m */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; path = main.m; sourceTree = "<group>"; };
		200000000000000000000002 /* Tests.m */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; path = AyuProjectionControlTests.m; sourceTree = "<group>"; };
		200000000000000000000003 /* UIKit.framework */ = {isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = UIKit.framework; path = System/Library/Frameworks/UIKit.framework; sourceTree = SDKROOT; };
		200000000000000000000004 /* XCTest.framework */ = {isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = XCTest.framework; path = Platforms/iPhoneSimulator.platform/Developer/Library/Frameworks/XCTest.framework; sourceTree = DEVELOPER_DIR; };
		200000000000000000000005 /* AyuProjectionControl.app */ = {isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = AyuProjectionControl.app; sourceTree = BUILT_PRODUCTS_DIR; };
		200000000000000000000006 /* AyuProjectionControlTests.xctest */ = {isa = PBXFileReference; explicitFileType = wrapper.cfbundle; includeInIndex = 0; path = AyuProjectionControlTests.xctest; sourceTree = BUILT_PRODUCTS_DIR; };
		200000000000000000000007 /* App Info.plist */ = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>"; };
		200000000000000000000008 /* Test Info.plist */ = {isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = "<group>"; };
		300000000000000000000001 /* Root */ = {isa = PBXGroup; children = (300000000000000000000002, 300000000000000000000003, 300000000000000000000004, 300000000000000000000005, ); sourceTree = "<group>"; };
		300000000000000000000002 /* App */ = {isa = PBXGroup; children = (200000000000000000000001, 200000000000000000000007, ); path = AyuProjectionControl; sourceTree = "<group>"; };
		300000000000000000000003 /* Tests */ = {isa = PBXGroup; children = (200000000000000000000002, 200000000000000000000008, ); path = AyuProjectionControlTests; sourceTree = "<group>"; };
		300000000000000000000004 /* Frameworks */ = {isa = PBXGroup; children = (200000000000000000000003, 200000000000000000000004, ); name = Frameworks; sourceTree = "<group>"; };
		300000000000000000000005 /* Products */ = {isa = PBXGroup; children = (200000000000000000000005, 200000000000000000000006, ); name = Products; sourceTree = "<group>"; };
		400000000000000000000001 /* App Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (100000000000000000000001, ); runOnlyForDeploymentPostprocessing = 0; };
		400000000000000000000002 /* Test Sources */ = {isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (100000000000000000000002, ); runOnlyForDeploymentPostprocessing = 0; };
		400000000000000000000003 /* App Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (100000000000000000000003, ); runOnlyForDeploymentPostprocessing = 0; };
		400000000000000000000004 /* Test Frameworks */ = {isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (100000000000000000000004, 100000000000000000000005, ); runOnlyForDeploymentPostprocessing = 0; };
		500000000000000000000001 /* AyuProjectionControl */ = {
			isa = PBXNativeTarget; buildConfigurationList = 700000000000000000000002;
			buildPhases = (400000000000000000000001, 400000000000000000000003, ); buildRules = (); dependencies = ();
			name = AyuProjectionControl; productName = AyuProjectionControl; productReference = 200000000000000000000005; productType = "com.apple.product-type.application";
		};
		500000000000000000000002 /* AyuProjectionControlTests */ = {
			isa = PBXNativeTarget; buildConfigurationList = 700000000000000000000003;
			buildPhases = (400000000000000000000002, 400000000000000000000004, ); buildRules = (); dependencies = (800000000000000000000002, );
			name = AyuProjectionControlTests; productName = AyuProjectionControlTests; productReference = 200000000000000000000006; productType = "com.apple.product-type.bundle.unit-test";
		};
		600000000000000000000001 /* Project */ = {
			isa = PBXProject;
			attributes = {BuildIndependentTargetsInParallel = 0; LastUpgradeCheck = 2710; TargetAttributes = {500000000000000000000001 = {CreatedOnToolsVersion = 27.1; }; 500000000000000000000002 = {CreatedOnToolsVersion = 27.1; TestTargetID = 500000000000000000000001; }; }; };
			buildConfigurationList = 700000000000000000000001; compatibilityVersion = "Xcode 14.0"; developmentRegion = en;
			hasScannedForEncodings = 0; knownRegions = (en, Base, ); mainGroup = 300000000000000000000001;
			productRefGroup = 300000000000000000000005; projectDirPath = ""; projectRoot = "";
			targets = (500000000000000000000001, 500000000000000000000002, );
		};
		700000000000000000000001 /* Project Configurations */ = {isa = XCConfigurationList; buildConfigurations = (900000000000000000000001, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
		700000000000000000000002 /* App Configurations */ = {isa = XCConfigurationList; buildConfigurations = (900000000000000000000002, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
		700000000000000000000003 /* Test Configurations */ = {isa = XCConfigurationList; buildConfigurations = (900000000000000000000003, ); defaultConfigurationIsVisible = 0; defaultConfigurationName = Debug; };
		800000000000000000000001 /* App Proxy */ = {isa = PBXContainerItemProxy; containerPortal = 600000000000000000000001; proxyType = 1; remoteGlobalIDString = 500000000000000000000001; remoteInfo = AyuProjectionControl; };
		800000000000000000000002 /* App Dependency */ = {isa = PBXTargetDependency; target = 500000000000000000000001; targetProxy = 800000000000000000000001; };
		900000000000000000000001 /* Project Debug */ = {
			isa = XCBuildConfiguration; name = Debug;
			buildSettings = {ARCHS = arm64; CLANG_ENABLE_MODULES = YES; CLANG_ENABLE_OBJC_ARC = YES; CODE_SIGNING_ALLOWED = NO; CODE_SIGNING_REQUIRED = NO; DEBUG_INFORMATION_FORMAT = dwarf; ENABLE_TESTABILITY = YES; GCC_C_LANGUAGE_STANDARD = gnu17; GCC_OPTIMIZATION_LEVEL = 0; IPHONEOS_DEPLOYMENT_TARGET = 15.0; ONLY_ACTIVE_ARCH = YES; SDKROOT = iphonesimulator; SUPPORTED_PLATFORMS = iphonesimulator; TARGETED_DEVICE_FAMILY = 1; };
		};
		900000000000000000000002 /* App Debug */ = {
			isa = XCBuildConfiguration; name = Debug;
			buildSettings = {GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = AyuProjectionControl/Info.plist; LD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks", ); PRODUCT_BUNDLE_IDENTIFIER = "org.livara.ayugram.tests.modern-projection-control"; PRODUCT_NAME = "$(TARGET_NAME)"; };
		};
		900000000000000000000003 /* Test Debug */ = {
			isa = XCBuildConfiguration; name = Debug;
			buildSettings = {BUNDLE_LOADER = "$(TEST_HOST)"; FRAMEWORK_SEARCH_PATHS = ("$(inherited)", "$(PLATFORM_DIR)/Developer/Library/Frameworks", ); GENERATE_INFOPLIST_FILE = NO; INFOPLIST_FILE = AyuProjectionControlTests/Info.plist; LD_RUNPATH_SEARCH_PATHS = ("$(inherited)", "@executable_path/Frameworks", "@loader_path/Frameworks", ); PRODUCT_BUNDLE_IDENTIFIER = "org.livara.ayugram.tests.modern-projection-controlTests"; PRODUCT_NAME = "$(TARGET_NAME)"; TEST_HOST = "$(BUILT_PRODUCTS_DIR)/AyuProjectionControl.app/AyuProjectionControl"; TEST_TARGET_NAME = AyuProjectionControl; };
		};
	};
	rootObject = 600000000000000000000001 /* Project */;
}
'''

SCHEME = '''<?xml version="1.0" encoding="UTF-8"?>
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- Created for the AyuGram iOS fork on 2026-11-06. -->
<Scheme LastUpgradeVersion="2710" version="1.3">
  <BuildAction parallelizeBuildables="NO" buildImplicitDependencies="YES">
    <BuildActionEntries>
      <BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">
        <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="500000000000000000000001" BuildableName="AyuProjectionControl.app" BlueprintName="AyuProjectionControl" ReferencedContainer="container:AyuProjectionControl.xcodeproj"/>
      </BuildActionEntry>
      <BuildActionEntry buildForTesting="YES" buildForRunning="NO" buildForProfiling="NO" buildForArchiving="NO" buildForAnalyzing="NO">
        <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="500000000000000000000002" BuildableName="AyuProjectionControlTests.xctest" BlueprintName="AyuProjectionControlTests" ReferencedContainer="container:AyuProjectionControl.xcodeproj"/>
      </BuildActionEntry>
    </BuildActionEntries>
  </BuildAction>
  <TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES" codeCoverageEnabled="NO">
    <Testables>
      <TestableReference skipped="NO" parallelizable="NO">
        <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="500000000000000000000002" BuildableName="AyuProjectionControlTests.xctest" BlueprintName="AyuProjectionControlTests" ReferencedContainer="container:AyuProjectionControl.xcodeproj"/>
      </TestableReference>
    </Testables>
    <MacroExpansion>
      <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="500000000000000000000001" BuildableName="AyuProjectionControl.app" BlueprintName="AyuProjectionControl" ReferencedContainer="container:AyuProjectionControl.xcodeproj"/>
    </MacroExpansion>
  </TestAction>
  <LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="NO" debugServiceExtension="internal" allowLocationSimulation="NO">
    <BuildableProductRunnable runnableDebuggingMode="0">
      <BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="500000000000000000000001" BuildableName="AyuProjectionControl.app" BlueprintName="AyuProjectionControl" ReferencedContainer="container:AyuProjectionControl.xcodeproj"/>
    </BuildableProductRunnable>
  </LaunchAction>
  <AnalyzeAction buildConfiguration="Debug"/>
  <ArchiveAction buildConfiguration="Debug" revealArchiveInOrganizer="NO"/>
</Scheme>
'''


def _info(package_type):
    return {'CFBundleDevelopmentRegion': 'en', 'CFBundleExecutable': '$(EXECUTABLE_NAME)',
            'CFBundleIdentifier': '$(PRODUCT_BUNDLE_IDENTIFIER)', 'CFBundleInfoDictionaryVersion': '6.0',
            'CFBundleName': '$(PRODUCT_NAME)', 'CFBundlePackageType': package_type,
            'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1',
            'CFBundleSupportedPlatforms': ['iPhoneSimulator'], 'MinimumOSVersion': '15.0',
            'UIDeviceFamily': [1]}


def create_project(root):
    """Create a fixed project in a fresh private root, without running tools."""
    root = Path(root)
    if (not root.is_absolute() or root.exists() or not root.parent.is_dir() or
            any(path.is_symlink() for path in (root, *root.parents))):
        raise ValueError('A fresh project root under a real existing parent is required')
    root.mkdir(mode=0o700)
    for relative in (APP_NAME, TEST_NAME, APP_NAME + '.xcodeproj',
                     APP_NAME + '.xcodeproj/xcshareddata', APP_NAME + '.xcodeproj/xcshareddata/xcschemes'):
        (root / relative).mkdir(mode=0o700)
    app_info = _info('APPL')
    app_info.update(LSRequiresIPhoneOS=True, UILaunchScreen={}, UISupportedInterfaceOrientations=['UIInterfaceOrientationPortrait'],
                    UIApplicationSceneManifest={'UIApplicationSupportsMultipleScenes': False,
                        'UISceneConfigurations': {'UIWindowSceneSessionRoleApplication': [
                            {'UISceneConfigurationName': 'Default Configuration',
                             'UISceneDelegateClassName': 'AyuProjectionControlSceneDelegate'}]}})
    files = {APP_NAME + '/main.m': APP_SOURCE.encode(),
             APP_NAME + '/Info.plist': plistlib.dumps(app_info),
             TEST_NAME + '/' + TEST_NAME + '.m': TEST_SOURCE.encode(),
             TEST_NAME + '/Info.plist': plistlib.dumps(_info('BNDL')),
             APP_NAME + '.xcodeproj/project.pbxproj': PROJECT.encode(),
             APP_NAME + '.xcodeproj/xcshareddata/xcschemes/' + APP_NAME + '.xcscheme': SCHEME.encode()}
    for relative, data in files.items():
        descriptor = os.open(root / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
    return {'project': root / (APP_NAME + '.xcodeproj'), 'scheme': APP_NAME,
            'app_name': APP_NAME, 'test_name': TEST_NAME, 'app_bundle': APP_BUNDLE,
            'test_bundle': TEST_BUNDLE, 'expected_case': EXPECTED_CASE}
