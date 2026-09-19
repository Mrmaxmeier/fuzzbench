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
"""Tests for run_experiment.py."""

import os
import tempfile
from unittest import mock
import unittest

import pytest

from common import experiment_utils
from experiment import run_experiment

BENCHMARKS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir))

# pylint: disable=no-value-for-parameter


def test_validate_benchmarks_valid_benchmarks():
    """Tests that validate_benchmarks properly validates and parses a list of
    valid benchmarks."""
    # It won't raise an exception if everything is valid.
    run_experiment.validate_benchmarks(['freetype2_ftfuzzer', 'libxml2_xml'])


def test_validate_benchmarks_invalid_benchmark():
    """Tests that validate_benchmarks does not validate invalid benchmarks."""
    with pytest.raises(run_experiment.ValidationError):
        run_experiment.validate_benchmarks('fake_benchmark')
    with pytest.raises(run_experiment.ValidationError):
        run_experiment.validate_benchmarks('common.sh')


class TestReadAndValdiateExperimentConfig(unittest.TestCase):
    """Tests for read_and_validate_experiment_config."""

    def setUp(self):
        self.config_filename = 'config'
        self.config = {
            'experiment_filestore': '/tmp/experiment',
            'report_filestore': '/tmp/report',
            'docker_registry': 'localhost/fuzzbench',
            'trials': 10,
            'max_total_time': 1000,
        }

    @mock.patch('common.logs.error')
    def test_missing_required(self, mocked_error):
        """Tests that an error is logged when the config file is missing a
        required config parameter."""
        # All but trials.
        del self.config['trials']
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
            mocked_error.assert_called_with(
                'Config does not contain required parameter "%s".', 'trials')

    def test_invalid_upper(self):
        """Tests that an error is logged when the config file has a config
        parameter that should be a lower case string but has some upper case
        chars."""
        self._test_invalid(
            'experiment_filestore', '/EXPERIMENT',
            'Config parameter "%s" is "%s". It must be a lowercase string.')

    def test_invalid_string(self):
        """Tests that an error is logged when the config file has a config
        parameter that should be a string but is not."""
        self._test_invalid(
            'experiment_filestore', 1,
            f'Config parameter "%s" is "%s". It must be a {str}.')

    def test_invalid_local_filestore(self):
        """Tests that an error is logged when the config file has a config
        parameter that should be a local filestore but is not."""
        self._test_invalid(
            'report_filestore', 'gs://wrong-here', 'Config parameter "%s" is '
            '"%s". Local experiments only support Posix file systems '
            'filestores.')

    def test_invalid_non_posix_filestore(self):
        """Tests that an error is logged when filestore is not a Posix path."""
        self._test_invalid(
            'experiment_filestore', 'invalid', 'Config parameter "%s" is "%s". '
            'Local experiments only support Posix file systems filestores.')

    @mock.patch('common.logs.error')
    def test_multiple_invalid(self, mocked_error):
        """Test that multiple errors are logged when multiple parameters are
        invalid."""
        self.config['experiment_filestore'] = 1
        self.config['report_filestore'] = None
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
        mocked_error.assert_any_call(
            f'Config parameter "%s" is "%s". It must be a {str}.',
            'experiment_filestore', str(self.config['experiment_filestore']))
        mocked_error.assert_any_call(
            f'Config parameter "%s" is "%s". It must be a {str}.',
            'report_filestore', str(self.config['report_filestore']))

    @mock.patch('common.logs.error')
    def _test_invalid(self, param, value, expected_log_message, mocked_error):
        """Tests that |expected_log_message| is logged as an error when config
        |param| is |value| which is invalid."""
        # Don't parameterize this function, it would be too messsy.
        self.config[param] = value
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
        mocked_error.assert_called_with(expected_log_message, param, str(value))

    @mock.patch('common.logs.error')
    def test_read_and_validate_experiment_config(self, _):
        """Tests that read_and_validat_experiment_config works as intended when
        config is valid."""
        expected_config = self.config.copy()
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            validated_config = (
                run_experiment.read_and_validate_experiment_config(
                    'config_file'))
        expected_config['local_experiment'] = True
        expected_config['snapshot_period'] = (
            experiment_utils.DEFAULT_SNAPSHOT_SECONDS)
        expected_config['private'] = False
        expected_config['micro_experiment'] = False
        expected_config['executor'] = experiment_utils.EXECUTOR_LOCAL
        expected_config['runner_memory_mb'] = (
            experiment_utils.DEFAULT_RUNNER_MEMORY_MB)
        assert expected_config == validated_config

    @mock.patch('common.logs.error')
    def test_negative_runner_memory(self, mocked_error):
        """Tests that a negative memory limit is rejected."""
        self.config['runner_memory_mb'] = -1
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
        mocked_error.assert_any_call(
            'Config parameter "runner_memory_mb" is "%s". It must be a number '
            'of MiB, or 0 for no limit.', -1)

    @mock.patch('common.logs.error')
    def test_invalid_executor(self, mocked_error):
        """Tests that an unknown executor is rejected."""
        self.config['executor'] = 'slurm'
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
        mocked_error.assert_called_with(
            'Config parameter "executor" is "%s". It must be one of %s.',
            'slurm', 'local, hyperqueue')

    @mock.patch('common.logs.error')
    def test_hyperqueue_relative_path(self, mocked_error):
        """Tests that HyperQueue paths must be absolute, since the dispatcher
        container gets them mounted at the same path."""
        self.config['executor'] = 'hyperqueue'
        self.config['hq_binary'] = 'bin/hq'
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml:
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')
        mocked_error.assert_called_with(
            'Config parameter "%s" is "%s". It must be an absolute path.',
            'hq_binary', 'bin/hq')

    def test_hyperqueue_resolves_paths(self):
        """Tests that the HyperQueue client and server directory are found and
        recorded as real paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            hq_binary = os.path.join(temp_dir, 'hq')
            with open(hq_binary, 'w', encoding='utf-8'):
                pass
            server_dir = os.path.join(temp_dir, 'server')
            os.mkdir(server_dir)
            server_dir_link = os.path.join(temp_dir, 'server-link')
            os.symlink(server_dir, server_dir_link)

            self.config['executor'] = 'hyperqueue'
            self.config['hq_binary'] = hq_binary
            with mock.patch('common.yaml_utils.read') as mocked_read_yaml, \
                    mock.patch.dict(os.environ,
                                    {'HQ_SERVER_DIR': server_dir_link}):
                mocked_read_yaml.return_value = self.config
                config = run_experiment.read_and_validate_experiment_config(
                    'config_file')
            assert config['hq_binary'] == os.path.realpath(hq_binary)
            assert config['hq_server_dir'] == os.path.realpath(server_dir)

    def test_hyperqueue_missing_binary(self):
        """Tests that the hyperqueue executor requires an `hq` client."""
        self.config['executor'] = 'hyperqueue'
        with mock.patch('common.yaml_utils.read') as mocked_read_yaml, \
                mock.patch('common.hyperqueue.find_binary', return_value=None):
            mocked_read_yaml.return_value = self.config
            with pytest.raises(run_experiment.ValidationError,
                               match='needs the `hq` client'):
                run_experiment.read_and_validate_experiment_config(
                    'config_file')


def test_validate_fuzzer():
    """Tests that validate_fuzzer says that a valid fuzzer name is valid and
    that an invalid one is not."""
    run_experiment.validate_fuzzer('afl')

    with pytest.raises(run_experiment.ValidationError) as exception:
        run_experiment.validate_fuzzer('afl:')
    assert 'is invalid' in str(exception.value)

    with pytest.raises(run_experiment.ValidationError) as exception:
        run_experiment.validate_fuzzer('not_exist')
    assert 'is invalid' in str(exception.value)


def test_validate_experiment_name_valid():
    """Tests that validate_experiment_name says that a valid experiment_name is
    valid."""
    run_experiment.validate_experiment_name('experiment-1')


@pytest.mark.parametrize(('experiment_name',), [('a' * 100,), ('abc_',)])
def test_validate_experiment_name_invalid(experiment_name):
    """Tests that validate_experiment_name raises an exception when passed an
    an invalid experiment name."""
    with pytest.raises(run_experiment.ValidationError) as exception:
        run_experiment.validate_experiment_name(experiment_name)
    assert 'is invalid. Must match' in str(exception.value)


# This test takes up to a minute to complete.
@pytest.mark.slow
def test_copy_resources_to_filestore(tmp_path):
    """Tests that copy_resources_to_filestore copies the correct resources."""
    # Do this so that Ctrl-C doesn't pollute the repo.
    cwd = os.getcwd()
    os.chdir(tmp_path)

    config_dir = 'config'
    config = {
        'experiment_filestore': '/tmp/filestore-bucket',
        'experiment': 'experiment',
        'benchmarks': ['libxslt_xpath'],
        'custom_seed_corpus_dir': None,
    }
    try:
        with mock.patch('common.filestore_utils.cp') as mocked_filestore_cp:
            with mock.patch(
                    'common.filestore_utils.rsync') as mocked_filestore_rsync:
                run_experiment.copy_resources_to_filestore(config_dir, config)
                mocked_filestore_cp.assert_called_once_with(
                    'src.tar.gz',
                    '/tmp/filestore-bucket/experiment/input/',
                    parallel=True)
                mocked_filestore_rsync.assert_called_once_with(
                    'config',
                    '/tmp/filestore-bucket/experiment/input/config',
                    parallel=True)
    finally:
        os.chdir(cwd)


@pytest.mark.parametrize('docker_host,expected_socket', [
    (None, '/var/run/docker.sock'),
    ('unix:///run/user/1000/podman/podman.sock',
     '/run/user/1000/podman/podman.sock'),
    ('tcp://127.0.0.1:2375', '/var/run/docker.sock'),
])
def test_get_docker_socket(docker_host, expected_socket):
    """Tests that the dispatcher is given the socket this host's docker uses."""
    environment = {'DOCKER_HOST': docker_host} if docker_host else {}
    with mock.patch.dict(os.environ, environment, clear=True):
        assert run_experiment.get_docker_socket() == expected_socket


