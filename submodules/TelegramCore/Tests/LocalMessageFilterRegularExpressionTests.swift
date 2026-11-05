// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

final class LocalMessageFilterRegularExpressionTests: XCTestCase {
    private func result(_ pattern: String, _ text: String, caseSensitive: Bool = false) throws -> LocalMessageFilterRegularExpression.Result {
        let expression = try XCTUnwrap(LocalMessageFilterRegularExpression(pattern: pattern, caseSensitive: caseSensitive))
        var work = LocalMessageFilterLimits.maximumRegularExpressionSteps
        return expression.evaluate(text, remainingWork: &work)
    }

    func testAnchorsClassesRepetitionsAndSubstringSearch() throws {
        let cases: [(String, String, Bool)] = [
            ("offer", "special OFFER today", true),
            ("^offer[0-9]+$", "offer123", true),
            ("^offer[0-9]+$", "offer", false),
            ("^offer[0-9]+$", "xoffer123", false),
            ("^colou?r$", "color", true),
            ("^colou?r$", "colour", true),
            ("^colou?r$", "colouur", false),
            ("^a*b$", "b", true),
            ("^a*b$", "aaab", true),
            ("^a+b$", "b", false),
            ("^a+b$", "aab", true),
            ("^a.b$", "a\nb", false),
            ("^a\\sb$", "a\nb", true),
            ("^[^0-9]+$", "letters", true),
            ("^[^0-9]+$", "letters2", false),
            ("^\\w+\\s\\d+$", "abc_9 123", true),
            ("^\\w+$", "é", false),
            ("^\\D+$", "abc", true),
            ("^\\D+$", "abc1", false),
            ("^\\S+$", "a b", false),
            ("^\\W+$", "!?", true),
            ("^[a-z\\d_-]+$", "abc_19-", true),
            ("^price\\.\\$\\[x\\]$", "price.$[x]", true),
            ("^a\\tb$", "a\tb", true),
            ("^a$", "a\n", false)
        ]
        for (pattern, text, expected) in cases {
            XCTAssertEqual(try self.result(pattern, text), expected ? .matched : .noMatch, pattern + " / " + text)
        }
    }

    func testFixedLocaleCaseAndCanonicalUnicodeEquivalence() throws {
        XCTAssertEqual(try self.result("^ÄBC$", "äbc"), .matched)
        XCTAssertEqual(try self.result("^ÄBC$", "äbc", caseSensitive: true), .noMatch)
        XCTAssertEqual(try self.result("^café$", "cafe\u{301}", caseSensitive: true), .matched)
        XCTAssertEqual(try self.result("^[A-Z]+$", "abc"), .matched)
        XCTAssertEqual(try self.result("^[A-Z]+$", "abc", caseSensitive: true), .noMatch)
    }

    func testUnsupportedOrMalformedSyntaxIsRejectedRatherThanReinterpreted() {
        for pattern in ["", "^$", "(a+)+$", "a|b", "a{2}", "(?=a)", "(?<=a)", "\\1", "\\p{L}", "\\bword\\b", "a**", "a+?", "[]", "[z-a]", "[\\d-z]", "[abc", "trailing\\", "a^b", "a$b", String(repeating: "a", count: 257)] {
            XCTAssertNil(LocalMessageFilterRegularExpression(pattern: pattern, caseSensitive: false), pattern)
            XCTAssertFalse(LocalMessageFilterRule(text: pattern, matchingMode: .regularExpression).isSupported, pattern)
        }
    }

    func testAdversarialRegularLanguageConsumesBoundedWorkWithoutBacktracking() throws {
        let expression = try XCTUnwrap(LocalMessageFilterRegularExpression(pattern: String(repeating: "a*", count: 100) + "b$", caseSensitive: true))
        var work = 20_000
        XCTAssertEqual(expression.evaluate(String(repeating: "a", count: 1000), remainingWork: &work), .workLimit)
        XCTAssertEqual(work, 0)
        XCTAssertEqual(expression.evaluate("b", remainingWork: &work), .workLimit)
        var sufficient = 1000
        XCTAssertEqual(expression.evaluate("b", remainingWork: &sufficient), .matched)
    }

    func testCharacterClassComparisonsAlsoConsumeTheSharedBudget() throws {
        let expression = try XCTUnwrap(LocalMessageFilterRegularExpression(pattern: "^[abcdefghijklmnopqrstuvwx]+$", caseSensitive: true))
        var work = 5
        XCTAssertEqual(expression.evaluate("x", remainingWork: &work), .workLimit)
        XCTAssertEqual(work, 0)
    }

