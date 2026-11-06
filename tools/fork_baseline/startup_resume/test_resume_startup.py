# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Portable owned artifacts/receipts only, never a startup claim.
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile
import resume_origins as r
import resume_startup as startup


def evidence():
    proof={'mode':'main_app_only','startup_variant':'main-app-only','passed':True,'phase':'complete',
           'source_simulator_ipa_sha256':r.SIMULATOR_IPA_SHA256,'extensions_removed':6,
           'private_control_only':True,'private_mode_control':'explicitFreshPinnedSelection',
           'automatic_control_run':False,'automatic_fallback_verified':False,'automatic_group_selection_observed':False,
           'app_group_entitlement_present_before':False,'app_group_entitlement_absent_after':True,
           'framework_files_unchanged':77,'retained_resource_files_unchanged':855,
           'pinned_private_control_attempted':True,'source_codesign_verified':True,'main_code_unchanged':True,
           'source_ipa_unchanged':True,'main_app_only_control_passed':True,
           'application_recompiled':False,'physical_device_verified':False,
           'normal_control_passed':False,'no_app_groups_control_attempted':False,
           'simulator_device_type':'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro',
           'simulator_runtime':'com.apple.CoreSimulator.SimRuntime.iOS-27-0'}
    launches=[{'attempt':i,'storage_kind':'private','private_account_store_present':True,
               'marker':{'stage':'authorizationVisible','failure':None},
               'screenshot':{'nonblank':True,'width':1178,'height':2556}} for i in (1,2)]
    launches[1]['private_account_store_stable_after_restart']=True
    result={'passed':True,'phase':'complete','failure':None,'storage_test_setup':'freshPinnedPrivate',
            'private_selection_seeded_before_launch':True,'private_account_store_absent_before_launch':True,
            'screenshot_checker':{'compiled':True,'self_test_passed':True,'self_test_cases':3},
            'recording_return_code':0,'launches':launches}
    cleanup={'schema':1,'owned_device_deleted':True,'owned_uuid_absence_verified':True,'exact_owned_device_only':True}
    return proof,result,cleanup


class FreshRuntimeGateTests(unittest.TestCase):
    def test_strict_private_two_launches_and_independent_cleanup_are_mandatory(self):
        proof,result,cleanup=evidence()
        self.assertIs(startup.validate_fresh(proof,result,cleanup,lambda value:value),result)
        result['cleanup_failures']=[]
        startup.validate_fresh(proof,result,cleanup,lambda value:value)
        for key in cleanup:
            bad=copy.deepcopy(cleanup);bad[key]=False
            with self.subTest(cleanup=key),self.assertRaises(r.Rejected):
                startup.validate_fresh(proof,result,bad,lambda value:value)

    def test_new_runtime_cannot_reuse_failed_missing_or_changed_source_proof(self):
        proof,result,cleanup=evidence()
        for key,value in [('passed',False),('source_simulator_ipa_sha256','a'*64),('extensions_removed',5),
             ('source_codesign_verified',False),('main_code_unchanged',False),('source_ipa_unchanged',False),
             ('framework_files_unchanged',76),('retained_resource_files_unchanged',854),
             ('application_recompiled',True),('physical_device_verified',True),('private_control_only',False),
             ('automatic_fallback_verified',True),('private_mode_control','automaticSelection')]:
            with self.subTest(field=key),self.assertRaises(r.Rejected):
                startup.validate_fresh({**proof,key:value},result,cleanup,lambda value:value)

    def test_replay_count_proofs_are_typed_integers_not_equal_floats_or_booleans(self):
        proof,result,cleanup=evidence()
        for key in ('extensions_removed','framework_files_unchanged','retained_resource_files_unchanged'):
            for value in (float(proof[key]), True, False, str(proof[key])):
                with self.subTest(key=key,value=value),self.assertRaises(r.Rejected):
                    startup.validate_fresh({**proof,key:value},result,cleanup,lambda value:value)

    def test_replay_success_and_partial_failures_reject_private_or_unknown_fields(self):
        proof,result,cleanup=evidence()
        partial={key:proof[key] for key in ('mode','physical_device_verified','startup_variant','private_control_only',
            'application_recompiled','passed','normal_control_passed','no_app_groups_control_attempted',
            'source_codesign_verified','phase','simulator_device_type','simulator_runtime')}
        partial.update(passed=False,phase='verifyCodeSignature',source_codesign_verified=False)
        self.assertIs(startup.validate_replay_schema(partial),partial)
        for candidate in (proof,partial):
            for changes in ({'private_canary':'SYNTHETIC-PRIVATE-CANARY'},{'phase':'/private/path'},
                            {'source_codesign_verified':1},{'source_simulator_ipa_sha256':'not-source'},
                            {'automatic_storage_kind':'unexpected-private-value'}):
                with self.subTest(changes=changes),self.assertRaises(r.Rejected):
                    startup.validate_replay_schema({**candidate,**changes})

    def test_actual_failed_replay_matches_closed_fields_without_normalization(self):
        path=Path(__file__).parent/'fixtures/original-replay.json'
        raw=path.read_bytes();value=json.loads(raw)
        self.assertIs(startup.validate_replay_schema(value),value)
        self.assertEqual(path.read_bytes(),raw)

    def test_missing_second_controller_screenshot_storage_or_recorder_rejects(self):
        proof,result,cleanup=evidence()
        for key,value in [('passed',False),('phase','installApplication'),('launches',[]),
                          ('recording_return_code',1),('cleanup_failures',[{'code':'timeout'}]),
                          ('private_selection_seeded_before_launch',False)]:
            with self.subTest(field=key),self.assertRaises(r.Rejected):
                startup.validate_fresh(proof,{**result,key:value},cleanup,lambda value:value)
        for changes in [dict(storage_kind='group'),dict(private_account_store_present=False),
                        dict(private_account_store_stable_after_restart=False),dict(marker={'stage':'windowCreated','failure':None}),
                        dict(screenshot={'nonblank':False})]:
            bad=copy.deepcopy(result);bad['launches'][1].update(changes)
            with self.subTest(launch=changes),self.assertRaises(r.Rejected):
                startup.validate_fresh(proof,bad,cleanup,lambda value:value)

    def test_retained_component_receipt_is_not_original_job_success(self):
        receipt=r.source_build_origin()
        self.assertEqual(r.validate_source_build_origin(receipt),receipt)
        self.assertEqual(receipt['producing_job_conclusion'],'failure')
        self.assertFalse(receipt['original_startup_passed'])
        for key,value in [('producing_job_conclusion','success'),('source_ipa_sha256','a'*64),
                          ('producing_compile_step_passed',1),('schema',True),('application_recompiled',True)]:
            with self.subTest(field=key),self.assertRaises(r.Rejected):
                r.validate_source_build_origin({**receipt,key:value})


