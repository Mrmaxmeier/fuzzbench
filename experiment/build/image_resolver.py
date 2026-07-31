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
"""Resolves the image graph to digests, building only what is missing.

Walks the dependency graph bottom-up. For each image it computes a recipe hash
over the recipe *and its parents' digests*, looks that hash up in the lock, and
builds only on a miss. What comes back is an identity -- a digest -- rather
than a name that whatever built last happens to be sitting on.

Two references are in play and they are not interchangeable:

  - Parents are passed to `docker build` as an immutable tag,
    {registry}/{tag}:{recipe_hash}. BuildKit resolves a bare digest in FROM as
    a repository name and fails, so a tag is the only option at build time.
    The tag is immutable by construction: one recipe hash, one build.
  - Identity is the config digest. That is what gets recorded, compared and
    handed to the runner, and it is what survives `docker save`/`load` when
    images later move to compute nodes as archives.
"""

import argparse
import dataclasses
import os
import subprocess
import sys
import threading

from common import experiment_utils
from common import logs
from common import new_process
from common import utils
from experiment.build import docker_images
from experiment.build import image_lock
from experiment.build import recipe_hash

logger = logs.Logger()  # pylint: disable=invalid-name

# What resolving an image would do, as reported by Resolver.plan.
CACHED = 'cached'  # The lock has it and the image is present.
BUILD = 'build'  # No lock entry: it would be built and recorded.
STALE = 'stale'  # The lock has it but the image is gone. See StaleLockError.
UNKNOWN = 'unknown'  # A parent must be built before this one has a key.


@dataclasses.dataclass
class PlannedImage:
    """What resolving an image would do."""

    name: str
    recipe_hash: str
    digest: str
    status: str


@dataclasses.dataclass(repr=False)
class ResolvedImage:
    """An image that has been resolved to a digest."""

    name: str
    image: dict
    recipe_hash: str
    digest: str
    # The immutable {registry}/{tag}:{recipe_hash} reference.
    reference: str
    was_built: bool

    def __repr__(self):
        state = 'built' if self.was_built else 'cached'
        return f'<{self.name} {self.recipe_hash} {self.digest[:19]} {state}>'


def _image_reference(image, tag=None):
    """Returns {registry}/{tag_path} for |image|, optionally tagged."""
    reference = os.path.join(experiment_utils.get_docker_registry(),
                             image['tag'])
    return f'{reference}:{tag}' if tag else reference


def parse_build_args(image):
    """Returns |image|'s build args as an ordered name -> value mapping."""
    build_args = {}
    for arg in image.get('build_arg', []):
        name, _, value = arg.partition('=')
        build_args[name] = value
    return build_args


def dependency_references(name, image, images):
    """Returns a mapping from each dependency's mutable reference to its image
    name, used to spot which build args are parent references."""
    references = {}
    for dependency in image.get('depends_on', []):
        if dependency not in images:
            raise ValueError(f'{name} depends on unknown image {dependency}.')
        reference = _image_reference(images[dependency])
        if reference in references:
            raise ValueError(
                f'{name} has two dependencies sharing the reference '
                f'{reference}: {references[reference]} and {dependency}. The '
                'parent cannot be identified unambiguously.')
        references[reference] = dependency
    return references


