// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
internal import TelegramApi
import SwiftSignalKit

let localGhostReadAfterSendKey = ValueBoxKey("ayugram.ghost.read-after-send.v1")
private let localGhostReadAfterSendLimit = 128
private let localGhostReadAfterSendLifetime: Int64 = 15 * 60

public struct LocalGhostReadAfterSendSettings: Equatable {
    public let enabled: Bool
}

public enum LocalGhostReadAfterSendSettingsState: Equatable {
    case available(LocalGhostReadAfterSendSettings)
    case unsupported
}

/// A local permission for one outgoing random id and one pre-send read bound.
/// It is never attached to a Telegram message or transmitted over the network.
struct LocalGhostReadAfterSendIntent: Codable, Equatable {
    let accountId: Int64
    let peerId: Int64
    let uniqueId: Int64
    let maximumId: Int32
    let generation: Int64
    let createdAt: Int64
}

struct LocalGhostReadAfterSendRecord: Codable, Equatable {
    var schemaVersion: Int32 = 1
    var enabled: Bool = false
    var generation: Int64 = 0
    var intents: [LocalGhostReadAfterSendIntent] = []
}

func localGhostReadAfterSendRecord(transaction: Transaction) -> LocalGhostReadAfterSendRecord? {
    guard let entry = transaction.getPreferencesEntry(key: localGhostReadAfterSendKey) else {
        return LocalGhostReadAfterSendRecord()
    }
    guard entry.data.count <= 64 * 1024,
          let value = entry.get(LocalGhostReadAfterSendRecord.self), value.schemaVersion == 1,
          value.generation >= 0, value.intents.count <= localGhostReadAfterSendLimit,
          Set(value.intents.map { $0.uniqueId }).count == value.intents.count,
          value.intents.allSatisfy({ $0.maximumId > 0 && $0.createdAt >= 0 && $0.generation == value.generation }) else { return nil }
    return value
}

func localGhostReadAfterSendSettingsSignal(postbox: Postbox) -> Signal<LocalGhostReadAfterSendSettingsState, NoError> {
    return postbox.preferencesView(keys: [localGhostReadAfterSendKey])
    |> map { view -> LocalGhostReadAfterSendSettingsState in
        guard let entry = view.values[localGhostReadAfterSendKey] else {
            return .available(LocalGhostReadAfterSendSettings(enabled: false))
        }
        guard entry.data.count <= 64 * 1024,
              let record = entry.get(LocalGhostReadAfterSendRecord.self), record.schemaVersion == 1,
              record.generation >= 0, record.intents.count <= localGhostReadAfterSendLimit,
              Set(record.intents.map { $0.uniqueId }).count == record.intents.count,
              record.intents.allSatisfy({ $0.maximumId > 0 && $0.createdAt >= 0 && $0.generation == record.generation }) else { return .unsupported }
        return .available(LocalGhostReadAfterSendSettings(enabled: record.enabled))
    }
    |> distinctUntilChanged
}

/// Any policy transition revokes in-flight permissions. A disable/re-enable
/// cannot revive an old intent. Unknown records remain read-only and fail closed.
@discardableResult
func invalidateLocalGhostReadAfterSend(transaction: Transaction, enabled: Bool? = nil) -> Bool {
    guard var record = localGhostReadAfterSendRecord(transaction: transaction), record.generation < Int64.max else { return false }
    record.generation += 1
    record.intents.removeAll()
    if let enabled { record.enabled = enabled }
    guard let entry = PreferencesEntry(record) else { return false }
    transaction.setPreferencesEntry(key: localGhostReadAfterSendKey, value: entry)
    return true
}

private func localGhostReadAfterSendIsOrdinary(peer: Peer) -> Bool {
    guard peer.id.namespace == Namespaces.Peer.CloudUser || peer.id.namespace == Namespaces.Peer.CloudGroup || peer.id.namespace == Namespaces.Peer.CloudChannel else { return false }
    return !peer.isForumOrMonoForum && peerKeepsReadState(peer)
}

