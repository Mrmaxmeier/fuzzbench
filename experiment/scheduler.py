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
"""Code for starting and ending trials."""
import datetime
import multiprocessing
import os
import sys
import random
import time

import jinja2

from common import benchmark_utils
from common import experiment_utils
from common import local_instance
from common import logs
from common import utils
from common import yaml_utils
from database import models
from database import utils as db_utils

# Give the trial runner a little extra time to shut down and account for how
# long it can take to actually start running once an instance is started. 5
# minutes is an arbitrary amount of time.
GRACE_TIME_SECONDS = 5 * 60

FAIL_WAIT_SECONDS = 10 * 60
# Poll interval while trials are running or waiting for free local CPUs.
SCHEDULE_POLL_SECONDS = 60

logger = logs.Logger()  # pylint: disable=invalid-name

RESOURCES_DIR = os.path.join(utils.ROOT_DIR, 'experiment', 'resources')

JINJA_ENV = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    loader=jinja2.FileSystemLoader(RESOURCES_DIR),
)

STARTED_TRIALS_FILTER = models.Trial.time_started.isnot(None)


def datetime_now() -> datetime.datetime:
    """Return datetime.datetime.utcnow(). This function is needed for
    mocking."""
    return datetime.datetime.now(
        datetime.timezone.utc).replace(tzinfo=datetime.timezone.utc)


# TODO(metzman): Figure out what are the best practices for the functions which
# must return sqlalchemy.orm.Query. Importing it just for annotation might be
# confusing to readers. There may also be weird situations where it is
# acceptable to use a list or query (because of duck typing) but type hints
# prevents us unless handled intelligently.
def get_nonpreempted_trials(experiment: str):
    """Returns a query of trials in |experiment|."""
    not_preempted_filter = models.Trial.preempted == False  # pylint: disable=singleton-comparison
    return get_experiment_trials(experiment).filter(not_preempted_filter)


def get_pending_trials(experiment: str):
    """Returns trial entities from |experiment| that have not run yet."""
    return get_nonpreempted_trials(experiment).filter(~STARTED_TRIALS_FILTER)


def get_running_trials(experiment: str):
    """Returns trial entities from |experiment| that have been marked started
    but not marked ended."""
    return get_nonpreempted_trials(experiment).filter(
        models.Trial.time_ended.is_(None), STARTED_TRIALS_FILTER)


def get_expired_trials(experiment: str, max_total_time: int):
    """Returns trial entities from |experiment| that have not ended and were
    started more than |max_total_time| + |GRACE_TIME_SECONDS| ago."""
    earliest_nonexpired_dt = datetime_now() - datetime.timedelta(
        seconds=max_total_time + GRACE_TIME_SECONDS)

    return get_nonpreempted_trials(experiment).filter(
        models.Trial.time_started <= earliest_nonexpired_dt).filter(
            models.Trial.time_ended.is_(None))


def all_trials_ended(experiment: str) -> bool:
    """Return a bool if there are any trials in |experiment| that have not
    started."""
    try:
        return not get_experiment_trials(experiment).filter(
            models.Trial.time_ended.is_(None)).all()
    except RuntimeError:
        logger.error('Failed to check whether all trials ended.')
        return False


def end_expired_trials(experiment_config: dict, core_allocation: dict):
    """Get all expired trials, end them and return them."""
    trials_past_expiry = get_expired_trials(experiment_config['experiment'],
                                            experiment_config['max_total_time'])
    expired_trial_ids = []
    current_dt = datetime_now()
    for trial in trials_past_expiry:
        trial_id = trial.id
        expired_trial_ids.append(trial_id)
        trial.time_ended = current_dt

    # Bail out here because trials_past_expiry will be truthy until evaluated.
    if not expired_trial_ids:
        return

    if core_allocation is not None:
        for cpuset, trial_id in core_allocation.items():
            if trial_id in expired_trial_ids:
                core_allocation[cpuset] = None

    db_utils.bulk_save(trials_past_expiry)


def get_experiment_trials(experiment: str):
    """Returns a query for trials in |experiment| ordered by id."""
    with db_utils.session_scope() as session:
        return session.query(models.Trial).filter(
            models.Trial.experiment == experiment).order_by(models.Trial.id)


def get_started_trials(experiment: str):
    """Returns a query for trials in |experiment| that have been started."""
    return get_experiment_trials(experiment).filter(STARTED_TRIALS_FILTER)


def get_last_trial_time_started(experiment: str):
    """Returns the time_started of the last trial that was started in
    |experiment|. This function cannot be called if there are any unstarted
    (e.g. pending trials). It will raise an assertion failure if there are any
    pending trials because it does not make sense to call this function before
    that time."""
    assert get_pending_trials(experiment).first() is None
    # Don't use get_experiment_trials because it already orders the results by
    # id.
    with db_utils.session_scope() as session:
        last_trial = session.query(models.Trial).filter(
            models.Trial.experiment == experiment,
            STARTED_TRIALS_FILTER).order_by(
                models.Trial.time_started.desc()).first()
        return last_trial.time_started


