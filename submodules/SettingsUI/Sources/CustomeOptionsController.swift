// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import Display
import SwiftSignalKit

public enum CustomeOptionsMode: Equatable {
    case dashboard
    case category(CustomeOptionsCategory)
}

public struct CustomeOptionsPalette {
    public let background: UIColor
    public let surface: UIColor
    public let text: UIColor
    public let secondaryText: UIColor
    public let separator: UIColor
    public let accent: UIColor
    public let dark: Bool

    public init(background: UIColor, surface: UIColor, text: UIColor,
                secondaryText: UIColor, separator: UIColor, accent: UIColor, dark: Bool) {
        self.background = background; self.surface = surface; self.text = text
        self.secondaryText = secondaryText; self.separator = separator
        self.accent = accent; self.dark = dark
    }

    /// Also used by the native renderer smoke host, without account state or
    /// Telegram's app-bundle-dependent localized strings.
    public static func standard(dark: Bool) -> CustomeOptionsPalette {
        return CustomeOptionsPalette(
            background: dark ? UIColor(white: 0.055, alpha: 1.0) : UIColor(white: 0.95, alpha: 1.0),
            surface: dark ? UIColor(white: 0.12, alpha: 1.0) : .white,
            text: dark ? .white : UIColor(white: 0.08, alpha: 1.0),
            secondaryText: dark ? UIColor(white: 0.68, alpha: 1.0) : UIColor(white: 0.40, alpha: 1.0),
            separator: dark ? UIColor(white: 0.23, alpha: 1.0) : UIColor(white: 0.87, alpha: 1.0),
            accent: UIColor(red: 0.39, green: 0.31, blue: 0.91, alpha: 1.0), dark: dark)
    }
}

private final class CustomeOptionsHeader: UIView {
    let search = UISearchBar()
    private let showsSearch: Bool

    init(mode: CustomeOptionsMode) {
        self.showsSearch = mode == .dashboard
        super.init(frame: .zero)
        self.search.searchBarStyle = .minimal
        self.search.placeholder = "Search settings"
        self.search.accessibilityIdentifier = "ayugram.custom-options.search"
        self.accessibilityIdentifier = "ayugram.custom-options.header"
        if self.showsSearch { self.addSubview(self.search) }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    func update(palette: CustomeOptionsPalette) {
        self.search.overrideUserInterfaceStyle = palette.dark ? .dark : .light
        self.search.tintColor = palette.accent
        self.search.searchTextField.textColor = palette.text
    }

    func height(for width: CGFloat) -> CGFloat {
        return self.showsSearch ? 60.0 : 8.0
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        self.search.frame = CGRect(x: 8.0, y: 4.0, width: max(0, self.bounds.width - 16.0), height: 52.0)
    }
}

/// Actual production dashboard renderer. The account binding lives separately
/// so the same UIKit views can be exercised in a fresh native smoke host.
public final class CustomeOptionsController: ViewController, UITableViewDataSource, UITableViewDelegate, UISearchBarDelegate {
    private let mode: CustomeOptionsMode
    private var palette: CustomeOptionsPalette
    private let table = UITableView(frame: .zero, style: .insetGrouped)
    private let header: CustomeOptionsHeader
    private let stateDisposable = MetaDisposable()
    private let additionalDisposables = DisposableSet()
    private var state = CustomeOptionsState()
    private var query = ""
    private var busy: Set<CustomeOptionsToggle> = []
    private var resetting = false
    private var displayTop: CGFloat?
    private var displayBottom: CGFloat = 0
    private let openAction: (CustomeOptionsAction) -> Void
    private let setToggle: (CustomeOptionsToggle, Bool, @escaping (Bool) -> Void) -> Void
    private let reset: (@escaping (Bool) -> Void) -> Void
    private var sizeObserver: NSObjectProtocol?

    public init(mode: CustomeOptionsMode = .dashboard, palette: CustomeOptionsPalette,
                navigationBarPresentationData: NavigationBarPresentationData? = nil,
                state: Signal<CustomeOptionsState, NoError>,
                open: @escaping (CustomeOptionsAction) -> Void,
                setToggle: @escaping (CustomeOptionsToggle, Bool, @escaping (Bool) -> Void) -> Void,
                reset: @escaping (@escaping (Bool) -> Void) -> Void) {
        self.mode = mode; self.palette = palette
        self.header = CustomeOptionsHeader(mode: mode)
        self.openAction = open; self.setToggle = setToggle; self.reset = reset
        super.init(navigationBarPresentationData: navigationBarPresentationData)
        switch mode {
        case .dashboard: self.title = CustomeOptionsCatalog.title
        case let .category(category): self.title = category.title
        }
        self.table.dataSource = self
        self.table.delegate = self
        self.table.rowHeight = UITableView.automaticDimension
        self.table.estimatedRowHeight = 52.0
        self.table.keyboardDismissMode = .interactive
        self.table.contentInsetAdjustmentBehavior = .never
        self.table.accessibilityIdentifier = "ayugram.custom-options.list"
        self.header.search.delegate = self
        self.table.tableHeaderView = self.header
        self.stateDisposable.set((state |> deliverOnMainQueue).start(next: { [weak self] state in
            self?.state = state
            self?.table.reloadData()
        }))
        self.sizeObserver = NotificationCenter.default.addObserver(forName: UIContentSizeCategory.didChangeNotification, object: nil, queue: .main) { [weak self] _ in
            self?.table.reloadData()
            self?.view.setNeedsLayout()
        }
    }

