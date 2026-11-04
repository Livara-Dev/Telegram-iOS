load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")
load("@build_bazel_rules_apple//apple:ios.bzl", "ios_unit_test")
load("@build_bazel_rules_apple//apple/testing/default_runner:ios_test_runner.bzl", "ios_test_runner")

swift_library(
    name = "LocalMessageArchive",
    module_name = "LocalMessageArchive",
    srcs = glob(["Sources/LocalMessageArchive/*.swift"]),
    deps = ["//submodules/sqlcipher"],
    visibility = ["//visibility:public"],
)

swift_library(
    name = "LocalMessageArchiveTestsLib",
    testonly = True,
    srcs = glob(["Tests/LocalMessageArchiveTests/*.swift"]),
    deps = [":LocalMessageArchive", "//submodules/sqlcipher"],
)

ios_test_runner(name = "ArchiveTestRunner", device_type = "iPhone 17", os_version = "27.0")
ios_unit_test(
    name = "LocalMessageArchiveTests",
    minimum_os_version = "15.0",
    runner = ":ArchiveTestRunner",
    deps = [":LocalMessageArchiveTestsLib"],
    visibility = ["//visibility:public"],
)
