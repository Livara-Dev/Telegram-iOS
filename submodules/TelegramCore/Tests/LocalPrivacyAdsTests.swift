// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalPrivacyAdsTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "local-privacy-ads")
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private var context: LocalPrivacyAdsContext { LocalPrivacyAdsContext(postbox: self.fixture.postbox) }

    func testDefaultAndPersistedLocalPolicySurvivesReopenAndReset() throws {
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalPrivacyAdsSettings()))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(true)))
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalPrivacyAdsSettings(hideSponsoredMessages: true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(false)))
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalPrivacyAdsSettings()))
    }

    func testTwoAccountsDoNotShareSponsoredMessagePolicy() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "local-privacy-ads-other")
        var otherContext: LocalPrivacyAdsContext! = LocalPrivacyAdsContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(true)))
        XCTAssertEqual(try self.localArchiveValue(otherContext.settings), .available(LocalPrivacyAdsSettings()))
        XCTAssertTrue(try self.localArchiveValue(otherContext.setHideSponsoredMessages(false)))
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .available(LocalPrivacyAdsSettings(hideSponsoredMessages: true)))
    }

    func testConsumerProjectionHidesAndRestoresWithoutDeletingStoredMessages() throws {
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7301))
        let id = MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 1)
        let message = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { transaction -> Message? in
            transaction.updatePeersInternal([localArchiveTestUser(peerId)], update: { _, updated in updated })
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "unchanged canonical content")], location: .Random)
            return transaction.getMessage(id)
        }))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(true)))
        let hiddenPolicy = try self.localArchiveValue(self.context.settings)
        XCTAssertTrue(locallyVisibleSponsoredMessages([message], policy: hiddenPolicy).isEmpty)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id)?.text }), message.text)
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(false)))
        let visiblePolicy = try self.localArchiveValue(self.context.settings)
        XCTAssertEqual(locallyVisibleSponsoredMessages([message], policy: visiblePolicy).map(\.id), [id])
        XCTAssertEqual(message.text, "unchanged canonical content")
    }

    func testFutureOrMalformedPolicyIsReadOnlyAndDoesNotSilentlyHideContent() throws {
        struct Future: Encodable { let schemaVersion: Int32 = 99; let hideSponsoredMessages = true }
        struct Malformed: Encodable { let schemaVersion: Int32 = 1 }
        for entry in [try XCTUnwrap(PreferencesEntry(Future())), try XCTUnwrap(PreferencesEntry(Malformed()))] {
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                transaction.setPreferencesEntry(key: localPrivacyAdsSettingsKey, value: entry)
            })
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.setHideSponsoredMessages(false)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setHideSponsoredMessages(true)))
            try self.fixture.reopen()
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localPrivacyAdsSettingsKey)?.data }), entry.data)
            XCTAssertEqual(locallyVisibleSponsoredMessages([1, 2], policy: .unsupported), [1, 2])
        }
    }

    func testOpenConsumerReceivesCommittedPolicyChange() throws {
        let initial = self.expectation(description: "initial policy")
        let changed = self.expectation(description: "committed policy")
        let subscription = self.context.settings.start(next: { state in
            if state.hidesSponsoredMessages { changed.fulfill() } else { initial.fulfill() }
        })
        defer { subscription.dispose() }
        self.wait(for: [initial], timeout: 10)
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideSponsoredMessages(true)))
        self.wait(for: [changed], timeout: 10)
    }
}
