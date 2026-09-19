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
"""Starts a local dispatcher and sends it all the files and configurations
it needs to begin an experiment."""

import argparse
import os
import re
import subprocess
import sys
import tarfile
from collections import namedtuple
from typing import Dict, List, Optional, Union

import yaml

from common import benchmark_utils
from common import experiment_utils
from common import filestore_utils
from common import filesystem
from common import fuzzer_utils
from common import hyperqueue
from common import logs
from common import new_process
from common import utils
from common import yaml_utils

BENCHMARKS_DIR = os.path.join(utils.ROOT_DIR, 'benchmarks')
FUZZERS_DIR = os.path.join(utils.ROOT_DIR, 'fuzzers')
FUZZER_NAME_REGEX = re.compile(r'^[a-z][a-z0-9_]+$')
EXPERIMENT_CONFIG_REGEX = re.compile(r'^[a-z0-9-]{0,30}$')
FILTER_SOURCE_REGEX = re.compile(r'('
                                 r'^\.git/|'
                                 r'^\.venv/|'
                                 r'^.*\.pyc$|'
                                 r'^__pycache__/|'
                                 r'.*~$|'
                                 r'\#*\#$|'
                                 r'\.pytest_cache/|'
                                 r'.*/test_data/|'
                                 r'^docker/generated.mk$|'
                                 r'^docs/)')
DEFAULT_CONCURRENT_BUILDS = 30

Requirement = namedtuple('Requirement',
                         ['mandatory', 'type', 'lowercase', 'startswith'])


def _set_default_config_values(config: Dict[str, Union[int, str, bool]]):
    """Set the default configuration values if they are not specified."""
    config['local_experiment'] = True
    config['executor'] = experiment_utils.get_executor(config)
    config['snapshot_period'] = config.get(
        'snapshot_period', experiment_utils.DEFAULT_SNAPSHOT_SECONDS)
    config['private'] = config.get('private', False)
    config['micro_experiment'] = config.get('micro_experiment', False)
    config['runner_memory_mb'] = config.get(
        'runner_memory_mb', experiment_utils.DEFAULT_RUNNER_MEMORY_MB)


def _validate_config_parameters(
        config: Dict[str, Union[int, str, bool]],
        config_requirements: Dict[str, Requirement]) -> bool:
    """Validates if the required |params| exist in |config|."""
    if 'cloud_experiment_bucket' in config or 'cloud_web_bucket' in config:
        logs.error('"cloud_experiment_bucket" and "cloud_web_bucket" are now '
                   '"experiment_filestore" and "report_filestore".')

    missing_params, optional_params = [], []
    for param, requirement in config_requirements.items():
        if param in config:
            continue
        if requirement.mandatory:
            missing_params.append(param)
            continue
        optional_params.append(param)

    for param in missing_params:
        logs.error('Config does not contain required parameter "%s".', param)

    return not missing_params


# pylint: disable=too-many-arguments
def _validate_config_values(
        config: Dict[str, Union[str, int, bool]],
        config_requirements: Dict[str, Requirement]) -> bool:
    """Validates if |params| types and formats in |config| are correct."""

    valid = True
    for param, value in config.items():
        requirement = config_requirements.get(param, None)
        # Unrecognised parameter.
        error_param = 'Config parameter "%s" is "%s".'
        if requirement is None:
            valid = False
            error_reason = 'This parameter is not recognized.'
            logs.error(f'{error_param} {error_reason}', param, str(value))
            continue

        if not isinstance(value, requirement.type):
            valid = False
            error_reason = f'It must be a {requirement.type}.'
            logs.error(f'{error_param} {error_reason}', param, str(value))

        if not isinstance(value, str):
            continue

        if requirement.lowercase and not value.islower():
            valid = False
            error_reason = 'It must be a lowercase string.'
            logs.error(f'{error_param} {error_reason}', param, str(value))

        if requirement.startswith and not value.startswith(
                requirement.startswith):
            valid = False
            error_reason = (
                'Local experiments only support Posix file systems filestores.')
            logs.error(f'{error_param} {error_reason}', param, value)

    return valid


