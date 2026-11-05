// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData

private let localArchiveRowDateFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.locale = .autoupdatingCurrent
    formatter.timeZone = .autoupdatingCurrent
    formatter.dateStyle = .short
    formatter.timeStyle = .short
    return formatter
}()

/// The production renderer is independently constructible in a native UIKit
/// test host. Plain labels have no link parsing, fetches, selection or preview.
public final class ChatLocalArchiveRowView: UIView {
    private let titleLabel = UILabel()
    private let bodyLabel = UILabel()
    private let metadataLabel = UILabel()
    private var revocation: Disposable?
    private var content: LocalArchiveDeletedHistoryContent?
    private var openHistory: (() -> Void)?

    public override init(frame: CGRect) {
        super.init(frame: frame)
        self.layer.cornerRadius = 14.0
        self.clipsToBounds = true
        self.titleLabel.font = Font.semibold(13.0)
        self.bodyLabel.font = Font.regular(15.0)
        self.bodyLabel.numberOfLines = 4
        self.bodyLabel.lineBreakMode = .byTruncatingTail
        self.metadataLabel.font = Font.regular(11.0)
        self.metadataLabel.numberOfLines = 0
        for label in [self.titleLabel, self.bodyLabel, self.metadataLabel] {
            label.isUserInteractionEnabled = false
            self.addSubview(label)
        }
        self.isAccessibilityElement = true
        self.accessibilityTraits = .button
        self.accessibilityIdentifier = "AyuGram.LocalDeletedMessage"
        self.addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(self.tapped)))
    }

    required init?(coder: NSCoder) { return nil }

    public static func metadata(_ row: LocalArchiveDeletedHistoryItem) -> String {
        let date = localArchiveRowDateFormatter.string(from: Date(timeIntervalSince1970: Double(row.deletedAt)))
        let sent = localArchiveRowDateFormatter.string(from: Date(timeIntervalSince1970: Double(row.sentAt)))
        var lines = ["Sent: " + sent, "Observed deletion: " + date]
        if row.storedMediaCount > 0 { lines.append("Saved media: \(row.storedMediaCount)") }
        if row.unavailableMediaCount > 0 { lines.append("Media not captured: \(row.unavailableMediaCount)") }
        lines.append(row.moreAvailable ? "More saved messages in archive · Tap for history" : "Saved on this device · Tap for history")
        return lines.joined(separator: "\n")
    }

    public static func height(row: LocalArchiveDeletedHistoryItem, width: CGFloat) -> CGFloat {
        let innerWidth = max(1.0, width - 24.0)
        let body = row.content.text ?? ""
        let bodyHeight = body.isEmpty ? 0.0 : min(ceil(Font.regular(15.0).lineHeight * 4.0), ceil((body as NSString).boundingRect(
            with: CGSize(width: innerWidth, height: CGFloat.greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: Font.regular(15.0)], context: nil).height))
        let metadataHeight = ceil((self.metadata(row) as NSString).boundingRect(
            with: CGSize(width: innerWidth, height: CGFloat.greatestFiniteMagnitude), options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: Font.regular(11.0)], context: nil).height)
        return 12.0 + ceil(Font.semibold(13.0).lineHeight) + (bodyHeight > 0.0 ? 6.0 + bodyHeight : 0.0) + 7.0 + metadataHeight + 12.0
    }

    public func update(row: LocalArchiveDeletedHistoryItem, theme: PresentationTheme, openHistory: @escaping () -> Void) {
        assert(Thread.isMainThread)
        self.revocation?.dispose(); self.revocation = nil
        self.content = row.content
        self.openHistory = openHistory
        self.backgroundColor = theme.list.itemBlocksBackgroundColor
        self.layer.borderWidth = 1.0 / UIScreenScale
        self.layer.borderColor = theme.list.itemAccentColor.withAlphaComponent(0.35).cgColor
        self.titleLabel.textColor = theme.list.itemAccentColor
        self.bodyLabel.textColor = theme.list.itemPrimaryTextColor
        self.metadataLabel.textColor = theme.list.itemSecondaryTextColor
        self.titleLabel.text = row.marker + " · Local archive"
        self.bodyLabel.text = row.content.text
        self.metadataLabel.text = Self.metadata(row)
        self.accessibilityLabel = [self.titleLabel.text, self.bodyLabel.text, self.metadataLabel.text].compactMap { $0 }.joined(separator: ". ")
        self.isHidden = row.content.text == nil
        self.revocation = row.content.onRevoked { [weak self] in self?.clearProtectedContent() }
        self.setNeedsLayout()
    }

    /// Called synchronously even for a node retained by a departing transition.
    public func clearProtectedContent() {
        assert(Thread.isMainThread)
        self.titleLabel.text = nil; self.bodyLabel.text = nil; self.metadataLabel.text = nil
        self.accessibilityLabel = nil
        self.openHistory = nil
        self.content = nil
        self.isHidden = true
        self.layer.removeAllAnimations()
    }

    public override func layoutSubviews() {
        super.layoutSubviews()
        let width = max(1.0, self.bounds.width - 24.0)
        let titleHeight = ceil(Font.semibold(13.0).lineHeight)
        self.titleLabel.frame = CGRect(x: 12.0, y: 12.0, width: width, height: titleHeight)
        let bodyHeight = self.bodyLabel.text?.isEmpty == false ? min(ceil(self.bodyLabel.font.lineHeight * 4.0), ceil(self.bodyLabel.sizeThatFits(CGSize(width: width, height: CGFloat.greatestFiniteMagnitude)).height)) : 0.0
        self.bodyLabel.frame = CGRect(x: 12.0, y: self.titleLabel.frame.maxY + 6.0, width: width, height: bodyHeight)
        let metadataY = (bodyHeight > 0.0 ? self.bodyLabel.frame.maxY : self.titleLabel.frame.maxY) + 7.0
        self.metadataLabel.frame = CGRect(x: 12.0, y: metadataY, width: width, height: max(0.0, self.bounds.height - metadataY - 12.0))
    }

    @objc private func tapped() {
        guard self.content?.text != nil, !self.isHidden else { return }
        self.openHistory?()
    }

    public override func accessibilityActivate() -> Bool {
        guard self.content?.text != nil, !self.isHidden, let openHistory = self.openHistory else { return false }
        openHistory()
        return true
    }

    deinit { self.revocation?.dispose() }
}
