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
"""On-disk layout of the corpus store.

One directory per benchmark holding content-addressed units, which is exactly
the layout `run_experiment.py --custom-seed-corpus-dir` expects, so a store can
be handed to an experiment without any repackaging.

Units are named by the sha1 of their contents, the way libFuzzer names the
units it writes. Exact duplicates therefore collapse on import for free, and a
unit keeps the same name across every campaign it survives, which is what makes
the store diffable between rounds.

Nothing here talks to docker. Deciding *which* units to keep needs an
instrumented build of the benchmark and lives in engine.py; this module only
knows how to hold the answer.
"""

import dataclasses
import datetime
import hashlib
import json
import os
import shutil
import typing

# Skip units larger than this on import. Matches the limit the runner applies
# when it unpacks a seed corpus, so the store cannot hold a unit that a trial
# would refuse to start from.
MAX_UNIT_BYTES = 1 * 1024 * 1024

# Where a store lives when nothing says otherwise. Deliberately outside the
# repository: a saturated corpus is generated data that grows without bound,
# and it is not the kind of thing to carry in git.
STORE_DIR_VAR = 'FUZZBENCH_CORPUS_STORE'
DEFAULT_STORE_DIR = os.path.join(
    os.getenv('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'fuzzbench',
    'corpora')

# Sits beside the per-benchmark directories rather than inside one, so that
# pointing --custom-seed-corpus-dir at the store never feeds bookkeeping to a
# fuzzer as if it were an input.
META_DIRNAME = '.meta'
WORK_DIRNAME = '.work'

# How many rounds of provenance to keep per benchmark.
MAX_HISTORY_ENTRIES = 50


def get_default_store_dir():
    """Returns the store location, honouring the environment."""
    return os.getenv(STORE_DIR_VAR) or DEFAULT_STORE_DIR


def unit_name(contents: bytes) -> str:
    """Returns the content-addressed name for a unit."""
    return hashlib.sha1(contents).hexdigest()


@dataclasses.dataclass
class Stats:
    """How much of a corpus there is."""

    units: int
    total_bytes: int

    @property
    def median_bytes(self) -> int:
        """Present so callers do not have to special-case an empty corpus."""
        return self.total_bytes // self.units if self.units else 0


def iter_input_files(sources: typing.Iterable[str]):
    """Yields every file under |sources|, which may name files or directories.

    Fuzzers lay their output out differently -- libFuzzer keeps crashes in a
    sibling of the corpus, others nest by worker -- so walking whatever is
    handed over is more useful than insisting on one shape.
    """
    for source in sources:
        if os.path.isfile(source):
            yield source
            continue
        for dirpath, _, filenames in os.walk(source):
            for filename in sorted(filenames):
                yield os.path.join(dirpath, filename)


def import_units(sources: typing.Iterable[str], destination: str) -> int:
    """Copies every unit under |sources| into |destination| under its
    content-addressed name. Returns the number of distinct units written.

    Empty and oversized files are dropped rather than stored: neither can teach
    the corpus anything, and the oversized ones would be skipped by the runner
    later anyway.
    """
    os.makedirs(destination, exist_ok=True)
    written = set()
    for path in iter_input_files(sources):
        size = os.path.getsize(path)
        if size == 0 or size > MAX_UNIT_BYTES:
            continue
        with open(path, 'rb') as file_handle:
            contents = file_handle.read()
        name = unit_name(contents)
        if name in written:
            continue
        written.add(name)
        target = os.path.join(destination, name)
        if not os.path.exists(target):
            shutil.copyfile(path, target)
    return len(written)


class CorpusStore:
    """A directory of per-benchmark corpora plus their provenance."""

    def __init__(self, root: str = None):
        self.root = os.path.abspath(root or get_default_store_dir())

    def __repr__(self):
        return f'<CorpusStore {self.root}>'

    def corpus_dir(self, benchmark: str) -> str:
        """Returns the directory holding |benchmark|'s units."""
        return os.path.join(self.root, benchmark)

    def meta_path(self, benchmark: str) -> str:
        """Returns the path of |benchmark|'s metadata file."""
        return os.path.join(self.root, META_DIRNAME, f'{benchmark}.json')

    def work_dir(self) -> str:
        """Returns the scratch directory, on the same filesystem as the store
        so that a finished corpus can be swapped in with a rename."""
        path = os.path.join(self.root, WORK_DIRNAME)
        os.makedirs(path, exist_ok=True)
        return path

    def scratch_dir(self, purpose: str, benchmark: str) -> str:
        """Returns a fresh scratch directory for |purpose| on |benchmark|.

        Keyed by pid as well as by purpose, so that two invocations working on
        the same benchmark cannot delete each other's working set. They will
        still race on the final commit, which no naming scheme can fix; this
        just keeps the common case of an unrelated concurrent run from
        corrupting either one.
        """
        path = os.path.join(self.work_dir(),
                            f'{purpose}-{benchmark}-{os.getpid()}')
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
        return path

    def benchmarks(self) -> typing.List[str]:
        """Returns the benchmarks this store holds a corpus for."""
        if not os.path.isdir(self.root):
            return []
        return sorted(entry for entry in os.listdir(self.root)
                      if not entry.startswith('.') and
                      os.path.isdir(os.path.join(self.root, entry)))

    def create(self, benchmark: str) -> str:
        """Creates and returns |benchmark|'s corpus directory."""
        path = self.corpus_dir(benchmark)
        os.makedirs(path, exist_ok=True)
        return path

    def stats(self, benchmark: str) -> Stats:
        """Returns how many units |benchmark| holds and how large they are."""
        directory = self.corpus_dir(benchmark)
        if not os.path.isdir(directory):
            return Stats(0, 0)
        units = 0
        total = 0
        for entry in os.scandir(directory):
            if entry.is_file():
                units += 1
                total += entry.stat().st_size
        return Stats(units, total)

    def stage(self, benchmark: str) -> str:
        """Returns a private copy of |benchmark|'s corpus for a fuzzer to
        chew on.

        Fuzzers are given a staging copy rather than the store itself. Some
        write into the input corpus they are handed, and a campaign that
        crashes halfway through must not be able to leave the store in a state
        no minimization produced.
        """
        source = self.corpus_dir(benchmark)
        staged = self.scratch_dir('staged', benchmark)
        if not os.path.isdir(source):
            return staged
        for entry in os.scandir(source):
            if not entry.is_file():
                continue
            target = os.path.join(staged, entry.name)
            try:
                # Same filesystem by construction, so this is free. The fuzzer
                # only ever adds files, so sharing the inode is safe.
                os.link(entry.path, target)
            except OSError:
                shutil.copyfile(entry.path, target)
        return staged

    def new_corpus_dir(self, benchmark: str) -> str:
        """Returns an empty directory to build |benchmark|'s next corpus in."""
        return self.scratch_dir('next', benchmark)

    def commit(self, benchmark: str, new_dir: str):
        """Replaces |benchmark|'s corpus with the contents of |new_dir|.

        Refuses to install an empty corpus over a non-empty one. Every way that
        can happen -- a merge that found no features, a container that died
        before writing anything -- is a failure rather than a real answer, and
        the store is the only copy.
        """
        if not os.listdir(new_dir) and self.stats(benchmark).units:
            raise ValueError(
                f'Refusing to replace {benchmark}\'s corpus with an empty one. '
                'The minimizer produced nothing, which means it failed rather '
                'than that the corpus is worthless.')

        current = self.corpus_dir(benchmark)
        os.makedirs(self.root, exist_ok=True)
        replaced = os.path.join(self.work_dir(),
                                f'replaced-{benchmark}-{os.getpid()}')
        shutil.rmtree(replaced, ignore_errors=True)

        if os.path.isdir(current):
            os.rename(current, replaced)
        try:
            os.rename(new_dir, current)
        except OSError:
            if os.path.isdir(replaced):
                os.rename(replaced, current)
            raise
        shutil.rmtree(replaced, ignore_errors=True)

    def read_meta(self, benchmark: str) -> dict:
        """Returns |benchmark|'s metadata, or an empty record."""
        try:
            with open(self.meta_path(benchmark), encoding='utf-8') as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {'benchmark': benchmark, 'history': []}

    def write_meta(self, benchmark: str, meta: dict):
        """Writes |benchmark|'s metadata."""
        path = self.meta_path(benchmark)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f'{path}.tmp'
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(meta, handle, indent=2, sort_keys=True)
        os.replace(temporary, path)

    def record(self, benchmark: str, operation: str, **details):
        """Appends a round of provenance to |benchmark|'s metadata.

        A corpus is minimal only with respect to the build that minimized it,
        and benchmark builds drift, so which image did the work is part of the
        answer rather than a footnote. Same reason a trial records its image
        digest.
        """
        stats = self.stats(benchmark)
        meta = self.read_meta(benchmark)
        entry = {
            'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'operation': operation,
            'units': stats.units,
            'bytes': stats.total_bytes,
        }
        entry.update(details)

        history = meta.get('history', [])
        history.append(entry)
        meta['history'] = history[-MAX_HISTORY_ENTRIES:]
        meta['benchmark'] = benchmark
        meta['units'] = stats.units
        meta['bytes'] = stats.total_bytes
        meta['updated'] = entry['time']
        for key in ('minimizer_image', 'minimizer_fuzzer', 'features', 'edges'):
            if key in details:
                meta[key] = details[key]
        self.write_meta(benchmark, meta)
        return entry

    def clean_work_dir(self):
        """Removes scratch directories left behind by a crashed run."""
        shutil.rmtree(os.path.join(self.root, WORK_DIRNAME), ignore_errors=True)
