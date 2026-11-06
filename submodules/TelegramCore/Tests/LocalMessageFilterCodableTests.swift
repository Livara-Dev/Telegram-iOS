// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

private struct RawFilterWireRule: Codable {
    let id: String
    let text: String
    let caseSensitive: Bool
    let peerId: Int64?
    let matchingMode: Int32?
    let mediaType: Int32?
    let isInverted: Bool?
}

private struct RawFilterWirePolicy: Codable {
    let schemaVersion: Int32
    let enabled: Bool
    let rules: [RawFilterWireRule]
    let excludedPeerIds: [Int64]
}

/// Uses the actual adapted Postbox codec: JSON alone would conceal an enum's
/// unsupported single-value container and could not catch the native crash.
final class LocalMessageFilterCodableTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private var context: LocalMessageFilterContext!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "filter-primitive-codec")
        self.context = LocalMessageFilterContext(postbox: self.fixture.postbox)
    }

    override func tearDownWithError() throws {
        self.context = nil
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func install(_ entry: PreferencesEntry) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction {
            $0.setPreferencesEntry(key: localMessageFilterSettingsKey, value: entry)
        })
    }

    private func bytes() throws -> Data? {
        return try self.localArchiveValue(self.fixture.postbox.transaction {
            $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.data
        })
    }

    func testEveryMediaAndExpressionModeUsesPrimitivePostboxWireAndReopens() throws {
        var rules = LocalMessageFilterMediaType.allCases.map {
            LocalMessageFilterRule(id: "media-\($0.rawValue)", text: "", mediaType: $0, isInverted: true)
        }
        rules.append(LocalMessageFilterRule(id: "expression", text: "^offer[0-9]+$", matchingMode: .regularExpression))
        for rule in rules {
            let entry = try XCTUnwrap(PreferencesEntry(rule))
            let raw = try XCTUnwrap(entry.get(RawFilterWireRule.self))
            XCTAssertEqual(raw.mediaType, rule.mediaType?.rawValue)
            XCTAssertEqual(raw.matchingMode, rule.matchingMode == .literal ? nil : rule.matchingMode.rawValue)
            XCTAssertEqual(entry.get(LocalMessageFilterRule.self), rule)
            XCTAssertTrue(try self.localArchiveValue(self.context.upsertRule(rule)))
        }
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(true)))
        self.context = nil
        try self.fixture.reopen()
        self.context = LocalMessageFilterContext(postbox: self.fixture.postbox)
        guard case let .available(settings) = try self.localArchiveValue(self.context.settings) else {
            return XCTFail("Saved fixed-width rules must remain readable")
        }
        XCTAssertEqual(settings.rules, rules)
        XCTAssertTrue(settings.enabled)
        let expression = try XCTUnwrap(rules.last)
        let evaluator = LocalMessageFilterEvaluator(settings: LocalMessageFilterSettings(enabled: true, rules: [expression]))
        XCTAssertEqual(evaluator.evaluate(text: "OFFER12", peerId: 1), .matched(ruleId: "expression"))
    }

    func testVersionTwoRawNumericRulesRemainByteIdenticalUntilAnExplicitMutation() throws {
        var rules = LocalMessageFilterMediaType.allCases.map {
            RawFilterWireRule(id: "legacy-\($0.rawValue)", text: "", caseSensitive: false, peerId: nil,
                matchingMode: nil, mediaType: $0.rawValue, isInverted: nil)
        }
        rules.append(RawFilterWireRule(id: "regex", text: "^allowed$", caseSensitive: false, peerId: nil,
            matchingMode: 1, mediaType: nil, isInverted: nil))
        let entry = try XCTUnwrap(PreferencesEntry(RawFilterWirePolicy(schemaVersion: 2, enabled: true, rules: rules, excludedPeerIds: [])))
        try self.install(entry)
        self.context = nil
        try self.fixture.reopen()
        self.context = LocalMessageFilterContext(postbox: self.fixture.postbox)
        guard case let .available(settings) = try self.localArchiveValue(self.context.settings) else {
            return XCTFail("Legacy numeric mode/media keys must decode")
        }
        XCTAssertEqual(Array(settings.rules.prefix(6)).compactMap(\.mediaType), LocalMessageFilterMediaType.allCases)
        XCTAssertEqual(settings.rules.last?.matchingMode, .regularExpression)
        XCTAssertEqual(try self.bytes(), entry.data)
        XCTAssertTrue(try self.localArchiveValue(self.context.setEnabled(false)))
        let updated = try XCTUnwrap(self.localArchiveValue(self.fixture.postbox.transaction {
            $0.getPreferencesEntry(key: localMessageFilterSettingsKey)?.get(RawFilterWirePolicy.self)
        }))
        XCTAssertEqual(updated.schemaVersion, 3)
        XCTAssertEqual(updated.rules.map(\.mediaType), rules.map(\.mediaType))
        XCTAssertEqual(updated.rules.map(\.matchingMode), rules.map(\.matchingMode))
    }

    func testUnknownRawModesAndMediaStayReadOnlyWithoutInvokingEnumCodable() throws {
        for version in [Int32(1), 2, 3] {
            for raw in [Int32(-1), 99] {
                for mediaRule in [false, true] {
                    let rule = RawFilterWireRule(id: "unknown", text: mediaRule ? "" : "word", caseSensitive: false,
                        peerId: nil, matchingMode: mediaRule ? nil : raw, mediaType: mediaRule ? raw : nil, isInverted: nil)
                    let entry = try XCTUnwrap(PreferencesEntry(RawFilterWirePolicy(schemaVersion: version, enabled: true, rules: [rule], excludedPeerIds: [])))
                    try self.install(entry)
                    XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
                    XCTAssertFalse(try self.localArchiveValue(self.context.setEnabled(false)))
                    XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
                    XCTAssertEqual(try self.bytes(), entry.data)
                }
            }
        }
        struct FutureShape: Encodable {
            let schemaVersion: Int32 = 99
            let enabled = true
            let rules: [Int32] = [7]
            let excludedPeerIds: [Int64] = []
        }
        let future = try XCTUnwrap(PreferencesEntry(FutureShape()))
        try self.install(future)
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
        XCTAssertEqual(try self.bytes(), future.data)
    }
}
