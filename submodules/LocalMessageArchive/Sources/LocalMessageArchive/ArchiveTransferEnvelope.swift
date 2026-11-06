// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import CryptoKit
import CommonCrypto
import Darwin

/// A bounded password envelope around a separately encrypted SQLCipher snapshot.
/// This is an archive format, not a Telegram authentication or networking protocol.
enum ArchiveTransferEnvelope {
    static let magic = Data("AYUARC01".utf8)
    static let iterations: UInt32 = 600_000
    static let maximumDatabaseBytes: UInt64 = 8 * 1024 * 1024 * 1024
    static let chunkBytes = 512 * 1024

    struct Header: Codable {
        let version: UInt32
        let iterations: UInt32
        let salt: Data
        let payloadBytes: UInt64
        let payloadDigest: Data
        let wrappedKey: Data

        var authenticatedData: Data {
            var value = ArchiveTransferEnvelope.magic
            value.appendInteger(self.version)
            value.appendInteger(self.iterations)
            value.append(self.salt)
            value.appendInteger(self.payloadBytes)
            value.append(self.payloadDigest)
            return value
        }
    }

    static func checkPassword(_ password: String) throws {
        guard password.count >= 12, password.utf8.count <= 1024 else {
            throw ArchiveTransferError.invalidPassword
        }
    }

    private static func derive(_ password: String, salt: Data, rounds: UInt32) throws -> SymmetricKey {
        try self.checkPassword(password)
        guard salt.count == 16, rounds == self.iterations else { throw ArchiveTransferError.invalidArchive }
        var bytes = Data(password.utf8)
        defer { bytes.resetBytes(in: 0..<bytes.count) }
        var result = Data(count: 32)
        defer { result.resetBytes(in: 0..<result.count) }
        let status = bytes.withUnsafeBytes { pass in
            salt.withUnsafeBytes { saltBytes in
                result.withUnsafeMutableBytes { derived in
                    CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2),
                        pass.bindMemory(to: Int8.self).baseAddress, bytes.count,
                        saltBytes.bindMemory(to: UInt8.self).baseAddress, salt.count,
                        CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256), rounds,
                        derived.bindMemory(to: UInt8.self).baseAddress, 32)
                }
            }
        }
        guard status == kCCSuccess else { throw ArchiveTransferError.storageUnavailable }
        return SymmetricKey(data: result)
    }

    static func randomKey() -> Data {
        return SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
    }

    static func seal(snapshot: URL, destination: URL, accountId: Int64, ownerId: Int64,
                     testingEnvironment: Bool, transportKey: Data,
                     password: String, cancellation: ArchiveTransferCancellation) throws -> ArchiveTransferExport {
        try self.checkPassword(password)
        try cancellation.check()
        guard transportKey.count == 32 else { throw ArchiveTransferError.invalidArchive }
        let source = try ArchiveTransferFiles.openRead(snapshot)
        defer { Darwin.close(source.fd) }
        guard source.bytes >= 4096, source.bytes <= self.maximumDatabaseBytes else { throw ArchiveTransferError.invalidArchive }
        let digest = try ArchiveTransferFiles.hash(source.fd, count: source.bytes, cancellation: cancellation)
        let salt = self.randomKey().prefix(16)
        let key = try self.derive(password, salt: Data(salt), rounds: self.iterations)
        try cancellation.check()
        var wrappedContents = transportKey
        wrappedContents.appendInteger(UInt64(bitPattern: accountId))
        wrappedContents.appendInteger(UInt64(bitPattern: ownerId))
        wrappedContents.append(testingEnvironment ? 1 : 0)
        let unsigned = Header(version: 1, iterations: self.iterations, salt: Data(salt),
                              payloadBytes: source.bytes, payloadDigest: digest, wrappedKey: Data())
        let sealed = try AES.GCM.seal(wrappedContents, using: key, authenticating: unsigned.authenticatedData)
        guard let combined = sealed.combined, combined.count == 77 else { throw ArchiveTransferError.invalidArchive }
        let header = Header(version: 1, iterations: self.iterations, salt: unsigned.salt,
                            payloadBytes: source.bytes, payloadDigest: digest, wrappedKey: combined)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let encoded = try encoder.encode(header)
        guard encoded.count <= 4096 else { throw ArchiveTransferError.invalidArchive }
        let target = try ArchiveTransferFiles.create(destination)
        var complete = false
        defer {
            Darwin.close(target)
            if !complete { try? FileManager.default.removeItem(at: destination) }
        }
        var prefix = self.magic
        prefix.appendInteger(UInt32(encoded.count)); prefix.append(encoded)
        try ArchiveTransferFiles.write(target, prefix)
        guard lseek(source.fd, 0, SEEK_SET) == 0 else { throw ArchiveTransferError.storageUnavailable }
        let copied = try ArchiveTransferFiles.copy(source.fd, target, count: source.bytes, cancellation: cancellation)
        guard copied == digest, fsync(target) == 0 else { throw ArchiveTransferError.storageUnavailable }
        try ArchiveTransferFiles.unchanged(source)
        try cancellation.check()
        complete = true
        return ArchiveTransferExport(byteCount: Int64(prefix.count) + Int64(source.bytes))
    }

    static func unseal(source: URL, snapshot: URL, ownerId: Int64, testingEnvironment: Bool, password: String,
                       cancellation: ArchiveTransferCancellation) throws -> (key: Data, accountId: Int64) {
        try self.checkPassword(password)
        try cancellation.check()
        let input = try ArchiveTransferFiles.openRead(source)
        defer { Darwin.close(input.fd) }
        guard input.bytes >= 12,
              try ArchiveTransferFiles.read(input.fd, count: 8) == self.magic else { throw ArchiveTransferError.invalidArchive }
        let headerLength = try ArchiveTransferFiles.read(input.fd, count: 4).integer(UInt32.self)
        guard (1...4096).contains(headerLength), UInt64(headerLength) + 12 <= input.bytes else {
            throw ArchiveTransferError.invalidArchive
        }
        let encoded = try ArchiveTransferFiles.read(input.fd, count: Int(headerLength))
        let header: Header
        do {
            guard let object = try JSONSerialization.jsonObject(with: encoded) as? [String: Any],
                  Set(object.keys) == Set(["version", "iterations", "salt", "payloadBytes", "payloadDigest", "wrappedKey"]) else {
                throw ArchiveTransferError.invalidArchive
            }
            header = try JSONDecoder().decode(Header.self, from: encoded)
        }
        catch { throw ArchiveTransferError.invalidArchive }
        guard header.version == 1 else { throw ArchiveTransferError.unsupportedVersion }
        guard header.iterations == self.iterations, header.salt.count == 16, header.wrappedKey.count == 77,
              header.payloadDigest.count == 32, header.payloadBytes >= 4096,
              header.payloadBytes <= self.maximumDatabaseBytes,
              input.bytes == 12 + UInt64(headerLength) + header.payloadBytes else { throw ArchiveTransferError.invalidArchive }
        let key = try self.derive(password, salt: header.salt, rounds: header.iterations)
        let contents: Data
        do {
            contents = try AES.GCM.open(AES.GCM.SealedBox(combined: header.wrappedKey), using: key,
                                        authenticating: header.authenticatedData)
        } catch { throw ArchiveTransferError.authenticationFailed }
        guard contents.count == 49 else { throw ArchiveTransferError.authenticationFailed }
        guard Int64(bitPattern: try Data(contents[40..<48]).integer(UInt64.self)) == ownerId,
              contents[48] == (testingEnvironment ? 1 : 0) else {
            throw ArchiveTransferError.wrongAccount
        }
        try cancellation.check()
        let output = try ArchiveTransferFiles.create(snapshot)
        var complete = false
        defer {
            Darwin.close(output)
            if !complete { try? FileManager.default.removeItem(at: snapshot) }
        }
        let digest = try ArchiveTransferFiles.copy(input.fd, output, count: header.payloadBytes, cancellation: cancellation)
        guard digest == header.payloadDigest else { throw ArchiveTransferError.authenticationFailed }
        guard fsync(output) == 0 else { throw ArchiveTransferError.storageUnavailable }
        try ArchiveTransferFiles.unchanged(input)
        try cancellation.check()
        complete = true
        return (Data(contents.prefix(32)), Int64(bitPattern: try Data(contents[32..<40]).integer(UInt64.self)))
    }
}

