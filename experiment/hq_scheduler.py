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
"""Runs an experiment's trials as HyperQueue jobs.

This is the HyperQueue executor's counterpart of scheduler.schedule_loop,
which starts every trial's container on the dispatcher's own host. Here each
trial is submitted as a job to a HyperQueue server, which runs it on a worker
with free cpus through experiment/resources/hq_trial.py. Everything the two
sides exchange goes through the experiment filestore, which the workers must
see at the same path as the dispatcher.

The executors differ in when a trial counts as started. A local container
starts when it is launched, so the local scheduler marks the trial started on
launch. A HyperQueue job can wait in the queue for any length of time, and the
measurer times a trial's cycles from its start, so here a trial is only marked
started once its container really starts. The worker records that moment in a
start marker on the filestore, and this reads it back.

Runner images reach the workers through the filestore too. Each is exported
once, under the digest recorded for its trials, to an archive that workers
load it from.
"""

import collections
import datetime
import json
import os
import random
import shutil
import threading
import time
from multiprocessing.pool import ThreadPool
from typing import Dict, Iterable, List, Optional, Set

from common import benchmark_utils
from common import experiment_utils
from common import hyperqueue
from common import logs
from common import new_process
from database import models
from database import utils as db_utils
from experiment import scheduler

logger = logs.Logger()  # pylint: disable=invalid-name

# The experiment's HyperQueue files live under this directory of its filestore
# directory: task files, the jobs' logs and the trials' start markers.
HQ_DIRNAME = 'hq'
WRAPPER_NAME = 'hq_trial.py'

# Exported runner images live under this directory of the experiment filestore,
# beside the experiments rather than in one, so that experiments running the
# same image share its archive. Archives are named by digest and never change.
IMAGE_STORE_DIRNAME = 'runner-images'
EXPORT_THREADS = 4

# Time allowed on top of the trial's own for a worker to load the runner image
# before starting it. The first trials to land on a fresh worker all load at
# once.
IMAGE_LOAD_SECONDS = 15 * 60

# A trial whose job fails before starting its container is resubmitted this
# many times in total before it is given up on. Giving up ends the trial
# without ever starting it, so the measurer never measures it.
MAX_LAUNCH_ATTEMPTS = 3


def get_image_store_dir(experiment_filestore: str) -> str:
    """Returns the directory that runner images are exported to."""
    return os.path.join(experiment_filestore, IMAGE_STORE_DIRNAME)


def get_image_archive_path(image_store_dir: str, image: str) -> str:
    """Returns the path of the archive that |image| is exported to."""
    return os.path.join(image_store_dir, image.split(':')[-1] + '.tar')


def export_runner_image(image: str, image_store_dir: str) -> bool:
    """Exports |image| to its archive unless it's already there. Returns
    whether the archive exists."""
    archive = get_image_archive_path(image_store_dir, image)
    if os.path.exists(archive):
        return True

    os.makedirs(image_store_dir, exist_ok=True)
    # Written aside and renamed into place, so that a worker never loads a
    # partial archive.
    temp_archive = f'{archive}.tmp-{os.getpid()}-{threading.get_ident()}'
    logger.info('Exporting %s to %s.', image, archive)
    try:
        result = new_process.execute(
            ['docker', 'save', '--output', temp_archive, image],
            expect_zero=False)
        if result.retcode != 0:
            logger.error('Failed to export %s: %s', image, result.output)
            return False
        os.chmod(temp_archive, 0o644)
        os.rename(temp_archive, archive)
        return True
    finally:
        if os.path.exists(temp_archive):
            os.remove(temp_archive)


def _read_start_marker(path: str) -> Optional[datetime.datetime]:
    """Returns the time recorded in the start marker at |path|, or None if
    there isn't one."""
    try:
        with open(path, encoding='utf-8') as file_handle:
            content = file_handle.read().strip()
    except FileNotFoundError:
        return None
    try:
        time_started = datetime.datetime.fromisoformat(content)
    except ValueError:
        logger.error('Invalid start marker %s: %r.', path, content)
        return None
    if time_started.tzinfo is None:
        time_started = time_started.replace(tzinfo=datetime.timezone.utc)
    return time_started


