// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext
import PresentationDataUtils

private enum LocalGhostSetting: Int {
    case enabled
    case typing
    case stickers
    case recording
    case uploads
    case games
    case storyViews
    case readReceipts
    case onlineStatus
    case automaticOffline
    case storyOpeningWarning
    case readAfterSend
}

private struct LocalGhostRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case toggle(LocalGhostSetting, String, Bool, Bool)
        case reset(Bool)
    }

    let stableId: Int
    let section: ItemListSectionId
    let content: Content

    static func < (lhs: LocalGhostRow, rhs: LocalGhostRow) -> Bool {
        if lhs.section != rhs.section {
            return lhs.section < rhs.section
        }
        return lhs.stableId < rhs.stableId
    }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalGhostArguments
        switch self.content {
        case let .heading(text):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: text, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .reset(enabled):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass, title: "Reset Ghost Mode Settings", kind: enabled ? .destructive : .disabled, alignment: .natural, sectionId: self.section, style: .blocks, action: {
                if enabled { arguments.reset() }
            })
        case let .toggle(setting, title, value, enabled):
            return ItemListSwitchItem(presentationData: presentationData, systemStyle: .glass, title: title, value: value, enableInteractiveChanges: false, enabled: enabled, maximumNumberOfLines: 2, sectionId: self.section, style: .blocks, updated: { value in
                arguments.set(setting, value)
            })
        }
    }
}

private final class LocalGhostArguments {
    let set: (LocalGhostSetting, Bool) -> Void
    let reset: () -> Void

    init(set: @escaping (LocalGhostSetting, Bool) -> Void, reset: @escaping () -> Void) {
        self.set = set
        self.reset = reset
    }
}

