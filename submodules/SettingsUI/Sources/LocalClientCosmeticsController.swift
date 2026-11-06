// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import Display
import SwiftSignalKit

public struct LocalClientCosmeticsChoice {
    public let id: Int32
    public let title: String
    public let rgb: UInt32?

    public init(id: Int32, title: String, rgb: UInt32?) {
        self.id = id; self.title = title; self.rgb = rgb
    }
}

public struct LocalClientCosmeticsViewState: Equatable {
    public let presetId: Int32
    public let reduceMotion: Bool
    public let allowBundledPremiumIcons: Bool
    public let editable: Bool
    public let information: String

    public init(presetId: Int32 = 0, reduceMotion: Bool = false,
                allowBundledPremiumIcons: Bool = false, editable: Bool = false,
                information: String = "Loading this account’s local appearance choices…") {
        self.presetId = presetId; self.reduceMotion = reduceMotion
        self.allowBundledPremiumIcons = allowBundledPremiumIcons
        self.editable = editable; self.information = information
    }
}

/// Production UIKit renderer; its account-binding facade is kept separate so
/// native tests can drive these exact controls without Telegram credentials.
public final class LocalClientCosmeticsController: ViewController, UITableViewDataSource, UITableViewDelegate {
    private let choices: [LocalClientCosmeticsChoice]
    private let table = UITableView(frame: .zero, style: .insetGrouped)
    private let bannerContainer = UIView()
    private let banner = UILabel()
    private var palette: CustomeOptionsPalette
    private var state = LocalClientCosmeticsViewState()
    private var busy = false
    private var layoutTop: CGFloat?
    private var layoutBottom: CGFloat = 0
    private let stateDisposable = MetaDisposable()
    private let retainedDisposables = DisposableSet()
    private let selectAccent: (Int32, @escaping (Bool) -> Void) -> Void
    private let setReducedMotion: (Bool, @escaping (Bool) -> Void) -> Void
    private let setBundledIcons: (Bool, @escaping (Bool) -> Void) -> Void
    private let reset: (@escaping (Bool) -> Void) -> Void
    private let open: (CustomeOptionsAction) -> Void

    public init(palette: CustomeOptionsPalette, navigationBarPresentationData: NavigationBarPresentationData? = nil,
                choices: [LocalClientCosmeticsChoice], state: Signal<LocalClientCosmeticsViewState, NoError>,
                selectAccent: @escaping (Int32, @escaping (Bool) -> Void) -> Void,
                setReducedMotion: @escaping (Bool, @escaping (Bool) -> Void) -> Void,
                setBundledIcons: @escaping (Bool, @escaping (Bool) -> Void) -> Void,
                reset: @escaping (@escaping (Bool) -> Void) -> Void,
                open: @escaping (CustomeOptionsAction) -> Void) {
        self.palette = palette; self.choices = choices
        self.selectAccent = selectAccent; self.setReducedMotion = setReducedMotion
        self.setBundledIcons = setBundledIcons; self.reset = reset; self.open = open
        super.init(navigationBarPresentationData: navigationBarPresentationData)
        self.title = "Appearance"
        self.table.dataSource = self; self.table.delegate = self
        self.table.rowHeight = UITableView.automaticDimension
        self.table.estimatedRowHeight = 52
        self.table.contentInsetAdjustmentBehavior = .never
        self.table.accessibilityIdentifier = "ayugram.cosmetics.list"
        self.banner.text = ""
        self.banner.numberOfLines = 0
        self.banner.font = UIFont.preferredFont(forTextStyle: .headline)
        self.banner.adjustsFontForContentSizeCategory = true
        self.banner.accessibilityIdentifier = "ayugram.cosmetics.header"
        self.bannerContainer.addSubview(self.banner)
        self.table.tableHeaderView = self.bannerContainer
        self.stateDisposable.set((state |> deliverOnMainQueue).start(next: { [weak self] state in
            self?.state = state
            self?.table.reloadData()
        }))
    }

    required public init(coder: NSCoder) { fatalError("init(coder:) is not supported") }
    deinit { self.stateDisposable.dispose(); self.retainedDisposables.dispose() }

