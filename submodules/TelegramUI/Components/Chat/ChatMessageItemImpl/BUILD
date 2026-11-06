# Modified for AyuGram iOS.
# Modification dates recorded in Git history: 2026-11-06.
# See this file's history for the corresponding changes.
load("@build_bazel_rules_swift//swift:swift.bzl", "swift_library")

filegroup(
    name = "LocalArchiveRowRendererSource",
    srcs = ["Sources/ChatLocalArchiveRowView.swift"],
    visibility = ["//visibility:public"],
)

swift_library(
    name = "ChatMessageItemImpl",
    module_name = "ChatMessageItemImpl",
    srcs = glob([
        "Sources/**/*.swift",
    ]),
    copts = [
        "-warnings-as-errors",
    ],
    deps = [
        "//submodules/AsyncDisplayKit",
        "//submodules/Display",
        "//submodules/SSignalKit/SwiftSignalKit",
        "//submodules/TelegramCore",
        "//submodules/TelegramPresentationData",
        "//submodules/TelegramUIPreferences",
        "//submodules/AccountContext",
        "//submodules/Emoji",
        "//submodules/PersistentStringHash",
        "//submodules/TelegramUI/Components/ChatControllerInteraction",
        "//submodules/TelegramUI/Components/Chat/ChatHistoryEntry",
        "//submodules/TelegramUI/Components/Chat/ChatMessageItem",
        "//submodules/TelegramUI/Components/Chat/ChatMessageItemCommon",
        "//submodules/TelegramUI/Components/Chat/ChatMessageItemView",
        "//submodules/TelegramUI/Components/Chat/ChatMessageStickerItemNode",
        "//submodules/TelegramUI/Components/Chat/ChatMessageAnimatedStickerItemNode",
        "//submodules/TelegramUI/Components/Chat/ChatMessageBubbleItemNode",
        "//submodules/AvatarNode",
        "//submodules/TelegramUniversalVideoContent",
        "//submodules/MediaPlayer:UniversalMediaPlayer",
        "//submodules/GalleryUI",
        "//submodules/Components/HierarchyTrackingLayer",
        "//submodules/WallpaperBackgroundNode",
        "//submodules/AvatarVideoNode",
        "//submodules/ComponentFlow",
        "//submodules/TelegramUI/Components/EmojiStatusComponent",
    ],
    visibility = [
        "//visibility:public",
    ],
)
