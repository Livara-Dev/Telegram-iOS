// SPDX-License-Identifier: GPL-2.0-or-later
// Created 2026-11-06 for the AyuGram iOS fork.
import Foundation
import Postbox
import LocalMessageArchive

/// An in-process bridge across canonical cache unlink, never durable storage.
/// Neither this registry nor a pinned descriptor is evidence of archived bytes.
private final class LocalArchiveMediaSourceRegistry {
    static let shared = LocalArchiveMediaSourceRegistry()
    static let maximumOwners = 16
    static let maximumDescriptors = 64
    static let maximumBytes: Int64 = 256 * 1024 * 1024

    private final class WeakOwner {
        weak var value: LocalArchiveMediaSourceHandoff?
        init(_ value: LocalArchiveMediaSourceHandoff) { self.value = value }
    }
    private let lock = NSLock()
    private var owners: [ObjectIdentifier: WeakOwner] = [:]
    private var descriptors = 0
    private var bytes: Int64 = 0

    func register(_ owner: LocalArchiveMediaSourceHandoff, mediaBox: MediaBox) -> Bool {
        self.lock.lock()
        // A weak promotion can become an owner's final strong reference. Keep
        // it beyond unlock: deinit unregisters, and must never reenter this lock.
        let retainedOwners = self.owners.compactMap { key, value -> (ObjectIdentifier, LocalArchiveMediaSourceHandoff)? in
            guard let owner = value.value else { return nil }
            return (key, owner)
        }
        let liveKeys = Set(retainedOwners.map { $0.0 })
        self.owners = self.owners.filter { liveKeys.contains($0.key) }
        let key = ObjectIdentifier(mediaBox)
        let registered = self.owners[key] == nil && self.owners.count < Self.maximumOwners
        if registered { self.owners[key] = WeakOwner(owner) }
        self.lock.unlock()
        withExtendedLifetime(retainedOwners) {}
        return registered
    }

    func owner(for mediaBox: MediaBox) -> LocalArchiveMediaSourceHandoff? {
        self.lock.lock()
        let owner = self.owners[ObjectIdentifier(mediaBox)]?.value
        let result = owner?.mediaBox === mediaBox ? owner : nil
        self.lock.unlock()
        withExtendedLifetime(owner) {}
        return result
    }

    func unregister(_ owner: LocalArchiveMediaSourceHandoff, mediaBox: MediaBox) {
        self.lock.lock()
        let current = self.owners[ObjectIdentifier(mediaBox)]?.value
        if current === owner {
            self.owners.removeValue(forKey: ObjectIdentifier(mediaBox))
        }
        self.lock.unlock()
        withExtendedLifetime(current) {}
    }

    func reserveDescriptor(preserveOnDemandHeadroom: Bool) -> Bool {
        self.lock.lock()
        defer { self.lock.unlock() }
        let limit = Self.maximumDescriptors - (preserveOnDemandHeadroom ? 1 : 0)
        guard self.descriptors < limit else { return false }
        self.descriptors += 1
        return true
    }

    func reserveBytes(_ count: Int64) -> Bool {
        self.lock.lock()
        defer { self.lock.unlock() }
        guard count >= 0, count <= Self.maximumBytes - self.bytes else { return false }
        self.bytes += count
        return true
    }

    func release(bytes: Int64) {
        self.lock.lock()
        self.descriptors -= 1
        self.bytes -= bytes
        assert(self.descriptors >= 0 && self.bytes >= 0)
        self.lock.unlock()
    }
}

private struct LocalArchiveMediaLeaseEvent: Hashable {
    let eventKey: String
    let generation: Int64
}

private struct LocalArchiveMediaLeaseKey: Hashable {
    let event: LocalArchiveMediaLeaseEvent
    let slot: Int
    let resourceKey: String
}

/// The source owns this reservation until its actual descriptor has closed.
/// Expiry/cancellation must not free a global FD slot while a worker still owns it.
private final class LocalArchiveMediaSourceReservation {
    weak var owner: LocalArchiveMediaSourceHandoff?
    var globalBytes: Int64 = 0
    var ownerBytes: Int64 = 0

    init(owner: LocalArchiveMediaSourceHandoff) { self.owner = owner }
    deinit {
        LocalArchiveMediaSourceRegistry.shared.release(bytes: self.globalBytes)
        self.owner?.reservationReleased(bytes: self.ownerBytes)
    }
}

