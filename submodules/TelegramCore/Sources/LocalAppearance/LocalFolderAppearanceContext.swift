// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox
import SwiftSignalKit

let localFolderAppearanceSettingsKey = ValueBoxKey("ayugram.folder-appearance.v1")

func localFolderAppearanceSettings(entry: PreferencesEntry?) -> LocalFolderAppearanceSettingsState {
    guard let entry else { return .available(LocalFolderAppearanceSettings()) }
    guard entry.data.count <= 32768, let settings = entry.get(LocalFolderAppearanceSettings.self),
          settings.isValid else { return .unsupported }
    return .available(settings)
}

/// Native folder IDs can be reused. Remove overlays in the same transaction as
/// a canonical folder removal so a later folder cannot inherit an old label.
/// This never changes the native filter document or sync operation payloads.
func pruneLocalFolderAppearance(transaction: Transaction, remainingFolderIds: Set<Int32>) {
    guard case let .available(current) = localFolderAppearanceSettings(entry: transaction.getPreferencesEntry(key: localFolderAppearanceSettingsKey)) else { return }
    let retained = current.folders.filter { remainingFolderIds.contains($0.key) }
    guard retained.count != current.folders.count else { return }
    if retained.isEmpty {
        transaction.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: nil)
    } else {
        var updated = current
        updated.folders = retained
        guard let entry = PreferencesEntry(updated), entry.data.count <= 32768 else { return }
        transaction.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: entry)
    }
}

public final class LocalFolderAppearanceContext {
    private let postbox: Postbox
    init(postbox: Postbox) { self.postbox = postbox }

    public var settings: Signal<LocalFolderAppearanceSettingsState, NoError> {
        return self.postbox.preferencesView(keys: [localFolderAppearanceSettingsKey])
        |> map { localFolderAppearanceSettings(entry: $0.values[localFolderAppearanceSettingsKey]) }
        |> distinctUntilChanged
    }

    public func setFolder(id: Int32, color: LocalFolderColor?, tag: String, expectedFilter: ChatListFilter) -> Signal<Bool, NoError> {
        guard id > 0, expectedFilter.id == id, let appearance = LocalFolderAppearance(color: color, tag: tag) else { return .single(false) }
        return self.postbox.transaction { transaction in
            guard case let .available(current) = localFolderAppearanceSettings(entry: transaction.getPreferencesEntry(key: localFolderAppearanceSettingsKey)),
                  _internal_currentChatListFilters(transaction: transaction).first(where: { $0.id == id }) == expectedFilter else { return false }
            var settings = current
            if appearance.color == nil && appearance.tag.isEmpty {
                settings.folders.removeValue(forKey: id)
            } else {
                settings.folders[id] = appearance
            }
            guard settings.isValid, let entry = PreferencesEntry(settings), entry.data.count <= 32768 else { return false }
            transaction.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: entry)
            return true
        }
    }

    /// Clear local overlays only. This does not remove or synchronize folders.
    /// Incompatible saved documents remain byte-identical and read-only.
    public func reset() -> Signal<Bool, NoError> {
        return self.postbox.transaction { transaction in
            guard case .available = localFolderAppearanceSettings(entry: transaction.getPreferencesEntry(key: localFolderAppearanceSettingsKey)) else { return false }
            transaction.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: nil)
            return true
        }
    }
}
