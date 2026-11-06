import base64
import hashlib
import io
import json
import pathlib
import re
import textwrap
import unittest
import urllib.error


ROOT = pathlib.Path(__file__).resolve().parents[3] / '.github' / 'workflows'
WORKFLOW = (ROOT / 'projection-discovery-dispatch.yml').read_text()
PROGRAM = textwrap.dedent(WORKFLOW.split('          # BEGIN FIXED DISPATCH PROGRAM\n', 1)[1]
                         .split('          # END FIXED DISPATCH PROGRAM', 1)[0])
MODULE = {'__name__': 'dispatch_bridge_tests'}
exec(compile(PROGRAM, '<fixed-dispatch-program>', 'exec'), MODULE)
TOOL_SHA = 'a' * 40
EVENT_SHA = 'b' * 40


def encoded(value):
    return json.dumps(value).encode()


def request(nonce='reviewed-001', expected_tool_sha=TOOL_SHA):
    return dict(schema=1, nonce=nonce, expected_tool_sha=expected_tool_sha)


def content(raw=None):
    raw = encoded(request()) if raw is None else raw
    return dict(type='file', path='ci/projection-discovery-request.json', encoding='base64',
                size=len(raw), content=base64.encodebytes(raw).decode(),
                sha=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())


def head(sha=TOOL_SHA):
    return dict(ref='refs/heads/diagnostics/c55-projection-deferred', object=dict(type='commit', sha=sha))


