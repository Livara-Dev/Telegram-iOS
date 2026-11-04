// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import AVFoundation
import UniformTypeIdentifiers

public struct ArchivePlaybackInfo {
    public let mimeType: String
    public let byteCount: Int64
    public init(mimeType: String, byteCount: Int64) {
        self.mimeType = mimeType
        self.byteCount = byteCount
    }
}

/// Cancellable source with no filesystem or networking assumptions. Completion
/// may arrive on any queue; the loader serializes it before touching AVFoundation.
public protocol ArchivePlaybackSource: AnyObject {
    func info(completion: @escaping (ArchivePlaybackInfo?) -> Void) -> ArchivePlaybackCancellation
    func read(offset: Int64, count: Int, completion: @escaping (Data?) -> Void) -> ArchivePlaybackCancellation
}

public final class ArchivePlaybackCancellation {
    private let lock = NSLock()
    private var action: (() -> Void)?
    private var cancelled = false
    public init(_ action: @escaping () -> Void = {}) { self.action = action }
    public func cancel() {
        self.lock.lock()
        self.cancelled = true
        let action = self.action
        self.action = nil
        self.lock.unlock()
        action?()
    }
    func replace(_ next: ArchivePlaybackCancellation) {
        self.lock.lock()
        let cancelled = self.cancelled
        let previous = self.action
        self.action = cancelled ? nil : { next.cancel() }
        self.lock.unlock()
        previous?()
        if cancelled { next.cancel() }
    }
}

/// AVFoundation consumes bounded reads directly from the encrypted archive.
/// The private URL is an in-process identifier, never an HTTP endpoint or file.
/// All request bookkeeping is confined to a serial utility queue; SQL work
/// stays on the source's worker. No plaintext temporary media file is created.
public final class ArchivePlaybackAsset: NSObject, AVAssetResourceLoaderDelegate {
    public let asset: AVURLAsset
    private let source: ArchivePlaybackSource
    private let queue = DispatchQueue(label: "LocalArchive.media-reader", qos: .userInitiated)
    private var active: [ObjectIdentifier: (AVAssetResourceLoadingRequest, ArchivePlaybackCancellation)] = [:]
    private var stopped = false

    public init(source: ArchivePlaybackSource) {
        self.source = source
        // A crafted movie may contain data references. Reject every external
        // reference instead of letting AVFoundation resolve local/remote URLs.
        // Use an opaque root resource, without file-path resolution or network
        // transport options. All bytes are supplied by this asset's delegate.
        self.asset = AVURLAsset(url: URL(string: "ayugram-archive://resource")!, options: [
            AVURLAssetReferenceRestrictionsKey: AVAssetReferenceRestrictions.forbidAll.rawValue
        ])
        super.init()
        self.asset.resourceLoader.setDelegate(self, queue: self.queue)
    }

    private static func unavailable() -> NSError {
        return NSError(domain: "AyuGram.LocalArchive", code: 1, userInfo: [NSLocalizedDescriptionKey: "Archived media is unavailable or unsupported."])
    }

    public func stop() {
        self.queue.async { self.stopOnQueue() }
    }

    private func stopOnQueue() {
        self.stopped = true
        let requests = Array(self.active.values)
        self.active.removeAll()
        for (request, disposable) in requests {
            disposable.cancel()
            if !request.isFinished && !request.isCancelled { request.finishLoading(with: Self.unavailable()) }
        }
    }

    private func finish(_ request: AVAssetResourceLoadingRequest, failed: Bool) {
        guard let (_, disposable) = self.active.removeValue(forKey: ObjectIdentifier(request)) else { return }
        disposable.cancel()
        if !request.isFinished && !request.isCancelled {
            if failed { request.finishLoading(with: Self.unavailable()) }
            else { request.finishLoading() }
        }
    }

    public func resourceLoader(_ resourceLoader: AVAssetResourceLoader, shouldWaitForLoadingOfRequestedResource request: AVAssetResourceLoadingRequest) -> Bool {
        guard !self.stopped, self.active.count < 8, request.request.url == self.asset.url else {
            request.finishLoading(with: Self.unavailable())
            return true
        }
        let disposable = ArchivePlaybackCancellation()
        self.active[ObjectIdentifier(request)] = (request, disposable)
        disposable.replace(self.source.info { [weak self, weak request] info in
            guard let self else { return }
            self.queue.async { [weak self, weak request] in
            guard let self, let request, self.active[ObjectIdentifier(request)] != nil else { return }
            guard let info, info.byteCount >= 0, info.byteCount <= 512 * 1024 * 1024,
                  let contentType = UTType(mimeType: info.mimeType) else {
                self.finish(request, failed: true); return
            }
            if let information = request.contentInformationRequest {
                information.contentType = contentType.identifier
                information.contentLength = info.byteCount
                information.isByteRangeAccessSupported = true
            }
            guard let data = request.dataRequest else { self.finish(request, failed: false); return }
            let start = max(data.requestedOffset, data.currentOffset)
            guard start >= 0, start <= info.byteCount, data.requestedLength >= 0 else {
                self.finish(request, failed: true); return
            }
            let remaining: Int64
            if data.requestsAllDataToEndOfResource {
                remaining = info.byteCount - start
            } else {
                guard data.requestedOffset >= 0, data.requestedOffset <= info.byteCount else {
                    self.finish(request, failed: true); return
                }
                let end = data.requestedOffset + min(Int64(data.requestedLength), info.byteCount - data.requestedOffset)
                remaining = max(0, end - start)
            }
            self.readNext(request, offset: start, remaining: remaining)
            }
        })
        return true
    }

    private func readNext(_ request: AVAssetResourceLoadingRequest, offset: Int64, remaining: Int64) {
        guard !self.stopped, let (_, disposable) = self.active[ObjectIdentifier(request)] else { return }
        guard !request.isCancelled else { self.finish(request, failed: true); return }
        guard remaining > 0 else { self.finish(request, failed: false); return }
        let count = Int(min(64 * 1024, remaining))
        disposable.replace(self.source.read(offset: offset, count: count) { [weak self, weak request] data in
            guard let self else { return }
            self.queue.async { [weak self, weak request] in
            guard let self, let request, self.active[ObjectIdentifier(request)] != nil else { return }
            guard let data, data.count == count, let response = request.dataRequest else {
                self.finish(request, failed: true); return
            }
            response.respond(with: data)
            self.readNext(request, offset: offset + Int64(count), remaining: remaining - Int64(count))
            }
        })
    }

    public func resourceLoader(_ resourceLoader: AVAssetResourceLoader, didCancel loadingRequest: AVAssetResourceLoadingRequest) {
        self.active.removeValue(forKey: ObjectIdentifier(loadingRequest))?.1.cancel()
    }
}

