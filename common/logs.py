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
"""Set up for logging."""
from enum import Enum

import logging
import os
import sys
import traceback

# Disable this check since we have a bunch of non-constant globals in this file.
# pylint: disable=invalid-name

_default_extras = {}

LOG_LENGTH_LIMIT = 250 * 1000


def initialize(name='fuzzbench', default_extras=None, log_level=logging.INFO):
    """Initializes stdlib logging."""
    del name  # Kept for API compatibility.
    logging.getLogger().setLevel(log_level)
    logging.getLogger().addFilter(LengthFilter())

    # Don't log so much with SQLalchemy to avoid stressing the logging library.
    # See crbug.com/1044343.
    logging.getLogger('sqlalchemy').setLevel(logging.ERROR)

    default_extras = {} if default_extras is None else default_extras

    _set_instance_name(default_extras)
    _set_experiment(default_extras)

    # pylint: disable=global-variable-not-assigned
    global _default_extras
    _default_extras.update(default_extras)


def _set_instance_name(extras: dict):
    """Set instance_name in |extras| if it is provided by the environment and
    not already set."""
    if 'instance_name' in extras:
        return

    instance_name = os.getenv('INSTANCE_NAME')
    if instance_name is None:
        return

    extras['instance_name'] = instance_name


def _set_experiment(extras: dict):
    """Set experiment in |extras| if it is provided by the environment and
    not already set."""
    if 'experiment' in extras:
        return

    experiment = os.getenv('EXPERIMENT')
    if experiment is None:
        return

    extras['experiment'] = experiment


class Logger:
    """Wrapper around logging.Logger for fuzzbench callers."""

    _LOGGER_NAME = 'fuzzbench'

    def __init__(self, default_extras=None, log_level=logging.INFO):
        self.logger = logging.getLogger(self._LOGGER_NAME)

        logging.getLogger(self._LOGGER_NAME).setLevel(log_level)
        logging.getLogger(self._LOGGER_NAME).addFilter(LengthFilter())
        self.default_extras = default_extras if default_extras else {}

    def error(self, *args, **kwargs):
        """Wrapper that uses _log_function_wrapper to call error."""
        self._log_function_wrapper(error, *args, **kwargs)

    def warning(self, *args, **kwargs):
        """Wrapper that uses _log_function_wrapper to call warning."""
        self._log_function_wrapper(warning, *args, **kwargs)

    def info(self, *args, **kwargs):
        """Wrapper that uses _log_function_wrapper to call info."""
        self._log_function_wrapper(info, *args, **kwargs)

    def debug(self, *args, **kwargs):
        """Wrapper that uses _log_function_wrapper to call debug."""
        self._log_function_wrapper(debug, *args, **kwargs)

    def _log_function_wrapper(self, log_function, message, *args, extras=None):
        """Wrapper around log functions that passes extras and the logger this
        object wraps (self.logger)."""
        extras = {} if extras is None else extras
        extras = extras.copy()
        extras.update(self.default_extras)
        log_function(message, *args, extras=extras, logger=self.logger)


class LogSeverity(Enum):
    """Enum for different levels of log severity."""
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    INFO = logging.INFO
    DEBUG = logging.DEBUG


def log(logger, severity, message, *args, extras=None):
    """Log a message with severity |severity|."""
    del logger  # Kept for API compatibility.
    message = str(message)
    if args:
        message = message % args

    all_extras = _default_extras.copy()
    extras = extras or {}
    all_extras.update(extras)
    if all_extras:
        message += ' Extras: ' + str(all_extras)
    logging.log(severity, message)


def error(message, *args, extras=None, logger=None):
    """Logs |message| with severity ERROR (including exception if there was
    one)."""
    if any(sys.exc_info()):
        extras = {} if extras is None else extras
        extras['traceback'] = traceback.format_exc()
    log(logger, logging.ERROR, message, *args, extras=extras)


def warning(message, *args, extras=None, logger=None):
    """Log a message with severity 'WARNING'."""
    log(logger, logging.WARNING, message, *args, extras=extras)


def info(message, *args, extras=None, logger=None):
    """Log a message with severity 'INFO'."""
    log(logger, logging.INFO, message, *args, extras=extras)


def debug(message, *args, extras=None, logger=None):
    """Log a message with severity 'DEBUG'."""
    log(logger, logging.DEBUG, message, *args, extras=extras)


class LengthFilter(logging.Filter):
    """Filter for truncating log messages that are too long."""

    def filter(self, record):
        if len(record.msg) > LOG_LENGTH_LIMIT:
            record.msg = ('TRUNCATED: ' + record.msg)[:LOG_LENGTH_LIMIT]
        return True
