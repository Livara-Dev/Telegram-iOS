# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Owned metadata fixtures only; no network/build operations.
import copy
import json
from pathlib import Path
import unittest
import resume_origins as r


def run():
    repo = {'id': r.REPOSITORY_ID, 'full_name': r.REPOSITORY}
    return {'id':r.RUN,'head_sha':r.TOOLING,'path':r.WORKFLOW,'head_branch':r.BRANCH,
            'event':'push','status':'completed','conclusion':'failure','run_attempt':1,
            'repository':dict(repo),'head_repository':dict(repo)}


def jobs():
    contract=json.loads(Path(r.__file__).with_name('original-step-contract.json').read_text())
    identities={'qualification':r.QUALIFICATION_JOB,'unsigned-device-build':r.DEVICE_JOB,
                'application-build':r.SIMULATOR_JOB,'package':r.PACKAGE_JOB}
    outcomes={'qualification':'success','unsigned-device-build':'success','application-build':'failure','package':'skipped'}
    rows=[{'id':identities[name],'name':name,'run_id':r.RUN,'head_sha':r.TOOLING,'head_branch':r.BRANCH,
           'status':'completed','conclusion':outcomes[name],'run_attempt':1,
           'steps':[{'number':n,'name':label,'status':'completed','conclusion':outcome} for n,label,outcome in steps]}
          for name,steps in contract.items()]
    return {'total_count':len(rows),'jobs':rows}


def artifact(role):
    identity,name,digest=r.ARTIFACTS[role]
    value={'id':identity,'name':name,'digest':'sha256:'+digest,'expired':False,'size_in_bytes':1024,
           'workflow_run':{'id':r.NATIVE_RUN if role=='native' else r.RUN,
             'head_sha':r.SOURCE if role=='native' else r.TOOLING,
             'repository_id':r.REPOSITORY_ID,'head_repository_id':r.REPOSITORY_ID}}
    return {'total_count':1,'artifacts':[value]}


