// SPDX-License-Identifier: GPL-2.0-or-later
public extension Account {
    var localFolderAppearance: LocalFolderAppearanceContext {
        return LocalFolderAppearanceContext(postbox: self.postbox)
    }
}
