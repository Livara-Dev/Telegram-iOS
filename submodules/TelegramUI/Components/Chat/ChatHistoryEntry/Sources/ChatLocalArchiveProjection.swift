// SPDX-License-Identifier: GPL-2.0-or-later
import TelegramCore
import TelegramPresentationData

public func chatHistoryCanonicalEntries(_ entries: [ChatHistoryEntry]) -> [ChatHistoryEntry] {
    return entries.filter { entry in
        switch entry {
        case .MessageEntry, .MessageGroupEntry: return true
        default: return false
        }
    }
}

/// Retained rows are local-only. They cannot be replied to, forwarded or marked
/// read on the server. Expired placeholders may be replaced; live messages may not.
public func chatHistoryApplyingLocalArchive(_ entries: [ChatHistoryEntry], rows: [LocalArchiveDeletedHistoryItem],
                                           peerId: EnginePeer.Id, presentationData: ChatPresentationData,
                                           permitted: Bool, reverse: Bool = false) -> [ChatHistoryEntry] {
    guard permitted, peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudGroup
        || peerId.namespace == Namespaces.Peer.CloudChannel || peerId.namespace == Namespaces.Peer.SecretChat else { return entries }
    var canonicalIds = Set<EngineMessage.Id>()
    var expiredIds = Set<EngineMessage.Id>()
    for entry in entries {
        switch entry {
        case let .MessageEntry(message, _, _, _, _, _):
            canonicalIds.insert(message.id)
            if !message.media.isEmpty && message.media.allSatisfy({ $0 is TelegramMediaExpiredContent }) {
                expiredIds.insert(message.id)
            }
        case let .MessageGroupEntry(_, messages, _):
            for message in messages { canonicalIds.insert(message.0.id) }
        default: break
        }
    }
    var addedIds = Set<EngineMessage.Id>()
    let boundedRows = rows.prefix(40)
    let moreAvailable = boundedRows.contains { $0.moreAvailable }
    let eligibleRows = boundedRows.filter { row in
        let id = EngineMessage.Id(peerId: row.peerId, namespace: row.namespace, id: row.messageId)
        guard row.peerId == peerId, localArchiveSupportsRetainedMessageId(id), row.sentAt >= 0, row.sequence > 0,
              row.content.text != nil, !addedIds.contains(id),
              !canonicalIds.contains(id) || expiredIds.contains(id) else { return false }
        addedIds.insert(id)
        return true
    }
    guard !eligibleRows.isEmpty else { return entries }
    let localEntries = eligibleRows.enumerated().map { index, row -> ChatHistoryEntry in
        return .LocalArchiveEntry(row.withMoreAvailable(moreAvailable && index == eligibleRows.count - 1), presentationData)
    }
    let retained = entries.filter { entry in
        if case let .MessageEntry(message, _, _, _, _, _) = entry {
            return !(expiredIds.contains(message.id) && addedIds.contains(message.id))
        }
        return true
    }
    return (retained + localEntries).sorted(by: reverse ? (>) : (<))
}
