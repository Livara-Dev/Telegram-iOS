// SPDX-License-Identifier: GPL-2.0-or-later
// Draft 2026-11-06: native execution and full Account initialization unverified.
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// Real Postbox/coordinator/worker ordering contract. This class does not create
/// a full Account or claim that source-only Account wiring was executed here.
final class LocalArchiveStartupOrderingTests: XCTestCase {
    private enum Order { case coordinatorFirst, workerFirst, protectionFailure }
    private struct FixtureKey: ArchiveKeyProvider {
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            return Data(repeating: 0x59, count: 32) // Owned temporary fixture only.
        }
    }

    private func assertRestoredDueTimer(_ order: Order,
        file: StaticString = #filePath, line: UInt = #line) throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "startup-order")
        defer { try? fixture.destroy() }
        let foreign = try LocalArchivePostboxFixture(test: self, name: "startup-order-other")
        defer { try? foreign.destroy() }
        let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let author = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
        let peer = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(19561))
        let id = MessageId(peerId: peer, namespace: Namespaces.Message.SecretIncoming, id: 1)
        let bytes = localArchiveTestPhotoData()
        let foreignBytes = Data(repeating: 0x73, count: bytes.count) // Equal-size distinct cache owner.
        let resource = SecretFileMediaResource(fileId: -19561, accessHash: 0,
            containerSize: Int64(bytes.count + 16), decryptedSize: Int64(bytes.count), datacenterId: 1,
            key: SecretFileEncryptionKey(aesKey: Data(repeating: 0x62, count: 32),
                aesIv: Data(repeating: 0x63, count: 32)))
        let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 19561),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        foreign.postbox.mediaBox.storeResourceData(resource.id, data: foreignBytes, synchronous: true)
        let inputPaths = fixture.postbox.mediaBox.storePathsForId(resource.id)
        try Data([0x31]).write(to: URL(fileURLWithPath: inputPaths.partial))
        try Data([0x32]).write(to: URL(fileURLWithPath: inputPaths.partial + ".meta"))
        _ = try self.localArchiveValue(fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(owner), localArchiveTestUser(author),
                TelegramSecretChat(id: peer, creationDate: 0, regularPeerId: author,
                    accessHash: 0, role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)],
                update: { _, updated in updated })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1,
                peerId: owner, state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true,
                includeSecretChats: true, includeExpiringMessages: true))
            _ = transaction.addMessages([localArchiveTestMessage(id, authorId: author,
                attributes: [AutoremoveTimeoutMessageAttribute(timeout: 1, countdownBeginTime: 1)],
                media: [image])], location: .Random)
        })
        // Restored persisted timestamp; no local/remote consume or expiry helper.
        try fixture.reopen()
        var postbox: Postbox? = fixture.postbox
        let head = try self.localArchiveValue(postbox!.timestampBasedMessageAttributesView(tag: 0))
        XCTAssertEqual(head.head?.messageId, id, file: file, line: line)
        XCTAssertEqual(head.head?.timestamp, 2, file: file, line: line)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("startup-archive-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let pendingOpen = Atomic<(() -> Void)?>(value: nil)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let attempts = Atomic<Int>(value: 0)
        let openRequested = self.expectation(description: "first real Store open requested")
        let canonical = self.expectation(description: "real due worker deleted canonical secret message")
        let isProtectionFailure: Bool
        if case .protectionFailure = order { isProtectionFailure = true } else { isProtectionFailure = false }
        let writing = isProtectionFailure ? nil : self.expectation(description: "new archive capture entered writing")
        var archive: LocalArchiveCoordinator?
        var worker: Disposable?
        var writingObserver: Disposable?
        var canonicalObserver: Disposable?
        let postboxGateEntered = self.expectation(description: "initial Postbox views held before publication")
        let releasePostbox = DispatchSemaphore(value: 0)
        let postboxGateTimedOut = Atomic<Bool>(value: false)
        defer { releasePostbox.signal() }
        func makeArchive(_ box: Postbox, reopening: Bool = false) -> LocalArchiveCoordinator {
            return LocalArchiveCoordinator(postbox: box, accountPeerId: owner, accountId: 19561,
                testingEnvironment: true, baseDirectory: directory,
                storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                    let first = attempts.modify { $0 + 1 } == 1
                    if case .protectionFailure = order {
                        completion(.failure(.protectionUnavailable))
                        if first { openRequested.fulfill() }
                        return
                    }
                    let start = {
                        ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: FixtureKey(),
                            createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
                                switch result {
                                case let .success(store): _ = opened.swap(store); completion(.success(store))
                                case let .failure(error): completion(.failure(error))
                                }
                            }
                    }
                    if reopening { start() } else {
                        XCTAssertNil(pendingOpen.swap(start), "Only one open may be pending", file: file, line: line)
                    }
                    // Publish the acknowledgement only after the corresponding
                    // state is available to the waiter; never acknowledge twice.
                    if first { openRequested.fulfill() }
                }, protectedData: .single(true))
        }
        func stopArchive(_ value: LocalArchiveCoordinator) throws {
            pendingOpen.swap(nil)?()
            value.dispose()
            _ = try self.localArchiveValue(value.status |> filter { $0 == .unavailable })
            if let store = opened.swap(nil) {
                let closed = self.expectation(description: "test archive closed")
                store.close { closed.fulfill() }
                self.wait(for: [closed], timeout: 15)
            }
        }
        defer {
            releasePostbox.signal()
            worker?.dispose(); canonicalObserver?.dispose(); writingObserver?.dispose()
            if let archive { try? stopArchive(archive) }
        }
        canonicalObserver = (postbox!.messageView(id) |> filter { $0.message == nil } |> take(1))
            .start(next: { _ in canonical.fulfill() })
        // Make the initial policy callback unavailable while both subscriptions
        // are installed. This forces the queue-order guarantee, not lucky timing.
        Postbox.sharedQueue.justDispatch {
            postboxGateEntered.fulfill()
            if releasePostbox.wait(timeout: .now() + 30) == .timedOut {
                _ = postboxGateTimedOut.swap(true)
            }
        }
        self.wait(for: [postboxGateEntered], timeout: 15)
        if case .workerFirst = order {} else {
            archive = makeArchive(postbox!)
            if let writing {
                writingObserver = (archive!.status |> filter { $0 == .writing } |> take(1))
                    .start(next: { _ in writing.fulfill() })
            }
        }
        // Deliberately DO NOT await ready, settings, opening, Keychain or Store.
        // This is the coordinator-before-worker order proposed for Account.init.
        worker = managedAutoremoveMessageOperations(postbox: postbox!, isRemove: true,
            globalTimeDifference: { 0 }).start()
        XCTAssertEqual(attempts.with { $0 }, 0, "Store cannot open while its bootstrap Postbox read is held", file: file, line: line)
        releasePostbox.signal()
        self.wait(for: [canonical], timeout: 15)
        worker?.dispose(); worker = nil
        canonicalObserver?.dispose(); canonicalObserver = nil
        postbox!.mediaBox.dataQueue.sync {}
        let paths = postbox!.mediaBox.storePathsForId(resource.id)
        for path in [paths.complete, paths.partial, paths.partial + ".meta"] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: path), file: file, line: line)
        }
        XCTAssertFalse(postboxGateTimedOut.with { $0 }, file: file, line: line)
        XCTAssertEqual(try localArchiveReadCompletedMedia(foreign.postbox.mediaBox.storePathsForId(resource.id).complete),
            foreignBytes, "Cleanup must remain account scoped", file: file, line: line)
        let pending = try self.localArchiveValue(postbox!.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: owner)
        })
        XCTAssertEqual(pending.count, 1, file: file, line: line)
        if case .workerFirst = order {
            // Deterministic old-order control: no owner existed until deletion
            // and canonical cache removal were acknowledged, without any sleeps.
            archive = makeArchive(postbox!)
            if let writing {
                writingObserver = (archive!.status |> filter { $0 == .writing } |> take(1))
                    .start(next: { _ in writing.fulfill() })
            }
        }
        self.wait(for: [openRequested], timeout: 15)
        if case .protectionFailure = order {
            XCTAssertNil(opened.with { $0 }, file: file, line: line)
            XCTAssertNil(try self.localArchiveValue(postbox!.transaction { $0.getMessage(id) }), file: file, line: line)
            // Real protection error must not gate expiry or mandatory cleanup.
            // No encrypted media persistence is claimed in this case.
            return
        }
        XCTAssertNil(opened.with { $0 }, "Canonical cleanup preceded Store availability", file: file, line: line)
        let resume = try XCTUnwrap(pendingOpen.swap(nil), file: file, line: line)
        resume()
        self.wait(for: [try XCTUnwrap(writing, file: file, line: line)], timeout: 15)
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .ready })
        XCTAssertTrue(try self.localArchiveValue(postbox!.transaction { transaction in
            localArchivePendingCaptures(transaction: transaction, accountPeerId: owner).isEmpty
        }), file: file, line: line)
        let page = try self.localArchiveValue(archive!.deletedMessages())
        XCTAssertFalse(page.unavailable, file: file, line: line)
        XCTAssertEqual(page.records.count, 1, file: file, line: line)
        let row = try XCTUnwrap(page.records.first, file: file, line: line)
        let expected: Data?
        if case .workerFirst = order { expected = nil } else { expected = bytes }
        XCTAssertEqual(row.storedMediaCount, expected == nil ? 0 : 1, file: file, line: line)
        XCTAssertEqual(row.unavailableMediaCount, expected == nil ? 1 : 0, file: file, line: line)
        XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: row.eventKey, slot: 0)), expected,
            file: file, line: line)
        writingObserver?.dispose(); writingObserver = nil
        XCTAssertTrue(try self.localArchiveValue(archive!.setEnabled(false)), file: file, line: line)
        try stopArchive(archive!); archive = nil; postbox = nil
        try fixture.reopen()
        archive = makeArchive(fixture.postbox, reopening: true)
        let read = archive!.acquireReadAccess()
        defer { read.dispose() }
        _ = try self.localArchiveValue(archive!.status |> filter { $0 == .browsing })
        let reopened = try self.localArchiveValue(archive!.timeline(peerId: peer.toInt64(), namespace: id.namespace, messageId: id.id))
        XCTAssertFalse(reopened.unavailable, file: file, line: line)
        XCTAssertEqual(reopened.records.map { $0.eventKey }, [row.eventKey], file: file, line: line)
        XCTAssertEqual(try self.localArchiveValue(archive!.mediaData(eventKey: row.eventKey, slot: 0)), expected,
            file: file, line: line)
        XCTAssertNil(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id) }), file: file, line: line)
    }

    func testCoordinatorSubscriptionBeforeRestoredDueWorkerPinsWhileStoreOpenIsPending() throws {
        try self.assertRestoredDueTimer(.coordinatorFirst)
    }
    func testRestoredDueWorkerBeforeCoordinatorRecordsHonestMissingMedia() throws {
        try self.assertRestoredDueTimer(.workerFirst)
    }
    func testProtectedStoreFailureDoesNotDelayRestoredSecretCanonicalExpiry() throws {
        try self.assertRestoredDueTimer(.protectionFailure)
    }
}
