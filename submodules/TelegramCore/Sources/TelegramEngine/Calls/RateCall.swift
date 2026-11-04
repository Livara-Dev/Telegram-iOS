import Foundation
import Postbox
import MtProtoKit
import SwiftSignalKit
internal import TelegramApi

func _internal_rateCall(account: Account, callId: CallId, starsCount: Int32, comment: String = "", userInitiated: Bool) -> Signal<Void, NoError> {
    var flags: Int32 = 0
    if userInitiated {
        flags |= (1 << 0)
    }
    return account.network.request(Api.functions.phone.setCallRating(flags: flags, peer: Api.InputPhoneCall.inputPhoneCall(.init(id: callId.id, accessHash: callId.accessHash)), rating: starsCount, comment: comment))
    |> retryRequest
    |> map { _ in }
}

public enum SaveCallDebugLogResult {
    case done
    case sendFullLog
}

// Automatic diagnostic submission is disabled, before reading or compressing files.
func _internal_saveCallDebugLog(network: Network, callId: CallId, log: String) -> Signal<SaveCallDebugLogResult, NoError> {
    return .complete()
}

func _internal_saveCompleteCallDebugLog(account: Account, callId: CallId, logPath: String) -> Signal<Never, NoError> {
    return .complete()
}
