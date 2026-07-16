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
"""Tests for crash_comparer.py."""

from common.crash_comparer import CrashComparer


def test_identical_states_are_similar():
    assert CrashComparer('a\nb\n', 'a\nb\n').is_similar()


def test_empty_states_are_not_similar():
    assert not CrashComparer('', 'a\n').is_similar()
    assert not CrashComparer('a\n', '').is_similar()


def test_shared_frames_are_similar():
    assert CrashComparer('foo\nbar\nbaz\n', 'foo\nbar\nqux\n').is_similar()
