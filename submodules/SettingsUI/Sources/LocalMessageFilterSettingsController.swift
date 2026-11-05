// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext
import LocalizedPeerData

private enum LocalFilterRowId: Hashable {
    case fixed(Int)
    case rule(String)
}

private struct LocalFilterRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case toggle(String, Bool, Bool)
        case action(String, String, Bool)
        case input(String, Bool)
        case destructive(String, Bool)
    }
    let stableId: LocalFilterRowId
    let order: Int
    let section: ItemListSectionId
    let content: Content

    static func < (lhs: LocalFilterRow, rhs: LocalFilterRow) -> Bool {
        return lhs.order < rhs.order
    }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalFilterArguments
        switch self.content {
        case let .heading(title):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: title, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .toggle(title, value, enabled):
            return ItemListSwitchItem(presentationData: presentationData, systemStyle: .glass, title: title, value: value, enableInteractiveChanges: false, enabled: enabled, maximumNumberOfLines: 2, sectionId: self.section, style: .blocks, updated: { value in
                arguments.toggle(self.stableId, value)
            })
        case let .action(title, detail, enabled):
            return ItemListDisclosureItem(presentationData: presentationData, systemStyle: .glass, title: title, enabled: enabled, label: detail, labelStyle: .detailText, sectionId: self.section, style: .blocks, action: {
                if enabled { arguments.action(self.stableId) }
            })
        case let .input(text, enabled):
            return ItemListMultilineInputItem(presentationData: presentationData, systemStyle: .glass, text: text, placeholder: "Text to hide", maxLength: ItemListMultilineInputItemTextLimit(value: LocalMessageFilterLimits.maximumLiteralUTF16Length, display: false), sectionId: self.section, style: .blocks, capitalization: false, autocorrection: false, textUpdated: { value in
                if enabled { arguments.updateText(value) }
            }, shouldUpdateText: { value in
                return enabled && localFilterTextFits(value)
            })
        case let .destructive(title, enabled):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass, title: title, kind: enabled ? .destructive : .disabled, alignment: .natural, sectionId: self.section, style: .blocks, action: {
                if enabled { arguments.action(self.stableId) }
            })
        }
    }
}

private final class LocalFilterArguments {
    let toggle: (LocalFilterRowId, Bool) -> Void
    let action: (LocalFilterRowId) -> Void
    let updateText: (String) -> Void

    init(toggle: @escaping (LocalFilterRowId, Bool) -> Void = { _, _ in },
         action: @escaping (LocalFilterRowId) -> Void = { _ in },
         updateText: @escaping (String) -> Void = { _ in }) {
        self.toggle = toggle
        self.action = action
        self.updateText = updateText
    }
}

private func localFilterRulePreview(_ text: String) -> String {
    if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return "Spaces or line breaks"
    }
    let visible = text.replacingOccurrences(of: "\n", with: " ↵ ")
    return String(visible.prefix(80)) + (visible.count > 80 ? "…" : "")
}

private func localFilterMediaTypeTitle(_ type: LocalMessageFilterMediaType) -> String {
    switch type {
    case .photo: return "Photos"
    case .video: return "Videos"
    case .gif: return "GIFs"
    case .voiceMessage: return "Voice Messages"
    case .videoMessage: return "Video Messages"
    case .document: return "Documents & Other Files"
    }
}

