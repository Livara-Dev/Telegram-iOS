// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Darwin
import LocalMessageArchive

/// Called only on the archive utility queue, for a validated MediaBox completed
/// resource path. An open descriptor survives concurrent cache unlink. No fetch
/// is started and partial files, links, directories and oversized files are rejected.
func localArchiveReadCompletedMedia(_ path: String) throws -> Data? {
    let fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    guard fd >= 0 else {
        if errno == ENOENT || errno == ELOOP { return nil }
        throw ArchiveError.protectionUnavailable
    }
    defer { close(fd) }
    var before = stat()
    guard fstat(fd, &before) == 0 else { throw ArchiveError.protectionUnavailable }
    guard (before.st_mode & S_IFMT) == S_IFREG, before.st_size >= 0,
          before.st_size <= 16 * 1024 * 1024 else { return nil }
    var data = Data()
    data.reserveCapacity(Int(before.st_size))
    var buffer = [UInt8](repeating: 0, count: 64 * 1024)
    while data.count < Int(before.st_size) {
        let requested = min(buffer.count, Int(before.st_size) - data.count)
        let count = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, requested) }
        if count < 0 && errno == EINTR { continue }
        guard count > 0 else { throw ArchiveError.protectionUnavailable }
        data.append(contentsOf: buffer.prefix(count))
    }
    var after = stat()
    guard fstat(fd, &after) == 0, before.st_size == after.st_size,
          before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
          before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec else { throw ArchiveError.protectionUnavailable }
    return data
}
