// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import TelegramCore
@testable import SettingsUI

/// Exercises the production route and menu helpers. These tests do not launch
/// a logged-in chat or establish that the navigation UI has rendered correctly.
final class LocalClientNavigationTests: XCTestCase {
    private func peer(_ namespace: EnginePeer.Id.Namespace, _ value: Int64 = 31) -> EnginePeer.Id {
        return EnginePeer.Id(namespace: namespace, id: EnginePeer.Id.Id._internalFromInt64Value(value))
    }

    func testArchiveRoutePreservesExactCloudConversationAndQuery() throws {
        for namespace in [Namespaces.Peer.CloudUser, Namespaces.Peer.CloudGroup, Namespaces.Peer.CloudChannel] {
            let id = self.peer(namespace)
            let request = try XCTUnwrap(LocalArchiveSearchRequest(peerId: id, query: "café 👩🏽‍💻"))
            XCTAssertEqual(request.peerId, id)
            XCTAssertEqual(request.query, "café 👩🏽‍💻")
        }
    }

    func testSecretChatAndUnsupportedNamespacesCannotBecomeArchiveRoutes() {
        for namespace in [Namespaces.Peer.SecretChat, EnginePeer.Id.Namespace._internalFromInt32Value(7)] {
            let id = self.peer(namespace)
            XCTAssertFalse(localArchiveSupportsPeer(id))
            XCTAssertNil(LocalArchiveSearchRequest(peerId: id, query: "saved text"))
        }
    }

    func testEmptyQueryRemainsEmptyAndRoutesToTheDeletedList() throws {
        let request = try XCTUnwrap(LocalArchiveSearchRequest(peerId: self.peer(Namespaces.Peer.CloudUser), query: ""))
        XCTAssertEqual(request.query, "")
        XCTAssertEqual(localArchiveBoundedSearchQuery("  x  "), "  x  ")
    }

    func testArchiveQueryAcceptsTheByteLimitAndBoundsLongAsciiInput() {
        let maximum = String(repeating: "a", count: 512)
        XCTAssertEqual(localArchiveBoundedSearchQuery(maximum), maximum)
        XCTAssertEqual(localArchiveBoundedSearchQuery(maximum + String(repeating: "b", count: 10000)), maximum)
    }

    func testArchiveQueryDoesNotSplitMultibyteScalarsAtTheByteLimit() {
        let prefix = String(repeating: "a", count: 510)
        XCTAssertEqual(localArchiveBoundedSearchQuery(prefix + "éz"), prefix + "é")
        XCTAssertEqual(localArchiveBoundedSearchQuery(prefix + "😀z"), prefix)
        let emoji = localArchiveBoundedSearchQuery(String(repeating: "😀", count: 10000))
        XCTAssertEqual(emoji.utf8.count, 512)
        XCTAssertFalse(emoji.contains("\u{fffd}"))
    }

    func testSingleLongGraphemeClusterCannotBypassBoundedQueryWork() {
        let result = localArchiveBoundedSearchQuery("a" + String(repeating: "\u{0301}", count: 10000))
        XCTAssertEqual(result.utf8.count, 511)
        XCTAssertEqual(result.unicodeScalars.count, 256)
        XCTAssertFalse(result.contains("\u{fffd}"))
    }

    func testPausedMasterNeverReportsItsSelectedProtectionsAsActive() {
        XCTAssertTrue(localGhostActiveProtectionLabels(LocalGhostSettings()).isEmpty)
        let summary = localGhostMenuSummary(.available(LocalGhostSettings()), storyState: .available(LocalStoryPrivacySettings()))
        XCTAssertEqual(summary, "Ghost paused · Story receipts unchanged")
    }

    func testEnabledMasterListsEveryActualSelectedCategory() {
        let labels = localGhostActiveProtectionLabels(LocalGhostSettings(enabled: true))
        XCTAssertEqual(labels, ["ordinary read receipts", "online updates", "offline retry", "typing", "sticker selection", "recording", "uploads", "gameplay"])
        var selected = LocalGhostSettings(enabled: true, suppressTyping: false, suppressStickerSelection: false, suppressRecording: false, suppressUploads: false, suppressGamePlay: false, suppressReadReceipts: false, suppressOnlineStatus: false, automaticOffline: true)
        XCTAssertTrue(localGhostActiveProtectionLabels(selected).isEmpty)
        selected.suppressOnlineStatus = true
        XCTAssertEqual(localGhostActiveProtectionLabels(selected), ["online updates", "offline retry"])
        selected.automaticOffline = false
        XCTAssertEqual(localGhostActiveProtectionLabels(selected), ["online updates"])
    }

    func testIndependentStorySuppressionRemainsVisibleWhenMasterIsPaused() {
        let summary = localGhostMenuSummary(.available(LocalGhostSettings()), storyState: .available(LocalStoryPrivacySettings(suppressViewReceipts: true)))
        XCTAssertEqual(summary, "Ghost paused · Story receipts suppressed separately")
    }

    func testUnknownPreferencesAreExplicitInsteadOfInventingAConfiguration() {
        XCTAssertEqual(localGhostMenuSummary(.unsupported, storyState: .unsupported), "Ghost settings unavailable · Story settings unavailable")
        let none = LocalGhostSettings(enabled: true, suppressTyping: false, suppressStickerSelection: false, suppressRecording: false, suppressUploads: false, suppressGamePlay: false, suppressReadReceipts: false, suppressOnlineStatus: false, automaticOffline: false)
        XCTAssertTrue(localGhostMenuSummary(.available(none), storyState: .available(LocalStoryPrivacySettings())).contains("no master controls selected"))
    }
}
