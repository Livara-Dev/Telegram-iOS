// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

/// Exercises the account facade, production preference codec and evaluator with
/// real on-disk Postbox messages. UI presentation and Telegram delivery are not
/// simulated by these tests.
final class LocalMessageFilterTests: XCTestCase {
    private let chatId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
    private let otherChatId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1002))
    private var fixture: LocalArchivePostboxFixture!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "message-filter")
        let peers = [localArchiveTestUser(self.chatId), localArchiveTestUser(self.otherChatId)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, current in current })
        })
    }

    override func tearDownWithError() throws {
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private var context: LocalMessageFilterContext {
        return LocalMessageFilterContext(postbox: self.fixture.postbox)
    }

    private func settings(_ context: LocalMessageFilterContext? = nil) throws -> LocalMessageFilterSettings {
        let state = try self.localArchiveValue((context ?? self.context).settings)
        guard case let .available(value) = state else {
            XCTFail("expected readable filter settings")
            throw NSError(domain: "LocalMessageFilterTests", code: 1)
        }
        return value
    }

    private func install(_ entry: PreferencesEntry) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry)
        })
    }

    private func storedBytes() throws -> Data? {
        return try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data
        })
    }

    private func store(_ text: String, id: Int32 = 1, peerId: PeerId? = nil) throws -> Message {
        let id = MessageId(peerId: peerId ?? self.chatId, namespace: Namespaces.Message.Cloud, id: id)
        return try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: text)], location: .Random)
            return transaction.getMessage(id)
        }))
    }

    private func evaluate(_ message: Message, context: LocalMessageFilterContext? = nil) throws -> LocalMessageFilterEvaluation {
        return LocalMessageFilterEvaluator(settings: try self.settings(context)).evaluate(text: message.text, peerId: message.id.peerId.toInt64())
    }

    func testDefaultSettingsDoNotFilterCanonicalMessages() throws {
        let message = try self.store("advertisement")
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings())
        XCTAssertEqual(try self.evaluate(message), .noMatch)
        XCTAssertNil(try self.storedBytes())
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(message.id)?.text }), message.text)
    }

    func testFacadePersistsRulesAndFixedWidthPeerIdsAcrossReopen() throws {
        let localPeer = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00fffffffffffffe)).toInt64()
        let firstExcluded = PeerId(namespace: Namespaces.Peer.CloudGroup, id: ._internalFromInt64Value(4_294_967_300)).toInt64()
        let secondExcluded = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(4_294_967_301)).toInt64()
        let global = LocalMessageFilterRule(id: "global", text: "offer", caseSensitive: true)
        let local = LocalMessageFilterRule(id: "local", text: "🙂", peerId: localPeer)
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(global)))
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(local)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: firstExcluded, excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: secondExcluded, excluded: true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let expected = LocalMessageFilterSettings(enabled: true, rules: [global, local], excludedPeerIds: [firstExcluded, secondExcluded])
        XCTAssertEqual(try self.settings(), expected)
        let entry = try XCTUnwrap(PreferencesEntry(expected))
        XCTAssertEqual(entry.get(LocalMessageFilterSettings.self), expected)
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings(), expected)
    }

    func testUpsertReplacesRuleInPlaceAndRemovalPreservesOtherRules() throws {
        let first = LocalMessageFilterRule(id: "first", text: "offer")
        let second = LocalMessageFilterRule(id: "second", text: "offer")
        let replacement = LocalMessageFilterRule(id: "first", text: "sale", caseSensitive: true, peerId: self.chatId.toInt64())
        for rule in [first, second, replacement] {
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        }
        XCTAssertEqual(try self.settings().rules, [replacement, second])
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertEqual(try self.evaluate(self.store("sale offer")), .matched(ruleId: "first"))
        XCTAssertTrue(try self.localArchiveValue(self.context.removeRule(id: "first")))
        XCTAssertEqual(try self.settings().rules, [second])
        XCTAssertEqual(try self.evaluate(self.store("sale offer", id: 2)), .matched(ruleId: "second"))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(false)))
        XCTAssertEqual(try self.evaluate(self.store("offer", id: 3)), .noMatch)
        XCTAssertEqual(try self.settings().rules, [second])
    }

    func testGlobalExclusionDoesNotDisableExplicitPerChatRules() throws {
        let global = LocalMessageFilterRule(id: "global", text: "offer")
        let local = LocalMessageFilterRule(id: "local", text: "offer", peerId: self.chatId.toInt64())
        for rule in [global, local] {
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        }
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let first = try self.store("offer")
        let second = try self.store("offer", peerId: self.otherChatId)
        XCTAssertEqual(try self.evaluate(first), .matched(ruleId: "global"))
        XCTAssertEqual(try self.evaluate(second), .matched(ruleId: "global"))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.chatId.toInt64(), excluded: true)))
        XCTAssertEqual(try self.evaluate(first), .matched(ruleId: "local"))
        XCTAssertEqual(try self.evaluate(second), .matched(ruleId: "global"))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.otherChatId.toInt64(), excluded: true)))
        XCTAssertEqual(try self.evaluate(second), .noMatch)
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.chatId.toInt64(), excluded: false)))
        XCTAssertEqual(try self.evaluate(first), .matched(ruleId: "global"))
    }

    func testCaseAndCanonicalUnicodeMatchingUseLiteralSubstrings() throws {
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let cases: [(String, String, Bool, Bool)] = [
            ("offer", "An OFFER today", true, false),
            ("offer", "An OFFER today", false, true),
            ("CAFÉ", "A café nearby", false, true),
            ("café", "A cafe\u{301} nearby", true, true),
            ("Ж", "ж", false, true),
            ("🙂", "hello 🙂", true, true),
            ("[a-z]+.*", "abc123", false, false),
            ("[a-z]+.*", "literal [a-z]+.* text", false, true),
            (" offer ", "offer", false, false),
            (" offer ", "an offer today", false, true)
        ]
        for (index, item) in cases.enumerated() {
            let (pattern, text, sensitive, matches) = item
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "rule", text: pattern, caseSensitive: sensitive))))
            let message = try self.store(text, id: Int32(index + 1))
            XCTAssertEqual(try self.evaluate(message), matches ? .matched(ruleId: "rule") : .noMatch, "case \(index)")
        }
    }

    func testInvalidRuleMutationsAreRejectedWithoutReplacingValidSettings() throws {
        let valid = LocalMessageFilterRule(id: "valid", text: "offer")
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(valid)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let before = try self.storedBytes()
        let invalid = [
            LocalMessageFilterRule(id: "empty", text: ""),
            LocalMessageFilterRule(id: "long", text: String(repeating: "a", count: 257)),
            LocalMessageFilterRule(id: "emoji-long", text: String(repeating: "🙂", count: 129)),
            LocalMessageFilterRule(id: "", text: "offer"),
            LocalMessageFilterRule(id: "bad\0id", text: "offer"),
            LocalMessageFilterRule(id: String(repeating: "a", count: 65), text: "offer"),
            LocalMessageFilterRule(id: String(repeating: "🙂", count: 17), text: "offer"),
            LocalMessageFilterRule(id: "valid", text: "")
        ]
        for rule in invalid {
            XCTAssertFalse(try self.localArchiveValue(self.context.upsertRule(rule)))
            XCTAssertEqual(try self.storedBytes(), before)
        }
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings(enabled: true, rules: [valid]))
        XCTAssertEqual(try self.evaluate(self.store("offer")), .matched(ruleId: "valid"))
    }

    func testUTF16PatternAndUTF8IdentifierLimitsAcceptExactBoundary() throws {
        let id = String(repeating: "🙂", count: 16)
        let text = String(repeating: "🙂", count: 128)
        let rule = LocalMessageFilterRule(id: id, text: text, caseSensitive: true)
        XCTAssertEqual(id.utf8.count, 64)
        XCTAssertEqual(text.utf16.count, 256)
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertEqual(try self.evaluate(self.store(text)), .matched(ruleId: id))
        // Whitespace and NUL are literal text, not empty rules or regex syntax.
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "space", text: " "))))
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "nul", text: "\0"))))
        XCTAssertEqual(try self.evaluate(self.store("a\0b", id: 2)), .matched(ruleId: "nul"))
    }

    func testRuleCountLimitRejectsNewRuleButAllowsReplacementAndRemoval() throws {
        let rules = (0..<64).map { LocalMessageFilterRule(id: "r\($0)", text: "pattern-\($0)") }
        try self.install(try XCTUnwrap(PreferencesEntry(LocalMessageFilterSettings(enabled: true, rules: rules))))
        let before = try self.storedBytes()
        XCTAssertFalse(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "overflow", text: "new"))))
        XCTAssertEqual(try self.storedBytes(), before)
        let replacement = LocalMessageFilterRule(id: "r63", text: "replacement")
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(replacement)))
        XCTAssertEqual(try self.settings().rules.count, 64)
        XCTAssertEqual(try self.settings().rules.last, replacement)
        XCTAssertTrue(try self.localArchiveValue(self.context.removeRule(id: "r0")))
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "new", text: "new"))))
        XCTAssertEqual(try self.settings().rules.count, 64)
    }

    func testExclusionLimitRejectsNewEntryButAllowsRemovalAndExistingEntry() throws {
        let exclusions = Set((0..<4096).map { Int64($0) })
        try self.install(try XCTUnwrap(PreferencesEntry(LocalMessageFilterSettings(excludedPeerIds: exclusions))))
        let before = try self.storedBytes()
        XCTAssertFalse(try self.localArchiveValue(self.context.setChatExcluded(peerId: 4096, excluded: true)))
        XCTAssertEqual(try self.storedBytes(), before)
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: 0, excluded: true)))
        XCTAssertEqual(try self.settings().excludedPeerIds, exclusions)
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: 0, excluded: false)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setChatExcluded(peerId: 4096, excluded: true)))
        XCTAssertEqual(try self.settings().excludedPeerIds.count, 4096)
        XCTAssertFalse(try self.settings().excludedPeerIds.contains(0))
        XCTAssertTrue(try self.settings().excludedPeerIds.contains(4096))
    }

    func testOversizedMessagesAreSkippedWithoutPartialPrefixMatching() throws {
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "rule", text: "x"))))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        XCTAssertEqual(try self.evaluate(self.store(String(repeating: "x", count: 32768))), .matched(ruleId: "rule"))
        XCTAssertEqual(try self.evaluate(self.store(String(repeating: "x", count: 32769), id: 2)), .skippedOversize)
        XCTAssertEqual(try self.evaluate(self.store("x" + String(repeating: "🙂", count: 16384), id: 3)), .skippedOversize)
        XCTAssertEqual(try self.evaluate(self.store("", id: 4)), .noMatch)
    }

    private func assertUnsupported(_ entry: PreferencesEntry) throws {
        try self.install(entry)
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(self.context.setEnabled(false)))
        XCTAssertFalse(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "new", text: "offer"))))
        XCTAssertFalse(try self.localArchiveValue(self.context.removeRule(id: "old")))
        XCTAssertFalse(try self.localArchiveValue(self.context.setChatExcluded(peerId: self.chatId.toInt64(), excluded: true)))
        XCTAssertEqual(try self.storedBytes(), entry.data)
        let message = try self.store("offer")
        XCTAssertEqual(try self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(message.id)?.text }), "offer")
    }

    func testFutureSettingsRemainReadOnlyAcrossReopen() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(LocalMessageFilterSettings())) as? [String: Any])
        object["schemaVersion"] = 99
        let future = try JSONDecoder().decode(LocalMessageFilterSettings.self, from: JSONSerialization.data(withJSONObject: object))
        try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(future)))
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: future).evaluate(text: "offer", peerId: self.chatId.toInt64()), .unavailable)
    }

    func testMalformedSettingsRemainReadOnlyAcrossReopen() throws {
        struct IncompleteSettings: Encodable { let schemaVersion: Int32 = 1 }
        try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(IncompleteSettings())))
    }

    func testInvalidDecodedSettingsAreUnavailableRatherThanSilentlyDroppingRules() throws {
        let invalid = [
            LocalMessageFilterSettings(enabled: true, rules: [LocalMessageFilterRule(id: "old", text: "")]),
            LocalMessageFilterSettings(enabled: true, rules: [LocalMessageFilterRule(id: "old", text: "one"), LocalMessageFilterRule(id: "old", text: "two")]),
            LocalMessageFilterSettings(enabled: true, rules: (0..<65).map { LocalMessageFilterRule(id: "r\($0)", text: "offer") }),
            LocalMessageFilterSettings(enabled: true, excludedPeerIds: Set((0..<4097).map { Int64($0) }))
        ]
        for settings in invalid {
            try self.assertUnsupported(try XCTUnwrap(PreferencesEntry(settings)))
            XCTAssertEqual(LocalMessageFilterEvaluator(settings: settings).evaluate(text: "offer", peerId: self.chatId.toInt64()), .unavailable)
        }
    }

    func testOversizedPreferenceDocumentIsPreservedWithoutApplyingItsRules() throws {
        struct PaddedSettings: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let rules = [LocalMessageFilterRule(id: "old", text: "offer")]
            let excludedPeerIds: [Int64] = []
            let padding = String(repeating: "x", count: 128 * 1024)
        }
        let entry = try XCTUnwrap(PreferencesEntry(PaddedSettings()))
        XCTAssertGreaterThan(entry.data.count, 128 * 1024)
        // The known fields form a valid policy. The stored document's byte bound
        // must reject it before decoding, even when unknown fields are ignorable.
        XCTAssertEqual(entry.get(LocalMessageFilterSettings.self)?.rules.map(\.id), ["old"])
        try self.assertUnsupported(entry)
    }

    func testLargestValidPolicyFitsPersistenceBudget() throws {
        let rules = (0..<64).map { index in
            LocalMessageFilterRule(id: String(repeating: "a", count: 61) + String(format: "%03d", index),
                text: String(repeating: "界", count: 256), peerId: PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(0x00ffffffffffffff - Int64(index))).toInt64())
        }
        let excluded = Set((0..<4096).map { PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(0x00ffffffffffffff - Int64($0))).toInt64() })
        let value = LocalMessageFilterSettings(enabled: true, rules: rules, excludedPeerIds: excluded)
        let entry = try XCTUnwrap(PreferencesEntry(value))
        XCTAssertLessThanOrEqual(entry.data.count, 128 * 1024)
        try self.install(entry)
        XCTAssertEqual(try self.settings(), value)
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(false)))
        try self.fixture.reopen()
        XCTAssertEqual(try self.settings(), LocalMessageFilterSettings(enabled: false, rules: rules, excludedPeerIds: excluded))
    }

    func testEditedMessagesAreReevaluatedWithoutMutatingCanonicalHistory() throws {
        let rule = LocalMessageFilterRule(id: "rule", text: "offer")
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let original = try self.store("offer")
        let evaluator = LocalMessageFilterEvaluator(settings: try self.settings())
        XCTAssertEqual(evaluator.evaluate(text: original.text, peerId: self.chatId.toInt64()), .matched(ruleId: "rule"))
        let replacement = localArchiveTestMessage(original.id, text: "ordinary conversation")
        let edited = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updateMessage(original.id) { _ in .update(replacement) }
            return transaction.getMessage(original.id)
        }))
        XCTAssertEqual(evaluator.evaluate(text: edited.text, peerId: self.chatId.toInt64()), .noMatch)
        XCTAssertEqual(evaluator.evaluate(text: original.text, peerId: self.chatId.toInt64()), .matched(ruleId: "rule"))
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(LocalMessageFilterRule(id: "rule", text: "ordinary"))))
        XCTAssertEqual(try self.evaluate(edited), .matched(ruleId: "rule"))
        try self.fixture.reopen()
        let stored = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction { $0.getMessage(original.id) }))
        XCTAssertEqual(stored.text, replacement.text)
        XCTAssertEqual(stored.id, original.id)
        XCTAssertEqual(stored.timestamp, original.timestamp)
        XCTAssertEqual(stored.flags, edited.flags)
        XCTAssertEqual(stored.tags, edited.tags)
        XCTAssertTrue(stored.media.isEmpty)
        XCTAssertTrue(stored.attributes.isEmpty)
        XCTAssertEqual(try self.evaluate(stored), .matched(ruleId: "rule"))
    }

    func testSettingsSignalEmitsPersistedPolicyAndCommittedUpdates() throws {
        let rule = LocalMessageFilterRule(id: "rule", text: "offer")
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        try self.fixture.reopen()
        let initial = expectation(description: "first value is persisted enabled policy")
        let updated = expectation(description: "observer sees committed disable")
        let states = Atomic<[LocalMessageFilterSettingsState]>(value: [])
        let disposable = (self.context.settings |> take(2)).start(next: { state in
            let index = states.modify { current in
                var values = current
                values.append(state)
                return values
            }.count
            if index == 1 { initial.fulfill() } else { updated.fulfill() }
        })
        defer { disposable.dispose() }
        wait(for: [initial], timeout: 15)
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(false)))
        wait(for: [updated], timeout: 15)
        XCTAssertEqual(states.with { $0 }, [
            .available(LocalMessageFilterSettings(enabled: true, rules: [rule])),
            .available(LocalMessageFilterSettings(enabled: false, rules: [rule]))
        ])
    }

    func testTwoAccountPostboxesKeepRulesAndMessagesIndependent() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "message-filter-other-account")
        var otherContext: LocalMessageFilterContext! = LocalMessageFilterContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        let rule = LocalMessageFilterRule(id: "rule", text: "offer")
        XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        let message = try self.store("offer")
        XCTAssertEqual(try self.evaluate(message), .matched(ruleId: "rule"))
        XCTAssertEqual(try self.evaluate(message, context: otherContext), .noMatch)
        XCTAssertTrue(try self.localArchiveValue(otherContext.upsertRule(LocalMessageFilterRule(id: "rule", text: "different"))))
        XCTAssertTrue(try self.localArchiveValue(otherContext.setEnabled(true)))
        XCTAssertTrue(try self.localArchiveValue(otherContext.setChatExcluded(peerId: self.chatId.toInt64(), excluded: true)))
        XCTAssertEqual(try self.settings().rules, [rule])
        XCTAssertTrue(try self.settings().excludedPeerIds.isEmpty)
        XCTAssertEqual(try self.evaluate(message), .matched(ruleId: "rule"))
        XCTAssertEqual(try self.evaluate(message, context: otherContext), .noMatch)
        XCTAssertNil(try self.localArchiveValue(other.postbox.transaction { $0.getMessage(message.id) }))
        otherContext = nil
        try other.reopen()
        XCTAssertEqual(try self.settings(LocalMessageFilterContext(postbox: other.postbox)).rules.map(\.text), ["different"])
        XCTAssertEqual(try self.settings().rules, [rule])
    }
}
