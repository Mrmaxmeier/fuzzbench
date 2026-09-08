# Copyright 2026 Google LLC
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
"""Tests for local_build.py."""

import subprocess
from unittest import mock

import pytest

from common import new_process
from experiment.build import local_build


@pytest.fixture(autouse=True)
def _reset_resolver():
    local_build.reset_resolver()
    yield
    local_build.reset_resolver()


def test_get_resolver_requires_init():
    """get_resolver fails fast if init_resolver was never called."""
    with pytest.raises(RuntimeError, match='init_resolver'):
        local_build.get_resolver()


def test_init_resolver_scopes_to_requested_pairs():
    """init_resolver builds the image graph for the given fuzzers/benchmarks."""
    with mock.patch('experiment.build.docker_images.get_images_to_build',
                    return_value={'base-image': {}}) as mocked_get:
        with mock.patch(
                'experiment.build.image_resolver.Resolver') as mocked_resolver:
            local_build.init_resolver(['libfuzzer'],
                                      ['zlib_zlib_uncompress_fuzzer'])
            mocked_get.assert_called_once_with(['libfuzzer'],
                                               ['zlib_zlib_uncompress_fuzzer'])
            assert local_build.get_resolver() is mocked_resolver.return_value


def test_get_resolver_is_singleton():
    """Repeated get_resolver calls return the same instance."""
    with mock.patch('experiment.build.docker_images.get_images_to_build',
                    return_value={}):
        with mock.patch('experiment.build.image_resolver.Resolver',
                        side_effect=lambda images: mock.Mock(images=images)):
            local_build.init_resolver(['afl'], ['re2_fuzzer'])
            first = local_build.get_resolver()
            second = local_build.get_resolver()
            assert first is second


def test_copy_coverage_binaries_runs_detached_and_waits(fs, experiment):  # pylint: disable=invalid-name,unused-argument
    """copy_coverage_binaries must run the container detached and poll its
    completion via `docker wait`, not a foreground `docker run`: podman's
    Docker-API compat layer can fail to signal stream EOF back to a
    foreground/attached client even after the container has actually
    exited, hanging the client forever."""
    resolved = mock.Mock(digest='sha256:deadbeef')
    with mock.patch('common.new_process.execute') as mocked_execute:
        mocked_execute.side_effect = [
            new_process.ProcessResult(0, 'container123\n', False),  # run -d
            new_process.ProcessResult(0, '0\n', False),  # wait
            new_process.ProcessResult(0, '', False),  # rm
        ]
        local_build.copy_coverage_binaries('re2_fuzzer', resolved)

    run_call, wait_call, rm_call = mocked_execute.call_args_list
    assert run_call[0][0][:3] == ['docker', 'run', '-d']
    assert wait_call[0][0] == ['docker', 'wait', 'container123']
    assert rm_call[0][0] == ['docker', 'rm', '-f', 'container123']


def test_copy_coverage_binaries_raises_and_still_cleans_up_on_failure(
        fs, experiment):  # pylint: disable=invalid-name,unused-argument
    """A nonzero container exit code must raise (so build_coverage's caller
    sees the failure), and the container must still be removed."""
    resolved = mock.Mock(digest='sha256:deadbeef')
    with mock.patch('common.new_process.execute') as mocked_execute:
        mocked_execute.side_effect = [
            new_process.ProcessResult(0, 'container123\n', False),  # run -d
            new_process.ProcessResult(0, '1\n', False),  # wait: exit code 1
            new_process.ProcessResult(1, 'tar: some error', False),  # logs
            new_process.ProcessResult(0, '', False),  # rm
        ]
        with pytest.raises(subprocess.CalledProcessError):
            local_build.copy_coverage_binaries('re2_fuzzer', resolved)

    rm_call = mocked_execute.call_args_list[-1]
    assert rm_call[0][0] == ['docker', 'rm', '-f', 'container123']


def test_copy_coverage_binaries_raises_on_wait_timeout(fs, experiment):  # pylint: disable=invalid-name,unused-argument
    """A `docker wait` that never returns must raise rather than trip over an
    empty exit code, and the container must still be removed."""
    resolved = mock.Mock(digest='sha256:deadbeef')
    with mock.patch('common.new_process.execute') as mocked_execute:
        mocked_execute.side_effect = [
            new_process.ProcessResult(0, 'container123\n', False),  # run -d
            new_process.ProcessResult(-9, '', True),  # wait: timed out
            new_process.ProcessResult(0, '', False),  # rm
        ]
        with pytest.raises(TimeoutError):
            local_build.copy_coverage_binaries('re2_fuzzer', resolved)

    rm_call = mocked_execute.call_args_list[-1]
    assert rm_call[0][0] == ['docker', 'rm', '-f', 'container123']
