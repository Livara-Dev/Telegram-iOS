// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import SwiftSignalKit
import CustomeOptionsRenderer

@MainActor
private final class FixtureAppearanceButton: UIButton {
    var sample: (() -> [String: Bool])?
    override var accessibilityValue: String? {
        get {
            guard let sample = self.sample,
                  let data = try? JSONSerialization.data(withJSONObject: sample(), options: [.sortedKeys]) else { return nil }
            return String(data: data, encoding: .utf8)
        }
        set { /* The value always samples rendered UIKit state. */ }
    }
}

/// Synthetic callback source only: no account, Postbox, archive or network.
@MainActor
public final class CustomeOptionsFixture: NSObject {
    public private(set) var state: CustomeOptionsState
    public private(set) var toggleCalls: [CustomeOptionsToggle] = []
    public private(set) var actions: [CustomeOptionsAction] = []
    public private(set) var resetCount = 0
    public private(set) var cosmeticsState = LocalClientCosmeticsViewState(editable: true, information: "Synthetic preference callbacks; no account or server entitlements")
    public private(set) var accentCalls: [Int32] = []
    public private(set) var motionCalls: [Bool] = []
    public private(set) var bundledIconCalls: [Bool] = []
    public private(set) var cosmeticsResetCount = 0
    public var persistSucceeds = true
    public var deferPersistence = false
    private var pending: (() -> Void)?
    private let statePromise = Promise<CustomeOptionsState>()
    private let cosmeticsPromise = Promise<LocalClientCosmeticsViewState>()
    public private(set) var navigation: UINavigationController!
    public private(set) var window: UIWindow?
    public private(set) var dark = false
    private var ledgers: [UILabel] = []

    public init(available: Bool = true) {
        self.state = CustomeOptionsState(archiveAvailable: available, archiveDetail: "Synthetic local archive preference",
            ghostAvailable: available, ghostDetail: "Synthetic account privacy preference",
            filtersAvailable: available, filtersDetail: "Synthetic local filtering preference")
        super.init()
        self.statePromise.set(.single(self.state))
        self.cosmeticsPromise.set(.single(self.cosmeticsState))
    }

    public func install(in scene: UIWindowScene) {
        let root = self.controller()
        let navigation = UINavigationController(rootViewController: root)
        navigation.navigationBar.prefersLargeTitles = false
        navigation.toolbar.isTranslucent = false
        navigation.setToolbarHidden(false, animated: false)
        self.navigation = navigation
        self.configureToolbar(root)
        let window = UIWindow(windowScene: scene)
        window.overrideUserInterfaceStyle = .light
        window.rootViewController = navigation
        self.window = window
        window.makeKeyAndVisible()
        self.updateLedger()
    }

    public func close() {
        self.window?.isHidden = true
        self.window?.rootViewController = nil
        self.window = nil
    }

    public func controller(mode: CustomeOptionsMode = .dashboard) -> CustomeOptionsController {
        return CustomeOptionsController(mode: mode, palette: self.palette(),
            navigationBarPresentationData: nil, state: self.statePromise.get(),
            open: { [weak self] action in self?.open(action) },
            setToggle: { [weak self] toggle, enabled, complete in
                guard let self else { complete(false); return }
                self.toggleCalls.append(toggle)
                let finish = { [weak self] in
                    guard let self else { complete(false); return }
                    if self.persistSucceeds {
                        switch toggle {
                        case .archive: self.state.archiveEnabled = enabled
                        case .ghostActivity: self.state.ghostEnabled = enabled
                        case .messageFilters: self.state.filtersEnabled = enabled
                        }
                        self.statePromise.set(.single(self.state))
                    }
                    self.updateLedger()
                    complete(self.persistSucceeds)
                }
                if self.deferPersistence { self.pending = finish } else { finish() }
            }, reset: { [weak self] complete in
                guard let self else { complete(false); return }
                self.resetCount += 1
                self.state.archiveEnabled = false
                self.state.ghostEnabled = false
                self.state.filtersEnabled = false
                self.statePromise.set(.single(self.state))
                self.updateLedger()
                complete(true)
            })
    }

    public func finishPersistence() { let pending = self.pending; self.pending = nil; pending?() }

    public func cosmeticsController() -> LocalClientCosmeticsController {
        return LocalClientCosmeticsController(palette: self.palette(), navigationBarPresentationData: nil,
            choices: [.init(id: 0, title: "Ayu", rgb: 0x6450E8), .init(id: 1, title: "Ocean", rgb: 0x007F9E)],
            state: self.cosmeticsPromise.get(), selectAccent: { [weak self] id, complete in
                guard let self else { complete(false); return }
                self.accentCalls.append(id)
                self.cosmeticsState = LocalClientCosmeticsViewState(presetId: id, reduceMotion: self.cosmeticsState.reduceMotion,
                    allowBundledPremiumIcons: self.cosmeticsState.allowBundledPremiumIcons, editable: true, information: self.cosmeticsState.information)
                self.publishCosmetics(); complete(true)
            }, setReducedMotion: { [weak self] enabled, complete in
                guard let self else { complete(false); return }
                self.motionCalls.append(enabled)
                self.cosmeticsState = LocalClientCosmeticsViewState(presetId: self.cosmeticsState.presetId, reduceMotion: enabled,
                    allowBundledPremiumIcons: self.cosmeticsState.allowBundledPremiumIcons, editable: true, information: self.cosmeticsState.information)
                self.publishCosmetics(); complete(true)
            }, setBundledIcons: { [weak self] enabled, complete in
                guard let self else { complete(false); return }
                self.bundledIconCalls.append(enabled)
                self.cosmeticsState = LocalClientCosmeticsViewState(presetId: self.cosmeticsState.presetId, reduceMotion: self.cosmeticsState.reduceMotion,
                    allowBundledPremiumIcons: enabled, editable: true, information: self.cosmeticsState.information)
                self.publishCosmetics(); complete(true)
            }, reset: { [weak self] complete in
                guard let self else { complete(false); return }
                self.cosmeticsResetCount += 1
                self.cosmeticsState = LocalClientCosmeticsViewState(editable: true, information: "Synthetic preference callbacks; no account or server entitlements")
                self.publishCosmetics(); complete(true)
            }, open: { [weak self] action in self?.open(action) })
    }

