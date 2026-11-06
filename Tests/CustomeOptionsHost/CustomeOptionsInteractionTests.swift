// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest

/// Real UIKit touches against the exact production renderer in a synthetic
/// callback host. This does not authenticate or verify logged-in Settings.
final class CustomeOptionsInteractionTests: XCTestCase {
    private let prefix = "ayugram.custom-options."

    @MainActor
    private func record(_ stage: String, app: XCUIApplication) {
        if stage != "reset-prompt" {
            self.assertAppearance(dark: !stage.hasSuffix("-light"), stage: stage, app: app)
        }
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "ayu-" + stage
        attachment.lifetime = .keepAlways
        self.add(attachment)
        let rect = app.windows.firstMatch.frame
        let optionsTable = app.tables[self.prefix + "list"].firstMatch
        let tableElement = optionsTable.exists ? optionsTable : app.tables["ayugram.cosmetics.list"].firstMatch
        let table = tableElement.exists ? tableElement.frame : .zero
        let evidence: [String: Any] = ["stage": stage,
            "window": [rect.minX, rect.minY, rect.width, rect.height],
            "table": [table.minX, table.minY, table.width, table.height]]
        if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
           let text = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_FRAME:" + text) }
        // Keep a still interval in the native variable-rate recording so each
        // settled UI state can be located without resampling the animation.
        Thread.sleep(forTimeInterval: 0.6)
    }

