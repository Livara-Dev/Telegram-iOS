// SPDX-License-Identifier: GPL-2.0-or-later
import XCTest
@testable import SettingsUI

final class LocalArchiveTransferLifecycleTests: XCTestCase {
    func testBackgroundAndForegroundWhilePickerCoversPresenterRestoresAccess() {
        var state = LocalArchiveTransferVisibility(foreground: true)
        state.visible = true
        XCTAssertTrue(state.needsReadAccess)
        state.subflow = true
        state.visible = false // Full-screen system picker covers presenter.
        XCTAssertTrue(state.needsReadAccess)
        state.foreground = false // External file provider or device lock.
        XCTAssertFalse(state.needsReadAccess)
        state.foreground = true // Presenter can still have view.window == nil.
        XCTAssertTrue(state.needsReadAccess)
        state.visible = true
        state.subflow = false
        XCTAssertTrue(state.needsReadAccess)
    }

    func testUnshownOrPoppedScreenDoesNotRetainAccess() {
        var state = LocalArchiveTransferVisibility(foreground: true)
        XCTAssertFalse(state.needsReadAccess)
        state.visible = true
        XCTAssertTrue(state.needsReadAccess)
        state.visible = false
        XCTAssertFalse(state.needsReadAccess)
        state.foreground = false
        state.foreground = true
        XCTAssertFalse(state.needsReadAccess)
    }

    func testCancelledCoveredPasswordFlowReleasesAccessUntilPresenterReturns() {
        var state = LocalArchiveTransferVisibility(foreground: true)
        state.subflow = true
        XCTAssertTrue(state.needsReadAccess)
        state.foreground = false
        XCTAssertFalse(state.needsReadAccess)
        state.foreground = true
        XCTAssertTrue(state.needsReadAccess)
        state.subflow = false
        XCTAssertFalse(state.needsReadAccess)
        state.visible = true
        XCTAssertTrue(state.needsReadAccess)
    }
}
