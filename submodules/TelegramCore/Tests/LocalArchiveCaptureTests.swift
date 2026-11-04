// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Real Postbox transaction/serialization coverage for the helpers used by state replay.
/// These are not end-to-end tests of server updates or a logged-in application.
final class LocalArchiveCaptureTests: XCTestCase {
    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-capture")
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ value: Int32, peerId: PeerId? = nil, namespace: Int32 = Namespaces.Message.Cloud) -> MessageId {
        return MessageId(peerId: peerId ?? self.chatPeerId, namespace: namespace, id: value)
    }

    private func settings(_ update: @escaping (inout LocalArchiveSettings) -> Void) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var value = localArchiveSettings(transaction: transaction)
            update(&value)
            setLocalArchiveSettings(transaction: transaction, settings: value)
        })
    }

    private func pending() throws -> [LocalArchivePendingCapture] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId)
        })
    }

    private func store(_ messages: [StoreMessage]) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages(messages, location: .Random)
        })
    }

    private func captureAndDelete(_ ids: [MessageId], observedAt: Int64 = 200) throws {
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: ids, observedAt: observedAt)
            transaction.deleteMessages(ids, forEachMedia: nil)
        })
    }

    private func captureAndEdit(_ id: MessageId, updated: StoreMessage, observedAt: Int64 = 200) throws {
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updateMessage(id) { previous in
                captureEditedLocalArchiveMessage(transaction: transaction, accountPeerId: accountPeerId, previous: previous, updated: updated, observedAt: observedAt)
                return .update(mergedServerMessageUpdate(previous: previous, updated: updated))
            }
        })
    }

    private func richMessage(_ text: String) -> RichTextMessageAttribute {
        return RichTextMessageAttribute(instantPage: InstantPage(blocks: [.paragraph(.plain(text))],
            media: [:], isComplete: true, rtl: false, url: "", views: nil), fullInstantPage: nil)
    }

    private func missedCaptures() throws -> Int64 {
        return try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures })
    }

    private func photo(_ id: Int64) -> TelegramMediaImage {
        return TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: id),
            representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
    }

    func testDeletedPhotoMetadataSurvivesPostboxReopenWithoutClaimingFileRetention() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(71)
        try self.store([localArchiveTestMessage(id, text: "caption", media: [self.photo(701)])])
        try self.captureAndDelete([id])
        try self.fixture.reopen()
        let operation = try XCTUnwrap(self.pending().first?.operation)
        let capture = try XCTUnwrap(operation.makeCapture(accountId: 55))
        let metadata = try XCTUnwrap(localArchiveDecodedMediaMetadata(capture.event.content.entities))
        XCTAssertEqual(capture.event.content.text, "caption")
        XCTAssertEqual(metadata.map(\.kind), [.photo])
        XCTAssertEqual(metadata.map(\.id), [701])
        XCTAssertFalse(try XCTUnwrap(metadata.first).bytesPreserved)
        XCTAssertEqual(try self.missedCaptures(), 0) // File availability is resolved by the worker.
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testAttachmentOnlyEditKeepsPreviousPhotoIdentity() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(72)
        try self.store([localArchiveTestMessage(id, text: "unchanged caption", media: [self.photo(702)])])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "unchanged caption", media: [self.photo(703)]))
        let operation = try XCTUnwrap(self.pending().first?.operation)
        XCTAssertEqual(operation.kindRawValue, ArchiveEventKind.edited.rawValue)
        XCTAssertEqual(try XCTUnwrap(localArchiveDecodedMediaMetadata(operation.entities)).map(\.id), [702])
        XCTAssertEqual(operation.text, "unchanged caption")
    }

    func testRepeatedPhotoIdentityDoesNotCreateAnEditVersion() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(73)
        try self.store([localArchiveTestMessage(id, text: "caption", media: [self.photo(704)])])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "caption", media: [self.photo(704)]))
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testDisabledByDefaultStillAppliesCanonicalDeletion() throws {
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id)])
        try self.captureAndDelete([id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testPreferencesCodecRoundTripPreservesNonemptySettingsAfterReopen() throws {
        var configured = LocalArchiveSettings(
            enabled: true, excludeBots: false,
            excludedPeerIds: [Int64.max - 1, -4_294_967_300],
            excludedSenderIds: [4_294_967_301, 7],
            maxAgeSeconds: 4_294_967_302, maxPayloadBytes: 512 * 1024 * 1024
        )
        configured.generation = 4_294_967_303
        configured.pendingPurge = true
        configured.droppedCaptures = 4_294_967_304

        for expected in [LocalArchiveSettings(), configured] {
            // Exercise the actual adapted Postbox codec, which rejects Swift Int.
            // JSONEncoder alone cannot catch that failure. Fresh data also avoids
            // mistaking the preference cache for a successful persistence round-trip.
            let encoded = try XCTUnwrap(PreferencesEntry(expected))
            let decodedEntry = PreferencesEntry(data: encoded.data)
            XCTAssertEqual(try XCTUnwrap(decodedEntry.get(LocalArchiveSettings.self)), expected)
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                transaction.setPreferencesEntry(key: localArchiveSettingsKey, value: decodedEntry)
            })
            try self.fixture.reopen()
            let actual = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                localArchiveSettings(transaction: transaction)
            })
            XCTAssertEqual(actual, expected)
        }
    }

    func testUnknownSettingsSchemaFailsClosedAfterReopen() throws {
        let encoded = try JSONEncoder().encode(LocalArchiveSettings(enabled: true))
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object["schemaVersion"] = 99
        let futureSettings = try JSONDecoder().decode(LocalArchiveSettings.self, from: JSONSerialization.data(withJSONObject: object))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: futureSettings)
        })
        try self.fixture.reopen()
        XCTAssertFalse(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).enabled }))
        try self.store([localArchiveTestMessage(self.id(1))])
        try self.captureAndDelete([self.id(1)])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testDeleteOutboxAndCanonicalDeletionSurviveReopen() throws {
        try self.settings { $0.enabled = true; $0.generation = 7 }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "text before deletion")])
        try self.captureAndDelete([id], observedAt: 234)
        let before = try XCTUnwrap(self.pending().first?.operation)
        XCTAssertEqual(before.text, "text before deletion")
        XCTAssertEqual(before.messageId, id)
        XCTAssertEqual(before.kindRawValue, ArchiveEventKind.deleted.rawValue)
        XCTAssertEqual(before.observedAt, 234)
        XCTAssertEqual(before.sentAt, 100)
        XCTAssertEqual(before.generation, 7)

        try self.fixture.reopen()
        let after = try XCTUnwrap(self.pending().first?.operation)
        XCTAssertEqual(after.eventKey, before.eventKey)
        XCTAssertEqual(after.text, before.text)
        XCTAssertEqual(after.observedAt, before.observedAt)
        XCTAssertEqual(after.generation, before.generation)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        let settings = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertTrue(settings.enabled)
        XCTAssertEqual(settings.generation, 7)
    }

    func testMissingAndRepeatedDeletesDoNotInventMessages() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id)])
        try self.captureAndDelete([id, self.id(2)])
        try self.captureAndDelete([id, self.id(2)])
        XCTAssertEqual(try self.pending().count, 1)
    }

    func testGlobalIdLookupPreservesMessageBeforeDelete() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(42)
        try self.store([localArchiveTestMessage(id, text: "global id message")])
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId,
                ids: transaction.messageIdsForGlobalIds([42]), observedAt: 200)
            transaction.deleteMessagesWithGlobalIds([42], forEachMedia: nil)
        })
        XCTAssertEqual(try self.pending().first?.operation?.messageId, id)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testEditPreservesPreviousTextAndBothTimestamps() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "version one")])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "version two", attributes: [EditedMessageAttribute(date: 190, isHidden: false)]), observedAt: 200)
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "version three", attributes: [EditedMessageAttribute(date: 210, isHidden: false)]), observedAt: 220)
        try self.fixture.reopen()
        let operations = try self.pending().compactMap { $0.operation }
        XCTAssertEqual(operations.map { $0.text }, ["version one", "version two"])
        XCTAssertEqual(operations.map { $0.kindRawValue }, [ArchiveEventKind.edited.rawValue, ArchiveEventKind.edited.rawValue])
        XCTAssertEqual(operations.map { $0.observedAt }, [200, 220])
        // serverEditAt describes the archived version, while observedAt records
        // when the replacement arrived locally. The original had never been edited.
        XCTAssertEqual(operations.map { $0.serverEditAt }, [nil, 190])
        XCTAssertNotEqual(operations[0].eventKey, operations[1].eventKey)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "version three")
    }

    func testRepeatedEditAndMetadataOnlyUpdateDoNotCreateVersions() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "same content")])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "same content", attributes: [EditedMessageAttribute(date: 150, isHidden: false)]))
        XCTAssertTrue(try self.pending().isEmpty)
        let updated = localArchiveTestMessage(id, text: "new content", attributes: [EditedMessageAttribute(date: 180, isHidden: false)])
        try self.captureAndEdit(id, updated: updated)
        try self.captureAndEdit(id, updated: updated)
        XCTAssertEqual(try self.pending().count, 1)
    }

    func testEntityOnlyEditPreservesPreviousEntityMetadata() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        let bold = TextEntitiesMessageAttribute(entities: [MessageTextEntity(range: 0..<4, type: .Bold)])
        let italic = TextEntitiesMessageAttribute(entities: [MessageTextEntity(range: 0..<4, type: .Italic)])
        try self.store([localArchiveTestMessage(id, text: "text", attributes: [bold])])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "text", attributes: [italic]))
        let operation = try XCTUnwrap(self.pending().first?.operation)
        XCTAssertEqual(operation.text, "text")
        XCTAssertFalse(operation.entities.isEmpty)
        XCTAssertEqual(try self.pending().count, 1)
    }

    func testRichDeletionRecordsOneMissInsteadOfAnEmptySuccessfulArchive() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "", attributes: [self.richMessage("content is in the page")])])
        try self.captureAndDelete([id, id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 1)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        try self.fixture.reopen()
        try self.captureAndDelete([id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 1)
    }

    func testChangedRichEditRecordsOneMissAndDuplicateEditDoesNot() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "", attributes: [self.richMessage("old page")])])
        let updatedRich = self.richMessage("new page")
        let updated = localArchiveTestMessage(id, text: "", attributes: [updatedRich, EditedMessageAttribute(date: 190, isHidden: false)])
        try self.captureAndEdit(id, updated: updated)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 1)
        try self.fixture.reopen()
        try self.captureAndEdit(id, updated: updated)
        XCTAssertEqual(try self.missedCaptures(), 1)
        let currentRich = try self.localArchiveValue(self.fixture.postbox.transaction {
            $0.getMessage(id)?.attributes.first(where: { $0 is RichTextMessageAttribute }) as? RichTextMessageAttribute
        })
        XCTAssertEqual(currentRich, updatedRich)
    }

    func testUnchangedRichPageWithMetadataOnlyEditDoesNotCountAsMiss() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "", attributes: [self.richMessage("unchanged page")])])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "", attributes: [
            self.richMessage("unchanged page"), EditedMessageAttribute(date: 190, isHidden: false)
        ]))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 0)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.editedTime }), 190)
    }

    func testRepeatedRichPrimaryPageWithoutLocalFullPageCacheDoesNotCountAsMiss() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        let primary = self.richMessage("primary page").instantPage
        let cachedFull = self.richMessage("locally fetched full page").instantPage
        try self.store([localArchiveTestMessage(id, text: "", attributes: [
            RichTextMessageAttribute(instantPage: primary, fullInstantPage: cachedFull)
        ])])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "", attributes: [
            RichTextMessageAttribute(instantPage: primary, fullInstantPage: nil),
            EditedMessageAttribute(date: 190, isHidden: false)
        ]))
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 0)
        let currentRich = try self.localArchiveValue(self.fixture.postbox.transaction {
            $0.getMessage(id)?.attributes.first(where: { $0 is RichTextMessageAttribute }) as? RichTextMessageAttribute
        })
        XCTAssertEqual(currentRich?.instantPage, primary)
        XCTAssertEqual(currentRich?.fullInstantPage, cachedFull, "normal Postbox seed merge preserves the fetched full page")
    }

    func testNormalToRichSavesNormalVersionAndRichToNormalReportsMiss() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: "available plain version")])
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "", attributes: [self.richMessage("structured replacement")]))
        XCTAssertEqual(try self.pending().compactMap { $0.operation?.text }, ["available plain version"])
        XCTAssertEqual(try self.missedCaptures(), 0)
        try self.captureAndEdit(id, updated: localArchiveTestMessage(id, text: "plain again"))
        XCTAssertEqual(try self.pending().compactMap { $0.operation?.text }, ["available plain version"])
        XCTAssertEqual(try self.missedCaptures(), 1)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), "plain again")
    }

    func testPolicyExcludedRichContentDoesNotCountAsMissedCapture() throws {
        let peerId = self.chatPeerId.toInt64()
        try self.settings { $0.enabled = true; $0.excludedPeerIds = [peerId] }
        try self.store([localArchiveTestMessage(self.id(1), text: "", attributes: [self.richMessage("excluded chat")])])
        try self.captureAndDelete([self.id(1)])
        try self.settings { $0.excludedPeerIds = [] }
        let expiring = self.id(2)
        let viewOnce = self.id(3)
        let protected = self.id(4)
        try self.store([
            localArchiveTestMessage(expiring, text: "", attributes: [self.richMessage("expiring"), AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)]),
            localArchiveTestMessage(viewOnce, text: "", attributes: [self.richMessage("view once"), AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil)]),
            localArchiveTestMessage(protected, text: "", attributes: [self.richMessage("copy protected")]).withUpdatedFlags([.Incoming, .CopyProtected])
        ])
        try self.captureAndDelete([expiring, viewOnce, protected])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 0)
    }

    func testSenderExclusionsFailClosedWhenAuthorPeerIsUnavailable() throws {
        let unknownAuthor = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(9999))
        let plain = self.id(1)
        let rich = self.id(2)
        try self.settings { $0.enabled = true; $0.excludedSenderIds = [unknownAuthor.toInt64()] }
        try self.store([
            localArchiveTestMessage(plain, text: "unknown author", authorId: unknownAuthor),
            localArchiveTestMessage(rich, text: "", authorId: unknownAuthor, attributes: [self.richMessage("unknown rich author")])
        ])
        let before = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(plain) }))
        XCTAssertNil(before.author, "fixture must exercise an unavailable author peer")
        try self.captureAndDelete([plain, rich])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.missedCaptures(), 0)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(plain) }))
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(rich) }))
    }

    func testPeerAndSenderExclusionsLeaveCanonicalDeletionUnchanged() throws {
        let excludedPeer = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1002))
        let excludedSender = self.chatPeerId
        let allowedSender = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(excludedPeer)], update: { _, peer in peer })
        })
        try self.settings {
            $0.enabled = true
            $0.excludedPeerIds = [excludedPeer.toInt64()]
            $0.excludedSenderIds = [excludedSender.toInt64()]
        }
        let excludedByPeer = self.id(1, peerId: excludedPeer)
        let excludedBySender = self.id(2)
        let allowed = self.id(3)
        try self.store([localArchiveTestMessage(excludedByPeer, authorId: allowedSender),
                        localArchiveTestMessage(excludedBySender, authorId: excludedSender),
                        localArchiveTestMessage(allowed, authorId: allowedSender)])
        try self.captureAndDelete([excludedByPeer, excludedBySender, allowed])
        XCTAssertEqual(try self.pending().compactMap { $0.operation?.messageId }, [allowed])
        for id in [excludedByPeer, excludedBySender, allowed] {
            XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        }
    }

    func testBotConversationRequiresExplicitOptIn() throws {
        let bot = self.chatPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(bot, bot: true)], update: { _, peer in peer })
        })
        try self.settings { $0.enabled = true }
        try self.store([localArchiveTestMessage(self.id(1))])
        try self.captureAndDelete([self.id(1)])
        XCTAssertTrue(try self.pending().isEmpty)
        try self.settings { $0.excludeBots = false }
        try self.store([localArchiveTestMessage(self.id(2))])
        try self.captureAndDelete([self.id(2)])
        XCTAssertEqual(try self.pending().first?.operation?.botConversation, true)
    }

    func testDisappearingAndViewOnceMessagesAreExcluded() throws {
        try self.settings { $0.enabled = true }
        let expiring = self.id(1)
        let viewOnce = self.id(2)
        try self.store([
            localArchiveTestMessage(expiring, attributes: [AutoremoveTimeoutMessageAttribute(timeout: 3600, countdownBeginTime: nil)]),
            localArchiveTestMessage(viewOnce, attributes: [AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil)])
        ])
        try self.captureAndDelete([expiring, viewOnce])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testLocalAndScheduledMessagesAreExcluded() throws {
        try self.settings { $0.enabled = true }
        let local = self.id(1, namespace: Namespaces.Message.Local)
        let scheduled = self.id(2, namespace: Namespaces.Message.ScheduledCloud)
        try self.store([localArchiveTestMessage(local), localArchiveTestMessage(scheduled)])
        try self.captureAndDelete([local, scheduled])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testPurgeBarrierPreventsNewOutboxItemsDuringPendingPurge() throws {
        try self.settings { $0.enabled = true; $0.pendingPurge = true; $0.generation = 3 }
        try self.store([localArchiveTestMessage(self.id(1))])
        try self.captureAndDelete([self.id(1)])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testAcknowledgementRequiresExactEventAndPersists() throws {
        try self.settings { $0.enabled = true }
        try self.store([localArchiveTestMessage(self.id(1)), localArchiveTestMessage(self.id(2))])
        try self.captureAndDelete([self.id(1), self.id(2)])
        let entries = try self.pending()
        let first = try XCTUnwrap(entries.first)
        let firstOperation = try XCTUnwrap(first.operation)
        let accountPeerId = self.accountPeerId
        let wrong = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            acknowledgeLocalArchiveCapture(transaction: transaction, accountPeerId: accountPeerId, index: first.index, eventKey: "wrong event")
        })
        XCTAssertFalse(wrong)
        XCTAssertEqual(try self.pending().count, 2)
        let removed = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            acknowledgeLocalArchiveCapture(transaction: transaction, accountPeerId: accountPeerId, index: first.index, eventKey: firstOperation.eventKey)
        })
        XCTAssertTrue(removed)
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().count, 1)
        XCTAssertEqual(try self.pending().first?.operation?.messageId, self.id(2))
    }

    func testBacklogBoundReportsGapAndDoesNotPreventCanonicalDeletion() throws {
        try self.settings { $0.enabled = true }
        let ids = (1...257).map { self.id(Int32($0)) }
        try self.store(ids.map { localArchiveTestMessage($0) })
        try self.captureAndDelete(ids)
        let accountPeerId = self.accountPeerId
        let state = try self.localArchiveValue(self.fixture.postbox.transaction { transaction -> (Int, Int64, Int) in
            var pendingCount = 0
            transaction.operationLogEnumerateEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { _ in
                pendingCount += 1
                return true
            }
            return (pendingCount, localArchiveSettings(transaction: transaction).droppedCaptures,
                    ids.filter { transaction.getMessage($0) != nil }.count)
        })
        XCTAssertEqual(state.0, 256)
        XCTAssertEqual(state.1, 1)
        XCTAssertEqual(state.2, 0)
        XCTAssertEqual(try self.pending().count, 64, "each drain reads only a bounded page")
    }

    func testOversizedMessageReportsGapWithoutBlockingDeletion() throws {
        try self.settings { $0.enabled = true }
        let id = self.id(1)
        try self.store([localArchiveTestMessage(id, text: String(repeating: "x", count: 256 * 1024 + 1))])
        try self.captureAndDelete([id])
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0).droppedCaptures }), 1)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
    }

    func testIdenticalMessageIdsInAnotherAccountAreIndependent() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "archive-other-account")
        defer { try? other.destroy() }
        let id = self.id(1)
        let chat = localArchiveTestUser(self.chatPeerId)
        let accountPeerId = self.accountPeerId
        try self.settings { $0.enabled = true }
        try self.store([localArchiveTestMessage(id, text: "account one")])
        let _ = try self.localArchiveValue(other.postbox.transaction { transaction in
            transaction.updatePeersInternal([chat], update: { _, peer in peer })
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "account two")], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: [id], observedAt: 200)
            transaction.deleteMessages([id], forEachMedia: nil)
        })
        try self.captureAndDelete([id])
        XCTAssertEqual(try self.pending().first?.operation?.text, "account one")
        XCTAssertTrue(try self.localArchiveValue(other.postbox.transaction {
            localArchivePendingCaptures(transaction: $0, accountPeerId: accountPeerId).isEmpty
        }))
        XCTAssertFalse(try self.localArchiveValue(other.postbox.transaction { localArchiveSettings(transaction: $0).enabled }))
    }
}
