// SPDX-License-Identifier: GPL-2.0-or-later

import Foundation
import Security
import LocalAuthentication
import XCTest
@testable import LocalMessageArchive

private final class FakeArchiveKeychainOperations: ArchiveKeychainOperations {
    var reads: [[String: Any]] = []
    var additions: [[String: Any]] = []
    var randomCalls = 0
    var randomStatus: OSStatus = errSecSuccess
    var randomLength = 32
    var addStatus: OSStatus?
    var readResponses: [(status: OSStatus, result: Any?)] = []
    var records: [String: [String: Any]] = [:]

    func copyMatching(_ query: [String: Any]) -> (status: OSStatus, result: Any?) {
        self.reads.append(query)
        if !self.readResponses.isEmpty {
            return self.readResponses.removeFirst()
        }
        guard let account = query[kSecAttrAccount as String] as? String,
              let record = self.records[account] else {
            return (errSecItemNotFound, nil)
        }
        return (errSecSuccess, record)
    }

    func add(_ attributes: [String: Any]) -> OSStatus {
        self.additions.append(attributes)
        if let status = self.addStatus {
            return status
        }
        guard let account = attributes[kSecAttrAccount as String] as? String else {
            return errSecParam
        }
        guard self.records[account] == nil else {
            return errSecDuplicateItem
        }
        self.records[account] = attributes
        return errSecSuccess
    }

    func randomBytes(count: Int) -> (status: OSStatus, data: Data) {
        self.randomCalls += 1
        return (self.randomStatus, Data(repeating: UInt8(self.randomCalls), count: self.randomLength))
    }
}

final class KeychainArchiveKeyStoreTests: XCTestCase {
    private func record(accountId: Int64, key: Data = Data(repeating: 7, count: 32)) -> [String: Any] {
        return [
            kSecAttrService as String: KeychainArchiveKeyStore.service,
            kSecAttrAccount as String: String(accountId),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            kSecAttrSynchronizable as String: false,
            kSecValueData as String: key
        ]
    }

