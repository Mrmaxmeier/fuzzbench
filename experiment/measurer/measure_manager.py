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
"""Module for measuring snapshots from trial runners."""

import collections
import datetime
import gc
import glob
import gzip
import multiprocessing
import json
import os
import pathlib
import posixpath
import sys
import tempfile
import tarfile
import time
from typing import List
import queue
from sqlalchemy import func

from common import benchmark_utils
from common import experiment_utils
from common import experiment_path as exp_path
from common import filesystem
from common import fuzzer_stats
from common import filestore_utils
from common import logs
from common import utils
from database import utils as db_utils
from database import models
from experiment.build import build_utils
from experiment.measurer import coverage_utils
from experiment.measurer import measure_worker
from experiment.measurer import run_coverage
from experiment.measurer import run_crashes
from experiment import scheduler
import experiment.measurer.datatypes as measurer_datatypes

logger = logs.Logger()

NUM_RETRIES = 3
MEASUREMENT_LOOP_WAIT = 10

# Lower bound on how long to keep waiting for a cycle's corpus archive to show
# up in the filestore before writing the cycle off. See
# _get_corpus_wait_seconds.
MIN_CORPUS_WAIT_SECONDS = 15 * 60


class CorpusNotReadyError(Exception):
    """The cycle's corpus archive is not in the filestore yet.

    Distinct from a measurement failure: nothing has gone wrong, the runner
    just has not uploaded this cycle. Raised so that measure_worker can tell
    the two apart, because they need very different retry policies.
    """


def _get_corpus_wait_seconds():
    """Returns how long to wait for a cycle's corpus archive before giving up
    on that cycle.

    The measurer decides a cycle is due at |time_started| + cycle * period,
    where time_started is when the scheduler launched the trial's container.
    The runner uploads that cycle at |first_sync| + cycle * period, where
    first_sync is after the container has booted and unpacked its seed corpus.
    Both clocks tick at the same rate, so the offset between them - container
    startup - never closes, and every cycle is asked for before it exists.

    The wait therefore has to cover startup, which is minutes at the scale
    these experiments run at, not the ~30 seconds that three loop passes used
    to allow.
    """
    return max(2 * experiment_utils.get_snapshot_seconds(),
               MIN_CORPUS_WAIT_SECONDS)


def measure_main(experiment_config):
    """Do the continuously measuring and the final measuring."""
    initialize_logs()
    logger.info('Start measuring.')

    # Start the measure loop first.
    experiment = experiment_config['experiment']
    max_total_time = experiment_config['max_total_time']
    measurers_cpus = experiment_config['measurers_cpus']
    region_coverage = experiment_config['region_coverage']
    measure_manager_loop(experiment, max_total_time, measurers_cpus,
                         region_coverage)

    # Clean up resources.
    gc.collect()

    # Do the final measuring and store the coverage data.
    coverage_utils.generate_coverage_reports(experiment_config)

    logger.info('Finished measuring.')


def _time_to_cycle(time_in_seconds: float) -> int:
    """Converts |time_in_seconds| to the corresponding cycle and returns it."""
    return time_in_seconds // experiment_utils.get_snapshot_seconds()


def _query_ids_of_measured_trials(experiment: str):
    """Returns a query of the ids of trials in |experiment| that have measured
    snapshots."""
    with db_utils.session_scope() as session:
        # No eager load of Snapshot.trial: the query below narrows to
        # Snapshot.trial_id, so the relationship is never read. SQLAlchemy 2.x
        # also rejects the string form this used to pass to joinedload().
        trials_and_snapshots_query = session.query(models.Snapshot)
        experiment_trials_filter = models.Snapshot.trial.has(
            experiment=experiment)
        experiment_trials_and_snapshots_query = (
            trials_and_snapshots_query.filter(experiment_trials_filter))
        experiment_snapshot_trial_ids_query = (
            experiment_trials_and_snapshots_query.with_entities(
                models.Snapshot.trial_id))
        return experiment_snapshot_trial_ids_query.distinct()


