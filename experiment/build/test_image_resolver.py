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
"""Tests for image_resolver.py."""

import hashlib
import os
import threading
from unittest import mock

import pytest

from common import new_process
from experiment.build import image_lock
from experiment.build import image_resolver
from experiment.build import recipe_hash

# pylint: disable=invalid-name,unused-argument,redefined-outer-name,protected-access


class FakeDocker:
    """Stands in for the docker CLI, tracking a local image store.

    Builds are deterministic in the recipe's inputs, mirroring the real
    behaviour that an unchanged recipe with a warm layer cache lands on the
    same digest.
    """

    def __init__(self):
        self.store = {}  # reference or digest -> digest
        self.builds = []
        self.lock = threading.Lock()
        # Set to make the next build produce a digest nobody can predict, as
        # two racing builders of the same recipe would.
        self.build_salt = ''

    def execute(self, command, *args, **kwargs):
        """Interprets a docker command."""
        assert command[0] == 'docker'
        if command[1] == 'build':
            return self._build(command)
        if command[1:3] == ['image', 'inspect']:
            return self._inspect(command[-1])
        if command[1] == 'tag':
            return self._tag(command[2], command[3])
        raise AssertionError(f'unexpected command {command}')

    def _build(self, command):
        reference = command[command.index('--tag') + 1]
        build_args = tuple(command[index + 1]
                           for index, item in enumerate(command)
                           if item == '--build-arg')
        with self.lock:
            self.builds.append((reference, build_args))
            payload = (reference + self.build_salt + repr(build_args)).encode()
            digest = 'sha256:' + hashlib.sha256(payload).hexdigest()
            self.store[reference] = digest
            self.store[digest] = digest
        return new_process.ProcessResult(0, '', False)

    def _inspect(self, reference):
        with self.lock:
            digest = self.store.get(reference)
        if digest is None:
            return new_process.ProcessResult(1, 'No such image', False)
        return new_process.ProcessResult(0, digest + '\n', False)

    def _tag(self, digest, reference):
        with self.lock:
            self.store[reference] = self.store.get(digest, digest)
        return new_process.ProcessResult(0, '', False)

    def forget(self, digest):
        """Drops an image from the store, as pruning the daemon would."""
        with self.lock:
            for key, value in list(self.store.items()):
                if digest in (key, value):
                    del self.store[key]


@pytest.fixture(autouse=True)
def clear_context_cache():
    """Keeps memoized context hashes from leaking between tests."""
    recipe_hash.hash_context.cache_clear()
    yield
    recipe_hash.hash_context.cache_clear()


@pytest.fixture
def docker():
    """Patches out the docker CLI."""
    fake = FakeDocker()
    with mock.patch.object(image_resolver.new_process, 'execute', fake.execute):
        yield fake


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """Builds a small parent -> child image graph on disk."""
    monkeypatch.setattr(recipe_hash, 'ROOT_DIR', str(tmp_path))

    for name, dockerfile in (
        ('base', 'FROM ubuntu@sha256:abc\n'),
        ('child', 'ARG parent_image\nFROM $parent_image\n'),
    ):
        directory = tmp_path / name
        directory.mkdir()
        (directory / 'Dockerfile').write_text(dockerfile, encoding='utf-8')

    return {
        'base': {
            'dockerfile': 'base/Dockerfile',
            'context': 'base',
            'tag': 'base-image',
        },
        'child': {
            'dockerfile': 'child/Dockerfile',
            'context': 'child',
            'tag': 'children/child',
            'build_arg': ['parent_image=localhost/fuzzbench/base-image'],
            'depends_on': ['base'],
        },
    }


@pytest.fixture
def lock_dir(tmp_path):
    """Returns an empty lock directory."""
    directory = tmp_path / 'image-lock'
    directory.mkdir()
    return str(directory)


def test_resolve_builds_on_miss(graph, docker, lock_dir):
    """Tests that an unseen recipe is built and recorded."""
    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    resolved = resolver.resolve('child')

    assert resolved.was_built
    assert resolved.digest.startswith('sha256:')
    assert image_lock.get(resolved.recipe_hash,
                          lock_dir)['digest'] == resolved.digest


def test_resolve_reuses_lock_on_hit(graph, docker, lock_dir):
    """Tests that a second resolver builds nothing."""
    first = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')
    docker.builds.clear()

    second = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')

    assert docker.builds == []
    assert not second.was_built
    assert second.digest == first.digest


def test_parent_digest_enters_the_childs_recipe_hash(graph, docker, lock_dir):
    """Tests the merkle property end to end: rebuilding the parent into
    different content re-keys the child, even though the parent's name and the
    child's own recipe are untouched."""
    before = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')

    # Make the parent build to something new, as an unpinned upstream would.
    docker.build_salt = 'different-upstream'
    for entry in os.listdir(lock_dir):
        os.unlink(os.path.join(lock_dir, entry))

    after = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')
    assert after.recipe_hash != before.recipe_hash


def test_child_is_built_against_the_immutable_parent_reference(
        graph, docker, lock_dir):
    """Tests that the parent is passed by its recipe-hash tag rather than by a
    mutable name, so the build cannot pick up a different parent."""
    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    parent = resolver.resolve('base')
    resolver.resolve('child')

    assert parent.reference.endswith(f':{parent.recipe_hash}')
    child_builds = [
        build_args for reference, build_args in docker.builds
        if 'children/child' in reference
    ]
    assert child_builds == [(f'parent_image={parent.reference}',)]


def test_stale_lock_raises_rather_than_rebuilding(graph, docker, lock_dir):
    """Tests that a lock entry naming an image we no longer hold is an error.

    Rebuilding would produce a different digest and silently contradict the
    entry that past experiments recorded, which is the failure this whole
    scheme exists to prevent.
    """
    resolved = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('base')
    docker.forget(resolved.digest)

    with pytest.raises(image_lock.StaleLockError):
        image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('base')