    @MainActor
    private func search(_ text: String, app: XCUIApplication) {
        let table = app.tables[self.prefix + "list"]
        for _ in 0..<8 where !app.searchFields.firstMatch.isHittable { table.swipeDown() }
        let field = app.searchFields.firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10))
        field.tap()
        if !String(describing: field.value ?? "").isEmpty {
            let clear = field.buttons["Clear text"]
            if clear.exists { clear.tap() }
        }
        if !text.isEmpty { field.typeText(text) }
        let keyboard = app.keyboards.firstMatch
        let isHidden = NSPredicate(format: "exists == false")
        if !text.isEmpty {
            let done = app.keyboards.buttons["Search"]
            if done.exists && done.isEnabled && done.isHittable { done.tap() }
            // Search dismisses asynchronously. Wait for the hidden state before
            // continuing; the disappearing keyboard has no stable frame to read.
            let hidden = XCTNSPredicateExpectation(predicate: isHidden, object: keyboard)
            XCTAssertEqual(XCTWaiter.wait(for: [hidden], timeout: 5), .completed)
            return
        }
        // Empty-search assertion-contract repair: hierarchy membership does
        // not prove visibility. Preserve the real interactive-dismissal drag
        // and five-second deadline; invalid geometry must never prove hidden.
        let window = app.windows.firstMatch
        func validFrame(_ rect: CGRect) -> Bool {
            return [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }
                && rect.size.width > 0 && rect.size.height > 0
        }
        func keyboardState() -> (visible: Bool, hidden: Bool, evidence: [String: Any]) {
            let windowFrame = window.frame
            let windowValid = validFrame(windowFrame)
            let exists = keyboard.exists
            let keyboardFrame = exists ? keyboard.frame : .zero
            let keyboardValid = exists && validFrame(keyboardFrame)
            let hittable = exists && keyboard.isHittable
            let intersectsWindow = windowValid && keyboardValid && !keyboardFrame.intersection(windowFrame).isEmpty
            let hidden = windowValid && (!exists || (keyboardValid && !hittable && !intersectsWindow))
            let evidence: [String: Any] = ["exists": exists, "hittable": hittable,
                "window_valid": windowValid, "keyboard_valid": keyboardValid,
                "intersects_window": intersectsWindow, "hidden": hidden,
                "window": windowValid ? [windowFrame.minX, windowFrame.minY, windowFrame.width, windowFrame.height] : [],
                "keyboard": keyboardValid ? [keyboardFrame.minX, keyboardFrame.minY, keyboardFrame.width, keyboardFrame.height] : []]
            return (exists && windowValid && keyboardValid && intersectsWindow, hidden, evidence)
        }
        func recordKeyboard(_ phase: String, state: (visible: Bool, hidden: Bool, evidence: [String: Any])) {
            var evidence = state.evidence
            evidence["phase"] = phase
            if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
               let value = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_KEYBOARD:" + value) }
        }
        let initial = keyboardState()
        recordKeyboard("before", state: initial)
        XCTAssertTrue(initial.visible, "Keyboard must initially intersect the valid application window")
        guard initial.visible else { return }
        let start = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
        let end = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
        start.press(forDuration: 0.1, thenDragTo: end)
        let hiddenState = NSPredicate { _, _ in keyboardState().hidden }
        let hidden = XCTNSPredicateExpectation(predicate: hiddenState, object: keyboard)
        let outcome = XCTWaiter.wait(for: [hidden], timeout: 5)
        recordKeyboard("after", state: keyboardState())
        XCTAssertEqual(outcome, .completed)
    }

    @MainActor
    private func assertAppearance(dark: Bool, stage: String, app: XCUIApplication) {
        let button = app.toolbars.buttons["fixture.appearance"]
        XCTAssertTrue(button.waitForExistence(timeout: 5))
        XCTAssertTrue(button.isHittable)
        let frame = button.frame
        XCTAssertGreaterThan(frame.width, 20)
        XCTAssertGreaterThan(frame.height, 20)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(frame))
        let expected = ["window_dark": dark, "table_found": true, "table_dark": dark,
                        "background_sampled": true, "background_dark": dark]
        guard let data = try? JSONSerialization.data(withJSONObject: expected, options: [.sortedKeys]),
              let value = String(data: data, encoding: .utf8) else { XCTFail("Invalid closed appearance expectation"); return }
        let sampled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", value), object: button)
        XCTAssertEqual(XCTWaiter.wait(for: [sampled], timeout: 5), .completed)
        if let observed = button.value as? String, let data = observed.data(using: .utf8),
           let actual = try? JSONSerialization.jsonObject(with: data) as? [String: Bool] {
            XCTAssertEqual(actual, expected)
            var evidence: [String: Any] = actual; evidence["stage"] = stage
            if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
               let text = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_APPEARANCE:" + text) }
        } else { XCTFail("Rendered table appearance was not observed") }
    }

    @MainActor
    private func select(_ category: String, app: XCUIApplication) {
        let row = app.tables.cells[self.prefix + "category." + category]
        if !row.isHittable {
            for _ in 0..<6 { app.tables[self.prefix + "list"].swipeDown() }
        }
        for _ in 0..<12 where !row.isHittable { app.tables[self.prefix + "list"].swipeUp() }
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        XCTAssertTrue(row.isHittable)
        row.tap()
    }

    @MainActor
    private func back(_ app: XCUIApplication) {
        // The standard UIKit back button may use a shortened accessibility
        // title. This synthetic host has no other navigation-bar buttons.
        let button = app.navigationBars.buttons.firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 5))
        button.tap()
    }

    @MainActor
    func testLightDarkSearchNavigationSwitchAndActualResetConfirmation() {
        self.continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        XCTAssertTrue(app.tables[self.prefix + "list"].waitForExistence(timeout: 30))
        self.record("root-light", app: app)
        let archive = app.switches[self.prefix + "toggle.archive"]
        let toggleWindow = app.windows.firstMatch
        let togglePoint = archive.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        func validToggleFrame(_ rect: CGRect) -> Bool {
            return [rect.origin.x, rect.origin.y, rect.width, rect.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }
                && rect.width > 0 && rect.height > 0
        }
        func recordArchiveToggle(_ phase: String) -> (safe: Bool, off: Bool, ledgerOn: Bool) {
            let exists = archive.exists
            let windowExists = toggleWindow.exists
            let windowFrame = windowExists ? toggleWindow.frame : .zero
            let controlFrame = exists ? archive.frame : .zero
            let windowValid = windowExists && validToggleFrame(windowFrame)
            let controlValid = exists && validToggleFrame(controlFrame)
            let enabled = exists && archive.isEnabled
            let hittable = exists && archive.isHittable
            let contained = windowValid && controlValid && windowFrame.contains(controlFrame)
            let center = contained ? togglePoint.screenPoint : .zero
            let centerValid = contained && center.x.isFinite && center.y.isFinite
                && controlFrame.contains(center) && windowFrame.contains(center)
            let value = exists ? archive.value as? String : nil
            let ledger = app.staticTexts["fixture.ledger"].firstMatch
            let ledgerValue = ledger.exists ? ledger.value as? String : nil
            let ledgerOff = ledgerValue?.hasPrefix("No Telegram account; archive off;") == true
            let ledgerOn = ledgerValue?.hasPrefix("No Telegram account; archive on;") == true
            let evidence: [String: Any] = ["phase": phase, "exists": exists,
                "enabled": enabled, "hittable": hittable, "window_valid": windowValid,
                "control_valid": controlValid, "contained": contained, "center_valid": centerValid,
                "value_known": value == "0" || value == "1", "is_on": value == "1",
                "ledger_known": ledgerOff || ledgerOn, "ledger_on": ledgerOn,
                "window": windowValid ? [windowFrame.minX, windowFrame.minY, windowFrame.width, windowFrame.height] : [],
                "control": controlValid ? [controlFrame.minX, controlFrame.minY, controlFrame.width, controlFrame.height] : [],
                "center": centerValid ? [center.x, center.y] : []]
            if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
               let text = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_ARCHIVE_TOGGLE:" + text) }
            return (contained && centerValid && enabled && hittable, value == "0" && ledgerOff, ledgerOn)
        }
        let initialToggle = recordArchiveToggle("before")
        XCTAssertTrue(initialToggle.safe, "Archive switch must be hittable inside a valid window")
        XCTAssertEqual(archive.value as? String, "0")
        XCTAssertTrue(initialToggle.off, "Synthetic callback ledger must initially report archive off")
        XCTAssertTrue(archive.isEnabled)
        XCTAssertTrue(archive.isHittable)
        guard initialToggle.safe && initialToggle.off else { return }
        togglePoint.tap()
        let expectedToggle = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "1"), object: archive)
        let toggleOutcome = XCTWaiter.wait(for: [expectedToggle], timeout: 5)
        let finalToggle = recordArchiveToggle("after")
        XCTAssertEqual(toggleOutcome, .completed)
        XCTAssertEqual(archive.value as? String, "1")
        XCTAssertTrue(finalToggle.ledgerOn, "Synthetic callback ledger must report archive on")

        self.search("history edited", app: app)
        XCTAssertFalse(app.switches[self.prefix + "toggle.archive"].exists)
        self.record("search-light", app: app)
        self.select("deletedMessages", app: app)
        self.record("category-light", app: app)
        self.back(app)
        self.search("", app: app)
        let appearance = app.toolbars.buttons["fixture.appearance"]
        XCTAssertTrue(appearance.waitForExistence(timeout: 5))
        XCTAssertTrue(appearance.isHittable)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(appearance.frame))
        appearance.tap()
        self.record("root-dark", app: app)
        self.search("history edited", app: app)
        self.record("search-dark", app: app)
        self.select("deletedMessages", app: app)
        self.record("category-dark", app: app)
        self.back(app)
        self.search("", app: app)
        self.select("advanced", app: app)
        XCTAssertTrue(app.alerts["Reset active features?"].waitForExistence(timeout: 5))
        self.record("reset-prompt", app: app)
        app.alerts.buttons["Cancel"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 0")
        self.record("reset-cancelled", app: app)
        self.select("advanced", app: app)
        app.alerts.buttons["Turn features off"].tap()
        XCTAssertTrue(app.alerts["Features switched off"].waitForExistence(timeout: 5))
        app.alerts.buttons["OK"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 1")
        for _ in 0..<10 where !archive.isHittable { app.tables[self.prefix + "list"].swipeDown() }
        XCTAssertTrue(archive.isHittable)
        XCTAssertEqual(app.switches[self.prefix + "toggle.archive"].value as? String, "0")
        self.record("reset-confirmed", app: app)

        self.select("localPremium", app: app)
        app.tables.cells[self.prefix + "category.localPremium.0"].tap()
        XCTAssertTrue(app.tables["ayugram.cosmetics.list"].waitForExistence(timeout: 5))
        self.record("cosmetics-before", app: app)
        app.tables.cells["ayugram.cosmetics.preset.1"].tap()
        let motion = app.switches["ayugram.cosmetics.reduce-motion"]
        for _ in 0..<8 where !motion.isHittable { app.tables["ayugram.cosmetics.list"].swipeUp() }
        motion.tap()
        XCTAssertEqual(motion.value as? String, "1")
        let icons = app.switches["ayugram.cosmetics.bundled-icons"]
        for _ in 0..<8 where !icons.isHittable { app.tables["ayugram.cosmetics.list"].swipeUp() }
        icons.tap()
        XCTAssertEqual(icons.value as? String, "1")
        self.record("cosmetics-changed", app: app)
        let reset = app.tables.cells["ayugram.cosmetics.reset"]
        for _ in 0..<10 where !reset.isHittable { app.tables["ayugram.cosmetics.list"].swipeUp() }
        reset.tap()
        XCTAssertTrue(app.alerts["Reset local cosmetics?"].waitForExistence(timeout: 5))
        app.alerts.buttons["Cancel"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 1")
        reset.tap()
        app.alerts.buttons["Reset"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 1 · cosmetics 1")
        for _ in 0..<8 where !motion.isHittable { app.tables["ayugram.cosmetics.list"].swipeDown() }
        XCTAssertEqual(motion.value as? String, "0")
        for _ in 0..<8 where !icons.isHittable { app.tables["ayugram.cosmetics.list"].swipeUp() }
        XCTAssertEqual(icons.value as? String, "0")
        for _ in 0..<6 { app.tables["ayugram.cosmetics.list"].swipeDown() }
        self.record("cosmetics-reset", app: app)
        app.terminate()
    }
}
