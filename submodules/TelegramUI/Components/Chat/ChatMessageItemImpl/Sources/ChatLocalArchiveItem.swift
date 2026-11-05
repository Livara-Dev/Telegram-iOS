// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import UIKit
import AsyncDisplayKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import ChatControllerInteraction

/// A distinct list item, deliberately outside ChatMessageItem/MessageItemView.
/// Its only action opens a read-only archive timeline in the current account.
public final class ChatLocalArchiveItem: ListViewItem {
    public let row: LocalArchiveDeletedHistoryItem
    public let presentationData: ChatPresentationData
    private let controllerInteraction: ChatControllerInteraction
    public var neighborDescriptor: AnyEquatable { return AnyEquatable.noNeighborInfluence }

    public init(row: LocalArchiveDeletedHistoryItem, presentationData: ChatPresentationData,
                controllerInteraction: ChatControllerInteraction) {
        self.row = row; self.presentationData = presentationData; self.controllerInteraction = controllerInteraction
    }

    public func nodeConfiguredForParams(async: @escaping (@escaping () -> Void) -> Void, params: ListViewItemLayoutParams,
        synchronousLoads: Bool, neighbors: ListViewItemNeighbors,
        completion: @escaping (ListViewItemNode, @escaping () -> (Signal<Void, NoError>?, (ListViewItemApply) -> Void)) -> Void) {
        let configure = {
            let node = ChatLocalArchiveItemNode()
            node.layoutForParams(params, item: self, neighbors: neighbors)
            completion(node, { return (nil, { _ in }) })
        }
        // CoreList's production host needs completion synchronously on main.
        // An unconditional main.async silently creates a zero-height item.
        if Thread.isMainThread { configure() } else { Queue.mainQueue().async(configure) }
    }

    public func updateNode(async: @escaping (@escaping () -> Void) -> Void, node: @escaping () -> ListViewItemNode,
        params: ListViewItemLayoutParams, neighbors: ListViewItemNeighbors, animation: ListViewItemUpdateAnimation,
        completion: @escaping (ListViewItemNodeLayout, @escaping (ListViewItemApply) -> Void) -> Void) {
        let update = {
            guard let node = node() as? ChatLocalArchiveItemNode else { return }
            let layout = node.measuredLayout(item: self, params: params)
            completion(layout, { _ in node.apply(item: self, params: params) })
        }
        if Thread.isMainThread { update() } else { Queue.mainQueue().async(update) }
    }

    fileprivate func openHistory() {
        guard self.row.content.text != nil else { return }
        self.controllerInteraction.openLocalArchiveHistory?(EngineMessage.Id(peerId: self.row.peerId,
            namespace: Namespaces.Message.Cloud, id: self.row.messageId))
    }
}

public final class ChatLocalArchiveItemNode: ListViewItemNode {
    private var item: ChatLocalArchiveItem?
    private let bodyNode: ASDisplayNode

    public init() {
        self.bodyNode = ASDisplayNode(viewBlock: { ChatLocalArchiveRowView(frame: .zero) })
        super.init(layerBacked: false, rotated: true)
        self.addSubnode(self.bodyNode)
        self.transform = CATransform3DMakeRotation(CGFloat.pi, 0.0, 0.0, 1.0)
        self.canBeUsedAsScrollToItemAnchor = false
    }

    fileprivate func measuredLayout(item: ChatLocalArchiveItem, params: ListViewItemLayoutParams) -> ListViewItemNodeLayout {
        let width = min(420.0, max(1.0, params.width - params.leftInset - params.rightInset - 32.0))
        return ListViewItemNodeLayout(contentSize: CGSize(width: params.width,
            height: ChatLocalArchiveRowView.height(row: item.row, width: width)),
            insets: UIEdgeInsets(top: 6.0, left: 0.0, bottom: 6.0, right: 0.0))
    }

    fileprivate func apply(item: ChatLocalArchiveItem, params: ListViewItemLayoutParams) {
        self.item = item
        let height = ChatLocalArchiveRowView.height(row: item.row,
            width: min(420.0, max(1.0, params.width - params.leftInset - params.rightInset - 32.0)))
        let width = min(420.0, max(1.0, params.width - params.leftInset - params.rightInset - 32.0))
        self.bodyNode.frame = CGRect(x: params.leftInset + 16.0, y: 0.0, width: width, height: height)
        (self.bodyNode.view as? ChatLocalArchiveRowView)?.update(row: item.row, theme: item.presentationData.theme.theme,
            openHistory: { [weak item] in item?.openHistory() })
    }

    public override func layoutForParams(_ params: ListViewItemLayoutParams, item: ListViewItem, neighbors: ListViewItemNeighbors) {
        guard let item = item as? ChatLocalArchiveItem else { return }
        let layout = self.measuredLayout(item: item, params: params)
        self.apply(item: item, params: params)
        self.contentSize = layout.contentSize; self.insets = layout.insets
    }

    // Deliberately no shared ChatMessageDateHeader: a local date must never
    // replace a canonical calendar action or navigate to an archived server id.
    // Both timestamps are shown inside the local card instead.
}
