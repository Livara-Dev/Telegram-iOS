// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import LocalMessageArchive

/// UI-facing failures contain no password, account identifiers, paths or SQL.
public enum LocalArchiveTransferFailure: String {
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

/// Owns a completed encrypted file until the system document picker finishes.
/// Only this dedicated temporary folder is removed; Telegram data is untouched.
public final class LocalArchiveExportFile {
    public let url: URL
    public let byteCount: Int64
    private let directory: URL

    init(url: URL, byteCount: Int64, directory: URL) {
        self.url = url; self.byteCount = byteCount; self.directory = directory
    }

    deinit {
        let directory = self.directory
        DispatchQueue.global(qos: .utility).async { try? FileManager.default.removeItem(at: directory) }
    }
}

public enum LocalArchiveTransferResult {
    case exported(LocalArchiveExportFile)
    case imported(addedVersions: Int, duplicateVersions: Int, addedMedia: Int, removedByRetention: Int)
    case failed(LocalArchiveTransferFailure)
}

func localArchiveTransferFailure(_ error: ArchiveTransferError) -> LocalArchiveTransferFailure {
    switch error {
    case .invalidPassword: return .invalidPassword
    case .authenticationFailed: return .authenticationFailed
    case .invalidArchive: return .invalidArchive
    case .unsupportedVersion: return .unsupportedVersion
    case .wrongAccount: return .wrongAccount
    case .conflict: return .conflict
    case .cancelled: return .cancelled
    case .storageUnavailable: return .storageUnavailable
    case .captureMustBePaused: return .captureMustBePaused
    case .unavailable: return .unavailable
    }
}
