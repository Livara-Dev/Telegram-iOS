// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Real local Postbox consumption/deletion and reopen coverage, using owned
/// synthetic media. Default exclusions and explicit opt-in storage are tested
/// separately; these do not establish remote delivery or physical-device behavior.
final class LocalArchiveTimedMediaExclusionTests: XCTestCase {
    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "timed-media-exclusion")
        let accountPeerId = self.accountPeerId
        let peers = [localArchiveTestUser(accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: accountPeerId, state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func id(_ number: Int32) -> MessageId {
        return MessageId(peerId: self.chatPeerId, namespace: Namespaces.Message.Cloud, id: number)
    }

    private func message(_ id: MessageId) throws -> Message? {
        return try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
    }

    private func captures() throws -> [LocalArchiveCaptureOperation] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId).compactMap { $0.operation }
        })
    }

    private func deleteInteractively(_ ids: [MessageId]) throws {
        let postbox = self.fixture.postbox!
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            deleteMessagesInteractively(transaction: transaction, stateManager: nil, postbox: postbox,
                messageIds: ids, type: .forEveryone, removeIfPossiblyDelivered: false)
        })
    }

    private func completedPhoto() -> (Media, MediaResource, Data) {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9401, accessHash: 0,
            sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9401),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        return (photo, resource, bytes)
    }

    private func completedFile(voice: Bool = false, round: Bool = false) -> (Media, MediaResource, Data) {
        // File flags exercise Telegram's actual media classification. Only the
        // photo fixture is decoded media; these bytes do not prove AV playback.
        let bytes = Data([1, 3, 5, 7])
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9402, accessHash: 0,
            size: Int64(bytes.count), fileReference: nil, fileName: nil)
        let attribute: TelegramMediaFileAttribute = voice
            ? .Audio(isVoice: true, duration: 1, title: nil, performer: nil, waveform: nil)
            : .Video(duration: 1, size: PixelDimensions(width: 16, height: 16),
                flags: round ? [.instantRoundVideo] : [], preloadSize: nil, coverTime: nil, videoCodec: nil)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9402),
            partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [],
            immediateThumbnailData: nil, mimeType: voice ? "audio/ogg" : "video/mp4", size: Int64(bytes.count),
            attributes: [attribute], alternativeRepresentations: [])
        return (file, resource, bytes)
    }

    private func assertExcluded(_ media: (Media, MediaResource, Data), timeout: Int32,
                                expectedExpired: TelegramMediaExpiredContentData,
                                file: StaticString = #filePath, line: UInt = #line) throws {
        let first = self.id(1)
        let second = self.id(2)
        let attributes: [MessageAttribute] = [AutoclearTimeoutMessageAttribute(timeout: timeout, countdownBeginTime: nil),
            ConsumableContentMessageAttribute(consumed: false)]
        self.fixture.postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages([
                localArchiveTestMessage(first, attributes: attributes, media: [media.0]),
                localArchiveTestMessage(second, attributes: attributes, media: [media.0])
            ], location: .Random)
        })
        XCTAssertEqual(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(media.1.id).complete), media.2, file: file, line: line)

        // The real interactive deletion path calls its archive adapter before
        // canonical deletion; locally available timed bytes must stay excluded.
        try self.deleteInteractively([first])
        XCTAssertNil(try self.message(first), file: file, line: line)
        XCTAssertTrue(try self.captures().isEmpty, file: file, line: line)

        // This is the production remote-consumption transaction helper, with an
        // owned synthetic old consume date. It expires these media normally.
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            markMessageContentAsConsumedRemotely(transaction: transaction, messageId: second, consumeDate: 1)
        })
        let consumed = try XCTUnwrap(self.message(second), file: file, line: line)
        XCTAssertEqual(consumed.attributes.compactMap { $0 as? ConsumableContentMessageAttribute }.first?.consumed, true, file: file, line: line)
        XCTAssertEqual(consumed.attributes.compactMap { $0 as? AutoclearTimeoutMessageAttribute }.first?.countdownBeginTime, 1, file: file, line: line)
        let expired = try XCTUnwrap(consumed.media.first as? TelegramMediaExpiredContent, file: file, line: line)
        XCTAssertEqual(expired.data.rawValue, expectedExpired.rawValue, file: file, line: line)
        XCTAssertTrue(try self.captures().isEmpty, file: file, line: line)
        try self.fixture.reopen()
        if let remaining = try self.message(second) {
            // A canonical expiry worker is also allowed to remove the message;
            // a restart must never restore its original media or archive it.
            let persistedExpired = try XCTUnwrap(remaining.media.first as? TelegramMediaExpiredContent, file: file, line: line)
            XCTAssertEqual(persistedExpired.data.rawValue, expectedExpired.rawValue, file: file, line: line)
        }
        XCTAssertTrue(try self.captures().isEmpty, file: file, line: line)

        let removed = expectation(description: "remove synthetic timed media cache")
        let disposable = self.fixture.postbox.mediaBox.removeCachedResources([media.1.id], force: true).start(completed: { removed.fulfill() })
        defer { disposable.dispose() }
        wait(for: [removed], timeout: 15)
        self.fixture.postbox.mediaBox.dataQueue.sync {}
        try self.deleteInteractively([second])
        try self.fixture.reopen()
        XCTAssertNil(try self.message(second), file: file, line: line)
        XCTAssertTrue(try self.captures().isEmpty, file: file, line: line)
        XCTAssertNil(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(media.1.id).complete), file: file, line: line)
    }

    func testTimedPhotoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedPhoto(), timeout: 30, expectedExpired: .image)
    }

    func testTimedVideoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(), timeout: 30, expectedExpired: .file)
    }

    func testViewOncePhotoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedPhoto(), timeout: viewOnceTimeout, expectedExpired: .image)
    }

    func testViewOnceVideoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(), timeout: viewOnceTimeout, expectedExpired: .file)
    }

    func testDisappearingVoiceExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(voice: true), timeout: 30, expectedExpired: .voiceMessage)
    }

    func testDisappearingRoundVideoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(round: true), timeout: 30, expectedExpired: .videoMessage)
    }

    func testViewOnceVoiceExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(voice: true), timeout: viewOnceTimeout, expectedExpired: .voiceMessage)
    }

    func testViewOnceRoundVideoExclusionAndCanonicalConsumptionSurviveRestart() throws {
        try self.assertExcluded(self.completedFile(round: true), timeout: viewOnceTimeout, expectedExpired: .videoMessage)
    }

    func testChatAutoDeleteTextAndPhotoTimersRemainArmedWithoutArchiveCapture() throws {
        let ids = [self.id(1), self.id(2)]
        let photo = self.completedPhoto()
        let beganAt = Int32(Date().timeIntervalSince1970)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let attributes: [MessageAttribute] = [AutoremoveTimeoutMessageAttribute(timeout: 3600, countdownBeginTime: beganAt)]
            let _ = transaction.addMessages([
                localArchiveTestMessage(ids[0], attributes: attributes),
                localArchiveTestMessage(ids[1], attributes: attributes, media: [photo.0])
            ], location: .Random)
        })
        try self.fixture.reopen()
        for id in ids {
            let message = try XCTUnwrap(self.message(id))
            let timer = try XCTUnwrap(message.attributes.compactMap { $0 as? AutoremoveTimeoutMessageAttribute }.first)
            XCTAssertEqual(timer.automaticTimestampBasedAttribute?.0, 0)
            XCTAssertEqual(timer.automaticTimestampBasedAttribute?.1, autoremoveExpiryTimestamp(countdownBeginTime: beganAt, timeout: 3600))
        }
        try self.deleteInteractively(ids)
        try self.fixture.reopen()
        for id in ids { XCTAssertNil(try self.message(id)) }
        XCTAssertTrue(try self.captures().isEmpty)
    }

    func testOrdinaryPhotoPositiveControlStillQueuesCaptureAfterRestart() throws {
        let id = self.id(1)
        let photo = self.completedPhoto()
        self.fixture.postbox.mediaBox.storeResourceData(photo.1.id, data: photo.2, synchronous: true)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages([localArchiveTestMessage(id, media: [photo.0])], location: .Random)
        })
        try self.deleteInteractively([id])
        try self.fixture.reopen()
        let captures = try self.captures()
        XCTAssertEqual(captures.count, 1)
        let capture = try XCTUnwrap(captures.first)
        let media = try XCTUnwrap(localArchiveDecodedMediaMetadata(capture.entities))
        XCTAssertEqual(capture.messageId, id)
        XCTAssertEqual(media.first?.kind, .photo)
    }

    func testLocalConsumptionStillStartsTimedPhotoCountdownWithoutArchiving() throws {
        let id = self.id(1)
        let photo = self.completedPhoto()
        let postbox = self.fixture.postbox!
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            let _ = transaction.addMessages([localArchiveTestMessage(id, attributes: [
                AutoclearTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil),
                ConsumableContentMessageAttribute(consumed: false)
            ], media: [photo.0])], location: .Random)
        })
        let before = Int32(Date().timeIntervalSince1970)
        let _ = try self.localArchiveValue(_internal_markMessageContentAsConsumedInteractively(postbox: postbox, messageId: id))
        let message = try XCTUnwrap(self.message(id))
        let timer = try XCTUnwrap(message.attributes.compactMap { $0 as? AutoclearTimeoutMessageAttribute }.first)
        let begin = try XCTUnwrap(timer.countdownBeginTime)
        XCTAssertGreaterThanOrEqual(begin, before)
        XCTAssertLessThanOrEqual(begin, Int32(Date().timeIntervalSince1970))
        XCTAssertEqual(timer.automaticTimestampBasedAttribute?.0, 1)
        XCTAssertEqual(timer.automaticTimestampBasedAttribute?.1, autoremoveExpiryTimestamp(countdownBeginTime: begin, timeout: 30))
        XCTAssertEqual(message.attributes.compactMap { $0 as? ConsumableContentMessageAttribute }.first?.consumed, true)
        XCTAssertNotNil(message.media.first as? TelegramMediaImage)
        try self.fixture.reopen()
        let reopened = try XCTUnwrap(self.message(id))
        XCTAssertEqual(reopened.attributes.compactMap { $0 as? AutoclearTimeoutMessageAttribute }.first?.countdownBeginTime, begin)
        XCTAssertTrue(try self.captures().isEmpty)
    }
    // Extended retention is exercised through production transaction helpers,
    // using only owned synthetic accounts and content. Default tests above stay.
    private func configureExtended(secret: Bool, timed: Bool) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.includeSecretChats = secret
            settings.includeExpiringMessages = timed
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
    }

    private func secretPeer() throws -> PeerId {
        let id = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(9461))
        let peer = TelegramSecretChat(id: id, creationDate: 0, regularPeerId: self.chatPeerId,
            accessHash: 0, role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([peer], update: { _, updated in updated })
        })
        return id
    }

    private func completedSecretPhoto() -> (Media, MediaResource, Data) {
        let bytes = localArchiveTestPhotoData()
        let resource = SecretFileMediaResource(fileId: -9403, accessHash: 123456789,
            containerSize: Int64(bytes.count + 16), decryptedSize: Int64(bytes.count), datacenterId: 1,
            key: SecretFileEncryptionKey(aesKey: Data(repeating: 0x62, count: 32), aesIv: Data(repeating: 0x63, count: 32)))
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9403),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        return (photo, resource, bytes)
    }

    private func assertSecretCacheRemoved(_ mediaBox: MediaBox, resourceId: MediaResourceId,
                                          file: StaticString = #filePath, line: UInt = #line) {
        // The production removal is queued before the deletion transaction returns.
        // Fence that queue; never make these assertions pass by clearing it ourselves.
        mediaBox.dataQueue.sync {}
        let paths = mediaBox.storePathsForId(resourceId)
        for path in [paths.complete, paths.partial, paths.partial + ".meta"] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: path), "Canonical secret cache cleanup must finish independently of the archive", file: file, line: line)
        }
    }

    private func assertOptedTimedCapture(_ media: (Media, MediaResource, Data), timeout: Int32,
                                        expectedExpired: TelegramMediaExpiredContentData,
                                        file: StaticString = #filePath, line: UInt = #line) throws {
        try self.configureExtended(secret: false, timed: true)
        let id = self.id(50)
        self.fixture.postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, attributes: [
                AutoclearTimeoutMessageAttribute(timeout: timeout, countdownBeginTime: nil),
                ConsumableContentMessageAttribute(consumed: false)
            ], media: [media.0])], location: .Random)
            markMessageContentAsConsumedRemotely(transaction: transaction, messageId: id, consumeDate: 1)
            // A repeated update must not manufacture another capture.
            markMessageContentAsConsumedRemotely(transaction: transaction, messageId: id, consumeDate: 1)
        })
        let expired = try XCTUnwrap(try self.message(id)?.media.first as? TelegramMediaExpiredContent, file: file, line: line)
        XCTAssertEqual(expired.data.rawValue, expectedExpired.rawValue, file: file, line: line)
        let captures = try self.captures()
        XCTAssertEqual(captures.count, 1, file: file, line: line)
        let operation = try XCTUnwrap(captures.first, file: file, line: line)
        let capture = try XCTUnwrap(operation.makeCapture(accountId: 1), file: file, line: line)
        XCTAssertTrue(capture.context.ordinaryCloudMessage, file: file, line: line)
        XCTAssertFalse(capture.context.secretChat, file: file, line: line)
        XCTAssertTrue(capture.context.expiring, file: file, line: line)
        XCTAssertEqual(capture.context.viewOnce, timeout == viewOnceTimeout, file: file, line: line)
        XCTAssertEqual(localArchiveDecodedMediaMetadata(operation.entities)?.first?.resourceKey,
            media.1.id.stringRepresentation, file: file, line: line)
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().map(\.eventKey), [operation.eventKey], file: file, line: line)
    }

    func testOptedTimedPhotoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedPhoto(), timeout: 30, expectedExpired: .image)
    }
    func testOptedTimedVideoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(), timeout: 30, expectedExpired: .file)
    }
    func testOptedViewOncePhotoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedPhoto(), timeout: viewOnceTimeout, expectedExpired: .image)
    }
    func testOptedViewOnceVideoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(), timeout: viewOnceTimeout, expectedExpired: .file)
    }
    func testOptedTimedVoiceCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(voice: true), timeout: 30, expectedExpired: .voiceMessage)
    }
    func testOptedViewOnceVoiceCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(voice: true), timeout: viewOnceTimeout, expectedExpired: .voiceMessage)
    }
    func testOptedTimedRoundVideoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(round: true), timeout: 30, expectedExpired: .videoMessage)
    }
    func testOptedViewOnceRoundVideoCapturesBeforeCanonicalRemoteExpiry() throws {
        try self.assertOptedTimedCapture(self.completedFile(round: true), timeout: viewOnceTimeout, expectedExpired: .videoMessage)
    }

    func testOptedLocalTimerUsesActualExpiryTransitionWithoutRestoringCanonicalMedia() throws {
        try self.configureExtended(secret: false, timed: true)
        let id = self.id(60), photo = self.completedPhoto()
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, attributes: [
                AutoclearTimeoutMessageAttribute(timeout: 30, countdownBeginTime: 1)
            ], media: [photo.0])], location: .Random)
            if let message = transaction.getMessage(id) {
                expireMessageContentLocally(transaction: transaction, message: message)
            }
        })
        XCTAssertTrue(try self.message(id)?.media.first is TelegramMediaExpiredContent)
        XCTAssertFalse(try XCTUnwrap(try self.message(id)).attributes.contains { $0 is AutoclearTimeoutMessageAttribute })
        XCTAssertEqual(try self.captures().count, 1)
        try self.fixture.reopen()
        XCTAssertTrue(try self.message(id)?.media.first is TelegramMediaExpiredContent)
        XCTAssertEqual(try self.captures().count, 1)
    }

    func testOptedSecretIncomingZeroIdentifierAndLocalNamespaceRemainDistinct() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer()
        let ids = [MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 0),
                   MessageId(peerId: peer, namespace: Namespaces.Message.Local, id: 0)]
        let postbox = self.fixture.postbox!
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages(ids.map { localArchiveTestMessage($0, authorId: self.chatPeerId) }, location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: ids)
        })
        XCTAssertEqual(Set(try self.captures().map(\.messageId)), Set(ids))
        for operation in try self.captures() {
            let capture = try XCTUnwrap(operation.makeCapture(accountId: 1))
            XCTAssertTrue(capture.context.secretChat)
            XCTAssertFalse(capture.context.ordinaryCloudMessage)
            XCTAssertFalse(capture.context.expiring)
            XCTAssertEqual(capture.event.message.messageId, 0)
        }
        try self.fixture.reopen()
        XCTAssertEqual(try self.captures().count, 2)
        for id in ids { XCTAssertNil(try self.message(id)) }
    }

    func testTimedSecretMessagesRequireBothIndependentOptIns() throws {
        let peer = try self.secretPeer(), postbox = self.fixture.postbox!
        var expected = 0
        for (index, flags) in [(false, false), (true, false), (false, true), (true, true)].enumerated() {
            try self.configureExtended(secret: flags.0, timed: flags.1)
            let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: Int32(index + 1))
            let _ = try self.localArchiveValue(postbox.transaction { transaction in
                _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId,
                    attributes: [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: 1)])], location: .Random)
                _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
            })
            if flags.0 && flags.1 { expected += 1 }
            XCTAssertEqual(try self.captures().count, expected)
            XCTAssertNil(try self.message(id))
        }
        let capture = try XCTUnwrap(try self.captures().first?.makeCapture(accountId: 1))
        XCTAssertTrue(capture.context.secretChat && capture.context.expiring)
    }

    func testOptedSecretMediaCaptureNeverSerializesDecryptionKeysOrAccessHash() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), photo = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 9)
        postbox.mediaBox.storeResourceData(photo.1.id, data: photo.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [photo.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        postbox.mediaBox.dataQueue.sync {}
        let operation = try XCTUnwrap(try self.captures().first)
        XCTAssertEqual(localArchiveDecodedMediaMetadata(operation.entities)?.first?.resourceKey, "secret-file--9403-1")
        let json = try XCTUnwrap(String(data: operation.entities, encoding: .utf8))
        for forbidden in ["123456789", "aesKey", "aesIv", "accessHash", Data(repeating: 0x62, count: 32).base64EncodedString(), Data(repeating: 0x63, count: 32).base64EncodedString()] {
            XCTAssertFalse(json.contains(forbidden))
        }
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: photo.1.id)
    }

    func testOptedSecretClearHistoryCapturesBeforeForcedCacheRemoval() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), photo = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 11)
        postbox.mediaBox.storeResourceData(photo.1.id, data: photo.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [photo.0])], location: .Random)
            _internal_clearHistory(transaction: transaction, mediaBox: postbox.mediaBox, peerId: peer, threadId: nil, namespaces: .all)
        })
        postbox.mediaBox.dataQueue.sync {}
        XCTAssertNil(try self.message(id))
        XCTAssertEqual(try self.captures().map(\.messageId), [id])
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: photo.1.id)
    }

    func testOptedSecretClearHistoryRangeRetainsOnlySelectedRows() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), postbox = self.fixture.postbox!
        let media = self.completedSecretPhoto(), outsideMedia = self.completedPhoto()
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 12)
        let outside = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 13)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        postbox.mediaBox.storeResourceData(outsideMedia.1.id, data: outsideMedia.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0]),
                StoreMessage(id: outside, customStableId: nil, globallyUniqueId: nil, groupingKey: nil, threadId: nil,
                    timestamp: 110, flags: [.Incoming], tags: [], globalTags: [], localTags: [], forwardInfo: nil,
                    authorId: self.chatPeerId, text: "outside removal range", attributes: [], media: [outsideMedia.0])], location: .Random)
            _internal_clearHistoryInRange(transaction: transaction, mediaBox: postbox.mediaBox, peerId: peer, threadId: nil,
                minTimestamp: 101, maxTimestamp: 109, namespaces: .all)
        })
        XCTAssertNotNil(try self.message(id)); XCTAssertTrue(try self.captures().isEmpty)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _internal_clearHistoryInRange(transaction: transaction, mediaBox: postbox.mediaBox, peerId: peer, threadId: nil,
                minTimestamp: 100, maxTimestamp: 100, namespaces: .all)
        })
        XCTAssertNil(try self.message(id)); XCTAssertEqual(try self.captures().map(\.messageId), [id])
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
        XCTAssertNotNil(try self.message(outside))
        XCTAssertEqual(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(outsideMedia.1.id).complete), outsideMedia.2)
    }

    func testSecretCleanupRemainsActiveWhenExtendedRetentionIsOff() throws {
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 14)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        XCTAssertNil(try self.message(id))
        XCTAssertTrue(try self.captures().isEmpty)
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
    }

    func testExplicitDeleteMediaFalseKeepsItsExistingCacheSemantics() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 15)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id], deleteMedia: false)
        })
        postbox.mediaBox.dataQueue.sync {}
        XCTAssertNil(try self.message(id))
        XCTAssertEqual(try self.captures().map(\.messageId), [id])
        XCTAssertEqual(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(media.1.id).complete), media.2)
    }

    func testExtendedDeletionObserversDeduplicateTheSamePendingSnapshot() throws {
        try self.configureExtended(secret: false, timed: true)
        let id = self.id(70), owner = self.accountPeerId, postbox = self.fixture.postbox!
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id,
                attributes: [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: 1)])], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: owner, ids: [id])
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        XCTAssertEqual(try self.captures().count, 1)
    }

    func testExtendedOptInPreferencesPersistAndMissingLegacyFieldsStayOff() throws {
        let configured = LocalArchiveSettings(enabled: true, includeSecretChats: true, includeExpiringMessages: true)
        var legacy = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(configured)) as? [String: Any])
        legacy.removeValue(forKey: "includeSecretChats"); legacy.removeValue(forKey: "includeExpiringMessages")
        let decoded = try JSONDecoder().decode(LocalArchiveSettings.self, from: JSONSerialization.data(withJSONObject: legacy))
        XCTAssertFalse(decoded.includeSecretChats || decoded.includeExpiringMessages)
        XCTAssertTrue(decoded.enabled && decoded.isSupported)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: configured)
        })
        try self.fixture.reopen()
        let actual = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertEqual(actual, configured)
    }

    func testExtendedResourceIdentifiersAreCanonicalAndNeverPaths() {
        for key in ["secret-file-1-1", "secret-file--1-1", "secret-file-9223372036854775807-2", "secret-file--9223372036854775808-2", "telegram-local-file-0", "telegram-local-file--19"] {
            XCTAssertTrue(localArchiveSafeResourceKey(key), key)
        }
        for key in ["secret-file-", "secret-file-1", "secret-file-1-0", "secret-file-1--1", "secret-file-01-1", "secret-file-1-01", "secret-file-+1-1", "secret-file-1-2147483648", "secret-file-9223372036854775808-1", "secret-file-1-1/../key", "telegram-local-file-01", "telegram-local-file--0", "telegram-local-file-1/../key", "telegram-local-file-1\0", "../telegram-cloud-photo", "/tmp/photo", "secret-file-١-1"] {
            XCTAssertFalse(localArchiveSafeResourceKey(key), key)
        }
    }

    func testExtendedMetadataCannotMasqueradeAsOrdinaryCloudContent() throws {
        let media: [[String: Any]] = [["kind": "photo", "resourceKey": "secret-file-1-1", "mimeType": "image/jpeg", "bytesPreserved": false]]
        let ordinary: [String: Any] = ["version": 2, "entities": [], "media": media]
        XCTAssertFalse(localArchiveEntityMetadataIsValid(try JSONSerialization.data(withJSONObject: ordinary)))
        var secret: [String: Any] = ["version": 3, "entities": [], "media": media,
            "retention": ["secretChat": true, "expiring": false, "viewOnce": false]]
        let valid = try JSONSerialization.data(withJSONObject: secret)
        XCTAssertTrue(localArchiveEntityMetadataIsValid(valid))
        let forged = LocalArchiveCaptureOperation(generation: 0, messageId: self.id(100), kindRawValue: 1,
            text: "fixture", entities: valid, authorId: nil, sentAt: 1, observedAt: 2, serverEditAt: nil, botConversation: false)
        XCTAssertNil(forged.makeCapture(accountId: 1))
        secret["retention"] = ["secretChat": true, "expiring": false, "viewOnce": true]
        XCTAssertFalse(localArchiveEntityMetadataIsValid(try JSONSerialization.data(withJSONObject: secret)))
    }

    private struct ExtendedFixtureKey: ArchiveKeyProvider {
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data { Data(repeating: 0x58, count: 32) }
    }

    private enum SecretDrainOutcome {
        case committed, quotaRejected, excluded, disabled, purged, storageUnavailable
    }

    /// Start draining only after canonical cache removal. These cases prove that
    /// cleanup has no dependency on archive success or an in-memory source owner.
    private func assertSecretCleanupIndependentOfArchive(_ outcome: SecretDrainOutcome) throws {
        try self.configureExtended(secret: true, timed: true)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto()
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 96)
        var postbox: Postbox? = self.fixture.postbox
        postbox!.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let paths = postbox!.mediaBox.storePathsForId(media.1.id)
        // Include real owned partial/meta paths in the production removal scope.
        try Data([0x31]).write(to: URL(fileURLWithPath: paths.partial))
        try Data([0x32]).write(to: URL(fileURLWithPath: paths.partial + ".meta"))
        let _ = try self.localArchiveValue(postbox!.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId,
                attributes: [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: 1)],
                media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox!.mediaBox, ids: [id])
        })
        self.assertSecretCacheRemoved(postbox!.mediaBox, resourceId: media.1.id)
        XCTAssertNil(try self.message(id))
        XCTAssertEqual(try self.captures().count, 1)

        let _ = try self.localArchiveValue(postbox!.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            switch outcome {
            case .quotaRejected: settings.maxPayloadBytes = 1
            case .excluded: settings.excludedPeerIds.insert(peer.toInt64())
            case .disabled: settings.enabled = false
            case .purged:
                // The persisted request produced by requestPurge, before a sink opens.
                settings.generation += 1
                settings.pendingPurge = true
            case .committed, .storageUnavailable: break
            }
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        // Reopening establishes that cleanup does not depend on a live capture
        // callback. It is not an actual OS-kill or physical-device test.
        postbox = nil
        try self.fixture.reopen()
        postbox = self.fixture.postbox
        self.assertSecretCacheRemoved(postbox!.mediaBox, resourceId: media.1.id)

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("secret-cleanup-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let attempted = ValuePromise<Bool>(false, ignoreRepeated: true)
        var archive: LocalArchiveCoordinator? = LocalArchiveCoordinator(postbox: postbox!, accountPeerId: self.accountPeerId,
            accountId: 9461, testingEnvironment: true, baseDirectory: directory,
            storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                attempted.set(true)
                if case .storageUnavailable = outcome {
                    completion(.failure(.protectionUnavailable))
                    return
                }
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: ExtendedFixtureKey(),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
                    switch result {
                    case let .success(store): _ = opened.swap(store); completion(.success(store))
                    case let .failure(error): completion(.failure(error))
                    }
                }
            }, protectedData: .single(true))
        defer {
            archive?.dispose()
            if let store = opened.swap(nil) {
                let closed = self.expectation(description: "close secret cleanup archive")
                store.close { closed.fulfill() }
                self.wait(for: [closed], timeout: 15)
            }
            if FileManager.default.fileExists(atPath: directory.path) { try? FileManager.default.removeItem(at: directory) }
        }
        switch outcome {
        case .disabled:
            _ = try self.localArchiveValue(archive!.status |> filter { $0 == .disabled })
            XCTAssertNil(opened.with { $0 })
            XCTAssertEqual(try self.captures().count, 1)
        case .storageUnavailable:
            _ = try self.localArchiveValue(attempted.get() |> filter { $0 })
            _ = try self.localArchiveValue(archive!.status |> filter { $0 == .unavailable })
            XCTAssertEqual(try self.captures().count, 1)
        case .committed, .quotaRejected, .excluded, .purged:
            _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
            XCTAssertTrue(try self.captures().isEmpty)
            let page = try self.localArchiveValue(archive!.deletedMessages())
            XCTAssertFalse(page.unavailable)
            let records = page.records
            if case .committed = outcome {
                let record = try XCTUnwrap(records.first)
                XCTAssertEqual(records.count, 1)
                XCTAssertEqual(record.text, "original text")
                XCTAssertEqual(record.storedMediaCount, 0)
                XCTAssertEqual(record.unavailableMediaCount, 1)
                XCTAssertNil(try self.localArchiveValue(archive!.mediaData(eventKey: record.eventKey, slot: 0)))
            } else {
                XCTAssertTrue(records.isEmpty)
            }
            if case .purged = outcome {
                let settings = try self.localArchiveValue(archive!.settings)
                XCTAssertFalse(settings.pendingPurge)
                XCTAssertEqual(settings.generation, 1)
            }
        }
        self.assertSecretCacheRemoved(postbox!.mediaBox, resourceId: media.1.id)
        archive?.dispose()
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .unavailable })
        archive = nil
    }

    func testSecretCacheCleanupDoesNotWaitForSuccessfulMetadataArchive() throws {
        try self.assertSecretCleanupIndependentOfArchive(.committed)
    }

    func testSecretCacheCleanupSurvivesArchiveQuotaRejection() throws {
        try self.assertSecretCleanupIndependentOfArchive(.quotaRejected)
    }

    func testSecretCacheCleanupSurvivesExclusionBeforeArchiveDrain() throws {
        try self.assertSecretCleanupIndependentOfArchive(.excluded)
    }

    func testSecretCacheCleanupSurvivesDisabledArchiveBeforeDrain() throws {
        try self.assertSecretCleanupIndependentOfArchive(.disabled)
    }

    func testSecretCacheCleanupSurvivesPendingArchivePurge() throws {
        try self.assertSecretCleanupIndependentOfArchive(.purged)
    }

    func testSecretCacheCleanupDoesNotWaitForAvailableArchiveStorage() throws {
        try self.assertSecretCleanupIndependentOfArchive(.storageUnavailable)
    }

    func testSecretCleanupDoesNotTouchSameResourceInAnotherAccountOrUnrelatedLiveMedia() throws {
        try self.configureExtended(secret: true, timed: false)
        let other = try LocalArchivePostboxFixture(test: self, name: "secret-cleanup-other-account")
        defer { try? other.destroy() }
        let peer = try self.secretPeer(), secretMedia = self.completedSecretPhoto(), liveMedia = self.completedPhoto()
        let deleted = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 97)
        let live = self.id(98), foreign = self.id(99)
        let foreignOwner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(2))
        let postbox = self.fixture.postbox!
        postbox.mediaBox.storeResourceData(secretMedia.1.id, data: secretMedia.2, synchronous: true)
        postbox.mediaBox.storeResourceData(liveMedia.1.id, data: liveMedia.2, synchronous: true)
        other.postbox.mediaBox.storeResourceData(secretMedia.1.id, data: secretMedia.2, synchronous: true)
        let _ = try self.localArchiveValue(other.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(foreignOwner), localArchiveTestUser(self.chatPeerId)], update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: foreignOwner, state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            _ = transaction.addMessages([localArchiveTestMessage(foreign, media: [secretMedia.0])], location: .Random)
        })
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(deleted, authorId: self.chatPeerId, media: [secretMedia.0]),
                localArchiveTestMessage(live, media: [liveMedia.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [deleted])
        })
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: secretMedia.1.id)
        other.postbox.mediaBox.dataQueue.sync {}
        XCTAssertNotNil(try self.message(live))
        XCTAssertNotNil(try self.localArchiveValue(other.postbox.transaction { $0.getMessage(foreign) }))
        XCTAssertEqual(try localArchiveReadCompletedMedia(postbox.mediaBox.storePathsForId(liveMedia.1.id).complete), liveMedia.2)
        XCTAssertEqual(try localArchiveReadCompletedMedia(other.postbox.mediaBox.storePathsForId(secretMedia.1.id).complete), secretMedia.2)
    }

    private func assertEncryptedExtendedMediaAfterCacheCleanup(secret: Bool, liveOwner: Bool = false) throws {
        try self.configureExtended(secret: secret, timed: true)
        let peer = secret ? try self.secretPeer() : self.chatPeerId
        let id = MessageId(peerId: peer, namespace: secret ? Namespaces.Message.SecretIncoming : Namespaces.Message.Cloud, id: secret ? 0 : 80)
        let media = secret ? self.completedSecretPhoto() : self.completedPhoto()
        var postbox: Postbox? = self.fixture.postbox
        postbox!.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        func deleteMessage() throws {
            let _ = try self.localArchiveValue(postbox!.transaction { transaction in
                _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId,
                    attributes: [AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil),
                        ConsumableContentMessageAttribute(consumed: false)], media: [media.0])], location: .Random)
                if secret {
                    _internal_deleteMessages(transaction: transaction, mediaBox: postbox!.mediaBox, ids: [id])
                } else {
                    markMessageContentAsConsumedRemotely(transaction: transaction, messageId: id, consumeDate: 1)
                }
            })
            if secret { self.assertSecretCacheRemoved(postbox!.mediaBox, resourceId: media.1.id) }
        }
        // Without an active owner, metadata is captured but normally erased secret
        // bytes cannot be recovered later. The live-owner case pins before unlink.
        if !liveOwner { try deleteMessage() }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("extended-archive-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let opened = Atomic<ArchiveStore?>(value: nil)
        let holdNextClock = Atomic<Bool>(value: false)
        let clockTimedOut = Atomic<Bool>(value: false)
        let writerBlocked = liveOwner ? self.expectation(description: "archive worker blocked before deleted media import") : nil
        let releaseWriter = DispatchSemaphore(value: 0)
        defer { releaseWriter.signal() }
        let owner = self.accountPeerId
        func start(_ postbox: Postbox) -> LocalArchiveCoordinator {
            LocalArchiveCoordinator(postbox: postbox, accountPeerId: owner, accountId: 9461, testingEnvironment: true, baseDirectory: directory,
                storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                    ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: ExtendedFixtureKey(),
                        createIfMissing: createIfMissing, retention: retention, callbackQueue: queue, clock: {
                            if holdNextClock.swap(false) {
                                writerBlocked?.fulfill()
                                if releaseWriter.wait(timeout: .now() + 8) == .timedOut { _ = clockTimedOut.swap(true) }
                            }
                            return Int64(Date().timeIntervalSince1970)
                        }) { result in
                        switch result {
                        case let .success(store): _ = opened.swap(store); completion(.success(store))
                        case let .failure(error): completion(.failure(error))
                        }
                    }
                }, protectedData: .single(true))
        }
        func stop(_ archive: LocalArchiveCoordinator) throws {
            archive.dispose()
            _ = try self.localArchiveValue(archive.status |> filter { $0 == .unavailable })
            if let store = opened.swap(nil) {
                let done = self.expectation(description: "close extended encrypted archive")
                store.close { done.fulfill() }; self.wait(for: [done], timeout: 15)
            }
        }
        var archive: LocalArchiveCoordinator? = start(postbox!)
        defer { if let archive { try? stop(archive) } }
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
        if liveOwner {
            let revision = try self.localArchiveValue(archive!.historyRevision)
            let settings = try self.localArchiveValue(archive!.settings)
            let store = try XCTUnwrap(opened.with { $0 })
            let priorOperationDone = self.expectation(description: "prior retention operation released")
            _ = holdNextClock.swap(true)
            defer { releaseWriter.signal() }
            // Block an independent earlier worker operation before deletion starts.
            // This cannot pass merely because a media API opened the URL before
            // its own clock callback: no media operation can reach the worker yet.
            store.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: settings.maxAgeSeconds,
                maxPayloadBytes: settings.maxPayloadBytes)) { result in
                if case .failure = result { XCTFail("Prior real archive operation failed") }
                priorOperationDone.fulfill()
            }
            self.wait(for: [try XCTUnwrap(writerBlocked)], timeout: 15)
            try deleteMessage()
            // The real MediaBox paths are gone while SQLite cannot copy anything.
            // A URL-only handoff cannot satisfy the subsequent byte assertions.
            self.assertSecretCacheRemoved(postbox!.mediaBox, resourceId: media.1.id)
            releaseWriter.signal()
            self.wait(for: [priorOperationDone], timeout: 15)
            _ = try self.localArchiveValue(archive!.historyRevision |> filter { $0 > revision })
            _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
            XCTAssertFalse(clockTimedOut.with { $0 })
        }
        let rows = try self.localArchiveValue(archive!.deletedMessages())
        XCTAssertFalse(rows.unavailable)
        let row = try XCTUnwrap(rows.records.first)
        XCTAssertEqual(rows.records.count, 1)
        XCTAssertEqual(row.messageId, id.id)
        XCTAssertEqual(row.namespace, id.namespace)
        let mediaExpected = !secret || liveOwner
        XCTAssertEqual(row.storedMediaCount, mediaExpected ? 1 : 0)
        XCTAssertEqual(row.unavailableMediaCount, mediaExpected ? 0 : 1)
        XCTAssertTrue(try self.captures().isEmpty, "durable commit precedes acknowledgement")
        if !secret {
            let removalDone = self.expectation(description: "clear actual MediaBox cache")
            let removal = postbox!.mediaBox.removeCachedResources([media.1.id], force: true).start(completed: { removalDone.fulfill() })
            self.wait(for: [removalDone], timeout: 15); removal.dispose()
        }
        XCTAssertNil(try localArchiveReadCompletedMedia(postbox!.mediaBox.storePathsForId(media.1.id).complete))
        let expectedMedia: Data? = mediaExpected ? media.2 : nil
        XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: row.eventKey, slot: 0)), expectedMedia)
        XCTAssertTrue(try self.localArchiveValue(archive!.setEnabled(false)))
        try stop(archive!); archive = nil; postbox = nil
        try self.fixture.reopen()
        archive = start(self.fixture.postbox)
        let lease = archive!.acquireReadAccess()
        defer { lease.dispose() }
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .browsing })
        XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: row.eventKey, slot: 0)), expectedMedia)
        XCTAssertEqual(try self.localArchiveValue(archive!.timeline(peerId: peer.toInt64(), namespace: id.namespace, messageId: id.id)).records.count, 1)
        XCTAssertFalse(try self.localArchiveValue(archive!.settings).enabled)
    }

    func testActualEncryptedViewOnceArchiveSurvivesCacheCleanupAndReopen() throws {
        try self.assertEncryptedExtendedMediaAfterCacheCleanup(secret: false)
    }
    func testSecretMetadataArchiveSurvivesCleanupAndReopenWithoutClaimingMissingMedia() throws {
        try self.assertEncryptedExtendedMediaAfterCacheCleanup(secret: true)
    }

    func testLiveSecretMediaOwnerPreservesBytesWhileCanonicalCleanupPrecedesArchiveWrite() throws {
        try self.assertEncryptedExtendedMediaAfterCacheCleanup(secret: true, liveOwner: true)
    }

    func testSecretDeletionWithoutPinnedOwnerDoesNotReopenURLWhileCleanupIsQueued() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 101)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let cacheBlocked = self.expectation(description: "MediaBox queue held before ordinary cleanup")
        let releaseCache = DispatchSemaphore(value: 0)
        let cacheTimedOut = Atomic<Bool>(value: false)
        postbox.mediaBox.dataQueue.async {
            cacheBlocked.fulfill()
            if releaseCache.wait(timeout: .now() + 30) == .timedOut { _ = cacheTimedOut.swap(true) }
        }
        defer { releaseCache.signal(); postbox.mediaBox.dataQueue.sync {} }
        self.wait(for: [cacheBlocked], timeout: 15)
        // There is no coordinator/descriptor owner at this destructive boundary.
        // Only its real queued cache removal is held; the source URL still exists.
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        XCTAssertEqual(try self.captures().count, 1)
        let sourcePath = postbox.mediaBox.storePathsForId(media.1.id).complete
        XCTAssertEqual(try localArchiveReadCompletedMedia(sourcePath), media.2)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("secret-refused-source-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let archive = LocalArchiveCoordinator(postbox: postbox, accountPeerId: self.accountPeerId,
            accountId: 9461, testingEnvironment: true, baseDirectory: directory,
            storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: ExtendedFixtureKey(),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
                    switch result {
                    case let .success(store): _ = opened.swap(store); completion(.success(store))
                    case let .failure(error): completion(.failure(error))
                    }
                }
            }, protectedData: .single(true))
        defer {
            releaseCache.signal()
            archive.dispose()
            if let store = opened.swap(nil) {
                let closed = self.expectation(description: "close refused-source archive")
                store.close { closed.fulfill() }; self.wait(for: [closed], timeout: 15)
            }
            if FileManager.default.fileExists(atPath: directory.path) { try? FileManager.default.removeItem(at: directory) }
        }
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .ready })
        let page = try self.localArchiveValue(archive.deletedMessages())
        XCTAssertFalse(page.unavailable)
        let record = try XCTUnwrap(page.records.first)
        XCTAssertEqual(page.records.count, 1)
        XCTAssertEqual(record.storedMediaCount, 0)
        XCTAssertEqual(record.unavailableMediaCount, 1)
        XCTAssertTrue(try self.captures().isEmpty)
        XCTAssertNil(try self.localArchiveValue(archive.mediaData(eventKey: record.eventKey, slot: 0)))
        XCTAssertEqual(try localArchiveReadCompletedMedia(sourcePath), media.2,
            "Missing archived media must follow the refused handoff even while its old URL remains readable")
        XCTAssertFalse(cacheTimedOut.with { $0 })
        releaseCache.signal()
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
    }

    func testPinnedSecretSourceExpiresWithoutReopeningReplacedCachePath() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let settings = try self.localArchiveValue(postbox.transaction { localArchiveSettings(transaction: $0) })
        let now = Atomic<TimeInterval>(value: 100)
        let handoff = LocalArchiveMediaSourceHandoff(mediaBox: postbox.mediaBox, accountPeerId: self.accountPeerId,
            accountId: 9461, testingEnvironment: true, clock: { now.with { $0 } })
        defer { handoff.dispose() }
        handoff.applySettings(settings); handoff.setProtectedDataAvailable(true)
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 102)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
        let operation = try XCTUnwrap(try self.captures().first)
        let key = media.1.id.stringRepresentation
        guard case .source = handoff.source(eventKey: operation.eventKey, generation: operation.generation, slot: 0, resourceKey: key) else {
            XCTFail("Eligible completed secret media was not pinned"); return
        }
        _ = now.swap(100 + LocalArchiveMediaSourceHandoff.lifetime + 1)
        postbox.mediaBox.storeResourceData(media.1.id, data: Data([8, 6, 4]), synchronous: true)
        guard case .revoked = handoff.source(eventKey: operation.eventKey, generation: operation.generation, slot: 0, resourceKey: key) else {
            XCTFail("Expired lease must not become an absent URL fallback"); return
        }
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            localArchivePinMediaBeforeRemoval(transaction: transaction, mediaBox: postbox.mediaBox,
                resourceIds: [media.1.id], capturedResources: Set([media.1.id]))
        })
        if case .revoked = handoff.source(eventKey: operation.eventKey, generation: operation.generation, slot: 0, resourceKey: key) {} else {
            XCTFail("Expired event must not be re-pinned from a replacement path")
        }
    }

    func testDisablingAndReenablingSecretRetentionDoesNotReviveRevokedEvent() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let settings = try self.localArchiveValue(postbox.transaction { localArchiveSettings(transaction: $0) })
        let handoff = LocalArchiveMediaSourceHandoff(mediaBox: postbox.mediaBox, accountPeerId: self.accountPeerId,
            accountId: 9461, testingEnvironment: true)
        defer { handoff.dispose() }
        handoff.applySettings(settings); handoff.setProtectedDataAvailable(true)
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 103)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
            _internal_deleteMessages(transaction: transaction, mediaBox: postbox.mediaBox, ids: [id])
        })
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
        let operation = try XCTUnwrap(try self.captures().first)
        let key = media.1.id.stringRepresentation
        guard case .source = handoff.source(eventKey: operation.eventKey, generation: operation.generation, slot: 0, resourceKey: key) else {
            XCTFail("Eligible completed secret media was not pinned"); return
        }
        var disabled = settings
        disabled.includeSecretChats = false
        handoff.applySettings(disabled)
        handoff.applySettings(settings)
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            localArchivePinMediaBeforeRemoval(transaction: transaction, mediaBox: postbox.mediaBox,
                resourceIds: [media.1.id], capturedResources: Set([media.1.id]))
        })
        if case .revoked = handoff.source(eventKey: operation.eventKey, generation: operation.generation, slot: 0, resourceKey: key) {} else {
            XCTFail("Same-generation policy cancellation must survive reenabling")
        }
    }

    func testSecretDescriptorLeasesStayWithTheirAccountForIdenticalResourceKeys() throws {
        try self.configureExtended(secret: true, timed: false)
        let peer = try self.secretPeer(), media = self.completedSecretPhoto(), postbox = self.fixture.postbox!
        let other = try LocalArchivePostboxFixture(test: self, name: "secret-source-other-account")
        defer { try? other.destroy() }
        let otherOwner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(2))
        let settings = try self.localArchiveValue(postbox.transaction { localArchiveSettings(transaction: $0) })
        let _ = try self.localArchiveValue(other.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(otherOwner), localArchiveTestUser(self.chatPeerId),
                TelegramSecretChat(id: peer, creationDate: 0, regularPeerId: self.chatPeerId, accessHash: 0,
                    role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)], update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: otherOwner, state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        let localHandoff = LocalArchiveMediaSourceHandoff(mediaBox: postbox.mediaBox, accountPeerId: self.accountPeerId,
            accountId: 9461, testingEnvironment: true)
        let otherHandoff = LocalArchiveMediaSourceHandoff(mediaBox: other.postbox.mediaBox, accountPeerId: otherOwner,
            accountId: 9462, testingEnvironment: true)
        defer { localHandoff.dispose(); otherHandoff.dispose() }
        for handoff in [localHandoff, otherHandoff] {
            handoff.applySettings(settings); handoff.setProtectedDataAvailable(true)
        }
        let otherBytes = media.2 + Data([0x41])
        postbox.mediaBox.storeResourceData(media.1.id, data: media.2, synchronous: true)
        other.postbox.mediaBox.storeResourceData(media.1.id, data: otherBytes, synchronous: true)
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 104)
        func delete(_ database: Postbox, owner: PeerId) throws -> LocalArchiveCaptureOperation {
            let operation: LocalArchiveCaptureOperation? = try self.localArchiveValue(database.transaction { transaction in
                _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId, media: [media.0])], location: .Random)
                _internal_deleteMessages(transaction: transaction, mediaBox: database.mediaBox, ids: [id])
                return localArchivePendingCaptures(transaction: transaction, accountPeerId: owner).compactMap { $0.operation }.first
            })
            return try XCTUnwrap(operation)
        }
        let localOperation = try delete(postbox, owner: self.accountPeerId)
        let key = media.1.id.stringRepresentation
        self.assertSecretCacheRemoved(postbox.mediaBox, resourceId: media.1.id)
        guard case let .source(localSource) = localHandoff.source(eventKey: localOperation.eventKey,
            generation: localOperation.generation, slot: 0, resourceKey: key) else {
            XCTFail("First account did not own its completed source"); return
        }
        XCTAssertEqual(localSource.source.byteCount, Int64(media.2.count))
        if case .absent = otherHandoff.source(eventKey: localOperation.eventKey,
            generation: localOperation.generation, slot: 0, resourceKey: key) {} else {
            XCTFail("Second account must not resolve the first account's event")
        }
        XCTAssertEqual(try localArchiveReadCompletedMedia(other.postbox.mediaBox.storePathsForId(media.1.id).complete), otherBytes)
        let otherOperation = try delete(other.postbox, owner: otherOwner)
        self.assertSecretCacheRemoved(other.postbox.mediaBox, resourceId: media.1.id)
        XCTAssertNotEqual(localOperation.eventKey, otherOperation.eventKey)
        guard case let .source(otherSource) = otherHandoff.source(eventKey: otherOperation.eventKey,
            generation: otherOperation.generation, slot: 0, resourceKey: key) else {
            XCTFail("Second account did not own its completed source"); return
        }
        XCTAssertEqual(otherSource.source.byteCount, Int64(otherBytes.count))
        if case .absent = localHandoff.source(eventKey: otherOperation.eventKey,
            generation: otherOperation.generation, slot: 0, resourceKey: key) {} else {
            XCTFail("First account must not resolve the second account's event")
        }
    }

    func testSecretZeroIdentifierRequiresAnExplicitPersistedIdentifierField() throws {
        let peer = try self.secretPeer()
        let metadata = Data("{\"version\":3,\"entities\":[],\"media\":[],\"retention\":{\"secretChat\":true,\"expiring\":false,\"viewOnce\":false}}".utf8)
        func decoded(includeIdentifier: Bool) -> LocalArchiveCaptureOperation {
            let encoder = PostboxEncoder()
            encoder.encodeInt32(1, forKey: "v")
            encoder.encodeString("owned-zero-id-fixture", forKey: "k")
            encoder.encodeInt64(0, forKey: "g")
            encoder.encodeInt64(peer.toInt64(), forKey: "p")
            encoder.encodeInt32(Namespaces.Message.SecretIncoming, forKey: "n")
            if includeIdentifier { encoder.encodeInt32(0, forKey: "i") }
            encoder.encodeInt32(ArchiveEventKind.deleted.rawValue, forKey: "t")
            encoder.encodeString("owned synthetic text", forKey: "x")
            encoder.encodeData(metadata, forKey: "e")
            encoder.encodeNil(forKey: "a")
            encoder.encodeInt64(1, forKey: "s")
            encoder.encodeInt64(2, forKey: "o")
            encoder.encodeNil(forKey: "d")
            encoder.encodeBool(false, forKey: "b")
            return LocalArchiveCaptureOperation(decoder: PostboxDecoder(buffer: MemoryBuffer(data: encoder.makeData())))
        }
        XCTAssertEqual(decoded(includeIdentifier: true).makeCapture(accountId: 1)?.event.message.messageId, 0)
        XCTAssertNil(decoded(includeIdentifier: false).makeCapture(accountId: 1))
    }

    func testServerFetchedExpiryPreservesOnlyOptedAvailableOriginalContent() throws {
        let photo = self.completedPhoto()
        for enabled in [false, true] {
            try self.configureExtended(secret: false, timed: enabled)
            let id = self.id(enabled ? 92 : 91)
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                _ = transaction.addMessages([localArchiveTestMessage(id, attributes: [
                    AutoclearTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)
                ], media: [photo.0])], location: .Random)
                let replacement = localArchiveTestMessage(id, media: [TelegramMediaExpiredContent(data: .image)])
                addMessagesWithLocalArchive(transaction: transaction, messages: [replacement], location: .Random)
                addMessagesWithLocalArchive(transaction: transaction, messages: [replacement], location: .Random)
            })
            XCTAssertNotNil(try self.message(id)?.media.first as? TelegramMediaExpiredContent)
            let rows = try self.captures().filter { $0.messageId == id }
            XCTAssertEqual(rows.count, enabled ? 1 : 0)
            if enabled {
                let row = try XCTUnwrap(rows.first)
                XCTAssertEqual(row.kindRawValue, ArchiveEventKind.deleted.rawValue)
                XCTAssertEqual(localArchiveDecodedMediaMetadata(row.entities)?.first?.kind, .photo)
                XCTAssertTrue(try XCTUnwrap(row.makeCapture(accountId: 1)).context.expiring)
            }
        }
    }

}
