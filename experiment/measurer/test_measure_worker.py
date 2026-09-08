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
"""Tests for measure_worker.py."""
import queue
import threading

import pytest

from database.models import Snapshot
from experiment.measurer import measure_manager
from experiment.measurer import measure_worker
import experiment.measurer.datatypes as measurer_datatypes

# Fixtures are referenced by name as parameters, which shadows them by design.
# pylint: disable=redefined-outer-name


@pytest.fixture
def local_measure_worker():
    """Fixture for instantiating a local measure worker object"""
    # Plain queues rather than multiprocessing ones: the worker only gets and
    # puts, and test_measure_manager notes that multiprocessing queues make
    # these tests flaky.
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    region_coverage = False
    config = {
        'request_queue': request_queue,
        'response_queue': response_queue,
        'region_coverage': region_coverage
    }
    return measure_worker.MeasureWorker(config)


def test_put_snapshot_in_response_queue(local_measure_worker):
    """Tests the scenario where measure_snapshot is not None, so snapshot is put
    in response_queue"""
    request = measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark',
                                                        1, 0)
    snapshot = Snapshot(trial_id=1)
    local_measure_worker.put_result_in_response_queue(snapshot, request)
    response_queue = local_measure_worker.response_queue
    assert response_queue.qsize() == 1
    assert isinstance(response_queue.get(), Snapshot)


def test_put_retry_in_response_queue(local_measure_worker):
    """Tests the scenario where measure_snapshot is None, so task needs to be
    retried"""
    request = measurer_datatypes.RetryRequest('fuzzer', 'benchmark', 1, 0)
    snapshot = None
    local_measure_worker.put_result_in_response_queue(snapshot, request)
    response_queue = local_measure_worker.response_queue
    assert response_queue.qsize() == 1
    assert isinstance(response_queue.get(), measurer_datatypes.RetryRequest)


def test_put_not_ready_in_response_queue(local_measure_worker):
    """A cycle whose corpus has not been synced yet is reported as not ready,
    not as a failure, so it does not spend the retry budget."""
    request = measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark',
                                                        1, 0)
    local_measure_worker.put_result_in_response_queue(None,
                                                      request,
                                                      corpus_not_ready=True)
    response_queue = local_measure_worker.response_queue
    assert response_queue.qsize() == 1
    assert isinstance(response_queue.get(), measurer_datatypes.NotReadyRequest)


def test_worker_loop_maps_missing_corpus_to_not_ready(local_measure_worker,
                                                      monkeypatch):
    """The worker loop turns CorpusNotReadyError into a NotReadyRequest rather
    than the RetryRequest every other failure produces."""

    def raise_not_ready(*_args, **_kwargs):
        raise measure_manager.CorpusNotReadyError('not synced yet')

    monkeypatch.setattr(measure_manager, 'measure_snapshot_coverage',
                        raise_not_ready)
    monkeypatch.setattr(measure_worker, 'MEASUREMENT_TIMEOUT', 0)
    local_measure_worker.request_queue.put(
        measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark', 1, 0))

    # The loop never returns, so run it in a daemon thread and read the one
    # response it produces for the single request queued above.
    thread = threading.Thread(target=local_measure_worker.measure_worker_loop,
                              daemon=True)
    thread.start()
    response = local_measure_worker.response_queue.get(timeout=10)
    assert isinstance(response, measurer_datatypes.NotReadyRequest)


def test_worker_loop_maps_other_errors_to_retry(local_measure_worker,
                                                monkeypatch):
    """Any other failure keeps the bounded-retry behaviour."""

    def raise_other(*_args, **_kwargs):
        raise ValueError('coverage run broke')

    monkeypatch.setattr(measure_manager, 'measure_snapshot_coverage',
                        raise_other)
    monkeypatch.setattr(measure_worker, 'MEASUREMENT_TIMEOUT', 0)
    local_measure_worker.request_queue.put(
        measurer_datatypes.SnapshotMeasureRequest('fuzzer', 'benchmark', 1, 0))

    thread = threading.Thread(target=local_measure_worker.measure_worker_loop,
                              daemon=True)
    thread.start()
    response = local_measure_worker.response_queue.get(timeout=10)
    assert isinstance(response, measurer_datatypes.RetryRequest)
