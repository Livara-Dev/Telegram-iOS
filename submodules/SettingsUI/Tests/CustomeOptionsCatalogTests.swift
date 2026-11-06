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
        for text in [String(repeating: "x", count: 10000), String(repeating: "👩🏽‍💻", count: 1000)] {
            XCTAssertLessThanOrEqual(CustomeOptionsCatalog.boundedQuery(text).utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)
            XCTAssertTrue(CustomeOptionsCatalog.categories(matching: text).isEmpty)
        }

        let combining = "a" + String(repeating: "\u{0301}", count: 10000)
        let bounded = CustomeOptionsCatalog.boundedQuery(combining)
        XCTAssertEqual(bounded, "a" + String(repeating: "\u{0301}", count: CustomeOptionsCatalog.maximumQueryUTF16Length - 1))
        XCTAssertEqual(bounded.utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)
        // Diacritic-insensitive search legitimately reduces this bounded
        // grapheme to "a"; a work limit does not make its matches disappear.
        let expected = CustomeOptionsCatalog.categories(matching: "a")
        XCTAssertFalse(expected.isEmpty)
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: combining), expected)
    }

    func testBoundedQueryRepairsSplitAstralScalarWithoutExceedingUTF16Limit() {
        let completePrefix = String(repeating: "x", count: CustomeOptionsCatalog.maximumQueryUTF16Length - 2)
        let complete = CustomeOptionsCatalog.boundedQuery(completePrefix + "👩" + "ignored")
        XCTAssertEqual(complete, completePrefix + "👩")
        XCTAssertEqual(complete.utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)

        let splitPrefix = completePrefix + "x"
        let repaired = CustomeOptionsCatalog.boundedQuery(splitPrefix + "👩")
        // String(decoding:as:) repairs the trailing high surrogate rather than
        // reading the low surrogate beyond the fixed work budget.
        XCTAssertEqual(repaired, splitPrefix + "\u{FFFD}")
        XCTAssertEqual(repaired.utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)
    }

    func testCombiningSequenceConsumesUTF16BudgetBeforeSearchFolding() {
        let prefix = "history"
        let bounded = prefix + String(repeating: "\u{0301}", count: CustomeOptionsCatalog.maximumQueryUTF16Length - prefix.utf16.count)
        let query = bounded + " does-not-exist-here"
        XCTAssertEqual(CustomeOptionsCatalog.boundedQuery(query), bounded)
        XCTAssertEqual(bounded.utf16.count, CustomeOptionsCatalog.maximumQueryUTF16Length)
        // The combining sequence consumes the budget before folding. A token
        // after that boundary must not suppress the valid "history" result.
        XCTAssertEqual(CustomeOptionsCatalog.categories(matching: query), [.deletedMessages])
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

    func testTimedCategoryDisclosesDefaultOffAccountRetentionAndUnchangedCanonicalTimers() {
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .timedMedia).map { $0.action }, [.timedMediaInformation])
        let explanation = CustomeOptionsCatalog.explanation(for: .timedMedia)
        XCTAssertTrue(explanation.contains("independent account-local archive options, off by default"))
        XCTAssertTrue(explanation.contains("completed-media, storage and API-contract limits"))
        XCTAssertTrue(explanation.contains("Canonical timers and reads remain active"))
        XCTAssertFalse(explanation.contains("not retained by the archive"))
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .localPremium).contains("do not grant Telegram Premium"))
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .localPremium).map { $0.action }, [.localCosmetics, .appearance, .animationSettings])
    }

    func testResetIsAnExplicitDestinationAndNeverAnArchiveDeletionAction() {
        XCTAssertEqual(CustomeOptionsCatalog.links(for: .advanced).map { $0.action }, [.resetActiveFeatures])
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .advanced).contains("archives"))
        XCTAssertTrue(CustomeOptionsCatalog.explanation(for: .advanced).contains("kept"))
    }
}
