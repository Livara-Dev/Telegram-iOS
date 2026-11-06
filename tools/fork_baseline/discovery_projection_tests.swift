// SPDX-License-Identifier: GPL-2.0-or-later
// Created for the AyuGram iOS fork on 2026-11-06.
// Experimental framework-discovered retained suite. No native compilation or
// test-manager registration success is claimed by this source alone.
import Foundation
import CoreFoundation
import Darwin
import UIKit
import XCTest

private enum DiscoveryProjection {
    static let appID = "org.livara.ayugram.tests.native-security.projection"
    static let payloadID = "org.livara.ayugram.tests.native-security.projectionTests"
    static let payloadName = "ChatLocalTextFilterProjectionTests.xctest"
    static let payloadExecutable = "ChatLocalTextFilterProjectionTests"
    static let profile = "experimentalDiscoveryPayloadSuite"
    static let runtimeModule = "submodules_TelegramUI_Components_Chat_ChatHistoryEntry_ChatLocalTextFilterProjectionTestsLib"
    static let runtimeClassIdentities = Dictionary(uniqueKeysWithValues: [
        "ChatLocalTextFilterProjectionTests", "ChatLocalArchiveProjectionTests", "ChatLocalArchiveRowRendererTests"
    ].map { (runtimeModule + "." + $0, $0) })
    static let expected: Set<String> = [
        "ChatLocalArchiveProjectionTests.testArchiveIdentityCannotCollideWithCanonicalOrArbitraryAlbumBits",
        "ChatLocalArchiveProjectionTests.testCanonicalAndAlbumIdentitiesWinOverArchiveDuplicates",
        "ChatLocalArchiveProjectionTests.testLocalRowsSortBetweenUnmodifiedCanonicalObjects",
        "ChatLocalArchiveProjectionTests.testOnlyCanonicalRowsCanSupplyNetworkPaginationAndReadAnchors",
        "ChatLocalArchiveProjectionTests.testOtherAccountChatPeerCannotAppear",
        "ChatLocalArchiveProjectionTests.testOutputBoundDoesNotExpandWithLargeArchivePage",
        "ChatLocalArchiveProjectionTests.testProtectedSelectionPreviewOrFilterGateDoesNotInsertAnyLocalRows",
        "ChatLocalArchiveProjectionTests.testRepeatedDeletedIdHasOneStableIdentityAndNewVersionUpdatesEquality",
        "ChatLocalArchiveProjectionTests.testReverseHistoryMaintainsComparatorOrder",
        "ChatLocalArchiveProjectionTests.testRevokedBodyNeverReentersAReplayedTransition",
        "ChatLocalArchiveProjectionTests.testTruncationNoticeSurvivesDeduplicationOfItsOriginalRow",
        "ChatLocalArchiveProjectionTests.testUnsafeIdentifiersAndTimestampsFailClosed",
        "ChatLocalArchiveRowRendererTests.testActualRendererExposesLocalMarkerTimestampAndMediaAvailabilityWithoutServerActions",
        "ChatLocalArchiveRowRendererTests.testLongAndMediaOnlyRowsHaveBoundedNonoverlappingLayoutOnPhoneWidths",
        "ChatLocalArchiveRowRendererTests.testRendererUsesRealLightAndDarkThemeColorsAndAlwaysClearsBoth",
        "ChatLocalArchiveRowRendererTests.testReusedRendererCannotRestoreRevokedPreviousBody",
        "ChatLocalTextFilterProjectionTests.testAlbumAndNonMessageEntriesPassThroughInTheirOriginalOrder",
        "ChatLocalTextFilterProjectionTests.testAllMatchedRowsKeepCountOrderIdentityAndIndexes",
        "ChatLocalTextFilterProjectionTests.testChangingAuthorOrMediaClassificationInvalidatesAQueuedDecision",
        "ChatLocalTextFilterProjectionTests.testInverseMediaProjectionUsesKnownTypesAndKeepsTextAndAllowedMediaVisible",
        "ChatLocalTextFilterProjectionTests.testInverseRulesAndSenderMatchesCannotHideIneligibleMetadata",
        "ChatLocalTextFilterProjectionTests.testLargeAndAnimatedEmojiKeepTheirOriginalNodeClassification",
        "ChatLocalTextFilterProjectionTests.testMediaExpiryProtectionGroupingAndUnknownMetadataStayVisible",
        "ChatLocalTextFilterProjectionTests.testOnlyMatchedMessageChangesPresentation",
        "ChatLocalTextFilterProjectionTests.testOrdinaryIncomingCloudTextAndAllowedMetadataRemainEligible",
        "ChatLocalTextFilterProjectionTests.testProductionSnapshotBridgeAppliesLocalSenderIdentityAndAllowsReveal",
        "ChatLocalTextFilterProjectionTests.testProjectionRunsInIsolatedIOSHostWithPresentationResources",
        "ChatLocalTextFilterProjectionTests.testRemovingMatchesClearsBothHiddenAndRevealedState",
        "ChatLocalTextFilterProjectionTests.testRevealAndRehidePreserveCanonicalMessageAndPriorRows",
        "ChatLocalTextFilterProjectionTests.testSelectedMessageStaysVisibleEvenWithInconsistentSelectionModeFlag",
        "ChatLocalTextFilterProjectionTests.testSelectionModeRevealsAllMatchesAndClearingSelectionAllowsRehide",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryDocumentCanHideAndRevealWithoutChangingItsMedia",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryGIFCanHideAndRevealWithoutChangingItsMedia",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryPhotoCanHideAndRevealWithoutChangingItsMedia",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryRoundVideoCanHideAndRevealWithoutConsumingIt",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryVideoCanHideAndRevealWithoutChangingItsMedia",
        "ChatLocalTextFilterProjectionTests.testSingleOrdinaryVoiceCanHideAndRevealWithoutConsumingIt",
        "ChatLocalTextFilterProjectionTests.testSnapshotIdentityRejectsOtherPeerMessageAndNamespace",
        "ChatLocalTextFilterProjectionTests.testStaleMessageVersionOrTextNeverInheritsPreviousMatch",
        "ChatLocalTextFilterProjectionTests.testStickerAndCustomEmojiNodesCannotBePromotedIntoFilterBubbles",
        "ChatLocalTextFilterProjectionTests.testUnsupportedContentIsNotHiddenEvenWhenSnapshotMatches",
    ]
    static let wrapperClass = "AyuProjectionControlTests.AyuDiscoveryProjectionLoaderTests"
    static let wrapperMethod = "testVerifiesRetainedProjectionSuiteWasRunByFramework"
    static func wrapperIdentity(_ testCase: XCTestCase) -> Bool {
        let expected = Bundle.main.bundleURL.appendingPathComponent("PlugIns/AyuProjectionControlTests.xctest")
        return NSStringFromClass(type(of: testCase)) == wrapperClass &&
            Bundle(for: type(of: testCase)).bundleURL.resolvingSymlinksInPath().path == expected.path &&
            ["-[" + wrapperClass + " " + wrapperMethod + "]",
             "-[AyuDiscoveryProjectionLoaderTests " + wrapperMethod + "]"].contains(testCase.name)
    }

