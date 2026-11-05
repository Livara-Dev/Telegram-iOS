// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

private final class InlineHistoryHarness {
    let status = ValuePromise<LocalArchiveStatus>(.ready, ignoreRepeated: true)
    let markers = ValuePromise<LocalArchivePresentationSettingsState>(.available(LocalArchivePresentationSettings()), ignoreRepeated: true)
    let policy = ValuePromise<LocalArchiveSettings>(LocalArchiveSettings(), ignoreRepeated: true)
    let revision = ValuePromise<Int64>(0, ignoreRepeated: true)
    var found = true
    var acquireCount = 0
    var releaseCount = 0
    var queryCount = 0
    var labels: [String?] = []
    var changed: ((String?) -> Void)?
    var queried: (() -> Void)?
    var delayedLookup: (() -> Signal<Bool, NoError>)?

    lazy var loader = LocalArchiveInlineHistoryLoader(status: self.status.get(), markers: self.markers.get(), policy: self.policy.get(), revision: self.revision.get(),
        acquire: { [weak self] in
            self?.acquireCount += 1
            return ActionDisposable { [weak self] in self?.releaseCount += 1 }
        }, lookup: { [weak self] in
            guard let self else { return .single(false) }
            self.queryCount += 1
            self.queried?()
            return self.delayedLookup?() ?? .single(self.found)
        }, debounceSeconds: 0.03, changed: { [weak self] label in
            self?.labels.append(label)
            self?.changed?(label)
        })
}

final class LocalArchiveInlineHistoryTests: XCTestCase {
    private func onMain(_ body: () -> Void) {
        if Thread.isMainThread { body() } else { DispatchQueue.main.sync(execute: body) }
    }

    private func awaitLabel(_ label: String?, harness: InlineHistoryHarness, action: () -> Void,
                            file: StaticString = #filePath, line: UInt = #line) {
        let changed = self.expectation(description: "inline marker changed")
        self.onMain {
            harness.changed = { value in if value == label { changed.fulfill() } }
            action()
        }
        self.wait(for: [changed], timeout: 10)
        self.onMain { harness.changed = nil; XCTAssertEqual(harness.labels.last ?? nil, label, file: file, line: line) }
    }

    func testInactiveCellDoesNotAcquireLeaseOrQueryArchive() {
        let harness = InlineHistoryHarness()
        self.onMain {
            harness.loader.setActive(false)
            harness.status.set(.browsing)
            XCTAssertEqual(harness.acquireCount, 0)
            XCTAssertEqual(harness.queryCount, 0)
            XCTAssertTrue(harness.labels.isEmpty)
            harness.loader.dispose()
        }
    }