final class LocalArchiveMediaSourceLease {
    let source: ArchiveMediaFileSource
    fileprivate let expiresAt: TimeInterval
    fileprivate init(source: ArchiveMediaFileSource, expiresAt: TimeInterval) {
        self.source = source
        self.expiresAt = expiresAt
    }
}

enum LocalArchiveMediaSourceLookup {
    case absent
    case revoked
    case source(LocalArchiveMediaSourceLease)
}

enum LocalArchiveMediaSourceAttempt {
    case source(LocalArchiveMediaSourceLease)
    // Confirmed absence/unsupported source or a file that cannot fit this bound.
    case missing
    // Protection/I/O errors, changed policy or temporarily occupied FD/byte quota.
    case retry
    // A source was revoked or this event is now excluded. Never reopen its path.
    case revoked
}

/// One authorized account owns a small pool. The static index contains weak
/// owners only; no account coordinator means no pin and ordinary cleanup proceeds.
final class LocalArchiveMediaSourceHandoff {
    static let maximumSources = 16
    static let maximumRevokedEvents = 256
    static let lifetime: TimeInterval = 60.0
    private static let releaseQueue = DispatchQueue(label: "LocalArchive.media-source-release", qos: .utility)

    fileprivate weak var mediaBox: MediaBox?
    let accountPeerId: PeerId
    let accountId: Int64
    let testingEnvironment: Bool
    private let lock = NSLock()
    private let clock: () -> TimeInterval
    private var timer: DispatchSourceTimer?
    private var registered = false
    private var stopped = false
    private var protectedDataAvailable = false
    private var settings: LocalArchiveSettings?
    private var epoch: Int64 = 0
    private var descriptors = 0
    private var bytes: Int64 = 0
    private var leases: [LocalArchiveMediaLeaseKey: LocalArchiveMediaSourceLease] = [:]
    private var revokedEvents = Set<LocalArchiveMediaLeaseEvent>()
    private var revocationOverflow = false

    init(mediaBox: MediaBox, accountPeerId: PeerId, accountId: Int64, testingEnvironment: Bool = false,
         clock: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.mediaBox = mediaBox
        self.accountPeerId = accountPeerId
        self.accountId = accountId
        self.testingEnvironment = testingEnvironment
        self.clock = clock
        let timer = DispatchSource.makeTimerSource(queue: Self.releaseQueue)
        self.timer = timer
        timer.setEventHandler { [weak self] in self?.expire() }
        timer.schedule(deadline: .distantFuture)
        timer.resume()
        self.registered = LocalArchiveMediaSourceRegistry.shared.register(self, mediaBox: mediaBox)
    }

    deinit { self.dispose() }

    private static func policy(_ settings: LocalArchiveSettings) -> LocalArchiveSettings {
        var result = settings
        result.droppedCaptures = 0 // A diagnostic counter is not a privacy policy change.
        return result
    }

    func applySettings(_ settings: LocalArchiveSettings) {
        let settings = Self.policy(settings)
        self.lock.lock()
        var retired: [LocalArchiveMediaSourceLease] = []
        if self.settings != settings {
            retired = self.revokeAllLocked()
            if self.settings?.generation != settings.generation {
                self.revokedEvents.removeAll()
                self.revocationOverflow = false
            }
            self.settings = settings
        }
        self.lock.unlock()
        Self.retire(retired)
    }

    func setProtectedDataAvailable(_ available: Bool) {
        self.lock.lock()
        self.protectedDataAvailable = available
        let retired = available ? [] : self.revokeAllLocked()
        self.lock.unlock()
        Self.retire(retired)
    }

    func invalidate() {
        self.lock.lock()
        let retired = self.revokeAllLocked()
        self.lock.unlock()
        Self.retire(retired)
    }

    func dispose() {
        self.lock.lock()
        self.stopped = true
        let retired = self.revokeAllLocked()
        self.timer?.cancel()
        self.timer = nil
        self.lock.unlock()
        if let mediaBox = self.mediaBox {
            LocalArchiveMediaSourceRegistry.shared.unregister(self, mediaBox: mediaBox)
        }
        Self.retire(retired)
    }

