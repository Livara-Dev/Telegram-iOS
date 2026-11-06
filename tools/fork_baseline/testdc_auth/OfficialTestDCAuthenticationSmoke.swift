// SPDX-License-Identifier: GPL-2.0-or-later
// Private source-only draft, 2026-11-06. Not compiled, launched or authenticated.
import Foundation
import XCTest

/// Official public TestDC authentication only. Existing public sandbox accounts
/// are allowed; this does not establish freshness or exclusive ownership.
/// All native stdout/xcresult remains private. Export ONLY closed stage markers.
final class OfficialTestDCAuthenticationSmoke: XCTestCase {
    private enum Stage: String {
        case disabled, welcomeObserved, reservedInputVerified, phoneSubmitted
        case publicCodeSubmitted, authorizedRootInstalled
        case rejectedBeforeSubmission, signupRequired, passwordRequired
        case blockedOrUnexpected, timedOut
    }
    private enum Rejected: Error { case localCheck }
    private enum Outcome { case authorized, codeEntry, signup, password, timedOut }

    private func mark(_ stage: Stage) {
        print("AYU_TESTDC_AUTH_STAGE:" + stage.rawValue)
    }
    private func fail(_ stage: Stage) {
        mark(stage)
        // Constant failure only: do not include input, UI content or errors.
        XCTFail("Official public TestDC authentication did not reach the authorized root.")
    }
    private func require(_ value: Bool) throws {
        if !value { throw Rejected.localCheck }
    }
    private func digits(_ value: Any?, placeholder: String? = nil) throws -> String {
        guard let text = value as? String else { throw Rejected.localCheck }
        if text == placeholder { return "" }
        try require(text.utf8.count <= 40)
        try require(text.unicodeScalars.allSatisfy { "0123456789+ ()-".unicodeScalars.contains($0) })
        return String(text.filter { "0123456789".contains($0) })
    }

    @MainActor
    private func waitOutcome(_ app: XCUIApplication, allowCodeEntry: Bool, seconds: TimeInterval) -> Outcome {
        let deadline = Date().addingTimeInterval(seconds)
        repeat {
            // Query only this constant top-level identifier first. Never read
            // chat labels, descendants' text, account details or UI tree dumps.
            if app.windows["AyuLaunch.conversationsVisible"].exists { return .authorized }
            if app.textFields["Auth.SetName.FirstNameField"].exists { return .signup }
            if app.secureTextFields.firstMatch.exists { return .password }
            if allowCodeEntry && app.textFields["Auth.CodeEntry.CodeField"].exists { return .codeEntry }
            if app.state != .runningForeground { return .timedOut }
            RunLoop.current.run(until: Date().addingTimeInterval(0.2))
        } while Date() < deadline
        return .timedOut
    }

    @MainActor
    func testOfficialPublicTestDCAuthenticationOnly() throws {
        self.continueAfterFailure = false
        // This explicit draft gate is not proof of the wrapper's artifact,
        // disposable-simulator, TestDC, contact-denial or evidence prerequisites.
        guard ProcessInfo.processInfo.environment["AYU_PRIVATE_TESTDC_AUTH_SMOKE"] == "PUBLIC_TESTDC_AUTH_ONLY" else {
            mark(.disabled)
            throw XCTSkip("Private TestDC authentication draft is disabled.")
        }
        // One predetermined reserved identity, no retries or suffix cycling.
        // This public account may already contain public sandbox data.
        let selector = "1"
        let suffix = "0041"
        let national = "66" + selector + suffix
        let expected = "999" + national
        let publicCode = String(repeating: selector, count: 5)
        let app = XCUIApplication(bundleIdentifier: "org.livara.ayugram")
        app.launchArguments = ["--ui-test", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        defer { app.terminate() }
        var submitted = false
        do {
            try require(app.state == .runningForeground)
            let welcome = app.buttons["Auth.Welcome.StartButton"]
            try require(welcome.waitForExistence(timeout: 30) && welcome.isHittable)
            try require(!app.windows["AyuLaunch.conversationsVisible"].exists)
            mark(.welcomeObserved)
            welcome.tap()
            let country = app.textFields["Auth.PhoneEntry.CountryCodeField"]
            let number = app.textFields["Auth.PhoneEntry.PhoneNumberField"]
            let next = app.buttons["Auth.PhoneEntry.ContinueButton"]
            try require(country.waitForExistence(timeout: 10) && number.exists && next.exists)
            try require(try digits(number.value, placeholder: "Your phone number").isEmpty)
            try require(try digits(country.value) == "1")
            country.tap()
            for _ in 0..<2 { country.typeText(XCUIKeyboardKey.delete.rawValue) }
            for digit in "999" { country.typeText(String(digit)) }
            func combinedDigits() throws -> String {
                try self.digits(country.value) + self.digits(number.value, placeholder: "Your phone number")
            }
            try require(try combinedDigits() == "999")
            var prefix = "999"
            for digit in national {
                number.tap()
                number.typeText(String(digit))
                prefix.append(digit)
                try require(try combinedDigits() == prefix)
            }
            try require(expected.utf8.count == 10 && expected.hasPrefix("99966"))
            try require(try combinedDigits() == expected)
            try require(next.isEnabled && next.isHittable)
            mark(.reservedInputVerified)
            next.tap()
            // iPhone 15 Pro width selects the stock confirmation overlay.
            let confirm = app.buttons["Auth.PhoneConfirm.ContinueButton"]
            try require(confirm.waitForExistence(timeout: 10) && confirm.isHittable)
            // Treat tapping confirmation as the only send-code submission.
            submitted = true
            confirm.tap()
            mark(.phoneSubmitted)
            switch waitOutcome(app, allowCodeEntry: true, seconds: 45) {
            case .authorized:
                // The stock send-code flow also supports sentCodeSuccess.
                mark(.authorizedRootInstalled)
                return
            case .signup: fail(.signupRequired); return
            case .password: fail(.passwordRequired); return
            case .timedOut: fail(.timedOut); return
            case .codeEntry: break
            }
            let code = app.textFields["Auth.CodeEntry.CodeField"]
            try require(code.exists)
            // Source: viewDidAppear activates CodeInputView's real text field.
            // It is 1x1 at alpha .01, so do NOT tap it or assume hittability.
            // Type only while the numeric keyboard and code route are present.
            try require(app.keyboards.firstMatch.waitForExistence(timeout: 10))
            try require(!number.isHittable && !country.isHittable)
            try require(try digits(code.value).isEmpty)
            try require(publicCode.utf8.count == 5)
            app.typeText(publicCode)
            // Native codeChanged submits when server-declared length is reached.
            // No manual Next, resend, OTP access or fallback authentication.
            mark(.publicCodeSubmitted)
            switch waitOutcome(app, allowCodeEntry: false, seconds: 60) {
            case .authorized: mark(.authorizedRootInstalled)
            case .signup: fail(.signupRequired)
            case .password: fail(.passwordRequired)
            case .timedOut: fail(.timedOut)
            case .codeEntry: fail(.blockedOrUnexpected)
            }
        } catch {
            // Never format the error or UI values. A blocked attempted run must
            // fail XCTest even if an external stage parser is accidentally omitted.
            fail(submitted ? .blockedOrUnexpected : .rejectedBeforeSubmission)
        }
        // Wrapper success REQUIRES authorizedRootInstalled + exact XCTest pass.
        // Disabled means SKIP; every attempted non-authorized terminal means FAIL.
        // Only an observed authorized root may complete this test successfully.
    }
}
