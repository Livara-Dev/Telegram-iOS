// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// The same catalog drives the production dashboard, its local search and the
/// global Settings search. Entries describe actual destinations, not entitlements.
public enum CustomeOptionsCategory: String, CaseIterable {
    case ghostMode
    case deletedMessages
    case timedMedia
    case mediaSaving
    case localPremium
    case privacyAds
    case messageFilters
    case appearance
    case chatInterface
    case advanced
    case storageBackup
    case smartInbox
    case chatStudio
    case mediaVault
    case ultraBatterySaver

    public var title: String {
        switch self {
        case .ghostMode: return "Ghost Mode"
        case .deletedMessages: return "Message History"
        case .timedMedia: return "Timed Media"
        case .mediaSaving: return "Media Saving"
        case .localPremium: return "Local Premium"
        case .smartInbox: return "Smart Inbox"
        case .chatStudio: return "Chat Studio"
        case .mediaVault: return "Media Vault"
        case .ultraBatterySaver: return "Ultra Battery Saver"
        case .privacyAds: return "Privacy & Ads"
        case .messageFilters: return "Message Filters"
        case .appearance: return "Appearance"
        case .chatInterface: return "Chat Interface"
        case .advanced: return "Advanced"
        case .storageBackup: return "Storage & Backup"
        }
    }

    public var detail: String {
        switch self {
        case .ghostMode: return "Choose privacy protections for this account"
        case .deletedMessages: return "Encrypted history, edits and exclusions"
        case .timedMedia: return "What can and cannot be retained"
        case .mediaSaving: return "Archived media and download settings"
        case .localPremium: return "Local cosmetics without server entitlements"
        case .privacyAds: return "Local sponsored-message controls and privacy"
        case .messageFilters: return "Hide unwanted content on this device"
        case .appearance: return "Colors, themes, icons and text size"
        case .chatInterface: return "Chat appearance and folders"
        case .advanced: return "Reset controls and account preferences"
        case .storageBackup: return "Storage usage and archive management"
        case .smartInbox: return "Priority, filters and chat organization"
        case .chatStudio: return "Build a personal chat appearance"
        case .mediaVault: return "Browse, play and export saved media"
        case .ultraBatterySaver: return "One-touch power and visual-effects profile"
        }
    }

    public var symbol: String {
        switch self {
        case .ghostMode: return "eye.slash.fill"
        case .deletedMessages: return "clock.arrow.circlepath"
        case .timedMedia: return "timer"
        case .mediaSaving: return "photo.on.rectangle.angled"
        case .localPremium: return "sparkles"
        case .privacyAds: return "hand.raised.fill"
        case .messageFilters: return "line.3.horizontal.decrease.circle.fill"
        case .appearance: return "paintpalette.fill"
        case .chatInterface: return "bubble.left.and.bubble.right.fill"
        case .advanced: return "slider.horizontal.3"
        case .storageBackup: return "externaldrive.fill"
        case .smartInbox: return "tray.full.fill"
        case .chatStudio: return "paintbrush.pointed.fill"
        case .mediaVault: return "play.rectangle.on.rectangle.fill"
        case .ultraBatterySaver: return "battery.100percent"
        }
    }

    public var keywords: String {
        switch self {
        case .ghostMode: return "read receipts story views online offline typing sticker stickers recording upload privacy"
        case .deletedMessages: return "deleted edited edit version timeline archive capture preservation exclusions bots sender chat"
        case .timedMedia: return "view once view-once viewonce self destruct self-destruct disappearing auto delete expiration ttl photos videos voice secret"
        case .mediaSaving: return "photo video gif animation voice document file gallery download save"
        case .localPremium: return "cosmetic animation app icon icons premium local server entitlement accent violet ocean forest sunset rose reduced reduce motion bundled"
        case .privacyAds: return "sponsored advertisements ads promotion privacy local"
        case .messageFilters: return "filter spam blocked user text case sensitive regex regular expression unwanted advertising"
        case .appearance: return "theme dark light color colour icon font text size wallpaper"
        case .chatInterface: return "folder tag bubble chat wallpaper font text size"
        case .advanced: return "reset defaults disable account experimental limitations"
        case .storageBackup: return "retention quota size usage clear purge encrypted archive import export backup sync"
        case .smartInbox: return "priority important unread inbox filter folders organize messages response"
        case .chatStudio: return "bubble style wallpaper themes font colors corners appearance customize"
        case .mediaVault: return "gallery video voice audio photo export photos files duplicate storage playback"
        case .ultraBatterySaver: return "ultra battery saver low power reduce motion translucency autoplay background preload"
        }
    }
}

