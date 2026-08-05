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
"""Tests for measure_manager.py."""
import datetime
import os
import shutil
from unittest import mock
import queue

import pytest

from common import experiment_utils
from common import new_process
from database import models
from database import utils as db_utils
from experiment.build import build_utils
from experiment.measurer import measure_manager
import experiment.measurer.datatypes as measurer_datatypes

TEST_DATA_PATH = os.path.join(os.path.dirname(__file__), 'test_data')

# Arbitrary values to use in tests.
FUZZER = 'fuzzer-a'
BENCHMARK = 'benchmark-a'
TRIAL_NUM = 12
FUZZERS = ['fuzzer-a', 'fuzzer-b']
BENCHMARKS = ['benchmark-1', 'benchmark-2']
NUM_TRIALS = 4
MAX_TOTAL_TIME = 100
GIT_HASH = 'FAKE-GIT-HASH'
CYCLE = 1

SNAPSHOT_LOGGER = measure_manager.logger
REGION_COVERAGE = False

# pylint: disable=unused-argument,invalid-name,redefined-outer-name,protected-access


@pytest.fixture
def db_experiment(experiment_config, db):
    """A fixture that populates the database with an experiment entity with the
    name specified in the experiment_config fixture."""
    experiment = models.Experiment(name=experiment_config['experiment'])
    db_utils.add_all([experiment])
    # yield so that the experiment exists until the using function exits.
    yield


def test_get_unmeasured_snapshots_executes_against_db(experiment_config,
                                                      db_experiment):
    """Tests that the snapshot queries actually execute. These run only inside
    a live experiment, so mocking them hides breakage from SQLAlchemy version
    changes -- a string passed to joinedload() survived the 2.x upgrade and
    only failed once the measurer ran for real."""
    experiment_name = experiment_config['experiment']
    trial = models.Trial(fuzzer=FUZZER,
                         benchmark=BENCHMARK,
                         experiment=experiment_name,
                         time_started=datetime.datetime.now())
    db_utils.add_all([trial])

    # A started trial with no snapshots yet is unmeasured, at cycle 0.
    snapshots = measure_manager.get_unmeasured_snapshots(experiment_name,
                                                         max_cycle=10)
    assert [(s.trial_id, s.cycle) for s in snapshots] == [(trial.id, 0)]

    # Once it has a snapshot, the next cycle is what needs measuring.
    db_utils.add_all([
        models.Snapshot(time=0,
                        trial_id=trial.id,
                        edges_covered=1,
                        fuzzer_stats={})
    ])
    snapshots = measure_manager.get_unmeasured_snapshots(experiment_name,
                                                         max_cycle=10)
    assert [(s.trial_id, s.cycle) for s in snapshots] == [(trial.id, 1)]


def test_get_current_coverage(fs, experiment):
    """Tests that get_current_coverage reads the correct data from json file."""
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    json_cov_summary_file = get_test_data_path('cov_summary.json')
    fs.add_real_file(json_cov_summary_file, read_only=False)
    snapshot_measurer.cov_summary_file = json_cov_summary_file
    covered_branches = snapshot_measurer.get_current_coverage()
    assert covered_branches == 7


def test_get_current_coverage_error(fs, experiment):
    """Tests that get_current_coverage returns None from a
    defective json file."""
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    json_cov_summary_file = get_test_data_path('cov_summary_defective.json')
    fs.add_real_file(json_cov_summary_file, read_only=False)
    snapshot_measurer.cov_summary_file = json_cov_summary_file
    covered_branches = snapshot_measurer.get_current_coverage()
    assert not covered_branches


def test_get_current_coverage_no_file(fs, experiment):
    """Tests that get_current_coverage returns None with no json file."""
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    json_cov_summary_file = get_test_data_path('cov_summary_not_exist.json')
    snapshot_measurer.cov_summary_file = json_cov_summary_file
    covered_branches = snapshot_measurer.get_current_coverage()
    assert not covered_branches


@mock.patch('common.new_process.execute')
@mock.patch('experiment.measurer.coverage_utils.get_coverage_binary',
            return_value='/work/coverage-binaries/benchmark-a/fuzz-target')