# pylint: disable=too-many-locals
def read_and_validate_experiment_config(config_filename: str) -> Dict:
    """Reads |config_filename|, validates it, finds as many errors as possible,
    and returns it."""
    # Reads config from file.
    config = yaml_utils.read(config_filename)

    # Requirement of each config field.
    config_requirements = {
        'experiment_filestore': Requirement(True, str, True, '/'),
        'report_filestore': Requirement(True, str, True, '/'),
        'docker_registry': Requirement(True, str, True, ''),
        'trials': Requirement(True, int, False, ''),
        'max_total_time': Requirement(True, int, False, ''),
        'snapshot_period': Requirement(False, int, False, ''),
        'local_experiment': Requirement(False, bool, False, ''),
        'private': Requirement(False, bool, False, ''),
        'merge_with_nonprivate': Requirement(False, bool, False, ''),
        'runner_num_cpu_cores': Requirement(False, int, False, ''),
        'runner_memory_mb': Requirement(False, int, False, ''),
        'micro_experiment': Requirement(False, bool, False, ''),
        'executor': Requirement(False, str, True, ''),
        'hq_binary': Requirement(False, str, False, ''),
        'hq_server_dir': Requirement(False, str, False, ''),
    }

    all_params_valid = _validate_config_parameters(config, config_requirements)
    all_values_valid = _validate_config_values(config, config_requirements)
    executor_valid = _validate_executor(config)
    memory_valid = _validate_runner_memory(config)
    if (not all_params_valid or not all_values_valid or not executor_valid or
            not memory_valid):
        raise ValidationError(f'Config: {config_filename} is invalid.')

    _set_default_config_values(config)
    if config['executor'] == experiment_utils.EXECUTOR_HYPERQUEUE:
        _resolve_hyperqueue_config(config)
    return config


def _validate_executor(config: Dict) -> bool:
    """Validates the executor settings in |config|."""
    valid = True
    executor = config.get('executor', experiment_utils.EXECUTOR_LOCAL)
    if executor not in experiment_utils.EXECUTORS:
        valid = False
        logs.error('Config parameter "executor" is "%s". It must be one of %s.',
                   executor, ', '.join(experiment_utils.EXECUTORS))

    for param in ('hq_binary', 'hq_server_dir'):
        value = config.get(param)
        if isinstance(value, str) and not os.path.isabs(value):
            valid = False
            logs.error(
                'Config parameter "%s" is "%s". It must be an absolute '
                'path.', param, value)
    return valid


def _validate_runner_memory(config: Dict) -> bool:
    """Validates the runners' memory limit in |config|."""
    memory_mb = config.get('runner_memory_mb', 0)
    # Its type is checked with the other parameters.
    if not isinstance(memory_mb, int) or memory_mb >= 0:
        return True
    logs.error(
        'Config parameter "runner_memory_mb" is "%s". It must be a number '
        'of MiB, or 0 for no limit.', memory_mb)
    return False


def _resolve_hyperqueue_config(config: Dict):
    """Fills in where the HyperQueue client and server directory are, from
    the environment if |config| doesn't say, and checks that they exist.

    The dispatcher runs in a container and gets both mounted from the host, so
    they are recorded as absolute, symlink-free paths.
    """
    binary = config.get('hq_binary') or hyperqueue.find_binary()
    if not binary or not os.path.isfile(binary):
        raise ValidationError(
            'The hyperqueue executor needs the `hq` client. Put it on PATH or '
            f'set "hq_binary" in the config (got: {binary}).')
    config['hq_binary'] = os.path.realpath(binary)

    server_dir = config.get('hq_server_dir') or hyperqueue.find_server_dir()
    if not os.path.isdir(server_dir):
        raise ValidationError(
            f'HyperQueue server directory {server_dir} does not exist. Set '
            f'{hyperqueue.HQ_SERVER_DIR_VAR} or "hq_server_dir" in the config.')
    config['hq_server_dir'] = os.path.realpath(server_dir)


class ValidationError(Exception):
    """Error validating user input to this program."""


