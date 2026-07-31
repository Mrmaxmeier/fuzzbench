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

from unittest import mock

import pytest

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
        with mock.patch('experiment.build.image_resolver.Resolver') as mocked_resolver:
            local_build.init_resolver(['libfuzzer'], ['zlib_zlib_uncompress_fuzzer'])
            mocked_get.assert_called_once_with(
                ['libfuzzer'], ['zlib_zlib_uncompress_fuzzer'])
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
