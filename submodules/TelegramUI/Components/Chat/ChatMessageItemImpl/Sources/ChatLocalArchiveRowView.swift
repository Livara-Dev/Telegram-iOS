// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import ImageIO
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData

private let localArchiveRowDateFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.locale = .autoupdatingCurrent
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateStyle = .none
    formatter.timeStyle = .short
    return formatter
}()

/// A retained attachment stays at its original position, with a compact deleted
/// marker. Opening it uses only the encrypted local archive, never Telegram fetches.
public final class ChatLocalArchiveRowView: UIView {
    private let titleLabel = UILabel()
    private let bodyLabel = UILabel()
    private let metadataLabel = UILabel()
    private let photoView = UIImageView()
    private var mediaButtons: [UIButton] = []
    private let imageRequest = MetaDisposable()
    private var revocation: Disposable?
    private var content: LocalArchiveDeletedHistoryContent?
    private var openHistory: (() -> Void)?
    private var openMedia: ((Int) -> Void)?
    private var row: LocalArchiveDeletedHistoryItem?
    private var renderVersion: Int64 = 0

    public override init(frame: CGRect) {
        super.init(frame: frame)
        self.layer.cornerRadius = 16.0
        self.clipsToBounds = true
        self.titleLabel.font = Font.semibold(12.0)
        self.bodyLabel.font = Font.regular(15.0)
        self.bodyLabel.numberOfLines = 4
        self.bodyLabel.lineBreakMode = .byTruncatingTail
        self.metadataLabel.font = Font.regular(11.0)
        self.metadataLabel.numberOfLines = 1
        self.metadataLabel.textAlignment = .right
        for label in [self.titleLabel, self.bodyLabel, self.metadataLabel] {
            label.isUserInteractionEnabled = false
            self.addSubview(label)
        }
        self.photoView.contentMode = .scaleAspectFill
        self.photoView.clipsToBounds = true
        self.photoView.layer.cornerRadius = 10.0
        self.photoView.isUserInteractionEnabled = false
        self.addSubview(self.photoView)
        self.isAccessibilityElement = true
        self.accessibilityTraits = .button
        self.accessibilityIdentifier = "AyuGram.LocalDeletedMessage"
        let tap = UITapGestureRecognizer(target: self, action: #selector(self.tapped(_:)))
        tap.cancelsTouchesInView = false
        self.addGestureRecognizer(tap)
    }

    required init?(coder: NSCoder) { return nil }

    private static func title(kind: String) -> String {
        switch kind {
        case "photo": return "Photo"
        case "video": return "Video"
        case "voice": return "Voice message"
        case "videoMessage": return "Video message"
        case "animation": return "Animation"
        default: return "File"
        }
    }

    private static func symbol(kind: String) -> String {
        switch kind {
        case "photo": return "photo"
        case "voice": return "waveform"
        case "videoMessage": return "video.circle.fill"
        case "video", "animation": return "play.circle.fill"
        default: return "doc.fill"
        }
    }

    private static func photoSlot(_ row: LocalArchiveDeletedHistoryItem) -> Int? {
        return row.storedMediaSlots.first { slot in row.mediaKinds.indices.contains(slot) && row.mediaKinds[slot] == "photo" }
    }

    public static func metadata(_ row: LocalArchiveDeletedHistoryItem) -> String {
        let time = localArchiveRowDateFormatter.string(from: Date(timeIntervalSince1970: Double(row.sentAt)))
        return row.moreAvailable ? "More in history · " + time : time
    }

    public static func height(row: LocalArchiveDeletedHistoryItem, width: CGFloat) -> CGFloat {
        let innerWidth = max(1.0, width - 24.0)
        let body = row.content.text ?? ""
        let bodyHeight = body.isEmpty ? 0.0 : min(ceil(Font.regular(15.0).lineHeight * 4.0), ceil((body as NSString).boundingRect(
            with: CGSize(width: innerWidth, height: .greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: Font.regular(15.0)], context: nil).height))
        let attachments = min(3, row.mediaKinds.count)
        let imageHeight: CGFloat = Self.photoSlot(row) == nil ? 0.0 : 128.0
        return 12.0 + ceil(Font.semibold(12.0).lineHeight) + (bodyHeight > 0.0 ? 6.0 + bodyHeight : 0.0)
            + imageHeight + CGFloat(attachments) * 48.0 + 7.0 + ceil(Font.regular(11.0).lineHeight) + 10.0
    }

    public func update(row: LocalArchiveDeletedHistoryItem, theme: PresentationTheme,
                       openHistory: @escaping () -> Void,
                       loadThumbnail: ((String, Int) -> Signal<Data?, NoError>)? = nil,
                       openMedia: ((Int) -> Void)? = nil) {
        assert(Thread.isMainThread)
        self.renderVersion &+= 1
        let version = self.renderVersion
        self.imageRequest.set(nil)
        self.revocation?.dispose(); self.revocation = nil
        self.row = row; self.content = row.content
        self.openHistory = openHistory; self.openMedia = openMedia
        self.backgroundColor = theme.list.itemBlocksBackgroundColor
        self.layer.borderWidth = 0.0
        self.titleLabel.textColor = theme.list.itemDestructiveColor
        self.bodyLabel.textColor = theme.list.itemPrimaryTextColor
        self.metadataLabel.textColor = theme.list.itemSecondaryTextColor
        self.titleLabel.text = row.marker
        self.bodyLabel.text = row.content.text
        self.metadataLabel.text = Self.metadata(row)
        self.photoView.image = nil
        self.photoView.isHidden = Self.photoSlot(row) == nil
        self.photoView.backgroundColor = theme.list.itemAccentColor.withAlphaComponent(0.08)
        for button in self.mediaButtons { button.removeFromSuperview() }
        self.mediaButtons.removeAll()
        for slot in row.mediaKinds.indices.prefix(3) {
            let kind = row.mediaKinds[slot]
            let saved = row.storedMediaSlots.contains(slot)
            let button = UIButton(type: .system)
            button.tag = slot
            button.contentHorizontalAlignment = .leading
            button.titleLabel?.font = Font.medium(14.0)
            button.titleLabel?.lineBreakMode = .byTruncatingTail
            button.setTitle("  " + Self.title(kind: kind) + (saved ? "" : " · Not saved"), for: .normal)
            button.setImage(UIImage(systemName: Self.symbol(kind: kind)), for: .normal)
            button.tintColor = saved ? theme.list.itemAccentColor : theme.list.itemSecondaryTextColor
            button.isEnabled = saved && openMedia != nil
            button.accessibilityLabel = Self.title(kind: kind) + (saved ? ", saved" : ", not saved")
            button.accessibilityIdentifier = "AyuGram.LocalDeletedMedia.\(slot)"
            button.addTarget(self, action: #selector(self.mediaTapped(_:)), for: .touchUpInside)
            self.addSubview(button)
            self.mediaButtons.append(button)
        }
        let attachmentLabels = row.mediaKinds.enumerated().map { slot, kind in
            Self.title(kind: kind) + (row.storedMediaSlots.contains(slot) ? ", saved" : ", not saved")
        }
        self.accessibilityLabel = ([self.titleLabel.text, self.bodyLabel.text, self.metadataLabel.text].compactMap { $0 } + attachmentLabels).joined(separator: ". ")
        self.accessibilityCustomActions = row.storedMediaSlots.compactMap { slot in
            guard row.mediaKinds.indices.contains(slot) else { return nil }
            return UIAccessibilityCustomAction(name: "Open " + Self.title(kind: row.mediaKinds[slot])) { [weak self] _ in
                guard let self, self.content?.text != nil, let open = self.openMedia else { return false }
                open(slot); return true
            }
        }
        self.accessibilityCustomActions?.append(UIAccessibilityCustomAction(name: "Message history") { [weak self] _ in
            guard let self, self.content?.text != nil, let open = self.openHistory else { return false }
            open(); return true
        })
        self.isHidden = row.content.text == nil
        self.revocation = row.content.onRevoked { [weak self] in self?.clearProtectedContent() }
        if !self.isHidden, let slot = Self.photoSlot(row), let loadThumbnail {
            self.imageRequest.set((loadThumbnail(row.eventKey, slot)
            |> deliverOn(Queue.concurrentDefaultQueue())
            |> map { data -> UIImage? in
                guard let data, data.count <= 16 * 1024 * 1024,
                      let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary),
                      let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
                      let w = (properties[kCGImagePropertyPixelWidth as String] as? NSNumber)?.uint64Value,
                      let h = (properties[kCGImagePropertyPixelHeight as String] as? NSNumber)?.uint64Value,
                      w > 0, h > 0, w <= 20_000, h <= 20_000, w * h <= 80_000_000,
                      let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
                          kCGImageSourceThumbnailMaxPixelSize: 640, kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { return nil }
                return UIImage(cgImage: image)
            }
            |> deliverOnMainQueue).start(next: { [weak self] image in
                guard let self, self.renderVersion == version, self.content?.text != nil else { return }
                self.photoView.image = image
            }))
        }
        self.setNeedsLayout()
    }

    public func clearProtectedContent() {
        assert(Thread.isMainThread)
        self.renderVersion &+= 1
        self.imageRequest.set(nil)
        self.titleLabel.text = nil; self.bodyLabel.text = nil; self.metadataLabel.text = nil
        self.photoView.image = nil
        for button in self.mediaButtons { button.removeFromSuperview() }
        self.mediaButtons.removeAll()
        self.accessibilityLabel = nil; self.accessibilityCustomActions = nil
        self.openHistory = nil; self.openMedia = nil
        self.content = nil; self.row = nil
        self.isHidden = true
        self.layer.removeAllAnimations()
    }

    public override func layoutSubviews() {
        super.layoutSubviews()
        let width = max(1.0, self.bounds.width - 24.0)
        self.titleLabel.frame = CGRect(x: 12, y: 12, width: width, height: ceil(self.titleLabel.font.lineHeight))
        var y = self.titleLabel.frame.maxY
        let bodyHeight = self.bodyLabel.text?.isEmpty == false ? min(ceil(self.bodyLabel.font.lineHeight * 4.0), ceil(self.bodyLabel.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height)) : 0.0
        if bodyHeight > 0 { y += 6 }
        self.bodyLabel.frame = CGRect(x: 12, y: y, width: width, height: bodyHeight)
        y += bodyHeight
        if !self.photoView.isHidden {
            self.photoView.frame = CGRect(x: 12, y: y + 8, width: width, height: 120)
            y += 128
        }
        for button in self.mediaButtons {
            button.frame = CGRect(x: 12, y: y, width: width, height: 48)
            y += 48
        }
        self.metadataLabel.frame = CGRect(x: 12, y: y + 7, width: width, height: ceil(self.metadataLabel.font.lineHeight))
    }

    @objc private func mediaTapped(_ button: UIButton) {
        guard self.content?.text != nil, !self.isHidden, self.row?.storedMediaSlots.contains(button.tag) == true else { return }
        self.openMedia?(button.tag)
    }

    @objc private func tapped(_ gesture: UITapGestureRecognizer) {
        let point = gesture.location(in: self)
        if self.mediaButtons.contains(where: { $0.frame.contains(point) }) { return }
        _ = self.accessibilityActivate()
    }

    public override func accessibilityActivate() -> Bool {
        guard self.content?.text != nil, !self.isHidden else { return false }
        if let slot = self.row?.storedMediaSlots.first, let openMedia { openMedia(slot); return true }
        guard let openHistory else { return false }
        openHistory(); return true
    }

    deinit { self.revocation?.dispose(); self.imageRequest.dispose() }
}
