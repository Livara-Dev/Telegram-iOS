// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
@testable import TelegramCore

extension XCTestCase {
    func localArchiveValue<T>(_ signal: Signal<T, NoError>, file: StaticString = #filePath, line: UInt = #line) throws -> T {
        let finished = expectation(description: "archive integration signal finished")
        let result = Atomic<T?>(value: nil)
        let disposable = (signal |> take(1)).start(next: { value in
            let _ = result.swap(value)
        }, completed: { finished.fulfill() })
        defer { disposable.dispose() }
        wait(for: [finished], timeout: 15)
        return try XCTUnwrap(result.with { $0 }, file: file, line: line)
    }
}

/// Uses the production Postbox schema, serializers and encrypted on-disk value box.
/// Closing and reopening exercises persisted operation-log payloads, not a model of them.
final class LocalArchivePostboxFixture {
    let directory: URL
    private(set) var postbox: Postbox!
    private unowned let test: XCTestCase
    private var initialized = false

    init(test: XCTestCase, name: String) throws {
        self.test = test
        self.directory = FileManager.default.temporaryDirectory.appendingPathComponent(name + "-" + UUID().uuidString)
        initializeAccountManagement()
        try self.open()
    }

    private func open() throws {
        // Synthetic test keys only. These never leave this temporary test database.
        let encryption = ValueBoxEncryptionParameters(forceEncryptionIfNoSet: true,
            key: try XCTUnwrap(.init(data: Data(repeating: 0x71, count: 32))),
            salt: try XCTUnwrap(.init(data: Data(repeating: 0x35, count: 16))))
        let opened = try self.test.localArchiveValue(openPostbox(basePath: self.directory.path,
            seedConfiguration: telegramPostboxSeedConfiguration,
            encryptionParameters: encryption,
            timestampForAbsoluteTimeBasedOperations: Int32(Date().timeIntervalSince1970),
            isMainProcess: true, isTemporary: false, isReadOnly: false,
            // The owner must initialize encryption on the empty SQLite file
            // created by open(). Reopens must never replace a damaged database.
            useCopy: false, useCaches: true, removeDatabaseOnError: !self.initialized)
            |> filter { result in
                if case .upgrading = result { return false }
                return true
            })
        guard case let .postbox(postbox) = opened else {
            throw NSError(domain: "LocalArchivePostboxFixture", code: 1)
        }
        self.postbox = postbox
        self.initialized = true
    }

    func close() throws {
        if let postbox = self.postbox {
            let _ = try self.test.localArchiveValue(postbox.transaction { _ in })
            let _ = try self.test.localArchiveValue(postbox.mediaBox.storageBox.all())
            let _ = try self.test.localArchiveValue(postbox.mediaBox.cacheStorageBox.all())
            postbox.mediaBox.dataQueue.sync {}
        }
        self.postbox = nil
        Postbox.sharedQueue.sync {}
    }

    func reopen() throws {
        try self.close()
        try self.open()
    }

    func destroy() throws {
        try self.close()
        try FileManager.default.removeItem(at: self.directory)
    }
}

func localArchiveTestUser(_ id: PeerId, bot: Bool = false) -> TelegramUser {
    return TelegramUser(id: id, accessHash: nil, firstName: "Archive fixture", lastName: nil,
        username: nil, phone: nil, photo: [],
        botInfo: bot ? BotUserInfo(flags: [], inlinePlaceholder: nil) : nil,
        restrictionInfo: nil, flags: [], emojiStatus: nil, usernames: [], storiesHidden: nil,
        nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
        subscriberCount: nil, verificationIconFileId: nil)
}

func localArchiveTestMessage(_ id: MessageId, text: String = "original text", authorId: PeerId? = nil,
                             attributes: [MessageAttribute] = []) -> StoreMessage {
    return StoreMessage(id: id, customStableId: nil, globallyUniqueId: nil, groupingKey: nil,
        threadId: nil, timestamp: 100, flags: [.Incoming], tags: [], globalTags: [], localTags: [],
        forwardInfo: nil, authorId: authorId ?? id.peerId, text: text, attributes: attributes, media: [])
}
