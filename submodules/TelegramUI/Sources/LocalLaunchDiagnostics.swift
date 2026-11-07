// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit

/// Local startup status only. This never records account identifiers, paths,
/// credentials, message content, exception descriptions or network payloads.
/// The smoke runner accepts a visible route, rather than an empty scene, as ready.
final class LocalLaunchDiagnostics {
    enum Stage: String {
        case applicationEntered
        case windowCreated
        case sceneAttached
        case storageReady
        case accountManagerReady
        case sharedContextReady
        case authorizationPrepared
        case conversationsPrepared
        case authorizationVisible
        case conversationsVisible
        case failed
    }

    enum Failure: String {
        case storageUnavailable
        case keychainUnavailable
        case storageConfiguration
        case initializationUnavailable
    }

    enum Route: Equatable {
        case authorization
        case conversations
    }

    private static let writer = DispatchQueue(label: "AyuGram.launch-status", qos: .utility)
    private weak var window: UIWindow?
    private var stage: Stage = .applicationEntered
    private var failure: Failure?
    private var diagnosticCode: Int?
    private var route: Route?
    private var isReady = false
    private var watchdog: DispatchWorkItem?
    private var overlay: UIView?
    private var protectedInterfaceActive = false

    func begin() {
        precondition(Thread.isMainThread)
        self.mark(.applicationEntered)
        self.scheduleWatchdog()
    }

    func attach(window: UIWindow) {
        precondition(Thread.isMainThread)
        self.window = window
        self.updateWindowIdentifier()
        if self.failure != nil { self.showStatus() }
    }

    func protectedInterfaceChanged(isLocked: Bool) {
        precondition(Thread.isMainThread)
        self.protectedInterfaceActive = isLocked
        if isLocked {
            self.overlay?.removeFromSuperview()
            self.overlay = nil
        } else if !self.isReady {
            self.scheduleWatchdog()
        }
    }

    func mark(_ stage: Stage) {
        precondition(Thread.isMainThread)
        guard !self.isReady else { return }
        if stage == .sceneAttached, self.finishReadyIfVisible() { return }
        // An early failure must remain visible even when the scene attaches later.
        if self.failure == nil { self.stage = stage }
        self.updateWindowIdentifier()
        self.writeMarker()
    }

    func fail(_ failure: Failure, diagnosticCode: Int? = nil) {
        precondition(Thread.isMainThread)
        guard !self.isReady else { return }
        self.failure = failure
        self.diagnosticCode = diagnosticCode
        self.stage = .failed
        self.watchdog?.cancel()
        self.updateWindowIdentifier()
        self.writeMarker()
        self.showStatus()
    }

    /// Called only after the genuine application/authentication controller is
    /// installed. A scene must also be attached and visible before success.
    func ready(_ route: Route) {
        precondition(Thread.isMainThread)
        self.route = route
        if !self.finishReadyIfVisible() {
            self.mark(route == .authorization ? .authorizationPrepared : .conversationsPrepared)
        }
    }

    @discardableResult
    private func finishReadyIfVisible() -> Bool {
        guard let route = self.route, let window = self.window,
              window.windowScene != nil, !window.isHidden else { return false }
        self.isReady = true
        self.failure = nil
        self.diagnosticCode = nil
        self.stage = route == .authorization ? .authorizationVisible : .conversationsVisible
        self.watchdog?.cancel()
        self.overlay?.removeFromSuperview()
        self.overlay = nil
        self.updateWindowIdentifier()
        self.writeMarker()
        return true
    }

    private func updateWindowIdentifier() {
        self.window?.accessibilityIdentifier = "AyuLaunch." + self.stage.rawValue
    }

