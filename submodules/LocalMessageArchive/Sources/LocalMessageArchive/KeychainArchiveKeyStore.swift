// SPDX-License-Identifier: GPL-2.0-or-later
// Native archive key management, added 2026-11-04.

import Foundation
import Security
import LocalAuthentication

/// Synchronous Keychain access. Invoke from the archive's storage worker, never
/// from the main UI queue. Keys are unrelated to Telegram authentication material.
public final class KeychainArchiveKeyStore: ArchiveKeyProvider {
    static let service = "org.livara.ayugram.local-archive"
    static let keyLength = 32

    private let operations: ArchiveKeychainOperations

    public init() {
        self.operations = SystemArchiveKeychainOperations()
    }

    init(operations: ArchiveKeychainOperations) {
        self.operations = operations
    }

    /// Existing archives must pass `createIfMissing: false`. A missing key then
    /// remains a key-loss error; it must never silently create a replacement key.
    /// This-device-only keys intentionally do not migrate through iCloud/backup.
    public func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
        if let existing = try self.read(accountId: accountId) {
            return existing
        }
        guard createIfMissing else {
            throw ArchiveError.unavailable(errSecItemNotFound)
        }

        let generated = self.operations.randomBytes(count: Self.keyLength)
        guard generated.status == errSecSuccess else {
            throw ArchiveError.unavailable(generated.status)
        }
        guard generated.data.count == Self.keyLength else {
            throw ArchiveError.invalidKey
        }

        var attributes = self.identity(accountId: accountId)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        attributes[kSecValueData as String] = generated.data
        let status = self.operations.add(attributes)
        switch status {
        case errSecSuccess:
            return generated.data
        case errSecDuplicateItem:
            // Another worker/process may have won creation. Use its key, never
            // overwrite it and never recursively generate another candidate.
            guard let winner = try self.read(accountId: accountId) else {
                throw ArchiveError.unavailable(errSecItemNotFound)
            }
            return winner
        default:
            throw ArchiveError.unavailable(status)
        }
    }

    private func identity(accountId: Int64) -> [String: Any] {
        let context = LAContext()
        context.interactionNotAllowed = true
        return [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: String(accountId),
            kSecAttrSynchronizable as String: false,
            kSecUseDataProtectionKeychain as String: true,
            kSecUseAuthenticationContext as String: context
        ]
    }

    private func read(accountId: Int64) throws -> Data? {
        var query = self.identity(accountId: accountId)
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        query[kSecReturnData as String] = true
        query[kSecReturnAttributes as String] = true
        let response = self.operations.copyMatching(query)
        if response.status == errSecItemNotFound {
            return nil
        }
        guard response.status == errSecSuccess else {
            // Locked-device, canceled/authentication and entitlement failures are
            // not absence. Do not fall back to generation or weaker protection.
            throw ArchiveError.unavailable(response.status)
        }
        guard let attributes = response.result as? [String: Any],
              let key = attributes[kSecValueData as String] as? Data,
              key.count == Self.keyLength else {
            throw ArchiveError.invalidKey
        }
        guard attributes[kSecAttrService as String] as? String == Self.service,
              attributes[kSecAttrAccount as String] as? String == String(accountId),
              attributes[kSecAttrAccessible as String] as? String == kSecAttrAccessibleWhenUnlockedThisDeviceOnly as String,
              attributes[kSecAttrSynchronizable as String] as? Bool == false else {
            throw ArchiveError.protectionUnavailable
        }
        return key
    }
}

/// Security calls are injectable for deterministic failure/race tests. Production
/// provides no key cache, update/delete operation, network or logging capability.
protocol ArchiveKeychainOperations {
    func copyMatching(_ query: [String: Any]) -> (status: OSStatus, result: Any?)
    func add(_ attributes: [String: Any]) -> OSStatus
    func randomBytes(count: Int) -> (status: OSStatus, data: Data)
}

private struct SystemArchiveKeychainOperations: ArchiveKeychainOperations {
    func copyMatching(_ query: [String: Any]) -> (status: OSStatus, result: Any?) {
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        return (status, result)
    }

    func add(_ attributes: [String: Any]) -> OSStatus {
        return SecItemAdd(attributes as CFDictionary, nil)
    }

    func randomBytes(count: Int) -> (status: OSStatus, data: Data) {
        var data = Data(count: count)
        let status = data.withUnsafeMutableBytes { bytes -> OSStatus in
            guard let address = bytes.baseAddress else {
                return errSecParam
            }
            return SecRandomCopyBytes(kSecRandomDefault, count, address)
        }
        if status != errSecSuccess {
            data.resetBytes(in: 0..<data.count)
            return (status, Data())
        }
        return (status, data)
    }
}
