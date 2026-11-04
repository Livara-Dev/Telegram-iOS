// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import ImageIO
import AsyncDisplayKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import PresentationDataUtils
import AccountContext
import LocalizedPeerData

// Archive text and local image previews never become live Telegram Messages
// or enter a send/forward pipeline.
private enum LocalArchiveRowId: Hashable {
    case fixed(Int)
    case record(Int64)
    case recordHeading(Int64)
    case media(Int64, Int)
}

private struct LocalArchiveRow: ItemListNodeEntry {
    enum Content: Equatable {
        case heading(String)
        case text(String)
        case toggle(String, Bool, Bool)
        case action(String, String, Bool)
        case search(String)
    }

    let stableId: LocalArchiveRowId
    let order: Int
    let section: ItemListSectionId
    let content: Content

    static func < (lhs: LocalArchiveRow, rhs: LocalArchiveRow) -> Bool {
        return lhs.order < rhs.order
    }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalArchiveArguments
        switch self.content {
        case let .heading(text):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: text, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .toggle(title, value, enabled):
            return ItemListSwitchItem(presentationData: presentationData, systemStyle: .glass, title: title, value: value, enableInteractiveChanges: false, enabled: enabled, maximumNumberOfLines: 2, sectionId: self.section, style: .blocks, updated: { value in
                arguments.setToggle(self.stableId, value)
            })
        case let .search(text):
            return ItemListSingleLineInputItem(presentationData: presentationData, systemStyle: .glass, title: NSAttributedString(), text: text, placeholder: "Search archived words or phrases", type: .regular(capitalization: false, autocorrection: false), returnKeyType: .search, clearType: .always, sectionId: self.section, textUpdated: { arguments.searchUpdated($0) }, action: {})
        case let .action(title, detail, enabled):
            return ItemListDisclosureItem(presentationData: presentationData, systemStyle: .glass, title: title, enabled: enabled, label: detail, labelStyle: .detailText, sectionId: self.section, style: .blocks, action: {
                if enabled {
                    arguments.action(self.stableId)
                }
            })
        }
    }
}

private final class LocalArchiveArguments {
    let setToggle: (LocalArchiveRowId, Bool) -> Void
    let action: (LocalArchiveRowId) -> Void
    let searchUpdated: (String) -> Void

    init(setToggle: @escaping (LocalArchiveRowId, Bool) -> Void = { _, _ in }, searchUpdated: @escaping (String) -> Void = { _ in }, action: @escaping (LocalArchiveRowId) -> Void = { _ in }) {
        self.setToggle = setToggle
        self.action = action
        self.searchUpdated = searchUpdated
    }
}

private func localArchiveStatusText(_ status: LocalArchiveStatus) -> String {
    switch status {
    case .disabled:
        return "Capture is off. Existing archived text is kept. Enable capture to browse this experimental archive."
    case .locked:
        return "Archive locked. Unlock this iPhone to continue."
    case .opening:
        return "Opening the encrypted archive…"
    case .ready:
        return "Capture is enabled for this account."
    case .writing:
        return "Saving captured text…"
    case .unavailable:
        return "Archive unavailable. Capture may have gaps. Check available storage and try again."
    case .invalidPendingCapture:
        return "A pending capture cannot be read. Capture has paused; some messages may be missing."
    case .purging:
        return "Clearing the archive…"
    }
}

private func localArchiveCanRead(_ status: LocalArchiveStatus) -> Bool {
    switch status {
    case .ready, .writing:
        return true
    default:
        return false
    }
}

