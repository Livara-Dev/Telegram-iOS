// SPDX-License-Identifier: GPL-2.0-or-later
import XCTest
import TelegramUIPreferences
@testable import SettingsUI

final class UltraBatterySaverTests: XCTestCase {
    func testEnableDisablesExpensiveEffectsAndBackgroundWorkAtTwentyPercent() {
        var original = MediaAutoDownloadSettings.defaultSettings
        original.energyUsageSettings.activationThreshold = 43
        original.energyUsageSettings.autoplayVideo = true
        original.energyUsageSettings.autoplayGif = true
        original.energyUsageSettings.loopStickers = true
        original.energyUsageSettings.loopEmoji = true
        original.energyUsageSettings.fullTranslucency = true
        original.energyUsageSettings.extendBackgroundWork = true
        original.energyUsageSettings.autodownloadInBackground = true
        original.downloadInBackground = true
        original.highQualityStories = true

        let result = ultraBatterySaverSettings(from: original)
        XCTAssertEqual(result.energyUsageSettings.activationThreshold, 20)
        XCTAssertFalse(result.energyUsageSettings.autoplayVideo)
        XCTAssertFalse(result.energyUsageSettings.autoplayGif)
        XCTAssertFalse(result.energyUsageSettings.loopStickers)
        XCTAssertFalse(result.energyUsageSettings.loopEmoji)
        XCTAssertFalse(result.energyUsageSettings.fullTranslucency)
        XCTAssertFalse(result.energyUsageSettings.extendBackgroundWork)
        XCTAssertFalse(result.energyUsageSettings.autodownloadInBackground)
        XCTAssertFalse(result.downloadInBackground)

        // The profile must not silently rewrite network presets or story quality.
        XCTAssertEqual(result.cellular, original.cellular)
        XCTAssertEqual(result.wifi, original.wifi)
        XCTAssertEqual(result.presets, original.presets)
        XCTAssertEqual(result.highQualityStories, original.highQualityStories)
    }

    func testRestoreReturnsPreviousPowerPreferencesAndPreservesOtherCurrentChanges() {
        var original = MediaAutoDownloadSettings.defaultSettings
        original.energyUsageSettings.activationThreshold = 37
        original.energyUsageSettings.autoplayVideo = true
        original.downloadInBackground = true

        var duringSaver = ultraBatterySaverSettings(from: original)
        duringSaver.highQualityStories = true
        let restored = restoringUltraBatterySaverSettings(
            from: duringSaver,
            energy: original.energyUsageSettings,
            downloadInBackground: original.downloadInBackground
        )

        XCTAssertEqual(restored.energyUsageSettings, original.energyUsageSettings)
        XCTAssertEqual(restored.downloadInBackground, original.downloadInBackground)
        XCTAssertTrue(restored.highQualityStories)
        XCTAssertEqual(restored.cellular, original.cellular)
        XCTAssertEqual(restored.wifi, original.wifi)
    }
}
