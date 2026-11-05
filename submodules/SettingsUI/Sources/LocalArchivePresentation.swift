// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import SwiftSignalKit
import TelegramCore

/// A bounded read-only projection, not an archived snapshot. No media bytes are
/// loaded, and timed/Secret Chat content is never copied into this alternate UI.
struct LocalArchiveCurrentVersionPreview: Equatable {
    let text: String
    let timestamp: Int32
    let serverEditAt: Int32?

    init?(message: EngineMessage) {
        guard localArchiveSupportsPeer(message.id.peerId), message.id.namespace == Namespaces.Message.Cloud,
              !message.attributes.contains(where: { $0 is AutoremoveTimeoutMessageAttribute || $0 is AutoclearTimeoutMessageAttribute }),
              !message.media.contains(where: { $0 is TelegramMediaExpiredContent }) else { return nil }
        self.timestamp = message.timestamp
        self.serverEditAt = message.attributes.compactMap { ($0 as? EditedMessageAttribute)?.date }.first
        if message.richText != nil {
            self.text = "Rich-text content is not previewed here. Open the current message in Telegram."
        } else if message.text.isEmpty {
            self.text = message.media.isEmpty ? "[No text]" : "[Current attachment; media is not copied into the archive by this view.]"
        } else {
            var text = ""
            var count = 0
            var truncated = false
            for scalar in message.text.unicodeScalars {
                let bytes = scalar.value <= 0x7f ? 1 : (scalar.value <= 0x7ff ? 2 : (scalar.value <= 0xffff ? 3 : 4))
                if count + bytes > 16384 { truncated = true; break }
                text.unicodeScalars.append(scalar)
                count += bytes
            }
            self.text = text + (truncated ? "\n\n[Current preview limited to 16 KiB of UTF-8 text.]" : "")
        }
    }
}

enum LocalArchiveCurrentVersionState: Equatable {
    case hidden
    case loading
    case unavailable
    case unsupportedContent
    case available(LocalArchiveCurrentVersionPreview)
}

/// Used by the real history controller with an engine Messages.Message view.
/// Invalidating the subscription also invalidates already-queued deliveries.
final class LocalArchiveCurrentVersionLoader {
    private let source: () -> Signal<EngineMessage?, NoError>
    private let changed: (LocalArchiveCurrentVersionState) -> Void
    private let disposable = MetaDisposable()
    private var active = false
    private var generation = 0
    private var disposed = false

    init(source: @escaping () -> Signal<EngineMessage?, NoError>, changed: @escaping (LocalArchiveCurrentVersionState) -> Void) {
        self.source = source
        self.changed = changed
    }

    func setActive(_ active: Bool) {
        guard !self.disposed, active != self.active else { return }
        self.active = active
        self.generation += 1
        let generation = self.generation
        self.disposable.set(nil)
        guard active else { self.changed(.hidden); return }
        self.changed(.loading)
        self.disposable.set((self.source() |> deliverOnMainQueue).start(next: { [weak self] message in
            guard let self, !self.disposed, self.active, self.generation == generation else { return }
            guard let message else { self.changed(.unavailable); return }
            guard let preview = LocalArchiveCurrentVersionPreview(message: message) else { self.changed(.unsupportedContent); return }
            self.changed(.available(preview))
        }))
    }

    func dispose() {
        self.disposed = true
        self.active = false
        self.generation += 1
        self.disposable.dispose()
    }

    deinit { self.dispose() }
}
