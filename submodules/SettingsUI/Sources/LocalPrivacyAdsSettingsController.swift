// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext

private struct LocalPrivacyAdsRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case toggle(Bool, Bool)
        case reset(Bool)
    }
    let stableId: Int
    let section: ItemListSectionId
    let content: Content

    static func < (lhs: LocalPrivacyAdsRow, rhs: LocalPrivacyAdsRow) -> Bool {
        return lhs.stableId < rhs.stableId
    }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalPrivacyAdsArguments
        switch self.content {
        case let .heading(text):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: text, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .toggle(value, enabled):
            return ItemListSwitchItem(presentationData: presentationData, systemStyle: .glass, title: "Hide Sponsored Messages", value: value, enableInteractiveChanges: false, enabled: enabled, maximumNumberOfLines: 2, sectionId: self.section, style: .blocks, updated: arguments.update)
        case let .reset(enabled):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass, title: "Reset Local Ad Settings", kind: enabled ? .destructive : .disabled, alignment: .natural, sectionId: self.section, style: .blocks, action: {
                if enabled { arguments.reset() }
            })
        }
    }
}

private final class LocalPrivacyAdsArguments {
    let update: (Bool) -> Void
    let reset: () -> Void
    init(update: @escaping (Bool) -> Void, reset: @escaping () -> Void) {
        self.update = update
        self.reset = reset
    }
}

public func localPrivacyAdsSettingsController(context: AccountContext) -> ViewController {
    let privacy = context.account.localPrivacyAds
    let disposable = MetaDisposable()
    let operation = Promise<(busy: Bool, failed: Bool)>((false, false))
    var present: ((ViewController) -> Void)?
    let update: (Bool) -> Void = { value in
        operation.set(.single((true, false)))
        disposable.set((privacy.setHideSponsoredMessages(value) |> deliverOnMainQueue).start(next: { saved in
            operation.set(.single((false, !saved)))
        }))
    }
    let arguments = LocalPrivacyAdsArguments(update: update, reset: {
        let presentationData = context.sharedContext.currentPresentationData.with { $0 }
        let sheet = ActionSheetController(presentationData: presentationData)
        sheet.setItemGroups([
            ActionSheetItemGroup(items: [
                ActionSheetTextItem(title: "Show sponsored messages again in this account? Other settings and Telegram messages stay unchanged."),
                ActionSheetButtonItem(title: "Reset Local Ad Settings", color: .destructive, action: { [weak sheet] in
                    sheet?.dismissAnimated()
                    update(false)
                })
            ]),
            ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
        ])
        present?(sheet)
    })
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, privacy.settings, operation.get())
    |> map { presentationData, state, operation -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let supported: Bool
        if case .available = state { supported = true } else { supported = false }
        var entries: [LocalPrivacyAdsRow] = [
            LocalPrivacyAdsRow(stableId: 0, section: 0, content: .heading("PRIVACY & ADS · THIS ACCOUNT")),
            LocalPrivacyAdsRow(stableId: 1, section: 0, content: .toggle(state.hidesSponsoredMessages, supported && !operation.busy)),
            LocalPrivacyAdsRow(stableId: 2, section: 0, content: .text(supported ? "Applies immediately to Telegram sponsored-message rows in open chats. Turning this off restores the upstream presentation. This is a local display option, not Telegram Premium." : "This version cannot read these saved settings. Their bytes are unchanged; use a compatible version to manage them.")),
            LocalPrivacyAdsRow(stableId: 3, section: 1, content: .heading("LIMITS & TELEGRAM POLICY")),
            LocalPrivacyAdsRow(stableId: 4, section: 1, content: .text("Interfering with sponsored messages conflicts with Telegram’s third-party API terms. This option does not change server entitlements, block ordinary promotional posts or hide every promotion in the app. Upstream advertisement requests may still occur.")),
            LocalPrivacyAdsRow(stableId: 5, section: 2, content: .reset(supported && !operation.busy))
        ]
        if operation.failed {
            entries.append(LocalPrivacyAdsRow(stableId: 6, section: 2, content: .text("The change could not be saved. The existing account settings were kept.")))
        }
        let data = ItemListPresentationData(presentationData)
        let controller = ItemListControllerState(presentationData: data, title: .text("Privacy & Ads"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed { disposable.dispose() }
    let controller = ItemListController(context: context, state: signal)
    present = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    return controller
}