enum ArchiveTransferFiles {
    struct Opened {
        let fd: Int32
        let bytes: UInt64
        let device: dev_t
        let inode: ino_t
        let modified: timespec
    }

    static func protectedDirectory(_ url: URL) throws {
        guard url.isFileURL else { throw ArchiveTransferError.storageUnavailable }
        try self.validateParents(url.deletingLastPathComponent())
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
            try self.protect(url, permissions: 0o700)
        } catch { throw ArchiveTransferError.storageUnavailable }
    }

    static func scratchDirectory() throws -> URL {
        let result = FileManager.default.temporaryDirectory.appendingPathComponent("AyuGram-archive-transfer-" + UUID().uuidString, isDirectory: true)
        try self.protectedDirectory(result)
        return try self.physicalURL(result)
    }

    static func physicalURL(_ url: URL) throws -> URL {
        let resolved = url.withUnsafeFileSystemRepresentation { pointer -> UnsafeMutablePointer<CChar>? in
            guard let pointer else { return nil }
            return realpath(pointer, nil)
        }
        guard let resolved else { throw ArchiveTransferError.storageUnavailable }
        defer { free(resolved) }
        return URL(fileURLWithPath: String(cString: resolved), isDirectory: url.hasDirectoryPath)
    }

    static func validateParents(_ url: URL) throws {
        guard url.isFileURL else { throw ArchiveTransferError.storageUnavailable }
        var current = url.standardizedFileURL
        while current.path != "/" {
            var info = stat()
            guard lstat(current.path, &info) == 0 else { throw ArchiveTransferError.storageUnavailable }
            if (info.st_mode & S_IFMT) == S_IFLNK {
                let aliases = ["/var": "/private/var", "/tmp": "/private/tmp"]
                // Foundation can preserve a Darwin /var or /tmp spelling even
                // when the endpoint is a known system alias. Match the POSIX
                // physical-path proof used for SQLite's NOFOLLOW opens. This
                // allowlist still rejects every caller-created parent link.
                guard let expected = aliases[current.path],
                      try self.physicalURL(current).path == expected else {
                    throw ArchiveTransferError.storageUnavailable
                }
            } else if (info.st_mode & S_IFMT) != S_IFDIR { throw ArchiveTransferError.storageUnavailable }
            current.deleteLastPathComponent()
        }
    }

    static func protect(_ url: URL, permissions: Int) throws {
        var attributes: [FileAttributeKey: Any] = [.posixPermissions: permissions]
        #if os(iOS)
        attributes[.protectionKey] = FileProtectionType.complete
        #endif
        try FileManager.default.setAttributes(attributes, ofItemAtPath: url.path)
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        var item = url; try item.setResourceValues(values)
    }

    static func create(_ url: URL) throws -> Int32 {
        guard url.isFileURL else { throw ArchiveTransferError.storageUnavailable }
        try self.validateParents(url.deletingLastPathComponent())
        let fd = Darwin.open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw ArchiveTransferError.storageUnavailable }
        do { try self.protect(url, permissions: 0o600) }
        catch {
            Darwin.close(fd); try? FileManager.default.removeItem(at: url)
            throw ArchiveTransferError.storageUnavailable
        }
        return fd
    }

    static func openRead(_ url: URL) throws -> Opened {
        guard url.isFileURL else { throw ArchiveTransferError.invalidArchive }
        try self.validateParents(url.deletingLastPathComponent())
        let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
        guard fd >= 0 else { throw ArchiveTransferError.storageUnavailable }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_size >= 0, UInt64(info.st_size) <= ArchiveTransferEnvelope.maximumDatabaseBytes + 8192 else {
            Darwin.close(fd); throw ArchiveTransferError.invalidArchive
        }
        return Opened(fd: fd, bytes: UInt64(info.st_size), device: info.st_dev, inode: info.st_ino, modified: info.st_mtimespec)
    }

    static func unchanged(_ value: Opened) throws {
        var current = stat()
        guard fstat(value.fd, &current) == 0, current.st_dev == value.device, current.st_ino == value.inode,
              current.st_size >= 0, UInt64(current.st_size) == value.bytes,
              current.st_mtimespec.tv_sec == value.modified.tv_sec,
              current.st_mtimespec.tv_nsec == value.modified.tv_nsec else { throw ArchiveTransferError.invalidArchive }
    }

    static func read(_ fd: Int32, count: Int) throws -> Data {
        guard count >= 0, count <= ArchiveTransferEnvelope.chunkBytes else { throw ArchiveTransferError.invalidArchive }
        var value = Data(count: count), position = 0
        try value.withUnsafeMutableBytes { bytes in
            while position < count {
                let result = Darwin.read(fd, bytes.baseAddress!.advanced(by: position), count - position)
                if result < 0 && errno == EINTR { continue }
                guard result > 0 else { throw ArchiveTransferError.invalidArchive }
                position += result
            }
        }
        return value
    }

    static func write(_ fd: Int32, _ bytes: Data) throws {
        try bytes.withUnsafeBytes { buffer in
            var position = 0
            while position < bytes.count {
                let result = Darwin.write(fd, buffer.baseAddress!.advanced(by: position), bytes.count - position)
                if result < 0 && errno == EINTR { continue }
                guard result > 0 else { throw ArchiveTransferError.storageUnavailable }
                position += result
            }
        }
    }

    static func hash(_ fd: Int32, count: UInt64, cancellation: ArchiveTransferCancellation) throws -> Data {
        guard lseek(fd, 0, SEEK_SET) == 0 else { throw ArchiveTransferError.storageUnavailable }
        var remaining = count, hash = SHA256()
        while remaining > 0 {
            try cancellation.check()
            let part = try self.read(fd, count: Int(min(remaining, UInt64(ArchiveTransferEnvelope.chunkBytes))))
            hash.update(data: part); remaining -= UInt64(part.count)
        }
        return Data(hash.finalize())
    }

    static func copy(_ source: Int32, _ destination: Int32, count: UInt64,
                     cancellation: ArchiveTransferCancellation) throws -> Data {
        var remaining = count, hash = SHA256()
        while remaining > 0 {
            try cancellation.check()
            let bytes = try self.read(source, count: Int(min(remaining, UInt64(ArchiveTransferEnvelope.chunkBytes))))
            try self.write(destination, bytes)
            hash.update(data: bytes); remaining -= UInt64(bytes.count)
        }
        return Data(hash.finalize())
    }
}

extension Data {
    fileprivate mutating func appendInteger<T: FixedWidthInteger>(_ number: T) {
        var value = number.bigEndian
        Swift.withUnsafeBytes(of: &value) { self.append(contentsOf: $0) }
    }

    fileprivate func integer<T: FixedWidthInteger>(_ type: T.Type) throws -> T {
        guard self.count == MemoryLayout<T>.size else { throw ArchiveTransferError.invalidArchive }
        return self.reduce(T.zero) { ($0 << 8) | T($1) }
    }
}
