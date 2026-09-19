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
"""Tests for resources/hq_trial.py, the wrapper that runs a trial on a
HyperQueue worker."""

import datetime
import importlib.util
import os
import signal
import subprocess
from unittest import mock

import pytest

from experiment import scheduler

# pylint: disable=redefined-outer-name,unused-argument

IMAGE = 'sha256:' + 'a' * 64


def _load_hq_trial():
    """Imports the wrapper, which is shipped to workers as a file rather than
    being part of a package."""
    path = os.path.join(scheduler.RESOURCES_DIR, 'hq_trial.py')
    spec = importlib.util.spec_from_file_location('hq_trial', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hq_trial = _load_hq_trial()


@pytest.fixture
def task(tmp_path):
    """Returns a task as the scheduler writes it."""
    return {
        'instance_name': 'r-exp-7',
        'image': IMAGE,
        'image_archive': '/shared/runner-images/' + 'a' * 64 + '.tar',
        'start_marker': str(tmp_path / 'started'),
        'num_cpu_cores': 2,
        'memory_mb': 8192,
        'container_timeout': 1234,
        'volumes': ['/shared/experiment-data', '/shared/report-data'],
        'environment': {
            'TRIAL_ID': '7',
            'FUZZER': 'afl'
        },
    }


def test_podman_run_command(task):
    """Tests that the container gets the task's resources, mounts and
    environment, with the image last."""
    command = hq_trial.get_podman_run_command(task)

    assert command[:3] == ['podman', 'run', '--rm']
    for option in ('--name=r-exp-7', '--privileged', '--cpus=2',
                   '--network=host', '--timeout=1234', '--memory=8192m',
                   '--memory-swap=8192m',
                   '--volume=/shared/experiment-data:/shared/experiment-data',
                   '--volume=/shared/report-data:/shared/report-data',
                   '--env=TRIAL_ID=7', '--env=FUZZER=afl'):
        assert option in command
    assert command[-1] == IMAGE


def test_podman_run_command_unlimited_memory(task):
    """Tests that a task without a memory limit gets a container without
    one."""
    task['memory_mb'] = 0
    command = hq_trial.get_podman_run_command(task)
    assert not [option for option in command if option.startswith('--memory')]


def _returncode(code):
    return subprocess.CompletedProcess(args=[], returncode=code)


@pytest.fixture
def lock_dir(tmp_path):
    """Keeps the image lock out of the real runtime directory."""
    with mock.patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(tmp_path)}):
        yield


def test_ensure_image_present(lock_dir):
    """Tests that an image already in the store isn't loaded again."""
    with mock.patch('subprocess.run',
                    return_value=_returncode(0)) as mocked_run:
        hq_trial.ensure_image(IMAGE, '/archive.tar')
    assert mocked_run.call_args_list == [
        mock.call(['podman', 'image', 'exists', IMAGE], check=False)
    ]


def test_ensure_image_loads(lock_dir):
    """Tests that a missing image is loaded from its archive."""
    # Missing before and inside the lock, present after loading.
    exists = iter([1, 1, 0])

    def run(command, **_):
        if command[:3] == ['podman', 'image', 'exists']:
            return _returncode(next(exists))
        return _returncode(0)

    with mock.patch('subprocess.run', side_effect=run) as mocked_run:
        hq_trial.ensure_image(IMAGE, '/archive.tar')
    assert mock.call(['podman', 'load', '--quiet', '--input', '/archive.tar'],
                     check=True) in mocked_run.call_args_list


def test_ensure_image_wrong_digest(lock_dir):
    """Tests that an archive loading as some other image is an error, since
    running that would misattribute the trial's results."""
    with mock.patch('subprocess.run',
                    side_effect=lambda command, **_: _returncode(
                        1 if 'exists' in command else 0)):
        with pytest.raises(RuntimeError, match='did not load as'):
            hq_trial.ensure_image(IMAGE, '/archive.tar')


def test_run_trial(task):
    """Tests that a trial removes any leftover container, records its start
    just before starting the container, and exits with the container's
    status."""
    marker_existed_at_start = []

    def popen(command, **_):
        marker_existed_at_start.append(os.path.exists(task['start_marker']))
        process = mock.Mock()
        process.wait.return_value = 3
        return process

    with mock.patch.object(hq_trial, 'ensure_image') as mocked_ensure_image, \
            mock.patch('subprocess.run') as mocked_run, \
            mock.patch('subprocess.Popen', side_effect=popen) as mocked_popen, \
            mock.patch('signal.signal'):
        assert hq_trial.run_trial(task) == 3

    mocked_ensure_image.assert_called_with(IMAGE, task['image_archive'])
    mocked_run.assert_called_with(
        ['podman', 'rm', '--force', '--ignore', 'r-exp-7'], check=False)
    assert mocked_popen.call_args[0][0] == hq_trial.get_podman_run_command(task)
    assert marker_existed_at_start == [True]
    with open(task['start_marker'], encoding='utf-8') as file_handle:
        datetime.datetime.fromisoformat(file_handle.read())


def test_run_trial_cancelled(task):
    """Tests that cancelling the job kills the container, from a session of
    its own so that HQ's kill of this wrapper's group can't interrupt it."""
    handlers = {}

    with mock.patch.object(hq_trial, 'ensure_image'), \
            mock.patch('subprocess.run') as mocked_run, \
            mock.patch('subprocess.Popen'), \
            mock.patch('signal.signal',
                       side_effect=handlers.__setitem__):
        hq_trial.run_trial(task)
        assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
        handlers[signal.SIGINT](signal.SIGINT, None)

    command = mocked_run.call_args[0][0]
    assert command == ['podman', 'kill', 'r-exp-7']
    assert mocked_run.call_args[1]['start_new_session']
