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
"""Helper functions for interacting with the file storage."""

from common import local_filestore

GCS_GSUTIL_PREFIX = 'gs://'
GCS_HTTP_PREFIX = 'https://storage.googleapis.com/'


def is_gcs_filestore_path(filestore_path):
    """Returns True if |filestore_path| is a GCS URL. Assumes that GCS paths are
    using gs:// and not http."""
    return filestore_path.startswith(GCS_GSUTIL_PREFIX)


def get_user_facing_path(filestore_path):
    """Returns the most user accessible version of |filestore_path|.
    If |filestore_path| isn't a GCS URL, then it simply returns
    |filestore_path|. If it is a GCS URL, then the HTTPS version of
    |filestore_path| is returned."""
    if not is_gcs_filestore_path(filestore_path):
        return filestore_path
    return filestore_path.replace(GCS_GSUTIL_PREFIX, GCS_HTTP_PREFIX)


def cp(source, destination, recursive=False, expect_zero=True, parallel=False):  # pylint: disable=invalid-name,unused-argument
    """Copies |source| to |destination|. If |expect_zero| is True then it can
    raise subprocess.CalledProcessError."""
    return local_filestore.cp(source,
                              destination,
                              recursive=recursive,
                              expect_zero=expect_zero,
                              parallel=parallel)


def ls(path, must_exist=True):  # pylint: disable=invalid-name
    """Lists files or folders in |path| as one filename per line.
    If |must_exist| is True then it can raise subprocess.CalledProcessError."""
    return local_filestore.ls(path, must_exist=must_exist)


def rm(path, recursive=True, force=False, parallel=False):  # pylint: disable=invalid-name,unused-argument
    """Removes |path|."""
    return local_filestore.rm(path,
                              recursive=recursive,
                              force=force,
                              parallel=parallel)


def rsync(  # pylint: disable=too-many-arguments,unused-argument
        source,
        destination,
        delete=True,
        recursive=True,
        gsutil_options=None,
        options=None,
        parallel=False):
    """Syncs |source| and |destination| folders."""
    return local_filestore.rsync(source,
                                 destination,
                                 delete,
                                 recursive,
                                 gsutil_options,
                                 options,
                                 parallel=parallel)


def cat(file_path, expect_zero=True):
    """Reads the file at |file_path| and returns the result."""
    return local_filestore.cat(file_path, expect_zero=expect_zero)
