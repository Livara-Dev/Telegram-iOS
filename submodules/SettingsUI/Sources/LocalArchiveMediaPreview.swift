// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import ImageIO
import PDFKit

/// Both history and gallery use the retained MIME type, never just a Telegram
/// media-kind label: Telegram animations can contain either MP4 or real GIF.
enum LocalArchiveMediaRoute: Equatable {
    case image
    case animatedGIF
    case playback
    case pdf
    case unsupported
}

func localArchiveMediaRoute(kind: String, mimeType: String) -> LocalArchiveMediaRoute {
    guard mimeType.utf8.count <= 256, !mimeType.contains("\0") else { return .unsupported }
    let raw = mimeType.split(separator: ";", maxSplits: 1).first.map(String.init) ?? ""
    let mime = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    // A video/voice label with an image MIME is a mismatched or thumbnail-only
    // record, not a playable saved message. Never silently open it as a photo.
    if ["video", "videoMessage"].contains(kind), !mime.hasPrefix("video/") { return .unsupported }
    if kind == "voice", !mime.hasPrefix("audio/") { return .unsupported }
    if mime == "image/gif" { return .animatedGIF }
    if ["image/jpeg", "image/png", "image/heic", "image/heif", "image/webp", "image/tiff", "image/bmp"].contains(mime) { return .image }
    if mime == "application/pdf" { return .pdf }
    if ["video/mp4", "video/quicktime", "video/x-m4v", "audio/mp4", "audio/aac", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg", "audio/opus"].contains(mime) {
        return .playback
    }
    return .unsupported
}

final class LocalArchivePreviewCancellation {
    private let lock = NSLock()
    private var cancelled = false
    var isCancelled: Bool {
        self.lock.lock(); defer { self.lock.unlock() }
        return self.cancelled
    }
    func cancel() {
        self.lock.lock(); self.cancelled = true; self.lock.unlock()
    }
}

struct LocalArchiveGIFLimits {
    var maximumFrames = 80
    var maximumDimension = 1024
    var maximumSourceDimension = 4096
    var maximumSourcePixels = 16 * 1024 * 1024
    var maximumDecodedBytes = 24 * 1024 * 1024
    var maximumDuration: TimeInterval = 60
}

struct LocalArchiveGIFPreview {
    let frames: [UIImage]
    let frameDurations: [TimeInterval]
    let decodedBytes: Int
    var duration: TimeInterval { return self.frameDurations.reduce(0, +) }

    func frameIndex(at elapsed: TimeInterval) -> Int {
        guard elapsed.isFinite, elapsed >= 0, !self.frames.isEmpty,
              self.frames.count == self.frameDurations.count, self.duration > 0 else { return 0 }
        var position = elapsed.truncatingRemainder(dividingBy: self.duration)
        for (index, delay) in self.frameDurations.enumerated() {
            if position < delay { return index }
            position -= delay
        }
        return max(0, self.frames.count - 1)
    }
}

