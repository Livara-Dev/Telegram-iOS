#!/bin/bash
# Supported test-only rules_apple post action. No credentials are read.
runfiles_root="${RUNFILES_DIR:-${TEST_SRCDIR:-}}"
helper="$runfiles_root/_main/tools/fork_baseline/test_product_capture.py"
if [ -n "$runfiles_root" ] && [ -f "$helper" ]; then
  python3 "$helper"
fi
# Preserve the original XCTest status even if optional capture is unavailable.
exit 0
