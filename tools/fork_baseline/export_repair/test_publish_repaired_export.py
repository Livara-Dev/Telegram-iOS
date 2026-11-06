# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2026 AyuGram iOS contributors
# Added: 2026-11-06
import json
from pathlib import Path
import tempfile
import unittest

import publish_repaired_export as publish
import repair_export as repair


def original_run(native=False):
    return {'id': repair.NATIVE_RUN if native else repair.DELIVERY_RUN,
            'head_sha': repair.SOURCE if native else publish.ORIGINAL_TOOL_SHA,
            'path': publish.NATIVE_WORKFLOW if native else publish.ORIGINAL_WORKFLOW,
            'status': 'completed', 'conclusion': 'success', 'run_attempt': 1,
            'event': 'workflow_dispatch' if native else 'push', 'head_branch': 'ci/qualified-device-delivery',
            'repository': {'full_name': publish.REPOSITORY, 'id': 16},
            'head_repository': {'full_name': publish.REPOSITORY, 'id': 16}}


class FixedOriginTests(unittest.TestCase):
    def test_requires_completed_successful_exact_source_and_workflow_origins(self):
        for native in (False, True):
            run = original_run(native)
            publish.completed_run(run, native)
            for field, value in (('id', 1), ('head_sha', 'a' * 40), ('path', '.github/workflows/other.yml'),
                                 ('status', 'in_progress'), ('conclusion', 'failure'), ('event', 'pull_request')):
                with self.subTest(native=native, field=field), self.assertRaises(ValueError):
                    publish.completed_run({**run, field: value}, native)
            with self.assertRaises(ValueError):
                publish.completed_run({**run, 'head_repository': {'full_name': 'other/repo', 'id': 17}}, native)

    def test_explicit_request_accepts_only_the_fixed_run_source_branch_and_two_ids(self):
        value = {'schema': 1, 'delivery_run': repair.DELIVERY_RUN, 'source_commit': repair.SOURCE,
                 'final_artifact_id': 100, 'simulator_artifact_id': 200}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'request.json'
            path.write_text(json.dumps(value))
            self.assertEqual(publish.request(path, 'push', publish.REPAIR_REF, publish.REPOSITORY), value)
            for field, item in (('schema', True), ('delivery_run', repair.DELIVERY_RUN + 1),
                                ('source_commit', 'b' * 40), ('final_artifact_id', True),
                                ('simulator_artifact_id', 100), ('extra', 'forbidden')):
                path.write_text(json.dumps({**value, field: item}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    publish.request(path, 'push', publish.REPAIR_REF, publish.REPOSITORY)
            path.write_text(json.dumps(value))
            for event, ref in (('workflow_dispatch', publish.REPAIR_REF), ('push', 'refs/heads/master'),
                               ('push', 'refs/heads/ci/qualified-device-delivery')):
                with self.subTest(event=event, ref=ref), self.assertRaises(ValueError):
                    publish.request(path, event, ref, publish.REPOSITORY)

    def test_artifact_id_name_and_run_digest_binding_cannot_be_forged_by_request(self):
        run = original_run()
        artifact = {'id': 100, 'name': publish.ARTIFACT_NAMES['final'], 'expired': False,
                    'size_in_bytes': 300, 'digest': 'sha256:' + 'a' * 64,
                    'workflow_run': {'id': run['id'], 'head_sha': run['head_sha'],
                                     'repository_id': 16, 'head_repository_id': 16}}
        listing = {'total_count': 1, 'artifacts': [artifact]}
        self.assertEqual(publish.select_artifact('final', listing, run, 100), artifact)
        with self.assertRaises(ValueError): publish.select_artifact('final', listing, run, 999)
        for field, value in (('expired', True), ('name', publish.ARTIFACT_NAMES['simulator']),
                             ('digest', 'bad'), ('size_in_bytes', 2 * 1024**3),
                             ('workflow_run', {**artifact['workflow_run'], 'head_sha': repair.SOURCE})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                publish.select_artifact('final', {'total_count': 1, 'artifacts': [{**artifact, field: value}]}, run, 100)
        with self.assertRaises(ValueError):
            publish.select_artifact('final', {'total_count': 2, 'artifacts': [artifact, artifact]}, run, 100)

    def test_all_original_jobs_must_be_complete_at_exact_build_job_ids(self):
        names = ('qualification', 'unsigned-device-build', 'application-build', 'package')
        jobs = [{'name': name, 'id': identity, 'run_id': repair.DELIVERY_RUN,
                 'head_sha': publish.ORIGINAL_TOOL_SHA, 'status': 'completed', 'conclusion': 'success'}
                for name, identity in zip(names, (10, 128105899974, 128105900214, 40))]
        publish.successful_delivery_jobs({'total_count': 4, 'jobs': jobs})
        for position, field, value in ((1, 'id', 99), (2, 'status', 'in_progress'),
                                       (3, 'conclusion', 'failure'), (0, 'head_sha', repair.SOURCE)):
            modified = [{**row} for row in jobs]
            modified[position][field] = value
            with self.subTest(position=position, field=field), self.assertRaises(ValueError):
                publish.successful_delivery_jobs({'total_count': 4, 'jobs': modified})
        with self.assertRaises(ValueError):
            publish.successful_delivery_jobs({'total_count': 4, 'jobs': jobs[:3]})


if __name__ == '__main__':
    unittest.main()
