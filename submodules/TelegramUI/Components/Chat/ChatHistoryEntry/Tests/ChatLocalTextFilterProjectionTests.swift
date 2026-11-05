// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import TelegramCore
import TelegramPresentationData
@testable import ChatHistoryEntry

/// Represents a future message attribute the older client does not understand.
private final class UnknownFilterTestAttribute: EngineMessage.Attribute {
    init() {}
    init(decoder: EnginePostboxDecoder) {}
    func encode(_ encoder: EnginePostboxEncoder) {}
}

/// Tests the production projection and eligibility boundary. It does not render
/// bubbles, measure scrolling or simulate taps in a logged-in Telegram client.
final class ChatLocalTextFilterProjectionTests: XCTestCase {
    private lazy var presentation = ChatPresentationData(presentationData: defaultPresentationData())

    func testProjectionRunsInIsolatedIOSHostWithPresentationResources() {
        XCTAssertEqual(Bundle.main.bundleIdentifier, "org.livara.ayugram.tests.native-security.projection")
        XCTAssertEqual(Bundle.main.bundleURL.pathExtension, "app")
        XCTAssertEqual(Bundle.main.object(forInfoDictionaryKey: "AyuGramProjectionHostDiagnostics") as? Bool, true)
        XCTAssertNotNil(Bundle.main.url(forResource: "PresentationStrings", withExtension: "data"))
        XCTAssertNotNil(Bundle.main.path(forResource: "Localizable", ofType: "strings", inDirectory: nil, forLocalization: "en"))
    }

    private func message(stableId: UInt32 = 1, id: Int32? = nil,
                         peerNamespace: EnginePeer.Id.Namespace = Namespaces.Peer.CloudUser, peerValue: Int64 = 1001,
                         namespace: Int32 = Namespaces.Message.Cloud,
                         flags: EngineMessage.Flags = [.Incoming], text: String = "an offer",
                         groupingKey: Int64? = nil, groupInfo: EngineMessage.GroupInfo? = nil,
                         forward: EngineMessage.ForwardInfo? = nil,
                         attributes: [EngineMessage.Attribute] = [], media: [EngineRawMedia] = [], author: EngineRawPeer? = nil) -> EngineRawMessage {
        let peerId = EnginePeer.Id(namespace: peerNamespace, id: ._internalFromInt64Value(peerValue))
        return EngineRawMessage(stableId: stableId, stableVersion: 4,
            id: EngineMessage.Id(peerId: peerId, namespace: namespace, id: id ?? Int32(stableId)),
            globallyUniqueId: nil, groupingKey: groupingKey, groupInfo: groupInfo,
            threadId: nil, timestamp: 100 + Int32(stableId), flags: flags, tags: [],
            globalTags: [], localTags: [], customTags: [], forwardInfo: forward, author: author,
            text: text, attributes: attributes, media: media, peers: .init(),
            associatedMessages: .init(), associatedMessageIds: [], associatedMedia: [:],
            associatedThreadInfo: nil, associatedStories: [:])
    }

    private func row(_ message: EngineRawMessage, attributes: ChatMessageEntryAttributes = ChatMessageEntryAttributes(),
                     selected: Bool? = nil) -> ChatHistoryEntry {
        if let selected {
            return .MessageEntry(message, self.presentation, true,
                EngineMessageHistoryEntryLocation(index: 2, count: 8),
                .selectable(selected: selected, num: 2), attributes)
        }
        return .MessageEntry(message, self.presentation, true,
            EngineMessageHistoryEntryLocation(index: 2, count: 8),
            .none, attributes)
    }

    private func snapshots(_ messages: [EngineRawMessage]) -> [EngineMessage.Id: ChatLocalTextFilterSnapshot] {
        return Dictionary(uniqueKeysWithValues: messages.map { ($0.id, ChatLocalTextFilterSnapshot(message: $0)) })
    }

