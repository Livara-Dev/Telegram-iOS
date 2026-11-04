// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import LocalMessageArchive

/// A minimal local outbox item; never consumed by a network operation worker.
/// Its identity and generation are fixed in the same transaction as the message change.
final class LocalArchiveCaptureOperation: PostboxCoding {
    private let hasRequiredFields: Bool
    let version: Int32
    let eventKey: String
    let generation: Int64
    let messageId: MessageId
    let kindRawValue: Int32
    let text: String
    let entities: Data
    let authorId: Int64?
    let sentAt: Int64
    let observedAt: Int64
    let serverEditAt: Int64?
    let botConversation: Bool

    init(eventKey: String = UUID().uuidString, generation: Int64, messageId: MessageId,
         kindRawValue: Int32, text: String, entities: Data, authorId: Int64?, sentAt: Int64,
         observedAt: Int64, serverEditAt: Int64?, botConversation: Bool) {
        self.hasRequiredFields = true
        self.version = 1
        self.eventKey = eventKey
        self.generation = generation
        self.messageId = messageId
        self.kindRawValue = kindRawValue
        self.text = text
        self.entities = entities
        self.authorId = authorId
        self.sentAt = sentAt
        self.observedAt = observedAt
        self.serverEditAt = serverEditAt
        self.botConversation = botConversation
    }

    init(decoder: PostboxDecoder) {
        self.hasRequiredFields = decoder.decodeOptionalStringForKey("x") != nil && decoder.decodeOptionalBoolForKey("b") != nil
        self.version = decoder.decodeInt32ForKey("v", orElse: -1)
        self.eventKey = decoder.decodeStringForKey("k", orElse: "")
        self.generation = decoder.decodeInt64ForKey("g", orElse: -1)
        self.messageId = MessageId(peerId: PeerId(decoder.decodeInt64ForKey("p", orElse: 0)), namespace: decoder.decodeInt32ForKey("n", orElse: -1), id: decoder.decodeInt32ForKey("i", orElse: 0))
        self.kindRawValue = decoder.decodeInt32ForKey("t", orElse: -1)
        self.text = decoder.decodeStringForKey("x", orElse: "")
        self.entities = decoder.decodeDataForKey("e") ?? Data()
        self.authorId = decoder.decodeOptionalInt64ForKey("a")
        self.sentAt = decoder.decodeInt64ForKey("s", orElse: -1)
        self.observedAt = decoder.decodeInt64ForKey("o", orElse: -1)
        self.serverEditAt = decoder.decodeOptionalInt64ForKey("d")
        self.botConversation = decoder.decodeBoolForKey("b", orElse: true)
    }

    func encode(_ encoder: PostboxEncoder) {
        encoder.encodeInt32(self.version, forKey: "v")
        encoder.encodeString(self.eventKey, forKey: "k")
        encoder.encodeInt64(self.generation, forKey: "g")
        encoder.encodeInt64(self.messageId.peerId.toInt64(), forKey: "p")
        encoder.encodeInt32(self.messageId.namespace, forKey: "n")
        encoder.encodeInt32(self.messageId.id, forKey: "i")
        encoder.encodeInt32(self.kindRawValue, forKey: "t")
        encoder.encodeString(self.text, forKey: "x")
        encoder.encodeData(self.entities, forKey: "e")
        if let authorId = self.authorId { encoder.encodeInt64(authorId, forKey: "a") } else { encoder.encodeNil(forKey: "a") }
        encoder.encodeInt64(self.sentAt, forKey: "s")
        encoder.encodeInt64(self.observedAt, forKey: "o")
        if let serverEditAt = self.serverEditAt { encoder.encodeInt64(serverEditAt, forKey: "d") } else { encoder.encodeNil(forKey: "d") }
        encoder.encodeBool(self.botConversation, forKey: "b")
    }

    var encodedByteCount: Int {
        let encoder = PostboxEncoder()
        encoder.encodeRootObject(self)
        return encoder.makeData().count
    }

    func makeCapture(accountId: Int64) -> ArchiveCapture? {
        let peerNamespace = self.messageId.peerId.namespace
        guard self.hasRequiredFields, self.version == 1, self.generation >= 0, !self.eventKey.isEmpty, !self.eventKey.contains("\0"),
              self.eventKey.utf8.count <= 128, self.messageId.namespace == Namespaces.Message.Cloud,
              peerNamespace == Namespaces.Peer.CloudUser || peerNamespace == Namespaces.Peer.CloudGroup || peerNamespace == Namespaces.Peer.CloudChannel,
              self.messageId.id > 0, self.sentAt >= 0, self.observedAt >= 0,
              self.serverEditAt.map({ $0 >= 0 }) ?? true,
              self.text.utf8.count <= 256 * 1024, localArchiveEntityMetadataIsValid(self.entities),
              let kind = ArchiveEventKind(rawValue: self.kindRawValue) else {
            return nil
        }
        return ArchiveCapture(event: ArchiveEvent(
            eventKey: self.eventKey,
            message: ArchiveMessageID(accountId: accountId, peerId: self.messageId.peerId.toInt64(), namespace: self.messageId.namespace, messageId: self.messageId.id),
            kind: kind, observedAt: self.observedAt, serverEditAt: self.serverEditAt,
            content: ArchiveContent(text: self.text, authorId: self.authorId, sentAt: self.sentAt, entities: self.entities)
        ), context: ArchiveCaptureContext(ordinaryCloudMessage: true, botConversation: self.botConversation))
    }
}

struct LocalArchivePendingCapture {
    let index: Int32
    /// Unknown content types remain visible to the consumer and are never silently acked.
    let operation: LocalArchiveCaptureOperation?
}

func localArchivePendingCaptures(transaction: Transaction, accountPeerId: PeerId, limit: Int = 64) -> [LocalArchivePendingCapture] {
    let boundedLimit = min(64, max(1, limit))
    var result: [LocalArchivePendingCapture] = []
    transaction.operationLogEnumerateEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { entry in
        result.append(LocalArchivePendingCapture(index: entry.tagLocalIndex, operation: entry.contents as? LocalArchiveCaptureOperation))
        return result.count < boundedLimit
    }
    return result
}

@discardableResult
func acknowledgeLocalArchiveCapture(transaction: Transaction, accountPeerId: PeerId, index: Int32, eventKey: String) -> Bool {
    var matches = false
    transaction.operationLogUpdateEntry(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture, tagLocalIndex: index) { entry in
        matches = (entry?.contents as? LocalArchiveCaptureOperation)?.eventKey == eventKey
        return PeerOperationLogEntryUpdate(mergedIndex: .none, contents: .none)
    }
    guard matches else { return false }
    return transaction.operationLogRemoveEntry(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture, tagLocalIndex: index)
}
