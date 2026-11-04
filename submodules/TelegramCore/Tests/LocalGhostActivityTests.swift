// SPDX-License-Identifier: GPL-2.0-or-later
import Foundation
import XCTest
import Postbox
import SwiftSignalKit
import TelegramApi
@testable import TelegramCore

private typealias GhostTestActivities = [PeerActivitySpace: [(PeerId, PeerInputActivityRecord)]]

/// Records subscriptions at the same typed boundary as the production RPC sender.
/// Pending mode lets worker tests observe cancellation without making network calls.
private final class GhostActivityRecorder {
    private let lock = NSLock()
    private var recorded: [LocalInputActivityRequest] = []
    private var active = Set<Int>()
    private var cancelled: [Int] = []
    let pending: Bool

    init(pending: Bool = false) { self.pending = pending }

    var requests: [LocalInputActivityRequest] {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.recorded
    }

    var activeCount: Int {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.active.count
    }

    var cancellationCount: Int {
        self.lock.lock()
        defer { self.lock.unlock() }
        return self.cancelled.count
    }

    func send(_ request: LocalInputActivityRequest) -> Signal<Void, NoError> {
        return Signal { subscriber in
            self.lock.lock()
            let index = self.recorded.count
            self.recorded.append(request)
            self.active.insert(index)
            self.lock.unlock()
            if !self.pending { subscriber.putCompletion() }
            return ActionDisposable {
                self.lock.lock()
                if self.active.remove(index) != nil { self.cancelled.append(index) }
                self.lock.unlock()
            }
        }
    }
}

