// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

let localStoryPrivacySettingsKey = ValueBoxKey("ayugram.ghost.story-views.v1")

/// Independent from activity-indicator Ghost settings. Enabling this feature
/// does not change read receipts, presence, or any Telegram entitlement.
public struct LocalStoryPrivacySettings: Codable, Equatable {
    let schemaVersion: Int32
    public let suppressViewReceipts: Bool
    /// Invalidates unsent receipts from before the most recent enable operation,
    /// even if suppression is disabled again before a worker or app restart.
    let suppressionGeneration: Int64

    public init(suppressViewReceipts: Bool = false) {
        self.init(suppressViewReceipts: suppressViewReceipts, suppressionGeneration: 0)
    }

    init(suppressViewReceipts: Bool, suppressionGeneration: Int64) {
        self.schemaVersion = 1
        self.suppressViewReceipts = suppressViewReceipts
        self.suppressionGeneration = suppressionGeneration
    }
}

public enum LocalStoryPrivacySettingsState: Equatable {
    case available(LocalStoryPrivacySettings)
    /// Preserve unknown bytes and suppress covered outgoing story receipts.
    case unsupported
}

func localStoryPrivacySettings(entry: PreferencesEntry?) -> LocalStoryPrivacySettingsState {
    guard let entry else { return .available(LocalStoryPrivacySettings()) }
    guard entry.data.count <= 4096, let value = entry.get(LocalStoryPrivacySettings.self),
          value.schemaVersion == 1, value.suppressionGeneration >= 0 else { return .unsupported }
    return .available(value)
}

func localStoryPrivacySettings(transaction: Transaction) -> LocalStoryPrivacySettingsState {
    return localStoryPrivacySettings(entry: transaction.getPreferencesEntry(key: localStoryPrivacySettingsKey))
}

func localStoryPrivacySettingsSignal(postbox: Postbox) -> Signal<LocalStoryPrivacySettingsState, NoError> {
    // No synthetic initial default: wait for this account's persisted policy.
    return postbox.preferencesView(keys: [localStoryPrivacySettingsKey])
    |> map { view in localStoryPrivacySettings(entry: view.values[localStoryPrivacySettingsKey]) }
    |> distinctUntilChanged
}

func localStoryPrivacyAllowsReceipt(_ settings: LocalStoryPrivacySettingsState, generation: Int64) -> Bool {
    guard case let .available(value) = settings else { return false }
    return !value.suppressViewReceipts && value.suppressionGeneration == generation
}
