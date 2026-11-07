# Archived media playback and compact chat presentation

Baseline application: b9d4f42433c52fb23673418e2daf4e6524d5e60f (build 103).
Baseline source checkout: 73114fad3fd3b42e64979c959bbdb4106f96de90.
The integration branch subsequently added a review document only.

## Observed defect and boundaries

The user's report describes a static first video frame and oversized deleted-message cards. The production `LocalArchivedPlaybackController` created an AVPlayerItem but never called `player.play()`, never acquired a managed audio session and relied exclusively on an embedded AVKit controller's controls. The screenshot title corresponds to this playback controller, not the image-only controller. This establishes a concrete playback defect; it does NOT prove whether every reported media file is complete. No private user media or screenshots are included in this source change.

## Repair

- An explicit AVPlayerLayer screen owns visible play/pause, replay, seeking and elapsed/duration controls. Playback starts only while visible, readable, ready, requested and holding Telegram's managed audio-session lease. It closes on lock/inactivity/navigation and releases its independent archive read lease. Round-video content is clipped to a circle.
- Video previews in chat are generated asynchronously from the actual encrypted movie with a bounded image size and timeout. No preview image is written back into a media slot.
- Chat rows use content-sized widths and message-theme colors, smaller spacing, real video previews and circular video-message previews. Stored-slot identity and local-only actions are preserved. Canonical message/network histories are not changed.
- Completed-file size verification and existing full-resource prefetch remain intact. A narrow file-header check rejects common still-image payloads in video, video-message and voice slots. Mismatched video/image MIME metadata is not silently routed to an image viewer. The signature check is NOT a complete video integrity/codec validator.
- The requested home-screen name is Telegram and the default icon is the existing upstream Telegram.icon. The distinct org.livara.ayugram identity, Keychain/application storage selection, signing and independent API credentials remain unchanged. This remains an unofficial modified client, not the official Telegram release. Existing selected alternate icons are not forcibly reset.

## Tests and evidence

Before publication: 96 host assertions exercised the actual playback-intent and payload-signature production code. Five branding checks, nine existing refinement checks and five new repair source-contract checks passed. Thirteen changed Swift files passed syntax parsing before the final documentation update. These are not iOS type-checking or device execution.

Native regressions were added for all playback-state combinations, invalid duration formatting, rejection of image payloads in audiovisual slots, MIME routing, compact bubble width, round-preview geometry, real saved-slot actions and revocation. The existing encrypted-store/cache-deletion/reopen video test now also starts AVPlayer and requires its clock to advance beyond the first frame.

The full local portable test run did not complete within the local execution budget; no full-suite pass is claimed. The new CI run must provide its own exact-source compilation and test outcomes. Historical build-103 native evidence reached an XCTest host bootstrap failure in the Projection suite; it cannot qualify this new UI.

## Visual acceptance, not yet observed

On the new native build, a saved movie must advance and seek, show working persistent controls and produce audio without changing global audio-session ownership manually. A saved round video must remain circular. Chat text bubbles must not stretch across the screen; video/round-video rows must show real previews and an enabled local playback action. Labels and controls must not overlap or clip in actual light and dark modes. Record the real simulator/device interaction and inspect original timestamped frames before declaring these visual checks passed.

No new IPA, physical-device verification, lossless capture, recovery of already missing media or exact Android UI parity is claimed by this document. Existing archive quotas and the 512 MiB file cap remain. Already fully archived videos should benefit from the playback repair; missing bytes cannot be reconstructed from a thumbnail.
