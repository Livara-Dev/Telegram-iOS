// SPDX-License-Identifier: GPL-2.0-or-later
import TelegramCore
import TelegramPresentationData

/// Only real Telegram rows may anchor remote history loading or read ranges.
/// A local row has an index for visual sorting, never a navigable server target.
public func chatHistoryCanonicalEntries(_ entries: [ChatHistoryEntry]) -> [ChatHistoryEntry] {
    return entries.filter { entry in
        switch entry {
        case .MessageEntry, .MessageGroupEntry: return true
        default: return false
        }
    }
}

/// Merge local rows without manufacturing a Telegram message. The caller proves
/// ordinary-main-chat eligibility and disables this projection during selection,
/// protected content, previews, topic lists and local-filter enforcement.
public func chatHistoryApplyingLocalArchive(_ entries: [ChatHistoryEntry], rows: [LocalArchiveDeletedHistoryItem],
                                           peerId: EnginePeer.Id, presentationData: ChatPresentationData,
                                           permitted: Bool, reverse: Bool = false) -> [ChatHistoryEntry] {
    guard permitted, peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudGroup
        || peerId.namespace == Namespaces.Peer.CloudChannel else { return entries }
    var canonicalIds = Set<EngineMessage.Id>()
    for entry in entries {
        switch entry {
        case let .MessageEntry(message, _, _, _, _, _): canonicalIds.insert(message.id)
        case let .MessageGroupEntry(_, messages, _):
            for message in messages { canonicalIds.insert(message.0.id) }
        default: break
        }
    }
    var addedIds = Set<Int32>()
    let boundedRows = rows.prefix(40)
    let moreAvailable = boundedRows.contains { $0.moreAvailable }
    let eligibleRows = boundedRows.filter { row in
        guard row.peerId == peerId, row.messageId > 0, row.sentAt >= 0, row.sequence > 0,
              row.content.text != nil, addedIds.insert(row.messageId).inserted,
              !canonicalIds.contains(EngineMessage.Id(peerId: peerId, namespace: Namespaces.Message.Cloud, id: row.messageId)) else { return false }
        return true
    }
    let localEntries = eligibleRows.enumerated().map { index, row -> ChatHistoryEntry in
        // Deduplicating a canonical id must not silently drop the page's
        // truncation notice when that id happened to carry the notice.
        return .LocalArchiveEntry(row.withMoreAvailable(moreAvailable && index == eligibleRows.count - 1), presentationData)
    }
    guard !localEntries.isEmpty else { return entries }
    return (entries + localEntries).sorted(by: reverse ? (>) : (<))
}
