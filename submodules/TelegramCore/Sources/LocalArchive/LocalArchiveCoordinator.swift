// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit
import LocalMessageArchive

/// Status contains no message contents, peer identifiers, paths or underlying errors.
public enum LocalArchiveStatus: Equatable {
    case disabled
    case locked
    case opening
    case ready
    case writing
    case unavailable
    case invalidPendingCapture
    case purging
}

public struct LocalArchivedMessage {
    public let sequence: Int64
    public let eventKey: String
    public let peerId: Int64
    public let namespace: Int32
    public let messageId: Int32
    public let text: String
    public let authorId: Int64?
    public let sentAt: Int64
    public let observedAt: Int64
    public let serverEditAt: Int64?
    public let isDeleted: Bool
    public let storedMediaCount: Int
    public let mediaKinds: [String]
    public let unavailableMediaCount: Int

    init(_ record: ArchivedRecord) {
        self.sequence = record.sequence
        self.eventKey = record.event.eventKey
        self.peerId = record.event.message.peerId
        self.namespace = record.event.message.namespace
        self.messageId = record.event.message.messageId
        self.text = record.event.content.text
        self.authorId = record.event.content.authorId
        self.sentAt = record.event.content.sentAt
        self.observedAt = record.event.observedAt
        self.serverEditAt = record.event.serverEditAt
        self.isDeleted = record.event.kind == .deleted
        self.storedMediaCount = record.storedMediaCount
        self.mediaKinds = (localArchiveDecodedMediaMetadata(record.event.content.entities) ?? []).map { $0.kind.rawValue }
        self.unavailableMediaCount = max(0, (localArchiveDecodedMediaMetadata(record.event.content.entities)?.count ?? 0) - record.storedMediaCount)
    }
}

public struct LocalArchiveUsage {
    public let snapshots: Int64
    public let payloadBytes: Int64
}

public struct LocalArchivePage {
    public let records: [LocalArchivedMessage]
    public let unavailable: Bool
}

// Injection is internal: production always uses the independently keyed SQLCipher store.
protocol LocalArchiveStorage: AnyObject {
    func recordFromOutbox(_ capture: ArchiveCapture, index: Int64, generation: Int64, policy: ArchiveCapturePolicy,
                          completion: @escaping (Result<ArchiveWriteResult, ArchiveError>) -> Void)
    func recordMedia(eventKey: String, slot: Int, kind: String, mimeType: String, data: Data, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void)
    func recordMediaFile(eventKey: String, slot: Int, kind: String, mimeType: String, source: URL, expectedSize: Int64?, generation: Int64, completion: @escaping (Result<Bool, ArchiveError>) -> Void)
    func mediaInfo(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMediaInfo?, ArchiveError>) -> Void)
    func mediaRange(eventKey: String, slot: Int, offset: Int64, count: Int, completion: @escaping (Result<Data?, ArchiveError>) -> Void)
    func containsMedia(eventKey: String, slot: Int, completion: @escaping (Result<Bool, ArchiveError>) -> Void)
    func media(eventKey: String, slot: Int, completion: @escaping (Result<ArchivedMedia?, ArchiveError>) -> Void)
    func currentGeneration(completion: @escaping (Result<Int64, ArchiveError>) -> Void)
    func purge(advancingToGeneration: Int64, completion: @escaping (Result<ArchivePurgeResult, ArchiveError>) -> Void)
    func setRetention(_ policy: ArchiveRetentionPolicy, completion: @escaping (Result<Int, ArchiveError>) -> Void)
    func close(completion: @escaping () -> Void)
    func deletedMessages(peerId: Int64?, beforeSequence: Int64?, limit: Int,
                         completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void)
    func timeline(for message: ArchiveMessageID, beforeSequence: Int64?, limit: Int,
                  completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void)
    func searchMessages(query: String, peerId: Int64?, kind: ArchiveEventKind?, beforeSequence: Int64?, limit: Int, completion: @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void)
    func usage(completion: @escaping (Result<ArchiveUsage, ArchiveError>) -> Void)
}

