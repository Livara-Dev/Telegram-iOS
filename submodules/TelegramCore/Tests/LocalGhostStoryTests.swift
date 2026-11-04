// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import MtProtoKit
@testable import TelegramCore

private struct StoryTestReadUpdate: Equatable {
    let peerId: PeerId
    let storyId: Int32
}

/// Replaces only the final transport. Retained subscribers allow deliberate
/// late completions after cancellation; no Telegram response is fabricated.
private final class StoryReceiptRecorder {
    private let lock = NSLock()
    private var recorded: [(LocalStoryViewReceiptRequest, Subscriber<Void, MTRpcError>)] = []
    private var finished = Set<Int>()
    private var cancelled = Set<Int>()
    let completeImmediately: Bool
    let readUpdates = Atomic<[StoryTestReadUpdate]>(value: [])

    init(completeImmediately: Bool = true) {
        self.completeImmediately = completeImmediately
    }

    var requests: [LocalStoryViewReceiptRequest] {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.recorded.map { $0.0 }
    }

    var cancellationCount: Int {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.cancelled.count
    }

    func send(_ request: LocalStoryViewReceiptRequest) -> Signal<Void, MTRpcError> {
        return Signal { subscriber in
            self.lock.lock()
            let index = self.recorded.count
            self.recorded.append((request, subscriber))
            self.lock.unlock()
            if self.completeImmediately { self.finish(index) }
            return ActionDisposable {
                self.lock.lock()
                if !self.finished.contains(index) { self.cancelled.insert(index) }
                self.lock.unlock()
            }
        }
    }

    func finish(_ index: Int, error: MTRpcError? = nil) {
        self.lock.lock()
        guard self.recorded.indices.contains(index) else {
            self.lock.unlock()
            XCTFail("transport completion index does not exist")
            return
        }
        let subscriber = self.recorded[index].1
        self.finished.insert(index)
        self.lock.unlock()
        if let error {
            subscriber.putError(error)
        } else {
            subscriber.putNext(())
            subscriber.putCompletion()
        }
    }

    func emitValueWithoutCompleting(_ index: Int) {
        self.lock.lock()
        guard self.recorded.indices.contains(index) else {
            self.lock.unlock()
            XCTFail("transport value index does not exist")
            return
        }
        let subscriber = self.recorded[index].1
        self.lock.unlock()
        subscriber.putNext(())
    }

    func injectReadUpdate(_ peerId: PeerId, _ storyId: Int32) {
        let _ = self.readUpdates.modify { $0 + [StoryTestReadUpdate(peerId: peerId, storyId: storyId)] }
    }
}