    private func palette() -> CustomeOptionsPalette {
        let base = CustomeOptionsPalette.standard(dark: self.dark)
        let rgb: UInt32 = self.cosmeticsState.presetId == 1 ? 0x007F9E : 0x6450E8
        return CustomeOptionsPalette(background: base.background, surface: base.surface, text: base.text,
            secondaryText: base.secondaryText, separator: base.separator,
            accent: UIColor(red: CGFloat((rgb >> 16) & 255) / 255, green: CGFloat((rgb >> 8) & 255) / 255,
                blue: CGFloat(rgb & 255) / 255, alpha: 1), dark: self.dark)
    }

    private func publishCosmetics() {
        self.cosmeticsPromise.set(.single(self.cosmeticsState))
        self.updatePalettes()
        self.updateLedger()
    }

    private func open(_ action: CustomeOptionsAction) {
        self.actions.append(action)
        if case let .category(category) = action {
            let controller = self.controller(mode: .category(category))
            self.configureToolbar(controller)
            self.navigation.pushViewController(controller, animated: true)
        } else if action == .localCosmetics {
            let controller = self.cosmeticsController()
            self.configureToolbar(controller)
            self.navigation.pushViewController(controller, animated: true)
        }
    }

    private func configureToolbar(_ controller: UIViewController) {
        let ledger = UILabel()
        ledger.font = UIFont.preferredFont(forTextStyle: .caption2)
        ledger.textColor = .secondaryLabel
        ledger.accessibilityIdentifier = "fixture.ledger"
        ledger.isAccessibilityElement = true
        self.ledgers.append(ledger)
        let appearance = FixtureAppearanceButton(type: .system)
        appearance.setTitle("Light / Dark", for: .normal)
        appearance.accessibilityIdentifier = "fixture.appearance"
        appearance.addTarget(self, action: #selector(self.toggleAppearance), for: .touchUpInside)
        appearance.sample = { [weak self] in self?.appearanceObservation() ?? [:] }
        appearance.sizeToFit()
        controller.toolbarItems = [UIBarButtonItem(customView: ledger),
            UIBarButtonItem(barButtonSystemItem: .flexibleSpace, target: nil, action: nil), UIBarButtonItem(customView: appearance)]
        self.updateLedger()
    }

    /// Sample the visible production table independently of the requested mode.
    /// Returning both its actual trait and resolved color prevents a renamed
    /// screenshot or a fixture boolean from passing as rendered dark mode.
    public func appearanceObservation() -> [String: Bool] {
        func findTable(_ root: UIView) -> UITableView? {
            if let table = root as? UITableView,
               table.accessibilityIdentifier == "ayugram.custom-options.list" || table.accessibilityIdentifier == "ayugram.cosmetics.list" { return table }
            for view in root.subviews { if let table = findTable(view) { return table } }
            return nil
        }
        let table = self.navigation?.topViewController?.viewIfLoaded.flatMap(findTable)
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        let color = table.flatMap { table in table.backgroundColor?.resolvedColor(with: table.traitCollection) }
        let sampled = color?.getRed(&red, green: &green, blue: &blue, alpha: &alpha) == true
        let luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722
        return ["window_dark": self.window?.traitCollection.userInterfaceStyle == .dark,
                "table_found": table != nil, "table_dark": table?.traitCollection.userInterfaceStyle == .dark,
                "background_sampled": sampled && alpha > 0.99,
                "background_dark": sampled && luminance < 0.35]
    }

    @objc private func toggleAppearance() {
        self.dark.toggle()
        self.window?.overrideUserInterfaceStyle = self.dark ? .dark : .light
        self.updatePalettes()
    }

    private func updatePalettes() {
        guard let navigation = self.navigation else { return }
        for controller in navigation.viewControllers {
            (controller as? CustomeOptionsController)?.updateAppearance(palette: self.palette())
            (controller as? LocalClientCosmeticsController)?.updateAppearance(palette: self.palette())
        }
    }

    private func updateLedger() {
        for ledger in self.ledgers {
            ledger.text = "Synthetic · resets \(self.resetCount)" + (self.cosmeticsResetCount == 0 ? "" : " · cosmetics \(self.cosmeticsResetCount)")
            ledger.accessibilityValue = "No Telegram account; archive \(self.state.archiveEnabled ? "on" : "off"); ghost \(self.state.ghostEnabled ? "on" : "off"); filters \(self.state.filtersEnabled ? "on" : "off"); accent \(self.cosmeticsState.presetId); motion \(self.cosmeticsState.reduceMotion ? "on" : "off"); bundled icons \(self.cosmeticsState.allowBundledPremiumIcons ? "on" : "off")"
            ledger.sizeToFit()
        }
    }
}
