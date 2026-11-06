// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

/// These tests use the production encrypted Postbox, coordinator and SQLCipher
/// store. Only Keychain is substituted with isolated synthetic test keys.
final class LocalArchiveTransferTests: XCTestCase {
    private final class Keys: ArchiveKeyProvider {
        let requests = Atomic<[Bool]>(value: [])
        let bytes: Data
        init(_ byte: UInt8) { self.bytes = Data(repeating: byte, count: 32) }
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            let _ = self.requests.modify { $0 + [createIfMissing] }
            return self.bytes
        }
    }

    private final class Running {
        var coordinator: LocalArchiveCoordinator?
        var lease: Disposable?
        let keys = Keys(0x63)
        let probed = ValuePromise<Bool>(false, ignoreRepeated: true)
        let handoffQueue = Atomic<DispatchQueue?>(value: nil)
        let stores = Atomic<[ArchiveStore]>(value: [])
    }

    /// Holds one genuine SQLCipher lifecycle callback; later callbacks are unblocked.
    private final class TransferGate {
        let intercepted = ValuePromise<Bool>(false, ignoreRepeated: true)
        let queue = Atomic<DispatchQueue?>(value: nil)
        private let first = Atomic<Bool>(value: true)
        private let delivery = Atomic<(() -> Void)?>(value: nil)

        func perform(queue: DispatchQueue, delivery: @escaping () -> Void) {
            _ = self.queue.swap(queue)
            guard self.first.swap(false) else { delivery(); return }
            _ = self.delivery.swap(delivery)
            self.intercepted.set(true)
        }

        func release() { self.delivery.swap(nil)?() }
    }

    private struct ObservedTransfer {
        let disposable: Disposable
        let result: Atomic<LocalArchiveTransferResult?>
        let values: Atomic<Int>
        let finished: XCTestExpectation
    }

    /// Transparent real-store forwarding, with only close delivery gated.
    private final class CloseGatedStorage: LocalArchiveStorage {
        let store: ArchiveStore
        let gate: TransferGate
        let queue: DispatchQueue
        init(store: ArchiveStore, gate: TransferGate, queue: DispatchQueue) {
            self.store = store; self.gate = gate; self.queue = queue
        }
        func setMutationObserver(_ observer: (() -> Void)?, completion: @escaping () -> Void) {
            self.store.setMutationObserver(observer, completion: completion)
        }
        func recordFromOutbox(_ capture: ArchiveCapture, index: Int64, generation: Int64, policy: ArchiveCapturePolicy,
                              completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void) {
            self.store.recordFromOutbox(capture, index: index, generation: generation, policy: policy, completion: completion)
        }
        func recordMedia(eventKey: String, slot: Int, kind: String, mimeType: String, data: Data, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
            self.store.recordMedia(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, data: data, generation: generation, completion: completion)
        }
        func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: URL, expectedSize: Int64?, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
            self.store.recordMediaFile(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, source: source, expectedSize: expectedSize, generation: generation, completion: completion)
        }
        func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: ArchiveMediaFileSource, expectedSize: Int64?, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void) {
            self.store.recordMediaFile(eventKey: eventKey, slot: slot, kind: kind, mimeType: mimeType, source: source, expectedSize: expectedSize, generation: generation, completion: completion)
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
            self.gate.perform(queue: self.queue) { self.store.close(completion: completion) }
        }
        func deletedMessages(peerId: Int64?, beforeSequence: Int64?, limit: Int, completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
            self.store.deletedMessages(peerId: peerId, beforeSequence: beforeSequence, limit: limit, completion: completion)
        }
        func deletedHistory(peerId: Int64, namespace: Int32, messageIds: ClosedRange<Int32>, sentAt: ClosedRange<Int64>, excludingAuthors: Set<Int64>, limit: Int, completion: @escaping (Result<ArchiveDeletedHistoryPage, ArchiveError>) -> Void) {
            self.store.deletedHistory(peerId: peerId, namespace: namespace, messageIds: messageIds, sentAt: sentAt, excludingAuthors: excludingAuthors, limit: limit, completion: completion)
        }
        func timeline(for message: ArchiveMessageID, beforeSequence: Int64?, limit: Int, completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) {
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
        func exportBackup(to destination: URL, password: String, ownerId: Int64?, testingEnvironment: Bool, cancellation: ArchiveTransferCancellation, completion: @escaping (Result<ArchiveTransferExport, ArchiveTransferError>) -> Void) {
            self.store.exportBackup(to: destination, password: password, ownerId: ownerId, testingEnvironment: testingEnvironment, cancellation: cancellation, completion: completion)
        }
        func importPreparedBackup(_ prepared: ArchivePreparedImport, ownerId: Int64, testingEnvironment: Bool, expectedGeneration: Int64, cancellation: ArchiveTransferCancellation, completion: @escaping (Result<ArchiveTransferImport, ArchiveTransferError>) -> Void) {
            self.store.importPreparedBackup(prepared, ownerId: ownerId, testingEnvironment: testingEnvironment, expectedGeneration: expectedGeneration, cancellation: cancellation, completion: completion)
        }
    }

    private let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(51))
    private let password = "a separate archive backup password"
    private let media = Data("an archived media payload".utf8)
    private var fixture: LocalArchivePostboxFixture!
    private var root: URL!
    private var running: [Running] = []
    private var sourceStores: [ArchiveStore] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-transfer-facade")
        self.root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-transfer-facade-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(self.owner)], update: { _, peer in peer })
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: false))
        })
    }

    override func tearDownWithError() throws {
        for instance in self.running { try self.stop(instance) }
        for store in self.sourceStores { try self.close(store) }
        self.running.removeAll(); self.sourceStores.removeAll()
        try self.fixture?.destroy(); self.fixture = nil
        if let root = self.root { try? FileManager.default.removeItem(at: root) }
    }

    private enum BackupStage: String {
        case openSource, recordVersion, recordMedia, exportSnapshot
    }

    private func awaitResult<T, E: Error>(_ stage: BackupStage, file: StaticString = #filePath, line: UInt = #line,
                                         _ action: (@escaping (Result<T, E>) -> Void) -> Void) throws -> T {
        let finished = self.expectation(description: "real archive transfer result: " + stage.rawValue)
        let result = Atomic<Result<T, E>?>(value: nil)
        action { value in let _ = result.swap(value); finished.fulfill() }
        self.wait(for: [finished], timeout: 60)
        let value = try XCTUnwrap(result.with { $0 }, file: file, line: line)
        if case .failure = value {
            // Closed stage names identify fixture failures without logging
            // passwords, SQL, keys, paths or underlying exception payloads.
            XCTFail("Encrypted archive fixture failed during " + stage.rawValue, file: file, line: line)
        }
        return try value.get()
    }

    private func transferValue(_ signal: Signal<LocalArchiveTransferResult, NoError>) throws -> LocalArchiveTransferResult {
        let finished = self.expectation(description: "real account transfer signal")
        let result = Atomic<LocalArchiveTransferResult?>(value: nil)
        let disposable = (signal |> take(1)).start(next: { value in
            let _ = result.swap(value)
        }, completed: { finished.fulfill() })
        defer { disposable.dispose() }
        self.wait(for: [finished], timeout: 60)
        return try XCTUnwrap(result.with { $0 })
    }

    private func observeTransfer(_ signal: Signal<LocalArchiveTransferResult, NoError>) -> ObservedTransfer {
        let finished = self.expectation(description: "queued account transfer completes once")
        let result = Atomic<LocalArchiveTransferResult?>(value: nil)
        let values = Atomic<Int>(value: 0)
        let disposable = signal.start(next: { value in
            _ = result.swap(value)
            _ = values.modify { $0 + 1 }
        }, completed: { finished.fulfill() })
        return ObservedTransfer(disposable: disposable, result: result, values: values, finished: finished)
    }

    private func awaitHandoff(_ gate: TransferGate) throws {
        let queue = try XCTUnwrap(gate.queue.with { $0 })
        let acknowledged = self.expectation(description: "transfer handoff queue acknowledged")
        queue.async { acknowledged.fulfill() }
        self.wait(for: [acknowledged], timeout: 15)
    }

    private func assertNoDestination(_ instance: Running, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty, file: file, line: line)
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("destination").path), file: file, line: line)
    }

    private func close(_ store: ArchiveStore) throws {
        let finished = self.expectation(description: "close transfer store")
        store.close { finished.fulfill() }
        self.wait(for: [finished], timeout: 15)
    }

    private func start(owner: PeerId? = nil, testingEnvironment: Bool = false,
                       protectedData: Signal<Bool, NoError> = .single(true), openGate: TransferGate? = nil,
                       closeGate: TransferGate? = nil) -> Running {
        let instance = Running()
        instance.coordinator = LocalArchiveCoordinator(postbox: self.fixture.postbox,
            accountPeerId: owner ?? self.owner, accountId: 72, testingEnvironment: testingEnvironment,
            baseDirectory: self.root.appendingPathComponent("destination"),
            storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                // A missing existing-only probe must not request a key.
                _ = instance.handoffQueue.swap(callbackQueue)
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: instance.keys,
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue,
                    clock: { 1000 }) { result in
                    let deliver = {
                        if case let .success(store) = result { let _ = instance.stores.modify { $0 + [store] } }
                        completion(result.map { store -> LocalArchiveStorage in
                            if let closeGate { return CloseGatedStorage(store: store, gate: closeGate, queue: callbackQueue) }
                            return store
                        })
                        instance.probed.set(true)
                    }
                    if let openGate { openGate.perform(queue: callbackQueue, delivery: deliver) } else { deliver() }
                }
            }, protectedData: protectedData)
        instance.lease = instance.coordinator?.acquireReadAccess()
        self.running.append(instance)
        return instance
    }

    private func awaitMissing(_ instance: Running) throws -> LocalArchiveCoordinator {
        let coordinator = try XCTUnwrap(instance.coordinator)
        let _ = try self.localArchiveValue(instance.probed.get() |> filter { $0 })
        // Observe an actual result and its handoff acknowledgement, rather than
        // trusting the cached initial .disabled value. Later finite startup
        // drains may still be active: the production transfer must queue safely.
        let queue = try XCTUnwrap(instance.handoffQueue.with { $0 })
        let acknowledged = self.expectation(description: "missing-store probe acknowledged")
        queue.async { acknowledged.fulfill() }
        self.wait(for: [acknowledged], timeout: 15)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .disabled }), .disabled)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
        return coordinator
    }

    private func stop(_ instance: Running) throws {
        instance.lease?.dispose(); instance.lease = nil
        instance.coordinator?.dispose(); instance.coordinator = nil
        for store in instance.stores.with({ $0 }) { try self.close(store) }
    }

    private func backup(ownerId: Int64? = nil, testingEnvironment: Bool = false) throws -> URL {
        let source: ArchiveStore = try self.awaitResult(.openSource) {
            ArchiveStore.open(baseDirectory: self.root.appendingPathComponent("source"), accountId: 17,
                keyProvider: Keys(0x31), clock: { 1000 }, completion: $0)
        }
        self.sourceStores.append(source)
        let event = ArchiveEvent(eventKey: "same-telegram-user-event",
            message: .init(accountId: 17, peerId: 100, namespace: 0, messageId: 1),
            kind: .deleted, observedAt: 900,
            content: .init(text: "restored through the real account facade", authorId: 100, sentAt: 800))
        let written: ArchiveWriteResult = try self.awaitResult(.recordVersion) {
            source.record([ArchiveCapture(event: event, context: .init(ordinaryCloudMessage: true))],
                generation: 0, policy: .init(enabled: true), completion: $0)
        }
        XCTAssertEqual(written.inserted + written.duplicates, 1)
        XCTAssertEqual(written.excluded, 0)
        let retained: Bool = try self.awaitResult(.recordMedia) {
            source.recordMedia(eventKey: event.eventKey, slot: 0, kind: "document", mimeType: "application/octet-stream",
                data: self.media, generation: 0, completion: $0)
        }
        XCTAssertTrue(retained, "the export fixture requires a genuinely retained media payload")
        let url = self.root.appendingPathComponent(UUID().uuidString + ".ayuarchive")
        let _: ArchiveTransferExport = try self.awaitResult(.exportSnapshot) {
            source.exportBackup(to: url, password: self.password, ownerId: ownerId ?? self.owner.toInt64(),
                testingEnvironment: testingEnvironment, cancellation: ArchiveTransferCancellation(), completion: $0)
        }
        return url
    }

    private func assertFailure(_ result: LocalArchiveTransferResult, _ expected: LocalArchiveTransferFailure,
                               file: StaticString = #filePath, line: UInt = #line) {
        guard case let .failed(actual) = result else { return XCTFail("unexpected transfer result", file: file, line: line) }
        XCTAssertEqual(actual, expected, file: file, line: line)
    }

    func testWrongPasswordAndMalformedFileNeverCreateDestinationOrRequestKey() throws {
        let url = try self.backup()
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: "another valid backup password")), .authenticationFailed)
        let malformed = self.root.appendingPathComponent("malformed.ayuarchive")
        try Data("not an encrypted archive".utf8).write(to: malformed)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: malformed, password: self.password)), .invalidArchive)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.root.appendingPathComponent("destination/account-72/archive.sqlite").path))
    }

    func testOwnerAndEnvironmentAreAuthenticatedBeforePersistentDestinationCreation() throws {
        let wrongOwner = try self.backup(ownerId: self.owner.toInt64() + 1)
        let wrongEnvironment = try self.backup(testingEnvironment: true)
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: wrongOwner, password: self.password)), .wrongAccount)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: wrongEnvironment, password: self.password)), .wrongAccount)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
    }

    func testExplicitImportCreatesNewAccountArchiveThenReadsExportsAndReopensWithoutEnablingCapture() throws {
        let url = try self.backup()
        let instance = self.start(), coordinator = try self.awaitMissing(instance)
        guard case let .imported(added, duplicates, media, removed) = try self.transferValue(coordinator.importBackup(from: url, password: self.password)) else {
            return XCTFail("authenticated backup did not merge")
        }
        XCTAssertEqual(added, 1); XCTAssertEqual(duplicates, 0); XCTAssertEqual(media, 1); XCTAssertEqual(removed, 0)
        XCTAssertEqual(instance.keys.requests.with { $0 }, [true])
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .browsing }), .browsing)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
        let page = try self.localArchiveValue(coordinator.deletedMessages())
        XCTAssertFalse(page.unavailable)
        XCTAssertEqual(page.records.map { $0.text }, ["restored through the real account facade"])
        XCTAssertEqual(try self.localArchiveValue(coordinator.mediaRange(eventKey: "same-telegram-user-event", slot: 0, offset: 0, count: self.media.count)), self.media)
        guard case let .exported(file) = try self.transferValue(coordinator.exportBackup(password: self.password)) else {
            return XCTFail("real account facade did not export")
        }
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.url.path))
        XCTAssertGreaterThan(file.byteCount, 4096)
        let targetKey = instance.keys.bytes
        XCTAssertNil(try Data(contentsOf: file.url).range(of: targetKey))
        try self.stop(instance)
        try self.fixture.reopen()
        let reopened = self.start(), newCoordinator = try XCTUnwrap(reopened.coordinator)
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.status |> filter { $0 == .browsing }), .browsing)
        XCTAssertEqual(reopened.keys.requests.with { $0 }, [false])
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.deletedMessages()).records.map { $0.eventKey }, ["same-telegram-user-event"])
        XCTAssertEqual(try self.localArchiveValue(newCoordinator.mediaRange(eventKey: "same-telegram-user-event", slot: 0, offset: 0, count: self.media.count)), self.media)
        XCTAssertFalse(try self.localArchiveValue(newCoordinator.settings).enabled)
    }

    func testCaptureMustBePausedBeforeImportAndNoReadLeaseCannotExport() throws {
        let url = try self.backup()
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
        })
        let instance = self.start(), coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .ready }), .ready)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .captureMustBePaused)
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.count, 0)
        instance.lease?.dispose(); instance.lease = nil
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
    }

    func testLockedArchiveRefusesImportWithoutDestinationKeyAccess() throws {
        let url = try self.backup()
        let instance = self.start(protectedData: .single(false)), coordinator = try XCTUnwrap(instance.coordinator)
        XCTAssertEqual(try self.localArchiveValue(coordinator.status |> filter { $0 == .locked }), .locked)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .unavailable)
        XCTAssertTrue(instance.keys.requests.with { $0 }.isEmpty)
    }

    func testPendingImportWaitsForReadOnlyOpenRejectsSecondRequestAndAuthenticatesBeforeCreation() throws {
        let url = try self.backup()
        let gate = TransferGate()
        defer { gate.release() }
        let instance = self.start(openGate: gate), coordinator = try XCTUnwrap(instance.coordinator)
        _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
        let pending = self.observeTransfer(coordinator.importBackup(from: url, password: "another valid backup password"))
        defer { pending.disposable.dispose() }
        // This second request runs behind the first on the real handoff queue.
        // It proves the first request is reserved, rather than merely unobserved.
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
        XCTAssertNil(pending.result.with { $0 })
        self.assertNoDestination(instance)
        gate.release()
        self.wait(for: [pending.finished], timeout: 60)
        self.assertFailure(try XCTUnwrap(pending.result.with { $0 }), .authenticationFailed)
        XCTAssertEqual(pending.values.with { $0 }, 1)
        self.assertNoDestination(instance)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
    }

    func testDisposedPendingImportReleasesItsSlotAndCannotCreateDestination() throws {
        let url = try self.backup()
        let gate = TransferGate()
        defer { gate.release() }
        let instance = self.start(openGate: gate), coordinator = try XCTUnwrap(instance.coordinator)
        _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
        let cancelled = self.observeTransfer(coordinator.importBackup(from: url, password: self.password))
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
        XCTAssertNil(cancelled.result.with { $0 })
        cancelled.finished.isInverted = true
        cancelled.disposable.dispose()
        try self.awaitHandoff(gate)
        // Reusing the sole slot after cancellation must authenticate this new
        // request, never resurrect the cancelled correct-password import.
        let next = self.observeTransfer(coordinator.importBackup(from: url, password: "another valid backup password"))
        defer { next.disposable.dispose() }
        try self.awaitHandoff(gate)
        XCTAssertNil(next.result.with { $0 })
        gate.release()
        self.wait(for: [next.finished], timeout: 60)
        self.assertFailure(try XCTUnwrap(next.result.with { $0 }), .authenticationFailed)
        XCTAssertEqual(next.values.with { $0 }, 1)
        self.wait(for: [cancelled.finished], timeout: 0.1)
        XCTAssertEqual(cancelled.values.with { $0 }, 0)
        self.assertNoDestination(instance)
    }

    func testPendingImportLosingItsLastReadLeaseCancelsOnceWithoutDestinationCreation() throws {
        let url = try self.backup()
        let gate = TransferGate()
        defer { gate.release() }
        let instance = self.start(openGate: gate), coordinator = try XCTUnwrap(instance.coordinator)
        _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
        let pending = self.observeTransfer(coordinator.importBackup(from: url, password: self.password))
        defer { pending.disposable.dispose() }
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
        XCTAssertNil(pending.result.with { $0 })
        instance.lease?.dispose(); instance.lease = nil
        self.wait(for: [pending.finished], timeout: 60)
        self.assertFailure(try XCTUnwrap(pending.result.with { $0 }), .cancelled)
        gate.release()
        try self.awaitHandoff(gate)
        XCTAssertEqual(pending.values.with { $0 }, 1)
        self.assertNoDestination(instance)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .unavailable)
    }

    func testPendingImportLockAndCoordinatorDisposalCancelOnceWithoutDestinationCreation() throws {
        let url = try self.backup()
        for dispose in [false, true] {
            let gate = TransferGate()
            defer { gate.release() }
            let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
            let instance = self.start(protectedData: protectedData.get(), openGate: gate)
            let coordinator = try XCTUnwrap(instance.coordinator)
            _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
            let pending = self.observeTransfer(coordinator.importBackup(from: url, password: self.password))
            defer { pending.disposable.dispose() }
            self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
            XCTAssertNil(pending.result.with { $0 })
            if dispose { coordinator.dispose() } else { protectedData.set(false) }
            self.wait(for: [pending.finished], timeout: 60)
            self.assertFailure(try XCTUnwrap(pending.result.with { $0 }), .cancelled)
            gate.release()
            try self.awaitHandoff(gate)
            XCTAssertEqual(pending.values.with { $0 }, 1)
            self.assertNoDestination(instance)
            self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .unavailable)
            try self.stop(instance)
        }
    }

    func testPendingImportWaitsForRealCloseAcknowledgementBeforeAuthenticationAndReopen() throws {
        let url = try self.backup()
        let first = self.start(), firstCoordinator = try self.awaitMissing(first)
        guard case .imported = try self.transferValue(firstCoordinator.importBackup(from: url, password: self.password)) else {
            return XCTFail("close-barrier fixture requires a real imported archive")
        }
        try self.stop(first)
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let closeGate = TransferGate()
        defer { closeGate.release() }
        let instance = self.start(protectedData: protectedData.get(), closeGate: closeGate)
        let coordinator = try XCTUnwrap(instance.coordinator)
        _ = try self.localArchiveValue(coordinator.status |> filter { $0 == .browsing })
        XCTAssertEqual(instance.keys.requests.with { $0 }, [false])
        protectedData.set(false)
        _ = try self.localArchiveValue(closeGate.intercepted.get() |> filter { $0 })
        protectedData.set(true)
        let pending = self.observeTransfer(coordinator.importBackup(from: url, password: "another valid backup password"))
        defer { pending.disposable.dispose() }
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
        XCTAssertNil(pending.result.with { $0 })
        XCTAssertEqual(instance.keys.requests.with { $0 }, [false], "a replacement cannot open before the old close is acknowledged")
        closeGate.release()
        self.wait(for: [pending.finished], timeout: 60)
        self.assertFailure(try XCTUnwrap(pending.result.with { $0 }), .authenticationFailed)
        XCTAssertEqual(pending.values.with { $0 }, 1)
        _ = try self.localArchiveValue(coordinator.status |> filter { $0 == .browsing })
        XCTAssertTrue(instance.keys.requests.with { $0 }.allSatisfy { !$0 })
        XCTAssertEqual(try self.localArchiveValue(coordinator.deletedMessages()).records.map { $0.eventKey }, ["same-telegram-user-event"])
        XCTAssertEqual(try self.localArchiveValue(coordinator.mediaRange(eventKey: "same-telegram-user-event", slot: 0, offset: 0, count: self.media.count)), self.media)
        XCTAssertFalse(try self.localArchiveValue(coordinator.settings).enabled)
    }

    func testPendingImportPolicyChangeCancelsOnceBeforeDestinationAccess() throws {
        let url = try self.backup()
        let gate = TransferGate()
        defer { gate.release() }
        let instance = self.start(openGate: gate), coordinator = try XCTUnwrap(instance.coordinator)
        _ = try self.localArchiveValue(gate.intercepted.get() |> filter { $0 })
        let pending = self.observeTransfer(coordinator.importBackup(from: url, password: self.password))
        defer { pending.disposable.dispose() }
        self.assertFailure(try self.transferValue(coordinator.exportBackup(password: self.password)), .unavailable)
        XCTAssertNil(pending.result.with { $0 })
        _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            settings.excludeBots.toggle()
            setLocalArchiveSettings(transaction: transaction, settings: settings)
        })
        self.wait(for: [pending.finished], timeout: 60)
        self.assertFailure(try XCTUnwrap(pending.result.with { $0 }), .cancelled)
        self.assertNoDestination(instance)
        gate.release()
        try self.awaitHandoff(gate)
        XCTAssertEqual(pending.values.with { $0 }, 1)
        self.assertNoDestination(instance)
    }

    func testUnverifiedInitialPolicyCannotAuthorizeOrQueueAnImport() throws {
        let url = try self.backup()
        let entered = self.expectation(description: "initial Postbox policy read held")
        let released = self.expectation(description: "initial Postbox policy read released")
        let gate = DispatchSemaphore(value: 0)
        let blocked = self.fixture.postbox.transaction { _ in
            entered.fulfill()
            XCTAssertEqual(gate.wait(timeout: .now() + 15), .success)
        }.start(completed: { released.fulfill() })
        defer { gate.signal(); blocked.dispose() }
        self.wait(for: [entered], timeout: 15)
        let instance = self.start(), coordinator = try XCTUnwrap(instance.coordinator)
        self.assertFailure(try self.transferValue(coordinator.importBackup(from: url, password: self.password)), .unavailable)
        self.assertNoDestination(instance)
        gate.signal()
        self.wait(for: [released], timeout: 15)
        _ = try self.awaitMissing(instance)
        self.assertNoDestination(instance)
    }
}