class HyperQueueScheduler:  # pylint: disable=too-many-instance-attributes
    """Submits the trials of an experiment to HyperQueue and keeps their
    start and end times up to date with their jobs."""

    def __init__(self,
                 experiment_config: dict,
                 client: Optional[hyperqueue.Client] = None):
        self.config = experiment_config
        self.experiment = experiment_config['experiment']
        self.client = client or hyperqueue.Client()
        experiment_filestore = experiment_config['experiment_filestore']
        self.hq_dir = os.path.join(experiment_filestore, self.experiment,
                                   HQ_DIRNAME)
        self.image_store_dir = get_image_store_dir(experiment_filestore)
        self.wrapper_path = os.path.join(self.hq_dir, WRAPPER_NAME)
        # Trial id -> id of the job running the trial's current attempt. A
        # trial is here from submission until its job stops.
        self.jobs: Dict[int, int] = {}
        self.launch_attempts: Dict[int, int] = collections.Counter()
        self.exported_images: Set[str] = set()

    def set_up(self):
        """Creates the experiment's HyperQueue directory and puts the trial
        wrapper in it for the workers."""
        for subdir in ('tasks', 'logs', 'started'):
            os.makedirs(os.path.join(self.hq_dir, subdir), exist_ok=True)
        shutil.copy(os.path.join(scheduler.RESOURCES_DIR, WRAPPER_NAME),
                    self.wrapper_path)

    def _get_start_marker_path(self, trial_id: int) -> str:
        return os.path.join(self.hq_dir, 'started', str(trial_id))

    def _get_log_path(self, trial_id: int, attempt: int) -> str:
        """Returns the base path of the logs of |trial_id|'s |attempt|."""
        return os.path.join(self.hq_dir, 'logs', f'{trial_id}-{attempt}')

    def schedule(self):
        """Updates trials from their jobs, then submits the trials that can be
        submitted."""
        self.update_trials()
        self.submit_trials()

    def update_trials(self):
        """Marks trials started once their containers have started, and ended
        once their jobs have stopped."""
        if not self.jobs:
            return
        jobs = {job.job_id: job for job in self.client.list_jobs()}
        now = scheduler.datetime_now()
        trials = scheduler.get_experiment_trials(self.experiment).filter(
            models.Trial.id.in_(list(self.jobs)))

        changed_trials = []
        for trial in trials:
            job = jobs.get(self.jobs[trial.id])
            changed = False
            if trial.time_started is None:
                time_started = _read_start_marker(
                    self._get_start_marker_path(trial.id))
                if time_started is not None:
                    # The marker comes from the worker's clock. A worker
                    # running ahead mustn't place the start in the future.
                    trial.time_started = min(time_started, now)
                    changed = True

            if job is None or job.is_done:
                changed = self._end_job(trial, job, now) or changed
            if changed:
                changed_trials.append(trial)

        if changed_trials:
            db_utils.add_all(changed_trials)

    def _end_job(self, trial, job: Optional[hyperqueue.Job],
                 now: datetime.datetime) -> bool:
        """Handles the end of |trial|'s |job|, which is None if the server has
        lost it. Returns whether |trial| changed."""
        job_id = self.jobs.pop(trial.id)
        log_path = self._get_log_path(trial.id,
                                      self.launch_attempts[trial.id]) + '.out'
        if job is None:
            logger.error('HyperQueue lost job %d of trial %d.', job_id,
                         trial.id)

        if trial.time_started is not None:
            if job is None or not job.succeeded:
                logger.error('Trial %d did not finish cleanly. See %s.',
                             trial.id, log_path)
            trial.time_ended = now
            return True

        if self.launch_attempts[trial.id] >= MAX_LAUNCH_ATTEMPTS:
            logger.error(
                'Trial %d failed to start %d times, giving up. See %s.',
                trial.id, MAX_LAUNCH_ATTEMPTS, log_path)
            trial.time_ended = now
            return True

        logger.warning('Trial %d failed to start, will retry. See %s.',
                       trial.id, log_path)
        return False

    def _get_capacity(self) -> Optional[int]:
        """Returns how many more trials may be submitted, or None if there is
        no limit. runners_cpus caps the cpus of the trials in flight, queued or
        running, so that an experiment can be kept to a share of a cluster."""
        runners_cpus = self.config.get('runners_cpus')
        if not runners_cpus:
            return None
        max_jobs = runners_cpus // self.config['runner_num_cpu_cores']
        return max(max_jobs - len(self.jobs), 0)

    def submit_trials(self):
        """Submits the trials that haven't been submitted yet, as far as
        capacity allows."""
        pending_trials = [
            trial for trial in scheduler.get_experiment_trials(
                self.experiment).filter(models.Trial.time_started.is_(None),
                                        models.Trial.time_ended.is_(None))
            if trial.id not in self.jobs
        ]
        # Shuffled for the same reason the local scheduler does it.
        random.shuffle(pending_trials)
        capacity = self._get_capacity()
        if capacity is not None:
            pending_trials = pending_trials[:capacity]
        if not pending_trials:
            return

        self.export_images(
            trial.runner_image_digest for trial in pending_trials)
        submitted = 0
        for trial in pending_trials:
            if trial.runner_image_digest not in self.exported_images:
                continue
            try:
                self.submit_trial(trial)
            except hyperqueue.HyperQueueError as error:
                # Most likely the server is unreachable, and so it would be for
                # every other trial too. Try again next time.
                logger.error('Failed to submit trial %d: %s', trial.id, error)
                break
            submitted += 1
        logger.info('Submitted %d trials.', submitted)

    def export_images(self, images: Iterable[str]):
        """Exports those of |images| that haven't been exported yet."""
        images = set(images) - self.exported_images
        if not images:
            return

        def export(image):
            return image, export_runner_image(image, self.image_store_dir)

        with ThreadPool(EXPORT_THREADS) as pool:
            for image, exported in pool.map(export, sorted(images)):
                if exported:
                    self.exported_images.add(image)

    def submit_trial(self, trial):
        """Submits |trial| as a job."""
        instance_name = experiment_utils.get_trial_instance_name(
            self.experiment, trial.id)
        image = benchmark_utils.get_runner_image_ref(trial.runner_image_digest)
        num_cpu_cores = self.config['runner_num_cpu_cores']
        # The runner stops by itself once max_total_time is up. What bounds a
        # trial that doesn't is the job's time limit, on whose expiry the
        # wrapper kills the container. The container's own timeout is only a
        # backstop for a wrapper that was killed outright, so it's the same.
        time_limit_seconds = (self.config['max_total_time'] +
                              scheduler.GRACE_TIME_SECONDS + IMAGE_LOAD_SECONDS)
        task = {
            'instance_name':
                instance_name,
            'image':
                image,
            'image_archive':
                get_image_archive_path(self.image_store_dir, image),
            'start_marker':
                self._get_start_marker_path(trial.id),
            'num_cpu_cores':
                num_cpu_cores,
            'memory_mb':
                self.config['runner_memory_mb'],
            'container_timeout':
                time_limit_seconds,
            'volumes': [
                self.config['experiment_filestore'],
                self.config['report_filestore'],
            ],
            'environment':
                scheduler.get_runner_environment(instance_name, trial.fuzzer,
                                                 trial.benchmark, trial.id,
                                                 trial.trial_group_num,
                                                 self.config),
        }
        task_path = os.path.join(self.hq_dir, 'tasks', f'{trial.id}.json')
        with open(task_path, 'w', encoding='utf-8') as file_handle:
            json.dump(task, file_handle, indent=2)

        attempt = self.launch_attempts[trial.id] + 1
        log_path = self._get_log_path(trial.id, attempt)
        job_id = self.client.submit(['python3', self.wrapper_path, task_path],
                                    name=instance_name,
                                    cpus=num_cpu_cores,
                                    memory_mb=self.config['runner_memory_mb'],
                                    time_limit_seconds=time_limit_seconds,
                                    stdout=log_path + '.out',
                                    stderr=log_path + '.err',
                                    cwd=self.hq_dir)
        self.jobs[trial.id] = job_id
        self.launch_attempts[trial.id] = attempt


def schedule_loop(experiment_config: dict):
    """Runs the experiment's trials on HyperQueue until they have all ended."""
    logger.info('Starting HyperQueue scheduler.')
    hq_scheduler = HyperQueueScheduler(experiment_config)
    hq_scheduler.set_up()
    experiment = experiment_config['experiment']
    while not scheduler.all_trials_ended(experiment):
        try:
            hq_scheduler.schedule()
        except Exception:  # pylint: disable=broad-except
            logger.error('Error occurred during scheduling.')
        time.sleep(scheduler.SCHEDULE_POLL_SECONDS)
    logger.info('Finished scheduling.')


def get_trial_jobs(client: hyperqueue.Client,
                   experiment: str) -> List[hyperqueue.Job]:
    """Returns the jobs of |experiment|'s trials that the server knows of, found
    by name. Used where the scheduler's own record of its jobs isn't
    available, as when stopping an experiment."""
    prefix = experiment_utils.get_trial_instance_name(experiment, 0)[:-1]
    return [
        job for job in client.list_jobs()
        if job.name.startswith(prefix) and job.name[len(prefix):].isdigit()
    ]
