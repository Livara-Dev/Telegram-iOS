// SPDX-License-Identifier: GPL-2.0-or-later
// Created 2026-11-06.
// Authored native integration coverage; compilation and execution require Apple qualification.
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Calls the recipient's local consume API, then lets the real timestamp worker
/// perform expiry/deletion. Never calls either expiry transaction helper itself.
/// Uses complete synthetic media and real Postbox, MediaBox and ArchiveStore.
/// These are native core integration tests, not gallery/player UI or delivery tests.
final class LocalArchiveRecipientExpiryWorkerTests: XCTestCase {
    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    private struct FixtureKey: ArchiveKeyProvider {
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            // Owned synthetic key for this temporary test archive only.
            return Data(repeating: 0x58, count: 32)
        }
    }

    private enum Payload {
        case photo, video, voice, roundVideo

        var kind: String {
            switch self {
            case .photo: return "photo"
            case .video: return "video"
            case .voice: return "voice"
            case .roundVideo: return "videoMessage"
            }
        }

        var expired: TelegramMediaExpiredContentData {
            switch self {
            case .photo: return .image
            case .video: return .file
            case .voice: return .voiceMessage
            case .roundVideo: return .videoMessage
            }
        }

        var mimeType: String {
            switch self {
            case .photo: return "image/jpeg"
            case .voice: return "audio/ogg"
            case .video, .roundVideo: return "video/mp4"
            }
        }

        var bytes: Data {
            switch self {
            case .photo: return RecipientExpiryMediaFixture.jpeg
            case .video: return RecipientExpiryMediaFixture.video
            case .voice: return RecipientExpiryMediaFixture.opusVoice
            case .roundVideo: return RecipientExpiryMediaFixture.roundVideo
            }
        }
    }

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "recipient-expiry-worker")
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([
                localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)
            ], update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: self.accountPeerId,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func media(_ payload: Payload, secret: Bool) -> (Media, MediaResource, Data) {
        let bytes = payload.bytes
        let resource: TelegramMediaResource
        if secret {
            resource = SecretFileMediaResource(fileId: -19401, accessHash: 0,
                containerSize: Int64(bytes.count + 16), decryptedSize: Int64(bytes.count), datacenterId: 1,
                key: SecretFileEncryptionKey(aesKey: Data(repeating: 0x62, count: 32),
                    aesIv: Data(repeating: 0x63, count: 32)))
        } else if case .photo = payload {
            resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 19401, accessHash: 0,
                sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        } else {
            resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 19401, accessHash: 0,
                size: Int64(bytes.count), fileReference: nil, fileName: nil)
        }
        if case .photo = payload {
            let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 19401),
                representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                    resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
                immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
            return (image, resource, bytes)
        }
        let attribute: TelegramMediaFileAttribute
        switch payload {
        case .voice:
            attribute = .Audio(isVoice: true, duration: 1, title: nil, performer: nil, waveform: nil)
        case .roundVideo:
            attribute = .Video(duration: 1, size: PixelDimensions(width: 16, height: 16),
                flags: [.instantRoundVideo], preloadSize: nil, coverTime: nil, videoCodec: nil)
        case .video:
            attribute = .Video(duration: 1, size: PixelDimensions(width: 16, height: 16),
                flags: [], preloadSize: nil, coverTime: nil, videoCodec: nil)
        case .photo:
            preconditionFailure("Photo handled above")
        }
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 19401),
            partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [],
            immediateThumbnailData: nil, mimeType: payload.mimeType, size: Int64(bytes.count),
            attributes: [attribute], alternativeRepresentations: [])
        return (file, resource, bytes)
    }

    private func assertCacheAbsent(_ mediaBox: MediaBox, id: MediaResourceId,
                                   file: StaticString = #filePath, line: UInt = #line) {
        mediaBox.dataQueue.sync {}
        let paths = mediaBox.storePathsForId(id)
        for path in [paths.complete, paths.partial, paths.partial + ".meta"] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: path), file: file, line: line)
        }
    }

    /// A blocked earlier Store operation proves secret canonical unlink occurs
    /// before any archive metadata/media copy. The expiry worker/timer is never blocked.
    private func assertLocalConsumeThroughWorker(_ payload: Payload, timeout: Int32,
        secret: Bool = false, optedIn: Bool = true, completed: Bool = true,
        workerAlreadyRunning: Bool = true, file: StaticString = #filePath, line: UInt = #line) throws {
        let peer: PeerId
        if secret {
            peer = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(19461))
            let secretPeer = TelegramSecretChat(id: peer, creationDate: 0, regularPeerId: self.chatPeerId,
                accessHash: 0, role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                transaction.updatePeersInternal([secretPeer], update: { _, updated in updated })
            })
        } else {
            peer = self.chatPeerId
        }
        let id = MessageId(peerId: peer,
            namespace: secret ? Namespaces.Message.SecretIncoming : Namespaces.Message.Cloud, id: 1)
        let input = self.media(payload, secret: secret)
        var postbox: Postbox? = self.fixture.postbox
        let paths = postbox!.mediaBox.storePathsForId(input.1.id)
        if completed {
            postbox!.mediaBox.storeResourceData(input.1.id, data: input.2, synchronous: true)
            XCTAssertEqual(try localArchiveReadCompletedMedia(paths.complete), input.2, file: file, line: line)
        } else {
            // An owned partial file must not count as a completed cache source.
            try input.2.prefix(7).write(to: URL(fileURLWithPath: paths.partial))
            XCTAssertFalse(FileManager.default.fileExists(atPath: paths.complete), file: file, line: line)
        }
        // Normal secret cleanup must remove these paths too, independently of Store.
        if secret {
            try Data([0x31]).write(to: URL(fileURLWithPath: paths.partial))
            try Data([0x32]).write(to: URL(fileURLWithPath: paths.partial + ".meta"))
        }
        let _ = try self.localArchiveValue(postbox!.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true,
                includeSecretChats: secret, includeExpiringMessages: optedIn))
            let timer: MessageAttribute = secret
                ? AutoremoveTimeoutMessageAttribute(timeout: timeout, countdownBeginTime: nil)
                : AutoclearTimeoutMessageAttribute(timeout: timeout, countdownBeginTime: nil)
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: self.chatPeerId,
                attributes: [timer, ConsumableContentMessageAttribute(consumed: false)], media: [input.0])], location: .Random)
        })
        let inserted = try XCTUnwrap(try self.localArchiveValue(postbox!.transaction { $0.getMessage(id) }), file: file, line: line)
        XCTAssertTrue(inserted.flags.contains(.Incoming), file: file, line: line)
        XCTAssertNil(inserted.autoclearAttribute?.countdownBeginTime, file: file, line: line)
        XCTAssertNil(inserted.autoremoveAttribute?.countdownBeginTime, file: file, line: line)

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("recipient-worker-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let holdNextClock = Atomic<Bool>(value: false)
        let gateTimedOut = Atomic<Bool>(value: false)
        let releaseWriter = DispatchSemaphore(value: 0)
        let writerBlocked = self.expectation(description: "earlier real Store operation blocked")
        let priorOperationDone = self.expectation(description: "earlier real Store operation finished")
        func startArchive(_ box: Postbox) -> LocalArchiveCoordinator {
            return LocalArchiveCoordinator(postbox: box, accountPeerId: self.accountPeerId, accountId: 19461,
                testingEnvironment: true, baseDirectory: directory,
                storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                    ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: FixtureKey(),
                        createIfMissing: createIfMissing, retention: retention, callbackQueue: queue,
                        clock: {
                            if holdNextClock.swap(false) {
                                writerBlocked.fulfill()
                                if releaseWriter.wait(timeout: .now() + 30) == .timedOut {
                                    _ = gateTimedOut.swap(true)
                                }
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
        func stopArchive(_ value: LocalArchiveCoordinator) throws {
            value.dispose()
            _ = try self.localArchiveValue(value.status |> filter { $0 == .unavailable })
            if let store = opened.swap(nil) {
                let closed = self.expectation(description: "real Store closed")
                store.close { closed.fulfill() }
                self.wait(for: [closed], timeout: 15)
            }
        }
        var archive: LocalArchiveCoordinator? = startArchive(postbox!)
        var worker: Disposable?
        var canonicalObserver: Disposable?
        var writingObserver: Disposable?
        defer {
            releaseWriter.signal()
            worker?.dispose()
            canonicalObserver?.dispose()
            writingObserver?.dispose()
            if let archive { try? stopArchive(archive) }
            try? FileManager.default.removeItem(at: directory)
        }
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
        let settings = try self.localArchiveValue(archive!.settings)
        let store = try XCTUnwrap(opened.with { $0 }, file: file, line: line)
        _ = holdNextClock.swap(true)
        store.setRetention(ArchiveRetentionPolicy(maxAgeSeconds: settings.maxAgeSeconds,
            maxPayloadBytes: settings.maxPayloadBytes)) { result in
                if case .failure = result { XCTFail("Earlier Store operation failed", file: file, line: line) }
                priorOperationDone.fulfill()
            }
        self.wait(for: [writerBlocked], timeout: 15)

        let canonical = self.expectation(description: "actual worker performed canonical expiry/removal")
        canonicalObserver = (postbox!.messageView(id)
            |> filter { view in
                if secret { return view.message == nil }
                return view.message?.media.contains(where: { $0 is TelegramMediaExpiredContent }) == true
            }
            |> take(1)).start(next: { _ in canonical.fulfill() })
        let writing = optedIn ? self.expectation(description: "new capture entered writing state") : nil
        if let writing {
            writingObserver = (archive!.status |> filter { $0 == .writing } |> take(1))
                .start(next: { _ in writing.fulfill() })
        }
        func startWorker() -> Disposable {
            // Proposed internal seam removes only the unrelated Network object.
            // Offset zero preserves the real wall clock and original timer delay.
            return managedAutoremoveMessageOperations(postbox: postbox!, isRemove: secret,
                globalTimeDifference: { 0 }).start()
        }
        if workerAlreadyRunning { worker = startWorker() }
        let before = Int32(Date().timeIntervalSince1970)
        _ = try self.localArchiveValue(_internal_markMessageContentAsConsumedInteractively(postbox: postbox!, messageId: id))
        if !workerAlreadyRunning {
            // Catch-up case: inspect the actual locally armed timestamp before
            // subscribing the real worker. No manual countdown/expiry mutation.
            let armed = try XCTUnwrap(try self.localArchiveValue(postbox!.transaction { $0.getMessage(id) }), file: file, line: line)
            XCTAssertEqual(armed.attributes.compactMap { $0 as? ConsumableContentMessageAttribute }.first?.consumed, true, file: file, line: line)
            let begin = try XCTUnwrap(secret ? armed.autoremoveAttribute?.countdownBeginTime : armed.autoclearAttribute?.countdownBeginTime, file: file, line: line)
            XCTAssertGreaterThanOrEqual(begin, before, file: file, line: line)
            XCTAssertLessThanOrEqual(begin, Int32(Date().timeIntervalSince1970), file: file, line: line)
            let timestamp = secret ? armed.autoremoveAttribute?.automaticTimestampBasedAttribute : armed.autoclearAttribute?.automaticTimestampBasedAttribute
            XCTAssertEqual(timestamp?.0, secret ? 0 : 1, file: file, line: line)
            XCTAssertEqual(timestamp?.1, !secret && timeout == viewOnceTimeout ? begin : autoremoveExpiryTimestamp(countdownBeginTime: begin, timeout: timeout), file: file, line: line)
            let head = try self.localArchiveValue(postbox!.timestampBasedMessageAttributesView(tag: secret ? 0 : 1))
            XCTAssertEqual(head.head?.messageId, id, file: file, line: line)
            XCTAssertEqual(head.head?.timestamp, timestamp?.1, file: file, line: line)
            worker = startWorker()
        }
        self.wait(for: [canonical], timeout: 15)
        let pending = try self.localArchiveValue(postbox!.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: self.accountPeerId).compactMap { $0.operation }
        })
        XCTAssertEqual(pending.count, optedIn ? 1 : 0, file: file, line: line)
        if optedIn {
            let capture = try XCTUnwrap(pending.first, file: file, line: line)
            XCTAssertEqual(capture.messageId, id, file: file, line: line)
            let context = try XCTUnwrap(localArchiveCaptureContext(capture.entities), file: file, line: line)
            XCTAssertEqual(context.secretChat, secret, file: file, line: line)
            XCTAssertTrue(context.expiring, file: file, line: line)
            XCTAssertEqual(context.viewOnce, timeout == viewOnceTimeout, file: file, line: line)
        }
        if secret {
            // NO explicit test-side cache removal here: this must be the worker's
            // real _internal_deleteMessages → force:true removal before Store runs.
            self.assertCacheAbsent(postbox!.mediaBox, id: input.1.id, file: file, line: line)
        }
        releaseWriter.signal()
        self.wait(for: [priorOperationDone] + (writing.map { [$0] } ?? []), timeout: 15)
        if optedIn {
            // The observer was installed before release and has now observed
            // the new .writing state, so initial .ready cannot satisfy this wait.
            _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
        }
        XCTAssertFalse(gateTimedOut.with { $0 }, file: file, line: line)
        let drained = try self.localArchiveValue(postbox!.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: self.accountPeerId).isEmpty
        })
        XCTAssertTrue(drained, "Commit and media decision must precede acknowledgement", file: file, line: line)
        let page = try self.localArchiveValue(archive!.deletedMessages())
        XCTAssertFalse(page.unavailable, file: file, line: line)
        XCTAssertEqual(page.records.count, optedIn ? 1 : 0, file: file, line: line)
        let eventKey = page.records.first?.eventKey
        let expectedBytes: Data? = optedIn && completed ? input.2 : nil
        if let row = page.records.first {
            XCTAssertEqual(row.messageId, id.id, file: file, line: line)
            XCTAssertEqual(row.namespace, id.namespace, file: file, line: line)
            XCTAssertEqual(row.peerId, peer.toInt64(), file: file, line: line)
            XCTAssertEqual(row.mediaKinds, [payload.kind], file: file, line: line)
            XCTAssertEqual(row.storedMediaCount, completed ? 1 : 0, file: file, line: line)
            XCTAssertEqual(row.unavailableMediaCount, completed ? 0 : 1, file: file, line: line)
        }
        if !secret {
            // Cloud media expiry intentionally does not unlink the cache. Exercise
            // the actual standard MediaBox removal API after commit, not FileManager.
            // This establishes persistence, NOT survival of pre-acquire eviction.
            let removed = self.expectation(description: "standard MediaBox cache removal finished")
            let removal = postbox!.mediaBox.removeCachedResources([input.1.id], force: true)
                .start(completed: { removed.fulfill() })
            self.wait(for: [removed], timeout: 15)
            removal.dispose()
        }
        self.assertCacheAbsent(postbox!.mediaBox, id: input.1.id, file: file, line: line)
        if let eventKey {
            XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: eventKey, slot: 0)), expectedBytes, file: file, line: line)
        }
        worker?.dispose(); worker = nil
        canonicalObserver?.dispose(); canonicalObserver = nil
        writingObserver?.dispose(); writingObserver = nil
        XCTAssertTrue(try self.localArchiveValue(archive!.setEnabled(false)), file: file, line: line)
        try stopArchive(archive!); archive = nil; postbox = nil
        // Drop local references to the old Postbox before opening the same DB.
        try self.fixture.reopen()
        archive = startArchive(self.fixture.postbox)
        let readAccess = archive!.acquireReadAccess()
        defer { readAccess.dispose() }
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .browsing })
        XCTAssertFalse(try self.localArchiveValue(archive!.settings).enabled, file: file, line: line)
        let reopenedPage = try self.localArchiveValue(archive!.timeline(peerId: peer.toInt64(), namespace: id.namespace, messageId: id.id))
        XCTAssertFalse(reopenedPage.unavailable, file: file, line: line)
        XCTAssertEqual(reopenedPage.records.count, optedIn ? 1 : 0, file: file, line: line)
        if let eventKey {
            XCTAssertEqual(reopenedPage.records.first?.eventKey, eventKey, file: file, line: line)
            XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: eventKey, slot: 0)), expectedBytes, file: file, line: line)
        }
        let canonicalAfterReopen = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) })
        if secret {
            XCTAssertNil(canonicalAfterReopen, file: file, line: line)
        } else {
            let expired = try XCTUnwrap(canonicalAfterReopen?.media.first as? TelegramMediaExpiredContent, file: file, line: line)
            XCTAssertEqual(expired.data.rawValue, payload.expired.rawValue, file: file, line: line)
        }
        self.assertCacheAbsent(self.fixture.postbox.mediaBox, id: input.1.id, file: file, line: line)
    }

    func testIncomingTimedPhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: 1)
    }
    func testIncomingTimedVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.voice, timeout: 1)
    }
    func testIncomingTimedVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.video, timeout: 1)
    }
    func testIncomingTimedRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.roundVideo, timeout: 1)
    }
    func testIncomingViewOncePhotoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: viewOnceTimeout)
    }
    func testIncomingViewOnceVoiceUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.voice, timeout: viewOnceTimeout)
    }
    func testIncomingViewOnceVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.video, timeout: viewOnceTimeout)
    }
    func testIncomingViewOnceRoundVideoUsesLiveWorkerAndPersistsBytesAfterCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.roundVideo, timeout: viewOnceTimeout)
    }
    func testViewOnceWorkerCatchesUpFromActualLocallyArmedTimestamp() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: viewOnceTimeout, workerAlreadyRunning: false)
    }
    func testSecretTimedPhotoWorkerUnlinksCacheBeforeStoreCanCommit() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: 1, secret: true)
    }
    func testSecretTimedVoiceWorkerUnlinksCacheBeforeStoreCanCommit() throws {
        try self.assertLocalConsumeThroughWorker(.voice, timeout: 1, secret: true)
    }
    func testCloudTimedVoiceOptOutDoesNotPreventCanonicalExpiry() throws {
        try self.assertLocalConsumeThroughWorker(.voice, timeout: 1, optedIn: false)
    }
    func testCloudViewOncePhotoOptOutDoesNotPreventCanonicalExpiry() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: viewOnceTimeout, optedIn: false)
    }
    func testSecretTimedPhotoOptOutDoesNotPreventCanonicalCacheRemoval() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: 1, secret: true, optedIn: false)
    }
    func testCloudViewOnceVoiceDoesNotTreatPartialCacheAsRetainedBytes() throws {
        try self.assertLocalConsumeThroughWorker(.voice, timeout: viewOnceTimeout, completed: false)
    }
    func testSecretTimedPhotoPartialCacheIsRemovedWithoutInventingArchivedBytes() throws {
        try self.assertLocalConsumeThroughWorker(.photo, timeout: 1, secret: true, completed: false)
    }
}