def _query_unmeasured_trials(experiment: str):
    """Returns a query of trials in |experiment| that have not been measured."""
    ids_of_trials_with_snapshots = _query_ids_of_measured_trials(experiment)

    with db_utils.session_scope() as session:
        trial_query = session.query(models.Trial)
        no_snapshots_filter = ~models.Trial.id.in_(ids_of_trials_with_snapshots)
        started_trials_filter = ~models.Trial.time_started.is_(None)
        experiment_trials_filter = models.Trial.experiment == experiment
        return trial_query.filter(experiment_trials_filter, no_snapshots_filter,
                                  started_trials_filter)


def _get_unmeasured_first_snapshots(
        experiment: str) -> List[measurer_datatypes.SnapshotMeasureRequest]:
    """Returns a list of unmeasured SnapshotMeasureRequests that are the first
    snapshot for their trial. The trials are trials in |experiment|."""
    trials_without_snapshots = _query_unmeasured_trials(experiment)
    return [
        measurer_datatypes.SnapshotMeasureRequest(trial.fuzzer, trial.benchmark,
                                                  trial.id, 0)
        for trial in trials_without_snapshots
    ]


SnapshotWithTime = collections.namedtuple(
    'SnapshotWithTime',
    ['fuzzer', 'benchmark', 'trial_id', 'time', 'time_started'])


def _query_measured_latest_snapshots(experiment: str):
    """Returns a generator of a SnapshotWithTime representing a snapshot that is
    the first snapshot for their trial. The trials are trials in
    |experiment|."""
    latest_time_column = func.max(models.Snapshot.time)
    # The order of these columns must correspond to the fields in
    # SnapshotWithTime.
    columns = (models.Trial.fuzzer, models.Trial.benchmark,
               models.Snapshot.trial_id, latest_time_column,
               models.Trial.time_started)
    experiment_filter = models.Snapshot.trial.has(experiment=experiment)
    group_by_columns = (models.Snapshot.trial_id, models.Trial.benchmark,
                        models.Trial.fuzzer, models.Trial.time_started)
    with db_utils.session_scope() as session:
        snapshots_query = session.query(*columns).join(
            models.Trial).filter(experiment_filter).group_by(*group_by_columns)
        return (SnapshotWithTime(*snapshot) for snapshot in snapshots_query)


def _is_cycle_due(snapshot: SnapshotWithTime, cycle: int) -> bool:
    """Returns whether real time elapsed since |snapshot|'s trial started has
    reached |cycle|'s nominal timestamp.

    Recording a snapshot - including a zero-coverage one written after
    exhausting retries, see consume_snapshots_from_response_queue - is what
    unlocks the next cycle of a trial. Without this check, a single early miss
    cascades: the next cycle is attempted seconds later, fails too because its
    corpus archive doesn't exist yet either, and unlocks the cycle after it,
    running the trial's whole timeline down to zero-coverage snapshots within
    minutes.
    """
    if snapshot.time_started is None:
        return True
    time_started = snapshot.time_started.replace(tzinfo=datetime.timezone.utc)
    elapsed = (scheduler.datetime_now() - time_started).total_seconds()
    return elapsed >= experiment_utils.get_cycle_time(cycle)


def _get_unmeasured_next_snapshots(
        experiment: str,
        max_cycle: int) -> List[measurer_datatypes.SnapshotMeasureRequest]:
    """Returns a list of the latest unmeasured SnapshotMeasureRequests of
    trials in |experiment| that have been measured at least once in
    |experiment|. |max_total_time| is used to determine if a trial has another
    snapshot left."""
    # Measure the latest snapshot of every trial that hasn't been measured
    # yet.
    latest_snapshot_query = _query_measured_latest_snapshots(experiment)
    next_snapshots = []
    for snapshot in latest_snapshot_query:
        snapshot_time = snapshot.time
        cycle = _time_to_cycle(snapshot_time)
        next_cycle = cycle + 1
        if next_cycle > max_cycle:
            continue

        if not _is_cycle_due(snapshot, next_cycle):
            continue

        snapshot_with_cycle = measurer_datatypes.SnapshotMeasureRequest(
            snapshot.fuzzer, snapshot.benchmark, snapshot.trial_id, next_cycle)
        next_snapshots.append(snapshot_with_cycle)
    return next_snapshots


