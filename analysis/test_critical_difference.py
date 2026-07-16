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
"""Tests for critical_difference.py."""

import math

import pandas as pd

from analysis import critical_difference
from analysis import plotting


def test_compute_cd_nemenyi():
    """Nemenyi CD matches the Demšar formula for k=3, N=10."""
    ranks = [1.5, 2.0, 2.5]
    cd = critical_difference.compute_cd(ranks, num_datasets=10, alpha='0.05')
    expected = 2.343701 * math.sqrt(3 * 4 / (6.0 * 10))
    assert abs(cd - expected) < 1e-9


def test_write_critical_difference_plot(tmp_path):
    """CD plot writes an image without Orange."""
    ranks = pd.Series([1.2, 2.4, 2.8, 3.6],
                      index=['afl', 'libfuzzer', 'honggfuzz', 'aflplusplus'])
    plotter = plotting.Plotter(list(ranks.index))
    image_path = tmp_path / 'cd.svg'
    plotter.write_critical_difference_plot(ranks, num_of_benchmarks=20,
                                           image_path=image_path)
    assert image_path.exists()
    assert image_path.stat().st_size > 0
