// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Real encrypted-Postbox coverage of the replacement helper used by both
/// remote media refresh paths. Responses are supplied locally, without RPCs.
final class LocalArchiveRemoteReconciliationTests: XCTestCase {
    private let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let user = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9601))
    private let other = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9602))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-remote-reconciliation")
        let owner = self.owner
        let peers = [localArchiveTestUser(owner), localArchiveTestUser(self.user), localArchiveTestUser(self.other)]
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: owner,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ value: Int32, peer: PeerId? = nil, namespace: Int32 = Namespaces.Message.Cloud) -> MessageId {
        MessageId(peerId: peer ?? self.user, namespace: namespace, id: value)
    }

    private func seed(_ id: MessageId, text: String, attributes: [MessageAttribute] = []) throws {
        let message = localArchiveTestMessage(id, text: text, attributes: attributes)
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([message], location: .Random)
        })
    }

    private func refresh(_ message: StoreMessage, requestedIds: Set<MessageId>) throws {
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            applyLocalArchiveRemoteMessage(transaction: transaction, requestedIds: requestedIds, message: message)
        })
    }

    private func current(_ id: MessageId) throws -> Message? {
        try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
    }

    private func pending() throws -> [LocalArchiveCaptureOperation] {
        let owner = self.owner
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: owner).compactMap { $0.operation }
        })
    }

    func testMatchingOrdinaryRefreshCapturesPreviousBodyOnceAndSurvivesReopen() throws {
        let id = self.id(1)
        try self.seed(id, text: "before remote refresh")
        let replacement = localArchiveTestMessage(id, text: "after remote refresh", attributes: [EditedMessageAttribute(date: 200, isHidden: false)])
        try self.refresh(replacement, requestedIds: [id])
        try self.refresh(replacement, requestedIds: [id])
        let captured = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(try self.pending().count, 1)
        XCTAssertEqual(captured.messageId, id)
        XCTAssertEqual(captured.text, "before remote refresh")
        XCTAssertEqual(captured.kindRawValue, ArchiveEventKind.edited.rawValue)
        XCTAssertEqual(try self.current(id)?.text, "after remote refresh")
        let eventKey = captured.eventKey
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().map { $0.eventKey }, [eventKey])
        XCTAssertEqual(try self.current(id)?.text, "after remote refresh")
    }

    func testMetadataRefreshKeepsPostboxAttributeMergeWithoutCreatingEdit() throws {
        let id = self.id(2)
        try self.seed(id, text: "same body", attributes: [DerivedDataMessageAttribute(data: [:])])
        let refreshed = localArchiveTestMessage(id, text: "same body", attributes: [ViewCountMessageAttribute(count: 20), EditedMessageAttribute(date: 201, isHidden: false)])
        try self.refresh(refreshed, requestedIds: [id])
        let current = try XCTUnwrap(self.current(id))
        XCTAssertTrue(current.attributes.contains { $0 is DerivedDataMessageAttribute })
        XCTAssertEqual(current.attributes.compactMap { ($0 as? ViewCountMessageAttribute)?.count }, [20])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testScheduledAndQuickReplyResponsesMisparsedAsCloudNeverEnterArchive() throws {
        for (offset, namespace) in [Namespaces.Message.ScheduledCloud, Namespaces.Message.QuickReplyCloud].enumerated() {
            let cloudId = self.id(Int32(offset + 3))
            let requestedId = self.id(cloudId.id, namespace: namespace)
            try self.seed(cloudId, text: "ordinary cached body")
            // AccountViewTracker currently parses these replies with the default
            // Cloud namespace. The original request must determine eligibility.
            let misparsed = localArchiveTestMessage(cloudId, text: "nonordinary response")
            try self.refresh(misparsed, requestedIds: [requestedId])
            XCTAssertEqual(try self.current(cloudId)?.text, "nonordinary response", "preserve the existing canonical replacement")
            XCTAssertNil(try self.current(requestedId))
            XCTAssertTrue(try self.pending().isEmpty)
        }
        try self.fixture.reopen()
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testMixedNamespaceRequestCannotReclassifyScheduledResponseAsCloud() throws {
        let cloudId = self.id(5)
        let scheduledId = self.id(5, namespace: Namespaces.Message.ScheduledCloud)
        try self.seed(cloudId, text: "cached body")
        // The caller chooses its RPC from the first requested ID. A mixed batch
        // is ambiguous even when it also contains the parsed Cloud ID.
        try self.refresh(localArchiveTestMessage(cloudId, text: "refreshed body"), requestedIds: [scheduledId, cloudId])
        XCTAssertEqual(try self.current(cloudId)?.text, "refreshed body")
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testResponsePeerAndMessageNumberMustMatchAnOriginalRequest() throws {
        let requested = self.id(6)
        let wrongPeer = self.id(6, peer: self.other)
        let wrongNumber = self.id(7)
        for responseId in [wrongPeer, wrongNumber] {
            try self.seed(responseId, text: "cached mismatched body")
            try self.refresh(localArchiveTestMessage(responseId, text: "refreshed mismatched body"), requestedIds: [requested])
            XCTAssertEqual(try self.current(responseId)?.text, "refreshed mismatched body")
        }
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.current(requested))
    }

    func testMissingBodyRemainsUnstoredAndDoesNotCreateArchiveEntry() throws {
        let id = self.id(8)
        try self.refresh(localArchiveTestMessage(id, text: "not previously stored"), requestedIds: [id])
        XCTAssertNil(try self.current(id))
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testDisabledOrUnauthorizedArchiveKeepsCanonicalReplacement() throws {
        let disabled = self.id(9), unauthorized = self.id(10)
        try self.seed(disabled, text: "disabled old body")
        try self.seed(unauthorized, text: "unauthorized old body")
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
        })
        try self.refresh(localArchiveTestMessage(disabled, text: "disabled replacement"), requestedIds: [disabled])
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
            transaction.setState(UnauthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, contents: .empty))
        })
        try self.refresh(localArchiveTestMessage(unauthorized, text: "unauthorized replacement"), requestedIds: [unauthorized])
        XCTAssertEqual(try self.current(disabled)?.text, "disabled replacement")
        XCTAssertEqual(try self.current(unauthorized)?.text, "unauthorized replacement")
        XCTAssertTrue(try self.pending().isEmpty)
    }
}
