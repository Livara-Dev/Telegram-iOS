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
                title: "Ordinary Message Archive", label: "Saving, exclusions and storage",
                labelStyle: .detailText, sectionId: self.section, style: .blocks,
                action: { arguments.openArchive() })
        }
    }
}

/// A capability explanation, not a preservation switch. Timed and view-once
/// content continues through Telegram's original consumption and expiry paths.
public func localTimedMediaInformationController(context: AccountContext) -> ViewController {
    let arguments = LocalTimedMediaInformationArguments()
    let signal = context.sharedContext.presentationData
    |> deliverOnMainQueue
    |> map { presentationData -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let entries: [LocalTimedMediaInformationRow] = [
            LocalTimedMediaInformationRow(stableId: 0, section: 0, content: .heading("EXPIRATION REMAINS ACTIVE")),
            LocalTimedMediaInformationRow(stableId: 1, section: 0, content: .text("This client does not preserve timed or view-once content. Opening accessible media does not make it eligible for the encrypted archive. Telegram's original consumption, expiration and cache cleanup remain active.")),
            LocalTimedMediaInformationRow(stableId: 2, section: 1, content: .heading("SUPPORTED BEHAVIOR BY TYPE")),
            LocalTimedMediaInformationRow(stableId: 3, section: 1, content: .text("Timed photos — not archived. Their self-destruct timer continues normally.")),
            LocalTimedMediaInformationRow(stableId: 4, section: 1, content: .text("Timed videos — not archived. Playback does not retain an independent copy.")),
            LocalTimedMediaInformationRow(stableId: 5, section: 1, content: .text("View-once photos — not archived. Viewing does not disable consumption or expiration.")),
            LocalTimedMediaInformationRow(stableId: 6, section: 1, content: .text("View-once videos — not archived. No replayable copy is created.")),
            LocalTimedMediaInformationRow(stableId: 7, section: 1, content: .text("Disappearing voice messages — not archived. This includes voice messages marked for one-time playback.")),
            LocalTimedMediaInformationRow(stableId: 8, section: 1, content: .text("Disappearing video messages — not archived. This includes round video messages marked for one-time playback.")),
            LocalTimedMediaInformationRow(stableId: 9, section: 1, content: .text("Chat auto-delete messages — excluded while they carry an expiration timer, including text and ordinary media in that message.")),
            LocalTimedMediaInformationRow(stableId: 10, section: 1, content: .text("Secret Chats — excluded from the archive, including messages without a self-destruct timer.")),
            LocalTimedMediaInformationRow(stableId: 11, section: 2, content: .heading("WHY THERE IS NO PRESERVATION SWITCH")),
            LocalTimedMediaInformationRow(stableId: 12, section: 2, content: .text("Telegram API Terms of Service section 1.4 prohibits preventing self-destructing content from disappearing. No contractually permitted preservation mode has been established for the types above, so this app does not offer a switch that promises to save them.")),
            LocalTimedMediaInformationRow(stableId: 13, section: 2, content: .text("An expired placeholder is not recoverable media. The archive does not bypass encryption, access restrictions or incomplete downloads, and it cannot recover content that this client never received.")),
            LocalTimedMediaInformationRow(stableId: 14, section: 3, content: .heading("ORDINARY MESSAGES AND MEDIA")),
            LocalTimedMediaInformationRow(stableId: 15, section: 3, content: .archive),
            LocalTimedMediaInformationRow(stableId: 16, section: 3, content: .text("For eligible ordinary cloud messages, enable deletion and edit capture in the archive settings. Already completed local photos, videos, GIFs, voice messages and documents can be copied into encrypted storage within your limits. Missing or incomplete files are not downloaded. Enabling that archive does not change any expiration rule above."))
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
