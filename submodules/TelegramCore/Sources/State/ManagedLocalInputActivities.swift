// Modified for AyuGram iOS.
// Modification dates recorded in Git history: 2026-11-04.
// See this file's history for the corresponding changes.
import Foundation
import Postbox
import SwiftSignalKit
internal import TelegramApi
import MtProtoKit


public struct PeerActivitySpace: Hashable {
    public enum Category: Equatable, Hashable {
        case global
        case thread(Int64)
        case voiceChat
    }
    
    public var peerId: PeerId
    public var category: Category
    
    public init(peerId: PeerId, category: Category) {
        self.peerId = peerId
        self.category = category
    }
}

struct PeerInputActivityRecord: Equatable {
    let activity: PeerInputActivity
    let updateId: Int32
}

/// Typed dispatch boundary shared by the production network sender and integration
/// tests. It contains no replacement transport, authentication or fabricated response.
enum LocalInputActivityRequest {
    case cloud(flags: Int32, peer: Api.InputPeer, topMessageId: Int32?, action: Api.SendMessageAction)
    case secret(peer: Api.InputEncryptedChat, typing: Api.Bool)
}

typealias LocalInputActivityRequestSender = (LocalInputActivityRequest) -> Signal<Void, NoError>

private final class ManagedLocalTypingActivitiesContext {
    private var disposables: [PeerActivitySpace: (PeerInputActivityRecord, MetaDisposable)] = [:]
    
    func update(activities: [PeerActivitySpace: [(PeerId, PeerInputActivityRecord)]]) -> (start: [(PeerActivitySpace, PeerInputActivityRecord?, MetaDisposable)], dispose: [MetaDisposable]) {
        var start: [(PeerActivitySpace, PeerInputActivityRecord?, MetaDisposable)] = []
        var dispose: [MetaDisposable] = []
        
        var validPeerIds = Set<PeerActivitySpace>()
        for (peerId, record) in activities {
            if let activity = record.first?.1 {
                validPeerIds.insert(peerId)
                
                let currentRecord = self.disposables[peerId]
                if currentRecord == nil || currentRecord!.0 != activity {
                    if let disposable = currentRecord?.1 {
                        dispose.append(disposable)
                    }
                    
                    let disposable = MetaDisposable()
                    start.append((peerId, activity, disposable))
                    
                    self.disposables[peerId] = (activity, disposable)
                }
            }
        }
        
        var removePeerIds: [PeerActivitySpace] = []
        for key in self.disposables.keys {
            if !validPeerIds.contains(key) {
                removePeerIds.append(key)
            }
        }
        
        for peerId in removePeerIds {
            dispose.append(self.disposables[peerId]!.1)
            self.disposables.removeValue(forKey: peerId)
        }
        
        return (start, dispose)
    }
    
    func dispose() {
        for (_, record) in self.disposables {
            record.1.dispose()
        }
        self.disposables.removeAll()
    }
}

func managedLocalTypingActivities(activities: Signal<[PeerActivitySpace: [(PeerId, PeerInputActivityRecord)]], NoError>, postbox: Postbox, network: Network, accountPeerId: PeerId) -> Signal<Void, NoError> {
    return managedLocalTypingActivities(activities: activities, postbox: postbox, accountPeerId: accountPeerId, sendRequest: { request in
        let signal: Signal<Api.Bool, MTRpcError>
        switch request {
        case let .cloud(flags, peer, topMessageId, action):
            signal = network.request(Api.functions.messages.setTyping(flags: flags, peer: peer, topMsgId: topMessageId, action: action))
        case let .secret(peer, typing):
            signal = network.request(Api.functions.messages.setEncryptedTyping(peer: peer, typing: typing))
        }
        return signal
        |> `catch` { _ -> Signal<Api.Bool, NoError> in
            return .single(.boolFalse)
        }
        |> mapToSignal { _ -> Signal<Void, NoError> in
            return .complete()
        }
    })
}

func managedLocalTypingActivities(activities: Signal<[PeerActivitySpace: [(PeerId, PeerInputActivityRecord)]], NoError>, postbox: Postbox, accountPeerId: PeerId, queue: Queue = Queue(), sendRequest: @escaping LocalInputActivityRequestSender) -> Signal<Void, NoError> {
    return Signal { _ in
        let context = ManagedLocalTypingActivitiesContext()
        var stopped = false
        // Activities and preferences originate on different queues. Serialize the
        // entire update/dispose/start sequence, not just the context dictionary.
        let disposable = (combineLatest(activities, localGhostSettingsSignal(postbox: postbox))
        |> deliverOn(queue)).start(next: { activities, settings in
            guard !stopped else { return }
            var permitted: [PeerActivitySpace: [(PeerId, PeerInputActivityRecord)]] = [:]
            for (space, records) in activities {
                // Preserve upstream priority: suppressing the selected first record
                // must not promote another peer/activity in the same space.
                if let first = records.first, localGhostAllowsActivity(first.1.activity, settings: settings) {
                    permitted[space] = records
                }
            }
            let (start, dispose) = context.update(activities: permitted)
            
            for disposable in dispose {
                disposable.dispose()
            }
            
            for (peerId, activity, disposable) in start {
                var threadId: Int64?
                switch peerId.category {
                case let .thread(id):
                    threadId = id
                default:
                    break
                }
                disposable.set(requestLocalInputActivity(postbox: postbox, accountPeerId: accountPeerId, peerId: peerId.peerId, threadId: threadId, activity: activity?.activity, sendRequest: sendRequest).start())
            }
        })
        return ActionDisposable {
            disposable.dispose()
            
            queue.async {
                stopped = true
                context.dispose()
            }
        }
    }
}

