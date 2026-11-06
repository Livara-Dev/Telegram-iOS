// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import LocalMessageArchive

struct LocalArchiveMediaMetadata: Codable, Equatable {
    enum Kind: String, Codable { case photo, video, voice, videoMessage, animation, document, unsupported }
    let kind: Kind
    let namespace: Int32?
    let id: Int64?
    let resourceKey: String?
    let mimeType: String?
    let byteSize: Int64?
    // Descriptive metadata only. A record is never evidence of retained bytes.
    let bytesPreserved: Bool
    var alternativeResourceKeys: [String]? = nil
}

private struct LocalArchiveRetentionMetadata: Codable {
    let secretChat: Bool
    let expiring: Bool
    let viewOnce: Bool

    var isValid: Bool { (self.secretChat || self.expiring) && (!self.viewOnce || self.expiring) }
}

private struct LocalArchiveEntities: Codable {
    let version: Int
    let entities: [MessageTextEntity]
    let media: [LocalArchiveMediaMetadata]?
    var retention: LocalArchiveRetentionMetadata? = nil
}

func localArchiveSafeResourceKey(_ key: String) -> Bool {
    guard key.utf8.count <= 1024 else { return false }
    if key.hasPrefix("telegram-cloud-") {
        return key.utf8.allSatisfy { (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95 }
    }
    // These are MediaBox identifiers, not paths. Keys/IVs/access hashes are never
    // serialized into archive metadata. Canonical decimal checks reject path tricks.
    func canonicalInteger(_ value: Substring) -> Bool {
        guard let number = Int64(value) else { return false }
        return String(number) == value
    }
    if key.hasPrefix("telegram-local-file-") {
        return canonicalInteger(key.dropFirst("telegram-local-file-".count))
    }
    if key.hasPrefix("secret-file-"), let separator = key.lastIndex(of: "-") {
        let body = key.dropFirst("secret-file-".count)
        guard separator >= body.startIndex, let dc = Int32(key[key.index(after: separator)...]), dc > 0 else { return false }
        return canonicalInteger(body[..<separator]) && String(dc) == key[key.index(after: separator)...]
    }
    return false
}

func localArchiveMessageIdentityIsSupported(_ id: MessageId) -> Bool {
    guard id.peerId.id._internalGetInt64Value() > 0 else { return false }
    if id.peerId.namespace == Namespaces.Peer.SecretChat {
        return id.id >= 0 && (id.namespace == Namespaces.Message.SecretIncoming || id.namespace == Namespaces.Message.Local)
    }
    return id.namespace == Namespaces.Message.Cloud && id.id > 0 &&
        (id.peerId.namespace == Namespaces.Peer.CloudUser || id.peerId.namespace == Namespaces.Peer.CloudGroup || id.peerId.namespace == Namespaces.Peer.CloudChannel)
}

private func localArchiveResourceKeyAllowed(_ key: String, includeSecretResources: Bool) -> Bool {
    return localArchiveSafeResourceKey(key) && (includeSecretResources || key.hasPrefix("telegram-cloud-"))
}

func localArchiveMediaMetadata(_ media: [Media], includeSecretResources: Bool = false) -> [LocalArchiveMediaMetadata] {
    return media.prefix(10).map { item in
        if let photo = item as? TelegramMediaImage {
            let representations = Array(photo.representations.prefix(32)).sorted { lhs, rhs in
                Int64(max(0, lhs.dimensions.width)) * Int64(max(0, lhs.dimensions.height)) > Int64(max(0, rhs.dimensions.width)) * Int64(max(0, rhs.dimensions.height))
            }
            let keys = representations.prefix(10).map { $0.resource.id.stringRepresentation }.filter { localArchiveResourceKeyAllowed($0, includeSecretResources: includeSecretResources) }
            var result = LocalArchiveMediaMetadata(kind: .photo, namespace: photo.imageId.namespace, id: photo.imageId.id,
                resourceKey: keys.first, mimeType: "image/jpeg", byteSize: nil, bytesPreserved: false)
            result.alternativeResourceKeys = Array(keys.dropFirst())
            return result
        }
        if let file = item as? TelegramMediaFile {
            let kind: LocalArchiveMediaMetadata.Kind
            if file.isInstantVideo { kind = .videoMessage }
            else if file.isVoice { kind = .voice }
            else if file.isAnimated { kind = .animation }
            else if file.isVideo { kind = .video }
            else { kind = .document }
            let resource = file.resource.id.stringRepresentation
            return LocalArchiveMediaMetadata(kind: kind, namespace: file.fileId.namespace, id: file.fileId.id,
                resourceKey: localArchiveResourceKeyAllowed(resource, includeSecretResources: includeSecretResources) ? resource : nil,
                mimeType: file.mimeType.utf8.count <= 256 && !file.mimeType.contains("\0") ? file.mimeType : nil,
                byteSize: file.size.flatMap { $0 >= 0 ? $0 : nil }, bytesPreserved: false)
        }
        return LocalArchiveMediaMetadata(kind: .unsupported, namespace: item.id?.namespace, id: item.id?.id,
            resourceKey: nil, mimeType: nil, byteSize: nil, bytesPreserved: false)
    }
}

func localArchiveDecodedMediaMetadata(_ data: Data) -> [LocalArchiveMediaMetadata]? {
    guard localArchiveEntityMetadataIsValid(data), let value = try? JSONDecoder().decode(LocalArchiveEntities.self, from: data) else { return nil }
    return value.media ?? []
}

func localArchiveEntityMetadataIsValid(_ data: Data) -> Bool {
    guard data.count <= 64 * 1024, let value = try? JSONDecoder().decode(LocalArchiveEntities.self, from: data) else { return false }
    if value.version == 1 { return value.media == nil && value.retention == nil }
    guard value.version == 2 || value.version == 3,
          let media = value.media, media.count <= 10 else { return false }
    if value.version == 2 {
        guard value.retention == nil else { return false }
    } else {
        guard let retention = value.retention, retention.isValid else { return false }
    }
    let includeSecretResources = value.version == 3 && value.retention?.secretChat == true
    return media.allSatisfy { item in
        !item.bytesPreserved &&
        (item.resourceKey.map { localArchiveResourceKeyAllowed($0, includeSecretResources: includeSecretResources) } ?? true) &&
        (item.mimeType?.utf8.count ?? 0) <= 256 && !(item.mimeType?.contains("\0") ?? false) &&
        (item.byteSize.map { $0 >= 0 } ?? true) && (item.alternativeResourceKeys?.count ?? 0) <= 9 &&
        (item.alternativeResourceKeys?.allSatisfy { localArchiveResourceKeyAllowed($0, includeSecretResources: includeSecretResources) } ?? true)
    }
}

/// Older capture adapters reject version-3 pending outbox records instead of
/// treating them as ordinary captures. Already committed archives remain opaque
/// library records; this is not a restriction on reading previously saved copies.
func localArchiveCaptureContext(_ data: Data) -> ArchiveCaptureContext? {
    guard localArchiveEntityMetadataIsValid(data),
          let value = try? JSONDecoder().decode(LocalArchiveEntities.self, from: data) else { return nil }
    if let retention = value.retention {
        return ArchiveCaptureContext(ordinaryCloudMessage: !retention.secretChat, secretChat: retention.secretChat,
            expiring: retention.expiring, viewOnce: retention.viewOnce)
    }
    return ArchiveCaptureContext(ordinaryCloudMessage: true)
}

private func textEntities(_ attributes: [MessageAttribute]) -> [MessageTextEntity] {
    return attributes.compactMap { $0 as? TextEntitiesMessageAttribute }.first?.entities ?? []
}

private func richText(_ attributes: [MessageAttribute]) -> RichTextMessageAttribute? {
    return attributes.first(where: { $0 is RichTextMessageAttribute }) as? RichTextMessageAttribute
}

private func containsExpiringAttribute(_ attributes: [MessageAttribute]) -> Bool {
    return attributes.contains { $0 is AutoremoveTimeoutMessageAttribute || $0 is AutoclearTimeoutMessageAttribute }
}

private enum LocalArchiveEligibleContent {
    case text(botConversation: Bool, entities: Data)
    case unsupportedRichText
}

private func eligibleMessage(transaction: Transaction, message: Message, settings: LocalArchiveSettings, includeMetadata: Bool = true) -> LocalArchiveEligibleContent? {
    let secretChat = message.id.peerId.namespace == Namespaces.Peer.SecretChat
    let expiring = containsExpiringAttribute(message.attributes)
    let viewOnce = message.attributes.contains { attribute in
        (attribute as? AutoclearTimeoutMessageAttribute)?.timeout == viewOnceTimeout ||
        (attribute as? AutoremoveTimeoutMessageAttribute)?.timeout == viewOnceTimeout
    }
    guard localArchiveMessageIdentityIsSupported(message.id),
          !secretChat || settings.includeSecretChats,
          !expiring || settings.includeExpiringMessages,
          !settings.excludedPeerIds.contains(message.id.peerId.toInt64()),
          settings.excludedSenderIds.isEmpty || message.author != nil,
          !(message.author.map { settings.excludedSenderIds.contains($0.id.toInt64()) } ?? false),
          !message.flags.contains(.CopyProtected),
          !message.media.contains(where: { $0 is TelegramMediaExpiredContent || $0 is TelegramMediaAction }),
          let conversationPeer = transaction.getPeer(message.id.peerId), !conversationPeer.isCopyProtectionEnabled else {
        return nil
    }
    let botConversation = (conversationPeer as? TelegramUser)?.botInfo != nil
    guard !(settings.excludeBots && botConversation) else { return nil }
    // Rich messages carry content in an InstantPage and commonly have text == "".
    // The text-only archive cannot represent them faithfully. Classify this only
    // after exclusions so deliberate privacy exclusions are not reported as misses.
    if richText(message.attributes) != nil {
        return .unsupportedRichText
    }
    if !includeMetadata {
        // A full handoff still counts eligible missed captures, but must not
        // serialize every remaining body/attachment in a large history removal.
        return .text(botConversation: botConversation, entities: Data())
    }
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let extended = secretChat || expiring
    let metadata = LocalArchiveEntities(version: extended ? 3 : message.media.isEmpty ? 1 : 2,
        entities: textEntities(message.attributes),
        media: extended || !message.media.isEmpty ? localArchiveMediaMetadata(message.media, includeSecretResources: secretChat) : nil,
        retention: extended ? LocalArchiveRetentionMetadata(secretChat: secretChat, expiring: expiring, viewOnce: viewOnce) : nil)
    guard let entities = try? encoder.encode(metadata), localArchiveEntityMetadataIsValid(entities) else {
        return nil
    }
    return .text(botConversation: botConversation, entities: entities)
}

private struct LocalArchiveOutboxBudget {
    // Bounds protect update processing if the device is locked, full, or the sink fails.
    // They are handoff bounds, independent of the user's archive retention policy.
    static let maximumCount = 256
    static let maximumBytes = 8 * 1024 * 1024
    var count = 0
    var bytes = 0
    var hasCapacity: Bool { self.count < Self.maximumCount && self.bytes < Self.maximumBytes }

    init(transaction: Transaction, accountPeerId: PeerId) {
        transaction.operationLogEnumerateEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { entry in
            self.count += 1
            guard let operation = entry.contents as? LocalArchiveCaptureOperation, operation.version == 1 else {
                // Unknown records block appending; preserve them for a future compatible reader.
                self.bytes = Self.maximumBytes
                return false
            }
            self.bytes += operation.encodedByteCount
            return self.count < Self.maximumCount && self.bytes < Self.maximumBytes
        }
    }

    mutating func insert(_ operation: LocalArchiveCaptureOperation, transaction: Transaction, accountPeerId: PeerId) -> Bool {
        guard operation.text.utf8.count <= 256 * 1024, operation.entities.count <= 64 * 1024,
              operation.sentAt >= 0, operation.observedAt >= 0,
              self.count < Self.maximumCount else { return false }
        let nextIndex = transaction.operationLogGetNextEntryLocalIndex(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture)
        guard nextIndex >= 0, nextIndex < Int32.max else { return false }
        let bytes = operation.encodedByteCount
        guard bytes <= Self.maximumBytes - self.bytes else { return false }
        transaction.operationLogAddEntry(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: operation)
        self.count += 1
        self.bytes += bytes
        return true
    }
}

private func recordCaptureGap(_ settings: inout LocalArchiveSettings) {
    if settings.droppedCaptures < Int64.max {
        settings.droppedCaptures += 1
    }
}

/// One transaction-scoped collector for explicit and history-range deletions.
/// Reuses the outbox budget without rescanning it for every removed message.
final class LocalArchiveDeletionCapture {
    private let transaction: Transaction
    private let accountPeerId: PeerId
    private let observedAt: Int64
    private var settings: LocalArchiveSettings
    private var budget: LocalArchiveOutboxBudget
    private let previousDropped: Int64
    private var deferredResources = Set<MediaResourceId>()
    private var finished = false

    init?(transaction: Transaction, accountPeerId: PeerId, observedAt: Int64 = Int64(Date().timeIntervalSince1970)) {
        let settings = localArchiveSettings(transaction: transaction)
        guard settings.isSupported, settings.enabled, !settings.pendingPurge else { return nil }
        self.transaction = transaction
        self.accountPeerId = accountPeerId
        self.observedAt = observedAt
        self.settings = settings
        self.previousDropped = settings.droppedCaptures
        self.budget = LocalArchiveOutboxBudget(transaction: transaction, accountPeerId: accountPeerId)
    }

    func append(_ message: Message) {
        guard !self.finished,
              let eligible = eligibleMessage(transaction: self.transaction, message: message, settings: self.settings, includeMetadata: self.budget.hasCapacity || message.id.peerId.namespace == Namespaces.Peer.SecretChat || containsExpiringAttribute(message.attributes)) else { return }
        guard case let .text(botConversation, entities) = eligible else {
            recordCaptureGap(&self.settings)
            return
        }
        // Explicit deletion, secret-chat replay and the expiry worker can meet in
        // the same transaction. Preserve one identical pending deletion, not two.
        if message.id.peerId.namespace == Namespaces.Peer.SecretChat || containsExpiringAttribute(message.attributes) {
            var duplicate = false
            var inspected = 0
            self.transaction.operationLogEnumerateEntries(peerId: self.accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { entry in
                inspected += 1
                if let operation = entry.contents as? LocalArchiveCaptureOperation,
                   operation.version == 1, operation.generation == self.settings.generation,
                   operation.messageId == message.id, operation.kindRawValue == ArchiveEventKind.deleted.rawValue,
                   operation.text == message.text, operation.entities == entities {
                    duplicate = true
                    return false
                }
                return inspected < 256
            }
            if duplicate {
                for item in localArchiveDecodedMediaMetadata(entities) ?? [] {
                    for key in [item.resourceKey].compactMap({ $0 }) + (item.alternativeResourceKeys ?? []) {
                        self.deferredResources.insert(MediaResourceId(key))
                    }
                }
                return
            }
        }
        guard self.budget.hasCapacity else {
            recordCaptureGap(&self.settings)
            return
        }
        let operation = LocalArchiveCaptureOperation(
            generation: self.settings.generation, messageId: message.id, kindRawValue: ArchiveEventKind.deleted.rawValue,
            text: message.text, entities: entities, authorId: message.author?.id.toInt64(),
            sentAt: Int64(message.timestamp), observedAt: self.observedAt, serverEditAt: message.attributes.compactMap { ($0 as? EditedMessageAttribute).map { Int64($0.date) } }.first,
            botConversation: botConversation
        )
        if !self.budget.insert(operation, transaction: self.transaction, accountPeerId: self.accountPeerId) {
            recordCaptureGap(&self.settings)
        } else {
            for item in localArchiveDecodedMediaMetadata(entities) ?? [] {
                for key in [item.resourceKey].compactMap({ $0 }) + (item.alternativeResourceKeys ?? []) {
                    self.deferredResources.insert(MediaResourceId(key))
                }
            }
        }
    }

    /// Finish before leaving the same Postbox transaction. The caller can defer
    /// eager cache unlink only for resources belonging to successful captures.
    func finish() -> Set<MediaResourceId> {
        if !self.finished {
            self.finished = true
            if self.settings.droppedCaptures != self.previousDropped {
                setLocalArchiveSettings(transaction: self.transaction, settings: self.settings)
            }
        }
        return self.deferredResources
    }
}

/// Must run in the same transaction before canonical deletion. Deferred cache
/// resources are still subject to normal cleanup, not a guarantee of saved bytes.
@discardableResult
func captureDeletedLocalArchiveMessages(transaction: Transaction, accountPeerId: PeerId, ids: [MessageId], observedAt: Int64 = Int64(Date().timeIntervalSince1970)) -> Set<MediaResourceId> {
    guard !ids.isEmpty, let capture = LocalArchiveDeletionCapture(transaction: transaction, accountPeerId: accountPeerId, observedAt: observedAt) else { return [] }
    var seen = Set<MessageId>()
    for id in ids {
        if seen.insert(id).inserted, let message = transaction.getMessage(id) { capture.append(message) }
    }
    return capture.finish()
}

/// Must be called from the updateMessage closure before replacing the old value.
/// Ordinary reaction, view-count, pin-state and edit-date attributes alone are not
/// versions. A rich message's primary page uses its existing Equatable semantics;
/// its metadata changes can count as misses, but its locally cached full page does
/// not. The seed merge preserves that cache when the server repeats the primary page.
func captureEditedLocalArchiveMessage(transaction: Transaction, accountPeerId: PeerId, previous: Message, updated: StoreMessage, observedAt: Int64 = Int64(Date().timeIntervalSince1970)) {
    guard let capture = LocalArchiveEditCapture(transaction: transaction, accountPeerId: accountPeerId, observedAt: observedAt) else { return }
    capture.append(previous: previous, updated: updated)
    capture.finish()
}

/// Reuses one lazily initialized budget for a server-upsert batch. Comparing the
/// actual sequential previous/replacement bodies avoids rescanning the outbox
/// for unchanged loads and avoids re-encoding its entire backlog for every edit.
final class LocalArchiveEditCapture {
    private let transaction: Transaction
    private let accountPeerId: PeerId
    private let observedAt: Int64
    private var settings: LocalArchiveSettings
    private var budget: LocalArchiveOutboxBudget?
    private let previousDropped: Int64
    private var finished = false

    init?(transaction: Transaction, accountPeerId: PeerId, observedAt: Int64 = Int64(Date().timeIntervalSince1970)) {
        let settings = localArchiveSettings(transaction: transaction)
        guard settings.isSupported, settings.enabled, !settings.pendingPurge else { return nil }
        self.transaction = transaction
        self.accountPeerId = accountPeerId
        self.observedAt = observedAt
        self.settings = settings
        self.previousDropped = settings.droppedCaptures
    }

    func append(previous: Message, updated: StoreMessage) {
        // A server-fetched replacement may be the first expiry observation on
        // this client. Capture the available old body as a deletion, never as
        // an edited expired placeholder. Canonical replacement stays unchanged.
        let expiryReplacement = updated.media.contains(where: { $0 is TelegramMediaExpiredContent })
        guard !self.finished,
              !expiryReplacement || previous.id.peerId.namespace == Namespaces.Peer.SecretChat || containsExpiringAttribute(previous.attributes),
              previous.text != updated.text || textEntities(previous.attributes) != textEntities(updated.attributes) || richText(previous.attributes)?.instantPage != richText(updated.attributes)?.instantPage || localArchiveMediaMetadata(previous.media, includeSecretResources: previous.id.peerId.namespace == Namespaces.Peer.SecretChat) != localArchiveMediaMetadata(updated.media, includeSecretResources: previous.id.peerId.namespace == Namespaces.Peer.SecretChat),
              !updated.flags.contains(.CopyProtected), !containsExpiringAttribute(updated.attributes) || self.settings.includeExpiringMessages,
              !updated.media.contains(where: { $0 is TelegramMediaAction }),
              let eligible = eligibleMessage(transaction: self.transaction, message: previous, settings: self.settings, includeMetadata: self.budget?.hasCapacity ?? true) else { return }
        guard case let .text(botConversation, entities) = eligible else {
            recordCaptureGap(&self.settings)
            return
        }
        if self.budget == nil {
            self.budget = LocalArchiveOutboxBudget(transaction: self.transaction, accountPeerId: self.accountPeerId)
        }
        guard self.budget?.hasCapacity == true else {
            recordCaptureGap(&self.settings)
            return
        }
        let operation = LocalArchiveCaptureOperation(
            generation: self.settings.generation, messageId: previous.id,
            kindRawValue: expiryReplacement ? ArchiveEventKind.deleted.rawValue : ArchiveEventKind.edited.rawValue,
            text: previous.text, entities: entities, authorId: previous.author?.id.toInt64(),
            sentAt: Int64(previous.timestamp), observedAt: self.observedAt,
            serverEditAt: previous.attributes.compactMap { ($0 as? EditedMessageAttribute).map { Int64($0.date) } }.first,
            botConversation: botConversation
        )
        if self.budget?.insert(operation, transaction: self.transaction, accountPeerId: self.accountPeerId) != true {
            recordCaptureGap(&self.settings)
        }
    }

    /// Flush the bounded missed-attempt counter before leaving the transaction.
    func finish() {
        guard !self.finished else { return }
        self.finished = true
        if self.previousDropped != self.settings.droppedCaptures {
            setLocalArchiveSettings(transaction: self.transaction, settings: self.settings)
        }
    }
}

/// Additional capture at common destructive boundaries. Ordinary cloud paths
/// keep their existing observers. This does not alter reads, timers or encryption.
@discardableResult
func captureExtendedLocalArchiveBeforeRemoval(transaction: Transaction, ids: [MessageId]) -> Set<MediaResourceId> {
    let settings = localArchiveSettings(transaction: transaction)
    guard settings.isSupported, settings.enabled, !settings.pendingPurge,
          settings.includeSecretChats || settings.includeExpiringMessages,
          let state = transaction.getState() as? AuthorizedAccountState else { return [] }
    let extendedIds = ids.filter { id in
        guard let message = transaction.getMessage(id) else { return false }
        return message.id.peerId.namespace == Namespaces.Peer.SecretChat || containsExpiringAttribute(message.attributes)
    }
    return captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: state.peerId, ids: extendedIds)
}
