// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

/// Production account preferences, matcher and on-disk Postbox are exercised.
/// These tests do not establish authenticated UI behavior or notification filtering.
final class LocalMessageFilterSenderAndInverseTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private let chatId = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(7701))
    private let senderId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7702))

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "sender-inverse-filter")
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private var context: LocalMessageFilterContext { LocalMessageFilterContext(postbox: self.fixture.postbox) }

    private func settings(_ context: LocalMessageFilterContext? = nil) throws -> LocalMessageFilterSettings {
        guard case let .available(settings) = try self.localArchiveValue((context ?? self.context).settings) else {
            throw NSError(domain: "LocalMessageFilterSenderAndInverseTests", code: 1)
        }
        return settings
    }

    private func install(_ entry: PreferencesEntry) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry) })
    }

    private func bytes() throws -> Data? {
        return try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data })
    }

    func testHiddenSenderPersistsAcrossReopenAndEveryOtherMutationPreservesIt() throws {
        let channel = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(7703))
        let rule = LocalMessageFilterRule(id: "inverse", text: "allowed", isInverted: true)
        for id in [self.senderId, channel] {
            XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: id, hidden: true)))
        }
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideBlockedSenders(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.chatId.toInt64(), excluded: true)))
        try self.fixture.reopen()
        let expected = Set([self.senderId.toInt64(), channel.toInt64()])
        XCTAssertEqual(try self.settings().hiddenSenderIds, expected)
        XCTAssertEqual(try self.settings().rules, [rule])
        XCTAssertTrue(try self.localArchiveValue(self.context.removeRule(id: rule.id)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(false)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setHideBlockedSenders(false)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.chatId.toInt64(), excluded: false)))
        XCTAssertEqual(try self.settings().hiddenSenderIds, expected)
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: channel, hidden: false)))
        XCTAssertEqual(try self.settings().hiddenSenderIds, [self.senderId.toInt64()])
        XCTAssertTrue(try self.localArchiveValue(self.context.reset()))
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings())
    }

    func testHiddenSenderUsesKnownIdentityAndHonorsMasterAndGlobalChatExceptions() {
        let ids: Set<Int64> = [self.senderId.toInt64()]
        let disabled = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(hiddenSenderIds: ids))
        XCTAssertEqual(disabled.evaluate(text: "body", peerId: self.chatId.toInt64(), senderId: self.senderId.toInt64()), .noMatch)
        let enabled = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, hiddenSenderIds: ids))
        XCTAssertEqual(enabled.evaluate(text: "body", peerId: self.chatId.toInt64(), senderId: self.senderId.toInt64()), .matched(ruleId: "local-hidden-sender"))
        XCTAssertEqual(enabled.evaluate(text: "body", peerId: self.chatId.toInt64()), .noMatch)
        XCTAssertEqual(enabled.evaluate(text: "body", peerId: self.chatId.toInt64(), senderId: 999), .noMatch)
        let local = LocalMessageFilterRule(id: "chat", text: "body", peerId: self.chatId.toInt64())
        let excluded = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [local], excludedPeerIds: [self.chatId.toInt64()], hiddenSenderIds: ids))
        XCTAssertEqual(excluded.evaluate(text: "other", peerId: self.chatId.toInt64(), senderId: self.senderId.toInt64()), .noMatch)
        XCTAssertEqual(excluded.evaluate(text: "body", peerId: self.chatId.toInt64(), senderId: self.senderId.toInt64()), .matched(ruleId: local.id))
    }

    func testSenderHidingDoesNotMutateMessageOrServerBlockMetadataAndIsAccountIsolated() throws {
        let messageId = MessageId(peerId: self.chatId, namespace: Namespaces.Message.Cloud, id: 1)
        let original = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { transaction -> Message? in
            transaction.updatePeersInternal([localArchiveTestUser(self.senderId)], update: { _, updated in updated })
            let _ = transaction.addMessages([localArchiveTestMessage(messageId, text: "canonical", authorId: self.senderId)], location: .Random)
            return transaction.getMessage(messageId)
        }))
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: self.senderId, hidden: true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let current = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(messageId) }))
        XCTAssertEqual(current.text, original.text)
        XCTAssertEqual(current.stableVersion, original.stableVersion)
        XCTAssertEqual(try self.localArchiveValue(self.context.locallyBlockedAuthors([self.senderId])), [])
        let other = try LocalArchivePostboxFixture(test: self, name: "sender-filter-other")
        var otherContext: LocalMessageFilterContext! = LocalMessageFilterContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        XCTAssertEqual(try self.settings(otherContext).hiddenSenderIds, [])
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: try self.settings(otherContext)).evaluate(text: current.text, peerId: self.chatId.toInt64(), senderId: self.senderId.toInt64()), .noMatch)
        // Simulate canonical-history cleanup separately from applying a filter.
        // Preferences must not depend on retaining a message or media resource.
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.deleteMessages([messageId], forEachMedia: nil)
        })
        try self.fixture.reopen()
        XCTAssertNil(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(messageId) }))
        XCTAssertEqual(try self.settings().hiddenSenderIds, [self.senderId.toInt64()])
    }

    func testInvalidSenderNamespacesAndNonpositiveIdsAreRejectedWithoutWriting() throws {
        for id in [
            PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(1)),
            PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(1)),
            PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(0)),
            PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(-1))
        ] {
            XCTAssertFalse(try self.localArchiveValue(self.context.setSenderHidden(senderId: id, hidden: true)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setSenderHidden(senderId: id, hidden: false)))
        }
        XCTAssertNil(try self.bytes())
    }

    func testSenderLimitAllowsExistingIdAndRemovalButRejectsGrowth() throws {
        let ids = Set((1...LocalMessageFilterLimits.maximumHiddenSenders).map { PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(Int64($0))).toInt64() })
        try self.install(try XCTUnwrap(PreferencesEntry(LocalMessageFilterSettings(hiddenSenderIds: ids))))
        let first = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
        let extra = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9999))
        let original = try self.bytes()
        XCTAssertFalse(try self.localArchiveValue(self.context.setSenderHidden(senderId: extra, hidden: true)))
        XCTAssertEqual(try self.bytes(), original)
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: first, hidden: true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: first, hidden: false)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: extra, hidden: true)))
        XCTAssertEqual(try self.settings().hiddenSenderIds.count, LocalMessageFilterLimits.maximumHiddenSenders)
    }

    func testMalformedEncodedSenderIdsAreReadOnlyWithoutTriggeringPeerIdAssertions() throws {
        let group = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(1)).toInt64()
        for value in [Int64.min, -1, 0, Int64.max, group] {
            let settings = LocalMessageFilterSettings(enabled: true, hiddenSenderIds: [value])
            let entry = try XCTUnwrap(PreferencesEntry(settings))
            try self.install(entry)
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertEqual(LocalMessageFilterEvaluator(settings: settings).evaluate(text: "body", peerId: 1, senderId: value), .unavailable)
            XCTAssertFalse(try self.localArchiveValue(self.context.setSenderHidden(senderId: self.senderId, hidden: true)))
            XCTAssertEqual(try self.bytes(), entry.data)
        }
    }

    func testMalformedRuleAndExcludedChatScopesAreRejectedBeforeNativePeerLookup() throws {
        struct Policy: Encodable {
            let schemaVersion: Int32
            let enabled = true
            let rules: [LocalMessageFilterRule]
            let excludedPeerIds: [Int64]
        }
        for value in [Int64.min, -1, Int64.max, 0x0000000700000000] {
            let rule = LocalMessageFilterRule(id: "malformed-scope", text: "body", peerId: value)
            XCTAssertFalse(rule.isSupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.upsertRule(rule)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setChatExcluded(peerId: value, excluded: true)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setChatExcluded(peerId: value, excluded: false)))
            for version in [Int32(1), Int32(2), Int32(3)] {
                for policy in [
                    Policy(schemaVersion: version, rules: [rule], excludedPeerIds: []),
                    Policy(schemaVersion: version, rules: [], excludedPeerIds: [value])
                ] {
                    let entry = try XCTUnwrap(PreferencesEntry(policy))
                    try self.install(entry)
                    XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
                    XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
                    XCTAssertEqual(try self.bytes(), entry.data)
                }
            }
            // Restore a readable policy to exercise the next invalid mutation.
            try self.install(try XCTUnwrap(PreferencesEntry(LocalMessageFilterSettings())))
        }
    }

    func testSafelyEncodedScopeNamespacesRetainRoundTripIdentity() throws {
        let values: [Int64] = [
            0, self.chatId.toInt64(), self.senderId.toInt64(),
            PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00ffffffffffffff)).toInt64(),
            PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(-1)).toInt64(),
            0x0000000400000001, 0x0000000500000001, 0x0000000600000001,
            0x0000000700000001, 0x7fffffff00000000
        ]
        for (index, value) in values.enumerated() {
            XCTAssertTrue(telegramCorePeerIdIsSafelyDecodable(value))
            // Mirrors the native settings peer-name conversion after validation.
            XCTAssertEqual(EnginePeer.Id(value).toInt64(), value)
            let rule = LocalMessageFilterRule(id: "safe-\(index)", text: "body", peerId: value)
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
            XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: value, excluded: true)))
        }
        try self.fixture.reopen()
        XCTAssertEqual(Set(try self.settings().rules.compactMap(\.peerId)), Set(values))
        XCTAssertEqual(try self.settings().excludedPeerIds, Set(values))
    }

    func testInverseLiteralUnicodeAndCaseTruthTable() {
        let cases: [(String, String, Bool, Bool)] = [
            ("OFFER", "offer", false, false), ("OFFER", "offer", true, true),
            ("café", "cafe\u{301}", true, false), ("🙂", "🙂", true, false),
            ("different", "🙂", false, true), ("new\nline", "new line", false, true),
            ("spaces  matter", "spaces matter", false, true)
        ]
        for (text, pattern, sensitive, hides) in cases {
            let rule = LocalMessageFilterRule(id: "inverse", text: pattern, caseSensitive: sensitive, isInverted: true)
            let evaluator = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [rule]))
            XCTAssertEqual(evaluator.evaluate(text: text, peerId: 1), hides ? .matched(ruleId: rule.id) : .noMatch)
        }
    }

    func testInverseExpressionsKeepEmptyOversizedAndWorkLimitedTextVisible() {
        let rule = LocalMessageFilterRule(id: "inverse", text: "^allowed[0-9]+$", matchingMode: .regularExpression, isInverted: true)
        let evaluator = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [rule]))
        XCTAssertEqual(evaluator.evaluate(text: "allowed12", peerId: 1), .noMatch)
        XCTAssertEqual(evaluator.evaluate(text: "other", peerId: 1), .matched(ruleId: rule.id))
        XCTAssertEqual(evaluator.evaluate(text: "", peerId: 1, mediaType: .photo), .noMatch)
        XCTAssertEqual(evaluator.evaluate(text: String(repeating: "x", count: LocalMessageFilterLimits.maximumInputUTF16Length + 1), peerId: 1), .skippedOversize)
        let literal = LocalMessageFilterRule(id: "literal-inverse", text: "allowed", isInverted: true)
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [literal])).evaluate(text: String(repeating: "🙂", count: 16385), peerId: 1), .skippedOversize)
        let expensive = LocalMessageFilterRule(id: "limited", text: String(repeating: "a*", count: 100) + "b$", matchingMode: .regularExpression, isInverted: true)
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [expensive])).evaluate(text: String(repeating: "a", count: 10_000), peerId: 1), .skippedWorkLimit)
    }

    func testInverseMediaMatchesOnlyOtherRecognizedMediaTypes() {
        for selected in LocalMessageFilterMediaType.allCases {
            let rule = LocalMessageFilterRule(id: "media", text: "", mediaType: selected, isInverted: true)
            let evaluator = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [rule]))
            XCTAssertEqual(evaluator.evaluate(text: "ordinary text", peerId: 1), .noMatch)
            XCTAssertEqual(evaluator.evaluate(text: "", peerId: 1), .noMatch)
            for received in LocalMessageFilterMediaType.allCases {
                XCTAssertEqual(evaluator.evaluate(text: "", peerId: 1, mediaType: received), received == selected ? .noMatch : .matched(ruleId: rule.id))
            }
        }
    }

    func testInverseRulesRemainScopedOrPredicatesRatherThanGlobalAllowlists() {
        let global = LocalMessageFilterRule(id: "global", text: "allow", isInverted: true)
        let local = LocalMessageFilterRule(id: "local", text: "special", peerId: self.chatId.toInt64(), isInverted: true)
        let ordinary = LocalMessageFilterRule(id: "ordinary", text: "allow")
        let excluded = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [global, local], excludedPeerIds: [self.chatId.toInt64()]))
        XCTAssertEqual(excluded.evaluate(text: "special", peerId: self.chatId.toInt64()), .noMatch)
        XCTAssertEqual(excluded.evaluate(text: "other", peerId: self.chatId.toInt64()), .matched(ruleId: local.id))
        let multiple = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [global, ordinary]))
        XCTAssertEqual(multiple.evaluate(text: "allow", peerId: 1), .matched(ruleId: ordinary.id))
        XCTAssertEqual(multiple.evaluate(text: "other", peerId: 1), .matched(ruleId: global.id))
    }

    func testVersionTwoPolicyIsNotRewrittenUntilExplicitMutation() throws {
        struct Policy: Encodable {
            let schemaVersion: Int32 = 2
            let enabled = true
            let rules = [LocalMessageFilterRule(id: "old-expression", text: "a.*b", matchingMode: .regularExpression)]
            let excludedPeerIds: [Int64] = [123]
            let hideBlockedSenders = true
        }
        let entry = try XCTUnwrap(PreferencesEntry(Policy()))
        try self.install(entry)
        try self.fixture.reopen()
        let settings = try self.settings()
        XCTAssertEqual(settings.schemaVersion, 3)
        XCTAssertTrue(settings.hiddenSenderIds.isEmpty)
        XCTAssertFalse(settings.rules[0].isInverted)
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: settings).evaluate(text: "aZZb", peerId: 1), .matched(ruleId: "old-expression"))
        XCTAssertEqual(try self.bytes(), entry.data)
        XCTAssertTrue(try self.localArchiveValue(self.context.setSenderHidden(senderId: self.senderId, hidden: true)))
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings().hiddenSenderIds, [self.senderId.toInt64()])
        XCTAssertEqual(try self.settings().rules, settings.rules)
        XCTAssertTrue(try self.settings().hideBlockedSenders)
    }

    func testFutureAndSmuggledLegacyPoliciesStayReadOnlyIncludingSenderReset() throws {
        struct Policy: Encodable {
            let schemaVersion: Int32
            let enabled = true
            let rules: [LocalMessageFilterRule]
            let excludedPeerIds: [Int64] = []
            let hiddenSenderIds: [Int64]
        }
        for policy in [
            Policy(schemaVersion: 99, rules: [], hiddenSenderIds: [self.senderId.toInt64()]),
            Policy(schemaVersion: 2, rules: [], hiddenSenderIds: [self.senderId.toInt64()]),
            Policy(schemaVersion: 2, rules: [LocalMessageFilterRule(text: "allow", isInverted: true)], hiddenSenderIds: []),
            Policy(schemaVersion: 1, rules: [LocalMessageFilterRule(text: "allow", isInverted: true)], hiddenSenderIds: [])
        ] {
            let entry = try XCTUnwrap(PreferencesEntry(policy))
            try self.install(entry)
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.setSenderHidden(senderId: self.senderId, hidden: false)))
            XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
            XCTAssertEqual(try self.bytes(), entry.data)
        }
    }

    func testLargestVersionThreePolicyFitsBoundedPersistenceBudget() throws {
        let rules = (0..<LocalMessageFilterLimits.maximumRules).map { index in
            LocalMessageFilterRule(id: String(repeating: "a", count: 61) + String(format: "%03d", index), text: String(repeating: "界", count: LocalMessageFilterLimits.maximumLiteralUTF16Length), peerId: PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00ffffffffffffff - Int64(index))).toInt64(), isInverted: true)
        }
        let excluded = Set((0..<LocalMessageFilterLimits.maximumExcludedPeers).map { PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(0x00ffffffffffffff - Int64($0))).toInt64() })
        let hidden = Set((1...LocalMessageFilterLimits.maximumHiddenSenders).map { PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(1_000_000_000_000 + Int64($0))).toInt64() })
        let settings = LocalMessageFilterSettings(enabled: true, rules: rules, excludedPeerIds: excluded, hideBlockedSenders: true, hiddenSenderIds: hidden)
        XCTAssertTrue(validLocalMessageFilterSettings(settings))
        let entry = try XCTUnwrap(PreferencesEntry(settings))
        XCTAssertLessThanOrEqual(entry.data.count, LocalMessageFilterLimits.maximumEncodedSettingsBytes)
        try self.install(entry)
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings(), settings)
    }
}