def get_directories(parent_dir):
    """Returns a list of subdirectories in |parent_dir|."""
    return [
        directory for directory in os.listdir(parent_dir)
        if os.path.isdir(os.path.join(parent_dir, directory))
    ]


# pylint: disable=too-many-locals
def validate_custom_seed_corpus(custom_seed_corpus_dir, benchmarks):
    """Validate seed corpus provided by user"""
    if not os.path.isdir(custom_seed_corpus_dir):
        raise ValidationError(
            f'Corpus location "{custom_seed_corpus_dir}" is invalid.')

    for benchmark in benchmarks:
        benchmark_corpus_dir = os.path.join(custom_seed_corpus_dir, benchmark)
        if not os.path.exists(benchmark_corpus_dir):
            raise ValidationError('Custom seed corpus directory for '
                                  f'benchmark "{benchmark}" does not exist.')
        if not os.path.isdir(benchmark_corpus_dir):
            raise ValidationError(
                f'Seed corpus of benchmark "{benchmark}" must be a directory.')
        if not os.listdir(benchmark_corpus_dir):
            raise ValidationError(
                f'Seed corpus of benchmark "{benchmark}" is empty.')


def validate_benchmarks(benchmarks: List[str]):
    """Parses and validates list of benchmarks."""
    benchmark_types = set()
    for benchmark in set(benchmarks):
        if benchmarks.count(benchmark) > 1:
            raise ValidationError(
                f'Benchmark "{benchmark}" is included more than once.')
        # Validate benchmarks here. It's possible someone might run an
        # experiment without going through presubmit. Better to catch an invalid
        # benchmark than see it in production.
        if not benchmark_utils.validate(benchmark):
            raise ValidationError(f'Benchmark "{benchmark}" is invalid.')

        benchmark_types.add(benchmark_utils.get_type(benchmark))

    if (benchmark_utils.BenchmarkType.CODE.value in benchmark_types and
            benchmark_utils.BenchmarkType.BUG.value in benchmark_types):
        raise ValidationError(
            'Cannot mix bug benchmarks with code coverage benchmarks.')


def validate_fuzzer(fuzzer: str):
    """Parses and validates a fuzzer name."""
    if not fuzzer_utils.validate(fuzzer):
        raise ValidationError(f'Fuzzer: {fuzzer} is invalid.')


def validate_experiment_name(experiment_name: str):
    """Validate |experiment_name| so that it can be used in creating
    instances."""
    if not re.match(EXPERIMENT_CONFIG_REGEX, experiment_name):
        raise ValidationError(
            f'Experiment name "{experiment_name}" is invalid. '
            f'Must match: "{EXPERIMENT_CONFIG_REGEX.pattern}"')


def set_up_experiment_config_file(config):
    """Set up the config file that will actually be used in the
    experiment (not the one given to run_experiment.py)."""
    filesystem.recreate_directory(experiment_utils.CONFIG_DIR)
    experiment_config_filename = (
        experiment_utils.get_internal_experiment_config_relative_path())
    with open(experiment_config_filename, 'w',
              encoding='utf-8') as experiment_config_file:
        yaml.dump(config, experiment_config_file, default_flow_style=False)


def check_no_uncommitted_changes():
    """Make sure that there are no uncommitted changes."""
    if subprocess.check_output(['git', 'diff'], cwd=utils.ROOT_DIR):
        raise ValidationError('Local uncommitted changes found, exiting.')


def get_git_hash(allow_uncommitted_changes):
    """Return the git hash for the last commit in the local repo."""
    try:
        output = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                         cwd=utils.ROOT_DIR)
        return output.strip().decode('utf-8')
    except subprocess.CalledProcessError as error:
        if not allow_uncommitted_changes:
            raise error
        return ''


