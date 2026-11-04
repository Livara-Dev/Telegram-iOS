// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit

public final class LocalStoryPrivacyContext {
    private let postbox: Postbox

    init(postbox: Postbox) {
        self.postbox = postbox
    }

    public var settings: Signal<LocalStoryPrivacySettingsState, NoError> {
        return localStoryPrivacySettingsSignal(postbox: self.postbox)
    }

    /// The generation change and preference update commit together. Old queued
    /// receipts remain invalid after disable/restart; no archive or session data
    /// is written here. Already-dispatched network work cannot be recalled.
    public func setSuppressed(_ suppressed: Bool) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            guard case let .available(current) = localStoryPrivacySettings(transaction: transaction) else { return false }
            if current.suppressViewReceipts == suppressed { return true }
            var generation = current.suppressionGeneration
            if suppressed {
                guard generation < Int64.max else { return false }
                generation += 1
            }
            let updated = LocalStoryPrivacySettings(suppressViewReceipts: suppressed, suppressionGeneration: generation)
            guard let entry = PreferencesEntry(updated) else { return false }
            transaction.setPreferencesEntry(key: localStoryPrivacySettingsKey, value: entry)
            return true
        }
    }
}
