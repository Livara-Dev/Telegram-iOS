// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramPresentationData
import ItemListUI
import AccountContext

private final class LocalTimedMediaInformationArguments {
    var openArchive: () -> Void = {}
}

private struct LocalTimedMediaInformationRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case archive
    }

    let stableId: Int
    let section: ItemListSectionId
    let content: Content

    static func < (lhs: LocalTimedMediaInformationRow, rhs: LocalTimedMediaInformationRow) -> Bool {
        return lhs.stableId < rhs.stableId
    }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        switch self.content {
        case let .heading(text):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: text, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case .archive:
            let arguments = arguments as! LocalTimedMediaInformationArguments
            return ItemListDisclosureItem(presentationData: presentationData, systemStyle: .glass,
                title: "Archive and Extended Retention", label: "Saving, exclusions and storage",
                labelStyle: .detailText, sectionId: self.section, style: .blocks,
                action: { arguments.openArchive() })
        }
    }
}

/// Account-local opt-in retention. Canonical consumption and expiry are unchanged;
/// this screen describes limits and routes to the actual persisted controls.
public func localTimedMediaInformationController(context: AccountContext) -> ViewController {
    let arguments = LocalTimedMediaInformationArguments()
    let signal = context.sharedContext.presentationData
    |> deliverOnMainQueue
    |> map { presentationData -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let entries: [LocalTimedMediaInformationRow] = [
            LocalTimedMediaInformationRow(stableId: 0, section: 0, content: .heading("EXPERIMENTAL · LOCAL RETENTION")),
            LocalTimedMediaInformationRow(stableId: 1, section: 0, content: .text("The archive can retain independent copies of received content before normal deletion or expiration. Capture and extended retention are off by default. They must be enabled separately for each account.")),
            LocalTimedMediaInformationRow(stableId: 2, section: 1, content: .heading("PHOTOS, VIDEO AND MESSAGES")),
            LocalTimedMediaInformationRow(stableId: 3, section: 1, content: .text("The timed-content option includes timed and view-once photos, videos, voice messages and round video messages, plus messages with chat auto-delete timers. Only complete media bytes already available to this installation can be copied; no expired or unseen content is recovered.")),
            LocalTimedMediaInformationRow(stableId: 4, section: 1, content: .text("The Secret Chat option includes messages from Secret Chats belonging to this installation. Secret content with an expiration timer requires both options. This does not import Secret Chats from another Telegram application or change Telegram's encryption keys.")),
            LocalTimedMediaInformationRow(stableId: 5, section: 2, content: .heading("NORMAL TELEGRAM BEHAVIOR REMAINS")),
            LocalTimedMediaInformationRow(stableId: 6, section: 2, content: .text("Read acknowledgements, timers and canonical message deletion are not disabled by these archive options. Saved copies are shown in View Deleted Messages and Archived Media, not reinserted into Telegram history. This is not an invisibility guarantee.")),
            LocalTimedMediaInformationRow(stableId: 7, section: 2, content: .text("Keeping self-destructing content conflicts with Telegram API Terms section 1.4. Enabling an option does not make it API-compliant or establish another participant's permission.")),
            LocalTimedMediaInformationRow(stableId: 8, section: 3, content: .heading("STORAGE AND LIMITS")),
            LocalTimedMediaInformationRow(stableId: 9, section: 3, content: .text("The separate archive is encrypted and account-bound. Completed copies survive clearing Telegram's cache. Capture can still miss content when a download is incomplete, the device is locked, storage is exhausted, the app is terminated or the cache is removed before the copy completes. Copy-protected and unsupported structured rich content remains excluded.")),
            LocalTimedMediaInformationRow(stableId: 10, section: 3, content: .text("Archive limits and exclusions still apply. Oldest copies may be pruned to fit the selected quota. Disabling capture or an extended option does not erase already saved copies; Clear This Account's Archive removes them.")),
            LocalTimedMediaInformationRow(stableId: 11, section: 4, content: .archive)
        ]
        let data = ItemListPresentationData(presentationData)
        let controllerState = ItemListControllerState(presentationData: data, title: .text("Timed Media"),
            leftNavigationButton: nil, rightNavigationButton: nil,
            backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controllerState, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    let controller = ItemListController(context: context, state: signal)
    arguments.openArchive = { [weak controller] in
        (controller?.navigationController as? NavigationController)?.pushViewController(localArchiveSettingsController(context: context))
    }
    return controller
}
