// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit
import LocalMessageArchive

/// A proven, ordinary cloud chat and a small canonical history window. Archive
/// records do not carry enough thread metadata to mix safely into forum topics.
public struct LocalArchiveDeletedHistoryWindow: Equatable {
    public let peerId: EnginePeer.Id
    public let messageIds: ClosedRange<Int32>
    public let sentAt: ClosedRange<Int64>
    let peerIsBot: Bool
    public let namespaces: [Int32]

    public init?(peer: EnginePeer, messageIds: ClosedRange<Int32>, sentAt: ClosedRange<Int64>) {
        let rawPeer = peer._asPeer()
        guard Self.supportsPeer(peer), messageIds.lowerBound >= (peer.id.namespace == Namespaces.Peer.SecretChat ? 0 : 1), sentAt.lowerBound >= 0,
              sentAt.upperBound <= Int64(Int32.max) else { return nil }
        self.peerId = peer.id
        self.namespaces = peer.id.namespace == Namespaces.Peer.SecretChat ? [Namespaces.Message.SecretIncoming, Namespaces.Message.Local] : [Namespaces.Message.Cloud]
        self.messageIds = messageIds
        self.sentAt = sentAt
        self.peerIsBot = (rawPeer as? TelegramUser)?.botInfo != nil
    }

    public static func supportsPeer(_ peer: EnginePeer) -> Bool {
        let id = peer.id
        let rawPeer = peer._asPeer()
        return (id.namespace == Namespaces.Peer.CloudUser || id.namespace == Namespaces.Peer.CloudGroup
            || id.namespace == Namespaces.Peer.CloudChannel || id.namespace == Namespaces.Peer.SecretChat) && !rawPeer.isCopyProtectionEnabled
            && !rawPeer.isForumOrMonoForum
    }
}

public struct LocalArchiveDeletedHistoryResult {
    public let records: [LocalArchivedMessage]
    public let isTruncated: Bool
    public let unavailable: Bool
}

/// All queued list transitions/items share this one revocable body. Clearing a
/// page invalidates old copies too; nodes register a synchronous clear callback.
/// Swift strings cannot promise forensic zeroization, but no chat-owned value
/// continues exposing the body after the main-queue visibility/lock boundary.
public final class LocalArchiveDeletedHistoryContent {
    private let value: Atomic<String?>
    private var observers: [Int64: () -> Void] = [:]
    private var nextObserver: Int64 = 0

    public init(text: String) {
        self.value = Atomic(value: String(text.prefix(2048)))
    }

    public var text: String? { return self.value.with { $0 } }

    public func onRevoked(_ f: @escaping () -> Void) -> Disposable {
        assert(Queue.mainQueue().isCurrent())
        guard self.text != nil else { f(); return EmptyDisposable }
        self.nextObserver &+= 1
        let id = self.nextObserver
        self.observers[id] = f
        return ActionDisposable { [weak self] in
            if Queue.mainQueue().isCurrent() {
                self?.observers.removeValue(forKey: id)
            } else {
                Queue.mainQueue().async { [weak self] in self?.observers.removeValue(forKey: id) }
            }
        }
    }

    public func revoke() {
        assert(Queue.mainQueue().isCurrent())
        guard self.value.swap(nil) != nil else { return }
        let callbacks = Array(self.observers.values)
        self.observers.removeAll()
        for f in callbacks { f() }
    }
}

/// Presentation data only. There is deliberately no Message, outgoing state,
/// media resource, reply target, selection or server-read flag in this model.
public struct LocalArchiveDeletedHistoryItem: Equatable {
    public let peerId: EnginePeer.Id
    public let messageId: Int32
    public let namespace: Int32
    public let mediaKinds: [String]
    public let storedMediaSlots: [Int]
    public let sequence: Int64
    public let eventKey: String
    public let sentAt: Int32
    public let deletedAt: Int64
    public let marker: String
    public let storedMediaCount: Int
    public let unavailableMediaCount: Int
    public let moreAvailable: Bool
    public let content: LocalArchiveDeletedHistoryContent

    public init(peerId: EnginePeer.Id, messageId: Int32, sequence: Int64, eventKey: String,
                sentAt: Int32, deletedAt: Int64, marker: String, storedMediaCount: Int,
                unavailableMediaCount: Int, moreAvailable: Bool, content: LocalArchiveDeletedHistoryContent,
                namespace: Int32 = Namespaces.Message.Cloud, mediaKinds: [String] = [], storedMediaSlots: [Int] = []) {
        self.peerId = peerId; self.messageId = messageId; self.sequence = sequence; self.eventKey = eventKey
        self.sentAt = sentAt; self.deletedAt = deletedAt; self.marker = marker
        self.storedMediaCount = storedMediaCount; self.unavailableMediaCount = unavailableMediaCount
        self.moreAvailable = moreAvailable; self.content = content
        self.namespace = namespace; self.mediaKinds = Array(mediaKinds.prefix(10)); self.storedMediaSlots = storedMediaSlots.filter { (0..<10).contains($0) }
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        return lhs.peerId == rhs.peerId && lhs.messageId == rhs.messageId && lhs.namespace == rhs.namespace
            && lhs.mediaKinds == rhs.mediaKinds && lhs.storedMediaSlots == rhs.storedMediaSlots && lhs.sequence == rhs.sequence
            && lhs.eventKey == rhs.eventKey && lhs.sentAt == rhs.sentAt && lhs.deletedAt == rhs.deletedAt
            && lhs.marker == rhs.marker && lhs.storedMediaCount == rhs.storedMediaCount
            && lhs.unavailableMediaCount == rhs.unavailableMediaCount && lhs.moreAvailable == rhs.moreAvailable
            && lhs.content === rhs.content
    }