def get_unmeasured_snapshots(
        experiment: str,
        max_cycle: int) -> List[measurer_datatypes.SnapshotMeasureRequest]:
    """Returns a list of SnapshotMeasureRequests that need to be measured
    (assuming they have been saved already)."""
    # Measure the first snapshot of every started trial without any measured
    # snapshots.
    unmeasured_first_snapshots = _get_unmeasured_first_snapshots(experiment)

    unmeasured_latest_snapshots = _get_unmeasured_next_snapshots(
        experiment, max_cycle)

    # Measure the latest unmeasured snapshot of every other trial.
    return unmeasured_first_snapshots + unmeasured_latest_snapshots


def extract_corpus(corpus_archive: str, output_directory: str):
    """Extract a corpus from |corpus_archive| to |output_directory|."""
    pathlib.Path(output_directory).mkdir(exist_ok=True)
    with tarfile.open(corpus_archive, 'r:gz') as tar:
        for member in tar.getmembers():

            if not member.isfile():
                # We don't care about directory structure.
                # So skip if not a file.
                continue

            member_file_handle = tar.extractfile(member)
            if not member_file_handle:
                logger.info('Failed to get handle to %s.', member)
                continue

            # TODO(metzman): Consider removing the hashing. We don't really need
            # it anymore.
            member_contents = member_file_handle.read()
            filename = utils.string_hash(member_contents)
            file_path = os.path.join(output_directory, filename)

            if os.path.exists(file_path):
                # Don't write out duplicates in the archive.
                continue

            filesystem.write(file_path, member_contents, 'wb')


