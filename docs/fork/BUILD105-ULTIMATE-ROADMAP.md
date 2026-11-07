# Telegram Ultimate — Build 105

## Product direction

Build 105 expands the client through a polished **More Features** hub. The hub is the final entry in the main Settings list. It must remain native to iOS, searchable, accessible, and visually consistent in light and dark appearances.

## Included in the Build 105 source change

- **More Features:** renamed from the old fork label and moved to the final Settings section.
- **Feature hubs:** Smart Inbox, Chat Studio, Media Vault, and Ultra Battery Saver. Their navigation targets existing functional settings rather than placeholder pages.
- **Local Premium:** seven additional local accent palettes. These affect only client presentation; they do not grant Telegram Premium or alter server entitlements.
- **Ultra Battery Saver:** reversible local profile. It snapshots the previous energy settings, disables autoplay and animated loops, reduces translucency, disables background work and preloading, enables the local reduced-motion preference, and sets Telegram's automatic power-saving threshold to 20%. The previous settings are restored when the profile is turned off.
- **Settings presentation:** compact grouped rows, a clearer hierarchy, a concise hero/search header, and card-like controls.
- **Startup diagnostics:** a sanitized keychain OSStatus is included in the local startup status and copy action. No account identifiers, credentials, paths, message content, or key material are recorded.

## Functional boundaries

- Ultra Battery Saver changes app/Telegram preferences only. It cannot rewrite iOS system settings or guarantee that iOS stops all background activity.
- Local Premium covers local cosmetics and UI controls only. Server-side Premium entitlements remain controlled by Telegram.
- Smart Inbox uses existing local filters and Telegram chat folders. It does not reorder server history or bypass server limits.
- Media Vault exposes the encrypted local archive, playback, export, and native media settings. It cannot reconstruct bytes that were never fully captured.
- Startup OSStatus reporting improves diagnosis; it is not itself a Keychain entitlement repair. The exact device error must be observed before changing signing or access-group configuration.

## Next implementation waves

1. **Focus Profiles:** Study, Work, Night, Travel, and custom presets with explicit per-setting previews and reversible snapshots.
2. **Command Palette:** searchable actions for current chat, media, archive, translation, and message tools.
3. **Smart Inbox rules:** user-defined priority labels, response reminders, and local sorting with explainable rules.
4. **Conversation tools:** draft history, message bookmarks, local reminders, and timeline views for edits, media, links, and files.
5. **On-device intelligence:** OCR, optional offline voice transcription where supported, and local conversation summaries with explicit model availability.
6. **Media reliability:** completed-byte verification, interrupted-download recovery, Photos export, batch export, and duplicate detection.
7. **Account center:** account-specific appearance and power presets, clear session status, and load testing for the 50-account limit.
8. **Design system:** consistent spacing, typography, iconography, motion, haptics, empty states, loading states, accessibility, and screenshot-based acceptance across every screen.

## Release gates

- Portable checks pass.
- Swift source parses and the iOS app builds from the exact commit.
- Settings UI tests pass in light and dark appearances.
- Keychain startup reports the actual status code without resetting or migrating existing data.
- Ultra Battery Saver enable/disable restores the exact saved settings.
- Media playback, export, and archive tests verify complete file bytes.
- Physical-device upgrade from the previous build preserves app identity, sessions, archive, and Keychain access.

A feature is not called complete because a switch or destination is visible. Each release claim must be backed by its corresponding test or device evidence.
