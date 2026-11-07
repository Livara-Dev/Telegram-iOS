// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Shared lifecycle gate. A decoded poster frame is not a playing movie.
public struct ArchivePlaybackIntent {
    public var isVisible = false
    public var isReadable = false
    public var isReady = false
    public var hasAudioSession = false
    public var wantsPlayback = false
    public init() {}
    public var shouldPlay: Bool {
        return self.isVisible && self.isReadable && self.isReady && self.hasAudioSession && self.wantsPlayback
    }
    public mutating func stop() {
        self.isVisible = false
        self.isReadable = false
        self.wantsPlayback = false
        self.hasAudioSession = false
    }
    public static func clock(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0, seconds < 7 * 24 * 3600 else { return "0:00" }
        let total = Int(seconds)
        if total >= 3600 { return String(format: "%d:%02d:%02d", total / 3600, (total / 60) % 60, total % 60) }
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