def start_experiment(  # pylint: disable=too-many-arguments
        experiment_name: str,
        config_filename: str,
        benchmarks: List[str],
        fuzzers: List[str],
        description: Optional[str] = None,
        no_seeds: bool = False,
        no_dictionaries: bool = False,
        allow_uncommitted_changes: bool = False,
        concurrent_builds: Optional[int] = DEFAULT_CONCURRENT_BUILDS,
        measurers_cpus: Optional[int] = None,
        runners_cpus: Optional[int] = None,
        region_coverage: bool = False,
        custom_seed_corpus_dir: Optional[str] = None):
    """Start a fuzzer benchmarking experiment."""
    if not allow_uncommitted_changes:
        check_no_uncommitted_changes()

    validate_experiment_name(experiment_name)
    validate_benchmarks(benchmarks)

    config = read_and_validate_experiment_config(config_filename)
    config['fuzzers'] = fuzzers
    config['benchmarks'] = benchmarks
    config['experiment'] = experiment_name
    config['git_hash'] = get_git_hash(allow_uncommitted_changes)
    config['no_seeds'] = no_seeds
    config['no_dictionaries'] = no_dictionaries
    config['description'] = description
    config['concurrent_builds'] = concurrent_builds
    config['measurers_cpus'] = measurers_cpus
    config['runners_cpus'] = runners_cpus
    config['runner_num_cpu_cores'] = config.get('runner_num_cpu_cores', 1)
    assert (runners_cpus is None or
            runners_cpus >= config['runner_num_cpu_cores'])
    config['region_coverage'] = region_coverage

    config['custom_seed_corpus_dir'] = custom_seed_corpus_dir
    if config['custom_seed_corpus_dir']:
        validate_custom_seed_corpus(config['custom_seed_corpus_dir'],
                                    benchmarks)

    return start_experiment_from_full_config(config)


def start_experiment_from_full_config(config):
    """Start a fuzzer benchmarking experiment from a full (internal) config."""

    set_up_experiment_config_file(config)

    start_dispatcher(config, experiment_utils.CONFIG_DIR)


def start_dispatcher(config: Dict, config_dir: str):
    """Start the dispatcher instance and run the dispatcher code on it."""
    dispatcher = Dispatcher(config)
    # Is dispatcher code being run manually (useful for debugging)?
    copy_resources_to_filestore(config_dir, config)
    if not os.getenv('MANUAL_EXPERIMENT'):
        dispatcher.start()


def copy_resources_to_filestore(config_dir: str, config: Dict):
    """Copy resources the dispatcher will need for the experiment to the
    experiment_filestore."""

    def filter_file(tar_info):
        """Filter out unnecessary directories."""
        if FILTER_SOURCE_REGEX.match(tar_info.name):
            return None
        return tar_info

    # Set environment variables to use corresponding filestore_utils.
    os.environ['EXPERIMENT_FILESTORE'] = config['experiment_filestore']
    os.environ['EXPERIMENT'] = config['experiment']
    experiment_filestore_path = experiment_utils.get_experiment_filestore_path()

    base_destination = os.path.join(experiment_filestore_path, 'input')

    # Send the local source repository to the filestore for use by the
    # dispatcher. Local changes to any file will propagate.
    source_archive = 'src.tar.gz'
    with tarfile.open(source_archive, 'w:gz') as tar:
        tar.add(utils.ROOT_DIR, arcname='', recursive=True, filter=filter_file)
    filestore_utils.cp(source_archive, base_destination + '/', parallel=True)
    os.remove(source_archive)

    # Send config files.
    destination = os.path.join(base_destination, 'config')
    filestore_utils.rsync(config_dir, destination, parallel=True)

    if config['custom_seed_corpus_dir']:
        for benchmark in config['benchmarks']:
            benchmark_custom_corpus_dir = os.path.join(
                config['custom_seed_corpus_dir'], benchmark)
            filestore_utils.cp(
                benchmark_custom_corpus_dir,
                experiment_utils.get_custom_seed_corpora_filestore_path() + '/',
                recursive=True,
                parallel=True)