    private func assertRow(_ projected: ChatHistoryEntry, preserves original: ChatHistoryEntry,
                           state: ChatLocalTextFilterPresentation, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(projected.stableId, original.stableId, file: file, line: line)
        XCTAssertEqual(projected.index, original.index, file: file, line: line)
        XCTAssertEqual(projected.firstIndex, original.firstIndex, file: file, line: line)
        guard case let .MessageEntry(before, beforePresentation, beforeRead, beforeLocation, beforeSelection, beforeAttributes) = original,
              case let .MessageEntry(after, afterPresentation, afterRead, afterLocation, afterSelection, afterAttributes) = projected else {
            return XCTFail("expected message rows", file: file, line: line)
        }
        XCTAssertTrue(after === before, "projection must retain the canonical message object", file: file, line: line)
        XCTAssertTrue(afterPresentation === beforePresentation, file: file, line: line)
        XCTAssertEqual(afterRead, beforeRead, file: file, line: line)
        XCTAssertEqual(afterLocation, beforeLocation, file: file, line: line)
        XCTAssertEqual(afterSelection, beforeSelection, file: file, line: line)
        switch (beforeSelection, afterSelection) {
        case (.none, .none):
            break
        case let (.selectable(beforeSelected, beforeNum), .selectable(afterSelected, afterNum)):
            XCTAssertEqual(afterSelected, beforeSelected, file: file, line: line)
            XCTAssertEqual(afterNum, beforeNum, file: file, line: line)
        default:
            XCTFail("selection was lost", file: file, line: line)
        }
        var expectedAttributes = beforeAttributes
        expectedAttributes.localTextFilter = state
        XCTAssertEqual(afterAttributes, expectedAttributes, file: file, line: line)
    }

