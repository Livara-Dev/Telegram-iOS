// SPDX-License-Identifier: GPL-2.0-or-later
// Host-only screenshot inspection; never linked into the messaging application.
import AppKit
import Foundation

guard CommandLine.arguments.count == 2,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let bitmap = NSBitmapImageRep(data: image.tiffRepresentation ?? Data()) else {
    exit(1)
}
var minimum = 1.0
var maximum = 0.0
var count = 0
// Sample the central content, excluding status bars and home indicators.
for y in stride(from: bitmap.pixelsHigh / 5, to: bitmap.pixelsHigh * 4 / 5, by: 8) {
    for x in stride(from: bitmap.pixelsWide / 10, to: bitmap.pixelsWide * 9 / 10, by: 8) {
        if let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) {
            let brightness = (Double(color.redComponent) + Double(color.greenComponent) + Double(color.blueComponent)) / 3
            minimum = min(minimum, brightness)
            maximum = max(maximum, brightness)
            count += 1
        }
    }
}
let result: [String: Any] = ["nonblank": count > 0 && maximum - minimum > 0.08,
                             "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh]
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