    func retainDisposable(_ disposable: Disposable) { self.retainedDisposables.add(disposable) }

    public func updateAppearance(palette: CustomeOptionsPalette, navigationBarPresentationData: NavigationBarPresentationData? = nil) {
        self.palette = palette
        if let data = navigationBarPresentationData { self.navigationBar?.updatePresentationData(data, transition: .immediate) }
        if self.isNodeLoaded { self.applyPalette() }
    }

    override public func loadDisplayNode() {
        super.loadDisplayNode()
        self.displayNode.view.addSubview(self.table)
        self.applyPalette()
    }

    private func applyPalette() {
        self.displayNode.backgroundColor = self.palette.background
        self.displayNode.view.overrideUserInterfaceStyle = self.palette.dark ? .dark : .light
        self.statusBar.statusBarStyle = self.palette.dark ? .White : .Black
        self.table.backgroundColor = self.palette.background
        self.table.separatorColor = self.palette.separator
        self.table.overrideUserInterfaceStyle = self.palette.dark ? .dark : .light
        self.banner.textColor = self.palette.accent
        self.table.reloadData()
    }

    override public func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        self.layoutContents()
    }

    override public func containerLayoutUpdated(_ layout: ContainerViewLayout, transition: ContainedViewLayoutTransition) {
        super.containerLayoutUpdated(layout, transition: transition)
        self.layoutTop = self.navigationLayout(layout: layout).navigationFrame.maxY
        self.layoutBottom = layout.insets(options: .input).bottom
        self.layoutContents()
    }

    private func layoutContents() {
        guard self.isNodeLoaded else { return }
        let top = self.layoutTop ?? self.view.safeAreaInsets.top
        self.table.frame = CGRect(x: 0, y: top, width: self.view.bounds.width, height: max(0, self.view.bounds.height - top))
        self.table.contentInset.bottom = max(self.layoutBottom, self.view.safeAreaInsets.bottom)
        let height = ceil(self.banner.sizeThatFits(CGSize(width: max(1, self.table.bounds.width - 40), height: .greatestFiniteMagnitude)).height) + 36
        self.banner.frame = CGRect(x: 20, y: 18, width: max(0, self.table.bounds.width - 40), height: max(0, height - 36))
        if self.bannerContainer.frame.width != self.table.bounds.width || self.bannerContainer.frame.height != height {
            self.bannerContainer.frame = CGRect(x: 0, y: 0, width: self.table.bounds.width, height: height)
            self.table.tableHeaderView = self.bannerContainer
        }
    }

    public func numberOfSections(in tableView: UITableView) -> Int { return 4 }
    public func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        switch section { case 0: return self.choices.count; case 1, 2: return 2; default: return 1 }
    }
    public func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        return ["ACCENT COLOR", "BEHAVIOR", "APP APPEARANCE", "RESET"][section]
    }
    public func tableView(_ tableView: UITableView, titleForFooterInSection section: Int) -> String? {
        switch section {
        case 0: return self.state.editable ? nil : "Appearance settings are read-only."
        case 1: return "Local appearance only; no Premium subscription."
        case 2: return "App icons and themes apply to all accounts."
        default: return nil
        }
    }

    public func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .subtitle, reuseIdentifier: nil)
        cell.backgroundColor = self.palette.surface
        cell.textLabel?.textColor = self.palette.text
        cell.textLabel?.font = UIFont.preferredFont(forTextStyle: .body)
        cell.textLabel?.numberOfLines = 0
        cell.textLabel?.adjustsFontForContentSizeCategory = true
        cell.detailTextLabel?.textColor = self.palette.secondaryText
        cell.detailTextLabel?.numberOfLines = 0
        cell.detailTextLabel?.font = UIFont.preferredFont(forTextStyle: .caption1)
        cell.detailTextLabel?.adjustsFontForContentSizeCategory = true
        switch indexPath.section {
        case 0:
            let choice = self.choices[indexPath.row]
            cell.textLabel?.text = choice.title
            cell.accessoryType = self.state.presetId == choice.id ? .checkmark : .none
            cell.tintColor = self.palette.accent
            let rgb = choice.rgb
            let color = rgb.map { UIColor(red: CGFloat(($0 >> 16) & 255) / 255, green: CGFloat(($0 >> 8) & 255) / 255, blue: CGFloat($0 & 255) / 255, alpha: 1) } ?? self.palette.accent
            cell.imageView?.image = UIImage(systemName: "circle.fill")?.withTintColor(color, renderingMode: .alwaysOriginal)
            cell.accessibilityIdentifier = "ayugram.cosmetics.preset." + String(choice.id)
            cell.isUserInteractionEnabled = self.state.editable && !self.busy
        case 1:
            let motion = indexPath.row == 0
            let control = UISwitch()
            control.isOn = motion ? self.state.reduceMotion : self.state.allowBundledPremiumIcons
            control.isEnabled = self.state.editable && !self.busy
            control.onTintColor = self.palette.accent
            control.tag = indexPath.row
            control.accessibilityIdentifier = motion ? "ayugram.cosmetics.reduce-motion" : "ayugram.cosmetics.bundled-icons"
            control.accessibilityLabel = motion ? "Reduce dashboard motion" : "Allow bundled Premium icons locally"
            control.addTarget(self, action: #selector(self.switchChanged(_:)), for: .valueChanged)
            cell.textLabel?.text = control.accessibilityLabel
            cell.detailTextLabel?.text = nil
            cell.accessoryView = control
            cell.selectionStyle = .none
        case 2:
            cell.textLabel?.text = indexPath.row == 0 ? "Theme & app icon picker" : "Telegram animations & power saving"
            cell.accessoryType = .disclosureIndicator
            cell.accessibilityIdentifier = "ayugram.cosmetics.native." + String(indexPath.row)
        default:
            cell.textLabel?.text = "Reset local cosmetics"
            cell.textLabel?.textColor = .systemRed
            cell.isUserInteractionEnabled = self.state.editable && !self.busy
            cell.accessibilityIdentifier = "ayugram.cosmetics.reset"
        }
        return cell
    }

    public func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: !self.state.reduceMotion && !UIAccessibility.isReduceMotionEnabled)
        if indexPath.section == 2 {
            self.open(indexPath.row == 0 ? .appearance : .animationSettings)
            return
        }
        guard self.state.editable && !self.busy else { return }
        if indexPath.section == 0 {
            self.busy = true; self.table.reloadData()
            self.selectAccent(self.choices[indexPath.row].id, self.finishedMutation)
        } else if indexPath.section == 3 {
            let alert = UIAlertController(title: "Reset local cosmetics?", message: "Reset accent, motion and local icon access? Messages and accounts are kept.", preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
            alert.addAction(UIAlertAction(title: "Reset", style: .destructive, handler: { [weak self] _ in
                guard let self else { return }
                self.busy = true; self.table.reloadData()
                self.reset(self.finishedMutation)
            }))
            self.present(alert, animated: !self.state.reduceMotion && !UIAccessibility.isReduceMotionEnabled)
        }
    }

    @objc private func switchChanged(_ control: UISwitch) {
        guard self.state.editable && !self.busy && (control.tag == 0 || control.tag == 1) else { self.table.reloadData(); return }
        self.busy = true; control.isEnabled = false
        if control.tag == 0 { self.setReducedMotion(control.isOn, self.finishedMutation) }
        else { self.setBundledIcons(control.isOn, self.finishedMutation) }
    }

    private func finishedMutation(_ succeeded: Bool) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.busy = false; self.table.reloadData()
            if !succeeded { self.showFailure() }
        }
    }

    private func showFailure(remainingAttempts: Int = 12) {
        guard self.viewIfLoaded?.window != nil else { return }
        if self.presentedViewController != nil {
            guard remainingAttempts > 0 else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in self?.showFailure(remainingAttempts: remainingAttempts - 1) }
            return
        }
        let alert = UIAlertController(title: "Could not save", message: "The stored local appearance settings were kept. A future or unsupported version is read-only.", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        self.present(alert, animated: !self.state.reduceMotion && !UIAccessibility.isReduceMotionEnabled)
    }
}