    private func media(_ type: LocalMessageFilterMediaType) -> EngineRawMedia {
        if type == .photo {
            return TelegramMediaImage(imageId: .init(namespace: Namespaces.Media.CloudImage, id: 7601), representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
        }
        let attributes: [TelegramMediaFileAttribute]
        let mime: String
        switch type {
        case .video: mime = "video/mp4"; attributes = [.Video(duration: 1, size: .init(width: 16, height: 16), flags: [], preloadSize: nil, coverTime: nil, videoCodec: nil)]
        case .gif: mime = "image/gif"; attributes = [.Animated]
        case .voiceMessage: mime = "audio/ogg"; attributes = [.Audio(isVoice: true, duration: 1, title: nil, performer: nil, waveform: nil)]
        case .videoMessage: mime = "video/mp4"; attributes = [.Video(duration: 1, size: .init(width: 16, height: 16), flags: [.instantRoundVideo], preloadSize: nil, coverTime: nil, videoCodec: nil)]
        case .document: mime = "application/pdf"; attributes = [.FileName(fileName: "fixture.pdf")]
        case .photo: preconditionFailure("photo handled above")
        }
        return TelegramMediaFile(fileId: .init(namespace: Namespaces.Media.CloudFile, id: 7602), partialReference: nil, resource: CloudDocumentMediaResource(datacenterId: 1, fileId: 7602, accessHash: 0, size: 1, fileReference: nil, fileName: nil), previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: mime, size: 1, attributes: attributes, alternativeRepresentations: [])
    }

    private func assertMediaCanHideAndReveal(_ type: LocalMessageFilterMediaType, file: StaticString = #filePath, line: UInt = #line) {
        let media = self.media(type)
        let attributes: [EngineMessage.Attribute] = type == .voiceMessage || type == .videoMessage ? [ConsumableContentMessageAttribute(consumed: false)] : []
        let message = self.message(text: "", attributes: attributes, media: [media])
        XCTAssertTrue(chatHistoryMessageSupportsLocalTextFiltering(message), String(describing: type), file: file, line: line)
        XCTAssertEqual(ChatLocalTextFilterSnapshot(message: message).mediaType, type, file: file, line: line)
        let row = self.row(message)
        let hidden = chatHistoryApplyingLocalTextFilter([row], matches: self.snapshots([message]), revealed: false)
        self.assertRow(hidden[0], preserves: row, state: .hidden, file: file, line: line)
        let visible = chatHistoryApplyingLocalTextFilter(hidden, matches: self.snapshots([message]), revealed: true)
        self.assertRow(visible[0], preserves: row, state: .revealed, file: file, line: line)
        let selecting = chatHistoryApplyingLocalTextFilter(hidden, matches: self.snapshots([message]), revealed: false, selectionActive: true)
        self.assertRow(selecting[0], preserves: row, state: .revealed, file: file, line: line)
        XCTAssertTrue(message.media[0].isEqual(to: media), file: file, line: line)
        XCTAssertEqual(message.text, "", file: file, line: line)
    }

    func testSingleOrdinaryPhotoCanHideAndRevealWithoutChangingItsMedia() { self.assertMediaCanHideAndReveal(.photo) }
    func testSingleOrdinaryVideoCanHideAndRevealWithoutChangingItsMedia() { self.assertMediaCanHideAndReveal(.video) }
    func testSingleOrdinaryGIFCanHideAndRevealWithoutChangingItsMedia() { self.assertMediaCanHideAndReveal(.gif) }
    func testSingleOrdinaryVoiceCanHideAndRevealWithoutConsumingIt() { self.assertMediaCanHideAndReveal(.voiceMessage) }
    func testSingleOrdinaryRoundVideoCanHideAndRevealWithoutConsumingIt() { self.assertMediaCanHideAndReveal(.videoMessage) }
    func testSingleOrdinaryDocumentCanHideAndRevealWithoutChangingItsMedia() { self.assertMediaCanHideAndReveal(.document) }

    func testMediaExpiryProtectionGroupingAndUnknownMetadataStayVisible() {
        for type in LocalMessageFilterMediaType.allCases {
            let media = self.media(type)
            let cases = [
                self.message(flags: [.Incoming, .CopyProtected], text: "", media: [media]),
                self.message(text: "", attributes: [AutoclearTimeoutMessageAttribute(timeout: 30, countdownBeginTime: nil)], media: [media]),
                self.message(text: "", attributes: [AutoclearTimeoutMessageAttribute(timeout: Int32.max, countdownBeginTime: nil)], media: [media]),
                self.message(text: "", attributes: [AutoremoveTimeoutMessageAttribute(timeout: 60, countdownBeginTime: nil)], media: [media]),
                self.message(text: "", attributes: [UnknownFilterTestAttribute()], media: [media]),
                self.message(text: "", groupingKey: 71, media: [media]),
                self.message(text: "", groupInfo: .init(stableId: 72), media: [media]),
                self.message(peerNamespace: Namespaces.Peer.SecretChat, text: "", media: [media]),
                self.message(text: "", media: [media, self.media(.photo)])
            ]
            for message in cases {
                XCTAssertFalse(chatHistoryMessageSupportsLocalTextFiltering(message), String(describing: type))
                let row = self.row(message)
                let projected = chatHistoryApplyingLocalTextFilter([row], matches: self.snapshots([message]), revealed: false)
                self.assertRow(projected[0], preserves: row, state: .none)
            }
        }
    }

    private func author(_ value: Int64) -> TelegramUser {
        return TelegramUser(id: .init(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(value)), accessHash: nil, firstName: "Filter fixture", lastName: nil, username: nil, phone: nil, photo: [], botInfo: nil, restrictionInfo: nil, flags: [], emojiStatus: nil, usernames: [], storiesHidden: nil, nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil, subscriberCount: nil, verificationIconFileId: nil)
    }

    func testChangingAuthorOrMediaClassificationInvalidatesAQueuedDecision() {
        let original = self.message(text: "same caption", media: [self.media(.photo)], author: self.author(7611))
        let snapshot = ChatLocalTextFilterSnapshot(message: original)
        for changed in [
            self.message(text: "same caption", media: [self.media(.video)], author: self.author(7611)),
            self.message(text: "same caption", media: [self.media(.photo)], author: self.author(7612))
        ] {
            XCTAssertFalse(snapshot.matches(changed))
            let row = self.row(changed)
            let projected = chatHistoryApplyingLocalTextFilter([row], matches: [changed.id: snapshot], revealed: false)
            self.assertRow(projected[0], preserves: row, state: .none)
        }
    }

    func testStickerAndCustomEmojiNodesCannotBePromotedIntoFilterBubbles() throws {
        let file = try XCTUnwrap(self.media(.document) as? TelegramMediaFile)
        let cases: [[TelegramMediaFileAttribute]] = [
            [.Sticker(displayText: "fixture", packReference: nil, maskData: nil)],
            [.CustomEmoji(isPremium: false, isSingleColor: false, alt: "fixture", packReference: nil)]
        ]
        for attributes in cases {
            let special = file.withUpdatedAttributes(attributes)
            let message = self.message(media: [special])
            XCTAssertNil(localMessageFilterMediaType(message))
            XCTAssertFalse(chatHistoryMessageSupportsLocalTextFiltering(message))
            let row = self.row(message)
            let projected = chatHistoryApplyingLocalTextFilter([row], matches: self.snapshots([message]), revealed: false)
            self.assertRow(projected[0], preserves: row, state: .none)
        }
    }

    func testOrdinaryIncomingCloudTextAndAllowedMetadataRemainEligible() {
        let allowed: [EngineMessage.Attribute] = [
            TextEntitiesMessageAttribute(entities: [MessageTextEntity(range: 3..<8, type: .Bold)]),
            EditedMessageAttribute(date: 200, isHidden: false),
            ViewCountMessageAttribute(count: 3),
            ForwardCountMessageAttribute(count: 2),
            AuthorSignatureMessageAttribute(signature: "Author"),
            NotificationInfoMessageAttribute(flags: [.muted]),
            ConsumablePersonalMentionMessageAttribute(consumed: false, pending: false),
            ReportDeliveryMessageAttribute(untilDate: 300, isReported: false)
        ]
        for peerNamespace in [Namespaces.Peer.CloudUser, Namespaces.Peer.CloudGroup, Namespaces.Peer.CloudChannel] {
            XCTAssertTrue(chatHistoryMessageSupportsLocalTextFiltering(self.message(peerNamespace: peerNamespace)))
            for attribute in allowed {
                XCTAssertTrue(chatHistoryMessageSupportsLocalTextFiltering(self.message(peerNamespace: peerNamespace, attributes: [attribute])))
            }
            let message = self.message(peerNamespace: peerNamespace, attributes: allowed)
            let original = self.row(message)
            let projected = chatHistoryApplyingLocalTextFilter([original], matches: self.snapshots([message]), revealed: false)
            XCTAssertEqual(projected.count, 1)
            self.assertRow(projected[0], preserves: original, state: .hidden)
        }
    }

    func testUnsupportedContentIsNotHiddenEvenWhenSnapshotMatches() {
        let replyId = self.message().id
        let rich = RichTextMessageAttribute(instantPage: InstantPage(blocks: [.paragraph(.plain("rich offer"))],
            media: [:], isComplete: true, rtl: false, url: "", views: nil), fullInstantPage: nil)
        let cases: [(String, EngineRawMessage)] = [
            ("outgoing", self.message(flags: [])),
            ("protected", self.message(flags: [.Incoming, .CopyProtected])),
            ("empty", self.message(text: "")),
            ("forward", self.message(forward: .init(author: nil, source: nil, sourceMessageId: nil,
                date: 1, authorSignature: "Author", psaType: nil, flags: []))),
            ("reply", self.message(attributes: [ReplyMessageAttribute(messageId: replyId, threadMessageId: nil,
                quote: nil, isQuote: false, innerSubject: nil)])),
            ("quoted reply", self.message(attributes: [QuotedReplyMessageAttribute(peerId: nil, authorName: "Author", quote: nil, isQuote: true)])),
            ("rich", self.message(attributes: [rich])),
            ("disappearing", self.message(attributes: [AutoremoveTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)])),
            ("unknown attribute", self.message(attributes: [UnknownFilterTestAttribute()])),
            ("album key", self.message(groupingKey: 123)),
            ("album info", self.message(groupInfo: .init(stableId: 123))),
            ("local", self.message(namespace: Namespaces.Message.Local)),
            ("scheduled", self.message(namespace: Namespaces.Message.ScheduledCloud)),
            ("secret", self.message(peerNamespace: Namespaces.Peer.SecretChat)),
            ("zero id", self.message(id: 0)),
            ("negative id", self.message(id: -1))
        ]
        for (label, message) in cases {
            XCTAssertFalse(chatHistoryMessageSupportsLocalTextFiltering(message), label)
            let original = self.row(message)
            let projected = chatHistoryApplyingLocalTextFilter([original], matches: self.snapshots([message]), revealed: false)
            XCTAssertEqual(projected.count, 1, label)
            self.assertRow(projected[0], preserves: original, state: .none)
        }
    }

