# SPDX-License-Identifier: GPL-2.0-or-later
# Owned-metadata fixtures only, 2026-11-06. No CoreSimulator or native/auth.
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import patch

import auth_only_driver as d

SPEC = importlib.util.spec_from_file_location('owned_metadata_fixture', d.BASE / 'private_helpers/owned_simulator_metadata.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
NEW = '12345678-1234-4234-8234-123456789012'
OTHER = '87654321-4321-4321-8321-210987654321'
NAME = 'AYU_TESTDC_AUTH_SYNTHETIC_OWNED'


def device(root, identifier=NEW, name=NAME, extra=None):
    path = root / 'Library/Developer/CoreSimulator/Devices' / identifier
    path.mkdir(parents=True)
    value = {'UDID': identifier, 'name': name, 'deviceType': d.DEVICE, 'runtime': d.RUNTIME, 'state': 'Shutdown'}
    if extra: value.update(extra)
    (path / 'device.plist').write_bytes(plistlib.dumps(value))
    return path


def inventory(identifier=NEW, name=NAME):
    return json.dumps({'devices': {d.RUNTIME: [{'udid': identifier, 'name': name,
        'deviceTypeIdentifier': d.DEVICE, 'state': 'Shutdown'}]}}).encode()


def simulator(command):
    value = d.OwnedSimulator(command, m)
    value.identifier = NEW; value.name = NAME; value.created = True; value.before = set()
    return value


class IdentityTests(unittest.TestCase):
    def test_exact_reused_functions_match_the_reviewed_proven_source_bodies(self):
        root = d.BASE.parents[2]
        source = (root / 'tools/fork_baseline/modern_projection_plan.py').read_text()
        copied = (d.BASE / 'private_helpers/owned_simulator_metadata.py').read_text()
        private = source[source.index('def private_bytes('):source.index('\n\ndef entitlement_document', source.index('def private_bytes('))]
        identity = source[source.index('def owned_device_identity('):source.index('\n\ndef verify_owned_device', source.index('def owned_device_identity('))]
        self.assertIn(private, copied); self.assertIn(identity, copied)
        self.assertEqual(hashlib.sha256(private.encode()).hexdigest(), '4f5f903245f0f7a1be53f6e64bb2e6554346c2e505f0f95a80c1758930bae3d7')
        self.assertEqual(hashlib.sha256(identity.encode()).hexdigest(), 'da6e93ee1db544bf4f30c2518b3060ed12db696e04804ddf22eb4662cbc5b1b4')

    def test_exact_identity_never_claims_readiness_and_avoids_global_api(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            root = Path(temp).resolve(); device(root)
            proof = m.owned_device_identity(NEW, NAME)
            self.assertTrue(proof['identity_verified']); self.assertFalse(proof['readiness_asserted'])
            simulator(lambda *args, **kwargs: self.fail('No global inventory for exact metadata')).verify()

    def test_known_partial_mismatch_rejects_without_api_fallback(self):
        for key in ['UDID', 'name', 'deviceType', 'runtime']:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                root = Path(temp).resolve(); path = device(root)
                (path / 'device.plist').write_bytes(plistlib.dumps({key: 'SYNTHETIC_WRONG'}))
                proof = m.owned_device_identity(NEW, NAME)
                self.assertEqual(proof['state'], 'rejected')
                with self.assertRaises(d.Rejected): simulator(lambda *args, **kwargs: self.fail('No fallback after known mismatch')).verify()

    def test_unsafe_link_type_parent_or_owner_never_grants_identity(self):
        for kind in ['symlink', 'hardlink', 'fifo', 'parent_link', 'owner']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); path = device(root); metadata = path / 'device.plist'
                if kind == 'symlink':
                    outside = root / 'outside'; metadata.rename(outside); metadata.symlink_to(outside)
                elif kind == 'hardlink': os.link(metadata, root / 'other-link')
                elif kind == 'fifo': metadata.unlink(); os.mkfifo(metadata)
                elif kind == 'parent_link':
                    outside = root / 'outside'; path.rename(outside); path.symlink_to(outside, target_is_directory=True)
                proof = m.owned_device_identity(NEW, NAME, home=root, uid=os.getuid() + 1 if kind == 'owner' else None)
                self.assertEqual(proof['state'], 'rejected'); self.assertFalse(proof['identity_verified'])

    def test_unsupported_metadata_uses_one_bounded_full_inventory_without_booted_requirement(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            root = Path(temp).resolve(); path = device(root); (path / 'device.plist').write_bytes(plistlib.dumps({'UDID': NEW}))
            calls = []
            def command(args, **kwargs): calls.append((args, kwargs)); return 0, inventory()
            simulator(command).verify()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], ['xcrun', 'simctl', 'list', 'devices', '--json'])
            self.assertEqual(calls[0][1]['timeout'], 15)

    def test_preexisting_uuid_still_rejected_before_metadata_or_any_command(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            root = Path(temp).resolve(); device(root)
            value = simulator(lambda *args, **kwargs: self.fail('No commands'))
            value.before = {NEW}
            with self.assertRaises(d.Rejected): value.verify()


class AbsenceTests(unittest.TestCase):
    def test_complete_scan_proves_both_uuid_and_unique_name_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); device(root, OTHER, 'Unrelated synthetic device')
            value = m.owned_device_absence(NEW, NAME, home=root)
            self.assertTrue(value['absence_verified'])
            self.assertNotIn(NEW, json.dumps(value)); self.assertNotIn(str(root), json.dumps(value))

    def test_uuid_or_same_name_on_other_uuid_can_never_be_absent(self):
        for identifier, name in [(NEW, 'Other name'), (OTHER, NAME)]:
            with self.subTest(identifier=identifier), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); device(root, identifier, name)
                value = m.owned_device_absence(NEW, NAME, home=root)
                self.assertEqual(value['state'], 'present'); self.assertFalse(value['absence_verified'])

    def test_known_name_is_present_even_when_other_metadata_fields_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); path = device(root, OTHER, NAME)
            (path / 'device.plist').write_bytes(plistlib.dumps({'name': NAME}))
            self.assertEqual(m.owned_device_absence(NEW, NAME, home=root)['state'], 'present')

    def test_incomplete_malformed_unreadable_or_unsafe_candidates_never_prove_absence(self):
        for kind in ['missing', 'unsupported', 'malformed', 'oversized', 'permission', 'symlink', 'hardlink']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); path = device(root, OTHER, 'Unrelated'); metadata = path / 'device.plist'
                if kind == 'missing': metadata.unlink()
                elif kind == 'unsupported': metadata.write_bytes(plistlib.dumps({'UDID': OTHER}))
                elif kind == 'malformed': metadata.write_bytes(b'not-a-plist')
                elif kind == 'oversized': metadata.write_bytes(b'x' * 65537)
                elif kind == 'symlink':
                    outside = root / 'outside'; metadata.rename(outside); metadata.symlink_to(outside)
                elif kind == 'hardlink': os.link(metadata, root / 'other-link')
                context = patch.object(m, 'private_bytes', side_effect=PermissionError('SYNTHETIC_PRIVATE')) if kind == 'permission' else contextlib_null()
                with context: value = m.owned_device_absence(NEW, NAME, home=root)
                self.assertFalse(value['absence_verified'])
                self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(value))

    def test_uuid_named_regular_file_is_not_ignored_as_bookkeeping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); path = device(root, OTHER, 'Unrelated'); shutil.rmtree(path)
            path.write_bytes(plistlib.dumps({'name': NAME, 'UDID': OTHER}))
            proof = m.owned_device_absence(NEW, NAME, home=root)
            self.assertFalse(proof['absence_verified'])
            self.assertEqual(proof['reason'], 'unsupportedDeviceEntry')

    def test_expiry_during_final_revalidation_or_final_root_check_cannot_verify_absence(self):
        for values in [[0, 0, 0, 0, 3], [0, 0, 0, 0, 0, 3]]:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve(); device(root, OTHER, 'Unrelated')
                with patch.object(m.time, 'monotonic', side_effect=values):
                    proof = m.owned_device_absence(NEW, NAME, home=root)
                self.assertFalse(proof['absence_verified'])
                self.assertEqual(proof['reason'], 'metadataScanLimit')

    def test_scan_deadline_entry_limit_and_mutation_never_prove_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); path = device(root, OTHER, 'Unrelated')
            with patch.object(m.time, 'monotonic', side_effect=[0, 3]):
                self.assertFalse(m.owned_device_absence(NEW, NAME, home=root)['absence_verified'])
            original = m.private_bytes
            def changed(metadata, *args):
                raw = original(metadata, *args)
                (path.parent / 'bookkeeping-change').write_text('synthetic')
                return raw
            with patch.object(m, 'private_bytes', side_effect=changed):
                self.assertFalse(m.owned_device_absence(NEW, NAME, home=root)['absence_verified'])
            for i in range(1025): (path.parent / ('bookkeeping-' + str(i))).touch()
            self.assertFalse(m.owned_device_absence(NEW, NAME, home=root)['absence_verified'])


