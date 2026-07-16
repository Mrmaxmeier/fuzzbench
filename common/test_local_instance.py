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

import subprocess
from unittest import mock

from common import local_instance


@mock.patch('subprocess.Popen')
def test_run_local_instance_redirects_output(mocked_popen):
    """run_local_instance must not use an unread PIPE (deadlock risk)."""
    mocked_popen.return_value = mock.Mock()
    assert local_instance.run_local_instance('/tmp/startup.sh')
    mocked_popen.assert_called_once_with(
        ['/bin/bash', '/tmp/startup.sh'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@mock.patch('subprocess.Popen', side_effect=OSError('boom'))
def test_run_local_instance_returns_false_on_oserror(_mocked_popen):
    """run_local_instance returns False when the process cannot be started."""
    assert not local_instance.run_local_instance('/tmp/startup.sh')


def test_run_local_instance_requires_script():
    """run_local_instance returns False when no startup script is given."""
    assert not local_instance.run_local_instance(None)
