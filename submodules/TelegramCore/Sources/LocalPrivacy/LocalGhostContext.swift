// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit

/// Account-local access to activity-indicator preferences. No networking or
/// Telegram session material is involved in preference persistence.
public final class LocalGhostContext {
    private let postbox: Postbox

    init(postbox: Postbox) {
        self.postbox = postbox
    }

    public var settings: Signal<LocalGhostSettingsState, NoError> {
        return localGhostSettingsSignal(postbox: self.postbox)
    }

    /// Starts a Postbox transaction when subscribed. Unknown/malformed persisted
    /// preferences remain read-only; an older client must not erase a newer policy.
    public func updateSettings(_ update: @escaping (inout LocalGhostSettings) -> Void) -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            let current = localGhostSettings(transaction: transaction)
            guard case let .available(value) = current else { return false }
            var updated = value
            update(&updated)
            guard updated.schemaVersion == 1, let entry = PreferencesEntry(updated) else { return false }
            transaction.setPreferencesEntry(key: localGhostSettingsKey, value: entry)
            return true
        }
    }

    public func setEnabled(_ enabled: Bool) -> Signal<Bool, NoError> {
        return self.updateSettings { current in
            current.enabled = enabled
        }
    }
}
