// Modified for AyuGram iOS on 2026-11-04: persist story receipt suppression
// generations and prevent suppressed or stale receipts from being queued.
import Foundation
import Postbox

public final class SynchronizeViewStoriesOperation: PostboxCoding {
    public let peerId: PeerId
    public let storyId: Int32
    public let suppressionGeneration: Int64
    
    public init(peerId: PeerId, storyId: Int32, suppressionGeneration: Int64 = 0) {
        self.peerId = peerId
        self.storyId = storyId
        self.suppressionGeneration = suppressionGeneration
    }
    
    public init(decoder: PostboxDecoder) {
        self.peerId = PeerId(decoder.decodeInt64ForKey("p", orElse: 0))
        self.storyId = decoder.decodeInt32ForKey("s", orElse: 0)
        self.suppressionGeneration = decoder.decodeInt64ForKey("g", orElse: 0)
    }
    
    public func encode(_ encoder: PostboxEncoder) {
        encoder.encodeInt64(self.peerId.toInt64(), forKey: "p")
        encoder.encodeInt32(self.storyId, forKey: "s")
        encoder.encodeInt64(self.suppressionGeneration, forKey: "g")
    }
}

func _internal_addSynchronizeViewStoriesOperation(peerId: PeerId, storyId: Int32, transaction: Transaction) {
    guard case let .available(settings) = localStoryPrivacySettings(transaction: transaction), !settings.suppressViewReceipts else { return }
    let tag: PeerOperationLogTag = OperationLogTags.SynchronizeViewStories
    
    var topOperation: (SynchronizeViewStoriesOperation, Int32)?
    transaction.operationLogEnumerateEntries(peerId: peerId, tag: tag, { entry in
        if let operation = entry.contents as? SynchronizeViewStoriesOperation {
            topOperation = (operation, entry.tagLocalIndex)
        }
        return false
    })
    var replace = false
    if let (topOperation, topLocalIndex) = topOperation {
        if topOperation.suppressionGeneration != settings.suppressionGeneration || topOperation.storyId < storyId {
            let _ = transaction.operationLogRemoveEntry(peerId: peerId, tag: tag, tagLocalIndex: topLocalIndex)
            replace = true
        }
    } else {
        replace = true
    }
    if replace {
        transaction.operationLogAddEntry(peerId: peerId, tag: tag, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: SynchronizeViewStoriesOperation(peerId: peerId, storyId: storyId, suppressionGeneration: settings.suppressionGeneration))
    }
}
