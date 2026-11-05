// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

/// Explicit opt-in for server-fetched ordinary message upserts. The default-off
/// path forwards the original batch without any message scan or observer.
/// Postbox calls the observer only where an existing body will be replaced.
@discardableResult
func addMessagesWithLocalArchive(transaction: Transaction, messages: [StoreMessage], location: AddMessagesLocation) -> [Int64: MessageId] {
    let settings = localArchiveSettings(transaction: transaction)
    guard settings.isSupported, settings.enabled, !settings.pendingPurge,
          let accountState = transaction.getState() as? AuthorizedAccountState else {
        return transaction.addMessages(messages, location: location)
    }
    guard let capture = LocalArchiveEditCapture(transaction: transaction, accountPeerId: accountState.peerId) else {
        return transaction.addMessages(messages, location: location)
    }
    let result = transaction.addMessages(messages, location: location, beforeExistingUpdate: { previous, updated in
        capture.append(previous: previous, updated: updated)
    })
    capture.finish()
    return result
}

/// For explicit server-update closures that already compute their canonical
/// merge. Capture the previous body without changing the caller's replacement.
func captureEditedLocalArchiveServerMessage(transaction: Transaction, previous: Message, updated: StoreMessage) {
    let settings = localArchiveSettings(transaction: transaction)
    guard settings.isSupported, settings.enabled, !settings.pendingPurge,
          let accountState = transaction.getState() as? AuthorizedAccountState else { return }
    captureEditedLocalArchiveMessage(transaction: transaction, accountPeerId: accountState.peerId, previous: previous, updated: updated)
}

/// A cached full-channel response can advance the same minimum available ID as
/// a pushed update. Preserve eligible bodies before its existing range removal.
func applyLocalArchiveCachedMinAvailableMessage(transaction: Transaction, mediaBox: MediaBox, accountPeerId: PeerId, peerId: PeerId, minAvailableMessageId: MessageId) {
    let capture = LocalArchiveDeletionCapture(transaction: transaction, accountPeerId: accountPeerId)
    let beforeRemoving: ((Message) -> Void)? = capture.map { capture in { message in capture.append(message) } }
    var resources: [MediaResourceId] = []
    transaction.deleteMessagesInRange(peerId: peerId, namespace: minAvailableMessageId.namespace, minId: 1, maxId: minAvailableMessageId.id, beforeRemoving: beforeRemoving, forEachMedia: { media in
        addMessageMediaResourceIdsToRemove(media: media, resourceIds: &resources)
    })
    let deferred = capture?.finish() ?? []
    resources.removeAll(where: { deferred.contains($0) })
    if !resources.isEmpty {
        let _ = mediaBox.removeCachedResources(Array(Set(resources))).start()
    }
}

/// Validation may remove a cached message after a lookup failure as well as a
/// server absence. This records the observed local removal, not proof that a
/// remote user deleted the message. The upstream decision is kept unchanged.
func applyLocalArchiveValidatedDeletion(transaction: Transaction, mediaBox: MediaBox, accountPeerId: PeerId, ids: [MessageId]) {
    captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: ids)
    _internal_deleteMessages(transaction: transaction, mediaBox: mediaBox, ids: ids)
}

/// Preserve the validation path's existing timestamp decision and canonical
/// merge exactly. Metadata-only validation does not invent an edit snapshot.
func applyLocalArchiveValidatedMessage(transaction: Transaction, accountPeerId: PeerId, id: MessageId, message: StoreMessage, timestampChanged: Bool, channelPts: Int32?) {
    transaction.updateMessage(id, update: { currentMessage in
        if timestampChanged {
            var localTags = message.localTags
            if currentMessage.localTags.contains(.OutgoingLiveLocation) { localTags.insert(.OutgoingLiveLocation) }
            let updated = message.withUpdatedLocalTags(localTags)
            captureEditedLocalArchiveMessage(transaction: transaction, accountPeerId: accountPeerId, previous: currentMessage, updated: updated)
            return .update(updated)
        } else {
            let forward = currentMessage.forwardInfo.map { StoreMessageForwardInfo(authorId: $0.author?.id, sourceId: $0.source?.id, sourceMessageId: $0.sourceMessageId, date: $0.date, authorSignature: $0.authorSignature, psaType: $0.psaType, flags: $0.flags) }
            var attributes = currentMessage.attributes
            if let channelPts {
                for index in attributes.indices.reversed() where attributes[index] is ChannelMessageStateVersionAttribute { attributes.remove(at: index) }
                attributes.append(ChannelMessageStateVersionAttribute(pts: channelPts))
            }
            return .update(StoreMessage(id: message.id, customStableId: nil, globallyUniqueId: currentMessage.globallyUniqueId, groupingKey: currentMessage.groupingKey, threadId: currentMessage.threadId, timestamp: currentMessage.timestamp, flags: StoreMessageFlags(currentMessage.flags), tags: currentMessage.tags, globalTags: currentMessage.globalTags, localTags: currentMessage.localTags, forwardInfo: forward, authorId: currentMessage.author?.id, text: currentMessage.text, attributes: attributes, media: currentMessage.media))
        }
    })
}
