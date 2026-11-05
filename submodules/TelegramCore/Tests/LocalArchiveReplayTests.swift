// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import EncryptionProvider
import LocalMessageArchive
@testable import TelegramCore

/// Cloud edit/delete replay must not enter an authentication or secret-chat
/// cryptographic path. Every sentinel entry fails the test if that changes.
private final class LocalArchiveReplayEncryptionSentinel: NSObject, EncryptionProvider {
    func createBignumContext() -> MTBignumContext {
        preconditionFailure("cloud archive replay unexpectedly requested a bignum context")
    }
    func rsaEncrypt(withPublicKey publicKey: String, data: Data) -> Data? {
        XCTFail("cloud archive replay unexpectedly requested RSA encryption")
        return nil
    }
    func rsaEncryptPKCS1OAEP(withPublicKey publicKey: String, data: Data) -> Data? {
        XCTFail("cloud archive replay unexpectedly requested RSA-OAEP encryption")
        return nil
    }
    func parseRSAPublicKey(_ publicKey: String) -> MTRsaPublicKey {
        preconditionFailure("cloud archive replay unexpectedly requested an RSA key")
    }
    func macosRSAEncrypt(_ publicKey: String, data: Data) -> Data {
        XCTFail("cloud archive replay unexpectedly requested macOS RSA encryption")
        return Data()
    }
}

private struct LocalArchiveReplayKey: ArchiveKeyProvider {
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
        return Data(repeating: 0x49, count: 32)
    }
}

/// Calls the actual interactive-delete helper and replayFinalState switch used by
/// AccountStateManager. There are no direct archive-capture helper calls in this file.
/// This validates local mutation/replay, not MTProto delivery or logged-in UI behavior.
final class LocalArchiveReplayTests: XCTestCase {
    private static let logger: Logger = {
        let logger = Logger(rootPath: "", basePath: "")
        logger.logToFile = false
        logger.logToConsole = false
        return logger
    }()

