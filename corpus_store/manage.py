#!/usr/bin/env python3
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
"""Grow and minimize saturated corpora for FuzzBench benchmarks.

  python3 -m corpus_store.manage seed zlib_zlib_uncompress_fuzzer
  python3 -m corpus_store.manage campaign zlib_zlib_uncompress_fuzzer -t 600
  python3 -m corpus_store.manage add zlib_zlib_uncompress_fuzzer ~/some-corpus
  python3 -m corpus_store.manage status

The store's layout is the one --custom-seed-corpus-dir already expects, so
feeding a saturated corpus to an experiment needs no packaging step:

  PYTHONPATH=. python3 experiment/run_experiment.py \\
      --custom-seed-corpus-dir "$(python3 -m corpus_store.manage path)" ...
"""

import argparse
import os
import shutil
import sys

from common import benchmark_utils
from common import logs
from corpus_store import engine
from corpus_store import store as store_lib

logger = logs.Logger()  # pylint: disable=invalid-name

DEFAULT_FUZZER = 'libfuzzer'
DEFAULT_CAMPAIGN_SECONDS = 600


def report(*parts):
    """Prints a line of progress, unbuffered.

    A campaign runs for as long as it is told to, usually redirected to a log.
    Left to python's block buffering, a round's result would not appear until
    the whole run ended, which is precisely when it stops being useful.
    """
    print(''.join(parts), flush=True)


def _format_bytes(count: int) -> str:
    """Returns |count| as a human-readable size."""
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if count < 1024 or unit == 'GiB':
            return f'{count:.0f}{unit}' if unit == 'B' else f'{count:.1f}{unit}'
        count /= 1024
    return f'{count}B'


def _prepare(args):
    """Returns the store and the resolved image for |args|."""
    store = store_lib.CorpusStore(args.store)
    benchmark_utils.validate(args.benchmark)
    os.makedirs(store.root, exist_ok=True)
    resolved = engine.resolve_runner_image(args.fuzzer, args.benchmark,
                                           store.root)
    engine.check_target(resolved.digest, args.benchmark)
    logger.info('Minimizing %s against %s (%s).', args.benchmark, args.fuzzer,
                resolved.digest[:19])
    return store, resolved


def _redistill(store, resolved, args, operation, extra_sources):
    """Rebuilds the benchmark's corpus from its current units plus
    |extra_sources|, and commits the result.

    Always a full distillation from empty rather than a merge into what is
    already there. libFuzzer's merge only adds, so merging in place would make
    the store monotone: a unit accepted early is never reconsidered when a
    smaller input covering the same features turns up later, and the corpus
    would drift away from minimal one round at a time.
    """
    benchmark = args.benchmark
    before = store.stats(benchmark)

    sources = []
    if before.units:
        sources.append(store.corpus_dir(benchmark))

    staged = None
    if extra_sources:
        staged = store.scratch_dir('import', benchmark)
        imported = store_lib.import_units(extra_sources, staged)
        logger.info('Imported %d distinct units from %s.', imported,
                    ', '.join(extra_sources))
        if imported:
            sources.append(staged)

    if not sources:
        report(f'{benchmark}: nothing to minimize.')
        return None

    new_dir = store.new_corpus_dir(benchmark)
    result = engine.distill(resolved.digest, benchmark, sources, new_dir)
    store.commit(benchmark, new_dir)
    if staged:
        shutil.rmtree(staged, ignore_errors=True)

    after = store.stats(benchmark)
    store.record(benchmark,
                 operation,
                 minimizer_image=resolved.digest,
                 minimizer_fuzzer=args.fuzzer,
                 features=result.features,
                 edges=result.edges,
                 units_before=before.units)

    delta = after.units - before.units
    report(f'{benchmark}: {before.units} -> {after.units} units '
           f'({delta:+d}), {_format_bytes(after.total_bytes)}, '
           f'{result.features} features, {result.edges} edges')
    return result


def do_seed(args) -> int:
    """Imports the benchmark's own seed corpus into the store."""
    store, resolved = _prepare(args)
    seeds_dir = store.scratch_dir('seeds', args.benchmark)
    extracted = engine.extract_benchmark_seeds(resolved.digest, seeds_dir)
    logger.info('Unpacked %d seeds shipped with %s.', extracted, args.benchmark)
    if not extracted:
        report(f'{args.benchmark}: ships no seed corpus.')
        return 0
    _redistill(store, resolved, args, 'seed', [seeds_dir])
    shutil.rmtree(seeds_dir, ignore_errors=True)
    return 0


def do_add(args) -> int:
    """Imports arbitrary inputs into the store."""
    for path in args.inputs:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
    store, resolved = _prepare(args)
    _redistill(store, resolved, args, 'add', args.inputs)
    return 0


def do_minimize(args) -> int:
    """Re-distills the store's corpus against the current build."""
    store, resolved = _prepare(args)
    _redistill(store, resolved, args, 'minimize', [])
    return 0


def _keep_crashes(store, benchmark, campaign_out) -> int:
    """Files any crashers the campaign found somewhere they will not become
    seeds. A crasher is worth keeping and a poor thing to start every future
    campaign from."""
    crashes = os.path.join(campaign_out, 'crashes')
    if not os.path.isdir(crashes):
        return 0
    found = [entry for entry in os.scandir(crashes) if entry.is_file()]
    if not found:
        return 0
    destination = os.path.join(store.root, '.crashes', benchmark)
    store_lib.import_units([crashes], destination)
    return len(found)


