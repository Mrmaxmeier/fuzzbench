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
"""Tests for hyperqueue.py."""

import json
import subprocess
from unittest import mock

import pytest

from common import hyperqueue

# pylint: disable=redefined-outer-name


def _completed(stdout='', returncode=0, stderr=''):
    """Returns the result of a finished `hq` call."""
    return subprocess.CompletedProcess(args=[],
                                       returncode=returncode,
                                       stdout=stdout,
                                       stderr=stderr)


@pytest.fixture
def mocked_run():
    """Mocks the `hq` calls."""
    with mock.patch('subprocess.run') as mocked:
        yield mocked


@pytest.fixture
def client():
    """Returns a client with a fixed binary and server directory."""
    return hyperqueue.Client('/opt/hq', '/srv/hq-server')


def test_submit(mocked_run, client):
    """Tests that submit asks for a pinned single task and returns its job
    id."""
    mocked_run.return_value = _completed(json.dumps({'id': 42}))
    job_id = client.submit(['python3', 'wrapper.py', 'task.json'],
                           name='r-exp-1',
                           cpus=2,
                           memory_mb=8192,
                           time_limit_seconds=3600,
                           stdout='/shared/1.out',
                           stderr='/shared/1.err',
                           cwd='/shared')

    assert job_id == 42
    command = mocked_run.call_args[0][0]
    assert command[:5] == [
        '/opt/hq', '--output-mode', 'json', '--server-dir', '/srv/hq-server'
    ]
    assert command[5] == 'submit'
    for option in ('--name=r-exp-1', '--cpus=2', '--pin=taskset',
                   '--time-limit=3600s', '--crash-limit=1',
                   '--stdout=/shared/1.out', '--stderr=/shared/1.err',
                   '--cwd=/shared'):
        assert option in command
    assert command[command.index('--resource') + 1] == 'mem=8192'
    # The command follows `--`, so hq can't mistake its arguments for its own.
    separator = command.index('--')
    assert command[separator + 1:] == ['python3', 'wrapper.py', 'task.json']


def test_submit_without_memory(mocked_run, client):
    """Tests that a job asking for no memory reserves none."""
    mocked_run.return_value = _completed(json.dumps({'id': 42}))
    client.submit(['true'],
                  name='r-exp-1',
                  cpus=1,
                  memory_mb=0,
                  time_limit_seconds=60,
                  stdout='/shared/1.out',
                  stderr='/shared/1.err',
                  cwd='/shared')
    assert '--resource' not in mocked_run.call_args[0][0]


def test_list_jobs(mocked_run, client):
    """Tests that list_jobs parses the jobs' task counts."""
    mocked_run.return_value = _completed(
        json.dumps([
            {
                'id': 1,
                'name': 'r-exp-1',
                'task_count': 1,
                'task_stats': {
                    'running': 1
                }
            },
            {
                'id': 2,
                'name': 'r-exp-2',
                'task_count': 1,
                'task_stats': {
                    'failed': 1
                }
            },
        ]))
    running, failed = client.list_jobs()

    assert mocked_run.call_args[0][0][-3:] == ['job', 'list', '--all']
    assert running.job_id == 1 and running.name == 'r-exp-1'
    assert not running.is_done
    assert failed.is_done and not failed.succeeded


@pytest.mark.parametrize('stats,is_done,succeeded', [
    ({
        'waiting': 1
    }, False, False),
    ({
        'running': 1
    }, False, False),
    ({
        'finished': 1
    }, True, True),
    ({
        'failed': 1
    }, True, False),
    ({
        'canceled': 1
    }, True, False),
    ({
        'aborted': 1
    }, True, False),
])
def test_job_state(stats, is_done, succeeded):
    """Tests how a job's task counts translate into its state."""
    job = hyperqueue.Job(job_id=1, name='job', task_count=1, **stats)
    assert job.is_done == is_done
    assert job.succeeded == succeeded


def test_error_output(mocked_run, client):
    """Tests that an error reported in hq's JSON output raises, even though hq
    exits with 0 for some of them."""
    mocked_run.return_value = _completed(
        json.dumps({'error': 'Hyperqueue version mismatch detected.'}))
    with pytest.raises(hyperqueue.HyperQueueError, match='version mismatch'):
        client.list_jobs()


def test_nonzero_exit(mocked_run, client):
    """Tests that hq failing raises, with its stderr in the message."""
    mocked_run.return_value = _completed(returncode=1,
                                         stderr='Connection refused')
    with pytest.raises(hyperqueue.HyperQueueError, match='Connection refused'):
        client.list_jobs()


def test_timeout(mocked_run, client):
    """Tests that an unresponsive server raises rather than blocking."""
    mocked_run.side_effect = subprocess.TimeoutExpired(cmd='hq', timeout=1)
    with pytest.raises(hyperqueue.HyperQueueError):
        client.list_jobs()


def test_cancel_chunks(mocked_run, client):
    """Tests that cancel splits long lists of jobs across calls."""
    mocked_run.return_value = _completed(json.dumps({}))
    with mock.patch('common.hyperqueue.CANCEL_CHUNK_SIZE', 2):
        client.cancel([5, 1, 3, 3, 4])

    selectors = [call[0][0][-1] for call in mocked_run.call_args_list]
    assert selectors == ['1,3', '4,5']


def test_cancel_nothing(mocked_run, client):
    """Tests that cancelling no jobs doesn't call hq."""
    client.cancel([])
    assert not mocked_run.called


def test_find_server_dir(tmp_path):
    """Tests that the server directory comes from HQ_SERVER_DIR, with symlinks
    resolved."""
    server_dir = tmp_path / 'server'
    server_dir.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(server_dir)
    with mock.patch.dict('os.environ', {'HQ_SERVER_DIR': str(link)}):
        assert hyperqueue.find_server_dir() == str(server_dir)
