// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import SwiftSignalKit
import TelegramCore
@testable import SettingsUI

private final class CurrentVersionTestSource {
    let pipe = ValuePipe<EngineMessage?>()
    var starts = 0
    var cancellations = 0
    func signal() -> Signal<EngineMessage?, NoError> {
        return Signal { subscriber in
            self.starts += 1
            let disposable = self.pipe.signal().start(next: { subscriber.putNext($0) })
            return ActionDisposable {
                self.cancellations += 1
                disposable.dispose()
            }
        }
    }
}

final class LocalArchivePresentationTests: XCTestCase {
    private func message(text: String = "current version", attributes: [EngineMessage.Attribute] = [], namespace: EnginePeer.Id.Namespace = Namespaces.Peer.CloudUser) -> EngineMessage {
        let peerId = EnginePeer.Id(namespace: namespace, id: ._internalFromInt64Value(810))
        return EngineMessage(stableId: 1, stableVersion: 1, id: EngineMessage.Id(peerId: peerId, namespace: Namespaces.Message.Cloud, id: 12), globallyUniqueId: nil, groupingKey: nil, groupInfo: nil, threadId: nil, timestamp: 100, flags: [], tags: [], globalTags: [], localTags: [], customTags: [], forwardInfo: nil, author: nil, text: text, attributes: attributes, media: [], peers: [:], associatedMessages: [:], associatedMessageIds: [], associatedMedia: [:], associatedThreadInfo: nil, associatedStories: [:])
    }

    /// A queue fence, not a timing sleep: let pending deliverOnMainQueue work run.
    private func flushMainQueue() {
        let drained = self.expectation(description: "main-queue deliveries")
        DispatchQueue.main.async { drained.fulfill() }
        self.wait(for: [drained], timeout: 3)
    }

    func testReadOnlyCurrentPreviewKeepsOriginalAndEditTimestampsDistinct() throws {
        let original = try XCTUnwrap(LocalArchiveCurrentVersionPreview(message: self.message()))
        XCTAssertEqual(original.text, "current version")
        XCTAssertEqual(original.timestamp, 100)
        XCTAssertNil(original.serverEditAt)
        let edited = try XCTUnwrap(LocalArchiveCurrentVersionPreview(message: self.message(text: "new body", attributes: [EditedMessageAttribute(date: 170, isHidden: false)])))
        XCTAssertEqual(edited.timestamp, 100)
        XCTAssertEqual(edited.serverEditAt, 170)
        XCTAssertEqual(edited.text, "new body")
    }

    func testCurrentPreviewNeverCopiesTimedOrSecretChatText() {
        XCTAssertNil(LocalArchiveCurrentVersionPreview(message: self.message(namespace: Namespaces.Peer.SecretChat)))
        XCTAssertNil(LocalArchiveCurrentVersionPreview(message: self.message(attributes: [AutoremoveTimeoutMessageAttribute(timeout: 10, countdownBeginTime: nil)])))
        XCTAssertNil(LocalArchiveCurrentVersionPreview(message: self.message(attributes: [AutoclearTimeoutMessageAttribute(timeout: 1, countdownBeginTime: nil)])))
    }

    func testLongCurrentPreviewHasBoundedUnicodeWorkAndNoSplitScalar() throws {
        let preview = try XCTUnwrap(LocalArchiveCurrentVersionPreview(message: self.message(text: String(repeating: "😀", count: 10000))))
        XCTAssertTrue(preview.text.hasPrefix(String(repeating: "😀", count: 4096)))
        XCTAssertTrue(preview.text.contains("preview limited"))
        XCTAssertFalse(preview.text.contains("\u{fffd}"))
        XCTAssertLessThan(preview.text.utf8.count, 16500)
        let combining = try XCTUnwrap(LocalArchiveCurrentVersionPreview(message: self.message(text: "a" + String(repeating: "\u{0301}", count: 10000))))
        XCTAssertLessThan(combining.text.utf8.count, 16500)
    }