    private func recordRevocationLocked(_ event: LocalArchiveMediaLeaseEvent) {
        if self.revokedEvents.count < Self.maximumRevokedEvents || self.revokedEvents.contains(event) {
            self.revokedEvents.insert(event)
        } else {
            // Never discard a revocation and subsequently reopen its URL. A
            // bounded overflow fails closed until a new purge generation.
            self.revocationOverflow = true
        }
    }

    private func revokeAllLocked() -> [LocalArchiveMediaSourceLease] {
        self.epoch &+= 1
        for key in self.leases.keys { self.recordRevocationLocked(key.event) }
        let result = Array(self.leases.values)
        for lease in result { lease.source.invalidate() }
        self.leases.removeAll()
        self.timer?.schedule(deadline: .distantFuture)
        return result
    }

    private static func retire(_ leases: [LocalArchiveMediaSourceLease]) {
        guard !leases.isEmpty else { return }
        // ARC's final descriptor close must not move onto a UI callback/deinit.
        Self.releaseQueue.async { withExtendedLifetime(leases) {} }
    }

    private func scheduleTimerLocked() {
        if let next = self.leases.values.map({ $0.expiresAt }).min() {
            self.timer?.schedule(deadline: .now() + max(0.0, next - self.clock()), leeway: .milliseconds(100))
        } else {
            self.timer?.schedule(deadline: .distantFuture)
        }
    }

    private func expireLocked() -> [LocalArchiveMediaSourceLease] {
        let now = self.clock()
        let expired = self.leases.filter { $0.value.expiresAt <= now }
        for (key, lease) in expired {
            self.recordRevocationLocked(key.event)
            lease.source.invalidate()
            self.leases.removeValue(forKey: key)
        }
        self.scheduleTimerLocked()
        return Array(expired.values)
    }

    private func expire() {
        self.lock.lock()
        let retired = self.expireLocked()
        self.lock.unlock()
        Self.retire(retired)
    }

    fileprivate func reservationReleased(bytes: Int64) {
        self.lock.lock()
        self.descriptors -= 1
        self.bytes -= bytes
        assert(self.descriptors >= 0 && self.bytes >= 0)
        self.lock.unlock()
    }

    private func permittedLocked(settings: LocalArchiveSettings) -> Bool {
        return self.registered && !self.stopped && self.protectedDataAvailable
            && self.settings == Self.policy(settings) && settings.isSupported && settings.enabled
            && !settings.pendingPurge
    }

    private func policyAllows(_ capture: ArchiveCapture, settings: LocalArchiveSettings) -> Bool {
        let context = capture.context
        return context.ordinaryCloudMessage != context.secretChat
            && (!context.secretChat || settings.includeSecretChats)
            && (!(context.expiring || context.viewOnce) || settings.includeExpiringMessages)
            && (!context.viewOnce || context.expiring)
            && !(settings.excludeBots && context.botConversation)
            && !settings.excludedPeerIds.contains(capture.event.message.peerId)
            && (settings.excludedSenderIds.isEmpty || capture.event.content.authorId != nil)
            && !(capture.event.content.authorId.map { settings.excludedSenderIds.contains($0) } ?? false)
    }

    fileprivate func pin(operation: LocalArchiveCaptureOperation, slot: Int, resourceKey: String,
                         expectedSize: Int64?, settings: LocalArchiveSettings, mediaBox: MediaBox) {
        _ = self.acquire(operation: operation, slot: slot, resourceKey: resourceKey, expectedSize: expectedSize,
                         expectedSettings: settings, mediaBox: mediaBox, beforeUnlink: true)
    }

    /// Runs on the coordinator's utility queue before submitting archive work.
    /// Secret deletions deliberately cannot enter this path after a pin miss.
    func acquire(operation: LocalArchiveCaptureOperation, slot: Int, resourceKey: String,
                 expectedSize: Int64?) -> LocalArchiveMediaSourceAttempt {
        guard let mediaBox = self.mediaBox else { return .retry }
        guard operation.messageId.peerId.namespace != Namespaces.Peer.SecretChat
            || operation.kindRawValue != ArchiveEventKind.deleted.rawValue else { return .revoked }
        return self.acquire(operation: operation, slot: slot, resourceKey: resourceKey, expectedSize: expectedSize,
                            expectedSettings: nil, mediaBox: mediaBox, beforeUnlink: false)
    }

