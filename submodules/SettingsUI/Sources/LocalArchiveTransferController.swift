// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import UniformTypeIdentifiers
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ItemListUI
import AccountContext

/// Explicit picker/password coverage must survive foreground transitions even
/// when UIKit temporarily removes the presenter's view from its window.
struct LocalArchiveTransferVisibility: Equatable {
    var visible = false
    var foreground: Bool
    var subflow = false
    var needsReadAccess: Bool { self.foreground && (self.visible || self.subflow) }
}

private struct LocalArchiveTransferViewState: Equatable {
    var busy = false
    var message = "No backup has been selected."
}

private struct LocalArchiveTransferRow: ItemListNodeEntry {
    enum Content: Equatable { case header(String), text(String), action(String, Bool, Int) }
    let stableId: Int
    let section: ItemListSectionId
    let content: Content
    static func < (lhs: LocalArchiveTransferRow, rhs: LocalArchiveTransferRow) -> Bool { lhs.stableId < rhs.stableId }

    func item(presentationData: ItemListPresentationData, arguments: Any) -> ListViewItem {
        let arguments = arguments as! LocalArchiveTransferArguments
        switch self.content {
        case let .header(text):
            return ItemListSectionHeaderItem(presentationData: presentationData, text: text, sectionId: self.section)
        case let .text(text):
            return ItemListTextItem(presentationData: presentationData, text: .plain(text), sectionId: self.section)
        case let .action(title, enabled, action):
            return ItemListActionItem(presentationData: presentationData, systemStyle: .glass,
                title: title, kind: enabled ? .generic : .disabled, alignment: .natural,
                sectionId: self.section, style: .blocks, action: { if enabled { arguments.perform(action) } })
        }
    }
}

private final class LocalArchiveTransferArguments: NSObject, UIDocumentPickerDelegate, UITextFieldDelegate {
    let context: AccountContext
    let archive: LocalArchiveCoordinator?
    let viewState = ValuePromise(LocalArchiveTransferViewState(), ignoreRepeated: true)
    weak var controller: ViewController?
    private var current = LocalArchiveTransferViewState()
    private let operation = MetaDisposable()
    private let lease = MetaDisposable()
    private var observers: [NSObjectProtocol] = []
    private var selectedURL: URL?
    private var securityScope = false
    private var exportFile: LocalArchiveExportFile?
    private var visibility = LocalArchiveTransferVisibility(foreground: false)
    private var hasReadLease = false
    var captureEnabled = false

