// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalMessageFilterMediaTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private let peerId = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(7501))
    private let authorId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7502))

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "media-and-blocked-filter")
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let group = TelegramGroup(id: self.peerId, title: "Filter fixture group", photo: [], participantCount: 2, role: .member, membership: .Member, flags: [], defaultBannedRights: nil, migrationReference: nil, creationDate: 0, version: 0)
            transaction.updatePeersInternal([group, localArchiveTestUser(self.authorId)], update: { _, updated in updated })
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private var context: LocalMessageFilterContext { LocalMessageFilterContext(postbox: self.fixture.postbox) }

    private func settings() throws -> LocalMessageFilterSettings {
        guard case let .available(settings) = try self.localArchiveValue(self.context.settings) else {
            throw NSError(domain: "LocalMessageFilterMediaTests", code: 1)
        }
        return settings
    }

    private func setBlocked(_ blocked: Bool, postbox: Postbox? = nil) throws {
        let _ = try self.localArchiveValue((postbox ?? self.fixture.postbox).transaction { transaction in
            transaction.updatePeerCachedData(peerIds: [self.authorId], update: { _, current in
                return ((current as? CachedUserData) ?? CachedUserData()).withUpdatedIsBlocked(blocked)
            })
        })
    }

    private func stored(_ id: Int32, text: String = "", media: [Media]) throws -> Message {
        let id = MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: id)
        return try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { transaction -> Message? in
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: text, authorId: self.authorId, media: media)], location: .Random)
            return transaction.getMessage(id)
        }))
    }

    private func media(_ type: LocalMessageFilterMediaType, id: Int64) -> Media {
        if type == .photo {
            return TelegramMediaImage(imageId: MediaId(namespace: Namespaces.Media.CloudImage, id: id), representations: [], immediateThumbnailData: nil, reference: nil, partialReference: nil, flags: [])
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
        return TelegramMediaFile(fileId: MediaId(namespace: Namespaces.Media.CloudFile, id: id), partialReference: nil, resource: CloudDocumentMediaResource(datacenterId: 1, fileId: id, accessHash: 0, size: 1, fileReference: nil, fileName: nil), previewRepresentations: [], videoThumbnails: [], immediateThumbnailData: nil, mimeType: mime, size: 1, attributes: attributes, alternativeRepresentations: [])
    }

    func testEveryTypedMediaRuleUsesProductionClassificationAndPreservesPostbox() throws {
        for (index, type) in LocalMessageFilterMediaType.allCases.enumerated() {
            let rule = LocalMessageFilterRule(id: "media", text: "", mediaType: type)
            XCTAssertTrue(rule.isSupported)
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
            XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
            let message = try self.stored(Int32(index + 1), media: [self.media(type, id: Int64(index + 100))])
            XCTAssertEqual(localMessageFilterMediaType(message), type)
            let evaluator = LocalMessageFilterEvaluator(settings: try self.settings())
            XCTAssertEqual(evaluator.evaluate(text: message.text, peerId: self.peerId.toInt64(), mediaType: localMessageFilterMediaType(message)), .matched(ruleId: rule.id))
            let stored = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(message.id) }))
            XCTAssertEqual(stored.text, message.text)
            XCTAssertEqual(stored.stableVersion, message.stableVersion)
            XCTAssertTrue(stored.media[0].isEqual(to: message.media[0]))
        }
    }

    func testMediaRulesRespectGlobalExceptionsAndExplicitChatRules() {
        let global = LocalMessageFilterRule(id: "global", text: "", mediaType: .photo)
        let local = LocalMessageFilterRule(id: "local", text: "", peerId: self.peerId.toInt64(), mediaType: .photo)
        let excluded = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [global], excludedPeerIds: [self.peerId.toInt64()]))
        XCTAssertEqual(excluded.evaluate(text: "", peerId: self.peerId.toInt64(), mediaType: .photo), .noMatch)
        let overridden = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [global, local], excludedPeerIds: [self.peerId.toInt64()]))
        XCTAssertEqual(overridden.evaluate(text: "", peerId: self.peerId.toInt64(), mediaType: .photo), .matched(ruleId: local.id))
        XCTAssertEqual(overridden.evaluate(text: "", peerId: self.peerId.toInt64(), mediaType: .video), .noMatch)
    }

    func testMediaRulesRejectAmbiguousTextRegexOrCaseFields() throws {
        for rule in [
            LocalMessageFilterRule(text: "caption", mediaType: .photo),
            LocalMessageFilterRule(text: "", caseSensitive: true, mediaType: .photo),
            LocalMessageFilterRule(text: ".*", matchingMode: .regularExpression, mediaType: .photo)
        ] {
            XCTAssertFalse(rule.isSupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.upsertRule(rule)))
        }
        XCTAssertTrue(try self.settings().rules.isEmpty)
        let emptyCaption = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [LocalMessageFilterRule(text: "a*", matchingMode: .regularExpression)]))
        XCTAssertEqual(emptyCaption.evaluate(text: "", peerId: 1, mediaType: .photo), .noMatch)
    }

    func testBlockedSenderUsesLocalCacheWithoutChangingCanonicalMessages() throws {
        XCTAssertEqual(try self.localArchiveValue(self.context.locallyBlockedAuthors([self.authorId])), [])
        let message = try self.stored(20, text: "blocked sender body", media: [])
        try self.setBlocked(true)
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideBlockedSenders(true)))
        let blocked = try self.localArchiveValue(self.context.locallyBlockedAuthors([self.authorId]))
        XCTAssertEqual(blocked, [self.authorId])
        let evaluator = LocalMessageFilterEvaluator(settings: try self.settings())
        XCTAssertEqual(evaluator.evaluate(text: message.text, peerId: self.peerId.toInt64(), senderIsLocallyBlocked: blocked.contains(self.authorId)), .matched(ruleId: "local-blocked-sender"))
        XCTAssertEqual(evaluator.evaluate(text: message.text, peerId: self.peerId.toInt64(), senderIsLocallyBlocked: false), .noMatch)
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(message.id)?.text }), message.text)
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.peerId.toInt64(), excluded: true)))
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: try self.settings()).evaluate(text: message.text, peerId: self.peerId.toInt64(), senderIsLocallyBlocked: true), .noMatch)
    }

    func testBlockAndUnblockMetadataChangesReachAnAlreadyOpenConsumer() throws {
        let initial = self.expectation(description: "unknown block metadata")
        let blocked = self.expectation(description: "local block becomes known")
        let unblocked = self.expectation(description: "local unblock applies")
        let values = Atomic<[Set<PeerId>]>(value: [])
        let disposable = self.context.locallyBlockedAuthors([self.authorId]).start(next: { value in
            let count = values.modify { current -> [Set<PeerId>] in current + [value] }.count
            if count == 1 { initial.fulfill() }
            if count == 2 { blocked.fulfill() }
            if count == 3 { unblocked.fulfill() }
        })
        defer { disposable.dispose() }
        self.wait(for: [initial], timeout: 10)
        try self.setBlocked(true)
        self.wait(for: [blocked], timeout: 10)
        try self.setBlocked(false)
        self.wait(for: [unblocked], timeout: 10)
        XCTAssertEqual(values.with { $0 }, [[], [self.authorId], []])
    }

    func testBlockedPolicyAndMediaRulesPersistWhileAllMutationPathsPreserveThem() throws {
        let rule = LocalMessageFilterRule(id: "voice", text: "", mediaType: .voiceMessage)
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideBlockedSenders(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: 123, excluded: true)))
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings(enabled: true, rules: [rule], excludedPeerIds: [123], hideBlockedSenders: true))
        XCTAssertTrue(try self.localArchiveValue(self.context.removeRule(id: rule.id)))
        XCTAssertTrue(try self.settings().hideBlockedSenders)
        XCTAssertTrue(try self.localArchiveValue(self.context.reset()))
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings())
    }

    func testUnknownOrOversizedAuthorWindowsFailOpenAndAccountsRemainIsolated() throws {
        try self.setBlocked(true)
        XCTAssertEqual(try self.localArchiveValue(self.context.locallyBlockedAuthors(Array(repeating: self.authorId, count: LocalMessageFilterLimits.maximumObservedAuthors + 1))), [])
        let channel = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(7599))
        XCTAssertEqual(try self.localArchiveValue(self.context.locallyBlockedAuthors([channel])), [])
        let other = try LocalArchivePostboxFixture(test: self, name: "blocked-filter-other")
        var otherContext: LocalMessageFilterContext! = LocalMessageFilterContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        XCTAssertEqual(try self.localArchiveValue(otherContext.locallyBlockedAuthors([self.authorId])), [])
        XCTAssertEqual(try self.localArchiveValue(self.context.locallyBlockedAuthors([self.authorId])), [self.authorId])
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideBlockedSenders(true)))
        guard case let .available(otherSettings) = try self.localArchiveValue(otherContext.settings) else { return XCTFail("other account settings unavailable") }
        XCTAssertFalse(otherSettings.hideBlockedSenders)
    }

    func testUnknownMediaKindPolicyIsReadOnlyWithoutLosingStoredBytes() throws {
        struct UnknownRule: Encodable {
            let id = "future-media"
            let text = ""
            let caseSensitive = false
            let mediaType: Int32 = 99
        }
        struct Policy: Encodable {
            let schemaVersion: Int32 = 2
            let enabled = true
            let rules = [UnknownRule()]
            let excludedPeerIds: [Int64] = []
        }
        let entry = try XCTUnwrap(PreferencesEntry(Policy()))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry) })
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(self.context.setHideBlockedSenders(false)))
        XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data }), entry.data)
    }

    func testLegacyVersionCannotSilentlyGainMediaOrBlockedSenderSemantics() throws {
        struct Legacy: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let rules: [LocalMessageFilterRule]
            let excludedPeerIds: [Int64] = []
            let hideBlockedSenders: Bool
        }
        for value in [
            Legacy(rules: [LocalMessageFilterRule(id: "media", text: "", mediaType: .photo)], hideBlockedSenders: false),
            Legacy(rules: [], hideBlockedSenders: true)
        ] {
            let entry = try XCTUnwrap(PreferencesEntry(value))
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry) })
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.setEnabled(false)))
            XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data }), entry.data)
        }
    }
}
