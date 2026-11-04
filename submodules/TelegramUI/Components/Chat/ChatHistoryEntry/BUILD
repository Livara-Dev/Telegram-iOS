load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_application", "ios_unit_test")
load("@build_bazel_rules_apple//apple/testing/default_runner:ios_test_runner.bzl", "ios_test_runner")
load("//build-system/bazel-utils:plist_fragment.bzl", "plist_fragment")

swift_library(
    name = "ChatHistoryEntry",
    module_name = "ChatHistoryEntry",
    srcs = glob([
        "Sources/**/*.swift",
    ]),
    copts = [
        "-warnings-as-errors",
    ],
    deps = [
        "//submodules/TelegramCore",
        "//submodules/TelegramPresentationData",
        "//submodules/MergeLists",
        "//submodules/TemporaryCachedPeerDataManager",
        "//submodules/AccountContext",
    ],
    visibility = [
        "//visibility:public",
    ],
)

swift_library(
    name = "ChatLocalTextFilterProjectionTestsLib",
    testonly = True,
    srcs = glob(["Tests/**/*.swift"], exclude = ["Tests/ChatLocalTextFilterTestHost.swift"]),
    deps = [
        ":ChatHistoryEntry",
        "//submodules/TelegramCore",
        "//submodules/TelegramPresentationData",
    ],
)

swift_library(
    name = "ChatLocalTextFilterTestHostLib",
    testonly = True,
    srcs = ["Tests/ChatLocalTextFilterTestHost.swift"],
)

plist_fragment(
    name = "ChatLocalTextFilterTestHostInfo",
    extension = "plist",
    template = """
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleDisplayName</key>
    <string>Local Filter Projection Tests</string>
    <key>UILaunchScreen</key>
    <dict/>
    <key>UIApplicationSceneManifest</key>
    <dict>
        <key>UIApplicationSupportsMultipleScenes</key>
        <false/>
        <key>UISceneConfigurations</key>
        <dict>
            <key>UIWindowSceneSessionRoleApplication</key>
            <array>
                <dict>
                    <key>UISceneConfigurationName</key>
                    <string>Default Configuration</string>
                </dict>
            </array>
        </dict>
    </dict>
    """,
)

ios_application(
    name = "ChatLocalTextFilterTestHost",
    testonly = True,
    bundle_id = "org.livara.ayugram.tests.local-text-filter",
    families = ["iphone"],
    minimum_os_version = "15.0",
    infoplists = [":ChatLocalTextFilterTestHostInfo"],
    # ChatPresentationData needs real generated strings in getAppBundle().
    # Putting them only in the XCTest bundle would leave the main host empty.
    resources = [
        "//Telegram:AppStringResources",
        "//Telegram:GeneratedPresentationStrings/Resources/PresentationStrings.data",
    ],
    deps = [":ChatLocalTextFilterTestHostLib"],
)

ios_test_runner(
    name = "ChatLocalTextFilterProjectionTestRunner",
    device_type = "iPhone 17",
    os_version = "27.0",
)

ios_unit_test(
    name = "ChatLocalTextFilterProjectionTests",
    # Bazel's default 300s can expire during simulator startup, before the
    # pinned runner finishes its three bounded 150s startup attempts/reboots.
    # Allow 900s for that recovery without changing any test assertions.
    timeout = "long",
    # AccountContext -> LegacyComponents references MPVolumeView.
    linkopts = ["-framework", "MediaPlayer"],
    minimum_os_version = "15.0",
    runner = ":ChatLocalTextFilterProjectionTestRunner",
    test_host = ":ChatLocalTextFilterTestHost",
    deps = [":ChatLocalTextFilterProjectionTestsLib"],
    visibility = ["//visibility:public"],
)
