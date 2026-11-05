// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
@testable import PasscodeCore

final class ApplicationStorageTests: XCTestCase {
    private let privateURL = URL(fileURLWithPath: "/private/app-support/AyuGram", isDirectory: true)
    private let sharedURL = URL(fileURLWithPath: "/private/shared-container", isDirectory: true)
    private let group = "group.org.livara.ayugram"

    func testFreshInstallationPrefersAccessibleExactAppGroup() throws {
        var probes: [String] = []
        let (selection, location) = try ApplicationStorage.choose(saved: nil, candidates: [self.group], privateURL: self.privateURL) { group in
            probes.append(group)
            return self.sharedURL
        }
        XCTAssertEqual(probes, [self.group])
        XCTAssertEqual(selection.group, self.group)
        XCTAssertEqual(location.baseURL, self.sharedURL)
        XCTAssertFalse(location.usesPrivateContainer)
    }

    func testFreshInstallationWithoutGroupUsesPrivateContainer() throws {
        let (_, location) = try ApplicationStorage.choose(saved: nil, candidates: [self.group], privateURL: self.privateURL) { _ in nil }
        XCTAssertEqual(location.baseURL, self.privateURL)
        XCTAssertNil(location.appGroupIdentifier)
        XCTAssertTrue(location.usesPrivateContainer)
    }

    func testPrivateSelectionSurvivesRelaunchWhenAppGroupBecomesAvailable() throws {
        let saved = ApplicationStorage.Selection(group: nil).dictionary
        let (_, location) = try ApplicationStorage.choose(saved: saved, candidates: [self.group], privateURL: self.privateURL) { _ in
            XCTFail("A pinned private store must not probe or switch to a group")
            return self.sharedURL
        }
        XCTAssertEqual(location.baseURL, self.privateURL)
    }

    func testSelectedGroupLosingEntitlementNeverFallsBackToFreshDatabase() {
        XCTAssertThrowsError(try ApplicationStorage.choose(saved: ApplicationStorage.Selection(group: self.group).dictionary,
            candidates: [self.group], privateURL: self.privateURL, container: { _ in nil }))
    }

    func testIdentityChangeCannotSelectAnotherAccountsGroup() {
        XCTAssertThrowsError(try ApplicationStorage.choose(saved: ApplicationStorage.Selection(group: self.group).dictionary,
            candidates: ["group.org.livara.other"], privateURL: self.privateURL, container: { _ in self.sharedURL }))
    }

    func testMalformedSelectionDoesNotBecomeNewInstallation() {
        let cases: [[String: Any]] = [["version": 2, "kind": "private"], ["version": 1, "kind": "other"],
            ["version": 1, "kind": "group", "identifier": "../other"],
            ["version": 1, "kind": "private", "identifier": self.group]]
        for saved in cases {
            XCTAssertThrowsError(try ApplicationStorage.choose(saved: saved, candidates: [self.group], privateURL: self.privateURL, container: { _ in self.sharedURL }))
        }
    }

    func testConfiguredIdentityIsOnlyAnExactAdditionalCandidate() throws {
        var probes: [String] = []
        let runtime = self.group + ".TEAM000000"
        let (_, location) = try ApplicationStorage.choose(saved: nil, candidates: [runtime, self.group], privateURL: self.privateURL) { group in
            probes.append(group)
            return group == self.group ? self.sharedURL : nil
        }
        XCTAssertEqual(probes, [runtime, self.group])
        XCTAssertEqual(location.appGroupIdentifier, self.group)
    }

    func testSelectionRoundTripsWithoutStoringAccountOrSessionMaterial() throws {
        let groups: [String?] = [nil, self.group]
        for group in groups {
            let selected = ApplicationStorage.Selection(group: group)
            XCTAssertEqual(try ApplicationStorage.Selection(saved: selected.dictionary), selected)
            XCTAssertEqual(Set(selected.dictionary.keys), group == nil ? Set(["version", "kind"]) : Set(["version", "kind", "identifier"]))
        }
    }
}
