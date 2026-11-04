// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit
import MtProtoKit
internal import TelegramApi

/// Typed seam shared by the producer, persistent worker, and integration tests.
/// Only the final Telegram RPC transport is substituted in tests.
struct LocalStoryViewReceiptRequest {
    enum Kind: Equatable {
        case read
        case increment
    }

    let kind: Kind
    let peer: Peer
    let storyId: Int32
    let suppressionGeneration: Int64
}

typealias LocalStoryViewReceiptSender = (LocalStoryViewReceiptRequest) -> Signal<Void, MTRpcError>

func sendLocalStoryViewReceipt(network: Network, request: LocalStoryViewReceiptRequest) -> Signal<Void, MTRpcError> {
    guard let inputPeer = apiInputPeer(request.peer) else { return .complete() }
    switch request.kind {
    case .read:
        return network.request(Api.functions.stories.readStories(peer: inputPeer, maxId: request.storyId))
        |> map { _ in () }
    case .increment:
        return network.request(Api.functions.stories.incrementStoryViews(peer: inputPeer, id: [request.storyId]))
        |> map { _ in () }
    }
}

/// Observes persisted policy again immediately before requesting transport and
/// while it is pending. A later suppression/generation change disposes that
/// request best-effort. This is not atomic with MTProto transmission: bytes
/// already handed to the network may have reached Telegram.
func dispatchLocalStoryViewReceipt(postbox: Postbox, request: LocalStoryViewReceiptRequest, send: @escaping LocalStoryViewReceiptSender, queue: Queue) -> Signal<Void, NoError> {
    return localStoryPrivacySettingsSignal(postbox: postbox)
    |> deliverOn(queue)
    |> map { settings -> Signal<Void, NoError> in
        guard localStoryPrivacyAllowsReceipt(settings, generation: request.suppressionGeneration) else {
            return .single(())
        }
        // The observed snapshot may have waited on a busy delivery queue. Read
        // committed policy again and subscribe without another delivery hop;
        // an already committed enable must invalidate that stale snapshot.
        return postbox.transaction { transaction in
            localStoryPrivacySettings(transaction: transaction)
        }
        |> mapToSignal { latest -> Signal<Void, NoError> in
            guard localStoryPrivacyAllowsReceipt(latest, generation: request.suppressionGeneration) else { return .single(()) }
            // Match upstream's terminal error behavior. Errors do not fabricate
            // a Telegram success/pts and do not create a new retry queue.
            return send(request)
            |> `catch` { _ -> Signal<Void, NoError> in .complete() }
            // Drop transport values while keeping the Void signal type. The
            // durable operation retires only when transport completes.
            |> filter { _ in false }
            |> then(Signal<Void, NoError>.single(()))
        }
    }
    |> switchToLatest
    |> take(1)
}

private enum LocalStoryViewPreparation {
    case suppressed
    case localRead
    case request(LocalStoryViewReceiptRequest)
}

func markStoryAsSeen(postbox: Postbox, peerId: PeerId, id: Int32, asPinned: Bool, send: @escaping LocalStoryViewReceiptSender, injectReadUpdate: @escaping (PeerId, Int32) -> Void, queue: Queue = Queue()) -> Signal<Never, NoError> {
    return postbox.transaction { transaction -> LocalStoryViewPreparation in
        guard case let .available(settings) = localStoryPrivacySettings(transaction: transaction), !settings.suppressViewReceipts else {
            return .suppressed
        }
        if asPinned {
            guard let peer = transaction.getPeer(peerId), apiInputPeer(peer) != nil else { return .suppressed }
            return .request(LocalStoryViewReceiptRequest(kind: .increment, peer: peer, storyId: id, suppressionGeneration: settings.suppressionGeneration))
        }
        if let state = transaction.getPeerStoryState(peerId: peerId)?.entry.get(Stories.PeerState.self) {
            transaction.setPeerStoryState(peerId: peerId, state: Stories.PeerState(maxReadId: max(state.maxReadId, id)).postboxRepresentation)
        }
        _internal_addSynchronizeViewStoriesOperation(peerId: peerId, storyId: id, transaction: transaction)
        return .localRead
    }
    |> mapToSignal { preparation -> Signal<Never, NoError> in
        switch preparation {
        case .suppressed:
            return .complete()
        case .localRead:
            injectReadUpdate(peerId, id)
            return .complete()
        case let .request(request):
            return dispatchLocalStoryViewReceipt(postbox: postbox, request: request, send: send, queue: queue)
            |> ignoreValues
        }
    }
}