def _get_dispatcher_command(config, tmp_path):
    """Returns the command Dispatcher.start runs for |config|, with its
    filestores in |tmp_path|."""
    config['experiment_filestore'] = str(tmp_path / 'experiment-data')
    config['report_filestore'] = str(tmp_path / 'report-data')
    config['concurrent_builds'] = 1
    with mock.patch('common.new_process.execute') as mocked_execute:
        run_experiment.Dispatcher(config).start()
    return mocked_execute.call_args[0][0]


def test_dispatcher_start_hyperqueue(tmp_path, experiment_config):
    """Tests that a hyperqueue dispatcher gets the HyperQueue client and server
    directory, and the host's network to reach the server over."""
    experiment_config['executor'] = 'hyperqueue'
    experiment_config['hq_binary'] = '/mnt/hq/hq'
    experiment_config['hq_server_dir'] = '/mnt/hq/.hq-server'
    command = _get_dispatcher_command(experiment_config, tmp_path)

    assert '--network=host' in command
    assert '/mnt/hq/hq:/mnt/hq/hq:ro' in command
    assert '/mnt/hq/.hq-server:/mnt/hq/.hq-server:ro' in command
    assert 'HQ_BINARY=/mnt/hq/hq' in command
    assert 'HQ_SERVER_DIR=/mnt/hq/.hq-server' in command
    # Options must come before the image, or docker hands them to the image.
    image_index = command.index('localhost/fuzzbench/dispatcher-image')
    assert command.index('--network=host') < image_index


def test_dispatcher_start_local(tmp_path, experiment_config):
    """Tests that a local dispatcher doesn't get HyperQueue's arguments."""
    command = _get_dispatcher_command(experiment_config, tmp_path)

    assert '--network=host' not in command
    assert not any('HQ_' in arg for arg in command)


def test_dispatcher_start_creates_filestores(tmp_path, experiment_config):
    """Tests that both filestores exist before they are mounted, since podman
    won't create a missing mount source."""
    _get_dispatcher_command(experiment_config, tmp_path)
    assert os.path.isdir(experiment_config['experiment_filestore'])
    assert os.path.isdir(experiment_config['report_filestore'])
