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
        private var informationRequests = 0
        init(_ store: ArchiveStore) { self.store = store }
        var requestedLengths: [Int] { lock.lock(); defer { lock.unlock() }; return lengths }
        var diagnosticCounts: String {
            lock.lock(); defer { lock.unlock() }
            return "metadataRequests=\(informationRequests), rangeRequests=\(lengths.count), requestedBytes=\(lengths.reduce(0, +))"
        }
        func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
            lock.lock(); informationRequests += 1; lock.unlock()
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

    /// Uses the same delegate contract without involving SQLCipher or the
    /// archive worker, so a decoder failure can be separated from storage.
    private final class MemorySource: ArchivePlaybackSource {
        let bytes: Data
        private let lock = NSLock()
        private var informationRequests = 0
        private var rangeRequests = 0
        private var requestedBytes = 0
        init(_ bytes: Data) { self.bytes = bytes }
        var requestedRangeCount: Int { lock.lock(); defer { lock.unlock() }; return rangeRequests }
        var diagnosticCounts: String {
            lock.lock(); defer { lock.unlock() }
            return "metadataRequests=\(informationRequests), rangeRequests=\(rangeRequests), requestedBytes=\(requestedBytes)"
        }
        func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
            lock.lock(); informationRequests += 1; lock.unlock()
            completion(ArchivePlaybackInfo(mimeType: "video/mp4", byteCount: Int64(bytes.count)))
            return ArchivePlaybackCancellation()
        }
        func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation {
            lock.lock(); rangeRequests += 1; requestedBytes += count; lock.unlock()
            guard offset >= 0, offset <= Int64(bytes.count), count >= 0,
                  count <= bytes.count - Int(offset) else {
                completion(nil)
                return ArchivePlaybackCancellation()
            }
            completion(bytes.subdata(in: Int(offset)..<(Int(offset) + count)))
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

    /// Reports only synthetic fixture stage/counters and error codes. It never
    /// prints archived bytes, SQL paths, account identifiers or user filenames.
    private func assertMovieDecodes(_ asset: AVURLAsset, stage: String,
                                   diagnostics: () -> String,
                                   file: StaticString = #filePath, line: UInt = #line) async -> Bool {
        do {
            let duration = try await asset.load(.duration)
            XCTAssertGreaterThan(CMTimeGetSeconds(duration), 0, file: file, line: line)
            let playable = try await asset.load(.isPlayable)
            XCTAssertTrue(playable, file: file, line: line)
            let generator = AVAssetImageGenerator(asset: asset)
            if #available(iOS 16.0, macOS 13.0, *) {
                let frame = try await generator.image(at: .zero).image
                XCTAssertEqual(frame.width, 16, file: file, line: line)
                XCTAssertEqual(frame.height, 16, file: file, line: line)
            } else {
                XCTFail("The pinned test host must support asynchronous image decoding", file: file, line: line)
                return false
            }
            return true
        } catch {
            let value = error as NSError
            let underlying = value.userInfo[NSUnderlyingErrorKey] as? NSError
            XCTFail("Synthetic movie stage=\(stage), \(diagnostics()), error=\(value.domain):\(value.code), underlying=\(underlying?.domain ?? "none"):\(underlying?.code ?? 0)", file: file, line: line)
            return false
        }
    }

    private func movieFixture() throws -> Data {
        // Synthetic black H.264 fixture produced locally with FFmpeg; no user media.
        var movie = try XCTUnwrap(Data(base64Encoded: "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAN0bW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAMgAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAp90cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAMgAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAADIAAAEAAABAAAAAAIXbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAyAAAACgBVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABwm1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAYJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MS4xOS4xMDEgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAMg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAHZIAAAAAAAAABhzdHRzAAAAAAAAAAEAAAAFAAACAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAOGN0dHMAAAAAAAAABQAAAAEAAAQAAAAAAQAACgAAAAABAAAEAAAAAAEAAAAAAAAAAQAAAgAAAAAcc3RzYwAAAAAAAAABAAAAAQAAAAUAAAABAAAAKHN0c3oAAAAAAAAAAAAAAAUAAALFAAAADAAAAAwAAAAMAAAADAAAABRzdGNvAAAAAAAAAAEAAAOkAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2MS43LjEwMwAAAAhmcmVlAAAC/W1kYXQAAAKuBgX//6rcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMTA4IDMxZTE5ZjkgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDIzIC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAPZYiEADP//vbsvgU2FMjBAAAACEGaJGxCv/7AAAAACEGeQniF/8GBAAAACAGeYXRCv8SAAAAACAGeY2pCv8SB"))
        // A valid trailing free atom increases the source without changing sample offsets.
        movie.append(contentsOf: [0, 2, 0, 8, 0x66, 0x72, 0x65, 0x65])
        movie.append(Data(repeating: 0, count: 128 * 1024))
        return movie
    }

    // Independent synthetic white H.264 fixture, 0.4s versus the black
    // fixture's 0.2s. Different durations detect accidental resource reuse.
    private func secondMovieFixture() throws -> Data {
        return try XCTUnwrap(Data(base64Encoded: "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAOwbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAAZAAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAtt0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAAZAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAGQAAAEAAABAAAAAAJTbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAyAAAAFABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAB/m1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAb5zdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MS4xOS4xMDEgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAANGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAMg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAEDsAAAAAAAAABhzdHRzAAAAAAAAAAEAAAAKAAACAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAYGN0dHMAAAAAAAAACgAAAAEAAAQAAAAAAQAACgAAAAABAAAEAAAAAAEAAAAAAAAAAQAAAgAAAAABAAAKAAAAAAEAAAQAAAAAAQAAAAAAAAABAAACAAAAAAEAAAQAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAKAAAAAQAAADxzdHN6AAAAAAAAAAAAAAAKAAACxQAAAAwAAAAMAAAADAAAAAwAAAASAAAADgAAAAwAAAAMAAAAEgAAABRzdGNvAAAAAAAAAAEAAAPgAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2MS43LjEwMwAAAAhmcmVlAAADR21kYXQAAAKuBgX//6rcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY0IHIzMTA4IDMxZTE5ZjkgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDIzIC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MyBkZWJsb2NrPTE6MDowIGFuYWx5c2U9MHgzOjB4MTEzIG1lPWhleCBzdWJtZT03IHBzeT0xIHBzeV9yZD0xLjAwOjAuMDAgbWl4ZWRfcmVmPTEgbWVfcmFuZ2U9MTYgY2hyb21hX21lPTEgdHJlbGxpcz0xIDh4OGRjdD0xIGNxbT0wIGRlYWR6b25lPTIxLDExIGZhc3RfcHNraXA9MSBjaHJvbWFfcXBfb2Zmc2V0PS0yIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVybGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTMgYl9weXJhbWlkPTIgYl9hZGFwdD0xIGJfYmlhcz0wIGRpcmVjdD0xIHdlaWdodGI9MSBvcGVuX2dvcD0wIHdlaWdodHA9MiBrZXlpbnQ9MjUwIGtleWludF9taW49MjUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAAPZYiEADf//vbw/gU2O5lBAAAACEGaJGxDf/7gAAAACEGeQniF/8GBAAAACAGeYXRCv8SAAAAACAGeY2pCv8SBAAAADkGaaEmoQWiZTAhf//7hAAAACkGehkURLC//wYEAAAAIAZ6ldEK/xIEAAAAIAZ6nakK/xIAAAAAOQZqpSahBbJlMCFf//sA="))
    }

    func testMovieDecodesFromEncryptedArchiveAfterCacheFileRemovalAndRestart() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-playback-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store: ArchiveStore = try await result { ArchiveStore.open(baseDirectory: root, accountId: 8, keyProvider: Keys(), completion: $0) }
        let event = ArchiveEvent(eventKey: "playback", message: ArchiveMessageID(accountId: 8, peerId: 9, namespace: 0, messageId: 1), kind: .deleted, observedAt: 100, content: ArchiveContent(text: "", authorId: nil, sentAt: 90, entities: Data()))
        _ = try await result { store.record([ArchiveCapture(event: event, context: ArchiveCaptureContext(ordinaryCloudMessage: true))], generation: 0, policy: ArchiveCapturePolicy(enabled: true), completion: $0) }
        let movie = try self.movieFixture()
        let cacheFile = root.appendingPathComponent("telegram-cache.mp4")
        try movie.write(to: cacheFile, options: .completeFileProtection)
        // The same locally generated fixture must decode from a protected file
        // before testing cache deletion, encryption and the custom loader.
        let fileControl = AVURLAsset(url: cacheFile, options: [
            AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue
        ])
        guard await self.assertMovieDecodes(fileControl, stage: "protected-fixture-file", diagnostics: { "fixtureBytes=\(movie.count)" }) else {
            await close(store)
            return
        }
        let saved: Bool = try await result { store.recordMediaFile(eventKey: "playback", slot: 0, kind: "video", mimeType: "video/mp4", source: cacheFile, expectedSize: Int64(movie.count), generation: 0, completion: $0) }
        XCTAssertTrue(saved)
        try FileManager.default.removeItem(at: cacheFile)
        await close(store)
        let reopened: ArchiveStore = try await result { ArchiveStore.open(baseDirectory: root, accountId: 8, keyProvider: Keys(), completion: $0) }
        let roundTrip: Data? = try await result { reopened.mediaRange(eventKey: "playback", slot: 0, offset: 0, count: movie.count, completion: $0) }
        XCTAssertTrue(roundTrip == movie, "Synthetic movie bytes must survive encryption and reopen unchanged")
        let source = EncryptedSource(reopened)
        let playback = ArchivePlaybackAsset(source: source)
        XCTAssertEqual(playback.asset.referenceRestrictions, .forbidAll)
        XCTAssertEqual(playback.asset.url.scheme, "ayugram-archive")
        XCTAssertNotNil(playback.asset.url.host.flatMap { UUID(uuidString: $0) })
        XCTAssertTrue(playback.asset.url.path.isEmpty)
        if await self.assertMovieDecodes(playback.asset, stage: "encrypted-archive-loader", diagnostics: { source.diagnosticCounts }) {
            XCTAssertFalse(source.requestedLengths.isEmpty)
            XCTAssertTrue(source.requestedLengths.allSatisfy { $0 > 0 && $0 <= 64 * 1024 })
        }
        playback.stop()
        await close(reopened)
    }

    func testEachAssetHasAnIndependentOpaqueResourceIdentity() {
        let first = ArchivePlaybackAsset(source: MemorySource(Data()))
        let second = ArchivePlaybackAsset(source: MemorySource(Data()))
        defer { first.stop(); second.stop() }
        XCTAssertNotEqual(first.asset.url, second.asset.url)
        for asset in [first.asset, second.asset] {
            XCTAssertEqual(asset.url.scheme, "ayugram-archive")
            XCTAssertNotNil(asset.url.host.flatMap { UUID(uuidString: $0) })
            XCTAssertTrue(asset.url.path.isEmpty)
            XCTAssertNil(asset.url.query)
            XCTAssertEqual(asset.referenceRestrictions, .forbidAll)
        }
    }

    func testFailedAssetDoesNotPoisonIndependentValidAssets() async throws {
        let unavailable = ArchivePlaybackAsset(source: UnavailableSource())
        do { _ = try await unavailable.asset.load(.duration); XCTFail("Unavailable asset must fail") }
        catch { }
        unavailable.stop()

        let firstSource = MemorySource(try self.movieFixture())
        let secondSource = MemorySource(try self.secondMovieFixture())
        let first = ArchivePlaybackAsset(source: firstSource)
        let second = ArchivePlaybackAsset(source: secondSource)
        defer { first.stop(); second.stop() }
        XCTAssertNotEqual(unavailable.asset.url, first.asset.url)
        XCTAssertNotEqual(first.asset.url, second.asset.url)
        XCTAssertEqual(first.asset.referenceRestrictions, .forbidAll)
        XCTAssertEqual(second.asset.referenceRestrictions, .forbidAll)
        let firstDecoded = await self.assertMovieDecodes(first.asset, stage: "first-independent-memory-loader", diagnostics: { firstSource.diagnosticCounts })
        let secondDecoded = await self.assertMovieDecodes(second.asset, stage: "second-independent-memory-loader", diagnostics: { secondSource.diagnosticCounts })
        guard firstDecoded && secondDecoded else { return }
        let firstDuration = try await first.asset.load(.duration)
        let secondDuration = try await second.asset.load(.duration)
        XCTAssertEqual(CMTimeGetSeconds(firstDuration), 0.2, accuracy: 0.01)
        XCTAssertEqual(CMTimeGetSeconds(secondDuration), 0.4, accuracy: 0.01)
        XCTAssertGreaterThan(firstSource.requestedRangeCount, 0)
        XCTAssertGreaterThan(secondSource.requestedRangeCount, 0)
    }

    func testSyntheticMovieDecodesThroughMemoryLoaderWithExternalReferencesForbidden() async throws {
        let source = MemorySource(try movieFixture())
        let playback = ArchivePlaybackAsset(source: source)
        defer { playback.stop() }
        XCTAssertEqual(playback.asset.referenceRestrictions, .forbidAll)
        _ = await self.assertMovieDecodes(playback.asset, stage: "in-memory-loader", diagnostics: { source.diagnosticCounts })
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
