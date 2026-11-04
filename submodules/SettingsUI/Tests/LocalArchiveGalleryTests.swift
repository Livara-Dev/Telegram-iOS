// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import XCTest
import SwiftSignalKit
@testable import SettingsUI

private final class GalleryTestSource {
    private let lock = NSLock()
    private var keys: [String] = []
    private var callbacks: [String: (Data?) -> Void] = [:]
    var onStart: ((String) -> Void)?

    var started: [String] {
        self.lock.lock(); defer { self.lock.unlock() }; return self.keys
    }

    func signal(key: String, slot: Int) -> Signal<Data?, NoError> {
        return Signal { subscriber in
            XCTAssertFalse(Thread.isMainThread, "Gallery reads must stay off the UI thread")
            self.lock.lock()
            self.keys.append(key)
            self.callbacks[key] = { data in subscriber.putNext(data); subscriber.putCompletion() }
            self.lock.unlock()
            self.onStart?(key)
            return EmptyDisposable
        }
    }

    func finish(_ key: String, data: Data? = nil) {
        self.lock.lock()
        let callback = self.callbacks.removeValue(forKey: key)
        self.lock.unlock()
        XCTAssertNotNil(callback)
        callback?(data)
    }
}

final class LocalArchiveGalleryTests: XCTestCase {
    func testThumbnailDownsamplesImageAndRejectsInvalidBytes() throws {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let bytes = UIGraphicsImageRenderer(size: CGSize(width: 1024, height: 512), format: format).pngData { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 1024, height: 512))
        }
        let image = try XCTUnwrap(localArchiveGalleryImage(bytes, maximumDimension: 320))
        XCTAssertEqual(image.cgImage?.width, 320)
        XCTAssertEqual(image.cgImage?.height, 160)
        XCTAssertNil(localArchiveGalleryImage(Data([1, 2, 3]), maximumDimension: 320))
        XCTAssertNil(localArchiveGalleryImage(Data(repeating: 0, count: 16 * 1024 * 1024 + 1), maximumDimension: 320))
    }

    func testThumbnailReadsAreSerialEvenWhenMultipleCellsSubscribe() {
        let source = GalleryTestSource()
        let firstStarted = expectation(description: "first read started")
        let secondStarted = expectation(description: "second read started")
        source.onStart = { key in (key == "first" ? firstStarted : secondStarted).fulfill() }
        let loader = LocalArchiveThumbnailLoader(load: source.signal)
        let firstDone = expectation(description: "first decoded")
        let secondDone = expectation(description: "second decoded")
        let first = loader.image(eventKey: "first", slot: 0).start(next: { image in XCTAssertNil(image); firstDone.fulfill() })
        let second = loader.image(eventKey: "second", slot: 0).start(next: { image in XCTAssertNil(image); secondDone.fulfill() })
        defer { first.dispose(); second.dispose() }
        wait(for: [firstStarted], timeout: 5)
        XCTAssertEqual(source.started, ["first"])
        source.finish("first")
        wait(for: [firstDone, secondStarted], timeout: 5)
        XCTAssertEqual(source.started, ["first", "second"])
        source.finish("second")
        wait(for: [secondDone], timeout: 5)
    }

    func testCellReuseCancelsQueuedReadWithoutSkippingFollowingCell() {
        let source = GalleryTestSource()
        let firstStarted = expectation(description: "first read started")
        let lastStarted = expectation(description: "last read started")
        source.onStart = { key in
            if key == "first" { firstStarted.fulfill() }
            else if key == "last" { lastStarted.fulfill() }
            else { XCTFail("Reused cell must not start a media read") }
        }
        let loader = LocalArchiveThumbnailLoader(load: source.signal)
        let lastDone = expectation(description: "last cell completed")
        let first = loader.image(eventKey: "first", slot: 0).start()
        let reused = loader.image(eventKey: "reused", slot: 0).start(next: { _ in XCTFail("Disposed cell received a thumbnail") })
        reused.dispose()
        let last = loader.image(eventKey: "last", slot: 0).start(next: { _ in lastDone.fulfill() })
        defer { first.dispose(); last.dispose() }
        wait(for: [firstStarted], timeout: 5)
        source.finish("first")
        wait(for: [lastStarted], timeout: 5)
        XCTAssertEqual(source.started, ["first", "last"])
        source.finish("last")
        wait(for: [lastDone], timeout: 5)
    }

    func testActiveCellCancellationDoesNotOverlapReadsOrDeliverStaleThumbnail() {
        let source = GalleryTestSource()
        let firstStarted = expectation(description: "active read started")
        let lastStarted = expectation(description: "following read started")
        source.onStart = { key in (key == "first" ? firstStarted : lastStarted).fulfill() }
        let loader = LocalArchiveThumbnailLoader(load: source.signal)
        let lastDone = expectation(description: "following cell completed")
        let first = loader.image(eventKey: "first", slot: 0).start(next: { _ in XCTFail("Reused active cell received stale bytes") })
        wait(for: [firstStarted], timeout: 5)
        first.dispose()
        let last = loader.image(eventKey: "last", slot: 0).start(next: { _ in lastDone.fulfill() })
        defer { last.dispose() }
        XCTAssertEqual(source.started, ["first"])
        source.finish("first")
        wait(for: [lastStarted], timeout: 5)
        source.finish("last")
        wait(for: [lastDone], timeout: 5)
        XCTAssertEqual(source.started, ["first", "last"])
    }
}
