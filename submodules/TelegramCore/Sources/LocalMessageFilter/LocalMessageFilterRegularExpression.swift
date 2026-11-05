// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

/// A deliberately restricted regular language, evaluated as a finite-state
/// machine. There is no recursion, backtracking, JIT or arbitrary regex engine.
/// Unsupported syntax is rejected when the rule is saved, never reinterpreted
/// as a different expression. Matching work consumes the caller's shared budget.
struct LocalMessageFilterRegularExpression {
    enum Result: Equatable { case matched, noMatch, workLimit }
    private enum Repetition { case one, optional, zeroOrMore, oneOrMore }
    private enum Shortcut { case digit, word, whitespace }
    private enum Member {
        case range(UInt32, UInt32)
        case shortcut(Shortcut, negated: Bool)
    }
    private enum Matcher {
        case literal(UInt32, folded: String)
        case any
        case characterClass([Member], negated: Bool)
    }
    private struct Token {
        let matcher: Matcher
        let repetition: Repetition
    }
    private struct Input {
        let scalar: Unicode.Scalar
        let folded: String
        let lower: UInt32?
        let upper: UInt32?
    }

    private let tokens: [Token]
    private let anchoredStart: Bool
    private let anchoredEnd: Bool
    private let caseSensitive: Bool
    private static let locale = Locale(identifier: "en_US_POSIX")

    init?(pattern: String, caseSensitive: Bool) {
        guard !pattern.isEmpty,
              pattern.utf16.prefix(LocalMessageFilterLimits.maximumLiteralUTF16Length + 1).count <= LocalMessageFilterLimits.maximumLiteralUTF16Length else { return nil }
        var parser = Parser(scalars: Array(pattern.precomposedStringWithCanonicalMapping.unicodeScalars))
        guard let parsed = parser.parse(), !parsed.tokens.isEmpty else { return nil }
        self.tokens = parsed.tokens
        self.anchoredStart = parsed.start
        self.anchoredEnd = parsed.end
        self.caseSensitive = caseSensitive
    }

    private static func singleScalar(_ value: String) -> UInt32? {
        var iterator = value.unicodeScalars.makeIterator()
        guard let scalar = iterator.next(), iterator.next() == nil else { return nil }
        return scalar.value
    }

    private func input(_ scalar: Unicode.Scalar) -> Input {
        if self.caseSensitive {
            return Input(scalar: scalar, folded: "", lower: nil, upper: nil)
        }
        let text = String(scalar)
        let lower = text.lowercased(with: Self.locale)
        return Input(scalar: scalar, folded: lower, lower: Self.singleScalar(lower), upper: Self.singleScalar(text.uppercased(with: Self.locale)))
    }

    private func matches(_ matcher: Matcher, input: Input, remaining: inout Int) -> Bool? {
        switch matcher {
        case let .literal(value, folded):
            return self.caseSensitive ? input.scalar.value == value : input.folded == folded
        case .any:
            return ![UInt32(10), 13, 0x85, 0x2028, 0x2029].contains(input.scalar.value)
        case let .characterClass(members, negated):
            var included = false
            for member in members {
                guard remaining > 0 else { return nil }
                remaining -= 1
                let memberMatches: Bool
                switch member {
                case let .range(lower, upper):
                    var matched = lower...upper ~= input.scalar.value
                    if !self.caseSensitive {
                        if let value = input.lower, lower...upper ~= value { matched = true }
                        if let value = input.upper, lower...upper ~= value { matched = true }
                    }
                    memberMatches = matched
                case let .shortcut(shortcut, negated):
                    let matched: Bool
                    switch shortcut {
                    case .digit:
                        matched = 48...57 ~= input.scalar.value
                    case .word:
                        matched = 48...57 ~= input.scalar.value || 65...90 ~= input.scalar.value || 97...122 ~= input.scalar.value || input.scalar.value == 95
                    case .whitespace:
                        matched = CharacterSet.whitespacesAndNewlines.contains(input.scalar)
                    }
                    memberMatches = negated ? !matched : matched
                }
                if memberMatches { included = true; break }
            }
            return negated ? !included : included
        }
    }

    private func epsilonClosure(_ states: inout Set<Int>, remaining: inout Int) -> Bool {
        var pending = Array(states)
        var offset = 0
        while offset < pending.count {
            guard remaining > 0 else { return false }
            remaining -= 1
            let index = pending[offset]
            offset += 1
            guard index < self.tokens.count else { continue }
            switch self.tokens[index].repetition {
            case .optional, .zeroOrMore:
                if states.insert(index + 1).inserted { pending.append(index + 1) }
            case .one, .oneOrMore:
                break
            }
        }
        return true
    }