class SnapshotMeasurer(coverage_utils.TrialCoverage):  # pylint: disable=too-many-instance-attributes
    """Class used for storing details needed to measure coverage of a particular
    trial."""

    # pylint: disable=too-many-arguments
    def __init__(self, fuzzer: str, benchmark: str, trial_num: int,
                 trial_logger: logs.Logger, region_coverage: bool):
        super().__init__(fuzzer, benchmark, trial_num)
        self.logger = trial_logger
        self.corpus_dir = os.path.join(self.measurement_dir, 'corpus')

        self.crashes_dir = os.path.join(self.measurement_dir, 'crashes')
        self.coverage_dir = os.path.join(self.measurement_dir, 'coverage')
        self.trial_dir = os.path.join(self.work_dir, 'experiment-folders',
                                      self.benchmark_fuzzer_trial_dir)

        # Store the profraw file containing coverage data for each cycle.
        self.profraw_file_pattern = os.path.join(self.coverage_dir,
                                                 'data-%m.profraw')

        # Store the profdata file for the current trial.
        self.profdata_file = os.path.join(self.report_dir, 'data.profdata')

        # Store the coverage information in json form.
        self.cov_summary_file = os.path.join(self.report_dir,
                                             'cov_summary.json')

        # Use region coverage as coverage metric instead of branch (default)
        self.region_coverage = region_coverage

    def get_profraw_files(self):
        """Return generated profraw files."""
        return [
            f for f in glob.glob(self.profraw_file_pattern.replace('%m', '*'))
            if os.path.getsize(f)
        ]

    def initialize_measurement_dirs(self):
        """Initialize directories that will be needed for measuring
        coverage."""
        for directory in [self.corpus_dir, self.coverage_dir, self.crashes_dir]:
            filesystem.recreate_directory(directory)
        filesystem.create_directory(self.report_dir)
        # A prior cycle's summary must not survive into this one. report_dir is
        # not recreated because it also holds the cumulative .profdata file,
        # but an unchanged cov_summary.json would make a failed coverage run
        # look successful.
        if os.path.exists(self.cov_summary_file):
            os.remove(self.cov_summary_file)

    def run_cov_new_units(self):
        """Run the coverage binary on new units."""
        coverage_binary = coverage_utils.get_coverage_binary(self.benchmark)
        run_coverage.do_coverage_run(coverage_binary, self.corpus_dir,
                                     self.profraw_file_pattern,
                                     self.crashes_dir)

    def generate_summary(self, cycle: int, summary_only=False):
        """Transforms the .profdata file into json form."""
        coverage_binary = coverage_utils.get_coverage_binary(self.benchmark)
        result = coverage_utils.generate_json_summary(coverage_binary,
                                                      self.profdata_file,
                                                      self.cov_summary_file,
                                                      summary_only=summary_only)
        if result.retcode != 0:
            if cycle != 0:
                self.logger.error(
                    'Coverage summary json file generation failed for '
                    'cycle: %d.', cycle)
            else:
                self.logger.error(
                    'Coverage summary json file generation failed in the end.')

    def get_current_coverage(self) -> int:
        """Get the current number of lines covered."""
        if not os.path.exists(self.cov_summary_file):
            self.logger.warning('No coverage summary json file found.')
            return 0
        try:
            coverage_info = coverage_utils.get_coverage_infomation(
                self.cov_summary_file)
            coverage_data = coverage_info['data'][0]
            summary_data = coverage_data['totals']
            if self.region_coverage:
                code_coverage_data = summary_data['regions']
            else:
                code_coverage_data = summary_data['branches']
            code_coverage = code_coverage_data['covered']
            return code_coverage
        except Exception:  # pylint: disable=broad-except
            self.logger.error(
                'Coverage summary json file defective or missing.')
            return 0

    def generate_profdata(self, cycle: int):
        """Generate .profdata file from .profraw file."""
        files_to_merge = self.get_profraw_files()
        if os.path.isfile(self.profdata_file):
            # If coverage profdata exists, then merge it with
            # existing available data.
            files_to_merge += [self.profdata_file]

        coverage_binary = coverage_utils.get_coverage_binary(self.benchmark)
        result = coverage_utils.merge_profdata_files(
            files_to_merge, self.profdata_file, coverage_binary=coverage_binary)
        if result.retcode != 0:
            self.logger.error(
                'Coverage profdata generation failed for cycle: %d.', cycle)

    def generate_coverage_information(self, cycle: int):
        """Generate the .profdata file and then transform it into
        json summary."""
        if not self.get_profraw_files():
            self.logger.error('No valid profraw files found for cycle: %d.',
                              cycle)
            return
        self.generate_profdata(cycle)

        if not os.path.exists(self.profdata_file):
            self.logger.error('No profdata file found for cycle: %d.', cycle)
            return
        if not os.path.getsize(self.profdata_file):
            self.logger.error('Empty profdata file found for cycle: %d.', cycle)
            return
        self.generate_summary(cycle)

    def extract_corpus(self, corpus_archive_path) -> bool:
        """Extract the corpus archive for this cycle if it exists."""
        if not os.path.exists(corpus_archive_path):
            self.logger.warning('Corpus not found: %s.', corpus_archive_path)
            return False

        extract_corpus(corpus_archive_path, self.corpus_dir)
        return True

    def save_crash_files(self, cycle):
        """Save crashes in per-cycle crash archive."""
        crashes_archive_name = experiment_utils.get_crashes_archive_name(cycle)
        archive_path = os.path.join(os.path.dirname(self.crashes_dir),
                                    crashes_archive_name)
        with tarfile.open(archive_path, 'w:gz') as tar:
            tar.add(self.crashes_dir,
                    arcname=os.path.basename(self.crashes_dir))
        trial_crashes_dir = posixpath.join(self.trial_dir, 'crashes')
        archive_filestore_path = exp_path.filestore(
            posixpath.join(trial_crashes_dir, crashes_archive_name))
        filestore_utils.cp(archive_path, archive_filestore_path)
        os.remove(archive_path)

    def process_crashes(self, cycle):
        """Process and store crashes."""
        is_bug_benchmark = benchmark_utils.get_type(self.benchmark) == 'bug'
        if not is_bug_benchmark:
            return []

        if not os.listdir(self.crashes_dir):
            logs.info('No crashes found for cycle %d.', cycle)
            return []

        logs.info('Saving crash files crashes for cycle %d.', cycle)
        self.save_crash_files(cycle)

        logs.info('Processing crashes for cycle %d.', cycle)
        app_binary = coverage_utils.get_coverage_binary(self.benchmark)
        crash_metadata = run_crashes.do_crashes_run(app_binary,
                                                    self.crashes_dir)
        crashes = []
        for crash_key, crash in crash_metadata.items():
            crashes.append(
                models.Crash(crash_key=crash_key,
                             crash_testcase=crash.crash_testcase,
                             crash_type=crash.crash_type,
                             crash_address=crash.crash_address,
                             crash_state=crash.crash_state,
                             crash_stacktrace=crash.crash_stacktrace))
        return crashes

    def get_fuzzer_stats(self, cycle):
        """Get the fuzzer stats for |cycle|."""
        stats_filename = experiment_utils.get_stats_filename(cycle)
        stats_filestore_path = exp_path.filestore(
            os.path.join(self.trial_dir, stats_filename))
        try:
            return get_fuzzer_stats(stats_filestore_path)
        except (ValueError, json.decoder.JSONDecodeError):
            logger.error('Stats are invalid.')
            return None


