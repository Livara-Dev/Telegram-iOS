load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_application", "ios_unit_test", "ios_ui_test")
load("@build_bazel_rules_apple//apple/testing/default_runner:ios_test_runner.bzl", "ios_test_runner")
load("//build-system/bazel-utils:plist_fragment.bzl", "plist_fragment")

# Compile the exact production renderers, without the account binding module.
swift_library(
    name = "CustomeOptionsRenderer",
    module_name = "CustomeOptionsRenderer",
    testonly = True,
    srcs = ["//submodules/SettingsUI:CustomeOptionsRendererSources"],
    copts = ["-warnings-as-errors"],
    deps = ["//submodules/Display:Display", "//submodules/SSignalKit/SwiftSignalKit:SwiftSignalKit"],
)

swift_library(
    name = "CustomeOptionsFixture",
    module_name = "CustomeOptionsFixture",
    testonly = True,
    srcs = ["CustomeOptionsFixture.swift"],
    copts = ["-warnings-as-errors"],
    deps = [":CustomeOptionsRenderer", "//submodules/SSignalKit/SwiftSignalKit:SwiftSignalKit"],
)

swift_library(
    name = "CustomeOptionsHostLib",
    testonly = True,
    srcs = ["CustomeOptionsHost.swift"],
    copts = ["-warnings-as-errors"],
    deps = [":CustomeOptionsFixture"],
)

plist_fragment(
    name = "CustomeOptionsHostInfo",
    extension = "plist",
    template = """
    <key>CFBundleVersion</key><string>1</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleDisplayName</key><string>AyuGram Renderer Test</string>
    <key>UILaunchScreen</key><dict/>
    <key>UIApplicationSceneManifest</key><dict>
        <key>UIApplicationSupportsMultipleScenes</key><false/>
        <key>UISceneConfigurations</key><dict>
            <key>UIWindowSceneSessionRoleApplication</key><array><dict>
                <key>UISceneConfigurationName</key><string>Default Configuration</string>
            </dict></array>
        </dict>
    </dict>
    """,
)

ios_application(
    name = "CustomeOptionsHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.custome-options",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":CustomeOptionsHostInfo"],
    deps = [":CustomeOptionsHostLib"],
)

ios_test_runner(
    name = "CustomeOptionsTestRunner",
    testonly = True,
    device_type = "iPhone 15 Pro",
    os_version = "27.0",
)

swift_library(
    name = "CustomeOptionsControllerTestsLib",
    testonly = True,
    srcs = ["CustomeOptionsControllerTests.swift"],
    deps = [":CustomeOptionsRenderer", ":CustomeOptionsFixture"],
)

ios_unit_test(
    name = "CustomeOptionsControllerTests",
    minimum_os_version = "15.0",
    runner = ":CustomeOptionsTestRunner",
    test_host = ":CustomeOptionsHost",
    deps = [":CustomeOptionsControllerTestsLib"],
    timeout = "long",
)

swift_library(
    name = "CustomeOptionsInteractionTestsLib",
    testonly = True,
    srcs = ["CustomeOptionsInteractionTests.swift"],
)

ios_ui_test(
    name = "CustomeOptionsInteractionTests",
    minimum_os_version = "15.0",
    runner = ":CustomeOptionsTestRunner",
    test_host = ":CustomeOptionsHost",
    deps = [":CustomeOptionsInteractionTestsLib"],
    timeout = "long",
)
