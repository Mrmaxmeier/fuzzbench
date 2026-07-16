"""No-op logging for vendored stacktraces."""


def log_error(*_args, **_kwargs):
    return None


def log(*_args, **_kwargs):
    return None
