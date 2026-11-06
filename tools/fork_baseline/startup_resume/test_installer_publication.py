# SPDX-License-Identifier: GPL-2.0-or-later
# Created for the AyuGram iOS fork on 2026-11-06.
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import shutil
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

import installer_observation as observation
import resume_origins as origins
import resume_startup as startup


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.work=self.root/'work';self.replay=self.root/'replay';self.public=self.root/'public'
        self.work.mkdir();self.replay.mkdir()
        (self.work/'source-build-origin.json').write_text(json.dumps(origins.source_build_origin()))
        old=self.work/'simulator'/startup.OLD;old.mkdir(parents=True)
        for name in ('replay.json','result.json'):
            raw=(Path(__file__).parent/'fixtures'/('original-'+name)).read_bytes()
            path=old/('main-app-only/launch-smoke/result.json' if name=='result.json' else name)
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            target=self.replay/('main-app-only/launch-smoke/result.json' if name=='result.json' else name)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        (self.replay/'cleanup.json').write_text(json.dumps({'schema':1,'owned_device_deleted':False,
            'owned_uuid_absence_verified':False,'exact_owned_device_only':True}))

    def tearDown(self):self.temp.cleanup()

    def finalize(self):
        with mock.patch.object(startup,'helper_modules',return_value={
            'replay_artifact':SimpleNamespace(prior_smoke_report=lambda value:value)}),contextlib.redirect_stdout(io.StringIO()):
            startup.finalize(self.work,self.replay,self.public,self.root)

    def test_optional_closed_diagnostic_is_hashed_without_changing_failed_startup(self):
        value=observation.blank();(self.replay/'installer-observation.json').write_text(json.dumps(value))
        self.finalize()
        self.assertEqual(json.loads((self.public/'installer-observation.json').read_text()),value)
        self.assertFalse(json.loads((self.public/'main-app-only/launch-smoke/result.json').read_text())['passed'])
        rows=(self.public/'SHA256SUMS').read_text().splitlines()
        self.assertTrue(any(row.endswith('  installer-observation.json') for row in rows))
        for row in rows:
            sha,name=row.split('  ',1);self.assertEqual(hashlib.sha256((self.public/name).read_bytes()).hexdigest(),sha)

    def test_invalid_optional_diagnostic_is_rejected_before_any_public_directory(self):
        for change in ({'private_canary':'SYNTHETIC_PRIVATE'},{'startup_verified':True},
                       {'post_deadline_elapsed_ms':1.0},{'source':'a'*40}):
            with self.subTest(change=change):
                value={**observation.blank(),**change}
                (self.replay/'installer-observation.json').write_text(json.dumps(value))
                with self.assertRaises(ValueError):self.finalize()
                self.assertFalse(self.public.exists())

    def test_oversized_symlink_and_nonregular_optional_public_are_rejected(self):
        target=self.replay/'installer-observation.json'
        target.write_bytes(b'x'*65537)
        with self.assertRaises(origins.Rejected):self.finalize()
        self.assertFalse(self.public.exists());target.unlink()
        other=self.root/'canary.json';other.write_text(json.dumps(observation.blank()));target.symlink_to(other)
        with self.assertRaises(origins.Rejected):self.finalize()
        self.assertFalse(self.public.exists());target.unlink();target.mkdir()
        with self.assertRaises(origins.Rejected):self.finalize()
        self.assertFalse(self.public.exists())

    def test_existing_public_canary_never_overwritten_by_diagnostic(self):
        self.public.mkdir();canary=self.public/'canary';canary.write_text('UNCHANGED')
        with self.assertRaises(origins.Rejected):self.finalize()
        self.assertEqual(canary.read_text(),'UNCHANGED')

    def test_absent_observer_preserves_existing_evidence_contract(self):
        self.finalize()
        self.assertFalse((self.public/'installer-observation.json').exists())
        self.assertNotIn('installer-observation.json',(self.public/'SHA256SUMS').read_text())

    def test_validated_optional_bytes_are_retained_across_source_mutation(self):
        path=self.replay/'installer-observation.json';original=json.dumps(observation.blank()).encode()
        path.write_bytes(original);real=startup.validated_optional_bytes
        def mutate(name,source):
            data=real(name,source);source.write_text('{"private_canary":"SYNTHETIC_PRIVATE_AFTER_VALIDATION"}')
            return data
        with mock.patch.object(startup,'validated_optional_bytes',side_effect=mutate):self.finalize()
        self.assertEqual((self.public/'installer-observation.json').read_bytes(),original)
        self.assertNotIn('PRIVATE_AFTER_VALIDATION',(self.public/'installer-observation.json').read_text())

    def test_fresh_archive_keeps_closed_optional_for_exact_tree_verification(self):
        archive=self.root/'new.zip';out=self.root/'extract'
        with zipfile.ZipFile(archive,'w') as z:
            for name in set(startup.PUBLIC)|{'SHA256SUMS'}:z.writestr(name,b'fixed-placeholder')
            z.writestr('installer-observation.json',json.dumps(observation.blank()))
        startup.extract_fresh_startup(archive,out)
        self.assertEqual(json.loads((out/'installer-observation.json').read_text()),observation.blank())

    def test_fresh_archive_rejects_unknown_diagnostic_fields(self):
        archive=self.root/'new.zip';out=self.root/'extract'
        with zipfile.ZipFile(archive,'w') as z:
            for name in set(startup.PUBLIC)|{'SHA256SUMS'}:z.writestr(name,b'fixed-placeholder')
            z.writestr('installer-observation.json',json.dumps({**observation.blank(),'private_canary':'SECRET'}))
        with self.assertRaises(ValueError):startup.extract_fresh_startup(archive,out)

    def test_failed_sdk_report_can_be_published_without_a_started_runtime(self):
        import sdk_bootstrap
        value=sdk_bootstrap.blank();value.update(stage='verifyHost',failure='host')
        (self.work/'sdk-bootstrap.json').write_text(json.dumps(value));shutil.rmtree(self.replay)
        self.finalize()
        self.assertEqual(json.loads((self.public/'sdk-bootstrap.json').read_text()),value)
        self.assertFalse((self.public/'main-app-only/launch-smoke/result.json').exists())
        self.assertIn('  sdk-bootstrap.json',(self.public/'SHA256SUMS').read_text())

    def test_unknown_sdk_fields_reject_before_creating_public_directory(self):
        import sdk_bootstrap
        value={**sdk_bootstrap.blank(),'private_canary':'SYNTHETIC_PRIVATE'}
        (self.work/'sdk-bootstrap.json').write_text(json.dumps(value));shutil.rmtree(self.replay)
        with self.assertRaises(ValueError):self.finalize()
        self.assertFalse(self.public.exists())

    def test_successful_sdk_setup_cannot_substitute_for_missing_application_runtime(self):
        import sdk_bootstrap
        value=sdk_bootstrap.blank();value.update(stage='complete',success=True,host_verified=True,
            version_verified=True,initialization_attempted=True,initialization_completed=True,
            initialization_exit_code=0,after_completed=True,after_exit_code=0)
        sdk_bootstrap.validate_report(value)
        (self.work/'sdk-bootstrap.json').write_text(json.dumps(value));shutil.rmtree(self.replay)
        with self.assertRaises(origins.Rejected):self.finalize()
        self.assertFalse(self.public.exists())

    def test_missing_runtime_gate_and_public_sdk_use_same_retained_bytes(self):
        import sdk_bootstrap
        failed=sdk_bootstrap.blank();failed.update(stage='verifyHost',failure='host')
        success=sdk_bootstrap.blank();success.update(stage='complete',success=True,host_verified=True,
            version_verified=True,initialization_attempted=True,initialization_completed=True,
            initialization_exit_code=0,after_completed=True,after_exit_code=0)
        sdk_bootstrap.validate_report(success)
        path=self.work/'sdk-bootstrap.json';path.write_text(json.dumps(failed));shutil.rmtree(self.replay)
        original=startup.validated_optional_bytes;calls=[]
        def change(name,source):
            raw=original(name,source);calls.append(name);source.write_text(json.dumps(success));return raw
        with mock.patch.object(startup,'validated_optional_bytes',side_effect=change):self.finalize()
        self.assertEqual(calls,['sdk-bootstrap.json'])
        self.assertEqual(json.loads((self.public/'sdk-bootstrap.json').read_text()),failed)


if __name__=='__main__':unittest.main()
