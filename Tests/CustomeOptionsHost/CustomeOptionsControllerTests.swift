// SPDX-License-Identifier: GPL-2.0-or-later
import UIKit
import XCTest
import CustomeOptionsRenderer
import CustomeOptionsFixture

final class CustomeOptionsControllerTests: XCTestCase {
    @MainActor
    private func installed(available: Bool = true) throws -> CustomeOptionsFixture {
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let fixture = CustomeOptionsFixture(available: available)
        fixture.install(in: scene)
        self.drain()
        return fixture
    }

    @MainActor
    private func drain() { RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.12)) }

    @MainActor
    private func view<T: UIView>(_ type: T.Type, id: String, in root: UIView) -> T? {
        if let result = root as? T, result.accessibilityIdentifier == id { return result }
        for child in root.subviews {
            if let result = self.view(type, id: id, in: child) { return result }
        }
        return nil
    }

    @MainActor
    func testActualRendererAndInitialSwitchStateAreInstalled() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        let controller = try XCTUnwrap(fixture.navigation.topViewController as? CustomeOptionsController)
        let table = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.custom-options.list", in: controller.view))
        XCTAssertEqual(table.numberOfSections, 2)
        XCTAssertEqual(table.numberOfRows(inSection: 0), 3)
        XCTAssertEqual(table.numberOfRows(inSection: 1), 15)
        let control = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: controller.view))
        XCTAssertTrue(control.isEnabled)
        XCTAssertFalse(control.isOn)
        XCTAssertTrue(fixture.toggleCalls.isEmpty)
    }

    @MainActor
    func testActualSwitchSendActionsPersistsThroughSignalAndSerializesPendingChange() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        fixture.deferPersistence = true
        let root = try XCTUnwrap(fixture.navigation.topViewController)
        let control = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: root.view))
        control.setOn(true, animated: false)
        control.sendActions(for: .valueChanged)
        XCTAssertEqual(fixture.toggleCalls, [.archive])
        XCTAssertFalse(control.isEnabled)
        XCTAssertFalse(fixture.state.archiveEnabled)
        control.sendActions(for: .valueChanged)
        XCTAssertEqual(fixture.toggleCalls, [.archive])
        fixture.finishPersistence(); self.drain()
        let saved = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: root.view))
        XCTAssertTrue(fixture.state.archiveEnabled)
        XCTAssertTrue(saved.isOn)
        XCTAssertTrue(saved.isEnabled)
    }

    @MainActor
    func testUnavailableSwitchCannotCallPersistence() throws {
        let fixture = try self.installed(available: false); defer { fixture.close() }
        let root = try XCTUnwrap(fixture.navigation.topViewController)
        let control = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: root.view))
        XCTAssertFalse(control.isEnabled)
        control.setOn(true, animated: false)
        control.sendActions(for: .valueChanged)
        self.drain()
        XCTAssertTrue(fixture.toggleCalls.isEmpty)
        XCTAssertFalse(fixture.state.archiveEnabled)
    }

    @MainActor
    func testFailedPersistenceRevertsFromStoredStateAndPresentsActualAlert() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        fixture.persistSucceeds = false
        let root = try XCTUnwrap(fixture.navigation.topViewController)
        let control = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: root.view))
        control.setOn(true, animated: false)
        control.sendActions(for: .valueChanged)
        self.drain()
        XCTAssertEqual(fixture.toggleCalls, [.archive])
        XCTAssertFalse(fixture.state.archiveEnabled)
        let reverted = try XCTUnwrap(self.view(UISwitch.self, id: "ayugram.custom-options.toggle.archive", in: root.view))
        XCTAssertFalse(reverted.isOn)
        XCTAssertEqual(root.presentedViewController?.title, "Could not save")
    }

    @MainActor
    func testActualSearchDelegateFiltersRowsAndRealCategoryNavigationOpensRenderer() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        let root = try XCTUnwrap(fixture.navigation.topViewController as? CustomeOptionsController)
        let table = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.custom-options.list", in: root.view))
        let search = try XCTUnwrap(self.view(UISearchBar.self, id: "ayugram.custom-options.search", in: root.view))
        search.delegate?.searchBar?(search, textDidChange: "view once")
        self.drain()
        XCTAssertEqual(table.numberOfSections, 1)
        XCTAssertEqual(table.numberOfRows(inSection: 0), 1)
        XCTAssertEqual(table.cellForRow(at: IndexPath(row: 0, section: 0))?.textLabel?.text, "Timed Media")
        table.delegate?.tableView?(table, didSelectRowAt: IndexPath(row: 0, section: 0))
        self.drain()
        XCTAssertEqual(fixture.actions, [.timedMediaInformation], "Single-destination categories open directly")
        XCTAssertEqual(fixture.navigation.viewControllers.count, 1)
        XCTAssertNil(table.cellForRow(at: IndexPath(row: 0, section: 0))?.detailTextLabel?.text)
        search.delegate?.searchBar?(search, textDidChange: "history edited")
        self.drain()
        table.delegate?.tableView?(table, didSelectRowAt: IndexPath(row: 0, section: 0))
        self.drain()
        XCTAssertEqual(fixture.actions.last, .category(.deletedMessages))
        XCTAssertEqual(fixture.navigation.viewControllers.count, 2)
        let category = try XCTUnwrap(fixture.navigation.topViewController as? CustomeOptionsController)
        XCTAssertEqual(category.title, "Message History")
        let categoryTable = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.custom-options.list", in: category.view))
        XCTAssertNil(categoryTable.dataSource?.tableView?(categoryTable, titleForFooterInSection: 0))
        categoryTable.delegate?.tableView?(categoryTable, didSelectRowAt: IndexPath(row: 0, section: 0))
        XCTAssertEqual(fixture.actions.last, .archiveSettings)
    }

    @MainActor
    func testActualHeaderAndTableFramesFitLightAndDarkWindow() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        let root = try XCTUnwrap(fixture.navigation.topViewController as? CustomeOptionsController)
        let appearance = try XCTUnwrap(root.toolbarItems?.last?.customView as? UIButton)
        for dark in [false, true] {
            if fixture.dark != dark { appearance.sendActions(for: .touchUpInside) }
            root.view.setNeedsLayout(); root.view.layoutIfNeeded(); self.drain()
            let table = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.custom-options.list", in: root.view))
            let header = try XCTUnwrap(self.view(UIView.self, id: "ayugram.custom-options.header", in: root.view))
            XCTAssertGreaterThan(table.bounds.width, 300)
            XCTAssertGreaterThanOrEqual(header.bounds.height, 112)
            XCTAssertLessThanOrEqual(header.bounds.height, 132)
            let frame = header.convert(header.bounds, to: root.view)
            XCTAssertGreaterThanOrEqual(frame.minX, 0)
            XCTAssertLessThanOrEqual(frame.maxX, root.view.bounds.width + 0.5)
            XCTAssertTrue(frame.origin.x.isFinite && frame.origin.y.isFinite)
            XCTAssertEqual(table.overrideUserInterfaceStyle, dark ? .dark : .light)
            XCTAssertEqual(fixture.appearanceObservation(), ["window_dark": dark, "table_found": true,
                "table_dark": dark, "background_sampled": true, "background_dark": dark])
            let sampleData = try XCTUnwrap(appearance.accessibilityValue?.data(using: .utf8))
            XCTAssertEqual(try JSONDecoder().decode([String: Bool].self, from: sampleData), fixture.appearanceObservation())
            // A requested dark flag cannot conceal an actually light table.
            if dark {
                table.backgroundColor = .white
                XCTAssertFalse(fixture.appearanceObservation()["background_dark"] ?? true)
                table.overrideUserInterfaceStyle = .light
                // UIKit propagates resolved traits during the next update
                // cycle. Observe that real transition before testing the
                // negative control; the requested override alone is not proof.
                table.setNeedsLayout()
                let traitDeadline = Date(timeIntervalSinceNow: 3)
                while table.traitCollection.userInterfaceStyle != .light && Date() < traitDeadline {
                    root.view.layoutIfNeeded()
                    RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.02))
                }
                XCTAssertFalse(fixture.appearanceObservation()["table_dark"] ?? true)
                root.updateAppearance(palette: .standard(dark: true))
            }
        }
    }

    @MainActor
    func testActualCosmeticPresetRowPersistsCallbackAndUpdatesSelection() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        let controller = fixture.cosmeticsController()
        fixture.navigation.pushViewController(controller, animated: false); self.drain()
        let table = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.cosmetics.list", in: controller.view))
        XCTAssertEqual(table.numberOfRows(inSection: 0), 2)
        table.delegate?.tableView?(table, didSelectRowAt: IndexPath(row: 1, section: 0))
        self.drain()
        XCTAssertEqual(fixture.accentCalls, [1])
        XCTAssertEqual(fixture.cosmeticsState.presetId, 1)
        let row = table.dataSource?.tableView(table, cellForRowAt: IndexPath(row: 1, section: 0))
        XCTAssertEqual(row?.accessoryType, .checkmark)
        XCTAssertTrue(fixture.toggleCalls.isEmpty)
    }

    @MainActor
    func testActualCosmeticSwitchesPersistSignalWithoutChangingArchivePreferences() throws {
        let fixture = try self.installed(); defer { fixture.close() }
        let controller = fixture.cosmeticsController()
        fixture.navigation.pushViewController(controller, animated: false); self.drain()
        let table = try XCTUnwrap(self.view(UITableView.self, id: "ayugram.cosmetics.list", in: controller.view))
        for (row, identifier) in [(0, "ayugram.cosmetics.reduce-motion"), (1, "ayugram.cosmetics.bundled-icons")] {
            table.scrollToRow(at: IndexPath(row: row, section: 1), at: .middle, animated: false); self.drain()
            let control = try XCTUnwrap(self.view(UISwitch.self, id: identifier, in: controller.view))
            XCTAssertTrue(control.isEnabled)
            control.setOn(true, animated: false); control.sendActions(for: .valueChanged); self.drain()
        }
        XCTAssertEqual(fixture.motionCalls, [true])
        XCTAssertEqual(fixture.bundledIconCalls, [true])
        XCTAssertTrue(fixture.cosmeticsState.reduceMotion)
        XCTAssertTrue(fixture.cosmeticsState.allowBundledPremiumIcons)
        XCTAssertFalse(fixture.state.archiveEnabled)
        XCTAssertTrue(fixture.toggleCalls.isEmpty)
    }
}
