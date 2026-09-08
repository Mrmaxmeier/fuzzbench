"""Local experiment instance helpers."""

import shutil
from typing import Optional

from common import logs
from common import new_process

# The startup script only has to get the container running; it backgrounds the
# log streaming and returns. A launch that has not finished in this long is not
# going to.
STARTUP_TIMEOUT_SECONDS = 10 * 60


def _get_bash() -> str:
    """Returns the path to bash. The startup script is bash, and this runs on
    the host, where bash is not necessarily at /bin/bash (NixOS ships only
    /bin/sh)."""
    return shutil.which('bash') or '/bin/bash'


def run_local_instance(startup_script: Optional[str] = None) -> bool:
    """Does the equivalent of "create_instance" for local experiments. Runs
    |startup_script|, which starts the trial's container and returns, and
    reports whether it succeeded.

    The result matters: the caller marks the trial as started on a True, and a
    trial marked started that is not running holds its cpuset allocation until
    it expires max_total_time later, blocking a real trial from taking the
    slot, and feeds the measurer cycles whose corpus will never arrive. This
    used to Popen the script and return True unconditionally, with both output
    streams sent to /dev/null, so a missing image or a name collision was
    indistinguishable from a healthy launch.
    """
    if not startup_script:
        return False
    command = [_get_bash(), startup_script]
    try:
        result = new_process.execute(command,
                                     expect_zero=False,
                                     timeout=STARTUP_TIMEOUT_SECONDS)
    except OSError:
        logs.error('Failed to start local instance: %s', startup_script)
        return False

    if result.timed_out:
        logs.error('Timed out starting local instance: %s', startup_script)
        return False
    if result.retcode != 0:
        logs.error('Failed to start local instance: %s returned %d. Output: %s',
                   startup_script, result.retcode, result.output)
        return False
    return True
