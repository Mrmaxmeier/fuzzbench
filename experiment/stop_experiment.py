#!/usr/bin/env python3
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
"""Stops a running experiment."""

import subprocess
import sys

from common import experiment_utils
from common import hyperqueue
from common import logs
from common import yaml_utils
from experiment import hq_scheduler

logger = logs.Logger()  # pylint: disable=invalid-name

DISPATCHER_CONTAINER_NAME = 'dispatcher-container'


def stop_experiment(experiment_name, experiment_config_filename):
    """Stop the experiment specified by |experiment_config_filename|."""
    experiment_config = yaml_utils.read(experiment_config_filename)
    if not stop_dispatcher():
        return False
    # Stopped second, so that the dispatcher can't submit anything more.
    if (experiment_utils.get_executor(experiment_config) ==
            experiment_utils.EXECUTOR_HYPERQUEUE):
        return cancel_hyperqueue_jobs(experiment_name, experiment_config)
    return True


def cancel_hyperqueue_jobs(experiment_name, experiment_config):
    """Cancels the HyperQueue jobs of |experiment_name|'s trials. They don't
    stop with the dispatcher: they run on the cluster."""
    client = hyperqueue.Client(experiment_config.get('hq_binary'),
                               experiment_config.get('hq_server_dir'))
    try:
        jobs = [
            job for job in hq_scheduler.get_trial_jobs(client, experiment_name)
            if not job.is_done
        ]
        logger.info('Cancelling %d HyperQueue jobs.', len(jobs))
        client.cancel(job.job_id for job in jobs)
    except hyperqueue.HyperQueueError as error:
        logger.error('Failed to cancel HyperQueue jobs: %s', error)
        return False
    return True


def stop_dispatcher():
    """Stops the dispatcher container."""
    # Local experiments use a fixed dispatcher container name.
    logger.info(
        'Stopping local experiment by stopping dispatcher container: %s',
        DISPATCHER_CONTAINER_NAME)
    try:
        subprocess.run(
            ['docker', 'stop', DISPATCHER_CONTAINER_NAME],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        if 'No such container' in (error.stderr or ''):
            logger.warning('Dispatcher container not running, skip.')
            return True
        logger.error('Failed to stop dispatcher container: %s', error.stderr)
        return False

    logger.info('Successfully stopped dispatcher.')
    return True


def main():
    """Stop the experiment."""
    if len(sys.argv) != 3:
        print(f'Usage {sys.argv[0]} <experiment-name> <experiment-config.yaml>')
        return 1
    logs.initialize()
    return 0 if stop_experiment(sys.argv[1], sys.argv[2]) else 1


if __name__ == '__main__':
    sys.exit(main())
