# Modified for AyuGram iOS.
# Modification dates recorded in Git history: 2026-11-06.
# See this file's history for the corresponding changes.
load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")

swift_library(
    name = "ChatListFilterTabContainerNode",
    module_name = "ChatListFilterTabContainerNode",
    srcs = glob([
        "Sources/**/*.swift",
    ]),
    copts = [
        "-warnings-as-errors",
    ],
    deps = [
        "//submodules/SSignalKit/SwiftSignalKit",
        "//submodules/AsyncDisplayKit",
        "//submodules/Display",
        "//submodules/TelegramCore",
        "//submodules/TelegramPresentationData",
        "//submodules/TelegramUI/Components/TextNodeWithEntities",
        "//submodules/AccountContext",
        "//submodules/ComponentFlow",
        "//submodules/Components/ComponentDisplayAdapters",
        "//submodules/TelegramUI/Components/GlassBackgroundComponent",
        "//submodules/TelegramUI/Components/LiquidLens",
    ],
    visibility = [
        "//visibility:public",
    ],
)
