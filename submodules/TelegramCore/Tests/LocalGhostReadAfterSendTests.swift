// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

/// Exercises the production encrypted Postbox intent store and the same
/// after-commit adapter used by PendingMessageManager. The final sender alone
/// is replaced; this is not a Telegram server or physical-device ACK test.
final class LocalGhostReadAfterSendTests: XCTestCase {
    private let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(2100))
    private let peer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(2200))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "ghost-read-after-send")
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(self.owner), localArchiveTestUser(self.peer)], update: { _, peer in peer })
            let _ = transaction.addMessages([localArchiveTestMessage(self.id(100))], location: .Random)
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ value: Int32, peer: PeerId? = nil, namespace: MessageId.Namespace = Namespaces.Message.Cloud) -> MessageId {
        return MessageId(peerId: peer ?? self.peer, namespace: namespace, id: value)
    }

    private func message(attributes: [MessageAttribute] = [], threadId: Int64? = nil) -> EnqueueMessage {
        return .message(text: "outgoing", attributes: attributes, inlineStickers: [:], mediaReference: nil,
            threadId: threadId, replyToMessageId: nil, replyToStoryId: nil, localGroupingKey: nil,
            correlationId: nil, bubbleUpEmojiOrStickersets: [])
    }

    private func enable(postbox: Postbox? = nil) throws {
        let context = LocalGhostContext(postbox: postbox ?? self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.updateSettings { $0.enabled = true }))
        XCTAssertTrue(try self.localArchiveValue(context.setReadAfterSendEnabled(true)))
    }

    private func capture(_ uniqueId: Int64 = 7, message: EnqueueMessage? = nil,
                         now: Int64 = Int64(Date().timeIntervalSince1970)) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: self.peer,
                uniqueId: uniqueId, message: message ?? self.message(), now: now)
        })
    }

    private func record() throws -> LocalGhostReadAfterSendRecord {
        return try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { localGhostReadAfterSendRecord(transaction: $0) }))
    }

    private func delivered(_ id: MessageId, attributes: [MessageAttribute] = []) -> StoreMessage {
        return StoreMessage(id: id, customStableId: nil, globallyUniqueId: nil, groupingKey: nil,
            threadId: nil, timestamp: 110, flags: [], tags: [], globalTags: [], localTags: [], forwardInfo: nil,
            authorId: self.owner, text: "confirmed send", attributes: attributes, media: [])
    }

    private func consume(_ confirmed: [LocalGhostConfirmedSend], owner: PeerId? = nil,
                         now: Int64 = Int64(Date().timeIntervalSince1970)) throws -> [LocalGhostReadAfterSendScope] {
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            consumeLocalGhostReadAfterSend(transaction: transaction, accountPeerId: owner ?? self.owner, confirmed: confirmed, now: now)
        })
    }

    private func installDelivered(_ ids: [MessageId], attributes: [MessageAttribute] = []) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages(ids.map { self.delivered($0, attributes: attributes) }, location: .Random)
        })
    }

    private func complete(_ signal: Signal<Void, NoError>) {
        let done = expectation(description: "real Postbox ACK adapter completes")
        let disposable = signal.start(completed: { done.fulfill() })
        defer { disposable.dispose() }
        wait(for: [done], timeout: 15)
    }

    func testDefaultDoesNotCaptureOrPermitAReceipt() throws {
        try self.capture()
        XCTAssertTrue(try self.record().intents.isEmpty)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).isEmpty)
    }

    func testGhostAndExplicitOptInAreBothRequired() throws {
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.setReadAfterSendEnabled(true)))
        try self.capture()
        XCTAssertTrue(try self.record().intents.isEmpty)
        XCTAssertTrue(try self.localArchiveValue(context.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(context.setReadAfterSendEnabled(false)))
        try self.capture()
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testActualCommitAdapterUsesPreSendBoundAndConsumesBeforeSender() throws {
        try self.enable()
        try self.capture()
        let confirmed = Atomic<[LocalGhostConfirmedSend]>(value: [])
        let sent = Atomic<[LocalGhostReadAfterSendScope]>(value: [])
        let emptyAtDispatch = Atomic<Bool>(value: false)
        let optionalFinished = expectation(description: "optional read finishes separately")
        let optionalTask = MetaDisposable()
        defer { optionalTask.dispose() }
        let commit: Signal<Void, NoError> = self.fixture.postbox.transaction { transaction in
            // This higher-id arrival happens while the outgoing message waits
            // for its ACK and must remain outside the earlier read bound.
            let _ = transaction.addMessages([localArchiveTestMessage(self.id(104)), self.delivered(self.id(105))], location: .Random)
            let _ = confirmed.swap([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))])
        }
        let signal = commit |> readAfterLocalGhostSendCommit(postbox: self.fixture.postbox, accountPeerId: self.owner,
            confirmed: { confirmed.with { $0 } }, send: { scope in
                let _ = sent.modify { $0 + [scope] }
                return self.fixture.postbox.transaction { transaction -> Void in
                    let _ = emptyAtDispatch.swap(localGhostReadAfterSendRecord(transaction: transaction)?.intents.isEmpty == true)
                }
            }, start: { operation in
                optionalTask.set(operation.start(completed: { optionalFinished.fulfill() }))
            })
        XCTAssertTrue(sent.with { $0 }.isEmpty)
        self.complete(signal)
        wait(for: [optionalFinished], timeout: 15)
        XCTAssertEqual(sent.with { $0 }, [LocalGhostReadAfterSendScope(accountPeerId: self.owner, maximumMessageId: self.id(100))])
        XCTAssertTrue(emptyAtDispatch.with { $0 })
        XCTAssertTrue(try self.consume(confirmed.with { $0 }).isEmpty)
    }

    func testNoMappedAckNeverCallsFinalSender() throws {
        try self.enable()
        try self.capture()
        let called = Atomic<Bool>(value: false)
        let optionalFinished = expectation(description: "unmapped ACK read completes without a sender")
        let optionalTask = MetaDisposable()
        defer { optionalTask.dispose() }
        let commit: Signal<Void, NoError> = self.fixture.postbox.transaction { _ in }
        self.complete(commit |> readAfterLocalGhostSendCommit(postbox: self.fixture.postbox, accountPeerId: self.owner,
            confirmed: { [] }, send: { _ in let _ = called.swap(true); return .complete() },
            start: { operation in optionalTask.set(operation.start(completed: { optionalFinished.fulfill() })) }))
        wait(for: [optionalFinished], timeout: 15)
        XCTAssertFalse(called.with { $0 })
        XCTAssertEqual(try self.record().intents.count, 1)
    }

    func testWrongAccountPeerNamespaceAndUncommittedIdCannotConsumeIntent() throws {
        try self.enable()
        try self.capture()
        try self.installDelivered([self.id(105)])
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))], owner: self.peer).isEmpty)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105, peer: self.owner))]).isEmpty)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105, namespace: Namespaces.Message.ScheduledCloud))]).isEmpty)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(106))]).isEmpty)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 99, deliveredId: self.id(105))]).isEmpty)
        XCTAssertEqual(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).count, 1)
    }

    func testAckCannotReadBeyondOrEqualToDeliveredId() throws {
        try self.enable()
        try self.capture()
        try self.installDelivered([self.id(99)])
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(99))]).isEmpty)
    }

    func testOptOutAndReenableInvalidatesPendingPermissions() throws {
        try self.enable()
        try self.capture()
        let generation = try self.record().generation
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.setReadAfterSendEnabled(false)))
        XCTAssertTrue(try self.localArchiveValue(context.setReadAfterSendEnabled(true)))
        try self.installDelivered([self.id(105)])
        XCTAssertGreaterThan(try self.record().generation, generation)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).isEmpty)
    }

    func testGhostMasterTransitionInvalidatesPendingPermissionsWithoutChangingOptIn() throws {
        try self.enable()
        try self.capture()
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.setEnabled(false)))
        XCTAssertTrue(try self.localArchiveValue(context.setEnabled(true)))
        XCTAssertTrue(try self.record().enabled)
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testReadSuppressionChoiceTransitionInvalidatesPendingPermissions() throws {
        try self.enable()
        try self.capture()
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.updateSettings { $0.suppressReadReceipts = false }))
        XCTAssertTrue(try self.localArchiveValue(context.updateSettings { $0.suppressReadReceipts = true }))
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testRealPostboxRestartRetainsBoundAndConsumedIntentCannotReplay() throws {
        try self.enable()
        try self.capture()
        try self.installDelivered([self.id(105)])
        try self.fixture.reopen()
        XCTAssertEqual(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).first?.maximumMessageId, self.id(100))
        try self.fixture.reopen()
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).isEmpty)
    }

    func testExpiredOrClockReversedPermissionsFailClosed() throws {
        try self.enable()
        try self.capture(now: 1000)
        try self.installDelivered([self.id(105)])
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))], now: 1901).isEmpty)
        try self.capture(now: 2000)
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))], now: 1999).isEmpty)
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testPersistentPermissionStoreIsBounded() throws {
        try self.enable()
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            for uniqueId in 0 ..< Int64(150) {
                captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: self.peer,
                    uniqueId: uniqueId, message: self.message())
            }
        })
        let record = try self.record()
        XCTAssertEqual(record.intents.count, 128)
        XCTAssertEqual(record.intents.first?.uniqueId, 22)
        XCTAssertLessThan(try XCTUnwrap(PreferencesEntry(record)).data.count, 64 * 1024)
    }

    func testTwoEncryptedAccountStoresCannotShareIntent() throws {
        try self.enable()
        try self.capture()
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-read-after-send-other")
        defer { try? other.destroy() }
        try self.enable(postbox: other.postbox)
        let scopes = try self.localArchiveValue(other.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(self.peer)], update: { _, value in value })
            let _ = transaction.addMessages([self.delivered(self.id(105))], location: .Random)
            return consumeLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner,
                confirmed: [LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))])
        })
        XCTAssertTrue(scopes.isEmpty)
        XCTAssertEqual(try self.record().intents.count, 1)
    }

    func testFutureSchemaRemainsUnmodifiedAndNeverAuthorizesARead() throws {
        try self.enable()
        var future = try self.record()
        future.schemaVersion = 99
        let entry = try XCTUnwrap(PreferencesEntry(future))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localGhostReadAfterSendKey, value: entry) })
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertFalse(try self.localArchiveValue(context.setReadAfterSendEnabled(false)))
        try self.capture()
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostReadAfterSendKey)?.data }), entry.data)
    }

    func testThreadsScheduledQuickReplyAndExpiringRequestsAreExcluded() throws {
        try self.enable()
        let excluded = [self.message(threadId: 1),
            self.message(attributes: [OutgoingScheduleInfoMessageAttribute(scheduleTime: 9000, repeatPeriod: nil)]),
            self.message(attributes: [OutgoingQuickReplyMessageAttribute(shortcut: "fixture")]),
            self.message(attributes: [AutoremoveTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)]),
            self.message(attributes: [AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil)])]
        for (index, value) in excluded.enumerated() { try self.capture(Int64(index), message: value) }
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testServerIntroducedExpiryOnDeliveredMessageCannotAuthorizeOptionalRead() throws {
        try self.enable()
        try self.capture()
        try self.installDelivered([self.id(105)], attributes: [AutoremoveTimeoutMessageAttribute(timeout: 10, countdownBeginTime: 100)])
        XCTAssertTrue(try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))]).isEmpty)
    }

    func testAlbumAcksCollapseToOneBoundAndDoNotAuthorizeOtherChats() throws {
        try self.enable()
        try self.capture(7)
        try self.capture(8)
        try self.installDelivered([self.id(105), self.id(106)])
        let scopes = try self.consume([LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105)),
            LocalGhostConfirmedSend(uniqueId: 8, deliveredId: self.id(106))])
        XCTAssertEqual(scopes, [LocalGhostReadAfterSendScope(accountPeerId: self.owner, maximumMessageId: self.id(100))])
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testResetDisablesExceptionAndRevokesPendingPermissions() throws {
        try self.enable()
        try self.capture()
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).resetSettings()))
        XCTAssertFalse(try self.record().enabled)
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    private func channel(_ id: PeerId, forum: Bool = false, member: Bool = true) -> TelegramChannel {
        return TelegramChannel(id: id, accessHash: .personal(456), title: "Read-after-send fixture", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: member ? .member : .left,
            info: .group(TelegramChannelGroupInfo(flags: [])), flags: forum ? [.isForum] : [], restrictionInfo: nil,
            adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
            emojiStatus: nil, approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil,
            sendPaidMessageStars: nil, linkedMonoforumId: nil)
    }

    func testOrdinaryGroupAndMemberChannelUseIndependentPeerBounds() throws {
        try self.enable()
        let groupId = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(3001))
        let channelId = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(3002))
        let group = TelegramGroup(id: groupId, title: "Read-after-send group", photo: [], participantCount: 2,
            role: .member, membership: .Member, flags: [], defaultBannedRights: nil,
            migrationReference: nil, creationDate: 0, version: 0)
        let scopes = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([group, self.channel(channelId)], update: { _, value in value })
            let _ = transaction.addMessages([localArchiveTestMessage(self.id(12, peer: groupId)),
                localArchiveTestMessage(self.id(24, peer: channelId))], location: .Random)
            captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: groupId, uniqueId: 1, message: self.message())
            captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: channelId, uniqueId: 2, message: self.message())
            let _ = transaction.addMessages([self.delivered(self.id(13, peer: groupId)), self.delivered(self.id(25, peer: channelId))], location: .Random)
            return consumeLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, confirmed: [
                LocalGhostConfirmedSend(uniqueId: 1, deliveredId: self.id(13, peer: groupId)),
                LocalGhostConfirmedSend(uniqueId: 2, deliveredId: self.id(25, peer: channelId))])
        })
        XCTAssertEqual(Set(scopes.map { $0.maximumMessageId }), Set([self.id(12, peer: groupId), self.id(24, peer: channelId)]))
    }

    func testForumAndNonMemberChannelsAreExcluded() throws {
        try self.enable()
        let forumId = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(3003))
        let nonMemberId = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(3004))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([self.channel(forumId, forum: true), self.channel(nonMemberId, member: false)], update: { _, value in value })
            for (index, peerId) in [forumId, nonMemberId].enumerated() {
                let _ = transaction.addMessages([localArchiveTestMessage(self.id(10, peer: peerId))], location: .Random)
                captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: peerId, uniqueId: Int64(index), message: self.message())
            }
        })
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testSecretChatNeverCreatesOptionalReadPermission() throws {
        try self.enable()
        let secretId = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(3005))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            // Even if malformed or migrated metadata provides a Cloud-namespace
            // entry, the peer namespace itself must exclude Secret Chats.
            transaction.updatePeersInternal([localArchiveTestUser(secretId)], update: { _, value in value })
            let _ = transaction.addMessages([localArchiveTestMessage(self.id(10, peer: secretId))], location: .Random)
            captureLocalGhostReadAfterSend(transaction: transaction, accountPeerId: self.owner, peerId: secretId, uniqueId: 1, message: self.message())
        })
        XCTAssertTrue(try self.record().intents.isEmpty)
    }

    func testUncompletedOrCancelledSendCannotInvokeOptionalRead() throws {
        try self.enable()
        try self.capture()
        let called = Atomic<Bool>(value: false)
        let unfinished = Signal<Void, NoError> { _ in EmptyDisposable }
        let disposable = (unfinished |> readAfterLocalGhostSendCommit(postbox: self.fixture.postbox, accountPeerId: self.owner,
            confirmed: { [LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))] },
            send: { _ in let _ = called.swap(true); return .complete() },
            start: { _ in XCTFail("An uncompleted send must not start optional work") })).start()
        disposable.dispose()
        XCTAssertFalse(called.with { $0 })
        XCTAssertEqual(try self.record().intents.count, 1)
    }

    func testOptionalUnfinishedReadNeverDelaysNativeSendCompletion() throws {
        try self.enable()
        try self.capture()
        try self.installDelivered([self.id(105)])
        let requestStarted = expectation(description: "optional request started")
        let optionalTask = MetaDisposable()
        defer { optionalTask.dispose() }
        let commit: Signal<Void, NoError> = self.fixture.postbox.transaction { _ in }
        self.complete(commit |> readAfterLocalGhostSendCommit(postbox: self.fixture.postbox, accountPeerId: self.owner,
            confirmed: { [LocalGhostConfirmedSend(uniqueId: 7, deliveredId: self.id(105))] }, send: { _ in
                requestStarted.fulfill()
                return Signal<Void, NoError> { _ in EmptyDisposable }
            }, start: { operation in optionalTask.set(operation.start()) }))
        wait(for: [requestStarted], timeout: 15)
        XCTAssertTrue(try self.record().intents.isEmpty)
    }
}
