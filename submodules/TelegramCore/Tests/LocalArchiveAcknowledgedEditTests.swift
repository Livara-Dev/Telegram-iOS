// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Exercises the application helper called by all four edit-RPC acknowledgement
/// branches. Network delivery is outside this fixture; later update replay has a
/// separate regression in LocalArchiveReplayTests.
final class LocalArchiveAcknowledgedEditTests: XCTestCase {
    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-acknowledged-edit")
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ value: Int32, namespace: Int32 = Namespaces.Message.Cloud) -> MessageId {
        return MessageId(peerId: self.chatPeerId, namespace: namespace, id: value)
    }

    private func enableArchive() throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    private func store(_ message: StoreMessage) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages([message], location: .Random)
        })
    }

    private func acknowledge(_ message: StoreMessage) throws {
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            applyAcknowledgedEditMessage(transaction: transaction, accountPeerId: accountPeerId, message: message)
        })
    }

    private func pending() throws -> [LocalArchiveCaptureOperation] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId).compactMap { $0.operation }
        })
    }

    func testAcknowledgedEditCapturesPreviousVersionBeforeReplacementAndReopen() throws {
        try self.enableArchive()
        let id = self.id(1)
        try self.store(localArchiveTestMessage(id, text: "previous local version", authorId: self.accountPeerId,
            attributes: [EditedMessageAttribute(date: 150, isHidden: false)]).withUpdatedFlags([]))
        let beganAt = Int64(Date().timeIntervalSince1970)
        try self.acknowledge(localArchiveTestMessage(id, text: "acknowledged replacement", authorId: self.accountPeerId,
            attributes: [EditedMessageAttribute(date: 200, isHidden: false)]))
        let capture = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(capture.messageId, id)
        XCTAssertEqual(capture.text, "previous local version")
        XCTAssertEqual(capture.authorId, self.accountPeerId.toInt64())
        XCTAssertEqual(capture.serverEditAt, 150)
        XCTAssertEqual(capture.sentAt, 100)
        XCTAssertEqual(capture.kindRawValue, ArchiveEventKind.edited.rawValue)
        XCTAssertGreaterThanOrEqual(capture.observedAt, beganAt)
        XCTAssertLessThanOrEqual(capture.observedAt, Int64(Date().timeIntervalSince1970))

        try self.fixture.reopen()
        let retained = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(retained.eventKey, capture.eventKey)
        XCTAssertEqual(try self.pending().count, 1)
        let current = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(current.text, "acknowledged replacement")
        XCTAssertEqual(current.editedTime, 200)
        XCTAssertFalse(current.flags.contains(.Incoming))
    }

    func testDisabledArchiveStillAppliesAcknowledgedEdit() throws {
        let id = self.id(1)
        try self.store(localArchiveTestMessage(id, text: "before"))
        try self.acknowledge(localArchiveTestMessage(id, text: "after"))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "after")
    }

    func testDuplicateMetadataOnlyAndMissingAcknowledgementsDoNotInventVersions() throws {
        try self.enableArchive()
        let id = self.id(1)
        try self.store(localArchiveTestMessage(id, text: "same"))
        let updated = localArchiveTestMessage(id, text: "same", attributes: [EditedMessageAttribute(date: 200, isHidden: false)])
        try self.acknowledge(updated)
        try self.acknowledge(updated)
        try self.acknowledge(localArchiveTestMessage(self.id(2), text: "not received"))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(self.id(2)) }))
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.editedTime }), 200)
    }

    func testAcknowledgementPreservesIncomingFlagLiveLocationAndUnlockedPaidMedia() throws {
        try self.enableArchive()
        let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 1),
            representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        let unlocked = TelegramMediaPaidContent(amount: 10, extendedMedia: [.full(media: image)])
        let locked = TelegramMediaPaidContent(amount: 10,
            extendedMedia: [.preview(dimensions: nil, immediateThumbnailData: nil, videoDuration: nil)])
        for wasIncoming in [false, true] {
            let id = self.id(wasIncoming ? 2 : 1)
            let previousFlags: StoreMessageFlags = wasIncoming ? [.Incoming] : []
            let serverFlags: StoreMessageFlags = wasIncoming ? [] : [.Incoming]
            try self.store(localArchiveTestMessage(id, text: "old caption")
                .withUpdatedFlags(previousFlags).withUpdatedLocalTags([.OutgoingLiveLocation]).withUpdatedMedia([unlocked]))
            try self.acknowledge(localArchiveTestMessage(id, text: "new caption")
                .withUpdatedFlags(serverFlags).withUpdatedMedia([locked]))
            let current = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
            XCTAssertEqual(current.text, "new caption")
            XCTAssertEqual(current.flags.contains(.Incoming), wasIncoming)
            XCTAssertTrue(current.localTags.contains(.OutgoingLiveLocation))
            let paid = try XCTUnwrap(current.media.first as? TelegramMediaPaidContent)
            guard case .full = paid.extendedMedia.first else {
                XCTFail("acknowledged edit replaced already-unlocked media with its preview")
                continue
            }
        }
        XCTAssertEqual(try self.pending().map { $0.text }, ["old caption", "old caption"])
    }

    func testScheduledEditUpdatesNormallyWithoutArchiving() throws {
        try self.enableArchive()
        let id = self.id(1, namespace: Namespaces.Message.ScheduledCloud)
        try self.store(localArchiveTestMessage(id, text: "scheduled original"))
        try self.acknowledge(localArchiveTestMessage(id, text: "scheduled replacement"))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "scheduled replacement")
    }
}
