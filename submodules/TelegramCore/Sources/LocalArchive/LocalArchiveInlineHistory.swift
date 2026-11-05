// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

private func localArchiveInlineHistorySupportsPeer(_ peerId: PeerId) -> Bool {
    return peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudGroup
        || peerId.namespace == Namespaces.Peer.CloudChannel
}

/// The normal chat can link to a saved version, but must never construct a
/// Telegram message from it. Check the *current* message before showing a link:
/// an ordinary message can subsequently acquire a timer or copy protection.
public func localArchiveInlineHistorySupportsMessage(_ message: EngineMessage) -> Bool {
    guard localArchiveInlineHistorySupportsPeer(message.id.peerId), message.id.namespace == Namespaces.Message.Cloud,
          message.id.id > 0, message.richText == nil, !message.flags.contains(.CopyProtected),
          message.attributes.contains(where: { $0 is EditedMessageAttribute }),
          !message.attributes.contains(where: { $0 is AutoremoveTimeoutMessageAttribute || $0 is AutoclearTimeoutMessageAttribute }),
          !message.media.contains(where: { $0 is TelegramMediaExpiredContent || $0 is TelegramMediaAction }),
          let peer = message.peers[message.id.peerId], !peer.isCopyProtectionEnabled,
          message.forwardInfo?.flags.contains(.isImported) != true else { return false }
    return true
}

/// Main-queue, visible-cell lifetime for a read-only history marker. It passes no
/// archived text/media to the chat renderer. A positive result means an actual
/// indexed archive query found a saved edit, not merely a server edited flag.
///
/// Each query reads at most one bounded snapshot. Ready transitions are debounced
/// to coalesce a burst of captures; unedited messages never create this loader.
/// Releasing visibility also releases the existing-store read lease. That lease
/// cannot enable capture, create an absent database, or create a Keychain key.
public final class LocalArchiveInlineHistoryLoader {
    private let status: Signal<LocalArchiveStatus, NoError>
    private let markers: Signal<LocalArchivePresentationSettingsState, NoError>
    private let policy: Signal<LocalArchiveSettings, NoError>
    private let revision: Signal<Int64, NoError>
    private let acquire: () -> Disposable
    private let lookup: () -> Signal<Bool, NoError>
    private let allowed: (LocalArchiveSettings) -> Bool
    private let changed: (String?) -> Void
    private let debounceSeconds: Double
    private let subscriptions = MetaDisposable()
    private let query = MetaDisposable()
    private var lease: Disposable?
    private var active = false
    private var disposed = false
    private var generation: Int64 = 0
    private var request: Int64 = 0
    private var label: String?

    public convenience init(archive: LocalArchiveCoordinator, presentation: LocalArchivePresentationContext,
                            message: EngineMessage, changed: @escaping (String?) -> Void) {
        let messageId = message.id
        self.init(status: archive.status, markers: presentation.settings, policy: archive.settings, revision: archive.historyRevision,
                  acquire: { archive.acquireReadAccess() }, lookup: {
            guard localArchiveInlineHistorySupportsMessage(message) else { return .single(false) }
            return archive.timeline(peerId: messageId.peerId.toInt64(), namespace: messageId.namespace,
                                    messageId: messageId.id, limit: 1)
            |> map { !$0.unavailable && $0.records.contains(where: { !$0.isDeleted }) }
        }, allowed: { settings in
            guard !settings.excludedPeerIds.contains(messageId.peerId.toInt64()),
                  settings.excludedSenderIds.isEmpty || message.author != nil,
                  !(message.author.map { settings.excludedSenderIds.contains($0.id.toInt64()) } ?? false) else { return false }
            return !(settings.excludeBots && (message.peers[messageId.peerId] as? TelegramUser)?.botInfo != nil)
        }, changed: changed)
    }

    // Native tests inject a deliberately late source to exercise invalidation.
    init(status: Signal<LocalArchiveStatus, NoError>, markers: Signal<LocalArchivePresentationSettingsState, NoError>,
         policy: Signal<LocalArchiveSettings, NoError>, revision: Signal<Int64, NoError> = .single(0), acquire: @escaping () -> Disposable,
         lookup: @escaping () -> Signal<Bool, NoError>, allowed: @escaping (LocalArchiveSettings) -> Bool = { _ in true },
         debounceSeconds: Double = 0.25,
         changed: @escaping (String?) -> Void) {
        self.status = status
        self.markers = markers
        self.policy = policy
        self.revision = revision
        self.acquire = acquire
        self.lookup = lookup
        self.allowed = allowed
        self.debounceSeconds = debounceSeconds
        self.changed = changed
    }

    public func setActive(_ active: Bool) {
        assert(Queue.mainQueue().isCurrent())
        guard !self.disposed, self.active != active else { return }
        self.active = active
        self.generation &+= 1
        let generation = self.generation
        self.request &+= 1
        self.query.set(nil)
        self.subscriptions.set(nil)
        self.lease?.dispose()
        self.lease = nil
        self.updateLabel(nil)
        guard active else { return }
        self.lease = self.acquire()
        self.subscriptions.set((combineLatest(self.status, self.markers, self.policy, self.revision)
        |> deliverOnMainQueue).start(next: { [weak self] status, markers, policy, _ in
            guard let self, !self.disposed, self.active, self.generation == generation else { return }
            self.request &+= 1
            let request = self.request
            self.query.set(nil)
            self.updateLabel(nil)
            // A write can prune older versions under the configured quota. Do
            // not retain an unverified positive cache across a write or purge.
            guard status == .ready || status == .browsing, policy.isSupported, !policy.pendingPurge, self.allowed(policy),
                  case let .available(settings) = markers,
                  LocalArchivePresentationSettings.normalizedMarker(settings.editedMarker) == settings.editedMarker else {
                self.updateLabel(nil)
                return
            }
            self.query.set((Signal<Void, NoError>.single(())
            |> delay(self.debounceSeconds, queue: .mainQueue())
            |> mapToSignal { [lookup = self.lookup] _ in lookup() }
            |> deliverOnMainQueue).start(next: { [weak self] found in
                guard let self, !self.disposed, self.active, self.generation == generation,
                      self.request == request else { return }
                self.updateLabel(found ? settings.editedMarker : nil)
            }))
        }))
    }

    private func updateLabel(_ label: String?) {
        guard self.label != label else { return }
        self.label = label
        self.changed(label)
    }

    public func dispose() {
        assert(Queue.mainQueue().isCurrent())
        guard !self.disposed else { return }
        self.setActive(false)
        self.disposed = true
        self.generation &+= 1
        self.request &+= 1
        self.subscriptions.dispose()
        self.query.dispose()
    }

    deinit {
        self.subscriptions.dispose()
        self.query.dispose()
        self.lease?.dispose()
    }
}
