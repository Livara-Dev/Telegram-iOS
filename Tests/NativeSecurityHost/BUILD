load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_application")
load("//build-system/bazel-utils:plist_fragment.bzl", "plist_fragment")

swift_library(
    name = "NativeSecurityTestHostLib",
    testonly = True,
    srcs = ["NativeSecurityTestHost.swift"],
    copts = ["-warnings-as-errors"],
)

plist_fragment(
    name = "NativeSecurityTestHostInfo",
    extension = "plist",
    template = """
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleDisplayName</key><string>Native Security Tests</string>
    <key>NSFaceIDUsageDescription</key>
    <string>Tests verify that Keychain access does not request biometric interaction.</string>
    <key>UILaunchScreen</key><dict/>
    <key>UIApplicationSceneManifest</key>
    <dict>
        <key>UIApplicationSupportsMultipleScenes</key><false/>
        <key>UISceneConfigurations</key>
        <dict>
            <key>UIWindowSceneSessionRoleApplication</key>
            <array><dict>
                <key>UISceneConfigurationName</key><string>Default Configuration</string>
            </dict></array>
        </dict>
    </dict>
    """,
)

# Share only the tiny UIKit entry point. Distinct installed identities prevent
# a reused simulator from retaining a different suite's injected test bundle.
ios_application(
    name = "PasscodeTestHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.native-security.passcode",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":NativeSecurityTestHostInfo"],
    deps = [":NativeSecurityTestHostLib"],
    visibility = ["//submodules/PasscodeCore:__pkg__"],
)

ios_application(
    name = "ArchiveTestHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.native-security.archive",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":NativeSecurityTestHostInfo"],
    deps = [":NativeSecurityTestHostLib"],
    visibility = ["//submodules/LocalMessageArchive:__pkg__"],
)

ios_application(
    name = "PlaybackTestHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.native-security.playback",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":NativeSecurityTestHostInfo"],
    deps = [":NativeSecurityTestHostLib"],
    visibility = ["//submodules/LocalMessageArchive:__pkg__"],
)

plist_fragment(
    name = "ProjectionTestHostDiagnosticInfo",
    extension = "plist",
    template = """
    <key>AyuGramProjectionHostDiagnostics</key><true/>
    """,
)

# Run projection tests through the same verified UIKit entry point as the
# security/media suites. PresentationData still reads genuine main-bundle data.
ios_application(
    name = "ProjectionTestHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.native-security.projection",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":NativeSecurityTestHostInfo", ":ProjectionTestHostDiagnosticInfo"],
    resources = [
        "//Telegram:AppStringResources",
        "//Telegram:GeneratedPresentationStrings/Resources/PresentationStrings.data",
    ],
    deps = [":NativeSecurityTestHostLib"],
    visibility = ["//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:__pkg__"],
)
