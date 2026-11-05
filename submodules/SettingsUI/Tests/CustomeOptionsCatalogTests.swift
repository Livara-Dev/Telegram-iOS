// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
@testable import SettingsUI

final class CustomeOptionsCatalogTests: XCTestCase {
    func testAllRequestedCategoriesHaveStableIdentifiersAndActionableDestinations() {
        XCTAssertEqual(CustomeOptionsCatalog.title, "Custome options")
        XCTAssertEqual(CustomeOptionsCategory.allCases.count, 11)
        XCTAssertEqual(Set(CustomeOptionsCategory.allCases.map { $0.rawValue }).count, 11)
        for category in CustomeOptionsCategory.allCases {
            XCTAssertFalse(category.title.isEmpty)
            XCTAssertFalse(category.detail.isEmpty)
            XCTAssertFalse(CustomeOptionsCatalog.links(for: category).isEmpty)
            XCTAssertFalse(CustomeOptionsCatalog.explanation(for: category).isEmpty)
        }
    }

    func testSearchUsesAllTokensAndFindsFeatureTermsOutsideCategoryTitles() {
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: ""), CustomeOptionsCategory.allCases)
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: "   \n"), CustomeOptionsCategory.allCases)
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: "HISTÓRY edited"), [.deletedMessages])
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: "view once"), [.timedMedia])
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: "stickers typing"), [.ghostMode])
        XCTAssertTrue(CustomeOptionsCatalog.categories(matching: "does-not-exist-here").isEmpty)
    }

    func testSearchBoundsUTF16WorkIncludingAstralCharactersAndCombiningMarks() {
        for text in [String(repeating: "x", count: 10000), String(repeating: "👩🏽‍💻", count: 1000), "a" + String(repeating: "\u{0301}", count: 10000)] {
            XCTAssertLessThanOrEqual(CustomeOptionsCatalog.boundedQuery(text).utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)
            XCTAssertTrue(CustomeOptionsCatalog.categories(matching: text).isEmpty)
        }
    }

    func testUnavailablePreferencesNeverBecomeEditableDefaultSwitches() {
        let loading = CustomeOptionsState()
        for toggle in CustomeOptionsToggle.allCases {
            XCTAssertFalse(loading.isAvailable(toggle))
            XCTAssertFalse(loading.value(for: toggle))
        }
        let partial = CustomeOptionsState(archiveEnabled: true, archiveAvailable: false, ghostEnabled: true, ghostAvailable: true)
        XCTAssertTrue(partial.value(for: .archive))
        XCTAssertFalse(partial.isAvailable(.archive))
        XCTAssertTrue(partial.value(for: .ghostActivity))
        XCTAssertTrue(partial.isAvailable(.ghostActivity))
        XCTAssertFalse(partial.isAvailable(.messageFilters))
    }

    func testTimedAndPremiumCategoriesNeverAdvertiseRetentionOrServerEntitlementSwitches() {
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .timedMedia).map { $0.action }, [.timedMediaInformation])
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .timedMedia).contains("not retained"))
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .localPremium).contains("do not grant Telegram Premium"))
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .localPremium).map { $0.action }, [.localCosmetics, .appearance, .animationSettings])
    }

    func testResetIsAnExplicitDestinationAndNeverAnArchiveDeletionAction() {
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .advanced).map { $0.action }, [.resetActiveFeatures])
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .advanced).contains("archives"))
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .advanced).contains("kept"))
    }
}