    static let languages = ["ar", "be", "ca", "de", "en", "es", "fa", "fr", "id", "it", "ko", "ms", "nl", "pl", "pt", "ru", "tr", "uk", "uz"]

    // Accept only bounded Darwin Objective-C XCTest names. A module prefix
    // may be the captured product or its Bazel Swift library, never a wrapper.
    static func identity(_ name: String) -> String? {
        guard name.utf8.count <= 512, name.hasPrefix("-["), name.hasSuffix("]") else { return nil }
        let body = name.dropFirst(2).dropLast()
        let fields = body.split(separator: " ", omittingEmptySubsequences: false)
        guard fields.count == 2 else { return nil }
        let type = fields[0].split(separator: ".", omittingEmptySubsequences: false)
        guard type.count == 1 || type.count == 2 else { return nil }
        if type.count == 2 && !["ChatLocalTextFilterProjectionTests", "ChatLocalTextFilterProjectionTestsLib",
            "submodules_TelegramUI_Components_Chat_ChatHistoryEntry_ChatLocalTextFilterProjectionTestsLib"].contains(String(type[0])) { return nil }
        let key = String(type[type.count - 1]) + "." + String(fields[1])
        return expected.contains(key) ? key : nil
    }

    // Display-name aliases grant no origin permission. The live class must
    // come from the exact captured module and the validated payload image.
    static func identity(_ testCase: XCTestCase, payloadURL: URL,
                         classIdentities: [String: String]) -> String? {
        guard payloadURL.isFileURL, classIdentities == runtimeClassIdentities,
              let className = classIdentities[NSStringFromClass(type(of: testCase))],
              Bundle(for: type(of: testCase)).bundleURL.resolvingSymlinksInPath().path == payloadURL.path,
              let name = identity(testCase.name),
              name.components(separatedBy: ".").first == className else { return nil }
        return name
    }
}

