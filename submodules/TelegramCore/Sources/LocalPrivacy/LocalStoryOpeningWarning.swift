// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import Postbox
import SwiftSignalKit

let localStoryOpeningWarningKey = ValueBoxKey("ayugram.ghost.story-opening.v1")

public struct LocalStoryOpeningWarningSettings: Codable, Equatable {
    let schemaVersion: Int32
    public let enabled: Bool

    public init(enabled: Bool = true) {
        self.schemaVersion = 1
        self.enabled = enabled
    }
}

public enum LocalStoryOpeningWarningSettingsState: Equatable {
    case available(LocalStoryOpeningWarningSettings)
    case unsupported
}

func localStoryOpeningWarningSettings(entry: PreferencesEntry?) -> LocalStoryOpeningWarningSettingsState {
    guard let entry else { return .available(LocalStoryOpeningWarningSettings()) }
    guard entry.data.count <= 2048, let value = entry.get(LocalStoryOpeningWarningSettings.self),
          value.schemaVersion == 1 else { return .unsupported }
    return .available(value)
}

func localStoryOpeningWarningSettings(transaction: Transaction) -> LocalStoryOpeningWarningSettingsState {
    return localStoryOpeningWarningSettings(entry: transaction.getPreferencesEntry(key: localStoryOpeningWarningKey))
}

func localStoryOpeningWarningSettingsSignal(postbox: Postbox) -> Signal<LocalStoryOpeningWarningSettingsState, NoError> {
    return postbox.preferencesView(keys: [localStoryOpeningWarningKey])
    |> map { localStoryOpeningWarningSettings(entry: $0.values[localStoryOpeningWarningKey]) }
    |> distinctUntilChanged
}

func updateLocalStoryOpeningWarning(postbox: Postbox, enabled: Bool) -> Signal<Bool, NoError> {
    return postbox.transaction { transaction in
        guard case .available = localStoryOpeningWarningSettings(transaction: transaction),
              let entry = PreferencesEntry(LocalStoryOpeningWarningSettings(enabled: enabled)) else { return false }
        transaction.setPreferencesEntry(key: localStoryOpeningWarningKey, value: entry)
        return true
    }
}

/// One approval belongs to one viewer instance, not a global permission. UI
/// holds this object on the main queue and blocks all story rendering until it
/// is allowed. Cancellation wins over a late local-policy write completion.
public final class LocalStoryOpeningGate {
    public enum State: Equatable {
        case waitingForPolicy
        case confirmationRequired
        case savingPrivacy
        case allowed
        case cancelled
    }

    public private(set) var state: State = .waitingForPolicy

    public init() {
    }

    public var allowsContent: Bool { return self.state == .allowed }

    public func resolve(_ policy: LocalStoryOpeningWarningSettingsState) {
        guard self.state == .waitingForPolicy else { return }
        switch policy {
        case let .available(settings):
            self.state = settings.enabled ? .confirmationRequired : .allowed
        case .unsupported:
            self.state = .confirmationRequired
        }
    }

    @discardableResult
    public func approve() -> Bool {
        guard self.state == .confirmationRequired else { return false }
        self.state = .allowed
        return true
    }

    @discardableResult
    public func beginEnablingPrivacy() -> Bool {
        guard self.state == .confirmationRequired else { return false }
        self.state = .savingPrivacy
        return true
    }

    @discardableResult
    public func finishEnablingPrivacy(_ success: Bool) -> Bool {
        guard self.state == .savingPrivacy else { return false }
        self.state = success ? .allowed : .confirmationRequired
        return true
    }

    public func cancel() {
        self.state = .cancelled
    }
}