    public func withMoreAvailable(_ value: Bool) -> Self {
        return Self(peerId: self.peerId, messageId: self.messageId, sequence: self.sequence, eventKey: self.eventKey,
            sentAt: self.sentAt, deletedAt: self.deletedAt, marker: self.marker, storedMediaCount: self.storedMediaCount,
            unavailableMediaCount: self.unavailableMediaCount, moreAvailable: value, content: self.content,
            namespace: self.namespace, mediaKinds: self.mediaKinds, storedMediaSlots: self.storedMediaSlots)
    }
}

/// Main-queue lifetime for the chat's local rows. A lease opens an existing
/// encrypted archive only; capture and Keychain creation remain independent.
/// Every write/prune/import revision revokes the old page before a bounded read.
public final class LocalArchiveDeletedHistoryLoader {
    private let status: Signal<LocalArchiveStatus, NoError>
    private let policy: Signal<LocalArchiveSettings, NoError>
    private let markers: Signal<LocalArchivePresentationSettingsState, NoError>
    private let revision: Signal<Int64, NoError>
    private let acquire: () -> Disposable
    private let lookup: (LocalArchiveDeletedHistoryWindow, Set<Int64>) -> Signal<LocalArchiveDeletedHistoryResult, NoError>
    private let changed: ([LocalArchiveDeletedHistoryItem]) -> Void
    private let debounceSeconds: Double
    private let clock: () -> Int64
    private let schedule: (Double, @escaping () -> Void) -> Disposable
    private let subscriptions = MetaDisposable()
    private let query = MetaDisposable()
    private let expiry = MetaDisposable()
    private var lease: Disposable?
    private var window: LocalArchiveDeletedHistoryWindow?
    private var active = false
    private var disposed = false
    private var generation: Int64 = 0
    private var request: Int64 = 0
    private var rows: [LocalArchiveDeletedHistoryItem] = []

    public convenience init(archive: LocalArchiveCoordinator, presentation: LocalArchivePresentationContext,
                            changed: @escaping ([LocalArchiveDeletedHistoryItem]) -> Void) {
        self.init(status: archive.status, policy: archive.settings, markers: presentation.settings,
            revision: archive.historyRevision, acquire: { archive.acquireReadAccess() },
            lookup: { archive.deletedHistory(window: $0, excludingAuthors: $1) }, changed: changed)
    }

