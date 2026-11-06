// Modified for AyuGram iOS.
// Modification dates recorded in Git history: 2026-11-04.
// See this file's history for the corresponding changes.
import Foundation
import Postbox

// Optional analytics are disabled for this fork. Keep the producer API so
// existing product actions retain their behavior without queueing event data.
func _internal_addAppLogEvent(postbox: Postbox, time: Double = Date().timeIntervalSince1970, type: String, peerId: PeerId? = nil, data: JSON = .dictionary([:])) {
}

func _internal_addAppLogEvent(transaction: Transaction, time: Double, type: String, data: JSON) {
}

public func invokeAppLogEventsSynchronization(postbox: Postbox) {
}