    func testVisibleSavedEditLoadsLabelAndVisibilityReleaseClearsIt() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        self.onMain { XCTAssertEqual(harness.acquireCount, 1); XCTAssertEqual(harness.queryCount, 1) }
        self.awaitLabel(nil, harness: harness) { harness.loader.setActive(false) }
        self.onMain {
            XCTAssertEqual(harness.releaseCount, 1)
            harness.loader.dispose()
            XCTAssertEqual(harness.releaseCount, 1)
        }
    }

    func testServerEditedFlagWithoutSavedVersionDoesNotFabricateLocalMarker() {
        let harness = InlineHistoryHarness()
        harness.found = false
        let queried = self.expectation(description: "real existence decision requested")
        self.onMain { harness.queried = { queried.fulfill() }; harness.loader.setActive(true) }
        self.wait(for: [queried], timeout: 10)
        self.onMain {
            XCTAssertEqual(harness.queryCount, 1)
            XCTAssertTrue(harness.labels.isEmpty)
            harness.loader.dispose()
        }
    }

    func testLockClearsProofAndLateReadCannotRestoreIt() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        self.awaitLabel(nil, harness: harness) { harness.status.set(.locked) }
        let unwanted = self.expectation(description: "locked source cannot redraw history")
        unwanted.isInverted = true
        self.onMain {
            harness.changed = { if $0 != nil { unwanted.fulfill() } }
            harness.markers.set(.available(LocalArchivePresentationSettings(editedMarker: "Changed")))
        }
        self.wait(for: [unwanted], timeout: 0.12)
        self.onMain { XCTAssertEqual(harness.queryCount, 1); harness.loader.dispose() }
    }

    func testCaptureBurstCoalescesReadyTransitionsToOneIndexedRead() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) {
            harness.loader.setActive(true)
            for _ in 0 ..< 20 { harness.status.set(.writing); harness.status.set(.ready) }
        }
        self.onMain { XCTAssertEqual(harness.queryCount, 1); harness.loader.dispose() }
    }

    func testAutomaticPruningInvalidatesPreviouslyPositiveProof() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        self.awaitLabel(nil, harness: harness) { harness.status.set(.writing) }
        let queried = self.expectation(description: "requery after quota pruning")
        self.onMain {
            harness.found = false
            harness.queried = { queried.fulfill() }
            harness.status.set(.ready)
        }
        self.wait(for: [queried], timeout: 10)
        self.onMain {
            XCTAssertEqual(harness.queryCount, 2)
            XCTAssertNil(harness.labels.last ?? nil)
            harness.loader.dispose()
        }
    }

    func testCustomMarkerIsRefreshedFromAccountPreferences() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        self.awaitLabel("Earlier 👩🏽‍💻", harness: harness) {
            harness.markers.set(.available(LocalArchivePresentationSettings(deletedMarker: "Gone", editedMarker: "Earlier 👩🏽‍💻")))
        }
        self.onMain { harness.loader.dispose() }
    }

    func testCommittedMutationInvalidatesWithoutAStatusChange() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        let queried = self.expectation(description: "mutation revision requeries existing ready store")
        self.awaitLabel(nil, harness: harness) {
            harness.queried = { queried.fulfill() }
            harness.found = false
            harness.revision.set(1)
        }
        self.wait(for: [queried], timeout: 10)
        self.onMain { XCTAssertEqual(harness.queryCount, 2); XCTAssertNil(harness.labels.last ?? nil); harness.loader.dispose() }
    }

    func testUnknownPreferencesAndPendingPurgeFailClosed() {
        let harness = InlineHistoryHarness()
        self.onMain { harness.markers.set(.unsupported); harness.loader.setActive(true) }
        let unwanted = self.expectation(description: "unsupported settings do not query")
        unwanted.isInverted = true
        self.onMain { harness.queried = { unwanted.fulfill() } }
        self.wait(for: [unwanted], timeout: 0.12)
        self.onMain { harness.queried = nil }
        self.awaitLabel("Edited", harness: harness) { harness.markers.set(.available(LocalArchivePresentationSettings())) }
        self.awaitLabel(nil, harness: harness) {
            var policy = LocalArchiveSettings()
            policy.pendingPurge = true
            harness.policy.set(policy)
        }
        self.onMain { XCTAssertEqual(harness.queryCount, 1); harness.loader.dispose() }
    }

    func testAccountLoadersDoNotShareProofLabelsOrVisibility() {
        let first = InlineHistoryHarness()
        let second = InlineHistoryHarness()
        self.onMain { second.markers.set(.available(LocalArchivePresentationSettings(editedMarker: "Second account"))) }
        self.awaitLabel("Edited", harness: first) { first.loader.setActive(true) }
        self.awaitLabel("Second account", harness: second) { second.loader.setActive(true) }
        self.awaitLabel(nil, harness: first) { first.loader.setActive(false) }
        self.onMain {
            XCTAssertEqual(second.labels.last ?? nil, "Second account")
            XCTAssertEqual(second.releaseCount, 0)
            first.loader.dispose()
            second.loader.dispose()
        }
    }

    func testReactivationDoesNotReuseAStalePositiveAfterSnapshotDeletion() {
        let harness = InlineHistoryHarness()
        self.awaitLabel("Edited", harness: harness) { harness.loader.setActive(true) }
        self.awaitLabel(nil, harness: harness) { harness.loader.setActive(false) }
        let queried = self.expectation(description: "fresh query after returning to conversation")
        self.onMain {
            harness.found = false
            harness.queried = { queried.fulfill() }
            harness.loader.setActive(true)
        }
        self.wait(for: [queried], timeout: 10)
        self.onMain {
            XCTAssertEqual(harness.acquireCount, 2)
            XCTAssertNil(harness.labels.last ?? nil)
            harness.loader.dispose()
        }
    }

    func testDisposalCancelsQueuedLookupAndReleasesLeaseExactlyOnce() {
        let harness = InlineHistoryHarness()
        let unwanted = self.expectation(description: "disposed cell never performs delayed query")
        unwanted.isInverted = true
        self.onMain {
            harness.queried = { unwanted.fulfill() }
            harness.loader.setActive(true)
            harness.loader.dispose()
            harness.loader.dispose()
            harness.loader.setActive(true)
        }
        self.wait(for: [unwanted], timeout: 0.12)
        self.onMain { XCTAssertEqual(harness.acquireCount, 1); XCTAssertEqual(harness.releaseCount, 1) }
    }

    func testUnsafeMarkerCannotBeDisplayedOrTriggerAnArchiveLookup() {
        let harness = InlineHistoryHarness()
        let unwanted = self.expectation(description: "unsafe label is fail closed")
        unwanted.isInverted = true
        self.onMain {
            harness.markers.set(.available(LocalArchivePresentationSettings(editedMarker: "Hidden\u{202e}text")))
            harness.queried = { unwanted.fulfill() }
            harness.loader.setActive(true)
        }
        self.wait(for: [unwanted], timeout: 0.12)
        self.onMain { XCTAssertTrue(harness.labels.isEmpty); harness.loader.dispose() }
    }

    func testAlreadyRunningLookupCannotRedrawAfterVisibilityOrLockChanges() {
        let harness = InlineHistoryHarness()
        let lookedUp = self.expectation(description: "lookup begins")
        let pipe = ValuePipe<Bool>()
        let unwanted = self.expectation(description: "late lookup result cannot redraw")
        unwanted.isInverted = true
        self.onMain {
            harness.delayedLookup = { pipe.signal() }
            harness.queried = { lookedUp.fulfill() }
            harness.changed = { if $0 != nil { unwanted.fulfill() } }
            harness.loader.setActive(true)
        }
        self.wait(for: [lookedUp], timeout: 10)
        self.onMain {
            harness.status.set(.locked)
            harness.loader.setActive(false)
            pipe.putNext(true)
        }
        self.wait(for: [unwanted], timeout: 0.12)
        self.onMain { XCTAssertTrue(harness.labels.isEmpty); harness.loader.dispose() }
    }
}

