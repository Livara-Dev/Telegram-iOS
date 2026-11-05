// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext

private enum LocalFolderRowId: Hashable {
    case fixed(Int)
    case folder(Int32)
    case color(Int32)
}

private final class LocalFolderArguments {
    var action: (LocalFolderRowId) -> Void = { _ in }
    var edit: (String) -> Void = { _ in }
    var openFolder: (ChatListFilter) -> Void = { _ in }
}

private struct LocalFolderRow: ItemListNodeEntry {
    enum Content: Equatable {
        case text(String)
        case link(String, String, Bool)
        case folder(ChatListFilter, String, String, Bool)
        case choice(String, Bool, Bool)
        case input(String, Bool)
        case action(String, Bool, Bool)
    }
    let stableId: LocalFolderRowId
    let order: Int
    let section: ItemListSectionId
    let content: Content
    static func < (lhs: LocalFolderRow, rhs: LocalFolderRow) -> Bool { lhs.order < rhs.order }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalFolderArguments
        switch self.content {
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .link(title, detail, enabled):
            return ItemListDisclosureItem(presentationData: presentationData, systemStyle: .glass,
                title: title, enabled: enabled, label: detail, labelStyle: .detailText,
                sectionId: self.section, style: .blocks, action: { if enabled { arguments.action(self.stableId) } })
        case let .folder(filter, title, detail, enabled):
            // Capture the canonical filter represented by this exact visible
            // row, rather than looking an ID up again after a queued update.
            return ItemListDisclosureItem(presentationData: presentationData, systemStyle: .glass,
                title: title, enabled: enabled, label: detail, labelStyle: .detailText,
                sectionId: self.section, style: .blocks, action: { if enabled { arguments.openFolder(filter) } })
        case let .choice(title, checked, enabled):
            return ItemListCheckboxItem(presentationData: presentationData, systemStyle: .glass, title: title,
                style: .left, checked: checked, enabled: enabled, zeroSeparatorInsets: false,
                sectionId: self.section, action: { if enabled { arguments.action(self.stableId) } })
        case let .input(text, enabled):
            return ItemListSingleLineInputItem(presentationData: presentationData, systemStyle: .glass,
                title: NSAttributedString(string: "Local tag"), text: text, placeholder: "Optional · this account only",
                type: .regular(capitalization: false, autocorrection: false), clearType: .always, enabled: enabled,
                sectionId: self.section, textUpdated: { if enabled { arguments.edit($0) } },
                shouldUpdateText: { enabled && $0.utf8.prefix(1025).count <= 1024 }, action: {})
        case let .action(title, enabled, destructive):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass, title: title,
                kind: enabled ? (destructive ? .destructive : .generic) : .disabled,
                alignment: .natural, sectionId: self.section, style: .blocks,
                action: { if enabled { arguments.action(self.stableId) } })
        }
    }
}

