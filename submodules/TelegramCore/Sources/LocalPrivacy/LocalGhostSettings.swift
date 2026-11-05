// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

let localGhostSettingsKey = ValueBoxKey("ayugram.ghost.activity.v1")

/// Account-scoped application policy. These controls do not alter MTProto or
/// authentication, and cannot prevent server inference or other-client activity.
public struct LocalGhostSettings: Codable, Equatable {
    let schemaVersion: Int32
    public var enabled: Bool
    public var suppressTyping: Bool
    public var suppressStickerSelection: Bool
    public var suppressRecording: Bool
    public var suppressUploads: Bool
    public var suppressGamePlay: Bool
    public var suppressReadReceipts: Bool
    public var suppressOnlineStatus: Bool
    public var automaticOffline: Bool

    public init(enabled: Bool = false, suppressTyping: Bool = true,
                suppressStickerSelection: Bool = true, suppressRecording: Bool = true,
                suppressUploads: Bool = true, suppressGamePlay: Bool = true,
                suppressReadReceipts: Bool = true, suppressOnlineStatus: Bool = true,
                automaticOffline: Bool = true) {
        self.schemaVersion = 2
        self.enabled = enabled
        self.suppressTyping = suppressTyping
        self.suppressStickerSelection = suppressStickerSelection
        self.suppressRecording = suppressRecording
        self.suppressUploads = suppressUploads
        self.suppressGamePlay = suppressGamePlay
        self.suppressReadReceipts = suppressReadReceipts
        self.suppressOnlineStatus = suppressOnlineStatus
        self.automaticOffline = automaticOffline
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, enabled, suppressTyping, suppressStickerSelection
        case suppressRecording, suppressUploads, suppressGamePlay
        case suppressReadReceipts, suppressOnlineStatus, automaticOffline
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let storedVersion = try values.decode(Int32.self, forKey: .schemaVersion)
        self.schemaVersion = storedVersion == 1 ? 2 : storedVersion
        self.enabled = try values.decode(Bool.self, forKey: .enabled)
        self.suppressTyping = try values.decode(Bool.self, forKey: .suppressTyping)
        self.suppressStickerSelection = try values.decode(Bool.self, forKey: .suppressStickerSelection)
        self.suppressRecording = try values.decode(Bool.self, forKey: .suppressRecording)
        self.suppressUploads = try values.decode(Bool.self, forKey: .suppressUploads)
        self.suppressGamePlay = try values.decode(Bool.self, forKey: .suppressGamePlay)
        // An existing activity-only policy must not silently acquire extra
        // protections. New installations select these options, with Ghost off.
        if storedVersion == 1 {
            self.suppressReadReceipts = false
            self.suppressOnlineStatus = false
            self.automaticOffline = false
        } else {
            self.suppressReadReceipts = try values.decode(Bool.self, forKey: .suppressReadReceipts)
            self.suppressOnlineStatus = try values.decode(Bool.self, forKey: .suppressOnlineStatus)
            self.automaticOffline = try values.decode(Bool.self, forKey: .automaticOffline)
        }
    }
}

public enum LocalGhostSettingsState: Equatable {
    case available(LocalGhostSettings)
    /// Do not replace an unknown schema with defaults or emit covered activities
    /// while stored privacy preferences cannot be interpreted.
    case unsupported
}

func localGhostSettings(entry: PreferencesEntry?) -> LocalGhostSettingsState {
    guard let entry else { return .available(LocalGhostSettings()) }
    guard entry.data.count <= 4096, let value = entry.get(LocalGhostSettings.self), value.schemaVersion == 2 else {
        return .unsupported
    }
    return .available(value)
}

func localGhostSettings(transaction: Transaction) -> LocalGhostSettingsState {
    return localGhostSettings(entry: transaction.getPreferencesEntry(key: localGhostSettingsKey))
}

func localGhostSettingsSignal(postbox: Postbox) -> Signal<LocalGhostSettingsState, NoError> {
    // Wait for the real account preference value. A synthetic initial default
    // could otherwise send an activity before an enabled policy has loaded.
    return postbox.preferencesView(keys: [localGhostSettingsKey])
    |> map { view in localGhostSettings(entry: view.values[localGhostSettingsKey]) }
    |> distinctUntilChanged
}

