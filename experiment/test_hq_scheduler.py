# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for hq_scheduler.py."""

import datetime
import json
import os
from unittest import mock

import pytest

from common import hyperqueue
from common import new_process
from database import models
from database import utils as db_utils
from experiment import hq_scheduler
from experiment import scheduler

# pylint: disable=redefined-outer-name,unused-argument,protected-access

IMAGE = 'sha256:' + 'a' * 64
OTHER_IMAGE = 'sha256:' + 'b' * 64
NOW = datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.timezone.utc)


class FakeClient:
    """A HyperQueue server in memory. Jobs stay waiting until a test says
    otherwise."""

    def __init__(self):
        self.jobs = {}
        self.submissions = []
        self.submit_error = None

    def submit(self, command, **kwargs):
        """Records the submission and returns the new job's id."""
        if self.submit_error:
            raise self.submit_error
        job_id = len(self.submissions) + 1
        self.submissions.append((command, kwargs))
        self.jobs[job_id] = hyperqueue.Job(job_id=job_id,
                                           name=kwargs['name'],
                                           task_count=1,
                                           waiting=1)
        return job_id

    def set_state(self, job_id, state):
        """Moves the job's task to |state|."""
        job = self.jobs[job_id]
        self.jobs[job_id] = hyperqueue.Job(job_id=job_id,
                                           name=job.name,
                                           task_count=1,
                                           **{state: 1})

    def list_jobs(self):
        """Returns the jobs."""
        return list(self.jobs.values())


@pytest.fixture
def config(tmp_path, experiment_config):
    """Returns a hyperqueue experiment config with its filestores in
    |tmp_path|."""
    experiment_config['executor'] = 'hyperqueue'
    experiment_config['experiment_filestore'] = str(tmp_path / 'experiment')
    experiment_config['report_filestore'] = str(tmp_path / 'report')
    return experiment_config


@pytest.fixture
def client():
    """Returns a fake HyperQueue server."""
    return FakeClient()


@pytest.fixture
def hq(db, config, client):
    """Returns a set up scheduler, with image exports and the clock mocked."""
    db_utils.add_all([models.Experiment(name=config['experiment'])])
    hq_sched = hq_scheduler.HyperQueueScheduler(config, client)
    hq_sched.set_up()
    with mock.patch('experiment.hq_scheduler.export_runner_image',
                    return_value=True), \
            mock.patch('experiment.scheduler.datetime_now', return_value=NOW), \
            mock.patch('common.benchmark_utils.get_fuzz_target',
                       return_value='fuzz-target'):
        yield hq_sched


def add_trials(config, count, image=IMAGE):
    """Adds |count| pending trials to the experiment and returns their ids."""
    trials = [
        models.Trial(experiment=config['experiment'],
                     fuzzer='fuzzer',
                     benchmark='benchmark',
                     trial_group_num=index,
                     runner_image_digest=image) for index in range(count)
    ]
    db_utils.add_all(trials)
    return [trial.id for trial in trials]


def get_trial(trial_id):
    """Returns the trial with |trial_id| as the database has it."""
    with db_utils.session_scope() as session:
        return session.get(models.Trial, trial_id)


def write_start_marker(hq, trial_id, time_started):
    """Records that |trial_id|'s container started at |time_started|."""
    with open(hq._get_start_marker_path(trial_id), 'w',
              encoding='utf-8') as file_handle:
        file_handle.write(time_started.isoformat())


def test_set_up(hq):
    """Tests that set_up leaves the trial wrapper where workers look for it."""
    assert os.path.exists(hq.wrapper_path)
    assert hq.wrapper_path.startswith(hq.hq_dir)