private enum DiscoveryFailure: String, Error {
    case hostIdentity, hostNotActive, payloadLayout, payloadMetadata, payloadLoad
    case suiteShape, suiteIdentity, suiteCount, observedResults, suiteResults, diagnosticWrite
    case discoveryConcurrent, discoveryThreadTimeout, discoveryState, hostSceneUnproven
}

private enum DiscoveryPhase: String {
    case discoveryEntered, hostActive, payloadValidated, payloadLoadStart, payloadLoaded
    case suiteValidated, suiteReturned, suiteFinished, completed, failed
}

private enum SandboxReport {
    static func attributes(_ url: URL, directory: Bool, maximum: Int64 = 0) -> Bool {
        var value = stat()
        guard url.isFileURL, lstat(url.path, &value) == 0, value.st_uid == getuid() else { return false }
        if directory { return value.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR) }
        return value.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG) && value.st_size > 0 && value.st_size <= maximum
    }

    static func regular(_ root: URL, _ relative: String, maximum: Int64) -> URL? {
        let pieces = relative.split(separator: "/", omittingEmptySubsequences: false)
        guard !pieces.isEmpty, pieces.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { return nil }
        var current = root
        for (index, part) in pieces.enumerated() {
            current.appendPathComponent(String(part), isDirectory: index != pieces.count - 1)
            guard attributes(current, directory: index != pieces.count - 1, maximum: maximum) else { return nil }
        }
        return current
    }

    static func directory() throws -> URL {
        // NSHomeDirectory is the application's sandbox, not inherited HOME.
        let home = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true).resolvingSymlinksInPath()
        guard attributes(home, directory: true) else { throw DiscoveryFailure.diagnosticWrite }
        var current = home
        for part in ["Library", "Caches", "ayu-deferred-projection-loader"] {
            current.appendPathComponent(part, isDirectory: true)
            var info = stat()
            if lstat(current.path, &info) != 0 {
                guard errno == ENOENT, mkdir(current.path, mode_t(0o700)) == 0 else { throw DiscoveryFailure.diagnosticWrite }
            }
            guard attributes(current, directory: true) else { throw DiscoveryFailure.diagnosticWrite }
        }
        guard chmod(current.path, mode_t(0o700)) == 0 else { throw DiscoveryFailure.diagnosticWrite }
        return current
    }

    static func atomic(_ bytes: Data, name: String, directory: URL) throws {
        guard ["stage", "summary.json"].contains(name), !bytes.isEmpty, bytes.count <= 16384 else { throw DiscoveryFailure.diagnosticWrite }
        let temporary = directory.appendingPathComponent(".pending-" + UUID().uuidString)
        let destination = directory.appendingPathComponent(name)
        let descriptor = open(temporary.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
        guard descriptor >= 0 else { throw DiscoveryFailure.diagnosticWrite }
        var closed = false
        defer {
            if !closed { close(descriptor) }
            unlink(temporary.path)
        }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG),
              info.st_uid == getuid(), info.st_nlink == 1, fchmod(descriptor, mode_t(0o600)) == 0 else { throw DiscoveryFailure.diagnosticWrite }
        try bytes.withUnsafeBytes { buffer in
            guard let base = buffer.baseAddress else { throw DiscoveryFailure.diagnosticWrite }
            var offset = 0
            while offset < buffer.count {
                let amount = Darwin.write(descriptor, base.advanced(by: offset), buffer.count - offset)
                if amount < 0 && errno == EINTR { continue }
                guard amount > 0 else { throw DiscoveryFailure.diagnosticWrite }
                offset += amount
            }
        }
        let result = close(descriptor)
        closed = true
        guard result == 0, rename(temporary.path, destination.path) == 0 else { throw DiscoveryFailure.diagnosticWrite }
    }

    static func emit(_ phase: DiscoveryPhase, summary: inout [String: Any]) throws {
        summary["phase"] = phase.rawValue
        let root = try directory()
        let bytes = try JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys])
        try atomic(bytes, name: "summary.json", directory: root)
        try atomic(Data(phase.rawValue.utf8), name: "stage", directory: root)
    }
}