    func testAllMatchedRowsKeepCountOrderIdentityAndIndexes() {
        let messages = [self.message(stableId: 3), self.message(stableId: 1), self.message(stableId: 2)]
        var attributes = ChatMessageEntryAttributes()
        attributes.isContact = true
        attributes.isPlaying = true
        attributes.isCentered = true
        attributes.displayContinueThreadFooter = true
        attributes.pinToTop = true
        let original = messages.map { self.row($0, attributes: attributes) }
        let projected = chatHistoryApplyingLocalTextFilter(original, matches: self.snapshots(messages), revealed: false)
        XCTAssertEqual(projected.count, original.count)
        XCTAssertEqual(projected.map(\.stableId), original.map(\.stableId))
        XCTAssertEqual(projected.map(\.index), original.map(\.index))
        for (after, before) in zip(projected, original) {
            self.assertRow(after, preserves: before, state: .hidden)
        }
        XCTAssertEqual(messages.map(\.text), ["an offer", "an offer", "an offer"])
        XCTAssertEqual(messages.map(\.stableVersion), [4, 4, 4])
    }

    func testOnlyMatchedMessageChangesPresentation() {
        let matched = self.message(stableId: 1)
        let visible = self.message(stableId: 2, text: "ordinary conversation")
        let original = [self.row(matched), self.row(visible)]
        let projected = chatHistoryApplyingLocalTextFilter(original, matches: self.snapshots([matched]), revealed: false)
        self.assertRow(projected[0], preserves: original[0], state: .hidden)
        self.assertRow(projected[1], preserves: original[1], state: .none)
    }

