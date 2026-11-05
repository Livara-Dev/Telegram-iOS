// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import TelegramCore

/// Archive routes address ordinary cloud conversations, never a Secret Chat's
/// displayed user profile. A route remains separate from Telegram search results.
public func localArchiveSupportsPeer(_ id: EnginePeer.Id) -> Bool {
    return id.namespace == Namespaces.Peer.CloudUser || id.namespace == Namespaces.Peer.CloudGroup || id.namespace == Namespaces.Peer.CloudChannel
}

func localArchiveBoundedSearchQuery(_ text: String) -> String {
    var result = ""
    var byteCount = 0
    // Iterate scalars rather than grapheme clusters: one adversarial cluster can
    // contain an unbounded number of combining marks.
    for scalar in text.unicodeScalars {
        let count = scalar.value <= 0x7f ? 1 : (scalar.value <= 0x7ff ? 2 : (scalar.value <= 0xffff ? 3 : 4))
        guard byteCount + count <= 512 else { break }
        result.unicodeScalars.append(scalar)
        byteCount += count
    }
    return result
}

public struct LocalArchiveSearchRequest: Equatable {
    public let peerId: EnginePeer.Id
    public let query: String

    public init?(peerId: EnginePeer.Id, query: String) {
        guard localArchiveSupportsPeer(peerId) else { return nil }
        self.peerId = peerId
        self.query = localArchiveBoundedSearchQuery(query)
    }
}

public func localGhostActiveProtectionLabels(_ settings: LocalGhostSettings) -> [String] {
    guard settings.enabled else { return [] }
    var result: [String] = []
    if settings.suppressReadReceipts { result.append("ordinary read receipts") }
    if settings.suppressOnlineStatus { result.append("online updates") }
    if settings.suppressOnlineStatus && settings.automaticOffline { result.append("offline retry") }
    if settings.suppressTyping { result.append("typing") }
    if settings.suppressStickerSelection { result.append("sticker selection") }
    if settings.suppressRecording { result.append("recording") }
    if settings.suppressUploads { result.append("uploads") }
    if settings.suppressGamePlay { result.append("gameplay") }
    return result
}

public func localGhostMenuSummary(_ state: LocalGhostSettingsState, storyState: LocalStoryPrivacySettingsState) -> String {
    let master: String
    switch state {
    case let .available(settings):
        let labels = localGhostActiveProtectionLabels(settings)
        master = !settings.enabled ? "Ghost paused" : (labels.isEmpty ? "On · no master controls selected" : labels.joined(separator: ", "))
    case .unsupported:
        master = "Ghost settings unavailable"
    }
    let story: String
    switch storyState {
    case let .available(settings):
        story = settings.suppressViewReceipts ? "Story receipts suppressed separately" : "Story receipts unchanged"
    case .unsupported:
        story = "Story settings unavailable"
    }
    return master + " · " + story
}