# Environment passed to the dispatcher container on top of the experiment
# config.
#
# DOCKER_BUILDKIT: the dispatcher's `docker` CLI defaults to building through
# buildx/BuildKit, whose session protocol podman's Docker-API compatibility
# layer (used to run local experiments without a real dockerd) can't serve.
# Buildx then silently falls back to an isolated "docker-container" builder
# that can't see locally-built parent images, so `FROM localhost/...` fails to
# resolve. The legacy build path podman does support.
#
# *_NUM_THREADS: OpenBLAS, pulled in transitively by pandas/scipy for report
# generation, sizes its thread pool off the visible CPU count on *every*
# numpy-linked process, independent of any multiprocessing.Pool sizing done
# here. On high core-count hosts that alone exhausts the container's pids
# limit.
_DISPATCHER_ENV_ARGS = [
    '-e',
    'DOCKER_BUILDKIT=0',
    '-e',
    'OPENBLAS_NUM_THREADS=2',
    '-e',
    'OMP_NUM_THREADS=2',
    '-e',
    'NUMEXPR_NUM_THREADS=2',
]

DEFAULT_DOCKER_SOCKET = '/var/run/docker.sock'


def get_docker_socket() -> str:
    """Returns the path of the socket that this host's `docker` talks to.

    That is the engine holding the images an experiment builds and runs, so it
    is the one the dispatcher must be given. With rootless podman it is a
    per-user socket named by DOCKER_HOST, and /var/run/docker.sock, if it
    exists at all, belongs to a different engine.
    """
    docker_host = os.getenv('DOCKER_HOST', '')
    if docker_host.startswith('unix://'):
        return docker_host[len('unix://'):]
    return DEFAULT_DOCKER_SOCKET


def get_hyperqueue_dispatcher_args(config: Dict) -> List[str]:
    """Returns the `docker run` arguments that let the dispatcher submit jobs
    to HyperQueue: the client and the server directory, mounted from the host.

    The client reaches the server at the address in the server directory's
    access file, typically this host's own. A rootless container on the
    default network can't reach its host's addresses, so the dispatcher gets
    the host's network.
    """
    hq_binary = config['hq_binary']
    hq_server_dir = config['hq_server_dir']
    return [
        '--network=host',
        '-v',
        f'{hq_binary}:{hq_binary}:ro',
        '-v',
        f'{hq_server_dir}:{hq_server_dir}:ro',
        '-e',
        f'{hyperqueue.HQ_BINARY_VAR}={hq_binary}',
        '-e',
        f'{hyperqueue.HQ_SERVER_DIR_VAR}={hq_server_dir}',
    ]


