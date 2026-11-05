// SPDX-License-Identifier: GPL-2.0-or-later
import TelegramCore

/// Immutable filtering inputs handed to the background evaluator. A different
/// message revision, author, caption or media classification cannot inherit it.
public struct ChatLocalTextFilterSnapshot {
    public let id: EngineMessage.Id
    public let stableVersion: UInt32
    public let text: String
    public let authorId: EnginePeer.Id?
    public let mediaType: LocalMessageFilterMediaType?

    public init(message: EngineRawMessage) {
        self.id = message.id
        self.stableVersion = message.stableVersion
        self.text = message.text
        self.authorId = message.author?.id
        self.mediaType = localMessageFilterMediaType(message)
    }

    public func matches(_ message: EngineRawMessage) -> Bool {
        return self.id == message.id && self.stableVersion == message.stableVersion && self.text == message.text
            && self.authorId == message.author?.id && self.mediaType == localMessageFilterMediaType(message)
    }

    /// Shared production bridge for background evaluation. The caller must
    /// first apply the ordinary-message eligibility boundary below.
    public func evaluate(using evaluator: LocalMessageFilterEvaluator, senderIsLocallyBlocked: Bool = false) -> LocalMessageFilterEvaluation {
        return evaluator.evaluate(text: self.text, peerId: self.id.peerId.toInt64(), mediaType: self.mediaType, senderIsLocallyBlocked: senderIsLocallyBlocked, senderId: self.authorId?.toInt64())
    }
}

/// Ordinary incoming text and single generic-bubble media are supported. Keep
/// secret, protected, timed, grouped, rich and unknown content visible. This
/// projection never suppresses expiry, consumes media or changes message data.
public func chatHistoryMessageSupportsLocalTextFiltering(_ message: EngineRawMessage) -> Bool {
    let peerNamespace = message.id.peerId.namespace
    guard message.id.namespace == Namespaces.Message.Cloud, message.id.id > 0,
          peerNamespace == Namespaces.Peer.CloudUser || peerNamespace == Namespaces.Peer.CloudGroup || peerNamespace == Namespaces.Peer.CloudChannel,
          message.flags.contains(.Incoming), !message.flags.contains(.CopyProtected),
          (!message.text.isEmpty || localMessageFilterMediaType(message) != nil),
          (message.media.isEmpty || localMessageFilterMediaType(message) != nil), message.forwardInfo == nil,
          message.groupingKey == nil, message.groupInfo == nil else { return false }
    return message.attributes.allSatisfy { attribute in
        attribute is TextEntitiesMessageAttribute || attribute is EditedMessageAttribute
            || attribute is ViewCountMessageAttribute || attribute is ForwardCountMessageAttribute
            || attribute is AuthorSignatureMessageAttribute || attribute is NotificationInfoMessageAttribute
            || attribute is ConsumablePersonalMentionMessageAttribute || attribute is ReportDeliveryMessageAttribute
            || attribute is ConsumableContentMessageAttribute
    }
}

/// The pure projection preserves every row, original message, stable id and index.
/// It never promotes a sticker/large-emoji node to a bubble under the same identity.
public func chatHistoryApplyingLocalTextFilter(_ entries: [ChatHistoryEntry], matches: [EngineMessage.Id: ChatLocalTextFilterSnapshot], revealed: Bool, selectionActive: Bool = false) -> [ChatHistoryEntry] {
    return entries.map { entry in
        guard case let .MessageEntry(message, presentation, read, location, selection, originalAttributes) = entry else {
            return entry
        }
        var attributes = originalAttributes
        attributes.localTextFilter = .none
        if matches[message.id]?.matches(message) == true, chatHistoryMessageSupportsLocalTextFiltering(message),
           case .generic = attributes.contentTypeHint {
            let selected: Bool
            if case .selectable(selected: true, num: _) = selection {
                selected = true
            } else {
                selected = false
            }
            // A selected canonical message must never be hidden while the
            // existing selection toolbar can still copy or forward it.
            attributes.localTextFilter = revealed || selectionActive || selected ? .revealed : .hidden
        }
        return .MessageEntry(message, presentation, read, location, selection, attributes)
    }
}
