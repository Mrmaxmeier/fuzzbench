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
"""Module for building images for use in trials.

Builds go through the content-addressed resolver rather than through make. The
generated makefile is still what a developer drives by hand (`make run-*`,
`make build-*`); its targets are phony, so it re-runs `docker build` for every
image in a chain on every invocation and leaves caching entirely to docker's
layer cache. That is fine interactively and useless for deciding whether an
experiment can reuse an existing image, which is a question about identity
rather than about layers. Experiment builds must use this module's resolver.
"""

import os
import subprocess
import threading
from typing import List

from common import experiment_utils
from common import logs
from common import new_process
from experiment.build import docker_images
from experiment.build import image_resolver

_resolver = None  # pylint: disable=invalid-name
_resolver_lock = threading.Lock()  # pylint: disable=invalid-name

# Generous ceiling for `docker wait` on the coverage-binaries copy container:
# the largest benchmarks archive several GB of /src, /work and /out.
COVERAGE_BINARIES_COPY_TIMEOUT = 30 * 60


def init_resolver(fuzzers: List[str], benchmarks: List[str]):
    """Create the process-wide image resolver for |fuzzers| × |benchmarks|.

    Call this once before any build so the resolver graph covers only the
    experiment's images (plus shared parents like base-image), not the full
    fuzzer × benchmark matrix.
    """
    global _resolver  # pylint: disable=global-statement
    with _resolver_lock:
        images = docker_images.get_images_to_build(fuzzers, benchmarks)
        _resolver = image_resolver.Resolver(images)


def get_resolver():
    """Returns the process-wide image resolver.

    One resolver is shared by every build in the process so that its memo is
    too. The dispatcher builds fuzzer-benchmark pairs through a thread pool and
    those pairs have parents in common -- most of them share base-image, and
    every pair for one fuzzer shares that fuzzer's intermediate runner. Handing
    each build its own resolver would rebuild those parents once per pair.

    Raises RuntimeError if |init_resolver| has not been called.
    """
    with _resolver_lock:
        if _resolver is None:
            raise RuntimeError(
                'local_build.init_resolver(fuzzers, benchmarks) must be '
                'called before building images.')
        return _resolver


def reset_resolver():
    """Clear the process-wide resolver. Intended for tests."""
    global _resolver  # pylint: disable=global-statement
    with _resolver_lock:
        _resolver = None


def build_base_images():
    """Build base images locally. Raises if any of them cannot be built."""
    get_resolver().resolve('base-image')


def get_shared_coverage_binaries_dir():
    """Returns the shared coverage binaries directory."""
    experiment_filestore_path = experiment_utils.get_experiment_filestore_path()
    return os.path.join(experiment_filestore_path, 'coverage-binaries')


def make_shared_coverage_binaries_dir():
    """Make the shared coverage binaries directory."""
    shared_coverage_binaries_dir = get_shared_coverage_binaries_dir()
    if os.path.exists(shared_coverage_binaries_dir):
        return
    os.makedirs(shared_coverage_binaries_dir)


def build_coverage(benchmark):
    """Build (locally) coverage image for benchmark. Returns the resolved
    image."""
    resolved = get_resolver().resolve(f'coverage-{benchmark}-builder')
    make_shared_coverage_binaries_dir()
    copy_coverage_binaries(benchmark, resolved)
    return resolved


def copy_coverage_binaries(benchmark, resolved):
    """Copy coverage binaries in a local experiment.

    The archive also ships the image's llvm-profdata/llvm-cov under
    llvm-tools/. Measurement must use those rather than whatever llvm-* the
    host happens to have on PATH: a newer host tool rejects the raw profile
    format the coverage build wrote (version mismatch), which zeroes every
    trial's edges_covered.
    """
    shared_coverage_binaries_dir = get_shared_coverage_binaries_dir()
    mount_arg = f'{shared_coverage_binaries_dir}:{shared_coverage_binaries_dir}'
    coverage_build_archive = f'coverage-build-{benchmark}.tar.gz'
    coverage_build_archive_shared_dir_path = os.path.join(
        shared_coverage_binaries_dir, coverage_build_archive)
    # Pack llvm tools from the same image that built the binary, then /out,
    # /src and /work. /src is needed for llvm-cov's path-equivalence when
    # rendering HTML reports; glibc SONAMEs under it are scrubbed on extract
    # so a host newer than the build image is not forced onto the image's
    # libc via RUNPATH (see coverage_utils.scrub_host_incompatible_libs).
    command = (
        'set -e; cd /out; '
        'mkdir -p llvm-tools; '
        'cp "$(command -v llvm-profdata)" "$(command -v llvm-cov)" llvm-tools/; '
        f'tar -czvf {coverage_build_archive_shared_dir_path} * /src /work')
    # Run the digest rather than the builder's mutable name. The binaries the
    # measurer scores coverage against have to come from the same image the
    # fuzz build descends from, and a name would only promise the most recent
    # build of something with a matching label.
    _run_container_and_wait(
        ['-v', mount_arg, resolved.digest, '/bin/bash', '-c', command],
        COVERAGE_BINARIES_COPY_TIMEOUT,
        f'Coverage binaries copy for {benchmark}')


def _run_container_and_wait(run_args: List[str], timeout: int,
                            description: str):
    """Runs a container detached and blocks until it exits, raising if it fails
    or doesn't finish within |timeout| seconds. The container is always
    removed.

    Detached rather than a foreground `docker run` because podman's Docker-API
    compat layer can fail to signal stream EOF back to an attached client even
    after the container has exited, hanging it forever (same root cause as the
    trial-runner fix in runner-startup-script-template.sh). `docker wait` polls
    a plain container-state endpoint instead, so it isn't subject to that race;
    |timeout| is a second line of defense in case it wedges anyway.
    """
    run_command = ['docker', 'run', '-d'] + run_args
    container_id = new_process.execute(run_command).output.strip()
    try:
        wait_result = new_process.execute(['docker', 'wait', container_id],
                                          timeout=timeout)
        if wait_result.timed_out:
            raise TimeoutError(
                f'{description} did not finish within {timeout} seconds.')

        exit_code = int(wait_result.output.strip())
        if exit_code != 0:
            container_logs = new_process.execute(
                ['docker', 'logs', container_id], expect_zero=False).output
            logs.error('%s failed with exit code %d: %s', description,
                       exit_code, container_logs)
            raise subprocess.CalledProcessError(exit_code, run_command)
    finally:
        new_process.execute(['docker', 'rm', '-f', container_id],
                            expect_zero=False)


def build_fuzzer_benchmark(fuzzer: str, benchmark: str):
    """Builds |benchmark| for |fuzzer|. Returns the resolved runner image."""
    return get_resolver().resolve(f'{fuzzer}-{benchmark}-runner')
