// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Security
import XCTest
@testable import PasscodeCore

@available(macOS 10.15, *)
final class PrivateInstallationKeychainTests: XCTestCase {
    private let bundle = "org.livara.ayugram"
    private let privateGroup = "TESTTEAM00.org.livara.ayugram"

    private func environment(access: PasscodeConfiguration.KeychainAccess) throws -> PasscodeEnvironment {
        let environment = PasscodeEnvironment()
        try environment.configure(PasscodeConfiguration(appGroupIdentifier: "group." + self.bundle, processRole: .mainApp,
            biometricKeychainService: "wallet.biometric", keychainAccess: access), privateAccessGroup: { self.privateGroup })
        return environment
    }

    func testPrivateModeNeverAdoptsSharedPasscodeOrWalletServices() throws {
        let shared = try self.environment(access: .sharedAppGroup)
        let isolated = try self.environment(access: .applicationPrivate)
        for service in ["org.telegram.passcode.v1", "org.telegram.ton-wallet.vault.v1.envelope.account", "wallet.biometric"] {
            XCTAssertEqual(shared.scopedKeychainService(service), service)
            XCTAssertEqual(isolated.scopedKeychainService(service), "org.livara.ayugram.private.v1/" + service)
            XCTAssertFalse(isolated.scopedKeychainService(service).hasPrefix("org.telegram.ton-wallet."))
        }
    }

    func testDefaultSharedAccessIsUnchangedAndPrivateAccessUsesSignedResolver() throws {
        XCTAssertEqual(try self.environment(access: .sharedAppGroup).credentialAccessGroup(), "group." + self.bundle)
        XCTAssertEqual(try self.environment(access: .applicationPrivate).credentialAccessGroup(), self.privateGroup)
    }

    func testExtensionsCannotOptIntoPrivateMainAppCredentials() {
        XCTAssertThrowsError(try PasscodeEnvironment().configure(PasscodeConfiguration(appGroupIdentifier: "group." + self.bundle,
            processRole: .appExtension, keychainAccess: .applicationPrivate)))
    }

    func testPrivateModeCannotSwitchBackToSharedWithinProcess() throws {
        let isolated = try self.environment(access: .applicationPrivate)
        XCTAssertThrowsError(try isolated.configure(PasscodeConfiguration(appGroupIdentifier: "group." + self.bundle, processRole: .mainApp), privateAccessGroup: { self.privateGroup }))
    }

    func testMissingPrivateIdentityFailsInsteadOfUsingUnscopedKeychain() throws {
        let isolated = PasscodeEnvironment()
        try isolated.configure(PasscodeConfiguration(appGroupIdentifier: "group." + self.bundle, processRole: .mainApp,
            keychainAccess: .applicationPrivate), privateAccessGroup: { nil })
        XCTAssertThrowsError(try isolated.credentialAccessGroup())
    }

    func testPasscodeAndBiometricQueriesUseIsolatedServiceNames() throws {
        let isolated = try self.environment(access: .applicationPrivate)
        let keychain = PasscodeKeychain(environment: isolated)
        for account in ["credential", "device", "biometric.test"] {
            let query = try keychain.query(account)
            XCTAssertTrue((query[kSecAttrService as String] as? String)?.hasPrefix("org.livara.ayugram.private.v1/") == true)
            #if os(iOS)
            XCTAssertEqual(query[kSecAttrAccessGroup as String] as? String, self.privateGroup)
            #endif
            XCTAssertEqual(query[kSecAttrSynchronizable as String] as? Bool, false)
        }
    }
}
