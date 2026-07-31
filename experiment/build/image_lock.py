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
"""The recipe hash -> image digest lock.

This is a lockfile in the dependency-manager sense. The recipe no longer has to
determine the content; it only has to decide whether a build is needed. The
image, not the recipe, is the source of truth for what an experiment measured.

Two builders that miss the same key concurrently will produce two different
digests, and if both were recorded the experiments using them would quietly
diverge. Writes are therefore first-writer-wins: the loser throws its build
away and uses the winner's digest. Each key lives in its own file so there is
no read-modify-write to race on, and the file is published with link(), which
stays atomic on the shared filesystems this has to work on.

Because the image is the source of truth and cannot be rebuilt faithfully
(upstream sources move, apt packages roll forward), image retention is data
retention. Nothing should garbage-collect images that a lock entry names.
"""

import datetime
import json
import os
import tempfile

from common import filesystem
from common import logs

logger = logs.Logger()  # pylint: disable=invalid-name

# Environment variable that overrides where the lock lives. Without it the lock
# sits beside the experiment directories rather than inside one, because its
# whole purpose is to be shared across experiments.
IMAGE_LOCK_DIR_VAR = 'IMAGE_LOCK_DIR'

LOCK_DIR_NAME = 'image-lock'


class StaleLockError(Exception):
    """Raised when the lock claims an image was built but it is not present in
    the local image store."""


class CorruptLockError(ValueError):
    """Raised when a lock entry cannot be read. Its own class so that callers
    which turn a build failure into a dropped benchmark can tell it apart from
    one and let it through."""


def get_lock_dir():
    """Returns the directory holding the lock entries."""
    override = os.getenv(IMAGE_LOCK_DIR_VAR)
    if override:
        return override

    experiment_filestore = os.getenv('EXPERIMENT_FILESTORE')
    if not experiment_filestore:
        raise ValueError(
            f'Cannot locate the image lock: set {IMAGE_LOCK_DIR_VAR} or '
            'EXPERIMENT_FILESTORE.')
    return os.path.join(experiment_filestore, LOCK_DIR_NAME)


def _entry_path(recipe_hash, lock_dir=None):
    """Returns the path of the lock entry for |recipe_hash|."""
    return os.path.join(lock_dir or get_lock_dir(), f'{recipe_hash}.json')


def get(recipe_hash, lock_dir=None):
    """Returns the lock entry for |recipe_hash|, or None if there is none."""
    path = _entry_path(recipe_hash, lock_dir)
    try:
        with open(path, encoding='utf-8') as file_handle:
            return json.load(file_handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        # A torn read should be impossible given link() publication, so this
        # means the entry was corrupted by something else. Treating it as
        # absent would silently rebuild and change identity, so be loud.
        raise CorruptLockError(f'Corrupt image lock entry: {path}') from error


def put(recipe_hash, digest, image_name, reference, lock_dir=None):
    """Records that |recipe_hash| built |digest| and returns the entry that
    actually won.

    If another builder recorded this key first, its entry is returned unchanged
    and the caller must use that digest instead of its own. The caller's build
    is not wasted work exactly -- it just does not get to define the identity.
    """
    lock_dir = lock_dir or get_lock_dir()
    filesystem.create_directory(lock_dir)

    entry = {
        'recipe_hash':
            recipe_hash,
        'digest':
            digest,
        'image_name':
            image_name,
        'reference':
            reference,
        'git_hash':
            os.getenv('GIT_HASH'),
        'time_created':
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    path = _entry_path(recipe_hash, lock_dir)
    # Write to a temporary file in the same directory, then publish with
    # link(). rename() would silently clobber a concurrent winner; link() fails
    # instead, which is exactly the signal we want.
    handle, temp_path = tempfile.mkstemp(dir=lock_dir, suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as file_handle:
            json.dump(entry, file_handle, sort_keys=True, indent=2)
        try:
            os.link(temp_path, path)
        except FileExistsError as error:
            winner = get(recipe_hash, lock_dir)
            if winner is None:
                # The entry vanished between link() and read, which means
                # something is deleting entries underneath us.
                raise StaleLockError(
                    f'Lock entry {recipe_hash} disappeared while being '
                    'written.') from error
            if winner['digest'] != digest:
                logger.warning(
                    'Lost the build race for %s (%s). Using the recorded '
                    'digest %s instead of the one just built, %s.', image_name,
                    recipe_hash, winner['digest'], digest)
            return winner
    finally:
        os.unlink(temp_path)

    return entry


def delete(recipe_hash, lock_dir=None):
    """Removes the entry for |recipe_hash|. Only for deliberately discarding a
    stale entry: any experiment that already recorded the old digest now refers
    to an image that no longer has a recipe pointing at it."""
    try:
        os.unlink(_entry_path(recipe_hash, lock_dir))
        return True
    except FileNotFoundError:
        return False