/// Production Postbox producer/consumer paths with an injected final transport.
/// These tests do not verify server viewer lists, physical devices or a second
/// process; restart means disposing the worker and reopening its actual store.
final class LocalGhostStoryTests: XCTestCase {
    private let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
    private let secondPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1002))
    private var fixture: LocalArchivePostboxFixture!
    private var subscriptions: [(Disposable, Queue)] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "ghost-story")
        try self.seed(self.fixture.postbox)
    }

    override func tearDownWithError() throws {
        self.stopAll()
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func user(_ id: PeerId) -> TelegramUser {
        return TelegramUser(id: id, accessHash: .personal(12345), firstName: "Story fixture", lastName: nil,
            username: nil, phone: nil, photo: [], botInfo: nil, restrictionInfo: nil, flags: [],
            emojiStatus: nil, usernames: [], storiesHidden: nil, nameColor: nil, backgroundEmojiId: nil,
            profileColor: nil, profileBackgroundEmojiId: nil, subscriberCount: nil, verificationIconFileId: nil)
    }

    private func seed(_ postbox: Postbox) throws {
        let peers = [self.user(self.peerId), self.user(self.secondPeerId)]
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, current in current })
            for peer in peers {
                transaction.setPeerStoryState(peerId: peer.id, state: Stories.PeerState(maxReadId: 3).postboxRepresentation)
            }
        })
    }

    private func settings(_ postbox: Postbox? = nil) throws -> LocalStoryPrivacySettings {
        let state = try self.localArchiveValue(LocalStoryPrivacyContext(postbox: postbox ?? self.fixture.postbox).settings)
        guard case let .available(settings) = state else {
            throw NSError(domain: "LocalGhostStoryTests.settings", code: 1)
        }
        return settings
    }

    private func suppress(_ value: Bool, postbox: Postbox? = nil) throws {
        XCTAssertTrue(try self.localArchiveValue(LocalStoryPrivacyContext(postbox: postbox ?? self.fixture.postbox).setSuppressed(value)))
    }

    private func watermark(_ peerId: PeerId? = nil, postbox: Postbox? = nil) throws -> Int32? {
        let id = peerId ?? self.peerId
        return try self.localArchiveValue((postbox ?? self.fixture.postbox!).transaction { transaction in
            transaction.getPeerStoryState(peerId: id)?.entry.get(Stories.PeerState.self)?.maxReadId
        })
    }

    private func pending(_ peerId: PeerId? = nil, postbox: Postbox? = nil) throws -> [SynchronizeViewStoriesOperation] {
        let id = peerId ?? self.peerId
        return try self.localArchiveValue((postbox ?? self.fixture.postbox!).transaction { transaction in
            var result: [SynchronizeViewStoriesOperation] = []
            transaction.operationLogEnumerateEntries(peerId: id, tag: OperationLogTags.SynchronizeViewStories) { entry in
                if let operation = entry.contents as? SynchronizeViewStoriesOperation { result.append(operation) }
                return true
            }
            return result
        })
    }

    private func view(_ id: Int32, pinned: Bool = false, peerId: PeerId? = nil,
                      postbox: Postbox? = nil, recorder: StoryReceiptRecorder = StoryReceiptRecorder()) throws {
        let completed = expectation(description: "story producer completed")
        let queue = Queue(name: "LocalGhostStoryTests.producer")
        let disposable = markStoryAsSeen(postbox: postbox ?? self.fixture.postbox, peerId: peerId ?? self.peerId,
            id: id, asPinned: pinned, send: recorder.send, injectReadUpdate: recorder.injectReadUpdate, queue: queue)
            .start(completed: { completed.fulfill() })
        defer { disposable.dispose(); queue.sync {} }
        wait(for: [completed], timeout: 15)
    }

    private func startWorker(_ recorder: StoryReceiptRecorder, postbox: Postbox? = nil) -> Queue {
        let queue = Queue(name: "LocalGhostStoryTests.worker")
        let disposable = managedSynchronizeViewStoriesOperations(postbox: postbox ?? self.fixture.postbox,
            send: recorder.send, queue: queue).start()
        self.subscriptions.append((disposable, queue))
        return queue
    }

    private func settle(_ queue: Queue, postbox: Postbox? = nil) throws {
        for _ in 0..<4 {
            queue.sync {}
            let _ = try self.localArchiveValue((postbox ?? self.fixture.postbox!).transaction { _ in })
        }
        queue.sync {}
    }

    private func stopAll() {
        for (subscription, queue) in self.subscriptions {
            subscription.dispose()
            queue.sync {}
        }
        self.subscriptions.removeAll()
    }

    private func requestNames(_ recorder: StoryReceiptRecorder) -> [String] {
        return recorder.requests.map { request in
            let kind: String
            switch request.kind {
            case .read: kind = "read"
            case .increment: kind = "increment"
            }
            return "\(kind):\(request.peer.id.toInt64()):\(request.storyId)"
        }
    }

    func testDefaultOrdinaryViewAdvancesLocalStateAndWorkerSendsExactReceipt() throws {
        let recorder = StoryReceiptRecorder()
        XCTAssertFalse(try self.settings().suppressViewReceipts)
        try self.view(7, recorder: recorder)
        XCTAssertEqual(try self.watermark(), 7)
        XCTAssertEqual(recorder.readUpdates.with { $0 }, [StoryTestReadUpdate(peerId: self.peerId, storyId: 7)])
        XCTAssertEqual(try self.pending().map(\.storyId), [7])
        XCTAssertTrue(recorder.requests.isEmpty)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(self.requestNames(recorder), ["read:\(self.peerId.toInt64()):7"])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testDefaultPinnedViewSendsIncrementWithoutLocalReadOrQueue() throws {
        let recorder = StoryReceiptRecorder()
        try self.view(9, pinned: true, recorder: recorder)
        XCTAssertEqual(self.requestNames(recorder), ["increment:\(self.peerId.toInt64()):9"])
        XCTAssertEqual(try self.watermark(), 3)
        XCTAssertTrue(recorder.readUpdates.with { $0.isEmpty })
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testSuppressedOrdinaryAndPinnedViewsLeaveBothPeersUnreadAndUnqueued() throws {
        try self.suppress(true)
        let recorder = StoryReceiptRecorder()
        for peerId in [self.peerId, self.secondPeerId] {
            for storyId in [Int32(7), 9, 7] {
                try self.view(storyId, peerId: peerId, recorder: recorder)
                try self.view(storyId, pinned: true, peerId: peerId, recorder: recorder)
            }
            XCTAssertEqual(try self.watermark(peerId), 3)
            XCTAssertTrue(try self.pending(peerId).isEmpty)
        }
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertTrue(recorder.readUpdates.with { $0.isEmpty })
    }

    func testDisableAfterSuppressedViewsDoesNotReplayUntilNewAllowedView() throws {
        try self.suppress(true)
        let recorder = StoryReceiptRecorder()
        try self.view(7, recorder: recorder)
        try self.view(8, pinned: true, recorder: recorder)
        try self.suppress(false)
        try self.fixture.reopen()
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertEqual(try self.watermark(), 3)
        try self.view(9, recorder: recorder)
        try self.settle(queue)
        XCTAssertEqual(self.requestNames(recorder), ["read:\(self.peerId.toInt64()):9"])
        XCTAssertEqual(try self.watermark(), 9)
    }

    func testQueuedReceiptRetiresWhileSuppressedAndStaysRetiredAfterReopen() throws {
        try self.view(7)
        try self.suppress(true)
        let recorder = StoryReceiptRecorder()
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertTrue(recorder.requests.isEmpty)
        // This watermark predates enabling suppression; do not roll it back.
        XCTAssertEqual(try self.watermark(), 7)
        self.stopAll()
        try self.fixture.reopen()
        try self.suppress(false)
        try self.settle(self.startWorker(recorder))
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testEnableDisableBeforeConsumerStartPersistsGenerationFence() throws {
        try self.view(7)
        XCTAssertEqual(try self.pending().map(\.suppressionGeneration), [0])
        try self.suppress(true)
        try self.suppress(false)
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings().suppressionGeneration, 1)
        let recorder = StoryReceiptRecorder()
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertTrue(try self.pending().isEmpty)
        try self.view(8, recorder: recorder)
        try self.settle(queue)
        XCTAssertEqual(self.requestNames(recorder), ["read:\(self.peerId.toInt64()):8"])
        XCTAssertEqual(recorder.requests.map(\.suppressionGeneration), [1])
    }

    func testRepeatedAndLowerViewsCoalesceWithoutDuplicatingActiveRequest() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        try self.view(7)
        try self.view(5)
        // A second peer changes the merged operation view, which used to restart
        // the unchanged first peer's operation in the upstream helper.
        try self.view(8, peerId: self.secondPeerId)
        try self.settle(queue)
        XCTAssertEqual(self.requestNames(recorder), ["read:\(self.peerId.toInt64()):7", "read:\(self.secondPeerId.toInt64()):8"])
        XCTAssertEqual(try self.pending().map(\.storyId), [7])
        XCTAssertEqual(try self.watermark(), 7)
        XCTAssertEqual(recorder.cancellationCount, 0)
        recorder.finish(0)
        recorder.finish(1)
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertTrue(try self.pending(self.secondPeerId).isEmpty)
    }

    func testLateCompletionOfOlderCoalescedRequestCannotRemoveNewerReceipt() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        try self.view(9)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(\.storyId), [7, 9])
        XCTAssertEqual(recorder.cancellationCount, 1)
        recorder.finish(0)
        try self.settle(queue)
        XCTAssertEqual(try self.pending().map(\.storyId), [9])
        recorder.finish(1)
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testEnablingCancelsActiveReadAndLateCompletionCannotAcknowledgeNewGeneration() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        try self.suppress(true)
        try self.settle(queue)
        XCTAssertEqual(recorder.cancellationCount, 1)
        XCTAssertTrue(try self.pending().isEmpty)
        try self.suppress(false)
        try self.view(9)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(\.suppressionGeneration), [0, 1])
        recorder.finish(0)
        try self.settle(queue)
        XCTAssertEqual(try self.pending().map(\.storyId), [9])
        recorder.finish(1)
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testTransportValueAloneDoesNotRetireDurableReceipt() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        recorder.emitValueWithoutCompleting(0)
        try self.settle(queue)
        XCTAssertEqual(try self.pending().map(\.storyId), [7])
        recorder.finish(0)
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testTransportFailureRetiresReceiptWithoutAutomaticReplayOnRestart() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        recorder.finish(0, error: MTRpcError(errorCode: 500, errorDescription: "SYNTHETIC_TEST_FAILURE"))
        try self.settle(queue)
        XCTAssertTrue(try self.pending().isEmpty)
        self.stopAll()
        try self.fixture.reopen()
        try self.settle(self.startWorker(recorder))
        XCTAssertEqual(recorder.requests.count, 1)
    }

    func testStoppingWorkerCancelsRequestAndRestartHonorsPersistedSuppression() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        try self.settle(self.startWorker(recorder))
        XCTAssertEqual(recorder.requests.count, 1)
        self.stopAll()
        XCTAssertEqual(recorder.cancellationCount, 1)
        try self.suppress(true)
        try self.suppress(false)
        try self.fixture.reopen()
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        recorder.finish(0)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testPolicyIsReadAtProducerSubscriptionNotSignalConstruction() throws {
        let recorder = StoryReceiptRecorder()
        let queue = Queue(name: "LocalGhostStoryTests.deferred")
        let signal = markStoryAsSeen(postbox: self.fixture.postbox, peerId: self.peerId, id: 7,
            asPinned: true, send: recorder.send, injectReadUpdate: recorder.injectReadUpdate, queue: queue)
        try self.suppress(true)
        let completed = expectation(description: "deferred suppressed producer completed")
        let disposable = signal.start(completed: { completed.fulfill() })
        defer { disposable.dispose(); queue.sync {} }
        wait(for: [completed], timeout: 15)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertEqual(try self.watermark(), 3)
    }

    func testEnablingCancelsPinnedRequestWithoutReplayingAfterDisable() throws {
        let recorder = StoryReceiptRecorder(completeImmediately: false)
        let queue = Queue(name: "LocalGhostStoryTests.pinned")
        let disposable = markStoryAsSeen(postbox: self.fixture.postbox, peerId: self.peerId, id: 7,
            asPinned: true, send: recorder.send, injectReadUpdate: recorder.injectReadUpdate, queue: queue).start()
        self.subscriptions.append((disposable, queue))
        try self.settle(queue)
        XCTAssertEqual(self.requestNames(recorder), ["increment:\(self.peerId.toInt64()):7"])
        try self.suppress(true)
        try self.settle(queue)
        XCTAssertEqual(recorder.cancellationCount, 1)
        try self.suppress(false)
        recorder.finish(0)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 1)
        XCTAssertEqual(try self.watermark(), 3)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertTrue(recorder.readUpdates.with { $0.isEmpty })
    }

    func testResolvedRequestIsRecheckedAgainstLatestGenerationAtDispatch() throws {
        let recorder = StoryReceiptRecorder()
        let peerId = self.peerId
        let peer = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getPeer(peerId) }))
        let queue = Queue(name: "LocalGhostStoryTests.resolved")
        let resolved = LocalStoryViewReceiptRequest(kind: .increment, peer: peer, storyId: 7, suppressionGeneration: 0)
        let dispatch = dispatchLocalStoryViewReceipt(postbox: self.fixture.postbox, request: resolved, send: recorder.send, queue: queue)
        try self.suppress(true)
        try self.suppress(false)
        let _ = try self.localArchiveValue(dispatch)
        XCTAssertTrue(recorder.requests.isEmpty)
        let current = LocalStoryViewReceiptRequest(kind: .increment, peer: peer, storyId: 8, suppressionGeneration: 1)
        let _ = try self.localArchiveValue(dispatchLocalStoryViewReceipt(postbox: self.fixture.postbox, request: current, send: recorder.send, queue: queue))
        XCTAssertEqual(recorder.requests.map(\.storyId), [8])
    }

    func testQueuedOldPolicySnapshotCannotAuthorizeDispatchAfterEnableDisable() throws {
        let recorder = StoryReceiptRecorder()
        let peerId = self.peerId
        let peer = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getPeer(peerId) }))
        let queue = Queue(name: "LocalGhostStoryTests.blocked-dispatch")
        let release = DispatchSemaphore(value: 0)
        let blocked = expectation(description: "dispatch queue is held before policy delivery")
        queue.async {
            blocked.fulfill()
            if release.wait(timeout: .now() + 15) == .timedOut {
                XCTFail("dispatch queue test gate timed out")
            }
        }
        defer { release.signal(); queue.sync {} }
        wait(for: [blocked], timeout: 15)
        let request = LocalStoryViewReceiptRequest(kind: .increment, peer: peer, storyId: 7, suppressionGeneration: 0)
        let disposable = dispatchLocalStoryViewReceipt(postbox: self.fixture.postbox, request: request, send: recorder.send, queue: queue).start()
        defer { disposable.dispose() }
        // Fence Postbox so the initial allowed policy has been observed and
        // queued, but cannot yet run on the blocked final dispatch queue.
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { _ in })
        try self.suppress(true)
        try self.suppress(false)
        release.signal()
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertEqual(try self.settings().suppressionGeneration, 1)
    }

    func testNewGenerationLowerStoryReplacesStaleHigherQueuedReceipt() throws {
        try self.view(20)
        try self.suppress(true)
        try self.suppress(false)
        try self.view(7)
        XCTAssertEqual(try self.pending().map(\.storyId), [7])
        XCTAssertEqual(try self.pending().map(\.suppressionGeneration), [1])
        XCTAssertEqual(try self.watermark(), 20)
        let recorder = StoryReceiptRecorder()
        try self.settle(self.startWorker(recorder))
        XCTAssertEqual(recorder.requests.map(\.storyId), [7])
    }

    func testDisposedWorkerDoesNotDispatchNewWorkOrReactToPolicyChanges() throws {
        try self.view(7)
        let recorder = StoryReceiptRecorder()
        let queue = self.startWorker(recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(\.storyId), [7])
        self.stopAll()
        try self.view(9)
        try self.suppress(true)
        try self.suppress(false)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(\.storyId), [7])
        XCTAssertEqual(try self.pending().map(\.storyId), [9])
        try self.settle(self.startWorker(recorder))
        XCTAssertEqual(recorder.requests.map(\.storyId), [7])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    private func install(_ entry: PreferencesEntry) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localStoryPrivacySettingsKey, value: entry) })
    }

    private func encodedSettings(_ changes: [String: Any]) throws -> LocalStoryPrivacySettings {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(LocalStoryPrivacySettings())) as? [String: Any])
        for (key, value) in changes { object[key] = value }
        return try JSONDecoder().decode(LocalStoryPrivacySettings.self, from: JSONSerialization.data(withJSONObject: object))
    }

    private func assertUnsupported(_ entry: PreferencesEntry) throws {
        try self.view(7)
        try self.install(entry)
        try self.fixture.reopen()
        let context = LocalStoryPrivacyContext(postbox: self.fixture.postbox)
        XCTAssertEqual(try self.localArchiveValue(context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(context.setSuppressed(false)))
        XCTAssertFalse(try self.localArchiveValue(context.setSuppressed(true)))
        let stored = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localStoryPrivacySettingsKey)?.data })
        XCTAssertEqual(stored, entry.data)
        let recorder = StoryReceiptRecorder()
        try self.view(9, recorder: recorder)
        try self.view(9, pinned: true, recorder: recorder)
        try self.settle(self.startWorker(recorder))
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertTrue(recorder.readUpdates.with { $0.isEmpty })
        XCTAssertEqual(try self.watermark(), 7)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testFuturePolicyRemainsReadOnlyAndSuppressesProducerAndQueuedConsumer() throws {
        let settings = try self.encodedSettings(["schemaVersion": 99])
        try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(settings)))
    }

    func testMalformedPolicyRemainsReadOnlyAndSuppressesProducerAndQueuedConsumer() throws {
        struct IncompleteSettings: Encodable { let schemaVersion: Int32 = 1 }
        try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(IncompleteSettings())))
    }

    func testInvalidNegativeGenerationRemainsReadOnlyAndSuppressesReceipts() throws {
        let invalid = try self.encodedSettings(["suppressionGeneration": Int64(-1)])
        try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(invalid)))
    }

    func testGenerationIsFixedWidthPersistentAndOnlyEnablingAdvancesIt() throws {
        let large = try self.encodedSettings(["suppressionGeneration": Int64(4_294_967_302)])
        try self.install(try XCTUnwrap(PreferencesEntry(large)))
        try self.suppress(false)
        XCTAssertEqual(try self.settings().suppressionGeneration, 4_294_967_302)
        try self.suppress(true)
        try self.suppress(true)
        XCTAssertEqual(try self.settings().suppressionGeneration, 4_294_967_303)
        try self.suppress(false)
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings().suppressionGeneration, 4_294_967_303)
        XCTAssertFalse(try self.settings().suppressViewReceipts)
    }

    func testGenerationOverflowRejectsEnableWithoutResettingExistingPolicy() throws {
        let maximum = try self.encodedSettings(["suppressionGeneration": Int64.max])
        let entry = try XCTUnwrap(PreferencesEntry(maximum))
        try self.install(entry)
        XCTAssertFalse(try self.localArchiveValue(LocalStoryPrivacyContext(postbox: self.fixture.postbox).setSuppressed(true)))
        XCTAssertEqual(try self.settings().suppressionGeneration, Int64.max)
        XCTAssertFalse(try self.settings().suppressViewReceipts)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localStoryPrivacySettingsKey)?.data }), entry.data)
    }

    func testLegacyOperationDecodesGenerationZeroAndIsFencedAfterEnable() throws {
        let encoder = PostboxEncoder()
        encoder.encodeInt64(self.peerId.toInt64(), forKey: "p")
        encoder.encodeInt32(7, forKey: "s")
        let legacy = SynchronizeViewStoriesOperation(decoder: PostboxDecoder(buffer: MemoryBuffer(data: encoder.makeData())))
        XCTAssertEqual(legacy.suppressionGeneration, 0)
        XCTAssertEqual(legacy.storyId, 7)
        let peerId = self.peerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.operationLogAddEntry(peerId: peerId, tag: OperationLogTags.SynchronizeViewStories,
                tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: legacy)
        })
        try self.suppress(true)
        try self.suppress(false)
        try self.fixture.reopen()
        let recorder = StoryReceiptRecorder()
        try self.settle(self.startWorker(recorder))
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testStoryPolicyDoesNotRewriteActivityPrivacyPreferences() throws {
        let activity = LocalGhostSettings(enabled: true, suppressTyping: false, suppressStickerSelection: true,
            suppressRecording: false, suppressUploads: true, suppressGamePlay: false)
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).updateSettings { $0 = activity }))
        let before = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostSettingsKey)?.data })
        try self.suppress(true)
        try self.suppress(false)
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).settings), .available(activity))
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostSettingsKey)?.data }), before)
    }

    func testTwoAccountStoresKeepPolicyWatermarksAndReceiptsIndependent() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-story-other-account")
        defer { self.stopAll(); try? other.destroy() }
        try self.seed(other.postbox)
        try self.suppress(true)
        let recorder = StoryReceiptRecorder()
        let otherRecorder = StoryReceiptRecorder()
        try self.view(7, recorder: recorder)
        try self.view(7, pinned: true, recorder: recorder)
        try self.view(7, postbox: other.postbox, recorder: otherRecorder)
        try self.view(7, pinned: true, postbox: other.postbox, recorder: otherRecorder)
        try self.settle(self.startWorker(recorder))
        try self.settle(self.startWorker(otherRecorder, postbox: other.postbox), postbox: other.postbox)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertEqual(self.requestNames(otherRecorder), ["increment:\(self.peerId.toInt64()):7", "read:\(self.peerId.toInt64()):7"])
        XCTAssertEqual(try self.watermark(), 3)
        XCTAssertEqual(try self.watermark(postbox: other.postbox), 7)
        XCTAssertEqual(try self.settings().suppressionGeneration, 1)
        XCTAssertEqual(try self.settings(other.postbox).suppressionGeneration, 0)
    }
}
