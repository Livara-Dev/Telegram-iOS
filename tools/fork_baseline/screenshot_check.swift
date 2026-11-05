// SPDX-License-Identifier: GPL-2.0-or-later
// Host-only, headless inspection; never linked into the messaging application.
import CoreGraphics
import Foundation
import ImageIO

private let maximumSourceBytes = 64 * 1024 * 1024
private let maximumDimension = 16_384
private let maximumSourcePixels = 64 * 1024 * 1024
private let maximumThumbnailDimension = 256
private let rgbaFormat = CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue

private enum InspectionError: Error {
    case invalidImage
}

private struct Inspection {
    let width: Int
    let height: Int
    let nonblank: Bool

    var json: [String: Any] {
        return ["width": self.width, "height": self.height, "nonblank": self.nonblank]
    }
}

private func inspect(_ data: Data) throws -> Inspection {
    guard !data.isEmpty, data.count <= maximumSourceBytes,
          let source = CGImageSourceCreateWithData(data as CFData,
              [kCGImageSourceShouldCache: false] as CFDictionary),
          CGImageSourceGetCount(source) == 1,
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let widthValue = properties[kCGImagePropertyPixelWidth] as? NSNumber,
          let heightValue = properties[kCGImagePropertyPixelHeight] as? NSNumber else {
        throw InspectionError.invalidImage
    }
    let width = widthValue.intValue
    let height = heightValue.intValue
    guard width > 0, height > 0, width <= maximumDimension, height <= maximumDimension,
          widthValue.doubleValue == Double(width), heightValue.doubleValue == Double(height),
          width <= maximumSourcePixels / height else {
        throw InspectionError.invalidImage
    }
    // ImageIO downsamples during decoding. Never decode a full-size TIFF or
    // allocate a bitmap proportional to the screenshot's source dimensions.
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: maximumThumbnailDimension,
        kCGImageSourceCreateThumbnailWithTransform: false,
        kCGImageSourceShouldCacheImmediately: true
    ]
    guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary),
          image.width > 0, image.height > 0,
          image.width <= maximumThumbnailDimension, image.height <= maximumThumbnailDimension else {
        throw InspectionError.invalidImage
    }
    let sampleWidth = image.width
    let sampleHeight = image.height
    var pixels = [UInt8](repeating: 0, count: sampleWidth * sampleHeight * 4)
    let drawn = pixels.withUnsafeMutableBytes { storage -> Bool in
        guard let context = CGContext(data: storage.baseAddress, width: sampleWidth, height: sampleHeight,
                                      bitsPerComponent: 8, bytesPerRow: sampleWidth * 4,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: rgbaFormat) else {
            return false
        }
        context.interpolationQuality = .none
        context.draw(image, in: CGRect(x: 0, y: 0, width: CGFloat(sampleWidth), height: CGFloat(sampleHeight)))
        return true
    }
    guard drawn else { throw InspectionError.invalidImage }
    var minimum = 765
    var maximum = 0
    var count = 0
    // The central 80% x 60% excludes status bars and home indicators. Integer
    // channel sums give the original mean-RGB contrast without per-pixel objects.
    for y in (sampleHeight / 5)..<(sampleHeight * 4 / 5) {
        for x in (sampleWidth / 10)..<(sampleWidth * 9 / 10) {
            let index = (y * sampleWidth + x) * 4
            let brightness = Int(pixels[index]) + Int(pixels[index + 1]) + Int(pixels[index + 2])
            minimum = min(minimum, brightness)
            maximum = max(maximum, brightness)
            count += 1
        }
    }
    return Inspection(width: width, height: height,
                      nonblank: count > 0 && Double(maximum - minimum) / 765.0 > 0.08)
}

private func inspectFile(_ path: String) throws -> Inspection {
    let attributes = try FileManager.default.attributesOfItem(atPath: path)
    guard attributes[.type] as? FileAttributeType == .typeRegular,
          let size = attributes[.size] as? NSNumber,
          size.int64Value > 0, size.int64Value <= Int64(maximumSourceBytes) else {
        throw InspectionError.invalidImage
    }
    return try inspect(Data(contentsOf: URL(fileURLWithPath: path), options: .mappedIfSafe))
}

private func selfTest() throws {
    let width = 384
    let height = 512
    for control in 0..<3 {
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let offset = (y * width + x) * 4
                let light = control == 1 || (control == 2 && x >= width / 4 && x < width * 3 / 4
                                            && y >= height / 4 && y < height * 3 / 4)
                let value: UInt8 = light ? 255 : 0
                pixels[offset] = value
                pixels[offset + 1] = value
                pixels[offset + 2] = value
                pixels[offset + 3] = 255
            }
        }
        guard let provider = CGDataProvider(data: Data(pixels) as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                                  bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGBitmapInfo(rawValue: rgbaFormat), provider: provider,
                                  decode: nil, shouldInterpolate: false, intent: .defaultIntent) else {
            throw InspectionError.invalidImage
        }
        let encoded = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(encoded, "public.png" as CFString, 1, nil) else {
            throw InspectionError.invalidImage
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw InspectionError.invalidImage }
        // Round-trip PNGs exercise the exact metadata, thumbnail and RGBA path.
        let result = try inspect(encoded as Data)
        guard result.width == width, result.height == height, result.nonblank == (control == 2) else {
            throw InspectionError.invalidImage
        }
    }
}

private func emit(_ value: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}

do {
    guard CommandLine.arguments.count == 2 else { throw InspectionError.invalidImage }
    if CommandLine.arguments[1] == "--self-test" {
        try selfTest()
        try emit(["self_test_passed": true, "cases": 3])
    } else {
        try emit(inspectFile(CommandLine.arguments[1]).json)
    }
} catch {
    // No private paths, image content or framework errors enter CI logs.
    exit(1)
}
