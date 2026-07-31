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
"""Tests for corpus_store/engine.py."""

import os
from unittest import mock

import pytest

from common import new_process
from corpus_store import engine

# pylint: disable=invalid-name,redefined-outer-name,unused-argument

BENCHMARK = 'zlib_zlib_uncompress_fuzzer'
IMAGE = 'sha256:' + 'a' * 64

# The line libFuzzer prints at the end of a merge.
MERGE_OUTPUT = '''MERGE-INNER: 12 total files; 0 processed earlier
MERGE-OUTER: succesfull in 1 attempt(s)
MERGE-OUTER: 6 new files with 103 new features added; 87 new coverage edges
'''


@pytest.fixture
def fake_docker():
    """Captures docker invocations instead of running them."""
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return new_process.ProcessResult(0, MERGE_OUTPUT, False)

    with mock.patch.object(engine.new_process, 'execute', side_effect=execute):
        yield calls


def test_distill_parses_the_merge_result(tmp_path, fake_docker):
    """Tests that the merge's own report is what gets recorded."""
    destination = tmp_path / 'out'
    destination.mkdir()
    source = tmp_path / 'in'
    source.mkdir()

    result = engine.distill(IMAGE, BENCHMARK, [str(source)], str(destination))

    assert result.features == 103
    assert result.edges == 87
    # Units are counted from the directory, not from libFuzzer's "new files",
    # which is relative to a merge's starting point rather than a total.
    assert result.units == 0


def test_distill_mounts_every_source(tmp_path, fake_docker):
    """Tests that all sources reach the container, each on its own mount."""
    destination = tmp_path / 'out'
    destination.mkdir()
    sources = []
    for name in ('a', 'b'):
        source = tmp_path / name
        source.mkdir()
        sources.append(str(source))

    engine.distill(IMAGE, BENCHMARK, sources, str(destination))

    command = fake_docker[0]
    assert f'{sources[0]}:/work/in0:ro' in command
    assert f'{sources[1]}:/work/in1:ro' in command
    assert f'{destination}:/work/out' in command
    assert command[command.index('/work/out') - 1] == '-merge=1'


def test_distill_refuses_a_non_empty_destination(tmp_path, fake_docker):
    """Tests that a distillation cannot be turned into an in-place merge.

    libFuzzer's merge only adds to its output directory. Starting from a
    non-empty one would make the store monotone -- units accepted early are
    never reconsidered -- which is exactly what distilling from empty avoids.
    """
    destination = tmp_path / 'out'
    destination.mkdir()
    (destination / 'existing').write_bytes(b'unit')

    with pytest.raises(engine.EngineError):
        engine.distill(IMAGE, BENCHMARK, [], str(destination))
    assert not fake_docker


def test_distill_reports_a_merge_that_said_nothing(tmp_path):
    """Tests that a merge with no result line is an error rather than a
    silently empty corpus."""
    destination = tmp_path / 'out'
    destination.mkdir()

    with mock.patch.object(engine.new_process,
                           'execute',
                           return_value=new_process.ProcessResult(
                               1, 'container died', False)):
        with pytest.raises(engine.EngineError, match='did not report'):
            engine.distill(IMAGE, BENCHMARK, [], str(destination))


def test_docker_runs_as_the_invoking_user(tmp_path, fake_docker):
    """Tests that containers do not leave root-owned units in the store."""
    destination = tmp_path / 'out'
    destination.mkdir()
    engine.distill(IMAGE, BENCHMARK, [], str(destination))

    command = fake_docker[0]
    assert command[command.index('--user') +
                   1] == f'{os.getuid()}:{os.getgid()}'


def test_campaign_drives_the_fuzzer_module(tmp_path, fake_docker):
    """Tests that a campaign goes through the same fuzz() entry point a trial
    uses, so any engine in fuzzers/ can feed the store."""
    input_corpus = tmp_path / 'in'
    input_corpus.mkdir()
    output_corpus = tmp_path / 'out'
    output_corpus.mkdir()

    engine.run_campaign(IMAGE, 'libfuzzer', BENCHMARK, str(input_corpus),
                        str(output_corpus), 30)

    command = fake_docker[0]
    driver = command[-1]
    assert 'from fuzzers.libfuzzer import fuzzer' in driver
    assert "fuzzer.fuzz('/work/in', '/work/out'" in driver
    assert engine.target_binary_path(BENCHMARK) in driver


def test_campaign_bounds_the_run_from_outside(tmp_path, fake_docker):
    """Tests that the deadline is enforced by the container's pid 1.

    Not every engine takes a time limit, and killing pid 1 tears down the pid
    namespace, which is the only way to be sure a forking fuzzer leaves nothing
    running.
    """
    for name in ('in', 'out'):
        (tmp_path / name).mkdir()

    engine.run_campaign(IMAGE, 'libfuzzer', BENCHMARK, str(tmp_path / 'in'),
                        str(tmp_path / 'out'), 30)

    command = fake_docker[0]
    assert command[command.index('--entrypoint') + 1] == 'timeout'
    assert '30' in command


def test_target_binary_path_is_inside_the_image(tmp_path):
    """Tests that the target is looked for where a runner image keeps it."""
    assert engine.target_binary_path(BENCHMARK).startswith(engine.OUT_DIR + '/')
