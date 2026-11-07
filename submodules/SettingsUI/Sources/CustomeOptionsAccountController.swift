// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramUIPreferences
import TelegramPresentationData
import AccountContext
import StorageUsageScreen

private final class CustomeOptionsNavigation {
    weak var controller: CustomeOptionsController?
    var reducedMotion = true

    func push(_ next: ViewController) {
        (self.controller?.navigationController as? NavigationController)?.pushViewController(next,
            animated: !self.reducedMotion && !UIAccessibility.isReduceMotionEnabled, completion: {})
    }
}

private func customeOptionsPalette(_ presentation: PresentationData, accent: LocalClientAccentPreset = .ayu) -> CustomeOptionsPalette {
    let theme = presentation.theme
    let color = accent.rgb.map { UIColor(red: CGFloat(($0 >> 16) & 255) / 255,
        green: CGFloat(($0 >> 8) & 255) / 255, blue: CGFloat($0 & 255) / 255, alpha: 1) } ?? theme.list.itemAccentColor
    return CustomeOptionsPalette(background: theme.list.blocksBackgroundColor,
        surface: theme.list.itemBlocksBackgroundColor, text: theme.list.itemPrimaryTextColor,
        secondaryText: theme.list.itemSecondaryTextColor, separator: theme.list.itemBlocksSeparatorColor,
        accent: color, dark: theme.overallDarkAppearance)
}

private func customeArchiveDetail(_ settings: LocalArchiveSettings, _ status: LocalArchiveStatus) -> String {
    guard settings.isSupported else { return "Saved archive preferences need a compatible app version." }
    switch status {
    case .disabled: return "Off · previously saved content is kept."
    case .browsing: return "Capture off · saved archive available."
    case .locked: return "Protected storage is locked. Unlock this device to continue."
    case .opening: return "Opening this account’s encrypted archive…"
    case .ready: return settings.enabled ? "On · eligible received content and edits are captured locally." : "Capture off · saved archive available."
    case .writing: return "Saving eligible message history and available media."
    case .purging: return "Requested archive deletion is in progress."
    case .unavailable: return "Archive unavailable · existing files and keys are kept."
    case .invalidPendingCapture: return "A capture could not be saved. Open history settings for status."
    }
}

private func customeGhostDetail(_ settings: LocalGhostSettings, story: LocalStoryPrivacySettingsState) -> String {
    var protections: [String] = []
    if settings.enabled {
        if settings.suppressReadReceipts { protections.append("ordinary read receipts") }
        if settings.suppressOnlineStatus { protections.append("online update requests") }
        if settings.suppressOnlineStatus && settings.automaticOffline { protections.append("offline attempts") }
        if settings.suppressTyping { protections.append("typing") }
        if settings.suppressStickerSelection { protections.append("stickers") }
        if settings.suppressRecording { protections.append("recording") }
        if settings.suppressUploads { protections.append("uploads") }
        if settings.suppressGamePlay { protections.append("game activity") }
    }
    switch story {
    case let .available(value): if value.suppressViewReceipts { protections.append("story views") }
    case .unsupported: protections.append("story sending blocked; saved policy is read-only")
    }
    return protections.isEmpty ? "Off · open Ghost settings to choose protections." : "Selected protection: " + protections.joined(separator: ", ") + "."
}


func ultraBatterySaverSettings(from current: MediaAutoDownloadSettings) -> MediaAutoDownloadSettings {
    var updated = current
    var energy = updated.energyUsageSettings
    energy.activationThreshold = 20
    energy.autoplayVideo = false
    energy.autoplayGif = false
    energy.loopStickers = false
    energy.loopEmoji = false
    energy.fullTranslucency = false
    energy.extendBackgroundWork = false
    energy.autodownloadInBackground = false
    updated.energyUsageSettings = energy
    updated.downloadInBackground = false
    return updated
}

func restoringUltraBatterySaverSettings(from current: MediaAutoDownloadSettings, energy: EnergyUsageSettings, downloadInBackground: Bool) -> MediaAutoDownloadSettings {
    var updated = current
    updated.energyUsageSettings = energy
    updated.downloadInBackground = downloadInBackground
    return updated
}

private enum UltraBatterySaverStorage {
    static let active = "org.livara.telegram.ultra-battery.active.v1"
    static let energySnapshot = "org.livara.telegram.ultra-battery.energy.v1"
    static let backgroundSnapshot = "org.livara.telegram.ultra-battery.background.v1"
    static let reduceMotionSnapshot = "org.livara.telegram.ultra-battery.motion.v1"
}

