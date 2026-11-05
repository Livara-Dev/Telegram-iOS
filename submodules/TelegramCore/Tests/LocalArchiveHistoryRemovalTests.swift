// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

private struct HistoryRemovalTestKey: ArchiveKeyProvider {
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data { Data(repeating: 0x57, count: 32) }
}

/// Calls production history/moderation helpers against persistent Postbox. No
/// direct archive-capture helper is used to create these outbox records.
final class LocalArchiveHistoryRemovalTests: XCTestCase {
    private let account = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let user = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9201))
    private let other = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9202))
    private let group = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(9203))
    private let secret = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(9204))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-history-removal")
        let account = self.account
        let peers: [Peer] = [localArchiveTestUser(self.account), localArchiveTestUser(self.user), localArchiveTestUser(self.other),
            TelegramGroup(id: self.group, title: "Legacy group", photo: [], participantCount: 3, role: .member, membership: .Member, flags: [], defaultBannedRights: nil, migrationReference: nil, creationDate: 0, version: 0),
            TelegramSecretChat(id: self.secret, creationDate: 0, regularPeerId: self.user, accessHash: 0, role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: account, state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ peer: PeerId, _ number: Int32, namespace: Int32 = Namespaces.Message.Cloud) -> MessageId {
        MessageId(peerId: peer, namespace: namespace, id: number)
    }

    private func message(_ id: MessageId, text: String? = nil, timestamp: Int32 = 100, thread: Int64? = nil, author: PeerId? = nil, forward: PeerId? = nil, attributes: [MessageAttribute] = [], media: [Media] = []) -> StoreMessage {
        StoreMessage(id: id, customStableId: nil, globallyUniqueId: nil, groupingKey: nil, threadId: thread, timestamp: timestamp, flags: [.Incoming], tags: [], globalTags: [], localTags: [], forwardInfo: forward.map { StoreMessageForwardInfo(authorId: $0, sourceId: nil, sourceMessageId: nil, date: 90, authorSignature: nil, psaType: nil, flags: []) }, authorId: author ?? self.user, text: text ?? "body \(id.id)", attributes: attributes, media: media)
    }

    private func store(_ messages: [StoreMessage]) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in _ = transaction.addMessages(messages, location: .Random) })
    }

    private func captures() throws -> [LocalArchiveCaptureOperation] {
        let account = self.account
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var result: [LocalArchiveCaptureOperation] = []
            transaction.operationLogEnumerateEntries(peerId: account, tag: OperationLogTags.LocalArchiveCapture) { entry in
                if let capture = entry.contents as? LocalArchiveCaptureOperation { result.append(capture) }
                return true
            }
            return result
        })
    }

    private func current(_ id: MessageId) throws -> Message? {
        try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
    }

    private func clearOperations(_ peer: PeerId) throws -> [CloudChatClearHistoryOperation] {
        try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var result: [CloudChatClearHistoryOperation] = []
            transaction.operationLogEnumerateEntries(peerId: peer, tag: OperationLogTags.CloudChatRemoveMessages) { entry in
                if let operation = entry.contents as? CloudChatClearHistoryOperation { result.append(operation) }
                return true
            }
            return result
        })
    }

    func testInteractiveClearPrivateAndLegacyGroupPersistsCaptureAndOutgoingSemantics() throws {
        for (offset, peer) in [self.user, self.group].enumerated() {
            let first = self.id(peer, Int32(offset * 10 + 1)), last = self.id(peer, Int32(offset * 10 + 2))
            try self.store([self.message(first), self.message(last, timestamp: 110)])
            _ = try self.localArchiveValue(_internal_clearHistoryInteractively(postbox: self.fixture.postbox, peerId: peer, threadId: nil, type: .forEveryone))
            XCTAssertNil(try self.current(first))
            if peer == self.user {
                // Preserve Telegram's canonical history-cleared service marker.
                XCTAssertTrue(try self.current(last)?.media.first is TelegramMediaAction)
                XCTAssertEqual(try self.current(last)?.text, "")
            } else { XCTAssertNil(try self.current(last)) }
            let operation = try XCTUnwrap(self.clearOperations(peer).first)
            XCTAssertEqual(operation.topMessageId, last)
            XCTAssertEqual(operation.type, .forEveryone)
            XCTAssertNil(operation.minTimestamp)
            XCTAssertEqual(Set(try self.captures().filter { $0.messageId.peerId == peer }.map { $0.messageId }), Set([first, last]))
        }
        let keys = try self.captures().map { $0.eventKey }
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().map { $0.eventKey }, keys)
    }

    func testDateAndThreadRangeCapturesOnlyRowsActuallyRemoved() throws {
        let ids = (1...5).map { self.id(self.group, Int32($0)) }
        try self.store([
            self.message(ids[0], timestamp: 90, thread: 7),
            self.message(ids[1], timestamp: 100, thread: 7),
            self.message(ids[2], timestamp: 110, thread: 7),
            self.message(ids[3], timestamp: 120, thread: 7),
            self.message(ids[4], timestamp: 105, thread: 8)
        ])
        _ = try self.localArchiveValue(_internal_clearHistoryInRangeInteractively(postbox: self.fixture.postbox, peerId: self.group, threadId: 7, minTimestamp: 100, maxTimestamp: 110, type: .forLocalPeer))
        XCTAssertEqual(Set(try self.captures().map { $0.messageId }), Set([ids[1], ids[2]]))
        for id in [ids[0], ids[3], ids[4]] { XCTAssertNotNil(try self.current(id)) }
        for id in [ids[1], ids[2]] { XCTAssertNil(try self.current(id)) }
        let operation = try XCTUnwrap(self.clearOperations(self.group).first)
        XCTAssertEqual(operation.threadId, 7)
        XCTAssertEqual(operation.minTimestamp, 100)
        XCTAssertEqual(operation.maxTimestamp, 110)
        XCTAssertEqual(operation.type, .forLocalPeer)
    }

    func testCommonHistoryHelperUsedByRemoveChatPreservesPrivateAndLegacyGroupBodies() throws {
        // RemovePeerChat invokes this exact common helper for private/legacy
        // groups; this does not exercise the authenticated remove-chat RPC.
        let postbox = self.fixture.postbox!
        for peer in [self.user, self.group] {
            let id = self.id(peer, peer == self.user ? 21 : 22)
            try self.store([self.message(id)])
            _ = try self.localArchiveValue(postbox.transaction { transaction in
                _internal_clearHistory(transaction: transaction, mediaBox: postbox.mediaBox, peerId: peer, threadId: nil, namespaces: .all)
            })
            XCTAssertNil(try self.current(id))
            XCTAssertEqual(try self.captures().filter { $0.messageId == id }.map { $0.text }, ["body \(id.id)"])
        }
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().count, 2)
    }

    func testByAuthorAndForwardAuthorUseTheirExactSelection() throws {
        let ids = (1...4).map { self.id(self.group, Int32($0)) }
        try self.store([
            self.message(ids[0], author: self.user),
            self.message(ids[1], author: self.other, forward: self.user),
            self.message(ids[2], author: self.other, forward: self.other),
            self.message(ids[3], author: self.other)
        ])
        let postbox = self.fixture.postbox!, group = self.group, user = self.user
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_deleteAllMessagesWithAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, authorId: user, namespace: Namespaces.Message.Cloud)
        })
        XCTAssertEqual(try self.captures().map { $0.messageId }, [ids[0]])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_deleteAllMessagesWithForwardAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, forwardAuthorId: user, namespace: Namespaces.Message.Cloud)
        })
        XCTAssertEqual(Set(try self.captures().map { $0.messageId }), Set([ids[0], ids[1]]))
        XCTAssertNil(try self.current(ids[0])); XCTAssertNil(try self.current(ids[1]))
        XCTAssertNotNil(try self.current(ids[2])); XCTAssertNotNil(try self.current(ids[3]))
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().count, 2)
    }

    func testBulkClearCountsOverflowAndStillRemovesEveryCanonicalMessage() throws {
        let ids = (1...260).map { self.id(self.group, Int32($0)) }
        let timed = self.id(self.group, 261)
        try self.store(ids.map { self.message($0) } + [self.message(timed, attributes: [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)])])
        _ = try self.localArchiveValue(_internal_clearHistoryInteractively(postbox: self.fixture.postbox, peerId: self.group, threadId: nil, type: .forLocalPeer))
        let captures = try self.captures()
        XCTAssertEqual(captures.count, 256)
        XCTAssertLessThanOrEqual(captures.reduce(0) { $0 + $1.encodedByteCount }, 8 * 1024 * 1024)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 4)
        let remaining = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0) } })
        XCTAssertTrue(remaining.isEmpty)
        XCTAssertNil(try self.current(timed))
        XCTAssertEqual(try self.clearOperations(self.group).count, 1)
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().count, 256)
    }

    func testBulkByteBudgetStopsCaptureWithoutPreventingHistoryRemoval() throws {
        let ids = (1...40).map { self.id(self.group, Int32($0)) }
        let text = String(repeating: "x", count: 256 * 1024)
        try self.store(ids.map { self.message($0, text: text) })
        _ = try self.localArchiveValue(_internal_clearHistoryInteractively(postbox: self.fixture.postbox, peerId: self.group, threadId: nil, type: .forLocalPeer))
        let captures = try self.captures()
        XCTAssertGreaterThan(captures.count, 0)
        XCTAssertLessThan(captures.count, ids.count)
        XCTAssertLessThanOrEqual(captures.reduce(0) { $0 + $1.encodedByteCount }, 8 * 1024 * 1024)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), Int64(ids.count - captures.count))
        XCTAssertTrue(try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0) } }).isEmpty)
    }

    func testBulkRemovalRespectsDisabledPeerSenderAndTimedExclusions() throws {
        let peer = self.group, user = self.user
        let postbox = self.fixture.postbox!
        let policies = [LocalArchiveSettings(), LocalArchiveSettings(enabled: true, excludedPeerIds: [peer.toInt64()]), LocalArchiveSettings(enabled: true, excludedSenderIds: [user.toInt64()]), LocalArchiveSettings(enabled: true)]
        for (index, policy) in policies.enumerated() {
            let id = self.id(peer, Int32(index + 1))
            let attributes: [MessageAttribute] = index == 3 ? [AutoclearTimeoutMessageAttribute(timeout: 1, countdownBeginTime: nil)] : []
            try self.store([self.message(id, attributes: attributes)])
            _ = try self.localArchiveValue(postbox.transaction { transaction in
                setLocalArchiveSettings(transaction: transaction, settings: policy)
                _internal_deleteAllMessagesWithAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: peer, authorId: user, namespace: Namespaces.Message.Cloud)
            })
            XCTAssertNil(try self.current(id))
            XCTAssertTrue(try self.captures().isEmpty)
            XCTAssertEqual(try self.localArchiveValue(postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 0)
        }
    }

    func testSecretAndScheduledHistoryRemovalDoesNotEnterArchive() throws {
        let secretId = self.id(self.secret, 1), scheduled = self.id(self.group, 2, namespace: Namespaces.Message.ScheduledCloud)
        try self.store([self.message(secretId), self.message(scheduled)])
        let postbox = self.fixture.postbox!, secret = self.secret
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_clearHistory(transaction: transaction, mediaBox: postbox.mediaBox, peerId: secret, threadId: nil, namespaces: .all)
        })
        _ = try self.localArchiveValue(_internal_clearHistoryInteractively(postbox: postbox, peerId: self.group, threadId: nil, type: .scheduledMessages))
        XCTAssertNil(try self.current(secretId)); XCTAssertNil(try self.current(scheduled))
        XCTAssertTrue(try self.captures().isEmpty)
    }

    func testMissingAuthorizedOwnerAndDefaultPostboxObserverNeverGuessCaptureAccount() throws {
        let postbox = self.fixture.postbox!, group = self.group
        let first = self.id(group, 1), second = self.id(group, 2)
        try self.store([self.message(first)])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            transaction.clearHistory(group, threadId: nil, minTimestamp: nil, maxTimestamp: nil, namespaces: .all, forEachMedia: nil)
        })
        XCTAssertTrue(try self.captures().isEmpty)
        try self.store([self.message(second)])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            transaction.setState(UnauthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, contents: .empty))
            _internal_clearHistory(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, threadId: nil, namespaces: .all)
        })
        XCTAssertNil(try self.current(first)); XCTAssertNil(try self.current(second))
        XCTAssertTrue(try self.captures().isEmpty)
    }

    func testModerationDefersOnlyEligibleMediaUntilRealEncryptedStoreCopiesIt() throws {
        let postbox = self.fixture.postbox!, group = self.group, user = self.user
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9220, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9220), representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        try self.store([self.message(self.id(group, 1), media: [photo]), self.message(self.id(group, 2), author: self.other, forward: user, media: [photo])])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_deleteAllMessagesWithAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, authorId: user, namespace: Namespaces.Message.Cloud)
        })
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_deleteAllMessagesWithForwardAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, forwardAuthorId: user, namespace: Namespaces.Message.Cloud)
        })
        postbox.mediaBox.dataQueue.sync {}
        XCTAssertEqual(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(resource.id).complete), bytes)
        let events = try self.captures()
        XCTAssertEqual(events.count, 2)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let archive = LocalArchiveCoordinator(postbox: postbox, accountPeerId: self.account, accountId: 122, baseDirectory: self.fixture.directory.appendingPathComponent("independent-archive"), storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
            ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: HistoryRemovalTestKey(), createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
                switch result {
                case let .success(store): _ = opened.swap(store); completion(.success(store))
                case let .failure(error): completion(.failure(error))
                }
            }
        }, protectedData: .single(true))
        defer {
            archive.dispose()
            _ = try? self.localArchiveValue(archive.status |> filter { $0 == .unavailable })
            if let store = opened.with({ $0 }) {
                let closed = expectation(description: "moderation archive closed")
                store.close { closed.fulfill() }
                wait(for: [closed], timeout: 15)
            }
        }
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .ready })
        XCTAssertTrue(try self.captures().isEmpty)
        XCTAssertEqual(Set(try self.localArchiveValue(archive.deletedMessages()).records.map { $0.messageId }), Set([1, 2]))
        let removed = expectation(description: "clear source photo")
        let removal = postbox.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { removed.fulfill() })
        wait(for: [removed], timeout: 15); removal.dispose()
        for event in events { XCTAssertEqual(try self.localArchiveValue(archive.mediaData(eventKey: event.eventKey, slot: 0)), bytes) }
    }

    func testForwardModerationStillUnlinksTimedMediaWithoutCapturingIt() throws {
        let postbox = self.fixture.postbox!, group = self.group, user = self.user
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9230, accessHash: 0, size: 3, fileReference: nil, fileName: nil)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9230), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "video/mp4", size: 3, attributes: [], alternativeRepresentations: [])
        postbox.mediaBox.storeResourceData(resource.id, data: Data([1, 2, 3]), synchronous: true)
        let id = self.id(group, 1)
        try self.store([self.message(id, author: self.other, forward: user, attributes: [AutoclearTimeoutMessageAttribute(timeout: 1, countdownBeginTime: nil)], media: [file])])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_deleteAllMessagesWithForwardAuthor(transaction: transaction, mediaBox: postbox.mediaBox, peerId: group, forwardAuthorId: user, namespace: Namespaces.Message.Cloud)
        })
        postbox.mediaBox.dataQueue.sync {}
        XCTAssertNil(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(resource.id).complete))
        XCTAssertNil(try self.current(id))
        XCTAssertTrue(try self.captures().isEmpty)
    }
}
