#!/usr/bin/env python3
"""Dispatch one exact-revision qualification using gh's existing authentication.

Never reads token values, credential files, or raw auth output. Existing runs,
including completed runs, are returned without creating another build. Repository
Actions settings change only with explicit --enable-actions and preserve policy.
"""
import argparse
import json
import re
import subprocess
import time

REPOSITORY = 'Livara-Dev/Telegram-iOS'
BRANCH = 'ci/verify-latest'
WORKFLOW = 'archive-integration.yml'
BASE = 'repos/' + REPOSITORY
RUNS = BASE + '/actions/workflows/' + WORKFLOW + '/runs'
REF = BASE + '/git/ref/heads/' + BRANCH
PERMISSIONS = BASE + '/actions/permissions'
STATUSES = {'queued', 'requested', 'waiting', 'pending', 'in_progress', 'completed'}
CONCLUSIONS = {None, 'success', 'failure', 'neutral', 'cancelled', 'skipped',
               'timed_out', 'action_required', 'stale', 'startup_failure'}


class DispatchError(Exception):
    def __init__(self, code, dispatch_accepted=False):
        # Codes are fixed by this module; no API output is attached or exposed.
        super().__init__(code)
        self.code = code
        self.dispatch_accepted = dispatch_accepted


def github_api(method, endpoint, payload=None, timeout=15):
    arguments = ['gh', 'api', '--hostname', 'github.com', '--method', method,
                 '-H', 'Accept: application/vnd.github+json',
                 '-H', 'X-GitHub-Api-Version: 2022-11-28', endpoint]
    if payload is not None:
        arguments.extend(['--input', '-'])
    try:
        result = subprocess.run(arguments, input=None if payload is None else json.dumps(payload),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                timeout=timeout, check=False)
    except FileNotFoundError:
        raise DispatchError('github_cli_unavailable') from None
    except subprocess.TimeoutExpired:
        raise DispatchError('github_request_timeout') from None
    except (OSError, subprocess.SubprocessError):
        raise DispatchError('github_request_failed') from None
    if result.returncode:
        # Raw CLI stderr may contain runtime authentication diagnostics. Inspect
        # fixed error markers internally, but never print the original payload.
        diagnostic = (result.stderr + '\n' + result.stdout).lower()
        if 'http 422' in diagnostic and 'actions has been disabled for this repository' in diagnostic:
            raise DispatchError('actions_disabled')
        if 'http 403' in diagnostic or 'resource not accessible by integration' in diagnostic:
            raise DispatchError('forbidden')
        if 'http 401' in diagnostic or 'gh auth login' in diagnostic:
            raise DispatchError('authentication_required')
        if 'http 404' in diagnostic:
            raise DispatchError('repository_or_workflow_unavailable')
        if 'http 422' in diagnostic:
            raise DispatchError('dispatch_rejected')
        raise DispatchError('github_request_failed')
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise DispatchError('invalid_github_response') from None


def verify_ref(expected_sha, api):
    value = api('GET', REF)
    if not isinstance(value, dict) or not isinstance(value.get('object'), dict):
        raise DispatchError('invalid_github_response')
    if value['object'].get('type') != 'commit' or value['object'].get('sha') != expected_sha:
        raise DispatchError('canonical_revision_mismatch')


def find_run(expected_sha, api, timeout=15):
    endpoint = RUNS + '?branch=ci%2Fverify-latest&head_sha=' + expected_sha + '&per_page=100'
    value = api('GET', endpoint, timeout=timeout)
    if not isinstance(value, dict) or not isinstance(value.get('workflow_runs'), list):
        raise DispatchError('invalid_github_response')
    matches = [run for run in value['workflow_runs'] if isinstance(run, dict)
               and run.get('head_sha') == expected_sha and run.get('head_branch') == BRANCH
               and type(run.get('id')) is int and run['id'] > 0]
    return max(matches, key=lambda run: run['id']) if matches else None