func localGhostReadAfterSendIsOrdinary(message: EnqueueMessage) -> Bool {
    switch message {
    case let .message(_, attributes, _, mediaReference, threadId, _, replyToStoryId, _, _, _):
        guard threadId == nil, replyToStoryId == nil, !(mediaReference?.media is TelegramMediaAction) else { return false }
        return !attributes.contains(where: {
            $0 is OutgoingScheduleInfoMessageAttribute || $0 is OutgoingQuickReplyMessageAttribute ||
            $0 is AutoremoveTimeoutMessageAttribute || $0 is AutoclearTimeoutMessageAttribute
        })
    case let .forward(_, threadId, _, attributes, _):
        return threadId == nil && !attributes.contains(where: {
            $0 is OutgoingScheduleInfoMessageAttribute || $0 is OutgoingQuickReplyMessageAttribute ||
            $0 is AutoremoveTimeoutMessageAttribute || $0 is AutoclearTimeoutMessageAttribute
        })
    }
}

/// Called in the enqueue transaction BEFORE outgoing messages are inserted.
/// One intent is scoped to an account, chat, random id and existing Cloud max id.
func captureLocalGhostReadAfterSend(transaction: Transaction, accountPeerId: PeerId, peerId: PeerId,
                                    uniqueId: Int64, message: EnqueueMessage,
                                    now: Int64 = Int64(Date().timeIntervalSince1970)) {
    guard localGhostReadAfterSendIsOrdinary(message: message),
          let peer = transaction.getPeer(peerId), localGhostReadAfterSendIsOrdinary(peer: peer),
          case let .available(ghost) = localGhostSettings(transaction: transaction), ghost.enabled && ghost.suppressReadReceipts,
          var record = localGhostReadAfterSendRecord(transaction: transaction), record.enabled,
          let index = transaction.getTopPeerMessageIndex(peerId: peerId, namespace: Namespaces.Message.Cloud), index.id.id > 0 else { return }
    record.intents.removeAll(where: { $0.uniqueId == uniqueId || $0.createdAt > now || now - $0.createdAt > localGhostReadAfterSendLifetime })
    if record.intents.count >= localGhostReadAfterSendLimit {
        record.intents.removeFirst(record.intents.count - localGhostReadAfterSendLimit + 1)
    }
    record.intents.append(LocalGhostReadAfterSendIntent(accountId: accountPeerId.toInt64(), peerId: peerId.toInt64(),
        uniqueId: uniqueId, maximumId: index.id.id, generation: record.generation, createdAt: now))
    guard let entry = PreferencesEntry(record) else { return }
    transaction.setPreferencesEntry(key: localGhostReadAfterSendKey, value: entry)
}

struct LocalGhostConfirmedSend: Equatable {
    let uniqueId: Int64
    let deliveredId: MessageId
}

struct LocalGhostReadAfterSendScope: Equatable {
    let accountPeerId: PeerId
    let maximumMessageId: MessageId
}

/// Consume-before-dispatch: a crash can miss this optional receipt, but cannot
/// replay it after restart. Only actual mapped delivered ids authorize a read.
func consumeLocalGhostReadAfterSend(transaction: Transaction, accountPeerId: PeerId,
                                    confirmed: [LocalGhostConfirmedSend],
                                    now: Int64 = Int64(Date().timeIntervalSince1970)) -> [LocalGhostReadAfterSendScope] {
    guard case let .available(ghost) = localGhostSettings(transaction: transaction), ghost.enabled && ghost.suppressReadReceipts,
          var record = localGhostReadAfterSendRecord(transaction: transaction), record.enabled else { return [] }
    var maximumIds: [PeerId: Int32] = [:]
    for ack in confirmed {
        guard ack.deliveredId.namespace == Namespaces.Message.Cloud, ack.deliveredId.id > 0,
              let index = record.intents.firstIndex(where: { $0.uniqueId == ack.uniqueId }) else { continue }
        let intent = record.intents[index]
        guard intent.accountId == accountPeerId.toInt64(), intent.peerId == ack.deliveredId.peerId.toInt64(),
              intent.generation == record.generation, intent.createdAt <= now,
              now - intent.createdAt <= localGhostReadAfterSendLifetime,
              intent.maximumId < ack.deliveredId.id,
              let delivered = transaction.getMessage(ack.deliveredId), delivered.threadId == nil,
              !delivered.isSelfExpiring, delivered.flags.intersection([.Incoming, .Unsent, .Sending, .Failed]).isEmpty,
              let peer = transaction.getPeer(ack.deliveredId.peerId), localGhostReadAfterSendIsOrdinary(peer: peer) else { continue }
        record.intents.remove(at: index)
        maximumIds[ack.deliveredId.peerId] = max(maximumIds[ack.deliveredId.peerId] ?? 0, intent.maximumId)
    }
    record.intents.removeAll(where: { $0.createdAt > now || now - $0.createdAt > localGhostReadAfterSendLifetime })
    guard let entry = PreferencesEntry(record) else { return [] }
    transaction.setPreferencesEntry(key: localGhostReadAfterSendKey, value: entry)
    return maximumIds.map { peerId, maximumId in LocalGhostReadAfterSendScope(accountPeerId: accountPeerId,
        maximumMessageId: MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: maximumId)) }
}