/// Real account-scoped producers and dispatch workers observe these preferences.
/// Expiry/Secret Chat protocols and other clients remain outside suppression.
public func localGhostSettingsController(context: AccountContext) -> ViewController {
    var presentController: ((ViewController) -> Void)?
    let ghost = context.account.localGhost
    let storyPrivacy = context.account.localStoryPrivacy
    let actionDisposable = MetaDisposable()
    let actionState = Promise<(Bool, Bool)>((false, false))
    let arguments = LocalGhostArguments(set: { setting, value in
        actionState.set(.single((true, false)))
        let operation: Signal<Bool, NoError>
        if case .storyViews = setting {
            operation = storyPrivacy.setSuppressed(value)
        } else if case .storyOpeningWarning = setting {
            operation = ghost.setStoryOpeningWarningEnabled(value)
        } else if case .readAfterSend = setting {
            operation = ghost.setReadAfterSendEnabled(value)
        } else {
            operation = ghost.updateSettings { settings in
                switch setting {
                case .enabled:
                    settings.enabled = value
                case .typing:
                    settings.suppressTyping = value
                case .stickers:
                    settings.suppressStickerSelection = value
                case .recording:
                    settings.suppressRecording = value
                case .uploads:
                    settings.suppressUploads = value
                case .games:
                    settings.suppressGamePlay = value
                case .readReceipts:
                    settings.suppressReadReceipts = value
                case .onlineStatus:
                    settings.suppressOnlineStatus = value
                case .automaticOffline:
                    settings.automaticOffline = value
                case .storyViews, .storyOpeningWarning, .readAfterSend:
                    break
                }
            }
        }
        actionDisposable.set((operation |> deliverOnMainQueue).start(next: { success in
            actionState.set(.single((false, !success)))
        }))
    }, reset: {
        let presentationData = context.sharedContext.currentPresentationData.with { $0 }
        presentController?(textAlertController(context: context, title: "Reset Ghost Mode?", text: "This turns Ghost Mode and story-view suppression off for this account, and restores the story-opening warning. New reads and activity may be visible to other people.", actions: [
            TextAlertAction(type: .genericAction, title: presentationData.strings.Common_Cancel, action: {}),
            TextAlertAction(type: .defaultAction, title: "Reset", action: {
                actionState.set(.single((true, false)))
                actionDisposable.set((ghost.resetSettings() |> deliverOnMainQueue).start(next: { success in
                    actionState.set(.single((false, !success)))
                }))
            })
        ]))
    })

    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, ghost.settings, storyPrivacy.settings, ghost.storyOpeningWarningSettings, ghost.readAfterSendSettings, actionState.get())
    |> map { presentationData, state, storyState, warningState, readAfterSendState, actionState -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let settings: LocalGhostSettings
        let supported: Bool
        switch state {
        case let .available(value):
            settings = value
            supported = true
        case .unsupported:
            // The worker blocks all covered categories when an existing policy
            // cannot be decoded. Do not display a misleading disabled default.
            settings = LocalGhostSettings(enabled: true, automaticOffline: false)
            supported = false
        }
        let canChange = supported && !actionState.0
        let suppressStoryViews: Bool
        let canChangeStoryViews: Bool
        let storyStatus: String
        switch storyState {
        case let .available(settings):
            suppressStoryViews = settings.suppressViewReceipts
            canChangeStoryViews = !actionState.0
            storyStatus = settings.suppressViewReceipts
                ? "Story-view privacy is on."
                : "Off. This client can send story view receipts."
        case .unsupported:
            // Unknown story preferences block their receipt paths independently
            // of the activity settings. Never present that state as switched off.
            suppressStoryViews = true
            canChangeStoryViews = false
            storyStatus = "Saved story settings are read-only. View receipts remain blocked."
        }
        let warnBeforeOpening: Bool
        let canChangeWarning: Bool
        switch warningState {
        case let .available(settings):
            warnBeforeOpening = settings.enabled
            canChangeWarning = !actionState.0
        case .unsupported:
            warnBeforeOpening = true
            canChangeWarning = false
        }
        let readAfterSend: Bool
        let canChangeReadAfterSend: Bool
        switch readAfterSendState {
        case let .available(settings):
            readAfterSend = settings.enabled
            canChangeReadAfterSend = !actionState.0
        case .unsupported:
            readAfterSend = false
            canChangeReadAfterSend = false
        }
        var active: [String] = []
        if settings.enabled {
            if settings.suppressReadReceipts { active.append("ordinary message read receipts") }
            if settings.suppressOnlineStatus { active.append("online-status updates") }
            if settings.suppressTyping { active.append("typing") }
            if settings.suppressStickerSelection { active.append("sticker selection") }
            if settings.suppressRecording { active.append("voice and video recording") }
            if settings.suppressUploads { active.append("media and file uploads") }
            if settings.suppressGamePlay { active.append("game activity") }
        }
        let status: String
        if !supported {
            status = "Saved settings are read-only. Existing protections remain active."
        } else if !settings.enabled {
            status = "Off. Your selections are kept."
        } else if active.isEmpty {
            status = "On, with no protections selected. Choose the protections below."
        } else {
            status = "On · \(active.count) protections"
        }

        var entries: [LocalGhostRow] = [
            LocalGhostRow(stableId: 0, section: 0, content: .heading("AYUGRAM PRIVACY · THIS ACCOUNT")),
            LocalGhostRow(stableId: 1, section: 0, content: .toggle(.enabled, "Ghost Mode", settings.enabled, canChange)),
            LocalGhostRow(stableId: 2, section: 0, content: .text(status)),
            LocalGhostRow(stableId: 20, section: 1, content: .heading("MESSAGE READ RECEIPTS")),
            LocalGhostRow(stableId: 21, section: 1, content: .toggle(.readReceipts, "Hide read receipts", settings.suppressReadReceipts, canChange)),
            LocalGhostRow(stableId: 22, section: 1, content: .text("Hide ordinary read receipts. Secret and disappearing-message receipts are unchanged.")),
            LocalGhostRow(stableId: 31, section: 1, content: .toggle(.readAfterSend, "Read after a confirmed send", readAfterSend, canChange && canChangeReadAfterSend)),
            LocalGhostRow(stableId: 32, section: 1, content: .text("Mark the current chat read after a confirmed send. Excludes Secret Chats and threads.")),
            LocalGhostRow(stableId: 23, section: 2, content: .heading("ONLINE STATUS")),
            LocalGhostRow(stableId: 24, section: 2, content: .toggle(.onlineStatus, "Hide online updates", settings.suppressOnlineStatus, canChange)),
            LocalGhostRow(stableId: 25, section: 2, content: .toggle(.automaticOffline, "Return offline automatically", settings.automaticOffline, canChange && settings.suppressOnlineStatus)),
            LocalGhostRow(stableId: 26, section: 2, content: .text("Other sessions, messages and reactions may still reveal activity.")),
            LocalGhostRow(stableId: 3, section: 3, content: .heading("ACTIVITY INDICATORS")),
            LocalGhostRow(stableId: 4, section: 3, content: .toggle(.typing, "Typing", settings.suppressTyping, canChange)),
            LocalGhostRow(stableId: 5, section: 3, content: .toggle(.stickers, "Sticker selection", settings.suppressStickerSelection, canChange)),
            LocalGhostRow(stableId: 6, section: 3, content: .toggle(.recording, "Voice and video recording", settings.suppressRecording, canChange)),
            LocalGhostRow(stableId: 7, section: 3, content: .toggle(.uploads, "Media and file uploads", settings.suppressUploads, canChange)),
            LocalGhostRow(stableId: 8, section: 3, content: .toggle(.games, "Game activity", settings.suppressGamePlay, canChange)),
            LocalGhostRow(stableId: 13, section: 4, content: .heading("STORY VIEW RECEIPTS")),
            LocalGhostRow(stableId: 14, section: 4, content: .toggle(.storyViews, "Suppress story view receipts", suppressStoryViews, canChangeStoryViews)),
            LocalGhostRow(stableId: 15, section: 4, content: .text(storyStatus)),
            LocalGhostRow(stableId: 16, section: 4, content: .text("Independent of Ghost Mode. Replies and reactions can reveal views.")),
            LocalGhostRow(stableId: 29, section: 4, content: .toggle(.storyOpeningWarning, "Warn before opening stories", warnBeforeOpening, canChangeWarning)),
            LocalGhostRow(stableId: 30, section: 4, content: .text("Ask before opening a story.")),
            LocalGhostRow(stableId: 9, section: 5, content: .heading("LIMITS OF THIS BUILD")),
            LocalGhostRow(stableId: 10, section: 5, content: .text("Calls and required expiry signals are unchanged. This is not invisibility.")),
            LocalGhostRow(stableId: 11, section: 5, content: .text("Already sent receipts cannot be recalled. Telegram API restrictions still apply.")),
            LocalGhostRow(stableId: 27, section: 6, content: .reset(canChange && canChangeStoryViews && canChangeWarning && canChangeReadAfterSend)),
            LocalGhostRow(stableId: 28, section: 6, content: .text("Reset keeps chats, messages and saved history."))
        ]
        if actionState.1 {
            entries.append(LocalGhostRow(stableId: 12, section: 5, content: .text("The setting could not be saved. Existing privacy settings have been kept.")))
        }
        let controller = ItemListControllerState(presentationData: ItemListPresentationData(presentationData), title: .text("Ghost Mode"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: ItemListPresentationData(presentationData), entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed {
        actionDisposable.dispose()
    }
    let controller = ItemListController(context: context, state: signal)
    presentController = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    return controller
}
