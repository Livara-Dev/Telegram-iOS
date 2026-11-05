// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// The main app may use its own sandbox when a personal signing profile cannot
/// grant App Groups. A selected location is pinned: later capability changes
/// must never silently open a different, empty account database.
public enum ApplicationStorageError: Error {
    case invalidConfiguration
    case selectedContainerUnavailable
    case selectionNotPersisted
}

public struct ApplicationStorageLocation: Sendable {
    public let baseURL: URL
    public let appGroupIdentifier: String?

    public var usesPrivateContainer: Bool { self.appGroupIdentifier == nil }
}

public enum ApplicationStorage {
    public static let selectionKey = "org.livara.ayugram.startup-storage.v1"

    struct Selection: Equatable {
        let group: String?

        var dictionary: [String: Any] {
            var value: [String: Any] = ["version": 1, "kind": self.group == nil ? "private" : "group"]
            if let group = self.group { value["identifier"] = group }
            return value
        }

        init(group: String?) { self.group = group }

        init(saved: Any) throws {
            guard let value = saved as? [String: Any], value["version"] as? Int == 1,
                  let kind = value["kind"] as? String else { throw ApplicationStorageError.invalidConfiguration }
            switch kind {
            case "private":
                guard value["identifier"] == nil else { throw ApplicationStorageError.invalidConfiguration }
                self.group = nil
            case "group":
                guard let group = value["identifier"] as? String, Self.validGroup(group) else { throw ApplicationStorageError.invalidConfiguration }
                self.group = group
            default:
                throw ApplicationStorageError.invalidConfiguration
            }
        }

        static func validGroup(_ group: String) -> Bool {
            guard group.hasPrefix("group."), group.count > 6, group.count < 256 else { return false }
            let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_")
            return group.unicodeScalars.allSatisfy { allowed.contains($0) } && !group.contains("..")
        }
    }

    static func choose(saved: Any?, candidates: [String], privateURL: URL,
                       container: (String) -> URL?) throws -> (Selection, ApplicationStorageLocation) {
        guard !candidates.isEmpty, candidates.allSatisfy(Selection.validGroup), privateURL.isFileURL else {
            throw ApplicationStorageError.invalidConfiguration
        }
        if let saved = saved {
            let selection = try Selection(saved: saved)
            if let group = selection.group {
                // Only the exact runtime/configured identifiers are eligible.
                // An identity or entitlement change requires explicit recovery.
                guard candidates.contains(group), let url = container(group), url.isFileURL else {
                    throw ApplicationStorageError.selectedContainerUnavailable
                }
                return (selection, ApplicationStorageLocation(baseURL: url, appGroupIdentifier: group))
            }
            return (selection, ApplicationStorageLocation(baseURL: privateURL, appGroupIdentifier: nil))
        }
        for group in candidates {
            if let url = container(group), url.isFileURL {
                return (Selection(group: group), ApplicationStorageLocation(baseURL: url, appGroupIdentifier: group))
            }
        }
        return (Selection(group: nil), ApplicationStorageLocation(baseURL: privateURL, appGroupIdentifier: nil))
    }

    public static func resolve(bundleIdentifier: String, configuredBundleIdentifier: String? = nil,
                               defaults: UserDefaults = .standard) throws -> ApplicationStorageLocation {
        let fileManager = FileManager.default
        guard let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            throw ApplicationStorageError.selectedContainerUnavailable
        }
        var candidates = ["group." + bundleIdentifier]
        if let configured = configuredBundleIdentifier, configured != bundleIdentifier {
            candidates.append("group." + configured)
        }
        let saved = defaults.object(forKey: self.selectionKey)
        let (selection, location) = try self.choose(saved: saved, candidates: candidates,
            privateURL: support.appendingPathComponent("AyuGram", isDirectory: true), container: {
                #if os(iOS)
                return fileManager.containerURL(forSecurityApplicationGroupIdentifier: $0)
                #else
                return nil
                #endif
            })
        if saved == nil {
            defaults.set(selection.dictionary, forKey: self.selectionKey)
            // One first-launch flush before any account database is created.
            guard defaults.synchronize() else { throw ApplicationStorageError.selectionNotPersisted }
        }
        if location.usesPrivateContainer {
            if (try? location.baseURL.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true {
                throw ApplicationStorageError.selectedContainerUnavailable
            }
            #if os(iOS)
            try fileManager.createDirectory(at: location.baseURL, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            #else
            try fileManager.createDirectory(at: location.baseURL, withIntermediateDirectories: true)
            #endif
        }
        return location
    }
}
