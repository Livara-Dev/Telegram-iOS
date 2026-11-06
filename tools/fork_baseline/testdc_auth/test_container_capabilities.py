# SPDX-License-Identifier: GPL-2.0-or-later
# Container metadata/capability fixtures only, 2026-11-06. No native/auth/data reads.
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
import ci_gate as c

SPEC = importlib.util.spec_from_file_location('container_capability_fixture', d.BASE / 'private_helpers/owned_simulator_metadata.py')
m = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(m)
DEVICE = '12345678-1234-4234-8234-123456789012'
DATA = '11111111-1111-4111-8111-111111111111'
APP = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
NAME = 'AYU_TESTDC_AUTH_SYNTHETIC_CAPABILITY'
MCM = '.com.apple.mobile_container_manager.metadata.plist'


def fixture(root):
    device = root / 'Library/Developer/CoreSimulator/Devices' / DEVICE
    device.mkdir(parents=True)
    (device / 'device.plist').write_bytes(plistlib.dumps({'UDID': DEVICE, 'name': NAME, 'deviceType': d.DEVICE, 'runtime': d.RUNTIME}))
    data = candidate(device, 'data', DATA)
    app = candidate(device, 'app', APP)
    return device, data, app


def candidate(device, role, identifier, bundle=d.BUNDLE):
    base = device / 'data/Containers' / ('Data' if role == 'data' else 'Bundle') / 'Application'
    path = base / identifier; path.mkdir(parents=True)
    (path / MCM).write_bytes(plistlib.dumps({'MCMMetadataIdentifier': bundle, 'extra': 'A'}))
    if role == 'app':
        (path / 'Telegram.app').mkdir()
        (path / 'Telegram.app/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': bundle, 'CFBundleExecutable': 'Telegram'}))
    return path


def acquire(role='data'):
    return m.owned_container_capability(DEVICE, NAME, role, d.BUNDLE, 'Telegram')


def revalidate(value, role='data'):
    return m.revalidate_container_capability(value, DEVICE, NAME, role, d.BUNDLE, 'Telegram')


