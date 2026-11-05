// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// Fixed local palette. These codes are not Telegram's server folder colors.
public enum LocalFolderColor: Int32, Codable, CaseIterable {
    case violet = 0
    case blue = 1
    case teal = 2
    case green = 3
    case orange = 4
    case pink = 5

    public var title: String {
        switch self {
        case .violet: return "Violet"
        case .blue: return "Blue"
        case .teal: return "Teal"
        case .green: return "Green"
        case .orange: return "Orange"
        case .pink: return "Pink"
        }
    }

    public func rgb(dark: Bool) -> UInt32 {
        switch self {
        case .violet: return dark ? 0xb2a2ff : 0x6450c8
        case .blue: return dark ? 0x81b5ff : 0x2867ba
        case .teal: return dark ? 0x64cfdf : 0x007d90
        case .green: return dark ? 0x7dd5a7 : 0x237e4c
        case .orange: return dark ? 0xffb876 : 0xa65b20
        case .pink: return dark ? 0xffa0cc : 0xb23876
        }
    }
}

public struct LocalFolderAppearance: Codable, Equatable {
    public let color: LocalFolderColor?
    public let tag: String

    public init?(color: LocalFolderColor? = nil, tag: String = "") {
        guard let tag = Self.normalizedTag(tag) else { return nil }
        self.color = color
        self.tag = tag
    }

    /// Bound raw input before segmentation; keep emoji joiners, remove control
    /// and directional formatting characters. An empty tag removes the label.
    public static func normalizedTag(_ input: String) -> String? {
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
        guard result.utf8.count <= 128, result.count <= 20 else { return nil }
        return result
    }
}

/// Local-only overlays keyed by this account's native folder IDs. Canonical
/// filters and their synchronization document are never rewritten.
public struct LocalFolderAppearanceSettings: Codable, Equatable {
    let schemaVersion: Int32
    public internal(set) var folders: [Int32: LocalFolderAppearance]

    public init() {
        self.schemaVersion = 1
        self.folders = [:]
    }

    var isValid: Bool {
        return self.schemaVersion == 1 && self.folders.count <= 100 && self.folders.allSatisfy { id, value in
            id > 0 && LocalFolderAppearance.normalizedTag(value.tag) == value.tag && (value.color != nil || !value.tag.isEmpty)
        }
    }
}

public enum LocalFolderAppearanceSettingsState: Equatable {
    case available(LocalFolderAppearanceSettings)
    case unsupported
}

/// Shared production projection for tabs, folder settings and the local editor.
/// Always pass the canonical title: each surface projects once, preserving its
/// existing entity ranges/animations and never replacing a ChatListFilter.
public func localFolderAppearanceProjection(folderId: Int32, canonicalTitle: ChatFolderTitle,
                                            settings: LocalFolderAppearanceSettingsState,
                                            dark: Bool) -> (title: ChatFolderTitle, rgb: UInt32?) {
    guard folderId > 0, case let .available(settings) = settings,
          let value = settings.folders[folderId] else { return (canonicalTitle, nil) }
    let title: ChatFolderTitle
    if value.tag.isEmpty {
        title = canonicalTitle
    } else {
        title = ChatFolderTitle(text: canonicalTitle.text + " · " + value.tag,
            entities: canonicalTitle.entities, enableAnimations: canonicalTitle.enableAnimations)
    }
    return (title, value.color?.rgb(dark: dark))
}