/// The transaction is subscribed only AFTER applyUpdateMessage(s) has committed
/// actual Telegram ACK mappings. The final sender can be replaced in native
/// tests without replacing capture, validation, persistence or the ACK boundary.
func dispatchLocalGhostReadAfterSend(postbox: Postbox, accountPeerId: PeerId,
                                     confirmed: @escaping () -> [LocalGhostConfirmedSend],
                                     send: @escaping (LocalGhostReadAfterSendScope) -> Signal<Void, NoError>) -> Signal<Void, NoError> {
    return postbox.transaction { transaction in
        consumeLocalGhostReadAfterSend(transaction: transaction, accountPeerId: accountPeerId, confirmed: confirmed())
    }
    |> mapToSignal { scopes -> Signal<Void, NoError> in
        guard !scopes.isEmpty else { return .complete() }
        return combineLatest(scopes.map(send)) |> ignoreValues
    }
}

func readAfterLocalGhostSendCommit(postbox: Postbox, accountPeerId: PeerId,
                                  confirmed: @escaping () -> [LocalGhostConfirmedSend],
                                  send: @escaping (LocalGhostReadAfterSendScope) -> Signal<Void, NoError>,
                                  start: @escaping (Signal<Void, NoError>) -> Void) -> (Signal<Void, NoError>) -> Signal<Void, NoError> {
    return { commit in
        return commit |> afterCompleted {
            // Optional read work has separate account-owned lifetime and never
            // delays completion of the actual Telegram send operation.
            start(dispatchLocalGhostReadAfterSend(postbox: postbox, accountPeerId: accountPeerId, confirmed: confirmed, send: send))
        }
    }
}

/// Existing native read RPCs and real response PTS handling. This deliberately
/// bypasses only the general suppression gate for the one consumed permission;
/// it never changes Ghost preferences or grants another worker permission.
func sendLocalGhostReadAfterSend(postbox: Postbox, network: Network, stateManager: AccountStateManager,
                                scope: LocalGhostReadAfterSendScope) -> Signal<Void, NoError> {
    return postbox.transaction { transaction -> Api.InputPeer? in
        guard let peer = transaction.getPeer(scope.maximumMessageId.peerId), localGhostReadAfterSendIsOrdinary(peer: peer) else { return nil }
        return apiInputPeerOrSelf(peer, accountPeerId: scope.accountPeerId)
    }
    |> mapToSignal { inputPeer -> Signal<Void, NoError> in
        guard let inputPeer else { return .complete() }
        let successfulRead: Signal<Bool, NoError>
        switch inputPeer {
        case let .inputPeerChannel(data):
            successfulRead = network.request(Api.functions.channels.readHistory(channel: .inputChannel(.init(channelId: data.channelId, accessHash: data.accessHash)), maxId: scope.maximumMessageId.id), automaticFloodWait: false)
            |> map { value in if case .boolTrue = value { return true }; return false }
            |> `catch` { _ in .single(false) }
        default:
            successfulRead = network.request(Api.functions.messages.readHistory(peer: inputPeer, maxId: scope.maximumMessageId.id), automaticFloodWait: false)
            |> map { result in
                if case let .affectedMessages(data) = result {
                    stateManager.addUpdateGroups([.updatePts(pts: data.pts, ptsCount: data.ptsCount)])
                }
                return true
            }
            |> `catch` { _ in .single(false) }
        }
        return successfulRead |> mapToSignal { success -> Signal<Void, NoError> in
            guard success else { return .complete() }
            return _internal_applyMaxReadMessageIdInteractively(postbox: postbox, stateManager: stateManager, messageId: scope.maximumMessageId) |> ignoreValues
        }
    }
}
