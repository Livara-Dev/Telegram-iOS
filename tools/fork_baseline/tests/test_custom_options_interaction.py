# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Portable interaction-input/evidence checks; these execute no native UI."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import custom_options_interaction as interaction
import custom_options_xctest as native
from test_custom_options_xctest import png

REPOSITORY = Path(__file__).resolve().parents[3]


def native_log(state='passed'):
    name, method = interaction.EXPECTED_CASE.split('.')
    lines = [f"Test Case '-[{interaction.project.TARGET}.{name} {method}]' started."]
    for stage in interaction.STAGES:
        lines.append('AYU_OPTIONS_FRAME:' + json.dumps({'stage': stage,
            'window': [0, 0, 393, 852], 'table': [0, 80, 393, 700]}))
        if stage != 'reset-prompt':
            dark = not stage.endswith('-light')
            lines.append('AYU_OPTIONS_APPEARANCE:' + json.dumps({'stage': stage, 'window_dark': dark,
                'table_found': True, 'table_dark': dark, 'background_sampled': True, 'background_dark': dark}))
    lines.append(f"Test Case '-[{interaction.project.TARGET}.{name} {method}]' {state} (230 seconds).")
    return ('\n'.join(lines) + '\n').encode()


def attachments(root):
    root.mkdir()
    manifest = []
    for stage in interaction.STAGES:
        (root / (stage + '.png')).write_bytes(png())
        manifest.append({'configuredName': 'ayu-' + stage, 'exportedFileName': stage + '.png'})
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return manifest


