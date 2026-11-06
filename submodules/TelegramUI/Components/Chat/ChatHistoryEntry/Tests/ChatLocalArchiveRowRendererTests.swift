// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import XCTest
import TelegramCore
import TelegramPresentationData

/// Compiles the exact production ChatLocalArchiveRowView.swift through a source
/// filegroup. This is real UIKit rendering, not a replacement demo renderer.
/// It does not prove ListView/CoreList placement or a logged-in chat session.
final class ChatLocalArchiveRowRendererTests: XCTestCase {
    private func onMain(_ f: () -> Void) {
        if Thread.isMainThread { f() } else { DispatchQueue.main.sync(execute: f) }
    }

    private func row(text: String = "Saved private body", media: Int = 1, unavailable: Int = 1,
                     more: Bool = false) -> LocalArchiveDeletedHistoryItem {
        return LocalArchiveDeletedHistoryItem(peerId: .init(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7111)),
            messageId: 4, sequence: 2, eventKey: "renderer-fixture", sentAt: 800, deletedAt: 900, marker: "Custom Deleted",
            storedMediaCount: media, unavailableMediaCount: unavailable, moreAvailable: more,
            content: LocalArchiveDeletedHistoryContent(text: text), mediaKinds: ["voice", "video"], storedMediaSlots: media > 0 ? [0] : [])
    }

    func testActualRendererExposesLocalMarkerTimestampAndMediaAvailabilityWithoutServerActions() {
        self.onMain {
            let row = self.row()
            let view = ChatLocalArchiveRowView(frame: CGRect(x: 0, y: 0, width: 361, height: ChatLocalArchiveRowView.height(row: row, width: 361)))
            var opens = 0
            view.update(row: row, theme: defaultPresentationData().theme, openHistory: { opens += 1 })
            view.layoutIfNeeded()
            XCTAssertFalse(view.isHidden)
            XCTAssertTrue(view.accessibilityLabel?.contains("Custom Deleted") == true)
            XCTAssertTrue(view.accessibilityLabel?.contains("Saved private body") == true)
            XCTAssertFalse(view.accessibilityLabel?.contains("Observed deletion:") == true)
            XCTAssertTrue(view.accessibilityLabel?.contains("Voice message, saved") == true)
            XCTAssertTrue(view.accessibilityLabel?.contains("Video, not saved") == true)
            XCTAssertEqual(view.gestureRecognizers?.count, 1, "the container has one local-only navigation gesture")
            XCTAssertTrue(view.accessibilityActivate()); XCTAssertEqual(opens, 1)
            row.content.revoke()
            XCTAssertTrue(view.isHidden); XCTAssertNil(view.accessibilityLabel)
            XCTAssertTrue(view.subviews.compactMap { ($0 as? UILabel)?.text }.isEmpty)
            XCTAssertFalse(view.accessibilityActivate()); XCTAssertEqual(opens, 1)
        }
    }

    func testReusedRendererCannotRestoreRevokedPreviousBody() {
        self.onMain {
            let old = self.row(text: "old account body"), current = self.row(text: "current account body")
            let view = ChatLocalArchiveRowView(frame: CGRect(x: 0, y: 0, width: 320, height: 220))
            view.update(row: old, theme: defaultPresentationData().theme, openHistory: {})
            view.update(row: current, theme: defaultPresentationData().theme, openHistory: {})
            old.content.revoke()
            XCTAssertFalse(view.isHidden)
            XCTAssertTrue(view.accessibilityLabel?.contains("current account body") == true)
            current.content.revoke()
            view.update(row: old, theme: defaultPresentationData().theme, openHistory: { XCTFail("revoked row must not open") })
            XCTAssertTrue(view.isHidden); XCTAssertNil(view.accessibilityLabel)
        }
    }

    func testLongAndMediaOnlyRowsHaveBoundedNonoverlappingLayoutOnPhoneWidths() {
        self.onMain {
            for width in [CGFloat(288), 361, 420] {
                for row in [self.row(text: "", more: true), self.row(text: String(repeating: "word العربية 👩‍💻 ", count: 300))] {
                    let height = ChatLocalArchiveRowView.height(row: row, width: width)
                    XCTAssertGreaterThan(height, 60); XCTAssertLessThan(height, 300)
                    let view = ChatLocalArchiveRowView(frame: CGRect(x: 0, y: 0, width: width, height: height))
                    view.update(row: row, theme: defaultPresentationData().theme, openHistory: {})
                    view.layoutIfNeeded()
                    let visible = view.subviews.compactMap { $0 as? UILabel }.filter { $0.text?.isEmpty == false }
                    for label in visible {
                        XCTAssertGreaterThanOrEqual(label.frame.minX, 0)
                        XCTAssertLessThanOrEqual(label.frame.maxX, width)
                        XCTAssertLessThanOrEqual(label.frame.maxY, height + 0.5)
                    }
                    for index in 1..<visible.count { XCTAssertGreaterThanOrEqual(visible[index].frame.minY, visible[index - 1].frame.maxY) }
                    row.content.revoke()
                }
            }
        }
    }

    func testRendererUsesRealLightAndDarkThemeColorsAndAlwaysClearsBoth() {
        self.onMain {
            let light = makeDefaultPresentationTheme(reference: .day, serviceBackgroundColor: nil)
            let dark = makeDefaultPresentationTheme(reference: .night, serviceBackgroundColor: nil)
            let row = self.row(text: "مرحبا 👩‍💻 שלום saved body")
            let view = ChatLocalArchiveRowView(frame: CGRect(x: 0, y: 0, width: 361, height: 240))
            view.update(row: row, theme: light, openHistory: {})
            view.layoutIfNeeded()
            XCTAssertEqual(view.backgroundColor, light.list.itemBlocksBackgroundColor)
            let lightTextColors = view.subviews.compactMap { ($0 as? UILabel)?.textColor }
            view.update(row: row, theme: dark, openHistory: {})
            view.layoutIfNeeded()
            XCTAssertEqual(view.backgroundColor, dark.list.itemBlocksBackgroundColor)
            XCTAssertNotEqual(view.backgroundColor, light.list.itemBlocksBackgroundColor)
            let darkTextColors = view.subviews.compactMap { ($0 as? UILabel)?.textColor }
            XCTAssertNotEqual(lightTextColors, darkTextColors)
            row.content.revoke()
            XCTAssertTrue(view.isHidden); XCTAssertNil(view.accessibilityLabel)
        }
    }
    func testSavedAttachmentButtonOpensItsActualSlotAndRevocationRemovesActions() {
        self.onMain {
            let row = self.row()
            let view = ChatLocalArchiveRowView(frame: CGRect(x: 0, y: 0, width: 320, height: 250))
            var slots: [Int] = []
            view.update(row: row, theme: defaultPresentationData().theme, openHistory: {}, openMedia: { slots.append($0) })
            let buttons = view.subviews.compactMap { $0 as? UIButton }
            XCTAssertEqual(buttons.count, 2)
            XCTAssertTrue(buttons[0].isEnabled)
            XCTAssertFalse(buttons[1].isEnabled)
            buttons[0].sendActions(for: .touchUpInside)
            XCTAssertEqual(slots, [0])
            row.content.revoke()
            buttons[0].sendActions(for: .touchUpInside)
            XCTAssertEqual(slots, [0])
            XCTAssertNil(view.accessibilityCustomActions)
        }
    }

}
