// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext

private struct LocalArchiveMarkerDraft {
    var deleted: String
    var edited: String
}

private final class LocalArchiveMarkerArguments {
    let edit: (Bool, String) -> Void
    let save: () -> Void
    let reset: () -> Void
    init(edit: @escaping (Bool, String) -> Void, save: @escaping () -> Void, reset: @escaping () -> Void) {
        self.edit = edit; self.save = save; self.reset = reset
    }
}

private struct LocalArchiveMarkerRow: ItemListNodeEntry {
    enum Content: Equatable {
        case text(String)
        case input(String, String, Bool, Bool)
        case action(String, Bool, Bool)
    }
    let stableId: Int
    let section: ItemListSectionId
    let content: Content
    static func < (lhs: LocalArchiveMarkerRow, rhs: LocalArchiveMarkerRow) -> Bool { lhs.stableId < rhs.stableId }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalArchiveMarkerArguments
        switch self.content {
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .input(title, value, isDeleted, enabled):
            return ItemListSingleLineInputItem(presentationData: presentationData, systemStyle: .glass, title: NSAttributedString(string: title), text: value, placeholder: isDeleted ? "Deleted" : "Edited", type: .regular(capitalization: false, autocorrection: false), clearType: .always, enabled: enabled, sectionId: self.section, textUpdated: { arguments.edit(isDeleted, $0) }, shouldUpdateText: { enabled && $0.utf8.prefix(1025).count <= 1024 }, action: {})
        case let .action(title, enabled, reset):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass, title: title, kind: enabled ? .generic : .disabled, alignment: .natural, sectionId: self.section, style: .blocks, action: {
                if enabled { if reset { arguments.reset() } else { arguments.save() } }
            })
        }
    }
}

/// Uses this account's independent presentation preferences. Labels apply only
/// to archive screens; they never alter canonical Telegram message bubbles.
func localArchivePresentationController(context: AccountContext) -> ViewController {
    let preferences = context.account.localArchivePresentation
    let draft = Promise<LocalArchiveMarkerDraft?>(nil)
    let operation = Promise<(busy: Bool, result: Bool?)>((false, nil))
    let request = MetaDisposable()
    var currentDraft = LocalArchiveMarkerDraft(deleted: "Deleted", edited: "Edited")
    let apply: (LocalArchiveMarkerDraft) -> Void = { value in
        operation.set(.single((true, nil)))
        request.set((preferences.setMarkers(deleted: value.deleted, edited: value.edited) |> deliverOnMainQueue).start(next: { saved in
            if saved, let deleted = LocalArchivePresentationSettings.normalizedMarker(value.deleted), let edited = LocalArchivePresentationSettings.normalizedMarker(value.edited) {
                draft.set(.single(LocalArchiveMarkerDraft(deleted: deleted, edited: edited)))
            }
            operation.set(.single((false, saved)))
        }))
    }
    let arguments = LocalArchiveMarkerArguments(edit: { isDeleted, text in
        var value = currentDraft
        if isDeleted { value.deleted = text } else { value.edited = text }
        draft.set(.single(value))
        operation.set(.single((false, nil)))
    }, save: {
        apply(currentDraft)
    }, reset: {
        apply(LocalArchiveMarkerDraft(deleted: "Deleted", edited: "Edited"))
    })
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, preferences.settings, draft.get(), operation.get())
    |> map { presentationData, settings, draft, operation -> (ItemListControllerState, (ItemListNodeState, Any)) in
        var entries: [LocalArchiveMarkerRow] = []
        if case let .available(value) = settings {
            currentDraft = draft ?? LocalArchiveMarkerDraft(deleted: value.deletedMarker, edited: value.editedMarker)
            let deleted = LocalArchivePresentationSettings.normalizedMarker(currentDraft.deleted)
            let edited = LocalArchivePresentationSettings.normalizedMarker(currentDraft.edited)
            entries = [
                LocalArchiveMarkerRow(stableId: 0, section: 0, content: .text("Customize this account’s labels in archived history and saved-media screens. Telegram chat bubbles are unchanged.")),
                LocalArchiveMarkerRow(stableId: 1, section: 1, content: .input("Deleted", currentDraft.deleted, true, !operation.busy)),
                LocalArchiveMarkerRow(stableId: 2, section: 1, content: .input("Edited", currentDraft.edited, false, !operation.busy)),
                LocalArchiveMarkerRow(stableId: 3, section: 1, content: .text("Use 1–32 characters and at most 128 UTF-8 bytes per label. Control and directional-formatting characters are removed. These labels change presentation only.")),
                LocalArchiveMarkerRow(stableId: 4, section: 2, content: .text(deleted != nil && edited != nil ? "Preview: \(deleted!) · \(edited!) (previous version)" : "Both labels must be nonempty and fit the limits.")),
                LocalArchiveMarkerRow(stableId: 5, section: 2, content: .action("Save Archive Labels", !operation.busy && deleted != nil && edited != nil, false)),
                LocalArchiveMarkerRow(stableId: 6, section: 2, content: .action("Restore Default Labels", !operation.busy, true))
            ]
        } else {
            entries.append(LocalArchiveMarkerRow(stableId: 0, section: 0, content: .text("This version cannot read the saved archive label preferences. They remain unchanged and read-only; archive screens use default labels.")))
        }
        if let saved = operation.result {
            entries.append(LocalArchiveMarkerRow(stableId: 7, section: 3, content: .text(saved ? "Labels saved for this account." : "Labels could not be saved. Existing preferences were kept.")))
        }
        let data = ItemListPresentationData(presentationData)
        let controller = ItemListControllerState(presentationData: data, title: .text("Archive Labels"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed { request.dispose() }
    return ItemListController(context: context, state: signal)
}
