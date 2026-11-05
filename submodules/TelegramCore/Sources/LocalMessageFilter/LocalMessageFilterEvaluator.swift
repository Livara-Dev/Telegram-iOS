// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

public enum LocalMessageFilterEvaluation: Equatable {
    case noMatch
    case matched(ruleId: String)
    /// The whole message remains visible; a truncated body is never evaluated.
    case skippedOversize
    /// An expression exhausted its finite-state work budget. Keep the complete
    /// canonical message visible instead of making a partial hiding decision.
    case skippedWorkLimit
    case unavailable
}

/// Pure, immutable bounded matcher. Call it on the history preparation queue,
/// not the main thread. No cache retains messages or results between evaluations.
public struct LocalMessageFilterEvaluator {
    private let settings: LocalMessageFilterSettings
    private let valid: Bool
    private let regularExpressions: [String: LocalMessageFilterRegularExpression]

    public init(settings: LocalMessageFilterSettings) {
        self.settings = settings
        self.valid = validLocalMessageFilterSettings(settings)
        var expressions: [String: LocalMessageFilterRegularExpression] = [:]
        if self.valid {
            for rule in settings.rules where rule.matchingMode == .regularExpression {
                expressions[rule.id] = LocalMessageFilterRegularExpression(pattern: rule.text, caseSensitive: rule.caseSensitive)
            }
        }
        self.regularExpressions = expressions
    }

    public func evaluate(text: String, peerId: Int64, mediaType: LocalMessageFilterMediaType? = nil, senderIsLocallyBlocked: Bool = false, senderId: Int64? = nil) -> LocalMessageFilterEvaluation {
        guard self.valid else { return .unavailable }
        guard self.settings.enabled else { return .noMatch }

        let skipsGlobal = self.settings.excludedPeerIds.contains(peerId)
        if !skipsGlobal, let senderId, self.settings.hiddenSenderIds.contains(senderId) {
            return .matched(ruleId: "local-hidden-sender")
        }
        if self.settings.hideBlockedSenders && senderIsLocallyBlocked && !skipsGlobal {
            return .matched(ruleId: "local-blocked-sender")
        }
        let applicable = self.settings.rules.filter { rule in
            if let targetPeerId = rule.peerId { return targetPeerId == peerId }
            return !skipsGlobal
        }
        guard !applicable.isEmpty else { return .noMatch }
        for rule in applicable {
            // Nil media means text-only or unavailable classification; never
            // turn absence of metadata into a successful inverse predicate.
            if let type = rule.mediaType, let mediaType, (type == mediaType) != rule.isInverted {
                return .matched(ruleId: rule.id)
            }
        }
        let textRules = applicable.filter { $0.mediaType == nil }
        guard !textRules.isEmpty else { return .noMatch }
        guard !text.isEmpty else { return .noMatch }
        // Check at most max+1 code units before bridging/searching the body.
        // Do not allocate an unbounded UTF16 array or match a shortened message.
        guard text.utf16.prefix(LocalMessageFilterLimits.maximumInputUTF16Length + 1).count <= LocalMessageFilterLimits.maximumInputUTF16Length else {
            return .skippedOversize
        }
        let value = text as NSString
        let fullRange = NSRange(location: 0, length: value.length)
        let locale = Locale(identifier: "en_US_POSIX")
        var remainingWork = LocalMessageFilterLimits.maximumRegularExpressionSteps
        for rule in textRules {
            if rule.matchingMode == .regularExpression {
                guard let expression = self.regularExpressions[rule.id] else { return .unavailable }
                switch expression.evaluate(text, remainingWork: &remainingWork) {
                case .matched:
                    if !rule.isInverted { return .matched(ruleId: rule.id) }
                    continue
                case .noMatch:
                    if rule.isInverted { return .matched(ruleId: rule.id) }
                    continue
                case .workLimit: return .skippedWorkLimit
                }
            }
            // No regex, wildcards or diacritic-insensitive option. Foundation's
            // canonical Unicode equivalence applies; case folding uses a fixed
            // locale rather than the device's current language.
            let options: NSString.CompareOptions = rule.caseSensitive ? [] : [.caseInsensitive]
            let matches = value.range(of: rule.text, options: options, range: fullRange, locale: locale).location != NSNotFound
            if matches != rule.isInverted {
                return .matched(ruleId: rule.id)
            }
        }
        return .noMatch
    }
}
