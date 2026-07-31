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
"""Tests for recipe_hash.py."""

import os

import pytest

from common import benchmark_utils
from common import fuzzer_utils
from experiment.build import docker_images
from experiment.build import image_resolver
from experiment.build import recipe_hash

# pylint: disable=invalid-name,unused-argument,redefined-outer-name


@pytest.fixture(autouse=True)
def clear_context_cache():
    """The context hash is memoized for the life of the process, which is what
    a build run wants but not what a test wants."""
    recipe_hash.hash_context.cache_clear()
    yield
    recipe_hash.hash_context.cache_clear()


def write_context(directory, files):
    """Writes |files|, a path -> contents mapping, under |directory|."""
    for relpath, contents in files.items():
        path = os.path.join(directory, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as file_handle:
            file_handle.write(contents)
    return str(directory)


def test_context_hash_is_stable(tmp_path):
    """Tests that hashing the same tree twice gives the same answer."""
    context = write_context(tmp_path, {'a.txt': 'a', 'sub/b.txt': 'b'})
    first = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    assert recipe_hash.hash_context(context) == first


def test_context_hash_changes_with_contents(tmp_path):
    """Tests that editing a file in the context changes the hash."""
    context = write_context(tmp_path, {'a.txt': 'a'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    write_context(tmp_path, {'a.txt': 'changed'})
    assert recipe_hash.hash_context(context) != before


def test_context_hash_changes_when_file_added(tmp_path):
    """Tests that adding a file to the context changes the hash."""
    context = write_context(tmp_path, {'a.txt': 'a'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    write_context(tmp_path, {'sub/new.txt': 'new'})
    assert recipe_hash.hash_context(context) != before


def test_context_hash_changes_with_executable_bit(tmp_path):
    """Tests that chmod +x changes the hash, since it survives the build."""
    context = write_context(tmp_path, {'script.sh': '#!/bin/sh\n'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    os.chmod(os.path.join(context, 'script.sh'), 0o755)
    assert recipe_hash.hash_context(context) != before


def test_context_hash_ignores_dockerignored_files(tmp_path):
    """Tests that files docker would not send are not hashed."""
    context = write_context(tmp_path, {'.dockerignore': '*.pyc\nbuild\n'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    write_context(tmp_path, {
        'mod.pyc': 'compiled',
        'build/artifact.o': 'object',
    })
    assert recipe_hash.hash_context(context) == before


def test_context_hash_respects_dockerignore_exceptions(tmp_path):
    """Tests that a ! pattern re-includes an otherwise ignored file."""
    context = write_context(tmp_path, {'.dockerignore': 'data\n!data/keep\n'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    write_context(tmp_path, {'data/keep': 'kept'})
    assert recipe_hash.hash_context(context) != before


def test_unparseable_dockerignore_pattern_is_dropped(tmp_path):
    """Tests that a pattern we cannot compile leaves the file hashed rather
    than silently excluding it. Over-hashing is the safe direction."""
    context = write_context(tmp_path, {'.dockerignore': '[abc]*.txt\n'})
    before = recipe_hash.hash_context(context)
    recipe_hash.hash_context.cache_clear()
    write_context(tmp_path, {'a1.txt': 'content'})
    assert recipe_hash.hash_context(context) != before


def test_parse_dockerfile_parents_expands_args():
    """Tests that build args are substituted into FROM lines."""
    dockerfile = 'ARG parent_image\nFROM $parent_image\n'
    parents = recipe_hash.parse_dockerfile_parents(
        dockerfile, {'parent_image': 'registry/img:hash'})
    assert parents == ['registry/img:hash']


def test_parse_dockerfile_parents_uses_arg_defaults():
    """Tests that an ARG default is used when no build arg overrides it."""
    dockerfile = ('ARG base_image=localhost/fuzzbench/base-image\n'
                  'FROM $base_image\n')
    assert recipe_hash.parse_dockerfile_parents(
        dockerfile, {}) == ['localhost/fuzzbench/base-image']


def test_parse_dockerfile_parents_skips_internal_stages():
    """Tests that a FROM referring to an earlier stage is not a parent."""
    dockerfile = ('ARG parent_image\n'
                  'FROM $parent_image AS builder\n'
                  'FROM builder\n')
    parents = recipe_hash.parse_dockerfile_parents(dockerfile,
                                                   {'parent_image': 'img:tag'})
    assert parents == ['img:tag']


def test_parse_dockerfile_parents_reports_unexpandable():
    """Tests that an unresolvable reference comes back as None rather than
    being silently treated as an ordinary name."""
    assert recipe_hash.parse_dockerfile_parents('FROM $mystery\n', {}) == [None]


def test_parse_dockerfile_parents_arg_between_stages():
    """Tests the dispatcher-image shape: an ARG sitting between two FROMs,
    which BuildKit accepts even though the reference implies otherwise."""
    dockerfile = ('FROM external@sha256:abc AS base-clang\n'
                  'ARG base_image=localhost/fuzzbench/base-image\n'
                  'FROM $base_image\n')
    parents = recipe_hash.parse_dockerfile_parents(dockerfile, {})
    assert parents == ['external@sha256:abc', 'localhost/fuzzbench/base-image']


def test_check_parents_accepts_resolved_reference():
    """Tests that a parent we resolved to a digest passes."""
    dockerfile = 'ARG parent_image\nFROM $parent_image\n'
    recipe_hash.check_parents_are_hashed('img', dockerfile,
                                         {'parent_image': 'reg/img:hash'},
                                         {'reg/img:hash'})


def test_check_parents_accepts_digest_pinned_external():
    """Tests that an externally pinned base passes without being resolved."""
    recipe_hash.check_parents_are_hashed('img', 'FROM ubuntu@sha256:abcdef\n',
                                         {}, set())


def test_check_parents_rejects_mutable_external_tag():
    """Tests the under-hashing case: a base named by a moving tag. This is what
    caught base-image's FROM ubuntu:focal."""
    with pytest.raises(recipe_hash.UnhashedParentError):
        recipe_hash.check_parents_are_hashed('img', 'FROM ubuntu:focal\n', {},
                                             set())


def test_check_parents_rejects_unresolved_fuzzbench_image():
    """Tests the other under-hashing case: a parent assembled from a name
    inside the Dockerfile rather than passed in. This is what caught
    benchmark-runner's FROM $registry/builders/$fuzzer/$benchmark."""
    dockerfile = ('ARG registry\nARG fuzzer\nARG benchmark\n'
                  'FROM $registry/builders/$fuzzer/$benchmark\n')
    with pytest.raises(recipe_hash.UnhashedParentError):
        recipe_hash.check_parents_are_hashed('img', dockerfile, {
            'registry': 'localhost/fuzzbench',
            'fuzzer': 'afl',
            'benchmark': 'zlib'
        }, set())


def test_recipe_hash_changes_with_parent_digest(tmp_path):
    """Tests the merkle property: the same recipe against a rebuilt parent
    gets a different key, even though the parent's name never moved."""
    context = write_context(tmp_path, {'file': 'contents'})
    dockerfile = os.path.join(context, 'Dockerfile')
    with open(dockerfile, 'w', encoding='utf-8') as file_handle:
        file_handle.write('ARG parent_image\nFROM $parent_image\n')
    image = {'dockerfile': dockerfile, 'context': context, 'tag': 'img'}

    first = recipe_hash.compute(image, {'parent_image': 'sha256:aaa'})
    recipe_hash.hash_context.cache_clear()
    second = recipe_hash.compute(image, {'parent_image': 'sha256:bbb'})
    assert first != second


def test_every_image_in_the_graph_hashes_its_parents():
    """Tests that no Dockerfile in the repo builds FROM something whose
    identity escapes the recipe hash.

    This is the regression guard for the whole scheme. A Dockerfile that names
    a parent by a mutable tag would keep the same recipe hash while the image
    underneath it changed, which is the one failure mode that produces a
    silently stale experiment.
    """
    fuzzers = fuzzer_utils.get_fuzzer_names()
    benchmarks = benchmark_utils.get_all_benchmarks()
    images = docker_images.get_images_to_build(fuzzers, benchmarks)

    for name, image in images.items():
        build_args = image_resolver.parse_build_args(image)
        dependency_references = image_resolver.dependency_references(
            name, image, images)
        # Stand in for resolution: every dependency gets an immutable tag.
        docker_args = dict(build_args)
        parent_references = set()
        for arg_name, value in build_args.items():
            if value in dependency_references:
                reference = f'{value}:recipehash'
                docker_args[arg_name] = reference
                parent_references.add(reference)

        with open(recipe_hash.repo_path(image['dockerfile']),
                  encoding='utf-8') as file_handle:
            dockerfile_text = file_handle.read()

        recipe_hash.check_parents_are_hashed(name, dockerfile_text, docker_args,
                                             parent_references)
