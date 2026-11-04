// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

private struct LocalArchiveIntegrationKey: ArchiveKeyProvider {
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
        // Distinct synthetic per-account keys. No production Keychain access in this fixture.
        return Data(repeating: UInt8(truncatingIfNeeded: accountId), count: 32)
    }
}

private struct LocalArchiveMalformedPreference: Codable {
    let unrelatedField: Int32
}

/// Keeps all SQLCipher behavior real. Only the delivery of one write result is
/// intercepted to reproduce the archive-commit / Postbox-acknowledgement crash window.
private final class LocalArchiveInterceptingStorage: LocalArchiveStorage {
    enum WriteBehavior { case normal, fail, withholdAcknowledgement, withholdMediaAcknowledgement }
    let store: ArchiveStore
    let closed = ValuePromise<Bool>(false, ignoreRepeated: true)
    let mediaWritten = ValuePromise<Bool>(false, ignoreRepeated: true)
    private let behavior: WriteBehavior
    private let didWrite: ((Result<ArchiveWriteResult, ArchiveError>) -> Void)?

    init(store: ArchiveStore, behavior: WriteBehavior,
         didWrite: ((Result<ArchiveWriteResult, ArchiveError>) -> Void)?) {
        self.store = store
        self.behavior = behavior
        self.didWrite = didWrite
    }

    func recordFromOutbox(_ capture: ArchiveCapture, index: Int64, generation: Int64,
                          policy: ArchiveCapturePolicy,
                          completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void) {
        if case .fail = self.behavior {
            let result: Result<ArchiveWriteResult, ArchiveError> = .failure(.unavailable(13))
            self.didWrite?(result)
            completion(result)
            return
        }
        self.store.recordFromOutbox(capture, index: index, generation: generation, policy: policy) { result in
            self.didWrite?(result)
            if case .withholdAcknowledgement = self.behavior { return }
            completion(result)
        }
    }

    func recordMedia(eventKey: String, slot: Int, kind: String, mimeType: String, data: Data, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.recordMedia(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, data: data, generation: generation) { result in
            self.mediaWritten.set(true)
            if case .withholdMediaAcknowledgement = self.behavior { return }
            completion(result)
        }
    }
    func containsMedia(eventKey: String, slot: Int, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.containsMedia(eventKey: eventKey, slot: slot, completion: completion)
    }
    func media(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMedia?, ArchiveError>) -> Void) {
        self.store.media(eventKey: eventKey, slot: slot, completion: completion)
    }
    func currentGeneration(completion: @escaping (Result<Int64, ArchiveError>) -> Void) {
        self.store.currentGeneration(completion: completion)
    }
    func purge(advancingToGeneration: Int64, completion: @escaping (Result<ArchivePurgeResult, ArchiveError>) -> Void) {
        self.store.purge(advancingToGeneration: advancingToGeneration, completion: completion)
    }
    func setRetention(_ policy: ArchiveRetentionPolicy, completion: @escaping (Result<Int, ArchiveError>) -> Void) {
        self.store.setRetention(policy, completion: completion)
    }
    func close(completion: @escaping () -> Void) {
        self.store.close {
            self.closed.set(true)
            completion()
        }
    }
    func deletedMessages(peerId: Int64?, beforeSequence: Int64?, limit: Int,
                         completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        self.store.deletedMessages(peerId: peerId, beforeSequence: beforeSequence, limit: limit, completion: completion)
    }
    func timeline(for message: ArchiveMessageID, beforeSequence: Int64?, limit: Int,
                  completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        self.store.timeline(for: message, beforeSequence: beforeSequence, limit: limit, completion: completion)
    }
    func usage(completion: @escaping (Result<ArchiveUsage, ArchiveError>) -> Void) {
        self.store.usage(completion: completion)
    }
}

final class LocalArchiveCoordinatorTests: XCTestCase {
    private final class Running {
        var coordinator: LocalArchiveCoordinator?
        let storage = Atomic<LocalArchiveInterceptingStorage?>(value: nil)
    }

