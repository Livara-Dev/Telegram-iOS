// Modified for AyuGram iOS on 2026-11-04: account-local story receipt policy,
// durable suppression generations, and cancellation/operation identity guards.
import Foundation
import Postbox
import SwiftSignalKit

private final class ManagedSynchronizeViewStoriesOperationsHelper {
    private struct Key: Hashable {
        let peerId: PeerId
        let localIndex: Int32
    }

    private struct Identity: Equatable {
        let mergedIndex: Int32
        let storyId: Int32
        let generation: Int64
    }

    private var operations: [Key: (Identity, Disposable)] = [:]
    private var stopped = false

    func update(_ entries: [PeerMergedOperationLogEntry]) -> (disposeOperations: [Disposable], beginOperations: [(PeerMergedOperationLogEntry, MetaDisposable)]) {
        guard !self.stopped else { return ([], []) }
        var disposeOperations: [Disposable] = []
        var beginOperations: [(PeerMergedOperationLogEntry, MetaDisposable)] = []
        var validKeys = Set<Key>()

        for entry in entries {
            guard let operation = entry.contents as? SynchronizeViewStoriesOperation else { continue }
            let key = Key(peerId: entry.peerId, localIndex: entry.tagLocalIndex)
            let identity = Identity(mergedIndex: entry.mergedIndex, storyId: operation.storyId, generation: operation.suppressionGeneration)
            validKeys.insert(key)
            if let current = self.operations[key] {
                if current.0 == identity { continue }
                disposeOperations.append(current.1)
            }
            let disposable = MetaDisposable()
            self.operations[key] = (identity, disposable)
            beginOperations.append((entry, disposable))
        }

        for key in Array(self.operations.keys) where !validKeys.contains(key) {
            if let current = self.operations.removeValue(forKey: key) {
                disposeOperations.append(current.1)
            }
        }
        return (disposeOperations, beginOperations)
    }

    func reset() -> [Disposable] {
        self.stopped = true
        let disposables = self.operations.values.map { $0.1 }
        self.operations.removeAll()
        return disposables
    }
}

private func isCurrentStoryViewOperation(_ entry: PeerOperationLogEntry, expected: PeerMergedOperationLogEntry) -> Bool {
    guard entry.mergedIndex == expected.mergedIndex,
          let current = entry.contents as? SynchronizeViewStoriesOperation,
          let original = expected.contents as? SynchronizeViewStoriesOperation else { return false }
    return current.peerId == original.peerId && current.storyId == original.storyId
        && current.suppressionGeneration == original.suppressionGeneration
}

/// Resolve from the live operation and preferences in the same transaction.
/// Suppressed or old-generation entries are retired here, never parked for a
/// later disable. A crash before retirement still cannot authorize the old
/// generation after restart.
private func processStoryViewOperation(postbox: Postbox, expectedEntry: PeerMergedOperationLogEntry, send: @escaping LocalStoryViewReceiptSender, queue: Queue) -> Signal<Void, NoError> {
    return postbox.transaction { transaction -> LocalStoryViewReceiptRequest? in
        var current: PeerMergedOperationLogEntry?
        transaction.operationLogUpdateEntry(peerId: expectedEntry.peerId, tag: OperationLogTags.SynchronizeViewStories, tagLocalIndex: expectedEntry.tagLocalIndex, { entry in
            if let entry, isCurrentStoryViewOperation(entry, expected: expectedEntry) {
                current = entry.mergedEntry
            }
            return PeerOperationLogEntryUpdate(mergedIndex: .none, contents: .none)
        })
        guard let current, let operation = current.contents as? SynchronizeViewStoriesOperation else { return nil }
        guard operation.peerId == current.peerId,
              localStoryPrivacyAllowsReceipt(localStoryPrivacySettings(transaction: transaction), generation: operation.suppressionGeneration),
              let peer = transaction.getPeer(current.peerId), apiInputPeer(peer) != nil else {
            let _ = transaction.operationLogRemoveEntry(peerId: current.peerId, tag: OperationLogTags.SynchronizeViewStories, tagLocalIndex: current.tagLocalIndex)
            return nil
        }
        return LocalStoryViewReceiptRequest(kind: .read, peer: peer, storyId: operation.storyId, suppressionGeneration: operation.suppressionGeneration)
    }
    |> mapToSignal { request -> Signal<Void, NoError> in
        guard let request else { return .complete() }
        return dispatchLocalStoryViewReceipt(postbox: postbox, request: request, send: send, queue: queue)
    }
    |> then(postbox.transaction { transaction -> Void in
        // Normally coalescing allocates a new local index. Also check contents
        // and merged identity, so even an entry replaced in-place is retained.
        var shouldRemove = false
        transaction.operationLogUpdateEntry(peerId: expectedEntry.peerId, tag: OperationLogTags.SynchronizeViewStories, tagLocalIndex: expectedEntry.tagLocalIndex, { entry in
            if let entry { shouldRemove = isCurrentStoryViewOperation(entry, expected: expectedEntry) }
            return PeerOperationLogEntryUpdate(mergedIndex: .none, contents: .none)
        })
        if shouldRemove {
            let _ = transaction.operationLogRemoveEntry(peerId: expectedEntry.peerId, tag: OperationLogTags.SynchronizeViewStories, tagLocalIndex: expectedEntry.tagLocalIndex)
        }
    })
}

func managedSynchronizeViewStoriesOperations(postbox: Postbox, network: Network, stateManager: AccountStateManager) -> Signal<Void, NoError> {
    return managedSynchronizeViewStoriesOperations(postbox: postbox, send: { request in
        sendLocalStoryViewReceipt(network: network, request: request)
    })
}

func managedSynchronizeViewStoriesOperations(postbox: Postbox, send: @escaping LocalStoryViewReceiptSender, queue: Queue = Queue()) -> Signal<Void, NoError> {
    return Signal { _ in
        let helper = Atomic<ManagedSynchronizeViewStoriesOperationsHelper>(value: ManagedSynchronizeViewStoriesOperationsHelper())
        let stopped = Atomic<Bool>(value: false)
        let guardedSend: LocalStoryViewReceiptSender = { request in
            return Signal { subscriber in
                guard !stopped.with({ $0 }) else {
                    subscriber.putCompletion()
                    return EmptyDisposable
                }
                return send(request).start(next: { subscriber.putNext($0) }, error: { subscriber.putError($0) }, completed: { subscriber.putCompletion() })
            }
        }
        let disposable = (postbox.mergedOperationLogView(tag: OperationLogTags.SynchronizeViewStories, limit: 10)
        |> deliverOn(queue)).start(next: { view in
            let (disposeOperations, beginOperations) = helper.with { $0.update(view.entries) }
            for disposable in disposeOperations { disposable.dispose() }
            for (entry, disposable) in beginOperations {
                guard !stopped.with({ $0 }) else { break }
                disposable.set(processStoryViewOperation(postbox: postbox, expectedEntry: entry, send: guardedSend, queue: queue).start())
            }
        })
        return ActionDisposable {
            let _ = stopped.swap(true)
            disposable.dispose()
            let disposables = helper.with { $0.reset() }
            for operation in disposables { operation.dispose() }
        }
    }
}
