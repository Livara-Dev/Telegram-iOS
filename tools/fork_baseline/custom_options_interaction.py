# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
"""Inputs and evidence validation for one retained-renderer interaction case.

Only the generated private UI driver's source changes. This helper does not
build, launch, publish, authenticate, or rebuild the retained application.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import custom_options_smoke as visual
import test_product_capture as products
import tiny_ui_test_project as project

SOURCE_PATH = 'tools/fork_baseline/tests/fixtures/retained-c55-CustomeOptionsInteractionTests.swift'
SOURCE_SHA256 = 'd6e84d693255d95638edfa8d4bb5d34719172d153ff93678271adc79171a2de1'
EXPECTED_CASE = 'CustomeOptionsInteractionTests.testLightDarkSearchNavigationSwitchAndActualResetConfirmation'
# Run422470 left the switch visibly off after the ordinary AX tap. Its cause is
# unknown. This fourth substitution is a single-center-tap diagnostic experiment,
# not an application fix or permission to retry a failed interaction.
ARCHIVE_TOGGLE_BEFORE = b'''        let archive = app.switches[self.prefix + "toggle.archive"]
        XCTAssertTrue(archive.isEnabled)
        archive.tap()
        XCTAssertEqual(archive.value as? String, "1")'''
ARCHIVE_TOGGLE_AFTER = b'''        let archive = app.switches[self.prefix + "toggle.archive"]
        let toggleWindow = app.windows.firstMatch
        let togglePoint = archive.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        func validToggleFrame(_ rect: CGRect) -> Bool {
            return [rect.origin.x, rect.origin.y, rect.width, rect.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }
                && rect.width > 0 && rect.height > 0
        }
        func recordArchiveToggle(_ phase: String) -> (safe: Bool, off: Bool, ledgerOn: Bool) {
            let exists = archive.exists
            let windowExists = toggleWindow.exists
            let windowFrame = windowExists ? toggleWindow.frame : .zero
            let controlFrame = exists ? archive.frame : .zero
            let windowValid = windowExists && validToggleFrame(windowFrame)
            let controlValid = exists && validToggleFrame(controlFrame)
            let enabled = exists && archive.isEnabled
            let hittable = exists && archive.isHittable
            let contained = windowValid && controlValid && windowFrame.contains(controlFrame)
            let center = contained ? togglePoint.screenPoint : .zero
            let centerValid = contained && center.x.isFinite && center.y.isFinite
                && controlFrame.contains(center) && windowFrame.contains(center)
            let value = exists ? archive.value as? String : nil
            let ledger = app.staticTexts["fixture.ledger"].firstMatch
            let ledgerValue = ledger.exists ? ledger.value as? String : nil
            let ledgerOff = ledgerValue?.hasPrefix("No Telegram account; archive off;") == true
            let ledgerOn = ledgerValue?.hasPrefix("No Telegram account; archive on;") == true
            let evidence: [String: Any] = ["phase": phase, "exists": exists,
                "enabled": enabled, "hittable": hittable, "window_valid": windowValid,
                "control_valid": controlValid, "contained": contained, "center_valid": centerValid,
                "value_known": value == "0" || value == "1", "is_on": value == "1",
                "ledger_known": ledgerOff || ledgerOn, "ledger_on": ledgerOn,
                "window": windowValid ? [windowFrame.minX, windowFrame.minY, windowFrame.width, windowFrame.height] : [],
                "control": controlValid ? [controlFrame.minX, controlFrame.minY, controlFrame.width, controlFrame.height] : [],
                "center": centerValid ? [center.x, center.y] : []]
            if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
               let text = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_ARCHIVE_TOGGLE:" + text) }
            return (contained && centerValid && enabled && hittable, value == "0" && ledgerOff, ledgerOn)
        }
        let initialToggle = recordArchiveToggle("before")
        XCTAssertTrue(initialToggle.safe, "Archive switch must be hittable inside a valid window")
        XCTAssertEqual(archive.value as? String, "0")
        XCTAssertTrue(initialToggle.off, "Synthetic callback ledger must initially report archive off")
        XCTAssertTrue(archive.isEnabled)
        XCTAssertTrue(archive.isHittable)
        guard initialToggle.safe && initialToggle.off else { return }
        togglePoint.tap()
        let expectedToggle = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "1"), object: archive)
        let toggleOutcome = XCTWaiter.wait(for: [expectedToggle], timeout: 5)
        let finalToggle = recordArchiveToggle("after")
        XCTAssertEqual(toggleOutcome, .completed)
        XCTAssertEqual(archive.value as? String, "1")
        XCTAssertTrue(finalToggle.ledgerOn, "Synthetic callback ledger must report archive on")'''
# This is an assertion-contract repair, not a gesture or UIKit-cause fix.
# Apple's XCUIElement.exists describes hierarchy membership, including offscreen
# elements. The retained run422439 visibly dismissed the key grid while the
# empty-search existence-only assertion failed. Keep that exact gesture and its
# five-second deadline, but require verified hidden geometry. The original
# repository test and the retained application remain byte-for-byte unchanged.
EMPTY_SEARCH_BEFORE = b'''        // Empty searches disable the keyboard's Search action. Exercise the
        // production table's interactive dismissal with stable window-relative
        // coordinates on this fixed portrait iPhone profile, without querying
        // keyboard geometry between a visibility check and an animation.
        let window = app.windows.firstMatch
        let start = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
        let end = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
        if keyboard.exists {
            start.press(forDuration: 0.1, thenDragTo: end)
        }
        let hidden = XCTNSPredicateExpectation(predicate: isHidden, object: keyboard)
        XCTAssertEqual(XCTWaiter.wait(for: [hidden], timeout: 5), .completed)'''
EMPTY_SEARCH_AFTER = b'''        // Empty-search assertion-contract repair: hierarchy membership does
        // not prove visibility. Preserve the real interactive-dismissal drag
        // and five-second deadline; invalid geometry must never prove hidden.
        let window = app.windows.firstMatch
        func validFrame(_ rect: CGRect) -> Bool {
            return [rect.origin.x, rect.origin.y, rect.size.width, rect.size.height, rect.maxX, rect.maxY].allSatisfy { $0.isFinite }
                && rect.size.width > 0 && rect.size.height > 0
        }
        func keyboardState() -> (visible: Bool, hidden: Bool, evidence: [String: Any]) {
            let windowFrame = window.frame
            let windowValid = validFrame(windowFrame)
            let exists = keyboard.exists
            let keyboardFrame = exists ? keyboard.frame : .zero
            let keyboardValid = exists && validFrame(keyboardFrame)
            let hittable = exists && keyboard.isHittable
            let intersectsWindow = windowValid && keyboardValid && !keyboardFrame.intersection(windowFrame).isEmpty
            let hidden = windowValid && (!exists || (keyboardValid && !hittable && !intersectsWindow))
            let evidence: [String: Any] = ["exists": exists, "hittable": hittable,
                "window_valid": windowValid, "keyboard_valid": keyboardValid,
                "intersects_window": intersectsWindow, "hidden": hidden,
                "window": windowValid ? [windowFrame.minX, windowFrame.minY, windowFrame.width, windowFrame.height] : [],
                "keyboard": keyboardValid ? [keyboardFrame.minX, keyboardFrame.minY, keyboardFrame.width, keyboardFrame.height] : []]
            return (exists && windowValid && keyboardValid && intersectsWindow, hidden, evidence)
        }
        func recordKeyboard(_ phase: String, state: (visible: Bool, hidden: Bool, evidence: [String: Any])) {
            var evidence = state.evidence
            evidence["phase"] = phase
            if let data = try? JSONSerialization.data(withJSONObject: evidence, options: [.sortedKeys]),
               let value = String(data: data, encoding: .utf8) { print("AYU_OPTIONS_KEYBOARD:" + value) }
        }
        let initial = keyboardState()
        recordKeyboard("before", state: initial)
        XCTAssertTrue(initial.visible, "Keyboard must initially intersect the valid application window")
        guard initial.visible else { return }
        let start = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
        let end = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.9))
        start.press(forDuration: 0.1, thenDragTo: end)
        let hiddenState = NSPredicate { _, _ in keyboardState().hidden }
        let hidden = XCTNSPredicateExpectation(predicate: hiddenState, object: keyboard)
        let outcome = XCTWaiter.wait(for: [hidden], timeout: 5)
        recordKeyboard("after", state: keyboardState())
        XCTAssertEqual(outcome, .completed)'''
SUBSTITUTIONS = (
    (b'let app = XCUIApplication()', b'let app = XCUIApplication(bundleIdentifier: "org.livara.ayugram.tests.custome-options")'),
    (b'app.launch()', b'app.activate()'),
    (EMPTY_SEARCH_BEFORE, EMPTY_SEARCH_AFTER),
    (ARCHIVE_TOGGLE_BEFORE, ARCHIVE_TOGGLE_AFTER),
)
STAGES = visual.STAGES
APPEARANCE_STAGES = tuple(stage for stage in STAGES if stage != 'reset-prompt')
MAX_LOG = 1024**2
MAX_PNG = 16 * 1024**2


class InteractionError(ValueError):
    pass


def derived_source(original):
    """Apply four exact reversible changes, including one physical-toggle experiment."""
    if not isinstance(original, bytes) or hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise InteractionError('interaction_source_digest_mismatch')
    result = original
    for before, after in SUBSTITUTIONS:
        if result.count(before) != 1 or after in result:
            raise InteractionError('interaction_substitution_not_unique')
        result = result.replace(before, after, 1)
    restored = result
    for before, after in reversed(SUBSTITUTIONS):
        restored = restored.replace(after, before, 1)
    if restored != original:
        raise InteractionError('interaction_extra_source_change')
    return result


def create_inputs(repository, destination):
    """Create only private driver files; never overwrite the original harness."""
    repository, destination = Path(repository).resolve(strict=True), Path(destination)
    source_path = products.fixed_path(repository, SOURCE_PATH)
    original = products.read_small(source_path, MAX_LOG)
    source = derived_source(original)
    result = project.create_project(destination)
    generated = products.fixed_path(destination, project.TARGET + '/' + project.TARGET + '.swift')
    descriptor = os.open(generated, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(source)
    if products.read_small(source_path, MAX_LOG) != original:
        raise InteractionError('interaction_repository_source_changed')
    return {**result, 'source': generated, 'expected_case': EXPECTED_CASE,
            'original_source_sha256': SOURCE_SHA256,
            'derived_source_sha256': hashlib.sha256(source).hexdigest(),
            'source_substitution_count': len(SUBSTITUTIONS), 'expected_case_count': 1,
            'expected_stages': STAGES, 'expected_appearance_stages': APPEARANCE_STAGES,
            'application_recompiled': False, 'renderer_recompiled': False,
            'native_tests_executed': False}


def case_results(data):
    """Count the one original case, including prefixed or merged native events."""
    if not isinstance(data, bytes) or len(data) > MAX_LOG:
        raise InteractionError('interaction_case_log_invalid')
    text = data.decode('utf-8', errors='replace')
    pattern = r"Test Case '-\[(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+) (test[A-Za-z0-9_]+)\]' (started|passed|failed|skipped|crashed)(?=[ .(\r\n]|$)"
    matches = list(re.finditer(pattern, text))
    started, outcomes = set(), {}
    unexpected = len(matches) != text.count("Test Case '-[")
    duplicate = False
    for match in matches:
        name, state = match[1] + '.' + match[2], match[3]
        if name != EXPECTED_CASE:
            unexpected = True
            continue
        if state == 'started':
            duplicate = duplicate or name in started
            started.add(name)
        else:
            duplicate = duplicate or name in outcomes or name not in started
            outcomes[name] = state
    return {'expected': 1, 'started': len(started), 'executed': len(outcomes),
            'passed': int(outcomes.get(EXPECTED_CASE) == 'passed'),
            'failed': int(outcomes.get(EXPECTED_CASE) in {'failed', 'crashed'}),
            'skipped': int(outcomes.get(EXPECTED_CASE) == 'skipped'),
            'unexpected_case_events': unexpected, 'duplicate_or_unstarted_outcome': duplicate,
            'cases': outcomes, 'all_expected_passed': started == {EXPECTED_CASE} and
            outcomes == {EXPECTED_CASE: 'passed'} and not unexpected and not duplicate}


def parsed_observations(data):
    """Parse raw bounded output before any publication redaction or truncation."""
    cases = case_results(data)
    text = data.decode('utf-8', errors='replace')
    return {'case_results': cases, 'frames': visual.frames_from_log(text),
            'appearances': visual.appearances_from_log(text)}


def _new_destination(destination):
    destination = Path(destination)
    try:
        valid = (destination.is_absolute() and not destination.exists() and not destination.is_symlink()
                 and destination.parent.is_dir() and destination == destination.resolve(strict=False)
                 and not any(path.is_symlink() for path in destination.parents))
    except (OSError, RuntimeError):
        valid = False
    if not valid:
        raise InteractionError('interaction_destination_invalid')
    return destination


def collect_attachments(exported, destination, png_validator):
    """Validate all twelve PNGs before copying any fixed public screenshot.

    Source paths stay private. A second bounded read must match each validated
    digest, and each exclusive private destination write is verified afterward.
    """
    destination = _new_destination(destination)
    if not callable(png_validator):
        raise InteractionError('interaction_png_validator_invalid')
    exported = Path(exported)
    if not exported.is_absolute() or any(path.is_symlink() for path in (exported, *exported.parents)):
        raise InteractionError('interaction_export_invalid')
    try:
        manifest = json.loads(products.read_small(products.fixed_path(exported, 'manifest.json'), 2 * 1024**2))
        mapping = visual.attachment_mapping(manifest)
        prepared = []
        for stage in STAGES:
            path = products.fixed_path(exported, mapping[stage])
            data = products.read_small(path, MAX_PNG)
            geometry = png_validator(data)
            if (not isinstance(geometry, dict) or set(geometry) != {'width', 'height'} or
                    type(geometry.get('width')) is not int or not 1000 <= geometry['width'] <= 1500 or
                    type(geometry.get('height')) is not int or not 2000 <= geometry['height'] <= 3000):
                raise InteractionError('interaction_png_invalid')
            prepared.append((path, {'stage': stage, 'filename': 'screenshots/' + stage + '.png',
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), **geometry}))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        raise InteractionError('interaction_attachments_invalid') from None

    destination.mkdir(mode=0o700)
    records = []
    for source, record in prepared:
        data = products.read_small(source, MAX_PNG)
        if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise InteractionError('interaction_attachment_source_changed')
        filename = record['stage'] + '.png'
        descriptor = os.open(destination / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
        copied = products.read_small(products.fixed_path(destination, filename), MAX_PNG)
        if len(copied) != record['bytes'] or hashlib.sha256(copied).hexdigest() != record['sha256']:
            raise InteractionError('interaction_attachment_copy_mismatch')
        records.append(record)
    return records