private func actionFromActivity(_ activity: PeerInputActivity?) -> Api.SendMessageAction {
    if let activity = activity {
        switch activity {
            case .typingText:
                return .sendMessageTypingAction
            case .recordingVoice:
                return .sendMessageRecordAudioAction
            case .playingGame:
                return .sendMessageGamePlayAction
            case let .uploadingFile(progress):
                return .sendMessageUploadDocumentAction(.init(progress: progress))
            case let .uploadingPhoto(progress):
                return .sendMessageUploadPhotoAction(.init(progress: progress))
            case let .uploadingVideo(progress):
                return .sendMessageUploadVideoAction(.init(progress: progress))
            case .recordingInstantVideo:
                return .sendMessageRecordRoundAction
            case let .uploadingInstantVideo(progress):
                return .sendMessageUploadRoundAction(.init(progress: progress))
            case .speakingInGroupCall:
                return .speakingInGroupCallAction
            case .choosingSticker:
                return .sendMessageChooseStickerAction
            case let .interactingWithEmoji(emoticon, messageId, interaction):
                return .sendMessageEmojiInteraction(.init(emoticon: emoticon, msgId: messageId.id, interaction: interaction?.apiDataJson ?? .dataJSON(.init(data: ""))))
            case let .seeingEmojiInteraction(emoticon):
                return .sendMessageEmojiInteractionSeen(.init(emoticon: emoticon))
        }
    } else {
        return .sendMessageCancelAction
    }
}

func requestLocalInputActivity(postbox: Postbox, accountPeerId: PeerId, peerId: PeerId, threadId: Int64?, activity: PeerInputActivity?, sendRequest: @escaping LocalInputActivityRequestSender) -> Signal<Void, NoError> {
    return postbox.transaction { transaction -> LocalInputActivityRequest? in
        // Recheck the stored policy after queueing: a stale activity snapshot must
        // not bypass a preference change committed before this transaction runs.
        guard localGhostAllowsActivity(activity, settings: localGhostSettings(transaction: transaction)) else {
            return nil
        }
        if let peer = transaction.getPeer(peerId) {
            if peerId == accountPeerId {
                return nil
            }
            if let channel = peer as? TelegramChannel, case .broadcast = channel.info {
                if let activity = activity {
                    switch activity {
                    case .speakingInGroupCall:
                        break
                    default:
                        return nil
                    }
                }
            }
            if let _ = peer as? TelegramUser {
                if let presence = transaction.getPeerPresence(peerId: peerId) as? TelegramUserPresence {
                    switch presence.status {
                    case .none, .lastWeek, .lastMonth:
                        return nil
                    case .recently:
                        break
                    case let .present(statusTimestamp):
                        let timestamp = Int32(CFAbsoluteTimeGetCurrent() + NSTimeIntervalSince1970)
                        if statusTimestamp < timestamp - 30 {
                            return nil
                        }
                    }
                } else {
                    return nil
                }
            }
            
            if let inputPeer = apiInputPeer(peer) {
                var flags: Int32 = 0
                let topMessageId = threadId.flatMap { Int32(clamping: $0) }
                if topMessageId != nil {
                    flags |= 1 << 0
                }
                return .cloud(flags: flags, peer: inputPeer, topMessageId: topMessageId, action: actionFromActivity(activity))
            } else if let peer = peer as? TelegramSecretChat, activity == .typingText {
                let _ = PeerId(peer.id.toInt64())
                return .secret(peer: .inputEncryptedChat(.init(chatId: Int32(peer.id.id._internalGetInt64Value()), accessHash: peer.accessHash)), typing: .boolTrue)
            } else {
                return nil
            }
        } else {
            return nil
        }
    }
    |> mapToSignal { request -> Signal<Void, NoError> in
        guard let request else {
            return .complete()
        }
        // Disposing cancels in-flight work where possible, but cannot retract a
        // request already transmitted before the user enabled suppression.
        return sendRequest(request)
    }
}