/// These tests exercise the real Postbox resolver and managed activity worker.
/// The injected sender replaces only the final network request, not its policy,
/// peer/presence checks, request mapping, settings observation, or cancellation.
final class LocalGhostActivityTests: XCTestCase {
    private let accountId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1))
    private let peerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(1001))
    private var fixture: LocalArchivePostboxFixture!
    private var workers: [(Disposable, Queue)] = []

    override func setUpWithError() throws {
        self.fixture = try LocalArchivePostboxFixture(test: self, name: "ghost-activity")
        try self.seed(self.fixture.postbox, accountId: self.accountId)
    }

    override func tearDownWithError() throws {
        self.stopWorkers()
        try self.fixture?.destroy()
        self.fixture = nil
    }

    private func stopWorkers() {
        for (worker, queue) in self.workers {
            worker.dispose()
            queue.sync {}
        }
        self.workers.removeAll()
    }

    private func user(_ id: PeerId, accessHash: TelegramPeerAccessHash? = .personal(12345)) -> TelegramUser {
        return TelegramUser(id: id, accessHash: accessHash, firstName: "Ghost fixture", lastName: nil,
            username: nil, phone: nil, photo: [], botInfo: nil, restrictionInfo: nil, flags: [],
            emojiStatus: nil, usernames: [], storiesHidden: nil, nameColor: nil, backgroundEmojiId: nil,
            profileColor: nil, profileBackgroundEmojiId: nil, subscriberCount: nil, verificationIconFileId: nil)
    }

    private func seed(_ postbox: Postbox, accountId: PeerId) throws {
        let peers = [self.user(accountId), self.user(self.peerId)]
        let presence = TelegramUserPresence(status: .recently(isHidden: false), lastActivity: 0)
        let peerId = self.peerId
        let _ = try self.localArchiveValue(postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, current in current })
            transaction.updatePeerPresencesInternal(presences: [peerId: presence, accountId: presence], merge: { _, current in current })
        })
    }

    private func setSettings(_ settings: LocalGhostSettings, postbox: Postbox? = nil) throws {
        let context = LocalGhostContext(postbox: postbox ?? self.fixture.postbox)
        XCTAssertTrue(try self.localArchiveValue(context.updateSettings { $0 = settings }))
    }

    private func complete(_ signal: Signal<Void, NoError>) {
        let finished = expectation(description: "activity resolver completed")
        let disposable = signal.start(completed: { finished.fulfill() })
        defer { disposable.dispose() }
        wait(for: [finished], timeout: 15)
    }

    private func resolve(_ activity: PeerInputActivity?, peerId: PeerId? = nil,
                         threadId: Int64? = nil, postbox: Postbox? = nil, accountId: PeerId? = nil) -> [LocalInputActivityRequest] {
        let recorder = GhostActivityRecorder()
        self.complete(requestLocalInputActivity(postbox: postbox ?? self.fixture.postbox,
            accountPeerId: accountId ?? self.accountId, peerId: peerId ?? self.peerId,
            threadId: threadId, activity: activity, sendRequest: recorder.send))
        return recorder.requests
    }

    private var cases: [(PeerInputActivity, String)] {
        return [
            (.typingText, "typing"),
            (.recordingVoice, "record-voice"),
            (.recordingInstantVideo, "record-round"),
            (.uploadingFile(progress: 17), "file:17"),
            (.uploadingPhoto(progress: 23), "photo:23"),
            (.uploadingVideo(progress: 42), "video:42"),
            (.uploadingInstantVideo(progress: 81), "round:81"),
            (.choosingSticker, "sticker"),
            (.playingGame, "game"),
            (.speakingInGroupCall(timestamp: 9), "speaking"),
            (.interactingWithEmoji(emoticon: "🙂", messageId: MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: 7), interaction: nil), "emoji:🙂:7"),
            (.seeingEmojiInteraction(emoticon: "🙂"), "emoji-seen:🙂")
        ]
    }

    private func actionName(_ request: LocalInputActivityRequest) -> String {
        guard case let .cloud(_, _, _, action) = request else { return "secret" }
        switch action {
        case .sendMessageTypingAction: return "typing"
        case .sendMessageRecordAudioAction: return "record-voice"
        case .sendMessageRecordRoundAction: return "record-round"
        case let .sendMessageUploadDocumentAction(data): return "file:\(data.progress)"
        case let .sendMessageUploadPhotoAction(data): return "photo:\(data.progress)"
        case let .sendMessageUploadVideoAction(data): return "video:\(data.progress)"
        case let .sendMessageUploadRoundAction(data): return "round:\(data.progress)"
        case .sendMessageChooseStickerAction: return "sticker"
        case .sendMessageGamePlayAction: return "game"
        case .speakingInGroupCallAction: return "speaking"
        case let .sendMessageEmojiInteraction(data): return "emoji:\(data.emoticon):\(data.msgId)"
        case let .sendMessageEmojiInteractionSeen(data): return "emoji-seen:\(data.emoticon)"
        case .sendMessageCancelAction: return "cancel"
        default: return "unexpected"
        }
    }

    private func activities(_ values: [PeerInputActivity], updateId: Int32 = 1,
                            category: PeerActivitySpace.Category = .global) -> GhostTestActivities {
        return [PeerActivitySpace(peerId: self.peerId, category: category): values.map {
            (self.accountId, PeerInputActivityRecord(activity: $0, updateId: updateId))
        }]
    }

    private func startWorker(_ activities: Promise<GhostTestActivities>, recorder: GhostActivityRecorder,
                             postbox: Postbox? = nil, accountId: PeerId? = nil) -> Queue {
        let queue = Queue(name: "GhostActivityTests.worker")
        let worker = managedLocalTypingActivities(activities: activities.get(), postbox: postbox ?? self.fixture.postbox,
            accountPeerId: accountId ?? self.accountId, queue: queue, sendRequest: recorder.send).start()
        self.workers.append((worker, queue))
        return queue
    }

    /// Fence both real queues instead of sleeping: settings/view -> worker ->
    /// Postbox request resolver -> final sender. A second round also drains an
    /// initial preference view registered while the first fence was queued.
    private func settle(_ queue: Queue, postbox: Postbox? = nil) throws {
        let postbox = postbox ?? self.fixture.postbox!
        for _ in 0..<2 {
            queue.sync {}
            let _ = try self.localArchiveValue(postbox.transaction { _ in })
        }
        queue.sync {}
    }

    func testDefaultSettingsPreserveEveryUpstreamActivityMapping() throws {
        for (activity, expected) in self.cases {
            let requests = self.resolve(activity)
            XCTAssertEqual(requests.count, 1, expected)
            let request = try XCTUnwrap(requests.first)
            XCTAssertEqual(self.actionName(request), expected)
            guard case let .cloud(flags, peer, topMessageId, _) = request,
                  case let .inputPeerUser(data) = peer else {
                XCTFail("expected cloud user request")
                continue
            }
            XCTAssertEqual(flags, 0)
            XCTAssertNil(topMessageId)
            XCTAssertEqual(data.userId, 1001)
            XCTAssertEqual(data.accessHash, 12345)
        }
        XCTAssertEqual(self.resolve(nil).map(self.actionName), ["cancel"])
    }

    func testEnabledPolicySuppressesCoveredActivitiesAndKeepsExplicitInteractions() throws {
        try self.setSettings(LocalGhostSettings(enabled: true))
        let preserved: Set<String> = ["speaking", "emoji:🙂:7", "emoji-seen:🙂"]
        for (activity, name) in self.cases {
            XCTAssertEqual(self.resolve(activity).map(self.actionName), preserved.contains(name) ? [name] : [], name)
        }
        XCTAssertEqual(self.resolve(nil).map(self.actionName), ["cancel"])
        let interaction = EmojiInteraction(animations: [.init(index: 1, timeOffset: 0.25)])
        let id = MessageId(peerId: self.peerId, namespace: Namespaces.Message.Cloud, id: 7)
        let request = try XCTUnwrap(self.resolve(.interactingWithEmoji(emoticon: "🙂", messageId: id, interaction: interaction)).first)
        guard case let .cloud(_, _, _, action) = request, case let .sendMessageEmojiInteraction(data) = action else {
            return XCTFail("expected preserved emoji interaction")
        }
        let decoded = try XCTUnwrap(EmojiInteraction(apiDataJson: data.interaction))
        // The model's synthesized equality also compares its private decimal
        // formatter instance. Assert the wire contents rather than that identity.
        XCTAssertEqual(decoded.animations, interaction.animations)
        XCTAssertEqual(data.emoticon, "🙂")
        XCTAssertEqual(data.msgId, 7)
        guard case let .dataJSON(wire) = data.interaction, case let .dataJSON(roundTrip) = decoded.apiDataJson else {
            return XCTFail("expected JSON interaction payload")
        }
        let wireObject = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(wire.data.utf8)) as? NSDictionary)
        let roundTripObject = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(roundTrip.data.utf8)) as? NSDictionary)
        let expectedObject = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(#"{"v":1,"a":[{"i":1,"t":0.25}]}"#.utf8)) as? NSDictionary)
        XCTAssertEqual(wireObject, expectedObject)
        XCTAssertEqual(roundTripObject, wireObject)
    }

    func testEachSuppressionSwitchIsIndependentAtTheRequestBoundary() throws {
        let configurations: [(LocalGhostSettings, Set<Int32>)] = [
            (.init(enabled: true, suppressTyping: true, suppressStickerSelection: false, suppressRecording: false, suppressUploads: false, suppressGamePlay: false), [0]),
            (.init(enabled: true, suppressTyping: false, suppressStickerSelection: true, suppressRecording: false, suppressUploads: false, suppressGamePlay: false), [9]),
            (.init(enabled: true, suppressTyping: false, suppressStickerSelection: false, suppressRecording: true, suppressUploads: false, suppressGamePlay: false), [3, 6]),
            (.init(enabled: true, suppressTyping: false, suppressStickerSelection: false, suppressRecording: false, suppressUploads: true, suppressGamePlay: false), [2, 4, 5, 7]),
            (.init(enabled: true, suppressTyping: false, suppressStickerSelection: false, suppressRecording: false, suppressUploads: false, suppressGamePlay: true), [8])
        ]
        for (settings, suppressed) in configurations {
            try self.setSettings(settings)
            for (activity, name) in self.cases {
                XCTAssertEqual(self.resolve(activity).map(self.actionName), suppressed.contains(activity.key) ? [] : [name], name)
            }
        }
    }

    func testStoredPolicyLoadsBeforeFirstWorkerActivity() throws {
        try self.setSettings(LocalGhostSettings(enabled: true))
        try self.fixture.reopen()
        let activities = Promise<GhostTestActivities>(self.activities([.typingText]))
        let recorder = GhostActivityRecorder(pending: true)
        let queue = self.startWorker(activities, recorder: recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
        XCTAssertEqual(recorder.activeCount, 0)

        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(self.actionName), ["typing"])
    }

    func testToggleCancelsCurrentRequestAndDoesNotReplayExpiredActivities() throws {
        let activities = Promise<GhostTestActivities>(self.activities([.typingText]))
        let recorder = GhostActivityRecorder(pending: true)
        let queue = self.startWorker(activities, recorder: recorder)
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(self.actionName), ["typing"])
        XCTAssertEqual(recorder.activeCount, 1)

        try self.setSettings(LocalGhostSettings(enabled: true))
        try self.settle(queue)
        XCTAssertEqual(recorder.activeCount, 0)
        XCTAssertEqual(recorder.cancellationCount, 1)
        activities.set(.single(self.activities([.uploadingPhoto(progress: 50)], updateId: 2)))
        activities.set(.single([:]))
        try self.settle(queue)
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(self.actionName), ["typing"], "suppressed activities that ended are not replayed")

        activities.set(.single(self.activities([.typingText], updateId: 3)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 2)
        try self.setSettings(LocalGhostSettings(enabled: true))
        try self.settle(queue)
        XCTAssertEqual(recorder.activeCount, 0)
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(self.actionName), ["typing", "typing", "typing"], "a still-active indicator may restart when explicitly permitted")
        self.stopWorkers()
        XCTAssertEqual(recorder.activeCount, 0)
        XCTAssertEqual(recorder.cancellationCount, 3)
        activities.set(.single(self.activities([.uploadingPhoto(progress: 99)], updateId: 4)))
        try self.setSettings(LocalGhostSettings(enabled: true))
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.count, 3, "a disposed worker must ignore later activity and policy changes")
    }

    func testSuppressingSelectedActivityDoesNotPromoteLowerPriorityRecord() throws {
        try self.setSettings(LocalGhostSettings(enabled: true))
        let activities = Promise<GhostTestActivities>(self.activities([.typingText, .speakingInGroupCall(timestamp: 5)]))
        let recorder = GhostActivityRecorder(pending: true)
        let queue = self.startWorker(activities, recorder: recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
        activities.set(.single(self.activities([.speakingInGroupCall(timestamp: 5)], updateId: 2)))
        try self.settle(queue)
        XCTAssertEqual(recorder.requests.map(self.actionName), ["speaking"])
    }

    func testWorkerPreservesThreadFlagsAndVoiceChatCategory() throws {
        let activities = Promise<GhostTestActivities>(self.activities([.uploadingFile(progress: 17)], category: .thread(Int64.max)))
        let recorder = GhostActivityRecorder(pending: true)
        let queue = self.startWorker(activities, recorder: recorder)
        try self.settle(queue)
        let first = try XCTUnwrap(recorder.requests.first)
        guard case let .cloud(flags, _, topMessageId, _) = first else { return XCTFail("expected cloud request") }
        XCTAssertEqual(flags, 1 << 0)
        XCTAssertEqual(topMessageId, Int32.max)
        XCTAssertEqual(self.actionName(first), "file:17")
        activities.set(.single(self.activities([.speakingInGroupCall(timestamp: 5)], updateId: 2, category: .voiceChat)))
        try self.settle(queue)
        let last = try XCTUnwrap(recorder.requests.last)
        guard case let .cloud(lastFlags, _, lastTopMessageId, _) = last else { return XCTFail("expected cloud request") }
        XCTAssertEqual(lastFlags, 0)
        XCTAssertNil(lastTopMessageId)
        XCTAssertEqual(self.actionName(last), "speaking")
        XCTAssertEqual(recorder.activeCount, 1)
    }

    func testResolverReadsPolicyWhenSubscribedNotWhenSignalWasCreated() throws {
        let recorder = GhostActivityRecorder()
        let signal = requestLocalInputActivity(postbox: self.fixture.postbox, accountPeerId: self.accountId,
            peerId: self.peerId, threadId: nil, activity: .typingText, sendRequest: recorder.send)
        try self.setSettings(LocalGhostSettings(enabled: true))
        self.complete(signal)
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testExistingPresenceAndSelfGuardsRemainEffective() throws {
        try self.setSettings(LocalGhostSettings(enabled: true, suppressTyping: false))
        XCTAssertTrue(self.resolve(.typingText, peerId: self.accountId).isEmpty)
        let missing = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9000))
        XCTAssertTrue(self.resolve(.typingText, peerId: missing).isEmpty)
        let missingPresence = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9001))
        let missingAccessHash = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(9002))
        let peers = [self.user(missingPresence), self.user(missingAccessHash, accessHash: nil)]
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal(peers, update: { _, current in current })
            transaction.updatePeerPresencesInternal(presences: [missingAccessHash: TelegramUserPresence(status: .recently(isHidden: false), lastActivity: 0)], merge: { _, current in current })
        })
        XCTAssertTrue(self.resolve(.typingText, peerId: missingPresence).isEmpty)
        XCTAssertTrue(self.resolve(.typingText, peerId: missingAccessHash).isEmpty)
        let now = Int32(Date().timeIntervalSince1970)
        let statuses: [(UserPresenceStatus, Bool)] = [
            (.none, false), (.lastWeek(isHidden: false), false), (.lastMonth(isHidden: true), false),
            (.present(until: now - 120), false), (.present(until: now + 120), true), (.recently(isHidden: true), true)
        ]
        let peerId = self.peerId
        for (status, allowed) in statuses {
            let presence = TelegramUserPresence(status: status, lastActivity: 0)
            let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
                transaction.updatePeerPresencesInternal(presences: [peerId: presence], merge: { _, current in current })
            })
            XCTAssertEqual(self.resolve(.typingText).count, allowed ? 1 : 0)
        }
    }

    func testBroadcastStillRejectsTypingButPermitsGroupCallSpeaking() throws {
        let id = PeerId(namespace: Namespaces.Peer.CloudChannel, id: ._internalFromInt64Value(77))
        let channel = TelegramChannel(id: id, accessHash: .personal(456), title: "Test broadcast", username: nil,
            photo: [], creationDate: 0, version: 0, participationStatus: .member,
            info: .broadcast(TelegramChannelBroadcastInfo(flags: [])), flags: [], restrictionInfo: nil,
            adminRights: nil, bannedRights: nil, defaultBannedRights: nil, usernames: [], storiesHidden: nil,
            nameColor: nil, backgroundEmojiId: nil, profileColor: nil, profileBackgroundEmojiId: nil,
            emojiStatus: nil, approximateBoostLevel: nil, subscriptionUntilDate: nil, verificationIconFileId: nil,
            sendPaidMessageStars: nil, linkedMonoforumId: nil)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([channel], update: { _, current in current })
        })
        XCTAssertTrue(self.resolve(.typingText, peerId: id).isEmpty)
        try self.setSettings(LocalGhostSettings(enabled: true))
        XCTAssertEqual(self.resolve(.speakingInGroupCall(timestamp: 5), peerId: id).map(self.actionName), ["speaking"])
        XCTAssertEqual(self.resolve(nil, peerId: id).map(self.actionName), ["cancel"])
    }

    func testSecretTypingKeepsEncryptedRequestAndOtherSecretActivitiesStayAbsent() throws {
        let id = PeerId(namespace: Namespaces.Peer.SecretChat, id: ._internalFromInt64Value(77))
        let secret = TelegramSecretChat(id: id, creationDate: 0, regularPeerId: self.peerId,
            accessHash: 456, role: .creator, embeddedState: .active, messageAutoremoveTimeout: nil)
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.updatePeersInternal([secret], update: { _, current in current })
        })
        let request = try XCTUnwrap(self.resolve(.typingText, peerId: id, threadId: 999).first)
        guard case let .secret(peer, typing) = request, case let .inputEncryptedChat(data) = peer else {
            return XCTFail("expected encrypted typing")
        }
        XCTAssertEqual(data.chatId, 77)
        XCTAssertEqual(data.accessHash, 456)
        guard case .boolTrue = typing else { return XCTFail("expected true typing flag") }
        XCTAssertTrue(self.resolve(.recordingVoice, peerId: id).isEmpty)
        XCTAssertTrue(self.resolve(nil, peerId: id).isEmpty)
        try self.setSettings(LocalGhostSettings(enabled: true))
        XCTAssertTrue(self.resolve(.typingText, peerId: id).isEmpty)
    }

    func testSettingsUsePostboxCodecAndSurviveReopen() throws {
        let settings = LocalGhostSettings(enabled: true, suppressTyping: false, suppressStickerSelection: true,
            suppressRecording: false, suppressUploads: true, suppressGamePlay: false)
        let entry = try XCTUnwrap(PreferencesEntry(settings))
        XCTAssertEqual(localGhostSettings(entry: entry), .available(settings))
        try self.setSettings(settings)
        try self.fixture.reopen()
        XCTAssertEqual(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).settings), .available(settings))
        XCTAssertEqual(self.resolve(.typingText).map(self.actionName), ["typing"])
        XCTAssertTrue(self.resolve(.uploadingFile(progress: 17)).isEmpty)
    }

    private func assertUnsupportedPolicy(_ entry: PreferencesEntry) throws {
        let _ = try self.localArchiveValue(self.fixture.postbox.transaction { transaction in
            transaction.setPreferencesEntry(key: localGhostSettingsKey, value: entry)
        })
        try self.fixture.reopen()
        let context = LocalGhostContext(postbox: self.fixture.postbox)
        XCTAssertEqual(try self.localArchiveValue(context.settings), .unsupported)
        XCTAssertFalse(try self.localArchiveValue(context.setEnabled(false)))
        XCTAssertFalse(try self.localArchiveValue(context.updateSettings { $0.suppressTyping = false }))
        let stored = try self.localArchiveValue(self.fixture.postbox.transaction { $0.getPreferencesEntry(key: localGhostSettingsKey)?.data })
        XCTAssertEqual(stored, entry.data)
        let preserved: Set<String> = ["speaking", "emoji:🙂:7", "emoji-seen:🙂"]
        for (activity, name) in self.cases {
            XCTAssertEqual(self.resolve(activity).map(self.actionName), preserved.contains(name) ? [name] : [], name)
        }
        XCTAssertEqual(self.resolve(nil).map(self.actionName), ["cancel"])
        let activities = Promise<GhostTestActivities>(self.activities([.typingText]))
        let recorder = GhostActivityRecorder(pending: true)
        let queue = self.startWorker(activities, recorder: recorder)
        try self.settle(queue)
        XCTAssertTrue(recorder.requests.isEmpty)
    }

    func testFutureSettingsRemainReadOnlyAndFailClosedForCoveredActivities() throws {
        let encoded = try JSONEncoder().encode(LocalGhostSettings())
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        object["schemaVersion"] = 99
        let future = try JSONDecoder().decode(LocalGhostSettings.self, from: JSONSerialization.data(withJSONObject: object))
        try self.assertUnsupportedPolicy(try XCTUnwrap(PreferencesEntry(future)))
    }

    func testMalformedSettingsRemainReadOnlyAndFailClosedForCoveredActivities() throws {
        struct IncompleteSettings: Encodable { let schemaVersion: Int32 = 1 }
        try self.assertUnsupportedPolicy(try XCTUnwrap(PreferencesEntry(IncompleteSettings())))
    }

    func testTwoAccountWorkersUseIndependentPersistedPolicies() throws {
        let other = try LocalArchivePostboxFixture(test: self, name: "ghost-other-account")
        defer { self.stopWorkers(); try? other.destroy() }
        let otherAccount = PeerId(namespace: Namespaces.Peer.CloudUser, id: ._internalFromInt64Value(2))
        try self.seed(other.postbox, accountId: otherAccount)
        try self.setSettings(LocalGhostSettings(enabled: true))
        let activities = Promise<GhostTestActivities>(self.activities([.typingText]))
        let firstRecorder = GhostActivityRecorder(pending: true)
        let secondRecorder = GhostActivityRecorder(pending: true)
        let firstQueue = self.startWorker(activities, recorder: firstRecorder)
        let secondQueue = self.startWorker(activities, recorder: secondRecorder, postbox: other.postbox, accountId: otherAccount)
        try self.settle(firstQueue)
        try self.settle(secondQueue, postbox: other.postbox)
        XCTAssertTrue(firstRecorder.requests.isEmpty)
        XCTAssertEqual(secondRecorder.requests.map(self.actionName), ["typing"])
        try self.setSettings(LocalGhostSettings(enabled: true), postbox: other.postbox)
        try self.settle(secondQueue, postbox: other.postbox)
        XCTAssertEqual(secondRecorder.activeCount, 0)
        XCTAssertTrue(try self.localArchiveValue(LocalGhostContext(postbox: self.fixture.postbox).setEnabled(false)))
        try self.settle(firstQueue)
        XCTAssertEqual(firstRecorder.requests.map(self.actionName), ["typing"])
        XCTAssertEqual(secondRecorder.requests.count, 1)
        XCTAssertEqual(secondRecorder.activeCount, 0)
    }
}
