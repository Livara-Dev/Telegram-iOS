// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import TelegramCore
import TelegramPresentationData
@testable import ChatHistoryEntry

/// Tests the production entry projection. It never inserts a saved body into
/// Postbox or fabricates a live EngineMessage from encrypted archive data.
final class ChatLocalArchiveProjectionTests: XCTestCase {
    private lazy var presentation = ChatPresentationData(presentationData: defaultPresentationData())
    private let peer = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7111))

    private func saved(_ id: Int32, peer: EnginePeer.Id? = nil, sequence: Int64 = 1, timestamp: Int32? = nil) -> LocalArchiveDeletedHistoryItem {
        return LocalArchiveDeletedHistoryItem(peerId: peer ?? self.peer, messageId: id, sequence: sequence,
            eventKey: "local-event-\(id)-\(sequence)", sentAt: timestamp ?? id, deletedAt: 900,
            marker: "Deleted", storedMediaCount: 1, unavailableMediaCount: 1, moreAvailable: false,
            content: LocalArchiveDeletedHistoryContent(text: "saved body \(id)"))
    }

    private func message(_ id: Int32) -> EngineRawMessage {
        return EngineRawMessage(stableId: UInt32(id), stableVersion: 1,
            id: EngineMessage.Id(peerId: self.peer, namespace: Namespaces.Message.Cloud, id: id), globallyUniqueId: nil,
            groupingKey: nil, groupInfo: nil, threadId: nil, timestamp: id, flags: [.Incoming], tags: [], globalTags: [],
            localTags: [], customTags: [], forwardInfo: nil, author: nil, text: "canonical \(id)", attributes: [], media: [],
            peers: .init(), associatedMessages: .init(), associatedMessageIds: [], associatedMedia: [:], associatedThreadInfo: nil, associatedStories: [:])
    }

    private func row(_ message: EngineRawMessage) -> ChatHistoryEntry {
        return .MessageEntry(message, self.presentation, false, nil, .none, ChatMessageEntryAttributes())
    }

    private func project(_ entries: [ChatHistoryEntry], _ rows: [LocalArchiveDeletedHistoryItem], permitted: Bool = true,
                         reverse: Bool = false) -> [ChatHistoryEntry] {
        return chatHistoryApplyingLocalArchive(entries, rows: rows, peerId: self.peer, presentationData: self.presentation,
            permitted: permitted, reverse: reverse)
    }

    func testLocalRowsSortBetweenUnmodifiedCanonicalObjects() {
        let first = self.message(1), last = self.message(3)
        let result = self.project([self.row(first), self.row(last)], [self.saved(2)])
        XCTAssertEqual(result.map { $0.index.id.id }, [1, 2, 3])
        guard case let .MessageEntry(actualFirst, _, _, _, _, _) = result[0],
              case let .LocalArchiveEntry(local, _) = result[1],
              case let .MessageEntry(actualLast, _, _, _, _, _) = result[2] else { return XCTFail("distinct row types required") }
        XCTAssertTrue(actualFirst === first); XCTAssertTrue(actualLast === last)
        XCTAssertEqual(local.content.text, "saved body 2")
        XCTAssertEqual(chatHistoryCanonicalEntries(result).count, 2)
    }

    func testCanonicalAndAlbumIdentitiesWinOverArchiveDuplicates() {
        let m1 = self.message(1), m2 = self.message(2), m3 = self.message(3)
        let album: ChatHistoryEntry = .MessageGroupEntry(77, [(m2, false, .none, .init(), nil), (m3, false, .none, .init(), nil)], self.presentation)
        let result = self.project([self.row(m1), album], [self.saved(1), self.saved(2), self.saved(3), self.saved(4)])
        XCTAssertEqual(result.count, 3)
        XCTAssertEqual(result.filter { if case .LocalArchiveEntry = $0 { return true }; return false }.map { $0.index.id.id }, [4])
    }

    func testRepeatedDeletedIdHasOneStableIdentityAndNewVersionUpdatesEquality() {
        let old: ChatHistoryEntry = .LocalArchiveEntry(self.saved(9), self.presentation)
        let new: ChatHistoryEntry = .LocalArchiveEntry(self.saved(9, sequence: 2), self.presentation)
        XCTAssertEqual(old.stableId, new.stableId)
        XCTAssertNotEqual(old, new)
        XCTAssertEqual(self.project([], [self.saved(9), self.saved(9)]).count, 1)
    }

    func testArchiveIdentityCannotCollideWithCanonicalOrArbitraryAlbumBits() {
        for id in [Int32(1), 2, 40, 65535, Int32.max] {
            let local = ChatHistoryEntry.LocalArchiveEntry(self.saved(id), self.presentation)
            for hint in [ChatMessageEntryContentType.generic, .largeEmoji, .animatedEmoji] {
                var attributes = ChatMessageEntryAttributes(); attributes.contentTypeHint = hint
                let canonical = ChatHistoryEntry.MessageEntry(self.message(id), self.presentation, false, nil, .none, attributes)
                XCTAssertNotEqual(local.stableId, canonical.stableId)
            }
            for group in [Int64(0), 1, -1, Int64.min, Int64.max, Int64(bitPattern: local.stableId)] {
                let album = ChatHistoryEntry.MessageGroupEntry(group, [(self.message(1), false, .none, .init(), nil)], self.presentation)
                XCTAssertNotEqual(local.stableId, album.stableId)
            }
        }
    }

    func testOtherAccountChatPeerCannotAppear() {
        let other = EnginePeer.Id(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7222))
        XCTAssertTrue(self.project([], [self.saved(1, peer: other)]).isEmpty)
    }

    func testUnsafeIdentifiersAndTimestampsFailClosed() {
        XCTAssertTrue(self.project([], [self.saved(0), self.saved(-1), self.saved(2, sequence: 0), self.saved(3, timestamp: -1)]).isEmpty)
    }

    func testRevokedBodyNeverReentersAReplayedTransition() {
        let saved = self.saved(2)
        let oldResult = self.project([], [saved])
        if Thread.isMainThread { saved.content.revoke() } else { DispatchQueue.main.sync { saved.content.revoke() } }
        XCTAssertNil(saved.content.text)
        if case let .LocalArchiveEntry(retained, _) = oldResult[0] { XCTAssertNil(retained.content.text) } else { XCTFail() }
        XCTAssertTrue(self.project([], [saved]).isEmpty)
    }

    func testProtectedSelectionPreviewOrFilterGateDoesNotInsertAnyLocalRows() {
        let canonical = self.row(self.message(1))
        XCTAssertEqual(self.project([canonical], [self.saved(2)], permitted: false), [canonical])
    }

    func testOutputBoundDoesNotExpandWithLargeArchivePage() {
        let rows = (1...200).map { self.saved(Int32($0)) }
        XCTAssertEqual(self.project([], rows).count, 40)
    }

    func testTruncationNoticeSurvivesDeduplicationOfItsOriginalRow() {
        let first = self.saved(2), last = self.saved(1).withMoreAvailable(true)
        let result = self.project([self.row(self.message(1))], [first, last])
        let local = result.compactMap { entry -> LocalArchiveDeletedHistoryItem? in
            if case let .LocalArchiveEntry(row, _) = entry { return row }; return nil
        }
        XCTAssertEqual(local.count, 1)
        XCTAssertTrue(local[0].moreAvailable)
        XCTAssertTrue(local[0].content === first.content)
    }

    func testReverseHistoryMaintainsComparatorOrder() {
        let result = self.project([self.row(self.message(3)), self.row(self.message(1))], [self.saved(2)], reverse: true)
        XCTAssertEqual(result.map { $0.index.id.id }, [3, 2, 1])
    }

    func testOnlyCanonicalRowsCanSupplyNetworkPaginationAndReadAnchors() {
        let m2 = self.row(self.message(2)), m4 = self.row(self.message(4))
        let result = self.project([m2, m4], [self.saved(1), self.saved(3), self.saved(5)])
        let anchors = chatHistoryCanonicalEntries(result)
        XCTAssertEqual(anchors.first?.index.id.id, 2)
        XCTAssertEqual(anchors.last?.index.id.id, 4)
        XCTAssertTrue(chatHistoryCanonicalEntries(self.project([], [self.saved(1)])).isEmpty)
    }
}
