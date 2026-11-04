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

/// Calls the actual replayFinalState switch used by AccountStateManager. There
/// are deliberately no direct calls to the archive capture helpers in this file.
/// This validates local update replay, not MTProto delivery or logged-in UI behavior.
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

    private func finalState(_ operations: [AccountStateMutationOperation]) throws -> AccountFinalState {
        let peers: Set<PeerId> = [self.accountPeerId, self.chatPeerId]
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

    private func drainIntoIndependentArchive() throws -> LocalArchivePage {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("archive-real-replay-sink-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let coordinator = LocalArchiveCoordinator(postbox: self.fixture.postbox, accountPeerId: self.accountPeerId,
            accountId: 73, baseDirectory: directory, storageFactory: { directory, accountId, retention, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: LocalArchiveReplayKey(),
                                  retention: retention, callbackQueue: callbackQueue) { result in
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
        return page
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
}
