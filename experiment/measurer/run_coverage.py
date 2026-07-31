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
"""Module for running a clang source-based coverage instrumented binary
on a corpus."""

import os
import tempfile

from common import experiment_utils
from common import logs
from common import new_process
from common import sanitizer

logger = logs.Logger()

# Time buffer for libfuzzer merge to gracefully exit.
EXIT_BUFFER = 15

# Memory limit for libfuzzer merge.
RSS_LIMIT_MB = 2048

# Per-unit processing timeout for libfuzzer merge.
UNIT_TIMEOUT = 10

# Max time to spend on libfuzzer merge.
MAX_TOTAL_TIME = experiment_utils.get_snapshot_seconds()


def do_coverage_run(  # pylint: disable=too-many-locals
        coverage_binary: str, new_units_dir: str, profraw_file_pattern: str,
        crashes_dir: str):
    """Does a coverage run of |coverage_binary| on |new_units_dir|. Writes
    the result to |profraw_file_pattern|."""
    # A cycle in which the fuzzer found nothing new is normal: it happens on
    # the first cycle before any unit exists, and on every cycle once the
    # fuzzer plateaus. libfuzzer's merge exits non-zero on an empty input dir
    # ("MERGE-OUTER: zero succesfull attempts"), so on those cycles its exit
    # code says nothing about whether anything went wrong.
    #
    # The run still has to happen. The binary writes its .profraw when the
    # process exits, whatever the merge did, and that file is what the rest of
    # measurement is built on: without it the cycle produces no coverage
    # summary and measure_snapshot_coverage returns None. The measure manager
    # retries a failed cycle a bounded number of times, then records a
    # zero-coverage snapshot so later cycles can still advance.
    found_new_units = bool(os.listdir(new_units_dir))

    with tempfile.TemporaryDirectory() as merge_dir:
        command = [
            coverage_binary, '-merge=1', '-dump_coverage=1',
            f'-artifact_prefix={crashes_dir}/', f'-timeout={UNIT_TIMEOUT}',
            f'-rss_limit_mb={RSS_LIMIT_MB}',
            f'-max_total_time={MAX_TOTAL_TIME - EXIT_BUFFER}', merge_dir,
            new_units_dir
        ]
        coverage_binary_dir = os.path.dirname(coverage_binary)
        env = os.environ.copy()
        env['LLVM_PROFILE_FILE'] = profraw_file_pattern
        sanitizer.set_sanitizer_options(env)
        result = new_process.execute(command,
                                     env=env,
                                     cwd=coverage_binary_dir,
                                     expect_zero=False,
                                     kill_children=True,
                                     timeout=MAX_TOTAL_TIME)

    if result.retcode != 0 and found_new_units:
        logger.error('Coverage run failed.',
                     extras={
                         'coverage_binary': coverage_binary,
                         'output': result.output[-new_process.LOG_LIMIT_FIELD:],
                     })
