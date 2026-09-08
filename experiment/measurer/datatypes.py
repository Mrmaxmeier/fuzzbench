# Copyright 2024 Google LLC
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
"""Module for common data types shared under the measurer module."""
import collections

SnapshotMeasureRequest = collections.namedtuple(
    'SnapshotMeasureRequest', ['fuzzer', 'benchmark', 'trial_id', 'cycle'])

# A measurement that failed for a reason that may not repeat: the coverage
# run errored, the summary was unreadable, the filestore hiccuped. Retried a
# bounded number of times, see measure_manager.NUM_RETRIES.
RetryRequest = collections.namedtuple(
    'RetryRequest', ['fuzzer', 'benchmark', 'trial_id', 'cycle'])

# A cycle whose corpus archive is simply not in the filestore yet. This is the
# expected state for a while after a cycle becomes due, because the measurer's
# clock starts when the trial's container is launched while the runner's starts
# when it has booted and finished unpacking seeds. It is not a failure, so it
# does not spend the retry budget above; measure_manager waits for it by wall
# clock instead.
NotReadyRequest = collections.namedtuple(
    'NotReadyRequest', ['fuzzer', 'benchmark', 'trial_id', 'cycle'])
