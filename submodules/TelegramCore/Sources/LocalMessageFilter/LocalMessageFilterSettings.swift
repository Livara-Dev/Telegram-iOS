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
    public static let maximumRegularExpressionSteps = 1_000_000
    public static let maximumObservedAuthors = 512
    public static let maximumHiddenSenders = 1024
    // The maximum rules, exclusions and hidden senders fit below this bound.
    static let maximumEncodedSettingsBytes = 128 * 1024
}

public enum LocalMessageFilterMatchingMode: Int32, Codable {
    case literal = 0
    /// Finite-state subset: no backreferences, groups, look-around or recursion.
    case regularExpression = 1
}

public enum LocalMessageFilterMediaType: Int32, Codable, CaseIterable {
    case photo = 0
    case video = 1
    case gif = 2
    case voiceMessage = 3
    case videoMessage = 4
    case document = 5
}

public struct LocalMessageFilterRule: Codable, Equatable {
    public let id: String
    public let text: String
    public let caseSensitive: Bool
    /// Nil applies globally. Otherwise the rule applies only to this local peer ID.
    public let peerId: Int64?
    public let matchingMode: LocalMessageFilterMatchingMode
    /// A media-only rule. Text/case/regex fields must be empty/default so a
    /// stored rule never silently changes between caption and type semantics.
    public let mediaType: LocalMessageFilterMediaType?
    /// Hide a proven non-match, never unknown, unsupported or skipped content.
    public let isInverted: Bool

    public var isSupported: Bool { validLocalMessageFilterRule(self) }

    public init(id: String = UUID().uuidString, text: String, caseSensitive: Bool = false, peerId: Int64? = nil, matchingMode: LocalMessageFilterMatchingMode = .literal, mediaType: LocalMessageFilterMediaType? = nil, isInverted: Bool = false) {
        self.id = id
        self.text = text
        self.caseSensitive = caseSensitive
        self.peerId = peerId
        self.matchingMode = matchingMode
        self.mediaType = mediaType
        self.isInverted = isInverted
    }

    private enum CodingKeys: String, CodingKey { case id, text, caseSensitive, peerId, matchingMode, mediaType, isInverted }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.text = try container.decode(String.self, forKey: .text)
        self.caseSensitive = try container.decode(Bool.self, forKey: .caseSensitive)
        self.peerId = try container.decodeIfPresent(Int64.self, forKey: .peerId)
        self.matchingMode = try container.decodeIfPresent(LocalMessageFilterMatchingMode.self, forKey: .matchingMode) ?? .literal
        self.mediaType = try container.decodeIfPresent(LocalMessageFilterMediaType.self, forKey: .mediaType)
        self.isInverted = try container.decodeIfPresent(Bool.self, forKey: .isInverted) ?? false
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(self.id, forKey: .id)
        try container.encode(self.text, forKey: .text)
        try container.encode(self.caseSensitive, forKey: .caseSensitive)
        try container.encodeIfPresent(self.peerId, forKey: .peerId)
        // Preserve the literal-rule representation and its persistence budget.
        if self.matchingMode != .literal { try container.encode(self.matchingMode, forKey: .matchingMode) }
        try container.encodeIfPresent(self.mediaType, forKey: .mediaType)
        if self.isInverted { try container.encode(true, forKey: .isInverted) }
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
    public let hideBlockedSenders: Bool
    /// Encoded peer identities, separate from Telegram's server block list.
    public let hiddenSenderIds: Set<Int64>

