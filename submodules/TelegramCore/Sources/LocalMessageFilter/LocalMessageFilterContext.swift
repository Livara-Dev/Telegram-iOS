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
            LocalMessageFilterSettings(enabled: enabled, rules: current.rules, excludedPeerIds: current.excludedPeerIds)
        }
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
            return LocalMessageFilterSettings(enabled: current.enabled, rules: rules, excludedPeerIds: current.excludedPeerIds)
        }
    }

    public func removeRule(id: String) -> Signal<Bool, NoError> {
        return self.update { current in
            LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules.filter { $0.id != id }, excludedPeerIds: current.excludedPeerIds)
        }
    }

    public func setChatExcluded(peerId: Int64, excluded: Bool) -> Signal<Bool, NoError> {
        return self.update { current in
            var peers = current.excludedPeerIds
            if excluded {
                guard peers.contains(peerId) || peers.count < LocalMessageFilterLimits.maximumExcludedPeers else { return nil }
                peers.insert(peerId)
            } else {
                peers.remove(peerId)
            }
            return LocalMessageFilterSettings(enabled: current.enabled, rules: current.rules, excludedPeerIds: peers)
        }
    }
}
