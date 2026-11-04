// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import AVFoundation
import LocalMessageArchive
@testable import LocalArchivePlayback

final class ArchivePlaybackTests: XCTestCase {
    private struct Keys: ArchiveKeyProvider {
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data { Data(repeating: 0x6a, count: 32) }
    }

    private final class EncryptedSource: ArchivePlaybackSource {
        let store: ArchiveStore
        private let lock = NSLock()
        private var lengths: [Int] = []
        init(_ store: ArchiveStore) { self.store = store }
        var requestedLengths: [Int] { lock.lock(); defer { lock.unlock() }; return lengths }
        func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
            store.mediaInfo(eventKey: "playback", slot: 0) { result in
                completion((try? result.get()).flatMap { $0 }.map { ArchivePlaybackInfo(mimeType: $0.mimeType, byteCount: $0.byteCount) })
            }
            return ArchivePlaybackCancellation()
        }
        func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation {
            lock.lock(); lengths.append(count); lock.unlock()
            store.mediaRange(eventKey: "playback", slot: 0, offset: offset, count: count) { completion((try? $0.get()) ?? nil) }
            return ArchivePlaybackCancellation()
        }
    }

    private final class UnavailableSource: ArchivePlaybackSource {
        let started: XCTestExpectation?
        let cancelled: XCTestExpectation?
        let missingBytes: Bool
        init(started: XCTestExpectation? = nil, cancelled: XCTestExpectation? = nil, missingBytes: Bool = false) {
            self.started = started; self.cancelled = cancelled; self.missingBytes = missingBytes
        }
        func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
            started?.fulfill()
            if started == nil { completion(missingBytes ? ArchivePlaybackInfo(mimeType: "video/mp4", byteCount: 100) : nil) }
            return ArchivePlaybackCancellation { [cancelled] in cancelled?.fulfill() }
        }
        func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation {
            completion(nil)
            return ArchivePlaybackCancellation()
        }
    }

    private func result<Value>(_ work: (@escaping (Result<Value, ArchiveError>) -> Void) -> Void) async throws -> Value {
        return try await withCheckedThrowingContinuation { continuation in work { continuation.resume(with: $0) } }
    }

    private func close(_ store: ArchiveStore) async {
        await withCheckedContinuation { continuation in store.close { continuation.resume() } }
    }

    func testMovieDecodesFromEncryptedArchiveAfterCacheFileRemovalAndRestart() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-playback-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store: ArchiveStore = try await result { ArchiveStore.open(baseDirectory: root, accountId: 8, keyProvider: Keys(), completion: $0) }
        let event = ArchiveEvent(eventKey: "playback", message: ArchiveMessageID(accountId: 8, peerId: 9, namespace: 0, messageId: 1), kind: .deleted, observedAt: 100, content: ArchiveContent(text: "", authorId: nil, sentAt: 90, entities: Data()))
        _ = try await result { store.record([ArchiveCapture(event: event, context: ArchiveCaptureContext(ordinaryCloudMessage: true))], generation: 0, policy: ArchiveCapturePolicy(enabled: true), completion: $0) }
        // Synthetic black H.264 fixture produced locally with FFmpeg; no user media.
        var movie = try XCTUnwrap(Data(base64Encoded: "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAN0bW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAMgAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAp90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAMgAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAADIAAAEAAABAAAAAAIXbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAyAAAACgBVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABwm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAYJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MS4xOS4xMDEgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAMg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAHZIAAAAAAAAABhzdHRzAAAAAAAAAAEAAAAFAAACAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAOGN0dHMAAAAAAAAABQAAAAEAAAQAAAAAAQAACgAAAAABAAAEAAAAAAEAAAAAAAAAAQAAAgAAAAAcc3RzYwAAAAAAAAABAAAAAQAAAAUAAAABAAAAKHN0c3oAAAAAAAAAAAAAAAUAAALFAAAADAAAAAwAAAAMAAAADAAAABRzdGNvAAAAAAAAAAEAAAOkAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2MS43LjEwMwAAAAhmcmVlAAAC/W1kYXQAAAKuBgX//6rcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMTA4IDMxZTE5ZjkgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDIzIC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAPZYiEADP//vbsvgU2FMjBAAAACEGaJGxCv/7AAAAACEGeQniF/8GBAAAACAGeYXRCv8SAAAAACAGeY2pCv8SB"))
        // A valid trailing free atom increases the source without changing sample offsets.
        movie.append(contentsOf: [0, 2, 0, 8, 0x66, 0x72, 0x65, 0x65])
        movie.append(Data(repeating: 0, count: 128 * 1024))
        let cacheFile = root.appendingPathComponent("telegram-cache.mp4")
        try movie.write(to: cacheFile)
        let saved: Bool = try await result { store.recordMediaFile(eventKey: "playback", slot: 0, kind: "video", mimeType: "video/mp4", source: cacheFile, expectedSize: Int64(movie.count), generation: 0, completion: $0) }
        XCTAssertTrue(saved)
        try FileManager.default.removeItem(at: cacheFile)
        await close(store)
        let reopened: ArchiveStore = try await result { ArchiveStore.open(baseDirectory: root, accountId: 8, keyProvider: Keys(), completion: $0) }
        let source = EncryptedSource(reopened)
        let playback = ArchivePlaybackAsset(source: source)
        XCTAssertEqual(playback.asset.referenceRestrictions, .forbidAll)
        XCTAssertEqual(playback.asset.url.scheme, "ayugram-archive")
        let duration = try await playback.asset.load(.duration)
        XCTAssertGreaterThan(CMTimeGetSeconds(duration), 0)
        let playable = try await playback.asset.load(.isPlayable)
        XCTAssertTrue(playable)
        let generator = AVAssetImageGenerator(asset: playback.asset)
        if #available(iOS 16.0, macOS 13.0, *) {
            let frame = try await generator.image(at: .zero).image
            XCTAssertEqual(frame.width, 16)
            XCTAssertEqual(frame.height, 16)
        } else {
            XCTFail("The pinned test host must support asynchronous image decoding")
        }
        XCTAssertFalse(source.requestedLengths.isEmpty)
        XCTAssertTrue(source.requestedLengths.allSatisfy { $0 > 0 && $0 <= 64 * 1024 })
        playback.stop()
        await close(reopened)
    }

    func testMissingMetadataFailsWithoutFetchingNetwork() async {
        let playback = ArchivePlaybackAsset(source: UnavailableSource())
        do { _ = try await playback.asset.load(.duration); XCTFail("Missing archive metadata must not load") }
        catch { }
        playback.stop()
    }

    func testMissingBytesFailInsteadOfReturningTruncatedContent() async {
        let playback = ArchivePlaybackAsset(source: UnavailableSource(missingBytes: true))
        do { _ = try await playback.asset.load(.duration); XCTFail("Missing bytes must not load") }
        catch { }
        playback.stop()
    }

    func testClosingPlayerCancelsPendingArchiveRead() async {
        let started = expectation(description: "archive metadata requested")
        let cancelled = expectation(description: "archive read cancelled")
        let playback = ArchivePlaybackAsset(source: UnavailableSource(started: started, cancelled: cancelled))
        let loading = Task { try await playback.asset.load(.duration) }
        await fulfillment(of: [started], timeout: 5)
        playback.stop()
        await fulfillment(of: [cancelled], timeout: 5)
        do { _ = try await loading.value; XCTFail("Stopped asset must not finish loading") }
        catch { }
    }

    func testCancellationIsIdempotentAndAlsoCancelsLateReplacement() {
        var cancelled = 0
        let cancellation = ArchivePlaybackCancellation { cancelled += 1 }
        cancellation.cancel()
        cancellation.cancel()
        cancellation.replace(ArchivePlaybackCancellation { cancelled += 1 })
        cancellation.cancel()
        XCTAssertEqual(cancelled, 2)
    }
}