private func applyUltraBatterySaver(context: AccountContext, enabled: Bool, completion: @escaping (Bool) -> Void) {
    let defaults = UserDefaults.standard
    let cosmetics = context.account.localClientCosmetics

    if enabled {
        let current = context.sharedContext.currentAutomaticMediaDownloadSettings
        if defaults.data(forKey: UltraBatterySaverStorage.energySnapshot) == nil {
            guard let data = try? JSONEncoder().encode(current.energyUsageSettings) else {
                completion(false)
                return
            }
            defaults.set(data, forKey: UltraBatterySaverStorage.energySnapshot)
            defaults.set(current.downloadInBackground, forKey: UltraBatterySaverStorage.backgroundSnapshot)
        }

        let _ = (cosmetics.settings |> take(1) |> deliverOnMainQueue).startStandalone(next: { state in
            if case let .available(value) = state {
                defaults.set(value.reduceMotion, forKey: UltraBatterySaverStorage.reduceMotionSnapshot)
            } else {
                defaults.set(false, forKey: UltraBatterySaverStorage.reduceMotionSnapshot)
            }

            let update = updateMediaDownloadSettingsInteractively(accountManager: context.sharedContext.accountManager) { current in
                ultraBatterySaverSettings(from: current)
            }
            let _ = update.startStandalone()
            let _ = (cosmetics.setReducedMotion(true) |> deliverOnMainQueue).startStandalone(next: { saved in
                defaults.set(true, forKey: UltraBatterySaverStorage.active)
                completion(saved)
            })
        })
    } else {
        guard let data = defaults.data(forKey: UltraBatterySaverStorage.energySnapshot),
              let previousEnergy = try? JSONDecoder().decode(EnergyUsageSettings.self, from: data) else {
            completion(false)
            return
        }
        let previousBackground = defaults.bool(forKey: UltraBatterySaverStorage.backgroundSnapshot)
        let previousMotion = defaults.bool(forKey: UltraBatterySaverStorage.reduceMotionSnapshot)
        let update = updateMediaDownloadSettingsInteractively(accountManager: context.sharedContext.accountManager) { current in
            restoringUltraBatterySaverSettings(from: current, energy: previousEnergy, downloadInBackground: previousBackground)
        }
        let _ = update.startStandalone()
        let _ = (cosmetics.setReducedMotion(previousMotion) |> deliverOnMainQueue).startStandalone(next: { _ in
            defaults.removeObject(forKey: UltraBatterySaverStorage.active)
            defaults.removeObject(forKey: UltraBatterySaverStorage.energySnapshot)
            defaults.removeObject(forKey: UltraBatterySaverStorage.backgroundSnapshot)
            defaults.removeObject(forKey: UltraBatterySaverStorage.reduceMotionSnapshot)
            completion(true)
        })
    }
}

private func presentUltraBatterySaver(context: AccountContext, navigation: CustomeOptionsNavigation) {
    let defaults = UserDefaults.standard
    let enabled = defaults.bool(forKey: UltraBatterySaverStorage.active)
    let previousMotion = defaults.bool(forKey: UltraBatterySaverStorage.reduceMotionSnapshot)
    let alert = UIAlertController(
        title: enabled ? "Ultra Battery Saver is on" : "Enable Ultra Battery Saver?",
        message: enabled
            ? "Restore the power, autoplay, translucency and motion settings that were active before this profile."
            : "Turn off autoplay, animated loops, translucency, background work and preloading. Reduce motion and activate Telegram’s power-saving profile at 20% battery. Your previous settings will be saved for restoration.",
        preferredStyle: .alert
    )
    alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
    alert.addAction(UIAlertAction(title: enabled ? "Restore settings" : "Enable", style: .default, handler: { _ in
        applyUltraBatterySaver(context: context, enabled: !enabled, completion: { success in
            DispatchQueue.main.async {
                navigation.reducedMotion = enabled ? previousMotion : true
                let result = UIAlertController(
                    title: success ? (enabled ? "Settings restored" : "Ultra Battery Saver enabled") : "Some settings were unavailable",
                    message: success ? "The selected profile has been applied." : "Power settings were applied where supported. A local motion preference may be unavailable in this account version.",
                    preferredStyle: .alert
                )
                result.addAction(UIAlertAction(title: "OK", style: .default))
                navigation.controller?.present(result, animated: true)
            }
        })
    }))
    navigation.controller?.present(alert, animated: true)
}

