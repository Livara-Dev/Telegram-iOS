// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

public enum LocalMessageFilterEvaluation: Equatable {
    case noMatch
    case matched(ruleId: String)
    /// The whole message remains visible; a truncated body is never evaluated.
    case skippedOversize
    case unavailable
}

/// Pure, immutable literal matcher. Call it on the history preparation queue,
/// not the main thread. No cache retains messages or results between evaluations.
public struct LocalMessageFilterEvaluator {
    private let settings: LocalMessageFilterSettings
    private let valid: Bool

    public init(settings: LocalMessageFilterSettings) {
        self.settings = settings
        self.valid = validLocalMessageFilterSettings(settings)
    }

    public func evaluate(text: String, peerId: Int64) -> LocalMessageFilterEvaluation {
        guard self.valid else { return .unavailable }
        guard self.settings.enabled else { return .noMatch }

        let skipsGlobal = self.settings.excludedPeerIds.contains(peerId)
        let applicable = self.settings.rules.filter { rule in
            if let targetPeerId = rule.peerId { return targetPeerId == peerId }
            return !skipsGlobal
        }
        guard !applicable.isEmpty else { return .noMatch }
        // Check at most max+1 code units before bridging/searching the body.
        // Do not allocate an unbounded UTF16 array or match a shortened message.
        guard text.utf16.prefix(LocalMessageFilterLimits.maximumInputUTF16Length + 1).count <= LocalMessageFilterLimits.maximumInputUTF16Length else {
            return .skippedOversize
        }
        let value = text as NSString
        let fullRange = NSRange(location: 0, length: value.length)
        let locale = Locale(identifier: "en_US_POSIX")
        for rule in applicable {
            // No regex, wildcards or diacritic-insensitive option. Foundation's
            // canonical Unicode equivalence applies; case folding uses a fixed
            // locale rather than the device's current language.
            let options: NSString.CompareOptions = rule.caseSensitive ? [] : [.caseInsensitive]
            if value.range(of: rule.text, options: options, range: fullRange, locale: locale).location != NSNotFound {
                return .matched(ruleId: rule.id)
            }
        }
        return .noMatch
    }
}
