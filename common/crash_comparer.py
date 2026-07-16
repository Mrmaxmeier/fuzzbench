# Copyright 2019 Google LLC
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
"""Compare crash states for approximate uniqueness (from ClusterFuzz)."""


def _levenshtein_distance(string_1, string_2):
    """Iterative Levenshtein distance with two matrix rows."""
    if string_1 == string_2:
        return 0
    if not string_1:
        return len(string_2)
    if not string_2:
        return len(string_1)

    v0 = list(range(len(string_2) + 1))
    v1 = [None] * (len(string_2) + 1)

    for i in range(len(string_1)):
        v1[0] = i + 1
        for j in range(len(string_2)):
            cost = 0 if string_1[i] == string_2[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        for j in range(len(v0)):
            v0[j] = v1[j]

    return v1[len(string_2)]


def _similarity_ratio(string_1, string_2):
    """Return a ratio for how similar two strings are."""
    length_sum = len(string_1) + len(string_2)
    if length_sum == 0:
        return 1.0
    return (length_sum - _levenshtein_distance(string_1, string_2)) / (
        1.0 * length_sum)


def longest_common_subsequence(first_frames, second_frames):
    """Count frames that match in order."""
    first_len = len(first_frames)
    second_len = len(second_frames)
    solution = [[0 for _ in range(second_len + 1)]
                for _ in range(first_len + 1)]

    for i in range(1, first_len + 1):
        for j in range(1, second_len + 1):
            if first_frames[i - 1] == second_frames[j - 1]:
                solution[i][j] = solution[i - 1][j - 1] + 1
            else:
                solution[i][j] = max(solution[i - 1][j], solution[i][j - 1])

    return solution[first_len][second_len]


class CrashComparer:
    """Compares two crash states for approximate similarity."""
    COMPARE_THRESHOLD = 0.8
    SAME_FRAMES_THRESHOLD = 2

    def __init__(self, crash_state_1, crash_state_2, compare_threshold=None):
        self.crash_state_1 = crash_state_1
        self.crash_state_2 = crash_state_2
        self.compare_threshold = compare_threshold or self.COMPARE_THRESHOLD

    def is_similar(self):
        """Return whether the two crash states are similar."""
        if not self.crash_state_1 or not self.crash_state_2:
            return False
        if self.crash_state_1 == self.crash_state_2:
            return True
        if 'FuzzerHash=' in self.crash_state_1:
            return False

        crash_state_lines_1 = self.crash_state_1.splitlines()
        crash_state_lines_2 = self.crash_state_2.splitlines()

        if (longest_common_subsequence(crash_state_lines_1,
                                       crash_state_lines_2) >=
                self.SAME_FRAMES_THRESHOLD):
            return True

        lines_compared = 0
        similarity_ratio_sum = 0.0
        for i in range(len(crash_state_lines_1)):
            if i >= len(crash_state_lines_2):
                break
            similarity_ratio_sum += _similarity_ratio(crash_state_lines_1[i],
                                                      crash_state_lines_2[i])
            lines_compared += 1

        if not lines_compared:
            return False
        return (similarity_ratio_sum / lines_compared) > self.compare_threshold
