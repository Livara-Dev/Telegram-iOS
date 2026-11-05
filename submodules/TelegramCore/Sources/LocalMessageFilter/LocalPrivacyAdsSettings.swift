// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

/// Local presentation only. This policy is not a Telegram Premium entitlement
/// and does not change server advertising preferences or networking.
public struct LocalPrivacyAdsSettings: Codable, Equatable {
    let schemaVersion: Int32
    public let hideSponsoredMessages: Bool

    public init(hideSponsoredMessages: Bool = false) {
        self.schemaVersion = 1
        self.hideSponsoredMessages = hideSponsoredMessages
    }
}

public enum LocalPrivacyAdsSettingsState: Equatable {
    case available(LocalPrivacyAdsSettings)
    case unsupported

    public var hidesSponsoredMessages: Bool {
        if case let .available(settings) = self {
            return settings.hideSponsoredMessages
        }
        return false
    }
}

let localPrivacyAdsSettingsKey = ValueBoxKey("ayugram.privacy-ads.v1")

func localPrivacyAdsSettings(entry: PreferencesEntry?) -> LocalPrivacyAdsSettingsState {
    guard let entry else { return .available(LocalPrivacyAdsSettings()) }
    guard entry.data.count <= 1024,
          let settings = entry.get(LocalPrivacyAdsSettings.self), settings.schemaVersion == 1 else {
        return .unsupported
    }
    return .available(settings)
}

/// The production chat advertisement consumer uses this projection. Canonical
/// messages and the upstream advertisement state/cache are never mutated.
public func locallyVisibleSponsoredMessages<T>(_ messages: [T], policy: LocalPrivacyAdsSettingsState) -> [T] {
    return policy.hidesSponsoredMessages ? [] : messages
}

public final class LocalPrivacyAdsContext {
    private let postbox: Postbox

    init(postbox: Postbox) {
        self.postbox = postbox
    }

    public var settings: Signal<LocalPrivacyAdsSettingsState, NoError> {
        return self.postbox.preferencesView(keys: [localPrivacyAdsSettingsKey])
        |> map { localPrivacyAdsSettings(entry: $0.values[localPrivacyAdsSettingsKey]) }
        |> distinctUntilChanged
    }

    public func setHideSponsoredMessages(_ hide: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            guard case .available = localPrivacyAdsSettings(entry: transaction.getPreferencesEntry(key: localPrivacyAdsSettingsKey)),
                  let entry = PreferencesEntry(LocalPrivacyAdsSettings(hideSponsoredMessages: hide)) else {
                return false
            }
            transaction.setPreferencesEntry(key: localPrivacyAdsSettingsKey, value: entry)
            return true
        }
    }
}