def test_stale_lock_can_be_dropped_explicitly(graph, docker, lock_dir):
    """Tests the opt-in escape hatch for a stale entry."""
    resolved = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('base')
    docker.forget(resolved.digest)

    resolver = image_resolver.Resolver(graph,
                                       lock_dir=lock_dir,
                                       allow_stale_lock=True)
    assert resolver.resolve('base').was_built


def test_shared_parent_is_built_once_under_concurrency(graph, docker, lock_dir):
    """Tests that threads racing on a shared parent build it a single time.

    The dispatcher resolves fuzzer-benchmark pairs through a thread pool and
    those pairs share parents, so without per-image locking the same parent
    would be built repeatedly and each build would race to define the identity
    of everything above it.
    """
    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    barrier = threading.Barrier(8)
    digests = []
    digests_lock = threading.Lock()

    def resolve():
        barrier.wait()
        resolved = resolver.resolve('child')
        with digests_lock:
            digests.append(resolved.digest)

    threads = [threading.Thread(target=resolve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(digests)) == 1
    assert len(docker.builds) == 2, 'expected exactly one build per image'


def test_plan_reports_what_would_be_built(graph, docker, lock_dir):
    """Tests that a dry run reports a build without performing one."""
    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    planned = {}
    resolver.plan('child', planned)

    assert docker.builds == [], 'a dry run must not build'
    assert planned['base'].status == image_resolver.BUILD
    assert planned['base'].recipe_hash


def test_plan_cannot_see_past_an_unbuilt_parent(graph, docker, lock_dir):
    """Tests that an image whose parent has never been built is reported as
    unknown rather than guessed at.

    Its recipe hash covers the parent's digest, and that digest does not exist
    until the parent is built, so there is genuinely no key to look up.
    """
    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    planned = {}
    resolver.plan('child', planned)

    assert planned['child'].status == image_resolver.UNKNOWN
    assert planned['child'].recipe_hash is None


def test_plan_reports_cached_images(graph, docker, lock_dir):
    """Tests that a dry run after a build reports everything as cached."""
    image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')
    docker.builds.clear()

    planned = {}
    image_resolver.Resolver(graph, lock_dir=lock_dir).plan('child', planned)

    assert docker.builds == []
    assert {entry.status for entry in planned.values()
           } == {image_resolver.CACHED}


def test_plan_reports_a_stale_lock_entry(graph, docker, lock_dir):
    """Tests that a dry run surfaces a missing image before a build run hits
    it as an error."""
    resolved = image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('base')
    docker.forget(resolved.digest)

    planned = {}
    image_resolver.Resolver(graph, lock_dir=lock_dir).plan('base', planned)
    assert planned['base'].status == image_resolver.STALE


def test_plan_covers_every_parent(graph, docker, lock_dir, tmp_path):
    """Tests that one unresolvable parent does not hide the others from the
    report. A dry run exists to say what will happen, so stopping at the first
    unknown would defeat it."""
    # Give the child a second parent that is already built and cached. Its
    # recipe has to differ from base's, or the two would legitimately share a
    # recipe hash and therefore a lock entry.
    (tmp_path / 'other').mkdir()
    (tmp_path / 'other' / 'Dockerfile').write_text(
        'FROM ubuntu@sha256:abc\nRUN echo other\n', encoding='utf-8')
    graph['other'] = {
        'dockerfile': 'other/Dockerfile',
        'context': 'other',
        'tag': 'other-image',
    }
    graph['child']['build_arg'].append(
        'other_image=localhost/fuzzbench/other-image')
    graph['child']['depends_on'].append('other')
    image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('other')

    planned = {}
    image_resolver.Resolver(graph, lock_dir=lock_dir).plan('child', planned)

    assert planned['child'].status == image_resolver.UNKNOWN
    assert planned['base'].status == image_resolver.BUILD
    assert planned['other'].status == image_resolver.CACHED


def test_identical_recipes_share_one_build(graph, docker, lock_dir, tmp_path):
    """Tests that two image names with the same recipe are built once.

    This is not a curiosity: every fuzzer's intermediate runner is
    byte-identical across all 29 benchmarks, because its Dockerfile takes only
    base_image and its context is the fuzzer directory. Addressing by content
    collapses those into a single build per fuzzer.
    """
    (tmp_path / 'twin').mkdir()
    (tmp_path / 'twin' / 'Dockerfile').write_text('FROM ubuntu@sha256:abc\n',
                                                  encoding='utf-8')
    graph['twin'] = {
        'dockerfile': 'twin/Dockerfile',
        'context': 'twin',
        'tag': 'twin-image',
    }

    resolver = image_resolver.Resolver(graph, lock_dir=lock_dir)
    base = resolver.resolve('base')
    twin = resolver.resolve('twin')

    assert base.recipe_hash == twin.recipe_hash
    assert base.digest == twin.digest
    assert len(docker.builds) == 1, 'the second name should not rebuild'


def test_unknown_dependency_is_rejected(graph, docker, lock_dir):
    """Tests that a typo in depends_on does not silently become a non-parent."""
    graph['child']['depends_on'] = ['nonexistent']
    with pytest.raises(ValueError):
        image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')


def test_unhashed_parent_is_rejected(graph, docker, lock_dir, tmp_path):
    """Tests that a Dockerfile naming a parent we did not resolve fails."""
    (tmp_path / 'child' / 'Dockerfile').write_text(
        'FROM localhost/fuzzbench/base-image\n', encoding='utf-8')
    with pytest.raises(recipe_hash.UnhashedParentError):
        image_resolver.Resolver(graph, lock_dir=lock_dir).resolve('child')
