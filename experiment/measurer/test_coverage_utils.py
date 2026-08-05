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
# See the License for the specific language governing permissions andsss
# limitations under the License.
"""Tests for coverage_utils.py"""
import os
from unittest import mock

import pytest

from experiment.measurer import coverage_utils

TEST_DATA_PATH = os.path.join(os.path.dirname(__file__), 'test_data')

# pylint: disable=unused-argument


def get_test_data_path(*subpaths):
    """Returns the path of |subpaths| relative to TEST_DATA_PATH."""
    return os.path.join(TEST_DATA_PATH, *subpaths)


def test_extract_covered_branches_from_summary_json(fs):
    """Tests that extract_covered_branches_from_summary_json returns the covered
    branches from summary json file."""
    summary_json_file = get_test_data_path('cov_summary.json')
    fs.add_real_file(summary_json_file, read_only=False)
    covered_branches = coverage_utils. \
    extract_covered_branches_from_summary_json(
        summary_json_file)
    assert len(covered_branches) == 9


@mock.patch('common.benchmark_utils.get_fuzz_target',
            return_value='fuzz-target')
@mock.patch('common.fuzzer_utils.get_fuzz_target_binary', return_value=None)
def test_get_coverage_binary_missing(_mocked_binary, _mocked_target,
                                     experiment):
    """Tests that a missing coverage binary raises an error naming the
    benchmark. It used to return None, which every caller passed into a
    subprocess argv, so a failed coverage build surfaced as an opaque
    TypeError from inside subprocess instead."""
    with pytest.raises(coverage_utils.CoverageBinaryNotFound,
                       match='benchmark-a'):
        coverage_utils.get_coverage_binary('benchmark-a')


@mock.patch('common.benchmark_utils.get_fuzz_target',
            return_value='fuzz-target')
@mock.patch('common.fuzzer_utils.get_fuzz_target_binary',
            return_value='/work/coverage-binaries/benchmark-a/fuzz-target')
def test_get_coverage_binary_found(_mocked_binary, _mocked_target, experiment):
    """Tests that an existing coverage binary is returned unchanged."""
    assert (coverage_utils.get_coverage_binary('benchmark-a') ==
            '/work/coverage-binaries/benchmark-a/fuzz-target')


def test_llvm_tool_prefers_shipped_binary(fs):
    """Tests that llvm_tool uses the archive's llvm-tools when present."""
    coverage_binary = '/work/coverage-binaries/bench/fuzz-target'
    shipped = '/work/coverage-binaries/bench/llvm-tools/llvm-profdata'
    fs.create_file(coverage_binary)
    fs.create_file(shipped)
    os.chmod(shipped, 0o755)
    assert coverage_utils.llvm_tool('llvm-profdata',
                                    coverage_binary) == shipped


def test_llvm_tool_falls_back_to_path_name(fs):
    """Tests that llvm_tool falls back to PATH when llvm-tools is absent."""
    coverage_binary = '/work/coverage-binaries/bench/fuzz-target'
    fs.create_file(coverage_binary)
    assert coverage_utils.llvm_tool('llvm-profdata',
                                    coverage_binary) == 'llvm-profdata'
    assert coverage_utils.llvm_tool('llvm-cov') == 'llvm-cov'


def test_scrub_host_incompatible_libs(fs):
    """Tests that only glibc/loader SONAMEs are removed from coverage dirs."""
    root = '/work/coverage-binaries/systemd'
    keep = os.path.join(root, 'src/shared/libsystemd-shared-252.so')
    remove_paths = [
        os.path.join(root, 'src/shared/libc.so.6'),
        os.path.join(root, 'src/shared/libm.so.6'),
        os.path.join(root, 'src/shared/ld-linux-x86-64.so.2'),
    ]
    fs.create_file(keep)
    for path in remove_paths:
        fs.create_file(path)

    coverage_utils.scrub_host_incompatible_libs(root)

    assert os.path.exists(keep)
    for path in remove_paths:
        assert not os.path.exists(path)