    init(context: AccountContext) {
        self.context = context; self.archive = context.account.localArchive
        super.init()
        self.visibility.foreground = UIApplication.shared.applicationState == .active
        self.observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification,
            object: nil, queue: .main, using: { [weak self] _ in
                guard let self else { return }
                self.visibility.foreground = false
                self.operation.set(nil)
                if self.current.busy {
                    self.releaseImportScope(); self.visibility.subflow = false
                    self.message("Transfer interrupted in the background. Uncommitted work was cancelled; an already committed restore may have completed.")
                }
                self.refreshLease()
            }))
        self.observers.append(NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification,
            object: nil, queue: .main, using: { [weak self] _ in
                guard let self else { return }
                self.visibility.foreground = true
                self.refreshLease()
            }))
    }

    deinit {
        self.operation.dispose(); self.lease.dispose()
        self.observers.forEach { NotificationCenter.default.removeObserver($0) }
        if self.securityScope { self.selectedURL?.stopAccessingSecurityScopedResource() }
    }

    func setVisible(_ visible: Bool) {
        self.visibility.visible = visible
        if !visible && !self.visibility.subflow { self.cancel() }
        self.refreshLease()
    }

    private func setSubflow(_ active: Bool) {
        self.visibility.subflow = active
        self.refreshLease()
    }

    private func refreshLease() {
        let needed = self.archive != nil && self.visibility.needsReadAccess
        guard needed != self.hasReadLease else { return }
        self.hasReadLease = needed
        self.lease.set(needed ? self.archive?.acquireReadAccess() : nil)
    }

    func perform(_ action: Int) {
        if action == 3 { self.cancel(); return }
        guard !self.current.busy else { return }
        switch action {
        case 0: self.password(export: true)
        case 1: self.confirmImport()
        case 2:
            if self.captureEnabled {
                let alert = UIAlertController(title: "Pause message preservation?",
                    message: "New deletions and edits while capture is paused will not be saved. Existing archive history stays. Capture remains paused after backup or restore until you explicitly resume it.", preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
                alert.addAction(UIAlertAction(title: "Pause capture", style: .destructive, handler: { [weak self] _ in self?.saveCapture(false) }))
                self.controller?.present(alert, animated: true)
            } else {
                self.saveCapture(true)
            }
        default: break
        }
    }

    private func saveCapture(_ enabled: Bool) {
        guard let archive = self.archive else { return }
        self.operation.set((archive.setEnabled(enabled) |> deliverOnMainQueue).start(next: { [weak self] saved in
            self?.message(saved ? "Capture preference saved for this account." : "The capture preference could not be changed.")
        }))
    }

    private func message(_ text: String, busy: Bool = false) {
        self.current = LocalArchiveTransferViewState(busy: busy, message: text)
        self.viewState.set(self.current)
    }

    private func cancel() {
        self.operation.set(nil)
        self.releaseImportScope()
        self.setSubflow(false)
        if self.current.busy {
            self.message("Cancellation requested. Uncommitted work will stop; a restore that already committed may have completed.")
        }
    }

    private func confirmImport() {
        self.setSubflow(true)
        let alert = UIAlertController(title: "Restore local history?", message: "This explicitly adds saved versions and media for the same Telegram account, including content previously purged or currently excluded from automatic capture. Existing versions stay; a conflicting version aborts the merge. Current storage limits still apply. Telegram messages and sessions are never imported.", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel, handler: { [weak self] _ in self?.setSubflow(false) }))
        alert.addAction(UIAlertAction(title: "Choose encrypted backup", style: .default, handler: { [weak self] _ in
            guard let self else { return }
            let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.data], asCopy: false)
            picker.allowsMultipleSelection = false; picker.delegate = self
            self.controller?.present(picker, animated: true)
        }))
        self.controller?.present(alert, animated: true)
    }

    private func password(export: Bool) {
        self.setSubflow(true)
        let alert = UIAlertController(title: export ? "Protect this backup" : "Unlock encrypted backup",
            message: export ? "Use at least 12 characters. The password cannot be recovered. Only an encrypted file goes to your chosen destination." : "Enter this backup's password. It must belong to the same Telegram account.", preferredStyle: .alert)
        alert.addTextField { [weak self] field in
            field.placeholder = "Password"; field.isSecureTextEntry = true
            field.autocorrectionType = .no; field.autocapitalizationType = .none; field.delegate = self
        }
        if export {
            alert.addTextField { [weak self] field in
                field.placeholder = "Repeat password"; field.isSecureTextEntry = true
                field.autocorrectionType = .no; field.autocapitalizationType = .none; field.delegate = self
            }
        }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel, handler: { [weak self] _ in
            self?.releaseImportScope(); self?.setSubflow(false)
        }))
        alert.addAction(UIAlertAction(title: export ? "Create encrypted backup" : "Restore history", style: .default, handler: { [weak self, weak alert] _ in
            guard let self, let alert else { return }
            let password = alert.textFields?.first?.text ?? ""
            let repeated = export ? alert.textFields?.last?.text : password
            alert.textFields?.forEach { $0.text = nil }
            guard password.count >= 12, password.utf8.count <= 1024, repeated == password else {
                self.releaseImportScope()
                self.setSubflow(false)
                self.message("Use a matching password of at least 12 characters (at most 1024 UTF-8 bytes).")
                return
            }
            if export { self.startExport(password) } else { self.startImport(password) }
        }))
        self.controller?.present(alert, animated: true)
    }

    func textField(_ textField: UITextField, shouldChangeCharactersIn range: NSRange, replacementString string: String) -> Bool {
        guard let current = textField.text, let target = Range(range, in: current) else { return false }
        return current.replacingCharacters(in: target, with: string).utf8.count <= 1024
    }

    private func startExport(_ password: String) {
        guard let archive = self.archive else { self.setSubflow(false); self.message("Archive access is unavailable."); return }
        self.message("Creating a consistent encrypted snapshot…", busy: true)
        self.operation.set((archive.exportBackup(password: password) |> deliverOnMainQueue).start(next: { [weak self] result in
            guard let self else { return }
            switch result {
            case let .exported(file):
                self.exportFile = file
                self.message("Encrypted backup created. Choose where to save it.")
                let picker = UIDocumentPickerViewController(forExporting: [file.url], asCopy: true)
                picker.delegate = self
                self.controller?.present(picker, animated: true)
            case let .failed(failure): self.setSubflow(false); self.message(Self.description(failure))
            case .imported: self.setSubflow(false); self.message("Unexpected operation result; no file was shared.")
            }
        }))
    }

    private func startImport(_ password: String) {
        guard let archive = self.archive, let source = self.selectedURL else {
            self.releaseImportScope(); self.setSubflow(false); self.message("Select an encrypted archive first."); return
        }
        self.message("Authenticating and merging encrypted history…", busy: true)
        self.operation.set((archive.importBackup(from: source, password: password) |> deliverOnMainQueue).start(next: { [weak self] result in
            guard let self else { return }
            self.releaseImportScope()
            self.setSubflow(false)
            switch result {
            case let .imported(added, duplicates, media, removed):
                self.message("Restored \(added) versions and \(media) media items; \(duplicates) identical versions already existed. Current retention removed \(removed) versions.")
            case let .failed(failure): self.message(Self.description(failure))
            case .exported: self.message("Unexpected operation result; restore did not complete.")
            }
        }))
    }

    private func releaseImportScope() {
        if self.securityScope { self.selectedURL?.stopAccessingSecurityScopedResource() }
        self.selectedURL = nil; self.securityScope = false
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        if self.exportFile != nil {
            self.exportFile = nil
            self.setSubflow(false)
            self.message("Encrypted backup handed to your selected file provider.")
            return
        }
        self.releaseImportScope()
        guard urls.count == 1, let url = urls.first, url.isFileURL else {
            self.setSubflow(false); self.message("Choose one encrypted archive file."); return
        }
        self.selectedURL = url; self.securityScope = url.startAccessingSecurityScopedResource()
        // Present only after the system picker completes its dismissal.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            guard let self, self.selectedURL != nil else { return }
            self.password(export: false)
        }
    }

    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        self.exportFile = nil; self.releaseImportScope(); self.setSubflow(false); self.message("No file was transferred.")
    }

    private static func description(_ failure: LocalArchiveTransferFailure) -> String {
        switch failure {
        case .invalidPassword: return "Use a password of at least 12 characters and at most 1024 UTF-8 bytes."
        case .authenticationFailed: return "Wrong password or an altered encrypted backup. Nothing was merged."
        case .wrongAccount: return "This backup belongs to a different Telegram account. Nothing was merged."
        case .unsupportedVersion: return "The backup schema or format is not supported by this app version."
        case .invalidArchive: return "This is not a complete supported encrypted archive. Nothing was merged."
        case .conflict: return "A saved version or media item conflicts with existing history. The entire merge was rolled back."
        case .cancelled: return "Transfer cancelled before completion."
        case .storageUnavailable: return "Storage is unavailable or full. Check archive status before retrying; the archive key was not replaced."
        case .captureMustBePaused: return "Pause automatic capture before starting a backup or restore."
        case .unavailable: return "Unlock the device and wait for archive access, then retry. A damaged existing archive is never overwritten by restore."
        }
    }
}