/// Called on the preview worker. Validate the entire size/timing inventory
/// before decoding a frame; never allocate an unbounded UIImage animation.
func localArchiveGIFPreview(_ data: Data, limits: LocalArchiveGIFLimits = LocalArchiveGIFLimits(),
                            cancelled: () -> Bool = { false }) -> LocalArchiveGIFPreview? {
    guard !cancelled(), data.count <= 16 * 1024 * 1024,
          limits.maximumFrames > 0, (1...1024).contains(limits.maximumDimension),
          limits.maximumDecodedBytes > 0, limits.maximumDuration.isFinite, limits.maximumDuration > 0,
          let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
          CGImageSourceGetType(source).map({ $0 as String }) == "com.compuserve.gif" else { return nil }
    let count = CGImageSourceGetCount(source)
    guard count > 0, count <= limits.maximumFrames else { return nil }
    var delays: [TimeInterval] = []
    var estimatedBytes = 0
    for index in 0..<count {
        guard !cancelled(),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [String: Any],
              let width = (properties[kCGImagePropertyPixelWidth as String] as? NSNumber)?.intValue,
              let height = (properties[kCGImagePropertyPixelHeight as String] as? NSNumber)?.intValue,
              width > 0, height > 0, width <= limits.maximumSourceDimension, height <= limits.maximumSourceDimension,
              width <= limits.maximumSourcePixels / height else { return nil }
        let scale = min(1.0, Double(limits.maximumDimension) / Double(max(width, height)))
        let decodedWidth = max(1, Int(ceil(Double(width) * scale)))
        let decodedHeight = max(1, Int(ceil(Double(height) * scale)))
        let bytes = decodedWidth * decodedHeight * 4
        guard bytes <= limits.maximumDecodedBytes - estimatedBytes else { return nil }
        estimatedBytes += bytes
        let gif = properties[kCGImagePropertyGIFDictionary as String] as? [String: Any]
        let rawDelay = (gif?[kCGImagePropertyGIFUnclampedDelayTime as String] as? NSNumber)?.doubleValue
            ?? (gif?[kCGImagePropertyGIFDelayTime as String] as? NSNumber)?.doubleValue ?? 0.1
        guard rawDelay.isFinite, rawDelay >= 0 else { return nil }
        delays.append(min(10, max(0.02, rawDelay)))
        guard delays.reduce(0, +) <= limits.maximumDuration else { return nil }
    }
    var frames: [UIImage] = []
    var decodedBytes = 0
    for index in 0..<count {
        guard !cancelled(), let frame = CGImageSourceCreateThumbnailAtIndex(source, index, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: limits.maximumDimension,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true
        ] as CFDictionary), frame.width <= limits.maximumDimension, frame.height <= limits.maximumDimension else { return nil }
        guard frame.bytesPerRow <= (limits.maximumDecodedBytes - decodedBytes) / frame.height else { return nil }
        let bytes = frame.bytesPerRow * frame.height
        decodedBytes += bytes
        frames.append(UIImage(cgImage: frame))
    }
    guard !cancelled() else { return nil }
    return LocalArchiveGIFPreview(frames: frames, frameDurations: delays, decodedBytes: decodedBytes)
}

/// PDFKit parses only the in-memory archive bytes. Previewing raster pages
/// rather than an interactive PDFView prevents URI/actions, embedded media,
/// JavaScript and annotations from becoming navigation or network requests.
final class LocalArchivePDFPreview {
    static let maximumPages = 200
    static let maximumDimension = 1536
    static let maximumDecodedBytes = maximumDimension * maximumDimension * 4
    let pageCount: Int
    private let document: PDFDocument
    private let lock = NSLock()

    init?(data: Data, cancelled: () -> Bool = { false }) {
        guard !cancelled(), !data.isEmpty, data.count <= 16 * 1024 * 1024,
              data.starts(with: Data("%PDF-".utf8)),
              let document = PDFDocument(data: data), !document.isEncrypted, !document.isLocked,
              (1...Self.maximumPages).contains(document.pageCount), !cancelled() else { return nil }
        self.document = document
        self.pageCount = document.pageCount
    }

    /// Serialized even when a cancelled render and the next page overlap on
    /// utility workers. No file I/O, PDF actions or system document viewer.
    func image(pageIndex: Int, cancelled: () -> Bool = { false }) -> UIImage? {
        guard !cancelled(), (0..<self.pageCount).contains(pageIndex) else { return nil }
        self.lock.lock(); defer { self.lock.unlock() }
        guard !cancelled(), let page = self.document.page(at: pageIndex), let reference = page.pageRef else { return nil }
        let bounds = reference.getBoxRect(.cropBox)
        guard bounds.width.isFinite, bounds.height.isFinite, bounds.minX.isFinite, bounds.minY.isFinite,
              bounds.width > 0, bounds.height > 0, bounds.width <= 20_000, bounds.height <= 20_000,
              bounds.width * bounds.height <= 80_000_000 else { return nil }
        let rotation = (reference.rotationAngle % 360 + 360) % 360
        let rotated = rotation == 90 || rotation == 270
        let pageWidth = rotated ? bounds.height : bounds.width
        let pageHeight = rotated ? bounds.width : bounds.height
        let scale = min(1, CGFloat(Self.maximumDimension) / max(pageWidth, pageHeight))
        let width = max(1, Int(ceil(pageWidth * scale)))
        let height = max(1, Int(ceil(pageHeight * scale)))
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                      bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        context.setFillColor(UIColor.white.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.concatenate(reference.getDrawingTransform(.cropBox, rect: CGRect(x: 0, y: 0, width: width, height: height), rotate: 0, preserveAspectRatio: true))
        context.drawPDFPage(reference)
        guard !cancelled(), let image = context.makeImage() else { return nil }
        return UIImage(cgImage: image)
    }
}
