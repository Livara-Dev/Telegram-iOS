// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
#if os(iOS)
import UIKit
#endif
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

private final class LocalArchiveReadKeyProbe: ArchiveKeyProvider {
    let requests = Atomic<[Bool]>(value: [])
    let result: Result<Data, ArchiveError>

    init(result: Result<Data, ArchiveError>) { self.result = result }

    func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
        let _ = self.requests.modify { $0 + [createIfMissing] }
        return try self.result.get()
    }
}

/// Delay one real open/close operation so lifecycle ordering is deterministic,
/// rather than relying on SQLite winning a lock/unlock scheduling race.
private final class LocalArchiveOperationGate {
    let intercepted = ValuePromise<Bool>(false, ignoreRepeated: true)
    private let didIntercept = Atomic<Bool>(value: false)
    private let pending = Atomic<(() -> Void)?>(value: nil)

    func perform(_ operation: @escaping () -> Void) {
        if !self.didIntercept.swap(true) {
            let _ = self.pending.swap(operation)
            self.intercepted.set(true)
        } else {
            operation()
        }
    }

    func release() {
        self.pending.swap(nil)?()
    }
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
    private let closeGate: LocalArchiveOperationGate?
    private let beforeOwnedMediaWrite: ((ArchiveStore) -> Void)?
    private let metadataAcknowledgementGate: LocalArchiveOperationGate?

    init(store: ArchiveStore, behavior: WriteBehavior,
         didWrite: ((Result<ArchiveWriteResult, ArchiveError>) -> Void)?,
         closeGate: LocalArchiveOperationGate? = nil,
         beforeOwnedMediaWrite: ((ArchiveStore) -> Void)? = nil,
         metadataAcknowledgementGate: LocalArchiveOperationGate? = nil) {
        self.store = store
        self.behavior = behavior
        self.didWrite = didWrite
        self.closeGate = closeGate
        self.beforeOwnedMediaWrite = beforeOwnedMediaWrite
        self.metadataAcknowledgementGate = metadataAcknowledgementGate
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
            if let gate = self.metadataAcknowledgementGate {
                gate.perform { completion(result) }
            } else {
                completion(result)
            }
        }
    }

    func recordMedia(eventKey: String, slot: Int, kind: String, mimeType: String, data: Data, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.recordMedia(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, data: data, generation: generation) { result in
            self.mediaWritten.set(true)
            if case .withholdMediaAcknowledgement = self.behavior { return }
            completion(result)
        }
    }
    func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: URL, expectedSize: Int64?, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.recordMediaFile(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, source: source, expectedSize: expectedSize, generation: generation) { result in
            self.mediaWritten.set(true)
            if case .withholdMediaAcknowledgement = self.behavior { return }
            completion(result)
        }
    }
    func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: ArchiveMediaFileSource, expectedSize: Int64?, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        // Keep the actual owned descriptor and SQLCipher operation. A test can
        // enqueue an independent earlier operation to hold the real worker.
        self.beforeOwnedMediaWrite?(self.store)
        self.store.recordMediaFile(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, source: source, expectedSize: expectedSize, generation: generation) { result in
            self.mediaWritten.set(true)
            if case .withholdMediaAcknowledgement = self.behavior { return }
            completion(result)
        }
    }
    func mediaInfo(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMediaInfo?, ArchiveError>) -> Void) {
        self.store.mediaInfo(eventKey: eventKey, slot: slot, completion: completion)
    }
    func mediaRange(eventKey: String, slot: Int, offset: Int64, count: Int, completion: @escaping (Result<Data?, ArchiveError>) -> Void) {
        self.store.mediaRange(eventKey: eventKey, slot: slot, offset: offset, count: count, completion: completion)
    }
    func containsMedia(eventKey: String, slot: Int, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.containsMedia(eventKey: eventKey, slot: slot, completion: completion)
    }
    func media(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMedia?, ArchiveError>) -> Void) {
        self.store.media(eventKey: eventKey, slot: slot, completion: completion)
    }
    func deleteSnapshot(eventKey: String, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
        self.store.deleteSnapshot(eventKey: eventKey, generation: generation, completion: completion)
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
        let close = {
            self.store.close {
                self.closed.set(true)
                completion()
            }
        }
        if let closeGate = self.closeGate { closeGate.perform(close) } else { close() }
    }
    func deletedMessages(peerId: Int64?, beforeSequence: Int64?, limit: Int,
                         completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        self.store.deletedMessages(peerId: peerId, beforeSequence: beforeSequence, limit: limit, completion: completion)
    }
    func timeline(for message: ArchiveMessageID, beforeSequence: Int64?, limit: Int,
                  completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        self.store.timeline(for: message, beforeSequence: beforeSequence, limit: limit, completion: completion)
    }
    func searchMessages(query: String, peerId: Int64?, kind: ArchiveEventKind?, beforeSequence: Int64?, limit: Int, completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
        self.store.searchMessages(query: query, peerId: peerId, kind: kind, beforeSequence: beforeSequence, limit: limit, completion: completion)
    }
    func mediaItems(peerId: Int64?, kind: String?, beforeSequence: Int64?, beforeSlot: Int?, limit: Int, completion: @escaping (Result<[ArchivedMediaItem], ArchiveError>) -> Void) {
        self.store.mediaItems(peerId: peerId, kind: kind, beforeSequence: beforeSequence, beforeSlot: beforeSlot, limit: limit, completion: completion)
    }
    func usage(completion: @escaping (Result<ArchiveUsage, ArchiveError>) -> Void) {
        self.store.usage(completion: completion)
    }
}