@MainActor
private final class ActiveUIKitProbe {
    let expectation = XCTestExpectation(description: "Deferred projection active UIKit host")
    private var enabled = true
    private var fulfilled = false
    func check() {
        guard enabled, !fulfilled, UIApplication.shared.applicationState == .active else { return }
        let visible = UIApplication.shared.connectedScenes.contains { scene in
            guard let windowScene = scene as? UIWindowScene, scene.activationState == .foregroundActive else { return false }
            return windowScene.windows.contains { window in
                !window.isHidden && window.alpha > 0 && window.bounds.width > 0 && window.bounds.height > 0 &&
                window.rootViewController != nil && window.windowScene === windowScene
            }
        }
        if visible { fulfilled = true; expectation.fulfill() }
    }
    func stop() { enabled = false }
}

// Intentionally non-actor-isolated: XCTestObservation callbacks may arrive on
// different threads. No UIKit, file operations, raw names, or issue prose here.
private final class RetainedProjectionObserver: NSObject, XCTestObservation {
    private struct CaseState { var starts = 0; var finishes = 0; var passes = 0 }
    private let payloadURL: URL
    private let classIdentities: [String: String]
    private let lock = NSLock()
    private var records = Dictionary(uniqueKeysWithValues: DiscoveryProjection.expected.map { ($0, CaseState()) })
    private var failed = 0
    private var skipped = 0
    private var unknown = 0
    private var duplicates = 0
    private var outOfOrder = 0

    init(payloadURL: URL, classIdentities: [String: String]) {
        self.payloadURL = payloadURL
        self.classIdentities = classIdentities
        super.init()
    }

    func testCaseWillStart(_ testCase: XCTestCase) {
        if DiscoveryProjection.wrapperIdentity(testCase) { return }
        let key = DiscoveryProjection.identity(testCase, payloadURL: payloadURL, classIdentities: classIdentities)
        lock.lock(); defer { lock.unlock() }
        guard let name = key, var value = records[name] else { unknown += 1; return }
        if value.starts != 0 || value.finishes != 0 { duplicates += 1 }
        value.starts += 1; records[name] = value
    }

    func testCaseDidFinish(_ testCase: XCTestCase) {
        if DiscoveryProjection.wrapperIdentity(testCase) { return }
        let key = DiscoveryProjection.identity(testCase, payloadURL: payloadURL, classIdentities: classIdentities)
        let run = testCase.testRun
        let failureCount = run?.totalFailureCount ?? 1
        let skippedCount = max(run?.skipCount ?? 0, run?.hasBeenSkipped == true ? 1 : 0)
        let succeeded = run?.executionCount == 1 && run?.testCaseCount == 1 && run?.hasSucceeded == true &&
            run?.hasBeenSkipped == false && failureCount == 0 && skippedCount == 0 && run?.unexpectedExceptionCount == 0
        lock.lock(); defer { lock.unlock() }
        guard let name = key, var value = records[name] else { unknown += 1; return }
        if value.finishes != 0 { duplicates += 1 }
        if value.starts != 1 { outOfOrder += 1 }
        value.finishes += 1
        if succeeded { value.passes += 1 }
        failed += max(failureCount, succeeded ? 0 : 1)
        skipped += skippedCount
        records[name] = value
    }

    func testCase(_ testCase: XCTestCase, didRecord issue: XCTIssue) {
        if DiscoveryProjection.wrapperIdentity(testCase) { return }
        let known = DiscoveryProjection.identity(testCase, payloadURL: payloadURL, classIdentities: classIdentities) != nil
        lock.lock(); defer { lock.unlock() }
        if known { failed += 1 } else { unknown += 1 }
        // No issue description, attachments, file paths, or userInfo is read.
    }

    func snapshot() -> (values: [String: Any], consistent: Bool) {
        lock.lock(); defer { lock.unlock() }
        let starts = records.values.reduce(0) { $0 + $1.starts }
        let passes = records.values.reduce(0) { $0 + $1.passes }
        let consistent = failed == 0 && skipped == 0 && unknown == 0 && duplicates == 0 && outOfOrder == 0 &&
            records.count == 41 && records.values.allSatisfy { $0.starts == 1 && $0.finishes == 1 && $0.passes == 1 }
        return (["observer_started": starts, "observer_passed": passes, "observer_failed": failed,
                 "observer_skipped": skipped, "observer_unknown": unknown, "observer_duplicates": duplicates,
                 "observer_out_of_order": outOfOrder], consistent)
    }
}