def get_fuzzer_stats(stats_filestore_path):
    """Reads, validates and returns the stats in |stats_filestore_path|."""
    with tempfile.NamedTemporaryFile() as temp_file:
        result = filestore_utils.cp(stats_filestore_path,
                                    temp_file.name,
                                    expect_zero=False)
        if result.retcode != 0:
            return None
        stats_str = temp_file.read()
    fuzzer_stats.validate_fuzzer_stats(stats_str)
    return json.loads(stats_str)


def measure_snapshot_coverage(  # pylint: disable=too-many-locals
        fuzzer: str, benchmark: str, trial_num: int, cycle: int,
        region_coverage: bool) -> models.Snapshot:
    """Measure coverage of the snapshot for |cycle| for |trial_num| of |fuzzer|
    and |benchmark|."""
    snapshot_logger = logs.Logger(
        default_extras={
            'fuzzer': fuzzer,
            'benchmark': benchmark,
            'trial_id': str(trial_num),
            'cycle': str(cycle),
        })
    snapshot_measurer = SnapshotMeasurer(fuzzer, benchmark, trial_num,
                                         snapshot_logger, region_coverage)

    measuring_start_time = time.time()
    snapshot_logger.info('Measuring cycle: %d.', cycle)
    this_time = experiment_utils.get_cycle_time(cycle)
    corpus_archive_dst = os.path.join(
        snapshot_measurer.trial_dir, 'corpus',
        experiment_utils.get_corpus_archive_name(cycle))
    corpus_archive_src = exp_path.filestore(corpus_archive_dst)

    corpus_archive_dir = os.path.dirname(corpus_archive_dst)
    if not os.path.exists(corpus_archive_dir):
        os.makedirs(corpus_archive_dir)

    if filestore_utils.cp(corpus_archive_src,
                          corpus_archive_dst,
                          expect_zero=False).retcode:
        # Not an error: the runner has not synced this cycle yet. Tell the
        # caller so it waits rather than spending a retry.
        raise CorpusNotReadyError(
            f'Corpus archive for cycle {cycle} of trial {trial_num} is not in '
            'the filestore yet.')

    snapshot_measurer.initialize_measurement_dirs()
    snapshot_measurer.extract_corpus(corpus_archive_dst)
    # Don't keep corpus archives around longer than they need to be.
    os.remove(corpus_archive_dst)

    # Run coverage on the new corpus units.
    snapshot_measurer.run_cov_new_units()

    # Generate profdata and transform it into json form.
    snapshot_measurer.generate_coverage_information(cycle)

    # Compress and save the exported profdata snapshot.
    coverage_archive_zipped = os.path.join(
        snapshot_measurer.trial_dir, 'coverage',
        experiment_utils.get_coverage_archive_name(cycle) + '.gz')

    coverage_archive_dir = os.path.dirname(coverage_archive_zipped)
    if not os.path.exists(coverage_archive_dir):
        os.makedirs(coverage_archive_dir)

    if not os.path.exists(snapshot_measurer.cov_summary_file):
        snapshot_logger.warning('Coverage summary json not found for cycle: %d.', cycle)
        return None

    with gzip.open(str(coverage_archive_zipped), 'wb') as compressed:
        with open(snapshot_measurer.cov_summary_file, 'rb') as uncompressed:
            # avoid saving warnings so we can direct import with pandas
            compressed.write(uncompressed.readlines()[-1])

    coverage_archive_dst = exp_path.filestore(coverage_archive_zipped)
    if filestore_utils.cp(coverage_archive_zipped,
                          coverage_archive_dst,
                          expect_zero=False).retcode:
        snapshot_logger.warning('Coverage not found for cycle: %d.', cycle)
        return None

    os.remove(coverage_archive_zipped)  # no reason to keep this around

    # Run crashes again, parse stacktraces and generate crash signatures.
    crashes = snapshot_measurer.process_crashes(cycle)

    # Get the coverage summary of the new corpus units.
    branches_covered = snapshot_measurer.get_current_coverage()
    fuzzer_stats_data = snapshot_measurer.get_fuzzer_stats(cycle)
    snapshot = models.Snapshot(time=this_time,
                               trial_id=trial_num,
                               edges_covered=branches_covered,
                               fuzzer_stats=fuzzer_stats_data,
                               crashes=crashes)

    measuring_time = round(time.time() - measuring_start_time, 2)
    snapshot_logger.info('Measured cycle: %d in %f seconds.', cycle,
                         measuring_time)
    return snapshot