def test_generate_profdata_create(mocked_get_coverage_binary, mocked_execute,
                                  experiment, fs):
    """Tests that generate_profdata can run the correct command."""
    del mocked_get_coverage_binary  # Used as patch target only.
    mocked_execute.return_value = new_process.ProcessResult(0, '', False)
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    snapshot_measurer.profdata_file = '/work/reports/data.profdata'
    snapshot_measurer.profraw_file_pattern = '/work/reports/data-%m.profraw'
    profraw_file = '/work/reports/data-123.profraw'
    fs.create_file(profraw_file, contents='fake_contents')
    snapshot_measurer.generate_profdata(CYCLE)

    expected = [
        'llvm-profdata', 'merge', '-sparse', '/work/reports/data-123.profraw',
        '-o', '/work/reports/data.profdata'
    ]

    assert (len(mocked_execute.call_args_list)) == 1
    args = mocked_execute.call_args_list[0]
    assert args[0][0] == expected


@mock.patch('common.new_process.execute')
@mock.patch('experiment.measurer.coverage_utils.get_coverage_binary',
            return_value='/work/coverage-binaries/benchmark-a/fuzz-target')
def test_generate_profdata_merge(mocked_get_coverage_binary, mocked_execute,
                                 experiment, fs):
    """Tests that generate_profdata can run correctly with existing profraw."""
    del mocked_get_coverage_binary  # Used as patch target only.
    mocked_execute.return_value = new_process.ProcessResult(0, '', False)
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    snapshot_measurer.profdata_file = '/work/reports/data.profdata'
    snapshot_measurer.profraw_file_pattern = '/work/reports/data-%m.profraw'
    profraw_file = '/work/reports/data-123.profraw'
    fs.create_file(profraw_file, contents='fake_contents')
    fs.create_file(snapshot_measurer.profdata_file, contents='fake_contents')
    snapshot_measurer.generate_profdata(CYCLE)

    expected = [
        'llvm-profdata', 'merge', '-sparse', '/work/reports/data-123.profraw',
        '/work/reports/data.profdata', '-o', '/work/reports/data.profdata'
    ]

    assert (len(mocked_execute.call_args_list)) == 1
    args = mocked_execute.call_args_list[0]
    assert args[0][0] == expected


@mock.patch('common.new_process.execute')
@mock.patch('experiment.measurer.coverage_utils.get_coverage_binary')
def test_generate_profdata_uses_shipped_llvm_tool(mocked_get_coverage_binary,
                                                  mocked_execute, experiment,
                                                  fs):
    """Tests that generate_profdata prefers llvm-tools next to the binary."""
    coverage_binary = '/work/coverage-binaries/benchmark-a/fuzz-target'
    shipped = '/work/coverage-binaries/benchmark-a/llvm-tools/llvm-profdata'
    mocked_get_coverage_binary.return_value = coverage_binary
    mocked_execute.return_value = new_process.ProcessResult(0, '', False)
    fs.create_file(coverage_binary)
    fs.create_file(shipped)
    os.chmod(shipped, 0o755)

    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    snapshot_measurer.profdata_file = '/work/reports/data.profdata'
    snapshot_measurer.profraw_file_pattern = '/work/reports/data-%m.profraw'
    profraw_file = '/work/reports/data-123.profraw'
    fs.create_file(profraw_file, contents='fake_contents')
    snapshot_measurer.generate_profdata(CYCLE)

    expected = [
        shipped, 'merge', '-sparse', '/work/reports/data-123.profraw', '-o',
        '/work/reports/data.profdata'
    ]
    assert mocked_execute.call_args_list[0][0][0] == expected


@mock.patch('common.new_process.execute')
@mock.patch('experiment.measurer.coverage_utils.get_coverage_binary')
def test_generate_summary(mocked_get_coverage_binary, mocked_execute,
                          experiment, fs):
    """Tests that generate_summary can run the correct command."""
    mocked_execute.return_value = new_process.ProcessResult(0, '', False)
    coverage_binary_path = '/work/coverage-binaries/benchmark-a/fuzz-target'
    mocked_get_coverage_binary.return_value = coverage_binary_path

    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    snapshot_measurer.cov_summary_file = '/reports/cov_summary.txt'
    snapshot_measurer.profdata_file = '/reports/data.profdata'
    fs.create_dir('/reports')
    fs.create_file(snapshot_measurer.profdata_file, contents='fake_contents')
    snapshot_measurer.generate_summary(CYCLE)

    expected = [
        'llvm-cov', 'export', '-format=text', '-num-threads=1',
        '-region-coverage-gt=0', '-skip-expansions',
        '/work/coverage-binaries/benchmark-a/fuzz-target',
        '-instr-profile=/reports/data.profdata'
    ]

    assert (len(mocked_execute.call_args_list)) == 1
    args = mocked_execute.call_args_list[0]
    assert args[0][0] == expected
    assert args[1]['output_file'].name == '/reports/cov_summary.txt'


@mock.patch('common.new_process.execute')
@mock.patch('common.benchmark_utils.get_fuzz_target',
            return_value='fuzz-target')
