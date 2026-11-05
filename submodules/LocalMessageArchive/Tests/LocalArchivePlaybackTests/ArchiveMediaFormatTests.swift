// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import AVFoundation
import AudioToolbox
import CoreGraphics
import ImageIO
import LocalMessageArchive
@testable import LocalArchivePlayback

/// These are real encrypted-store/native-decoder integration tests. They do not
/// claim Telegram event capture, gallery presentation or physical-device support.
final class ArchiveMediaFormatTests: XCTestCase {
    private struct Keys: ArchiveKeyProvider {
        func key(accountId: Int64, createIfMissing: Bool) throws -> Data {
            return Data(repeating: 0x73, count: 32)
        }
    }

    private final class EncryptedSource: ArchivePlaybackSource {
        private let store: ArchiveStore
        private let lock = NSLock()
        private var lengths: [Int] = []
        init(_ store: ArchiveStore) { self.store = store }
        var requestedLengths: [Int] {
            self.lock.lock(); defer { self.lock.unlock() }
            return self.lengths
        }
        func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
            self.store.mediaInfo(eventKey: "format-fixture", slot: 0) { result in
                completion((try? result.get()).flatMap { $0 }.map {
                    ArchivePlaybackInfo(mimeType: $0.mimeType, byteCount: $0.byteCount)
                })
            }
            return ArchivePlaybackCancellation()
        }
        func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation {
            self.lock.lock(); self.lengths.append(count); self.lock.unlock()
            self.store.mediaRange(eventKey: "format-fixture", slot: 0, offset: offset, count: count) {
                completion((try? $0.get()) ?? nil)
            }
            return ArchivePlaybackCancellation()
        }
    }

    private func result<Value>(_ work: (@escaping (Result<Value, ArchiveError>) -> Void) -> Void) async throws -> Value {
        return try await withCheckedThrowingContinuation { continuation in
            work { continuation.resume(with: $0) }
        }
    }

    private func close(_ store: ArchiveStore) async {
        await withCheckedContinuation { continuation in store.close { continuation.resume() } }
    }

    /// The only plaintext file is the synthetic received-cache source. It is
    /// deleted before the store is reopened and before any archived decoding.
    /// Decoder input then comes exclusively from SQLCipher reads into memory.
    private func withReopenedFixture(_ fixture: ArchiveMediaFixture,
                                     body: (ArchiveStore, Data) async throws -> Void) async throws {
        let bytes = try XCTUnwrap(Data(base64Encoded: fixture.encoded))
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("archive-format-" + UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store: ArchiveStore = try await self.result {
            ArchiveStore.open(baseDirectory: root, accountId: 18, keyProvider: Keys(), completion: $0)
        }
        do {
            let event = ArchiveEvent(eventKey: "format-fixture",
                                     message: ArchiveMessageID(accountId: 18, peerId: 19, namespace: 0, messageId: 1),
                                     kind: .deleted, observedAt: 100,
                                     content: ArchiveContent(text: "", sentAt: 90))
            let write: ArchiveWriteResult = try await self.result {
                store.record([ArchiveCapture(event: event, context: ArchiveCaptureContext(ordinaryCloudMessage: true))],
                             generation: 0, policy: ArchiveCapturePolicy(enabled: true), completion: $0)
            }
            XCTAssertEqual(write.inserted, 1)
            let cache = root.appendingPathComponent("synthetic-telegram-cache")
            try bytes.write(to: cache, options: .completeFileProtection)
            let saved: Bool = try await self.result {
                store.recordMediaFile(eventKey: "format-fixture", slot: 0, kind: fixture.kind,
                                      mimeType: fixture.mimeType, source: cache, expectedSize: Int64(bytes.count),
                                      generation: 0, completion: $0)
            }
            XCTAssertTrue(saved)
            try FileManager.default.removeItem(at: cache)
            XCTAssertFalse(FileManager.default.fileExists(atPath: cache.path))
        } catch {
            await self.close(store)
            throw error
        }
        await self.close(store)
        let reopened: ArchiveStore = try await self.result {
            ArchiveStore.open(baseDirectory: root, accountId: 18, keyProvider: Keys(), completion: $0)
        }
        do {
            let archived: ArchivedMediaInfo? = try await self.result {
                reopened.mediaInfo(eventKey: "format-fixture", slot: 0, completion: $0)
            }
            XCTAssertEqual(archived?.kind, fixture.kind)
            XCTAssertEqual(archived?.mimeType, fixture.mimeType)
            XCTAssertEqual(archived?.byteCount, Int64(bytes.count))
            let roundTrip: Data? = try await self.result {
                reopened.mediaRange(eventKey: "format-fixture", slot: 0, offset: 0, count: bytes.count, completion: $0)
            }
            XCTAssertTrue(roundTrip == bytes, "Synthetic media must survive encryption, cache removal and reopen unchanged")
            try await body(reopened, bytes)
        } catch {
            await self.close(reopened)
            throw error
        }
        await self.close(reopened)
    }

    private func imagePixels(_ image: CGImage) throws -> Data {
        var pixels = Data(count: image.width * image.height * 4)
        let drawn = pixels.withUnsafeMutableBytes { storage -> Bool in
            guard let context = CGContext(data: storage.baseAddress, width: image.width, height: image.height,
                                          bitsPerComponent: 8, bytesPerRow: image.width * 4,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
            return true
        }
        XCTAssertTrue(drawn)
        return pixels
    }

    private func archivedBytes(_ store: ArchiveStore) async throws -> Data {
        let media: ArchivedMedia? = try await self.result {
            store.media(eventKey: "format-fixture", slot: 0, completion: $0)
        }
        return try XCTUnwrap(media?.data)
    }

    private func assertRangeContract(_ source: EncryptedSource, _ playback: ArchivePlaybackAsset,
                                     file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(playback.asset.referenceRestrictions, .forbidAll, file: file, line: line)
        XCTAssertEqual(playback.asset.url.scheme, "ayugram-archive", file: file, line: line)
        XCTAssertFalse(source.requestedLengths.isEmpty, file: file, line: line)
        XCTAssertTrue(source.requestedLengths.allSatisfy { $0 > 0 && $0 <= 64 * 1024 }, file: file, line: line)
    }

    private func assertAudioDecodes(_ playback: ArchivePlaybackAsset,
                                    file: StaticString = #filePath, line: UInt = #line) async throws {
        let tracks = try await playback.asset.loadTracks(withMediaType: .audio)
        let track = try XCTUnwrap(tracks.first, "Archived audio must expose an audio track", file: file, line: line)
        let reader = try AVAssetReader(asset: playback.asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false
        ])
        guard reader.canAdd(output) else {
            XCTFail("Native reader rejected the synthetic audio output", file: file, line: line)
            return
        }
        reader.add(output)
        guard reader.startReading() else {
            XCTFail("Native reader could not start synthetic audio decoding", file: file, line: line)
            return
        }
        var buffers = 0
        var samples = 0
        while let buffer = output.copyNextSampleBuffer() {
            buffers += 1
            samples += CMSampleBufferGetNumSamples(buffer)
            if let description = CMSampleBufferGetFormatDescription(buffer),
               let format = CMAudioFormatDescriptionGetStreamBasicDescription(description) {
                XCTAssertEqual(format.pointee.mFormatID, kAudioFormatLinearPCM, file: file, line: line)
            } else {
                XCTFail("Decoded audio must have a PCM format description", file: file, line: line)
            }
            if buffers > 128 || samples > 480_000 {
                reader.cancelReading()
                XCTFail("Synthetic audio exceeded its finite fixture bounds", file: file, line: line)
                return
            }
        }
        XCTAssertGreaterThan(samples, 0, "Archived audio must actually decode PCM samples", file: file, line: line)
        XCTAssertEqual(reader.status, .completed, file: file, line: line)
    }

    func testPNGAndJPEGPhotosDecodeAfterEncryptedReopenAndCacheRemoval() async throws {
        for fixture in [ArchiveMediaFixture.png, .jpeg] {
            try await self.withReopenedFixture(fixture) { store, _ in
                let data = try await self.archivedBytes(store)
                let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
                let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
                XCTAssertEqual(image.width, 16)
                XCTAssertEqual(image.height, 16)
                XCTAssertEqual(try self.imagePixels(image).count, 16 * 16 * 4)
            }
        }
    }

    func testTrueGIFRetainsAndDecodesBothFramesAfterEncryptedReopen() async throws {
        try await self.withReopenedFixture(.gif) { store, _ in
            let data = try await self.archivedBytes(store)
            let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
            XCTAssertEqual(CGImageSourceGetCount(source), 2)
            let first = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
            let second = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 1, nil))
            XCTAssertEqual(first.width, 16)
            XCTAssertEqual(second.height, 16)
            XCTAssertNotEqual(try self.imagePixels(first), try self.imagePixels(second))
        }
    }

    func testMP4AnimationDecodesFromEncryptedRangesAfterCacheRemoval() async throws {
        try await self.withReopenedFixture(.animation) { store, _ in
            let source = EncryptedSource(store)
            let playback = ArchivePlaybackAsset(source: source)
            defer { playback.stop() }
            let generator = AVAssetImageGenerator(asset: playback.asset)
            if #available(iOS 16.0, macOS 13.0, *) {
                let frame = try await generator.image(at: .zero).image
                XCTAssertEqual(frame.width, 16)
                XCTAssertEqual(frame.height, 16)
            } else {
                XCTFail("Native qualification requires asynchronous frame decoding")
            }
            self.assertRangeContract(source, playback)
        }
    }

    func testRoundVideoDecodesVideoAndAudioFromEncryptedRangesAfterReopen() async throws {
        try await self.withReopenedFixture(.roundVideo) { store, _ in
            let source = EncryptedSource(store)
            let playback = ArchivePlaybackAsset(source: source)
            defer { playback.stop() }
            let generator = AVAssetImageGenerator(asset: playback.asset)
            if #available(iOS 16.0, macOS 13.0, *) {
                let frame = try await generator.image(at: .zero).image
                XCTAssertEqual(frame.width, 16)
                XCTAssertEqual(frame.height, 16)
            } else {
                XCTFail("Native qualification requires asynchronous frame decoding")
            }
            try await self.assertAudioDecodes(playback)
            self.assertRangeContract(source, playback)
        }
    }

    func testAACVoiceDecodesPCMFromEncryptedRangesAfterReopen() async throws {
        try await self.withReopenedFixture(.aacVoice) { store, _ in
            let source = EncryptedSource(store)
            let playback = ArchivePlaybackAsset(source: source)
            defer { playback.stop() }
            try await self.assertAudioDecodes(playback)
            self.assertRangeContract(source, playback)
        }
    }

    /// A real Ogg/Opus fixture, not AAC relabelled as voice. A failure is an
    /// unsupported codec/integration result to fix; it must never be skipped or
    /// replaced with a plaintext file fallback to manufacture a passing test.
    func testOGGOpusVoiceDecodesPCMFromEncryptedRangesAfterReopen() async throws {
        try await self.withReopenedFixture(.opusVoice) { store, _ in
            let source = EncryptedSource(store)
            let playback = ArchivePlaybackAsset(source: source)
            defer { playback.stop() }
            try await self.assertAudioDecodes(playback)
            self.assertRangeContract(source, playback)
        }
    }

    func testPDFDocumentRemainsReadableInMemoryAfterEncryptedReopen() async throws {
        try await self.withReopenedFixture(.pdf) { store, _ in
            let data = try await self.archivedBytes(store)
            let provider = try XCTUnwrap(CGDataProvider(data: data as CFData))
            let document = try XCTUnwrap(CGPDFDocument(provider))
            XCTAssertEqual(document.numberOfPages, 1)
            let page = try XCTUnwrap(document.page(at: 1))
            XCTAssertEqual(page.getBoxRect(.mediaBox).width, 16)
            XCTAssertEqual(page.getBoxRect(.mediaBox).height, 16)
        }
    }
}
