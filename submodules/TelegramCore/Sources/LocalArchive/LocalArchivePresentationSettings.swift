// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

/// Presentation preferences are separate from capture policy and the archive
/// schema. Changing a label never changes an archived event or Telegram message.
public struct LocalArchivePresentationSettings: Codable, Equatable {
    let schemaVersion: Int32
    public let deletedMarker: String
    public let editedMarker: String

    public init(deletedMarker: String = "Deleted", editedMarker: String = "Edited") {
        self.schemaVersion = 1
        self.deletedMarker = deletedMarker
        self.editedMarker = editedMarker
    }

    /// Reject oversized raw input before Unicode segmentation. Strip controls,
    /// line separators and directional formatting; preserve emoji joiners.
    /// The final label has at most 32 graphemes and 128 UTF-8 bytes.
    public static func normalizedMarker(_ input: String) -> String? {
        guard input.utf8.prefix(1025).count <= 1024 else { return nil }
        var result = ""
        for scalar in input.unicodeScalars {
            let value = scalar.value
            if scalar.properties.generalCategory == .control
                || (scalar.properties.generalCategory == .format && value != 0x200c && value != 0x200d)
                || value == 0x2028 || value == 0x2029 {
                continue
            }
            result.unicodeScalars.append(scalar)
        }
        result = result.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !result.isEmpty, result.utf8.count <= 128, result.count <= 32 else { return nil }
        return result
    }
}

public enum LocalArchivePresentationSettingsState: Equatable {
    case available(LocalArchivePresentationSettings)
    case unsupported

    /// Safe display fallback only. Unsupported preferences remain read-only.
    public func marker(isDeleted: Bool) -> String {
        guard case let .available(settings) = self else { return isDeleted ? "Deleted" : "Edited" }
        return isDeleted ? settings.deletedMarker : settings.editedMarker
    }
}

let localArchivePresentationSettingsKey = ValueBoxKey("ayugram.archive-presentation.v1")

func localArchivePresentationSettings(entry: PreferencesEntry?) -> LocalArchivePresentationSettingsState {
    guard let entry else { return .available(LocalArchivePresentationSettings()) }
    guard entry.data.count <= 2048,
          let settings = entry.get(LocalArchivePresentationSettings.self), settings.schemaVersion == 1,
          LocalArchivePresentationSettings.normalizedMarker(settings.deletedMarker) == settings.deletedMarker,
          LocalArchivePresentationSettings.normalizedMarker(settings.editedMarker) == settings.editedMarker else {
        return .unsupported
    }
    return .available(settings)
}

public final class LocalArchivePresentationContext {
    private let postbox: Postbox
    init(postbox: Postbox) { self.postbox = postbox }

    public var settings: Signal<LocalArchivePresentationSettingsState, NoError> {
        return self.postbox.preferencesView(keys: [localArchivePresentationSettingsKey])
        |> map { localArchivePresentationSettings(entry: $0.values[localArchivePresentationSettingsKey]) }
        |> distinctUntilChanged
    }

    public func setMarkers(deleted: String, edited: String) -> Signal<Bool, NoError> {
        guard let deleted = LocalArchivePresentationSettings.normalizedMarker(deleted),
              let edited = LocalArchivePresentationSettings.normalizedMarker(edited) else { return .single(false) }
        return self.postbox.transaction { transaction in
            guard case .available = localArchivePresentationSettings(entry: transaction.getPreferencesEntry(key: localArchivePresentationSettingsKey)),
                  let entry = PreferencesEntry(LocalArchivePresentationSettings(deletedMarker: deleted, editedMarker: edited)) else { return false }
            transaction.setPreferencesEntry(key: localArchivePresentationSettingsKey, value: entry)
            return true
        }
    }

    public func resetMarkers() -> Signal<Bool, NoError> {
        return self.setMarkers(deleted: "Deleted", edited: "Edited")
    }
}

extension Account {
    public var localArchivePresentation: LocalArchivePresentationContext {
        return LocalArchivePresentationContext(postbox: self.postbox)
    }
}