private struct InlineHistoryTestKey: ArchiveKeyProvider {
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data { Data(repeating: 0x4b, count: 32) }
}

/// Actual Postbox replacement -> durable outbox -> SQLCipher -> the same loader
/// used by the bubble. No RPC, account-session or physical-device claim.
final class LocalArchiveInlineHistoryIntegrationTests: XCTestCase {
    private func onMain(_ body: () -> Void) {
        if Thread.isMainThread { body() } else { DispatchQueue.main.sync(execute: body) }
    }

    func testProductionServerReplacementIsDiscoverableWhileCapturePausedAndLockClearsIt() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "inline-history")
        defer { try? fixture.destroy() }
        let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9601))
        let id = MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 4)
        _ = try self.localArchiveValue(fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(owner), localArchiveTestUser(peerId)], update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: owner,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(id, text: "Original")], location: .Random)
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(id, text: "Current",
                attributes: [EditedMessageAttribute(date: 200, isHidden: true)])], location: .Random)
        })
        let current = try XCTUnwrap(self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id).map(EngineMessage.init) }))
        XCTAssertTrue(localArchiveInlineHistorySupportsMessage(current), "hidden server edited flag does not hide actual saved history")
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("inline-archive-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let protectedData = ValuePromise<Bool>(true, ignoreRepeated: true)
        let archive = LocalArchiveCoordinator(postbox: fixture.postbox, accountPeerId: owner, accountId: 96, baseDirectory: directory,
            storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: InlineHistoryTestKey(),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue) { result in
                    switch result {
                    case let .success(store): _ = opened.swap(store); completion(.success(store))
                    case let .failure(error): completion(.failure(error))
                    }
                }
            }, protectedData: protectedData.get())
        defer {
            archive.dispose()
            _ = try? self.localArchiveValue(archive.status |> filter { $0 == .unavailable })
            if let store = opened.with({ $0 }) {
                let closed = self.expectation(description: "close inline archive")
                store.close { closed.fulfill() }
                self.wait(for: [closed], timeout: 15)
            }
            try? FileManager.default.removeItem(at: directory)
        }
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .ready })
        XCTAssertTrue(try self.localArchiveValue(archive.setEnabled(false)))
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .disabled })
        let shown = self.expectation(description: "real paused archive links saved edit")
        let cleared = self.expectation(description: "locked archive hides inline proof")
        let reshown = self.expectation(description: "unlock performs fresh saved edit query")
        let deleted = self.expectation(description: "committed snapshot deletion invalidates visible proof")
        let phase = Atomic<Int>(value: 0)
        var loader: LocalArchiveInlineHistoryLoader!
        self.onMain {
            loader = LocalArchiveInlineHistoryLoader(archive: archive, presentation: LocalArchivePresentationContext(postbox: fixture.postbox), message: current, changed: { label in
                if label == "Edited", phase.with({ $0 }) == 0 { _ = phase.swap(1); shown.fulfill() }
                else if label == nil, phase.with({ $0 }) == 1 { _ = phase.swap(2); cleared.fulfill() }
                else if label == "Edited", phase.with({ $0 }) == 2 { _ = phase.swap(3); reshown.fulfill() }
                else if label == nil, phase.with({ $0 }) == 3 { _ = phase.swap(4); deleted.fulfill() }
            })
            loader.setActive(true)
        }
        defer { self.onMain { loader.dispose(); loader = nil } }
        self.wait(for: [shown], timeout: 15)
        let versions = try self.localArchiveValue(archive.timeline(peerId: peerId.toInt64(), namespace: id.namespace, messageId: id.id, limit: 1))
        XCTAssertEqual(versions.records.map(\.text), ["Original"])
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id)?.text }), "Current")
        XCTAssertFalse(try self.localArchiveValue(archive.settings).enabled, "browsing must not enable capture")
        protectedData.set(false)
        self.wait(for: [cleared], timeout: 15)
        protectedData.set(true)
        self.wait(for: [reshown], timeout: 15)
        let revision = try self.localArchiveValue(archive.historyRevision)
        let key = try XCTUnwrap(versions.records.first?.eventKey)
        XCTAssertTrue(try self.localArchiveValue(archive.deleteSnapshot(eventKey: key)))
        self.wait(for: [deleted], timeout: 15)
        XCTAssertGreaterThan(try self.localArchiveValue(archive.historyRevision), revision)
        XCTAssertEqual(try self.localArchiveValue(archive.timeline(peerId: peerId.toInt64(), namespace: id.namespace, messageId: id.id, limit: 1)).records.count, 0)
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id)?.text }), "Current")
    }

    func testCurrentTimedExpiredAndCopyProtectedMessagesNeverQualify() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "inline-eligibility")
        defer { try? fixture.destroy() }
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9602))
        let edited = EditedMessageAttribute(date: 200, isHidden: false)
        let specifications: [(namespace: Int32, attributes: [MessageAttribute], media: [Media], flags: StoreMessageFlags)] = [
            (Namespaces.Message.Cloud, [edited], [], [.Incoming]),
            (Namespaces.Message.Cloud, [], [], [.Incoming]),
            (Namespaces.Message.Cloud, [edited, AutoremoveTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)], [], [.Incoming]),
            (Namespaces.Message.Cloud, [edited, AutoclearTimeoutMessageAttribute(timeout: Int32.max, countdownBeginTime: nil)], [], [.Incoming]),
            (Namespaces.Message.Cloud, [edited], [TelegramMediaExpiredContent(data: .image)], [.Incoming]),
            (Namespaces.Message.Cloud, [edited], [], [.Incoming, .CopyProtected]),
            (Namespaces.Message.ScheduledCloud, [edited], [], [.Incoming])
        ]
        let values = try self.localArchiveValue(fixture.postbox.transaction { transaction -> [Bool] in
            transaction.updatePeersInternal([localArchiveTestUser(peerId)], update: { _, peer in peer })
            return specifications.enumerated().map { offset, specification in
                let id = MessageId(peerId: peerId, namespace: specification.namespace, id: Int32(offset + 1))
                let message = localArchiveTestMessage(id, attributes: specification.attributes, media: specification.media).withUpdatedFlags(specification.flags)
                _ = transaction.addMessages([message], location: .Random)
                return transaction.getMessage(id).map { localArchiveInlineHistorySupportsMessage(EngineMessage($0)) } ?? false
            }
        })
        XCTAssertEqual(values, [true, false, false, false, false, false, false])
    }

    func testAgeBoundaryPruneInsideRealMediaDataReadClearsActiveMarker() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "inline-retention")
        defer { try? fixture.destroy() }
        let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9603))
        let id = MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 5)
        let clock = Atomic<Int64>(value: Int64(Date().timeIntervalSince1970) + 10)
        let crossBoundary = Atomic<Bool>(value: false)
        let boundaryReads = Atomic<Int>(value: 0)
        _ = try self.localArchiveValue(fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(owner), localArchiveTestUser(peerId)], update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: owner,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true, maxAgeSeconds: 86400))
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(id, text: "Earlier")], location: .Random)
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(id, text: "Still current",
                attributes: [EditedMessageAttribute(date: 200, isHidden: false)])], location: .Random)
        })
        let current = try XCTUnwrap(self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id).map(EngineMessage.init) }))
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("inline-retention-" + UUID().uuidString)
        let opened = Atomic<ArchiveStore?>(value: nil)
        let archive = LocalArchiveCoordinator(postbox: fixture.postbox, accountPeerId: owner, accountId: 97, baseDirectory: directory,
            storageFactory: { directory, accountId, retention, createIfMissing, callbackQueue, completion in
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: InlineHistoryTestKey(),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: callbackQueue, clock: {
                        let base = clock.with { $0 }
                        guard crossBoundary.with({ $0 }) else { return base }
                        // Coordinator pre-read setRetention sees the old time;
                        // the media read's own clock crosses the expiry edge.
                        return base + (boundaryReads.modify { $0 + 1 } >= 2 ? 2 * 86400 : 0)
                    }) { result in
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
                let closed = self.expectation(description: "close retention archive")
                store.close { closed.fulfill() }
                self.wait(for: [closed], timeout: 15)
            }
            try? FileManager.default.removeItem(at: directory)
        }
        _ = try self.localArchiveValue(archive.status |> filter { $0 == .ready })
        let shown = self.expectation(description: "real retained snapshot found")
        let pruned = self.expectation(description: "real read-triggered pruning invalidates positive marker")
        let didShow = Atomic<Bool>(value: false)
        var loader: LocalArchiveInlineHistoryLoader!
        self.onMain {
            loader = LocalArchiveInlineHistoryLoader(archive: archive, presentation: LocalArchivePresentationContext(postbox: fixture.postbox), message: current, changed: { label in
                if label == "Edited", !didShow.swap(true) { shown.fulfill() }
                else if label == nil, didShow.with({ $0 }) { pruned.fulfill() }
            })
            loader.setActive(true)
        }
        defer { self.onMain { loader.dispose(); loader = nil } }
        self.wait(for: [shown], timeout: 15)
        let snapshot = try XCTUnwrap(self.localArchiveValue(archive.timeline(peerId: peerId.toInt64(), namespace: id.namespace, messageId: id.id, limit: 1)).records.first)
        let revision = try self.localArchiveValue(archive.historyRevision)
        _ = crossBoundary.swap(true)
        XCTAssertNil(try self.localArchiveValue(archive.mediaData(eventKey: snapshot.eventKey, slot: 0)))
        let usage = try XCTUnwrap(self.localArchiveValue(archive.usage()))
        XCTAssertEqual(usage.snapshots, 0)
        self.wait(for: [pruned], timeout: 15)
        XCTAssertGreaterThan(try self.localArchiveValue(archive.historyRevision), revision)
        XCTAssertGreaterThanOrEqual(boundaryReads.with { $0 }, 2)
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id)?.text }), "Still current")
    }
}