/// Account-scoped entry point. No global preferences or direct Postbox access.
func localArchiveSettingsController(context: AccountContext, peerId: Int64? = nil) -> ViewController {
    var pushController: ((ViewController) -> Void)?
    var presentController: ((ViewController) -> Void)?
    let actionDisposable = MetaDisposable()
    let actionState = Promise<(Bool, Bool)>((false, false))
    let archive = context.account.localArchive
    var currentSettings = LocalArchiveSettings()
    let apply: (Signal<Bool, NoError>) -> Void = { operation in
        actionState.set(.single((true, false)))
        actionDisposable.set((operation |> deliverOnMainQueue).start(next: { success in
            actionState.set(.single((false, !success)))
        }))
    }
    let selectRetention: (Bool) -> Void = { selectAge in
        guard let archive else { return }
        let presentationData = context.sharedContext.currentPresentationData.with { $0 }
        let sheet = ActionSheetController(presentationData: presentationData)
        let values: [Int64] = selectAge ? [7, 30, 90, 365, 0] : [8, 32, 128, 256, 1024]
        let options = values.map { value -> ActionSheetItem in
            return ActionSheetButtonItem(title: selectAge ? (value == 0 ? "No age expiry (size limit still applies)" : "\(value) days") : "\(value) MiB", color: .accent, action: { [weak sheet] in
                sheet?.dismissAnimated()
                apply(archive.setRetention(maxAgeSeconds: selectAge ? (value == 0 ? Int64.max : value * 86400) : currentSettings.maxAgeSeconds,
                                           maxPayloadBytes: selectAge ? currentSettings.maxPayloadBytes : value * 1024 * 1024))
            })
        }
        sheet.setItemGroups([
            ActionSheetItemGroup(items: options),
            ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
        ])
        presentController?(sheet)
    }
    let arguments = LocalArchiveArguments(setToggle: { id, enabled in
        guard let archive else { return }
        if id == .fixed(8) {
            apply(archive.setExcludeBots(enabled))
        } else if id == .fixed(1) {
            apply(archive.setEnabled(enabled))
        } else if id == .fixed(15), let peerId {
            apply(archive.setPeerExcluded(peerId: peerId, excluded: enabled))
        }
    }, action: { id in
        guard let archive else { return }
        switch id {
        case .fixed(3):
            pushController?(localArchiveMessagesController(context: context, archive: archive, peerId: nil, message: nil))
        case .fixed(9):
            selectRetention(true)
        case .fixed(10):
            selectRetention(false)
        case .fixed(13):
            let presentationData = context.sharedContext.currentPresentationData.with { $0 }
            let sheet = ActionSheetController(presentationData: presentationData)
            sheet.setItemGroups([
                ActionSheetItemGroup(items: [
                    ActionSheetTextItem(title: "Delete all archived messages, versions and media for this account? This cannot be undone. Capture will continue if enabled."),
                    ActionSheetButtonItem(title: "Clear Archive", color: .destructive, action: { [weak sheet] in
                        sheet?.dismissAnimated()
                        apply(archive.requestPurge())
                    })
                ]),
                ActionSheetItemGroup(items: [ActionSheetButtonItem(title: presentationData.strings.Common_Cancel, color: .accent, action: { [weak sheet] in sheet?.dismissAnimated() })])
            ])
            presentController?(sheet)
        default:
            break
        }
    })

    let settings = archive?.settings ?? .single(LocalArchiveSettings())
    let status: Signal<LocalArchiveStatus, NoError> = archive?.status ?? .single(.unavailable)
    let usage: Signal<LocalArchiveUsage?, NoError> = combineLatest(queue: .mainQueue(), status, settings) |> mapToSignal { status, _ in
        guard let archive, localArchiveCanRead(status) else { return .single(nil) }
        return archive.usage()
    }
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, settings, status, actionState.get(), usage)
    |> map { presentationData, settings, status, actionState, usage -> (ItemListControllerState, (ItemListNodeState, Any)) in
        currentSettings = settings
        let canChange = archive != nil && settings.isSupported && !actionState.0 && !settings.pendingPurge
        let statusText = settings.isSupported ? localArchiveStatusText(status) : "This version cannot read the saved archive settings. They have been kept unchanged. Use a compatible app version to manage the archive."
        var entries: [LocalArchiveRow] = [
            LocalArchiveRow(stableId: .fixed(0), order: 0, section: 0, content: .heading("EXPERIMENTAL · THIS ACCOUNT")),
            LocalArchiveRow(stableId: .fixed(1), order: 1, section: 0, content: .toggle("Preserve deleted messages and cached media", settings.enabled, canChange)),
            LocalArchiveRow(stableId: .fixed(2), order: 2, section: 0, content: .text(statusText)),
            LocalArchiveRow(stableId: .fixed(3), order: 3, section: 1, content: .action("View Deleted Messages", "Messages, media and earlier versions", settings.enabled && localArchiveCanRead(status))),
            LocalArchiveRow(stableId: .fixed(4), order: 4, section: 1, content: .text("Stored on this device in a separate encrypted archive. Clearing Telegram’s cache does not clear this archive. Nothing here is uploaded or sent to other chats.")),
            LocalArchiveRow(stableId: .fixed(11), order: 5, section: 2, content: .heading("RETENTION AND EXCLUSIONS")),
            LocalArchiveRow(stableId: .fixed(9), order: 6, section: 2, content: .action("Archive retention", settings.maxAgeSeconds == Int64.max ? "No age expiry" : "\(settings.maxAgeSeconds / 86400) days", canChange)),
            LocalArchiveRow(stableId: .fixed(10), order: 7, section: 2, content: .action("Archive payload limit", "\(settings.maxPayloadBytes / (1024 * 1024)) MiB", canChange)),
            LocalArchiveRow(stableId: .fixed(8), order: 8, section: 2, content: .toggle("Exclude bot conversations", settings.excludeBots, canChange)),
            LocalArchiveRow(stableId: .fixed(12), order: 9, section: 2, content: .text("Oldest records are removed as archive activity runs. The storage limit counts text, metadata and retained media; database files can use more disk space. Lowering either limit may permanently remove records.")),
            LocalArchiveRow(stableId: .fixed(18), order: 10, section: 3, content: .text(usage.map { "Archived snapshots: \($0.snapshots)\nStored payload: \(ByteCountFormatter.string(fromByteCount: $0.payloadBytes, countStyle: .file))\nDatabase and search-index overhead are additional." } ?? "Storage usage is available when the archive is unlocked and enabled.")),
            LocalArchiveRow(stableId: .fixed(13), order: 11, section: 3, content: .action("Clear This Account’s Archive", "Permanent local deletion", canChange)),
            LocalArchiveRow(stableId: .fixed(5), order: 12, section: 4, content: .heading("CURRENT LIMITS")),
            LocalArchiveRow(stableId: .fixed(6), order: 13, section: 4, content: .text("This preview captures supported deletion and edit updates for messages already received while capture is enabled. It cannot recover earlier deletions and does not cover every deletion path. Completed local cloud files up to 512 MiB are attempted within the storage limit; image previews are limited to 16 MiB; missing or incomplete files are not downloaded. Structured rich messages, secret chats and disappearing content are excluded. Storage failures or a locked device can leave gaps."))
        ]
        if actionState.1 {
            entries.append(LocalArchiveRow(stableId: .fixed(7), order: 14, section: 4, content: .text("The setting could not be changed. Unlock the device and try again.")))
        }
        if settings.droppedCaptures > 0 {
            entries.append(LocalArchiveRow(stableId: .fixed(14), order: 15, section: 4, content: .text("Missed captures: \(settings.droppedCaptures). Some updates could not be archived.")))
        }
        if let peerId {
            entries.append(LocalArchiveRow(stableId: .fixed(16), order: 16, section: 5, content: .heading("THIS CHAT")))
            entries.append(LocalArchiveRow(stableId: .fixed(15), order: 17, section: 5, content: .toggle("Exclude this chat", settings.excludedPeerIds.contains(peerId), canChange)))
            entries.append(LocalArchiveRow(stableId: .fixed(17), order: 18, section: 5, content: .text("Stops new captures from this chat. Previously archived content is kept until retention or Clear Archive removes it.")))
        }
        let data = ItemListPresentationData(presentationData)
        let controller = ItemListControllerState(presentationData: data, title: .text("Local Message Archive"), leftNavigationButton: nil, rightNavigationButton: nil, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed {
        actionDisposable.dispose()
    }

    let controller = ItemListController(context: context, state: signal)
    pushController = { [weak controller] next in
        (controller?.navigationController as? NavigationController)?.pushViewController(next)
    }
    presentController = { [weak controller] next in
        controller?.present(next, in: .window(.root))
    }
    return controller
}

/// Opens the local archive scoped to an actual conversation.
public func localArchiveChatController(context: AccountContext, peerId: EnginePeer.Id) -> ViewController {
    guard let archive = context.account.localArchive else {
        return localArchiveSettingsController(context: context)
    }
    return localArchiveMessagesController(context: context, archive: archive, peerId: peerId.toInt64(), message: nil)
}

/// Opens prior snapshots without reconstructing or sending a Telegram message.
public func localArchiveMessageHistoryController(context: AccountContext, messageId: EngineMessage.Id) -> ViewController {
    guard let archive = context.account.localArchive else {
        return localArchiveSettingsController(context: context)
    }
    return localArchiveMessagesController(context: context, archive: archive, peerId: messageId.peerId.toInt64(), message: LocalArchiveMessageReference(messageId))
}

private struct LocalArchiveMessageReference {
    let peerId: Int64
    let namespace: Int32
    let messageId: Int32

    init(_ id: EngineMessage.Id) {
        self.peerId = id.peerId.toInt64()
        self.namespace = id.namespace
        self.messageId = id.id
    }

    init(_ message: LocalArchivedMessage) {
        self.peerId = message.peerId
        self.namespace = message.namespace
        self.messageId = message.messageId
    }
}

private struct LocalArchiveBrowserState {
    var searchText = ""
    var records: [LocalArchivedMessage] = []
    var peerNames: [Int64: String] = [:]
    var loading = false
    var unavailable = false
    var hasOlder = false
    var hasNewer = false
}

/// Retains a single bounded page. Cursor history contains numbers, not message text.
/// All mutable UI state and callbacks are confined to the main queue.
private final class LocalArchiveBrowserModel {
    private let context: AccountContext
    private let archive: LocalArchiveCoordinator
    private let peerId: Int64?
    private let message: LocalArchiveMessageReference?
    private let requestDisposable = MetaDisposable()
    private let peersDisposable = MetaDisposable()
    private let statusDisposable = MetaDisposable()
    private var requestId = 0
    private var readable = false
    private var cursor: Int64?
    private var previousCursors: [Int64?] = []
    private var searchText = ""
    private var searchGeneration = 0
    private var disposed = false
    private(set) var state = LocalArchiveBrowserState()
    let statePromise = Promise<LocalArchiveBrowserState>(LocalArchiveBrowserState())

    init(context: AccountContext, archive: LocalArchiveCoordinator, peerId: Int64?, message: LocalArchiveMessageReference?) {
        self.context = context
        self.archive = archive
        self.peerId = peerId
        self.message = message
        self.statusDisposable.set((archive.status |> deliverOnMainQueue).start(next: { [weak self] status in
            guard let self else {
                return
            }
            let readable = localArchiveCanRead(status)
            if readable && !self.readable {
                self.readable = true
                self.refresh()
            } else if !readable {
                self.readable = false
                self.requestId += 1
                self.requestDisposable.set(nil)
                self.peersDisposable.set(nil)
                self.cursor = nil
                self.previousCursors.removeAll()
                self.searchText = ""
                self.searchGeneration += 1
                self.state = LocalArchiveBrowserState()
                self.statePromise.set(.single(self.state))
            }
        }))
    }

    deinit {
        self.dispose()
    }

    func dispose() {
        self.disposed = true
        self.searchGeneration += 1
        self.requestDisposable.dispose()
        self.peersDisposable.dispose()
        self.statusDisposable.dispose()
    }

    func setSearchText(_ value: String) {
        guard !self.disposed, self.message == nil, self.readable else { return }
        var bounded = String(decoding: value.utf8.prefix(512), as: UTF8.self)
        while bounded.utf8.count > 512 { bounded.removeLast() }
        guard bounded != self.searchText else { return }
        self.searchText = bounded
        self.searchGeneration += 1
        let generation = self.searchGeneration
        self.requestId += 1
        self.requestDisposable.set(nil)
        self.peersDisposable.set(nil)
        self.state.searchText = bounded
        self.state.loading = true
        self.state.records = []
        self.statePromise.set(.single(self.state))
        Queue.mainQueue().after(0.25) { [weak self] in
            guard let self, !self.disposed, self.readable, self.searchGeneration == generation else { return }
            self.refresh()
        }
    }

    func refresh() {
        self.cursor = nil
        self.previousCursors.removeAll()
        self.load()
    }

    func older() {
        guard !self.state.loading, self.state.hasOlder, let oldest = self.state.records.map({ $0.sequence }).min() else {
            return
        }
        self.previousCursors.append(self.cursor)
        self.cursor = oldest
        self.load()
    }

    func newer() {
        guard !self.state.loading, !self.previousCursors.isEmpty else {
            return
        }
        self.cursor = self.previousCursors.removeLast()
        self.load()
    }

    private func load() {
        guard self.readable, !self.disposed else {
            return
        }
        self.requestId += 1
        let requestId = self.requestId
        self.peersDisposable.set(nil)
        self.state = LocalArchiveBrowserState()
        self.state.searchText = self.searchText
        self.state.loading = true
        self.state.hasNewer = !self.previousCursors.isEmpty
        self.statePromise.set(.single(self.state))
        let query: Signal<LocalArchivePage, NoError>
        if let message = self.message {
            query = self.archive.timeline(peerId: message.peerId, namespace: message.namespace, messageId: message.messageId, beforeSequence: self.cursor, limit: 21)
        } else if !self.searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            query = self.archive.searchMessages(query: self.searchText, peerId: self.peerId, beforeSequence: self.cursor, limit: 21)
        } else {
            query = self.archive.deletedMessages(peerId: self.peerId, beforeSequence: self.cursor, limit: 21)
        }
        self.requestDisposable.set((query |> deliverOnMainQueue).start(next: { [weak self] page in
            guard let self, self.readable, self.requestId == requestId else {
                return
            }
            self.state.loading = false
            self.state.unavailable = page.unavailable
            self.state.hasOlder = page.records.count > 20
            let records = Array(page.records.sorted(by: { $0.sequence > $1.sequence }).prefix(20))
            self.state.records = self.message == nil ? records : Array(records.reversed())
            self.statePromise.set(.single(self.state))
            let peerIds = Array(Set(records.map { $0.peerId }))
            if !peerIds.isEmpty {
                let queries = peerIds.map { self.context.engine.data.get(TelegramEngine.EngineData.Item.Peer.Peer(id: EnginePeer.Id($0))) }
                self.peersDisposable.set((combineLatest(queries) |> deliverOnMainQueue).start(next: { [weak self] peers in
                    guard let self, self.readable, self.requestId == requestId else {
                        return
                    }
                    for (id, peer) in zip(peerIds, peers) {
                        if let peer {
                            self.state.peerNames[id] = peer.compactDisplayTitle
                        }
                    }
                    self.statePromise.set(.single(self.state))
                }))
            }
        }))
    }
}

