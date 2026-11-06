# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Fixed failed-delivery recovery guards; no API writes/builds.
"""Pure origin checks for one artifact-only resume; never converts failure to success."""
import json
import re
from pathlib import Path

REPOSITORY = 'Livara-Dev/Telegram-iOS'
REPOSITORY_ID = 1445292587
SOURCE = '5d241267735b3958c225681d173853568bab705d'
TOOLING = '2f68bca8027980250bf8f9b0328bb1e063fc55c1'
RUN = 42279866706
ATTEMPT = 1
WORKFLOW = '.github/workflows/unsigned-device-delivery.yml'
BRANCH = 'ci/qualified-device-delivery'
RESUME_REF = 'refs/heads/ci/qualified-device-startup-resume'
RESUME_WORKFLOW = '.github/workflows/resume-qualified-device-delivery.yml'
REQUEST = 'ci/qualified-device-startup-resume-request.json'
NATIVE_RUN = 42272821047
NATIVE_SOURCE = SOURCE
NATIVE_ARTIFACT = 13533034081
NATIVE_SHA256 = '7b67ad73d3338a48fb1e04b009db6c2650b9109302a573edd49c0ae458f2790c'
QUALIFICATION_JOB = 128105738352
DEVICE_JOB = 128105899974
SIMULATOR_JOB = 128105900214
PACKAGE_JOB = 128163674895
ARTIFACTS = {
    'device': (13539444219, 'device-delivery-device-' + SOURCE,
               '48a9c84a22cfffdc8c5ab5bdfdc2d60b3a84c1df1c1c34313b61594a0bd84cf9'),
    'simulator': (13542840666, 'device-delivery-simulator-' + SOURCE,
                  '89407b99ce5e76d3314dcc873950b754756e851eda760f063cb4ee02c4ba833b'),
    'native': (NATIVE_ARTIFACT, 'archive-integration-tests-' + SOURCE, NATIVE_SHA256),
}
SIM_COMPILE = 'Build the full simulator app and run TextFormat regression tests'
DEVICE_COMPILE = 'Compile unsigned device package'
FAILED_STARTUP = 'Verify source-matched main-app startup'
SIM_SKIPPED = {'Verify active fork branding in the simulator IPA',
               'Record exact application and workflow build origins'}


