// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

let localGhostSettingsKey = ValueBoxKey("ayugram.ghost.activity.v1")

/// This experimental policy covers outgoing activity indicators only. It does
/// not suppress message/story receipts or presence and does not imply invisibility.
public struct LocalGhostSettings: Codable, Equatable {
    let schemaVersion: Int32
    public var enabled: Bool
    public var suppressTyping: Bool
    public var suppressStickerSelection: Bool
    public var suppressRecording: Bool
    public var suppressUploads: Bool
    public var suppressGamePlay: Bool

    public init(enabled: Bool = false, suppressTyping: Bool = true,
                suppressStickerSelection: Bool = true, suppressRecording: Bool = true,
                suppressUploads: Bool = true, suppressGamePlay: Bool = true) {
        self.schemaVersion = 1
        self.enabled = enabled
        self.suppressTyping = suppressTyping
        self.suppressStickerSelection = suppressStickerSelection
        self.suppressRecording = suppressRecording
        self.suppressUploads = suppressUploads
        self.suppressGamePlay = suppressGamePlay
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
    guard let value = entry.get(LocalGhostSettings.self), value.schemaVersion == 1 else {
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