class CleanupTests(unittest.TestCase):
    def test_blocked_global_api_does_not_prevent_attested_exact_uuid_cleanup(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            root = Path(temp).resolve(); path = device(root); other = device(root, OTHER, 'Unrelated'); calls = []
            def command(args, **kwargs):
                calls.append(args)
                if args[1:3] == ['simctl', 'list']: self.fail('No global API when exact metadata/absence are proven')
                if args[1:3] == ['simctl', 'delete']: self.assertEqual(args[-1], NEW); shutil.rmtree(path)
                return 0, b''
            self.assertTrue(simulator(command).cleanup())
            self.assertTrue(other.exists()); self.assertFalse(path.exists())
            self.assertEqual([args[2] for args in calls], ['terminate', 'shutdown', 'delete'])

    def test_failed_delete_or_remaining_uuid_or_name_never_reports_cleanup_success(self):
        for kind in ['delete_failure', 'uuid_remains', 'name_remains']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                root = Path(temp).resolve(); path = device(root)
                if kind == 'name_remains': device(root, OTHER, NAME)
                def command(args, **kwargs):
                    if args[1:3] == ['simctl', 'list']: self.fail('Known presence must not downgrade to API fallback')
                    if args[1:3] == ['simctl', 'delete']:
                        if kind == 'delete_failure': raise d.Rejected('commandFailure')
                        if kind != 'uuid_remains': shutil.rmtree(path)
                    return 0, b''
                with self.assertRaises(d.Rejected): simulator(command).cleanup()

    def test_unknown_absence_with_blocked_api_stays_failed_after_exact_delete(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            root = Path(temp).resolve(); path = device(root); other = device(root, OTHER, 'Unrelated'); (other / 'device.plist').unlink()
            def command(args, **kwargs):
                if args[1:3] == ['simctl', 'delete']: shutil.rmtree(path)
                if args[1:3] == ['simctl', 'list']: raise d.Rejected('commandTimeout')
                return 0, b''
            with self.assertRaises(d.Rejected): simulator(command).cleanup()
            self.assertFalse(path.exists()); self.assertTrue(other.exists())


def contextlib_null():
    import contextlib
    return contextlib.nullcontext()


if __name__ == '__main__':
    unittest.main()
