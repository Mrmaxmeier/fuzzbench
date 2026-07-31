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
"""Common utilities."""

import hashlib
import os

ROOT_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def string_hash(obj):
    """Returns a SHA-1 hash of the object. Not used for security purposes."""
    return hashlib.sha1(str(obj).encode('utf-8')).hexdigest()


def file_hash(file_path):
    """Returns the SHA-1 hash of |file_path| contents."""
    chunk_size = 51200  # Read in 50 KB chunks.
    digest = hashlib.sha1()
    with open(file_path, 'rb') as file_handle:
        chunk = file_handle.read(chunk_size)
        while chunk:
            digest.update(chunk)
            chunk = file_handle.read(chunk_size)

    return digest.hexdigest()


# Calculate a retry delay based on the current try,
# a default delay, and an exponential backoff
def get_retry_delay(num_try, delay, backoff):
    """Compute backoff delay."""
    return delay * (backoff**(num_try - 1))