def test_run_cov_new_units(_, mocked_execute, fs, environ):
    """Tests that run_cov_new_units does a coverage run as we expect."""
    os.environ = {
        'WORK': '/work',
        'EXPERIMENT_FILESTORE': '/tmp/bucket',
        'EXPERIMENT': 'experiment',
    }
    mocked_execute.return_value = new_process.ProcessResult(0, '', False)
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    snapshot_measurer.initialize_measurement_dirs()
    new_units = ['new1', 'new2']
    for unit in new_units:
        fs.create_file(os.path.join(snapshot_measurer.corpus_dir, unit))
    fuzz_target_path = '/work/coverage-binaries/benchmark-a/fuzz-target'
    fs.create_file(fuzz_target_path)
    profraw_file_path = os.path.join(snapshot_measurer.coverage_dir,
                                     'data.profraw')
    fs.create_file(profraw_file_path)

    snapshot_measurer.run_cov_new_units()
    assert len(mocked_execute.call_args_list) == 1  # Called once
    args = mocked_execute.call_args_list[0]
    command_arg = args[0][0]
    assert command_arg[0] == fuzz_target_path
    expected = {
        'cwd': '/work/coverage-binaries/benchmark-a',
        'env': {
            'ASAN_OPTIONS':
                ('alloc_dealloc_mismatch=0:allocator_may_return_null=1:'
                 'allocator_release_to_os_interval_ms=500:'
                 'allow_user_segv_handler=0:check_malloc_usable_size=0:'
                 'detect_leaks=1:detect_odr_violation=0:'
                 'detect_stack_use_after_return=1:fast_unwind_on_fatal=0:'
                 'handle_abort=2:handle_segv=2:handle_sigbus=2:handle_sigfpe=2:'
                 'handle_sigill=2:max_uar_stack_size_log=16:'
                 'quarantine_size_mb=64:strict_memcmp=1:symbolize=1:'
                 'symbolize_inline_frames=0'),
            'UBSAN_OPTIONS':
                ('allocator_release_to_os_interval_ms=500:handle_abort=2:'
                 'handle_segv=2:handle_sigbus=2:handle_sigfpe=2:'
                 'handle_sigill=2:print_stacktrace=1:'
                 'symbolize=1:symbolize_inline_frames=0'),
            'LLVM_PROFILE_FILE':
                ('/work/measurement-folders/'
                 'benchmark-a-fuzzer-a/trial-12/coverage/data-%m.profraw'),
            'WORK': '/work',
            'EXPERIMENT_FILESTORE': '/tmp/bucket',
            'EXPERIMENT': 'experiment',
        },
        'expect_zero': False,
    }
    args = args[1]
    for arg, value in expected.items():
        assert args[arg] == value


def get_test_data_path(*subpaths):
    """Returns the path of |subpaths| relative to TEST_DATA_PATH."""
    return os.path.join(TEST_DATA_PATH, *subpaths)


class TestIntegrationMeasurement:
    """Integration tests for measurement."""

    # TODO(metzman): Get this test working everywhere by using docker or a more
    # portable binary.
    @pytest.mark.skipif(not os.getenv('FUZZBENCH_TEST_INTEGRATION'),
                        reason='Not running integration tests.')
    def test_measure_snapshot_coverage(  # pylint: disable=too-many-locals
            self, db, experiment, tmp_path):
        """Integration test for measure_snapshot_coverage."""
        # WORK is set by experiment to a directory that only makes sense in a
        # fakefs. A directory containing necessary llvm tools is also added to
        # PATH.
        llvm_tools_path = get_test_data_path('llvm_tools')
        os.environ['PATH'] += os.pathsep + llvm_tools_path
        os.environ['WORK'] = str(tmp_path)
        # Set up the coverage binary.
        benchmark = 'freetype2_ftfuzzer'
        coverage_binary_src = get_test_data_path(
            'test_measure_snapshot_coverage', benchmark + '-coverage')
        benchmark_cov_binary_dir = os.path.join(
            build_utils.get_coverage_binaries_dir(), benchmark)

        os.makedirs(benchmark_cov_binary_dir)
        coverage_binary_dst_dir = os.path.join(benchmark_cov_binary_dir,
                                               'ftfuzzer')

        shutil.copy(coverage_binary_src, coverage_binary_dst_dir)

        # Set up entities in database so that the snapshot can be created.
        experiment = models.Experiment(name=os.environ['EXPERIMENT'])
        db_utils.add_all([experiment])
        trial = models.Trial(fuzzer=FUZZER,
                             benchmark=benchmark,
                             experiment=os.environ['EXPERIMENT'])
        db_utils.add_all([trial])

        snapshot_measurer = measure_manager.SnapshotMeasurer(
            trial.fuzzer, trial.benchmark, trial.id, SNAPSHOT_LOGGER,
            REGION_COVERAGE)

        # Set up the snapshot archive.
        cycle = 1
        archive = get_test_data_path('test_measure_snapshot_coverage',
                                     f'corpus-archive-{cycle:04d}.tar.gz')
        corpus_dir = os.path.join(snapshot_measurer.trial_dir, 'corpus')
        os.makedirs(corpus_dir)
        shutil.copy(archive, corpus_dir)

        with mock.patch('common.filestore_utils.cp') as mocked_cp:
            mocked_cp.return_value = new_process.ProcessResult(0, '', False)
            # TODO(metzman): Create a system for using actual buckets in
            # integration tests.
            snapshot = measure_manager.measure_snapshot_coverage(
                snapshot_measurer.fuzzer, snapshot_measurer.benchmark,
                snapshot_measurer.trial_num, cycle, False)
        assert snapshot
        assert snapshot.time == cycle * experiment_utils.get_snapshot_seconds()
        assert snapshot.edges_covered == 4629