func localGhostAllowsActivity(_ activity: PeerInputActivity?, settings: LocalGhostSettingsState) -> Bool {
    guard let activity else { return true }
    let configured: LocalGhostSettings
    switch settings {
    case let .available(value):
        if !value.enabled { return true }
        configured = value
    case .unsupported:
        configured = LocalGhostSettings(enabled: true)
    }
    switch activity {
    case .typingText:
        return !configured.suppressTyping
    case .choosingSticker:
        return !configured.suppressStickerSelection
    case .recordingVoice, .recordingInstantVideo:
        return !configured.suppressRecording
    case .uploadingFile, .uploadingPhoto, .uploadingVideo, .uploadingInstantVideo:
        return !configured.suppressUploads
    case .playingGame:
        return !configured.suppressGamePlay
    case .speakingInGroupCall, .interactingWithEmoji, .seeingEmojiInteraction:
        // These are explicit interaction/call behavior, outside this increment.
        return true
    }
}


/// Only ordinary cloud receipts are covered. Secret-chat read operations and
/// timed/view-once consumption remain upstream so Ghost cannot stop expiry.
func localGhostSuppressesReadReceipts(_ settings: LocalGhostSettingsState, peerId: PeerId) -> Bool {
    guard peerId.namespace != Namespaces.Peer.SecretChat else { return false }
    switch settings {
    case let .available(value):
        return value.enabled && value.suppressReadReceipts
    case .unsupported:
        return true
    }
}

func localGhostSuppressesReadReceipts(transaction: Transaction, peerId: PeerId) -> Bool {
    return localGhostSuppressesReadReceipts(localGhostSettings(transaction: transaction), peerId: peerId)
}

/// Unknown message metadata is allowed through deliberately: suppressing a
/// mandatory consumed-content notification could stop the server expiry flow.
func localGhostContentReadIds(transaction: Transaction, messageIds: [MessageId]) -> [MessageId] {
    let settings = localGhostSettings(transaction: transaction)
    return messageIds.filter { id in
        guard localGhostSuppressesReadReceipts(settings, peerId: id.peerId) else { return true }
        guard let message = transaction.getMessage(id) else { return true }
        return message.isSelfExpiring
    }
}

func localGhostSuppressesOnline(_ settings: LocalGhostSettingsState) -> Bool {
    switch settings {
    case let .available(value): return value.enabled && value.suppressOnlineStatus
    case .unsupported: return true
    }
}

func localGhostAttemptsAutomaticOffline(_ settings: LocalGhostSettingsState) -> Bool {
    guard case let .available(value) = settings else { return false }
    return value.enabled && value.suppressOnlineStatus && value.automaticOffline
}

/// Complete suppressed work locally without constructing an RPC or fabricating
/// a Telegram result/PTS. A request already dispatched cannot be recalled.
func localGhostReadReceipt<T, E>(transaction: Transaction, peerId: PeerId,
                                request: () -> Signal<T, E>) -> Signal<T, E> {
    if localGhostSuppressesReadReceipts(transaction: transaction, peerId: peerId) { return .complete() }
    return request()
}

func localGhostReadReceipt<T, E>(postbox: Postbox, peerId: PeerId,
                                request: @escaping () -> Signal<T, E>) -> Signal<T, E> {
    return postbox.transaction { transaction in
        return !localGhostSuppressesReadReceipts(transaction: transaction, peerId: peerId)
    }
    |> castError(E.self)
    |> mapToSignal { allowed -> Signal<T, E> in allowed ? request() : .complete() }
}

/// Pagination must terminate, not restart, when a whole page is suppressed.
/// nil is the caller's end-of-pages cursor, never a fabricated Telegram result.
func localGhostContentReadReceiptPage<T, E>(postbox: Postbox, messageIds: [MessageId],
                                          request: @escaping ([MessageId]) -> Signal<T?, E>) -> Signal<T?, E> {
    return postbox.transaction { transaction in localGhostContentReadIds(transaction: transaction, messageIds: messageIds) }
    |> castError(E.self)
    |> mapToSignal { ids -> Signal<T?, E> in ids.isEmpty ? .single(nil) : request(ids) }
}
