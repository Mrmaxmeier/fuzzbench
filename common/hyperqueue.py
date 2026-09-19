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
"""A client for a HyperQueue server, driven through the `hq` command line.

Every call asks `hq` for JSON output. `hq` logs to stderr, so stdout is parsed
on its own rather than through new_process.execute, which merges the two.
"""

import dataclasses
import json
import os
import shutil
import subprocess
from typing import Iterable, List, Optional

# Where the dispatcher finds the client. Set by run_experiment for the
# dispatcher container, which has the binary mounted rather than installed.
HQ_BINARY_VAR = 'HQ_BINARY'
# Read by `hq` itself: the directory holding the server's access file.
HQ_SERVER_DIR_VAR = 'HQ_SERVER_DIR'
DEFAULT_SERVER_DIR = '~/.hq-server'

# `hq` only talks to the server, which answers in milliseconds. A call that
# takes this long means the server is gone.
HQ_TIMEOUT_SECONDS = 2 * 60

# Selectors for `hq job cancel` are passed on the command line. Chunk them so
# that cancelling a large experiment can't exceed the argument length limit.
CANCEL_CHUNK_SIZE = 500


class HyperQueueError(Exception):
    """Raised when `hq` fails or answers with an error."""


def find_binary() -> Optional[str]:
    """Returns the absolute path of the `hq` client, or None if there isn't
    one."""
    binary = os.getenv(HQ_BINARY_VAR) or shutil.which('hq')
    if not binary or not os.path.isfile(binary):
        return None
    return os.path.realpath(binary)


def find_server_dir() -> str:
    """Returns the absolute path of the server directory `hq` would use."""
    server_dir = os.getenv(HQ_SERVER_DIR_VAR) or DEFAULT_SERVER_DIR
    return os.path.realpath(os.path.expanduser(server_dir))


@dataclasses.dataclass(frozen=True)
class Job:  # pylint: disable=too-many-instance-attributes
    """The state of a HyperQueue job, as `hq job list` reports it."""
    job_id: int
    name: str
    task_count: int
    waiting: int = 0
    running: int = 0
    finished: int = 0
    failed: int = 0
    canceled: int = 0
    aborted: int = 0

    @property
    def is_done(self) -> bool:
        """Returns whether every task of the job has stopped for good."""
        stopped = self.finished + self.failed + self.canceled + self.aborted
        return stopped >= self.task_count

    @property
    def succeeded(self) -> bool:
        """Returns whether every task of the job finished successfully."""
        return self.finished >= self.task_count


class Client:
    """Talks to one HyperQueue server."""

    def __init__(self,
                 binary: Optional[str] = None,
                 server_dir: Optional[str] = None):
        self.binary = binary or find_binary() or 'hq'
        self.server_dir = server_dir

    def _run(self, args: List[str]):
        """Runs `hq` with |args| and returns its parsed JSON output."""
        command = [self.binary, '--output-mode', 'json']
        if self.server_dir:
            command += ['--server-dir', self.server_dir]
        command += args
        try:
            result = subprocess.run(command,
                                    capture_output=True,
                                    text=True,
                                    timeout=HQ_TIMEOUT_SECONDS,
                                    check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HyperQueueError(
                f'Failed to run {command}: {error}') from error

        try:
            output = json.loads(
                result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError:
            output = None
        if isinstance(output, dict) and 'error' in output:
            raise HyperQueueError(f'{command} failed: {output["error"]}')
        if result.returncode != 0:
            raise HyperQueueError(f'{command} returned {result.returncode}: '
                                  f'{result.stderr.strip()}')
        return output

    def submit(  # pylint: disable=too-many-arguments
            self,
            command: List[str],
            *,
            name: str,
            cpus: int,
            memory_mb: int,
            time_limit_seconds: int,
            stdout: str,
            stderr: str,
            cwd: str,
            crash_limit: int = 1) -> int:
        """Submits |command| as a single-task job and returns the job's id.

        The task is pinned to the cpus the worker allocates it, and every path
        must be one the workers can reach. |memory_mb| is only a reservation,
        which HQ keeps each worker's tasks within; 0 reserves none. Enforcing
        it is up to the task.

        |crash_limit| defaults to 1 so that a task whose worker is lost fails
        rather than being silently re-run: a trial restarted from scratch would
        wipe what it had already uploaded and report a start time that the
        measurer's cycles no longer line up with.
        """
        resources = ['--resource', f'mem={memory_mb}'] if memory_mb else []
        output = self._run([
            'submit',
            f'--name={name}',
            f'--cpus={cpus}',
            *resources,
            '--pin=taskset',
            f'--time-limit={time_limit_seconds}s',
            f'--crash-limit={crash_limit}',
            f'--stdout={stdout}',
            f'--stderr={stderr}',
            f'--cwd={cwd}',
            '--',
            *command,
        ])
        return int(output['id'])

    def list_jobs(self) -> List[Job]:
        """Returns every job the server knows about, finished or not."""
        jobs = []
        for job in self._run(['job', 'list', '--all']) or []:
            stats = job.get('task_stats', {})
            jobs.append(
                Job(job_id=int(job['id']),
                    name=job.get('name', ''),
                    task_count=int(job.get('task_count', 1)),
                    waiting=int(stats.get('waiting', 0)),
                    running=int(stats.get('running', 0)),
                    finished=int(stats.get('finished', 0)),
                    failed=int(stats.get('failed', 0)),
                    canceled=int(stats.get('canceled', 0)),
                    aborted=int(stats.get('aborted', 0))))
        return jobs

    def cancel(self, job_ids: Iterable[int]):
        """Cancels |job_ids|. Jobs that already stopped are left as they are."""
        job_ids = sorted(set(job_ids))
        for start in range(0, len(job_ids), CANCEL_CHUNK_SIZE):
            chunk = job_ids[start:start + CANCEL_CHUNK_SIZE]
            self._run(['job', 'cancel', ','.join(str(i) for i in chunk)])
