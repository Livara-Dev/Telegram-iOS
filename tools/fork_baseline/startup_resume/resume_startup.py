#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Created 2026-11-06. Artifact-only metadata preparation/publication, never app build.
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import subprocess
import sys
import zipfile
import resume_origins as r

RAW = 'fork-baseline-simulator/'
OLD = 'fork-evidence/startup-main-app-only/'
RAW_NAMES = {'Telegram.ipa','manifest.json','SHA256SUMS','COPYING','LICENSE-NOTICE.md','CORRESPONDING-SOURCE.md'}
SELECTED = {RAW+n for n in RAW_NAMES} | {
    'fork-evidence/textformat/collection.json','fork-evidence/textformat/test.xml','fork-evidence/textformat/test.log',
    OLD+'replay.json',OLD+'main-app-only/launch-smoke/result.json'}
PUBLIC = ('replay.json','cleanup.json','source-build-origin.json','original-failure/replay.json','original-failure/result.json',
          'main-app-only/launch-smoke/result.json','main-app-only/launch-smoke/checker-compiler.log',
          'main-app-only/launch-smoke/recording.log','main-app-only/launch-smoke/launch-1.png',
          'main-app-only/launch-smoke/launch-2.png','main-app-only/launch-smoke/launch.mp4')
OPTIONAL_PUBLIC = ('installer-observation.json','sdk-bootstrap.json')


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data:=stream.read(1024*1024):h.update(data)
    return h.hexdigest()


def helper_modules(helper_root):
    helper_root=Path(helper_root).resolve(strict=True)
    r.require(helper_root.is_dir(),'helperRoot')
    sys.path.insert(0,str(helper_root))
    return {name:importlib.import_module(name) for name in
            ('device_delivery','artifact_licensing','ipa_inspect','replay_artifact')}


def api(endpoint):
    # Read-only standard Actions token. This process never launches the app.
    result=subprocess.run(['gh','api','--hostname','github.com','--method','GET',
        'repos/'+r.REPOSITORY+'/'+endpoint],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
    r.require(result.returncode==0 and len(result.stdout)<=8*1024**2,'metadataReadFailed')
    return r.document(result.stdout,8*1024**2)


def download(item,destination):
    r.require(not destination.exists(),'downloadDestinationExists')
    with destination.open('xb') as output:
        result=subprocess.run(['gh','api','--hostname','github.com','--method','GET',
            'repos/'+r.REPOSITORY+'/actions/artifacts/'+str(item['id'])+'/zip'],
            stdout=output,stderr=subprocess.PIPE,timeout=600,check=False)
    r.require(result.returncode==0 and destination.stat().st_size==item['size_in_bytes']
              and digest(destination)==item['digest'][7:],'downloadDigest')


def extract_selected(path,destination,selected=SELECTED):
    r.require(not destination.exists(),'extractionDestinationExists')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist();names=[e.filename for e in entries];aliases=set()
        r.require(len(entries)<=256 and len(names)==len(set(names))
                  and sum(e.file_size for e in entries)<=2*1024**3,'zipBounds')
        for e in entries:
            p=PurePosixPath(e.filename);kind=stat.S_IFMT(e.external_attr>>16)
            key=e.filename.rstrip('/').casefold()
            r.require(not p.is_absolute() and '..' not in p.parts and '\\' not in e.filename
                      and '\0' not in e.filename and e.orig_filename==e.filename
                      and p.as_posix()==e.filename.rstrip('/') and key not in aliases
                      and kind in ({0,stat.S_IFDIR} if e.is_dir() else {0,stat.S_IFREG})
                      and e.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)
                      and not e.flag_bits&1,'unsafeZipMember')
            aliases.add(key)
        r.require(selected.issubset(names),'requiredSimulatorEvidenceMissing')
        for e in entries:
            if e.filename in selected:
                maximum=1024**3 if e.filename==RAW+'Telegram.ipa' else 96*1024**2 if e.filename.endswith('.mp4') else 16*1024**2
                r.require(not e.is_dir() and e.file_size<=maximum,'selectedMemberBounds')
        destination.mkdir(mode=0o700)
        for name in sorted(selected):
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(name) as original,target.open('xb') as output:shutil.copyfileobj(original,output,1024*1024)
            target.chmod(0o600)


def raw_checksums(root):
    lines=(root/'SHA256SUMS').read_text().splitlines();seen=set()
    for line in lines:
        sha,name=line.split(None,1);name=name.lstrip('*')
        r.require(name in RAW_NAMES-{'SHA256SUMS'} and name not in seen
                  and len(sha)==64 and digest(root/name)==sha,'rawChecksums')
        seen.add(name)
    r.require(seen==RAW_NAMES-{'SHA256SUMS'},'rawChecksumInventory')


