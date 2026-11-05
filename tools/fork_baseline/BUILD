load("@rules_shell//shell:sh_binary.bzl", "sh_binary")

sh_binary(
    name = "projection_test_capture",
    testonly = True,
    srcs = ["projection_test_capture.sh"],
    data = ["test_product_capture.py"],
    visibility = ["//submodules/TelegramUI/Components/Chat/ChatHistoryEntry:__pkg__"],
)
