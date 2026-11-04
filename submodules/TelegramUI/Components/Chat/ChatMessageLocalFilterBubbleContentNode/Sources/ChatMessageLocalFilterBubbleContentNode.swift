// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import Display
import AsyncDisplayKit
import LottieSettings
import TelegramPresentationData
import ChatMessageBubbleContentNode
import ChatMessageItemCommon

/// A presentation-only replacement inside the existing text bubble item class.
/// It never formats the original body, entities, reply, or accessibility value.
public final class ChatMessageLocalFilterBubbleContentNode: ChatMessageBubbleContentNode {
    private let textNode: TextNode

    required public init(lottieSettings: LottieRenderingSettings) {
        self.textNode = TextNode()
        super.init(lottieSettings: lottieSettings)
        self.textNode.isUserInteractionEnabled = false
        self.textNode.isAccessibilityElement = false
        self.textNode.displaysAsynchronously = false
        self.addSubnode(self.textNode)
    }

    required public init?(coder aDecoder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override public func asyncLayoutContent() -> (_ item: ChatMessageBubbleContentItem, _ layoutConstants: ChatMessageItemLayoutConstants, _ preparePosition: ChatMessageBubblePreparePosition, _ messageSelection: Bool?, _ constrainedSize: CGSize, _ avatarInset: CGFloat) -> (ChatMessageBubbleContentProperties, CGSize?, CGFloat, (CGSize, ChatMessageBubbleContentPosition) -> (CGFloat, (CGFloat) -> (CGSize, (ListViewItemUpdateAnimation, Bool, ListViewItemApply?) -> Void))) {
        let makeTextLayout = TextNode.asyncLayout(self.textNode)
        return { [weak self] item, constants, _, _, _, _ in
            let properties = ChatMessageBubbleContentProperties(hidesSimpleAuthorHeader: true, headerSpacing: 0.0, hidesBackground: .never, forceFullCorners: false, forceAlignment: .none)
            return (properties, nil, CGFloat.greatestFiniteMagnitude, { [weak self] constrainedSize, _ in
                let insets = constants.text.bubbleInsets
                let text = NSAttributedString(string: "Message hidden by a local filter\nTap to show filtered messages in this chat", attributes: [
                    .font: item.presentationData.messageFont,
                    .foregroundColor: item.presentationData.theme.theme.chat.message.incoming.secondaryTextColor
                ])
                let (layout, apply) = makeTextLayout(TextNodeLayoutArguments(attributedString: text, backgroundColor: nil, maximumNumberOfLines: 0, truncationType: .end, constrainedSize: CGSize(width: max(1.0, constrainedSize.width - insets.left - insets.right), height: constrainedSize.height), alignment: .natural, cutout: nil, insets: .zero))
                let size = CGSize(width: layout.size.width + insets.left + insets.right, height: layout.size.height + insets.top + insets.bottom)
                return (size.width, { [weak self] _ in
                    return (size, { [weak self] _, _, _ in
                        guard let self else { return }
                        self.item = item
                        let _ = apply()
                        self.textNode.frame = CGRect(origin: CGPoint(x: insets.left, y: insets.top), size: layout.size)
                    })
                })
            })
        }
    }

    override public func tapActionAtPoint(_ point: CGPoint, gesture: TapLongTapOrDoubleTapGesture, isEstimating: Bool) -> ChatMessageBubbleContentTapAction {
        // The outer bubble owns the explicit reveal action and rejects all
        // message actions (including double tap and long press) while hidden.
        return ChatMessageBubbleContentTapAction(content: .none)
    }
}