class ContainerCapabilityTests(unittest.TestCase):
    def test_exact_data_and_app_paths_have_independent_bound_capabilities(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            _, data, app = fixture(Path(temp).resolve())
            data_value, app_value = acquire(), acquire('app')
            self.assertEqual(data_value.path, data); self.assertEqual(app_value.path, app / 'Telegram.app')
            self.assertTrue(revalidate(data_value)); self.assertTrue(revalidate(app_value, 'app'))

    def test_well_formed_foreign_candidates_are_ignored_but_unidentified_candidate_blocks_uniqueness(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            device, data, _ = fixture(Path(temp).resolve())
            other = candidate(device, 'data', OTHER, 'org.synthetic.foreign')
            self.assertEqual(acquire().path, data)
            (other / MCM).write_bytes(plistlib.dumps({'unsupported': True}))
            with self.assertRaises(m.PlanError): acquire()

    def test_duplicate_own_candidate_never_grants_capability(self):
        for role in ['data', 'app']:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                device, _, _ = fixture(Path(temp).resolve()); candidate(device, role, OTHER)
                with self.assertRaises(m.PlanError): acquire(role)

    def test_wrong_app_identifier_or_executable_rejects(self):
        for key, value in [('CFBundleIdentifier', 'org.synthetic.foreign'), ('CFBundleExecutable', 'Other')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                _, _, app = fixture(Path(temp).resolve()); path = app / 'Telegram.app/Info.plist'
                info = plistlib.loads(path.read_bytes()); info[key] = value; path.write_bytes(plistlib.dumps(info))
                with self.assertRaises(m.PlanError): acquire('app')

    def test_unsafe_or_unreadable_candidate_metadata_never_grants_capability(self):
        for kind in ['missing', 'symlink', 'hardlink', 'fifo', 'malformed', 'oversized', 'permission']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                root = Path(temp).resolve(); _, data, _ = fixture(root); metadata = data / MCM
                if kind == 'missing': metadata.unlink()
                elif kind == 'symlink':
                    outside = root / 'outside'; metadata.rename(outside); metadata.symlink_to(outside)
                elif kind == 'hardlink': os.link(metadata, root / 'link')
                elif kind == 'fifo': metadata.unlink(); os.mkfifo(metadata)
                elif kind == 'malformed': metadata.write_bytes(b'not-a-plist')
                elif kind == 'oversized': metadata.write_bytes(b'x' * 16385)
                original = m.private_bytes
                def read(path, *args):
                    if kind == 'permission' and path.name == MCM: raise PermissionError('SYNTHETIC_PRIVATE')
                    return original(path, *args)
                with patch.object(m, 'private_bytes', side_effect=read), self.assertRaises((m.PlanError, OSError, ValueError)):
                    acquire()

    def test_parent_or_candidate_symlink_and_wrong_uid_reject(self):
        for kind in ['parent', 'candidate', 'owner']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                root = Path(temp).resolve(); _, data, _ = fixture(root)
                if kind in {'parent', 'candidate'}:
                    path = data.parent if kind == 'parent' else data
                    outside = root / 'outside'; path.rename(outside); path.symlink_to(outside, target_is_directory=True)
                if kind == 'owner':
                    actual = os.getuid()
                    with patch.object(m.os, 'getuid', return_value=actual + 1), self.assertRaises(m.PlanError): acquire()
                else:
                    with self.assertRaises(m.PlanError): acquire()

    def test_revalidation_rejects_new_duplicate_foreign_mcm_or_replaced_directory(self):
        for kind in ['duplicate', 'foreign', 'replaced']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                root = Path(temp).resolve(); device, data, _ = fixture(root); value = acquire()
                if kind == 'duplicate': candidate(device, 'data', OTHER)
                elif kind == 'foreign': (data / MCM).write_bytes(plistlib.dumps({'MCMMetadataIdentifier': 'org.synthetic.foreign'}))
                else:
                    saved = root / 'held'; data.rename(saved); shutil.copytree(saved, data)
                with self.assertRaises(m.PlanError): revalidate(value)

    def test_equal_size_metadata_change_invalidates_preseed_binding(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            _, data, _ = fixture(Path(temp).resolve()); value = acquire(); metadata = data / MCM
            old = metadata.read_bytes(); changed = old.replace(b'<string>A</string>', b'<string>B</string>')
            self.assertEqual(len(changed), len(old)); self.assertNotEqual(changed, old); metadata.write_bytes(changed)
            with self.assertRaises(m.PlanError): revalidate(value)

    def test_capability_cannot_cross_device_name_role_or_raw_path(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            _, data, _ = fixture(Path(temp).resolve()); value = acquire()
            with self.assertRaises(m.PlanError): revalidate(data)
            for identifier, name, role in [(OTHER, NAME, 'data'), (DEVICE, 'Other name', 'data'), (DEVICE, NAME, 'app')]:
                with self.subTest(identifier=identifier, role=role), self.assertRaises(m.PlanError):
                    m.revalidate_container_capability(value, identifier, name, role, d.BUNDLE, 'Telegram')

    def test_finder_reads_only_container_metadata_device_identity_and_app_info_not_account_contents(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            device, data, app = fixture(Path(temp).resolve()); secret = data / 'Library/private-account/database'
            secret.parent.mkdir(parents=True); secret.write_bytes(b'SYNTHETIC_DO_NOT_READ')
            allowed = {device / 'device.plist', data / MCM, app / MCM, app / 'Telegram.app/Info.plist'}
            original = m.private_bytes; reads = []
            def read(path, *args):
                self.assertIn(path, allowed); reads.append(path); return original(path, *args)
            with patch.object(m, 'private_bytes', side_effect=read):
                self.assertTrue(revalidate(acquire())); self.assertTrue(revalidate(acquire('app'), 'app'))
            self.assertNotIn(secret, reads)

    def test_late_deadline_after_final_device_revalidation_never_grants_capability(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
            fixture(Path(temp).resolve()); clock = [0]; original = m.owned_device_identity; calls = [0]
            def identity(*args, **kwargs):
                value = original(*args, **kwargs); calls[0] += 1
                if calls[0] == 2: clock[0] = m.CONTAINER_SCAN_BUDGET_SECONDS + 0.001
                return value
            with patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(m, 'owned_device_identity', side_effect=identity), self.assertRaises(m.PlanError) as caught:
                acquire()
            self.assertEqual(caught.exception.code, 'owned_container_inventory_deadline')

    def test_inventory_cap_and_non_uuid_candidate_cannot_grant_capability(self):
        for kind in ['limit', 'invalid']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, patch.object(Path, 'home', return_value=Path(temp)):
                _, data, _ = fixture(Path(temp).resolve())
                if kind == 'invalid': (data.parent / 'unknown-layout').mkdir()
                else:
                    for i in range(m.MAX_CONTAINER_CANDIDATES): (data.parent / ('00000000-0000-4000-8000-' + ('%012d' % i))).mkdir()
                with self.assertRaises(m.PlanError) as caught: acquire()
                self.assertEqual(caught.exception.code, 'owned_container_inventory_count_limit' if kind == 'limit' else 'unsupported_owned_container_layout')
        self.assertEqual(m.MAX_CONTAINER_CANDIDATES, 512)
        self.assertEqual(m.CONTAINER_SCAN_BUDGET_SECONDS, 18)


class ClosedMetadataFailureTests(unittest.TestCase):
    def test_helper_and_driver_reason_sets_match_and_known_reason_is_acknowledged(self):
        self.assertEqual(m.METADATA_FAILURE_CODES, d.METADATA_FAILURE_CODES)
        error = m.PlanError('owned_container_not_unique')
        self.assertEqual(d.metadata_failure_code(error, m), 'owned_container_not_unique')

    def test_unknown_secret_or_path_argument_is_never_retained_or_exported(self):
        for secret in ['SYNTHETIC_PRIVATE_DO_NOT_EXPORT', '/synthetic/private/account/path']:
            error = m.PlanError(secret)
            self.assertEqual(error.code, 'unexpectedMetadataFailure')
            self.assertNotIn(secret, str(error)); self.assertNotIn(secret, repr(error.args))
            value = d.closed_result(setup_only=True)
            value.update(failure='internal', exception_kind='otherException', phase='dataContainer',
                metadata_failure=d.metadata_failure_code(error, m))
            c.exportable(value)
            self.assertNotIn(secret, json.dumps(value))

    def test_foreign_or_subclass_error_and_mutated_unknown_code_are_not_acknowledged(self):
        class Foreign(Exception): code = 'owned_container_not_unique'
        class Subclass(m.PlanError): pass
        self.assertIsNone(d.metadata_failure_code(Foreign('private'), m))
        self.assertIsNone(d.metadata_failure_code(Subclass('owned_container_not_unique'), m))
        error = m.PlanError('owned_container_not_unique'); error.code = '/synthetic/private/path'
        self.assertIsNone(d.metadata_failure_code(error, m))

    def test_closed_schema_rejects_unapproved_metadata_reason_or_unfailed_success(self):
        for reason in ['/synthetic/private/path', 'arbitrary-message', True, ['owned_container_not_unique']]:
            value = d.closed_result(setup_only=True); value['metadata_failure'] = reason
            with self.subTest(reason=reason), self.assertRaises(d.Rejected): c.exportable(value)
        value = d.closed_result(setup_only=True); value['metadata_failure'] = 'owned_container_not_unique'
        with self.assertRaises(d.Rejected): c.exportable(value)


if __name__ == '__main__':
    unittest.main()