    init(status: Signal<LocalArchiveStatus, NoError>, policy: Signal<LocalArchiveSettings, NoError>,
         markers: Signal<LocalArchivePresentationSettingsState, NoError>, revision: Signal<Int64, NoError>,
         acquire: @escaping () -> Disposable,
         lookup: @escaping (LocalArchiveDeletedHistoryWindow, Set<Int64>) -> Signal<LocalArchiveDeletedHistoryResult, NoError>,
         debounceSeconds: Double = 0.25, clock: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970) },
         schedule: ((Double, @escaping () -> Void) -> Disposable)? = nil,
         changed: @escaping ([LocalArchiveDeletedHistoryItem]) -> Void) {
        self.status = status; self.policy = policy; self.markers = markers; self.revision = revision
        self.acquire = acquire; self.lookup = lookup; self.changed = changed; self.debounceSeconds = debounceSeconds
        self.clock = clock
        self.schedule = schedule ?? { seconds, callback in
            let timer = SwiftSignalKit.Timer(timeout: seconds, repeat: false, completion: callback, queue: .mainQueue())
            timer.start()
            return ActionDisposable { timer.invalidate() }
        }
    }

    public func setWindow(_ window: LocalArchiveDeletedHistoryWindow?) {
        assert(Queue.mainQueue().isCurrent())
        guard !self.disposed, self.window != window else { return }
        self.window = window
        self.restart()
    }

    public func setActive(_ active: Bool) {
        assert(Queue.mainQueue().isCurrent())
        guard !self.disposed, self.active != active else { return }
        self.active = active
        self.restart()
    }

    private func clearRows() {
        self.expiry.set(nil)
        for row in self.rows { row.content.revoke() }
        guard !self.rows.isEmpty else { return }
        self.rows = []
        self.changed([])
    }

    private func restart() {
        self.generation &+= 1
        self.request &+= 1
        let generation = self.generation
        self.query.set(nil)
        self.subscriptions.set(nil)
        self.lease?.dispose(); self.lease = nil
        self.clearRows()
        guard self.active, let window = self.window else { return }
        self.lease = self.acquire()
        self.subscriptions.set((combineLatest(self.status, self.policy, self.markers, self.revision)
        |> deliverOnMainQueue).start(next: { [weak self] status, policy, markers, _ in
            guard let self, !self.disposed, self.active, self.generation == generation else { return }
            self.request &+= 1
            let request = self.request
            self.query.set(nil)
            self.clearRows()
            guard status == .ready || status == .browsing, policy.isSupported, !policy.pendingPurge,
                  !policy.excludedPeerIds.contains(window.peerId.toInt64()),
                  !(policy.excludeBots && window.peerIsBot), policy.excludedSenderIds.count <= ArchiveDeletedHistoryPage.maximumExcludedAuthors,
                  case let .available(markers) = markers,
                  LocalArchivePresentationSettings.normalizedMarker(markers.deletedMarker) == markers.deletedMarker else { return }
            self.query.set((Signal<Void, NoError>.single(())
            |> delay(self.debounceSeconds, queue: .mainQueue())
            |> mapToSignal { [lookup = self.lookup] _ in lookup(window, policy.excludedSenderIds) }
            |> deliverOnMainQueue).start(next: { [weak self] page in
                guard let self, !self.disposed, self.active, self.generation == generation,
                      self.request == request, !page.unavailable else { return }
                let now = self.clock()
                guard now >= 0 else { return }
                let cutoff = max(0, now - min(now, policy.maxAgeSeconds))
                var seen = Set<EngineMessage.Id>()
                let records = page.records.prefix(40).filter { record in
                    return record.isDeleted && record.peerId == window.peerId.toInt64()
                        && window.namespaces.contains(record.namespace) && window.messageIds.contains(record.messageId)
                        && window.sentAt.contains(record.sentAt) && record.sentAt <= Int64(Int32.max)
                        && record.sequence > 0 && record.observedAt >= cutoff && record.observedAt <= now
                        && (policy.excludedSenderIds.isEmpty || record.authorId.map { !policy.excludedSenderIds.contains($0) } == true)
                        && seen.insert(EngineMessage.Id(peerId: window.peerId, namespace: record.namespace, id: record.messageId)).inserted
                }
                self.clearRows()
                self.rows = records.enumerated().map { index, record in
                    LocalArchiveDeletedHistoryItem(peerId: window.peerId, messageId: record.messageId,
                        sequence: record.sequence, eventKey: record.eventKey, sentAt: Int32(record.sentAt),
                        deletedAt: record.observedAt, marker: markers.deletedMarker,
                        storedMediaCount: max(0, record.storedMediaCount), unavailableMediaCount: max(0, record.unavailableMediaCount),
                        moreAvailable: page.isTruncated && index == records.count - 1,
                        content: LocalArchiveDeletedHistoryContent(text: record.text), namespace: record.namespace,
                        mediaKinds: record.mediaKinds, storedMediaSlots: record.storedMediaSlots)
                }
                self.changed(self.rows)
                self.scheduleExpiration(policy: policy, generation: generation, request: request)
            }))
        }))
    }

    private func scheduleExpiration(policy: LocalArchiveSettings, generation: Int64, request: Int64) {
        self.expiry.set(nil)
        guard !self.rows.isEmpty, policy.maxAgeSeconds != Int64.max else { return }
        let now = self.clock()
        guard now >= 0 else { self.clearRows(); return }
        let deadlines = self.rows.compactMap { row -> Int64? in
            let (sum, overflow) = row.deletedAt.addingReportingOverflow(policy.maxAgeSeconds)
            guard !overflow else { return nil }
            // Storage prunes observed_at < now - maxAge, hence the +1 boundary.
            let (deadline, finalOverflow) = sum.addingReportingOverflow(1)
            return finalOverflow ? nil : deadline
        }
        guard let deadline = deadlines.min() else { return }
        guard deadline > now else { self.clearRows(); return }
        // DispatchTime cannot represent arbitrarily many nanoseconds. One
        // outstanding annual checkpoint handles huge finite policies safely;
        // normal policies schedule exactly at their earliest expiration.
        let seconds = min(Double(deadline - now), 365.0 * 24.0 * 60.0 * 60.0)
        self.expiry.set(self.schedule(seconds, { [weak self] in
            guard let self, !self.disposed, self.active, self.generation == generation, self.request == request else { return }
            let current = self.clock()
            guard current >= now else { self.clearRows(); return }
            if current >= deadline {
                self.restart()
            } else {
                self.scheduleExpiration(policy: policy, generation: generation, request: request)
            }
        }))
    }

    public func dispose() {
        assert(Queue.mainQueue().isCurrent())
        guard !self.disposed else { return }
        self.active = false
        self.restart()
        self.disposed = true
        self.subscriptions.dispose(); self.query.dispose(); self.expiry.dispose()
    }

    deinit {
        self.subscriptions.dispose(); self.query.dispose(); self.expiry.dispose(); self.lease?.dispose()
        // Production ownership disposes on the main queue before releasing the
        // loader. Do not call UI revocation callbacks from a worker deinit.
    }
}