class SafeExtractionTests(unittest.TestCase):
    def create(self,path,entries):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            with zipfile.ZipFile(path,'w') as archive:
                for name,data,mode in entries:
                    info=zipfile.ZipInfo(name)
                    if mode:info.external_attr=mode<<16
                    archive.writestr(info,data)

    def test_extract_only_explicit_regular_selected_files_into_new_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'artifact.zip'
            self.create(archive,[('allowed',b'owned',0),('not-selected',b'private-not-copied',0)])
            startup.extract_selected(archive,root/'output',{'allowed'})
            self.assertEqual((root/'output/allowed').read_bytes(),b'owned')
            self.assertFalse((root/'output/not-selected').exists())
            with self.assertRaises(r.Rejected):startup.extract_selected(archive,root/'output',{'allowed'})

    def test_all_paths_are_checked_even_unselected_traversal_alias_links(self):
        for extra in [('..\x2fescape',b'x',0),('/absolute',b'x',0),('bad\\path',b'x',0),
                      ('allowed',b'x',0),('ALLOWED',b'x',0),('link',b'/outside',stat.S_IFLNK|0o777)]:
            with self.subTest(name=extra[0]),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'artifact.zip'
                self.create(archive,[('allowed',b'owned',0),extra])
                with self.assertRaises(r.Rejected):startup.extract_selected(archive,root/'output',{'allowed'})
                self.assertFalse((root/'output').exists())

    def test_finalizer_refuses_unknown_proof_fields_before_public_directory_exists(self):
        for passed in (True,False):
            with self.subTest(passed=passed),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);work=root/'work';replay=root/'replay';public=root/'public'
                work.mkdir();replay.mkdir()
                (work/'source-build-origin.json').write_text(json.dumps(r.source_build_origin()))
                proof,_,_=evidence();proof.update(passed=passed,private_canary='SYNTHETIC-PRIVATE-CANARY')
                if not passed:proof['phase']='explicitPrivateControl'
                (replay/'replay.json').write_text(json.dumps(proof))
                modules={'replay_artifact':SimpleNamespace(prior_smoke_report=lambda value:value)}
                with patch.object(startup,'helper_modules',return_value=modules):
                    with self.assertRaises(r.Rejected):startup.finalize(work,replay,public,root)
                self.assertFalse(public.exists())


