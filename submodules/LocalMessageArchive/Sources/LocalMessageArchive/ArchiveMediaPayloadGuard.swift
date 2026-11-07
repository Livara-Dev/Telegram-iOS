// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Reject known still-image signatures in audiovisual slots. This is a narrow
/// type-confusion check, not a claim that arbitrary remaining bytes are playable.
enum ArchiveMediaPayloadGuard {
    static func rejectsStillImage(kind: String, prefix: Data) -> Bool {
        guard ["video", "videoMessage", "voice"].contains(kind) else { return false }
        let signatures: [[UInt8]] = [
            [0xff, 0xd8, 0xff], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
            Array("GIF87a".utf8), Array("GIF89a".utf8), [0x49, 0x49, 0x2a, 0x00], [0x4d, 0x4d, 0x00, 0x2a]
        ]
        if signatures.contains(where: { prefix.starts(with: $0) }) { return true }
        if prefix.count >= 12, prefix.prefix(4) == Data("RIFF".utf8), prefix.dropFirst(8).prefix(4) == Data("WEBP".utf8) { return true }
        return false
    }
}
