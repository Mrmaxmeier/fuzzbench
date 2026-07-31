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
"""The parts of corpus keeping that need a built benchmark.

Minimization is done by the fuzzer's own build, not by the coverage build. The
coverage build is compiled with -fprofile-instr-generate for llvm-cov and
carries no SanitizerCoverage instrumentation, so libFuzzer's merge sees zero
features in it and distills any corpus down to nothing. Only a build made with
-fsanitize=fuzzer-no-link -- which is to say a fuzzer's own builder image --
can say whether a unit is worth keeping.

Which fuzzer, then, is a real choice: a corpus minimal for libFuzzer's notion
of features is not necessarily minimal for another engine's. libFuzzer is the
default because its feature set (edges plus value profile) is the richest of
the engines here, so a corpus distilled against it over-approximates what the
others would keep, and over-keeping is the safe direction for a seed corpus.
The image that did the work is recorded either way.
"""

import dataclasses
import os
import re
import shlex
import typing

from common import benchmark_utils
from common import logs
from common import new_process
from experiment.build import local_build

logger = logs.Logger()  # pylint: disable=invalid-name

# Where a runner image keeps the benchmark's build.
OUT_DIR = '/out'

# libFuzzer reports the outcome of a merge on a single line, e.g.
# "MERGE-OUTER: 6 new files with 103 new features added; 87 new coverage edges".
_MERGE_RESULT_RE = re.compile(
    r'MERGE-OUTER:\s+(\d+)\s+new files with\s+(\d+)\s+new features added'
    r'(?:;\s+(\d+)\s+new coverage edges)?')

# A distill of a large corpus is slow but bounded; a hung one is not.
DEFAULT_MERGE_TIMEOUT = 60 * 60


class EngineError(Exception):
    """Raised when a benchmark build cannot do what was asked of it."""


@dataclasses.dataclass
class MergeResult:
    """What a distillation kept."""

    units: int
    features: int
    edges: int


def _lock_dir_default(store_root: str) -> str:
    """Returns where to keep the image lock when nothing else says.

    The lock maps a recipe hash to the image digest it built, and it wants to
    outlive any one run. An experiment filestore is the natural home when there
    is one; without it the corpus store is the longest-lived directory around,
    and it is exactly the thing whose contents these images define.
    """
    return os.path.join(store_root, '.image-lock')


def resolve_runner_image(fuzzer: str, benchmark: str, store_root: str):
    """Resolves the runner image for |fuzzer| on |benchmark|, building it if
    the lock has never seen this recipe.

    Returns the resolver's ResolvedImage, whose digest is what gets recorded
    against the corpus.
    """
    if not os.getenv('IMAGE_LOCK_DIR') and not os.getenv(
            'EXPERIMENT_FILESTORE'):
        os.environ['IMAGE_LOCK_DIR'] = _lock_dir_default(store_root)

    local_build.init_resolver([fuzzer], [benchmark])
    return local_build.get_resolver().resolve(f'{fuzzer}-{benchmark}-runner')


def target_binary_path(benchmark: str) -> str:
    """Returns the fuzz target's path inside a runner image."""
    fuzz_target = benchmark_utils.get_fuzz_target(benchmark)
    if not fuzz_target:
        raise EngineError(f'{benchmark} has no fuzz_target in benchmark.yaml.')
    return os.path.join(OUT_DIR, fuzz_target)


def _docker_run(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        image: str,
        entrypoint: str,
        args: typing.List[str],
        volumes: typing.List[typing.Tuple[str, str, bool]],
        timeout: int = None,
        env: typing.Dict[str, str] = None,
        expect_zero: bool = True) -> new_process.ProcessResult:
    """Runs |entrypoint| in |image| with |volumes| bind-mounted.

    Runs as the invoking user rather than as root. Everything these containers
    write lands in the corpus store, and a store full of root-owned units is
    one the person who created it cannot curate.
    """
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        f'{os.getuid()}:{os.getgid()}',
        # $HOME is /root in the image, which is not writable for us.
        '-e',
        'HOME=/tmp',
    ]
    for host_path, container_path, read_only in volumes:
        suffix = ':ro' if read_only else ''
        command += ['-v', f'{host_path}:{container_path}{suffix}']
    for name, value in sorted((env or {}).items()):
        command += ['-e', f'{name}={value}']
    command += ['--entrypoint', entrypoint, image] + args

    return new_process.execute(command,
                               timeout=timeout,
                               expect_zero=expect_zero,
                               kill_children=True)


def check_target(image: str, benchmark: str):
    """Fails loudly if the image does not hold a runnable fuzz target."""
    target = target_binary_path(benchmark)
    result = _docker_run(image,
                         '/bin/sh', ['-c', f'test -x {shlex.quote(target)}'],
                         volumes=[],
                         expect_zero=False)
    if result.retcode != 0:
        raise EngineError(
            f'{image} has no executable fuzz target at {target}. The image may '
            'predate a rename of the benchmark\'s fuzz_target.')