    required public init(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    deinit {
        self.stateDisposable.dispose()
        self.additionalDisposables.dispose()
        if let observer = self.sizeObserver { NotificationCenter.default.removeObserver(observer) }
    }

    func retainDisposable(_ disposable: Disposable) { self.additionalDisposables.add(disposable) }

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
        self.table.indicatorStyle = self.palette.dark ? .white : .black
        self.table.overrideUserInterfaceStyle = self.palette.dark ? .dark : .light
        self.header.update(palette: self.palette)
        self.table.reloadData()
    }

    override public func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        self.layoutTable()
    }

    override public func containerLayoutUpdated(_ layout: ContainerViewLayout, transition: ContainedViewLayoutTransition) {
        super.containerLayoutUpdated(layout, transition: transition)
        self.displayTop = self.navigationLayout(layout: layout).navigationFrame.maxY
        self.displayBottom = layout.insets(options: .input).bottom
        self.layoutTable()
    }

    private func layoutTable() {
        guard self.isNodeLoaded else { return }
        let top = self.displayTop ?? self.view.safeAreaInsets.top
        self.table.frame = CGRect(x: 0, y: top, width: self.view.bounds.width, height: max(0, self.view.bounds.height - top))
        self.table.contentInset.bottom = max(self.displayBottom, self.view.safeAreaInsets.bottom)
        self.table.scrollIndicatorInsets = self.table.contentInset
        let height = self.header.height(for: self.table.bounds.width)
        if self.header.frame.width != self.table.bounds.width || abs(self.header.frame.height - height) > 0.5 {
            self.header.frame = CGRect(x: 0, y: 0, width: self.table.bounds.width, height: height)
            self.table.tableHeaderView = self.header
        }
    }

    public func setSearchQuery(_ query: String) {
        self.query = CustomeOptionsCatalog.boundedQuery(query)
        self.header.search.text = self.query
        self.table.reloadData()
    }

    public func searchBar(_ searchBar: UISearchBar, textDidChange searchText: String) { self.setSearchQuery(searchText) }
    public func searchBarSearchButtonClicked(_ searchBar: UISearchBar) { searchBar.resignFirstResponder() }

    private var categories: [CustomeOptionsCategory] { CustomeOptionsCatalog.categories(matching: self.query) }
    private var showsQuickControls: Bool { self.mode == .dashboard && self.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    public func numberOfSections(in tableView: UITableView) -> Int {
        switch self.mode {
        case .dashboard: return self.showsQuickControls ? 2 : 1
        case .category: return 1
        }
    }

    public func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        switch self.mode {
        case .dashboard:
            return self.showsQuickControls && section == 0 ? CustomeOptionsToggle.allCases.count : self.categories.count
        case let .category(category): return CustomeOptionsCatalog.links(for: category).count
        }
    }