private final class DiscoveredProjection {
    let composite: XCTestSuite
    let payload: XCTestSuite
    let observer: RetainedProjectionObserver
    var summary: [String: Any]
    init(composite: XCTestSuite, payload: XCTestSuite, observer: RetainedProjectionObserver, summary: [String: Any]) {
        self.composite = composite; self.payload = payload; self.observer = observer; self.summary = summary
    }
}

private enum DiscoveryFactory {
    // No lock is held while dispatching, waiting for UIKit, or loading code.
    private static let lock = NSLock()
    private static var building = false
    private static var prepared: DiscoveredProjection?
    private static var failure: DiscoveryFailure?
    private static var verificationTaken = false

    static func hostIdentity() -> Bool {
        #if targetEnvironment(simulator) && arch(arm64)
        let app = Bundle.main.bundleURL
        guard app.isFileURL, app.pathExtension == "app", app.path == app.resolvingSymlinksInPath().path,
              Bundle.main.bundleIdentifier == DiscoveryProjection.appID,
              Bundle.main.object(forInfoDictionaryKey: "CFBundleExecutable") as? String == "AyuProjectionControl",
              Bundle.main.executableURL?.path == app.appendingPathComponent("AyuProjectionControl").path,
              Bundle.main.object(forInfoDictionaryKey: "CFBundleSupportedPlatforms") as? [String] == ["iPhoneSimulator"],
              let flag = Bundle.main.object(forInfoDictionaryKey: "AyuGramProjectionHostDiagnostics") as? NSNumber,
              CFGetTypeID(flag) == CFBooleanGetTypeID(), flag.boolValue,
              SandboxReport.attributes(app, directory: true),
              SandboxReport.regular(app, "AyuProjectionControl", maximum: 32 * 1024 * 1024) != nil else { return false }
        return true
        #else
        return false
        #endif
    }

    static func initialSummary() -> [String: Any] {
        return ["schema": 1, "profile": DiscoveryProjection.profile, "phase": DiscoveryPhase.discoveryEntered.rawValue,
            "expected_count": 41, "discovered_count": 0, "observer_started": 0, "observer_passed": 0,
            "observer_failed": 0, "observer_skipped": 0, "observer_unknown": 0, "observer_duplicates": 0,
            "observer_out_of_order": 0, "run_execution_count": 0, "run_test_case_count": 0,
            "run_total_failure_count": 0, "run_unexpected_exception_count": 0, "run_skip_count": 0,
            "run_failure_count": 0, "run_has_succeeded": false, "run_has_been_skipped": false,
            "failure_reason": "none", "load_error_category": "none", "load_error_code": 0,
            "checks_consistent": false, "nestedtestmanager_unverified": true, "release_qualified": false,
            "framework_managed_execution": true, "manual_suite_run_performed": false]
    }

