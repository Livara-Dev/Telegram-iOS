// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import TelegramApi
@testable import TelegramCore

private struct RawLocalFolderAppearance: Codable {
    let color: Int32?
    let tag: String
}

private struct RawLocalFolderAppearanceDocument: Codable {
    let schemaVersion: Int32
    let folders: [Int32: RawLocalFolderAppearance]
}

final class LocalFolderAppearanceTests: XCTestCase {
    private var fixture: LocalArchivePostboxFixture!
    private var context: LocalFolderAppearanceContext!

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "folder-appearance")
        self.context = LocalFolderAppearanceContext(postbox: self.fixture.postbox)
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(7), self.folder(9)])
    }

    override func tearDownWithError() throws {
        self.context = nil
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func folder(_ id: Int32, shared: Bool = false) -> ChatListFilter {
        let title = ChatFolderTitle(text: "Work \(id)", entities: [MessageTextEntity(range: 0..<4, type: .Bold)], enableAnimations: false)
        return .filter(id: id, title: title, emoticon: "📁", data: ChatListFilterData(isShared: shared,
            hasSharedLinks: shared, categories: [.contacts], excludeMuted: true, excludeRead: true,
            excludeArchived: true, includePeers: ChatListFilterIncludePeers(peers: [], pinnedPeers: []),
            excludePeers: [], color: PeerNameColor(rawValue: 2)))
    }

    private func seed(_ postbox: Postbox, filters: [ChatListFilter]) throws {
        let _ = try self.localArchiveValue(_internal_updateChatListFiltersInteractively(postbox: postbox, { _ in filters }))
    }

    private func saveFolder(id: Int32, color: LocalFolderColor?, tag: String,
                            context: LocalFolderAppearanceContext? = nil,
                            expectedFilter: ChatListFilter? = nil) -> Signal<Bool, NoError> {
        return (context ?? self.context!).setFolder(id: id, color: color, tag: tag,
            expectedFilter: expectedFilter ?? self.folder(id))
    }

    private func settings(_ context: LocalFolderAppearanceContext? = nil) throws -> LocalFolderAppearanceSettings {
        guard case let .available(value) = try self.localArchiveValue((context ?? self.context!).settings) else {
            throw NSError(domain: "FolderAppearanceFixture", code: 1)
        }
        return value
    }

    private func savedDocument() throws -> Data? {
        return try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localFolderAppearanceSettingsKey)?.data })
    }

    private func canonicalTitle(_ filter: ChatListFilter) throws -> ChatFolderTitle {
        guard case let .filter(_, title, _, _) = filter else { throw NSError(domain: "FolderAppearanceFixture", code: 2) }
        return title
    }

    func testDefaultReadDoesNotWritePreferencesOrChangeNativeTitle() throws {
        XCTAssertEqual(try self.settings(), LocalFolderAppearanceSettings())
        XCTAssertNil(try self.savedDocument())
        let canonical = try self.canonicalTitle(self.folder(7))
        let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical,
            settings: try self.localArchiveValue(self.context.settings), dark: false)
        XCTAssertEqual(projected.title, canonical)
        XCTAssertNil(projected.rgb)
    }

    func testTagAndColorPersistAfterPostboxReopen() throws {
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .teal, tag: "  Focus\u{202e}\n  ")))
        self.context = nil
        try self.fixture.reopen()
        self.context = LocalFolderAppearanceContext(postbox: self.fixture.postbox)
        let saved = try XCTUnwrap(self.settings().folders[7])
        XCTAssertEqual(saved.tag, "Focus")
        XCTAssertEqual(saved.color, .teal)
        let title = try self.canonicalTitle(self.folder(7))
        let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: title,
            settings: try self.localArchiveValue(self.context.settings), dark: true)
        XCTAssertEqual(projected.title.text, "Work 7 · Focus")
        XCTAssertEqual(projected.title.entities, title.entities)
        XCTAssertFalse(projected.title.enableAnimations)
        XCTAssertEqual(projected.rgb, LocalFolderColor.teal.rgb(dark: true))
    }

    func testRealPreferenceObservationReprojectsCanonicalTitleOnceOnEveryChange() throws {
        let observed = self.expectation(description: "real account preference projection")
        let fulfilled = Atomic<Bool>(value: false)
        let canonical = try self.canonicalTitle(self.folder(7))
        let disposable = self.context.settings.start(next: { state in
            let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical, settings: state, dark: false)
            if projected.title.text == "Work 7 · Urgent", projected.rgb == LocalFolderColor.orange.rgb(dark: false),
               !fulfilled.swap(true) { observed.fulfill() }
        })
        defer { disposable.dispose() }
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .blue, tag: "Focus")))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .orange, tag: "Urgent")))
        self.wait(for: [observed], timeout: 15)
        for dark in [false, true, false] {
            let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical,
                settings: try self.localArchiveValue(self.context.settings), dark: dark)
            XCTAssertEqual(projected.title.text, "Work 7 · Urgent")
        }
        XCTAssertEqual(canonical.text, "Work 7")
    }

    func testSameFolderIdInTwoAccountsCannotShareOverlays() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "folder-appearance-other")
        var otherContext: LocalFolderAppearanceContext? = LocalFolderAppearanceContext(postbox: other.postbox)
        defer { otherContext = nil; try? other.destroy() }
        try self.seed(other.postbox, filters: [.allChats, self.folder(7)])
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .pink, tag: "Private")))
        let canonical = try self.canonicalTitle(self.folder(7))
        let emptyNewSurface = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical,
            settings: .available(LocalFolderAppearanceSettings()), dark: true)
        XCTAssertEqual(emptyNewSurface.title, canonical)
        XCTAssertNil(emptyNewSurface.rgb)
        XCTAssertEqual(try self.settings(try XCTUnwrap(otherContext)), LocalFolderAppearanceSettings())
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Team", context: try XCTUnwrap(otherContext))))
        XCTAssertEqual(try self.settings().folders[7]?.tag, "Private")
        XCTAssertEqual(try self.settings(try XCTUnwrap(otherContext)).folders[7]?.tag, "Team")
        XCTAssertTrue(try self.localArchiveValue(try XCTUnwrap(otherContext).reset()))
        XCTAssertEqual(try self.settings().folders[7]?.color, .pink)
    }

    func testLocalChangesDoNotRewriteCanonicalFiltersOrSerializedTelegramRequests() throws {
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9, shared: true), self.folder(7)])
        let snapshot: () throws -> (Data?, [Int32], [Data]) = {
            try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                let filters = _internal_currentChatListFilters(transaction: transaction)
                let payloads = filters.map { filter -> Data in
                    Api.functions.messages.updateDialogFilter(flags: filter.id == 0 ? 0 : 1, id: filter.id,
                        filter: filter.apiFilter(transaction: transaction)).1.makeData()
                }
                return (transaction.getPreferencesEntry(key: PreferencesKeys.chatListFilters)?.data, filters.map(\.id), payloads)
            })
        }
        let before = try snapshot()
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .blue, tag: "Focus")))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 9, color: .orange, tag: "Shared", expectedFilter: self.folder(9, shared: true))))
        let filters = try self.localArchiveValue(_internal_currentChatListFilters(postbox: self.fixture.postbox))
        for filter in filters where filter.id > 0 {
            let canonical = try self.canonicalTitle(filter)
            let display = localFolderAppearanceProjection(folderId: filter.id, canonicalTitle: canonical,
                settings: try self.localArchiveValue(self.context.settings), dark: false)
            XCTAssertNotEqual(display.title.text, canonical.text)
        }
        let after = try snapshot()
        XCTAssertEqual(after.0, before.0)
        XCTAssertEqual(after.1, [0, 9, 7])
        XCTAssertEqual(after.1, before.1)
        XCTAssertEqual(after.2, before.2)
        XCTAssertTrue(try self.localArchiveValue(self.context.reset()))
        let reset = try snapshot()
        XCTAssertEqual(reset.0, before.0)
        XCTAssertEqual(reset.2, before.2)
    }

    func testUnknownAndAllChatsIdsCannotCreateMetadataOrChangeNativePresentation() throws {
        for id in [Int32(0), -1, 101] {
            XCTAssertFalse(try self.localArchiveValue(self.saveFolder(id: id, color: .blue, tag: "No")))
        }
        XCTAssertNil(try self.savedDocument())
        let canonical = try self.canonicalTitle(self.folder(9))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Only 7")))
        let state = try self.localArchiveValue(self.context.settings)
        for id in [Int32(0), -1, 9, 101] {
            let projection = localFolderAppearanceProjection(folderId: id, canonicalTitle: canonical, settings: state, dark: false)
            XCTAssertEqual(projection.title, canonical)
            XCTAssertNil(projection.rgb)
        }
    }

    func testDeletedFolderCannotBeSavedByStaleEditor() throws {
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Before")))
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9)])
        XCTAssertNil(try self.savedDocument())
        XCTAssertFalse(try self.localArchiveValue(self.saveFolder(id: 7, color: .pink, tag: "After")))
        XCTAssertNil(try self.savedDocument())
    }

    func testInteractiveRemovalAndRecreatedIdCannotInheritDeletedFolderOverlay() throws {
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Old folder")))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 9, color: .blue, tag: "Kept")))
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9)])
        XCTAssertNil(try self.settings().folders[7])
        XCTAssertEqual(try self.settings().folders[9]?.tag, "Kept")
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(7), self.folder(9)])
        XCTAssertNil(try self.settings().folders[7])
        let title = try self.canonicalTitle(self.folder(7))
        let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: title,
            settings: try self.localArchiveValue(self.context.settings), dark: true)
        XCTAssertEqual(projected.title, title)
        XCTAssertNil(projected.rgb)
    }

    func testStaleEditorCannotSaveChangedOrRecreatedFolderWithSameId() throws {
        let original = self.folder(7)
        let replacement = ChatListFilter.filter(id: 7,
            title: ChatFolderTitle(text: "Different folder", entities: [], enableAnimations: true), emoticon: nil,
            data: ChatListFilterData(isShared: false, hasSharedLinks: false, categories: [.groups],
                excludeMuted: false, excludeRead: false, excludeArchived: false,
                includePeers: ChatListFilterIncludePeers(), excludePeers: [], color: nil))
        // A rename/change while open must fail even if the editor's pending
        // main-queue observation has not executed before the Save transaction.
        try self.seed(self.fixture.postbox, filters: [.allChats, replacement, self.folder(9)])
        XCTAssertFalse(try self.localArchiveValue(self.context.setFolder(id: 7, color: .pink, tag: "Old draft", expectedFilter: original)))
        XCTAssertNil(try self.savedDocument())
        // Reproduce remove -> create with the same random native ID. A stale
        // editor must not write metadata to the new canonical folder.
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9)])
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9), replacement])
        XCTAssertFalse(try self.localArchiveValue(self.context.setFolder(id: 7, color: .pink, tag: "Old draft", expectedFilter: original)))
        XCTAssertNil(try self.savedDocument())
        // Reopening from the replacement's real row supplies its own snapshot.
        XCTAssertTrue(try self.localArchiveValue(self.context.setFolder(id: 7, color: .blue, tag: "New draft", expectedFilter: replacement)))
        XCTAssertEqual(try self.settings().folders[7]?.tag, "New draft")
    }

    func testTransactionAndServerReconcileTransitionsPruneOnlyRemovedIdsAndKeepRenamedFolder() throws {
        for serverTransition in [false, true] {
            try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(7), self.folder(9)])
            XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Old")))
            XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 9, color: .pink, tag: "Keep")))
            let renamed = ChatListFilter.filter(id: 9,
                title: ChatFolderTitle(text: "Renamed", entities: [], enableAnimations: true), emoticon: nil,
                data: ChatListFilterData(isShared: false, hasSharedLinks: false, categories: [.contacts],
                    excludeMuted: false, excludeRead: false, excludeArchived: false,
                    includePeers: ChatListFilterIncludePeers(), excludePeers: [], color: nil))
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                if serverTransition {
                    let _ = updateChatListFiltersState(transaction: transaction, { state in
                        var updated = state; updated.filters = [.allChats, renamed]; return updated
                    })
                } else {
                    _internal_updateChatListFiltersInteractively(transaction: transaction, { _ in [.allChats, renamed] })
                }
            })
            XCTAssertNil(try self.settings().folders[7])
            XCTAssertEqual(try self.settings().folders[9]?.tag, "Keep")
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                let _ = updateChatListFiltersState(transaction: transaction, { state in
                    var updated = state; updated.filters.append(self.folder(7)); return updated
                })
            })
            XCTAssertNil(try self.settings().folders[7])
            let display = localFolderAppearanceProjection(folderId: 9,
                canonicalTitle: try self.canonicalTitle(renamed), settings: try self.localArchiveValue(self.context.settings), dark: false)
            XCTAssertEqual(display.title.text, "Renamed · Keep")
        }
    }

    func testCanonicalRemovalPreservesFutureLocalPreferenceBytes() throws {
        let raw = RawLocalFolderAppearanceDocument(schemaVersion: 2, folders: [7: .init(color: 0, tag: "Future")])
        let entry = try XCTUnwrap(PreferencesEntry(raw))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: entry) })
        try self.seed(self.fixture.postbox, filters: [.allChats, self.folder(9)])
        XCTAssertEqual(try self.savedDocument(), entry.data)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            let _ = updateChatListFiltersState(transaction: transaction, { state in
                var updated = state; updated.filters = []; return updated
            })
        })
        XCTAssertEqual(try self.savedDocument(), entry.data)
        XCTAssertEqual(try self.localArchiveValue(self.context.settings), .unsupported)
    }

    func testRemovingOneOverlayKeepsOtherFolderAndResetPreservesUnrelatedPreferences() throws {
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .blue, tag: "One")))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 9, color: .green, tag: "Two")))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: nil, tag: "")))
        XCTAssertNil(try self.settings().folders[7])
        XCTAssertEqual(try self.settings().folders[9]?.tag, "Two")
        let cosmetics = LocalClientCosmeticsContext(postbox: self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(cosmetics.setAccent(.sunset)))
        XCTAssertTrue(try self.localArchiveValue(self.context.reset()))
        XCTAssertEqual(try self.settings(), LocalFolderAppearanceSettings())
        XCTAssertNil(try self.savedDocument())
        guard case let .available(preserved) = try self.localArchiveValue(cosmetics.settings) else { return XCTFail("Other preferences lost") }
        XCTAssertEqual(preserved.accent, .sunset)
    }

    func testFutureUnknownAndInvalidDocumentsAreReadOnlyAndDisplayNativeFallback() throws {
        let documents = [
            RawLocalFolderAppearanceDocument(schemaVersion: 2, folders: [7: .init(color: 0, tag: "Future")]),
            RawLocalFolderAppearanceDocument(schemaVersion: 1, folders: [7: .init(color: 999, tag: "Unknown")]),
            RawLocalFolderAppearanceDocument(schemaVersion: 1, folders: [0: .init(color: 0, tag: "Invalid ID")]),
            RawLocalFolderAppearanceDocument(schemaVersion: 1, folders: [7: .init(color: nil, tag: "")]),
            RawLocalFolderAppearanceDocument(schemaVersion: 1, folders: [7: .init(color: 0, tag: "\u{202e}Direction")])
        ]
        let canonical = try self.canonicalTitle(self.folder(7))
        for document in documents {
            let entry = try XCTUnwrap(PreferencesEntry(document))
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: entry) })
            let state = try self.localArchiveValue(self.context.settings)
            XCTAssertEqual(state, .unsupported)
            XCTAssertFalse(try self.localArchiveValue(self.saveFolder(id: 7, color: .blue, tag: "Overwrite")))
            XCTAssertFalse(try self.localArchiveValue(self.context.reset()))
            XCTAssertEqual(try self.savedDocument(), entry.data)
            let display = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical, settings: state, dark: false)
            XCTAssertEqual(display.title, canonical)
            XCTAssertNil(display.rgb)
        }
    }

    func testBoundedDocumentRejects101stOverrideWithoutPartialWrite() throws {
        let folders = (2...102).map { self.folder(Int32($0)) }
        try self.seed(self.fixture.postbox, filters: folders)
        var current = LocalFolderAppearanceSettings()
        let value = try XCTUnwrap(LocalFolderAppearance(color: .blue, tag: "Label"))
        for id in 2...101 { current.folders[Int32(id)] = value }
        let entry = try XCTUnwrap(PreferencesEntry(current))
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { $0.setPreferencesEntry(key: localFolderAppearanceSettingsKey, value: entry) })
        XCTAssertFalse(try self.localArchiveValue(self.saveFolder(id: 102, color: .pink, tag: "Overflow")))
        XCTAssertEqual(try self.savedDocument(), entry.data)
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Replace")))
        XCTAssertEqual(try self.settings().folders.count, 100)
        XCTAssertEqual(try self.settings().folders[7]?.tag, "Replace")
    }

    func testTagLimitsAndUnicodeSanitizationRejectWithoutChangingSavedData() throws {
        let family = "👩‍👩‍👧‍👦"
        XCTAssertEqual(LocalFolderAppearance.normalizedTag(" \(family)\u{2066} Focus\u{2069}\n"), "\(family) Focus")
        XCTAssertEqual(LocalFolderAppearance.normalizedTag(" \n "), "")
        XCTAssertNotNil(LocalFolderAppearance.normalizedTag(String(repeating: "a", count: 20)))
        XCTAssertNil(LocalFolderAppearance.normalizedTag(String(repeating: "a", count: 21)))
        XCTAssertNil(LocalFolderAppearance.normalizedTag(String(repeating: family, count: 6)))
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: .green, tag: "Kept")))
        let before = try self.savedDocument()
        for invalid in [String(repeating: "a", count: 21), String(repeating: family, count: 6), String(repeating: "a", count: 1025)] {
            XCTAssertFalse(try self.localArchiveValue(self.saveFolder(id: 7, color: .pink, tag: invalid)))
            XCTAssertEqual(try self.savedDocument(), before)
        }
    }

    func testEveryPaletteAndColorOnlyTagOnlyModesPersistAndProjectInLightAndDark() throws {
        let canonical = try self.canonicalTitle(self.folder(7))
        for color in LocalFolderColor.allCases {
            XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: color, tag: "")))
            for dark in [false, true] {
                let projected = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical,
                    settings: try self.localArchiveValue(self.context.settings), dark: dark)
                XCTAssertEqual(projected.title, canonical)
                XCTAssertEqual(projected.rgb, color.rgb(dark: dark))
            }
        }
        XCTAssertTrue(try self.localArchiveValue(self.saveFolder(id: 7, color: nil, tag: "Local")))
        let tagOnly = localFolderAppearanceProjection(folderId: 7, canonicalTitle: canonical,
            settings: try self.localArchiveValue(self.context.settings), dark: false)
        XCTAssertEqual(tagOnly.title.text, "Work 7 · Local")
        XCTAssertNil(tagOnly.rgb)
    }
}