public enum CustomeOptionsAction: Equatable {
    case category(CustomeOptionsCategory)
    case ghostSettings
    case archiveSettings
    case archiveGallery
    case timedMediaInformation
    case mediaSaving
    case privacyAds
    case privacySettings
    case messageFilters
    case appearance
    case bubbleSettings
    case chatFolders
    case animationSettings
    case localCosmetics
    case archiveBackup
    case storageUsage
    case resetActiveFeatures
    case ultraBatterySaver
}

public enum CustomeOptionsToggle: String, CaseIterable {
    case archive
    case ghostActivity
    case messageFilters

    public var title: String {
        switch self {
        case .archive: return "Preserve deleted messages"
        case .ghostActivity: return "Ghost Mode"
        case .messageFilters: return "Message filters"
        }
    }

    public var symbol: String {
        switch self {
        case .archive: return "clock.arrow.circlepath"
        case .ghostActivity: return "eye.slash.fill"
        case .messageFilters: return "line.3.horizontal.decrease.circle.fill"
        }
    }
}

/// A closed presentation snapshot. It contains no account identifiers, message
/// contents, file paths or keys. Unavailable preferences stay noneditable.
public struct CustomeOptionsState: Equatable {
    public var archiveEnabled: Bool
    public var archiveAvailable: Bool
    public var archiveDetail: String
    public var ghostEnabled: Bool
    public var ghostAvailable: Bool
    public var ghostDetail: String
    public var filtersEnabled: Bool
    public var filtersAvailable: Bool
    public var filtersDetail: String

    public init(archiveEnabled: Bool = false, archiveAvailable: Bool = false,
                archiveDetail: String = "Loading this account’s archive settings…",
                ghostEnabled: Bool = false, ghostAvailable: Bool = false,
                ghostDetail: String = "Loading this account’s privacy settings…",
                filtersEnabled: Bool = false, filtersAvailable: Bool = false,
                filtersDetail: String = "Loading this account’s filter settings…") {
        self.archiveEnabled = archiveEnabled
        self.archiveAvailable = archiveAvailable
        self.archiveDetail = archiveDetail
        self.ghostEnabled = ghostEnabled
        self.ghostAvailable = ghostAvailable
        self.ghostDetail = ghostDetail
        self.filtersEnabled = filtersEnabled
        self.filtersAvailable = filtersAvailable
        self.filtersDetail = filtersDetail
    }

    public func value(for toggle: CustomeOptionsToggle) -> Bool {
        switch toggle {
        case .archive: return self.archiveEnabled
        case .ghostActivity: return self.ghostEnabled
        case .messageFilters: return self.filtersEnabled
        }
    }

    public func isAvailable(_ toggle: CustomeOptionsToggle) -> Bool {
        switch toggle {
        case .archive: return self.archiveAvailable
        case .ghostActivity: return self.ghostAvailable
        case .messageFilters: return self.filtersAvailable
        }
    }

    public func detail(for toggle: CustomeOptionsToggle) -> String {
        switch toggle {
        case .archive: return self.archiveDetail
        case .ghostActivity: return self.ghostDetail
        case .messageFilters: return self.filtersDetail
        }
    }
}

public struct CustomeOptionsLink: Equatable {
    public let title: String
    public let detail: String
    public let symbol: String
    public let action: CustomeOptionsAction

    public init(title: String, detail: String, symbol: String, action: CustomeOptionsAction) {
        self.title = title; self.detail = detail; self.symbol = symbol; self.action = action
    }
}

