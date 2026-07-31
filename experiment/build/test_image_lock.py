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
"""Tests for image_lock.py."""

import os
import threading

import pytest

from experiment.build import image_lock

# pylint: disable=invalid-name,unused-argument,redefined-outer-name

RECIPE_HASH = 'a' * 32
DIGEST = 'sha256:' + 'b' * 64
REFERENCE = 'localhost/fuzzbench/runners/afl/zlib:' + RECIPE_HASH


@pytest.fixture
def lock_dir(tmp_path):
    """Returns an empty lock directory."""
    directory = tmp_path / 'image-lock'
    directory.mkdir()
    return str(directory)


def test_get_missing_entry_returns_none(lock_dir):
    """Tests that an unknown recipe hash is simply absent."""
    assert image_lock.get(RECIPE_HASH, lock_dir) is None


def test_put_then_get_round_trips(lock_dir):
    """Tests that a recorded entry can be read back."""
    image_lock.put(RECIPE_HASH, DIGEST, 'afl-zlib-runner', REFERENCE, lock_dir)
    entry = image_lock.get(RECIPE_HASH, lock_dir)
    assert entry['digest'] == DIGEST
    assert entry['image_name'] == 'afl-zlib-runner'
    assert entry['reference'] == REFERENCE


def test_put_is_first_writer_wins(lock_dir):
    """Tests that a second writer for the same key does not overwrite the
    first, and is told what the winning digest is.

    Two builders that miss the same key concurrently produce two different
    images. If both were recorded, experiments using them would diverge while
    appearing to share a recipe.
    """
    first = image_lock.put(RECIPE_HASH, DIGEST, 'afl-zlib-runner', REFERENCE,
                           lock_dir)
    other_digest = 'sha256:' + 'c' * 64
    second = image_lock.put(RECIPE_HASH, other_digest, 'afl-zlib-runner',
                            REFERENCE, lock_dir)

    assert first['digest'] == DIGEST
    assert second['digest'] == DIGEST, 'the loser must adopt the winner'
    assert image_lock.get(RECIPE_HASH, lock_dir)['digest'] == DIGEST


def test_concurrent_put_agrees_on_one_digest(lock_dir):
    """Tests that many racing writers all come away with the same digest."""
    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def record(index):
        barrier.wait()
        entry = image_lock.put(RECIPE_HASH, f'sha256:{index:064d}',
                               'afl-zlib-runner', REFERENCE, lock_dir)
        with results_lock:
            results.append(entry['digest'])

    threads = [threading.Thread(target=record, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert len(set(results)) == 1, 'writers disagreed about the digest'


def test_put_leaves_no_temporary_files(lock_dir):
    """Tests that publication cleans up after itself, including on the losing
    path where the entry is not the one that lands."""
    image_lock.put(RECIPE_HASH, DIGEST, 'afl-zlib-runner', REFERENCE, lock_dir)
    image_lock.put(RECIPE_HASH, 'sha256:' + 'c' * 64, 'afl-zlib-runner',
                   REFERENCE, lock_dir)
    assert os.listdir(lock_dir) == [f'{RECIPE_HASH}.json']


def test_delete_removes_entry(lock_dir):
    """Tests that an entry can be deliberately discarded."""
    image_lock.put(RECIPE_HASH, DIGEST, 'afl-zlib-runner', REFERENCE, lock_dir)
    assert image_lock.delete(RECIPE_HASH, lock_dir) is True
    assert image_lock.get(RECIPE_HASH, lock_dir) is None
    assert image_lock.delete(RECIPE_HASH, lock_dir) is False


def test_corrupt_entry_raises_rather_than_rebuilding(lock_dir):
    """Tests that a damaged entry is an error. Treating it as absent would
    quietly build a new image and change identity."""
    path = os.path.join(lock_dir, f'{RECIPE_HASH}.json')
    with open(path, 'w', encoding='utf-8') as file_handle:
        file_handle.write('{not json')
    with pytest.raises(image_lock.CorruptLockError):
        image_lock.get(RECIPE_HASH, lock_dir)


def test_get_lock_dir_prefers_override(monkeypatch):
    """Tests that IMAGE_LOCK_DIR wins over the filestore default."""
    monkeypatch.setenv(image_lock.IMAGE_LOCK_DIR_VAR, '/somewhere/lock')
    monkeypatch.setenv('EXPERIMENT_FILESTORE', '/filestore')
    assert image_lock.get_lock_dir() == '/somewhere/lock'


def test_get_lock_dir_sits_beside_experiments(monkeypatch):
    """Tests that the lock defaults next to the experiment directories rather
    than inside one, since it is shared across experiments."""
    monkeypatch.delenv(image_lock.IMAGE_LOCK_DIR_VAR, raising=False)
    monkeypatch.setenv('EXPERIMENT_FILESTORE', '/filestore')
    assert image_lock.get_lock_dir() == '/filestore/image-lock'


def test_get_lock_dir_without_configuration_raises(monkeypatch):
    """Tests that we do not invent a location for the lock."""
    monkeypatch.delenv(image_lock.IMAGE_LOCK_DIR_VAR, raising=False)
    monkeypatch.delenv('EXPERIMENT_FILESTORE', raising=False)
    with pytest.raises(ValueError):
        image_lock.get_lock_dir()
