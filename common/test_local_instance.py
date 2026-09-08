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
"""Tests for local_instance.py."""

from unittest import mock

from common import local_instance
from common import new_process


def _result(retcode=0, timed_out=False, output=''):
    """Returns a ProcessResult like new_process.execute would."""
    return new_process.ProcessResult(retcode, output, timed_out)


@mock.patch('shutil.which', return_value='/usr/local/bin/bash')
@mock.patch('common.new_process.execute', return_value=_result())
def test_run_local_instance_runs_the_startup_script(mocked_execute,
                                                    _mocked_which):
    """A successful launch reports success and runs the script under bash."""
    assert local_instance.run_local_instance('/tmp/startup.sh')
    assert mocked_execute.call_args[0][0] == [
        '/usr/local/bin/bash', '/tmp/startup.sh'
    ]


@mock.patch('shutil.which', return_value=None)
@mock.patch('common.new_process.execute', return_value=_result())
def test_run_local_instance_falls_back_to_bin_bash(mocked_execute,
                                                   _mocked_which):
    """run_local_instance falls back to /bin/bash when bash is not on PATH."""
    assert local_instance.run_local_instance('/tmp/startup.sh')
    assert mocked_execute.call_args[0][0] == ['/bin/bash', '/tmp/startup.sh']


@mock.patch('common.new_process.execute',
            return_value=_result(retcode=125, output='no such image'))
def test_run_local_instance_reports_a_failed_launch(_mocked_execute):
    """A trial whose container did not start must not be reported as started:
    the caller would mark it started, and it would then hold its cpuset until
    it expired max_total_time later while producing no corpus at all."""
    assert not local_instance.run_local_instance('/tmp/startup.sh')


@mock.patch('common.new_process.execute',
            return_value=_result(retcode=-9, timed_out=True))
def test_run_local_instance_reports_a_hung_launch(_mocked_execute):
    """A startup script that never returns is a failed launch too."""
    assert not local_instance.run_local_instance('/tmp/startup.sh')


@mock.patch('common.new_process.execute', side_effect=OSError('boom'))
def test_run_local_instance_returns_false_on_oserror(_mocked_execute):
    """run_local_instance returns False when the process cannot be started."""
    assert not local_instance.run_local_instance('/tmp/startup.sh')


def test_run_local_instance_requires_script():
    """run_local_instance returns False when no startup script is given."""
    assert not local_instance.run_local_instance(None)
