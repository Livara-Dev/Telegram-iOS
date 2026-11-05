load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_unit_test")
load("@build_bazel_rules_apple//apple/testing/default_runner:ios_test_runner.bzl", "ios_test_runner")

swift_library(
    name = "PasscodeCore",
    module_name = "PasscodeCore",
    srcs = glob(["Sources/**/*.swift"]),
    copts = ["-warnings-as-errors"],
    deps = [
        "//submodules/SSignalKit/SwiftSignalKit",
        "//submodules/TelegramCore",
    ],
    visibility = ["//visibility:public"],
)

swift_library(
    name = "PasscodeCoreTestsLib",
    testonly = True,
    srcs = [
        "Tests/PasscodeCoreTests/ApplicationStorageTests.swift",
        "Tests/PasscodeCoreTests/PrivateInstallationKeychainTests.swift",
    ],
    deps = [":PasscodeCore"],
)

ios_test_runner(
    name = "PasscodeCoreTestRunner",
    device_type = "iPhone 17",
    os_version = "27.0",
)

ios_unit_test(
    name = "PasscodeCoreTests",
    timeout = "long",
    minimum_os_version = "15.0",
    runner = ":PasscodeCoreTestRunner",
    deps = [":PasscodeCoreTestsLib"],
    visibility = ["//visibility:public"],
)
