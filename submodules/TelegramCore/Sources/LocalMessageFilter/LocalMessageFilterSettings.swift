// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

public enum LocalMessageFilterLimits {
    public static let maximumRules = 64
    public static let maximumLiteralUTF16Length = 256
    public static let maximumRuleIdUTF8Length = 64
    public static let maximumExcludedPeers = 4096
    public static let maximumInputUTF16Length = 32768
    // A maximum-sized version-1 document fits below this bound, including
    // three-byte BMP characters and every excluded peer's Int64 representation.
    static let maximumEncodedSettingsBytes = 128 * 1024
}

public struct LocalMessageFilterRule: Codable, Equatable {
    public let id: String
    public let text: String
    public let caseSensitive: Bool
    /// Nil applies globally. Otherwise the rule applies only to this local peer ID.
    public let peerId: Int64?

    public init(id: String = UUID().uuidString, text: String, caseSensitive: Bool = false, peerId: Int64? = nil) {
        self.id = id
        self.text = text
        self.caseSensitive = caseSensitive
        self.peerId = peerId
    }
}

/// Immutable snapshots may be evaluated off the main thread. They contain only
/// local display preferences and are never synchronized through Telegram.
public struct LocalMessageFilterSettings: Codable, Equatable {
    let schemaVersion: Int32
    public let enabled: Bool
    public let rules: [LocalMessageFilterRule]
    /// Skip global rules in these chats. Explicit rules for the chat still apply.
    public let excludedPeerIds: Set<Int64>

    public init(enabled: Bool = false, rules: [LocalMessageFilterRule] = [], excludedPeerIds: Set<Int64> = []) {
        self.schemaVersion = 1
        self.enabled = enabled
        self.rules = rules
        self.excludedPeerIds = excludedPeerIds
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, enabled, rules, excludedPeerIds
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try container.decode(Int32.self, forKey: .schemaVersion)
        self.enabled = try container.decode(Bool.self, forKey: .enabled)
        self.rules = try container.decode([LocalMessageFilterRule].self, forKey: .rules)
        self.excludedPeerIds = Set(try container.decode([Int64].self, forKey: .excludedPeerIds))
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(self.schemaVersion, forKey: .schemaVersion)
        try container.encode(self.enabled, forKey: .enabled)
        try container.encode(self.rules, forKey: .rules)
        try container.encode(self.excludedPeerIds.sorted(), forKey: .excludedPeerIds)
    }
}

public enum LocalMessageFilterSettingsState: Equatable {
    case available(LocalMessageFilterSettings)
    /// Keep the original bytes and display content normally until a compatible
    /// client can interpret the preferences. This is not a secrecy boundary.
    case unsupported
}

let localMessageFilterSettingsKey = ValueBoxKey("ayugram.message-filter.v1")

func validLocalMessageFilterRule(_ rule: LocalMessageFilterRule) -> Bool {
    let idLength = rule.id.utf8.prefix(LocalMessageFilterLimits.maximumRuleIdUTF8Length + 1).count
    guard idLength > 0, idLength <= LocalMessageFilterLimits.maximumRuleIdUTF8Length, !rule.id.contains("\0") else { return false }
    let textLength = rule.text.utf16.prefix(LocalMessageFilterLimits.maximumLiteralUTF16Length + 1).count
    return textLength > 0 && textLength <= LocalMessageFilterLimits.maximumLiteralUTF16Length
}

func validLocalMessageFilterSettings(_ settings: LocalMessageFilterSettings) -> Bool {
    guard settings.schemaVersion == 1, settings.rules.count <= LocalMessageFilterLimits.maximumRules,
          settings.excludedPeerIds.count <= LocalMessageFilterLimits.maximumExcludedPeers else { return false }
    var seenIds = Set<String>()
    for rule in settings.rules {
        guard validLocalMessageFilterRule(rule), seenIds.insert(rule.id).inserted else { return false }
    }
    return true
}

func localMessageFilterSettings(entry: PreferencesEntry?) -> LocalMessageFilterSettingsState {
    guard let entry else { return .available(LocalMessageFilterSettings()) }
    guard entry.data.count <= LocalMessageFilterLimits.maximumEncodedSettingsBytes,
          let settings = entry.get(LocalMessageFilterSettings.self), validLocalMessageFilterSettings(settings) else {
        return .unsupported
    }
    return .available(settings)
}

func localMessageFilterSettings(transaction: Transaction) -> LocalMessageFilterSettingsState {
    return localMessageFilterSettings(entry: transaction.getPreferencesEntry(key: localMessageFilterSettingsKey))
}

func localMessageFilterSettingsSignal(postbox: Postbox) -> Signal<LocalMessageFilterSettingsState, NoError> {
    return postbox.preferencesView(keys: [localMessageFilterSettingsKey])
    |> map { localMessageFilterSettings(entry: $0.values[localMessageFilterSettingsKey]) }
    |> distinctUntilChanged
}