def enable_repository_actions(api):
    # Repeat the current policy explicitly; never write the separate selected-
    # action allowlist endpoint or change all/local_only/selected restrictions.
    try:
        before = api('GET', PERMISSIONS)
        if (not isinstance(before, dict) or type(before.get('enabled')) is not bool
                or before.get('allowed_actions') not in {'all', 'local_only', 'selected'}):
            raise DispatchError('invalid_actions_settings')
        if before['enabled']:
            return
        api('PUT', PERMISSIONS, payload={'enabled': True, 'allowed_actions': before['allowed_actions']})
        after = api('GET', PERMISSIONS)
        if (not isinstance(after, dict) or after.get('enabled') is not True
                or after.get('allowed_actions') != before['allowed_actions']):
            raise DispatchError('actions_enable_not_verified')
    except DispatchError as error:
        if error.code == 'forbidden':
            raise DispatchError('repository_administration_permission_required') from None
        raise


def run_result(run, expected_sha, existing):
    status, conclusion = run.get('status'), run.get('conclusion')
    return {
        'repository': REPOSITORY, 'branch': BRANCH, 'source_commit': expected_sha,
        'run_id': run['id'],
        'run_url': 'https://github.com/' + REPOSITORY + '/actions/runs/' + str(run['id']),
        'status': status if isinstance(status, str) and status in STATUSES else 'unknown',
        'conclusion': conclusion if (conclusion is None or isinstance(conclusion, str) and conclusion in CONCLUSIONS) else 'unknown',
        'existing_run': existing, 'dispatch_accepted': not existing, 'run_observed': True,
    }


def dispatch_qualification(expected_sha, enable_actions=False, api=github_api,
                           clock=time.monotonic, sleep=time.sleep):
    if not isinstance(expected_sha, str) or re.fullmatch(r'[0-9a-f]{40}', expected_sha) is None:
        raise DispatchError('invalid_expected_sha')
    verify_ref(expected_sha, api)
    existing = find_run(expected_sha, api)
    if existing is not None:
        return run_result(existing, expected_sha, existing=True)
    # A branch can move during the first request. Refuse before dispatch if the
    # source batch changed; no force-push or old-run re-execution is performed.
    verify_ref(expected_sha, api)
    try:
        api('POST', BASE + '/actions/workflows/' + WORKFLOW + '/dispatches',
            payload={'ref': BRANCH, 'inputs': {'unsigned_device': True}})
    except DispatchError as error:
        if error.code == 'forbidden':
            raise DispatchError('actions_write_permission_required') from None
        if error.code != 'actions_disabled' or not enable_actions:
            raise
        enable_repository_actions(api)
        # No failed/rejected dispatch is assumed to have started. Recheck for a
        # push-created run before the one permitted retry after settings change.
        verify_ref(expected_sha, api)
        existing = find_run(expected_sha, api)
        if existing is not None:
            return run_result(existing, expected_sha, existing=True)
        try:
            api('POST', BASE + '/actions/workflows/' + WORKFLOW + '/dispatches',
                payload={'ref': BRANCH, 'inputs': {'unsigned_device': True}})
        except DispatchError as retry:
            if retry.code == 'forbidden':
                raise DispatchError('actions_write_permission_required') from None
            raise
    # A 204 is acceptance, not evidence of a created or running workflow.
    deadline = clock() + 30
    try:
        verify_ref(expected_sha, api)
        while clock() < deadline:
            remaining = deadline - clock()
            run = find_run(expected_sha, api, timeout=max(0.01, min(10, remaining)))
            if run is not None:
                return run_result(run, expected_sha, existing=False)
            sleep(min(2, max(0, deadline - clock())))
    except DispatchError as error:
        raise DispatchError(error.code, dispatch_accepted=True) from None
    raise DispatchError('accepted_run_not_observed', dispatch_accepted=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--enable-actions', action='store_true',
                        help='Explicitly enable repository Actions if disabled; preserve allowed-actions policy')
    arguments = parser.parse_args(argv)
    try:
        result = dispatch_qualification(arguments.expected_sha, enable_actions=arguments.enable_actions)
    except DispatchError as error:
        print(json.dumps({'error': error.code, 'dispatch_accepted': error.dispatch_accepted,
                          'run_observed': False}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
