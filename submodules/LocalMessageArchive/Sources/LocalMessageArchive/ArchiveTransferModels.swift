// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import sqlcipher

/// Closed errors only. Passwords, database keys, SQL and file paths must never
/// be included in descriptions or logs.
public enum ArchiveTransferError: Error, Equatable {
    case invalidPassword
    case authenticationFailed
    case invalidArchive
    case unsupportedVersion
    case wrongAccount
    case conflict
    case cancelled
    case storageUnavailable
    case captureMustBePaused
    case unavailable
}

enum ArchiveTransferCheckpoint: Equatable {
    case snapshotRow
    case mediaChunk
    case beforeCommit
}

/// Cooperative cancellation is checked between bounded file/blob copies and
/// SQLite pages. CommonCrypto's fixed-cost password derivation is not cancellable.
public final class ArchiveTransferCancellation {
    private let lock = NSLock()
    private var cancelled = false
    private var checkpointObserver: ((ArchiveTransferCheckpoint) -> Void)?

    public init() {}

    public func cancel() {
        self.lock.lock(); self.cancelled = true; self.lock.unlock()
    }

    public var isCancelled: Bool {
        self.lock.lock(); defer { self.lock.unlock() }
        return self.cancelled
    }

    func check() throws {
        if self.isCancelled { throw ArchiveTransferError.cancelled }
    }

    // Internal fault-injection seam. Production has no observer; tests install
    // one before starting an operation to interrupt real SQLite/blob work.
    func observeCheckpoints(_ observer: @escaping (ArchiveTransferCheckpoint) -> Void) {
        self.lock.lock(); self.checkpointObserver = observer; self.lock.unlock()
    }

    func checkpoint(_ value: ArchiveTransferCheckpoint) throws {
        self.lock.lock(); let observer = self.checkpointObserver; self.lock.unlock()
        observer?(value)
        try self.check()
    }
}

public struct ArchiveTransferExport: Equatable {
    public let byteCount: Int64
}

public struct ArchiveTransferImport: Equatable {
    public let addedVersions: Int
    public let duplicateVersions: Int
    public let addedMedia: Int
    public let removedByRetention: Int
}

/// A fully authenticated, same-user, read-only encrypted snapshot. There is no
/// public key/path access. Its temporary ciphertext is removed when released.
public final class ArchivePreparedImport {
    let database: OpaquePointer
    let sourceAccountId: Int64
    let ownerId: Int64
    let testingEnvironment: Bool
    private let directory: URL
    // Retain the callback context for SQLite's cooperative progress handler.
    let cancellation: ArchiveTransferCancellation

    init(database: OpaquePointer, sourceAccountId: Int64, ownerId: Int64, testingEnvironment: Bool,
         directory: URL, cancellation: ArchiveTransferCancellation) {
        self.database = database; self.sourceAccountId = sourceAccountId
        self.ownerId = ownerId; self.directory = directory; self.cancellation = cancellation
        self.testingEnvironment = testingEnvironment
    }

    deinit {
        sqlite3_close_v2(self.database)
        try? FileManager.default.removeItem(at: self.directory)
    }
}
