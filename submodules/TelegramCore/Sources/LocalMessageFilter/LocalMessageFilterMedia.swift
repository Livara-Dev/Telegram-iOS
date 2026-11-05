// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox

/// Classification for presentation-only filtering. It never reads/downloads
/// media bytes or changes consumption, expiry, cache or archive state.
public func localMessageFilterMediaType(_ message: EngineRawMessage) -> LocalMessageFilterMediaType? {
    guard message.media.count == 1 else { return nil }
    if message.media[0] is TelegramMediaImage { return .photo }
    guard let file = message.media[0] as? TelegramMediaFile,
          !file.isSticker, !file.isAnimatedSticker, !file.isVideoSticker,
          !file.isCustomEmoji, file.mimeType != "application/x-tgsticker" else { return nil }
    if file.isInstantVideo { return .videoMessage }
    if file.isVoice { return .voiceMessage }
    if file.isAnimated || file.mimeType == "image/gif" { return .gif }
    if file.isVideo { return .video }
    return .document
}
