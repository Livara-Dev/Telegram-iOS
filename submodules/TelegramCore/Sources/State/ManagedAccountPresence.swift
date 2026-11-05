import Foundation
internal import TelegramApi
import Postbox
import SwiftSignalKit
import MtProtoKit

private typealias SignalKitTimer = SwiftSignalKit.Timer

struct LocalAccountPresenceDecision: Equatable {
    let online: Bool
    let repeatOffline: Bool
}

func localAccountPresenceDecision(keepOnline: Bool, settings: LocalGhostSettingsState) -> LocalAccountPresenceDecision {
    return LocalAccountPresenceDecision(online: keepOnline && !localGhostSuppressesOnline(settings),
        repeatOffline: keepOnline && localGhostAttemptsAutomaticOffline(settings))
}

/// The injected sender is the existing typed account.updateStatus boundary.
/// Production uses the normal Network; tests replace only that final sender.
final class AccountPresenceManagerImpl {
    private let queue: Queue
    private let sendRequest: (Bool) -> Signal<Void, NoError>
    private let refreshInterval: Double
    let isPerformingUpdate = ValuePromise<Bool>(false, ignoreRepeated: true)

    private var shouldKeepOnlinePresenceDisposable: Disposable?
    private let currentRequestDisposable = MetaDisposable()
    private var onlineTimer: SignalKitTimer?
    private var currentDecision: LocalAccountPresenceDecision?
    private var keepOnline = false

    init(queue: Queue, shouldKeepOnlinePresence: Signal<Bool, NoError>,
         settings: Signal<LocalGhostSettingsState, NoError>, refreshInterval: Double = 30.0,
         sendRequest: @escaping (Bool) -> Signal<Void, NoError>) {
        self.queue = queue
        self.sendRequest = sendRequest
        self.refreshInterval = refreshInterval
        self.shouldKeepOnlinePresenceDisposable = (combineLatest(shouldKeepOnlinePresence, settings)
        |> deliverOn(self.queue)).start(next: { [weak self] keepOnline, settings in
            guard let self else { return }
            let decision = localAccountPresenceDecision(keepOnline: keepOnline, settings: settings)
            let changed = self.currentDecision != decision || self.keepOnline != keepOnline
            let first = self.currentDecision == nil
            self.keepOnline = keepOnline
            self.currentDecision = decision
            guard changed else { return }
            // A background account that has never announced online needs no RPC.
            if first && !keepOnline { return }
            self.updatePresence(decision)
        })
    }

    deinit {
        assert(self.queue.isCurrent())
        self.shouldKeepOnlinePresenceDisposable?.dispose()
        self.currentRequestDisposable.dispose()
        self.onlineTimer?.invalidate()
    }

    private func updatePresence(_ decision: LocalAccountPresenceDecision) {
        self.onlineTimer?.invalidate()
        self.onlineTimer = nil
        if decision.online || decision.repeatOffline {
            let timer = SignalKitTimer(timeout: self.refreshInterval, repeat: false, completion: { [weak self] in
                guard let self, self.currentDecision == decision else { return }
                self.updatePresence(decision)
            }, queue: self.queue)
            self.onlineTimer = timer
            timer.start()
        }
        self.isPerformingUpdate.set(true)
        self.currentRequestDisposable.set((self.sendRequest(decision.online)
        |> deliverOn(self.queue)).start(completed: { [weak self] in
            self?.isPerformingUpdate.set(false)
        }))
    }
}

final class AccountPresenceManager {
    private let queue = Queue()
    private let impl: QueueLocalObject<AccountPresenceManagerImpl>

    init(shouldKeepOnlinePresence: Signal<Bool, NoError>, postbox: Postbox, network: Network) {
        let queue = self.queue
        self.impl = QueueLocalObject(queue: self.queue, generate: {
            return AccountPresenceManagerImpl(queue: queue, shouldKeepOnlinePresence: shouldKeepOnlinePresence,
                settings: localGhostSettingsSignal(postbox: postbox), sendRequest: { online in
                    return network.request(Api.functions.account.updateStatus(offline: online ? .boolFalse : .boolTrue))
                    |> `catch` { _ -> Signal<Api.Bool, NoError> in .single(.boolFalse) }
                    |> mapToSignal { _ -> Signal<Void, NoError> in .complete() }
                })
        })
    }

    func isPerformingUpdate() -> Signal<Bool, NoError> {
        return Signal { subscriber in
            let disposable = MetaDisposable()
            self.impl.with { impl in
                disposable.set(impl.isPerformingUpdate.get().start(next: { value in
                    subscriber.putNext(value)
                }))
            }
            return disposable
        }
    }
}