def set_up_coverage_binaries(pool, experiment):
    """Set up coverage binaries for all benchmarks in |experiment|."""
    # Use set comprehension to select distinct benchmarks.
    with db_utils.session_scope() as session:
        benchmarks = [
            benchmark_tuple[0]
            for benchmark_tuple in session.query(models.Trial.benchmark).
            distinct().filter(models.Trial.experiment == experiment)
        ]

    coverage_binaries_dir = build_utils.get_coverage_binaries_dir()
    filesystem.create_directory(coverage_binaries_dir)
    pool.map(set_up_coverage_binary, benchmarks)


def set_up_coverage_binary(benchmark):
    """Set up coverage binaries for |benchmark|."""
    initialize_logs()
    coverage_binaries_dir = build_utils.get_coverage_binaries_dir()
    benchmark_coverage_binary_dir = coverage_binaries_dir / benchmark
    filesystem.create_directory(benchmark_coverage_binary_dir)
    archive_name = f'coverage-build-{benchmark}.tar.gz'
    archive_filestore_path = exp_path.filestore(coverage_binaries_dir /
                                                archive_name)
    filestore_utils.cp(archive_filestore_path,
                       str(benchmark_coverage_binary_dir))
    archive_path = benchmark_coverage_binary_dir / archive_name
    with tarfile.open(archive_path, 'r:gz') as tar:
        tar.extractall(benchmark_coverage_binary_dir)
        os.remove(archive_path)
    # Archives include /src for llvm-cov reports. Scrub glibc copies that
    # would otherwise sit on a binary's RUNPATH and break measurement on a
    # host newer than the build image. Also applies to archives built before
    # llvm-tools/ was added.
    coverage_utils.scrub_host_incompatible_libs(
        str(benchmark_coverage_binary_dir))


def initialize_logs():
    """Initialize logs. This must be called on process start."""
    logs.initialize(default_extras={
        'component': 'dispatcher',
        'subcomponent': 'measurer',
    })


class AttemptState:
    """How a (trial, cycle) measurement has been going.

    Failures and waits are counted separately because they mean different
    things. A failure is a measurement that went wrong and may go wrong again,
    so it gets a small fixed budget. A wait is the corpus archive not being in
    the filestore yet, which is the normal state for the first minutes of every
    cycle and is not evidence of anything; it is bounded by wall clock instead.
    """

    def __init__(self):
        self.failures = 0
        self.first_attempt_time = time.time()

    def waited_too_long(self) -> bool:
        """Returns whether this cycle's corpus has been awaited long enough to
        conclude that it is never arriving."""
        return (time.time() -
                self.first_attempt_time) >= _get_corpus_wait_seconds()