@pytest.mark.parametrize('archive_name',
                         ['libfuzzer-corpus.tgz', 'afl-corpus.tgz'])
def test_extract_corpus(archive_name, tmp_path):
    """"Tests that extract_corpus unpacks a corpus as we expect."""
    archive_path = get_test_data_path(archive_name)
    measure_manager.extract_corpus(archive_path, tmp_path)
    expected_corpus_files = {
        '5ea57dfc9631f35beecb5016c4f1366eb6faa810',
        '2f1507c3229c5a1f8b619a542a8e03ccdbb3c29c',
        'b6ccc20641188445fa30c8485a826a69ac4c6b60'
    }
    assert expected_corpus_files.issubset(set(os.listdir(tmp_path)))




def test_consume_unmapped_type_from_response_queue():
    """Tests the scenario where an unmapped type is retrieved from the response
    queue. This scenario is not expected to happen, so in this case no snapshots
    are returned."""
    # Use normal queue here as multiprocessing queue gives flaky tests.
    response_queue = queue.Queue()
    response_queue.put('unexpected string')
    snapshots = measure_manager.consume_snapshots_from_response_queue(
        response_queue, set())
    assert not snapshots


def test_initialize_measurement_dirs_clears_stale_cov_summary(fs, environ):
    """initialize_measurement_dirs deletes a leftover cov_summary.json so a
    failed cycle cannot reuse a prior cycle's summary."""
    os.environ = {
        'WORK': '/work',
        'EXPERIMENT_FILESTORE': '/tmp/bucket',
        'EXPERIMENT': 'experiment',
    }
    snapshot_measurer = measure_manager.SnapshotMeasurer(
        FUZZER, BENCHMARK, TRIAL_NUM, SNAPSHOT_LOGGER, REGION_COVERAGE)
    fs.create_dir(snapshot_measurer.report_dir)
    fs.create_file(snapshot_measurer.cov_summary_file, contents='stale')
    # Cumulative profdata must survive.
    profdata = os.path.join(snapshot_measurer.report_dir, 'data.profdata')
    fs.create_file(profdata, contents='keep')

    snapshot_measurer.initialize_measurement_dirs()

    assert not os.path.exists(snapshot_measurer.cov_summary_file)
    assert os.path.exists(profdata)


def test_consume_retry_type_from_response_queue():
    """Tests that a RetryRequest removes the snapshot from queued_snapshots so
    it can be re-queued, while under the retry limit."""
    response_queue = queue.Queue()
    retry_request_object = measurer_datatypes.RetryRequest(
        'fuzzer', 'benchmark', TRIAL_NUM, CYCLE)
    snapshot_identifier = (TRIAL_NUM, CYCLE)
    response_queue.put(retry_request_object)
    queued_snapshots_set = set([snapshot_identifier])
    retry_counts = {}
    snapshots = measure_manager.consume_snapshots_from_response_queue(
        response_queue, queued_snapshots_set, retry_counts)
    assert not snapshots
    assert len(queued_snapshots_set) == 0
    assert retry_counts[snapshot_identifier] == 1