public enum CustomeOptionsCatalog {
    public static let title = "More Features"
    public static let maximumQueryUTF16Length = 512

    public static func boundedQuery(_ query: String) -> String {
        return String(decoding: query.utf16.prefix(self.maximumQueryUTF16Length), as: UTF16.self)
    }

    public static func categories(matching query: String) -> [CustomeOptionsCategory] {
        let tokens = self.boundedQuery(query).folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX"))
            .split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard !tokens.isEmpty else { return CustomeOptionsCategory.allCases }
        return CustomeOptionsCategory.allCases.filter { category in
            let searchable = (category.title + " " + category.detail + " " + category.keywords)
                .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "en_US_POSIX"))
            return tokens.allSatisfy { searchable.contains($0) }
        }
    }

    public static func links(for category: CustomeOptionsCategory) -> [CustomeOptionsLink] {
        let archive = CustomeOptionsLink(title: "Capture & history settings", detail: "Retention, excluded chats, senders and bots", symbol: "clock.arrow.circlepath", action: .archiveSettings)
        let gallery = CustomeOptionsLink(title: "Archived media gallery", detail: "Browse encrypted photos, videos and files", symbol: "photo.on.rectangle.angled", action: .archiveGallery)
        let appearance = CustomeOptionsLink(title: "Themes, icons & text size", detail: "Use the native appearance controls", symbol: "paintpalette.fill", action: .appearance)
        switch category {
        case .ghostMode:
            return [.init(title: "Choose Ghost protections", detail: "Independent controls and their exact limitations", symbol: category.symbol, action: .ghostSettings)]
        case .deletedMessages: return [archive, gallery]
        case .timedMedia:
            return [.init(title: "Timed-media support & limits", detail: "View-once, self-destruct and auto-delete rules", symbol: category.symbol, action: .timedMediaInformation)]
        case .mediaSaving:
            return [gallery, .init(title: "Download & saving preferences", detail: "Native media download and Photos settings", symbol: "arrow.down.circle.fill", action: .mediaSaving)]
        case .localPremium:
            return [.init(title: "Client colors & motion", detail: "Per-account accents and local icon access", symbol: "sparkles", action: .localCosmetics), appearance, .init(title: "Animations & power saving", detail: "Control the client’s supported visual effects", symbol: "sparkles", action: .animationSettings)]
        case .privacyAds:
            return [.init(title: "Sponsored messages & promotions", detail: "Local controls for this account", symbol: "hand.raised.fill", action: .privacyAds),
                    .init(title: "Telegram privacy settings", detail: "Manage the account’s server privacy choices", symbol: "lock.shield.fill", action: .privacySettings)]
        case .messageFilters:
            return [.init(title: "Message filter rules", detail: "Global and chat-specific local rules", symbol: category.symbol, action: .messageFilters)]
        case .appearance: return [appearance]
        case .chatInterface:
            return [appearance, .init(title: "Chat folders", detail: "Organize chats with the native folder editor", symbol: "folder.fill", action: .chatFolders)]
        case .advanced:
            return [.init(title: "Reset active features", detail: "Turn features off without deleting saved messages", symbol: "arrow.counterclockwise", action: .resetActiveFeatures)]
        case .storageBackup:
            return [archive, gallery, .init(title: "Encrypted import & export", detail: "Password-protected archive files you control", symbol: "lock.doc.fill", action: .archiveBackup), .init(title: "Telegram cache usage", detail: "Normal cache and archive storage are separate", symbol: "externaldrive.fill", action: .storageUsage)]
        case .smartInbox:
            return [.init(title: "Message filter rules", detail: "Prioritize or hide content locally", symbol: "line.3.horizontal.decrease.circle.fill", action: .messageFilters), .init(title: "Chat folders", detail: "Organize conversations", symbol: "folder.fill", action: .chatFolders), archive]
        case .chatStudio:
            return [appearance, .init(title: "Message bubble design", detail: "Preview and tune bubble corners", symbol: "bubble.left.and.bubble.right", action: .bubbleSettings), .init(title: "Local colors, icons & motion", detail: "Client-side appearance controls", symbol: "sparkles", action: .localCosmetics), .init(title: "Chat folders & layout", detail: "Native chat organization", symbol: "rectangle.3.group", action: .chatFolders)]
        case .mediaVault:
            return [gallery, .init(title: "Encrypted import & export", detail: "Move an archive using a password", symbol: "lock.doc.fill", action: .archiveBackup), .init(title: "Download & Photos settings", detail: "Native media and gallery controls", symbol: "arrow.down.circle.fill", action: .mediaSaving), .init(title: "Storage usage", detail: "Review cache and archive storage", symbol: "externaldrive.fill", action: .storageUsage)]
        case .ultraBatterySaver:
            return [.init(title: "Toggle Ultra Battery Saver", detail: "Reduce motion, translucency, autoplay and background work", symbol: "battery.100percent", action: .ultraBatterySaver), .init(title: "Fine-tune power settings", detail: "Battery threshold and individual controls", symbol: "slider.horizontal.3", action: .animationSettings)]
        }
    }

    public static func explanation(for category: CustomeOptionsCategory) -> String {
        switch category {
        case .ghostMode:
            return "Only protections listed in Ghost settings apply. Sending, explicit interactions, server behavior and other Telegram clients may reveal activity. This client cannot promise invisibility."
        case .deletedMessages:
            return "Capture starts when enabled for this account. It can preserve eligible content received by this client; it cannot recover messages already absent or files that never finished downloading."
        case .timedMedia:
            return "Secret Chats and timed or view-once content have independent account-local archive options, off by default. The support page opens the real controls and explains completed-media, storage and API-contract limits. Canonical timers and reads remain active."
        case .mediaSaving:
            return "The encrypted archive keeps eligible media independently of Telegram’s media cache. Saving to Photos is a separate native setting and creates a copy outside the archive’s protection."
        case .localPremium:
            return "These are client-side appearance and animation controls. They do not grant Telegram Premium, change your server account status or unlock features that require a subscription. Appearance settings can apply across accounts on this device."
        case .privacyAds:
            return "Local display controls do not change Telegram messages on the server. Telegram privacy settings are separate and can affect your account on other clients."
        case .messageFilters:
            return "Filters affect local conversation display. Rules do not delete or edit messages on Telegram’s servers. Use the chat’s reveal control to inspect hidden content."
        case .appearance:
            return "Native themes, wallpapers, supported app icons and text-size controls apply immediately where supported. Appearance preferences may be shared by accounts on this device."
        case .chatInterface:
            return "Chat appearance uses the native renderer. Folder features that require server-side Premium remain subject to Telegram’s entitlement checks."
        case .advanced:
            return "Resetting active features changes only this account’s local AyuGram switches. Existing archives, filter rules, exclusions, Telegram sessions and encryption keys are kept. No application code is downloaded or executed."
        case .storageBackup:
            return "The archive remains local. Import/export uses a password and a file destination you explicitly choose; there is no automatic synchronization or developer-operated upload service. Quotas and deletion controls are in Capture & history settings. Clearing the normal cache does not clear the archive; removing the account can remove its storage."
        case .smartInbox:
            return "Smart Inbox combines local message filters with Telegram’s native chat folders. Filters only affect this device; server-side folder limits and Premium entitlements remain unchanged."
        case .chatStudio:
            return "Chat Studio collects native themes, wallpapers, chat folders, local accents, bundled icons and motion controls. It does not grant server-side Premium features."
        case .mediaVault:
            return "Media Vault opens the encrypted local archive, export tools and native download settings. A thumbnail is never treated as a complete video or voice file; only fully captured media can be played or exported."
        case .ultraBatterySaver:
            return "Ultra Battery Saver applies a reversible local profile: reduced motion, reduced translucency, autoplay off, sticker and emoji loops off, background work off, preloading off, and a 20% activation threshold. It does not change iOS system settings or guarantee that iOS stops all background activity."
        }
    }
}