    private func writeMarker() {
        let marker: [String: Any] = [
            "stage": self.stage.rawValue,
            "failure": self.failure.map { $0.rawValue as Any } ?? NSNull(),
            "diagnosticCode": self.diagnosticCode.map { $0 as Any } ?? NSNull()
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys]),
              data.count < 1024,
              let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return }
        let directory = support.appendingPathComponent("AyuGram/LaunchDiagnostics", isDirectory: true)
        Self.writer.async {
            // Overwrite one bounded marker; diagnostics must never block startup.
            do {
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [
                    .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication
                ])
                try data.write(to: directory.appendingPathComponent("latest.json"), options: [
                    .atomic, .completeFileProtectionUntilFirstUserAuthentication
                ])
            } catch {
                // Error descriptions can contain private paths. Omit them.
            }
        }
    }

    private func scheduleWatchdog() {
        self.watchdog?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.isReady else { return }
            if self.protectedInterfaceActive {
                // An existing account's passcode screen is legitimate UI; never
                // cover it or treat waiting for the user as a startup failure.
                self.scheduleWatchdog()
                return
            }
            self.showStatus()
        }
        self.watchdog = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 20.0, execute: work)
    }

    private func showStatus() {
        guard !self.isReady, let window = self.window, self.overlay == nil,
              !self.protectedInterfaceActive else { return }
        let panel = UIView(frame: window.bounds)
        panel.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        panel.backgroundColor = .systemBackground
        panel.layer.zPosition = 1_000_000
        panel.accessibilityViewIsModal = true
        panel.accessibilityIdentifier = "AyuLaunch.StartupStatus"

        let title = UILabel()
        title.font = .preferredFont(forTextStyle: .title2)
        title.adjustsFontForContentSizeCategory = true
        title.textColor = .label
        title.numberOfLines = 0
        title.text = self.failure == nil ? "Startup is taking longer than expected" : "Telegram could not finish starting"

        let detail = UILabel()
        detail.font = .preferredFont(forTextStyle: .body)
        detail.adjustsFontForContentSizeCategory = true
        detail.textColor = .secondaryLabel
        detail.numberOfLines = 0
        let codeText = self.diagnosticCode.map { " / OSStatus: " + String($0) } ?? ""
        detail.text = "This screen does not reset your data. Keep the same SideStore app identity and signing account when updating. You can copy the local startup status for diagnosis.\n\nStatus: " + self.stage.rawValue + (self.failure.map { " / " + $0.rawValue } ?? "") + codeText

        let copy = UIButton(type: .system)
        copy.setTitle("Copy startup status", for: .normal)
        copy.addAction(UIAction { [weak self] _ in
            guard let self else { return }
            UIPasteboard.general.string = "Telegram startup: " + self.stage.rawValue + (self.failure.map { " / " + $0.rawValue } ?? "") + (self.diagnosticCode.map { " / OSStatus: " + String($0) } ?? "")
        }, for: .touchUpInside)

        let wait = UIButton(type: .system)
        wait.setTitle("Continue waiting", for: .normal)
        wait.addAction(UIAction { [weak self] _ in
            guard let self else { return }
            self.overlay?.removeFromSuperview()
            self.overlay = nil
            self.scheduleWatchdog()
        }, for: .touchUpInside)

        let stack = UIStackView(arrangedSubviews: [title, detail, copy, wait])
        stack.axis = .vertical
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        let scroll = UIScrollView()
        scroll.translatesAutoresizingMaskIntoConstraints = false
        panel.addSubview(scroll)
        scroll.addSubview(stack)
        NSLayoutConstraint.activate([
            scroll.leadingAnchor.constraint(equalTo: panel.safeAreaLayoutGuide.leadingAnchor),
            scroll.trailingAnchor.constraint(equalTo: panel.safeAreaLayoutGuide.trailingAnchor),
            scroll.topAnchor.constraint(equalTo: panel.safeAreaLayoutGuide.topAnchor),
            scroll.bottomAnchor.constraint(equalTo: panel.safeAreaLayoutGuide.bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 32),
            stack.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -32),
            stack.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -48),
            copy.heightAnchor.constraint(greaterThanOrEqualToConstant: 44),
            wait.heightAnchor.constraint(greaterThanOrEqualToConstant: 44)
        ])
        window.addSubview(panel)
        self.overlay = panel
    }
}
