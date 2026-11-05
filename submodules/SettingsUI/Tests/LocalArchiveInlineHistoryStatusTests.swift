// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import XCTest
import ChatMessageDateAndStatusNode

/// Real UIKit/ASDisplayNode interaction, run by the existing SettingsUI native
/// suite. This does not claim a recorded full-conversation visual inspection.
final class LocalArchiveInlineHistoryStatusTests: XCTestCase {
    private func onMain(_ body: () throws -> Void) rethrows {
        if Thread.isMainThread { try body() } else { try DispatchQueue.main.sync(execute: body) }
    }

    func testDateOnlyExposesLocalHistoryCustomActionAndClearsOnReuse() throws {
        try self.onMain {
            let node = ChatMessageDateAndStatusNode()
            var opened = 0
            node.localHistoryPressed = { opened += 1 }
            node.setLocalHistoryAccessibility(label: "Earlier 👩🏽‍💻", action: node.localHistoryPressed)
            let date = try XCTUnwrap(node.subnodes?.first(where: { $0.isAccessibilityElement }))
            XCTAssertEqual(date.accessibilityLabel, "Earlier 👩🏽‍💻. Local edit history available")
            XCTAssertEqual(date.accessibilityHint, "Opens saved versions on this device")
            XCTAssertFalse(node.isAccessibilityElement, "the parent must not hide separate reaction buttons")
            let actions = try XCTUnwrap(date.view.accessibilityCustomActions)
            XCTAssertEqual(actions.count, 1)
            let action = try XCTUnwrap(actions.first)
            XCTAssertEqual(action.actionHandler?(action), true)
            XCTAssertEqual(opened, 1)
            node.setLocalHistoryAccessibility(label: nil, action: nil)
            XCTAssertFalse(date.isAccessibilityElement)
            XCTAssertNil(date.accessibilityLabel)
            XCTAssertNil(date.accessibilityHint)
            XCTAssertNil(date.view.accessibilityCustomActions)
        }
    }

    func testClearingLocalHistoryAccessibilityDoesNotReplaceImportedMessageAction() throws {
        try self.onMain {
            let node = ChatMessageDateAndStatusNode()
            var importedTooltip = 0
            node.pressed = { importedTooltip += 1 }
            node.setLocalHistoryAccessibility(label: nil, action: nil)
            try XCTUnwrap(node.pressed)()
            XCTAssertEqual(importedTooltip, 1)
            XCTAssertTrue(node.subnodes?.allSatisfy { !$0.isAccessibilityElement } ?? false)
        }
    }

    func testLocalHistoryHitRegionDoesNotAbsorbOtherStatusAffordances() throws {
        try self.onMain {
            let node = ChatMessageDateAndStatusNode()
            node.frame = CGRect(x: 0, y: 0, width: 200, height: 30)
            let date = try XCTUnwrap(node.subnodes?.first)
            date.frame = CGRect(x: 130, y: 2, width: 70, height: 20)
            node.localHistoryPressed = {}
            XCTAssertTrue(node.hitTest(CGPoint(x: 150, y: 10), with: nil) === node.view)
            XCTAssertNil(node.hitTest(CGPoint(x: 30, y: 10), with: nil), "reply/count area keeps its existing parent action")
            node.localHistoryPressed = nil
            XCTAssertNil(node.hitTest(CGPoint(x: 150, y: 10), with: nil))
        }
    }
}
