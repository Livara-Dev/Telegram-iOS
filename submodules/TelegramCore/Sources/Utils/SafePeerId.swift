// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox

/// Check untrusted persisted identifiers before PeerId's assertion-bearing
/// decoder. This preserves its complete encoded representation, not a feature's
/// eligibility policy: callers still decide which peer namespaces they support.
func telegramCorePeerIdIsSafelyDecodable(_ value: Int64) -> Bool {
    if value == 0x7fffffff00000000 { return true }
    guard value >= 0, UInt64(bitPattern: value) >> 35 <= 0x00ffffff else { return false }
    // Namespace.max/id.zero uses the legacy sentinel, not the normal bit layout.
    return value != 0x0000000700000000
}

func telegramCoreDecodePeerId(_ value: Int64) -> PeerId? {
    guard telegramCorePeerIdIsSafelyDecodable(value) else { return nil }
    return PeerId(value)
}
