"""Environment stub: ClusterFuzz env vars are unused in FuzzBench."""

import os


def get_value(_key, default_value=None):
    return default_value


def is_posix():
    return os.name == 'posix'


def is_android():
    return False


def get_suppressions_file(*_args, **_kwargs):
    return None
