"""Refinement source contracts; these do not claim iOS execution."""
import hashlib
from pathlib import Path
import re
import unittest
ROOT = Path(__file__).resolve().parents[3]
class RefinementContracts(unittest.TestCase):
    def read(self, name): return (ROOT / name).read_text()
    def test_consistent_fifty_account_capacity_without_premium_upsell(self):
        limits = self.read("submodules/AccountUtils/Sources/AccountUtils.swift")
        self.assertIn("maximumNumberOfAccounts = 50", limits)
        self.assertIn("maximumPremiumNumberOfAccounts = maximumNumberOfAccounts", limits)
        for name in ("submodules/SettingsUI/Sources/LogoutOptionsController.swift", "submodules/SettingsUI/Sources/DeleteAccountOptionsController.swift", "submodules/TelegramUI/Components/PeerInfo/PeerInfoScreen/Sources/PeerInfoScreenSettingsActions.swift"):
            body = self.read(name)
            self.assertIn("count >= maximumNumberOfAccounts", body)
            self.assertNotIn("subject: .accounts", body)
            self.assertIn('title: "Account limit"', body)
    def test_compact_settings_have_no_main_description_footer(self):
        body = self.read("submodules/SettingsUI/Sources/CustomeOptionsController.swift")
        self.assertIn("self.showsSearch ? 60.0 : 8.0", body)
        self.assertNotIn("CAGradientLayer", body)
        footer = body.split("titleForFooterInSection", 1)[1].split("cellForRowAt", 1)[0]
        self.assertNotIn("explanation", footer)
        self.assertIn('"No matching settings" : nil', footer)
        self.assertIn("links.count == 1", body)
    def test_timed_media_goes_directly_to_real_account_controls(self):
        body = self.read("submodules/SettingsUI/Sources/LocalTimedMediaInformationController.swift")
        self.assertIn("return localArchiveSettingsController(context: context)", body)
        self.assertNotIn("ItemListTextItem", body)
    def test_native_prefetch_preserves_original_consumption(self):
        engine = self.read("submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift")
        self.assertIn("prepareMediaForConsumption(messageId: messageId)", engine)
        self.assertIn("then(_internal_markMessageContentAsConsumedInteractively", engine)
        coordinator = self.read("submodules/TelegramCore/Sources/LocalArchive/LocalArchiveCoordinator.swift")
        for token in ("mediaWarmups.count < 8", "timeout(120.0", "queue.after(600.0)", "fetchedMediaResource(mediaBox:", "keepResource(id:", "hasSameCaptureAuthority(as: settings)"):
            self.assertIn(token, coordinator)
    def test_alternate_video_size_does_not_use_primary_size(self):
        body = self.read("submodules/TelegramCore/Sources/LocalArchive/LocalArchiveCoordinator.swift")
        self.assertIn("expectedSize: candidate == 0 ? item.byteSize : nil", body)
        self.assertIn("if let warmup = self.mediaWarmups.removeValue(forKey: keys[candidate])", body)
    def test_actual_slot_query_and_namespace_identity(self):
        self.assertIn("SELECT slot FROM event_media WHERE event_key=? ORDER BY slot LIMIT 10", self.read("submodules/LocalMessageArchive/Sources/LocalMessageArchive/ArchiveStore.swift"))
        body = self.read("submodules/TelegramCore/Sources/LocalArchive/LocalArchiveDeletedHistory.swift")
        self.assertIn("Set<EngineMessage.Id>()", body)
        self.assertIn("storedMediaSlots: record.storedMediaSlots", body)
    def test_only_expired_canonical_placeholders_are_replaced(self):
        body = self.read("submodules/TelegramUI/Components/Chat/ChatHistoryEntry/Sources/ChatLocalArchiveProjection.swift")
        self.assertIn("message.media.allSatisfy({ $0 is TelegramMediaExpiredContent })", body)
        self.assertIn("expiredIds.contains(message.id) && addedIds.contains(message.id)", body)
    def test_historical_ui_driver_keeps_its_original_bytes(self):
        raw = (ROOT / "tools/fork_baseline/tests/fixtures/retained-c55-CustomeOptionsInteractionTests.swift").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "d6e84d693255d95638edfa8d4bb5d34719172d153ff93678271adc79171a2de1")
    def test_renderer_actions_use_saved_slots_and_clear_on_revocation(self):
        body = self.read("submodules/TelegramUI/Components/Chat/ChatMessageItemImpl/Sources/ChatLocalArchiveRowView.swift")
        self.assertIn("button.isEnabled = saved && openMedia != nil", body)
        self.assertIn("self.row?.storedMediaSlots.contains(button.tag) == true", body)
        self.assertIn("self.accessibilityCustomActions = nil", body)
        self.assertIn("self.photoView.image = nil", body)
if __name__ == "__main__": unittest.main()