def any_pending_trials(experiment):
    """Returns True if there are any pending trials in |experiment|."""
    return bool(get_pending_trials(experiment).first())


def any_running_trials(experiment):
    """Returns True if there are any running trials in |experiment|."""
    return bool(get_running_trials(experiment).first())


def schedule(experiment_config: dict, pool, core_allocation=None):
    """Gets all pending trials for the current experiment and then schedules
    those that are possible."""
    logger.info('Finding trials to schedule.')

    # End expired trials
    end_expired_trials(experiment_config, core_allocation)

    # Start pending trials.
    pending_trials = list(get_pending_trials(experiment_config['experiment']))
    started_trials = start_trials(pending_trials, experiment_config, pool,
                                  core_allocation)
    return started_trials


def schedule_loop(experiment_config: dict):
    """Continuously run the scheduler until there is nothing left to schedule.
    Note that this should not be called unless
    multiprocessing.set_start_method('spawn') was called first. Otherwise it
    will use fork to create the Pool which breaks logging."""
    # Create the thread pool once and reuse it to avoid leaking threads and
    # other issues.
    logger.info('Starting scheduler.')
    local_experiment = experiment_utils.is_local_experiment()
    pool_args = ()
    core_allocation = None
    runners_cpus = experiment_config['runners_cpus']
    if runners_cpus is not None:
        if local_experiment:
            runner_num_cpu_cores = experiment_config['runner_num_cpu_cores']
            processes = runners_cpus // runner_num_cpu_cores
            logger.info('Scheduling runners from core 0 to %d.',
                        runner_num_cpu_cores * processes - 1)
            core_allocation = {}
            for cpu in range(0, runner_num_cpu_cores * processes,
                             runner_num_cpu_cores):
                core_allocation[
                    f'{cpu}-{cpu + runner_num_cpu_cores - 1}'] = None
            pool_args = (processes,)
        else:
            pool_args = (runners_cpus,)

    experiment = experiment_config['experiment']
    with multiprocessing.Pool(*pool_args) as pool:
        while not all_trials_ended(experiment):
            started_trials = []
            scheduling_error = False
            try:
                started_trials = schedule(experiment_config, pool,
                                          core_allocation)
            except Exception:  # pylint: disable=broad-except
                logger.error('Error occurred during scheduling.')
                scheduling_error = True

            # Back off on unexpected errors or when pending trials could not be
            # started (e.g. cloud instance quota). Otherwise poll periodically
            # while trials are still running.
            if scheduling_error or (not started_trials and
                                    any_pending_trials(experiment)):
                time.sleep(FAIL_WAIT_SECONDS)
            elif not all_trials_ended(experiment):
                time.sleep(SCHEDULE_POLL_SECONDS)

    logger.info('Finished scheduling.')


def update_started_trials(trial_proxies, trial_id_mapping, core_allocation):
    """Update started trials in |trial_id_mapping| with results from
    |trial_proxies| and save the updated trials."""
    # Map proxies back to trials and mark trials as started when proxies were
    # marked as such.
    started_trials = []
    for proxy in trial_proxies:
        if not proxy:
            continue
        trial = trial_id_mapping[proxy.id]
        trial.time_started = proxy.time_started

        if core_allocation is not None:
            core_allocation[proxy.cpuset] = proxy.id

        started_trials.append(trial)
    if started_trials:
        db_utils.add_all(started_trials)
    return started_trials


def start_trials(trials, experiment_config: dict, pool, core_allocation=None):
    """Start all |trials| that are possible to start. Marks the ones that were
    started as started."""
    logger.info('Starting trials.')
    trial_id_mapping = {trial.id: trial for trial in trials}

    # Shuffle trials so that we don't create trials for the same fuzzer
    # benchmark close to one another. This *may* make the preemption rate more
    # evenly distributed across fuzzer benchmarks which will help if we don't
    # end up completing the target number of trials. A more rigourous approach
    # where we increase the distance in between trials for the same
    # fuzzer-benchmark might be useful.
    shuffled_trials = list(trial_id_mapping.values())
    random.shuffle(shuffled_trials)

    free_cpusets = [
        cpuset for cpuset, trial_id in core_allocation.items()
        if trial_id is None
    ] if core_allocation is not None else None

    start_trial_args = []
    for index, trial in enumerate(shuffled_trials):
        if free_cpusets is not None and index >= len(free_cpusets):
            break

        start_trial_args += [
            (TrialProxy(trial), experiment_config,
             free_cpusets[index] if free_cpusets is not None else None)
        ]

    started_trial_proxies = pool.starmap(_start_trial, start_trial_args)
    started_trials = update_started_trials(started_trial_proxies,
                                           trial_id_mapping, core_allocation)
    logger.info(f'Started {len(started_trials)} trials.')
    return started_trials