    private func acquire(operation: LocalArchiveCaptureOperation, slot: Int, resourceKey: String,
                         expectedSize: Int64?, expectedSettings: LocalArchiveSettings?, mediaBox: MediaBox,
                         beforeUnlink: Bool) -> LocalArchiveMediaSourceAttempt {
        guard !Thread.isMainThread, self.mediaBox === mediaBox,
              let capture = operation.makeCapture(accountId: self.accountId),
              let media = localArchiveDecodedMediaMetadata(operation.entities), slot >= 0, slot < media.count,
              expectedSize == media[slot].byteSize, localArchiveSafeResourceKey(resourceKey),
              ([media[slot].resourceKey].compactMap { $0 } + (media[slot].alternativeResourceKeys ?? [])).contains(resourceKey) else { return .retry }
        let event = LocalArchiveMediaLeaseEvent(eventKey: operation.eventKey, generation: operation.generation)
        let key = LocalArchiveMediaLeaseKey(event: event, slot: slot, resourceKey: resourceKey)
        self.lock.lock()
        let retired = self.expireLocked()
        guard let settings = self.settings, self.permittedLocked(settings: settings),
              expectedSettings.map({ Self.policy($0) == settings }) ?? true,
              operation.generation == settings.generation else {
            self.lock.unlock()
            Self.retire(retired)
            return .retry
        }
        // Metadata may already have committed before an exclusion changes. Its
        // durable duplicate receipt does not reevaluate capture policy; a now
        // denied event must advance without importing media or blocking the queue.
        guard self.policyAllows(capture, settings: settings) else {
            self.lock.unlock()
            Self.retire(retired)
            return .revoked
        }
        if self.revocationOverflow || self.revokedEvents.contains(event) {
            self.lock.unlock()
            Self.retire(retired)
            return .revoked
        }
        if let existing = self.leases[key] {
            self.lock.unlock()
            Self.retire(retired)
            return .source(existing)
        }
        let maximumBytes = min(settings.maxPayloadBytes, LocalArchiveMediaSourceRegistry.maximumBytes)
        if expectedSize.map({ $0 > maximumBytes }) ?? false {
            self.lock.unlock()
            Self.retire(retired)
            return .missing
        }
        // Keep one descriptor slot for an ordinary head-of-line media item.
        // Byte pressure can still wait for expiry; this does not reorder outbox work.
        guard self.descriptors < Self.maximumSources - (beforeUnlink ? 1 : 0) else {
            self.lock.unlock()
            Self.retire(retired)
            return .retry
        }
        let epoch = self.epoch
        self.descriptors += 1
        self.lock.unlock()
        Self.retire(retired)
        guard LocalArchiveMediaSourceRegistry.shared.reserveDescriptor(preserveOnDemandHeadroom: beforeUnlink) else {
            self.reservationReleased(bytes: 0)
            return .retry
        }
        let reservation = LocalArchiveMediaSourceReservation(owner: self)
        // Only MediaBox's completed pathname is eligible. Do not use partial
        // files, hard links, fetch signals, decryption paths or plaintext staging.
        let path = mediaBox.storePathsForId(MediaResourceId(resourceKey)).complete
        let source: ArchiveMediaFileSource
        do {
            guard let acquired = try ArchiveMediaFileSource.acquire(source: URL(fileURLWithPath: path), expectedSize: expectedSize,
                onClose: { withExtendedLifetime(reservation) {} }) else { return .missing }
            source = acquired
        } catch {
            // Preserve the outbox on permission/protection or filesystem failure.
            // It is not evidence that the attachment was absent.
            return .retry
        }
        guard source.byteCount <= maximumBytes else { return .missing }
        self.lock.lock()
        guard self.epoch == epoch, self.permittedLocked(settings: settings) else {
            self.lock.unlock()
            source.invalidate()
            return .retry
        }
        guard !self.revocationOverflow, !self.revokedEvents.contains(event) else {
            self.lock.unlock()
            source.invalidate()
            return .revoked
        }
        if let existing = self.leases[key] {
            self.lock.unlock()
            source.invalidate()
            return .source(existing)
        }
        guard source.byteCount <= settings.maxPayloadBytes - self.bytes,
              LocalArchiveMediaSourceRegistry.shared.reserveBytes(source.byteCount) else {
            self.lock.unlock()
            return .retry
        }
        reservation.globalBytes = source.byteCount
        self.bytes += source.byteCount
        reservation.ownerBytes = source.byteCount
        let lease = LocalArchiveMediaSourceLease(source: source, expiresAt: self.clock() + Self.lifetime)
        self.leases[key] = lease
        self.scheduleTimerLocked()
        self.lock.unlock()
        return .source(lease)
    }