class Resolver:
    """Resolves images to digests, building on a lock miss.

    Safe to call from several threads: the dispatcher builds fuzzer-benchmark
    pairs through a thread pool, and those pairs share parents. Without
    per-image locking the same parent would be built several times over, and
    each build would race to define the identity of the next layer up.
    """

    def __init__(self, images, lock_dir=None, allow_stale_lock=False):
        self.images = images
        self.lock_dir = lock_dir
        # When set, a lock entry naming an image the local daemon does not have
        # is dropped and rebuilt instead of raising. This changes the identity
        # of everything downstream, so it is opt-in.
        self.allow_stale_lock = allow_stale_lock
        self._resolved = {}
        self._resolved_lock = threading.Lock()
        self._name_locks = {}
        self._name_locks_lock = threading.Lock()

    def resolved_images(self):
        """Returns the images resolved so far, keyed by image name."""
        with self._resolved_lock:
            return dict(self._resolved)

    def _lock_for(self, name):
        """Returns the lock guarding resolution of |name|."""
        with self._name_locks_lock:
            if name not in self._name_locks:
                self._name_locks[name] = threading.Lock()
            return self._name_locks[name]

    def resolve(self, name):
        """Resolves |name|, and everything it depends on, to a digest."""
        with self._resolved_lock:
            if name in self._resolved:
                return self._resolved[name]

        with self._lock_for(name):
            # Another thread may have finished while we waited.
            with self._resolved_lock:
                if name in self._resolved:
                    return self._resolved[name]

            resolved = self._resolve_uncached(name)

            with self._resolved_lock:
                self._resolved[name] = resolved
            return resolved

    def _resolve_parents(self, name, image):
        """Resolves the parents |image| consumes and substitutes them into its
        build args twice over: the digest into what gets hashed, the immutable
        tag into what gets built.

        Only parents that appear in a build arg are resolved. A dependency the
        Dockerfile never references cannot affect the result, and folding it in
        would break the collapse that makes a fuzzer's intermediate runner one
        image rather than one per benchmark. Anything the Dockerfile does
        reference is caught by check_parents_are_hashed.
        """
        build_args = parse_build_args(image)
        parent_names = dependency_references(name, image, self.images)

        hashed_args = dict(build_args)
        docker_args = dict(build_args)
        parent_references = set()
        for arg_name, value in build_args.items():
            dependency = parent_names.get(value)
            if dependency is None:
                continue
            parent = self.resolve(dependency)
            hashed_args[arg_name] = parent.digest
            docker_args[arg_name] = parent.reference
            parent_references.add(parent.reference)

        return hashed_args, docker_args, parent_references

    def _resolve_uncached(self, name):
        """Resolves |name| assuming it is not already in the memo."""
        if name not in self.images:
            raise ValueError(f'Unknown image {name}.')
        image = self.images[name]

        hashed_args, docker_args, parent_references = self._resolve_parents(
            name, image)

        dockerfile = recipe_hash.repo_path(image['dockerfile'])
        with open(dockerfile, encoding='utf-8') as file_handle:
            dockerfile_text = file_handle.read()
        # Fails loudly if the Dockerfile builds FROM something whose identity
        # did not enter the hash. Under-hashing is the direction that produces
        # silently stale images, so it is never tolerated.
        recipe_hash.check_parents_are_hashed(name, dockerfile_text, docker_args,
                                             parent_references)

        recipe = recipe_hash.compute(image, hashed_args)
        reference = _image_reference(image, recipe)

        entry = image_lock.get(recipe, self.lock_dir)
        if entry is not None:
            digest = self._use_locked_entry(name, entry, reference)
            if digest is not None:
                return ResolvedImage(name,
                                     image,
                                     recipe,
                                     digest,
                                     reference,
                                     was_built=False)

        digest = self._build(name, image, recipe, reference, docker_args)
        entry = image_lock.put(recipe, digest, name, reference, self.lock_dir)
        if entry['digest'] != digest:
            # Lost the race. The winner's digest is the identity everything
            # downstream must use, so point our recipe-hash tag at it.
            digest = entry['digest']
            self._retag(name, digest, reference)

        return ResolvedImage(name,
                             image,
                             recipe,
                             digest,
                             reference,
                             was_built=True)

    def _use_locked_entry(self, name, entry, reference):
        """Returns the digest recorded for a lock hit, or None if the entry is
        stale and the caller should rebuild."""
        digest = entry['digest']
        if _image_exists(digest):
            # The recipe-hash tag may be missing even when the image is
            # present, for instance after resolving on a different machine.
            self._retag(name, digest, reference)
            return digest

        message = (
            f'The image lock says {name} ({entry["recipe_hash"]}) built '
            f'{digest}, but that image is not in the local store. The image is '
            'the source of truth for what past experiments measured and it '
            'cannot be faithfully rebuilt, so this is not silently rebuilt.')
        if not self.allow_stale_lock:
            raise image_lock.StaleLockError(
                message + ' Restore the image, or pass allow_stale_lock to '
                'drop the entry and build a new identity.')

        logger.warning('%s Dropping the entry and rebuilding.', message)
        image_lock.delete(entry['recipe_hash'], self.lock_dir)
        return None

    def _build(self, name, image, recipe, reference, docker_args):
        """Builds |image| and returns the digest of the result."""
        logger.info('Building %s (%s).', name, recipe)
        command = ['docker', 'build', '--tag', reference]
        for arg_name, value in sorted(docker_args.items()):
            command += ['--build-arg', f'{arg_name}={value}']
        command += ['--file', image['dockerfile'], image['context']]
        new_process.execute(command, cwd=utils.ROOT_DIR)

        digest = _image_digest(reference)
        # Keep the mutable name pointing at the newest build so that the
        # interactive `make run-*` and `make debug-*` targets keep working.
        # Nothing on the experiment path may read it.
        self._retag(name, digest, _image_reference(image))
        return digest

    def _retag(self, name, digest, reference):
        """Points |reference| at |digest|."""
        try:
            new_process.execute(['docker', 'tag', digest, reference],
                                cwd=utils.ROOT_DIR)
        except subprocess.CalledProcessError:
            logger.error('Failed to tag %s (%s) as %s.', name, digest,
                         reference)
            raise

    def plan(self, name, planned=None):
        """Reports what resolving |name| would do, without building anything.

        A plan can only see so far. A recipe hash covers its parents' digests,
        so an image whose parent has never been built has no key to look up
        yet: it is reported as UNKNOWN rather than guessed at. Everything above
        an image that needs building is therefore unknowable until it is built.
        """
        planned = {} if planned is None else planned
        if name in planned:
            return planned[name]

        image = self.images[name]
        parent_names = dependency_references(name, image, self.images)
        build_args = parse_build_args(image)

        # Plan every parent before deciding, rather than stopping at the first
        # unknown one. Short-circuiting would leave the other parents out of
        # the report entirely, which is the opposite of what a dry run is for.
        hashed_args = dict(build_args)
        unknown_parent = False
        for arg_name, value in build_args.items():
            dependency = parent_names.get(value)
            if dependency is None:
                continue
            parent = self.plan(dependency, planned)
            if parent.digest is None:
                unknown_parent = True
            else:
                hashed_args[arg_name] = parent.digest

        if unknown_parent:
            planned[name] = PlannedImage(name, None, None, UNKNOWN)
            return planned[name]

        recipe = recipe_hash.compute(image, hashed_args)
        entry = image_lock.get(recipe, self.lock_dir)
        if entry is None:
            planned[name] = PlannedImage(name, recipe, None, BUILD)
        elif not _image_exists(entry['digest']):
            planned[name] = PlannedImage(name, recipe, entry['digest'], STALE)
        else:
            planned[name] = PlannedImage(name, recipe, entry['digest'], CACHED)
        return planned[name]