class Rejected(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Rejected(reason)


def document(raw, maximum=1024*1024):
    require(isinstance(raw, bytes) and 0 < len(raw) <= maximum, 'jsonBounds')
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicateJsonKey')
            value[key] = item
        return value
    def nonfinite(value):
        raise Rejected('nonfiniteJson')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
        require(isinstance(value, dict), 'jsonObjectRequired')
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Rejected('invalidJson') from None


def request(raw, environment):
    require(environment.get('GITHUB_REPOSITORY') == REPOSITORY
            and environment.get('GITHUB_EVENT_NAME') == 'push'
            and environment.get('GITHUB_REF') == RESUME_REF
            and environment.get('GITHUB_RUN_ATTEMPT') == '1', 'unsupportedRequestOrigin')
    value = document(raw, 1024)
    require(isinstance(value, dict) and set(value) == {'schema', 'original_run', 'source', 'nonce'}
            and type(value['schema']) is int and value['schema'] == 1
            and type(value['original_run']) is int and value['original_run'] == RUN
            and value['source'] == SOURCE and isinstance(value['nonce'], str)
            and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', value['nonce']), 'requestSchema')
    return value


def repository_origin(run):
    for key in ('repository', 'head_repository'):
        require(isinstance(run.get(key), dict) and run[key].get('full_name') == REPOSITORY
                and type(run[key].get('id')) is int and run[key]['id'] == REPOSITORY_ID,
                'repositoryOrigin')


def original_graph(run, listing):
    require(isinstance(run, dict) and run.get('id') == RUN and run.get('head_sha') == TOOLING
            and run.get('path') == WORKFLOW and run.get('head_branch') == BRANCH
            and run.get('event') == 'push' and run.get('status') == 'completed'
            and run.get('conclusion') == 'failure' and type(run.get('run_attempt')) is int
            and run['run_attempt'] == ATTEMPT, 'originalRunOrigin')
    repository_origin(run)
    require(isinstance(listing, dict) and isinstance(listing.get('jobs'), list)
            and listing.get('total_count') == len(listing['jobs']) == 4, 'jobInventory')
    rows = {job.get('name'): job for job in listing['jobs']}
    require(set(rows) == {'qualification', 'unsigned-device-build', 'application-build', 'package'}, 'jobNames')
    for job in rows.values():
        require(type(job.get('id')) is int and job['id'] > 0 and job.get('run_id') == RUN
                and job.get('head_sha') == TOOLING and job.get('status') == 'completed'
                and type(job.get('run_attempt')) is int and job['run_attempt'] == ATTEMPT
                and job.get('head_branch') == BRANCH, 'jobOrigin')
    require(rows['unsigned-device-build']['id'] == DEVICE_JOB and rows['application-build']['id'] == SIMULATOR_JOB
            and rows['package']['id'] == PACKAGE_JOB and rows['qualification']['id'] == QUALIFICATION_JOB, 'jobIdentity')
    for name in ('qualification', 'unsigned-device-build'):
        require(rows[name].get('conclusion') == 'success' and isinstance(rows[name].get('steps'), list)
                and rows[name]['steps'] and all(s.get('status') == 'completed' and s.get('conclusion') == 'success'
                                             for s in rows[name]['steps']), 'successfulProducerRequired')
    require(rows['application-build'].get('conclusion') == 'failure'
            and rows['package'].get('conclusion') == 'skipped' and not rows['package'].get('steps'), 'originalGraphShape')
    steps = rows['application-build'].get('steps')
    require(isinstance(steps, list) and steps, 'simulatorStepsMissing')
    require(len({s.get('number') for s in steps}) == len(steps)
            and all(type(s.get('number')) is int and s['number'] > 0 and s.get('status') == 'completed' for s in steps),
            'simulatorStepIdentity')
    failed = [s for s in steps if s.get('conclusion') == 'failure']
    require(len(failed) == 1 and failed[0].get('name') == FAILED_STARTUP, 'unexpectedOriginalFailure')
    startup_number = failed[0]['number']
    compiled = [s for s in steps if s.get('name') == SIM_COMPILE]
    require(len(compiled) == 1 and compiled[0].get('conclusion') == 'success'
            and compiled[0]['number'] < startup_number, 'simulatorCompileNotPassed')
    skipped = [s for s in steps if s.get('conclusion') == 'skipped']
    require({s.get('name') for s in skipped} == SIM_SKIPPED and len(skipped) == 2
            and all(s['number'] > startup_number for s in skipped), 'unexpectedOriginalSkippedStep')
    require(all(s.get('conclusion') in {'success', 'failure', 'skipped'} for s in steps), 'unknownOriginalStepOutcome')
    device_compile = [s for s in rows['unsigned-device-build']['steps'] if s.get('name') == DEVICE_COMPILE]
    require(len(device_compile) == 1 and device_compile[0].get('conclusion') == 'success', 'deviceCompileNotPassed')
    contract = document(Path(__file__).with_name('original-step-contract.json').read_bytes())
    require({name: [[s['number'], s['name'], s['conclusion']] for s in job.get('steps', [])]
             for name, job in rows.items()} == contract, 'originalStepContract')
    return {'schema': 1, 'original_run': RUN, 'original_attempt': ATTEMPT, 'original_conclusion': 'failure',
            'workflow': WORKFLOW, 'tooling_commit': TOOLING, 'application_source_commit': SOURCE,
            'device_job': DEVICE_JOB, 'device_job_conclusion': 'success',
            'simulator_job': SIMULATOR_JOB, 'simulator_job_conclusion': 'failure',
            'simulator_compile_step_passed': True, 'original_startup_passed': False,
            'original_package_conclusion': 'skipped', 'application_rebuilt': False}


def artifact(role, listing):
    identity, name, expected_sha = ARTIFACTS[role]
    run_id, source = (NATIVE_RUN, SOURCE) if role == 'native' else (RUN, TOOLING)
    require(isinstance(listing, dict) and isinstance(listing.get('artifacts'), list)
            and listing.get('total_count') == len(listing['artifacts']) <= 100, 'artifactInventory')
    matches = [a for a in listing['artifacts'] if a.get('name') == name]
    require(len(matches) == 1, 'artifactMissingOrAmbiguous')
    value = matches[0];origin = value.get('workflow_run', {})
    maximum = 64*1024**2 if role == 'native' else 1024**3
    require(type(value.get('id')) is int and value['id'] == identity and value.get('expired') is False
            and type(value.get('size_in_bytes')) is int and 0 < value['size_in_bytes'] <= maximum
            and value.get('digest') == 'sha256:' + expected_sha
            and origin.get('id') == run_id and origin.get('head_sha') == source
            and origin.get('repository_id') == REPOSITORY_ID and origin.get('head_repository_id') == REPOSITORY_ID,
            'artifactOriginOrDigest')
    return value

SIMULATOR_IPA_SHA256 = '01f32ba769f876c20bed6090c54a8066d27f09cc73d75d890e7f57c435219e0a'
OLD_REPLAY_SHA256 = 'a7ae08823764d940a10b51860df415f8695eb1c5ee2b371a7aa43be6746dacd3'
OLD_RESULT_SHA256 = 'fa635e217b917d699b66ce3eda6c19430962100da3c246469fd2d709a79aa833'
STARTUP_ARTIFACT = 'startup-resume-5d241267-42279866706'
STARTUP_JOB = 'retained-startup'
STARTUP_STEP = 'Verify the retained application twice on a fresh owned simulator'


def original_failure(replay_bytes, result_bytes):
    import hashlib
    require(hashlib.sha256(replay_bytes).hexdigest() == OLD_REPLAY_SHA256
            and hashlib.sha256(result_bytes).hexdigest() == OLD_RESULT_SHA256, 'originalFailureFingerprint')
    replay, result = document(replay_bytes), document(result_bytes)
    require(replay.get('source_simulator_ipa_sha256') == SIMULATOR_IPA_SHA256
            and replay.get('source_codesign_verified') is True and replay.get('main_code_unchanged') is True
            and replay.get('passed') is False and replay.get('phase') == 'explicitPrivateControl'
            and replay.get('application_recompiled') is False and replay.get('extensions_removed') == 6,
            'originalReplayFailureShape')
    require(result.get('passed') is False and result.get('launches') == []
            and result.get('phase') == 'installApplication'
            and result.get('failure') == {'code':'commandTimedOut','phase':'installApplication',
                 'return_code':None,'timeout':True,'system_errors':[]}
            and [x.get('phase') for x in result.get('cleanup_failures', [])] == ['shutdownDevice','deleteDevice'],
            'originalInstallFailureShape')
    return {'schema':1,'original_startup_passed':False,'application_launch_attempts':0,
            'failure_phase':'installApplication','failure_code':'commandTimedOut',
            'cleanup_failed_phases':['shutdownDevice','deleteDevice'],
            'application_runtime_failure_established':False,'source_ipa_sha256':SIMULATOR_IPA_SHA256,
            'replay_sha256':OLD_REPLAY_SHA256,'result_sha256':OLD_RESULT_SHA256}


def validate_startup_origin(value):
    keys={'schema','repository','workflow','resume_run','resume_attempt','resume_tooling_commit',
          'resume_job','resume_job_name','resume_job_conclusion','resume_step_name','resume_step_conclusion',
          'artifact_id','artifact_sha256','source_commit','source_run','source_tooling_commit','source_job',
          'source_artifact_id','source_ipa_sha256','application_recompiled','physical_device_verified','release_qualified'}
    require(isinstance(value,dict) and set(value)==keys and type(value['schema']) is int and value['schema']==1
            and value['repository']==REPOSITORY and value['workflow']==RESUME_WORKFLOW
            and type(value['resume_run']) is int and value['resume_run']>0 and value['resume_run'] not in {RUN,NATIVE_RUN}
            and type(value['resume_attempt']) is int and value['resume_attempt']==1
            and isinstance(value['resume_tooling_commit'],str) and re.fullmatch(r'[0-9a-f]{40}',value['resume_tooling_commit'])
            and type(value['resume_job']) is int and value['resume_job']>0
            and value['resume_job_name']==STARTUP_JOB and value['resume_job_conclusion']=='success'
            and value['resume_step_name']==STARTUP_STEP and value['resume_step_conclusion']=='success'
            and type(value['artifact_id']) is int and value['artifact_id']>0
            and isinstance(value['artifact_sha256'],str) and re.fullmatch(r'[0-9a-f]{64}',value['artifact_sha256'])
            and value['source_commit']==SOURCE and value['source_run']==RUN and value['source_tooling_commit']==TOOLING
            and value['source_job']==SIMULATOR_JOB and value['source_artifact_id']==ARTIFACTS['simulator'][0]
            and value['source_ipa_sha256']==SIMULATOR_IPA_SHA256
            and all(value[k] is False for k in ('application_recompiled','physical_device_verified','release_qualified')),
            'newStartupOrigin')
    return value


def startup_origin(run, listing, artifacts, expected_run, expected_tooling):
    require(type(expected_run) is int and expected_run>0 and isinstance(expected_tooling,str)
            and re.fullmatch(r'[0-9a-f]{40}',expected_tooling), 'currentResumeCoordinates')
    require(isinstance(run,dict) and run.get('id')==expected_run and run.get('head_sha')==expected_tooling
            and run.get('path')==RESUME_WORKFLOW and run.get('head_branch')==RESUME_REF.removeprefix('refs/heads/')
            and run.get('event')=='push' and run.get('status') in {'in_progress','completed'}
            and run.get('conclusion') in {None,'success'} and type(run.get('run_attempt')) is int and run['run_attempt']==1,
            'currentResumeRunOrigin')
    repository_origin(run)
    require(isinstance(listing,dict) and isinstance(listing.get('jobs'),list)
            and listing.get('total_count')==len(listing['jobs'])==2, 'resumeJobInventory')
    rows={j.get('name'):j for j in listing['jobs']}
    require(set(rows)=={STARTUP_JOB,'package'},'resumeJobNames')
    job=rows[STARTUP_JOB]
    require(type(job.get('id')) is int and job['id']>0 and job.get('run_id')==expected_run
            and job.get('head_sha')==expected_tooling and type(job.get('run_attempt')) is int and job['run_attempt']==1
            and job.get('status')=='completed' and job.get('conclusion')=='success'
            and isinstance(job.get('steps'),list) and job['steps']
            and all(s.get('status')=='completed' and s.get('conclusion')=='success' for s in job['steps'])
            and len([s for s in job['steps'] if s.get('name')==STARTUP_STEP])==1,'resumeStartupNotSuccessful')
    require(isinstance(artifacts,dict) and isinstance(artifacts.get('artifacts'),list)
            and artifacts.get('total_count')==len(artifacts['artifacts'])<=100,'resumeArtifactInventory')
    matches=[a for a in artifacts['artifacts'] if a.get('name')==STARTUP_ARTIFACT]
    require(len(matches)==1,'resumeArtifactAmbiguous')
    item=matches[0];origin=item.get('workflow_run',{})
    require(type(item.get('id')) is int and item['id']>0 and item.get('expired') is False
            and type(item.get('size_in_bytes')) is int and 0<item['size_in_bytes']<=128*1024**2
            and isinstance(item.get('digest'),str) and re.fullmatch(r'sha256:[0-9a-f]{64}',item['digest'])
            and origin.get('id')==expected_run and origin.get('head_sha')==expected_tooling
            and origin.get('repository_id')==REPOSITORY_ID and origin.get('head_repository_id')==REPOSITORY_ID,
            'resumeArtifactOrigin')
    return validate_startup_origin({'schema':1,'repository':REPOSITORY,'workflow':RESUME_WORKFLOW,
        'resume_run':expected_run,'resume_attempt':1,'resume_tooling_commit':expected_tooling,
        'resume_job':job['id'],'resume_job_name':STARTUP_JOB,'resume_job_conclusion':'success',
        'resume_step_name':STARTUP_STEP,'resume_step_conclusion':'success',
        'artifact_id':item['id'],'artifact_sha256':item['digest'][7:],
        'source_commit':SOURCE,'source_run':RUN,'source_tooling_commit':TOOLING,'source_job':SIMULATOR_JOB,
        'source_artifact_id':ARTIFACTS['simulator'][0],'source_ipa_sha256':SIMULATOR_IPA_SHA256,
        'application_recompiled':False,'physical_device_verified':False,'release_qualified':False})

SIMULATOR_MANIFEST_SHA256 = 'cdd10ce85fbbbbd8802161645a5fd4cc0aa7fee70215a1ffdb6aadef768277fa'
BUILD_NUMBER = 1001


def source_build_origin():
    # This constructor is called only after original_graph, artifact, raw IPA,
    # source materials, manifest checksums and actual TextFormat evidence pass.
    # The receipt is not independent authority: package must recheck those inputs.
    return {'schema':1,'record_type':'retainedBuildComponent','repository':REPOSITORY,
        'application_source_commit':SOURCE,'producing_run':RUN,'producing_run_attempt':ATTEMPT,
        'producing_workflow':WORKFLOW,'producing_tooling_commit':TOOLING,
        'producing_job':SIMULATOR_JOB,'producing_job_conclusion':'failure',
        'producing_compile_step':SIM_COMPILE,'producing_compile_step_passed':True,
        'producing_artifact_id':ARTIFACTS['simulator'][0],
        'producing_artifact_sha256':ARTIFACTS['simulator'][2],
        'source_manifest_sha256':SIMULATOR_MANIFEST_SHA256,'source_ipa_sha256':SIMULATOR_IPA_SHA256,
        'build_number':BUILD_NUMBER,'textformat':{'target':'//submodules/TextFormat:TextFormatTests',
            'native_passed_cases':270,'failed':0,'skipped':0,'actual_case_events_verified':True},
        'original_startup_passed':False,'original_failure_replay_sha256':OLD_REPLAY_SHA256,
        'original_failure_result_sha256':OLD_RESULT_SHA256,'source_license_materials_verified':True,
        'application_recompiled':False,'physical_device_verified':False,'release_qualified':False}


def same_typed_value(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return set(first) == set(second) and all(same_typed_value(first[k], second[k]) for k in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(same_typed_value(a, b) for a, b in zip(first, second))
    return first == second


def validate_source_build_origin(value):
    require(same_typed_value(value, source_build_origin()), 'retainedBuildOrigin')
    return value