class Response:
    def __init__(self, value=None, status=200, raw=None):
        self.status = status
        self.raw = encoded(value) if raw is None else raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.raw[:limit]


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if not self.responses:
            raise AssertionError('Unexpected repeated API call')
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class BridgeTests(unittest.TestCase):
    def env(self, **changes):
        env = dict(GITHUB_REPOSITORY='Livara-Dev/Telegram-iOS', GITHUB_EVENT_NAME='push',
                   GITHUB_REF='refs/heads/ci/qualified-device-delivery', GITHUB_RUN_ATTEMPT='1',
                   GITHUB_SHA=EVENT_SHA, DISPATCH_TOKEN='private-test-token-never-publish')
        env.update(changes)
        return env
    def execute(self, responses=None, **changes):
        opener = Opener(responses if responses is not None else [Response(content()), Response(head()), Response(status=204, raw=b'')])
        result, code = MODULE['execute'](self.env(**changes), opener)
        self.assertNotIn('private-test-token', json.dumps(result))
        self.assertFalse(result['native_run_verified'])
        self.assertFalse(result['app_build_requested'])
        return result, code, opener
    def reject_raw(self, raw):
        result, code, opener = self.execute([Response(content(raw))])
        self.assertEqual(code, 1)
        self.assertFalse(result['submitted'])
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result['phase'], 'request')

    def test_only_fixed_bounded_single_submission(self):
        result, code, opener = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'submitted')
        self.assertFalse(result['submission_unknown'])
        self.assertTrue(result['submitted'])
        self.assertEqual(result['expected_tool_sha'], TOOL_SHA)
        self.assertEqual(len(opener.calls), 3)
        urls = [req.full_url for req, _ in opener.calls]
        self.assertEqual(urls, [
            'https://api.github.com/repos/Livara-Dev/Telegram-iOS/contents/ci/projection-discovery-request.json?ref=' + EVENT_SHA,
            'https://api.github.com/repos/Livara-Dev/Telegram-iOS/git/ref/heads/diagnostics/c55-projection-deferred',
            'https://api.github.com/repos/Livara-Dev/Telegram-iOS/actions/workflows/archive-integration.yml/dispatches'])
        self.assertEqual([req.get_method() for req, _ in opener.calls], ['GET', 'GET', 'POST'])
        self.assertEqual([timeout for _, timeout in opener.calls], [20, 20, 20])
        self.assertEqual(json.loads(opener.calls[-1][0].data), {
            'ref': 'diagnostics/c55-projection-deferred', 'inputs': {
                'validation_scope': 'projection-discovery-only', 'unsigned_device': False,
                'replay_run': '42160468600', 'replay_source': 'c55e19c4f5cbe0d730499035d0a0ef712f6903a9'}})

    def test_event_coordinates_and_reruns_rejected_without_network(self):
        for name, value in [('GITHUB_REPOSITORY', 'another/repo'), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
                            ('GITHUB_REF', 'refs/heads/main'), ('GITHUB_RUN_ATTEMPT', '2'),
                            ('GITHUB_RUN_ATTEMPT', ''), ('GITHUB_SHA', 'B' * 40), ('GITHUB_SHA', '')]:
            with self.subTest(name=name, value=value):
                result, code, opener = self.execute([], **{name: value})
                self.assertEqual(code, 1)
                self.assertEqual(result['reason'], 'eventCoordinates')
                self.assertEqual(opener.calls, [])

    def test_missing_or_header_injection_token_rejected(self):
        for token in ['', 'a' * 8193, 'a\rb', 'a\nb']:
            with self.subTest(token_length=len(token)):
                result, code, opener = self.execute([], DISPATCH_TOKEN=token)
                self.assertEqual(code, 1)
                self.assertEqual(result['reason'], 'missingOrInvalidToken')
                self.assertEqual(opener.calls, [])

    def test_strict_schema_rejects_types_unknown_fields_and_unbounded_nonce(self):
        for changes in [dict(schema=True), dict(schema=2), dict(nonce=''), dict(nonce='x' * 65),
                        dict(nonce='../secret'), dict(nonce='a\n'), dict(nonce=1),
                        dict(expected_tool_sha='A' * 40), dict(expected_tool_sha='a' * 39),
                        dict(expected_tool_sha=True), dict(ref='other'), dict(endpoint='https://evil.invalid')]:
            with self.subTest(changes=changes):
                self.reject_raw(encoded(dict(request(), **changes)))
        for raw in [b'[]', b'null', b'1', b'{', b'\xff', b'{}', b'"value"']:
            with self.subTest(raw=raw):
                self.reject_raw(raw)

    def test_duplicate_and_nonfinite_values_rejected(self):
        self.reject_raw(b'{"schema":1,"schema":1,"nonce":"x","expected_tool_sha":"' + TOOL_SHA.encode() + b'"}')
        for value in [b'NaN', b'Infinity', b'-Infinity']:
            self.reject_raw(b'{"schema":' + value + b',"nonce":"x","expected_tool_sha":"' + TOOL_SHA.encode() + b'"}')

    def test_contents_envelope_and_git_blob_attestation(self):
        for changes in [dict(type='symlink'), dict(path='other'), dict(encoding='utf-8'),
                        dict(size=4097), dict(size=0), dict(size=True), dict(content=1),
                        dict(content='**not-base64**'), dict(sha='c' * 40), dict(sha='bad')]:
            with self.subTest(changes=changes):
                result, code, opener = self.execute([Response(dict(content(), **changes))])
                self.assertEqual(code, 1)
                self.assertFalse(result['submitted'])
                self.assertEqual(len(opener.calls), 1)
        result, code, opener = self.execute([Response(content(b' ' * 4097))])
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'requestEnvelope')
        self.assertEqual(len(opener.calls), 1)

    def test_bounded_api_read_rejects_oversized_response(self):
        result, code, opener = self.execute([Response(raw=b' ' * 65537)])
        self.assertEqual(code, 1)
        self.assertEqual(result['reason'], 'apiResponseTooLarge')
        self.assertEqual(len(opener.calls), 1)

    def test_target_head_must_match_before_post(self):
        for response in [head('d' * 40), dict(head(), ref='refs/heads/main'),
                         dict(head(), object=dict(type='tag', sha=TOOL_SHA)), [], None]:
            with self.subTest(response=response):
                result, code, opener = self.execute([Response(content()), Response(response)])
                self.assertEqual(code, 1)
                self.assertEqual(result['reason'], 'diagnosticHeadMismatch')
                self.assertEqual(len(opener.calls), 2)
                self.assertFalse(result['submission_unknown'])

    def test_read_failures_never_dispatch_or_expose_exception_body(self):
        for error in [urllib.error.HTTPError('https://invalid', 403, 'secret', {}, io.BytesIO(b'private-test-token body')),
                      urllib.error.URLError('private-test-token unreachable'), TimeoutError('private-test-token')]:
            result, code, opener = self.execute([error])
            self.assertEqual(code, 1)
            self.assertEqual(len(opener.calls), 1)
            self.assertFalse(result['submission_unknown'])
            self.assertNotIn('unreachable', json.dumps(result))

    def test_definite_dispatch_rejections_never_retry(self):
        for status in [400, 401, 403, 404, 422]:
            error = urllib.error.HTTPError('https://invalid', status, 'private-test-token', {}, io.BytesIO(b'secret body'))
            result, code, opener = self.execute([Response(content()), Response(head()), error])
            self.assertEqual(code, 1)
            self.assertEqual(len(opener.calls), 3)
            self.assertFalse(result['submission_unknown'])
            self.assertEqual(result['status'], 'rejected')
            if status in (401, 403):
                self.assertEqual(result['reason'], 'githubPermissionDenied')

    def test_ambiguous_dispatch_outcomes_never_retry_or_claim_native_run(self):
        for failure in [TimeoutError('secret'), urllib.error.URLError('secret'),
                        urllib.error.HTTPError('https://invalid', 500, 'secret', {}, io.BytesIO(b'secret')),
                        Response(status=202, raw=b''), Response(status=204, raw=b'body')]:
            with self.subTest(kind=type(failure).__name__):
                result, code, opener = self.execute([Response(content()), Response(head()), failure])
                self.assertEqual(code, 1)
                self.assertEqual(len(opener.calls), 3)
                self.assertTrue(result['submission_unknown'])
                self.assertEqual(result['status'], 'submissionUnknown')
                self.assertFalse(result['submitted'])
                self.assertNotIn('secret', json.dumps(result))

    def test_redirects_are_rejected_so_authorization_cannot_follow(self):
        handler = MODULE['NoRedirect']()
        self.assertIsNone(handler.redirect_request(None, None, 302, 'move', {}, 'https://evil.invalid'))