class FixedRecoveryTests(unittest.TestCase):
    def test_actual_failed_graph_remains_failed_in_typed_receipt(self):
        receipt=r.original_graph(run(),jobs())
        self.assertEqual(receipt['original_conclusion'],'failure')
        self.assertEqual(receipt['simulator_job_conclusion'],'failure')
        self.assertFalse(receipt['original_startup_passed'])
        self.assertFalse(receipt['application_rebuilt'])
        self.assertTrue(receipt['simulator_compile_step_passed'])
        self.assertEqual(receipt['original_package_conclusion'],'skipped')

    def test_original_must_be_final_fixed_attempt_and_origin(self):
        for field,value in [('id',1),('head_sha',r.SOURCE),('path','.github/workflows/other.yml'),
             ('head_branch','main'),('event','workflow_dispatch'),('status','in_progress'),
             ('conclusion','success'),('conclusion','cancelled'),('run_attempt',2),('run_attempt',True)]:
            with self.subTest(field=field,value=value),self.assertRaises(r.Rejected):
                r.original_graph({**run(),field:value},jobs())

    def test_fork_repository_and_unknown_job_cannot_supply_evidence(self):
        wrong=run();wrong['head_repository']={'id':1,'full_name':'another/fork'}
        with self.assertRaises(r.Rejected):r.original_graph(wrong,jobs())
        wrong=jobs();wrong['jobs'][0]['name']='unreviewed'
        with self.assertRaises(r.Rejected):r.original_graph(run(),wrong)

    def test_all_job_ids_attempts_sources_and_completed_states_are_exact(self):
        for index in range(4):
            for field,value in [('id',1),('head_sha',r.SOURCE),('run_id',1),('run_attempt',2),
                                ('head_branch','main'),('status','in_progress')]:
                changed=jobs();changed['jobs'][index][field]=value
                with self.subTest(index=index,field=field),self.assertRaises(r.Rejected):
                    r.original_graph(run(),changed)

    def test_only_startup_can_fail_and_only_later_two_steps_can_skip(self):
        for index,job in enumerate(jobs()['jobs']):
            if job['name']=='application-build':break
        for step_index,step in enumerate(job['steps']):
            changed=jobs()
            changed['jobs'][index]['steps'][step_index]['conclusion']='success' if step['conclusion']!='success' else 'failure'
            with self.subTest(step=step['name']),self.assertRaises(r.Rejected):r.original_graph(run(),changed)
        changed=jobs();changed['jobs'][index]['steps'].append({'number':999,'name':'Unreviewed step','status':'completed','conclusion':'success'})
        with self.assertRaises(r.Rejected):r.original_graph(run(),changed)

    def test_original_package_cannot_have_executed_or_be_claimed_successful(self):
        changed=jobs();row=next(j for j in changed['jobs'] if j['name']=='package');row['conclusion']='success'
        with self.assertRaises(r.Rejected):r.original_graph(run(),changed)
        row['conclusion']='skipped';row['steps']=[{'number':1,'name':'Package','status':'completed','conclusion':'success'}]
        with self.assertRaises(r.Rejected):r.original_graph(run(),changed)

    def test_fixed_artifact_id_name_source_and_full_digest_binding(self):
        for role in r.ARTIFACTS:
            self.assertEqual(r.artifact(role,artifact(role))['id'],r.ARTIFACTS[role][0])
            for field,value in [('id',1),('name','arbitrary'),('digest','sha256:'+'a'*64),
                                ('expired',True),('size_in_bytes',2*1024**3)]:
                altered=artifact(role);altered['artifacts'][0][field]=value
                with self.subTest(role=role,field=field),self.assertRaises(r.Rejected):r.artifact(role,altered)
            for field,value in [('id',1),('head_sha','a'*40),('repository_id',1),('head_repository_id',1)]:
                altered=artifact(role);altered['artifacts'][0]['workflow_run'][field]=value
                with self.subTest(role=role,origin=field),self.assertRaises(r.Rejected):r.artifact(role,altered)

    def test_artifact_inventory_must_be_complete_and_unambiguous(self):
        value=artifact('simulator');value['artifacts']*=2;value['total_count']=2
        with self.assertRaises(r.Rejected):r.artifact('simulator',value)
        value=artifact('simulator');value['total_count']=2
        with self.assertRaises(r.Rejected):r.artifact('simulator',value)

    def test_request_allows_only_fixed_readonly_resume_event(self):
        env={'GITHUB_REPOSITORY':r.REPOSITORY,'GITHUB_EVENT_NAME':'push','GITHUB_REF':r.RESUME_REF,'GITHUB_RUN_ATTEMPT':'1'}
        payload={'schema':1,'original_run':r.RUN,'source':r.SOURCE,'nonce':'reviewed-001'}
        self.assertEqual(r.request(json.dumps(payload).encode(),env),payload)
        for field,value in [('schema',True),('original_run',1),('source','a'*40),('nonce','../unsafe'),('extra','x')]:
            with self.subTest(field=field),self.assertRaises(r.Rejected):r.request(json.dumps({**payload,field:value}).encode(),env)
        for field,value in [('GITHUB_REPOSITORY','other/repo'),('GITHUB_EVENT_NAME','workflow_dispatch'),('GITHUB_REF','refs/heads/main'),('GITHUB_RUN_ATTEMPT','2')]:
            with self.subTest(event=field),self.assertRaises(r.Rejected):r.request(json.dumps(payload).encode(),{**env,field:value})

    def test_json_is_bounded_duplicate_and_nonfinite_closed(self):
        for raw in [b'',b' '*1048577,b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'['*1100+b']'*1100]:
            with self.subTest(raw=raw[:20]),self.assertRaises(r.Rejected):r.document(raw)

    def test_actual_original_failure_fingerprint_is_install_not_black_screen(self):
        folder=Path(__file__).parent/'fixtures'
        replay=(folder/'original-replay.json').read_bytes();result=(folder/'original-result.json').read_bytes()
        receipt=r.original_failure(replay,result)
        self.assertEqual(receipt['application_launch_attempts'],0)
        self.assertFalse(receipt['application_runtime_failure_established'])
        self.assertEqual(receipt['cleanup_failed_phases'],['shutdownDevice','deleteDevice'])
        for first,second in [(replay+b' ',result),(replay,result+b' '),
                             (replay,result.replace(b'commandTimedOut',b'otherFailure'))]:
            with self.assertRaises(r.Rejected):r.original_failure(first,second)


