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
"""Recipe hashing for docker images.

The recipe hash answers exactly one question: "have I already built this?" It
is a cache key, not a promise that the same recipe yields the same image.
Benchmark builds install unpinned apt packages and fetch dependency sources at
build time, so they are not reproducible and chasing that is not the goal.
Build once, then address the *result* by its digest.

Under-hashing is the dangerous direction: a false cache hit silently reuses a
stale image forever, and every experiment that follows quietly measures the
wrong thing. Over-hashing only costs a rebuild, and since docker's own layer
cache still applies, rebuilding an unchanged recipe usually lands on the
identical digest anyway -- a new cache entry pointing at the same identity.
Everything here is therefore biased toward hashing too much.
"""

import functools
import hashlib
import json
import os
import re

from common.utils import ROOT_DIR

# Bump when the hash payload changes shape, so that old entries in the lock
# are not mistaken for entries computed by the current code.
RECIPE_HASH_VERSION = 1

# How much of the hex digest to keep. Docker tags are limited to 128
# characters and a full sha256 is 64, which fits, but shorter keys keep `docker
# images` output readable. 32 hex chars is 128 bits of collision resistance.
RECIPE_HASH_LENGTH = 32

# Matches a FROM line, capturing the image reference and optional stage alias.
_FROM_RE = re.compile(
    r'^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$',
    re.IGNORECASE)

# Matches an ARG line, capturing the name and optional default value.
_ARG_RE = re.compile(r'^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$',
                     re.IGNORECASE)