    func testCustomMarkersHaveDeterministicDefaultFallbackForUnknownPreferences() {
        let markers = LocalArchivePresentationSettingsState.available(LocalArchivePresentationSettings(deletedMarker: "Removed", editedMarker: "Changed"))
        XCTAssertEqual(markers.marker(isDeleted: true), "Removed")
        XCTAssertEqual(markers.marker(isDeleted: false), "Changed")
        XCTAssertEqual(LocalArchivePresentationSettingsState.unsupported.marker(isDeleted: true), "Deleted")
        XCTAssertEqual(LocalArchivePresentationSettingsState.unsupported.marker(isDeleted: false), "Edited")
    }

    func testCurrentLoaderStartsOnlyWhenActiveAndTracksActualEditAndRemovalEvents() throws {
        let source = CurrentVersionTestSource()
        var states: [LocalArchiveCurrentVersionState] = []
        let loader = LocalArchiveCurrentVersionLoader(source: { source.signal() }, changed: { states.append($0) })
        defer { loader.dispose() }
        XCTAssertEqual(source.starts, 0)
        loader.setActive(true)
        XCTAssertEqual(source.starts, 1)
        XCTAssertEqual(states.last, .loading)
        source.pipe.putNext(self.message(text: "first"))
        self.flushMainQueue()
        XCTAssertEqual(states.last, .available(try XCTUnwrap(LocalArchiveCurrentVersionPreview(message: self.message(text: "first")))))
        source.pipe.putNext(self.message(text: "edited", attributes: [EditedMessageAttribute(date: 170, isHidden: false)]))
        self.flushMainQueue()
        if case let .available(current)? = states.last {
            XCTAssertEqual(current.text, "edited")
            XCTAssertEqual(current.serverEditAt, 170)
        } else { XCTFail("updated local message was not displayed") }
        source.pipe.putNext(nil)
        self.flushMainQueue()
        XCTAssertEqual(states.last, .unavailable)
        XCTAssertEqual(source.starts, 1)
    }

    func testLockOrNavigationAwayClearsTextCancelsAndDoesNotAcceptLateDelivery() {
        let source = CurrentVersionTestSource()
        var states: [LocalArchiveCurrentVersionState] = []
        let loader = LocalArchiveCurrentVersionLoader(source: { source.signal() }, changed: { states.append($0) })
        defer { loader.dispose() }
        loader.setActive(true)
        source.pipe.putNext(self.message(text: "pending value"))
        loader.setActive(false)
        self.flushMainQueue()
        XCTAssertEqual(states.last, .hidden)
        XCTAssertEqual(source.cancellations, 1)
        let count = states.count
        source.pipe.putNext(self.message(text: "hidden update"))
        self.flushMainQueue()
        XCTAssertEqual(states.count, count)
        loader.setActive(true)
        loader.setActive(true)
        XCTAssertEqual(source.starts, 2)
        source.pipe.putNext(self.message(text: "fresh visible value"))
        self.flushMainQueue()
        if case let .available(current)? = states.last { XCTAssertEqual(current.text, "fresh visible value") }
        else { XCTFail("reactivated reader did not receive a fresh value") }
    }

    func testDisposedLoaderCannotRestartOrExposeAnotherUpdate() {
        let source = CurrentVersionTestSource()
        var states: [LocalArchiveCurrentVersionState] = []
        let loader = LocalArchiveCurrentVersionLoader(source: { source.signal() }, changed: { states.append($0) })
        loader.setActive(true)
        loader.dispose()
        let count = states.count
        loader.setActive(true)
        source.pipe.putNext(self.message())
        self.flushMainQueue()
        XCTAssertEqual(states.count, count)
        XCTAssertEqual(source.starts, 1)
        XCTAssertEqual(source.cancellations, 1)
    }

    func testTimedCurrentMessageProducesExplicitUnsupportedState() {
        let source = CurrentVersionTestSource()
        var states: [LocalArchiveCurrentVersionState] = []
        let loader = LocalArchiveCurrentVersionLoader(source: { source.signal() }, changed: { states.append($0) })
        defer { loader.dispose() }
        loader.setActive(true)
        source.pipe.putNext(self.message(attributes: [AutoclearTimeoutMessageAttribute(timeout: 1, countdownBeginTime: nil)]))
        self.flushMainQueue()
        XCTAssertEqual(states.last, .unsupportedContent)
    }
}
