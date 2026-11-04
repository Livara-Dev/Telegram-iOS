// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalTelemetryTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private let root = PeerId(0)
    private let otherPeer = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(912))

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "local-telemetry")
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func count(_ peer: PeerId, tag: PeerOperationLogTag = OperationLogTags.SynchronizeAppLogEvents, postbox: Postbox? = nil) throws -> Int {
        return try self.localArchiveValue((postbox ?? self.fixture.postbox).transaction { transaction in
            var count = 0
            transaction.operationLogEnumerateEntries(peerId: peer, tag: tag) { _ in count += 1; return true }
            return count
        })
    }

    func testBothProducersAndSyncTriggerDoNotPersistTelemetry() throws {
        _internal_addAppLogEvent(postbox: self.fixture.postbox, time: 100, type: "canary", peerId: self.otherPeer, data: .dictionary(["message": .string("synthetic-private-canary")]))
        invokeAppLogEventsSynchronization(postbox: self.fixture.postbox)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _internal_addAppLogEvent(transaction: transaction, time: 200, type: "second", data: .dictionary([:]))
        })
        XCTAssertEqual(try self.count(self.root), 0)
        XCTAssertEqual(try self.count(self.otherPeer), 0)
    }

    func testLegacyCleanupRemovesOnlyRootTelemetryAndKeepsCanonicalMessages() throws {
        let root = self.root, otherPeer = self.otherPeer
        let id = MessageId(peerId: otherPeer, namespace: Namespaces.Message.Cloud, id: 1)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(otherPeer)], update: { _, peer in peer })
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "canonical content")], location: .Random)
            for _ in 0..<140 {
                transaction.operationLogAddEntry(peerId: root, tag: OperationLogTags.SynchronizeAppLogEvents, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: SynchronizeAppLogEventsOperation(content: .add(time: 100, type: "legacy", peerId: otherPeer, data: .dictionary([:]))))
            }
            transaction.operationLogAddEntry(peerId: root, tag: OperationLogTags.LocalArchiveCapture, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: SynchronizeAppLogEventsOperation(content: .sync))
            transaction.operationLogAddEntry(peerId: otherPeer, tag: OperationLogTags.SynchronizeAppLogEvents, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: SynchronizeAppLogEventsOperation(content: .sync))
        })
        XCTAssertEqual(try self.count(root), 140)
        let _ = try self.localArchiveValue(managedSynchronizeAppLogEventsOperations(postbox: self.fixture.postbox))
        XCTAssertEqual(try self.count(root), 0)
        XCTAssertEqual(try self.count(root, tag: OperationLogTags.LocalArchiveCapture), 1)
        XCTAssertEqual(try self.count(otherPeer), 1)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "canonical content")
        try self.fixture.reopen()
        let _ = try self.localArchiveValue(managedSynchronizeAppLogEventsOperations(postbox: self.fixture.postbox))
        XCTAssertEqual(try self.count(root), 0)
        XCTAssertEqual(try self.count(root, tag: OperationLogTags.LocalArchiveCapture), 1)
    }

    func testCleanupIsIsolatedToTheOwningAccount() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "local-telemetry-other")
        defer { try? other.destroy() }
        for postbox in [self.fixture.postbox!, other.postbox!] {
            let root = self.root
            let _ = try self.localArchiveValue(postbox.transaction { transaction in
                transaction.operationLogAddEntry(peerId: root, tag: OperationLogTags.SynchronizeAppLogEvents, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: SynchronizeAppLogEventsOperation(content: .sync))
            })
        }
        let _ = try self.localArchiveValue(managedSynchronizeAppLogEventsOperations(postbox: self.fixture.postbox))
        XCTAssertEqual(try self.count(self.root), 0)
        XCTAssertEqual(try self.count(self.root, postbox: other.postbox), 1)
    }
}