def do_campaign(args) -> int:
    """Fuzzes the benchmark, then folds what it found back into the store."""
    store, resolved = _prepare(args)

    for round_number in range(1, args.rounds + 1):
        staged_input = store.stage(args.benchmark)
        if not os.listdir(staged_input):
            extracted = engine.extract_benchmark_seeds(resolved.digest,
                                                       staged_input)
            logger.info('Corpus was empty; started from %d shipped seeds.',
                        extracted)

        campaign_out = store.scratch_dir('campaign', args.benchmark)

        report(f'{args.benchmark}: round {round_number}/{args.rounds}, '
               f'{args.seconds}s from {len(os.listdir(staged_input))} units')
        result = engine.run_campaign(resolved.digest, args.fuzzer,
                                     args.benchmark, staged_input, campaign_out,
                                     args.seconds)

        found = os.path.join(campaign_out, 'corpus')
        if not os.path.isdir(found):
            logger.error('The campaign wrote no corpus directory. Output:\n%s',
                         result.output[-4000:])
            return 1

        crashes = _keep_crashes(store, args.benchmark, campaign_out)
        if crashes:
            report(f'{args.benchmark}: kept {crashes} crasher(s) under '
                   f'{os.path.join(store.root, ".crashes", args.benchmark)}')

        _redistill(store, resolved, args, 'campaign', [found])

        shutil.rmtree(staged_input, ignore_errors=True)
        shutil.rmtree(campaign_out, ignore_errors=True)

    return 0


def do_status(args) -> int:
    """Reports what the store holds."""
    store = store_lib.CorpusStore(args.store)
    benchmarks = args.benchmarks or store.benchmarks()
    if not benchmarks:
        print(f'{store.root} holds no corpora yet.')
        return 0

    print(f'{store.root}\n')
    header = f'{"benchmark":45s} {"units":>7s} {"size":>9s} {"updated":>20s}'
    print(header)
    print('-' * len(header))
    for benchmark in benchmarks:
        stats = store.stats(benchmark)
        meta = store.read_meta(benchmark)
        updated = (meta.get('updated') or '-')[:19]
        print(f'{benchmark:45s} {stats.units:7d} '
              f'{_format_bytes(stats.total_bytes):>9s} {updated:>20s}')

        if args.verbose:
            for entry in meta.get('history', [])[-args.verbose:]:
                print(f'    {entry["time"][:19]}  {entry["operation"]:9s} '
                      f'{entry.get("units_before", "-")} -> {entry["units"]} '
                      f'units, {entry.get("features", "-")} features')
    return 0


def do_path(args) -> int:
    """Prints the store root, for pasting into a --custom-seed-corpus-dir."""
    print(store_lib.CorpusStore(args.store).root)
    return 0


def do_clean(args) -> int:
    """Removes scratch directories left behind by an interrupted run."""
    store = store_lib.CorpusStore(args.store)
    store.clean_work_dir()
    print(f'Cleaned {os.path.join(store.root, store_lib.WORK_DIRNAME)}.')
    return 0


def _add_benchmark_arguments(parser):
    """Adds the arguments every benchmark-scoped subcommand takes."""
    parser.add_argument('benchmark')
    parser.add_argument(
        '-f',
        '--fuzzer',
        default=DEFAULT_FUZZER,
        help=('The fuzzer whose build decides which units are worth keeping. '
              f'Default {DEFAULT_FUZZER}.'))


def get_parser():
    """Returns the argument parser."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '-s',
        '--store',
        default=None,
        help=(f'Corpus store location. Defaults to ${store_lib.STORE_DIR_VAR} '
              f'or {store_lib.DEFAULT_STORE_DIR}.'))
    subparsers = parser.add_subparsers(dest='command', required=True)

    seed = subparsers.add_parser(
        'seed', help='Import the benchmark\'s own seed corpus.')
    _add_benchmark_arguments(seed)
    seed.set_defaults(func=do_seed)

    add = subparsers.add_parser('add', help='Import inputs from a path.')
    _add_benchmark_arguments(add)
    add.add_argument('inputs', nargs='+', help='Files or directories.')
    add.set_defaults(func=do_add)

    minimize = subparsers.add_parser(
        'minimize', help='Re-distill the corpus against the current build.')
    _add_benchmark_arguments(minimize)
    minimize.set_defaults(func=do_minimize)

    campaign = subparsers.add_parser(
        'campaign', help='Fuzz, then fold what was found back in.')
    _add_benchmark_arguments(campaign)
    campaign.add_argument('-t',
                          '--seconds',
                          type=int,
                          default=DEFAULT_CAMPAIGN_SECONDS,
                          help='Length of each round.')
    campaign.add_argument('-n',
                          '--rounds',
                          type=int,
                          default=1,
                          help='How many times to fuzz and fold back.')
    campaign.set_defaults(func=do_campaign)

    status = subparsers.add_parser('status', help='Report what the store has.')
    status.add_argument('benchmarks', nargs='*')
    status.add_argument('-v',
                        '--verbose',
                        nargs='?',
                        type=int,
                        const=5,
                        default=0,
                        help='Also show the last N rounds of history.')
    status.set_defaults(func=do_status)

    path = subparsers.add_parser('path', help='Print the store root.')
    path.set_defaults(func=do_path)

    clean = subparsers.add_parser('clean', help='Remove scratch directories.')
    clean.set_defaults(func=do_clean)

    return parser


def main() -> int:
    """Runs a subcommand."""
    logs.initialize()
    args = get_parser().parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
