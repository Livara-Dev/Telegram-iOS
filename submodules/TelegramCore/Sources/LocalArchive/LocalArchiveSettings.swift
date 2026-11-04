// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox

// Longer than the upstream four-byte preference keys; scoped to each Postbox.
public let localArchiveSettingsKey = ValueBoxKey("ayugram.archive1")

/// Experimental, local-only preferences. Enabling this does not enable media capture.
public struct LocalArchiveSettings: Codable, Equatable {
    let schemaVersion: Int
    public var enabled: Bool
    public var excludeBots: Bool
    public var excludedPeerIds: Set<Int64>
    public var excludedSenderIds: Set<Int64>
    public var maxAgeSeconds: Int64
    public var maxPayloadBytes: Int64
    public internal(set) var generation: Int64
    public internal(set) var pendingPurge: Bool
    public internal(set) var droppedCaptures: Int64

    public init(enabled: Bool = false, excludeBots: Bool = true,
                excludedPeerIds: Set<Int64> = [], excludedSenderIds: Set<Int64> = [],
                maxAgeSeconds: Int64 = 30 * 86400, maxPayloadBytes: Int64 = 32 * 1024 * 1024) {
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
}

func localArchiveSettings(transaction: Transaction) -> LocalArchiveSettings {
    return localArchiveSettings(entry: transaction.getPreferencesEntry(key: localArchiveSettingsKey))
}

func localArchiveSettings(entry: PreferencesEntry?) -> LocalArchiveSettings {
    // Unknown/malformed settings fail closed. The archive generation check then prevents
    // an old/default generation from silently overwriting a newer database state.
    guard let settings = entry?.get(LocalArchiveSettings.self),
          settings.schemaVersion == 1, settings.generation >= 0, settings.droppedCaptures >= 0,
          settings.maxAgeSeconds > 0, settings.maxPayloadBytes > 0,
          settings.maxPayloadBytes <= 1024 * 1024 * 1024 else { return LocalArchiveSettings() }
    return settings
}

func setLocalArchiveSettings(transaction: Transaction, settings: LocalArchiveSettings) {
    transaction.setPreferencesEntry(key: localArchiveSettingsKey, value: PreferencesEntry(settings))
}
