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
"""Tests for scheduler.py"""
import datetime
from multiprocessing.pool import ThreadPool
import time
from unittest import mock

import pytest

from database import models
from database import utils as db_utils
from experiment import scheduler

FUZZER = 'fuzzer'
BENCHMARK = 'bench'
ARBITRARY_DATETIME = datetime.datetime(2020, 1, 1)

# pylint: disable=invalid-name,unused-argument,redefined-outer-name,too-many-arguments,no-value-for-parameter,protected-access


def get_other_experiment_name(experiment_config):
    """Returns the name of an experiment different from the one in
    |experiment_config|."""
    return experiment_config['experiment'] + 'other'


def create_experiments(experiment_config):
    """Create the experiment experiment entity for the experiment in
    |experiment_config| and create another one and save the results to the
    db."""
    other_experiment_name = get_other_experiment_name(experiment_config)
    db_utils.add_all([
        models.Experiment(name=experiment_config['experiment']),
        models.Experiment(name=other_experiment_name)
    ])


@pytest.fixture
def pending_trials(db, experiment_config):
    """Adds trials to the database and returns pending trials."""
    create_experiments(experiment_config)

    def create_trial(experiment, time_started=None, time_ended=None):
        """Creates a database trial."""
        return models.Trial(experiment=experiment,
                            benchmark=BENCHMARK,
                            fuzzer=FUZZER,
                            time_started=time_started,
                            time_ended=time_ended)

    our_pending_trials = [
        create_trial(experiment_config['experiment']),
        create_trial(experiment_config['experiment'])
    ]
    other_experiment_name = get_other_experiment_name(experiment_config)
    other_trials = [
        create_trial(other_experiment_name),
        create_trial(experiment_config['experiment'], ARBITRARY_DATETIME),
        create_trial(experiment_config['experiment'], ARBITRARY_DATETIME)
    ]
    db_utils.add_all(other_trials + our_pending_trials)
    our_trial_ids = [trial.id for trial in our_pending_trials]
    with db_utils.session_scope() as session:
        return session.query(models.Trial).filter(
            models.Trial.id.in_(our_trial_ids))


@pytest.mark.parametrize(
    'benchmark,expected_image,expected_target',
    [('benchmark1',
      'localhost/fuzzbench/runners/fuzzer-a/benchmark1:test-experiment',
      'fuzz-target'),
     ('bloaty_fuzz_target',
      'localhost/fuzzbench/runners/fuzzer-a/bloaty_fuzz_target:test-experiment',
      'fuzz_target')])
def test_create_trial_instance(benchmark, expected_image, expected_target,
                               experiment_config):
    """Test that create_trial_instance runs a local instance and creates a
    startup script for the trial, as we expect it to."""
    expected_startup_script = '''# Start docker.


docker run \\
--privileged --cpus=1 --rm \\
\\
-e INSTANCE_NAME=r-test-experiment-9 \\
-e FUZZER=fuzzer-a \\
-e BENCHMARK={benchmark} \\
-e EXPERIMENT=test-experiment \\
-e TRIAL_ID=9 \\
-e TRIAL_GROUP_NUM=0 \\
-e MICRO_EXPERIMENT=False \\
-e MAX_TOTAL_TIME=86400 \\
-e SNAPSHOT_PERIOD=900 \\
-e NO_SEEDS=False \\
-e NO_DICTIONARIES=False \\
-e OSS_FUZZ_CORPUS=False \\
-e CUSTOM_SEED_CORPUS_DIR=None \\
-e DOCKER_REGISTRY=localhost/fuzzbench \\
-e EXPERIMENT_FILESTORE=/tmp/experiment-data -v /tmp/experiment-data:/tmp/experiment-data \\
-e REPORT_FILESTORE=/tmp/web-reports -v /tmp/web-reports:/tmp/web-reports \\
-e FUZZ_TARGET={oss_fuzz_target} \\
-e PRIVATE=False \\
-e LOCAL_EXPERIMENT=True \\
\\
--shm-size=2g \\
--cap-add SYS_NICE --cap-add SYS_PTRACE \\
--security-opt seccomp=unconfined \\
{docker_image_url} 2>&1 | tee /tmp/runner-log-9.txt'''
    with mock.patch('common.benchmark_utils.get_fuzz_target',
                    return_value=expected_target):
        _test_create_trial_instance(benchmark, expected_image, expected_target,
                                    expected_startup_script, experiment_config)