def distill(image: str,
            benchmark: str,
            sources: typing.List[str],
            destination: str,
            timeout: int = DEFAULT_MERGE_TIMEOUT) -> MergeResult:
    """Merges |sources| into the empty |destination|, keeping only units that
    contribute a feature.

    |destination| must start empty. libFuzzer's merge only ever *adds* to its
    output directory, so merging into the existing corpus would make the store
    monotone: a unit kept in round one is never reconsidered once a shorter
    input covering the same features shows up in round five. Distilling from
    empty every time also re-minimizes against the current build, which is the
    only thing that makes the result mean anything after the benchmark drifts.
    """
    if os.listdir(destination):
        raise EngineError(
            f'{destination} is not empty. A distillation has to start from an '
            'empty corpus or it can only ever grow.')

    volumes = [(destination, '/work/out', False)]
    args = [target_binary_path(benchmark), '-merge=1', '/work/out']
    for index, source in enumerate(sources):
        container_path = f'/work/in{index}'
        volumes.append((source, container_path, True))
        args.append(container_path)

    result = _docker_run(image,
                         args[0],
                         args[1:],
                         volumes=volumes,
                         timeout=timeout,
                         expect_zero=False)

    match = None
    for line in result.output.splitlines():
        found = _MERGE_RESULT_RE.search(line)
        if found:
            match = found

    if match is None:
        raise EngineError(
            f'The merge of {benchmark} did not report a result. Last output:\n'
            f'{result.output[-2000:]}')

    kept = len(os.listdir(destination))
    return MergeResult(units=kept,
                       features=int(match.group(2)),
                       edges=int(match.group(3) or 0))


def extract_benchmark_seeds(image: str, destination: str) -> int:
    """Unpacks the benchmark's own seed corpus out of |image|.

    A benchmark ships seeds two ways -- a clusterfuzz seed_corpus.zip beside the
    target and a plain /out/seeds directory -- and which one it uses is up to
    the project. Take both.
    """
    os.makedirs(destination, exist_ok=True)
    script = '''
import os, shutil, zipfile
destination = '/work/out'
count = 0
archive = os.path.join('/out', 'seed_corpus.zip')
for name in os.listdir('/out'):
    if name.endswith('_seed_corpus.zip'):
        archive = os.path.join('/out', name)
if os.path.exists(archive):
    with zipfile.ZipFile(archive) as zip_file:
        for info in zip_file.infolist():
            if info.is_dir():
                continue
            with zip_file.open(info) as source:
                with open(os.path.join(destination, f'{count:016d}'),
                          'wb') as target:
                    shutil.copyfileobj(source, target)
            count += 1
seeds_dir = os.path.join('/out', 'seeds')
if os.path.isdir(seeds_dir):
    for root, _, filenames in os.walk(seeds_dir):
        for filename in filenames:
            shutil.copyfile(os.path.join(root, filename),
                            os.path.join(destination, f'{count:016d}'))
            count += 1
print(f'EXTRACTED {count}')
'''
    result = _docker_run(image,
                         'python3', ['-c', script],
                         volumes=[(destination, '/work/out', False)],
                         expect_zero=False)
    match = re.search(r'EXTRACTED (\d+)', result.output)
    if match is None:
        raise EngineError(
            f'Could not unpack seeds from {image}:\n{result.output[-2000:]}')
    return int(match.group(1))


def run_campaign(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        image: str, fuzzer: str, benchmark: str, input_corpus: str,
        output_corpus: str, seconds: int) -> new_process.ProcessResult:
    """Fuzzes |benchmark| for |seconds|, seeded from |input_corpus|.

    Goes through the fuzzer's own fuzz() entry point, the same one a trial
    uses, so a campaign is driven exactly the way an experiment drives it and
    any engine in fuzzers/ can feed the store.

    The time limit is enforced by `timeout` running as the container's pid 1
    rather than by asking the engine to stop. Not every engine takes a
    deadline, and killing pid 1 tears down the whole pid namespace, which is
    the only way to be sure a forking fuzzer leaves nothing behind. Units are
    written as they are found, so an interrupted campaign still contributes
    everything up to the moment it was cut off.
    """
    driver = (f'from fuzzers.{fuzzer} import fuzzer; '
              f'fuzzer.fuzz({shlex.quote("/work/in")!r}, '
              f'{shlex.quote("/work/out")!r}, '
              f'{target_binary_path(benchmark)!r})')

    return _docker_run(
        image,
        'timeout',
        # SIGINT first: libFuzzer treats it as "wrap up", so a clean stop is
        # attempted before the namespace goes away.
        ['-s', 'INT', '-k', '15',
         str(seconds), 'python3', '-u', '-c', driver],
        volumes=[
            (input_corpus, '/work/in', False),
            (output_corpus, '/work/out', False),
        ],
        env={
            'BENCHMARK': benchmark,
            'FUZZER': fuzzer,
            'FUZZ_TARGET': benchmark_utils.get_fuzz_target(benchmark),
            'OUT': OUT_DIR,
        },
        # Add a grace period so docker teardown is never what times us out.
        timeout=seconds + 120,
        expect_zero=False)
