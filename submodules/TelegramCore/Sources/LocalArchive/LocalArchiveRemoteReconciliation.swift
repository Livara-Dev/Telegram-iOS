// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox

/// Media-reference refreshes can replace an entire stored body. Bind capture to
/// the original request, because some refresh parsers default non-cloud replies
/// to the cloud namespace. Preserve the caller's existing replacement behavior.
func applyLocalArchiveRemoteMessage(transaction: Transaction, requestedIds: Set<MessageId>, message: StoreMessage) {
    guard case let .Id(id) = message.id else { return }
    let canCapture = id.namespace == Namespaces.Message.Cloud
        && requestedIds.contains(id)
        && requestedIds.allSatisfy { $0.namespace == Namespaces.Message.Cloud }
    transaction.updateMessage(id, update: { previous in
        if canCapture && previous.id == id {
            captureEditedLocalArchiveServerMessage(transaction: transaction, previous: previous, updated: message)
        }
        return .update(message)
    })
}