@mock.patch('common.local_instance.run_local_instance')
def _test_create_trial_instance(  # pylint: disable=too-many-locals
        benchmark, expected_image, expected_target, expected_startup_script,
        experiment_config, mocked_run_local_instance):
    """Test that create_trial_instance invokes run_local_instance and creates a
    startup script for the instance, as we expect it to."""
    fuzzer_param = 'fuzzer-a'
    trial = 9
    mocked_run_local_instance.return_value = True
    scheduler.create_trial_instance(fuzzer_param, benchmark, trial,
                                    experiment_config, False)
    instance_name = 'r-test-experiment-9'
    expected_startup_script_path = f'/tmp/{instance_name}-start-docker.sh'

    mocked_run_local_instance.assert_called_with(expected_startup_script_path)

    with open(expected_startup_script_path, encoding='utf-8') as file_handle:
        content = file_handle.read()
        check_from = '# Start docker.'
        assert check_from in content
        script_for_docker = content[content.find(check_from):]
        assert script_for_docker == expected_startup_script.format(
            benchmark=benchmark,
            oss_fuzz_target=expected_target,
            docker_image_url=expected_image)


@mock.patch('common.local_instance.run_local_instance')
@mock.patch('common.benchmark_utils.get_fuzz_target',
            return_value='fuzz-target')
def test_start_trials_not_started(mocked_run_local_instance, pending_trials,
                                  experiment_config):
    """Test that start_trials returns an empty list nothing when all trials fail
    to be created/started."""
    mocked_run_local_instance.return_value = False
    with ThreadPool() as pool:
        result = scheduler.start_trials(pending_trials, experiment_config, pool)
    assert not result


@mock.patch('common.local_instance.run_local_instance', return_value=True)
@mock.patch('experiment.scheduler.datetime_now')
@mock.patch('common.benchmark_utils.get_fuzz_target',
            return_value='fuzz-target')
def test_schedule(mocked_get_fuzz_target, mocked_datetime_now,
                  mocked_run_local_instance, pending_trials, experiment_config):
    """Tests that schedule() ends expired trials and starts new ones as
    needed."""
    del mocked_get_fuzz_target, mocked_run_local_instance
    experiment = experiment_config['experiment']
    with db_utils.session_scope() as session:
        datetimes_first_experiments_started = [
            trial.time_started for trial in session.query(models.Trial).filter(
                models.Trial.experiment == experiment).filter(
                    models.Trial.time_started.isnot(None))
        ]

    mocked_datetime_now.return_value = (
        max(datetimes_first_experiments_started) +
        datetime.timedelta(seconds=(experiment_config['max_total_time'] +
                                    scheduler.GRACE_TIME_SECONDS * 2)))

    with ThreadPool() as pool:
        scheduler.schedule(experiment_config, pool)
    with db_utils.session_scope() as session:
        assert session.query(models.Trial).filter(
            models.Trial.time_started.in_(
                datetimes_first_experiments_started)).all() == (session.query(
                    models.Trial).filter(
                        models.Trial.time_ended.isnot(None)).all())

    assert pending_trials.filter(
        models.Trial.time_started.isnot(None)).all() == pending_trials.all()


def test_get_last_trial_time_started(db, experiment_config):
    """Tests that get_last_trial_time_started returns the time_started of the
    last trial to be started."""
    experiment = experiment_config['experiment']
    db_utils.add_all([
        models.Experiment(name=experiment),
    ])
    trial1 = models.Trial(experiment=experiment,
                          benchmark=BENCHMARK,
                          fuzzer=FUZZER)
    trial2 = models.Trial(experiment=experiment,
                          benchmark=BENCHMARK,
                          fuzzer=FUZZER)
    first_time = datetime.datetime.fromtimestamp(time.mktime(time.gmtime(0)))
    trial1.time_started = first_time
    last_time_started = first_time + datetime.timedelta(days=1)
    trial2.time_started = last_time_started
    trials = [trial1, trial2]
    db_utils.add_all(trials)

    assert scheduler.get_last_trial_time_started(
        experiment) == last_time_started


def test_get_last_trial_time_started_called_early(db, experiment_config):
    """Tests that get_last_trial_time_started raises an exception if called
    while there are still pending trials."""
    experiment = experiment_config['experiment']
    db_utils.add_all([
        models.Experiment(name=experiment),
    ])
    trial1 = models.Trial(experiment=experiment,
                          benchmark=BENCHMARK,
                          fuzzer=FUZZER)
    trial2 = models.Trial(experiment=experiment,
                          benchmark=BENCHMARK,
                          fuzzer=FUZZER)
    first_time = datetime.datetime.fromtimestamp(time.mktime(time.gmtime(0)))
    trial1.time_started = first_time
    trials = [trial1, trial2]
    db_utils.add_all(trials)
    with pytest.raises(AssertionError):
        scheduler.get_last_trial_time_started(experiment)