def prepare(request_file,output,helper_root):
    r.require(not request_file.is_symlink() and request_file.is_file() and request_file.stat().st_size<=1024,'requestFile')
    r.request(request_file.read_bytes(),os.environ)
    r.require(not output.exists() and not output.is_symlink(),'prepareOutput')
    run=api('actions/runs/'+str(r.RUN));jobs=api('actions/runs/'+str(r.RUN)+'/jobs?filter=latest&per_page=100')
    graph=r.original_graph(run,jobs)
    listing=api('actions/runs/'+str(r.RUN)+'/artifacts?per_page=100')
    sim=r.artifact('simulator',listing);device=r.artifact('device',listing)
    output.mkdir(mode=0o700);download(sim,output/'simulator.zip')
    extract_selected(output/'simulator.zip',output/'simulator')
    raw=output/'simulator'/RAW;raw_checksums(raw)
    r.require(digest(raw/'manifest.json')==r.SIMULATOR_MANIFEST_SHA256
              and digest(raw/'Telegram.ipa')==r.SIMULATOR_IPA_SHA256,'simulatorBytes')
    manifest=r.document((raw/'manifest.json').read_bytes())
    h=helper_modules(helper_root);delivery=h['device_delivery'];licensing=h['artifact_licensing']
    r.require(manifest.get('source_commit')==r.SOURCE and manifest.get('mode')=='simulator'
              and manifest.get('bundle_id')=='org.livara.ayugram' and type(manifest.get('build_number')) is int
              and manifest['build_number']==r.BUILD_NUMBER and manifest.get('sha256')==r.SIMULATOR_IPA_SHA256
              and manifest.get('toolchain')==delivery.PINS and manifest.get('runtime_verified') is False
              and manifest.get('tests')=={'//submodules/TextFormat:TextFormatTests':'passed'},'simulatorManifest')
    materials=licensing.read_materials(raw,r.SOURCE)
    r.require(licensing.validate_materials(materials,r.SOURCE)==manifest.get('license_materials'),'sourceMaterials')
    h['ipa_inspect'].inspect_ipa(raw/'Telegram.ipa','simulator','org.livara.ayugram')
    text=output/'simulator/fork-evidence/textformat'
    r.require(r.document((text/'collection.json').read_bytes())=={
        'target':'//submodules/TextFormat:TextFormatTests','invocation':'passed',
        'files':{'test.xml':'retained_redacted','test.log':'retained_redacted'}},'textformatCollection')
    report,_=delivery.native_tests((text/'test.xml').read_bytes(),(text/'test.log').read_bytes())
    r.require(report['native_passed_cases']==270,'textformatNot270Passed')
    old=output/'simulator'/OLD
    failure=r.original_failure((old/'replay.json').read_bytes(),(old/'main-app-only/launch-smoke/result.json').read_bytes())
    (output/'source-build-origin.json').write_text(json.dumps(r.source_build_origin(),sort_keys=True,indent=2)+'\n')
    receipt={'schema':1,'original_graph':graph,'original_failure':failure,'textformat':report,
             'artifacts':{'simulator':sim,'device':device},'application_recompiled':False}
    (output/'prepared.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print('Verified original failed graph, retained simulator bytes, TextFormat and source materials; no app was built.')


def valid_cleanup(value,successful):
    keys={'schema','owned_device_deleted','owned_uuid_absence_verified','exact_owned_device_only'}
    r.require(isinstance(value,dict) and set(value)==keys and type(value['schema']) is int and value['schema']==1
              and all(type(value[k]) is bool for k in keys-{'schema'}),'cleanupSchema')
    if successful:r.require(all(value[k] for k in keys-{'schema'}),'ownedCleanupNotVerified')



def validate_replay_schema(proof):
    required={'mode','physical_device_verified','startup_variant','private_control_only',
              'application_recompiled','passed','normal_control_passed','no_app_groups_control_attempted',
              'source_codesign_verified','phase','simulator_device_type','simulator_runtime'}
    flags={'physical_device_verified','private_control_only','application_recompiled','passed',
           'normal_control_passed','no_app_groups_control_attempted','source_codesign_verified',
           'app_group_entitlement_present_before','app_group_entitlement_absent_after',
           'main_bundle_resigned_ad_hoc','main_code_unchanged','other_entitlements_preserved',
           'automatic_fallback_verified','automatic_group_selection_observed','pinned_private_control_attempted',
           'automatic_control_run','source_ipa_unchanged','main_app_only_control_passed'}
    counts={'extensions_removed':6,'framework_files_unchanged':77,'retained_resource_files_unchanged':855}
    strings={'mode':{'main_app_only'},'startup_variant':{'main-app-only'},
             'phase':{'extractApplication','verifyCodeSignature','inspectEntitlements',
                      'prepareMainAppOnly','explicitPrivateControl','complete'},
             'simulator_device_type':{'com.apple.CoreSimulator.SimDeviceType.iPhone-15-Pro'},
             'simulator_runtime':{'com.apple.CoreSimulator.SimRuntime.iOS-27-0'},
             'source_simulator_ipa_sha256':{r.SIMULATOR_IPA_SHA256},
             'automatic_storage_kind':{'notObserved'},'private_mode_control':{'explicitFreshPinnedSelection'}}
    allowed=flags|set(counts)|set(strings)
    r.require(isinstance(proof,dict) and required.issubset(proof) and set(proof).issubset(allowed),'replayProofFields')
    for key,value in proof.items():
        if key in flags:r.require(type(value) is bool,'replayProofBoolean')
        elif key in counts:r.require(type(value) is int and value==counts[key],'replayProofCount')
        else:r.require(type(value) is str and value in strings[key],'replayProofEnum')
    r.require(proof['private_control_only'] is True and proof['application_recompiled'] is False
              and proof['physical_device_verified'] is False and proof['normal_control_passed'] is False
              and proof['no_app_groups_control_attempted'] is False,'replayProofScope')
    if proof['passed']:r.require(proof['phase']=='complete','replayProofPassPhase')
    return proof


def validate_fresh(proof,result,cleanup,parser):
    validate_replay_schema(proof)
    checked=parser(result);valid_cleanup(cleanup,True)
    r.require(proof.get('mode')=='main_app_only' and proof.get('startup_variant')=='main-app-only'
              and proof.get('passed') is True and proof.get('phase')=='complete'
              and proof.get('source_simulator_ipa_sha256')==r.SIMULATOR_IPA_SHA256
              and type(proof.get('extensions_removed')) is int and proof['extensions_removed']==6
              and proof.get('private_control_only') is True
              and proof.get('private_mode_control')=='explicitFreshPinnedSelection'
              and proof.get('automatic_control_run') is False and proof.get('automatic_fallback_verified') is False
              and proof.get('automatic_group_selection_observed') is False
              and proof.get('app_group_entitlement_present_before') is False
              and proof.get('app_group_entitlement_absent_after') is True
              and type(proof.get('framework_files_unchanged')) is int and proof['framework_files_unchanged']==77
              and type(proof.get('retained_resource_files_unchanged')) is int and proof['retained_resource_files_unchanged']==855
              and proof.get('pinned_private_control_attempted') is True
              and all(proof.get(k) is True for k in ('source_codesign_verified','main_code_unchanged','source_ipa_unchanged','main_app_only_control_passed'))
              and proof.get('application_recompiled') is False and proof.get('physical_device_verified') is False,
              'freshReplayNotPassed')
    r.require(checked.get('passed') is True and checked.get('phase')=='complete'
              and checked.get('failure') is None and checked.get('cleanup_failures',[])==[]
              and checked.get('storage_test_setup')=='freshPinnedPrivate'
              and checked.get('private_selection_seeded_before_launch') is True
              and checked.get('private_account_store_absent_before_launch') is True
              and checked.get('screenshot_checker')=={'compiled':True,'self_test_passed':True,'self_test_cases':3}
              and checked.get('recording_return_code')==0 and len(checked['launches'])==2,'freshLaunchesNotPassed')
    for index,launch in enumerate(checked['launches'],1):
        r.require(launch.get('attempt')==index and launch.get('storage_kind')=='private'
                  and launch.get('private_account_store_present') is True
                  and launch['marker']['stage'] in {'authorizationVisible','conversationsVisible'}
                  and launch['marker']['failure'] is None and launch['screenshot']['nonblank'] is True
                  and (index==1 or launch.get('private_account_store_stable_after_restart') is True),'freshLaunchIdentity')
    return checked


def finalize(work,replay,public,helper_root):
    r.require(work.is_dir() and not work.is_symlink() and not replay.is_symlink()
              and not public.exists() and not public.is_symlink(),'publicRoots')
    optional_bytes={}
    if not replay.is_dir():
        r.require(not replay.exists(),'replayRoot')
        raw=validated_optional_bytes('sdk-bootstrap.json',work/'sdk-bootstrap.json')
        sdk=r.document(raw,65536);optional_bytes['sdk-bootstrap.json']=raw
        r.require(sdk.get('success') is False,'missingReplayRequiresFailedSdk')
    source=r.document((work/'source-build-origin.json').read_bytes());r.validate_source_build_origin(source)
    h=helper_modules(helper_root);parser=h['replay_artifact'].prior_smoke_report
    proof=r.document((replay/'replay.json').read_bytes()) if (replay/'replay.json').is_file() else None
    if proof is not None:validate_replay_schema(proof)
    result_path=replay/'main-app-only/launch-smoke/result.json'
    result=r.document(result_path.read_bytes()) if result_path.is_file() else None
    cleanup=r.document((replay/'cleanup.json').read_bytes()) if (replay/'cleanup.json').is_file() else None
    if cleanup is not None:valid_cleanup(cleanup,False)
    if result is not None:parser(result)
    success=bool(proof and result and cleanup and proof.get('passed') is True and result.get('passed') is True
                 and all(cleanup.get(k) is True for k in ('owned_device_deleted','owned_uuid_absence_verified','exact_owned_device_only')))
    if success:validate_fresh(proof,result,cleanup,parser)
    # Fixed names only; validate all file bounds and links before creating public.
    sources={'source-build-origin.json':work/'source-build-origin.json',
             'original-failure/replay.json':work/'simulator'/OLD/'replay.json',
             'original-failure/result.json':work/'simulator'/OLD/'main-app-only/launch-smoke/result.json'}
    sources.update({name:replay/name for name in PUBLIC if name not in sources and (replay/name).exists()})
    for name,path in (('installer-observation.json',replay/'installer-observation.json'),
                      ('sdk-bootstrap.json',work/'sdk-bootstrap.json')):
        if path.exists() or path.is_symlink():
            if name not in optional_bytes:optional_bytes[name]=validated_optional_bytes(name,path)
            sources[name]=path
    total=0
    for name,path in sources.items():
        maximum=96*1024**2 if name.endswith('.mp4') else 16*1024**2 if name.endswith('.png') else 2*1024**2
        r.require(path.is_file() and not path.is_symlink() and all(not p.is_symlink() for p in path.parents)
                  and path.stat().st_size<=maximum,'publicFileBoundsOrAlias')
        total+=path.stat().st_size
    r.require(total<=128*1024**2,'publicAggregateBounds')
    if success:r.require(set(PUBLIC).issubset(sources) and set(sources).issubset(set(PUBLIC)|set(OPTIONAL_PUBLIC)),
                         'successfulEvidenceIncomplete')
    public.mkdir(mode=0o700)
    for name,path in sources.items():
        dest=public/name;dest.parent.mkdir(parents=True,exist_ok=True)
        if name in optional_bytes:dest.write_bytes(optional_bytes[name])
        else:shutil.copyfile(path,dest)
        dest.chmod(0o600)
    (public/'SHA256SUMS').write_text(''.join(digest(public/name)+'  '+name+'\n' for name in sorted(sources)))
    if os.environ.get('GITHUB_OUTPUT'):
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            stream.write('new_startup_verified='+str(success).lower()+'\n')
    print(json.dumps({'schema':1,'new_startup_verified':success,'original_run_remains_failed':True,'application_recompiled':False}))


def validate_optional_public(name,path):
    return r.document(validated_optional_bytes(name,path),65536)


def validated_optional_bytes(name,path):
    r.require(name in OPTIONAL_PUBLIC and path.is_file() and not path.is_symlink()
              and all(not parent.is_symlink() for parent in path.parents)
              and 0<path.stat().st_size<=65536,'optionalPublicFileBoundsOrAlias')
    before=path.lstat();descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    signature=lambda info:(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
                           info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    try:
        r.require(signature(os.fstat(descriptor))==signature(before),'optionalPublicIdentity')
        data=os.read(descriptor,65537)
        r.require(len(data)==before.st_size and signature(os.fstat(descriptor))==signature(before)
                  and signature(path.lstat())==signature(before),'optionalPublicIdentity')
    finally:os.close(descriptor)
    value=r.document(data,65536)
    if name=='installer-observation.json':
        from installer_observation import validate_observation
        validate_observation(value)
    else:
        from sdk_bootstrap import validate_report
        validate_report(value)
    return data


def extract_fresh_startup(path,destination):
    with zipfile.ZipFile(path) as archive:
        present=set(archive.namelist())
    selected=set(PUBLIC)|{'SHA256SUMS'}|(set(OPTIONAL_PUBLIC)&present)
    extract_selected(path,destination,selected)
    for name in OPTIONAL_PUBLIC:
        if name in selected:validate_optional_public(name,destination/name)



def package_resume(output,helper_root):
    # This token-bearing Linux process performs static checks/copying only.
    environment=os.environ
    r.require(environment.get('GITHUB_REPOSITORY')==r.REPOSITORY
              and environment.get('GITHUB_REF')==r.RESUME_REF
              and environment.get('GITHUB_EVENT_NAME')=='push'
              and environment.get('GITHUB_RUN_ATTEMPT')=='1','packageEvent')
    identity=environment.get('GITHUB_RUN_ID','')
    r.require(identity.isdecimal() and 0<int(identity)<2**63,'resumeRunId')
    expected_run=int(identity);tooling=environment.get('GITHUB_SHA','')
    original_run=api('actions/runs/'+str(r.RUN))
    original_jobs=api('actions/runs/'+str(r.RUN)+'/jobs?filter=latest&per_page=100')
    r.original_graph(original_run,original_jobs)
    original_artifacts=api('actions/runs/'+str(r.RUN)+'/artifacts?per_page=100')
    native_run=api('actions/runs/'+str(r.NATIVE_RUN))
    r.require(native_run.get('id')==r.NATIVE_RUN and native_run.get('head_sha')==r.SOURCE
              and native_run.get('path')=='.github/workflows/archive-integration.yml'
              and native_run.get('status')=='completed' and native_run.get('conclusion')=='success'
              and type(native_run.get('run_attempt')) is int and native_run['run_attempt']==1
              and native_run.get('event')=='workflow_dispatch','nativeRunOrigin')
    r.repository_origin(native_run)
    native_jobs=api('actions/runs/'+str(r.NATIVE_RUN)+'/jobs?filter=latest&per_page=100')
    selected=[j for j in native_jobs.get('jobs',[]) if j.get('id')==128081500429]
    r.require(len(selected)==1 and selected[0].get('run_id')==r.NATIVE_RUN
              and selected[0].get('head_sha')==r.SOURCE and selected[0].get('status')=='completed'
              and selected[0].get('conclusion')=='success','nativeJobOrigin')
    native_artifacts=api('actions/runs/'+str(r.NATIVE_RUN)+'/artifacts?per_page=100')
    items={role:r.artifact(role,native_artifacts if role=='native' else original_artifacts)
           for role in ('device','simulator','native')}
    fresh_run=api('actions/runs/'+str(expected_run))
    fresh_jobs=api('actions/runs/'+str(expected_run)+'/jobs?filter=latest&per_page=100')
    fresh_artifacts=api('actions/runs/'+str(expected_run)+'/artifacts?per_page=100')
    startup=r.startup_origin(fresh_run,fresh_jobs,fresh_artifacts,expected_run,tooling)
    fresh=next(a for a in fresh_artifacts['artifacts'] if a['id']==startup['artifact_id'])
    helper_modules(helper_root)
    from resume_package import package
    with tempfile.TemporaryDirectory(prefix='retained-delivery-resume-') as temporary:
        work=Path(temporary)
        for role,item in {**items,'startup':fresh}.items():download(item,work/(role+'.zip'))
        extract_fresh_startup(work/'startup.zip',work/'startup')
        package(Path(helper_root).resolve().parents[1],work/'device.zip',work/'simulator.zip',work/'native.zip',
                original_run,original_jobs,original_artifacts,native_artifacts,work/'startup',startup,output,
                startup_archive=work/'startup.zip')
        # Do not publish if the immutable producer was rerun during recovery.
        r.original_graph(api('actions/runs/'+str(r.RUN)),
                         api('actions/runs/'+str(r.RUN)+'/jobs?filter=latest&per_page=100'))
    print('Retained device export prepared with fresh startup evidence; original delivery remains failed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    prep=sub.add_parser('prepare')
    for name in ('request','output','helper-root'):prep.add_argument('--'+name,required=True,type=Path)
    final=sub.add_parser('finalize')
    for name in ('work','replay','public','helper-root'):final.add_argument('--'+name,required=True,type=Path)
    pack=sub.add_parser('package')
    for name in ('output','helper-root'):pack.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    try:
        if args.mode=='prepare':prepare(args.request,args.output,args.helper_root)
        elif args.mode=='finalize':finalize(args.work,args.replay,args.public,args.helper_root)
        else:package_resume(args.output,args.helper_root)
    except Exception:
        print('Artifact-only startup resume blocked by source, origin, runtime or evidence checks.',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
