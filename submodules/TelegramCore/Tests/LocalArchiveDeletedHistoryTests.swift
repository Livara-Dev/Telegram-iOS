// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import LocalMessageArchive
@testable import TelegramCore

private final class DeletedHistoryHarness {
    let peer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9811))
    let status = ValuePromise<LocalArchiveStatus>(.ready, ignoreRepeated: true)
    let policy = ValuePromise<LocalArchiveSettings>(LocalArchiveSettings(), ignoreRepeated: true)
    let markers = ValuePromise<LocalArchivePresentationSettingsState>(.available(LocalArchivePresentationSettings()), ignoreRepeated: true)
    let revision = ValuePromise<Int64>(0, ignoreRepeated: true)
    var acquired = 0, released = 0, queries = 0
    var rows: [LocalArchiveDeletedHistoryItem] = []
    var changed: (([LocalArchiveDeletedHistoryItem]) -> Void)?
    var queried: (() -> Void)?
    var delayed: (() -> Signal<LocalArchiveDeletedHistoryResult, NoError>)?
    var empty = false
    var lastExcluded: Set<Int64> = []
    var now: Int64 = 1000
    var scheduledSeconds: Double?
    var scheduledAction: (() -> Void)?
    var scheduleId: Int64 = 0
    var cancelledTimers = 0
    lazy var loader = LocalArchiveDeletedHistoryLoader(status: self.status.get(), policy: self.policy.get(), markers: self.markers.get(),
        revision: self.revision.get(), acquire: { [weak self] in
            self?.acquired += 1
            return ActionDisposable { [weak self] in self?.released += 1 }
        }, lookup: { [weak self] window, excluded in
            guard let self else { return .single(LocalArchiveDeletedHistoryResult(records: [], isTruncated: false, unavailable: true)) }
            self.queries += 1; self.lastExcluded = excluded; self.queried?()
            return self.delayed?() ?? .single(LocalArchiveDeletedHistoryResult(records: self.empty ? [] : [self.record(peer: window.peerId)], isTruncated: false, unavailable: false))
        }, debounceSeconds: 0.03, clock: { [weak self] in self?.now ?? -1 }, schedule: { [weak self] seconds, callback in
            guard let self else { return EmptyDisposable }
            self.scheduleId += 1
            let id = self.scheduleId
            self.scheduledSeconds = seconds; self.scheduledAction = callback
            return ActionDisposable { [weak self] in
                guard let self else { return }
                self.cancelledTimers += 1
                if self.scheduleId == id { self.scheduledAction = nil }
            }
        }, changed: { [weak self] rows in
            self?.rows = rows; self?.changed?(rows)
        })

    func record(peer: PeerId? = nil, namespace: Int32 = 0, message: Int32 = 2, author: Int64? = 7,
                sent: Int64 = 100, deleted: Bool = true) -> LocalArchivedMessage {
        return LocalArchivedMessage(ArchivedRecord(sequence: 1, event: ArchiveEvent(eventKey: "deleted-history-fixture",
            message: ArchiveMessageID(accountId: 1, peerId: (peer ?? self.peer).toInt64(), namespace: namespace, messageId: message),
            kind: deleted ? .deleted : .edited, observedAt: 900,
            content: ArchiveContent(text: "protected deleted body", authorId: author, sentAt: sent))))
    }

    func window(peer: PeerId? = nil, bot: Bool = false) -> LocalArchiveDeletedHistoryWindow {
        return LocalArchiveDeletedHistoryWindow(peer: EnginePeer(localArchiveTestUser(peer ?? self.peer, bot: bot)), messageIds: 1...100,
            sentAt: 0...1000)!
    }
}

final class LocalArchiveDeletedHistoryTests: XCTestCase {
    private func onMain(_ f: () -> Void) { if Thread.isMainThread { f() } else { DispatchQueue.main.sync(execute: f) } }

    private func awaitRows(_ count: Int, harness: DeletedHistoryHarness, action: () -> Void) {
        let done = self.expectation(description: "deleted history row count \(count)")
        self.onMain { harness.changed = { if $0.count == count { done.fulfill() } }; action() }
        self.wait(for: [done], timeout: 10)
        self.onMain { harness.changed = nil; XCTAssertEqual(harness.rows.count, count) }
    }

