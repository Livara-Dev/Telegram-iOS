// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import LocalMessageArchive

private struct LocalArchiveEntities: Codable {
    let version: Int
    let entities: [MessageTextEntity]
}

func localArchiveEntityMetadataIsValid(_ data: Data) -> Bool {
    guard data.count <= 64 * 1024, let value = try? JSONDecoder().decode(LocalArchiveEntities.self, from: data) else { return false }
    return value.version == 1
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

private func eligibleMessage(transaction: Transaction, message: Message, settings: LocalArchiveSettings) -> LocalArchiveEligibleContent? {
    let peerNamespace = message.id.peerId.namespace
    guard message.id.namespace == Namespaces.Message.Cloud, message.id.id > 0,
          peerNamespace == Namespaces.Peer.CloudUser || peerNamespace == Namespaces.Peer.CloudGroup || peerNamespace == Namespaces.Peer.CloudChannel,
          !settings.excludedPeerIds.contains(message.id.peerId.toInt64()),
          settings.excludedSenderIds.isEmpty || message.author != nil,
          !(message.author.map { settings.excludedSenderIds.contains($0.id.toInt64()) } ?? false),
          !message.flags.contains(.CopyProtected), !containsExpiringAttribute(message.attributes),
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
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let entities = try? encoder.encode(LocalArchiveEntities(version: 1, entities: textEntities(message.attributes))) else {
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

/// Must be called inside the same transaction, before the canonical deletion.
/// Only already-received eligible content is copied; media files are not retained.
func captureDeletedLocalArchiveMessages(transaction: Transaction, accountPeerId: PeerId, ids: [MessageId], observedAt: Int64 = Int64(Date().timeIntervalSince1970)) {
    var settings = localArchiveSettings(transaction: transaction)
    guard settings.enabled, !settings.pendingPurge, settings.generation >= 0, !ids.isEmpty else { return }
    var budget = LocalArchiveOutboxBudget(transaction: transaction, accountPeerId: accountPeerId)
    let previousDropped = settings.droppedCaptures
    var seen = Set<MessageId>()
    for id in ids {
        guard seen.insert(id).inserted, let message = transaction.getMessage(id),
              let eligible = eligibleMessage(transaction: transaction, message: message, settings: settings) else { continue }
        guard case let .text(botConversation, entities) = eligible else {
            recordCaptureGap(&settings)
            continue
        }
        let operation = LocalArchiveCaptureOperation(
            generation: settings.generation, messageId: message.id, kindRawValue: ArchiveEventKind.deleted.rawValue,
            text: message.text, entities: entities, authorId: message.author?.id.toInt64(),
            sentAt: Int64(message.timestamp), observedAt: observedAt, serverEditAt: message.attributes.compactMap { ($0 as? EditedMessageAttribute).map { Int64($0.date) } }.first,
            botConversation: botConversation
        )
        if !budget.insert(operation, transaction: transaction, accountPeerId: accountPeerId) {
            recordCaptureGap(&settings)
        }
    }
    if settings.droppedCaptures != previousDropped {
        setLocalArchiveSettings(transaction: transaction, settings: settings)
    }
}

/// Must be called from the updateMessage closure before replacing the old value.
/// Ordinary reaction, view-count, pin-state and edit-date attributes alone are not
/// versions. A rich message's primary page uses its existing Equatable semantics;
/// its metadata changes can count as misses, but its locally cached full page does
/// not. The seed merge preserves that cache when the server repeats the primary page.
func captureEditedLocalArchiveMessage(transaction: Transaction, accountPeerId: PeerId, previous: Message, updated: StoreMessage, observedAt: Int64 = Int64(Date().timeIntervalSince1970)) {
    var settings = localArchiveSettings(transaction: transaction)
    guard settings.enabled, !settings.pendingPurge, settings.generation >= 0,
          previous.text != updated.text || textEntities(previous.attributes) != textEntities(updated.attributes) || richText(previous.attributes)?.instantPage != richText(updated.attributes)?.instantPage,
          !updated.flags.contains(.CopyProtected), !containsExpiringAttribute(updated.attributes),
          !updated.media.contains(where: { $0 is TelegramMediaExpiredContent || $0 is TelegramMediaAction }),
          let eligible = eligibleMessage(transaction: transaction, message: previous, settings: settings) else { return }
    guard case let .text(botConversation, entities) = eligible else {
        recordCaptureGap(&settings)
        setLocalArchiveSettings(transaction: transaction, settings: settings)
        return
    }
    var budget = LocalArchiveOutboxBudget(transaction: transaction, accountPeerId: accountPeerId)
    let operation = LocalArchiveCaptureOperation(
        generation: settings.generation, messageId: previous.id, kindRawValue: ArchiveEventKind.edited.rawValue,
        text: previous.text, entities: entities, authorId: previous.author?.id.toInt64(),
        sentAt: Int64(previous.timestamp), observedAt: observedAt,
        serverEditAt: previous.attributes.compactMap { ($0 as? EditedMessageAttribute).map { Int64($0.date) } }.first,
        botConversation: botConversation
    )
    if !budget.insert(operation, transaction: transaction, accountPeerId: accountPeerId) {
        recordCaptureGap(&settings)
        setLocalArchiveSettings(transaction: transaction, settings: settings)
    }
}
