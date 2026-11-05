// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit

/// Account-local display rules. These transactions never alter messages, queued
/// Telegram operations, server preferences, networking or archive contents.
public final class LocalMessageFilterContext {
    private let postbox: Postbox

    init(postbox: Postbox) {
        self.postbox = postbox
    }

    public var settings: Signal<LocalMessageFilterSettingsState, NoError> {
        return localMessageFilterSettingsSignal(postbox: self.postbox)
    }

    private func update(_ transform: @escaping (LocalMessageFilterSettings) -> LocalMessageFilterSettings?) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            guard case let .available(current) = localMessageFilterSettings(transaction: transaction),
                  let updated = transform(current), validLocalMessageFilterSettings(updated),
                  let entry = PreferencesEntry(updated), entry.data.count <= LocalMessageFilterLimits.maximumEncodedSettingsBytes else {
                return false
            }
            transaction.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry)
            return true
        }
    }

    public func setEnabled(_ enabled: Bool) -> Signal<Bool, NoError> {
        return self.update { current in
            LocalMessageFilterSettings(enabled: enabled, rules: current.rules, excludedPeerIds: current.excludedPeerIds, hideBlockedSenders: current.hideBlockedSenders, hiddenSenderIds: current.hiddenSenderIds)
        }
    }

    public func reset() -> Signal<Bool, NoError> {
        return self.update { _ in LocalMessageFilterSettings() }
    }

    public func setHideBlockedSenders(_ hide: Bool) -> Signal<Bool, NoError> {
        return self.update { current in
            LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules, excludedPeerIds: current.excludedPeerIds, hideBlockedSenders: hide, hiddenSenderIds: current.hiddenSenderIds)
        }
    }

    /// A local presentation preference only: no block/unblock RPC, history
    /// deletion or archive exclusion is performed. Global-chat exceptions apply.
    public func setSenderHidden(senderId: EnginePeer.Id, hidden: Bool) -> Signal<Bool, NoError> {
        guard (senderId.namespace == Namespaces.Peer.CloudUser || senderId.namespace == Namespaces.Peer.CloudChannel),
              senderId.id._internalGetInt64Value() > 0 else { return .single(false) }
        return self.update { current in
            let value = senderId.toInt64()
            guard validLocalMessageFilterSenderId(value) else { return nil }
            var ids = current.hiddenSenderIds
            if hidden {
                guard ids.contains(value) || ids.count < LocalMessageFilterLimits.maximumHiddenSenders else { return nil }
                ids.insert(value)
            } else {
                ids.remove(value)
            }
            return LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules, excludedPeerIds: current.excludedPeerIds, hideBlockedSenders: current.hideBlockedSenders, hiddenSenderIds: ids)
        }
    }

    /// Observe only local cached block metadata for the current history window.
    /// This makes no peer fetch/block request. Missing/oversized metadata remains
    /// visible; block state from another Telegram session is known after sync.
    public func locallyBlockedAuthors(_ peerIds: [EnginePeer.Id]) -> Signal<Set<EnginePeer.Id>, NoError> {
        guard peerIds.count <= LocalMessageFilterLimits.maximumObservedAuthors else { return .single([]) }
        let ids = Set(peerIds.filter { $0.namespace == Namespaces.Peer.CloudUser })
        guard !ids.isEmpty else { return .single([]) }
        let keys = ids.map { PostboxViewKey.cachedPeerData(peerId: $0) }
        return self.postbox.combinedView(keys: keys)
        |> map { view in
            var blocked = Set<EnginePeer.Id>()
            for id in ids {
                if let cached = (view.views[.cachedPeerData(peerId: id)] as? CachedPeerDataView)?.cachedPeerData as? CachedUserData, cached.isBlocked {
                    blocked.insert(id)
                }
            }
            return blocked
        }
        |> distinctUntilChanged
    }

    public func upsertRule(_ rule: LocalMessageFilterRule) -> Signal<Bool, NoError> {
        return self.update { current in
            guard validLocalMessageFilterRule(rule) else { return nil }
            var rules = current.rules
            if let index = rules.firstIndex(where: { $0.id == rule.id }) {
                rules[index] = rule
            } else {
                guard rules.count < LocalMessageFilterLimits.maximumRules else { return nil }
                rules.append(rule)
            }
            return LocalMessageFilterSettings(enabled: current.enabled, rules: rules, excludedPeerIds: current.excludedPeerIds, hideBlockedSenders: current.hideBlockedSenders, hiddenSenderIds: current.hiddenSenderIds)
        }
    }

    public func removeRule(id: String) -> Signal<Bool, NoError> {
        return self.update { current in
            LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules.filter { $0.id != id }, excludedPeerIds: current.excludedPeerIds, hideBlockedSenders: current.hideBlockedSenders, hiddenSenderIds: current.hiddenSenderIds)
        }
    }

    public func setChatExcluded(peerId: Int64, excluded: Bool) -> Signal<Bool, NoError> {
        return self.update { current in
            guard validLocalMessageFilterPeerId(peerId) else { return nil }
            var peers = current.excludedPeerIds
            if excluded {
                guard peers.contains(peerId) || peers.count < LocalMessageFilterLimits.maximumExcludedPeers else { return nil }
                peers.insert(peerId)
            } else {
                peers.remove(peerId)
            }
            return LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules, excludedPeerIds: peers, hideBlockedSenders: current.hideBlockedSenders, hiddenSenderIds: current.hiddenSenderIds)
        }
    }
}