    func testInactiveOrUnknownWindowNeverAcquiresStoreOrQueries() {
        let h = DeletedHistoryHarness()
        self.onMain {
            h.loader.setWindow(h.window()); h.loader.setActive(false)
            XCTAssertEqual(h.acquired, 0); XCTAssertEqual(h.queries, 0)
            h.loader.setWindow(nil); h.loader.setActive(true)
            XCTAssertEqual(h.acquired, 0); XCTAssertEqual(h.queries, 0)
            h.loader.dispose()
        }
    }

    func testSecretNamespacesHaveWindowsWhileForumAndCopyProtectedPeersRemainExcluded() {
        let id = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(9813))
        let channel = TelegramChannel(id: id, accessHash: .personal(456), title: "Deleted row eligibility", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: .member, info: .group(TelegramChannelGroupInfo(flags: [])),
            flags: [], restrictionInfo: nil, adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil, emojiStatus: nil,
            approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil, sendPaidMessageStars: nil, linkedMonoforumId: nil)
        XCTAssertNotNil(LocalArchiveDeletedHistoryWindow(peer: .channel(channel), messageIds: 1...100, sentAt: 0...1000))
        for flags in [TelegramChannelFlags.isForum, .isMonoforum, .copyProtectionEnabled] {
            XCTAssertNil(LocalArchiveDeletedHistoryWindow(peer: .channel(channel.withUpdatedFlags(flags)), messageIds: 1...100, sentAt: 0...1000))
        }
        let secret = TelegramSecretChat(id: PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(9814)),
            creationDate: 0, regularPeerId: PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9811)), accessHash: 0,
            role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)
        let window = LocalArchiveDeletedHistoryWindow(peer: .secretChat(secret), messageIds: 0...100, sentAt: 0...1000)
        XCTAssertNotNil(window)
        XCTAssertEqual(window?.namespaces, [Namespaces.Message.SecretIncoming, Namespaces.Message.Local])
        XCTAssertNil(LocalArchiveDeletedHistoryWindow(peer: .channel(channel), messageIds: 0...100, sentAt: 0...1000))
        XCTAssertNil(LocalArchiveDeletedHistoryWindow(peer: .channel(channel), messageIds: 1...100, sentAt: -1...1000))
    }

    func testVisibleOrdinaryChatGetsReadLeaseAndRevokesRetainedBodyOnExit() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) { h.loader.setWindow(h.window()); h.loader.setActive(true) }
        var retained: LocalArchiveDeletedHistoryItem!
        self.onMain { retained = h.rows[0]; XCTAssertEqual(h.acquired, 1); XCTAssertEqual(retained.content.text, "protected deleted body") }
        self.awaitRows(0, harness: h) { h.loader.setActive(false) }
        self.onMain { XCTAssertNil(retained.content.text); XCTAssertEqual(h.released, 1); h.loader.dispose() }
    }

    func testLockRevokesAllOldCopiesSynchronouslyAndLatePageCannotRestoreThem() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) { h.loader.setWindow(h.window()); h.loader.setActive(true) }
        var retained: LocalArchiveDeletedHistoryItem!
        self.onMain { retained = h.rows[0] }
        self.awaitRows(0, harness: h) { h.status.set(.locked) }
        self.onMain { XCTAssertNil(retained.content.text); h.loader.dispose() }
    }

    func testAlreadyRunningPageCannotPublishAfterAccountWindowSwitchOrBackground() {
        let h = DeletedHistoryHarness(), pipe = ValuePipe<LocalArchiveDeletedHistoryResult>()
        let began = self.expectation(description: "actual lookup started")
        let unexpected = self.expectation(description: "late lookup cannot expose body"); unexpected.isInverted = true
        self.onMain {
            h.delayed = { pipe.signal() }; h.queried = { began.fulfill() }
            h.changed = { if !$0.isEmpty { unexpected.fulfill() } }
            h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        self.wait(for: [began], timeout: 10)
        self.onMain {
            h.loader.setActive(false)
            h.loader.setWindow(h.window(peer: PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9812))))
            pipe.putNext(LocalArchiveDeletedHistoryResult(records: [h.record()], isTruncated: false, unavailable: false))
        }
        self.wait(for: [unexpected], timeout: 0.12)
        self.onMain { XCTAssertTrue(h.rows.isEmpty); h.loader.dispose(); XCTAssertEqual(h.acquired, h.released) }
    }

    func testCommittedPruningRevisionRevokesPreviousBodyBeforeReload() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) { h.loader.setWindow(h.window()); h.loader.setActive(true) }
        var retained: LocalArchiveDeletedHistoryItem!
        self.onMain { retained = h.rows[0]; h.empty = true }
        self.awaitRows(0, harness: h) { h.revision.set(1) }
        self.onMain { XCTAssertNil(retained.content.text); h.loader.dispose() }
    }

    func testPausedArchiveCanBrowseWithoutEnablingCaptureAndMarkerIsAccountLocal() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) {
            h.status.set(.browsing)
            h.markers.set(.available(LocalArchivePresentationSettings(deletedMarker: "Saved deletion")))
            h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        self.onMain { XCTAssertEqual(h.rows.first?.marker, "Saved deletion"); h.loader.dispose() }
    }

    func testExcludedChatAndDefaultExcludedBotNeverQueryBodies() {
        for bot in [false, true] {
            let h = DeletedHistoryHarness()
            let unexpected = self.expectation(description: "excluded chat cannot read bodies"); unexpected.isInverted = true
            self.onMain {
                if !bot { h.policy.set(LocalArchiveSettings(excludedPeerIds: [h.peer.toInt64()])) }
                h.queried = { unexpected.fulfill() }
                h.loader.setWindow(h.window(bot: bot)); h.loader.setActive(true)
            }
            self.wait(for: [unexpected], timeout: 0.12)
            self.onMain { XCTAssertTrue(h.rows.isEmpty); h.loader.dispose() }
        }
    }

    func testUnsupportedPreferencesAndUnsafeDeletedMarkerFailClosed() {
        for state in [LocalArchivePresentationSettingsState.unsupported,
                      .available(LocalArchivePresentationSettings(deletedMarker: "Misleading\u{202e}label"))] {
            let h = DeletedHistoryHarness()
            let unexpected = self.expectation(description: "untrusted markers cannot cause plaintext reads"); unexpected.isInverted = true
            self.onMain {
                h.markers.set(state); h.queried = { unexpected.fulfill() }
                h.loader.setWindow(h.window()); h.loader.setActive(true)
            }
            self.wait(for: [unexpected], timeout: 0.12)
            self.onMain { XCTAssertTrue(h.rows.isEmpty); h.loader.dispose() }
        }
    }

    func testExcludedAuthorIdsArePassedToStorageAndUnknownAuthorsFailClosed() {
        let h = DeletedHistoryHarness()
        let read = self.expectation(description: "bounded author-filtered query")
        self.onMain {
            h.policy.set(LocalArchiveSettings(excludedSenderIds: [7]))
            h.queried = { read.fulfill() }
            h.delayed = { .single(LocalArchiveDeletedHistoryResult(records: [h.record(author: 7), h.record(message: 3, author: nil)], isTruncated: false, unavailable: false)) }
            h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        self.wait(for: [read], timeout: 10)
        self.onMain { XCTAssertEqual(h.lastExcluded, [7]); XCTAssertTrue(h.rows.isEmpty); h.loader.dispose() }
    }

    func testWrongPeerNamespaceOutOfWindowEditsAndOversizedDatesNeverBecomeRows() {
        let h = DeletedHistoryHarness()
        let read = self.expectation(description: "malformed page checked")
        self.onMain {
            h.queried = { read.fulfill() }
            h.delayed = { .single(LocalArchiveDeletedHistoryResult(records: [
                h.record(peer: PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9999))),
                h.record(namespace: 1), h.record(message: 101), h.record(sent: -1), h.record(sent: Int64(Int32.max) + 1), h.record(deleted: false)
            ], isTruncated: false, unavailable: false)) }
            h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        self.wait(for: [read], timeout: 10)
        self.onMain { XCTAssertTrue(h.rows.isEmpty); h.loader.dispose() }
    }

    func testDisposalCancelsDelayedQueriesAndBalancesLeaseOnce() {
        let h = DeletedHistoryHarness()
        let unexpected = self.expectation(description: "disposed chat cannot query"); unexpected.isInverted = true
        self.onMain {
            h.queried = { unexpected.fulfill() }; h.loader.setWindow(h.window()); h.loader.setActive(true)
            h.loader.dispose(); h.loader.dispose(); h.loader.setActive(true)
        }
        self.wait(for: [unexpected], timeout: 0.12)
        self.onMain { XCTAssertEqual(h.acquired, 1); XCTAssertEqual(h.released, 1) }
    }

    func testRevocationCallbackClearsEveryRegisteredRendererAndCannotFireTwice() {
        self.onMain {
            let body = LocalArchiveDeletedHistoryContent(text: "body")
            var callbacks = 0
            let first = body.onRevoked { callbacks += 1 }, second = body.onRevoked { callbacks += 1 }
            body.revoke(); body.revoke()
            XCTAssertNil(body.text); XCTAssertEqual(callbacks, 2)
            let late = body.onRevoked { callbacks += 1 }
            XCTAssertEqual(callbacks, 3)
            first.dispose(); second.dispose(); late.dispose()
        }
    }

    func testFiniteRetentionExpiresVisiblePlaintextWithoutAnotherDatabaseWrite() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) {
            h.policy.set(LocalArchiveSettings(maxAgeSeconds: 200))
            h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        var retained: LocalArchiveDeletedHistoryItem!
        self.onMain { retained = h.rows[0]; XCTAssertEqual(h.scheduledSeconds, 101) }
        self.awaitRows(0, harness: h) {
            h.now = 1101
            let fire = h.scheduledAction
            fire?()
        }
        self.onMain { XCTAssertNil(retained.content.text); h.loader.dispose(); XCTAssertGreaterThan(h.cancelledTimers, 0) }
    }

    func testPermanentRetentionHasNoTimerAndVisibilityCancelsFiniteTimer() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) { h.loader.setWindow(h.window()); h.loader.setActive(true) }
        self.onMain { XCTAssertNil(h.scheduledSeconds); h.loader.dispose() }
        let finite = DeletedHistoryHarness()
        self.awaitRows(1, harness: finite) {
            finite.policy.set(LocalArchiveSettings(maxAgeSeconds: 200)); finite.loader.setWindow(finite.window()); finite.loader.setActive(true)
        }
        self.onMain { finite.loader.setActive(false); XCTAssertNil(finite.scheduledAction); XCTAssertEqual(finite.cancelledTimers, 1); finite.loader.dispose() }
    }

    func testBackwardsClockAtRetentionBoundaryRevokesInsteadOfExtendingVisibleBody() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) {
            h.policy.set(LocalArchiveSettings(maxAgeSeconds: 200)); h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        var retained: LocalArchiveDeletedHistoryItem!
        self.onMain { retained = h.rows[0] }
        self.awaitRows(0, harness: h) { h.now = 999; let fire = h.scheduledAction; fire?() }
        self.onMain { XCTAssertNil(retained.content.text); XCTAssertNil(h.scheduledAction); h.loader.dispose() }
    }

    func testHugeFiniteRetentionCannotOverflowDispatchNanoseconds() {
        let h = DeletedHistoryHarness()
        self.awaitRows(1, harness: h) {
            h.policy.set(LocalArchiveSettings(maxAgeSeconds: Int64.max - 1000)); h.loader.setWindow(h.window()); h.loader.setActive(true)
        }
        self.onMain {
            XCTAssertEqual(h.scheduledSeconds, 365.0 * 24.0 * 60.0 * 60.0)
            h.loader.dispose()
        }
    }
}