class TrialProxy:  # pylint: disable=too-many-instance-attributes
    """A proxy object for a model.Trial. TrialProxy's allow these fields to be
    set and retreived without making any database calls."""

    def __init__(self, trial):
        self.id = trial.id  # pylint: disable=invalid-name
        self.fuzzer = trial.fuzzer
        self.benchmark = trial.benchmark
        self.time_started = trial.time_started
        self.time_ended = trial.time_ended
        self.preemptible = trial.preemptible
        self.cpuset = None
        self.trial_group_num = trial.trial_group_num


def _initialize_logs(experiment):
    """Initialize logs. This must be called on process start."""
    logs.initialize(
        default_extras={
            'experiment': experiment,
            'component': 'dispatcher',
            'subcomponent': 'scheduler'
        })


def _start_trial(trial: TrialProxy, experiment_config: dict, cpuset=None):
    """Start a trial if possible. Mark the trial as started if it was and then
    return the Trial. Otherwise return None."""
    # TODO(metzman): Add support for early exit (trial_creation_failed) that was
    # removed when this started using multiprocessing.
    # Also, support batched saves of trials (with a queue, like measurer uses)
    # so that measuring a schedule doesn't require waiting until the map call
    # that calls this function completely terminates.
    _initialize_logs(experiment_config['experiment'])
    logger.info('Start trial %d.', trial.id)
    started = create_trial_instance(trial.fuzzer, trial.benchmark, trial.id,
                                    experiment_config, trial.preemptible,
                                    cpuset, trial.trial_group_num)
    if started:
        trial.time_started = datetime_now()
        trial.cpuset = cpuset
        return trial
    logger.info('Trial: %d not started.', trial.id)
    return None


def render_startup_script_template(  # pylint: disable=too-many-arguments
        instance_name: str,
        fuzzer: str,
        benchmark: str,
        trial_id: int,
        trial_group_num: int,
        experiment_config: dict,
        cpuset=None):
    """Render the startup script using the template and the parameters
    provided and return the result."""
    experiment = experiment_config['experiment']
    docker_image_url = benchmark_utils.get_runner_image_url(
        experiment, benchmark, fuzzer, experiment_config['docker_registry'])
    fuzz_target = benchmark_utils.get_fuzz_target(benchmark)

    template = JINJA_ENV.get_template('runner-startup-script-template.sh')
    kwargs = {
        'instance_name': instance_name,
        'benchmark': benchmark,
        'experiment': experiment,
        'fuzzer': fuzzer,
        'trial_id': trial_id,
        'trial_group_num': trial_group_num,
        'micro_experiment': experiment_config['micro_experiment'],
        'max_total_time': experiment_config['max_total_time'],
        'snapshot_period': experiment_config['snapshot_period'],
        'experiment_filestore': experiment_config['experiment_filestore'],
        'report_filestore': experiment_config['report_filestore'],
        'fuzz_target': fuzz_target,
        'docker_image_url': docker_image_url,
        'docker_registry': experiment_config['docker_registry'],
        'local_experiment': True,
        'no_seeds': experiment_config['no_seeds'],
        'no_dictionaries': experiment_config['no_dictionaries'],
        'oss_fuzz_corpus': experiment_config['oss_fuzz_corpus'],
        'num_cpu_cores': experiment_config['runner_num_cpu_cores'],
        'private': experiment_config['private'],
        'cpuset': cpuset,
        'custom_seed_corpus_dir': experiment_config['custom_seed_corpus_dir'],
    }

    return template.render(**kwargs)


def create_trial_instance(  # pylint: disable=too-many-arguments
        fuzzer: str,
        benchmark: str,
        trial_id: int,
        experiment_config: dict,
        preemptible: bool,
        cpuset=None,
        trial_group_num: int = 0) -> bool:
    """Create or start a trial instance for a specific
    trial_id,fuzzer,benchmark."""
    del preemptible  # Kept for API compatibility; local runs ignore preemptible.
    instance_name = experiment_utils.get_trial_instance_name(
        experiment_config['experiment'], trial_id)
    startup_script = render_startup_script_template(instance_name, fuzzer,
                                                    benchmark, trial_id,
                                                    trial_group_num,
                                                    experiment_config, cpuset)
    startup_script_path = f'/tmp/{instance_name}-start-docker.sh'
    with open(startup_script_path, 'w', encoding='utf-8') as file_handle:
        file_handle.write(startup_script)

    return local_instance.run_local_instance(startup_script_path)


def main():
    """Main function for running scheduler independently."""
    logs.initialize(default_extras={
        'component': 'dispatcher',
        'subcomponent': 'scheduler'
    })

    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <experiment_config.yaml>')
        return 1

    experiment_config = yaml_utils.read(sys.argv[1])
    schedule_loop(experiment_config)

    return 0


if __name__ == '__main__':
    sys.exit(main())
