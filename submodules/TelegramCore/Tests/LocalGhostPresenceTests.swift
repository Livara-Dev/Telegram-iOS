// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

private final class GhostPresenceRecorder {
    let values = Atomic<[Bool]>(value: [])
    let cancellations = Atomic<Int>(value: 0)
    let pending: Bool
    private let callback = Atomic<((Bool) -> Void)?>(value: nil)
    var next: ((Bool) -> Void)? {
        get { self.callback.with { $0 } }
        set { let _ = self.callback.swap(newValue) }
    }

    init(pending: Bool = false) { self.pending = pending }

    func send(_ online: Bool) -> Signal<Void, NoError> {
        return Signal { subscriber in
            let _ = self.values.modify { $0 + [online] }
            self.next?(online)
            if !self.pending { subscriber.putCompletion() }
            return ActionDisposable {
                if self.pending { let _ = self.cancellations.modify { $0 + 1 } }
            }
        }
    }
}

/// Exercises the production presence manager with real account preferences;
/// only the final typed updateStatus sender is replaced.
final class LocalGhostPresenceTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private var managers: [(QueueLocalObject<AccountPresenceManagerImpl>, Queue)] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "ghost-presence")
    }

    override func tearDownWithError() throws {
        let queues = self.managers.map { $0.1 }
        self.managers.removeAll()
        for queue in queues { queue.sync {} }
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func configure(_ settings: LocalGhostSettings, postbox: Postbox? = nil) throws {
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: postbox ?? self.fixture.postbox).updateSettings { $0 = settings }))
    }

    private func start(_ intent: Signal<Bool, NoError>, _ recorder: GhostPresenceRecorder,
                       postbox: Postbox? = nil, refreshInterval: Double = 30.0) -> Queue {
        let queue = Queue(name: "GhostPresenceTests.worker")
        let box = postbox ?? self.fixture.postbox!
        let manager = QueueLocalObject(queue: queue, generate: {
            AccountPresenceManagerImpl(queue: queue, shouldKeepOnlinePresence: intent,
                settings: localGhostSettingsSignal(postbox: box), refreshInterval: refreshInterval,
                sendRequest: recorder.send)
        })
        self.managers.append((manager, queue))
        return queue
    }

    private func settle(_ queue: Queue, postbox: Postbox? = nil) throws {
        let box = postbox ?? self.fixture.postbox!
        for _ in 0 ..< 3 {
            let _ = try self.localArchiveValue(box.transaction { _ in })
            queue.sync {}
        }
    }

    func testNormalForegroundPresenceRemainsOnline() throws {
        let recorder = GhostPresenceRecorder()
        let queue = self.start(.single(true), recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [true])
    }

    func testGhostSuppressesOnlineAndIssuesOnlyAnActualOfflineRequest() throws {
        try self.configure(LocalGhostSettings(enabled: true, automaticOffline: false))
        let recorder = GhostPresenceRecorder()
        let queue = self.start(.single(true), recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [false])
    }

    func testBackgroundColdAccountDoesNotSendUnnecessaryPresence() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let recorder = GhostPresenceRecorder()
        let queue = self.start(.single(false), recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.values.with { $0 }.isEmpty)
    }

    func testEnablingAndDisablingSuppressionCancelsOldRequestAndUsesNewPolicyImmediately() throws {
        let recorder = GhostPresenceRecorder(pending: true)
        let queue = self.start(.single(true), recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [true])
        try self.configure(LocalGhostSettings(enabled: true, automaticOffline: false))
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [true, false])
        XCTAssertGreaterThanOrEqual(recorder.cancellations.with { $0 }, 1)
        try self.configure(LocalGhostSettings(enabled: false))
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [true, false, true])
    }

    func testActivityOnlyChangeDoesNotPublishAnotherPresencePacket() throws {
        try self.configure(LocalGhostSettings(enabled: true, automaticOffline: false))
        let recorder = GhostPresenceRecorder()
        let queue = self.start(.single(true), recorder)
        try self.settle(queue)
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).updateSettings { $0.suppressTyping = false }))
        try self.settle(queue)
        XCTAssertEqual(recorder.values.with { $0 }, [false])
    }

    func testTwoAccountsHaveIndependentPresencePolicy() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-presence-other-account")
        defer { try? other.destroy() }
        try self.configure(LocalGhostSettings(enabled: true, automaticOffline: false))
        let first = GhostPresenceRecorder(), second = GhostPresenceRecorder()
        let firstQueue = self.start(.single(true), first)
        let secondQueue = self.start(.single(true), second, postbox: other.postbox)
        try self.settle(firstQueue)
        try self.settle(secondQueue, postbox: other.postbox)
        XCTAssertEqual(first.values.with { $0 }, [false])
        XCTAssertEqual(second.values.with { $0 }, [true])
        // Release the second manager before its fixture is destroyed.
        self.managers.removeLast()
        secondQueue.sync {}
    }

    func testAutomaticOfflineRetriesOnlyWhileAccountIsForeground() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let foreground = ValuePromise<Bool>(true, ignoreRepeated: true)
        let recorder = GhostPresenceRecorder()
        let pulses = expectation(description: "initial and periodic offline attempts")
        let count = Atomic<Int>(value: 0)
        recorder.next = { online in
            XCTAssertFalse(online)
            let current = count.modify { $0 + 1 }
            if current == 2 {
                foreground.set(false)
                pulses.fulfill()
            }
        }
        let queue = self.start(foreground.get(), recorder, refreshInterval: 0.05)
        wait(for: [pulses], timeout: 15)
        try self.settle(queue)
        let noFurtherPulse = expectation(description: "no background presence timer")
        noFurtherPulse.isInverted = true
        recorder.next = { _ in noFurtherPulse.fulfill() }
        wait(for: [noFurtherPulse], timeout: 0.15)
        XCTAssertFalse(recorder.values.with { $0 }.contains(true))
    }

    func testAutomaticOfflineCannotRunWithoutOnlineSuppression() {
        let state = LocalGhostSettingsState.available(LocalGhostSettings(enabled: true, suppressOnlineStatus: false, automaticOffline: true))
        let decision = localAccountPresenceDecision(keepOnline: true, settings: state)
        XCTAssertTrue(decision.online)
        XCTAssertFalse(decision.repeatOffline)
    }

    func testUnknownPolicyDoesNotAnnounceOnlineOrStartAnUnknownHeartbeat() {
        let decision = localAccountPresenceDecision(keepOnline: true, settings: .unsupported)
        XCTAssertFalse(decision.online)
        XCTAssertFalse(decision.repeatOffline)
    }
}
