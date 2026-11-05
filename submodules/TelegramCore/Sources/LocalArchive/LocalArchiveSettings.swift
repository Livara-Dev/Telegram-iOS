// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox

// Longer than the upstream four-byte preference keys; scoped to each Postbox.
public let localArchiveSettingsKey = ValueBoxKey("ayugram.archive1")

/// Experimental, account-local preferences for eligible message and cached-media capture.
public struct LocalArchiveSettings: Codable, Equatable {
    // Local read state, deliberately absent from CodingKeys. A placeholder for
    // unreadable preferences must never become an editable default configuration.
    private var sourceIsSupported = true
    // Postbox's adapted Codable format supports fixed-width integers; encoding an
    // Int deliberately asserts. Int32 also preserves the existing version-1 wire type.
    let schemaVersion: Int32
    public var enabled: Bool
    public var excludeBots: Bool
    public var excludedPeerIds: Set<Int64>
    public var excludedSenderIds: Set<Int64>
    public var maxAgeSeconds: Int64
    public var maxPayloadBytes: Int64
    public internal(set) var generation: Int64
    public internal(set) var pendingPurge: Bool
    public internal(set) var droppedCaptures: Int64

    /// False means an existing preference entry cannot be interpreted safely.
    /// Keep its original bytes until a compatible version can migrate it.
    public var isSupported: Bool {
        return self.sourceIsSupported && self.schemaVersion == 1
            && self.generation >= 0 && self.droppedCaptures >= 0
            && self.maxAgeSeconds > 0 && self.maxPayloadBytes > 0
            && self.maxPayloadBytes <= 1024 * 1024 * 1024
            && self.excludedPeerIds.count <= 4096 && self.excludedSenderIds.count <= 4096
            && self.excludedPeerIds.allSatisfy(telegramCorePeerIdIsSafelyDecodable)
            && self.excludedSenderIds.allSatisfy(telegramCorePeerIdIsSafelyDecodable)
    }

    fileprivate static var unsupported: LocalArchiveSettings {
        var result = LocalArchiveSettings()
        result.sourceIsSupported = false
        return result
    }

    public init(enabled: Bool = false, excludeBots: Bool = true,
                excludedPeerIds: Set<Int64> = [], excludedSenderIds: Set<Int64> = [],
                maxAgeSeconds: Int64 = Int64.max, maxPayloadBytes: Int64 = 32 * 1024 * 1024) {
        self.schemaVersion = 1
        self.enabled = enabled
        self.excludeBots = excludeBots
        self.excludedPeerIds = excludedPeerIds
        self.excludedSenderIds = excludedSenderIds
        self.maxAgeSeconds = maxAgeSeconds
        self.maxPayloadBytes = maxPayloadBytes
        self.generation = 0
        self.pendingPurge = false
        self.droppedCaptures = 0
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, enabled, excludeBots, excludedPeerIds, excludedSenderIds
        case maxAgeSeconds, maxPayloadBytes, generation, pendingPurge, droppedCaptures
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try container.decode(Int32.self, forKey: .schemaVersion)
        self.enabled = try container.decode(Bool.self, forKey: .enabled)
        self.excludeBots = try container.decode(Bool.self, forKey: .excludeBots)
        self.excludedPeerIds = Set(try container.decode([Int64].self, forKey: .excludedPeerIds))
        self.excludedSenderIds = Set(try container.decode([Int64].self, forKey: .excludedSenderIds))
        self.maxAgeSeconds = try container.decode(Int64.self, forKey: .maxAgeSeconds)
        self.maxPayloadBytes = try container.decode(Int64.self, forKey: .maxPayloadBytes)
        self.generation = try container.decode(Int64.self, forKey: .generation)
        self.pendingPurge = try container.decode(Bool.self, forKey: .pendingPurge)
        self.droppedCaptures = try container.decode(Int64.self, forKey: .droppedCaptures)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(self.schemaVersion, forKey: .schemaVersion)
        try container.encode(self.enabled, forKey: .enabled)
        try container.encode(self.excludeBots, forKey: .excludeBots)
        // Sets use the same primitive-array representation as the first draft.
        // Explicit sorted arrays give deterministic bytes and use Postbox's
        // supported Int64-array codec without changing the public preference API.
        try container.encode(self.excludedPeerIds.sorted(), forKey: .excludedPeerIds)
        try container.encode(self.excludedSenderIds.sorted(), forKey: .excludedSenderIds)
        try container.encode(self.maxAgeSeconds, forKey: .maxAgeSeconds)
        try container.encode(self.maxPayloadBytes, forKey: .maxPayloadBytes)
        try container.encode(self.generation, forKey: .generation)
        try container.encode(self.pendingPurge, forKey: .pendingPurge)
        try container.encode(self.droppedCaptures, forKey: .droppedCaptures)
    }
}

func localArchiveSettings(transaction: Transaction) -> LocalArchiveSettings {
    return localArchiveSettings(entry: transaction.getPreferencesEntry(key: localArchiveSettingsKey))
}

func localArchiveSettings(entry: PreferencesEntry?) -> LocalArchiveSettings {
    // Absence is editable. Existing unknown/malformed preferences are disabled
    // and read-only: silently replacing them could lose exclusions or a purge epoch.
    guard let entry else { return LocalArchiveSettings() }
    guard entry.data.count <= 128 * 1024,
          let settings = entry.get(LocalArchiveSettings.self), settings.isSupported else {
        return .unsupported
    }
    return settings
}

func setLocalArchiveSettings(transaction: Transaction, settings: LocalArchiveSettings) {
    transaction.setPreferencesEntry(key: localArchiveSettingsKey, value: PreferencesEntry(settings))
}
