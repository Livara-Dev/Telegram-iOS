// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalArchivePresentationTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private var context: LocalArchivePresentationContext { LocalArchivePresentationContext(postbox: self.fixture.postbox) }

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "archive-presentation")
    }
    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    func testAbsentPreferencesUseDisplayDefaultsWithoutEnablingCapture() throws {
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalArchivePresentationSettings()))
        let values = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            (transaction.getPreferencesEntry(key: localArchivePresentationSettingsKey), localArchiveSettings(transaction: transaction))
        })
        XCTAssertNil(values.0)
        XCTAssertFalse(values.1.enabled)
    }

    func testRealPreferencesCodecPersistsSanitizedLabelsAcrossReopenAndReset() throws {
        XCTAssertTrue(try self.localArchiveValue(self.context.setMarkers(deleted: "  Gone\u{202e}\n  ", edited: "Earlier 👩🏽‍💻")))
        let expected = LocalArchivePresentationSettings(deletedMarker: "Gone", editedMarker: "Earlier 👩🏽‍💻")
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(expected))
        let entry = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchivePresentationSettingsKey) }))
        XCTAssertEqual(entry.get(LocalArchivePresentationSettings.self), expected)
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(expected))
        XCTAssertTrue(try self.localArchiveValue(self.context.resetMarkers()))
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalArchivePresentationSettings()))
    }

    func testLabelsStayIsolatedBetweenTwoAccountPostboxes() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "archive-presentation-other")
        defer { try? other.destroy() }
        XCTAssertTrue(try self.localArchiveValue(self.context.setMarkers(deleted: "Account one", edited: "First edit")))
        XCTAssertTrue(try self.localArchiveValue(LocalArchivePresentationContext(postbox: other.postbox).setMarkers(deleted: "Account two", edited: "Second edit")))
        try self.fixture.reopen()
        try other.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalArchivePresentationSettings(deletedMarker: "Account one", editedMarker: "First edit")))
        XCTAssertEqual(try self.localArchiveValue(LocalArchivePresentationContext(postbox: other.postbox).settings), .available(LocalArchivePresentationSettings(deletedMarker: "Account two", editedMarker: "Second edit")))
    }

    func testCharacterAndByteLimitsIncludeEmojiAndCombiningClusters() {
        XCTAssertEqual(LocalArchivePresentationSettings.normalizedMarker(String(repeating: "a", count: 32)), String(repeating: "a", count: 32))
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker(String(repeating: "a", count: 33)))
        XCTAssertEqual(LocalArchivePresentationSettings.normalizedMarker(String(repeating: "😀", count: 32))?.utf8.count, 128)
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker("a" + String(repeating: "\u{0301}", count: 64)))
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker("a" + String(repeating: "\u{0301}", count: 10000)))
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker(String(repeating: "\n", count: 10000) + "a"))
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker(" \t\u{202e}\u{2066} "))
        XCTAssertNil(LocalArchivePresentationSettings.normalizedMarker("\u{200b}\u{206f}\u{feff}"))
        XCTAssertEqual(LocalArchivePresentationSettings.normalizedMarker("a\u{0000}\t\n\u{0085}\u{061c}\u{200e}\u{200f}\u{2028}\u{2029}\u{202a}\u{202c}\u{202d}\u{202e}\u{2066}\u{2067}\u{2068}\u{2069}b"), "ab")
    }

    func testInvalidUpdateIsAtomicAndKeepsBothPreviousLabels() throws {
        XCTAssertTrue(try self.localArchiveValue(self.context.setMarkers(deleted: "Saved", edited: "Earlier")))
        let before = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchivePresentationSettingsKey)?.data })
        for invalid in ["", "   ", String(repeating: "x", count: 33), String(repeating: "😀", count: 33)] {
            XCTAssertFalse(try self.localArchiveValue(self.context.setMarkers(deleted: "Replacement", edited: invalid)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setMarkers(deleted: invalid, edited: "Replacement")))
        }
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchivePresentationSettingsKey)?.data }), before)
    }

    func testFutureMalformedOrUnsafeStoredLabelsAreReadOnlyAndBytesSurviveReopen() throws {
        struct Future: Encodable { let schemaVersion: Int32 = 9; let deletedMarker = "Future"; let editedMarker = "Future" }
        struct Malformed: Encodable { let schemaVersion: Int32 = 1; let deletedMarker = "Missing edit" }
        let entries = [try XCTUnwrap(PreferencesEntry(Future())), try XCTUnwrap(PreferencesEntry(Malformed())), try XCTUnwrap(PreferencesEntry(LocalArchivePresentationSettings(deletedMarker: "Unsafe\u{202e}", editedMarker: "Edited"))), try XCTUnwrap(PreferencesEntry(LocalArchivePresentationSettings(deletedMarker: String(repeating: "x", count: 4096))))]
        for entry in entries {
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localArchivePresentationSettingsKey, value: entry) })
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.setMarkers(deleted: "New", edited: "New")))
            XCTAssertFalse(try self.localArchiveValue(self.context.resetMarkers()))
            try self.fixture.reopen()
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localArchivePresentationSettingsKey)?.data }), entry.data)
        }
    }

    func testOpenSettingsObserverReceivesCommittedLabels() throws {
        let initial = self.expectation(description: "default labels")
        let changed = self.expectation(description: "saved labels")
        let subscription = self.context.settings.start(next: { settings in
            if settings.marker(isDeleted: true) == "Removed" { changed.fulfill() } else { initial.fulfill() }
        })
        defer { subscription.dispose() }
        self.wait(for: [initial], timeout: 10)
        XCTAssertTrue(try self.localArchiveValue(self.context.setMarkers(deleted: "Removed", edited: "Changed")))
        self.wait(for: [changed], timeout: 10)
    }

    func testPresentationUpdateDoesNotMutateCapturePolicyOrCanonicalMessages() throws {
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(8711))
        let id = MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 4)
        let before = try self.localArchiveValue(self.fixture.postbox.transaction { transaction -> Data? in
            transaction.updatePeersInternal([localArchiveTestUser(peerId)], update: { _, updated in updated })
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "current text")], location: .Random)
            setLocalArchiveSettings(transaction: transaction, settings: LocalArchiveSettings(enabled: false, excludedSenderIds: [73]))
            return transaction.getPreferencesEntry(key: localArchiveSettingsKey)?.data
        })
        XCTAssertTrue(try self.localArchiveValue(self.context.setMarkers(deleted: "Gone", edited: "Updated")))
        let after = try self.localArchiveValue(self.fixture.postbox.transaction { ($0.getPreferencesEntry(key: localArchiveSettingsKey)?.data, $0.getMessage(id)?.text) })
        XCTAssertEqual(after.0, before)
        XCTAssertEqual(after.1, "current text")
    }
}
