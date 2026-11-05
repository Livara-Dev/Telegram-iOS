// SPDX-License-Identifier: GPL-2.0-or-later
// Host-only public-API controls. Never linked into the application. Input must
// be the exact synthetic fixture; no Telegram keys or accounts are accessed.
import Foundation
import AVFoundation
import LocalAuthentication
import UniformTypeIdentifiers
import CryptoKit

private final class ProbeSource: ArchivePlaybackSource {
    let bytes: Data
    private let lock = NSLock()
    private var infoCount = 0
    private var readCount = 0
    init(_ bytes: Data) { self.bytes = bytes }
    var counts: [String: Int] {
        lock.lock(); defer { lock.unlock() }
        return ["metadata": infoCount, "reads": readCount]
    }
    func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation {
        lock.lock(); infoCount += 1; lock.unlock()
        completion(ArchivePlaybackInfo(mimeType: "video/mp4", byteCount: Int64(bytes.count)))
        return ArchivePlaybackCancellation()
    }
    func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation {
        lock.lock(); readCount += 1; lock.unlock()
        guard offset >= 0, offset <= Int64(bytes.count), count >= 0, count <= bytes.count - Int(offset) else {
            completion(nil); return ArchivePlaybackCancellation()
        }
        completion(bytes.subdata(in: Int(offset)..<(Int(offset) + count)))
        return ArchivePlaybackCancellation()
    }
}

/// This independent control is restricted to the pinned self-contained fixture.
/// Policy variants here do not create an unrestricted application playback API.
private final class ContractLoader: NSObject, AVAssetResourceLoaderDelegate {
    let asset: AVURLAsset
    private let bytes: Data
    private let response: Bool
    private let queue = DispatchQueue(label: "AyuGram.synthetic-contract-probe")
    private let lock = NSLock()
    private var counts: [String: Int] = [:]
    init(bytes: Data, restrictions: AVAssetReferenceRestrictions, path: Bool = true,
         response: Bool = false, mimeOverride: Bool = false) {
        self.bytes = bytes; self.response = response
        let url = URL(string: "ayugram-probe://" + UUID().uuidString.lowercased() + (path ? "/fixture.mp4" : ""))!
        var options: [String: Any] = [AVURLAssetReferenceRestrictionsKey: restrictions.rawValue]
        if mimeOverride, #available(macOS 14.0, *) {
            options[AVURLAssetOverrideMIMETypeKey] = "video/mp4"
        }
        self.asset = AVURLAsset(url: url, options: options)
        super.init()
        asset.resourceLoader.setDelegate(self, queue: queue)
    }
    private func note(_ key: String) {
        lock.lock(); counts[key] = min(10000, (counts[key] ?? 0) + 1); lock.unlock()
    }
    var snapshot: [String: Int] { lock.lock(); defer { lock.unlock() }; return counts }
    func resourceLoader(_ loader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource request: AVAssetResourceLoadingRequest) -> Bool {
        note("requests")
        guard request.request.url == asset.url else {
            note("rejectedURL"); request.finishLoading(with: NSError(domain: "SyntheticProbe", code: 1)); return true
        }
        if response {
            request.response = URLResponse(url: asset.url, mimeType: "video/mp4", expectedContentLength: bytes.count, textEncodingName: nil)
        }
        if let info = request.contentInformationRequest {
            note("information")
            info.contentType = UTType.mpeg4Movie.identifier
            info.contentLength = Int64(bytes.count)
            info.isByteRangeAccessSupported = true
            if #available(macOS 13.0, *) { info.isEntireLengthAvailableOnDemand = true }
        }
        if let data = request.dataRequest {
            note("data")
            let offset = max(data.currentOffset, data.requestedOffset)
            guard offset >= 0, data.requestedLength >= 0 else {
                request.finishLoading(with: NSError(domain: "SyntheticProbe", code: 2)); return true
            }
            if offset < Int64(bytes.count) {
                let count = data.requestsAllDataToEndOfResource ? bytes.count - Int(offset) : min(data.requestedLength, bytes.count - Int(offset))
                if count > 0 { data.respond(with: bytes.subdata(in: Int(offset)..<(Int(offset) + count))); note("responses") }
            }
        } else { note("informationOnly") }
        request.finishLoading()
        return true
    }
}

