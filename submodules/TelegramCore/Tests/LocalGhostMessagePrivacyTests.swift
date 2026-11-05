// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

/// Real persisted account preferences and actual consumption producer, with
/// only the final transport factory replaced. No server results are fabricated.
final class LocalGhostMessagePrivacyTests: XCTestCase {
    private let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "ghost-message-privacy")
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func configure(_ settings: LocalGhostSettings, postbox: Postbox? = nil) throws {
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: postbox ?? self.fixture.postbox).updateSettings { $0 = settings }))
    }

    private func completedValues<T>(_ signal: Signal<T, NoError>) -> [T] {
        let values = Atomic<[T]>(value: [])
        let done = expectation(description: "privacy operation completes")
        let disposable = signal.start(next: { value in
            let _ = values.modify { $0 + [value] }
        }, completed: { done.fulfill() })
        defer { disposable.dispose() }
        wait(for: [done], timeout: 15)
        return values.with { $0 }
    }

    private func history(_ postbox: Postbox? = nil, peerId: PeerId? = nil, calls: Atomic<Int>) -> [Int] {
        return self.completedValues(localGhostReadReceipt(postbox: postbox ?? self.fixture.postbox, peerId: peerId ?? self.peerId, request: {
            let _ = calls.modify { $0 + 1 }
            return Signal<Int, NoError>.single(7)
        }))
    }

    func testDisabledGhostPreservesActualSenderAndResult() throws {
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: calls), [7])
        XCTAssertEqual(calls.with { $0 }, 1)
    }

    func testEnabledReadSuppressionNeverConstructsRequestOrInventsAResult() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: calls), [])
        XCTAssertEqual(calls.with { $0 }, 0)
    }

    func testReadChoiceIsIndependentFromActivityChoices() throws {
        try self.configure(LocalGhostSettings(enabled: true, suppressReadReceipts: false))
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: calls), [7])
        XCTAssertEqual(calls.with { $0 }, 1)
    }

    func testSecretHistoryReceiptRemainsAvailableForExpiryProtocol() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let secret = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(123))
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(peerId: secret, calls: calls), [7])
        XCTAssertEqual(calls.with { $0 }, 1)
    }

    func testFuturePolicyBytesRemainReadOnlyAndOrdinaryReadsFailClosed() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(LocalGhostSettings())) as? [String: Any])
        object["schemaVersion"] = 99
        let future = try JSONDecoder().decode(LocalGhostSettings.self, from: JSONSerialization.data(withJSONObject: object))
        let entry = try XCTUnwrap(PreferencesEntry(future))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localGhostSettingsKey, value: entry) })
        XCTAssertFalse(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: calls), [])
        XCTAssertEqual(calls.with { $0 }, 0)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostSettingsKey)?.data }), entry.data)
    }

    func testReadPolicyPersistsAcrossRealPostboxReopen() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        try self.fixture.reopen()
        let calls = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: calls), [])
        XCTAssertEqual(calls.with { $0 }, 0)
    }

    func testTwoAccountsCannotShareReadPolicy() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-message-other-account")
        defer { try? other.destroy() }
        try self.configure(LocalGhostSettings(enabled: true))
        let first = Atomic<Int>(value: 0), second = Atomic<Int>(value: 0)
        XCTAssertEqual(self.history(calls: first), [])
        XCTAssertEqual(self.history(other.postbox, calls: second), [7])
        XCTAssertEqual(first.with { $0 }, 0)
        XCTAssertEqual(second.with { $0 }, 1)
    }

    func testSettingsAreReadWhenTheReceiptIsSubscribedNotWhenItIsCreated() throws {
        let calls = Atomic<Int>(value: 0)
        let pending: Signal<Int, NoError> = localGhostReadReceipt(postbox: self.fixture.postbox, peerId: self.peerId, request: {
            let _ = calls.modify { $0 + 1 }
            return .single(7)
        })
        try self.configure(LocalGhostSettings(enabled: true))
        XCTAssertEqual(self.completedValues(pending), [])
        XCTAssertEqual(calls.with { $0 }, 0)
    }

    private func insertContent(_ values: [(Int32, [MessageAttribute])], media: [Media] = []) throws -> [MessageId] {
        let ids = values.map { MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: $0.0) }
        let messages = zip(ids, values).map { id, value in localArchiveTestMessage(id, attributes: value.1, media: media) }
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([localArchiveTestUser(self.peerId)], update: { _, value in value })
            let _ = transaction.addMessages(messages, location: .Random)
        })
        return ids
    }

    func testMixedBatchSuppressesOnlyKnownOrdinaryContentAndKeepsMandatoryExpiryIds() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let ids = try self.insertContent([
            (1, [ConsumableContentMessageAttribute(consumed: false)]),
            (2, [ConsumableContentMessageAttribute(consumed: false), AutoclearTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)]),
            (3, [ConsumableContentMessageAttribute(consumed: false), AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil)]),
            (4, [AutoremoveTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)])
        ])
        let missing = MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: 99)
        let selected = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            localGhostContentReadIds(transaction: transaction, messageIds: ids + [missing])
        })
        XCTAssertEqual(selected, Array(ids.dropFirst()) + [missing])
    }

    func testSuppressedMentionPageReturnsOnlyAnEndCursorSoRestartLoopTerminates() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let ids = try self.insertContent([(1, [ConsumableContentMessageAttribute(consumed: false)])])
        let calls = Atomic<Int>(value: 0)
        let values: [Int32?] = self.completedValues(localGhostContentReadReceiptPage(postbox: self.fixture.postbox, messageIds: ids, request: { _ -> Signal<Int32?, NoError> in
            let _ = calls.modify { $0 + 1 }
            return .single(50)
        }))
        XCTAssertEqual(values, [nil])
        XCTAssertEqual(calls.with { $0 }, 0)
    }

    private func cachedPhoto() -> TelegramMediaImage {
        let bytes = localArchiveTestPhotoData()
        let resource = CloudPhotoSizeMediaResource(datacenterId: 1, photoId: 9501, accessHash: 0,
            sizeSpec: "x", size: Int64(bytes.count), fileReference: nil)
        self.fixture.postbox.mediaBox.storeResourceData(resource.id, data: bytes)
        return TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: 9501),
            representations: [TelegramMediaImageRepresentation(dimensions: PixelDimensions(width: 16, height: 16),
                resource: resource, progressiveSizes: [], immediateThumbnailData: nil)],
            immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
    }

    func testGhostDoesNotStopCanonicalTimedPhotoConsumptionCountdown() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let ids = try self.insertContent([(1, [ConsumableContentMessageAttribute(consumed: false), AutoclearTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)])], media: [self.cachedPhoto()])
        let _ = try self.localArchiveValue(_internal_markMessageContentAsConsumedInteractively(postbox: self.fixture.postbox, messageId: ids[0]))
        let message = try XCTUnwrap(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(ids[0]) }))
        XCTAssertEqual(message.attributes.compactMap { $0 as? ConsumableContentMessageAttribute }.first?.consumed, true)
        XCTAssertGreaterThan(try XCTUnwrap(message.autoclearAttribute?.countdownBeginTime), 0)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localGhostContentReadIds(transaction: $0, messageIds: ids) }), ids)
        try self.fixture.reopen()
        let reopened = try XCTUnwrap(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(ids[0]) }))
        XCTAssertEqual(reopened.autoclearAttribute?.countdownBeginTime, message.autoclearAttribute?.countdownBeginTime)
    }

    func testGhostDoesNotStopCanonicalViewOnceAndAutoremoveConsumption() throws {
        try self.configure(LocalGhostSettings(enabled: true))
        let ids = try self.insertContent([
            (1, [ConsumableContentMessageAttribute(consumed: false), AutoclearTimeoutMessageAttribute(timeout: viewOnceTimeout, countdownBeginTime: nil)]),
            (2, [ConsumableContentMessageAttribute(consumed: false), AutoremoveTimeoutMessageAttribute(timeout: 20, countdownBeginTime: nil)])
        ], media: [self.cachedPhoto()])
        for id in ids {
            let _ = try self.localArchiveValue(_internal_markMessageContentAsConsumedInteractively(postbox: self.fixture.postbox, messageId: id))
            let message = try XCTUnwrap(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(id) }))
            XCTAssertGreaterThan(try XCTUnwrap(message.autoclearAttribute?.countdownBeginTime ?? message.autoremoveAttribute?.countdownBeginTime), 0)
        }
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { localGhostContentReadIds(transaction: $0, messageIds: ids) }), ids)
    }

    func testLegacyActivityPolicyMigratesWithoutSilentlySuppressingReadsOrPresence() throws {
        struct Legacy: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let suppressTyping = true
            let suppressStickerSelection = false
            let suppressRecording = true
            let suppressUploads = false
            let suppressGamePlay = true
        }
        let entry = try XCTUnwrap(PreferencesEntry(Legacy()))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localGhostSettingsKey, value: entry) })
        let state = try self.localArchiveValue(self.fixture.postbox.transaction { localGhostSettings(transaction: $0) })
        guard case let .available(value) = state else { return XCTFail("legacy policy lost") }
        XCTAssertEqual(value.schemaVersion, 2)
        XCTAssertTrue(value.enabled)
        XCTAssertFalse(value.suppressStickerSelection)
        XCTAssertFalse(value.suppressUploads)
        XCTAssertFalse(value.suppressReadReceipts)
        XCTAssertFalse(value.suppressOnlineStatus)
        XCTAssertFalse(value.automaticOffline)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostSettingsKey)?.data }), entry.data)
    }

    func testIncompleteNewPolicyFailsClosedInsteadOfDefaultingProtectionOff() throws {
        struct Incomplete: Encodable { let schemaVersion: Int32 = 2; let enabled = true }
        let entry = try XCTUnwrap(PreferencesEntry(Incomplete()))
        XCTAssertEqual(localGhostSettings(entry: entry), .unsupported)
    }

    func testResetPreservesStoryGenerationAndDoesNotTouchOtherAccount() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-reset-other")
        defer { try? other.destroy() }
        try self.configure(LocalGhostSettings(enabled: true))
        try self.configure(LocalGhostSettings(enabled: true), postbox: other.postbox)
        let story = LocalStoryPrivacyContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(story.setSuppressed(true)))
        let before = try self.localArchiveValue(self.fixture.postbox.transaction { localStoryPrivacySettings(transaction: $0) })
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).resetSettings()))
        let after = try self.localArchiveValue(self.fixture.postbox.transaction { localStoryPrivacySettings(transaction: $0) })
        guard case let .available(a) = before, case let .available(b) = after else { return XCTFail("story policy lost") }
        XCTAssertFalse(b.suppressViewReceipts)
        XCTAssertEqual(a.suppressionGeneration, b.suppressionGeneration)
        let current = try self.localArchiveValue(self.fixture.postbox.transaction { localGhostSettings(transaction: $0) })
        let otherState = try self.localArchiveValue(other.postbox.transaction { localGhostSettings(transaction: $0) })
        guard case let .available(currentValue) = current, case let .available(otherValue) = otherState else { return XCTFail("policy missing") }
        XCTAssertFalse(currentValue.enabled)
        XCTAssertTrue(otherValue.enabled)
    }
}