def _abandon_snapshot(trial_id: int, cycle: int) -> models.Snapshot:
    """Returns the placeholder snapshot recorded for a cycle that could not be
    measured, which unblocks the trial's later cycles.

    Note that this loses that cycle's coverage permanently, not just its data
    point: corpus archives are incremental (see runner.archive_corpus) and the
    measurer folds each one into a cumulative .profdata, so units that are
    never extracted never contribute to any later cycle either.
    """
    return models.Snapshot(time=experiment_utils.get_cycle_time(cycle),
                           trial_id=trial_id,
                           edges_covered=0,
                           fuzzer_stats=None,
                           crashes=[])


def consume_snapshots_from_response_queue(
        response_queue,
        queued_snapshots,
        attempt_state=None) -> List[models.Snapshot]:
    """Consume response_queue and return all measured snapshots in a list.

    Requeues work that should be tried again, and records a placeholder
    snapshot for a cycle that has run out of either budget, so that the trial's
    later cycles are not blocked behind it forever.
    """
    if attempt_state is None:
        attempt_state = {}
    measured_snapshots = []
    while True:
        try:
            response_object = response_queue.get_nowait()
        except queue.Empty:
            break

        if isinstance(response_object, models.Snapshot):
            measured_snapshots.append(response_object)
            continue

        is_retry = isinstance(response_object, measurer_datatypes.RetryRequest)
        is_not_ready = isinstance(response_object,
                                  measurer_datatypes.NotReadyRequest)
        if not (is_retry or is_not_ready):
            logger.error('Type of response object not mapped! %s',
                         type(response_object))
            continue

        trial_id = response_object.trial_id
        cycle = response_object.cycle
        snapshot_identifier = (trial_id, cycle)
        state = attempt_state.setdefault(snapshot_identifier, AttemptState())

        if is_retry:
            state.failures += 1
            give_up = state.failures >= NUM_RETRIES
            give_up_reason = f'{state.failures} failures'
        else:
            give_up = state.waited_too_long()
            waited = round(time.time() - state.first_attempt_time)
            give_up_reason = f'its corpus never arrived ({waited}s)'

        if give_up:
            logger.error(
                'Giving up measuring trial %s cycle %s after %s; recording a '
                'placeholder snapshot so the trial can advance.', trial_id,
                cycle, give_up_reason)
            measured_snapshots.append(_abandon_snapshot(trial_id, cycle))
            # Leave the identifier in queued_snapshots so it is not re-queued
            # while the placeholder is being written.
            continue

        # Allow the task to be re-queued on the next loop.
        queued_snapshots.discard(snapshot_identifier)
        if is_retry:
            logger.info(
                'Rescheduling task for trial %s and cycle %s '
                '(attempt %d/%d)', trial_id, cycle, state.failures,
                NUM_RETRIES)
        else:
            logger.debug(
                'Corpus for trial %s cycle %s has not arrived yet; will try '
                'again.', trial_id, cycle)
    return measured_snapshots