class WorkflowScopeTests(unittest.TestCase):
    def test_supported_sdk_initialization_precedes_single_opt_in_startup_without_tokens(self):
        folder=Path(__file__).resolve().parent
        path=folder/'.github/workflows/resume-qualified-device-delivery.yml'
        if not path.exists():path=Path(__file__).resolve().parents[3]/'.github/workflows/resume-qualified-device-delivery.yml'
        text=path.read_text();name='Initialize required Xcode system components without building the application'
        step=text.split('      - name: '+name+'\n',1)[1].split('      - name:',1)[0]
        self.assertIn('env -i HOME="$HOME" PATH="$PATH" TMPDIR="$RUNNER_TEMP"',step)
        self.assertIn('DEVELOPER_DIR=/Applications/Xcode_27.1.app/Contents/Developer',step)
        self.assertIn('sdk_bootstrap.py',step);self.assertIn('--output work/sdk-bootstrap.json',step)
        for token in ('github.token','GH_TOKEN','secrets.','simctl','bazel','baseline.py'):
            self.assertNotIn(token,step)
        self.assertLess(text.index('resume_startup.py prepare'),text.index('      - name: '+name))
        self.assertLess(text.index('      - name: '+name),text.index('      - name: '+r.STARTUP_STEP))
        self.assertEqual(text.count('--installer-diagnostics'),1)
        self.assertEqual(text.count('isolated_startup.py'),1)

    def test_two_jobs_no_application_build_or_credential_at_runtime(self):
        folder=Path(__file__).resolve().parent
        workflow=folder/'.github/workflows/resume-qualified-device-delivery.yml'
        if not workflow.exists():workflow=Path(__file__).resolve().parents[3]/'.github/workflows/resume-qualified-device-delivery.yml'
        source=workflow.read_text()
        self.assertIn('branches: [ci/qualified-device-startup-resume]',source)
        self.assertIn('paths: [ci/qualified-device-startup-resume-request.json]',source)
        self.assertIn('  contents: read\n  actions: read',source)
        self.assertIn('cancel-in-progress: false',source)
        self.assertNotIn('workflow_dispatch:',source)
        self.assertNotIn('secrets.',source)
        self.assertNotIn('baseline.py',source)
        self.assertNotIn('bazel ',source)
        self.assertNotIn('COMPILE_STEP_OUTCOME',source)
        runtime=source.split('      - name: '+r.STARTUP_STEP+'\n',1)[1].split('      - name:',1)[0]
        self.assertNotIn('GH_TOKEN',runtime);self.assertNotIn('github.token',runtime);self.assertNotIn('env:',runtime)
        self.assertIn('isolated_startup.py',runtime)
        self.assertIn('env -i HOME="$HOME" PATH="$PATH" TMPDIR="$TMPDIR"',runtime)
        self.assertIn('DEVELOPER_DIR=/Applications/Xcode_27.1.app/Contents/Developer',runtime)
        self.assertIn('export TMPDIR',runtime)
        self.assertLess(runtime.index('export TMPDIR'),runtime.index('python3 tooling/'))
        self.assertIn("os.environ.get('RUNNER_TEMP', '')",runtime)
        self.assertIn('supplied.resolve(strict=True)',runtime)
        self.assertIn('info.st_uid != os.getuid()',runtime)
        self.assertIn('info.st_mode & 0o022',runtime)
        self.assertIn("if: always() && steps.public_evidence.outcome == 'success'",source)
        self.assertIn('needs: retained-startup',source)
        self.assertIn("if os.environ.get('NEW_STARTUP_VERIFIED') != 'true'",source)

    def test_runtime_temp_setup_resolves_only_safe_owned_ancestor_alias(self):
        folder=Path(__file__).resolve().parent
        workflow=folder/'.github/workflows/resume-qualified-device-delivery.yml'
        if not workflow.exists():workflow=Path(__file__).resolve().parents[3]/'.github/workflows/resume-qualified-device-delivery.yml'
        text=workflow.read_text()
        program=textwrap.dedent(text.split('TMPDIR="$(python3 - <<\'PY\'\n',1)[1].split('\n          PY\n',1)[0])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);actual=root/'real';actual.mkdir();actual.chmod(0o700)
            runner=actual/'runner';runner.mkdir();runner.chmod(0o700)
            alias=root/'alias';alias.symlink_to(actual,target_is_directory=True)
            output=io.StringIO()
            with patch.dict(os.environ,{'RUNNER_TEMP':str(alias/'runner')}),contextlib.redirect_stdout(output):
                exec(compile(program,'<workflow-canonical-temp>','exec'),{})
            self.assertEqual(output.getvalue().strip(),str(runner.resolve()))
            for unsafe in [str(alias),'relative',str(root/'missing'),'bad\npath']:
                with self.subTest(unsafe=unsafe),patch.dict(os.environ,{'RUNNER_TEMP':unsafe}),self.assertRaises((SystemExit,OSError)):
                    exec(compile(program,'<workflow-canonical-temp>','exec'),{})
            runner.chmod(0o777)
            with patch.dict(os.environ,{'RUNNER_TEMP':str(runner)}),self.assertRaises(SystemExit):
                exec(compile(program,'<workflow-canonical-temp>','exec'),{})


if __name__=='__main__':unittest.main()
