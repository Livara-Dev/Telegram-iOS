// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

private struct RawLocalCosmeticsDocument: Codable {
    let schemaVersion: Int32
    let accent: Int32
    let reduceMotion: Bool
    let allowBundledPremiumIcons: Bool
}

final class LocalClientCosmeticsTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private var context: LocalClientCosmeticsContext!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "client-cosmetics")
        self.context = LocalClientCosmeticsContext(postbox: self.fixture.postbox)
    }

    override func tearDownWithError() throws {
        self.context = nil
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func settings(_ context: LocalClientCosmeticsContext? = nil) throws -> LocalClientCosmeticsSettings {
        let state = try self.localArchiveValue((context ?? self.context!).settings)
        guard case let .available(settings) = state else { throw NSError(domain: "CosmeticsFixture", code: 1) }
        return settings
    }

    func testAbsentSettingsAreEditableDefaultsWithoutWritingOnRead() throws {
        XCTAssertEqual(try self.settings(), LocalClientCosmeticsSettings())
        let present = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localClientCosmeticsSettingsKey) != nil })
        XCTAssertFalse(present)
    }

    func testPaletteMotionAndLocalIconPermissionPersistAcrossReopen() throws {
        XCTAssertTrue(try self.localArchiveValue(self.context.setAccent(.ocean)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setReducedMotion(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setAllowBundledPremiumIcons(true)))
        self.context = nil
        try self.fixture.reopen()
        self.context = LocalClientCosmeticsContext(postbox: self.fixture.postbox)
        XCTAssertEqual(try self.settings(), LocalClientCosmeticsSettings(accent: .ocean, reduceMotion: true, allowBundledPremiumIcons: true))
    }

    func testSettingsObservationAppliesRealPostboxChangesImmediately() throws {
        let updated = self.expectation(description: "real account preference observation")
        let fulfilled = Atomic<Bool>(value: false)
        let disposable = self.context.settings.start(next: { state in
            if case let .available(settings) = state, settings.accent == .forest, settings.reduceMotion,
               !fulfilled.swap(true) { updated.fulfill() }
        })
        defer { disposable.dispose() }
        XCTAssertTrue(try self.localArchiveValue(self.context.setAccent(.forest)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setReducedMotion(true)))
        self.wait(for: [updated], timeout: 15)
    }

    func testTwoAccountPostboxesRemainIsolated() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "client-cosmetics-other")
        var otherContext: LocalClientCosmeticsContext? = LocalClientCosmeticsContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        XCTAssertTrue(try self.localArchiveValue(self.context.setAccent(.sunset)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setAllowBundledPremiumIcons(true)))
        XCTAssertEqual(try self.settings(try XCTUnwrap(otherContext)), LocalClientCosmeticsSettings())
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(otherContext).setAccent(.rose)))
        XCTAssertEqual(try self.settings().accent, .sunset)
        XCTAssertEqual(try self.settings(try XCTUnwrap(otherContext)).accent, .rose)
    }

    func testFutureAndUnknownPaletteDocumentsStayByteIdenticalOnMutationAndReset() throws {
        for document in [RawLocalCosmeticsDocument(schemaVersion: 2, accent: 0, reduceMotion: false, allowBundledPremiumIcons: false),
                         RawLocalCosmeticsDocument(schemaVersion: 1, accent: 999, reduceMotion: true, allowBundledPremiumIcons: true)] {
            let original = try XCTUnwrap(PreferencesEntry(document))
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction {
                $0.setPreferencesEntry(key: localClientCosmeticsSettingsKey, value: original)
            })
            XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.context.setAccent(.forest)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setReducedMotion(false)))
            XCTAssertFalse(try self.localArchiveValue(self.context.setAllowBundledPremiumIcons(false)))
            XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
            let retained = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localClientCosmeticsSettingsKey)?.data })
            XCTAssertEqual(retained, original.data)
        }
    }

    func testResetTouchesOnlyOwnDocumentAndPreservesArchivePolicy() throws {
        let archive = LocalArchiveSettings(enabled: true, excludeBots: false, excludedPeerIds: [91], excludedSenderIds: [72], maxAgeSeconds: 86400, maxPayloadBytes: 67108864)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { setLocalArchiveSettings(transaction: $0, settings: archive) })
        XCTAssertTrue(try self.localArchiveValue(self.context.setAccent(.rose)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setReducedMotion(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.setAllowBundledPremiumIcons(true)))
        XCTAssertTrue(try self.localArchiveValue(self.context.reset()))
        XCTAssertEqual(try self.settings(), LocalClientCosmeticsSettings())
        let preserved = try self.localArchiveValue(self.fixture.postbox.transaction { localArchiveSettings(transaction: $0) })
        XCTAssertEqual(preserved, archive)
    }

    func testAllPresetCodesRoundTripInProductionPreferenceCodec() throws {
        XCTAssertEqual(LocalClientAccentPreset.allCases.map { $0.rawValue }, Array(0...12))
        for accent in LocalClientAccentPreset.allCases {
            XCTAssertTrue(try self.localArchiveValue(self.context.setAccent(accent)))
            XCTAssertEqual(try self.settings().accent, accent)
        }
    }

    func testLocalIconOptInNeverAuthorizesUnbundledAssetsOrChangesServerPremiumInput() {
        let disabled = LocalClientCosmeticsSettingsState.available(.init())
        let enabled = LocalClientCosmeticsSettingsState.available(.init(allowBundledPremiumIcons: true))
        for accountIsPremium in [false, true] {
            for local in [disabled, enabled, .unsupported] {
                XCTAssertFalse(localClientAllowsBundledAppIcon(isBundled: false, isPremiumIcon: false, accountIsPremium: accountIsPremium, settings: local))
                XCTAssertFalse(localClientAllowsBundledAppIcon(isBundled: false, isPremiumIcon: true, accountIsPremium: accountIsPremium, settings: local))
                XCTAssertTrue(localClientAllowsBundledAppIcon(isBundled: true, isPremiumIcon: false, accountIsPremium: accountIsPremium, settings: local))
            }
        }
        XCTAssertFalse(localClientAllowsBundledAppIcon(isBundled: true, isPremiumIcon: true, accountIsPremium: false, settings: disabled))
        XCTAssertFalse(localClientAllowsBundledAppIcon(isBundled: true, isPremiumIcon: true, accountIsPremium: false, settings: .unsupported))
        XCTAssertTrue(localClientAllowsBundledAppIcon(isBundled: true, isPremiumIcon: true, accountIsPremium: false, settings: enabled))
        XCTAssertTrue(localClientAllowsBundledAppIcon(isBundled: true, isPremiumIcon: true, accountIsPremium: true, settings: disabled))
    }
}