class NewStartupOriginTests(unittest.TestCase):
    def fixtures(self):
        current=run();current.update(id=50000000000,head_sha='a'*40,path=r.RESUME_WORKFLOW,
            head_branch=r.RESUME_REF.removeprefix('refs/heads/'),status='in_progress',conclusion=None)
        job={'id':50001,'name':r.STARTUP_JOB,'run_id':current['id'],'head_sha':current['head_sha'],
             'status':'completed','conclusion':'success','run_attempt':1,
             'steps':[{'number':1,'name':r.STARTUP_STEP,'status':'completed','conclusion':'success'}]}
        listing={'total_count':2,'jobs':[job,{'id':50002,'name':'package','status':'in_progress'}]}
        value=artifact('simulator')['artifacts'][0]
        value.update(id=60001,name=r.STARTUP_ARTIFACT,digest='sha256:'+'b'*64)
        value['workflow_run'].update(id=current['id'],head_sha=current['head_sha'])
        return current,listing,{'total_count':1,'artifacts':[value]}

    def check(self,current,listing,artifacts):
        return r.startup_origin(current,listing,artifacts,50000000000,'a'*40)

    def test_only_actual_completed_fresh_startup_job_can_supply_typed_receipt(self):
        receipt=self.check(*self.fixtures())
        self.assertEqual(receipt['source_run'],r.RUN)
        self.assertEqual(receipt['resume_run'],50000000000)
        self.assertEqual(receipt['resume_job_conclusion'],'success')
        self.assertFalse(receipt['application_recompiled'])
        for field,value in [('source_commit','a'*40),('source_job',1),('resume_attempt',True),
                            ('resume_run',r.RUN),('resume_job_conclusion','failure'),('release_qualified',True)]:
            with self.subTest(field=field),self.assertRaises(r.Rejected):r.validate_startup_origin({**receipt,field:value})

    def test_wrong_new_workflow_source_attempt_or_producer_cannot_qualify_package(self):
        for field,value in [('head_sha',r.SOURCE),('path',r.WORKFLOW),('run_attempt',2),
                            ('event','workflow_dispatch'),('conclusion','failure')]:
            current,listing,artifacts=self.fixtures();current[field]=value
            with self.subTest(run=field),self.assertRaises(r.Rejected):self.check(current,listing,artifacts)
        for field,value in [('conclusion','failure'),('status','in_progress'),('run_attempt',2),('run_attempt',True)]:
            current,listing,artifacts=self.fixtures();listing['jobs'][0][field]=value
            with self.subTest(job=field),self.assertRaises(r.Rejected):self.check(current,listing,artifacts)
        current,listing,artifacts=self.fixtures();listing['jobs'][0]['steps'][0]['conclusion']='failure'
        with self.assertRaises(r.Rejected):self.check(current,listing,artifacts)

    def test_fresh_artifact_is_bound_to_actual_current_workflow_run_and_digest(self):
        for field,value in [('name','arbitrary'),('expired',True),('digest','bad')]:
            current,listing,artifacts=self.fixtures();artifacts['artifacts'][0][field]=value
            with self.subTest(field=field),self.assertRaises(r.Rejected):self.check(current,listing,artifacts)
        for field,value in [('id',r.RUN),('head_sha',r.TOOLING),('repository_id',1)]:
            current,listing,artifacts=self.fixtures();artifacts['artifacts'][0]['workflow_run'][field]=value
            with self.subTest(origin=field),self.assertRaises(r.Rejected):self.check(current,listing,artifacts)


if __name__=='__main__':unittest.main()