    func testRevealAndRehidePreserveCanonicalMessageAndPriorRows() {
        let message = self.message()
        let original = self.row(message)
        let matches = self.snapshots([message])
        let hidden = chatHistoryApplyingLocalTextFilter([original], matches: matches, revealed: false)
        let revealed = chatHistoryApplyingLocalTextFilter(hidden, matches: matches, revealed: true)
        let rehidden = chatHistoryApplyingLocalTextFilter(revealed, matches: matches, revealed: false)
        self.assertRow(hidden[0], preserves: original, state: .hidden)
        self.assertRow(revealed[0], preserves: original, state: .revealed)
        self.assertRow(rehidden[0], preserves: original, state: .hidden)
        self.assertRow(original, preserves: original, state: .none)
        XCTAssertNotEqual(hidden[0], original, "history diff must reload the new placeholder")
        XCTAssertNotEqual(revealed[0], hidden[0], "history diff must reload revealed content")
        XCTAssertEqual(rehidden[0], hidden[0])
        XCTAssertEqual(message.text, "an offer")
        XCTAssertEqual(message.stableVersion, 4)
    }

    func testRemovingMatchesClearsBothHiddenAndRevealedState() {
        let message = self.message()
        let original = self.row(message)
        for reveal in [false, true] {
            let active = chatHistoryApplyingLocalTextFilter([original], matches: self.snapshots([message]), revealed: reveal)
            let cleared = chatHistoryApplyingLocalTextFilter(active, matches: [:], revealed: reveal)
            self.assertRow(cleared[0], preserves: original, state: .none)
        }
        XCTAssertTrue(chatHistoryApplyingLocalTextFilter([], matches: self.snapshots([message]), revealed: false).isEmpty)
    }

    func testSelectionModeRevealsAllMatchesAndClearingSelectionAllowsRehide() {
        let messages = [self.message(stableId: 1), self.message(stableId: 2), self.message(stableId: 3)]
        let selectedRows = [self.row(messages[0], selected: true),
                            self.row(messages[1], selected: false), self.row(messages[2])]
        let matches = self.snapshots(messages)
        let selecting = chatHistoryApplyingLocalTextFilter(selectedRows, matches: matches, revealed: false, selectionActive: true)
        for (after, before) in zip(selecting, selectedRows) {
            self.assertRow(after, preserves: before, state: .revealed)
        }
        // Exiting the real selection flow clears the selection metadata as well
        // as the mode flag; each unchanged canonical message can be hidden again.
        let clearedRows = messages.map { self.row($0) }
        let rehidden = chatHistoryApplyingLocalTextFilter(clearedRows, matches: matches, revealed: false, selectionActive: false)
        for (after, before) in zip(rehidden, clearedRows) {
            self.assertRow(after, preserves: before, state: .hidden)
        }
    }

