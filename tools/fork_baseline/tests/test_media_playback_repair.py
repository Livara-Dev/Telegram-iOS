"""Source-contract checks only; native player/rendering checks remain separate."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[3]

class MediaPlaybackRepairTests(unittest.TestCase):
    def read(self, path): return (ROOT / path).read_text()
    def test_player_has_visible_controls_and_starts_only_through_lifecycle_gate(self):
        source = self.read('submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift')
        player = source.split('private final class LocalArchivedPlaybackController:', 1)[1].split('func localArchiveGalleryImage', 1)[0]
        for token in ('self.player.play()', 'ArchivePlaybackIntent()', 'override func viewDidAppear', 'UISlider()', 'self.player.seek(', 'self.audioDisposable.set(nil)', 'self.readLease?.dispose()'):
            self.assertIn(token, player)
        self.assertNotIn('AVPlayerViewController()', player)
        self.assertIn('mediaManager.audioSession.push(', player)
        self.assertIn('self.kind == "videoMessage"', player)
    def test_video_thumbnail_never_replaces_the_archived_movie(self):
        source = self.read('submodules/SettingsUI/Sources/LocalArchiveSettingsController.swift')
        helper = source.split('public func localArchiveSavedVideoThumbnail', 1)[1].split('private final class LocalArchiveAnimationTarget', 1)[0]
        self.assertIn('AVAssetImageGenerator', helper)
        self.assertIn('ArchivePlaybackAsset(source:', helper)
        self.assertIn('generator.maximumSize', helper)
        self.assertIn('timeout(8', helper)
        self.assertNotIn('recordMedia', helper)
        self.assertNotIn('.write(', helper)
    def test_capture_requires_complete_size_and_rejects_still_image_confusion(self):
        source = self.read('submodules/LocalMessageArchive/Sources/LocalMessageArchive/ArchiveStore.swift')
        self.assertIn('expectedSize.map({ $0 == size })', source)
        self.assertIn('ArchiveMediaPayloadGuard.rejectsStillImage', source)
        self.assertIn('O_RDONLY | O_NOFOLLOW | O_NONBLOCK', source)
    def test_chat_bubbles_share_geometry_and_use_message_theme(self):
        row = self.read('submodules/TelegramUI/Components/Chat/ChatMessageItemImpl/Sources/ChatLocalArchiveRowView.swift')
        item = self.read('submodules/TelegramUI/Components/Chat/ChatMessageItemImpl/Sources/ChatLocalArchiveItem.swift')
        self.assertIn('available * 0.78', row)
        self.assertIn('bubbleColorComponents(theme: theme', row)
        self.assertIn('height * 0.5', row)
        self.assertGreaterEqual(item.count('ChatLocalArchiveRowView.bubbleWidth('), 3)
        self.assertNotIn('min(420.0', item)
    def test_existing_id_authentication_and_retention_timers_are_unchanged(self):
        source = self.read('Telegram/BUILD')
        self.assertIn('<key>AyuGramBaseBundleIdentifier</key>', source)
        self.assertIn('composer_icon_folders = ["Telegram"]', source)
        engine = self.read('submodules/TelegramCore/Sources/TelegramEngine/Messages/TelegramEngineMessages.swift')
        self.assertIn('then(_internal_markMessageContentAsConsumedInteractively', engine)

if __name__ == '__main__': unittest.main()
