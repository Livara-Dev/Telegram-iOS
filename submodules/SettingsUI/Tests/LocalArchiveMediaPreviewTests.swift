// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import XCTest
import ImageIO
import PDFKit
@testable import SettingsUI

final class LocalArchiveMediaPreviewTests: XCTestCase {
    private func gif(width: Int = 16, height: Int = 16, delays: [Double] = [0.2, 0.4]) throws -> Data {
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(data as CFMutableData, "com.compuserve.gif" as CFString, delays.count, nil))
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: CGFloat(width), height: CGFloat(height)), format: format)
        for (index, delay) in delays.enumerated() {
            let image = renderer.image { context in
                (index.isMultiple(of: 2) ? UIColor.red : UIColor.blue).setFill()
                context.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
            }
            CGImageDestinationAddImage(destination, try XCTUnwrap(image.cgImage), [
                kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: delay]
            ] as CFDictionary)
        }
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }

    private func pdf(pages: Int = 2, size: CGSize = CGSize(width: 32, height: 16)) -> Data {
        return UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: size)).pdfData { context in
            for _ in 0..<pages {
                context.beginPage()
                UIColor.red.setFill()
                context.cgContext.fill(CGRect(x: 1, y: 1, width: 8, height: 8))
            }
        }
    }

    func testSharedRoutingUsesActualContainerForHistoryAndGalleryAnimations() {
        XCTAssertEqual(localArchiveMediaRoute(kind: "animation", mimeType: "video/mp4"), .playback)
        XCTAssertEqual(localArchiveMediaRoute(kind: "animation", mimeType: "image/gif"), .animatedGIF)
        XCTAssertEqual(localArchiveMediaRoute(kind: "document", mimeType: "application/pdf"), .pdf)
        XCTAssertEqual(localArchiveMediaRoute(kind: "document", mimeType: "image/png"), .image)
        XCTAssertEqual(localArchiveMediaRoute(kind: "voice", mimeType: "audio/ogg"), .playback)
        XCTAssertEqual(localArchiveMediaRoute(kind: "videoMessage", mimeType: " Video/MP4; charset=binary "), .playback)
    }

    func testUnknownActiveOrExternalDocumentFormatsNeverBecomeAnImageOrPlayRoute() {
        for mime in ["application/zip", "text/html", "image/svg+xml", "application/octet-stream", "", "video/unknown", "application/pdf\0", String(repeating: "a", count: 257)] {
            XCTAssertEqual(localArchiveMediaRoute(kind: "animation", mimeType: mime), .unsupported)
        }
    }

    func testGIFDecodesDistinctFramesAndPreservesUnequalTimingWithinBudget() throws {
        let preview = try XCTUnwrap(localArchiveGIFPreview(try self.gif()))
        XCTAssertEqual(preview.frames.count, 2)
        XCTAssertEqual(preview.frameDurations[0], 0.2, accuracy: 0.01)
        XCTAssertEqual(preview.frameDurations[1], 0.4, accuracy: 0.01)
        XCTAssertEqual(preview.duration, 0.6, accuracy: 0.01)
        XCTAssertGreaterThan(preview.decodedBytes, 0)
        XCTAssertLessThanOrEqual(preview.decodedBytes, 24 * 1024 * 1024)
        XCTAssertEqual(preview.frames[0].cgImage?.width, 16)
        XCTAssertEqual(preview.frames[1].cgImage?.height, 16)
        XCTAssertNotEqual(preview.frames[0].pngData(), preview.frames[1].pngData())
        XCTAssertEqual(preview.frameIndex(at: 0), 0)
        XCTAssertEqual(preview.frameIndex(at: 0.21), 1)
        XCTAssertEqual(preview.frameIndex(at: 0.59), 1)
        XCTAssertEqual(preview.frameIndex(at: 0.61), 0)
        XCTAssertEqual(preview.frameIndex(at: .nan), 0)
        XCTAssertEqual(preview.frameIndex(at: -1), 0)
    }

    func testGIFInventoryRejectsFrameMemoryDimensionAndDurationLimitsBeforeRendering() throws {
        let bytes = try self.gif()
        var limits = LocalArchiveGIFLimits()
        limits.maximumFrames = 1
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        limits = LocalArchiveGIFLimits(); limits.maximumDecodedBytes = 1024
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        limits = LocalArchiveGIFLimits(); limits.maximumSourceDimension = 8
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        limits = LocalArchiveGIFLimits(); limits.maximumSourcePixels = 128
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        limits = LocalArchiveGIFLimits(); limits.maximumDuration = 0.1
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        limits = LocalArchiveGIFLimits(); limits.maximumDimension = 0
        XCTAssertNil(localArchiveGIFPreview(bytes, limits: limits))
        XCTAssertNil(localArchiveGIFPreview(Data([1, 2, 3])))
        XCTAssertNil(localArchiveGIFPreview(Data(repeating: 0, count: 16 * 1024 * 1024 + 1)))
    }

    func testGIFDownsamplesOnWorkerAndCancellationStopsAnInventoryInProgress() throws {
        let bytes = try self.gif()
        let finished = self.expectation(description: "bounded worker GIF decode")
        DispatchQueue.global(qos: .utility).async {
            XCTAssertFalse(Thread.isMainThread)
            var limits = LocalArchiveGIFLimits(); limits.maximumDimension = 8
            let preview = localArchiveGIFPreview(bytes, limits: limits)
            XCTAssertEqual(preview?.frames.first?.cgImage?.width, 8)
            XCTAssertLessThanOrEqual(preview?.decodedBytes ?? Int.max, 1024)
            finished.fulfill()
        }
        self.wait(for: [finished], timeout: 5)
        let cancellation = LocalArchivePreviewCancellation()
        var checks = 0
        XCTAssertNil(localArchiveGIFPreview(bytes, cancelled: {
            checks += 1
            if checks == 3 { cancellation.cancel() }
            return cancellation.isCancelled
        }))
        XCTAssertTrue(cancellation.isCancelled)
    }

    func testPDFIsParsedAndRasterizedFromMemoryWithBoundedPagePixels() throws {
        let preview = try XCTUnwrap(LocalArchivePDFPreview(data: self.pdf()))
        XCTAssertEqual(preview.pageCount, 2)
        let finished = self.expectation(description: "worker-only PDF raster")
        DispatchQueue.global(qos: .utility).async {
            XCTAssertFalse(Thread.isMainThread)
            let image = preview.image(pageIndex: 0)
            XCTAssertEqual(image?.cgImage?.width, 32)
            XCTAssertEqual(image?.cgImage?.height, 16)
            if let pixels = image?.cgImage {
                XCTAssertLessThanOrEqual(pixels.bytesPerRow * pixels.height, LocalArchivePDFPreview.maximumDecodedBytes)
            } else { XCTFail("The native PDF renderer did not produce a page") }
            finished.fulfill()
        }
        self.wait(for: [finished], timeout: 5)
        XCTAssertNil(preview.image(pageIndex: -1))
        XCTAssertNil(preview.image(pageIndex: 2))
    }

    func testPDFPageRotationPreservesTheVisiblePageAspectRatio() throws {
        let document = try XCTUnwrap(PDFDocument(data: self.pdf(pages: 1)))
        let page = try XCTUnwrap(document.page(at: 0))
        page.rotation = 90
        let data = try XCTUnwrap(document.dataRepresentation())
        let preview = try XCTUnwrap(LocalArchivePDFPreview(data: data))
        let image = try XCTUnwrap(preview.image(pageIndex: 0))
        XCTAssertEqual(image.cgImage?.width, 16)
        XCTAssertEqual(image.cgImage?.height, 32)
    }

    func testPDFRejectsHugePagesPageCountsInvalidBytesAndCancelledPreviews() throws {
        XCTAssertNil(LocalArchivePDFPreview(data: Data([1, 2, 3])))
        XCTAssertNil(LocalArchivePDFPreview(data: Data(repeating: 0, count: 16 * 1024 * 1024 + 1)))
        XCTAssertNil(LocalArchivePDFPreview(data: self.pdf(pages: 201)))
        let huge = try XCTUnwrap(LocalArchivePDFPreview(data: self.pdf(pages: 1, size: CGSize(width: 50000, height: 50000))))
        XCTAssertNil(huge.image(pageIndex: 0))
        let bytes = self.pdf(pages: 1)
        let cancellation = LocalArchivePreviewCancellation(); cancellation.cancel()
        XCTAssertNil(LocalArchivePDFPreview(data: bytes, cancelled: { cancellation.isCancelled }))
        let preview = try XCTUnwrap(LocalArchivePDFPreview(data: bytes))
        XCTAssertNil(preview.image(pageIndex: 0, cancelled: { cancellation.isCancelled }))
    }
}
