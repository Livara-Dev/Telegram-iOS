// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalStoryOpeningWarningTests: XCTestCase {
    func testNewAccountRequiresConfirmationBeforeContentIsAllowed() {
        let gate = LocalStoryOpeningGate()
        XCTAssertFalse(gate.allowsContent)
        gate.resolve(localStoryOpeningWarningSettings(entry: nil))
        XCTAssertEqual(gate.state, .confirmationRequired)
        XCTAssertFalse(gate.allowsContent)
        XCTAssertTrue(gate.approve())
        XCTAssertTrue(gate.allowsContent)
        XCTAssertFalse(gate.approve())
    }

    func testCancellationWinsOverLateEnablePrivacyCompletion() {
        let gate = LocalStoryOpeningGate()
        gate.resolve(.available(LocalStoryOpeningWarningSettings()))
        XCTAssertTrue(gate.beginEnablingPrivacy())
        XCTAssertFalse(gate.allowsContent)
        gate.cancel()
        XCTAssertFalse(gate.finishEnablingPrivacy(true))
        XCTAssertFalse(gate.allowsContent)
        XCTAssertEqual(gate.state, .cancelled)
    }

    func testFailureToEnablePrivacyRequiresAnotherExplicitDecision() {
        let gate = LocalStoryOpeningGate()
        gate.resolve(.available(LocalStoryOpeningWarningSettings()))
        XCTAssertTrue(gate.beginEnablingPrivacy())
        XCTAssertTrue(gate.finishEnablingPrivacy(false))
        XCTAssertFalse(gate.allowsContent)
        XCTAssertEqual(gate.state, .confirmationRequired)
    }

    func testUnknownPolicyRequiresConfirmationAndCannotBeResolvedAgain() {
        let gate = LocalStoryOpeningGate()
        gate.resolve(.unsupported)
        gate.resolve(.available(LocalStoryOpeningWarningSettings(enabled: false)))
        XCTAssertFalse(gate.allowsContent)
        XCTAssertEqual(gate.state, .confirmationRequired)
    }

    func testExplicitlyDisabledWarningAllowsViewerWithoutDialog() {
        let gate = LocalStoryOpeningGate()
        gate.resolve(.available(LocalStoryOpeningWarningSettings(enabled: false)))
        XCTAssertTrue(gate.allowsContent)
    }

    func testApprovalIsSpecificToOneViewerInstance() {
        let first = LocalStoryOpeningGate()
        let second = LocalStoryOpeningGate()
        first.resolve(.available(LocalStoryOpeningWarningSettings()))
        second.resolve(.available(LocalStoryOpeningWarningSettings()))
        XCTAssertTrue(first.approve())
        XCTAssertTrue(first.allowsContent)
        XCTAssertFalse(second.allowsContent)
        second.cancel()
        XCTAssertTrue(first.allowsContent)
    }

    func testCancellationBeforePolicyLoadCannotBeOverriddenByDisabledWarning() {
        let gate = LocalStoryOpeningGate()
        gate.cancel()
        gate.resolve(.available(LocalStoryOpeningWarningSettings(enabled: false)))
        XCTAssertEqual(gate.state, .cancelled)
        XCTAssertFalse(gate.allowsContent)
    }

    func testWarningPreferenceIsIndependentOfGhostAndPersistsAcrossReopen() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "story-opening-warning")
        defer { try? fixture.destroy() }
        XCTAssertTrue(try self.localArchiveValue(updateLocalStoryOpeningWarning(postbox: fixture.postbox, enabled: false)))
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: fixture.postbox).setEnabled(true)))
        try fixture.reopen()
        let state = try self.localArchiveValue(fixture.postbox.transaction { localStoryOpeningWarningSettings(transaction: $0) })
        XCTAssertEqual(state, .available(LocalStoryOpeningWarningSettings(enabled: false)))
    }

    func testWarningPreferenceIsAccountIsolated() throws {
        let first = try LocalArchivePostboxFixture(test: self, name: "story-warning-first")
        let second = try LocalArchivePostboxFixture(test: self, name: "story-warning-second")
        defer { try? first.destroy(); try? second.destroy() }
        XCTAssertTrue(try self.localArchiveValue(updateLocalStoryOpeningWarning(postbox: first.postbox, enabled: false)))
        XCTAssertEqual(try self.localArchiveValue(first.postbox.transaction { localStoryOpeningWarningSettings(transaction: $0) }), .available(LocalStoryOpeningWarningSettings(enabled: false)))
        XCTAssertEqual(try self.localArchiveValue(second.postbox.transaction { localStoryOpeningWarningSettings(transaction: $0) }), .available(LocalStoryOpeningWarningSettings()))
    }

    func testFutureWarningBytesRemainReadOnly() throws {
        struct FutureWarning: Encodable { let schemaVersion: Int32 = 99; let enabled = false }
        let fixture = try LocalArchivePostboxFixture(test: self, name: "story-warning-future")
        defer { try? fixture.destroy() }
        let entry = try XCTUnwrap(PreferencesEntry(FutureWarning()))
        let _ = try self.localArchiveValue(fixture.postbox.transaction { $0.setPreferencesEntry(key: localStoryOpeningWarningKey, value: entry) })
        XCTAssertFalse(try self.localArchiveValue(updateLocalStoryOpeningWarning(postbox: fixture.postbox, enabled: false)))
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getPreferencesEntry(key: localStoryOpeningWarningKey)?.data }), entry.data)
        XCTAssertEqual(localStoryOpeningWarningSettings(entry: entry), .unsupported)
    }
}