def test_submit(hq, config, client):
    """Tests that pending trials are submitted, each with a task file that
    describes its container, and are not yet marked started."""
    trial_id, = add_trials(config, 1)
    hq.schedule()

    command, kwargs = client.submissions[0]
    task_path = os.path.join(hq.hq_dir, 'tasks', f'{trial_id}.json')
    assert command == ['python3', hq.wrapper_path, task_path]
    assert kwargs['name'] == f'r-test-experiment-{trial_id}'
    assert kwargs['cpus'] == 1
    assert kwargs['memory_mb'] == 8192
    assert kwargs['cwd'] == hq.hq_dir
    assert kwargs['time_limit_seconds'] == (config['max_total_time'] +
                                            scheduler.GRACE_TIME_SECONDS +
                                            hq_scheduler.IMAGE_LOAD_SECONDS)
    assert kwargs['stdout'] == os.path.join(hq.hq_dir, 'logs',
                                            f'{trial_id}-1.out')

    with open(task_path, encoding='utf-8') as file_handle:
        task = json.load(file_handle)
    assert task['image'] == IMAGE
    assert task['image_archive'] == os.path.join(config['experiment_filestore'],
                                                 'runner-images',
                                                 'a' * 64 + '.tar')
    assert task['start_marker'] == hq._get_start_marker_path(trial_id)
    assert task['volumes'] == [
        config['experiment_filestore'], config['report_filestore']
    ]
    assert task['container_timeout'] == kwargs['time_limit_seconds']
    assert task['memory_mb'] == kwargs['memory_mb']
    assert task['environment']['TRIAL_ID'] == str(trial_id)
    assert task['environment']['INSTANCE_NAME'] == kwargs['name']

    assert get_trial(trial_id).time_started is None
    # Submitted trials aren't submitted again.
    hq.schedule()
    assert len(client.submissions) == 1


def test_started_from_marker(hq, config, client):
    """Tests that a trial is marked started at the time its start marker
    records, and not ended while its job runs."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    client.set_state(1, 'running')
    time_started = NOW - datetime.timedelta(seconds=30)
    write_start_marker(hq, trial_id, time_started)
    hq.schedule()

    trial = get_trial(trial_id)
    assert trial.time_started.replace(
        tzinfo=datetime.timezone.utc) == time_started
    assert trial.time_ended is None


def test_start_marker_in_future(hq, config, client):
    """Tests that a start marker from a worker whose clock runs ahead doesn't
    place the start in the future."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    client.set_state(1, 'running')
    write_start_marker(hq, trial_id, NOW + datetime.timedelta(minutes=5))
    hq.schedule()

    assert get_trial(trial_id).time_started.replace(
        tzinfo=datetime.timezone.utc) == NOW


def test_ended_when_job_stops(hq, config, client):
    """Tests that a trial is marked ended once its job stops, and that one
    which starts and ends between two passes gets both times."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    write_start_marker(hq, trial_id, NOW - datetime.timedelta(hours=1))
    client.set_state(1, 'finished')
    hq.schedule()

    trial = get_trial(trial_id)
    assert trial.time_started is not None
    assert trial.time_ended.replace(tzinfo=datetime.timezone.utc) == NOW
    assert not hq.jobs
    assert len(client.submissions) == 1


def test_failed_trial_not_resubmitted(hq, config, client):
    """Tests that a trial whose job fails after starting its container is
    ended rather than run again."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    write_start_marker(hq, trial_id, NOW)
    client.set_state(1, 'failed')
    hq.schedule()

    assert get_trial(trial_id).time_ended is not None
    assert len(client.submissions) == 1