def _image_exists(reference):
    """Returns True if |reference| is present in the local image store."""
    result = new_process.execute(
        ['docker', 'image', 'inspect', '--format', '{{.Id}}', reference],
        expect_zero=False,
        cwd=utils.ROOT_DIR)
    return result.retcode == 0


def _image_digest(reference):
    """Returns the config digest of |reference|."""
    result = new_process.execute(
        ['docker', 'image', 'inspect', '--format', '{{.Id}}', reference],
        cwd=utils.ROOT_DIR)
    return result.output.strip()


def main():
    """Resolves images for fuzzer-benchmark pairs, or reports what it would
    do."""
    parser = argparse.ArgumentParser(description=(
        'Resolve FuzzBench images to digests, building what is missing.'))
    parser.add_argument('-f', '--fuzzers', nargs='+', required=True)
    parser.add_argument('-b', '--benchmarks', nargs='+', required=True)
    parser.add_argument(
        '-n',
        '--dry-run',
        action='store_true',
        help='Report what would be built without building anything.')
    parser.add_argument(
        '--allow-stale-lock',
        action='store_true',
        help=('Drop lock entries whose images are missing and build new ones. '
              'This gives those images a new identity, so results recorded '
              'against the old digests no longer refer to anything buildable.'))
    args = parser.parse_args()

    logs.initialize()
    images = docker_images.get_images_to_build(args.fuzzers, args.benchmarks)
    resolver = Resolver(images, allow_stale_lock=args.allow_stale_lock)

    targets = [
        f'{fuzzer}-{benchmark}-runner' for fuzzer in args.fuzzers
        for benchmark in args.benchmarks
    ]

    if args.dry_run:
        planned = {}
        for target in targets:
            resolver.plan(target, planned)
        for name, entry in sorted(planned.items()):
            digest = entry.digest[:19] if entry.digest else '-'
            print(
                f'{entry.status:8s} {name:60s} {entry.recipe_hash or "-":32s} '
                f'{digest}')
        return 1 if any(
            entry.status == STALE for entry in planned.values()) else 0

    for target in targets:
        print(repr(resolver.resolve(target)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