class WorkflowTests(unittest.TestCase):
    def test_fixed_push_scope_and_minimum_token_permissions(self):
        self.assertRegex(WORKFLOW, r'on:\n  push:\n    branches: \[ci/qualified-device-delivery\]\n    paths: \[ci/projection-discovery-request.json\]')
        self.assertIn('permissions:\n  contents: read\n  actions: write\n', WORKFLOW)
        self.assertIn('runs-on: ubuntu-24.04', WORKFLOW)
        self.assertIn('cancel-in-progress: false', WORKFLOW)
        self.assertIn('timeout-minutes: 3', WORKFLOW)
        self.assertIn('DISPATCH_TOKEN: ${{ github.token }}', WORKFLOW)
        self.assertEqual(len(re.findall(r'^  [\w-]+:\n', WORKFLOW.split('jobs:\n', 1)[1], re.M)), 1)
        self.assertNotIn('workflow_dispatch:', WORKFLOW)
        self.assertNotIn('workflow_run:', WORKFLOW)
        self.assertNotIn('secrets.', WORKFLOW)
        self.assertNotIn('uses:', WORKFLOW)
        self.assertNotIn('macos', WORKFLOW.lower())
        self.assertNotIn('xcodebuild', WORKFLOW)
        self.assertNotIn('bazel', WORKFLOW)
        self.assertNotIn('git clone', WORKFLOW)
        self.assertNotIn('sleep(', PROGRAM)
        self.assertEqual(PROGRAM.count("api('/actions/workflows/archive-integration.yml/dispatches'"), 1)

    def test_program_embedded_as_single_quoted_heredoc(self):
        self.assertIn("python3 - <<'PY'", WORKFLOW)
        self.assertTrue(WORKFLOW.endswith('          PY\n'))
        self.assertNotIn('${{', PROGRAM)
        compile(PROGRAM, '<fixed-dispatch-program>', 'exec')


if __name__ == '__main__':
    unittest.main()
