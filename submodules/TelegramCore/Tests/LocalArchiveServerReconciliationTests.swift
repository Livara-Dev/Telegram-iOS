// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

private struct ReconciliationArchiveTestKey: ArchiveKeyProvider {
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data { Data(repeating: 0x61, count: 32) }
}

/// Production server-upsert/validation helpers on persistent encrypted Postbox.
/// RPC responses are supplied locally; these are not authenticated network tests.
final class LocalArchiveServerReconciliationTests: XCTestCase {
    private let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let user = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9401))
    private let channel = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(9402))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-server-reconciliation")
        let owner = self.owner
        let channel = TelegramChannel(id: self.channel, accessHash: .personal(456), title: "Reconciliation fixture", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: .member,
            info: .group(TelegramChannelGroupInfo(flags: [])), flags: [], restrictionInfo: nil,
            adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
            emojiStatus: nil, approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil,
            sendPaidMessageStars: nil, linkedMonoforumId: nil)
        let peers: [Peer] = [localArchiveTestUser(owner), localArchiveTestUser(self.user), channel]
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

    private func id(_ value: Int32, namespace: Int32 = Namespaces.Message.Cloud) -> MessageId {
        MessageId(peerId: self.channel, namespace: namespace, id: value)
    }

    private func message(_ id: MessageId, _ text: String, timestamp: Int32 = 100,
                         uniqueId: Int64? = nil, flags: StoreMessageFlags = [.Incoming],
                         localTags: LocalMessageTags = [], attributes: [MessageAttribute] = [], media: [Media] = []) -> StoreMessage {
        StoreMessage(id: id, customStableId: nil, globallyUniqueId: uniqueId, groupingKey: nil, threadId: 7,
            timestamp: timestamp, flags: flags, tags: [], globalTags: [], localTags: localTags,
            forwardInfo: nil, authorId: self.user, text: text, attributes: attributes, media: media)
    }

    @discardableResult
    private func upsert(_ messages: [StoreMessage]) throws -> [Int64: MessageId] {
        try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            addMessagesWithLocalArchive(transaction: transaction, messages: messages, location: .Random)
        })
    }

    private func current(_ id: MessageId) throws -> Message? {
        try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
    }

    private func pending() throws -> [LocalArchiveCaptureOperation] {
        let owner = self.owner
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var result: [LocalArchiveCaptureOperation] = []
            transaction.operationLogEnumerateEntries(peerId: owner, tag: OperationLogTags.LocalArchiveCapture) { entry in
                if let operation = entry.contents as? LocalArchiveCaptureOperation { result.append(operation) }
                return true
            }
            return result
        })
    }

    private func withDrainedArchive(_ body: (LocalArchiveCoordinator) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("archive-reconciliation-sink-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let archive = LocalArchiveCoordinator(postbox: self.fixture.postbox, accountPeerId: self.owner, accountId: 94,
            baseDirectory: directory, storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: ReconciliationArchiveTestKey(),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue) { result in
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
                let closed = expectation(description: "close reconciliation sink")
                store.close { closed.fulfill() }
                wait(for: [closed], timeout: 15)
            }
            try? FileManager.default.removeItem(at: directory)
        }
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .ready })
        XCTAssertTrue(try self.pending().isEmpty)
        try body(archive)
    }

    func testServerUpsertArchivesOnlyReplacedBodyAndPersistsAfterReopen() throws {
        let id = self.id(1)
        try self.upsert([self.message(id, "original")])
        XCTAssertTrue(try self.pending().isEmpty)
        try self.upsert([self.message(id, "edited", attributes: [EditedMessageAttribute(date: 190, isHidden: false)])])
        let key = try XCTUnwrap(self.pending().first?.eventKey)
        try self.upsert([self.message(id, "edited", attributes: [EditedMessageAttribute(date: 210, isHidden: false)])])
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().map { $0.text }, ["original"])
        XCTAssertEqual(try self.pending().first?.eventKey, key)
        XCTAssertEqual(try self.current(id)?.text, "edited")
    }

    func testDuplicateIdsUseSequentialBodiesAndPreserveUniqueIdReturnMap() throws {
        let id = self.id(1), newId = self.id(2)
        try self.upsert([self.message(id, "A")])
        let mapping = try self.upsert([self.message(id, "B", uniqueId: 10), self.message(id, "C", uniqueId: 11),
            self.message(id, "C", uniqueId: 12), self.message(newId, "new", uniqueId: 13)])
        XCTAssertEqual(mapping, [10: id, 11: id, 12: id, 13: newId])
        XCTAssertEqual(try self.pending().map { $0.text }, ["A", "B"])
        XCTAssertEqual(try self.current(id)?.text, "C")
        XCTAssertEqual(try self.current(newId)?.text, "new")
    }

    func testTimestampChangingDuplicateIdsCaptureBeforeEachRemoveInsertPair() throws {
        let id = self.id(1)
        try self.upsert([self.message(id, "A", timestamp: 100)])
        try self.upsert([self.message(id, "B", timestamp: 110), self.message(id, "C", timestamp: 120),
            self.message(id, "D", timestamp: 120), self.message(id, "D", timestamp: 130)])
        XCTAssertEqual(try self.pending().map { $0.text }, ["A", "B", "C"])
        XCTAssertEqual(try self.pending().map { $0.sentAt }, [100, 110, 120])
        XCTAssertEqual(try self.current(id)?.timestamp, 130)
        XCTAssertEqual(try self.current(id)?.text, "D")
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().count, 3)
    }

    func testObserverReceivesActualMergedAttributesAndPreservedLocalTags() throws {
        let id = self.id(1)
        let original = self.message(id, "A", localTags: [.OutgoingLiveLocation], attributes: [DerivedDataMessageAttribute(data: [:])])
        let sameIndex = self.message(id, "B"), moved = self.message(id, "C", timestamp: 110)
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([original], location: .Random)
            var calls = 0
            _ = transaction.addMessages([sameIndex, moved], location: .Random, beforeExistingUpdate: { previous, updated in
                calls += 1
                if calls == 1 {
                    XCTAssertEqual(previous.text, "A")
                    XCTAssertEqual(updated.text, "B")
                    XCTAssertTrue(updated.attributes.contains { $0 is DerivedDataMessageAttribute })
                    XCTAssertTrue(updated.localTags.contains(.OutgoingLiveLocation))
                } else {
                    XCTAssertEqual(previous.text, "B")
                    XCTAssertEqual(updated.text, "C")
                    XCTAssertFalse(updated.attributes.contains { $0 is DerivedDataMessageAttribute })
                    XCTAssertFalse(updated.localTags.contains(.OutgoingLiveLocation))
                }
            })
            XCTAssertEqual(calls, 2)
        })
        XCTAssertTrue(try self.pending().isEmpty, "a Postbox observer alone does not install archive policy")
        XCTAssertFalse(try XCTUnwrap(self.current(id)).localTags.contains(.OutgoingLiveLocation))
    }

    func testDefaultObserverDisabledAndPurgeKeepCanonicalUpdatesWithoutCapturing() throws {
        let id = self.id(1), a = self.message(self.id(1), "A"), b = self.message(self.id(1), "B")
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([a, b], location: .Random)
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings())
        })
        try self.upsert([self.message(id, "C")])
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = LocalArchiveSettings(enabled: true); settings.pendingPurge = true
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        try self.upsert([self.message(id, "D")])
        XCTAssertEqual(try self.current(id)?.text, "D")
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testMissingAuthorizedOwnerDoesNotGuessAnArchiveAccount() throws {
        let id = self.id(1)
        try self.upsert([self.message(id, "A")])
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setState(UnauthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, contents: .empty))
        })
        try self.upsert([self.message(id, "B")])
        XCTAssertEqual(try self.current(id)?.text, "B")
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testOldAndIncomingExpiryProtectionAndScheduledBodiesStayExcluded() throws {
        let timed = AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)
        let a = self.id(1), b = self.id(2), c = self.id(3), d = self.id(4), scheduled = self.id(5, namespace: Namespaces.Message.ScheduledCloud)
        try self.upsert([self.message(a, "A", attributes: [timed]), self.message(b, "B"),
            self.message(c, "C", flags: [.Incoming, .CopyProtected]), self.message(d, "D"), self.message(scheduled, "scheduled")])
        try self.upsert([self.message(a, "A2"), self.message(b, "B2", attributes: [timed]),
            self.message(c, "C2"), self.message(d, "D2", flags: [.Incoming, .CopyProtected]), self.message(scheduled, "scheduled2")])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.current(a)?.text, "A2")
        XCTAssertEqual(try self.current(scheduled)?.text, "scheduled2")
    }

    func testRichPrimaryRepeatPreservesFullPageCacheWithoutFalseMiss() throws {
        let id = self.id(1)
        let page = InstantPage(blocks: [.paragraph(.plain("rich"))], media: [:], isComplete: true, rtl: false, url: "", views: nil)
        let full = InstantPage(blocks: [.paragraph(.plain("cached full"))], media: [:], isComplete: true, rtl: false, url: "", views: nil)
        try self.upsert([self.message(id, "", attributes: [RichTextMessageAttribute(instantPage: page, fullInstantPage: full)])])
        try self.upsert([self.message(id, "", attributes: [RichTextMessageAttribute(instantPage: page, fullInstantPage: nil)])])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 0)
        XCTAssertEqual(try self.current(id)?.attributes.compactMap { $0 as? RichTextMessageAttribute }.first?.fullInstantPage, full)
    }

    func testValidationCapturesChangedBodyButRetainsMetadataOnlyCanonicalBody() throws {
        let id = self.id(1), owner = self.owner
        try self.upsert([self.message(id, "old", localTags: [.OutgoingLiveLocation])])
        let edited = self.message(id, "new", timestamp: 120, attributes: [EditedMessageAttribute(date: 190, isHidden: false)])
        let ignored = self.message(id, "must not replace")
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            applyLocalArchiveValidatedMessage(transaction: transaction, accountPeerId: owner, id: id, message: edited, timestampChanged: true, channelPts: 5)
            applyLocalArchiveValidatedMessage(transaction: transaction, accountPeerId: owner, id: id, message: ignored, timestampChanged: false, channelPts: 9)
        })
        XCTAssertEqual(try self.pending().map { $0.text }, ["old"])
        let current = try XCTUnwrap(self.current(id))
        XCTAssertEqual(current.text, "new")
        XCTAssertEqual(current.timestamp, 120)
        XCTAssertTrue(current.localTags.contains(.OutgoingLiveLocation))
        XCTAssertEqual(current.attributes.compactMap { $0 as? ChannelMessageStateVersionAttribute }.first?.pts, 9)
    }

    func testValidationLocalRemovalCapturesOnceWithoutInventingRemoteDeletionEvidence() throws {
        let id = self.id(1), owner = self.owner, postbox = self.fixture.postbox!
        try self.upsert([self.message(id, "locally removed")])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            applyLocalArchiveValidatedDeletion(transaction: transaction, mediaBox: postbox.mediaBox, accountPeerId: owner, ids: [id])
            applyLocalArchiveValidatedDeletion(transaction: transaction, mediaBox: postbox.mediaBox, accountPeerId: owner, ids: [id])
        })
        XCTAssertNil(try self.current(id))
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().map { $0.text }, ["locally removed"])
        XCTAssertEqual(try self.pending().first?.kindRawValue, ArchiveEventKind.deleted.rawValue)
    }

    func testCachedMinimumAvailableRemovesExactCloudRangeAndHonorsExpiryExclusion() throws {
        let owner = self.owner, channel = self.channel, postbox = self.fixture.postbox!, bound = self.id(3)
        try self.upsert([self.message(self.id(1), "one"), self.message(self.id(2), "timed", attributes: [AutoclearTimeoutMessageAttribute(timeout: 1, countdownBeginTime: nil)]),
            self.message(bound, "three"), self.message(self.id(4), "after"), self.message(self.id(1, namespace: Namespaces.Message.ScheduledCloud), "scheduled")])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            applyLocalArchiveCachedMinAvailableMessage(transaction: transaction, mediaBox: postbox.mediaBox, accountPeerId: owner, peerId: channel, minAvailableMessageId: bound)
            applyLocalArchiveCachedMinAvailableMessage(transaction: transaction, mediaBox: postbox.mediaBox, accountPeerId: owner, peerId: channel, minAvailableMessageId: bound)
        })
        XCTAssertEqual(Set(try self.pending().map { $0.text }), Set(["one", "three"]))
        XCTAssertNil(try self.current(self.id(1))); XCTAssertNil(try self.current(self.id(2))); XCTAssertNil(try self.current(bound))
        XCTAssertNotNil(try self.current(self.id(4)))
        XCTAssertNotNil(try self.current(self.id(1, namespace: Namespaces.Message.ScheduledCloud)))
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().count, 2)
    }

    func testCachedMinimumAvailableRetainsCompleteMediaAcrossDrainAndCacheClear() throws {
        let owner = self.owner, channel = self.channel, postbox = self.fixture.postbox!, id = self.id(1)
        let bytes = Data([1, 9, 4, 2])
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9402, accessHash: 0, size: Int64(bytes.count), fileReference: nil, fileName: nil)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9402), partialReference: nil,
            resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil,
            mimeType: "application/octet-stream", size: Int64(bytes.count), attributes: [], alternativeRepresentations: [])
        postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        try self.upsert([self.message(id, "document", media: [file])])
        _ = try self.localArchiveValue(postbox.transaction { transaction in
            applyLocalArchiveCachedMinAvailableMessage(transaction: transaction, mediaBox: postbox.mediaBox, accountPeerId: owner, peerId: channel, minAvailableMessageId: id)
        })
        let key = try XCTUnwrap(self.pending().first?.eventKey)
        XCTAssertEqual(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(resource.id).complete), bytes)
        try self.fixture.reopen()
        try self.withDrainedArchive { archive in
            let reopened = self.fixture.postbox!
            let removed = expectation(description: "clear source document")
            let disposable = reopened.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { removed.fulfill() })
            wait(for: [removed], timeout: 15); disposable.dispose()
            XCTAssertEqual(try self.localArchiveValue(archive.mediaData(eventKey: key, slot: 0)), bytes)
            XCTAssertEqual(try self.localArchiveValue(archive.deletedMessages()).records.map { $0.text }, ["document"])
        }
    }

    func testSameSecondContentCycleKeepsDistinctEventsThroughRealEncryptedDrain() throws {
        let id = self.id(1), owner = self.owner
        let attributes: [MessageAttribute] = [EditedMessageAttribute(date: 190, isHidden: false)]
        let original = self.message(id, "A", attributes: attributes)
        let cycle = ["B", "A", "B"].map { self.message(id, $0, attributes: attributes) }
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([original], location: .Random)
            let capture = LocalArchiveEditCapture(transaction: transaction, accountPeerId: owner, observedAt: 200)
            _ = transaction.addMessages(cycle, location: .Random, beforeExistingUpdate: { previous, updated in
                capture?.append(previous: previous, updated: updated)
            })
            capture?.finish()
        })
        let operations = try self.pending()
        XCTAssertEqual(operations.map { $0.text }, ["A", "B", "A"])
        XCTAssertEqual(Set(operations.map { $0.observedAt }), [200])
        XCTAssertEqual(Set(operations.compactMap { $0.serverEditAt }), [190])
        XCTAssertEqual(Set(operations.map { $0.eventKey }).count, 3)
        try self.fixture.reopen()
        try self.withDrainedArchive { archive in
            let timeline = try self.localArchiveValue(archive.timeline(peerId: id.peerId.toInt64(), namespace: id.namespace, messageId: id.id))
            XCTAssertEqual(timeline.records.map { $0.text }, ["A", "B", "A"])
            XCTAssertEqual(timeline.records.map { $0.eventKey }, operations.map { $0.eventKey })
        }
        XCTAssertEqual(try self.current(id)?.text, "B")
    }

    func testBatchCountBudgetCountsMissesWithoutBlockingCanonicalUpdates() throws {
        let ids = (1...260).map { self.id(Int32($0)) }
        try self.upsert(ids.map { self.message($0, "old \($0.id)") })
        let replacements = ids.map { self.message($0, "new \($0.id)") }
        try self.upsert(replacements)
        XCTAssertEqual(try self.pending().count, 256)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 4)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0)?.text } }), ids.map { "new \($0.id)" })
        try self.upsert(replacements)
        XCTAssertEqual(try self.pending().count, 256)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 4, "unchanged bodies never count as missed captures")
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().count, 256)
    }

    func testBatchByteBudgetPreservesBoundAndCountsAllEligibleMisses() throws {
        let ids = (1...40).map { self.id(Int32($0)) }
        let large = String(repeating: "x", count: 256 * 1024)
        try self.upsert(ids.map { self.message($0, large) })
        try self.upsert(ids.map { self.message($0, "replacement") })
        let events = try self.pending()
        XCTAssertGreaterThan(events.count, 0)
        XCTAssertLessThan(events.count, ids.count)
        XCTAssertLessThanOrEqual(events.reduce(0) { $0 + $1.encodedByteCount }, 8 * 1024 * 1024)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), Int64(ids.count - events.count))
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0)?.text } }), Array(repeating: "replacement", count: ids.count))
    }

}
