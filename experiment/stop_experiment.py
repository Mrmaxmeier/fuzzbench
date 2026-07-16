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

from common import logs
from common import yaml_utils

logger = logs.Logger()  # pylint: disable=invalid-name

DISPATCHER_CONTAINER_NAME = 'dispatcher-container'


def stop_experiment(experiment_name, experiment_config_filename):
    """Stop the experiment specified by |experiment_config_filename|."""
    del experiment_name  # Local experiments use a fixed dispatcher container name.
    yaml_utils.read(experiment_config_filename)

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

    logger.info('Successfully stopped experiment.')
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