public func localArchiveTransferController(context: AccountContext) -> ViewController {
    let arguments = LocalArchiveTransferArguments(context: context)
    let archive = context.account.localArchive
    let settings = archive?.settings ?? .single(LocalArchiveSettings())
    let status = archive?.status ?? .single(.unavailable)
    let signal = combineLatest(context.sharedContext.presentationData, settings, status, arguments.viewState.get())
    |> deliverOnMainQueue
    |> map { presentationData, settings, status, state -> (ItemListControllerState, (ItemListNodeState, Any)) in
        arguments.captureEnabled = settings.enabled
        let paused = settings.isSupported && !settings.enabled && !settings.pendingPurge
        let editable = archive != nil && settings.isSupported && !settings.pendingPurge && !state.busy
        let canImport = editable && paused && (status == .browsing || status == .disabled || status == .ready)
        let canExport = editable && paused && status == .browsing
        let entries: [LocalArchiveTransferRow] = [
            .init(stableId: 0, section: 0, content: .header("PASSWORD-PROTECTED ARCHIVE")),
            .init(stableId: 1, section: 0, content: .text("Back up archived message versions and captured media. Separate Telegram session, authorization-key and credential stores, live Postbox data and capture preferences are never included. Archived messages or files can themselves contain sensitive information. The account's persistent archive key is never exported.")),
            .init(stableId: 2, section: 1, content: .action(settings.enabled ? "Pause automatic capture" : "Resume automatic capture", editable, 2)),
            .init(stableId: 3, section: 1, content: .text("Backup and restore currently require an explicit capture pause. New deletions and edits during that pause are not saved. Capture stays paused until you explicitly resume it. Existing saved history remains browsable; pending captures and messages outside the archive are not part of a backup.")),
            .init(stableId: 4, section: 2, content: .action("Export encrypted archive", canExport, 0)),
            .init(stableId: 5, section: 2, content: .action("Import encrypted archive", canImport, 1)),
            .init(stableId: 6, section: 2, content: .text("Restore merges history for the same Telegram account, including backups from another installation. Identical entries are deduplicated; conflicts abort the entire merge. This explicit restore can reintroduce previously purged or currently excluded history. Current retention and storage limits still apply. No live Telegram message is created or changed.")),
            .init(stableId: 7, section: 3, content: .header("TRANSFER STATUS")),
            .init(stableId: 8, section: 3, content: .text(state.message)),
            .init(stableId: 9, section: 3, content: .action("Cancel current transfer", state.busy, 3)),
            .init(stableId: 10, section: 4, content: .text("The password cannot be recovered. Keep a secure copy separately. Files remain local until you explicitly choose a destination in the system picker; a selected cloud provider receives only ciphertext. This is manual backup, not AyuSync or automatic upload. File-format and cryptographic implementation review remain separate from compilation/tests."))
        ]
        let data = ItemListPresentationData(presentationData)
        let state = ItemListControllerState(presentationData: data, title: .text("Encrypted Archive Backup"),
            leftNavigationButton: nil, rightNavigationButton: nil,
            backNavigationButton: ItemListBackButton(title: presentationData.strings.Common_Back), animateChanges: false)
        return (state, (ItemListNodeState(presentationData: data, entries: entries, style: .blocks), arguments))
    }
    let controller = ItemListController(context: context, state: signal)
    arguments.controller = controller
    controller.didAppear = { _ in arguments.setVisible(true) }
    controller.willDisappear = { _ in arguments.setVisible(false) }
    return controller
}
