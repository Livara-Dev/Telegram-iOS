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

    private enum CodingKeys: String, CodingKey { case color, tag }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        if let raw = try values.decodeIfPresent(Int32.self, forKey: .color) {
            guard let color = LocalFolderColor(rawValue: raw) else {
                throw DecodingError.dataCorruptedError(forKey: .color, in: values, debugDescription: "Unknown local folder color")
            }
            self.color = color
        } else { self.color = nil }
        self.tag = try values.decode(String.self, forKey: .tag)
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        if let color = self.color { try values.encode(color.rawValue, forKey: .color) }
        try values.encode(self.tag, forKey: .tag)
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

/// Postbox supports homogeneous object arrays, not an alternating Int32/value
/// dictionary representation. Keep only keyed fixed-width primitives on disk.
private struct LocalFolderAppearanceRecord: Codable {
    let id: Int32
    let color: Int32?
    let tag: String
}

/// Local-only overlays keyed by this account's native folder IDs. Canonical
/// filters and their synchronization document are never rewritten.
public struct LocalFolderAppearanceSettings: Codable, Equatable {
    let schemaVersion: Int32
    public internal(set) var folders: [Int32: LocalFolderAppearance]

    public init() {
        self.schemaVersion = 2
        self.folders = [:]
    }

    private enum CodingKeys: String, CodingKey { case schemaVersion, folders }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let version = try values.decode(Int32.self, forKey: .schemaVersion)
        // Version 1 was an unreleased candidate with an unencodable nonempty
        // dictionary. Reject it and future versions before reading their shape;
        // the preference facade preserves incompatible bytes as read-only.
        guard version == 2 else {
            throw DecodingError.dataCorruptedError(forKey: .schemaVersion, in: values, debugDescription: "Unsupported local folder schema")
        }
        let records = try values.decode([LocalFolderAppearanceRecord].self, forKey: .folders)
        guard records.count <= 100 else {
            throw DecodingError.dataCorruptedError(forKey: .folders, in: values, debugDescription: "Too many local folder records")
        }
        var folders: [Int32: LocalFolderAppearance] = [:]
        for record in records {
            let color = record.color.flatMap(LocalFolderColor.init(rawValue:))
            guard record.id > 0, folders[record.id] == nil,
                  record.color == nil || color != nil,
                  let appearance = LocalFolderAppearance(color: color, tag: record.tag), appearance.tag == record.tag,
                  appearance.color != nil || !appearance.tag.isEmpty else {
                throw DecodingError.dataCorruptedError(forKey: .folders, in: values, debugDescription: "Invalid or duplicate local folder record")
            }
            folders[record.id] = appearance
        }
        self.schemaVersion = version
        self.folders = folders
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(self.schemaVersion, forKey: .schemaVersion)
        let records = self.folders.sorted(by: { $0.key < $1.key }).map { id, value in
            LocalFolderAppearanceRecord(id: id, color: value.color?.rawValue, tag: value.tag)
        }
        try values.encode(records, forKey: .folders)
    }

    var isValid: Bool {
        return self.schemaVersion == 2 && self.folders.count <= 100 && self.folders.allSatisfy { id, value in
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