def measure_manager_inner_loop(experiment: str,
                               max_cycle: int,
                               request_queue,
                               response_queue,
                               queued_snapshots,
                               attempt_state=None):
    """Reads from database to determine which snapshots needs measuring. Write
    measurements tasks to request queue, get results from response queue, and
    write measured snapshots to database. Returns False if nothing was due to
    be measured on this pass - NOT a signal that the experiment is done (the
    next cycle may simply not be due in real time yet); callers should keep
    polling until trials have actually ended."""
    if attempt_state is None:
        attempt_state = {}
    initialize_logs()
    # Read database to determine which snapshots needs measuring.
    unmeasured_snapshots = get_unmeasured_snapshots(experiment, max_cycle)
    logger.info('Retrieved %d unmeasured snapshots from measure manager',
                len(unmeasured_snapshots))

    # Write measurements requests to request queue
    for unmeasured_snapshot in unmeasured_snapshots:
        # No need to insert fuzzer and benchmark info here as it's redundant
        # (Can be retrieved through trial_id).
        unmeasured_snapshot_identifier = (unmeasured_snapshot.trial_id,
                                          unmeasured_snapshot.cycle)
        # Checking if snapshot already was queued so workers will not repeat
        # measurement for same snapshot
        if unmeasured_snapshot_identifier not in queued_snapshots:
            request_queue.put(unmeasured_snapshot)
            queued_snapshots.add(unmeasured_snapshot_identifier)

    # Read results from response queue. Done even when nothing was unmeasured
    # on this pass, so that results which arrive after the last outstanding
    # request are still written rather than left sitting in the queue.
    measured_snapshots = consume_snapshots_from_response_queue(
        response_queue, queued_snapshots, attempt_state)
    logger.info('Retrieved %d measured snapshots from response queue',
                len(measured_snapshots))

    # Save measured snapshots to database.
    if measured_snapshots:
        db_utils.add_all(measured_snapshots)

    return bool(unmeasured_snapshots)


def measure_manager_loop(experiment: str,
                         max_total_time: int,
                         measurers_cpus=None,
                         region_coverage=False):  # pylint: disable=too-many-locals
    """Measure manager loop. Creates request and response queues, request
    measurements tasks from workers, retrieve measurement results from response
    queue and writes measured snapshots in database."""
    logger.info('Starting measure manager loop.')
    if not measurers_cpus:
        measurers_cpus = multiprocessing.cpu_count()
        logger.info('Number of measurer CPUs not passed as argument. using %d',
                    measurers_cpus)
    with multiprocessing.Pool(measurers_cpus) as pool, \
            multiprocessing.Manager() as manager:
        logger.info('Setting up coverage binaries')
        set_up_coverage_binaries(pool, experiment)
        request_queue = manager.Queue()
        response_queue = manager.Queue()

        config = {
            'request_queue': request_queue,
            'response_queue': response_queue,
            'region_coverage': region_coverage,
        }
        local_measure_worker = measure_worker.MeasureWorker(config)

        # Since each worker is going to be in an infinite loop, we dont need
        # result return. Workers' life scope will end automatically when there
        # are no more snapshots left to measure.
        logger.info('Starting measure worker loop for %d workers',
                    measurers_cpus)
        for _ in range(measurers_cpus):
            _result = pool.apply_async(local_measure_worker.measure_worker_loop)

        max_cycle = _time_to_cycle(max_total_time)
        queued_snapshots = set()  # type: ignore[var-annotated]
        attempt_state: dict = {}
        while not scheduler.all_trials_ended(experiment):
            # A pass that finds nothing due is not a reason to stop, so the
            # inner loop's return value isn't used here: with next-cycle
            # scheduling gated on real elapsed time, "nothing unmeasured right
            # now" is the normal state between snapshot syncs. Trials still
            # running, checked above, is the only correct stop condition.
            measure_manager_inner_loop(experiment, max_cycle, request_queue,
                                       response_queue, queued_snapshots,
                                       attempt_state)
            time.sleep(MEASUREMENT_LOOP_WAIT)

        # Trials have ended; drain any last worker responses so exhausted
        # retries still get a sentinel snapshot written.
        final_snapshots = consume_snapshots_from_response_queue(
            response_queue, queued_snapshots, attempt_state)
        if final_snapshots:
            db_utils.add_all(final_snapshots)
            logger.info('Drained %d final snapshots after trials ended.',
                        len(final_snapshots))
        logger.info('All trials ended. Ending measure manager loop')


def main():
    """Measure the experiment."""
    initialize_logs()
    multiprocessing.set_start_method('spawn')

    experiment_name = experiment_utils.get_experiment_name()

    try:
        measure_manager_loop(experiment_name, int(sys.argv[1]))
    except Exception as error:
        logs.error('Error conducting experiment.')
        raise error


if __name__ == '__main__':
    sys.exit(main())
