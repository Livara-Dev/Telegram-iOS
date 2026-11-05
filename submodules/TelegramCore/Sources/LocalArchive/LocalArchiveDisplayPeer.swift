// SPDX-License-Identifier: GPL-2.0-or-later
import Postbox

/// Optional bridge from generic archive Int64 metadata to a local Telegram peer
/// lookup. Unknown/corrupt/imported identities remain local archive data and must
/// not reach an assertion-bearing EnginePeer.Id initializer in native UI.
public func localArchiveDisplayPeerId(_ value: Int64) -> EnginePeer.Id? {
    guard let peerId = telegramCoreDecodePeerId(value), peerId.id._internalGetInt64Value() > 0,
          peerId.namespace == Namespaces.Peer.CloudUser || peerId.namespace == Namespaces.Peer.CloudGroup
            || peerId.namespace == Namespaces.Peer.CloudChannel else { return nil }
    return peerId
}

/// A read-only live-version lookup is meaningful only for ordinary cloud ids.
/// Returning nil never removes or rewrites the original archived record.
public func localArchiveDisplayMessageId(peerId: Int64, namespace: Int32, messageId: Int32) -> EngineMessage.Id? {
    guard namespace == Namespaces.Message.Cloud, messageId > 0,
          let peer = localArchiveDisplayPeerId(peerId) else { return nil }
    return EngineMessage.Id(peerId: peer, namespace: namespace, id: messageId)
}
