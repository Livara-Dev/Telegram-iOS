// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

@main
struct MediaPlaybackRepairHostChecks {
    static func main() {
        var checks = 0
        func check(_ condition: Bool) { precondition(condition); checks += 1 }
        for bits in 0..<32 {
            var intent = ArchivePlaybackIntent()
            intent.isVisible = bits & 1 != 0
            intent.isReadable = bits & 2 != 0
            intent.isReady = bits & 4 != 0
            intent.hasAudioSession = bits & 8 != 0
            intent.wantsPlayback = bits & 16 != 0
            check(intent.shouldPlay == (bits == 31))
            intent.stop(); check(!intent.shouldPlay)
        }
        for invalid in [Double.nan, Double.infinity, -1, 1e100] { check(ArchivePlaybackIntent.clock(invalid) == "0:00") }
        check(ArchivePlaybackIntent.clock(63.8) == "1:03")
        check(ArchivePlaybackIntent.clock(3602) == "1:00:02")
        let signatures = [Data([0xff, 0xd8, 0xff]), Data([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Data("GIF89a".utf8), Data("RIFF0000WEBP".utf8)]
        for signature in signatures {
            for kind in ["video", "videoMessage", "voice"] { check(ArchiveMediaPayloadGuard.rejectsStillImage(kind: kind, prefix: signature)) }
            for kind in ["photo", "animation", "document"] { check(!ArchiveMediaPayloadGuard.rejectsStillImage(kind: kind, prefix: signature)) }
        }
        check(!ArchiveMediaPayloadGuard.rejectsStillImage(kind: "voice", prefix: Data("OggS".utf8)))
        check(!ArchiveMediaPayloadGuard.rejectsStillImage(kind: "video", prefix: Data([0,0,0,32,0x66,0x74,0x79,0x70])))
        print("Host policy assertions: \(checks). No native iOS playback claimed.")
    }
}