    func testActualPostboxRulesReopenEvaluateAndLeaveCanonicalTextUnchanged() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "safe-expression-filter")
        var context: LocalMessageFilterContext! = LocalMessageFilterContext(postbox: fixture.postbox)
        defer { context = nil; try? fixture.destroy() }
        let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7401))
        let otherPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(7402))
        let rule = LocalMessageFilterRule(id: "expression", text: "^sale[0-9]+$", peerId: peerId.toInt64(), matchingMode: .regularExpression)
        XCTAssertTrue(try self.localArchiveValue(context.upsertRule(rule)))
        XCTAssertTrue(try self.localArchiveValue(context.setEnabled(true)))
        context = nil
        try fixture.reopen()
        context = LocalMessageFilterContext(postbox: fixture.postbox)
        guard case let .available(settings) = try self.localArchiveValue(context.settings) else { return XCTFail("policy unavailable") }
        XCTAssertEqual(settings.rules, [rule])
        let id = MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 1)
        let original = try XCTUnwrap(self.localArchiveValue(fixture.postbox.transaction { transaction -> Message? in
            transaction.updatePeersInternal([localArchiveTestUser(peerId)], update: { _, updated in updated })
            let _ = transaction.addMessages([localArchiveTestMessage(id, text: "SALE42")], location: .Random)
            return transaction.getMessage(id)
        }))
        let evaluator = LocalMessageFilterEvaluator(settings: settings)
        XCTAssertEqual(evaluator.evaluate(text: original.text, peerId: peerId.toInt64()), .matched(ruleId: rule.id))
        XCTAssertEqual(evaluator.evaluate(text: original.text, peerId: otherPeerId.toInt64()), .noMatch)
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id)?.text }), original.text)
        XCTAssertFalse(try self.localArchiveValue(context.upsertRule(LocalMessageFilterRule(id: rule.id, text: "(a+)+", matchingMode: .regularExpression))))
        guard case let .available(unchanged) = try self.localArchiveValue(context.settings) else { return XCTFail("policy unavailable") }
        XCTAssertEqual(unchanged.rules, [rule])
        XCTAssertTrue(try self.localArchiveValue(context.reset()))
        XCTAssertEqual(try self.localArchiveValue(context.settings), .available(LocalMessageFilterSettings()))
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getMessage(id)?.text }), original.text)
    }

    func testLegacyLiteralPolicyMigratesOnlyOnAnExplicitMutation() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "filter-policy-v1")
        var context: LocalMessageFilterContext!
        defer { context = nil; try? fixture.destroy() }
        struct LegacyPolicy: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let rules: [LocalMessageFilterRule]
            let excludedPeerIds: [Int64] = [123]
        }
        let oldRule = LocalMessageFilterRule(id: "legacy", text: "a.*b")
        let entry = try XCTUnwrap(PreferencesEntry(LegacyPolicy(rules: [oldRule])))
        let _ = try self.localArchiveValue(fixture.postbox.transaction { $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry) })
        context = LocalMessageFilterContext(postbox: fixture.postbox)
        guard case let .available(settings) = try self.localArchiveValue(context.settings) else { return XCTFail("legacy policy unavailable") }
        XCTAssertEqual(settings.rules, [oldRule])
        XCTAssertEqual(settings.schemaVersion, 3)
        XCTAssertEqual(LocalMessageFilterEvaluator(settings: settings).evaluate(text: "aZZb", peerId: 1), .noMatch)
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data }), entry.data)
        XCTAssertTrue(try self.localArchiveValue(context.setEnabled(false)))
        context = nil
        try fixture.reopen()
        guard case let .available(updated) = try self.localArchiveValue(LocalMessageFilterContext(postbox: fixture.postbox).settings) else { return XCTFail("updated policy unavailable") }
        XCTAssertEqual(updated.rules, [oldRule])
        XCTAssertEqual(updated.schemaVersion, 3)
        XCTAssertFalse(updated.enabled)
        XCTAssertEqual(updated.excludedPeerIds, [123])
    }

    func testProductionEvaluatorKeepsCompleteMessageVisibleWhenWorkIsExhausted() {
        let rule = LocalMessageFilterRule(id: "limited", text: String(repeating: "a*", count: 100) + "b$", matchingMode: .regularExpression)
        let evaluator = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [rule]))
        let text = String(repeating: "a", count: 10_000)
        XCTAssertEqual(evaluator.evaluate(text: text, peerId: 1), .skippedWorkLimit)
        XCTAssertEqual(text.count, 10_000)
    }

    func testVersionOneCannotSmuggleAnExpressionPastLegacyLiteralSemantics() throws {
        let fixture = try LocalArchivePostboxFixture(test: self, name: "filter-policy-invalid-v1")
        var context: LocalMessageFilterContext!
        defer { context = nil; try? fixture.destroy() }
        struct InvalidLegacyPolicy: Encodable {
            let schemaVersion: Int32 = 1
            let enabled = true
            let rules = [LocalMessageFilterRule(id: "not-literal", text: "a.*b", matchingMode: .regularExpression)]
            let excludedPeerIds: [Int64] = []
        }
        let entry = try XCTUnwrap(PreferencesEntry(InvalidLegacyPolicy()))
        let _ = try self.localArchiveValue(fixture.postbox.transaction { $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry) })
        context = LocalMessageFilterContext(postbox: fixture.postbox)
        XCTAssertEqual(try self.localArchiveValue(context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(context.setEnabled(false)))
        XCTAssertFalse(try self.localArchiveValue(context.reset()))
        XCTAssertEqual(try self.localArchiveValue(fixture.postbox.transaction { $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data }), entry.data)
    }
}