    private let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1))
    private let chatPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!
    private var archiveDirectory: URL!
    private var running: [Running] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-coordinator")
        // Deliberately outside Postbox's db/media directories, as in the app integration.
        self.archiveDirectory = FileManager.default.temporaryDirectory.appendingPathComponent("independent-archive-" + UUID().uuidString)
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
    }

    override func tearDownWithError() throws {
        for instance in self.running {
            try self.stop(instance)
        }
        self.running.removeAll()
        try self.fixture?.destroy()
        self.fixture = nil
        if let archiveDirectory = self.archiveDirectory, FileManager.default.fileExists(atPath: archiveDirectory.path) {
            try FileManager.default.removeItem(at: archiveDirectory)
        }
    }

    private func start(postbox: Postbox? = nil, accountId: Int64 = 17,
                       behavior: LocalArchiveInterceptingStorage.WriteBehavior = .normal,
                       protectedData: Signal<Bool, NoError> = .single(true),
                       didWrite: ((Result<ArchiveWriteResult, ArchiveError>) -> Void)? = nil) -> Running {
        let instance = Running()
        instance.coordinator = LocalArchiveCoordinator(postbox: postbox ?? self.fixture.postbox, accountPeerId: self.accountPeerId,
            accountId: accountId, baseDirectory: self.archiveDirectory,
            storageFactory: { directory, accountId, retention, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId,
                    keyProvider: LocalArchiveIntegrationKey(), retention: retention,
                    callbackQueue: callbackQueue, clock: { 1000 }) { result in
                    switch result {
                    case let .success(store):
                        let intercepted = LocalArchiveInterceptingStorage(store: store, behavior: behavior, didWrite: didWrite)
                        let _ = instance.storage.swap(intercepted)
                        completion(.success(intercepted))
                    case let .failure(error):
                        completion(.failure(error))
                    }
                }
            }, protectedData: protectedData)
        self.running.append(instance)
        return instance
    }

    private func stop(_ instance: Running) throws {
        guard let coordinator = instance.coordinator else { return }
        coordinator.dispose()
        if let storage = instance.storage.with({ $0 }) {
            let _ = try self.localArchiveValue(storage.closed.get() |> filter { $0 })
        }
        instance.coordinator = nil
    }

    private func awaitStatus(_ status: LocalArchiveStatus, in instance: Running) throws {
        let coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == status }), status)
    }

    private func queueDeletion(_ number: Int32 = 1, text: String = "preserved locally", media: [Media] = []) throws -> MessageId {
        let id = MessageId(peerId: self.chatPeerId, namespace: Namespaces.Message.Cloud, id: number)
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: text, media: media)], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: [id], observedAt: 200)
            transaction.deleteMessages([id], forEachMedia: nil)
        })
        return id
    }

    private func pending() throws -> [LocalArchivePendingCapture] {
        let accountPeerId = self.accountPeerId
        return try self.localArchiveValue(self.fixture.postbox.transaction {
            localArchivePendingCaptures(transaction: $0, accountPeerId: accountPeerId)
        })
    }

    private func assertUnsupportedPreferencesRemainUnchanged(_ entry: PreferencesEntry) throws {
        // An earlier version may have left valid queued work. Unsupported preferences
        // must block draining it as well as prevent edits to the original entry.
        let _ = try self.queueDeletion(text: "Keep queued while preferences are unsupported")
        let eventKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setPreferencesEntry(key: localArchiveSettingsKey, value: entry)
        })
        try self.fixture.reopen()
        let instance = self.start()
        do {
            let coordinator = try XCTUnwrap(instance.coordinator)
            let settings = try self.localArchiveValue(coordinator.settings)
            XCTAssertFalse(settings.isSupported)
            XCTAssertFalse(settings.enabled)
            try self.awaitStatus(.unavailable, in: instance)
            XCTAssertNil(instance.storage.with { $0 })

            let mutations: [() -> Signal<Bool, NoError>] = [
                { coordinator.setEnabled(true) },
                { coordinator.setEnabled(false) },
                { coordinator.setRetention(maxAgeSeconds: 7 * 86400, maxPayloadBytes: 4 * 1024 * 1024) },
                { coordinator.setExcludeBots(false) },
                { coordinator.setPeerExcluded(peerId: 71, excluded: true) },
                { coordinator.setPeerExcluded(peerId: 71, excluded: false) },
                { coordinator.setSenderExcluded(senderId: 72, excluded: true) },
                { coordinator.setSenderExcluded(senderId: 72, excluded: false) },
                { coordinator.requestPurge() }
            ]
            for mutate in mutations {
                XCTAssertFalse(try self.localArchiveValue(mutate()))
                let actual = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                    transaction.getPreferencesEntry(key: localArchiveSettingsKey)
                })
                XCTAssertEqual(try XCTUnwrap(actual).data, entry.data)
                XCTAssertEqual(try self.pending().first?.operation?.eventKey, eventKey)
            }
        }
        try self.stop(instance)
        try self.fixture.reopen()
        let reopened = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.getPreferencesEntry(key: localArchiveSettingsKey)
        })
        XCTAssertEqual(try XCTUnwrap(reopened).data, entry.data)
        XCTAssertFalse(localArchiveSettings(entry: reopened).isSupported)
    }

    func testFuturePreferencesAreReadOnlyAndPreservedAcrossAllMutations() throws {
        var configured = LocalArchiveSettings(enabled: true, excludeBots: false,
            excludedPeerIds: [71, 73], excludedSenderIds: [72, 74])
        configured.generation = 23
        configured.droppedCaptures = 19
        let encoded = try JSONEncoder().encode(configured)
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object["schemaVersion"] = 99
        let future = try JSONDecoder().decode(LocalArchiveSettings.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertFalse(future.isSupported)
        try self.assertUnsupportedPreferencesRemainUnchanged(try XCTUnwrap(PreferencesEntry(future)))
    }

    func testMalformedPreferencesAreReadOnlyAndPreservedAcrossAllMutations() throws {
        let entry = try XCTUnwrap(PreferencesEntry(LocalArchiveMalformedPreference(unrelatedField: 12)))
        XCTAssertNil(entry.get(LocalArchiveSettings.self))
        try self.assertUnsupportedPreferencesRemainUnchanged(entry)
    }

    func testInvalidPreferenceValuesAreReadOnlyAndPreservedAcrossAllMutations() throws {
        var invalid = LocalArchiveSettings(enabled: true, excludedPeerIds: [71], excludedSenderIds: [72])
        invalid.generation = 23
        invalid.maxPayloadBytes = 0
        try self.assertUnsupportedPreferencesRemainUnchanged(try XCTUnwrap(PreferencesEntry(invalid)))
    }

    func testAbsentPreferencesRemainEditableWithoutResettingConfiguredFields() throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setPreferencesEntry(key: localArchiveSettingsKey, value: nil)
        })
        let instance = self.start(protectedData: .single(false))
        let coordinator = try XCTUnwrap(instance.coordinator)
        let initial = try self.localArchiveValue(coordinator.settings)
        XCTAssertTrue(initial.isSupported)
        XCTAssertFalse(initial.enabled)
        XCTAssertTrue(try self.localArchiveValue(coordinator.setExcludeBots(false)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setPeerExcluded(peerId: 71, excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setSenderExcluded(senderId: 72, excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setRetention(maxAgeSeconds: 7 * 86400, maxPayloadBytes: 4 * 1024 * 1024)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.requestPurge()))
        let configured = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertTrue(configured.isSupported)
        XCTAssertTrue(configured.enabled)
        XCTAssertFalse(configured.excludeBots)
        XCTAssertEqual(configured.excludedPeerIds, [71])
        XCTAssertEqual(configured.excludedSenderIds, [72])
        XCTAssertEqual(configured.maxAgeSeconds, 7 * 86400)
        XCTAssertEqual(configured.maxPayloadBytes, 4 * 1024 * 1024)
        XCTAssertEqual(configured.generation, 1)
        XCTAssertTrue(configured.pendingPurge)
        XCTAssertEqual(configured.droppedCaptures, 0)
        XCTAssertNil(instance.storage.with { $0 })
    }

    func testDeletedPhotoMetadataDrainsToEncryptedStoreAndSurvivesRestart() throws {
        let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 501),
            representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        let id = try self.queueDeletion(51, text: "photo caption", media: [image])
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        XCTAssertTrue(try self.pending().isEmpty)
        let before = try self.localArchiveValue(try XCTUnwrap(first.coordinator).deletedMessages())
        XCTAssertEqual(before.records.first?.unavailableMediaCount, 1)
        try self.stop(first)
        try self.fixture.reopen()
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        let after = try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages())
        XCTAssertEqual(after.records.first?.messageId, id.id)
        XCTAssertEqual(after.records.first?.text, "photo caption")
        XCTAssertEqual(after.records.first?.unavailableMediaCount, 1)
    }

    func testMediaReaderRejectsSymlinksDirectoriesAndOversizedFiles() throws {
        let directory = self.fixture.directory.appendingPathComponent("media-reader")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("file")
        try Data([1, 2, 3]).write(to: file)
        XCTAssertEqual(try localArchiveReadCompletedMedia(file.path), Data([1, 2, 3]))
        let link = directory.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
        XCTAssertNil(try localArchiveReadCompletedMedia(link.path))
        XCTAssertNil(try localArchiveReadCompletedMedia(directory.path))
        let handle = try FileHandle(forWritingTo: file)
        try handle.truncate(atOffset: 16 * 1024 * 1024 + 1)
        try handle.close()
        XCTAssertNil(try localArchiveReadCompletedMedia(file.path))
    }

    func testNoAgeExpiryAndMediaQuotaPersistInAccountPreferences() throws {
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(instance.coordinator).setRetention(maxAgeSeconds: Int64.max, maxPayloadBytes: 1024 * 1024 * 1024)))
        try self.stop(instance)
        try self.fixture.reopen()
        let settings = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertEqual(settings.maxAgeSeconds, Int64.max)
        XCTAssertEqual(settings.maxPayloadBytes, 1024 * 1024 * 1024)
    }

    func testTypedCloudFileBytesSurviveDeletionCacheClearAndRestart() throws {
        let video: TelegramMediaFileAttribute = .Video(duration: 1, size: PixelDimensions(width: 16, height: 16), flags: [], preloadSize: nil, coverTime: nil, videoCodec: nil)
        let round: TelegramMediaFileAttribute = .Video(duration: 1, size: PixelDimensions(width: 16, height: 16), flags: [.instantRoundVideo], preloadSize: nil, coverTime: nil, videoCodec: nil)
        let cases: [(String, String, [TelegramMediaFileAttribute])] = [
            ("video", "video/mp4", [video]),
            ("videoMessage", "video/mp4", [round]),
            ("voice", "audio/ogg", [.Audio(isVoice: true, duration: 1, title: nil, performer: nil, waveform: nil)]),
            ("animation", "image/gif", [.Animated]),
            ("document", "application/pdf", [.FileName(fileName: "document.pdf")])
        ]
        var resources: [MediaResourceId] = []
        var expected: [(String, String, Data)] = []
        for (index, item) in cases.enumerated() {
            let bytes = Data([UInt8(index), 5, 9, 11])
            let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: Int64(9100 + index), accessHash: 0, size: Int64(bytes.count), fileReference: nil, fileName: nil)
            resources.append(resource.id)
            self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
            let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: Int64(9100 + index)), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: item.1, size: Int64(bytes.count), attributes: item.2, alternativeRepresentations: [])
            let id = try self.queueDeletion(Int32(70 + index), media: [file])
            let key = try XCTUnwrap(self.pending().first(where: { $0.operation?.messageId == id })?.operation?.eventKey)
            expected.append((key, item.0, bytes))
        }
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        XCTAssertTrue(try self.pending().isEmpty)
        let cleared = expectation(description: "clear typed media cache")
        let removal = self.fixture.postbox.mediaBox.removeCachedResources(resources, force: true).start(completed: { cleared.fulfill() })
        wait(for: [cleared], timeout: 15)
        removal.dispose()
        try self.stop(first)
        try self.fixture.reopen()
        let second = self.start()
        try self.awaitStatus(.ready, in: second)
        let store = try XCTUnwrap(second.storage.with { $0?.store })
        for item in expected {
            let done = expectation(description: "read retained typed media")
            var result: Result<ArchivedMedia?, ArchiveError>?
            store.media(eventKey: item.0, slot: 0) { result = $0; done.fulfill() }
            wait(for: [done], timeout: 15)
            let archived = try XCTUnwrap(try XCTUnwrap(result).get())
            XCTAssertEqual(archived.kind, item.1)
            XCTAssertEqual(archived.data, item.2)
        }
    }

    func testMediaCommitBeforeOutboxAckSurvivesRestartAndSourceRemoval() throws {
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9200, accessHash: 0, size: 3, fileReference: nil, fileName: nil)
        let bytes = Data([2, 4, 6])
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9200), representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(91, media: [image])
        let key = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let first = self.start(behavior: .withholdMediaAcknowledgement)
        try self.awaitStatus(.writing, in: first)
        let initialStore = try XCTUnwrap(first.storage.with { $0 })
        XCTAssertTrue(try self.localArchiveValue(initialStore.mediaWritten.get() |> filter { $0 }))
        XCTAssertEqual(try self.pending().count, 1)
        try self.stop(first)
        try FileManager.default.removeItem(atPath: self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete)
        try self.fixture.reopen()
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertTrue(try self.pending().isEmpty)
        let store = try XCTUnwrap(recovered.storage.with { $0?.store })
        let done = expectation(description: "read media after ack recovery")
        var result: Result<ArchivedMedia?, ArchiveError>?
        store.media(eventKey: key, slot: 0) { result = $0; done.fulfill() }
        wait(for: [done], timeout: 15)
        XCTAssertEqual(try XCTUnwrap(result).get()?.data, bytes)
    }

    func testCompletedPhotoBytesSurviveCacheRemovalAndRestart() throws {
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9001, accessHash: 0, size: 4, fileReference: nil, fileName: nil)
        let bytes = Data([11, 22, 33, 44])
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let image = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9001),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(61, media: [image])
        let eventKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        let page = try self.localArchiveValue(try XCTUnwrap(first.coordinator).deletedMessages())
        XCTAssertEqual(page.records.first?.storedMediaCount, 1)
        XCTAssertEqual(page.records.first?.unavailableMediaCount, 0)
        let cleared = expectation(description: "clear original media cache")
        let removal = self.fixture.postbox.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { cleared.fulfill() })
        wait(for: [cleared], timeout: 15)
        removal.dispose()
        try self.stop(first)
        try self.fixture.reopen()
        let second = self.start()
        try self.awaitStatus(.ready, in: second)
        let store = try XCTUnwrap(second.storage.with { $0?.store })
        let read = expectation(description: "read archived bytes")
        var value: Result<ArchivedMedia?, ArchiveError>?
        store.media(eventKey: eventKey, slot: 0) { value = $0; read.fulfill() }
        wait(for: [read], timeout: 15)
        XCTAssertEqual(try XCTUnwrap(value).get()?.data, bytes)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testCommittedDeletionDrainsIntoRealEncryptedArchiveAndReadsThroughFacade() throws {
        let id = try self.queueDeletion()
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
        let page = try self.localArchiveValue(try XCTUnwrap(instance.coordinator).deletedMessages(peerId: self.chatPeerId.toInt64()))
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.count, 1)
        XCTAssertEqual(page.records.first?.text, "preserved locally")
        XCTAssertEqual(page.records.first?.messageId, id.id)
        XCTAssertEqual(page.records.first?.observedAt, 200)
        XCTAssertTrue(page.records.first?.isDeleted ?? false)
    }

    func testMediaBoxCacheRemovalPreservesSiblingArchiveAcrossReopen() throws {
        // Match the production layout: account/postbox/media and
        // account/local-message-archive/account-<recordId>/archive.sqlite.
        // This calls the actual MediaBox endpoint used by clearCachedMediaResources;
        // it does not exercise the Settings UI or its selection/usage collection.
        let accountDirectory = FileManager.default.temporaryDirectory.appendingPathComponent("archive-cache-account-" + UUID().uuidString)
        let postboxDirectory = accountDirectory.appendingPathComponent("postbox")
        let previousArchiveDirectory = self.archiveDirectory
        self.archiveDirectory = accountDirectory.appendingPathComponent("local-message-archive")
        var layoutPostbox: Postbox?
        var first: Running?
        var recovered: Running?

        func openLayoutPostbox(firstOpen: Bool) throws -> Postbox {
            let encryption = ValueBoxEncryptionParameters(forceEncryptionIfNoSet: true,
                key: try XCTUnwrap(.init(data: Data(repeating: 0x62, count: 32))),
                salt: try XCTUnwrap(.init(data: Data(repeating: 0x28, count: 16))))
            let result = try self.localArchiveValue(openPostbox(basePath: postboxDirectory.path,
                seedConfiguration: telegramPostboxSeedConfiguration, encryptionParameters: encryption,
                timestampForAbsoluteTimeBasedOperations: Int32(Date().timeIntervalSince1970),
                isMainProcess: true, isTemporary: false, isReadOnly: false,
                useCopy: false, useCaches: true, removeDatabaseOnError: firstOpen)
                |> filter { result in
                    if case .upgrading = result { return false }
                    return true
                })
            guard case let .postbox(postbox) = result else {
                throw NSError(domain: "LocalArchiveCacheRemovalTest", code: 1)
            }
            return postbox
        }

        func closeLayoutPostbox() throws {
            if let postbox = layoutPostbox {
                let _ = try self.localArchiveValue(postbox.transaction { _ in })
                let _ = try self.localArchiveValue(postbox.mediaBox.storageBox.all())
                let _ = try self.localArchiveValue(postbox.mediaBox.cacheStorageBox.all())
                postbox.mediaBox.dataQueue.sync {}
            }
            layoutPostbox = nil
            Postbox.sharedQueue.sync {}
        }

        defer {
            if let first { try? self.stop(first) }
            if let recovered { try? self.stop(recovered) }
            try? closeLayoutPostbox()
            try? FileManager.default.removeItem(at: accountDirectory)
            self.archiveDirectory = previousArchiveDirectory
        }
        layoutPostbox = try openLayoutPostbox(firstOpen: true)
        let deletedId = MessageId(peerId: self.chatPeerId, namespace: Namespaces.Message.Cloud, id: 81)
        let retainedId = MessageId(peerId: self.chatPeerId, namespace: Namespaces.Message.Cloud, id: 82)
        let accountPeerId = self.accountPeerId
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(try XCTUnwrap(layoutPostbox).transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
            let _ = transaction.addMessages([
                localArchiveTestMessage(deletedId, text: "Archive survives cache removal"),
                localArchiveTestMessage(retainedId, text: "Canonical message remains")
            ], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: [deletedId], observedAt: 200)
            transaction.deleteMessages([deletedId], forEachMedia: nil)
        })
        let started = self.start(postbox: try XCTUnwrap(layoutPostbox))
        first = started
        try self.awaitStatus(.ready, in: started)
        let before = try self.localArchiveValue(try XCTUnwrap(started.coordinator).deletedMessages())
        XCTAssertEqual(before.records.map { $0.text }, ["Archive survives cache removal"])
        let archiveFile = self.archiveDirectory.appendingPathComponent("account-17/archive.sqlite")
        XCTAssertTrue(FileManager.default.fileExists(atPath: archiveFile.path))

        do {
            let mediaBox = try XCTUnwrap(layoutPostbox).mediaBox
            let resourceId = MediaResourceId("archive-cache-removal-fixture")
            let bytes = Data("Synthetic cached resource".utf8)
            mediaBox.storeResourceData(resourceId, data: bytes, synchronous: true)
            let stored = try self.localArchiveValue(mediaBox.resourceData(id: resourceId))
            XCTAssertTrue(stored.complete)
            XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: stored.path)), bytes)

            let cleared = expectation(description: "actual MediaBox cache removal completed")
            let removal = mediaBox.removeCachedResources([resourceId]).start(completed: { cleared.fulfill() })
            defer { removal.dispose() }
            wait(for: [cleared], timeout: 15)
            XCTAssertFalse(FileManager.default.fileExists(atPath: stored.path))
            XCTAssertTrue(FileManager.default.fileExists(atPath: archiveFile.path))
        }

        try self.stop(started)
        try closeLayoutPostbox()
        layoutPostbox = try openLayoutPostbox(firstOpen: false)
        let restarted = self.start(postbox: try XCTUnwrap(layoutPostbox))
        recovered = restarted
        try self.awaitStatus(.ready, in: restarted)
        let after = try self.localArchiveValue(try XCTUnwrap(restarted.coordinator).deletedMessages())
        XCTAssertFalse(after.unavailable)
        XCTAssertEqual(after.records.map { $0.text }, before.records.map { $0.text })
        XCTAssertEqual(after.records.map { $0.sequence }, before.records.map { $0.sequence })
        XCTAssertNil(try self.localArchiveValue(try XCTUnwrap(layoutPostbox).transaction { $0.getMessage(deletedId) }))
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(layoutPostbox).transaction { $0.getMessage(retainedId)?.text }), "Canonical message remains")
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(layoutPostbox).transaction {
            localArchivePendingCaptures(transaction: $0, accountPeerId: accountPeerId).isEmpty
        }))
    }

    func testWriteFailureKeepsOutboxUntilSuccessfulRestart() throws {
        let _ = try self.queueDeletion()
        let failing = self.start(behavior: .fail)
        try self.awaitStatus(.unavailable, in: failing)
        let eventKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        try self.stop(failing)
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().first?.operation?.eventKey, eventKey)

        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertTrue(try self.pending().isEmpty)
        let page = try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages())
        XCTAssertEqual(page.records.count, 1)
    }

    func testRestartAfterArchiveCommitBeforeAcknowledgementDoesNotDuplicate() throws {
        let _ = try self.queueDeletion()
        let committed = expectation(description: "archive committed without acknowledgement")
        let interrupted = self.start(behavior: .withholdAcknowledgement, didWrite: { result in
            if case .failure = result { XCTFail("injected interruption must follow a successful real archive commit") }
            committed.fulfill()
        })
        wait(for: [committed], timeout: 15)
        let originalKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let before = try self.localArchiveValue(try XCTUnwrap(interrupted.coordinator).deletedMessages())
        XCTAssertEqual(before.records.count, 1)
        try self.stop(interrupted)
        try self.fixture.reopen()
        XCTAssertEqual(try self.pending().first?.operation?.eventKey, originalKey)

        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertTrue(try self.pending().isEmpty)
        let after = try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages())
        XCTAssertEqual(after.records.count, 1)
        XCTAssertEqual(after.records.first?.sequence, before.records.first?.sequence)
        XCTAssertEqual(after.records.first?.text, before.records.first?.text)
    }

    func testProtectedDataGateDefersOpeningAndPreventsLockedReads() throws {
        let _ = try self.queueDeletion()
        let protectedData = ValuePromise<Bool>(false, ignoreRepeated: true)
        let instance = self.start(protectedData: protectedData.get())
        try self.awaitStatus(.locked, in: instance)
        XCTAssertNil(instance.storage.with { $0 })
        XCTAssertEqual(try self.pending().count, 1)
        protectedData.set(true)
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        protectedData.set(false)
        try self.awaitStatus(.locked, in: instance)
        let lockedPage = try self.localArchiveValue(try XCTUnwrap(instance.coordinator).deletedMessages())
        XCTAssertTrue(lockedPage.unavailable)
        XCTAssertTrue(lockedPage.records.isEmpty)
    }

    func testPendingPurgeSurvivesRestartAndRetiresUnacknowledgedGeneration() throws {
        let _ = try self.queueDeletion()
        let committed = expectation(description: "old-generation capture committed")
        let interrupted = self.start(behavior: .withholdAcknowledgement, didWrite: { result in
            if case .failure = result { XCTFail("expected real archive commit") }
            committed.fulfill()
        })
        wait(for: [committed], timeout: 15)
        try self.stop(interrupted)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.generation = 1
            settings.pendingPurge = true
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        try self.fixture.reopen()
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertTrue(try self.pending().isEmpty)
        let page = try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertTrue(page.records.isEmpty)
        let settings = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertEqual(settings.generation, 1)
        XCTAssertFalse(settings.pendingPurge)
    }

    func testRetentionAcknowledgesExpiredCaptureWithoutReturningDeletedMessage() throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.maxAgeSeconds = 10
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        let _ = try self.queueDeletion()
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        let page = try self.localArchiveValue(try XCTUnwrap(instance.coordinator).deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertTrue(page.records.isEmpty)
    }

    func testTwoAccountsWithIdenticalMessageIdsHaveSeparateArchivesAndPurge() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "archive-coordinator-other")
        var second: Running?
        defer {
            if let second { try? self.stop(second) }
            try? other.destroy()
        }
        let id = try self.queueDeletion(text: "first account")
        let accountPeerId = self.accountPeerId
        let peers = [localArchiveTestUser(self.accountPeerId), localArchiveTestUser(self.chatPeerId)]
        let _ = try self.localArchiveValue(other.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "second account")], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: [id], observedAt: 200)
            transaction.deleteMessages([id], forEachMedia: nil)
        })
        let first = self.start()
        let otherRunning = self.start(postbox: other.postbox, accountId: 18)
        second = otherRunning
        try self.awaitStatus(.ready, in: first)
        try self.awaitStatus(.ready, in: otherRunning)
        let firstCoordinator = try XCTUnwrap(first.coordinator)
        let otherCoordinator = try XCTUnwrap(otherRunning.coordinator)
        XCTAssertEqual(try self.localArchiveValue(firstCoordinator.deletedMessages()).records.map { $0.text }, ["first account"])
        XCTAssertEqual(try self.localArchiveValue(otherCoordinator.deletedMessages()).records.map { $0.text }, ["second account"])

        XCTAssertTrue(try self.localArchiveValue(firstCoordinator.requestPurge()))
        let _ = try self.localArchiveValue(firstCoordinator.settings |> filter { $0.generation == 1 && !$0.pendingPurge })
        try self.awaitStatus(.ready, in: first)
        XCTAssertTrue(try self.localArchiveValue(firstCoordinator.deletedMessages()).records.isEmpty)
        XCTAssertEqual(try self.localArchiveValue(otherCoordinator.deletedMessages()).records.map { $0.text }, ["second account"])
    }
}