class RetainedInteractionTests(unittest.TestCase):
    def test_pinned_original_changes_only_four_exact_reversible_harness_expressions(self):
        original = (REPOSITORY / interaction.SOURCE_PATH).read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), interaction.SOURCE_SHA256)
        result = interaction.derived_source(original)
        expected = original
        for before, after in interaction.SUBSTITUTIONS:
            self.assertEqual(original.count(before), 1)
            expected = expected.replace(before, after, 1)
        self.assertEqual(result, expected)
        for before, after in reversed(interaction.SUBSTITUTIONS):
            result = result.replace(after, before, 1)
        self.assertEqual(result, original)
        with self.assertRaisesRegex(interaction.InteractionError, '^interaction_source_digest_mismatch$'):
            interaction.derived_source(original + b'\n')

    def test_assertion_contract_repair_keeps_the_same_drag_deadline_and_nonempty_search(self):
        original = (REPOSITORY / interaction.SOURCE_PATH).read_bytes()
        result = interaction.derived_source(original)
        before, after = interaction.EMPTY_SEARCH_BEFORE, interaction.EMPTY_SEARCH_AFTER
        self.assertEqual(len(interaction.SUBSTITUTIONS), 4)
        self.assertEqual(original.count(before), 1)
        self.assertEqual(result.count(after), 1)
        self.assertNotIn(before, result)
        # This is precisely one contract repair, not a new dismissal action or
        # permission to silently change any of the twelve behavioral stages.
        for expression in (b'CGVector(dx: 0.5, dy: 0.45)', b'CGVector(dx: 0.5, dy: 0.9)',
                           b'start.press(forDuration: 0.1, thenDragTo: end)'):
            self.assertEqual(before.count(expression), 1)
            self.assertEqual(after.count(expression), 1)
        self.assertEqual(before.count(b'timeout: 5'), 1)
        self.assertEqual(after.count(b'timeout: 5'), 1)
        nonempty = original.split(b'        if !text.isEmpty {\n', 1)[1].split(before, 1)[0]
        self.assertIn(nonempty, result)
        self.assertEqual(result.count(b'let isHidden = NSPredicate(format: "exists == false")'), 1)
        self.assertEqual(result.count(b'predicate: isHidden, object: keyboard'), 1)
        self.assertNotIn(b'sleep', after)
        self.assertNotIn(b'.tap()', after)
        self.assertNotIn(b'while ', after)

    def test_hidden_contract_fails_closed_for_invalid_or_obstructing_geometry(self):
        # Pin the actual generated Swift expressions. These portable checks do
        # not execute UIKit or claim that native AX geometry matches its pixels.
        after = interaction.EMPTY_SEARCH_AFTER
        self.assertIn(b'[rect.origin.x, rect.origin.y, rect.size.width, rect.size.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }', after)
        self.assertIn(b'&& rect.size.width > 0 && rect.size.height > 0', after)
        self.assertIn(b'let keyboardValid = exists && validFrame(keyboardFrame)', after)
        self.assertIn(b'let intersectsWindow = windowValid && keyboardValid && !keyboardFrame.intersection(windowFrame).isEmpty', after)
        self.assertIn(b'let hidden = windowValid && (!exists || (keyboardValid && !hittable && !intersectsWindow))', after)
        self.assertIn(b'return (exists && windowValid && keyboardValid && intersectsWindow, hidden, evidence)', after)
        self.assertLess(after.index(b'XCTAssertTrue(initial.visible'), after.index(b'start.press('))
        self.assertLess(after.index(b'guard initial.visible else { return }'), after.index(b'start.press('))
        self.assertIn(b'let hiddenState = NSPredicate { _, _ in keyboardState().hidden }', after)
        self.assertLess(after.index(b'recordKeyboard("after"'), after.index(b'XCTAssertEqual(outcome, .completed)'))

    def test_keyboard_observation_can_publish_only_fixed_booleans_and_rectangles(self):
        source = interaction.EMPTY_SEARCH_AFTER
        evidence = source.split(b'let evidence: [String: Any] = [', 1)[1].split(b'            return ', 1)[0]
        self.assertEqual(re.findall(rb'"([a-z_]+)":', evidence),
            [b'exists', b'hittable', b'window_valid', b'keyboard_valid', b'intersects_window',
             b'hidden', b'window', b'keyboard'])
        self.assertIn(b'evidence["phase"] = phase', source)
        self.assertEqual(re.findall(rb'recordKeyboard\("([a-z]+)"', source), [b'before', b'after'])
        self.assertEqual(source.count(b'print("AYU_OPTIONS_KEYBOARD:" + value)'), 1)
        for private in (b'.label', b'.value', b'.debugDescription', b'print(error)', b'print(app)', b'print(keyboard)'):
            self.assertNotIn(private, source)
        # Finite geometry alone reaches JSON; invalid rectangles are empty arrays.
        self.assertIn(b'"window": windowValid ? [', evidence)
        self.assertIn(b'"keyboard": keyboardValid ? [', evidence)
        self.assertEqual(evidence.count(b': []'), 2)

    def test_toggle_experiment_has_one_center_event_and_preserves_exact_final_assertion(self):
        original = (REPOSITORY / interaction.SOURCE_PATH).read_bytes()
        generated = interaction.derived_source(original)
        before, after = interaction.ARCHIVE_TOGGLE_BEFORE, interaction.ARCHIVE_TOGGLE_AFTER
        self.assertEqual(original.count(before), 1)
        self.assertEqual(generated.count(after), 1)
        self.assertEqual(after.count(b'togglePoint.tap()'), 1)
        self.assertNotIn(b'archive.tap()', after)
        self.assertEqual(after.count(b'withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)'), 1)
        self.assertEqual(after.count(b'timeout: 5'), 1)
        self.assertIn(b'XCTAssertEqual(archive.value as? String, "1")', before)
        self.assertEqual(after.count(b'XCTAssertEqual(archive.value as? String, "1")'), 1)
        self.assertIn(b'NSPredicate(format: "value == %@", "1"), object: archive', after)
        for operation in (b'while ', b'for ', b'sleep', b'togglePoint.press', b'app.tap()', b'app.launch()'):
            self.assertNotIn(operation, after)

    def test_toggle_preconditions_reject_unknown_value_geometry_and_synthetic_ledger(self):
        after = interaction.ARCHIVE_TOGGLE_AFTER
        self.assertIn(b'[rect.origin.x, rect.origin.y, rect.width, rect.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }', after)
        self.assertIn(b'&& rect.width > 0 && rect.height > 0', after)
        self.assertIn(b'let contained = windowValid && controlValid && windowFrame.contains(controlFrame)', after)
        self.assertIn(b'center.x.isFinite && center.y.isFinite', after)
        self.assertIn(b'&& controlFrame.contains(center) && windowFrame.contains(center)', after)
        self.assertIn(b'return (contained && centerValid && enabled && hittable, value == "0" && ledgerOff, ledgerOn)', after)
        self.assertIn(b'XCTAssertEqual(archive.value as? String, "0")', after)
        self.assertIn(b'XCTAssertTrue(archive.isEnabled)', after)
        self.assertIn(b'XCTAssertTrue(archive.isHittable)', after)
        self.assertIn(b'guard initialToggle.safe && initialToggle.off else { return }', after)
        self.assertLess(after.index(b'guard initialToggle.safe'), after.index(b'togglePoint.tap()'))
        self.assertIn(b'hasPrefix("No Telegram account; archive off;") == true', after)
        self.assertIn(b'hasPrefix("No Telegram account; archive on;") == true', after)
        self.assertIn(b'XCTAssertTrue(finalToggle.ledgerOn', after)
        self.assertLess(after.index(b'let finalToggle = recordArchiveToggle("after")'),
            after.index(b'XCTAssertEqual(toggleOutcome, .completed)'))

    def test_toggle_diagnostics_publish_no_raw_ax_or_ledger_values(self):
        after = interaction.ARCHIVE_TOGGLE_AFTER
        evidence = after.split(b'let evidence: [String: Any] = [', 1)[1].split(b'            if let data', 1)[0]
        self.assertEqual(re.findall(rb'"([a-z_]+)":', evidence), [b'phase', b'exists', b'enabled',
            b'hittable', b'window_valid', b'control_valid', b'contained', b'center_valid',
            b'value_known', b'is_on', b'ledger_known', b'ledger_on', b'window', b'control', b'center'])
        for unsafe in (b'"value":', b'"ledger":', b'"label":', b'"text":', b'.debugDescription'):
            self.assertNotIn(unsafe, evidence)
        self.assertEqual(re.findall(rb'recordArchiveToggle\("([a-z]+)"', after), [b'before', b'after'])
        self.assertEqual(after.count(b'print("AYU_OPTIONS_ARCHIVE_TOGGLE:" + text)'), 1)
        self.assertEqual(evidence.count(b': []'), 3)
        self.assertIn(b'"center": centerValid ? [center.x, center.y] : []', evidence)

    def test_every_source_mutation_is_rejected_before_private_project_creation(self):
        original = (REPOSITORY / interaction.SOURCE_PATH).read_bytes()
        mutations = (original + b'\n', original.replace(b'timeout: 5', b'timeout: 6', 1),
            original.replace(b'dy: 0.45', b'dy: 0.40', 1),
            original.replace(b'XCTAssertTrue(archive.isEnabled)', b'// removed assertion', 1),
            original.replace(interaction.EMPTY_SEARCH_BEFORE, interaction.EMPTY_SEARCH_AFTER, 1))
        for mutated in mutations:
            with self.subTest(digest=hashlib.sha256(mutated).hexdigest()):
                self.assertNotEqual(mutated, original)
                with self.assertRaisesRegex(interaction.InteractionError, '^interaction_source_digest_mismatch$'):
                    interaction.derived_source(mutated)
        # Independently exercise uniqueness guards, even if a future maintainer
        # intentionally changes the digest pin without updating the exact repair.
        for before, after in interaction.SUBSTITUTIONS:
            for mutated in (original.replace(before, b'', 1), original + before, original + after):
                with self.subTest(before=before[:30], digest=hashlib.sha256(mutated).hexdigest()), \
                        patch.object(interaction, 'SOURCE_SHA256', hashlib.sha256(mutated).hexdigest()), \
                        self.assertRaisesRegex(interaction.InteractionError, '^interaction_substitution_not_unique$'):
                    interaction.derived_source(mutated)

    def test_private_project_replaces_only_generated_swift_and_preserves_every_original_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); baseline = root / 'baseline'; full = root / 'full'
            interaction.project.create_project(baseline)
            original = (REPOSITORY / interaction.SOURCE_PATH).read_bytes()
            result = interaction.create_inputs(REPOSITORY, full)
            def contents(folder): return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
            before, after = contents(baseline), contents(full)
            source = interaction.project.TARGET + '/' + interaction.project.TARGET + '.swift'
            self.assertEqual([name for name in before if before[name] != after[name]], [source])
            self.assertEqual(after[source], interaction.derived_source(original))
            self.assertEqual((REPOSITORY / interaction.SOURCE_PATH).read_bytes(), original)
            self.assertEqual(result['expected_case'], interaction.EXPECTED_CASE)
            self.assertEqual(result['expected_case_count'], 1)
            self.assertEqual(result['source_substitution_count'], 4)
            self.assertEqual(result['derived_source_sha256'], hashlib.sha256(after[source]).hexdigest())
            self.assertFalse(result['native_tests_executed']); self.assertFalse(result['application_recompiled'])
            self.assertFalse(result['renderer_recompiled'])
            self.assertEqual(re.findall(rb'func (test\w+)\(', after[source]),
                [interaction.EXPECTED_CASE.split('.')[1].encode()])
            self.assertEqual(re.findall(rb'self\.record\("([^"]+)", app: app\)', after[source]),
                [stage.encode() for stage in interaction.STAGES])
            self.assertIn(b'self.continueAfterFailure = false', after[source])
            self.assertIn(b'app.terminate()', after[source])
            for path in (full, *full.rglob('*')):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_case_parser_requires_one_case_and_cannot_ignore_prefixed_merged_or_extra_failures(self):
        self.assertTrue(interaction.case_results(native_log())['all_expected_passed'])
        prefix = b'2026-11-06 XCTest: '
        self.assertTrue(interaction.case_results(prefix + native_log().replace(b'\n', b' '))['all_expected_passed'])
        name, method = interaction.EXPECTED_CASE.split('.')
        failed = f"Test Case '-[{name} {method}]' failed.\n".encode()
        for data in (b'', native_log('failed'), native_log('skipped'), native_log() + native_log(),
                     native_log() + prefix + failed,
                     native_log() + prefix + b"Test Case '-[Other testExtra]' started.\n",
                     native_log() + b"Test Case '-[Other.Nested.Type testExtra]' started.\n"):
            with self.subTest(data=data[-80:]):
                self.assertFalse(interaction.case_results(data)['all_expected_passed'])
        with self.assertRaises(ValueError): interaction.case_results(b'x' * (interaction.MAX_LOG + 1))

    def test_raw_log_requires_exact_twelve_frames_and_eleven_correct_appearance_samples(self):
        result = interaction.parsed_observations(native_log())
        self.assertEqual(set(result), {'case_results', 'frames', 'appearances'})
        self.assertTrue(result['case_results']['all_expected_passed'])
        self.assertEqual([row['stage'] for row in result['frames']], list(interaction.STAGES))
        self.assertEqual([row['stage'] for row in result['appearances']], list(interaction.APPEARANCE_STAGES))
        self.assertEqual(len(result['frames']), 12); self.assertEqual(len(result['appearances']), 11)
        for marker in ('AYU_OPTIONS_FRAME:', 'AYU_OPTIONS_APPEARANCE:'):
            lines = native_log().splitlines(keepends=True)
            index = next(i for i, line in enumerate(lines) if line.startswith(marker.encode()))
            for data in (b''.join(lines[:index] + lines[index + 1:]), b''.join(lines + [lines[index]])):
                with self.assertRaises(ValueError): interaction.parsed_observations(data)
        wrong = native_log().replace(b'"window_dark": false', b'"window_dark": true', 1)
        with self.assertRaises(ValueError): interaction.parsed_observations(wrong)
        # A failed native outcome may retain valid observations, but cannot pass.
        self.assertFalse(interaction.parsed_observations(native_log('failed'))['case_results']['all_expected_passed'])

    def test_all_twelve_complete_pngs_copy_to_exact_private_public_names_with_verified_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); exported = root / 'exported'; destination = root / 'screenshots'
            attachments(exported)
            result = interaction.collect_attachments(exported, destination, native.png_geometry)
            self.assertEqual([item['stage'] for item in result], list(interaction.STAGES))
            self.assertEqual({p.name for p in destination.iterdir()}, {stage + '.png' for stage in interaction.STAGES})
            for row in result:
                self.assertEqual(set(row), {'stage', 'filename', 'bytes', 'sha256', 'width', 'height'})
                self.assertEqual(row['filename'], 'screenshots/' + row['stage'] + '.png')
                copied = destination / (row['stage'] + '.png')
                self.assertEqual(copied.read_bytes(), png())
                self.assertEqual(row['sha256'], hashlib.sha256(copied.read_bytes()).hexdigest())
                self.assertEqual(row['bytes'], copied.stat().st_size)
                self.assertEqual((row['width'], row['height']), (1179, 2556))
                self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertNotIn(str(root), json.dumps(result))
            # Identical fixture pixels validate encoding only, not UI states.
            self.assertEqual(len({row['sha256'] for row in result}), 1)

    def test_every_initial_validation_failure_occurs_before_destination_or_copy_is_created(self):
        for kind in ('missing', 'duplicate', 'traversal', 'symlink', 'directory', 'headerOnly', 'truncated', 'oversized', 'badGeometry'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); exported = root / 'exported'; destination = root / 'screenshots'
                manifest = attachments(exported); last = exported / (interaction.STAGES[-1] + '.png')
                if kind == 'missing': manifest.pop()
                if kind == 'duplicate': manifest.append(copy.deepcopy(manifest[0]))
                if kind == 'traversal': manifest[-1]['exportedFileName'] = '../outside.png'
                if kind == 'symlink': last.unlink(); last.symlink_to(exported / (interaction.STAGES[0] + '.png'))
                if kind == 'directory': last.unlink(); last.mkdir()
                if kind == 'headerOnly': last.write_bytes(png()[:33])
                if kind == 'truncated': last.write_bytes(png()[:-12])
                if kind == 'oversized':
                    with last.open('wb') as stream: stream.truncate(interaction.MAX_PNG + 1)
                (exported / 'manifest.json').write_text(json.dumps(manifest))
                validator = (lambda _: {'width': 1179, 'height': 2556, 'private': str(root)}) if kind == 'badGeometry' else native.png_geometry
                with self.assertRaises(ValueError): interaction.collect_attachments(exported, destination, validator)
                self.assertFalse(destination.exists())

    def test_preexisting_or_aliased_destination_never_overwrites_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); exported = root / 'exported'; attachments(exported)
            existing = root / 'existing'; existing.mkdir(); keep = existing / 'keep'; keep.write_bytes(b'original')
            alias = root / 'alias'; alias.symlink_to(existing, target_is_directory=True)
            for destination in (existing, alias, alias / 'screenshots', root / 'missing' / 'screenshots', Path('relative')):
                with self.subTest(destination=str(destination)), self.assertRaises(ValueError):
                    interaction.collect_attachments(exported, destination, native.png_geometry)
            self.assertEqual(keep.read_bytes(), b'original')
            self.assertFalse((existing / 'screenshots').exists())

    def test_copy_phase_rechecks_validated_source_digest_and_detects_changed_destination(self):
        for kind in ('source', 'destination'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); exported = root / 'exported'; destination = root / 'screenshots'
                attachments(exported); original = interaction.products.read_small; changed = False
                def read(path, limit=2 * 1024**2):
                    nonlocal changed
                    path = Path(path)
                    if kind == 'source' and destination.exists() and path.parent == exported and not changed:
                        changed = True
                        return original(path, limit) + b'changed'
                    if kind == 'destination' and path.parent == destination:
                        return original(path, limit) + b'changed'
                    return original(path, limit)
                with patch.object(interaction.products, 'read_small', side_effect=read):
                    with self.assertRaisesRegex(interaction.InteractionError,
                            '^interaction_attachment_' + ('source_changed' if kind == 'source' else 'copy_mismatch') + '$'):
                        interaction.collect_attachments(exported, destination, native.png_geometry)
                if kind == 'source': self.assertEqual(list(destination.iterdir()), [])

    def test_module_does_not_import_runner_build_native_commands_or_change_video_policy(self):
        source = Path(interaction.__file__).read_text()
        for word in ('import custom_options_xctest', 'subprocess', 'PROPOSED_VIDEO_LIMIT', 'MAX_VIDEO',
                     'xcodebuild', 'simctl', 'WebDriverAgent'):
            self.assertNotIn(word, source)


if __name__ == '__main__': unittest.main()