private func localFolderListState(presentation: PresentationData, title: String, entries: [LocalFolderRow],
                                  arguments: LocalFolderArguments) -> (ItemListControllerState, (ItemListNodeState, Any)) {
    let data = ItemListPresentationData(presentation)
    return (ItemListControllerState(presentationData: data, title: .text(title), leftNavigationButton: nil,
        rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentation.strings.Common_Back),
        animateChanges: false), (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
}

/// Account-bound entry used by Custome options. Both destinations remain real
/// native screens; native Telegram appearance is unchanged behind this menu.
func localAppearanceOptionsController(context: AccountContext) -> ViewController {
    let arguments = LocalFolderArguments()
    var push: ((ViewController) -> Void)?
    arguments.action = { id in
        switch id {
        case .fixed(1): push?(themeSettingsController(context: context))
        case .fixed(2): push?(localFolderAppearanceController(context: context))
        default: break
        }
    }
    let signal = context.sharedContext.presentationData |> deliverOnMainQueue
    |> map { presentation in
        localFolderListState(presentation: presentation, title: "Appearance & Themes", entries: [
            LocalFolderRow(stableId: .fixed(0), order: 0, section: 0, content: .text("Themes use Telegram's native appearance settings. Folder colors and tags below are local to the selected account and never sent to Telegram.")),
            LocalFolderRow(stableId: .fixed(1), order: 1, section: 1, content: .link("Themes, Icons & Text Size", "Native appearance controls", true)),
            LocalFolderRow(stableId: .fixed(2), order: 2, section: 1, content: .link("Local Folder Colors & Tags", "This account only", true))
        ], arguments: arguments)
    }
    let controller = ItemListController(context: context, state: signal)
    push = { [weak controller] next in (controller?.navigationController as? NavigationController)?.pushViewController(next) }
    return controller
}

func localFolderAppearanceController(context: AccountContext) -> ViewController {
    let preferences = context.account.localFolderAppearance
    let request = MetaDisposable()
    let operation = Promise<(busy: Bool, result: Bool?)>((false, nil))
    let arguments = LocalFolderArguments()
    var push: ((ViewController) -> Void)?
    var present: ((ViewController) -> Void)?
    arguments.openFolder = { filter in push?(localFolderEditorController(context: context, expectedFilter: filter)) }
    arguments.action = { id in
        switch id {
        case .fixed(1):
            push?(context.sharedContext.makeFilterSettingsController(context: context, modal: false, scrollToTags: false, dismissed: nil))
        case .fixed(2):
            let presentation = context.sharedContext.currentPresentationData.with { $0 }
            let sheet = ActionSheetController(presentationData: presentation)
            sheet.setItemGroups([
                ActionSheetItemGroup(items: [
                    ActionSheetTextItem(title: "Remove all local folder colors and tags for this account? Telegram folders, messages and other accounts stay unchanged."),
                    ActionSheetButtonItem(title: "Reset Local Folder Appearance", color: .destructive, action: { [weak sheet] in
                        sheet?.dismissAnimated()
                        operation.set(.single((true, nil)))
                        request.set((preferences.reset() |> deliverOnMainQueue).start(next: { saved in operation.set(.single((false, saved))) }))
                    })
                ]),
                ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentation.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
            ])
            present?(sheet)
        default: break
        }
    }
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, preferences.settings,
        context.engine.peers.updatedChatListFilters(), operation.get())
    |> map { presentation, settings, filters, operation in
        let editable: Bool
        switch settings { case .available: editable = !operation.busy; case .unsupported: editable = false }
        var entries = [
            LocalFolderRow(stableId: .fixed(0), order: 0, section: 0, content: .text("Colors and optional 20-character tags appear on this account's folder tabs and native folder settings list. They do not change folder names, server tags, Premium status or chat membership on Telegram or your other clients.")),
            LocalFolderRow(stableId: .fixed(1), order: 1, section: 1, content: .link("Manage Telegram Folders", "Create, rename and organize native folders", true))
        ]
        var index = 0
        for filter in filters {
            guard case let .filter(id, title, _, _) = filter else { continue }
            let local = localFolderAppearanceProjection(folderId: id, canonicalTitle: title, settings: settings, dark: presentation.theme.overallDarkAppearance)
            let detail: String
            if case let .available(value) = settings, let folder = value.folders[id] {
                detail = [folder.color?.title, folder.tag.isEmpty ? nil : folder.tag].compactMap { $0 }.joined(separator: " · ")
            } else { detail = "Use native appearance" }
            entries.append(LocalFolderRow(stableId: .folder(id), order: 10 + index, section: 2, content: .folder(filter, local.title.text, detail, editable)))
            index += 1
        }
        if index == 0 {
            entries.append(LocalFolderRow(stableId: .fixed(3), order: 1000, section: 2, content: .text("No folders are available. Create one with Manage Telegram Folders, then choose its local appearance here.")))
        }
        if case .unsupported = settings {
            entries.append(LocalFolderRow(stableId: .fixed(4), order: 1001, section: 3, content: .text("Saved folder appearance needs a compatible app version. It remains unchanged and read-only; native folders continue to work.")))
        }
        entries.append(LocalFolderRow(stableId: .fixed(2), order: 1002, section: 3, content: .action("Reset All Local Folder Colors & Tags", editable, true)))
        if let saved = operation.result {
            entries.append(LocalFolderRow(stableId: .fixed(5), order: 1003, section: 3, content: .text(saved ? "Local appearance reset for this account." : "Could not reset local appearance. Existing preferences were kept.")))
        }
        return localFolderListState(presentation: presentation, title: "Local Folder Appearance", entries: entries, arguments: arguments)
    }
    |> afterDisposed { request.dispose() }
    let controller = ItemListController(context: context, state: signal)
    push = { [weak controller] next in (controller?.navigationController as? NavigationController)?.pushViewController(next) }
    present = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    return controller
}

private struct LocalFolderDraft {
    var color: LocalFolderColor?
    var tag: String
}