private func customeOptionsState(context: AccountContext) -> Signal<CustomeOptionsState, NoError> {
    let archive = context.account.localArchive
    let archiveSettings: Signal<LocalArchiveSettings, NoError> = archive?.settings ?? .single(LocalArchiveSettings())
    let archiveStatus: Signal<LocalArchiveStatus, NoError> = archive?.status ?? .single(.unavailable)
    return combineLatest(archiveSettings, archiveStatus, context.account.localGhost.settings,
                         context.account.localStoryPrivacy.settings, context.account.localMessageFilter.settings)
    |> map { settings, status, ghost, story, filters in
        var result = CustomeOptionsState(archiveEnabled: settings.enabled,
            archiveAvailable: archive != nil && settings.isSupported,
            archiveDetail: archive == nil ? "Archive capture is unavailable for this account context." : customeArchiveDetail(settings, status))
        switch ghost {
        case let .available(value):
            result.ghostAvailable = true
            result.ghostEnabled = value.enabled
            result.ghostDetail = customeGhostDetail(value, story: story)
        case .unsupported:
            result.ghostEnabled = true
            result.ghostAvailable = false
            result.ghostDetail = "Covered activity is blocked; saved privacy settings need a compatible version."
        }
        switch filters {
        case let .available(value):
            result.filtersAvailable = true
            result.filtersEnabled = value.enabled
            let count = value.rules.count
            result.filtersDetail = value.enabled ? "On · \(count) local rule\(count == 1 ? "" : "s")." : "Off · \(count) saved rule\(count == 1 ? "" : "s") kept."
        case .unsupported:
            result.filtersAvailable = false
            result.filtersDetail = "Saved filters are read-only in this app version."
        }
        return result
    }
    |> distinctUntilChanged
}

/// Opens the account-bound AyuGram dashboard. Every mutable control below calls
/// an existing account facade; no global preference store or auth proxy is used.
public func customeOptionsController(context: AccountContext, category: CustomeOptionsCategory? = nil, initialQuery: String = "") -> ViewController {
    let navigation = CustomeOptionsNavigation()
    let presentation = context.sharedContext.currentPresentationData.with { $0 }
    let controller = CustomeOptionsController(mode: category.map(CustomeOptionsMode.category) ?? .dashboard,
        palette: customeOptionsPalette(presentation),
        navigationBarPresentationData: NavigationBarPresentationData(presentationTheme: presentation.theme, presentationStrings: presentation.strings),
        state: customeOptionsState(context: context),
        open: { action in
            switch action {
            case let .category(category): navigation.push(customeOptionsController(context: context, category: category))
            case .ghostSettings: navigation.push(localGhostSettingsController(context: context))
            case .archiveSettings: navigation.push(localArchiveSettingsController(context: context))
            case .archiveGallery: navigation.push(localArchiveMediaGalleryController(context: context))
            case .timedMediaInformation: navigation.push(localTimedMediaInformationController(context: context))
            case .mediaSaving: navigation.push(dataAndStorageController(context: context))
            case .privacyAds: navigation.push(localPrivacyAdsSettingsController(context: context))
            case .privacySettings: navigation.push(makePrivacyAndSecurityController(context: context))
            case .messageFilters: navigation.push(localMessageFilterSettingsController(context: context))
            case .appearance: navigation.push(localAppearanceOptionsController(context: context))
            case .chatFolders:
                navigation.push(localFolderAppearanceController(context: context))
            case .animationSettings: navigation.push(energySavingSettingsScreen(context: context))
            case .localCosmetics: navigation.push(localClientCosmeticsController(context: context))
            case .archiveBackup: navigation.push(localArchiveTransferController(context: context))
            case .storageUsage:
                navigation.push(StorageUsageScreen(context: context, makeStorageUsageExceptionsScreen: { category in
                    return storageUsageExceptionsScreen(context: context, category: category)
                }))
            case .resetActiveFeatures:
                // The renderer presents an explicit confirmation before reset.
                break
            case .ultraBatterySaver:
                presentUltraBatterySaver(context: context, navigation: navigation)
            }
        },
        setToggle: { toggle, enabled, completion in
            let operation: Signal<Bool, NoError>
            switch toggle {
            case .archive: operation = context.account.localArchive?.setEnabled(enabled) ?? .single(false)
            case .ghostActivity: operation = context.account.localGhost.setEnabled(enabled)
            case .messageFilters: operation = context.account.localMessageFilter.setEnabled(enabled)
            }
            let _ = (operation |> deliverOnMainQueue).startStandalone(next: completion)
        },
        reset: { completion in
            // Only active flags change. Quotas, generations, exclusions, saved
            // records, filter rules, encryption keys and Telegram sessions stay.
            // Individual guarded transactions can fail; report a partial reset.
            let capture = context.account.localArchive?.setEnabled(false) ?? .single(true)
            let operation = combineLatest(capture, context.account.localGhost.setEnabled(false),
                context.account.localStoryPrivacy.setSuppressed(false),
                context.account.localMessageFilter.setEnabled(false),
                context.account.localPrivacyAds.setHideSponsoredMessages(false))
            |> map { capture, ghost, stories, filters, ads in capture && ghost && stories && filters && ads }
            let _ = (operation |> deliverOnMainQueue).startStandalone(next: completion)
        })
    navigation.controller = controller
    if !initialQuery.isEmpty { controller.setSearchQuery(initialQuery) }
    controller.retainDisposable((combineLatest(context.sharedContext.presentationData, context.account.localClientCosmetics.settings)
    |> deliverOnMainQueue).start(next: { [weak controller] presentation, local in
        let accent: LocalClientAccentPreset
        switch local {
        case let .available(settings): accent = settings.accent; navigation.reducedMotion = settings.reduceMotion
        case .unsupported: accent = .nativeTheme; navigation.reducedMotion = true
        }
        controller?.updateAppearance(palette: customeOptionsPalette(presentation, accent: accent),
            navigationBarPresentationData: NavigationBarPresentationData(presentationTheme: presentation.theme, presentationStrings: presentation.strings))
    }))
    return controller
}

