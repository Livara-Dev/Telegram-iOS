// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
import Foundation
import Darwin

/// Pins one completed local file before a caller queues asynchronous archive work.
/// Unlinking or replacing its path does not change the opened file being read.
/// This is an in-process lifetime guarantee, not durability before SQLite commits.
/// Process death after unlink but before commit can still lose those bytes; an
/// in-memory descriptor owner cannot recover them on the next launch.
public final class ArchiveMediaFileSource {
    public let byteCount: Int64

    // Kept module-internal for native descriptor-lifetime invariant tests. Never
    // expose or close this descriptor through the public archive interface.
    let descriptor: Int32
    private let capturedStatus: stat
    private let onClose: (() -> Void)?
    private let validityLock = NSLock()
    private var invalidated = false

    private init(descriptor: Int32, status: stat, onClose: (() -> Void)?) {
        self.descriptor = descriptor
        self.capturedStatus = status
        self.byteCount = Int64(status.st_size)
        self.onClose = onClose
    }

    /// Performs only open/fstat validation, without reading media bytes. Call
    /// before normal cache deletion; retain the returned owner through handoff.
    /// Missing or unsupported sources return nil; permission/I/O failures throw.
    /// onClose is invoked once, after final-owner descriptor close, on whichever
    /// thread releases the owner. It must be quick/thread-safe, must not strongly
    /// capture the owner, and must not do UI work. Nil/error acquisitions do not
    /// invoke it: callers must release their own reservation on those paths.
    public static func acquire(source: URL, expectedSize: Int64? = nil, onClose: (() -> Void)? = nil) throws -> ArchiveMediaFileSource? {
        guard source.isFileURL, !source.path.contains("\0"),
              expectedSize.map({ $0 >= 0 }) ?? true else { throw ArchiveError.invalidInput }
        let descriptor = source.path.withCString {
            Darwin.open($0, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        }
        guard descriptor >= 0 else {
            if errno == ENOENT || errno == ELOOP || errno == ENOTDIR { return nil }
            throw ArchiveError.protectionUnavailable
        }
        var transferOwnership = false
        defer { if !transferOwnership { Darwin.close(descriptor) } }
        var status = stat()
        guard fstat(descriptor, &status) == 0 else { throw ArchiveError.protectionUnavailable }
        guard (status.st_mode & S_IFMT) == S_IFREG, status.st_size >= 0,
              status.st_size <= 512 * 1024 * 1024 else { return nil }
        guard expectedSize.map({ $0 == Int64(status.st_size) }) ?? true else { return nil }
        let owner = ArchiveMediaFileSource(descriptor: descriptor, status: status, onClose: onClose)
        transferOwnership = true
        return owner
    }

    deinit {
        // The immutable descriptor has exactly one owner and one close site.
        // Never retry close: a reused descriptor must not be closed accidentally.
        Darwin.close(descriptor)
        onClose?()
    }

    /// Cooperatively revokes queued/in-progress copies without closing a
    /// descriptor that a reader may still use. This flag is not atomic with
    /// SQLite COMMIT: a copy past its final check can still commit. Durable
    /// purge/generation coordination is a separate application responsibility.
    public func invalidate() {
        validityLock.lock()
        invalidated = true
        validityLock.unlock()
    }

    func checkValid() throws {
        validityLock.lock()
        let revoked = invalidated
        validityLock.unlock()
        guard !revoked else { throw ArchiveError.protectionUnavailable }
    }

    /// pread gives each consumer an independent offset, including retries and
    /// concurrent consumers of the same retained owner. No path is reopened.
    func stream(_ consume: (UnsafeRawPointer, Int, Int64) throws -> Void) throws {
        try verifyUnchanged()
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)
        var offset: Int64 = 0
        while offset < byteCount {
            try checkValid()
            let length = min(buffer.count, Int(byteCount - offset))
            let count = buffer.withUnsafeMutableBytes {
                Darwin.pread(descriptor, $0.baseAddress, length, off_t(offset))
            }
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { throw ArchiveError.protectionUnavailable }
            try buffer.withUnsafeBytes { try consume($0.baseAddress!, count, offset) }
            offset += Int64(count)
        }
        try verifyUnchanged()
    }

    private func verifyUnchanged() throws {
        try checkValid()
        var current = stat()
        guard fstat(descriptor, &current) == 0,
              (current.st_mode & S_IFMT) == S_IFREG,
              current.st_dev == capturedStatus.st_dev, current.st_ino == capturedStatus.st_ino,
              current.st_size == capturedStatus.st_size,
              current.st_mtimespec.tv_sec == capturedStatus.st_mtimespec.tv_sec,
              current.st_mtimespec.tv_nsec == capturedStatus.st_mtimespec.tv_nsec else {
            throw ArchiveError.protectionUnavailable
        }
        // ctime/link count intentionally may change when the original cache path
        // is unlinked or replaced; neither changes this descriptor's byte source.
    }
}
