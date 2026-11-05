// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import TelegramApi
import LocalMessageArchive
@testable import TelegramCore

/// Exercises the real feed-page storage function against encrypted Postbox.
/// This does not enable the News feed or make authenticated network requests.
final class LocalArchiveFeedReconciliationTests: XCTestCase {
    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let authorPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9301))
    private let channelPeerId = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(9300))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-feed-reconciliation")
        let channel = TelegramChannel(id: self.channelPeerId, accessHash: .personal(456), title: "Archive feed fixture", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: .member,
            info: .broadcast(TelegramChannelBroadcastInfo(flags: [])), flags: [], restrictionInfo: nil,
            adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
            emojiStatus: nil, approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil,
            sendPaidMessageStars: nil, linkedMonoforumId: nil)
        let accountPeerId = self.accountPeerId
        let peers: [Peer] = [localArchiveTestUser(accountPeerId), localArchiveTestUser(self.authorPeerId), channel]
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: accountPeerId,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ number: Int32) -> MessageId {
        MessageId(peerId: self.channelPeerId, namespace: Namespaces.Message.Cloud, id: number)
    }

    private func serverMessage(_ number: Int32, text: String, editDate: Int32? = nil, views: Int32? = nil,
                               ttlPeriod: Int32? = nil, copyProtected: Bool = false, groupedId: Int64? = nil) -> Api.Message {
        var flags: Int32 = (1 << 8) | (1 << 14)
        if editDate != nil { flags |= 1 << 15 }
        if views != nil { flags |= 1 << 10 }
        if ttlPeriod != nil { flags |= 1 << 25 }
        if copyProtected { flags |= 1 << 26 }
        if groupedId != nil { flags |= 1 << 17 }
        return .message(.init(flags: flags, flags2: 0, id: number,
            fromId: .peerUser(.init(userId: self.authorPeerId.id._internalGetInt64Value())), fromBoostsApplied: nil, fromRank: nil,
            peerId: .peerChannel(.init(channelId: self.channelPeerId.id._internalGetInt64Value())), savedPeerId: nil,
            fwdFrom: nil, viaBotId: nil, viaBusinessBotId: nil, guestchatViaFrom: nil, replyTo: nil, date: 100,
            message: text, media: nil, replyMarkup: nil, entities: nil, views: views, forwards: views.map { _ in 0 },
            replies: nil, editDate: editDate, postAuthor: nil, groupedId: groupedId, reactions: nil, restrictionReason: nil,
            ttlPeriod: ttlPeriod, quickReplyShortcutId: nil, effect: nil, factcheck: nil, reportDeliveryUntilDate: nil,
            paidMessageStars: nil, suggestedPost: nil, scheduleRepeatPeriod: nil, summaryFromLanguage: nil, richMessage: nil))
    }

    private func seed(_ number: Int32, text: String, attributes: [MessageAttribute] = []) throws {
        let message = localArchiveTestMessage(self.id(number), text: text, authorId: self.authorPeerId, attributes: attributes)
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([message], location: .Random)
        })
    }

    private func storePage(_ messages: [Api.Message]) throws -> [FeedPageMessage] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            storeFeedMessages(transaction: transaction, accountPeerId: accountPeerId, messages: messages, chats: [], users: [])
        })
    }

    private func pending() throws -> [LocalArchiveCaptureOperation] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId).compactMap { $0.operation }
        })
    }

    private func current(_ number: Int32) throws -> Message? {
        let id = self.id(number)
        return try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
    }

    func testFeedRefreshPersistsPreviousBodyOnceAndKeepsPageOrder() throws {
        try self.seed(1, text: "previous feed body")
        let edited = self.serverMessage(1, text: "current feed body", editDate: 200, groupedId: 31)
        let added = self.serverMessage(2, text: "newly received body", groupedId: 31)
        let page = try self.storePage([edited, added])
        XCTAssertEqual(page.map { $0.id }, [self.id(1), self.id(2)])
        XCTAssertEqual(page.map { $0.timestamp }, [100, 100])
        XCTAssertEqual(page.map { $0.groupingKey }, [31, 31])
        _ = try self.storePage([edited, added])
        XCTAssertEqual(try self.current(1)?.text, "current feed body")
        XCTAssertEqual(try self.current(2)?.text, "newly received body")
        let captured = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(try self.pending().count, 1)
        XCTAssertEqual(captured.messageId, self.id(1))
        XCTAssertEqual(captured.text, "previous feed body")
        XCTAssertEqual(captured.kindRawValue, ArchiveEventKind.edited.rawValue)
        let eventKey = captured.eventKey
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().map { $0.eventKey }, [eventKey])
        XCTAssertEqual(try self.current(1)?.text, "current feed body")
    }

    func testMetadataRefreshKeepsTranslationAndCreatesNoArchiveVersion() throws {
        try self.seed(3, text: "unchanged body", attributes: [TranslationMessageAttribute(text: "translation", entities: [], toLang: "de")])
        _ = try self.storePage([self.serverMessage(3, text: "unchanged body", editDate: 201, views: 10)])
        let stored = try XCTUnwrap(self.current(3))
        XCTAssertTrue(stored.attributes.contains { $0 is TranslationMessageAttribute })
        XCTAssertEqual(stored.attributes.compactMap { ($0 as? ViewCountMessageAttribute)?.count }, [10])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testIncomingProtectedOrExpiringBodiesDoNotArchivePreviousContent() throws {
        try self.seed(4, text: "previous protected body")
        try self.seed(5, text: "previous expiring body")
        _ = try self.storePage([
            self.serverMessage(4, text: "current protected body", editDate: 202, copyProtected: true),
            self.serverMessage(5, text: "current expiring body", editDate: 203, ttlPeriod: 30)
        ])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.current(4)?.text, "current protected body")
        XCTAssertTrue(try self.current(4)?.flags.contains(.CopyProtected) ?? false)
        XCTAssertEqual(try self.current(5)?.text, "current expiring body")
        XCTAssertTrue(try self.current(5)?.attributes.contains { $0 is AutoremoveTimeoutMessageAttribute } ?? false)
    }

    func testDisabledArchiveLeavesNormalFeedReplacementAndInsertionIntact() throws {
        try self.seed(6, text: "old body")
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
        })
        let page = try self.storePage([self.serverMessage(6, text: "replacement"), self.serverMessage(7, text: "inserted")])
        XCTAssertEqual(page.map { $0.id }, [self.id(6), self.id(7)])
        XCTAssertEqual(try self.current(6)?.text, "replacement")
        XCTAssertEqual(try self.current(7)?.text, "inserted")
        XCTAssertTrue(try self.pending().isEmpty)
    }
}