/// No peer lookup here fetches messages or makes a network request. Names come
/// from the engine's local peer-data view and rules stay in this account.
public func localMessageFilterSettingsController(context: AccountContext, peerId: Int64? = nil) -> ViewController {
    let filter = context.account.localMessageFilter
    let actionDisposable = MetaDisposable()
    let actionState = Promise<(Bool, Bool)>((false, false))
    var pushController: ((ViewController) -> Void)?
    var presentController: ((ViewController) -> Void)?
    var currentRules: [LocalMessageFilterRule] = []
    let apply: (Signal<Bool, NoError>) -> Void = { operation in
        actionState.set(.single((true, false)))
        actionDisposable.set((operation |> deliverOnMainQueue).start(next: { success in
            actionState.set(.single((false, !success)))
        }))
    }
    let arguments = LocalFilterArguments(toggle: { id, enabled in
        switch id {
        case .fixed(1):
            apply(filter.setEnabled(enabled))
        case .fixed(11):
            if let peerId { apply(filter.setChatExcluded(peerId: peerId, excluded: enabled)) }
        case .fixed(15):
            apply(filter.setHideBlockedSenders(enabled))
        default:
            break
        }
    }, action: { id in
        switch id {
        case .fixed(3):
            pushController?(localMessageFilterRuleEditorController(context: context, peerId: peerId, initialText: ""))
        case let .rule(id):
            if let rule = currentRules.first(where: { $0.id == id }) {
                pushController?(localFilterRuleEditor(context: context, peerId: rule.peerId ?? peerId, rule: rule, initialText: "", fromMessage: false))
            }
        case .fixed(14):
            let presentationData = context.sharedContext.currentPresentationData.with { $0 }
            let sheet = ActionSheetController(presentationData: presentationData)
            sheet.setItemGroups([
                ActionSheetItemGroup(items: [
                    ActionSheetTextItem(title: "Disable local filtering and remove this account’s rules and global-chat exceptions? Telegram messages and the archive stay unchanged."),
                    ActionSheetButtonItem(title: "Reset Message Filters", color: .destructive, action: { [weak sheet] in
                        sheet?.dismissAnimated()
                        apply(filter.reset())
                    })
                ]),
                ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
            ])
            presentController?(sheet)
        default:
            break
        }
    })

    let displayData: Signal<(LocalMessageFilterSettingsState, [Int64: String]), NoError> = filter.settings
    |> mapToSignal { state in
        guard case let .available(settings) = state else { return .single((state, [:])) }
        var ids = Set(settings.rules.compactMap { $0.peerId })
        if let peerId { ids.insert(peerId) }
        let peerIds = Array(ids)
        guard !peerIds.isEmpty else { return .single((state, [:])) }
        return combineLatest(peerIds.map { id in
            context.engine.data.get(TelegramEngine.EngineData.Item.Peer.Peer(id: EnginePeer.Id(id)))
        }) |> map { peers in
            var names: [Int64: String] = [:]
            for (id, peer) in zip(peerIds, peers) {
                if let peer { names[id] = peer.compactDisplayTitle }
            }
            return (state, names)
        }
    }
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, displayData, actionState.get())
    |> map { presentationData, data, actionState -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let settings: LocalMessageFilterSettings
        let supported: Bool
        switch data.0 {
        case let .available(value):
            settings = value
            supported = true
        case .unsupported:
            settings = LocalMessageFilterSettings()
            supported = false
        }
        currentRules = settings.rules
        let canChange = supported && !actionState.0
        let status: String
        if !supported {
            status = "This version cannot read the saved filter settings. They have been kept unchanged. Use a compatible version to edit them."
        } else if settings.enabled {
            status = "On for this account. Matching supported incoming messages use a local hidden-message placeholder. Telegram’s stored messages are unchanged."
        } else {
            status = "Off. Saved rules do not hide messages until you enable filtering for this account."
        }
        var entries: [LocalFilterRow] = [
            LocalFilterRow(stableId: .fixed(0), order: 0, section: 0, content: .heading("LOCAL FILTERS · THIS ACCOUNT")),
            LocalFilterRow(stableId: .fixed(1), order: 1, section: 0, content: .toggle("Filter Messages Locally", settings.enabled, canChange)),
            LocalFilterRow(stableId: .fixed(2), order: 2, section: 0, content: .text(status)),
            LocalFilterRow(stableId: .fixed(3), order: 3, section: 1, content: .action(peerId == nil ? "Add Global Rule" : "Add Rule for This Chat", "\(settings.rules.count) of \(LocalMessageFilterLimits.maximumRules) rules", canChange && settings.rules.count < LocalMessageFilterLimits.maximumRules)),
            LocalFilterRow(stableId: .fixed(4), order: 4, section: 1, content: .text("Create text/caption rules or hide a supported media type. Rules can apply globally or to one chat.")),
            LocalFilterRow(stableId: .fixed(15), order: 5, section: 1, content: .toggle("Hide Blocked Senders", settings.hideBlockedSenders, canChange)),
            LocalFilterRow(stableId: .fixed(16), order: 6, section: 1, content: .text("Uses this account’s locally cached user-block status for supported messages. Changes update open chats; missing block metadata stays visible. No block or peer-fetch request is sent by filtering. Chats excluded from global rules are also exempt from this option."))
        ]
        var nextOrder = 7
        if let peerId {
            entries.append(LocalFilterRow(stableId: .fixed(10), order: nextOrder, section: 2, content: .heading(data.1[peerId] ?? "THIS CHAT")))
            entries.append(LocalFilterRow(stableId: .fixed(11), order: nextOrder + 1, section: 2, content: .toggle("Exclude this chat from global rules", settings.excludedPeerIds.contains(peerId), canChange)))
            entries.append(LocalFilterRow(stableId: .fixed(12), order: nextOrder + 2, section: 2, content: .text("Rules saved specifically for this chat still apply.")))
            nextOrder += 3
        }
        entries.append(LocalFilterRow(stableId: .fixed(5), order: nextOrder, section: 3, content: .heading("SAVED RULES")))
        nextOrder += 1
        let visibleRules = settings.rules.filter { peerId == nil || $0.peerId == nil || $0.peerId == peerId }
        for rule in visibleRules {
            let scope: String
            if let rulePeerId = rule.peerId {
                scope = data.1[rulePeerId] ?? (rulePeerId == peerId ? "This chat" : "One chat")
            } else {
                scope = "All chats"
            }
            let kind = rule.mediaType == nil ? (rule.matchingMode == .regularExpression ? "Expression" : "Literal") + " · " + (rule.caseSensitive ? "Case-sensitive" : "Ignore case") : "Media type"
            let title = rule.mediaType.map { "Hide " + localFilterMediaTypeTitle($0) } ?? localFilterRulePreview(rule.text)
            entries.append(LocalFilterRow(stableId: .rule(rule.id), order: nextOrder, section: 3, content: .action(title, scope + " · " + kind, canChange)))
            nextOrder += 1
        }
        if visibleRules.isEmpty {
            entries.append(LocalFilterRow(stableId: .fixed(6), order: nextOrder, section: 3, content: .text("No saved rules for this view.")))
            nextOrder += 1
        }
        entries.append(LocalFilterRow(stableId: .fixed(7), order: nextOrder, section: 4, content: .text("Supports ordinary incoming text and single photos, videos, GIFs, voice/video messages and files in generic bubbles. Forwarded or grouped messages, stickers, large emoji, rich/unknown attributes, secret, protected and disappearing content stay visible. Filtering does not stop already playing media, block receipts, remove unread counts or delete server messages.")))
        entries.append(LocalFilterRow(stableId: .fixed(9), order: nextOrder + 1, section: 4, content: .text("Tap a hidden-message placeholder to show filtered messages in that chat. Use Hide Filtered Messages Again in the chat menu to restore the placeholders.")))
        entries.append(LocalFilterRow(stableId: .fixed(13), order: nextOrder + 2, section: 4, content: .text("Oversized messages and expressions that exceed the work limit remain visible. Matching uses a finite-state evaluator, not unrestricted backtracking.")))
        entries.append(LocalFilterRow(stableId: .fixed(14), order: nextOrder + 3, section: 5, content: .destructive("Reset Message Filters", canChange)))
        if actionState.1 {
            entries.append(LocalFilterRow(stableId: .fixed(8), order: nextOrder + 4, section: 5, content: .text("The filter change could not be saved. Existing rules have been kept.")))
        }
        let listData = ItemListPresentationData(presentationData)
        let state = ItemListControllerState(presentationData: listData, title: .text("Message Filters"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (state, (ItemListNodeState(presentationData: listData, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed { actionDisposable.dispose() }
    let controller = ItemListController(context: context, state: signal)
    pushController = { [weak controller] next in
        (controller?.navigationController as? NavigationController)?.pushViewController(next)
    }
    presentController = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    return controller
}

private struct LocalFilterDraft: Equatable {
    var text: String
    var caseSensitive: Bool
    var onlyThisChat: Bool
    var regularExpression: Bool
    var mediaType: LocalMessageFilterMediaType?
}

private func localFilterRule(id: String, peerId: Int64?, draft: LocalFilterDraft) -> LocalMessageFilterRule {
    return LocalMessageFilterRule(id: id, text: draft.mediaType == nil ? draft.text : "", caseSensitive: draft.mediaType == nil && draft.caseSensitive, peerId: draft.onlyThisChat ? peerId : nil, matchingMode: draft.mediaType == nil && draft.regularExpression ? .regularExpression : .literal, mediaType: draft.mediaType)
}

private func localFilterBoundedTextLength(_ text: String) -> Int {
    return text.utf16.prefix(LocalMessageFilterLimits.maximumLiteralUTF16Length + 1).count
}

private func localFilterTextFits(_ text: String) -> Bool {
    // Reject an oversized paste before inspecting its remaining contents.
    return localFilterBoundedTextLength(text) <= LocalMessageFilterLimits.maximumLiteralUTF16Length
        && !text.contains("\0")
}

private func localFilterPrefill(_ text: String) -> String {
    // Read a bounded code-unit window before decoding. A single grapheme may
    // contain arbitrarily many combining scalars, so Character iteration here
    // would perform unbounded work before it could enforce the input limit.
    var units = Array(text.utf16.prefix(LocalMessageFilterLimits.maximumLiteralUTF16Length))
    if let nul = units.firstIndex(of: 0) {
        units.removeSubrange(nul...)
    }
    if let last = units.last, last >= 0xd800, last <= 0xdbff {
        units.removeLast()
    }
    // Preserve Unicode scalar validity, including surrogate pairs. The excerpt
    // can end within a composed grapheme and remains visibly editable before Save.
    return String(decoding: units, as: UTF16.self)
}

/// Creating a rule only pre-fills an editor. It neither saves the rule nor enables
/// filtering until the user explicitly confirms the respective action.
public func localMessageFilterRuleEditorController(context: AccountContext, peerId: Int64?, initialText: String, initialMediaType: LocalMessageFilterMediaType? = nil) -> ViewController {
    return localFilterRuleEditor(context: context, peerId: peerId, rule: nil, initialText: initialText, fromMessage: !initialText.isEmpty || initialMediaType != nil, initialMediaType: initialMediaType)
}

private func localFilterRuleEditor(context: AccountContext, peerId: Int64?, rule: LocalMessageFilterRule?, initialText: String, fromMessage: Bool, initialMediaType: LocalMessageFilterMediaType? = nil) -> ViewController {
    let filter = context.account.localMessageFilter
    let ruleId = rule?.id ?? UUID().uuidString
    var draft = LocalFilterDraft(text: rule?.text ?? localFilterPrefill(initialText), caseSensitive: rule?.caseSensitive ?? false, onlyThisChat: rule.map { $0.peerId != nil } ?? (peerId != nil), regularExpression: rule?.matchingMode == .regularExpression, mediaType: rule?.mediaType ?? initialMediaType)
    let draftValue = ValuePromise<LocalFilterDraft>(draft, ignoreRepeated: true)
    let actionState = Promise<(Bool, Bool)>((false, false))
    let actionDisposable = MetaDisposable()
    var supported = false
    var busy = false
    var dismiss: (() -> Void)?
    var present: ((ViewController) -> Void)?
    var push: ((ViewController) -> Void)?

    let apply: (Signal<Bool, NoError>) -> Void = { operation in
        actionState.set(.single((true, false)))
        actionDisposable.set((operation |> deliverOnMainQueue).start(next: { success in
            actionState.set(.single((false, !success)))
            if success { dismiss?() }
        }))
    }
    let save: () -> Void = {
        let value = localFilterRule(id: ruleId, peerId: peerId, draft: draft)
        guard supported, !busy, (draft.mediaType != nil || localFilterTextFits(draft.text)), value.isSupported else { return }
        apply(filter.upsertRule(value))
    }
    let arguments = LocalFilterArguments(toggle: { id, value in
        guard supported, !busy else { return }
        if id == .fixed(3), draft.mediaType == nil { draft.caseSensitive = value }
        if id == .fixed(4), peerId != nil { draft.onlyThisChat = value }
        if id == .fixed(12), draft.mediaType == nil { draft.regularExpression = value }
        draftValue.set(draft)
    }, action: { id in
        switch id {
        case .fixed(16):
            guard supported, !busy else { return }
            let presentationData = context.sharedContext.currentPresentationData.with { $0 }
            let sheet = ActionSheetController(presentationData: presentationData)
            let choices: [(String, LocalMessageFilterMediaType?)] = [("Text or Caption", nil)] + LocalMessageFilterMediaType.allCases.map { (localFilterMediaTypeTitle($0), Optional($0)) }
            sheet.setItemGroups([
                ActionSheetItemGroup(items: choices.map { title, type in
                    ActionSheetButtonItem(title: title, color: .accent, action: { [weak sheet] in
                        sheet?.dismissAnimated()
                        draft.mediaType = type
                        if type != nil { draft.regularExpression = false; draft.caseSensitive = false }
                        draftValue.set(draft)
                    })
                }),
                ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
            ])
            present?(sheet)
        case .fixed(7):
            push?(localMessageFilterSettingsController(context: context, peerId: peerId))
        case .fixed(8):
            guard supported, !busy, rule != nil else { return }
            let presentationData = context.sharedContext.currentPresentationData.with { $0 }
            let sheet = ActionSheetController(presentationData: presentationData)
            sheet.setItemGroups([
                ActionSheetItemGroup(items: [
                    ActionSheetTextItem(title: "Remove this local filter rule? Matching messages may become visible again."),
                    ActionSheetButtonItem(title: "Remove Rule", color: .destructive, action: { [weak sheet] in
                        sheet?.dismissAnimated()
                        apply(filter.removeRule(id: ruleId))
                    })
                ]),
                ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
            ])
            present?(sheet)
        default:
            break
        }
    }, updateText: { text in
        guard supported, !busy, draft.mediaType == nil, localFilterTextFits(text) else { return }
        draft.text = text
        draftValue.set(draft)
    })

    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, filter.settings, draftValue.get(), actionState.get())
    |> map { presentationData, state, value, actionState -> (ItemListControllerState, (ItemListNodeState, Any)) in
        let enabled: Bool
        switch state {
        case let .available(settings):
            supported = true
            enabled = settings.enabled
        case .unsupported:
            supported = false
            enabled = false
        }
        busy = actionState.0
        let canChange = supported && !busy
        let title: String
        let bodyId: LocalFilterRowId
        let body: LocalFilterRow.Content
        let explanation: String
        if let type = value.mediaType {
            title = "MEDIA RULE · " + localFilterMediaTypeTitle(type)
            bodyId = .fixed(15)
            body = .text("Hide supported incoming " + localFilterMediaTypeTitle(type).lowercased() + " in the selected scope. Captions and media bytes are kept unchanged on Telegram.")
            explanation = "Media rules apply by type, including messages with no caption. Unsupported, protected, grouped and disappearing content stays visible."
        } else {
            title = "\(value.regularExpression ? "REGULAR EXPRESSION" : "TEXT OR CAPTION") · \(localFilterBoundedTextLength(value.text))/\(LocalMessageFilterLimits.maximumLiteralUTF16Length)"
            bodyId = .fixed(1)
            body = .input(value.text, canChange)
            explanation = value.regularExpression ? "Supported: . ^ $ [ranges] [^ranges] * + ? and \\d \\w \\s with uppercase negations; \\n \\r \\t and escaped punctuation. Groups, alternation, counted repeats, look-around, Unicode properties and backreferences are rejected. \\d and \\w use ASCII characters; anchors refer to the complete text." : (fromMessage ? "A message excerpt is prefilled. Edit the text, choose its scope, then tap Save. Spaces and line breaks are matched as entered." : "Enter the exact text to match in a nonempty body or caption. Spaces and line breaks are significant.")
        }
        var entries: [LocalFilterRow] = [
            LocalFilterRow(stableId: .fixed(0), order: 0, section: 0, content: .heading(title)),
            LocalFilterRow(stableId: .fixed(16), order: 1, section: 0, content: .action("Filter By", value.mediaType.map(localFilterMediaTypeTitle) ?? "Text or Caption", canChange)),
            // Distinct identities force node replacement when its item class changes.
            LocalFilterRow(stableId: supported ? bodyId : .fixed(10), order: 2, section: 0, content: supported ? body : .text("Saved filter settings are unsupported. This version cannot change them.")),
            LocalFilterRow(stableId: .fixed(2), order: 3, section: 0, content: .text(explanation))
        ]
        if value.mediaType == nil {
            entries.append(LocalFilterRow(stableId: .fixed(12), order: 4, section: 1, content: .toggle("Regular expression · safe subset", value.regularExpression, canChange)))
            entries.append(LocalFilterRow(stableId: .fixed(3), order: 5, section: 1, content: .toggle("Case-sensitive matching", value.caseSensitive, canChange)))
        }
        if peerId != nil {
            entries.append(LocalFilterRow(stableId: .fixed(4), order: 6, section: 1, content: .toggle("Only apply to this chat", value.onlyThisChat, canChange)))
        }
        let scope = value.onlyThisChat ? "This rule applies only to its selected chat. Excluding that chat from global rules does not disable this rule." : "This global rule applies to all chats except chats excluded from global rules. To create a rule for one chat, open Message Filters from that chat."
        entries.append(LocalFilterRow(stableId: .fixed(5), order: 7, section: 1, content: .text(scope)))
        entries.append(LocalFilterRow(stableId: .fixed(6), order: 8, section: 2, content: .text(enabled ? "Filtering is enabled. Saving updates this account’s local rules." : "Filtering is off. Saving this rule will not turn it on.")))
        entries.append(LocalFilterRow(stableId: .fixed(7), order: 9, section: 2, content: .action("Filter Settings", "This account", !busy)))
        if rule != nil {
            entries.append(LocalFilterRow(stableId: .fixed(8), order: 10, section: 3, content: .destructive("Remove Rule", canChange)))
        }
        if actionState.1 {
            entries.append(LocalFilterRow(stableId: .fixed(9), order: 11, section: 3, content: .text("The rule could not be saved. Check the expression syntax, text limit and the account’s 64-rule limit.")))
        }
        let ruleIsSupported = localFilterRule(id: ruleId, peerId: peerId, draft: value).isSupported
        if value.regularExpression && !ruleIsSupported {
            entries.append(LocalFilterRow(stableId: .fixed(11), order: 12, section: 3, content: .text("This expression is invalid or uses unsupported syntax. Save is disabled; existing rules stay unchanged.")))
        }
        let canSave = canChange && (value.mediaType != nil || localFilterTextFits(value.text)) && ruleIsSupported
        let data = ItemListPresentationData(presentationData)
        let controller = ItemListControllerState(presentationData: data, title: .text(rule == nil ? "New Filter Rule" : "Edit Filter Rule"), leftNavigationButton: nil, rightNavigationButton: ItemListNavigationButton(content: .text(presentationData.strings.Common_Save), style: .bold, enabled: canSave, action: save), backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed { actionDisposable.dispose() }
    let controller = ItemListController(context: context, state: signal)
    dismiss = { [weak controller] in
        let _ = (controller?.navigationController as? NavigationController)?.popViewController(animated: true)
    }
    present = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    push = { [weak controller] next in
        (controller?.navigationController as? NavigationController)?.pushViewController(next)
    }
    return controller
}