    func testSelectedMessageStaysVisibleEvenWithInconsistentSelectionModeFlag() {
        let selected = self.message(stableId: 1)
        let unselected = self.message(stableId: 2)
        let rows = [self.row(selected, selected: true), self.row(unselected, selected: false)]
        let projected = chatHistoryApplyingLocalTextFilter(rows, matches: self.snapshots([selected, unselected]), revealed: false, selectionActive: false)
        self.assertRow(projected[0], preserves: rows[0], state: .revealed)
        self.assertRow(projected[1], preserves: rows[1], state: .hidden)
        let cleared = chatHistoryApplyingLocalTextFilter(projected, matches: [:], revealed: false, selectionActive: true)
        self.assertRow(cleared[0], preserves: rows[0], state: .none)
        self.assertRow(cleared[1], preserves: rows[1], state: .none)
    }

    func testStaleMessageVersionOrTextNeverInheritsPreviousMatch() {
        let original = self.message()
        let snapshot = ChatLocalTextFilterSnapshot(message: original)
        XCTAssertTrue(snapshot.matches(original))
        let edits = [original.withUpdatedStableVersion(stableVersion: 5),
                     original.withUpdatedText("edited ordinary conversation")]
        for edit in edits {
            XCTAssertFalse(snapshot.matches(edit))
            let row = self.row(edit)
            let projected = chatHistoryApplyingLocalTextFilter([row], matches: [edit.id: snapshot], revealed: false)
            self.assertRow(projected[0], preserves: row, state: .none)
            let current = chatHistoryApplyingLocalTextFilter([row], matches: self.snapshots([edit]), revealed: false)
            self.assertRow(current[0], preserves: row, state: .hidden)
        }
    }

    func testSnapshotIdentityRejectsOtherPeerMessageAndNamespace() {
        let original = self.message()
        let snapshot = ChatLocalTextFilterSnapshot(message: original)
        let unrelated = [self.message(id: 2), self.message(peerValue: 1002),
                         self.message(namespace: Namespaces.Message.ScheduledCloud)]
        for message in unrelated {
            XCTAssertFalse(snapshot.matches(message))
            let row = self.row(message)
            // Even a wrongly keyed result must pass the snapshot's identity check.
            let projected = chatHistoryApplyingLocalTextFilter([row], matches: [message.id: snapshot], revealed: false)
            self.assertRow(projected[0], preserves: row, state: .none)
        }
    }

    func testLargeAndAnimatedEmojiKeepTheirOriginalNodeClassification() {
        let message = self.message(text: "🙂")
        XCTAssertTrue(chatHistoryMessageSupportsLocalTextFiltering(message))
        for contentType in [ChatMessageEntryContentType.largeEmoji, .animatedEmoji] {
            var attributes = ChatMessageEntryAttributes()
            attributes.contentTypeHint = contentType
            let original = self.row(message, attributes: attributes)
            let projected = chatHistoryApplyingLocalTextFilter([original], matches: self.snapshots([message]), revealed: false)
            self.assertRow(projected[0], preserves: original, state: .none)
        }
    }

    func testAlbumAndNonMessageEntriesPassThroughInTheirOriginalOrder() {
        let first = self.message(stableId: 1, groupingKey: 99)
        let second = self.message(stableId: 2, groupingKey: 99)
        let album: ChatHistoryEntry = .MessageGroupEntry(99, [
            (first, true, .none, ChatMessageEntryAttributes(), nil),
            (second, false, .none, ChatMessageEntryAttributes(), nil)
        ], self.presentation)
        let entries: [ChatHistoryEntry] = [
            .ChatInfoEntry(.newThreadInfo, self.presentation),
            .UnreadEntry(first.index, self.presentation), album,
            .ReplyCountEntry(second.index, false, 7, self.presentation)
        ]
        for reveal in [false, true] {
            let projected = chatHistoryApplyingLocalTextFilter(entries, matches: self.snapshots([first, second]), revealed: reveal)
            XCTAssertEqual(projected, entries)
            XCTAssertEqual(projected.map(\.stableId), entries.map(\.stableId))
            XCTAssertEqual(projected.map(\.index), entries.map(\.index))
            XCTAssertEqual(projected.map(\.firstIndex), entries.map(\.firstIndex))
            guard case let .MessageGroupEntry(_, messages, _) = projected[2] else {
                return XCTFail("album row was replaced")
            }
            XCTAssertTrue(messages[0].0 === first)
            XCTAssertTrue(messages[1].0 === second)
            XCTAssertEqual(messages.map { $0.3.localTextFilter }, [.none, .none])
        }
    }
}