class Dispatcher:
    """Class representing the dispatcher, which runs the experiment in a
    container on this host."""

    def __init__(self, config: Dict):
        self.config = config
        self.instance_name = experiment_utils.get_dispatcher_instance_name(
            config['experiment'])
        self.process = None

    def start(self):
        """Start the experiment on the dispatcher."""
        container_name = 'dispatcher-container'
        experiment_filestore_path = os.path.abspath(
            self.config['experiment_filestore'])
        filesystem.create_directory(experiment_filestore_path)
        # Both filestores are bind-mounted, and podman, unlike docker, won't
        # create a missing mount source.
        filesystem.create_directory(
            os.path.abspath(self.config['report_filestore']))
        sql_database_arg = (
            'SQL_DATABASE_URL=sqlite:///'
            f'{os.path.join(experiment_filestore_path, "local.db")}'
            '?check_same_thread=False')

        docker_registry = self.config['docker_registry']
        set_instance_name_arg = f'INSTANCE_NAME={self.instance_name}'
        set_experiment_arg = f'EXPERIMENT={self.config["experiment"]}'
        filestore = self.config['experiment_filestore']
        shared_experiment_filestore_arg = f'{filestore}:{filestore}'
        # TODO: (#484) Use config in function args or set as environment
        # variables.
        set_docker_registry_arg = f'DOCKER_REGISTRY={docker_registry}'
        set_experiment_filestore_arg = (
            f'EXPERIMENT_FILESTORE={self.config["experiment_filestore"]}')

        filestore = self.config['report_filestore']
        shared_report_filestore_arg = f'{filestore}:{filestore}'
        set_report_filestore_arg = f'REPORT_FILESTORE={filestore}'
        set_snapshot_period_arg = (
            f'SNAPSHOT_PERIOD={self.config["snapshot_period"]}')
        docker_image_url = f'{docker_registry}/dispatcher-image'
        set_concurrent_builds_arg = (
            f'CONCURRENT_BUILDS={self.config["concurrent_builds"]}')
        environment_args = [
            '-e',
            'LOCAL_EXPERIMENT=True',
            *_DISPATCHER_ENV_ARGS,
            '-e',
            set_instance_name_arg,
            '-e',
            set_experiment_arg,
            '-e',
            sql_database_arg,
            '-e',
            set_experiment_filestore_arg,
            '-e',
            set_snapshot_period_arg,
            '-e',
            set_report_filestore_arg,
            '-e',
            set_docker_registry_arg,
            '-e',
            set_concurrent_builds_arg,
        ]
        executor_args = []
        if (experiment_utils.get_executor(
                self.config) == experiment_utils.EXECUTOR_HYPERQUEUE):
            executor_args = get_hyperqueue_dispatcher_args(self.config)
        command = [
            'docker',
            'run',
            # Only ask for a TTY when we have one. Without this, launching an
            # experiment from anything non-interactive (a batch job, CI, a
            # background shell) fails with "cannot attach stdin to a
            # TTY-enabled container". The TTY only matters for the
            # "|| /bin/bash" fallback below, which is useless without one
            # anyway.
            *(['-ti'] if sys.stdin.isatty() else []),
            '--rm',
            # Podman caps new containers at 2048 pids. Every trial spawns a
            # `docker run` and a `docker logs -f` client inside this container,
            # both Go binaries needing several OS threads just to start, so at
            # dozens to hundreds of concurrent trials most of them abort with
            # "pthread_create failed: Resource temporarily unavailable" before
            # ever reaching the container engine's API - and those trials
            # silently never get a container. --runners-cpus/--measurers-cpus
            # don't help; they bound CPU shares, not this pids ceiling.
            '--pids-limit=-1',
            '-v',
            f'{get_docker_socket()}:{DEFAULT_DOCKER_SOCKET}',
            '-v',
            shared_experiment_filestore_arg,
            '-v',
            shared_report_filestore_arg,
        ] + environment_args + executor_args + [
            '--shm-size=2g',
            '--cap-add=SYS_PTRACE',
            '--cap-add=SYS_NICE',
            f'--name={container_name}',
            docker_image_url,
            '/bin/bash',
            '-c',
            'rsync -r '
            '"${EXPERIMENT_FILESTORE}/${EXPERIMENT}/input/" ${WORK} && '
            'mkdir ${WORK}/src && '
            'tar -xvzf ${WORK}/src.tar.gz -C ${WORK}/src && '
            'PYTHONPATH=${WORK}/src python3 '
            '${WORK}/src/experiment/dispatcher.py || '
            '/bin/bash'  # Open shell if experiment fails.
        ]
        logs.info('Starting dispatcher with container name: %s', container_name)
        return new_process.execute(command, write_to_stdout=True)


def main():
    """Run an experiment."""
    return run_experiment_main()


