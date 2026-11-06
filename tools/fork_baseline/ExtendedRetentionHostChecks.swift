// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation

@main
struct ExtendedRetentionHostChecks {
    static func main() throws {
        var assertions = 0
        func check(_ condition: Bool, _ message: String) {
            assertions += 1
            precondition(condition, message)
        }
        let contextTypes: [(ArchiveCaptureContext, Int)] = [
            (.init(ordinaryCloudMessage: true), 0), (.init(secretChat: true), 1),
            (.init(ordinaryCloudMessage: true, expiring: true), 2), (.init(secretChat: true, expiring: true), 3),
            (.init(ordinaryCloudMessage: true, expiring: true, viewOnce: true), 2),
            (.init(secretChat: true, expiring: true, viewOnce: true), 3),
            (.init(), -1), (.init(ordinaryCloudMessage: true, secretChat: true), -1),
            (.init(ordinaryCloudMessage: true, viewOnce: true), -1), (.init(secretChat: true, viewOnce: true), -1)
        ]
        for flags in 0..<128 {
            let enabled = flags & 1 != 0, secret = flags & 2 != 0, timed = flags & 4 != 0
            let peerExcluded = flags & 8 != 0, senderExcluded = flags & 16 != 0
            let botExcluded = flags & 32 != 0, isBot = flags & 64 != 0
            let policy = ArchiveCapturePolicy(enabled: enabled,
                excludedPeers: peerExcluded ? [42] : [], excludedSenders: senderExcluded ? [9] : [],
                excludeBots: botExcluded, includeSecretChats: secret, includeExpiringMessages: timed)
            for (original, capability) in contextTypes {
                for author: Int64? in [9, nil] {
                    var context = original; context.botConversation = isBot
                    let event = ArchiveEvent(eventKey: "fixture", message: .init(accountId: 1, peerId: 42, namespace: 0, messageId: 1),
                        kind: .deleted, observedAt: 1, content: .init(text: "owned fixture", authorId: author, sentAt: 0))
                    let allowedCapabilities: Set<Int> = secret && timed ? [0,1,2,3] : secret ? [0,1] : timed ? [0,2] : [0]
                    let expected = enabled && !peerExcluded && !senderExcluded && !(botExcluded && isBot) && allowedCapabilities.contains(capability)
                    check(policy.allows(event, context: context) == expected, "policy truth-table mismatch")
                }
            }
        }
        let valid = ["telegram-cloud-photo-1-2", "secret-file-1-1", "secret-file--1-1",
            "secret-file--9223372036854775808-2", "secret-file-9223372036854775807-2147483647",
            "telegram-local-file-0", "telegram-local-file--19"]
        let invalid = ["", "secret-file-", "secret-file-1", "secret-file-1-0", "secret-file-1--1", "secret-file-01-1",
            "secret-file-1-01", "secret-file-+1-1", "secret-file-1-2147483648", "secret-file-9223372036854775808-1",
            "secret-file-1-1/../key", "telegram-local-file-01", "telegram-local-file--0", "telegram-local-file-1/../key",
            "telegram-local-file-1\0", "../telegram-cloud-photo", "/tmp/photo", "secret-file-١-1", String(repeating: "a", count: 1025)]
        for value in valid { check(localArchiveSafeResourceKey(value), "valid resource identifier rejected") }
        for value in invalid { check(!localArchiveSafeResourceKey(value), "unsafe resource identifier accepted") }
        let report: [String: Any] = ["passed": true, "assertions": assertions]
        print(String(data: try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]), encoding: .utf8)!)
    }
}