private func localArchiveMessagesController(context: AccountContext, archive: LocalArchiveCoordinator, peerId: Int64?, message: LocalArchiveMessageReference?) -> ViewController {
    let model = LocalArchiveBrowserModel(context: context, archive: archive, peerId: peerId, message: message)
    let mediaDisposable = MetaDisposable()
    var presentController: ((ViewController) -> Void)?
    let formatter = DateFormatter()
    formatter.dateStyle = .medium
    formatter.timeStyle = .short
    var pushController: ((ViewController) -> Void)?
    let arguments = LocalArchiveArguments(searchUpdated: { model.setSearchText($0) }, action: { id in
        switch id {
        case .fixed(1):
            model.newer()
        case .fixed(2):
            model.older()
        case .fixed(3):
            pushController?(localArchiveSettingsController(context: context, peerId: peerId))
        case let .media(sequence, slot):
            guard let record = model.state.records.first(where: { $0.sequence == sequence }) else { return }
            mediaDisposable.set((archive.mediaData(eventKey: record.eventKey, slot: slot)
            |> deliverOn(Queue.concurrentDefaultQueue())
            |> map { data -> UIImage? in
                guard let data, let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
                      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
                      let width = (properties[kCGImagePropertyPixelWidth as String] as? NSNumber)?.uint64Value,
                      let height = (properties[kCGImagePropertyPixelHeight as String] as? NSNumber)?.uint64Value,
                      width > 0, height > 0, width <= 20_000, height <= 20_000, width * height <= 80_000_000,
                      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 2048, kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { return nil }
                return UIImage(cgImage: image)
            }
            |> deliverOnMainQueue).start(next: { image in
                if let image {
                    pushController?(LocalArchivedImageController(image: image, presentationData: context.sharedContext.currentPresentationData.with { $0 }))
                } else {
                    let data = context.sharedContext.currentPresentationData.with { $0 }
                    presentController?(textAlertController(context: context, title: "Preview unavailable", text: "This attachment is missing or cannot be previewed as an image.", actions: [TextAlertAction(type: .defaultAction, title: data.strings.Common_OK, action: {})]))
                }
            }))
        case let .record(sequence):
            if message == nil, let record = model.state.records.first(where: { $0.sequence == sequence }) {
                pushController?(localArchiveMessagesController(context: context, archive: archive, peerId: record.peerId, message: LocalArchiveMessageReference(record)))
            }
        default:
            break
        }
    })
    let signal = combineLatest(queue: .mainQueue(), context.sharedContext.presentationData, archive.status, model.statePromise.get())
    |> map { presentationData, status, state -> (ItemListControllerState, (ItemListNodeState, Any)) in
        var entries: [LocalArchiveRow] = []
        let ready = localArchiveCanRead(status)
        let information: String
        if !ready {
            information = localArchiveStatusText(status)
        } else if state.loading {
            information = "Loading archived text…"
        } else if state.unavailable {
            information = "This page could not be loaded. Try Refresh after checking available storage."
        } else if state.records.isEmpty {
            information = "No captured messages on this page. Only text received and captured by this client can appear here."
        } else if !state.searchText.isEmpty {
            information = "Local word and phrase matches in deleted messages and earlier versions. Search is case-insensitive; no query or result is sent to Telegram."
        } else if message != nil {
            information = "Earlier versions in capture order, oldest first on this page. Times reflect this device’s observation; they may differ from the server’s event time."
        } else {
            information = "Most recently captured deletions first. Tap a message to see its captured versions."
        }
        if message == nil && ready {
            entries.append(LocalArchiveRow(stableId: .fixed(4), order: 0, section: 0, content: .search(state.searchText)))
        }
        entries.append(LocalArchiveRow(stableId: .fixed(0), order: 1, section: 0, content: .text(information)))
        if ready && state.hasNewer {
            entries.append(LocalArchiveRow(stableId: .fixed(1), order: 2, section: 0, content: .action("Newer records", "", !state.loading)))
        }
        if ready && state.hasOlder {
            entries.append(LocalArchiveRow(stableId: .fixed(2), order: 3, section: 0, content: .action("Older records", "", !state.loading)))
        }
        entries.append(LocalArchiveRow(stableId: .fixed(3), order: 4, section: 0, content: .action("Archive Settings", "This account", true)))
        if ready {
            for (index, record) in state.records.enumerated() {
                let date = formatter.string(from: Date(timeIntervalSince1970: TimeInterval(record.observedAt)))
                let kind = record.isDeleted ? "Deleted" : "Before edit"
                let peerName = state.peerNames[record.peerId] ?? "Unavailable chat"
                if message == nil {
                    let preview = record.text.isEmpty ? (record.storedMediaCount > 0 ? "[Archived attachment]" : (record.unavailableMediaCount > 0 ? "[Attachment file not archived]" : "[No text]")) : String(record.text.prefix(160))
                    entries.append(LocalArchiveRow(stableId: .record(record.sequence), order: 5 + index, section: 1, content: .action(preview, state.searchText.isEmpty ? "\(peerName) · \(date)" : "\(kind) · \(peerName) · \(date)", true)))
                } else {
                    let sent = formatter.string(from: Date(timeIntervalSince1970: TimeInterval(record.sentAt)))
                    var detail = "\(kind) · \(date)\n\(peerName) · Originally sent \(sent)"
                    if let editAt = record.serverEditAt {
                        detail += "\nServer edit time: \(formatter.string(from: Date(timeIntervalSince1970: TimeInterval(editAt))))"
                    }
                    entries.append(LocalArchiveRow(stableId: .recordHeading(record.sequence), order: 5 + index * 20, section: Int32(1 + index), content: .heading(detail)))
                    let boundedText = String(record.text.prefix(8001))
                    var text = boundedText.isEmpty ? "[No text]" : String(boundedText.prefix(8000))
                    if record.storedMediaCount > 0 {
                        text += "\n\n[\(record.storedMediaCount) attachment(s) retained in encrypted storage.]"
                    }
                    if record.unavailableMediaCount > 0 {
                        text += "\n\n[\(record.unavailableMediaCount) attachment(s): metadata saved; file bytes not archived.]"
                    }
                    if boundedText.count > 8000 {
                        text += "\n\n[Preview limited to 8,000 characters.]"
                    }
                    entries.append(LocalArchiveRow(stableId: .record(record.sequence), order: 6 + index * 20, section: Int32(1 + index), content: .text(text)))
                    for (slot, kind) in record.mediaKinds.enumerated() where kind == "photo" || kind == "animation" {
                        entries.append(LocalArchiveRow(stableId: .media(record.sequence, slot), order: 7 + index * 20 + slot, section: Int32(1 + index), content: .action("Preview archived image \(slot + 1)", "Local archive", true)))
                    }
                }
            }
        }
        let data = ItemListPresentationData(presentationData)
        let refresh = ItemListNavigationButton(content: .text("Refresh"), style: .regular, enabled: ready && !state.loading, action: { model.refresh() })
        let controller = ItemListControllerState(presentationData: data, title: .text(message == nil ? (state.searchText.isEmpty ? "Deleted Messages" : "Archive Search") : "Captured Versions"), leftNavigationButton: nil, rightNavigationButton: refresh, backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (controller, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    |> afterDisposed {
        model.dispose()
        mediaDisposable.dispose()
    }
    let controller = ItemListController(context: context, state: signal)
    presentController = { [weak controller] next in controller?.present(next, in: .window(.root)) }
    pushController = { [weak controller] next in
        (controller?.navigationController as? NavigationController)?.pushViewController(next)
    }
    return controller
}

private final class LocalArchivedImageController: ViewController {
    private let imageView: UIImageView

    init(image: UIImage, presentationData: PresentationData) {
        self.imageView = UIImageView(image: image)
        super.init(navigationBarPresentationData: NavigationBarPresentationData(presentationTheme: presentationData.theme, presentationStrings: presentationData.strings))
        self.title = "Archived image"
        self.imageView.contentMode = .scaleAspectFit
        self.imageView.accessibilityLabel = "Locally archived image"
        self.imageView.isAccessibilityElement = true
    }

    required init(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func loadDisplayNode() {
        super.loadDisplayNode()
        self.displayNode.backgroundColor = .black
        self.displayNode.view.addSubview(self.imageView)
    }

    override func containerLayoutUpdated(_ layout: ContainerViewLayout, transition: ContainedViewLayoutTransition) {
        super.containerLayoutUpdated(layout, transition: transition)
        self.imageView.frame = CGRect(origin: .zero, size: layout.size)
    }
}