def run_experiment_main(args=None):
    """Run an experiment."""
    logs.initialize()

    parser = argparse.ArgumentParser(
        description='Begin an experiment that evaluates fuzzers on one or '
        'more benchmarks.')

    all_benchmarks = benchmark_utils.get_all_benchmarks()
    coverage_benchmarks = benchmark_utils.get_coverage_benchmarks()

    parser.add_argument('-b',
                        '--benchmarks',
                        help=('Benchmark names. '
                              'All code coverage benchmarks of them by '
                              'default.'),
                        nargs='+',
                        required=False,
                        default=coverage_benchmarks,
                        choices=all_benchmarks)
    parser.add_argument('-c',
                        '--experiment-config',
                        help='Path to the experiment configuration yaml file.',
                        required=True)
    parser.add_argument('-e',
                        '--experiment-name',
                        help='Experiment name.',
                        required=True)
    parser.add_argument('-d',
                        '--description',
                        help='Description of the experiment.',
                        required=False)
    parser.add_argument('-cb',
                        '--concurrent-builds',
                        help='Max concurrent builds allowed.',
                        default=DEFAULT_CONCURRENT_BUILDS,
                        type=int,
                        required=False)
    parser.add_argument('-mc',
                        '--measurers-cpus',
                        help='Cpus available to the measurers.',
                        type=int,
                        required=False)
    parser.add_argument('-rc',
                        '--runners-cpus',
                        help='Cpus available to the runners.',
                        type=int,
                        required=False)
    parser.add_argument('-cs',
                        '--custom-seed-corpus-dir',
                        help='Path to the custom seed corpus',
                        required=False)

    all_fuzzers = fuzzer_utils.get_fuzzer_names()
    parser.add_argument('-f',
                        '--fuzzers',
                        help='Fuzzers to use.',
                        nargs='+',
                        required=False,
                        default=None,
                        choices=all_fuzzers)
    parser.add_argument('-ns',
                        '--no-seeds',
                        help='Should trials be conducted without seed corpora.',
                        required=False,
                        default=False,
                        action='store_true')
    parser.add_argument('-nd',
                        '--no-dictionaries',
                        help='Should trials be conducted without dictionaries.',
                        required=False,
                        default=False,
                        action='store_true')
    parser.add_argument('-a',
                        '--allow-uncommitted-changes',
                        help='Skip check that no uncommited changes made.',
                        required=False,
                        default=False,
                        action='store_true')
    parser.add_argument('-cr',
                        '--region-coverage',
                        help='Use region as coverage metric.',
                        required=False,
                        default=False,
                        action='store_true')
    args = parser.parse_args(args)
    fuzzers = args.fuzzers or all_fuzzers

    concurrent_builds = args.concurrent_builds
    if concurrent_builds is not None and concurrent_builds <= 0:
        parser.error('The concurrent build argument must be a positive number,'
                     f' received {concurrent_builds}.')

    runners_cpus = args.runners_cpus
    if runners_cpus is not None and runners_cpus <= 0:
        parser.error('The runners cpus argument must be a positive number,'
                     f' received {runners_cpus}.')

    measurers_cpus = args.measurers_cpus
    if measurers_cpus is not None and measurers_cpus <= 0:
        parser.error('The measurers cpus argument must be a positive number,'
                     f' received {measurers_cpus}.')

    # With the hyperqueue executor the runners run on the cluster's workers, so
    # only the measurers need to fit on this host, and runners_cpus caps the
    # cluster cpus the experiment's trials may hold at once.
    executor = experiment_utils.get_executor(
        yaml_utils.read(args.experiment_config))
    runners_are_local = executor == experiment_utils.EXECUTOR_LOCAL
    if (runners_are_local and runners_cpus is None and
            measurers_cpus is not None):
        parser.error('With the measurers cpus argument (received '
                     f'{measurers_cpus}) you need to specify the runners cpus '
                     'argument too.')

    local_runners_cpus = (runners_cpus or 0) if runners_are_local else 0
    if local_runners_cpus + (measurers_cpus or 0) > os.cpu_count():
        parser.error(f'The sum of runners ({local_runners_cpus}) and measurers '
                     f'cpus ({measurers_cpus}) is greater than the available '
                     f'cpu cores ({os.cpu_count()}).')

    if args.custom_seed_corpus_dir:
        if args.no_seeds:
            parser.error('Cannot enable options "custom_seed_corpus_dir" and '
                         '"no_seeds" at the same time')

    if benchmark_utils.are_benchmarks_mixed(args.benchmarks):
        benchmark_types = ';'.join(
            [f'{b}: {benchmark_utils.get_type(b)}' for b in args.benchmarks])
        raise ValidationError(
            'Selected benchmarks are a mix between coverage '
            'and bug benchmarks. This is currently not supported.'
            f'Selected benchmarks: {benchmark_types}')

    start_experiment(args.experiment_name,
                     args.experiment_config,
                     args.benchmarks,
                     fuzzers,
                     description=args.description,
                     no_seeds=args.no_seeds,
                     no_dictionaries=args.no_dictionaries,
                     allow_uncommitted_changes=args.allow_uncommitted_changes,
                     concurrent_builds=concurrent_builds,
                     measurers_cpus=measurers_cpus,
                     runners_cpus=runners_cpus,
                     region_coverage=args.region_coverage,
                     custom_seed_corpus_dir=args.custom_seed_corpus_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