/// A functional per-account local-cosmetics screen. The native theme and chosen
/// iOS icon remain device-wide; only this local preference document is reset.
public func localClientCosmeticsController(context: AccountContext) -> ViewController {
    let cosmetics = context.account.localClientCosmetics
    let presentation = context.sharedContext.currentPresentationData.with { $0 }
    let state = cosmetics.settings |> map { state -> LocalClientCosmeticsViewState in
        switch state {
        case let .available(settings):
            return LocalClientCosmeticsViewState(presetId: settings.accent.rawValue,
                reduceMotion: settings.reduceMotion, allowBundledPremiumIcons: settings.allowBundledPremiumIcons,
                editable: true, information: "Saved locally for this Telegram account.")
        case .unsupported:
            return LocalClientCosmeticsViewState(information: "Saved appearance preferences belong to a future or unsupported version and are kept read-only.")
        }
    }
    let navigation = LocalCosmeticsNavigation()
    let save: (Signal<Bool, NoError>, @escaping (Bool) -> Void) -> Void = { operation, completed in
        let _ = (operation |> deliverOnMainQueue).startStandalone(next: completed)
    }
    let controller = LocalClientCosmeticsController(palette: customeOptionsPalette(presentation),
        navigationBarPresentationData: NavigationBarPresentationData(presentationTheme: presentation.theme, presentationStrings: presentation.strings),
        choices: LocalClientAccentPreset.allCases.map { LocalClientCosmeticsChoice(id: $0.rawValue, title: $0.title, rgb: $0.rgb) },
        state: state, selectAccent: { id, completed in
            guard let accent = LocalClientAccentPreset(rawValue: id) else { completed(false); return }
            save(cosmetics.setAccent(accent), completed)
        }, setReducedMotion: { reduced, completed in save(cosmetics.setReducedMotion(reduced), completed) },
        setBundledIcons: { allowed, completed in save(cosmetics.setAllowBundledPremiumIcons(allowed), completed) },
        reset: { completed in save(cosmetics.reset(), completed) }, open: { action in
            switch action {
            case .appearance: navigation.push(themeSettingsController(context: context))
            case .animationSettings: navigation.push(energySavingSettingsScreen(context: context))
            default: break
            }
        })
    navigation.controller = controller
    controller.retainDisposable((combineLatest(context.sharedContext.presentationData, cosmetics.settings)
    |> deliverOnMainQueue).start(next: { [weak controller] presentation, state in
        let accent: LocalClientAccentPreset
        switch state {
        case let .available(settings): accent = settings.accent; navigation.reducedMotion = settings.reduceMotion
        case .unsupported: accent = .nativeTheme; navigation.reducedMotion = true
        }
        controller?.updateAppearance(palette: customeOptionsPalette(presentation, accent: accent),
            navigationBarPresentationData: NavigationBarPresentationData(presentationTheme: presentation.theme, presentationStrings: presentation.strings))
    }))
    return controller
}

private final class LocalCosmeticsNavigation {
    weak var controller: LocalClientCosmeticsController?
    var reducedMotion = true
    func push(_ controller: ViewController) {
        (self.controller?.navigationController as? NavigationController)?.pushViewController(controller,
            animated: !self.reducedMotion && !UIAccessibility.isReduceMotionEnabled, completion: {})
    }
}