    func evaluate(_ text: String, remainingWork: inout Int) -> Result {
        guard remainingWork > 0 else { return .workLimit }
        // The public evaluator bounds the complete UTF-16 input before calling
        // this function. Keep that invariant here for direct test callers too.
        guard text.utf16.prefix(LocalMessageFilterLimits.maximumInputUTF16Length + 1).count <= LocalMessageFilterLimits.maximumInputUTF16Length else { return .workLimit }
        let characters = Array(text.precomposedStringWithCanonicalMapping.unicodeScalars)
        var active: Set<Int> = [0]
        for position in 0...characters.count {
            if !self.anchoredStart { active.insert(0) }
            guard self.epsilonClosure(&active, remaining: &remainingWork) else { return .workLimit }
            if active.contains(self.tokens.count), !self.anchoredEnd || position == characters.count {
                return .matched
            }
            if position == characters.count { break }
            let input = self.input(characters[position])
            var next = Set<Int>()
            for index in active where index < self.tokens.count {
                guard remainingWork > 0 else { return .workLimit }
                remainingWork -= 1
                let token = self.tokens[index]
                guard let matched = self.matches(token.matcher, input: input, remaining: &remainingWork) else { return .workLimit }
                if matched {
                    switch token.repetition {
                    case .one, .optional: next.insert(index + 1)
                    case .zeroOrMore: next.insert(index)
                    case .oneOrMore: next.insert(index); next.insert(index + 1)
                    }
                }
            }
            active = next
            if self.anchoredStart && active.isEmpty { return .noMatch }
        }
        return .noMatch
    }

    private struct Parser {
        let scalars: [Unicode.Scalar]
        var index = 0

        mutating func parse() -> (tokens: [Token], start: Bool, end: Bool)? {
            var start = false
            var end = false
            var tokens: [Token] = []
            if self.peek == "^" { start = true; self.index += 1 }
            while self.index < self.scalars.count {
                if self.peek == "$" {
                    guard self.index == self.scalars.count - 1 else { return nil }
                    end = true; self.index += 1; break
                }
                guard let matcher = self.atom() else { return nil }
                let repetition: Repetition
                switch self.peek {
                case "*": repetition = .zeroOrMore; self.index += 1
                case "+": repetition = .oneOrMore; self.index += 1
                case "?": repetition = .optional; self.index += 1
                default: repetition = .one
                }
                tokens.append(Token(matcher: matcher, repetition: repetition))
                guard tokens.count <= LocalMessageFilterLimits.maximumLiteralUTF16Length else { return nil }
            }
            return (tokens, start, end)
        }

        private var peek: Unicode.Scalar? {
            return self.index < self.scalars.count ? self.scalars[self.index] : nil
        }

        private static func literal(_ scalar: Unicode.Scalar) -> Matcher {
            return .literal(scalar.value, folded: String(scalar).lowercased(with: LocalMessageFilterRegularExpression.locale))
        }

        private mutating func atom() -> Matcher? {
            guard let scalar = self.peek else { return nil }
            self.index += 1
            switch scalar {
            case ".": return .any
            case "[": return self.characterClass()
            case "\\":
                guard let escaped = self.escape() else { return nil }
                switch escaped {
                case let .range(lower, upper):
                    guard lower == upper, let value = Unicode.Scalar(lower) else { return nil }
                    return Self.literal(value)
                case .shortcut: return .characterClass([escaped], negated: false)
                }
            case "^", "$", "*", "+", "?", "{", "}", "(", ")", "|", "]": return nil
            default: return Self.literal(scalar)
            }
        }

        private mutating func escape() -> Member? {
            guard let scalar = self.peek else { return nil }
            self.index += 1
            switch scalar {
            case "d": return .shortcut(.digit, negated: false)
            case "D": return .shortcut(.digit, negated: true)
            case "w": return .shortcut(.word, negated: false)
            case "W": return .shortcut(.word, negated: true)
            case "s": return .shortcut(.whitespace, negated: false)
            case "S": return .shortcut(.whitespace, negated: true)
            case "n": return .range(10, 10)
            case "r": return .range(13, 13)
            case "t": return .range(9, 9)
            case "\\", ".", "^", "$", "*", "+", "?", "{", "}", "(", ")", "|", "[", "]", "-": return .range(scalar.value, scalar.value)
            default: return nil
            }
        }

        private mutating func classMember() -> Member? {
            guard let scalar = self.peek, scalar != "]" else { return nil }
            self.index += 1
            if scalar == "\\" { return self.escape() }
            return .range(scalar.value, scalar.value)
        }

        private mutating func characterClass() -> Matcher? {
            var negated = false
            if self.peek == "^" { negated = true; self.index += 1 }
            var members: [Member] = []
            while self.index < self.scalars.count {
                if self.peek == "]" {
                    self.index += 1
                    return members.isEmpty ? nil : .characterClass(members, negated: negated)
                }
                guard let first = self.classMember() else { return nil }
                if self.peek == "-", self.index + 1 < self.scalars.count, self.scalars[self.index + 1] != "]" {
                    self.index += 1
                    guard case let .range(lower, firstUpper) = first, lower == firstUpper,
                          let last = self.classMember(), case let .range(lastLower, upper) = last,
                          lastLower == upper, lower <= upper else { return nil }
                    members.append(.range(lower, upper))
                } else {
                    members.append(first)
                }
            }
            return nil
        }
    }
}