private struct DeletedHistoryIntegrationKeys: ArchiveKeyProvider {
    let requests: Atomic<[Bool]>
    func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
        _ = self.requests.modify { $0 + [createIfMissing] }
        return Data(repeating: 0x6d, count: 32)
    }
}

/// Real Postbox mutation -> durable capture outbox -> SQLCipher -> the exact
/// loader used by the normal chat. These fixtures contain no live credentials
/// or RPC delivery, and do not constitute an on-device or logged-in UI test.
final class LocalArchiveDeletedHistoryIntegrationTests: XCTestCase {
    private func onMain(_ f: () -> Void) { if Thread.isMainThread { f() } else { DispatchQueue.main.sync(execute: f) } }

    func testAutomaticDeleteAndEditCaptureReachRowsAfterCacheCleanupAndRestart() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "deleted-row-integration")
        defer { try? fixture.destroy() }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("deleted-row-store-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let peer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9811))
        let textId = MessageId(peerId: peer, namespace: Namespaces.Message.Cloud, id: 1)
        let photoId = MessageId(peerId: peer, namespace: Namespaces.Message.Cloud, id: 2)
        let timedId = MessageId(peerId: peer, namespace: Namespaces.Message.Cloud, id: 3)
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9811, accessHash: 0, sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        let photo = TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9811),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16), resource: resource,
                progressiveSizes: [], immediateThumbnailData: nil)], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        var postbox: Postbox? = fixture.postbox
        postbox!.mediaBox.storeResourceData(resource.id, data: bytes, synchronous: true)
        _ = try self.localArchiveValue(postbox!.transaction { [postbox = postbox!] transaction in
            transaction.updatePeersInternal([localArchiveTestUser(owner), localArchiveTestUser(peer)], update: { _, peer in peer })
            transaction.setState(AuthorizedAccountState(isTestingEnvironment: true, masterDatacenterId: 1, peerId: owner,
                state: AuthorizedAccountState.State(pts: 10, qts: 2, date: 100, seq: 3), invalidatedChannels: []))
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: true))
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(textId, text: "original body"),
                localArchiveTestMessage(photoId, text: "photo caption", media: [photo]),
                localArchiveTestMessage(timedId, text: "must not retain timed content", attributes: [AutoclearTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)])], location: .Random)
            _ = addMessagesWithLocalArchive(transaction: transaction, messages: [localArchiveTestMessage(textId, text: "edited body",
                attributes: [EditedMessageAttribute(date: 200, isHidden: false)])], location: .Random)
            deleteMessagesInteractively(transaction: transaction, stateManager: nil, postbox: postbox,
                messageIds: [textId, photoId, timedId], type: .forEveryone, deleteAllInGroup: false, removeIfPossiblyDelivered: false)
        })
        XCTAssertNil(try self.localArchiveValue(postbox!.transaction { $0.getMessage(textId) }))
        let keys = Atomic<[Bool]>(value: [])
        let openRequests = Atomic<[Bool]>(value: [])
        let opened = Atomic<ArchiveStore?>(value: nil)
        func start(_ postbox: Postbox) -> LocalArchiveCoordinator {
            return LocalArchiveCoordinator(postbox: postbox, accountPeerId: owner, accountId: 98, baseDirectory: directory,
                storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                    _ = openRequests.modify { $0 + [createIfMissing] }
                    ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: DeletedHistoryIntegrationKeys(requests: keys),
                        createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
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
                let done = self.expectation(description: "close deleted row archive")
                store.close { done.fulfill() }
                self.wait(for: [done], timeout: 15)
            }
        }
        var first: LocalArchiveCoordinator? = start(postbox!)
        defer { if let first { try? stop(first) } }
        _ = try self.localArchiveValue(first!.status |> filter { $0 == .ready })
        let window = try XCTUnwrap(LocalArchiveDeletedHistoryWindow(peer: EnginePeer(localArchiveTestUser(peer)), messageIds: 1...100, sentAt: 0...1000))
        let initial = try self.localArchiveValue(first!.deletedHistory(window: window, excludingAuthors: []))
        XCTAssertEqual(Set(initial.records.map(\.messageId)), [1, 2])
        XCTAssertFalse(initial.records.contains { $0.messageId == timedId.id })
        XCTAssertEqual(initial.records.first(where: { $0.messageId == photoId.id })?.storedMediaCount, 1)
        XCTAssertEqual(try self.localArchiveValue(first!.timeline(peerId: peer.toInt64(), namespace: 0, messageId: 1)).records.map(\.text), ["original body", "edited body"])
        let mediaKey = try XCTUnwrap(initial.records.first(where: { $0.messageId == photoId.id })?.eventKey)
        let cacheRemoved = self.expectation(description: "actual Telegram cache endpoint")
        let removal = postbox!.mediaBox.removeCachedResources([resource.id], force: true).start(completed: { cacheRemoved.fulfill() })
        self.wait(for: [cacheRemoved], timeout: 15); removal.dispose()
        XCTAssertFalse(FileManager.default.fileExists(atPath: postbox!.mediaBox.storePathsForId(resource.id).complete))
        XCTAssertEqual(try self.localArchiveValue(first!.mediaData(eventKey: mediaKey, slot: 0)), bytes)
        XCTAssertTrue(try self.localArchiveValue(first!.setEnabled(false)))
        try stop(first!); first = nil
        postbox = nil
        try fixture.reopen()
        let reopened = start(fixture.postbox)
        defer { try? stop(reopened) }
        let shown = self.expectation(description: "paused reopened archive supplies real inline rows")
        var retainedRows: [LocalArchiveDeletedHistoryItem] = []
        var loader: LocalArchiveDeletedHistoryLoader!
        self.onMain {
            loader = LocalArchiveDeletedHistoryLoader(archive: reopened, presentation: LocalArchivePresentationContext(postbox: fixture.postbox), changed: { rows in
                if rows.count == 2 { retainedRows = rows; shown.fulfill() }
            })
            loader.setWindow(window); loader.setActive(true)
        }
        defer { self.onMain { loader.dispose(); loader = nil } }
        self.wait(for: [shown], timeout: 15)
        self.onMain { XCTAssertEqual(Set(retainedRows.compactMap { $0.content.text }), ["edited body", "photo caption"]) }
        XCTAssertEqual(try self.localArchiveValue(reopened.mediaData(eventKey: mediaKey, slot: 0)), bytes)
        XCTAssertFalse(try self.localArchiveValue(reopened.settings).enabled)
        XCTAssertTrue(openRequests.with { $0 }.contains(false), "visible read must reopen existing store without create authority")
        XCTAssertEqual(keys.with { $0 }.filter { $0 }.count, 1, "one initial encryption key creation, then existing-key reads only")
        self.onMain { loader.setActive(false); XCTAssertTrue(retainedRows.allSatisfy { $0.content.text == nil }) }
    }

    func testViewingAnAbsentArchiveNeverCreatesDatabaseOrKey() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "deleted-row-absent")
        defer { try? fixture.destroy() }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("absent-deleted-row-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let owner = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let peer = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9811))
        let keys = Atomic<[Bool]>(value: [])
        let openRequests = Atomic<[Bool]>(value: [])
        let attempted = ValuePromise<Bool>(false, ignoreRepeated: true)
        let pendingProbes = DispatchGroup()
        let handoffQueue = Atomic<DispatchQueue?>(value: nil)
        let archive = LocalArchiveCoordinator(postbox: fixture.postbox, accountPeerId: owner, accountId: 99, baseDirectory: directory,
            storageFactory: { directory, accountId, retention, createIfMissing, queue, completion in
                _ = handoffQueue.swap(queue)
                pendingProbes.enter()
                _ = openRequests.modify { $0 + [createIfMissing] }
                XCTAssertFalse(createIfMissing)
                ArchiveStore.open(baseDirectory: directory, accountId: accountId, keyProvider: DeletedHistoryIntegrationKeys(requests: keys),
                    createIfMissing: createIfMissing, retention: retention, callbackQueue: queue) { result in
                    if case .failure(.missingArchive) = result {} else {
                        XCTFail("A read-only probe of an absent archive must report missingArchive")
                    }
                    completion(result.map { $0 as LocalArchiveStorage })
                    attempted.set(true)
                    pendingProbes.leave()
                }
            }, protectedData: .single(true))
        var stopped = false
        func stop() throws {
            guard !stopped else { return }
            archive.dispose()
            // This is the coordinator's own serial callback queue, supplied by
            // openStorage. An acknowledgement queued after dispose cannot run
            // before its stopped flag is set; a cached status is not a barrier.
            let queue = try XCTUnwrap(handoffQueue.with { $0 })
            let stopAcknowledged = self.expectation(description: "archive coordinator stopped")
            queue.async { stopAcknowledged.fulfill() }
            self.wait(for: [stopAcknowledged], timeout: 15)
            XCTAssertEqual(try self.localArchiveValue(archive.status), .unavailable)
            let drained = self.expectation(description: "all read-only existing-store probes finished")
            pendingProbes.notify(queue: .main) { drained.fulfill() }
            self.wait(for: [drained], timeout: 15)
            stopped = true
        }
        defer { try? stop() }
        var loader: LocalArchiveDeletedHistoryLoader!
        self.onMain {
            loader = LocalArchiveDeletedHistoryLoader(archive: archive, presentation: LocalArchivePresentationContext(postbox: fixture.postbox),
                changed: { XCTAssertTrue($0.isEmpty) })
            loader.setWindow(LocalArchiveDeletedHistoryWindow(peer: EnginePeer(localArchiveTestUser(peer)), messageIds: 1...100, sentAt: 0...1000))
            loader.setActive(true)
        }
        defer { self.onMain { loader.dispose(); loader = nil } }
        // Protected-data, settings and operation-log wakeups may each probe the
        // missing store. Wait once, then stop and drain every probe before
        // checking that none obtained key or database creation authority.
        XCTAssertTrue(try self.localArchiveValue(attempted.get() |> filter { $0 }))
        try stop()
        XCTAssertFalse(openRequests.with { $0 }.isEmpty)
        XCTAssertTrue(openRequests.with { $0 }.allSatisfy { !$0 })
        XCTAssertTrue(keys.with { $0 }.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
        XCTAssertFalse(try self.localArchiveValue(archive.settings).enabled)
    }
}