final class LocalArchiveCoordinatorTests: XCTestCase {
    private final class Running {
        var coordinator: LocalArchiveCoordinator?
        let storage = Atomic<LocalArchiveInterceptingStorage?>(value: nil)
        let creationMode = ValuePromise<Bool?>(nil, ignoreRepeated: true)
        let factoryOpenCount = Atomic<Int>(value: 0)
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
                       clock: @escaping () -> Int64 = { 1000 },
                       behavior: LocalArchiveInterceptingStorage.WriteBehavior = .normal,
                       protectedData: Signal<Bool, NoError>? = .single(true),
                       openGate: LocalArchiveOperationGate? = nil,
                       closeGate: LocalArchiveOperationGate? = nil,
                       beforeOwnedMediaWrite: ((ArchiveStore) -> Void)? = nil,
                       metadataAcknowledgementGate: LocalArchiveOperationGate? = nil,
                       didWrite: ((Result<ArchiveWriteResult, ArchiveError>) -> Void)? = nil) -> Running {
        let instance = Running()
        instance.coordinator = LocalArchiveCoordinator(postbox: postbox ?? self.fixture.postbox, accountPeerId: self.accountPeerId,
            accountId: accountId, baseDirectory: self.archiveDirectory,
            storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                let _ = instance.factoryOpenCount.modify { $0 + 1 }
                instance.creationMode.set(createIfMissing)
                ArchiveStore.open(baseDirectory: directory, accountId: accountId,
                    keyProvider: LocalArchiveIntegrationKey(), createIfMissing: createIfMissing, retention: retention,
                    callbackQueue: callbackQueue, clock: clock) { result in
                    let deliver = {
                        switch result {
                        case let .success(store):
                            let intercepted = LocalArchiveInterceptingStorage(store: store, behavior: behavior, didWrite: didWrite,
                                closeGate: closeGate, beforeOwnedMediaWrite: beforeOwnedMediaWrite,
                                metadataAcknowledgementGate: metadataAcknowledgementGate)
                            let _ = instance.storage.swap(intercepted)
                            completion(.success(intercepted))
                        case let .failure(error):
                            completion(.failure(error))
                        }
                    }
                    if let openGate { openGate.perform { callbackQueue.async { deliver() } } } else { deliver() }
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
                { coordinator.setIncludeSecretChats(true) },
                { coordinator.setIncludeSecretChats(false) },
                { coordinator.setIncludeExpiringMessages(true) },
                { coordinator.setIncludeExpiringMessages(false) },
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

    func testMalformedExcludedIdentitiesRemainReadOnlyAcrossReopenAndEveryMutation() throws {
        for value in [Int64.min, -1, Int64.max, 0x0000000700000000] {
            for sender in [false, true] {
                // Restore test setup explicitly; production must not reset an
                // unreadable policy to make the following mutation succeed.
                let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                    setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
                })
                let settings = LocalArchiveSettings(enabled: true, excludedPeerIds: sender ? [] : [value], excludedSenderIds: sender ? [value] : [])
                XCTAssertFalse(settings.isSupported)
                try self.assertUnsupportedPreferencesRemainUnchanged(try XCTUnwrap(PreferencesEntry(settings)))
            }
        }
    }

    func testMalformedNewExclusionMutationsLeaveValidAccountPolicyUnchanged() throws {
        let instance = self.start(protectedData: .single(false))
        let coordinator = try XCTUnwrap(instance.coordinator)
        let before = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchiveSettingsKey)?.data })
        for value in [Int64.min, -1, Int64.max, 0x0000000700000000] {
            for excluded in [true, false] {
                XCTAssertFalse(try self.localArchiveValue(coordinator.setPeerExcluded(peerId: value, excluded: excluded)))
                XCTAssertFalse(try self.localArchiveValue(coordinator.setSenderExcluded(senderId: value, excluded: excluded)))
            }
        }
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchiveSettingsKey)?.data }), before)
        XCTAssertNil(instance.storage.with { $0 })
    }

    func testMalformedOutboxPeerDecoderPreservesRawIdentityAndWorkerDoesNotAcknowledgeIt() throws {
        _ = try self.queueDeletion(text: "valid control content")
        let valid = try XCTUnwrap(self.pending().first?.operation)
        func withPeer(_ peerId: Int64?) -> LocalArchiveCaptureOperation {
            let encoder = PostboxEncoder()
            encoder.encodeInt32(valid.version, forKey: "v")
            encoder.encodeString(valid.eventKey, forKey: "k")
            encoder.encodeInt64(valid.generation, forKey: "g")
            if let peerId { encoder.encodeInt64(peerId, forKey: "p") }
            encoder.encodeInt32(valid.messageId.namespace, forKey: "n")
            encoder.encodeInt32(valid.messageId.id, forKey: "i")
            encoder.encodeInt32(valid.kindRawValue, forKey: "t")
            encoder.encodeString(valid.text, forKey: "x")
            encoder.encodeData(valid.entities, forKey: "e")
            if let author = valid.authorId { encoder.encodeInt64(author, forKey: "a") } else { encoder.encodeNil(forKey: "a") }
            encoder.encodeInt64(valid.sentAt, forKey: "s")
            encoder.encodeInt64(valid.observedAt, forKey: "o")
            if let edited = valid.serverEditAt { encoder.encodeInt64(edited, forKey: "d") } else { encoder.encodeNil(forKey: "d") }
            encoder.encodeBool(valid.botConversation, forKey: "b")
            return LocalArchiveCaptureOperation(decoder: PostboxDecoder(buffer: MemoryBuffer(data: encoder.makeData())))
        }
        XCTAssertNotNil(withPeer(valid.messageId.peerId.toInt64()).makeCapture(accountId: 17), "all other fixture fields must form a valid production capture")
        XCTAssertNil(withPeer(nil).makeCapture(accountId: 17), "a missing peer field cannot become a guessed peer-zero identity")
        let invalid: [Int64] = [Int64.min, -1, 0, Int64.max, 0x0000000700000000]
        let operations = invalid.map { withPeer($0) }
        for operation in operations { XCTAssertNil(operation.makeCapture(accountId: 17)) }
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.operationLogRemoveAllEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture)
            for operation in operations {
                transaction.operationLogAddEntry(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture, tagLocalIndex: .automatic, tagMergedIndex: .automatic, contents: operation)
            }
        })
        try self.fixture.reopen()
        let first = self.start()
        try self.awaitStatus(.invalidPendingCapture, in: first)
        XCTAssertEqual(try self.pending().count, invalid.count)
        let usage = try XCTUnwrap(self.localArchiveValue(try XCTUnwrap(first.coordinator).usage()))
        XCTAssertEqual(usage.snapshots, 0)
        try self.stop(first)
        try self.fixture.reopen()
        for (pending, original) in zip(try self.pending(), invalid) {
            let operation = try XCTUnwrap(pending.operation)
            XCTAssertNil(operation.makeCapture(accountId: 17))
            let encoder = PostboxEncoder()
            operation.encode(encoder)
            let decoder = PostboxDecoder(buffer: MemoryBuffer(data: encoder.makeData()))
            XCTAssertEqual(decoder.decodeInt64ForKey("p", orElse: 0), original)
        }
        let restarted = self.start()
        try self.awaitStatus(.invalidPendingCapture, in: restarted)
        XCTAssertEqual(try self.pending().count, invalid.count)
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

    func testUsageFacadeReportsSnapshotAndPayloadCounts() throws {
        _ = try self.queueDeletion(text: "usage fixture")
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let usage = try XCTUnwrap(self.localArchiveValue(coordinator.usage()))
        XCTAssertEqual(usage.snapshots, 1)
        XCTAssertGreaterThan(usage.payloadBytes, Int64("usage fixture".utf8.count))
    }

    func testCommittedDeletionIsSearchableThroughAccountFacade() throws {
        _ = try self.queueDeletion(text: "Local searchable evidence")
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.searchMessages(query: "searchable evidence")).records.count, 1)
        XCTAssertTrue(try self.localArchiveValue(coordinator.searchMessages(query: "searchable", peerId: 999)).records.isEmpty)
    }

    func testMediaSignalCompletesAfterCoordinatorRelease() throws {
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        weak var coordinator = instance.coordinator
        let signal = try XCTUnwrap(instance.coordinator).mediaData(eventKey: "unavailable", slot: 0)
        try self.stop(instance)
        XCTAssertNil(coordinator, "an unstarted media signal must not retain an account coordinator")
        XCTAssertNil(try self.localArchiveValue(signal))
    }

    func testMalformedMimeDoesNotBlockDurableMediaCapture() throws {
        let bytes = Data([1, 2, 3])
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9500, accessHash: 0, size: Int64(bytes.count), fileReference: nil, fileName: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9500), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/pdf\0untrusted", size: Int64(bytes.count), attributes: [], alternativeRepresentations: [])
        _ = try self.queueDeletion(95, media: [file])
        let key = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        let store = try XCTUnwrap(instance.storage.with { $0?.store })
        let done = expectation(description: "read normalized MIME attachment")
        var result: Result<ArchivedMedia?, ArchiveError>?
        store.media(eventKey: key, slot: 0) { result = $0; done.fulfill() }
        wait(for: [done], timeout: 15)
        let media = try XCTUnwrap(try XCTUnwrap(result).get())
        XCTAssertEqual(media.mimeType, "application/octet-stream")
        XCTAssertEqual(media.data, bytes)
    }

    func testOversizedMediaReportsGapWithoutDiscardingText() throws {
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.maxPayloadBytes = 1024 * 1024
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        let bytes = Data(repeating: 0x58, count: 1024 * 1024)
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9400, accessHash: 0, size: Int64(bytes.count), fileReference: nil, fileName: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9400), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/pdf", size: Int64(bytes.count), attributes: [], alternativeRepresentations: [])
        _ = try self.queueDeletion(94, media: [file])
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertEqual(page.records.count, 1)
        XCTAssertEqual(page.records.first?.storedMediaCount, 0)
        XCTAssertEqual(page.records.first?.unavailableMediaCount, 1)
        let settings = try self.localArchiveValue(coordinator.settings)
        XCTAssertEqual(settings.droppedCaptures, 1)
        try self.stop(instance)
        try self.fixture.reopen()
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages()).records.count, 1)
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).settings).droppedCaptures, 1)
    }

    func testTruncatedCompletedFileIsNotArchivedAsCompleteMedia() throws {
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9300, accessHash: 0, size: 12, fileReference: nil, fileName: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: Data([1, 2]), synchronous: true)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9300), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/pdf", size: 12, attributes: [], alternativeRepresentations: [])
        _ = try self.queueDeletion(92, media: [file])
        let key = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        let page = try self.localArchiveValue(try XCTUnwrap(instance.coordinator).deletedMessages())
        XCTAssertEqual(page.records.first?.storedMediaCount, 0)
        XCTAssertEqual(page.records.first?.unavailableMediaCount, 1)
        let store = try XCTUnwrap(instance.storage.with { $0?.store })
        let done = expectation(description: "missing truncated attachment")
        var result: Result<ArchivedMedia?, ArchiveError>?
        store.media(eventKey: key, slot: 0) { result = $0; done.fulfill() }
        wait(for: [done], timeout: 15)
        XCTAssertNil(try XCTUnwrap(result).get())
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

    func testGalleryFacadeListsOnlyMediaCapturedFromPostbox() throws {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9700, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9700), representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource, progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(97, text: "gallery caption", media: [photo])
        let missing = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9701), representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(98, text: "missing bytes", media: [missing])
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let page = try self.localArchiveValue(coordinator.mediaItems())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.items.count, 1)
        XCTAssertEqual(page.items.first?.caption, "gallery caption")
        XCTAssertEqual(page.items.first?.byteCount, Int64(bytes.count))
        XCTAssertEqual(page.items.first?.kind, "photo")
        XCTAssertTrue(try self.localArchiveValue(coordinator.mediaItems(kind: "video")).items.isEmpty)
    }

    func testLargeDocumentStreamsFromRealPostboxAndSurvivesCacheRemoval() throws {
        let size: Int64 = 20 * 1024 * 1024
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9600, accessHash: 0, size: size, fileReference: nil, fileName: nil)
        let path = self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete
        XCTAssertTrue(FileManager.default.createFile(atPath: path, contents: nil))
        let writer = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
        let chunk = Data(repeating: 0x69, count: 64 * 1024)
        for _ in 0..<320 { try writer.write(contentsOf: chunk) }
        try writer.close()
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9600), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/octet-stream", size: size, attributes: [.FileName(fileName: "large.bin")], alternativeRepresentations: [])
        _ = try self.queueDeletion(96, media: [file])
        let key = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        XCTAssertTrue(try self.pending().isEmpty)
        let coordinator = try XCTUnwrap(first.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.first?.storedMediaCount, 1)
        XCTAssertEqual(try self.localArchiveValue(coordinator.mediaInfo(eventKey: key, slot: 0))?.byteCount, size)
        let cleared = expectation(description: "remove large document cache")
        let removal = self.fixture.postbox.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { cleared.fulfill() })
        wait(for: [cleared], timeout: 15)
        removal.dispose()
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(first.coordinator).setEnabled(false)))
        try self.awaitStatus(.disabled, in: first)
        try self.stop(first)
        try self.fixture.reopen()
        let second = self.start()
        let reader = try XCTUnwrap(second.coordinator)
        let access = reader.acquireReadAccess()
        defer { access.dispose() }
        try self.awaitStatus(.browsing, in: second)
        XCTAssertEqual(try self.localArchiveValue(reader.mediaItems()).items.count, 1)
        let reopened = try XCTUnwrap(second.coordinator)
        XCTAssertEqual(try self.localArchiveValue(reopened.mediaInfo(eventKey: key, slot: 0))?.byteCount, size)
        XCTAssertEqual(try self.localArchiveValue(reopened.mediaRange(eventKey: key, slot: 0, offset: size - 7, count: 64)), Data(repeating: 0x69, count: 7))
    }

    func testOrdinaryCloudOwnedSourceSurvivesForcedCacheRemovalAndSameSizeReplacementBeforeWorkerCopy() throws {
        let original = Data(repeating: 0x42, count: 128 * 1024)
        let replacement = Data(repeating: 0x64, count: original.count)
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9601, accessHash: 0,
            size: Int64(original.count), fileReference: nil, fileName: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: original, synchronous: true)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9601),
            partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [],
            immediateThumbnailData: nil, mimeType: "application/octet-stream", size: Int64(original.count),
            attributes: [.FileName(fileName: "owned-source.bin")], alternativeRepresentations: [])
        _ = try self.queueDeletion(106, media: [file])
        let eventKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let blockNextClock = Atomic<Bool>(value: false)
        let didIntercept = Atomic<Bool>(value: false)
        let gateTimedOut = Atomic<Bool>(value: false)
        let workerBlocked = self.expectation(description: "prior real storage operation blocks cloud media copy")
        let priorOperationDone = self.expectation(description: "prior real storage operation completed")
        let releaseWorker = DispatchSemaphore(value: 0)
        defer { releaseWorker.signal() }
        let first = self.start(clock: {
            if blockNextClock.swap(false) {
                workerBlocked.fulfill()
                if releaseWorker.wait(timeout: .now() + 15) == .timedOut { _ = gateTimedOut.swap(true) }
            }
            return 1000
        }, beforeOwnedMediaWrite: { store in
            // This callback is reached only with the actual owned-source API.
            // The coordinator has acquired its descriptor on its utility queue.
            guard !didIntercept.swap(true) else { XCTFail("Unexpected repeated media submission"); return }
            _ = blockNextClock.swap(true)
            // Enqueue a separate earlier operation. Holding the media method's
            // own clock would not prove its file had not already been opened.
            store.setRetention(ArchiveRetentionPolicy()) { result in
                if case .failure = result { XCTFail("Prior real retention operation failed") }
                priorOperationDone.fulfill()
            }
        })
        self.wait(for: [workerBlocked], timeout: 15)
        XCTAssertTrue(didIntercept.with { $0 })
        XCTAssertEqual(try self.pending().count, 1)
        let cleared = self.expectation(description: "real force-clear before cloud archive worker copy")
        let removal = self.fixture.postbox.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { cleared.fulfill() })
        defer { removal.dispose() }
        self.wait(for: [cleared], timeout: 15)
        let path = self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete
        XCTAssertFalse(FileManager.default.fileExists(atPath: path))
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: replacement, synchronous: true)
        XCTAssertEqual(try localArchiveReadCompletedMedia(path), replacement)
        releaseWorker.signal()
        self.wait(for: [priorOperationDone], timeout: 15)
        try self.awaitStatus(.ready, in: first)
        XCTAssertFalse(gateTimedOut.with { $0 })
        XCTAssertTrue(try self.pending().isEmpty)
        let coordinator = try XCTUnwrap(first.coordinator)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.count, 1)
        XCTAssertEqual(page.records.first?.storedMediaCount, 1)
        XCTAssertEqual(page.records.first?.unavailableMediaCount, 0)
        XCTAssertEqual(try self.localArchiveValue(coordinator.settings).droppedCaptures, 0)
        XCTAssertEqual(try self.localArchiveValue(coordinator.mediaData(eventKey: eventKey, slot: 0)), original)
        XCTAssertEqual(try localArchiveReadCompletedMedia(path), replacement, "Archive import must not alter the new cache resource")
        XCTAssertTrue(try self.localArchiveValue(coordinator.setEnabled(false)))
        try self.awaitStatus(.disabled, in: first)
        try self.stop(first)
        try self.fixture.reopen()
        let reopened = self.start()
        let reader = try XCTUnwrap(reopened.coordinator)
        let access = reader.acquireReadAccess()
        defer { access.dispose() }
        try self.awaitStatus(.browsing, in: reopened)
        XCTAssertEqual(try self.localArchiveValue(reader.mediaData(eventKey: eventKey, slot: 0)), original)
    }

    func testMediaCommitBeforeOutboxAckSurvivesRestartAndSourceRemoval() throws {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9200, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
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

    func testPeerExclusionAfterMetadataCommitDoesNotImportMediaOrStallReceiptReplay() throws {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9602, accessHash: 0,
            sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9602),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(107, text: "metadata committed before exclusion", media: [photo])
        let originalEventKey = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let gate = LocalArchiveOperationGate()
        defer { gate.release() }
        let first = self.start(metadataAcknowledgementGate: gate)
        _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
        XCTAssertEqual(try self.pending().count, 1)
        let coordinator = try XCTUnwrap(first.coordinator)
        XCTAssertTrue(try self.localArchiveValue(coordinator.setPeerExcluded(peerId: self.chatPeerId.toInt64(), excluded: true)))
        // A genuine Store callback runs on the coordinator's callback queue after
        // the committed Postbox preference notification has been enqueued there.
        let policyObserved = self.expectation(description: "coordinator callback queue observed new exclusion")
        let store = try XCTUnwrap(first.storage.with { $0?.store })
        store.currentGeneration { result in
            if case .failure = result { XCTFail("Real archive generation query failed") }
            policyObserved.fulfill()
        }
        self.wait(for: [policyObserved], timeout: 15)
        let allowedPeer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1003))
        let allowedId = MessageId(peerId: allowedPeer, namespace: Namespaces.Message.Cloud, id: 108)
        let owner = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(allowedPeer)], update: { _, updated in updated })
            _ = transaction.addMessages([localArchiveTestMessage(allowedId, text: "later allowed capture")], location: .Random)
            captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: owner, ids: [allowedId], observedAt: 201)
            transaction.deleteMessages([allowedId], forEachMedia: nil)
        })
        XCTAssertEqual(try self.pending().count, 2)
        // Recreate the coordinator after the real SQL commit but before its
        // acknowledgement. This exercises durable receipt replay, not an OS kill.
        try self.stop(first)
        gate.release()
        try self.fixture.reopen()
        let sawReceiptDuplicate = Atomic<Bool>(value: false)
        let sawLaterInsert = Atomic<Bool>(value: false)
        let recovered = self.start(beforeOwnedMediaWrite: { _ in
            XCTFail("Excluded attachment must not be admitted to a media write")
        }, didWrite: { result in
            if case let .success(written) = result {
                if written.duplicates == 1 && written.excluded == 0 { _ = sawReceiptDuplicate.swap(true) }
                if written.inserted == 1 { _ = sawLaterInsert.swap(true) }
            }
        })
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertTrue(sawReceiptDuplicate.with { $0 }, "Exercise the existing receipt path that does not reapply capture policy")
        XCTAssertTrue(sawLaterInsert.with { $0 }, "A denied attachment must not stall later allowed outbox entries")
        XCTAssertTrue(try self.pending().isEmpty)
        let reader = try XCTUnwrap(recovered.coordinator)
        let page = try self.localArchiveValue(reader.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.count, 2)
        let original = try XCTUnwrap(page.records.first { $0.eventKey == originalEventKey })
        XCTAssertEqual(original.text, "metadata committed before exclusion")
        XCTAssertEqual(original.storedMediaCount, 0)
        XCTAssertEqual(original.unavailableMediaCount, 1)
        XCTAssertEqual(page.records.first { $0.peerId == allowedPeer.toInt64() }?.text, "later allowed capture")
        XCTAssertNil(try self.localArchiveValue(reader.mediaData(eventKey: originalEventKey, slot: 0)))
        XCTAssertEqual(try self.localArchiveValue(reader.settings).droppedCaptures, 1)
        XCTAssertEqual(try localArchiveReadCompletedMedia(self.fixture.postbox.mediaBox.storePathsForId(resource.id).complete), bytes,
            "Policy denial must not import or erase an otherwise readable ordinary-cloud source")
    }

    func testPhotoFallbackPreservesOnlyTheCompletedLocalVariant() throws {
        let bytes = localArchiveTestPhotoData()
        let missing = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9700, accessHash: 0, sizeSpec: "x", size: nil, fileReference: nil)
        let cached = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9700, accessHash: 0, sizeSpec: "m", size: Int64(bytes.count), fileReference: nil)
        self.fixture.postbox.mediaBox.storeResourceData(cached.id, data: bytes, synchronous: true)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9700), representations: [
            TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 1024, height: 1024), resource: missing, progressiveSizes: [], immediateThumbnailData: nil),
            TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: cached, progressiveSizes: [], immediateThumbnailData: nil)
        ], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        _ = try self.queueDeletion(97, media: [photo])
        let key = try XCTUnwrap(self.pending().first?.operation?.eventKey)
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        let store = try XCTUnwrap(instance.storage.with { $0?.store })
        let done = expectation(description: "read available photo rendition")
        var result: Result<ArchivedMedia?, ArchiveError>?
        store.media(eventKey: key, slot: 0) { result = $0; done.fulfill() }
        wait(for: [done], timeout: 15)
        XCTAssertEqual(try XCTUnwrap(result).get()?.data, bytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.fixture.postbox.mediaBox.storePathsForId(missing.id).complete))
    }

    func testCompletedPhotoBytesSurviveCacheRemovalAndRestart() throws {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9001, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
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
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(first.coordinator).setEnabled(false)))
        try self.awaitStatus(.disabled, in: first)
        try self.stop(first)
        try self.fixture.reopen()
        let second = self.start()
        let reader = try XCTUnwrap(second.coordinator)
        let access = reader.acquireReadAccess()
        defer { access.dispose() }
        try self.awaitStatus(.browsing, in: second)
        XCTAssertEqual(try self.localArchiveValue(reader.mediaData(eventKey: eventKey, slot: 0)), bytes)
        XCTAssertEqual(try self.localArchiveValue(reader.mediaInfo(eventKey: eventKey, slot: 0))?.byteCount, Int64(bytes.count))
        XCTAssertEqual(try self.localArchiveValue(reader.mediaRange(eventKey: eventKey, slot: 0, offset: 0, count: 16)), Data(bytes.prefix(16)))
        XCTAssertEqual(try self.localArchiveValue(reader.mediaItems()).items.count, 1)
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

    #if os(iOS)
    func testUIKitProtectionNotificationsCloseAndReopenTheProductionArchiveGate() throws {
        _ = try self.queueDeletion(text: "retained across lock")
        // nil uses the production notification bridge, not the injected signal.
        let instance = self.start(protectedData: nil)
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let originalStorage = try XCTUnwrap(instance.storage.with { $0 })
        NotificationCenter.default.post(name: UIApplication.protectedDataWillBecomeUnavailableNotification, object: nil)
        try self.awaitStatus(.locked, in: instance)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        let _ = try self.localArchiveValue(originalStorage.closed.get() |> filter { $0 })
        NotificationCenter.default.post(name: UIApplication.protectedDataDidBecomeAvailableNotification, object: nil)
        try self.awaitStatus(.ready, in: instance)
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.first?.text, "retained across lock")
    }
    #endif

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
        let lockedGallery = try self.localArchiveValue(try XCTUnwrap(instance.coordinator).mediaItems())
        XCTAssertTrue(lockedGallery.unavailable)
        XCTAssertTrue(lockedGallery.items.isEmpty)
    }

    func testDeleteSnapshotPersistsAndRejectsLockedAccountMutation() throws {
        _ = try self.queueDeletion(88, text: "discard this saved version")
        _ = try self.queueDeletion(89, text: "keep this saved version")
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let instance = self.start(protectedData: protectedData.get())
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        let record = try XCTUnwrap(page.records.first(where: { $0.messageId == 88 }))
        protectedData.set(false)
        try self.awaitStatus(.locked, in: instance)
        XCTAssertFalse(try self.localArchiveValue(coordinator.deleteSnapshot(eventKey: record.eventKey)))
        protectedData.set(true)
        try self.awaitStatus(.ready, in: instance)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deleteSnapshot(eventKey: record.eventKey)))
        try self.stop(instance)
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        let retained = try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages())
        XCTAssertEqual(retained.records.map { $0.messageId }, [89])
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testImmediateUnlockWaitsForPreviousStoreCloseBeforeReopeningAndDeleting() throws {
        _ = try self.queueDeletion(88, text: "discard this saved version")
        _ = try self.queueDeletion(89, text: "keep this saved version")
        let closeGate = LocalArchiveOperationGate()
        defer { closeGate.release() }
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let instance = self.start(protectedData: protectedData.get(), closeGate: closeGate)
        try self.awaitStatus(.ready, in: instance)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        let record = try XCTUnwrap(page.records.first(where: { $0.messageId == 88 }))
        protectedData.set(false)
        try self.awaitStatus(.locked, in: instance)
        let _ = try self.localArchiveValue(closeGate.intercepted.get() |> filter { $0 })
        protectedData.set(true)
        // Two account-queue reads fence the unlock wake and its nested drain.
        // The real old database is deliberately still open at this point.
        for _ in 0..<2 {
            XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        }
        XCTAssertEqual(instance.factoryOpenCount.with { $0 }, 1)
        closeGate.release()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertEqual(instance.factoryOpenCount.with { $0 }, 2)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deleteSnapshot(eventKey: record.eventKey)))
        try self.stop(instance)
        let recovered = self.start()
        try self.awaitStatus(.ready, in: recovered)
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(recovered.coordinator).deletedMessages()).records.map { $0.messageId }, [89])
    }

    func testUnlockDuringPendingOpenWaitsForStaleResultAndItsCloseAcknowledgement() throws {
        _ = try self.queueDeletion(90, text: "retained after a cancelled open")
        let openGate = LocalArchiveOperationGate(), closeGate = LocalArchiveOperationGate()
        defer { openGate.release(); closeGate.release() }
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let instance = self.start(protectedData: protectedData.get(), openGate: openGate, closeGate: closeGate)
        let coordinator = try XCTUnwrap(instance.coordinator)
        let _ = try self.localArchiveValue(openGate.intercepted.get() |> filter { $0 })
        protectedData.set(false)
        try self.awaitStatus(.locked, in: instance)
        protectedData.set(true)
        for _ in 0..<2 {
            XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        }
        XCTAssertEqual(instance.factoryOpenCount.with { $0 }, 1)
        openGate.release()
        let _ = try self.localArchiveValue(closeGate.intercepted.get() |> filter { $0 })
        for _ in 0..<2 {
            XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        }
        XCTAssertEqual(instance.factoryOpenCount.with { $0 }, 1)
        closeGate.release()
        try self.awaitStatus(.ready, in: instance)
        XCTAssertEqual(instance.factoryOpenCount.with { $0 }, 2)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.map { $0.messageId }, [90])
        XCTAssertTrue(try self.pending().isEmpty)
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

    func testPausedCaptureCanBrowseRetainedHistoryWithoutDrainingOutboxAcrossRestart() throws {
        let retainedId = try self.queueDeletion(101, text: "saved before capture was paused")
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        try self.stop(first)
        _ = try self.queueDeletion(102, text: "queued before pause, not yet archived")
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.enabled = false
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        try self.fixture.reopen()
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let instance = self.start(protectedData: protectedData.get())
        let coordinator = try XCTUnwrap(instance.coordinator)
        let access = coordinator.acquireReadAccess()
        defer { access.dispose() }
        try self.awaitStatus(.browsing, in: instance)
        XCTAssertEqual(try self.localArchiveValue(instance.creationMode.get()), false)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
        XCTAssertEqual(try self.pending().count, 1, "a read lease must not acknowledge queued capture")
        _ = try self.queueDeletion(103, text: "must not capture while browsing")
        XCTAssertEqual(try self.pending().count, 1)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.map { $0.messageId }, [retainedId.id])
        XCTAssertEqual(try self.localArchiveValue(coordinator.searchMessages(query: "saved")).records.map { $0.messageId }, [retainedId.id])
        XCTAssertEqual(try self.localArchiveValue(coordinator.timeline(peerId: retainedId.peerId.toInt64(), namespace: retainedId.namespace, messageId: retainedId.id)).records.count, 1)

        protectedData.set(false)
        try self.awaitStatus(.locked, in: instance)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        protectedData.set(true)
        try self.awaitStatus(.browsing, in: instance)
        XCTAssertEqual(try self.pending().count, 1)
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.count, 1)

        XCTAssertTrue(try self.localArchiveValue(coordinator.requestPurge()))
        let _ = try self.localArchiveValue(coordinator.settings |> filter { $0.generation == 1 && !$0.pendingPurge })
        try self.awaitStatus(.browsing, in: instance)
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).records.isEmpty)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
        access.dispose()
        try self.awaitStatus(.disabled, in: instance)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
        try self.stop(instance)
        try self.fixture.reopen()
        let restarted = self.start()
        let restartedCoordinator = try XCTUnwrap(restarted.coordinator)
        let reopenedAccess = restartedCoordinator.acquireReadAccess()
        defer { reopenedAccess.dispose() }
        try self.awaitStatus(.browsing, in: restarted)
        XCTAssertFalse(try self.localArchiveValue(restartedCoordinator.settings).enabled)
        XCTAssertTrue(try self.localArchiveValue(restartedCoordinator.deletedMessages()).records.isEmpty)
        XCTAssertTrue(try self.pending().isEmpty)
    }

    func testChatSenderAndBotExclusionsPersistAndRetireQueuedCapturesBeforeMediaCopy() throws {
        let sender = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(2001))
        let otherChat = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(2002))
        let bot = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(2003))
        let unknownSender = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(2004))
        let resource = CloudDocumentMediaResource(datacenterId: 1, fileId: 9910, accessHash: 1, size: 4, fileReference: nil, fileName: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: Data([1, 2, 3, 4]), synchronous: true)
        let file = TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: 9910), partialReference: nil, resource: resource, previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: "application/pdf", size: 4, attributes: [], alternativeRepresentations: [])
        let ids = [self.chatPeerId, otherChat, bot, otherChat].enumerated().map { MessageId(peerId: $0.element, namespace: Namespaces.Message.Cloud, id: Int32(110 + $0.offset)) }
        let accountPeerId = self.accountPeerId
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(sender), localArchiveTestUser(otherChat), localArchiveTestUser(bot, bot: true)], update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true, excludeBots: false))
            for (index, id) in ids.enumerated() {
                let authorId = index == 1 ? sender : (index == 3 ? unknownSender : id.peerId)
                let _ = transaction.addMessages([localArchiveTestMessage(id, text: "excluded queued snapshot", authorId: authorId, media: [file])], location: .Random)
                captureDeletedLocalArchiveMessages(transaction: transaction, accountPeerId: accountPeerId, ids: [id], observedAt: 200)
                transaction.deleteMessages([id], forEachMedia: nil)
            }
        })
        XCTAssertEqual(try self.pending().count, 4)
        XCTAssertNil(try self.pending().last?.operation?.authorId, "fixture must preserve an unavailable author")
        let locked = self.start(protectedData: .single(false))
        let settingsController = try XCTUnwrap(locked.coordinator)
        XCTAssertTrue(try self.localArchiveValue(settingsController.setPeerExcluded(peerId: self.chatPeerId.toInt64(), excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(settingsController.setSenderExcluded(senderId: sender.toInt64(), excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(settingsController.setExcludeBots(true)))
        try self.stop(locked)
        try self.fixture.reopen()
        let restarted = self.start()
        try self.awaitStatus(.ready, in: restarted)
        let coordinator = try XCTUnwrap(restarted.coordinator)
        let settings = try self.localArchiveValue(coordinator.settings)
        XCTAssertEqual(settings.excludedPeerIds, [self.chatPeerId.toInt64()])
        XCTAssertEqual(settings.excludedSenderIds, [sender.toInt64()])
        XCTAssertTrue(settings.excludeBots)
        XCTAssertEqual(settings.droppedCaptures, 0, "deliberate exclusions are not storage failures")
        XCTAssertTrue(try self.pending().isEmpty)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).records.isEmpty)
        XCTAssertTrue(try self.localArchiveValue(coordinator.mediaItems()).items.isEmpty)
        let store = try XCTUnwrap(restarted.storage.with { $0 })
        XCTAssertFalse(try self.localArchiveValue(store.mediaWritten.get()), "excluded captures must never copy media")
        let remaining = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in ids.compactMap { transaction.getMessage($0) } })
        XCTAssertTrue(remaining.isEmpty, "exclusions must not alter canonical deletion")
        XCTAssertTrue(try self.localArchiveValue(coordinator.setPeerExcluded(peerId: self.chatPeerId.toInt64(), excluded: false)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setSenderExcluded(senderId: sender.toInt64(), excluded: false)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setExcludeBots(false)))
        try self.stop(restarted)
        try self.fixture.reopen()
        let resumed = self.start()
        try self.awaitStatus(.ready, in: resumed)
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(resumed.coordinator).deletedMessages()).records.isEmpty, "removing exclusions must not resurrect consumed captures")
    }

    func testSenderExclusionSkipsNewCapturesButKeepsExistingHistoryAfterRestart() throws {
        _ = try self.queueDeletion(114, text: "history predates exclusion")
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        let coordinator = try XCTUnwrap(first.coordinator)
        XCTAssertTrue(try self.localArchiveValue(coordinator.setSenderExcluded(senderId: self.chatPeerId.toInt64(), excluded: true)))
        try self.stop(first)
        try self.fixture.reopen()
        _ = try self.queueDeletion(115, text: "new excluded sender text")
        XCTAssertTrue(try self.pending().isEmpty)
        let restarted = self.start()
        try self.awaitStatus(.ready, in: restarted)
        let reader = try XCTUnwrap(restarted.coordinator)
        XCTAssertEqual(try self.localArchiveValue(reader.deletedMessages()).records.map { $0.messageId }, [114])
        XCTAssertEqual(try self.localArchiveValue(reader.settings).droppedCaptures, 0)
        XCTAssertTrue(try self.localArchiveValue(reader.setSenderExcluded(senderId: self.chatPeerId.toInt64(), excluded: false)))
        try self.stop(restarted)
        _ = try self.queueDeletion(116, text: "newly allowed sender")
        let resumed = self.start()
        try self.awaitStatus(.ready, in: resumed)
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(resumed.coordinator).deletedMessages()).records.map { $0.messageId }, [116, 114])
    }

    func testPausedReadLeasesAreAccountScopedAndLastDisposalClosesStorage() throws {
        _ = try self.queueDeletion(104)
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        let coordinator = try XCTUnwrap(first.coordinator)
        let firstAccess = coordinator.acquireReadAccess()
        let secondAccess = coordinator.acquireReadAccess()
        defer { firstAccess.dispose(); secondAccess.dispose() }
        XCTAssertTrue(try self.localArchiveValue(coordinator.setEnabled(false)))
        try self.awaitStatus(.browsing, in: first)
        firstAccess.dispose()
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.count, 1)

        let other = try LocalArchivePostboxFixture(test: self, name: "archive-disabled-other")
        let second = self.start(postbox: other.postbox, accountId: 18)
        defer { try? self.stop(second); try? other.destroy() }
        let otherCoordinator = try XCTUnwrap(second.coordinator)
        let otherAccess = otherCoordinator.acquireReadAccess()
        defer { otherAccess.dispose() }
        XCTAssertEqual(try self.localArchiveValue(second.creationMode.get() |> filter { $0 != nil }), false)
        try self.awaitStatus(.disabled, in: second)
        XCTAssertNil(second.storage.with { $0 })
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.archiveDirectory.appendingPathComponent("account-18").path))
        XCTAssertTrue(try self.localArchiveValue(otherCoordinator.deletedMessages()).unavailable)
        XCTAssertTrue(try self.localArchiveValue(coordinator.setPeerExcluded(peerId: self.chatPeerId.toInt64(), excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(otherCoordinator.settings).excludedPeerIds.isEmpty)
        secondAccess.dispose()
        try self.awaitStatus(.disabled, in: first)
        XCTAssertTrue(try self.localArchiveValue(coordinator.deletedMessages()).unavailable)
    }

    func testPausedBrowsingEnforcesAgeRetentionAfterRestart() throws {
        _ = try self.queueDeletion(105, text: "expires without any new capture")
        let first = self.start()
        try self.awaitStatus(.ready, in: first)
        let coordinator = try XCTUnwrap(first.coordinator)
        XCTAssertTrue(try self.localArchiveValue(coordinator.setRetention(maxAgeSeconds: 86400, maxPayloadBytes: 32 * 1024 * 1024)))
        XCTAssertTrue(try self.localArchiveValue(coordinator.setEnabled(false)))
        try self.awaitStatus(.disabled, in: first)
        try self.stop(first)
        try self.fixture.reopen()
        let restarted = self.start(clock: { 100_000 })
        let reader = try XCTUnwrap(restarted.coordinator)
        let access = reader.acquireReadAccess()
        defer { access.dispose() }
        try self.awaitStatus(.browsing, in: restarted)
        let page = try self.localArchiveValue(reader.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertTrue(page.records.isEmpty)
        XCTAssertEqual(try self.localArchiveValue(reader.usage())?.snapshots, 0)
        XCTAssertFalse(try self.localArchiveValue(reader.settings).enabled)
    }

    func testExistingOnlyOpenNeverCreatesArchiveOrKeyAndRejectsOrphanSidecar() throws {
        let key = LocalArchiveReadKeyProbe(result: .failure(.invalidKey))
        let missing = expectation(description: "existing-only archive is absent")
        ArchiveStore.open(baseDirectory: self.archiveDirectory, accountId: 17, keyProvider: key, createIfMissing: false) { result in
            guard case .failure(.missingArchive) = result else { XCTFail("must report absence without creation"); missing.fulfill(); return }
            missing.fulfill()
        }
        wait(for: [missing], timeout: 15)
        XCTAssertTrue(key.requests.with { $0 }.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.archiveDirectory.path))

        let accountDirectory = self.archiveDirectory.appendingPathComponent("account-17", isDirectory: true)
        try FileManager.default.createDirectory(at: accountDirectory, withIntermediateDirectories: true)
        let orphan = accountDirectory.appendingPathComponent("archive.sqlite-wal")
        let bytes = Data([1, 7, 3])
        try bytes.write(to: orphan)
        let damaged = expectation(description: "orphan sidecar is preserved")
        ArchiveStore.open(baseDirectory: self.archiveDirectory, accountId: 17, keyProvider: key, createIfMissing: false) { result in
            guard case .failure(.invalidInput) = result else { XCTFail("must distinguish orphan data from no archive"); damaged.fulfill(); return }
            damaged.fulfill()
        }
        wait(for: [damaged], timeout: 15)
        XCTAssertTrue(key.requests.with { $0 }.isEmpty)
        XCTAssertEqual(try Data(contentsOf: orphan), bytes)
        XCTAssertFalse(FileManager.default.fileExists(atPath: accountDirectory.appendingPathComponent("archive.sqlite").path))
    }

    func testExistingOnlyOpenDoesNotReplaceMissingKeyOrWrongKey() throws {
        _ = try self.queueDeletion(106)
        let instance = self.start()
        try self.awaitStatus(.ready, in: instance)
        try self.stop(instance)
        let database = self.archiveDirectory.appendingPathComponent("account-17/archive.sqlite")
        let original = try Data(contentsOf: database)
        let keys: [LocalArchiveReadKeyProbe] = [
            LocalArchiveReadKeyProbe(result: .failure(.invalidKey)),
            LocalArchiveReadKeyProbe(result: .success(Data(repeating: 0xff, count: 32)))
        ]
        for key in keys {
            let done = expectation(description: "existing archive key failure")
            ArchiveStore.open(baseDirectory: self.archiveDirectory, accountId: 17, keyProvider: key, createIfMissing: false) { result in
                if case .success = result { XCTFail("must not open archive without its original key") }
                done.fulfill()
            }
            wait(for: [done], timeout: 15)
            XCTAssertEqual(key.requests.with { $0 }, [false])
            XCTAssertEqual(try Data(contentsOf: database), original)
        }
        let restarted = self.start()
        try self.awaitStatus(.ready, in: restarted)
        XCTAssertEqual(try self.localArchiveValue(try XCTUnwrap(restarted.coordinator).deletedMessages()).records.map { $0.messageId }, [106])
    }
}
