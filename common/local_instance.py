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
"""Local experiment instance helpers."""

import shutil
import subprocess
from typing import Optional

from common import logs


def _get_bash() -> str:
    """Returns the path to bash. The startup script is bash, and this runs on
    the host, where bash is not necessarily at /bin/bash (NixOS ships only
    /bin/sh)."""
    return shutil.which('bash') or '/bin/bash'


def run_local_instance(startup_script: Optional[str] = None) -> bool:
    """Does the equivalent of "create_instance" for local experiments, runs
    |startup_script| in the background."""
    if not startup_script:
        return False
    command = [_get_bash(), startup_script]
    try:
        # pylint: disable=consider-using-with
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logs.error('Failed to start local instance: %s', startup_script)
        return False
    return True