extension ArchiveStore: LocalArchiveStorage {}

typealias LocalArchiveStorageFactory = (URL, Int64, ArchiveRetentionPolicy, DispatchQueue,
                                       @escaping (Result<LocalArchiveStorage, ArchiveError>) -> Void) -> Void

/// Account-owned, opt-in handoff. Operation-log views are wakeups, never commit acknowledgements.
/// All archive work starts from a fresh Postbox transaction's emitted result on `queue`.
/// Postbox's release-build disk-full commit behavior remains a separately documented limitation.
public final class LocalArchiveCoordinator {
    private let postbox: Postbox
    private let accountPeerId: PeerId
    private let accountId: Int64
    private let baseDirectory: URL
    private let queue = Queue(name: "LocalArchive.handoff", qos: .utility)
    private let storageFactory: LocalArchiveStorageFactory
    private let subscriptions = DisposableSet()
    private let transactionDisposable = MetaDisposable()
    private let statusValue = ValuePromise<LocalArchiveStatus>(.disabled, ignoreRepeated: true)
    private var storage: LocalArchiveStorage?
    private var stopped = false
    // True permits a fresh Keychain probe, not an assertion that the device is unlocked.
    private var mayAccessProtectedData = false
    private var working = false
    private var needsDrain = false
    private var retryScheduled = false
    private var epoch: Int64 = 0
    private var activeSettings = LocalArchiveSettings()

    public var status: Signal<LocalArchiveStatus, NoError> {
        return self.statusValue.get()
    }

    public var settings: Signal<LocalArchiveSettings, NoError> {
        return self.postbox.preferencesView(keys: [localArchiveSettingsKey])
        |> map { view in
            localArchiveSettings(entry: view.values[localArchiveSettingsKey])
        }
        |> distinctUntilChanged
    }

    init(postbox: Postbox, accountPeerId: PeerId, accountId: Int64, baseDirectory: URL,
         storageFactory: LocalArchiveStorageFactory? = nil, protectedData: Signal<Bool, NoError>? = nil) {
        self.postbox = postbox
        self.accountPeerId = accountPeerId
        self.accountId = accountId
        self.baseDirectory = baseDirectory
        self.storageFactory = storageFactory ?? { directory, id, retention, callbackQueue, completion in
            ArchiveStore.open(baseDirectory: directory, accountId: id, keyProvider: KeychainArchiveKeyStore(),
                              retention: retention, callbackQueue: callbackQueue) { result in
                completion(result.map { $0 as LocalArchiveStorage })
            }
        }

        self.subscriptions.add(self.transactionDisposable)
        self.subscriptions.add((protectedData ?? localArchiveProtectedDataSignal()).start(next: { [weak self] available in
            guard let self else { return }
            self.queue.justDispatch { [weak self] in
                guard let self, !self.stopped else { return }
                self.mayAccessProtectedData = available
                if available {
                    self.wake()
                } else {
                    self.invalidateWork()
                    self.statusValue.set(.locked)
                }
            }
        }))
        self.subscriptions.add(self.settings.start(next: { [weak self] _ in self?.wake() }))
        self.subscriptions.add(postbox.mergedOperationLogView(tag: OperationLogTags.LocalArchiveCapture, limit: 1)
            .start(next: { [weak self] _ in self?.wake() }))
    }

    deinit {
        self.subscriptions.dispose()
        self.storage?.close(completion: {})
    }

    public func dispose() {
        self.subscriptions.dispose()
        self.queue.justDispatch { [weak self] in
            guard let self else { return }
            self.stopped = true
            self.invalidateWork()
            self.statusValue.set(.unavailable)
        }
    }