private func localFolderEditorController(context: AccountContext, expectedFilter: ChatListFilter) -> ViewController {
    let folderId = expectedFilter.id
    let preferences = context.account.localFolderAppearance
    let draft = Promise<LocalFolderDraft?>(nil)
    let operation = Promise<(busy: Bool, result: Bool?)>((false, nil))
    let request = MetaDisposable()
    let arguments = LocalFolderArguments()
    var currentDraft = LocalFolderDraft(color: nil, tag: "")
    // Main-queue state. Once an observed update invalidates the original row,
    // later updates (including already queued ID reuse) cannot revive it.
    var invalidated = false
    let updateDraft: (LocalFolderDraft) -> Void = { value in
        draft.set(.single(value))
        operation.set(.single((false, nil)))
    }
    let save: (LocalFolderDraft) -> Void = { value in
        guard !invalidated else { return }
        operation.set(.single((true, nil)))
        request.set((preferences.setFolder(id: folderId, color: value.color, tag: value.tag, expectedFilter: expectedFilter) |> deliverOnMainQueue).start(next: { saved in
            if saved, let tag = LocalFolderAppearance.normalizedTag(value.tag) {
                draft.set(.single(LocalFolderDraft(color: value.color, tag: tag)))
            }
            operation.set(.single((false, saved)))
        }))
    }
    arguments.edit = { text in var value = currentDraft; value.tag = text; updateDraft(value) }
    arguments.action = { id in
        switch id {
        case let .color(raw):
            var value = currentDraft
            value.color = LocalFolderColor(rawValue: raw)
            updateDraft(value)
        case .fixed(3): save(currentDraft)
        case .fixed(4): save(LocalFolderDraft(color: nil, tag: ""))
        default: break
        }
    }
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, preferences.settings,
        context.engine.peers.updatedChatListFilters(), draft.get(), operation.get())
    |> map { presentation, settings, filters, draft, operation in
        let folder = filters.first(where: { $0.id == folderId })
        if folder != expectedFilter { invalidated = true }
        guard !invalidated, let folder, case let .filter(_, title, _, _) = folder else {
            return localFolderListState(presentation: presentation, title: "Folder Appearance", entries: [
                LocalFolderRow(stableId: .fixed(0), order: 0, section: 0, content: .text("This folder was removed, renamed or changed while the editor was open. Reopen it from Local Folder Appearance before saving. This editor cannot apply its draft to a replacement folder."))
            ], arguments: arguments)
        }
        guard case let .available(settings) = settings else {
            return localFolderListState(presentation: presentation, title: "Folder Appearance", entries: [
                LocalFolderRow(stableId: .fixed(0), order: 0, section: 0, content: .text("Saved folder appearance is read-only in this app version. Existing preferences were kept."))
            ], arguments: arguments)
        }
        currentDraft = draft ?? LocalFolderDraft(color: settings.folders[folderId]?.color, tag: settings.folders[folderId]?.tag ?? "")
        let normalizedTag = LocalFolderAppearance.normalizedTag(currentDraft.tag)
        var entries = [
            LocalFolderRow(stableId: .fixed(0), order: 0, section: 0, content: .text("\(title.text)\nLocal color and tag appear in this account's folder tabs and folder settings. Other clients and Telegram's server folder name are unchanged.")),
            LocalFolderRow(stableId: .fixed(1), order: 1, section: 1, content: .input(currentDraft.tag, !operation.busy)),
            LocalFolderRow(stableId: .fixed(2), order: 2, section: 1, content: .text("Up to 20 characters and 128 UTF-8 bytes. Control and directional-formatting characters are removed. Leave blank for no local tag.")),
            LocalFolderRow(stableId: .color(-1), order: 10, section: 2, content: .choice("Use Native Color", currentDraft.color == nil, !operation.busy))
        ]
        for color in LocalFolderColor.allCases {
            entries.append(LocalFolderRow(stableId: .color(color.rawValue), order: 11 + Int(color.rawValue), section: 2,
                content: .choice(color.title, currentDraft.color == color, !operation.busy)))
        }
        let preview = normalizedTag.map { title.text + ($0.isEmpty ? "" : " · " + $0) } ?? "Tag exceeds the limits."
        entries.append(LocalFolderRow(stableId: .fixed(5), order: 30, section: 3, content: .text("Preview: \(preview)\nColor: \(currentDraft.color?.title ?? "Native")")))
        entries.append(LocalFolderRow(stableId: .fixed(3), order: 31, section: 3, content: .action("Save Local Folder Appearance", !operation.busy && normalizedTag != nil, false)))
        entries.append(LocalFolderRow(stableId: .fixed(4), order: 32, section: 3, content: .action("Restore Native Folder Appearance", !operation.busy, false)))
        if let saved = operation.result {
            entries.append(LocalFolderRow(stableId: .fixed(6), order: 33, section: 3, content: .text(saved ? "Saved for this account. Folder tabs and settings update automatically." : "Could not save. The folder may have changed; reopen its editor and try again. The local storage limit may also have been reached. Existing preferences were kept.")))
        }
        return localFolderListState(presentation: presentation, title: "Folder Appearance", entries: entries, arguments: arguments)
    }
    |> afterDisposed { request.dispose() }
    return ItemListController(context: context, state: signal)
}
