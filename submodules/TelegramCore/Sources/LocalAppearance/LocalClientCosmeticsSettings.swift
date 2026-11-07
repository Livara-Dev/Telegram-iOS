// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

public enum LocalClientAccentPreset: Int32, CaseIterable {
    case ayu = 0
    case ocean = 1
    case forest = 2
    case sunset = 3
    case rose = 4
    case nativeTheme = 5
    case midnight = 6
    case emerald = 7
    case crimson = 8
    case amber = 9
    case graphite = 10
    case ice = 11
    case lavender = 12

    public var title: String {
        switch self {
        case .ayu: return "Ayu violet"
        case .ocean: return "Ocean"
        case .forest: return "Forest"
        case .sunset: return "Sunset"
        case .rose: return "Rose"
        case .nativeTheme: return "Use Telegram theme accent"
        case .midnight: return "Midnight"
        case .emerald: return "Emerald"
        case .crimson: return "Crimson"
        case .amber: return "Amber"
        case .graphite: return "Graphite"
        case .ice: return "Ice"
        case .lavender: return "Lavender"
        }
    }

    /// RGB values are presentation metadata only; Core never imports UIKit.
    public var rgb: UInt32? {
        switch self {
        case .ayu: return 0x6450e8
        case .ocean: return 0x007f9e
        case .forest: return 0x208c64
        case .sunset: return 0xd76542
        case .rose: return 0xbc447c
        case .nativeTheme: return nil
        case .midnight: return 0x52658f
        case .emerald: return 0x159b78
        case .crimson: return 0xc7445c
        case .amber: return 0xd28b24
        case .graphite: return 0x64748b
        case .ice: return 0x38a9d6
        case .lavender: return 0x9674d8
        }
    }
}

/// Per-account client presentation. These values never grant a server Premium
/// entitlement or change Telegram's peer/subscription models.
public struct LocalClientCosmeticsSettings: Codable, Equatable {
    let schemaVersion: Int32
    public var accent: LocalClientAccentPreset
    public var reduceMotion: Bool
    public var allowBundledPremiumIcons: Bool

    public init(accent: LocalClientAccentPreset = .ayu, reduceMotion: Bool = false,
                allowBundledPremiumIcons: Bool = false) {
        self.schemaVersion = 1
        self.accent = accent
        self.reduceMotion = reduceMotion
        self.allowBundledPremiumIcons = allowBundledPremiumIcons
    }

    private enum CodingKeys: String, CodingKey { case schemaVersion, accent, reduceMotion, allowBundledPremiumIcons }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try values.decode(Int32.self, forKey: .schemaVersion)
        let raw = try values.decode(Int32.self, forKey: .accent)
        guard let accent = LocalClientAccentPreset(rawValue: raw) else {
            throw DecodingError.dataCorruptedError(forKey: .accent, in: values, debugDescription: "Unknown local accent")
        }
        self.accent = accent
        self.reduceMotion = try values.decode(Bool.self, forKey: .reduceMotion)
        self.allowBundledPremiumIcons = try values.decode(Bool.self, forKey: .allowBundledPremiumIcons)
    }

    public func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(self.schemaVersion, forKey: .schemaVersion)
        try values.encode(self.accent.rawValue, forKey: .accent)
        try values.encode(self.reduceMotion, forKey: .reduceMotion)
        try values.encode(self.allowBundledPremiumIcons, forKey: .allowBundledPremiumIcons)
    }
}

public enum LocalClientCosmeticsSettingsState: Equatable {
    case available(LocalClientCosmeticsSettings)
    case unsupported
}

/// A local asset-picker decision only. The real Telegram Premium bit is an
/// input, never rewritten. Unknown assets are never authorized by this opt-in.
public func localClientAllowsBundledAppIcon(isBundled: Bool, isPremiumIcon: Bool,
                                            accountIsPremium: Bool, settings: LocalClientCosmeticsSettingsState) -> Bool {
    guard isBundled else { return false }
    if !isPremiumIcon || accountIsPremium { return true }
    if case let .available(value) = settings { return value.allowBundledPremiumIcons }
    return false
}
