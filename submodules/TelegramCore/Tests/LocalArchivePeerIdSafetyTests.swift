// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import LocalMessageArchive
@testable import TelegramCore

final class LocalArchivePeerIdSafetyTests: XCTestCase {
    func testSharedDecoderRejectsMalformedBitsAndPreservesDecodableNamespaces() throws {
        for value in [Int64.min, -1, Int64.max, 0x0000000700000000] {
            XCTAssertFalse(telegramCorePeerIdIsSafelyDecodable(value))
            XCTAssertNil(telegramCoreDecodePeerId(value))
        }
        let decodable: [Int64] = [0, 1, 0x00000003ffffffff, 0x0000000400000001, 0x0000000700000001, 0x7fffffff00000000]
        for value in decodable {
            XCTAssertTrue(telegramCorePeerIdIsSafelyDecodable(value))
            XCTAssertEqual(try XCTUnwrap(telegramCoreDecodePeerId(value)).toInt64(), value)
        }
    }

    func testArchiveDisplayAcceptsOnlyPositiveOrdinaryCloudPeerAndMessageIdentities() throws {
        for namespace in [Namespaces.Peer.CloudUser, Namespaces.Peer.CloudGroup, Namespaces.Peer.CloudChannel] {
            let peer = PeerId(namespace: namespace, id: ._internalFromInt64Value(0x00ffffffffffffff))
            XCTAssertEqual(localArchiveDisplayPeerId(peer.toInt64()), peer)
            XCTAssertEqual(localArchiveDisplayMessageId(peerId: peer.toInt64(), namespace: Namespaces.Message.Cloud, messageId: Int32.max), MessageId(peerId: peer, namespace: Namespaces.Message.Cloud, id: Int32.max))
            XCTAssertNil(localArchiveDisplayMessageId(peerId: peer.toInt64(), namespace: Namespaces.Message.Cloud, messageId: 0))
            XCTAssertNil(localArchiveDisplayMessageId(peerId: peer.toInt64(), namespace: Namespaces.Message.ScheduledCloud, messageId: 1))
        }
        let secret = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(-1)).toInt64()
        for value in [Int64.min, -1, 0, Int64.max, secret, 0x0000000400000001, 0x7fffffff00000000] {
            XCTAssertNil(localArchiveDisplayPeerId(value))
            XCTAssertNil(localArchiveDisplayMessageId(peerId: value, namespace: Namespaces.Message.Cloud, messageId: 1))
        }
    }

    func testUnknownGenericArchiveIdentityRemainsAvailableAsLocalDataWithoutPeerDecoding() {
        for value in [Int64.min, Int64.max, 0x0000000700000000] {
            let event = ArchiveEvent(eventKey: "unknown-identity", message: ArchiveMessageID(accountId: 7, peerId: value, namespace: 0, messageId: 1), kind: .deleted, observedAt: 200, content: ArchiveContent(text: "preserved body", authorId: value, sentAt: 100))
            let record = LocalArchivedMessage(ArchivedRecord(sequence: 1, event: event))
            XCTAssertEqual(record.peerId, value)
            XCTAssertEqual(record.authorId, value)
            XCTAssertEqual(record.text, "preserved body")
            XCTAssertEqual(record.eventKey, event.eventKey)
            XCTAssertNil(localArchiveDisplayPeerId(record.peerId))
            XCTAssertNil(localArchiveDisplayMessageId(peerId: record.peerId, namespace: record.namespace, messageId: record.messageId))
        }
    }

    func testLargestValidExclusionPolicyFitsPostboxBudgetWithoutChangingFixedWidthCodec() throws {
        let ids = Set((0..<4096).map { PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00ffffffffffffff - Int64($0))).toInt64() })
        let settings = LocalArchiveSettings(enabled: true, excludedPeerIds: ids, excludedSenderIds: ids)
        XCTAssertTrue(settings.isSupported)
        let entry = try XCTUnwrap(PreferencesEntry(settings))
        XCTAssertLessThanOrEqual(entry.data.count, 128 * 1024)
        XCTAssertEqual(entry.get(LocalArchiveSettings.self), settings)
        XCTAssertTrue(localArchiveSettings(entry: entry).isSupported)
        let oversized = Set((0..<4097).map { Int64($0 + 1) })
        XCTAssertFalse(LocalArchiveSettings(excludedPeerIds: oversized).isSupported)
        XCTAssertFalse(LocalArchiveSettings(excludedSenderIds: oversized).isSupported)
    }

    func testOversizedArchivePreferenceEntryIsReadOnlyEvenWhenItsKnownFieldsAreValid() throws {
        struct PaddedPolicy: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let excludeBots = true
            let excludedPeerIds: [Int64] = []
            let excludedSenderIds: [Int64] = []
            let maxAgeSeconds = Int64.max
            let maxPayloadBytes: Int64 = 32 * 1024 * 1024
            let generation: Int64 = 0
            let pendingPurge = false
            let droppedCaptures: Int64 = 0
            let padding = String(repeating: "x", count: 128 * 1024)
        }
        let entry = try XCTUnwrap(PreferencesEntry(PaddedPolicy()))
        XCTAssertTrue(try XCTUnwrap(entry.get(LocalArchiveSettings.self)).isSupported)
        XCTAssertFalse(localArchiveSettings(entry: entry).isSupported)
    }
}
