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
"""Runs one trial on a HyperQueue worker.

This is the HyperQueue executor's counterpart of the local executor's
runner-startup-script-template.sh. The scheduler submits it as the trial's HQ
job, with the path of a task file describing the trial. It runs on the worker's
own python3, outside any FuzzBench environment, so it must only use the
standard library.

It makes sure the trial's runner image is in the worker's podman store,
loading it by digest from the archive the dispatcher exported, then runs the
container in the foreground and exits with its status. Just before starting
the container it writes the trial's start marker, which is what the scheduler
takes as the trial's start time.

The worker needs podman (rootless is fine) and python3, and must see the
experiment filestore at the same path as the dispatcher.
"""

import datetime
import fcntl
import json
import os
import signal
import subprocess
import sys

# How long to wait for `podman kill` when the trial is cancelled.
KILL_TIMEOUT_SECONDS = 30


def log(message):
    """Writes |message| to the trial's log."""
    print(f'[hq-trial] {message}', flush=True)


def image_exists(image):
    """Returns whether |image| is in the local podman store."""
    return subprocess.run(['podman', 'image', 'exists', image],
                          check=False).returncode == 0


def ensure_image(image, archive):
    """Loads |image| from |archive| unless it's already present.

    Trials that start together on a fresh worker usually want the same image,
    so loading is serialized per image through a lock in the worker's own
    runtime directory: the podman store is per-worker, so the lock must be too.
    """
    if image_exists(image):
        return
    lock_dir = os.path.join(
        os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/fuzzbench-{os.getuid()}',
        'fuzzbench-hq-images')
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, image.split(':')[-1] + '.lock')
    with open(lock_path, 'w', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if image_exists(image):
            return
        log(f'Loading {image} from {archive}.')
        subprocess.run(['podman', 'load', '--quiet', '--input', archive],
                       check=True)
    # An archive holds the image under its config digest, which is what the
    # dispatcher recorded for the trial. If it loads as anything else, running
    # it would attribute this trial's results to an image it didn't run.
    if not image_exists(image):
        raise RuntimeError(f'{archive} did not load as {image}.')


def write_start_marker(path):
    """Records the current time at |path|, atomically."""
    temp_path = f'{path}.tmp-{os.getpid()}'
    with open(temp_path, 'w', encoding='utf-8') as file_handle:
        file_handle.write(
            datetime.datetime.now(datetime.timezone.utc).isoformat())
    os.replace(temp_path, path)


def get_podman_run_command(task):
    """Returns the command that runs the container |task| describes. It mirrors
    the local executor's `docker run`, see runner-startup-script-template.sh."""
    command = [
        'podman',
        'run',
        '--rm',
        f'--name={task["instance_name"]}',
        '--privileged',
        f'--cpus={task["num_cpu_cores"]}',
        # Host networking: the runner needs no network of its own, and a
        # rootless container on the default network costs a pasta process
        # apiece, of which a full worker would have hundreds.
        '--network=host',
        # A backstop for when this wrapper is killed outright and can't stop
        # the container itself.
        f'--timeout={task["container_timeout"]}',
        '--shm-size=2g',
        '--cap-add=SYS_NICE',
        '--cap-add=SYS_PTRACE',
        '--security-opt=seccomp=unconfined',
    ]
    if task['memory_mb']:
        # Swap is capped too, or a trial over its limit would page instead of
        # being killed.
        command += [
            f'--memory={task["memory_mb"]}m',
            f'--memory-swap={task["memory_mb"]}m',
        ]
    for volume in task['volumes']:
        command.append(f'--volume={volume}:{volume}')
    for key, value in task['environment'].items():
        command.append(f'--env={key}={value}')
    command.append(task['image'])
    return command


def run_trial(task):
    """Runs the trial described by |task| and returns its exit status."""
    ensure_image(task['image'], task['image_archive'])

    name = task['instance_name']
    # A container left over from an attempt whose worker was lost would make
    # the name clash.
    subprocess.run(['podman', 'rm', '--force', '--ignore', name], check=False)

    def stop(signum, _):
        # Kill rather than stop: the container's PID 1 is the shell of the
        # image's shell-form ENTRYPOINT, which ignores SIGTERM, so `podman
        # stop` would only get anywhere after its timeout. A cancelled trial
        # has nothing left to flush that its periodic syncs haven't uploaded.
        #
        # In a session of its own, because HQ follows its signal with a kill
        # of this wrapper's whole process group, and a `podman kill` caught in
        # that leaves the container running.
        log(f'Received signal {signum}, killing {name}.')
        try:
            subprocess.run(['podman', 'kill', name],
                           start_new_session=True,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=KILL_TIMEOUT_SECONDS,
                           check=False)
        except subprocess.TimeoutExpired:
            log(f'Timed out killing {name}.')

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    command = get_podman_run_command(task)
    log(f'Starting {name} on cpus {os.environ.get("HQ_CPUS", "?")}.')
    write_start_marker(task['start_marker'])
    process = subprocess.Popen(  # pylint: disable=consider-using-with
        command,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)
    returncode = process.wait()
    log(f'{name} exited with status {returncode}.')
    return returncode


def main():
    """Runs the trial described by the task file given as the argument."""
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <task.json>', file=sys.stderr)
        return 1
    with open(sys.argv[1], encoding='utf-8') as file_handle:
        task = json.load(file_handle)
    return run_trial(task)


if __name__ == '__main__':
    sys.exit(main())