    func source(eventKey: String, generation: Int64, slot: Int, resourceKey: String) -> LocalArchiveMediaSourceLookup {
        let event = LocalArchiveMediaLeaseEvent(eventKey: eventKey, generation: generation)
        let key = LocalArchiveMediaLeaseKey(event: event, slot: slot, resourceKey: resourceKey)
        self.lock.lock()
        let retired = self.expireLocked()
        let result: LocalArchiveMediaSourceLookup
        if self.stopped || !self.protectedDataAvailable || self.settings?.isSupported != true
            || self.settings?.enabled != true
            || self.settings?.pendingPurge == true || self.revocationOverflow
            || self.revokedEvents.contains(event) || self.settings?.generation != generation {
            result = .revoked
        } else if let source = self.leases[key] {
            result = .source(source)
        } else {
            result = .absent
        }
        self.lock.unlock()
        Self.retire(retired)
        return result
    }

    func acknowledged(eventKey: String, generation: Int64) {
        let event = LocalArchiveMediaLeaseEvent(eventKey: eventKey, generation: generation)
        self.lock.lock()
        let matching = self.leases.filter { $0.key.event == event }
        for key in matching.keys { self.leases.removeValue(forKey: key) }
        self.revokedEvents.remove(event)
        self.scheduleTimerLocked()
        self.lock.unlock()
        Self.retire(Array(matching.values))
    }
}

/// Called on the Postbox transaction worker immediately before normal cleanup.
/// Successful enqueue authorizes only an attempt to pin currently accessible bytes.
/// Canonical resource IDs/force flags are never modified by this helper.
func localArchivePinMediaBeforeRemoval(transaction: Transaction, mediaBox: MediaBox,
                                      resourceIds: [MediaResourceId], capturedResources: Set<MediaResourceId>) {
    guard !Thread.isMainThread, !resourceIds.isEmpty, !capturedResources.isEmpty,
          let owner = LocalArchiveMediaSourceRegistry.shared.owner(for: mediaBox),
          let state = transaction.getState() as? AuthorizedAccountState, state.peerId == owner.accountPeerId,
          state.isTestingEnvironment == owner.testingEnvironment else { return }
    let settings = localArchiveSettings(transaction: transaction)
    guard settings.isSupported, settings.enabled, !settings.pendingPurge, settings.includeSecretChats else { return }
    // Do not build another set for an arbitrarily large history deletion. Only
    // the bounded successful-capture identities enter this temporary index.
    let due = Set(resourceIds.lazy.filter { capturedResources.contains($0) })
    guard !due.isEmpty else { return }
    var inspected = 0
    var attempts = 0
    transaction.operationLogEnumerateEntries(peerId: owner.accountPeerId, tag: OperationLogTags.LocalArchiveCapture) { entry in
        inspected += 1
        guard entry.tagLocalIndex >= 0, let operation = entry.contents as? LocalArchiveCaptureOperation,
              operation.generation == settings.generation, operation.messageId.peerId.namespace == Namespaces.Peer.SecretChat,
              operation.kindRawValue == ArchiveEventKind.deleted.rawValue,
              let capture = operation.makeCapture(accountId: owner.accountId),
              !(capture.context.expiring || capture.context.viewOnce) || settings.includeExpiringMessages,
              !(settings.excludeBots && capture.context.botConversation),
              !settings.excludedPeerIds.contains(operation.messageId.peerId.toInt64()),
              settings.excludedSenderIds.isEmpty || operation.authorId != nil,
              !(operation.authorId.map { settings.excludedSenderIds.contains($0) } ?? false) else { return inspected < 256 }
        for (slot, item) in (localArchiveDecodedMediaMetadata(operation.entities) ?? []).enumerated() {
            for key in [item.resourceKey].compactMap({ $0 }) + (item.alternativeResourceKeys ?? []) {
                if due.contains(MediaResourceId(key)), localArchiveSafeResourceKey(key) {
                    owner.pin(operation: operation, slot: slot, resourceKey: key, expectedSize: item.byteSize, settings: settings, mediaBox: mediaBox)
                    attempts += 1
                    if attempts >= LocalArchiveMediaSourceHandoff.maximumSources { return false }
                }
            }
        }
        return inspected < 256
    }
}