    public func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        switch self.mode {
        case .dashboard: return self.showsQuickControls && section == 0 ? "Quick toggles" : "Settings"
        case .category: return nil
        }
    }

    public func tableView(_ tableView: UITableView, titleForFooterInSection section: Int) -> String? {
        return self.mode == .dashboard && self.categories.isEmpty ? "No matching settings" : nil
    }

    public func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        // Distinct reuse identifiers keep switches out of navigation cells.
        let isToggle = self.showsQuickControls && indexPath.section == 0
        let identifier = isToggle ? "custom-toggle" : "custom-link"
        let cell = tableView.dequeueReusableCell(withIdentifier: identifier) ?? UITableViewCell(style: .subtitle, reuseIdentifier: identifier)
        cell.backgroundColor = self.palette.surface
        cell.textLabel?.textColor = self.palette.text
        cell.textLabel?.font = UIFont.preferredFont(forTextStyle: .body)
        cell.textLabel?.numberOfLines = 2
        cell.textLabel?.adjustsFontForContentSizeCategory = true
        cell.detailTextLabel?.textColor = self.palette.secondaryText
        cell.detailTextLabel?.font = UIFont.preferredFont(forTextStyle: .caption1)
        cell.detailTextLabel?.numberOfLines = 1
        cell.detailTextLabel?.adjustsFontForContentSizeCategory = true
        cell.selectionStyle = isToggle ? .none : .default
        if isToggle {
            let toggle = CustomeOptionsToggle.allCases[indexPath.row]
            cell.textLabel?.text = toggle.title
            cell.detailTextLabel?.text = self.state.isAvailable(toggle) ? nil : "Unavailable"
            cell.imageView?.image = UIImage(systemName: toggle.symbol)?.withTintColor(self.palette.accent, renderingMode: .alwaysOriginal)
            let control = UISwitch()
            control.tag = indexPath.row
            control.onTintColor = self.palette.accent
            control.isOn = self.state.value(for: toggle)
            control.isEnabled = self.state.isAvailable(toggle) && !self.busy.contains(toggle) && !self.resetting
            control.accessibilityLabel = toggle.title
            control.accessibilityHint = self.state.detail(for: toggle)
            control.accessibilityIdentifier = "ayugram.custom-options.toggle." + toggle.rawValue
            control.addTarget(self, action: #selector(self.toggleChanged(_:)), for: .valueChanged)
            cell.accessoryView = control
            cell.accessoryType = .none
            cell.accessibilityIdentifier = "ayugram.custom-options.control." + toggle.rawValue
        } else {
            let title: String
            let detail: String
            let symbol: String
            let rowId: String
            switch self.mode {
            case .dashboard:
                let category = self.categories[indexPath.row]
                title = category.title; detail = category.detail; symbol = category.symbol; rowId = category.rawValue
            case let .category(category):
                let link = CustomeOptionsCatalog.links(for: category)[indexPath.row]
                title = link.title; detail = link.detail; symbol = link.symbol; rowId = category.rawValue + "." + String(indexPath.row)
            }
            cell.textLabel?.text = title
            cell.detailTextLabel?.text = nil
            cell.accessibilityHint = detail
            cell.imageView?.image = UIImage(systemName: symbol)?.withTintColor(self.palette.accent, renderingMode: .alwaysOriginal)
            cell.accessoryView = nil
            cell.accessoryType = .disclosureIndicator
            cell.accessibilityIdentifier = "ayugram.custom-options.category." + rowId
        }
        return cell
    }

    public func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        if self.showsQuickControls && indexPath.section == 0 { return }
        self.header.search.resignFirstResponder()
        switch self.mode {
        case .dashboard:
            let category = self.categories[indexPath.row]
            let links = CustomeOptionsCatalog.links(for: category)
            if links.count == 1, let action = links.first?.action {
                if action == .resetActiveFeatures { self.confirmReset() } else { self.openAction(action) }
            } else {
                self.openAction(.category(category))
            }
        case let .category(category):
            let action = CustomeOptionsCatalog.links(for: category)[indexPath.row].action
            if action == .resetActiveFeatures { self.confirmReset() } else { self.openAction(action) }
        }
    }

    @objc private func toggleChanged(_ control: UISwitch) {
        guard CustomeOptionsToggle.allCases.indices.contains(control.tag) else { return }
        let toggle = CustomeOptionsToggle.allCases[control.tag]
        guard self.state.isAvailable(toggle), !self.busy.contains(toggle), !self.resetting else {
            control.setOn(self.state.value(for: toggle), animated: true)
            return
        }
        self.busy.insert(toggle)
        control.isEnabled = false
        self.setToggle(toggle, control.isOn, { [weak self] saved in
            DispatchQueue.main.async {
                guard let self else { return }
                self.busy.remove(toggle)
                self.table.reloadData()
                if !saved { self.showMessage("Could not save", "Your settings were kept. Unlock the device and try again.") }
            }
        })
    }

    private func confirmReset() {
        guard !self.resetting else { return }
        let alert = UIAlertController(title: "Reset active features?", message: "Turn off AyuGram features for this account? Saved messages, rules and login sessions will be kept.", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        alert.addAction(UIAlertAction(title: "Turn features off", style: .destructive, handler: { [weak self] _ in
            guard let self else { return }
            self.resetting = true
            self.table.reloadData()
            self.reset({ [weak self] succeeded in
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.resetting = false
                    self.table.reloadData()
                    self.showMessage(succeeded ? "Features switched off" : "Reset incomplete", succeeded ? "Features are off. Saved messages and rules were kept." : "Some settings could not be changed. Your saved data was kept.")
                }
            })
        }))
        self.present(alert, animated: true)
    }

    private func showMessage(_ title: String, _ message: String, remainingPresentationAttempts: Int = 12) {
        // UIAlertAction callbacks run before the confirmation sheet has fully
        // dismissed. Do not silently lose a failed save/reset in that interval.
        guard self.viewIfLoaded?.window != nil else { return }
        if self.presentedViewController != nil {
            guard remainingPresentationAttempts > 0 else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
                self?.showMessage(title, message, remainingPresentationAttempts: remainingPresentationAttempts - 1)
            }
            return
        }
        let alert = UIAlertController(title: title, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        self.present(alert, animated: true)
    }
}
