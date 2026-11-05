// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

/// Real local Postbox consumption/deletion and reopen coverage, using owned
/// synthetic media. These verify exclusion and canonical processing, not timed
/// preservation, remote delivery, decoded playback or physical-device behavior.
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
}