    public init(enabled: Bool = false, rules: [LocalMessageFilterRule] = [], excludedPeerIds: Set<Int64> = [], hideBlockedSenders: Bool = false, hiddenSenderIds: Set<Int64> = []) {
        self.schemaVersion = 3
        self.enabled = enabled
        self.rules = rules
        self.excludedPeerIds = excludedPeerIds
        self.hideBlockedSenders = hideBlockedSenders
        self.hiddenSenderIds = hiddenSenderIds
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, enabled, rules, excludedPeerIds, hideBlockedSenders, hiddenSenderIds
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let version = try container.decode(Int32.self, forKey: .schemaVersion)
        self.enabled = try container.decode(Bool.self, forKey: .enabled)
        self.rules = try container.decode([LocalMessageFilterRule].self, forKey: .rules)
        self.excludedPeerIds = Set(try container.decode([Int64].self, forKey: .excludedPeerIds))
        self.hideBlockedSenders = try container.decodeIfPresent(Bool.self, forKey: .hideBlockedSenders) ?? false
        self.hiddenSenderIds = Set(try container.decodeIfPresent([Int64].self, forKey: .hiddenSenderIds) ?? [])
        if version == 1 && (self.hideBlockedSenders || self.rules.contains(where: { $0.matchingMode != .literal || $0.mediaType != nil })) {
            throw DecodingError.dataCorruptedError(forKey: .schemaVersion, in: container, debugDescription: "Version-1 rules must remain literal")
        }
        if version <= 2 && (!self.hiddenSenderIds.isEmpty || self.rules.contains(where: { $0.isInverted })) {
            throw DecodingError.dataCorruptedError(forKey: .schemaVersion, in: container, debugDescription: "Legacy policies cannot contain sender hiding or inverse rules")
        }
        // Read old policies without rewriting their bytes. The next explicit
        // change writes version 3, which older clients treat as read-only.
        self.schemaVersion = version == 1 || version == 2 ? 3 : version
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(self.schemaVersion, forKey: .schemaVersion)
        try container.encode(self.enabled, forKey: .enabled)
        try container.encode(self.rules, forKey: .rules)
        try container.encode(self.excludedPeerIds.sorted(), forKey: .excludedPeerIds)
        if self.hideBlockedSenders { try container.encode(true, forKey: .hideBlockedSenders) }
        if !self.hiddenSenderIds.isEmpty { try container.encode(self.hiddenSenderIds.sorted(), forKey: .hiddenSenderIds) }
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
    if let peerId = rule.peerId, !validLocalMessageFilterPeerId(peerId) { return false }
    if rule.mediaType != nil {
        return rule.text.isEmpty && !rule.caseSensitive && rule.matchingMode == .literal
    }
    let textLength = rule.text.utf16.prefix(LocalMessageFilterLimits.maximumLiteralUTF16Length + 1).count
    guard textLength > 0 && textLength <= LocalMessageFilterLimits.maximumLiteralUTF16Length else { return false }
    if rule.matchingMode == .regularExpression {
        return LocalMessageFilterRegularExpression(pattern: rule.text, caseSensitive: rule.caseSensitive) != nil
    }
    return true
}

func validLocalMessageFilterSettings(_ settings: LocalMessageFilterSettings) -> Bool {
    guard settings.schemaVersion == 3, settings.rules.count <= LocalMessageFilterLimits.maximumRules,
          settings.excludedPeerIds.count <= LocalMessageFilterLimits.maximumExcludedPeers,
          settings.excludedPeerIds.allSatisfy(validLocalMessageFilterPeerId),
          settings.hiddenSenderIds.count <= LocalMessageFilterLimits.maximumHiddenSenders,
          settings.hiddenSenderIds.allSatisfy(validLocalMessageFilterSenderId) else { return false }
    var seenIds = Set<String>()
    for rule in settings.rules {
        guard validLocalMessageFilterRule(rule), seenIds.insert(rule.id).inserted else { return false }
    }
    return true
}

func validLocalMessageFilterPeerId(_ value: Int64) -> Bool {
    // PeerId's decoder asserts on impossible high identifier bits. Reject
    // untrusted preference values before invoking that invariant-bearing init.
    // Preserve all safely decodable namespaces and the legacy empty sentinel;
    // message eligibility remains a separate, stricter cloud-message boundary.
    if value == 0x7fffffff00000000 { return true }
    guard value >= 0, UInt64(bitPattern: value) >> 35 <= 0x00ffffff else { return false }
    // Namespace.max/id.zero has a special sentinel representation; the normal
    // bit layout would fail PeerId's round-trip assertion for this one value.
    return value != 0x0000000700000000
}

func validLocalMessageFilterSenderId(_ value: Int64) -> Bool {
    guard value > 0, validLocalMessageFilterPeerId(value) else { return false }
    let id = PeerId(value)
    return (id.namespace == Namespaces.Peer.CloudUser || id.namespace == Namespaces.Peer.CloudChannel)
        && id.id._internalGetInt64Value() > 0
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
