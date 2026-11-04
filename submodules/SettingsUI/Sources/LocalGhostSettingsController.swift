// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext

private enum LocalGhostSetting: Int {
    case enabled
    case typing
    case stickers
    case recording
    case uploads
    case games
    case storyViews
}

private struct LocalGhostRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case toggle(LocalGhostSetting, String, Bool, Bool)
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
        case let .toggle(setting, title, value, enabled):
            return ItemListSwitchItem(presentationData: presentationData, systemStyle: .glass, title: title, value: value, enableInteractiveChanges: false, enabled: enabled, maximumNumberOfLines: 2, sectionId: self.section, style: .blocks, updated: { value in
                arguments.set(setting, value)
            })
        }
    }
}

private final class LocalGhostArguments {
    let set: (LocalGhostSetting, Bool) -> Void

    init(set: @escaping (LocalGhostSetting, Bool) -> Void) {
        self.set = set
    }
}

/// Independent account-scoped controls for activity indicators and story views.
/// Message read receipts and presence are outside these controls.
public func localGhostSettingsController(context: AccountContext) -> ViewController {
    let ghost = context.account.localGhost
    let storyPrivacy = context.account.localStoryPrivacy
    let actionDisposable = MetaDisposable()
    let actionState = Promise<(Bool, Bool)>((false, false))
    let arguments = LocalGhostArguments(set: { setting, value in
        actionState.set(.single((true, false)))
        let operation: Signal<Bool, NoError>
        if case .storyViews = setting {
            operation = storyPrivacy.setSuppressed(value)
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
                case .storyViews:
                    break
                }
            }
        }
        actionDisposable.set((operation |> deliverOnMainQueue).start(next: { success in
            actionState.set(.single((false, !success)))
        }))
    })

    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, ghost.settings, storyPrivacy.settings, actionState.get())
    |> map { presentationData, state, storyState, actionState -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let settings: LocalGhostSettings
        let supported: Bool
        switch state {
        case let .available(value):
            settings = value
            supported = true
        case .unsupported:
            // The worker blocks all covered categories when an existing policy
            // cannot be decoded. Do not display a misleading disabled default.
            settings = LocalGhostSettings(enabled: true)
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
                ? "Enabled for this account. Stories viewed here may remain unread locally. A receipt already sent to Telegram cannot be recalled."
                : "Off. This client can send story view receipts."
        case .unsupported:
            // Unknown story preferences block their receipt paths independently
            // of the activity settings. Never present that state as switched off.
            suppressStoryViews = true
            canChangeStoryViews = false
            storyStatus = "This version cannot read the saved story-privacy settings. Story-view receipt sending is blocked and those settings have been kept unchanged."
        }
        var active: [String] = []
        if settings.enabled {
            if settings.suppressTyping { active.append("typing") }
            if settings.suppressStickerSelection { active.append("sticker selection") }
            if settings.suppressRecording { active.append("voice and video recording") }
            if settings.suppressUploads { active.append("media and file uploads") }
            if settings.suppressGamePlay { active.append("game activity") }
        }
        let status: String
        if !supported {
            status = "This version cannot read the saved privacy settings. The activity indicators below are blocked until a compatible version can read them. Existing settings have been kept unchanged."
        } else if !settings.enabled {
            status = "Off. This feature is not hiding activity indicators. Your selections are saved for this account."
        } else if active.isEmpty {
            status = "On, with no indicators selected. Choose the activity indicators to hide below."
        } else {
            status = "Suppression enabled for this account: " + active.joined(separator: ", ") + "."
        }

        var entries: [LocalGhostRow] = [
            LocalGhostRow(stableId: 0, section: 0, content: .heading("EXPERIMENTAL · THIS ACCOUNT")),
            LocalGhostRow(stableId: 1, section: 0, content: .toggle(.enabled, "Hide selected activity indicators", settings.enabled, canChange)),
            LocalGhostRow(stableId: 2, section: 0, content: .text(status)),
            LocalGhostRow(stableId: 3, section: 1, content: .heading("CHOOSE INDICATORS")),
            LocalGhostRow(stableId: 4, section: 1, content: .toggle(.typing, "Typing", settings.suppressTyping, canChange)),
            LocalGhostRow(stableId: 5, section: 1, content: .toggle(.stickers, "Sticker selection", settings.suppressStickerSelection, canChange)),
            LocalGhostRow(stableId: 6, section: 1, content: .toggle(.recording, "Voice and video recording", settings.suppressRecording, canChange)),
            LocalGhostRow(stableId: 7, section: 1, content: .toggle(.uploads, "Media and file uploads", settings.suppressUploads, canChange)),
            LocalGhostRow(stableId: 8, section: 1, content: .toggle(.games, "Game activity", settings.suppressGamePlay, canChange)),
            LocalGhostRow(stableId: 13, section: 2, content: .heading("STORY VIEW RECEIPTS")),
            LocalGhostRow(stableId: 14, section: 2, content: .toggle(.storyViews, "Suppress story view receipts", suppressStoryViews, canChangeStoryViews)),
            LocalGhostRow(stableId: 15, section: 2, content: .text(storyStatus)),
            LocalGhostRow(stableId: 16, section: 2, content: .text("Independent of the activity-indicator switch and its quick toggle. When suppression is turned off, viewing a later story can mark earlier stories as read on Telegram. Story reactions, replies and other Telegram clients can reveal activity.")),
            LocalGhostRow(stableId: 9, section: 3, content: .heading("LIMITS OF THIS BUILD")),
            LocalGhostRow(stableId: 10, section: 3, content: .text("Message read receipts and online status are not suppressed. Calls and emoji interactions remain visible. Sending messages and using another Telegram client can reveal activity.")),
            LocalGhostRow(stableId: 11, section: 3, content: .text("Activity settings apply to new indicators sent by this client. An indicator already sent cannot be recalled and may remain visible briefly. Ghost Mode does not guarantee invisibility."))
        ]
        if actionState.1 {
            entries.append(LocalGhostRow(stableId: 12, section: 3, content: .text("The setting could not be saved. Existing privacy settings have been kept.")))
        }
        let controller = ItemListControllerState(presentationData: ItemListPresentationData(presentationData), title: .text("Ghost Mode"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: ItemListPresentationData(presentationData), entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed {
        actionDisposable.dispose()
    }
    return ItemListController(context: context, state: signal)
}