    // replayFinalState always schedules an AccountManager transaction for language
    // updates, even when the batch contains none. Keep this blank test manager for
    // the process lifetime so its queue cannot race deletion of its files. It holds
    // no real accounts or credentials; the ephemeral simulator owns its directory.
    private static let accountManager = AccountManager<TelegramAccountManagerTypes>(
        basePath: NSTemporaryDirectory() + "archive-replay-manager-" + UUID().uuidString,
        isTemporary: true, isReadOnly: false, useCaches: false, removeDatabaseOnError: true,
        accessChallenge: AccountManagerAccessChallenge(prepare: { _ in }, resolve: { current, _ in current }, finishInitialization: {}))

    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        Logger.setSharedLogger(Self.logger)
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-real-replay")
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let accountPeerId = self.accountPeerId
        let state = self.updateState(pts: 10)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: accountPeerId, state: state, invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        // Wait for the replay's empty language transaction before closing Postbox.
        let _ = try self.localArchiveValue(Self.accountManager.transaction { _ in })
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ number: Int32 = 1) -> MessageId {
        return MessageId(peerId: self.chatPeerId, namespace: Namespaces.Message.Cloud, id: number)
    }

    private func updateState(pts: Int32) -> AuthorizedAccountState.State {
        return AuthorizedAccountState.State(pts: pts, qts: 2, date: 100, seq: 3)
    }

    private func store(_ messages: [StoreMessage]) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages(messages, location: .Random)
        })
    }

    private func pending() throws -> [LocalArchiveCaptureOperation] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId).compactMap { $0.operation }
        })
    }

    private func interactiveDelete(_ ids: [MessageId], type: InteractiveMessagesDeletionType = .forEveryone,
                                   deleteAllInGroup: Bool = false) throws {
        let postbox = self.fixture.postbox!
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            deleteMessagesInteractively(transaction: transaction, stateManager: nil, postbox: postbox,
                messageIds: ids, type: type, deleteAllInGroup: deleteAllInGroup, removeIfPossiblyDelivered: false)
        })
    }

    private func outgoingDeletes() throws -> [CloudChatRemoveMessagesOperation] {
        let chatPeerId = self.chatPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var operations: [CloudChatRemoveMessagesOperation] = []
            transaction.operationLogEnumerateEntries(peerId: chatPeerId, tag: OperationLogTags.CloudChatRemoveMessages) { entry in
                if let operation = entry.contents as? CloudChatRemoveMessagesOperation {
                    operations.append(operation)
                }
                return true
            }
            return operations
        })
    }

    private func finalState(_ operations: [AccountStateMutationOperation], including additionalPeers: Set<PeerId> = []) throws -> AccountFinalState {
        let peers: Set<PeerId> = Set([self.accountPeerId, self.chatPeerId]).union(additionalPeers)
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var state = initialStateWithPeerIds(transaction, peerIds: peers, activeChannelIds: [],
                referencedReplyMessageIds: ReferencedReplyMessageIds(), referencedGeneralMessageIds: [],
                peerIdsRequiringLocalChatState: [], locallyGeneratedMessageTimestamps: [:], storedStories: [:])
            for operation in operations {
                state.addOperation(operation)
            }
            return AccountFinalState(state: state, shouldPoll: false, incomplete: false,
                missingUpdatesFromChannels: [], discard: false)
        })
    }

    private func replay(_ operations: [AccountStateMutationOperation]) throws -> AccountReplayedFinalState? {
        return try self.replay(self.finalState(operations))
    }

    private func replay(_ finalState: AccountFinalState) throws -> AccountReplayedFinalState? {
        let postbox = self.fixture.postbox!
        let accountPeerId = self.accountPeerId
        let accountManager = Self.accountManager
        let encryption = LocalArchiveReplayEncryptionSentinel()
        let auxiliary = AccountAuxiliaryMethods(fetchResource: { _, _, _, _ in
            XCTFail("text-only replay unexpectedly fetched media")
            return nil
        }, fetchResourceMediaReferenceHash: { _ in
            XCTFail("text-only replay unexpectedly fetched a media reference")
            return .single(nil)
        }, prepareSecretThumbnailData: { _ in
            XCTFail("cloud replay unexpectedly prepared a secret thumbnail")
            return nil
        }, backgroundUpload: { _, _, _ in
            XCTFail("local archive replay unexpectedly uploaded media")
            return .single(nil)
        })
        return try self.localArchiveValue(postbox.transaction { transaction in
            replayFinalState(accountManager: accountManager, postbox: postbox, accountPeerId: accountPeerId,
                mediaBox: postbox.mediaBox, encryptionProvider: encryption, transaction: transaction,
                auxiliaryMethods: auxiliary, finalState: finalState, removePossiblyDeliveredMessagesUniqueIds: [:],
                ignoreDate: false, skipVerification: false)
        })
    }

    private func drainIntoIndependentArchive(clearing resources: [MediaResourceId] = [], expectedMedia: [String: Data] = [:]) throws -> LocalArchivePage {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("archive-real-replay-sink-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let coordinator = LocalArchiveCoordinator(postbox: self.fixture.postbox, accountPeerId: self.accountPeerId,
            accountId: 73, baseDirectory: directory, storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: LocalArchiveReplayKey(),
                                  createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue) { result in
                    switch result {
                    case let .success(store):
                        let _ = opened.swap(store)
                        completion(.success(store))
                    case let .failure(error):
                        completion(.failure(error))
                    }
                }
            }, protectedData: .single(true))
        defer {
            coordinator.dispose()
            let _ = try? self.localArchiveValue(coordinator.status |> filter { $0 == .unavailable })
            if let store = opened.with({ $0 }) {
                let closed = expectation(description: "real replay archive closed")
                store.close { closed.fulfill() }
                wait(for: [closed], timeout: 15)
            }
            try? FileManager.default.removeItem(at: directory)
        }
        let _ = try self.localArchiveValue(coordinator.status |> filter { $0 == .ready })
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertTrue(try self.pending().isEmpty)
        if !resources.isEmpty {
            let removed = expectation(description: "clear Telegram cache after real deletion replay")
            let removal = self.fixture.postbox.mediaBox.removeCachedResources(resources, force: true).start(completed: { removed.fulfill() })
            wait(for: [removed], timeout: 15)
            removal.dispose()
            for resource in resources {
                XCTAssertFalse(FileManager.default.fileExists(atPath: self.fixture.postbox.mediaBox.storePathsForId(resource).complete))
            }
        }
        if !expectedMedia.isEmpty {
            let store = try XCTUnwrap(opened.with { $0 })
            var records: [LocalArchivedMessage] = []
            for record in page.records {
                let timeline = try self.localArchiveValue(coordinator.timeline(peerId: record.peerId, namespace: record.namespace, messageId: record.messageId))
                records.append(contentsOf: timeline.records)
            }
            for record in records {
                guard let bytes = expectedMedia[record.eventKey] else { continue }
                XCTAssertEqual(record.storedMediaCount, 1)
                let read = expectation(description: "read independent bytes after real deletion replay")
                var value: Result<ArchivedMedia?, ArchiveError>?
                store.media(eventKey: record.eventKey, slot: 0) { value = $0; read.fulfill() }
                wait(for: [read], timeout: 15)
                XCTAssertEqual(try XCTUnwrap(value).get()?.data, bytes)
            }
            XCTAssertEqual(Set(records.map { $0.eventKey }), Set(expectedMedia.keys))
        }
        return page
    }

    private func installRangeTestChannel() throws -> PeerId {
        let id = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(8200))
        let channel = TelegramChannel(id: id, accessHash: .personal(456), title: "Archive range fixture", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: .member,
            info: .broadcast(TelegramChannelBroadcastInfo(flags: [])), flags: [], restrictionInfo: nil,
            adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
            emojiStatus: nil, approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil,
            sendPaidMessageStars: nil, linkedMonoforumId: nil)
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([channel], update: { _, peer in peer })
        })
        return id
    }

    func testChannelRangeRemovalPreservesCachedPhotoAcrossRestartAndCacheClear() throws {
        let channel = try self.installRangeTestChannel()
        let id = MessageId(peerId: channel, namespace: Namespaces.Message.Cloud, id: 10)
        let later = MessageId(peerId: channel, namespace: Namespaces.Message.Cloud, id: 20)
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 8200, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 8200), representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        try self.store([localArchiveTestMessage(id, text: "range photo", media: [photo]), localArchiveTestMessage(later, text: "outside range")])
        let state = try self.finalState([.UpdateMinAvailableMessage(id)], including: [channel])
        _ = try XCTUnwrap(self.replay(state))
        self.fixture.postbox.mediaBox.dataQueue.sync {}
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertNotNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(later) }))
        XCTAssertEqual(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete), bytes)
        try self.fixture.reopen()
        let captures = try self.pending()
        XCTAssertEqual(captures.map { $0.messageId }, [id])
        XCTAssertEqual(captures.first?.text, "range photo")
        let key = try XCTUnwrap(captures.first?.eventKey)
        let page = try self.drainIntoIndependentArchive(clearing: [resource.id], expectedMedia: [key: bytes])
        XCTAssertEqual(page.records.first?.text, "range photo")
    }

    func testChannelRangeRemovalKeepsTimedMediaExclusionAndNormalCacheRemoval() throws {
        let channel = try self.installRangeTestChannel()
        let id = MessageId(peerId: channel, namespace: Namespaces.Message.Cloud, id: 10)
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 8201, accessHash: 0, size: 3, fileReference: nil, fileName: nil)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 8201), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "video/mp4", size: 3, attributes: [], alternativeRepresentations: [])
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: Data([1, 2, 3]), synchronous: true)
        try self.store([localArchiveTestMessage(id, attributes: [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)], media: [file])])
        _ = try XCTUnwrap(self.replay(self.finalState([.UpdateMinAvailableMessage(id)], including: [channel])))
        self.fixture.postbox.mediaBox.dataQueue.sync {}
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete))
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testChannelRangeRemovalBoundsOutboxAndRecordsOverflowWithoutBlockingDeletion() throws {
        let channel = try self.installRangeTestChannel()
        let ids = (1...260).map { MessageId(peerId: channel, namespace: Namespaces.Message.Cloud, id: Int32($0)) }
        try self.store(ids.map { localArchiveTestMessage($0, text: "range item") })
        _ = try XCTUnwrap(self.replay(self.finalState([.UpdateMinAvailableMessage(try XCTUnwrap(ids.last))], including: [channel])))
        // The consumer reads bounded pages, not the entire durable outbox.
        XCTAssertEqual(try self.pending().count, 64)
        let accountPeerId = self.accountPeerId
        let queuedCount = try self.localArchiveValue(self.fixture.postbox.transaction { transaction -> Int in
            var count = 0
            transaction.operationLogEnumerateEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { entry in
                if entry.contents is LocalArchiveCaptureOperation {
                    count += 1
                }
                return true
            }
            return count
        })
        XCTAssertEqual(queuedCount, 256)
        let remaining = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0) } })
        XCTAssertTrue(remaining.isEmpty)
        let settings = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertEqual(settings.droppedCaptures, 4)
    }

    func testChannelRangeRemovalRespectsDisabledAndExcludedChatSettings() throws {
        let channel = try self.installRangeTestChannel()
        for excluded in [false, true] {
            _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: excluded, excludedPeerIds: excluded ? [channel.toInt64()] : []))
            })
            let id = MessageId(peerId: channel, namespace: Namespaces.Message.Cloud, id: excluded ? 20 : 10)
            try self.store([localArchiveTestMessage(id)])
            _ = try XCTUnwrap(self.replay(self.finalState([.UpdateMinAvailableMessage(id)], including: [channel])))
            XCTAssertTrue(try self.pending().isEmpty)
            XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        }
    }

    func testGlobalDeleteDefersEagerMediaRemovalUntilArchiveCanCopy() throws {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 501, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        let file = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 501), representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let id = self.id(501)
        try self.store([localArchiveTestMessage(id, media: [file])])
        _ = try XCTUnwrap(self.replay([.DeleteMessagesWithGlobalIds([id.id])]))
        // Drain MediaBox's queue: this fails if replay already enqueued force-unlink.
        self.fixture.postbox.mediaBox.dataQueue.sync {}
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete), bytes)
        let eventKey = try XCTUnwrap(self.pending().first?.eventKey)
        let page = try self.drainIntoIndependentArchive(clearing: [resource.id], expectedMedia: [eventKey: bytes])
        XCTAssertEqual(page.records.count, 1)
    }

    func testMediaReplacementThenGlobalDeletionRetainsBothVersionBytes() throws {
        let firstBytes = Data([3, 5, 7])
        let lastBytes = Data([9, 11, 13])
        let resources = [601, 602].map { value in
            CloudDocumentMediaResource(datacenterId: 1, fileId: Int64(value), accessHash: 0, size: 3, fileReference: nil, fileName: nil)
        }
        let files = resources.map { resource in
            TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: resource.fileId), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/pdf", size: 3, attributes: [], alternativeRepresentations: [])
        }
        self.fixture.postbox.mediaBox.storeResourceData(resources[0].id, data: firstBytes, synchronous: true)
        self.fixture.postbox.mediaBox.storeResourceData(resources[1].id, data: lastBytes, synchronous: true)
        let id = self.id(601)
        try self.store([localArchiveTestMessage(id, text: "original document", media: [files[0]])])
        _ = try XCTUnwrap(self.replay([
            .EditMessage(id, localArchiveTestMessage(id, text: "replacement document", attributes: [EditedMessageAttribute(date: 200, isHidden: false)], media: [files[1]])),
            .DeleteMessagesWithGlobalIds([id.id])
        ]))
        self.fixture.postbox.mediaBox.dataQueue.sync {}
        let events = try self.pending()
        XCTAssertEqual(events.map { $0.kindRawValue }, [ArchiveEventKind.edited.rawValue, ArchiveEventKind.deleted.rawValue])
        XCTAssertEqual(events.count, 2)
        let firstKey = try XCTUnwrap(events.first?.eventKey)
        let lastKey = try XCTUnwrap(events.last?.eventKey)
        _ = try self.drainIntoIndependentArchive(clearing: resources.map { $0.id }, expectedMedia: [firstKey: firstBytes, lastKey: lastBytes])
    }

    func testGlobalDeleteStillRemovesExcludedAndExpiringMedia() throws {
        for (offset, disabled) in [false, true].enumerated() {
            let bytes = Data([1, 3, 5])
            let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: Int64(510 + offset), accessHash: 0, size: Int64(bytes.count), fileReference: nil, fileName: nil)
            let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: Int64(510 + offset)), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "video/mp4", size: Int64(bytes.count), attributes: [], alternativeRepresentations: [])
            self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
            let id = self.id(Int32(510 + offset))
            let attributes: [MessageAttribute] = disabled ? [] : [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)]
            try self.store([localArchiveTestMessage(id, attributes: attributes, media: [file])])
            if disabled {
                _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                    setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
                })
            }
            _ = try XCTUnwrap(self.replay([.DeleteMessagesWithGlobalIds([id.id])]))
            self.fixture.postbox.mediaBox.dataQueue.sync {}
            XCTAssertNil(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete))
            XCTAssertTrue(try self.pending().isEmpty)
        }
    }

    func testExplicitDeleteReplayCapturesBeforeRemovalAndPreservesStateProgress() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "deleted by replay")])
        let beganAt = Int64(Date().timeIntervalSince1970)
        let result = try XCTUnwrap(self.replay([.DeleteMessages([id]), .UpdateState(self.updateState(pts: 11))]))
        XCTAssertEqual(result.deletedMessageIds, [.messageId(id)])
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        let captures = try self.pending()
        XCTAssertEqual(captures.count, 1)
        let capture = try XCTUnwrap(captures.first)
        XCTAssertEqual(capture.text, "deleted by replay")
        XCTAssertEqual(capture.kindRawValue, ArchiveEventKind.deleted.rawValue)
        XCTAssertGreaterThanOrEqual(capture.observedAt, beganAt)
        XCTAssertLessThanOrEqual(capture.observedAt, Int64(Date().timeIntervalSince1970))
        let state = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getState() as? AuthorizedAccountState })
        XCTAssertEqual(state?.state, self.updateState(pts: 11))
        XCTAssertEqual(state?.peerId, self.accountPeerId)
        XCTAssertEqual(state?.masterDatacenterId, 1)
        XCTAssertEqual(state?.isTestingEnvironment, true)
        let archived = try self.drainIntoIndependentArchive()
        XCTAssertFalse(archived.unavailable)
        XCTAssertEqual(archived.records.map { $0.text }, ["deleted by replay"])
        XCTAssertEqual(archived.records.map { $0.messageId }, [id.id])
    }

    func testGlobalDeleteReplayResolvesStoredMessageAndDoesNotRecoverMissingContent() throws {
        let id = self.id(42)
        try self.store([localArchiveTestMessage(id, text: "global replay")])
        let result = try XCTUnwrap(self.replay([.DeleteMessagesWithGlobalIds([42, 43])]))
        XCTAssertEqual(result.deletedMessageIds, [.global(42), .global(43)])
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try self.pending().map { $0.messageId }, [id])
        XCTAssertEqual(try self.pending().map { $0.text }, ["global replay"])
        // Re-delivery sees no original message and must not manufacture a second snapshot.
        let _ = try XCTUnwrap(self.replay([.DeleteMessagesWithGlobalIds([42, 43])]))
        XCTAssertEqual(try self.pending().count, 1)
    }

    func testEditReplayCapturesPriorVersionAndPreservesCanonicalServerMerge() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "original", attributes: [
            EditedMessageAttribute(date: 120, isHidden: false),
            TranslationMessageAttribute(text: "translation", entities: [], toLang: "de")
        ])])
        let updated = localArchiveTestMessage(id, text: "replacement", attributes: [EditedMessageAttribute(date: 180, isHidden: false)])
        let _ = try XCTUnwrap(self.replay([.EditMessage(id, updated)]))
        let stored = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(stored.text, "replacement")
        XCTAssertFalse(stored.attributes.contains { $0 is TranslationMessageAttribute }, "normal server merge must still discard obsolete translation")
        let capture = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(capture.text, "original")
        XCTAssertEqual(capture.serverEditAt, 120)
        XCTAssertEqual(capture.kindRawValue, ArchiveEventKind.edited.rawValue)
        let _ = try XCTUnwrap(self.replay([.EditMessage(id, updated)]))
        XCTAssertEqual(try self.pending().count, 1)
    }

    func testEditThenDeleteInOneReplayPreservesBothVersionsAfterReopen() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "before edit")])
        let updated = localArchiveTestMessage(id, text: "after edit", attributes: [EditedMessageAttribute(date: 180, isHidden: false)])
        let _ = try XCTUnwrap(self.replay([.EditMessage(id, updated), .DeleteMessages([id])]))
        let before = try self.pending()
        XCTAssertEqual(before.map { $0.text }, ["before edit", "after edit"])
        XCTAssertEqual(before.map { $0.kindRawValue }, [ArchiveEventKind.edited.rawValue, ArchiveEventKind.deleted.rawValue])
        XCTAssertEqual(before.map { $0.serverEditAt }, [nil, 180])
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().map { $0.eventKey }, before.map { $0.eventKey })
        XCTAssertEqual(try self.pending().map { $0.text }, before.map { $0.text })
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testAddedAndDeletedInSameReplayIsCaptured() throws {
        let id = self.id()
        let _ = try XCTUnwrap(self.replay([
            .AddMessages([localArchiveTestMessage(id, text: "received and deleted in one batch")], .Random),
            .DeleteMessages([id])
        ]))
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try self.pending().map { $0.text }, ["received and deleted in one batch"])
    }

    func testDisabledArchiveDoesNotAlterCanonicalReplayOrAccountState() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id)])
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
        })
        let result = try XCTUnwrap(self.replay([.DeleteMessages([id]), .UpdateState(self.updateState(pts: 11))]))
        XCTAssertEqual(result.deletedMessageIds, [.messageId(id)])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { ($0.getState() as? AuthorizedAccountState)?.state }), self.updateState(pts: 11))
    }

    func testRejectedStaleReplayNeitherDeletesNorCaptures() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "still current")])
        let prepared = try self.finalState([.DeleteMessages([id]), .UpdateState(self.updateState(pts: 11))])
        let newerState = self.updateState(pts: 99)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            if let current = transaction.getState() as? AuthorizedAccountState {
                transaction.setState(current.changedState(newerState))
            }
        })
        XCTAssertNil(try self.replay(prepared))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "still current")
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { ($0.getState() as? AuthorizedAccountState)?.state }), newerState)
    }

    func testInteractiveDeleteForLocalPeerPersistsCaptureAndCanonicalRemovalTogether() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "locally deleted", attributes: [EditedMessageAttribute(date: 120, isHidden: false)])])
        let postbox = self.fixture.postbox!
        let accountPeerId = self.accountPeerId
        let result = try self.localArchiveValue(postbox.transaction { transaction -> (Bool, LocalArchiveCaptureOperation?) in
            deleteMessagesInteractively(transaction: transaction, stateManager: nil, postbox: postbox,
                messageIds: [id], type: .forLocalPeer, removeIfPossiblyDelivered: false)
            return (transaction.getMessage(id) == nil,
                    localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId).first?.operation)
        })
        XCTAssertTrue(result.0)
        let capture = try XCTUnwrap(result.1)
        XCTAssertEqual(capture.text, "locally deleted")
        XCTAssertEqual(capture.serverEditAt, 120)
        XCTAssertEqual(capture.kindRawValue, ArchiveEventKind.deleted.rawValue)
        try self.fixture.reopen()
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try self.pending().map { $0.eventKey }, [capture.eventKey])
        let outgoing = try self.outgoingDeletes()
        XCTAssertEqual(outgoing.count, 1)
        XCTAssertEqual(outgoing.first?.messageIds, [id])
        XCTAssertEqual(outgoing.first?.type, .forLocalPeer)
        XCTAssertNil(outgoing.first?.threadId)
    }

    func testInteractiveDeleteFollowedByServerReplayDoesNotCaptureTwice() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id, text: "deleted for everyone")])
        try self.interactiveDelete([id])
        let original = try XCTUnwrap(self.pending().first)
        let _ = try XCTUnwrap(self.replay([.DeleteMessages([id]), .DeleteMessagesWithGlobalIds([id.id])]))
        XCTAssertEqual(try self.pending().map { $0.eventKey }, [original.eventKey])
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        let outgoing = try self.outgoingDeletes()
        XCTAssertEqual(outgoing.count, 1, "server replay must not enqueue another outgoing deletion")
        XCTAssertEqual(outgoing.first?.messageIds, [id])
        XCTAssertEqual(outgoing.first?.type, .forEveryone)
        let page = try self.drainIntoIndependentArchive()
        XCTAssertEqual(page.records.map { $0.text }, ["deleted for everyone"])
    }

    func testInteractiveDeleteWithDisabledArchiveKeepsOutgoingOperation() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id)])
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
        })
        try self.interactiveDelete([id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        let outgoing = try self.outgoingDeletes()
        XCTAssertEqual(outgoing.count, 1)
        XCTAssertEqual(outgoing.first?.messageIds, [id])
        XCTAssertEqual(outgoing.first?.type, .forEveryone)
    }

    func testInteractiveDeleteHonorsPeerSenderAndExpiryExclusions() throws {
        let excludedByPeer = self.id(1)
        let excludedBySender = self.id(2)
        let expiring = self.id(3)
        let allowed = self.id(4)
        let chatPeerId = self.chatPeerId
        try self.store([
            localArchiveTestMessage(excludedByPeer),
            localArchiveTestMessage(excludedBySender),
            localArchiveTestMessage(expiring, authorId: self.accountPeerId, attributes: [AutoremoveTimeoutMessageAttribute(timeout: 3600, countdownBeginTime: nil)]),
            localArchiveTestMessage(allowed, text: "eligible", authorId: self.accountPeerId)
        ])
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction,
                settings: LocalArchiveSettings(enabled: true, excludedPeerIds: [chatPeerId.toInt64()]))
        })
        try self.interactiveDelete([excludedByPeer])
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction,
                settings: LocalArchiveSettings(enabled: true, excludedSenderIds: [chatPeerId.toInt64()]))
        })
        try self.interactiveDelete([excludedBySender, expiring, allowed])
        XCTAssertEqual(try self.pending().map { $0.messageId }, [allowed])
        for id in [excludedByPeer, excludedBySender, expiring, allowed] {
            XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        }
        let outgoing = try self.outgoingDeletes()
        XCTAssertEqual(outgoing.count, 2)
        XCTAssertEqual(try XCTUnwrap(outgoing.first).messageIds, [excludedByPeer])
        XCTAssertEqual(try XCTUnwrap(outgoing.dropFirst().first).messageIds, [excludedBySender, expiring, allowed])
        XCTAssertTrue(outgoing.allSatisfy { $0.type == .forEveryone })
    }

    func testInteractiveDeleteWithoutAuthorizedAccountDoesNotGuessArchiveOwner() throws {
        let id = self.id()
        try self.store([localArchiveTestMessage(id)])
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setState(UnauthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, contents: .empty))
        })
        try self.interactiveDelete([id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(try self.outgoingDeletes().count, 1)
    }

    func testInteractiveDeleteCapturesExpandedMessageGroup() throws {
        let first = self.id(1)
        let second = self.id(2)
        let messages = [first, second].map { id -> StoreMessage in
            let media = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: Int64(id.id)),
                representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
            return StoreMessage(id: id, customStableId: nil, globallyUniqueId: nil, groupingKey: 77,
                threadId: nil, timestamp: 100, flags: [.Incoming], tags: [], globalTags: [], localTags: [],
                forwardInfo: nil, authorId: id.peerId, text: "caption \(id.id)", attributes: [], media: [media])
        }
        try self.store(messages)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessageGroup(first)?.count }), 2)
        try self.interactiveDelete([first], deleteAllInGroup: true)
        XCTAssertEqual(Set(try self.pending().map { $0.messageId }), Set([first, second]))
        for operation in try self.pending() {
            let metadata = try XCTUnwrap(localArchiveDecodedMediaMetadata(operation.entities))
            XCTAssertEqual(metadata.map(\.kind), [.photo])
            XCTAssertEqual(metadata.map(\.id), [Int64(operation.messageId.id)])
            XCTAssertFalse(try XCTUnwrap(metadata.first).bytesPreserved)
        }
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(first) }))
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(second) }))
        let outgoing = try self.outgoingDeletes()
        XCTAssertEqual(outgoing.count, 1)
        let operation = try XCTUnwrap(outgoing.first)
        XCTAssertEqual(Set(operation.messageIds), Set([first, second]))
        XCTAssertEqual(operation.type, .forEveryone)
    }

    func testAcknowledgedOwnEditFollowedByServerReplayKeepsOnePreviousVersion() throws {
        let id = self.id()
        let original = localArchiveTestMessage(id, text: "sent original", authorId: self.accountPeerId,
            attributes: [EditedMessageAttribute(date: 120, isHidden: false)]).withUpdatedFlags([])
        let replacement = localArchiveTestMessage(id, text: "sent replacement", authorId: self.accountPeerId,
            attributes: [EditedMessageAttribute(date: 180, isHidden: false)]).withUpdatedFlags([])
        try self.store([original])
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            applyAcknowledgedEditMessage(transaction: transaction, accountPeerId: accountPeerId, message: replacement)
        })
        let capture = try XCTUnwrap(self.pending().first)
        XCTAssertEqual(capture.text, "sent original")
        XCTAssertEqual(capture.serverEditAt, 120)
        XCTAssertEqual(capture.kindRawValue, ArchiveEventKind.edited.rawValue)
        try self.fixture.reopen()
        let _ = try XCTUnwrap(self.replay([.EditMessage(id, replacement)]))
        XCTAssertEqual(try self.pending().map { $0.eventKey }, [capture.eventKey])
        let stored = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        XCTAssertEqual(stored.text, "sent replacement")
        XCTAssertFalse(stored.flags.contains(.Incoming))
    }

    func testStoryPrivacyPreservesIncomingServerReadReplayWithoutOutgoingEcho() throws {
        let peerId = self.chatPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setPeerStoryState(peerId: peerId, state: Stories.PeerState(maxReadId: 3).postboxRepresentation)
        })
        XCTAssertTrue(try self.localArchiveValue(LocalStoryPrivacyContext(postbox: self.fixture.postbox).setSuppressed(true)))
        let policy = try self.localArchiveValue(LocalStoryPrivacyContext(postbox: self.fixture.postbox).settings)
        for serverMax in [Int32(12), 8] {
            let result = try XCTUnwrap(self.replay([.UpdateReadStories(peerId: peerId, maxId: serverMax)]))
            XCTAssertEqual(result.storyUpdates.count, 1)
            let update = try XCTUnwrap(result.storyUpdates.first)
            guard case let .read(updatedPeer, updatedMax) = update else {
                return XCTFail("expected the genuine server read update")
            }
            XCTAssertEqual(updatedPeer, peerId)
            XCTAssertEqual(updatedMax, serverMax)
        }
        try self.fixture.reopen()
        let persisted = try self.localArchiveValue(self.fixture.postbox.transaction { transaction -> (Int32?, Int) in
            var outgoingCount = 0
            transaction.operationLogEnumerateEntries(peerId: peerId, tag: OperationLogTags.SynchronizeViewStories) { _ in
                outgoingCount += 1
                return true
            }
            return (transaction.getPeerStoryState(peerId: peerId)?.entry.get(Stories.PeerState.self)?.maxReadId, outgoingCount)
        })
        XCTAssertEqual(persisted.0, 12)
        XCTAssertEqual(persisted.1, 0)
        XCTAssertEqual(try self.localArchiveValue(LocalStoryPrivacyContext(postbox: self.fixture.postbox).settings), policy)
        XCTAssertTrue(try self.pending().isEmpty)
    }
}
