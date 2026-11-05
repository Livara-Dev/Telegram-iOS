// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit

let localClientCosmeticsSettingsKey = ValueBoxKey("ayugram.client-cosmetics.v1")

func localClientCosmeticsSettings(entry: PreferencesEntry?) -> LocalClientCosmeticsSettingsState {
    guard let entry else { return .available(LocalClientCosmeticsSettings()) }
    guard entry.data.count <= 4096, let settings = entry.get(LocalClientCosmeticsSettings.self),
          settings.schemaVersion == 1 else { return .unsupported }
    return .available(settings)
}

public final class LocalClientCosmeticsContext {
    private let postbox: Postbox

    init(postbox: Postbox) { self.postbox = postbox }

    public var settings: Signal<LocalClientCosmeticsSettingsState, NoError> {
        return self.postbox.preferencesView(keys: [localClientCosmeticsSettingsKey])
        |> map { localClientCosmeticsSettings(entry: $0.values[localClientCosmeticsSettingsKey]) }
        |> distinctUntilChanged
    }

    private func update(_ transform: @escaping (inout LocalClientCosmeticsSettings) -> Void) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            guard case let .available(current) = localClientCosmeticsSettings(entry: transaction.getPreferencesEntry(key: localClientCosmeticsSettingsKey)) else { return false }
            var settings = current
            transform(&settings)
            guard settings.schemaVersion == 1, let entry = PreferencesEntry(settings), entry.data.count <= 4096 else { return false }
            transaction.setPreferencesEntry(key: localClientCosmeticsSettingsKey, value: entry)
            return true
        }
    }

    public func setAccent(_ accent: LocalClientAccentPreset) -> Signal<Bool, NoError> {
        return self.update { $0.accent = accent }
    }

    public func setReducedMotion(_ reduced: Bool) -> Signal<Bool, NoError> {
        return self.update { $0.reduceMotion = reduced }
    }

    public func setAllowBundledPremiumIcons(_ allowed: Bool) -> Signal<Bool, NoError> {
        return self.update { $0.allowBundledPremiumIcons = allowed }
    }

    /// Resets this document only. It does not change the device-wide chosen app
    /// icon/theme, archive data, filters, Telegram sessions or another account.
    public func reset() -> Signal<Bool, NoError> {
        return self.update { $0 = LocalClientCosmeticsSettings() }
    }
}
