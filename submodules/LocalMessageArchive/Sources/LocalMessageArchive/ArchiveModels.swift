// SPDX-License-Identifier: GPL-2.0-or-later
// Native archive storage, 2026-11-04. Application integration is experimental.
import Foundation

public protocol ArchiveKeyProvider {
    /// Called on the archive worker. Existing database keys must never be regenerated.
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data
}

public enum ArchiveError: Error, Equatable {
    case invalidKey
    case invalidInput
    case accountMismatch
    case unavailable(Int32)
    case unsupportedSchema(Int)
    case closed
    case protectionUnavailable
    case eventConflict
    case generationMismatch(expected: Int64, received: Int64)
}

public struct ArchiveMessageID: Equatable {
    public let accountId: Int64
    public let peerId: Int64
    public let namespace: Int32
    public let messageId: Int32
    public init(accountId: Int64, peerId: Int64, namespace: Int32, messageId: Int32) {
        self.accountId = accountId; self.peerId = peerId
        self.namespace = namespace; self.messageId = messageId
    }
}

public struct ArchiveContent: Equatable {
    public let text: String
    public let authorId: Int64?
    public let sentAt: Int64
    /// Opaque, versioned entity metadata supplied by the Telegram adapter; never session material.
    public let entities: Data
    public init(text: String, authorId: Int64? = nil, sentAt: Int64, entities: Data = Data()) {
        self.text = text; self.authorId = authorId; self.sentAt = sentAt; self.entities = entities
    }
    var logicalBytes: Int64 { Int64(text.utf8.count + entities.count + 64) }
}

public enum ArchiveEventKind: Int32 { case deleted = 1, edited = 2 }

public struct ArchiveEvent: Equatable {
    public let eventKey: String
    public let message: ArchiveMessageID
    public let kind: ArchiveEventKind
    public let observedAt: Int64
    public let serverEditAt: Int64?
    public let content: ArchiveContent
    public init(eventKey: String, message: ArchiveMessageID, kind: ArchiveEventKind,
                observedAt: Int64, serverEditAt: Int64? = nil, content: ArchiveContent) {
        self.eventKey = eventKey; self.message = message; self.kind = kind
        self.observedAt = observedAt; self.serverEditAt = serverEditAt; self.content = content
    }
    var logicalBytes: Int64 { content.logicalBytes + Int64(eventKey.utf8.count) }
}

public struct ArchiveCaptureContext {
    public var ordinaryCloudMessage: Bool
    public var secretChat: Bool
    public var expiring: Bool
    public var viewOnce: Bool
    public var botConversation: Bool
    public init(ordinaryCloudMessage: Bool = false, secretChat: Bool = false,
                expiring: Bool = false, viewOnce: Bool = false, botConversation: Bool = false) {
        self.ordinaryCloudMessage = ordinaryCloudMessage; self.secretChat = secretChat
        self.expiring = expiring; self.viewOnce = viewOnce; self.botConversation = botConversation
    }
}

public struct ArchiveCapturePolicy {
    public var enabled: Bool
    public var excludedPeers: Set<Int64>
    public var excludedSenders: Set<Int64>
    public var excludeBots: Bool
    public init(enabled: Bool = false, excludedPeers: Set<Int64> = [], excludedSenders: Set<Int64> = [], excludeBots: Bool = true) {
        self.enabled = enabled; self.excludedPeers = excludedPeers
        self.excludedSenders = excludedSenders; self.excludeBots = excludeBots
    }
    func allows(_ event: ArchiveEvent, context: ArchiveCaptureContext) -> Bool {
        enabled && context.ordinaryCloudMessage && !context.secretChat && !context.expiring && !context.viewOnce
        && !(excludeBots && context.botConversation) && !excludedPeers.contains(event.message.peerId)
        && !(event.content.authorId.map { excludedSenders.contains($0) } ?? false)
    }
}

public struct ArchiveRetentionPolicy {
    public let maxAgeSeconds: Int64
    /// Logical text/entity/event-key/record bytes, not SQLite/WAL disk allocation.
    public let maxPayloadBytes: Int64
    public init(maxAgeSeconds: Int64 = 30 * 86400, maxPayloadBytes: Int64 = 32 * 1024 * 1024) {
        self.maxAgeSeconds = maxAgeSeconds; self.maxPayloadBytes = maxPayloadBytes
    }
}

public struct ArchiveCapture {
    public let event: ArchiveEvent
    public let context: ArchiveCaptureContext
    public init(event: ArchiveEvent, context: ArchiveCaptureContext) { self.event = event; self.context = context }
}

public struct ArchivedRecord: Equatable {
    public let sequence: Int64
    public let event: ArchiveEvent
}

public struct ArchiveWriteResult: Equatable {
    public let inserted: Int
    public let duplicates: Int
    public let excluded: Int
    public let removedByRetention: Int
}

public struct ArchiveUsage: Equatable {
    public let records: Int64
    public let logicalPayloadBytes: Int64
}

public struct ArchivePurgeResult: Equatable {
    public let generation: Int64
    /// False for an acknowledged retry of the current generation; newer records remain intact.
    public let didPurge: Bool
}