def test_consume_retry_gives_up_after_num_retries():
    """After NUM_RETRIES failures, a zero-coverage snapshot is recorded and the
    cycle stays queued so it is not retried again."""
    response_queue = queue.Queue()
    snapshot_identifier = (TRIAL_NUM, CYCLE)
    queued_snapshots_set = {snapshot_identifier}
    retry_counts = {snapshot_identifier: measure_manager.NUM_RETRIES - 1}
    response_queue.put(
        measurer_datatypes.RetryRequest('fuzzer', 'benchmark', TRIAL_NUM,
                                        CYCLE))
    snapshots = measure_manager.consume_snapshots_from_response_queue(
        response_queue, queued_snapshots_set, retry_counts)
    assert len(snapshots) == 1
    assert snapshots[0].trial_id == TRIAL_NUM
    assert snapshots[0].edges_covered == 0
    assert snapshot_identifier in queued_snapshots_set
    assert retry_counts[snapshot_identifier] == measure_manager.NUM_RETRIES


def test_consume_snapshot_type_from_response_queue():
    """Tests the scenario where a measured snapshot is retrieved from the
    response queue. In this scenario, we want to return the snapshot in the
    function."""
    # Use normal queue here as multiprocessing queue gives flaky tests.
    response_queue = queue.Queue()
    snapshot_identifier = (TRIAL_NUM, CYCLE)
    queued_snapshots_set = set([snapshot_identifier])
    measured_snapshot = models.Snapshot(trial_id=TRIAL_NUM)
    response_queue.put(measured_snapshot)
    assert response_queue.qsize() == 1
    snapshots = measure_manager.consume_snapshots_from_response_queue(
        response_queue, queued_snapshots_set)
    assert len(snapshots) == 1


@mock.patch('experiment.measurer.measure_manager.get_unmeasured_snapshots')
def test_measure_manager_inner_loop_break_condition(
        mocked_get_unmeasured_snapshots):
    """Tests that the measure manager inner loop returns False when there's no
    more snapshots left to be measured."""
    # Empty list means no more snapshots left to be measured.
    mocked_get_unmeasured_snapshots.return_value = []
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    continue_inner_loop = measure_manager.measure_manager_inner_loop(
        'experiment', 1, request_queue, response_queue, set())
    assert not continue_inner_loop


@mock.patch('experiment.measurer.measure_manager.get_unmeasured_snapshots')
@mock.patch(
    'experiment.measurer.measure_manager.consume_snapshots_from_response_queue')
def test_measure_manager_inner_loop_writes_to_request_queue(
        mocked_consume_snapshots_from_response_queue,
        mocked_get_unmeasured_snapshots):
    """Tests that the measure manager inner loop is writing measurement tasks to
    request queue."""
    mocked_get_unmeasured_snapshots.return_value = [
        measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark', 0, 0)
    ]
    mocked_consume_snapshots_from_response_queue.return_value = []
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    measure_manager.measure_manager_inner_loop('experiment', 1, request_queue,
                                               response_queue, set())
    assert request_queue.qsize() == 1


@mock.patch('experiment.measurer.measure_manager.get_unmeasured_snapshots')
@mock.patch(
    'experiment.measurer.measure_manager.consume_snapshots_from_response_queue')
@mock.patch('database.utils.add_all')
def test_measure_manager_inner_loop_dont_write_to_db(
        mocked_add_all, mocked_consume_snapshots_from_response_queue,
        mocked_get_unmeasured_snapshots):
    """Tests that the measure manager inner loop does not call add_all to write
    to the database, when there are no measured snapshots to be written."""
    mocked_get_unmeasured_snapshots.return_value = [
        measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark', 0, 0)
    ]
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    mocked_consume_snapshots_from_response_queue.return_value = []
    measure_manager.measure_manager_inner_loop('experiment', 1, request_queue,
                                               response_queue, set())
    mocked_add_all.not_called()


@mock.patch('experiment.measurer.measure_manager.get_unmeasured_snapshots')
@mock.patch(
    'experiment.measurer.measure_manager.consume_snapshots_from_response_queue')
@mock.patch('database.utils.add_all')
def test_measure_manager_inner_loop_writes_to_db(
        mocked_add_all, mocked_consume_snapshots_from_response_queue,
        mocked_get_unmeasured_snapshots):
    """Tests that the measure manager inner loop calls add_all to write
    to the database, when there are measured snapshots to be written."""
    mocked_get_unmeasured_snapshots.return_value = [
        measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark', 0, 0)
    ]
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    snapshot_model = models.Snapshot(trial_id=1)
    mocked_consume_snapshots_from_response_queue.return_value = [snapshot_model]
    measure_manager.measure_manager_inner_loop('experiment', 1, request_queue,
                                               response_queue, set())
    mocked_add_all.assert_called_with([snapshot_model])