    public func setEnabled(_ enabled: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported, !settings.pendingPurge else { return false }
            settings.enabled = enabled
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    /// Limits are for logical archived payload bytes, not total SQLite/WAL allocation.
    public func setRetention(maxAgeSeconds: Int64, maxPayloadBytes: Int64) -> Signal<Bool, NoError> {
        guard maxAgeSeconds >= 86400, (maxAgeSeconds <= 365 * 86400 || maxAgeSeconds == Int64.max),
              maxPayloadBytes >= 1024 * 1024, maxPayloadBytes <= 1024 * 1024 * 1024 else {
            return .single(false)
        }
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported, !settings.pendingPurge else { return false }
            settings.maxAgeSeconds = maxAgeSeconds
            settings.maxPayloadBytes = maxPayloadBytes
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    public func setExcludeBots(_ excluded: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported, !settings.pendingPurge else { return false }
            settings.excludeBots = excluded
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    public func setPeerExcluded(peerId: Int64, excluded: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported, !settings.pendingPurge else { return false }
            if excluded {
                guard settings.excludedPeerIds.count < 4096 || settings.excludedPeerIds.contains(peerId) else { return false }
                settings.excludedPeerIds.insert(peerId)
            } else {
                settings.excludedPeerIds.remove(peerId)
            }
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    public func setSenderExcluded(senderId: Int64, excluded: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported, !settings.pendingPurge else { return false }
            if excluded {
                guard settings.excludedSenderIds.count < 4096 || settings.excludedSenderIds.contains(senderId) else { return false }
                settings.excludedSenderIds.insert(senderId)
            } else {
                settings.excludedSenderIds.remove(senderId)
            }
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    /// The emitted Bool acknowledges the Postbox request, not completion of erasure.
    /// Observe `.purging` followed by `.ready`/`.disabled`; failures remain retryable.
    public func requestPurge() -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            var settings = localArchiveSettings(transaction: transaction)
            guard settings.isSupported else { return false }
            if settings.pendingPurge { return true }
            guard settings.generation < Int64.max else { return false }
            settings.generation += 1
            settings.pendingPurge = true
            setLocalArchiveSettings(transaction: transaction, settings: settings)
            return true
        }
    }

    public func deletedMessages(peerId: Int64? = nil, beforeSequence: Int64? = nil, limit: Int = 100) -> Signal<LocalArchivePage, NoError> {
        return self.readPage { storage, completion in
            storage.deletedMessages(peerId: peerId, beforeSequence: beforeSequence, limit: limit, completion: completion)
        }
    }

    public func searchMessages(query: String, peerId: Int64? = nil, deletedOnly: Bool = false,
                               beforeSequence: Int64? = nil, limit: Int = 100) -> Signal<LocalArchivePage, NoError> {
        return self.readPage { storage, completion in
            storage.searchMessages(query: query, peerId: peerId, kind: deletedOnly ? .deleted : nil,
                                   beforeSequence: beforeSequence, limit: limit, completion: completion)
        }
    }

    public func mediaData(eventKey: String, slot: Int) -> Signal<Data?, NoError> {
        return Signal { [weak self] subscriber in
            guard let self else {
                subscriber.putNext(nil); subscriber.putCompletion()
                return EmptyDisposable
            }
            self.queue.justDispatch { [weak self] in
                guard let self, !self.stopped, self.mayAccessProtectedData, self.activeSettings.enabled,
                      !self.activeSettings.pendingPurge, let storage = self.storage else {
                    subscriber.putNext(nil); subscriber.putCompletion(); return
                }
                let epoch = self.epoch
                storage.media(eventKey: eventKey, slot: slot) { [weak self] result in
                    guard let self else {
                        subscriber.putNext(nil); subscriber.putCompletion()
                        return
                    }
                    self.queue.justDispatch { [weak self] in
                        guard let self, self.isCurrent(epoch) else { subscriber.putNext(nil); subscriber.putCompletion(); return }
                        subscriber.putNext((try? result.get())?.data)
                        subscriber.putCompletion()
                    }
                }
            }
            return EmptyDisposable
        }
    }

    public func mediaInfo(eventKey: String, slot: Int) -> Signal<ArchivedMediaInfo?, NoError> {
        let value: Signal<ArchivedMediaInfo??, NoError> = self.readValue { storage, completion in
            storage.mediaInfo(eventKey: eventKey, slot: slot, completion: completion)
        }
        return value |> map { $0 ?? nil }
    }

    public func mediaRange(eventKey: String, slot: Int, offset: Int64, count: Int) -> Signal<Data?, NoError> {
        let value: Signal<Data??, NoError> = self.readValue { storage, completion in
            storage.mediaRange(eventKey: eventKey, slot: slot, offset: offset, count: count, completion: completion)
        }
        return value |> map { $0 ?? nil }
    }

    public func timeline(peerId: Int64, namespace: Int32, messageId: Int32,
                         beforeSequence: Int64? = nil, limit: Int = 100) -> Signal<LocalArchivePage, NoError> {
        let id = ArchiveMessageID(accountId: self.accountId, peerId: peerId, namespace: namespace, messageId: messageId)
        return self.readPage { storage, completion in
            storage.timeline(for: id, beforeSequence: beforeSequence, limit: limit, completion: completion)
        }
    }

    public func usage() -> Signal<LocalArchiveUsage?, NoError> {
        let result: Signal<ArchiveUsage?, NoError> = self.readValue { storage, completion in
            storage.usage(completion: completion)
        }
        return result |> map { value in
            value.map { LocalArchiveUsage(snapshots: $0.records, payloadBytes: $0.logicalPayloadBytes) }
        }
    }

    private func readPage(_ read: @escaping (LocalArchiveStorage, @escaping (Result<[ArchivedRecord], ArchiveError>) -> Void) -> Void) -> Signal<LocalArchivePage, NoError> {
        return self.readValue(read) |> map { records in
            guard let records else { return LocalArchivePage(records: [], unavailable: true) }
            return LocalArchivePage(records: records.map(LocalArchivedMessage.init), unavailable: false)
        }
    }

    private func readValue<Value>(_ read: @escaping (LocalArchiveStorage, @escaping (Result<Value, ArchiveError>) -> Void) -> Void) -> Signal<Value?, NoError> {
        return Signal { [weak self] subscriber in
            guard let self else {
                subscriber.putNext(nil)
                subscriber.putCompletion()
                return EmptyDisposable
            }
            self.queue.justDispatch { [weak self] in
                guard let self, !self.stopped, self.mayAccessProtectedData, self.activeSettings.enabled,
                      !self.activeSettings.pendingPurge, let storage = self.storage else {
                    subscriber.putNext(nil)
                    subscriber.putCompletion()
                    return
                }
                let epoch = self.epoch
                let receive: (Result<Value, ArchiveError>) -> Void = { [weak self] result in
                    guard let self else {
                        subscriber.putNext(nil)
                        subscriber.putCompletion()
                        return
                    }
                    self.queue.justDispatch { [weak self] in
                        guard let self, self.isCurrent(epoch), self.activeSettings.enabled,
                              !self.activeSettings.pendingPurge else {
                            subscriber.putNext(nil)
                            subscriber.putCompletion()
                            return
                        }
                        switch result {
                        case let .success(value):
                            subscriber.putNext(value)
                        case .failure:
                            subscriber.putNext(nil)
                        }
                        subscriber.putCompletion()
                    }
                }
                // An idle account may receive no writes for longer than its age
                // limit. Prune before presenting data, without a background timer.
                let retention = ArchiveRetentionPolicy(maxAgeSeconds: self.activeSettings.maxAgeSeconds,
                                                       maxPayloadBytes: self.activeSettings.maxPayloadBytes)
                storage.setRetention(retention) { [weak self] result in
                    guard let self else {
                        receive(.failure(.closed))
                        return
                    }
                    self.queue.justDispatch { [weak self] in
                        guard let self, self.isCurrent(epoch), self.activeSettings.enabled,
                              !self.activeSettings.pendingPurge else {
                            receive(.failure(.closed))
                            return
                        }
                        switch result {
                        case .success: read(storage, receive)
                        case let .failure(error): receive(.failure(error))
                        }
                    }
                }
            }
            return EmptyDisposable
        }
    }

    private func wake() {
        self.queue.justDispatch { [weak self] in
            guard let self, !self.stopped else { return }
            self.needsDrain = true
            self.drainIfNeeded()
        }
    }

    private func isCurrent(_ epoch: Int64) -> Bool {
        return !self.stopped && self.mayAccessProtectedData && self.epoch == epoch
    }

    private func invalidateWork() {
        self.epoch &+= 1
        self.working = false
        self.retryScheduled = false
        self.transactionDisposable.set(nil)
        let storage = self.storage
        self.storage = nil
        storage?.close(completion: {})
    }

    private func drainIfNeeded() {
        guard !self.stopped, self.mayAccessProtectedData, !self.working, !self.retryScheduled, self.needsDrain else { return }
        self.needsDrain = false
        self.working = true
        let epoch = self.epoch
        // A view can run before Postbox commits. This independent queue and fresh
        // transaction prevent reading that view's uncommitted payload directly.
        self.transactionDisposable.set((self.postbox.transaction { [accountPeerId = self.accountPeerId] transaction in
            return (localArchiveSettings(transaction: transaction),
                    localArchivePendingCaptures(transaction: transaction, accountPeerId: accountPeerId, limit: 1))
        } |> deliverOn(self.queue)).start(next: { [weak self] settings, pending in
            guard let self, self.isCurrent(epoch) else { return }
            self.activeSettings = settings
            guard settings.isSupported else {
                self.invalidateWork()
                self.needsDrain = false
                self.statusValue.set(.unavailable)
                return
            }
            guard settings.enabled || settings.pendingPurge else {
                self.invalidateWork()
                self.needsDrain = false
                self.statusValue.set(.disabled)
                return
            }
            self.openIfNeeded(settings: settings, epoch: epoch) { [weak self] storage in
                guard let self, self.isCurrent(epoch) else { return }
                if settings.pendingPurge {
                    self.completePurge(storage: storage, settings: settings, epoch: epoch)
                } else {
                    self.writePending(storage: storage, settings: settings, pending: pending, epoch: epoch)
                }
            }
        }))
    }

    private func openIfNeeded(settings: LocalArchiveSettings, epoch: Int64,
                              completion: @escaping (LocalArchiveStorage) -> Void) {
        let retention = ArchiveRetentionPolicy(maxAgeSeconds: settings.maxAgeSeconds, maxPayloadBytes: settings.maxPayloadBytes)
        let applyRetention: (LocalArchiveStorage) -> Void = { [weak self] storage in
            guard let self, self.isCurrent(epoch) else { return }
            storage.setRetention(retention) { [weak self] result in
                self?.queue.justDispatch { [weak self] in
                    guard let self, self.isCurrent(epoch) else { return }
                    switch result {
                    case .success: completion(storage)
                    case .failure: self.failed(.unavailable, epoch: epoch)
                    }
                }
            }
        }
        if let storage = self.storage {
            applyRetention(storage)
            return
        }
        self.statusValue.set(.opening)
        self.storageFactory(self.baseDirectory, self.accountId, retention, self.queue.queue) { [weak self] result in
            guard let self else {
                if case let .success(storage) = result { storage.close(completion: {}) }
                return
            }
            self.queue.justDispatch { [weak self] in
                guard let self, self.isCurrent(epoch) else {
                    if case let .success(storage) = result { storage.close(completion: {}) }
                    return
                }
                switch result {
                case let .success(storage):
                    self.storage = storage
                    applyRetention(storage)
                case .failure:
                    self.failed(.unavailable, epoch: epoch)
                }
            }
        }
    }

    private func writePending(storage: LocalArchiveStorage, settings: LocalArchiveSettings,
                              pending: [LocalArchivePendingCapture], epoch: Int64) {
        storage.currentGeneration { [weak self] result in
            self?.queue.justDispatch { [weak self] in
                guard let self, self.isCurrent(epoch) else { return }
                guard case let .success(generation) = result, generation == settings.generation else {
                    self.failed(.unavailable, epoch: epoch)
                    return
                }
                guard !pending.isEmpty else {
                    self.statusValue.set(.ready)
                    self.finished(epoch: epoch)
                    return
                }
                guard let entry = pending.first, let operation = entry.operation, operation.generation == generation,
                      let capture = operation.makeCapture(accountId: self.accountId) else {
                    // Unknown versions and corrupt payloads remain queued for review/recovery.
                    self.failed(.invalidPendingCapture, epoch: epoch)
                    return
                }
                self.statusValue.set(.writing)
                let policy = ArchiveCapturePolicy(enabled: settings.enabled, excludedPeers: settings.excludedPeerIds,
                                                  excludedSenders: settings.excludedSenderIds, excludeBots: settings.excludeBots)
                storage.recordFromOutbox(capture, index: Int64(entry.index), generation: generation, policy: policy) { [weak self] result in
                    self?.queue.justDispatch { [weak self] in
                        guard let self, self.isCurrent(epoch) else { return }
                        guard case let .success(written) = result else {
                            self.failed(.unavailable, epoch: epoch)
                            return
                        }
                        let acknowledge: (Bool) -> Void = { [weak self] missing in
                            guard let self, self.isCurrent(epoch) else { return }
                        // Archive commit precedes acknowledgement. A crash here replays
                        // identical event keys, timestamps and a durable outbox high-water
                        // mark. The mark also prevents replay after retention evicts a row.
                        self.transactionDisposable.set((self.postbox.transaction { [accountPeerId = self.accountPeerId] transaction in
                            let acknowledged = acknowledgeLocalArchiveCapture(transaction: transaction, accountPeerId: accountPeerId,
                                                               index: entry.index, eventKey: operation.eventKey)
                            if acknowledged && missing {
                                var current = localArchiveSettings(transaction: transaction)
                                if current.isSupported && current.generation == operation.generation && current.droppedCaptures < Int64.max {
                                    current.droppedCaptures += 1
                                    setLocalArchiveSettings(transaction: transaction, settings: current)
                                }
                            }
                        } |> deliverOn(self.queue)).start(next: { [weak self] _ in
                            guard let self, self.isCurrent(epoch) else { return }
                            self.needsDrain = true
                            self.finished(epoch: epoch)
                        }))
                        }
                        if written.excluded > 0 {
                            acknowledge(false)
                        } else {
                            self.copyCachedMedia(storage: storage, operation: operation, slot: 0, epoch: epoch, completion: acknowledge)
                        }
                    }
                }
            }
        }
    }

    private func copyCachedMedia(storage: LocalArchiveStorage, operation: LocalArchiveCaptureOperation,
                                 slot: Int, epoch: Int64, missing: Bool = false, completion: @escaping (Bool) -> Void) {
        guard self.isCurrent(epoch) else { return }
        let media = localArchiveDecodedMediaMetadata(operation.entities) ?? []
        guard slot < media.count else { completion(missing); return }
        let item = media[slot]
        storage.containsMedia(eventKey: operation.eventKey, slot: slot) { [weak self] result in
            self?.queue.justDispatch { [weak self] in
                guard let self, self.isCurrent(epoch) else { return }
                guard case let .success(exists) = result else { self.failed(.unavailable, epoch: epoch); return }
                if exists {
                    self.copyCachedMedia(storage: storage, operation: operation, slot: slot + 1, epoch: epoch, missing: missing, completion: completion)
                } else {
                    self.copyMediaFile(storage: storage, operation: operation, item: item, slot: slot, epoch: epoch, missing: missing, completion: completion)
                }
            }
        }
    }

    private func copyMediaFile(storage: LocalArchiveStorage, operation: LocalArchiveCaptureOperation, item: LocalArchiveMediaMetadata,
                               slot: Int, epoch: Int64, missing: Bool, candidate: Int = 0, completion: @escaping (Bool) -> Void) {
        guard self.isCurrent(epoch) else { return }
        let keys = ([item.resourceKey].compactMap { $0 } + (item.alternativeResourceKeys ?? [])).filter(localArchiveSafeResourceKey)
        guard candidate < keys.count else {
            self.copyCachedMedia(storage: storage, operation: operation, slot: slot + 1, epoch: epoch, missing: true, completion: completion)
            return
        }
        let path = self.postbox.mediaBox.storePathsForId(MediaResourceId(keys[candidate])).complete
        // ArchiveStore opens and streams the completed file on its own worker. Never
        // materialize a video or document as a full Data buffer on this account queue.
        storage.recordMediaFile(eventKey: operation.eventKey, slot: slot, kind: item.kind.rawValue,
                                mimeType: item.mimeType ?? "application/octet-stream", source: URL(fileURLWithPath: path),
                                expectedSize: item.byteSize, generation: operation.generation) { [weak self] result in
            self?.queue.justDispatch { [weak self] in
                guard let self, self.isCurrent(epoch) else { return }
                guard case let .success(retained) = result else { self.failed(.unavailable, epoch: epoch); return }
                if retained {
                    self.copyCachedMedia(storage: storage, operation: operation, slot: slot + 1, epoch: epoch, missing: missing, completion: completion)
                } else {
                    self.copyMediaFile(storage: storage, operation: operation, item: item, slot: slot, epoch: epoch, missing: missing, candidate: candidate + 1, completion: completion)
                }
            }
        }
    }

    private func completePurge(storage: LocalArchiveStorage, settings: LocalArchiveSettings, epoch: Int64) {
        self.statusValue.set(.purging)
        storage.purge(advancingToGeneration: settings.generation) { [weak self] result in
            self?.queue.justDispatch { [weak self] in
                guard let self, self.isCurrent(epoch) else { return }
                guard case .success = result else {
                    self.failed(.unavailable, epoch: epoch)
                    return
                }
                self.transactionDisposable.set((self.postbox.transaction { [accountPeerId = self.accountPeerId] transaction in
                    var current = localArchiveSettings(transaction: transaction)
                    guard current.isSupported, current.pendingPurge, current.generation == settings.generation else { return }
                    // Captures are paused by the persistent pendingPurge marker.
                    // Removing this account's outbox cannot touch another account.
                    transaction.operationLogRemoveAllEntries(peerId: accountPeerId, tag: OperationLogTags.LocalArchiveCapture)
                    current.pendingPurge = false
                    current.droppedCaptures = 0
                    setLocalArchiveSettings(transaction: transaction, settings: current)
                } |> deliverOn(self.queue)).start(next: { [weak self] _ in
                    guard let self, self.isCurrent(epoch) else { return }
                    self.needsDrain = true
                    self.finished(epoch: epoch)
                }))
            }
        }
    }

    private func finished(epoch: Int64) {
        guard self.isCurrent(epoch) else { return }
        self.working = false
        self.drainIfNeeded()
    }

    private func failed(_ status: LocalArchiveStatus, epoch: Int64) {
        guard self.isCurrent(epoch) else { return }
        self.statusValue.set(status)
        self.working = false
        self.needsDrain = true
        self.retryScheduled = true
        // No tight retry loop when locked, out of space or encountering a corrupt payload.
        self.queue.after(30.0) { [weak self] in
            guard let self, self.isCurrent(epoch) else { return }
            self.retryScheduled = false
            self.drainIfNeeded()
        }
    }
}

private func localArchiveProtectedDataSignal() -> Signal<Bool, NoError> {
    return Signal { subscriber in
        #if os(iOS)
        let center = NotificationCenter.default
        let locked = center.addObserver(forName: Notification.Name("UIApplicationProtectedDataWillBecomeUnavailable"),
                                        object: nil, queue: nil) { _ in subscriber.putNext(false) }
        let unlocked = center.addObserver(forName: Notification.Name("UIApplicationProtectedDataDidBecomeAvailable"),
                                          object: nil, queue: nil) { _ in subscriber.putNext(true) }
        // TelegramCore does not import UIKit. The first true value permits only a
        // fresh WhenUnlockedThisDeviceOnly Keychain lookup and protected file open.
        // It does not establish an unlocked state. Either failure leaves the store
        // unopened and the outbox queued; there is no cached-key fallback.
        subscriber.putNext(true)
        return ActionDisposable {
            center.removeObserver(locked)
            center.removeObserver(unlocked)
        }
        #else
        subscriber.putNext(true)
        return EmptyDisposable
        #endif
    }
}
