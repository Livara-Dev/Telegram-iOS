// SPDX-License-Identifier: GPL-2.0-or-later
import XCTest
import TelegramCore
@testable import SettingsUI

final class LocalArchivePeerReferenceTests: XCTestCase {
    func testUnknownArchivedReferenceRetainsLocalScopeWithoutCreatingLiveMessageLookup() {
        for rawId in [Int64.min, -1, Int64.max, 0x0000000700000000, 0x7fffffff00000000] {
            let reference = LocalArchiveMessageReference(peerId: rawId, namespace: Namespaces.Message.Cloud, messageId: 42)
            XCTAssertEqual(reference.peerId, rawId, "local timeline scope must not be rewritten")
            XCTAssertEqual(reference.messageId, 42)
            XCTAssertNil(reference.engineMessageId, "unknown archive metadata must not create a canonical lookup")
        }
    }

    func testKnownCloudReferenceUsesExactIdentityAndUnsupportedNamespacesStayLocalOnly() {
        let peer = EnginePeer.Id(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00ffffffffffffff))
        let reference = LocalArchiveMessageReference(peerId: peer.toInt64(), namespace: Namespaces.Message.Cloud, messageId: 42)
        XCTAssertEqual(reference.engineMessageId, EngineMessage.Id(peerId: peer, namespace: Namespaces.Message.Cloud, id: 42))
        for namespace in [Namespaces.Message.ScheduledCloud, Namespaces.Message.SecretIncoming, Int32.max] {
            XCTAssertNil(LocalArchiveMessageReference(peerId: peer.toInt64(), namespace: namespace, messageId: 42).engineMessageId)
        }
        XCTAssertNil(LocalArchiveMessageReference(peerId: peer.toInt64(), namespace: Namespaces.Message.Cloud, messageId: 0).engineMessageId)
    }
}
