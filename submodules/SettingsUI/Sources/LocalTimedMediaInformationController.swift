// SPDX-License-Identifier: GPL-2.0-or-later
import Display
import AccountContext

/// Stable search/deep-link destination, now routed directly to persisted controls.
public func localTimedMediaInformationController(context: AccountContext) -> ViewController {
    return localArchiveSettingsController(context: context)
}
