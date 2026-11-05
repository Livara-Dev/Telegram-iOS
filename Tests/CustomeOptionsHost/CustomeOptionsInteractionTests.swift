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
        let done = app.keyboards.buttons["Search"]
        if done.exists && done.isEnabled && done.isHittable { done.tap() }
        // Empty searches disable the keyboard's Search action. Exercise the
        // production table's interactive keyboard dismissal instead of tapping
        // an offscreen toolbar through the keyboard.
        for _ in 0..<3 where app.keyboards.firstMatch.exists {
            let keyboard = app.keyboards.firstMatch.frame
            XCTAssertGreaterThan(keyboard.height, 100)
            XCTAssertGreaterThan(keyboard.minY - table.frame.minY, 100)
            let start = table.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0))
                .withOffset(CGVector(dx: 0, dy: keyboard.minY - table.frame.minY - 60))
            let end = app.windows.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
            start.press(forDuration: 0.1, thenDragTo: end)
        }
        let hidden = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: app.keyboards.firstMatch)
        XCTAssertEqual(XCTWaiter.wait(for: [hidden], timeout: 5), .completed)
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
        XCTAssertTrue(archive.isEnabled)
        archive.tap()
        XCTAssertEqual(archive.value as? String, "1")

        self.search("view once", app: app)
        XCTAssertFalse(app.switches[self.prefix + "toggle.archive"].exists)
        self.record("search-light", app: app)
        self.select("timedMedia", app: app)
        self.record("category-light", app: app)
        self.back(app)
        self.search("", app: app)
        let appearance = app.toolbars.buttons["fixture.appearance"]
        XCTAssertTrue(appearance.waitForExistence(timeout: 5))
        XCTAssertTrue(appearance.isHittable)
        XCTAssertTrue(app.windows.firstMatch.frame.contains(appearance.frame))
        appearance.tap()
        self.record("root-dark", app: app)
        self.search("view once", app: app)
        self.record("search-dark", app: app)
        self.select("timedMedia", app: app)
        self.record("category-dark", app: app)
        self.back(app)
        self.search("", app: app)
        self.select("advanced", app: app)
        app.tables.cells[self.prefix + "category.advanced.0"].tap()
        XCTAssertTrue(app.alerts["Reset active features?"].waitForExistence(timeout: 5))
        self.record("reset-prompt", app: app)
        app.alerts.buttons["Cancel"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 0")
        self.record("reset-cancelled", app: app)
        app.tables.cells[self.prefix + "category.advanced.0"].tap()
        app.alerts.buttons["Turn features off"].tap()
        XCTAssertTrue(app.alerts["Features switched off"].waitForExistence(timeout: 5))
        app.alerts.buttons["OK"].tap()
        XCTAssertEqual(app.staticTexts["fixture.ledger"].firstMatch.label, "Synthetic · resets 1")
        self.back(app)
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
