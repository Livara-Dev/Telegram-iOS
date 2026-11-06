// Modified for AyuGram iOS.
// Modification dates recorded in Git history: 2026-11-06.
// See this file's history for the corresponding changes.
import Foundation
import Postbox

enum InternalAccountState {
    static func addMessages(transaction: Transaction, messages: [StoreMessage], location: AddMessagesLocation) -> [Int64 : MessageId] {
        return addMessagesWithLocalArchive(transaction: transaction, messages: messages, location: location)
    }
    
    static func deleteMessages(transaction: Transaction, ids: [MessageId], forEachMedia: ((Media) -> Void)?) {
        transaction.deleteMessages(ids, forEachMedia: forEachMedia)
    }
    
    static func invalidateChannelState(peerId: PeerId) {
        
    }
}