    static func hostSceneVisible() -> Bool {
        // The unchanged host writes this only after creating its UIWindow.
        // This proof uses Foundation/POSIX only; it never creates UIApplication.
        guard hostIdentity() else { return false }
        let home = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true).resolvingSymlinksInPath()
        let flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_NONBLOCK
        var descriptors: [Int32] = []
        defer { for descriptor in descriptors.reversed() { close(descriptor) } }
        var parent = open(home.path, flags)
        guard parent >= 0 else { return false }
        descriptors.append(parent)
        func ownedDirectory(_ descriptor: Int32) -> Bool {
            var info = stat()
            return fstat(descriptor, &info) == 0 && info.st_uid == getuid() &&
                info.st_mode & mode_t(S_IFMT) == mode_t(S_IFDIR)
        }
        guard ownedDirectory(parent) else { return false }
        for component in ["Library", "Caches", "ayu-modern-control"] {
            let next = component.withCString { openat(parent, $0, flags) }
            guard next >= 0 else { return false }
            descriptors.append(next); parent = next
            guard ownedDirectory(parent) else { return false }
        }
        let descriptor = openat(parent, "stage", O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { return false }
        descriptors.append(descriptor)
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_uid == getuid(), info.st_nlink == 1,
              info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), info.st_size > 0, info.st_size <= 128 else { return false }
        var bytes = [UInt8](repeating: 0, count: 129)
        let count = bytes.withUnsafeMutableBytes { Darwin.read(descriptor, $0.baseAddress, $0.count) }
        guard count == info.st_size, count <= 128 else { return false }
        return Data(bytes.prefix(count)) == Data("sceneVisible".utf8)
    }

    @MainActor
    private static func build(_ verification: XCTestSuite) throws -> DiscoveredProjection {
        // Identity proof precedes EVERY marker, UIKit probe and payload access.
        guard Thread.isMainThread, hostIdentity() else { throw DiscoveryFailure.hostIdentity }
        guard hostSceneVisible() else { throw DiscoveryFailure.hostSceneUnproven }
        var summary = initialSummary()
        do {
            try SandboxReport.emit(.discoveryEntered, summary: &summary)
            let probe = ActiveUIKitProbe(); probe.check()
            let timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
                DispatchQueue.main.async { probe.check() }
            }
            let readiness = XCTWaiter.wait(for: [probe.expectation], timeout: 5)
            timer.invalidate(); probe.stop()
            guard readiness == .completed else { throw DiscoveryFailure.hostNotActive }
            try SandboxReport.emit(.hostActive, summary: &summary)
            let app = Bundle.main.bundleURL.resolvingSymlinksInPath()
            let privatePayload = app.appendingPathComponent("AyuPrivatePayload", isDirectory: true)
            let payload = privatePayload.appendingPathComponent(DiscoveryProjection.payloadName, isDirectory: true)
            guard SandboxReport.attributes(app, directory: true), SandboxReport.attributes(privatePayload, directory: true),
                  SandboxReport.attributes(payload, directory: true),
                  let infoURL = SandboxReport.regular(payload, "Info.plist", maximum: 65536),
                  SandboxReport.regular(payload, DiscoveryProjection.payloadExecutable, maximum: 512 * 1024 * 1024) != nil,
                  SandboxReport.regular(payload, "_CodeSignature/CodeResources", maximum: 65536) != nil,
                  SandboxReport.regular(payload, "LibprismaBundle.bundle/Info.plist", maximum: 65536) != nil,
                  SandboxReport.regular(payload, "LibprismaBundle.bundle/grammars.dat", maximum: 32 * 1024 * 1024) != nil,
                  SandboxReport.regular(app, "PresentationStrings.data", maximum: 2 * 1024 * 1024) != nil else { throw DiscoveryFailure.payloadLayout }
            for language in DiscoveryProjection.languages {
                // The captured non-English dictionaries are valid empty files.
                let path = app.appendingPathComponent(language + ".lproj/Localizable.strings")
                var info = stat()
                guard SandboxReport.attributes(path.deletingLastPathComponent(), directory: true), lstat(path.path, &info) == 0,
                      info.st_mode & mode_t(S_IFMT) == mode_t(S_IFREG), info.st_uid == getuid(),
                      info.st_size >= 0, info.st_size <= 2 * 1024 * 1024 else { throw DiscoveryFailure.payloadLayout }
                if language == "en" && info.st_size == 0 { throw DiscoveryFailure.payloadLayout }
            }
            let infoBytes = try Data(contentsOf: infoURL)
            guard let info = try PropertyListSerialization.propertyList(from: infoBytes, options: [], format: nil) as? [String: Any],
                  info["CFBundleIdentifier"] as? String == DiscoveryProjection.payloadID,
                  info["CFBundleExecutable"] as? String == DiscoveryProjection.payloadExecutable,
                  info["CFBundleSupportedPlatforms"] as? [String] == ["iPhoneSimulator"],
                  info["DTPlatformName"] as? String == "iphonesimulator" else { throw DiscoveryFailure.payloadMetadata }
            try SandboxReport.emit(.payloadValidated, summary: &summary)
            guard let bundle = Bundle(url: payload), !bundle.isLoaded,
                  bundle.bundleURL.resolvingSymlinksInPath().path == payload.path,
                  bundle.executableURL?.resolvingSymlinksInPath().path == payload.appendingPathComponent(DiscoveryProjection.payloadExecutable).path,
                  bundle.bundleIdentifier == DiscoveryProjection.payloadID else { throw DiscoveryFailure.payloadLayout }
            try SandboxReport.emit(.payloadLoadStart, summary: &summary)
            do { try bundle.loadAndReturnError() }
            catch {
                let error = error as NSError
                summary["load_error_category"] = [NSCocoaErrorDomain: "cocoa", NSPOSIXErrorDomain: "posix", NSOSStatusErrorDomain: "osstatus"][error.domain] ?? "other"
                summary["load_error_code"] = (-2147483648...2147483647).contains(error.code) ? error.code : 0
                throw DiscoveryFailure.payloadLoad
            }
            try SandboxReport.emit(.payloadLoaded, summary: &summary)
            let suite = XCTestSuite(forBundlePath: payload.path)
            var discovered = Set<String>()
            var nodes = 0
            func enumerate(_ test: XCTest, depth: Int) throws {
                nodes += 1
                guard depth <= 16, nodes <= 256 else { throw DiscoveryFailure.suiteShape }
                if let nested = test as? XCTestSuite {
                    for child in nested.tests { try enumerate(child, depth: depth + 1) }
                } else if let testCase = test as? XCTestCase {
                    guard let name = DiscoveryProjection.identity(testCase, payloadURL: payload,
                        classIdentities: DiscoveryProjection.runtimeClassIdentities) else { throw DiscoveryFailure.suiteIdentity }
                    guard discovered.insert(name).inserted else { throw DiscoveryFailure.suiteIdentity }
                } else { throw DiscoveryFailure.suiteShape }
            }
            try enumerate(suite, depth: 0)
            summary["discovered_count"] = discovered.count
            guard discovered == DiscoveryProjection.expected, suite.testCaseCount == 41 else { throw DiscoveryFailure.suiteCount }
            try SandboxReport.emit(.suiteValidated, summary: &summary)
            var verificationLeaves = 0
            var verificationNodes = 0
            func validateVerification(_ test: XCTest, depth: Int) throws {
                verificationNodes += 1
                guard depth <= 16, verificationNodes <= 32 else { throw DiscoveryFailure.suiteShape }
                if let nested = test as? XCTestSuite {
                    for child in nested.tests { try validateVerification(child, depth: depth + 1) }
                } else if let testCase = test as? XCTestCase, DiscoveryProjection.wrapperIdentity(testCase) {
                    verificationLeaves += 1
                } else { throw DiscoveryFailure.suiteIdentity }
            }
            try validateVerification(verification, depth: 0)
            guard verificationLeaves == 1, verification.testCaseCount == 1 else { throw DiscoveryFailure.suiteCount }
            let composite = XCTestSuite(name: "AyuFrameworkDiscoveredProjection")
            composite.addTest(suite)
            composite.addTest(verification)
            guard composite.tests.count == 2, composite.tests[0] === suite,
                  composite.tests[1] === verification, composite.testCaseCount == 42 else { throw DiscoveryFailure.suiteCount }
            let observer = RetainedProjectionObserver(payloadURL: payload,
                classIdentities: DiscoveryProjection.runtimeClassIdentities)
            try SandboxReport.emit(.suiteReturned, summary: &summary)
            return DiscoveredProjection(composite: composite, payload: suite, observer: observer, summary: summary)
        } catch {
            summary["checks_consistent"] = false
            summary["failure_reason"] = (error as? DiscoveryFailure)?.rawValue ?? DiscoveryFailure.payloadMetadata.rawValue
            try? SandboxReport.emit(.failed, summary: &summary)
            throw error
        }
    }

    static func request(_ verification: XCTestSuite) -> XCTestSuite {
        // This can be invoked more than once by discovery. Reuse exactly one
        // immutable prepared hierarchy; no second load or manual execution.
        guard hostIdentity() else {
            lock.lock(); failure = .hostIdentity; lock.unlock(); return verification
        }
        lock.lock()
        if let value = prepared { lock.unlock(); return value.composite }
        if failure != nil { lock.unlock(); return verification }
        if building { failure = .discoveryConcurrent; building = false; lock.unlock(); return verification }
        building = true; lock.unlock()
        let deadline = ProcessInfo.processInfo.systemUptime + 20
        // Discovery on the application thread must not block UIApplicationMain
        // while waiting for a marker which that same thread needs to write.
        if Thread.isMainThread && !hostSceneVisible() {
            lock.lock(); failure = .hostSceneUnproven; building = false; lock.unlock()
            return verification
        }
        if !Thread.isMainThread {
            let markerDeadline = min(deadline, ProcessInfo.processInfo.systemUptime + 5)
            while !hostSceneVisible() {
                if ProcessInfo.processInfo.systemUptime >= markerDeadline {
                    lock.lock(); failure = .hostSceneUnproven; building = false; lock.unlock()
                    return verification
                }
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
        let done = DispatchSemaphore(value: 0)
        let work: @MainActor () -> Void = {
            lock.lock(); let allowed = building && failure == nil; lock.unlock()
            guard allowed else { done.signal(); return }
            let result: Result<DiscoveredProjection, Error>
            do { result = .success(try build(verification)) }
            catch { result = .failure(error) }
            lock.lock()
            if building && failure == nil {
                switch result {
                case .success(let value):
                    XCTestObservationCenter.shared.addTestObserver(value.observer)
                    prepared = value
                case .failure(let error):
                    failure = (error as? DiscoveryFailure) ?? .payloadMetadata
                }
                building = false
            }
            lock.unlock(); done.signal()
        }
        if Thread.isMainThread {
            MainActor.assumeIsolated { work() }
        } else {
            DispatchQueue.main.async { work() }
            let remaining = max(0, deadline - ProcessInfo.processInfo.systemUptime)
            if done.wait(timeout: .now() + remaining) != .success {
                lock.lock(); failure = .discoveryThreadTimeout; building = false; lock.unlock()
            }
        }
        lock.lock(); defer { lock.unlock() }
        return failure == nil ? prepared?.composite ?? verification : verification
    }

    static func takeVerification() throws -> DiscoveredProjection {
        lock.lock(); defer { lock.unlock() }
        if let reason = failure { throw reason }
        guard let value = prepared, !building, !verificationTaken else { throw DiscoveryFailure.discoveryState }
        verificationTaken = true
        return value
    }
}

final class AyuDiscoveryProjectionLoaderTests: XCTestCase {
    override class var defaultTestSuite: XCTestSuite {
        return DiscoveryFactory.request(super.defaultTestSuite)
    }

    @MainActor
    func testVerifiesRetainedProjectionSuiteWasRunByFramework() {
        var summary = DiscoveryFactory.initialSummary()
        do {
            guard DiscoveryFactory.hostIdentity() else { throw DiscoveryFailure.hostIdentity }
            let prepared = try DiscoveryFactory.takeVerification()
            summary = prepared.summary
            // The framework has already finished the returned payload suite.
            // This method performs no tests on its own and never runs a suite.
            XCTestObservationCenter.shared.removeTestObserver(prepared.observer)
            let observed = prepared.observer.snapshot()
            for (key, value) in observed.values { summary[key] = value }
            guard let run = prepared.payload.testRun else { throw DiscoveryFailure.suiteResults }
            summary["run_execution_count"] = run.executionCount
            summary["run_test_case_count"] = run.testCaseCount
            summary["run_total_failure_count"] = run.totalFailureCount
            summary["run_unexpected_exception_count"] = run.unexpectedExceptionCount
            summary["run_skip_count"] = run.skipCount
            summary["run_failure_count"] = run.failureCount
            summary["run_has_succeeded"] = run.hasSucceeded
            summary["run_has_been_skipped"] = run.hasBeenSkipped
            try SandboxReport.emit(.suiteFinished, summary: &summary)
            guard observed.consistent else { throw DiscoveryFailure.observedResults }
            guard run.executionCount == 41, run.testCaseCount == 41, run.totalFailureCount == 0,
                  run.failureCount == 0, run.unexpectedExceptionCount == 0, run.skipCount == 0,
                  run.hasSucceeded, !run.hasBeenSkipped else { throw DiscoveryFailure.suiteResults }
            summary["checks_consistent"] = true
            try SandboxReport.emit(.completed, summary: &summary)
        } catch {
            summary["checks_consistent"] = false
            summary["failure_reason"] = (error as? DiscoveryFailure)?.rawValue ?? DiscoveryFailure.payloadMetadata.rawValue
            // An unverified discovery process gets no sandbox writes.
            if DiscoveryFactory.hostIdentity() { try? SandboxReport.emit(.failed, summary: &summary) }
            XCTFail("Framework-discovered retained projection checks failed")
        }
    }
}
