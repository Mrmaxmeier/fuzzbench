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
"""Tests for corpus_store/store.py."""

import hashlib
import os

import pytest

from corpus_store import store as store_lib

# pylint: disable=invalid-name,redefined-outer-name

BENCHMARK = 'zlib_zlib_uncompress_fuzzer'


@pytest.fixture
def store(tmp_path):
    """Returns an empty corpus store."""
    return store_lib.CorpusStore(str(tmp_path / 'corpora'))


def write_units(directory, *contents):
    """Writes |contents| as arbitrarily named files under |directory|."""
    os.makedirs(directory, exist_ok=True)
    for index, payload in enumerate(contents):
        with open(os.path.join(directory, f'input-{index}'), 'wb') as handle:
            handle.write(payload)
    return str(directory)


def test_unit_name_is_the_sha1_libfuzzer_would_use():
    """Tests that a unit's name matches what libFuzzer names it.

    Not cosmetic: units come back from a campaign already named by libFuzzer's
    own sha1, so agreeing on the scheme is what lets a unit keep one identity
    across every round it survives.
    """
    contents = b'some input'
    assert store_lib.unit_name(contents) == hashlib.sha1(contents).hexdigest()


def test_import_collapses_duplicates(tmp_path, store):
    """Tests that the same bytes under different names import once."""
    source = write_units(tmp_path / 'src', b'aaa', b'aaa', b'bbb')
    imported = store_lib.import_units([source], store.create(BENCHMARK))
    assert imported == 2
    assert store.stats(BENCHMARK).units == 2


def test_import_walks_nested_directories(tmp_path, store):
    """Tests that a fuzzer's nested output layout is imported whole."""
    write_units(tmp_path / 'src', b'top')
    write_units(tmp_path / 'src' / 'worker1', b'nested')
    imported = store_lib.import_units([str(tmp_path / 'src')],
                                      store.create(BENCHMARK))
    assert imported == 2


def test_import_accepts_a_bare_file(tmp_path, store):
    """Tests that a single file is a legitimate source."""
    write_units(tmp_path / 'src', b'only')
    path = os.path.join(str(tmp_path / 'src'), 'input-0')
    assert store_lib.import_units([path], store.create(BENCHMARK)) == 1


def test_import_drops_empty_and_oversized_units(tmp_path, store):
    """Tests that units the runner would refuse never enter the store.

    An empty unit cannot teach the corpus anything, and one over the limit
    would be skipped when a trial unpacks its seeds, so storing either only
    makes the corpus look larger than it is.
    """
    source = write_units(tmp_path / 'src', b'',
                         b'x' * (store_lib.MAX_UNIT_BYTES + 1), b'keep')
    assert store_lib.import_units([source], store.create(BENCHMARK)) == 1


def test_commit_swaps_in_the_new_corpus(tmp_path, store):
    """Tests that committing replaces the corpus rather than merging into it."""
    store_lib.import_units([write_units(tmp_path / 'old', b'a', b'b')],
                           store.create(BENCHMARK))
    assert store.stats(BENCHMARK).units == 2

    new_dir = store.new_corpus_dir(BENCHMARK)
    store_lib.import_units([write_units(tmp_path / 'new', b'c')], new_dir)
    store.commit(BENCHMARK, new_dir)

    assert store.stats(BENCHMARK).units == 1
    names = os.listdir(store.corpus_dir(BENCHMARK))
    assert names == [store_lib.unit_name(b'c')]


def test_commit_refuses_to_install_an_empty_corpus(tmp_path, store):
    """Tests that a minimizer producing nothing cannot wipe the store.

    Every way that happens -- a merge that saw no features, a container that
    died before writing -- is a failure, and the store is the only copy.
    """
    store_lib.import_units([write_units(tmp_path / 'old', b'a')],
                           store.create(BENCHMARK))
    empty = store.new_corpus_dir(BENCHMARK)

    with pytest.raises(ValueError):
        store.commit(BENCHMARK, empty)
    assert store.stats(BENCHMARK).units == 1


def test_commit_accepts_an_empty_corpus_for_an_empty_benchmark(store):
    """Tests that the guard above does not block a benchmark that has yet to
    find anything."""
    store.commit(BENCHMARK, store.new_corpus_dir(BENCHMARK))
    assert store.stats(BENCHMARK).units == 0


def test_stage_does_not_expose_the_store_to_the_fuzzer(tmp_path, store):
    """Tests that a campaign writes into a copy.

    Fuzzers are handed an input corpus they are free to write to, and a
    campaign that dies halfway through must not leave the store in a state no
    minimization produced.
    """
    store_lib.import_units([write_units(tmp_path / 'src', b'a')],
                           store.create(BENCHMARK))
    staged = store.stage(BENCHMARK)
    assert sorted(os.listdir(staged)) == [store_lib.unit_name(b'a')]

    with open(os.path.join(staged, 'fuzzer-junk'), 'wb') as handle:
        handle.write(b'junk')
    os.unlink(os.path.join(staged, store_lib.unit_name(b'a')))

    assert store.stats(BENCHMARK).units == 1


def test_stage_of_an_untouched_benchmark_is_empty(store):
    """Tests that staging a benchmark with no corpus yet works."""
    assert not os.listdir(store.stage(BENCHMARK))


def test_record_keeps_the_image_that_did_the_minimizing(tmp_path, store):
    """Tests that provenance names the build.

    A corpus is minimal only with respect to the build that minimized it, and
    benchmark builds drift, so the digest is part of the answer.
    """
    store_lib.import_units([write_units(tmp_path / 'src', b'a')],
                           store.create(BENCHMARK))
    store.record(BENCHMARK,
                 'campaign',
                 minimizer_image='sha256:' + 'a' * 64,
                 features=42)

    meta = store.read_meta(BENCHMARK)
    assert meta['minimizer_image'] == 'sha256:' + 'a' * 64
    assert meta['units'] == 1
    assert meta['history'][-1]['operation'] == 'campaign'
    assert meta['history'][-1]['features'] == 42


def test_history_is_bounded(tmp_path, store):
    """Tests that provenance does not grow without limit."""
    store_lib.import_units([write_units(tmp_path / 'src', b'a')],
                           store.create(BENCHMARK))
    for _ in range(store_lib.MAX_HISTORY_ENTRIES + 10):
        store.record(BENCHMARK, 'campaign')
    assert len(
        store.read_meta(BENCHMARK)['history']) == store_lib.MAX_HISTORY_ENTRIES


def test_metadata_is_not_mistaken_for_a_seed(tmp_path, store):
    """Tests that bookkeeping sits beside the corpora, not inside one.

    The store's layout is what --custom-seed-corpus-dir consumes, so anything
    inside a benchmark's directory is handed to a fuzzer as an input.
    """
    store_lib.import_units([write_units(tmp_path / 'src', b'a')],
                           store.create(BENCHMARK))
    store.record(BENCHMARK, 'add')

    assert os.listdir(
        store.corpus_dir(BENCHMARK)) == [store_lib.unit_name(b'a')]
    assert store.benchmarks() == [BENCHMARK]


def test_benchmarks_ignores_bookkeeping_directories(store):
    """Tests that the scratch and metadata directories are not benchmarks."""
    store.create(BENCHMARK)
    store.work_dir()
    store.record(BENCHMARK, 'add')
    assert store.benchmarks() == [BENCHMARK]