# Matches $var and ${var} in an image reference.
_VAR_RE = re.compile(
    r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')


class UnhashedParentError(Exception):
    """Raised when a Dockerfile's FROM refers to an image whose identity did
    not make it into the recipe hash. That is the under-hashing case, so it is
    a hard error rather than a warning."""


def repo_path(relative_path):
    """Resolves a path from image_types.yaml, which are all relative to the
    repository root rather than to the caller's working directory."""
    return os.path.join(ROOT_DIR, relative_path)


def _compile_dockerignore_pattern(pattern):
    """Translates a single .dockerignore pattern into a regex, or returns None
    if the pattern uses syntax this does not understand. Returning None means
    the pattern is dropped, so the file is hashed -- the safe direction."""
    regex = ''
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == '*':
            if pattern.startswith('**', index):
                # '**' crosses directory separators.
                regex += '.*'
                index += 2
                continue
            # A single '*' does not cross a separator.
            regex += '[^/]*'
        elif char == '?':
            regex += '[^/]'
        elif char in '[]':
            # Character classes are rare here and easy to get subtly wrong.
            # Drop the pattern rather than risk excluding the wrong files.
            return None
        else:
            regex += re.escape(char)
        index += 1
    return re.compile('^' + regex + '$')


def _load_dockerignore(context_dir):
    """Reads |context_dir|/.dockerignore and returns a list of
    (is_exception, regex) pairs. Docker only honours a .dockerignore at the
    root of the build context, so that is the only place this looks."""
    path = os.path.join(context_dir, '.dockerignore')
    if not os.path.isfile(path):
        return []

    patterns = []
    with open(path, encoding='utf-8') as file_handle:
        for line in file_handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            is_exception = line.startswith('!')
            if is_exception:
                line = line[1:].strip()
            line = line.strip('/')
            if not line:
                continue
            regex = _compile_dockerignore_pattern(line)
            if regex is not None:
                patterns.append((is_exception, regex))
    return patterns


def _is_ignored(relpath, patterns):
    """Returns True if |relpath| is excluded by |patterns|. A path is also
    excluded when any of its parent directories is, which is how docker treats
    an ignored directory."""
    if not patterns:
        return False

    # Test the path itself and each ancestor directory.
    candidates = [relpath]
    parent = os.path.dirname(relpath)
    while parent:
        candidates.append(parent)
        parent = os.path.dirname(parent)

    ignored = False
    for is_exception, regex in patterns:
        if any(regex.match(candidate) for candidate in candidates):
            ignored = not is_exception
    return ignored


def _hash_file(path):
    """Returns the sha256 of the file at |path|. Symlinks are hashed by their
    target rather than followed, matching how docker tars up a context."""
    hasher = hashlib.sha256()
    if os.path.islink(path):
        hasher.update(b'symlink\0')
        hasher.update(os.readlink(path).encode('utf-8'))
        return hasher.hexdigest()

    hasher.update(b'file\0')
    with open(path, 'rb') as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


@functools.lru_cache(maxsize=None)
def hash_context(context_dir):
    """Returns a hash of every file in |context_dir| that docker would send as
    build context. Memoized because a handful of contexts (notably the repo
    root) are shared by hundreds of images within one build run.

    The whole context is hashed rather than only the paths a Dockerfile COPYs.
    Narrowing it by parsing COPY sources would be the under-hashing direction,
    and it is not worth the risk: a context-only change still hits docker's
    layer cache, so it costs a build invocation rather than a new identity.
    """
    context_dir = os.path.abspath(context_dir)
    patterns = _load_dockerignore(context_dir)
    # An ignored directory can still hold files that a later '!' pattern
    # re-includes, and pruning it would never see them. Only skip descending
    # when no exception could apply. Walking a little extra costs time; missing
    # a file that docker does send would under-hash.
    may_prune = not any(is_exception for is_exception, _ in patterns)

    entries = []
    for dirpath, dirnames, filenames in os.walk(context_dir):
        relative_dir = os.path.relpath(dirpath, context_dir)
        if relative_dir == '.':
            relative_dir = ''

        dirnames[:] = sorted(
            name for name in dirnames if not may_prune or
            not _is_ignored(os.path.join(relative_dir, name), patterns))

        for filename in sorted(filenames):
            relpath = os.path.join(relative_dir, filename)
            if _is_ignored(relpath, patterns):
                continue
            path = os.path.join(dirpath, filename)
            # Only the executable bit survives a docker build, so that is the
            # only part of the mode worth hashing.
            is_executable = bool(os.lstat(path).st_mode & 0o111)
            entries.append([relpath, is_executable, _hash_file(path)])

    entries.sort()
    payload = json.dumps(entries, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _expand_variables(value, variables):
    """Substitutes $var and ${var} in |value| from |variables|. Returns None if
    any referenced variable is unknown, since a half-expanded reference cannot
    be checked for safety."""
    unresolved = False

    def replace(match):
        nonlocal unresolved
        name = match.group(1) or match.group(2)
        if name not in variables:
            unresolved = True
            return ''
        return variables[name]

    expanded = _VAR_RE.sub(replace, value)
    return None if unresolved else expanded


def parse_dockerfile_parents(dockerfile_text, build_args):
    """Returns the list of external image references that |dockerfile_text|
    builds FROM, with build args and ARG defaults expanded.

    References to earlier stages in the same Dockerfile are internal and are
    left out. An entry of None means a reference could not be fully expanded,
    which callers must treat as unsafe rather than ignore.
    """
    variables = {}
    stages = set()
    parents = []

    for line in dockerfile_text.splitlines():
        from_match = _FROM_RE.match(line)
        if from_match:
            reference = _expand_variables(from_match.group(1), variables)
            if reference is None:
                parents.append(None)
            elif reference not in stages:
                parents.append(reference)
            if from_match.group(2):
                stages.add(from_match.group(2))
            continue

        arg_match = _ARG_RE.match(line)
        if arg_match:
            name = arg_match.group(1)
            # Every ARG is collected, not just those before the first FROM.
            # The reference says FROM only sees ARGs declared ahead of the
            # first stage, but BuildKit accepts an ARG sitting between two
            # FROMs and dispatcher-image/Dockerfile relies on that.
            if name in build_args:
                variables[name] = build_args[name]
            elif arg_match.group(2) is not None:
                variables[name] = arg_match.group(2).strip()

    return parents


def check_parents_are_hashed(image_name, dockerfile_text, build_args,
                             hashed_references):
    """Raises UnhashedParentError if the Dockerfile builds FROM an image whose
    identity is not covered by the recipe hash.

    This is the guard that makes over-hashing the default. A parent is
    acceptable when it is one of |hashed_references| (a FuzzBench image we
    resolved to a digest) or when it is externally pinned by digest. A parent
    named by a mutable tag we did not resolve would let the recipe hash stay
    the same while the image underneath changed.
    """
    for parent in parse_dockerfile_parents(dockerfile_text, build_args):
        if parent is None:
            raise UnhashedParentError(
                f'{image_name}: a FROM reference could not be expanded, so it '
                'cannot be checked. Give the Dockerfile an ARG default or pass '
                'the value as a build arg.')
        if parent in hashed_references:
            continue
        if '@sha256:' in parent:
            # Externally pinned by digest: immutable, so the Dockerfile bytes
            # already pin it.
            continue
        raise UnhashedParentError(
            f'{image_name}: builds FROM "{parent}", which is neither a '
            'resolved FuzzBench image nor pinned by digest. Pass it as a build '
            'arg so its digest enters the recipe hash, or pin it with '
            '@sha256:.')


def compute(image, hashed_build_args):
    """Returns the recipe hash for |image|.

    |hashed_build_args| is the image's build args as a name -> value mapping,
    with every reference to another FuzzBench image already replaced by that
    image's digest. Substituting digests rather than names is what keeps the
    merkle chain intact: a parent that gets rebuilt into different content
    changes its digest, which changes every descendant's key, even though the
    parent's *name* never moved.

    The image's own tag is deliberately not hashed. Two entries with identical
    recipes should collide -- and one pair does: the per-fuzzer intermediate
    runner is byte-identical across every benchmark, so content addressing
    collapses N builds into one.
    """
    with open(repo_path(image['dockerfile']), 'rb') as file_handle:
        dockerfile_hash = hashlib.sha256(file_handle.read()).hexdigest()

    payload = {
        'version': RECIPE_HASH_VERSION,
        'dockerfile': dockerfile_hash,
        'context': hash_context(repo_path(image['context'])),
        'build_args': dict(sorted(hashed_build_args.items())),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
    return digest[:RECIPE_HASH_LENGTH]