    private func assertUnavailable(_ status: OSStatus, operation: () throws -> Data, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try operation(), file: file, line: line) { error in
            guard case ArchiveError.unavailable(let actual) = error else {
                return XCTFail("Expected an opaque Security status", file: file, line: line)
            }
            XCTAssertEqual(actual, status, file: file, line: line)
        }
    }

    func testNewKeyIsDeviceOnlyUnlockedAndNotSynchronizable() throws {
        let operations = FakeArchiveKeychainOperations()
        let store = KeychainArchiveKeyStore(operations: operations)
        let key = try store.key(accountId: 42, createIfMissing: true)
        XCTAssertEqual(key.count, 32)
        XCTAssertEqual(operations.randomCalls, 1)
        XCTAssertEqual(operations.additions.count, 1)
        let attributes = try XCTUnwrap(operations.additions.first)
        XCTAssertEqual(attributes[kSecAttrService as String] as? String, "org.livara.ayugram.local-archive")
        XCTAssertEqual(attributes[kSecAttrAccount as String] as? String, "42")
        XCTAssertEqual(attributes[kSecAttrAccessible as String] as? String, kSecAttrAccessibleWhenUnlockedThisDeviceOnly as String)
        XCTAssertEqual(attributes[kSecAttrSynchronizable as String] as? Bool, false)
        XCTAssertEqual(attributes[kSecUseDataProtectionKeychain as String] as? Bool, true)
        let context = try XCTUnwrap(attributes[kSecUseAuthenticationContext as String] as? LAContext)
        XCTAssertTrue(context.interactionNotAllowed)
        XCTAssertNil(attributes[kSecAttrAccessGroup as String])
    }

    func testExistingKeyDoesNotGenerateOrRewrite() throws {
        let operations = FakeArchiveKeychainOperations()
        operations.records["42"] = self.record(accountId: 42)
        let key = try KeychainArchiveKeyStore(operations: operations).key(accountId: 42, createIfMissing: true)
        XCTAssertEqual(key, Data(repeating: 7, count: 32))
        XCTAssertEqual(operations.randomCalls, 0)
        XCTAssertTrue(operations.additions.isEmpty)
        let query = try XCTUnwrap(operations.reads.first)
        XCTAssertEqual(query[kSecAttrSynchronizable as String] as? Bool, false)
        let context = try XCTUnwrap(query[kSecUseAuthenticationContext as String] as? LAContext)
        XCTAssertTrue(context.interactionNotAllowed)
        XCTAssertEqual(query[kSecReturnAttributes as String] as? Bool, true)
        XCTAssertEqual(query[kSecReturnData as String] as? Bool, true)
    }

    func testMissingKeyForExistingArchiveNeverGeneratesReplacement() {
        let operations = FakeArchiveKeychainOperations()
        let store = KeychainArchiveKeyStore(operations: operations)
        self.assertUnavailable(errSecItemNotFound) {
            try store.key(accountId: 42, createIfMissing: false)
        }
        XCTAssertEqual(operations.randomCalls, 0)
        XCTAssertTrue(operations.additions.isEmpty)
    }

    func testLockedOrDeniedReadNeverGeneratesReplacement() {
        for status in [errSecInteractionNotAllowed, errSecAuthFailed, errSecMissingEntitlement] {
            let operations = FakeArchiveKeychainOperations()
            operations.readResponses = [(status, nil)]
            let store = KeychainArchiveKeyStore(operations: operations)
            self.assertUnavailable(status) { try store.key(accountId: 42, createIfMissing: true) }
            XCTAssertEqual(operations.randomCalls, 0)
            XCTAssertTrue(operations.additions.isEmpty)
        }
    }

    func testMalformedStoredKeysNeverGetReplaced() {
        let malformed: [Any?] = [nil, Data(repeating: 0, count: 32), self.record(accountId: 42, key: Data(count: 31)), self.record(accountId: 42, key: Data(count: 33))]
        for result in malformed {
            let operations = FakeArchiveKeychainOperations()
            operations.readResponses = [(errSecSuccess, result)]
            let store = KeychainArchiveKeyStore(operations: operations)
            XCTAssertThrowsError(try store.key(accountId: 42, createIfMissing: true)) { error in
                guard case ArchiveError.invalidKey = error else { return XCTFail("Expected invalid-key error") }
            }
            XCTAssertEqual(operations.randomCalls, 0)
            XCTAssertTrue(operations.additions.isEmpty)
        }
    }

    func testWeakProtectionAndWrongIdentityAreRejectedWithoutMutation() {
        let mutations: [(String, Any)] = [
            (kSecAttrAccessible as String, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly),
            (kSecAttrSynchronizable as String, true),
            (kSecAttrAccount as String, "another-account"),
            (kSecAttrService as String, "another-service")
        ]
        for (name, value) in mutations {
            let operations = FakeArchiveKeychainOperations()
            var attributes = self.record(accountId: 42)
            attributes[name] = value
            operations.readResponses = [(errSecSuccess, attributes)]
            XCTAssertThrowsError(try KeychainArchiveKeyStore(operations: operations).key(accountId: 42, createIfMissing: true)) { error in
                guard case ArchiveError.protectionUnavailable = error else { return XCTFail("Expected protection error") }
            }
            XCTAssertEqual(operations.randomCalls, 0)
            XCTAssertTrue(operations.additions.isEmpty)
        }
    }

    func testRandomFailureOrWrongLengthCannotBePersisted() {
        let failed = FakeArchiveKeychainOperations()
        failed.randomStatus = errSecNotAvailable
        self.assertUnavailable(errSecNotAvailable) {
            try KeychainArchiveKeyStore(operations: failed).key(accountId: 42, createIfMissing: true)
        }
        XCTAssertTrue(failed.additions.isEmpty)
        let malformed = FakeArchiveKeychainOperations()
        malformed.randomLength = 31
        XCTAssertThrowsError(try KeychainArchiveKeyStore(operations: malformed).key(accountId: 42, createIfMissing: true))
        XCTAssertTrue(malformed.additions.isEmpty)
    }

    func testAddFailureIsNotReportedAsUsableKey() {
        let operations = FakeArchiveKeychainOperations()
        operations.addStatus = errSecInteractionNotAllowed
        self.assertUnavailable(errSecInteractionNotAllowed) {
            try KeychainArchiveKeyStore(operations: operations).key(accountId: 42, createIfMissing: true)
        }
        XCTAssertEqual(operations.additions.count, 1)
        XCTAssertEqual(operations.reads.count, 1)
    }

    func testDuplicateCreationUsesTheWinningKey() throws {
        let operations = FakeArchiveKeychainOperations()
        let winner = Data(repeating: 9, count: 32)
        operations.readResponses = [(errSecItemNotFound, nil), (errSecSuccess, self.record(accountId: 42, key: winner))]
        operations.addStatus = errSecDuplicateItem
        let key = try KeychainArchiveKeyStore(operations: operations).key(accountId: 42, createIfMissing: true)
        XCTAssertEqual(key, winner)
        XCTAssertEqual(operations.randomCalls, 1)
        XCTAssertEqual(operations.additions.count, 1)
        XCTAssertEqual(operations.reads.count, 2)
    }

    func testDuplicateThenMissingDoesNotRetryOrOverwrite() {
        let operations = FakeArchiveKeychainOperations()
        operations.addStatus = errSecDuplicateItem
        self.assertUnavailable(errSecItemNotFound) {
            try KeychainArchiveKeyStore(operations: operations).key(accountId: 42, createIfMissing: true)
        }
        XCTAssertEqual(operations.randomCalls, 1)
        XCTAssertEqual(operations.additions.count, 1)
        XCTAssertEqual(operations.reads.count, 2)
    }

    func testDistinctAccountsHaveDistinctRecordsAndStableReopen() throws {
        let operations = FakeArchiveKeychainOperations()
        let store = KeychainArchiveKeyStore(operations: operations)
        let first = try store.key(accountId: -1, createIfMissing: true)
        let second = try store.key(accountId: 1, createIfMissing: true)
        XCTAssertNotEqual(first, second)
        XCTAssertEqual(try store.key(accountId: -1, createIfMissing: false), first)
        XCTAssertEqual(try store.key(accountId: 1, createIfMissing: false), second)
        XCTAssertEqual(Set(operations.records.keys), ["-1", "1"])
        XCTAssertEqual(operations.randomCalls, 2)
    }
}
