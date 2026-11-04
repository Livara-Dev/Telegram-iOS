import Postbox
import SwiftSignalKit

/// Retire only the legacy app-event queue for this account. No Network is
/// accepted: startup cleanup cannot upload stored diagnostics or resurrect them.
/// Producers are disabled, so one atomic cleanup needs no polling/restart loop.
func managedSynchronizeAppLogEventsOperations(postbox: Postbox) -> Signal<Void, NoError> {
    return postbox.transaction { transaction in
        transaction.operationLogRemoveAllEntries(peerId: PeerId(0), tag: OperationLogTags.SynchronizeAppLogEvents)
    }
}
