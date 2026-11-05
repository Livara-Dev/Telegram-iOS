// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// A short local-history window, never an incoming Telegram message batch.
public struct ArchiveDeletedHistoryPage {
    public static let maximumExcludedAuthors = 4096
    public let records: [ArchivedRecord]
    public let isTruncated: Bool
    public init(records: [ArchivedRecord], isTruncated: Bool) {
        self.records = records
        self.isTruncated = isTruncated
    }
}