def test_failed_launch_retried(hq, config, client):
    """Tests that a trial whose job fails before starting its container is
    submitted again, with logs of its own."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    client.set_state(1, 'failed')
    hq.schedule()

    trial = get_trial(trial_id)
    assert trial.time_started is None and trial.time_ended is None
    assert len(client.submissions) == 2
    assert client.submissions[1][1]['stdout'].endswith(f'{trial_id}-2.out')


def test_failed_launch_given_up(hq, config, client):
    """Tests that a trial which fails to start too many times is ended without
    being started, so that the experiment can finish and the measurer skips
    it."""
    trial_id, = add_trials(config, 1)
    for job_id in range(1, hq_scheduler.MAX_LAUNCH_ATTEMPTS + 1):
        hq.schedule()
        client.set_state(job_id, 'failed')
    hq.schedule()

    trial = get_trial(trial_id)
    assert trial.time_started is None
    assert trial.time_ended is not None
    assert len(client.submissions) == hq_scheduler.MAX_LAUNCH_ATTEMPTS
    assert scheduler.all_trials_ended(config['experiment'])


def test_lost_job(hq, config, client):
    """Tests that a job the server no longer knows of counts as stopped."""
    trial_id, = add_trials(config, 1)
    hq.schedule()
    write_start_marker(hq, trial_id, NOW)
    del client.jobs[1]
    hq.schedule()

    assert get_trial(trial_id).time_ended is not None


def test_capacity(hq, config, client):
    """Tests that runners_cpus caps the cpus of the trials in flight, queued or
    running."""
    config['runners_cpus'] = 4
    config['runner_num_cpu_cores'] = 2
    trial_ids = add_trials(config, 5)
    hq.schedule()
    assert len(client.submissions) == 2

    hq.schedule()
    assert len(client.submissions) == 2

    submitted_trial_id = next(iter(hq.jobs))
    write_start_marker(hq, submitted_trial_id, NOW)
    client.set_state(hq.jobs[submitted_trial_id], 'finished')
    hq.schedule()
    assert len(client.submissions) == 3
    assert len(hq.jobs) == 2
    assert set(hq.jobs) <= set(trial_ids)


def test_export_failure(hq, config, client):
    """Tests that trials whose image can't be exported wait, while the others
    go ahead."""
    add_trials(config, 1, IMAGE)
    other_trial_id, = add_trials(config, 1, OTHER_IMAGE)
    with mock.patch('experiment.hq_scheduler.export_runner_image',
                    side_effect=lambda image, _: image == OTHER_IMAGE):
        hq.schedule()

    assert list(hq.jobs) == [other_trial_id]


def test_submit_error(hq, config, client):
    """Tests that a failing submission stops the pass, since the server is
    probably down, and that the trials are submitted on a later one."""
    add_trials(config, 3)
    client.submit_error = hyperqueue.HyperQueueError('Connection refused')
    hq.schedule()
    assert not hq.jobs

    client.submit_error = None
    hq.schedule()
    assert len(hq.jobs) == 3


def test_export_runner_image(tmp_path):
    """Tests that an image is saved aside and renamed into place, readable by
    the workers."""
    store = str(tmp_path / 'images')

    def save(command, **_):
        with open(command[3], 'w', encoding='utf-8') as file_handle:
            file_handle.write('archive')
        return new_process.ProcessResult(0, '', False)

    with mock.patch('common.new_process.execute',
                    side_effect=save) as mocked_execute:
        assert hq_scheduler.export_runner_image(IMAGE, store)
        # Already exported.
        assert hq_scheduler.export_runner_image(IMAGE, store)

    assert mocked_execute.call_count == 1
    assert mocked_execute.call_args[0][0][:3] == ['docker', 'save', '--output']
    assert mocked_execute.call_args[0][0][-1] == IMAGE
    archive = hq_scheduler.get_image_archive_path(store, IMAGE)
    assert os.listdir(store) == [os.path.basename(archive)]
    assert os.stat(archive).st_mode & 0o777 == 0o644


def test_export_runner_image_failure(tmp_path):
    """Tests that a failed export leaves nothing behind for workers to load."""
    store = str(tmp_path / 'images')

    def save(command, **_):
        with open(command[3], 'w', encoding='utf-8') as file_handle:
            file_handle.write('partial')
        return new_process.ProcessResult(1, 'no space left', False)

    with mock.patch('common.new_process.execute', side_effect=save):
        assert not hq_scheduler.export_runner_image(IMAGE, store)
    assert not os.listdir(store)


def test_get_trial_jobs():
    """Tests that an experiment's jobs are found by name, without those of an
    experiment whose name merely starts the same."""
    client = FakeClient()
    for name in ('r-exp-1', 'r-exp-12', 'r-exp-other-1', 'r-other-1',
                 'wasmfuzz'):
        client.submit([], name=name)
    jobs = hq_scheduler.get_trial_jobs(client, 'exp')
    assert [job.name for job in jobs] == ['r-exp-1', 'r-exp-12']