@main
private struct NativeAPIProbe {
    static func decode(_ asset: AVURLAsset) async -> [String: Any] {
        do {
            let duration = try await asset.load(.duration)
            let playable = try await asset.load(.isPlayable)
            let generator = AVAssetImageGenerator(asset: asset)
            let frame = try await generator.image(at: .zero).image
            return ["decoded": true, "playable": playable, "seconds": CMTimeGetSeconds(duration),
                    "width": frame.width, "height": frame.height]
        } catch {
            let e = error as NSError
            let underlying = e.userInfo[NSUnderlyingErrorKey] as? NSError
            // Do not output exception descriptions, URLs, paths or contents.
            return ["decoded": false, "errorCode": e.code, "underlyingCode": underlying?.code ?? 0]
        }
    }
    static func main() async throws {
        guard CommandLine.arguments.count == 2 else { exit(1) }
        let url = URL(fileURLWithPath: CommandLine.arguments[1])
        let bytes = try Data(contentsOf: url)
        guard bytes.count == 132769,
              SHA256.hash(data: bytes).map({ String(format: "%02x", $0) }).joined() == "f45c335a8a3c11931a15e98022bc39e4d4db517f6758357071ea8f0456abae30" else { exit(1) }
        DispatchQueue.global().asyncAfter(deadline: .now() + 90) { exit(2) }
        var report: [String: Any] = ["hostProbeOnly": true, "iOSVerified": false, "bundleIdentityPresent": Bundle.main.bundleIdentifier != nil]
        let fresh = LAContext(); fresh.interactionNotAllowed = true
        let freshValue = fresh.interactionNotAllowed
        var afterError: NSError?
        let afterCanEvaluate = fresh.canEvaluatePolicy(.deviceOwnerAuthentication, error: &afterError)
        let afterValue = fresh.interactionNotAllowed
        let warm = LAContext()
        var warmError: NSError?
        let warmCanEvaluate = warm.canEvaluatePolicy(.deviceOwnerAuthentication, error: &warmError)
        warm.interactionNotAllowed = true
        let authentication: [String: Any] = ["freshSetGet": freshValue,
                                          "setThenCanEvaluate": afterValue,
                                          "setThenCanEvaluateResult": afterCanEvaluate,
                                          "setThenCanEvaluateError": afterError?.code ?? 0,
                                          "canEvaluateThenSetGet": warm.interactionNotAllowed,
                                          "canEvaluateThenSetResult": warmCanEvaluate,
                                          "canEvaluateThenSetError": warmError?.code ?? 0]
        report["localAuthentication"] = authentication
        var media: [String: Any] = [:]
        media["fileForbidAll"] = await decode(AVURLAsset(url: url, options: [AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue]))
        let source = ProbeSource(bytes)
        let production = ArchivePlaybackAsset(source: source)
        var prod = await decode(production.asset)
        prod["sourceCounts"] = source.counts; prod["loaderCounts"] = production.diagnosticSummary
        media["productionForbidAll"] = prod
        production.stop()
        let explicit: AVAssetReferenceRestrictions = [.forbidRemoteReferenceToLocal, .forbidLocalReferenceToRemote, .forbidCrossSiteReference, .forbidLocalReferenceToLocal]
        let cases: [(String, AVAssetReferenceRestrictions, Bool, Bool, Bool)] = [
            ("contractForbidAllRoot", .forbidAll, false, false, false),
            ("contractForbidAllPath", .forbidAll, true, false, false),
            ("contractForbidAllResponse", .forbidAll, true, true, false),
            ("contractForbidAllMIMEOverride", .forbidAll, true, false, true),
            ("contractExplicitRestrictions", explicit, true, false, false),
            ("contractDefaultPolicy", .defaultPolicy, true, false, false)
        ]
        for (name, policy, path, response, mime) in cases {
            let control = ContractLoader(bytes: bytes, restrictions: policy, path: path, response: response, mimeOverride: mime)
            var result = await decode(control.asset); result["requestCounts"] = control.snapshot
            media[name] = result
        }
        report["media"] = media
        let result = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        print(String(decoding: result, as: UTF8.self))
    }
}
