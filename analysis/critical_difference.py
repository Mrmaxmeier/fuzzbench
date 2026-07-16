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
"""Critical-difference diagrams for average ranks (Demšar 2006).

Replaces the former Orange3 dependency for Nemenyi CD plots.
"""

import math

import numpy as np
from matplotlib import pyplot as plt

# Studentized range critical values q_{alpha, k, inf} for the Nemenyi test.
# Indexed by k (number of algorithms); unused slots are 0.
# Source: Demšar, JMLR 7:1–30, 2006.
_NEMENYI_Q = {
    '0.05': [
        0, 0, 1.959964, 2.343701, 2.569032, 2.727774, 2.849705, 2.94832,
        3.030879, 3.101730, 3.163684, 3.218654, 3.268004, 3.312739, 3.353618,
        3.39123, 3.426041, 3.458425, 3.488685, 3.517073, 3.543799
    ],
    '0.1': [
        0, 0, 1.644854, 2.052293, 2.291341, 2.459516, 2.588521, 2.692732,
        2.779884, 2.854606, 2.919889, 2.977768, 3.029694, 3.076733, 3.119693,
        3.159199, 3.195743, 3.229723, 3.261461, 3.291224, 3.319233
    ],
}


def compute_cd(average_ranks, num_datasets, alpha='0.05'):
    """Return the Nemenyi critical difference for |average_ranks|."""
    k = len(average_ranks)
    q_values = _NEMENYI_Q[alpha]
    if k >= len(q_values):
        raise ValueError(
            f'Nemenyi CD table supports at most {len(q_values) - 1} algorithms, '
            f'got {k}.')
    return q_values[k] * math.sqrt(k * (k + 1) / (6.0 * num_datasets))


def _longest_nonsignificant_pairs(sorted_ranks, cd):
    """Return longest intervals of algorithms that are not significantly different."""
    n = len(sorted_ranks)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
             if abs(sorted_ranks[i] - sorted_ranks[j]) <= cd]

    def is_maximal(pair):
        left, right = pair
        for other_left, other_right in pairs:
            if ((other_left <= left and other_right > right) or
                (other_left < left and other_right >= right)):
                return False
        return True

    return [pair for pair in pairs if is_maximal(pair)]


def graph_ranks(average_ranks, names, cd, reverse=False, width=6.0,
                textspace=1.0):
    """Draw a critical-difference diagram on the current matplotlib figure.

    |average_ranks| and |names| are parallel sequences. Rank 1 is best unless
    |reverse| is True.
    """
    ranks = list(average_ranks)
    labels = list(names)
    order = sorted(range(len(ranks)),
                   key=lambda i: ranks[i],
                   reverse=reverse)
    sorted_ranks = [ranks[i] for i in order]
    sorted_names = [labels[i] for i in order]

    low = min(1, int(math.floor(min(sorted_ranks))))
    high = max(len(ranks), int(math.ceil(max(sorted_ranks))))

    def rank_x(rank):
        span = high - low
        if reverse:
            return textspace + (width - 2 * textspace) * (high - rank) / span
        return textspace + (width - 2 * textspace) * (rank - low) / span

    cliques = _longest_nonsignificant_pairs(sorted_ranks, cd) if cd else []
    clique_space = 0.2 + 0.2 + max(len(cliques) - 1, 0) * 0.1
    axis_y = 0.4 + 0.25
    height = axis_y + max(0.4, clique_space) + ((len(ranks) + 1) / 2) * 0.2

    fig = plt.figure(figsize=(width, height))
    fig.set_facecolor('white')
    axes = fig.add_axes([0, 0, 1, 1])
    axes.set_axis_off()
    axes.set_xlim(0, 1)
    axes.set_ylim(1, 0)

    def px(xs):
        return [x / width for x in xs]

    def py(ys):
        return [y / height for y in ys]

    def draw_line(points, **kwargs):
        axes.plot(px([p[0] for p in points]),
                  py([p[1] for p in points]),
                  color='k',
                  **kwargs)

    def draw_text(x, y, text, **kwargs):
        axes.text(x / width, y / height, text, **kwargs)

    # Rank axis.
    draw_line([(textspace, axis_y), (width - textspace, axis_y)], linewidth=0.7)
    for tick in list(np.arange(low, high, 0.5)) + [high]:
        size = 0.1 if tick == int(tick) else 0.05
        draw_line([(rank_x(tick), axis_y - size / 2),
                   (rank_x(tick), axis_y)],
                  linewidth=0.7)
    for tick in range(low, high + 1):
        draw_text(rank_x(tick),
                  axis_y - 0.1,
                  str(tick),
                  ha='center',
                  va='bottom')

    # CD scale bar.
    if cd:
        if reverse:
            begin, end = rank_x(high), rank_x(high - cd)
        else:
            begin, end = rank_x(low), rank_x(low + cd)
        cd_y = 0.25
        draw_line([(begin, cd_y), (end, cd_y)], linewidth=0.7)
        draw_line([(begin, cd_y + 0.05), (begin, cd_y - 0.05)], linewidth=0.7)
        draw_line([(end, cd_y + 0.05), (end, cd_y - 0.05)], linewidth=0.7)
        draw_text((begin + end) / 2,
                  cd_y - 0.05,
                  'CD',
                  ha='center',
                  va='bottom')

    # Labels left / right of the axis.
    label_base = axis_y + max(0.4, clique_space)
    half = math.ceil(len(sorted_ranks) / 2)
    for index in range(half):
        y = label_base + index * 0.2
        x = rank_x(sorted_ranks[index])
        draw_line([(x, axis_y), (x, y), (textspace - 0.1, y)], linewidth=0.7)
        draw_text(textspace - 0.2,
                  y,
                  sorted_names[index],
                  ha='right',
                  va='center')
    for index in range(half, len(sorted_ranks)):
        y = label_base + (len(sorted_ranks) - index - 1) * 0.2
        x = rank_x(sorted_ranks[index])
        draw_line([(x, axis_y), (x, y),
                   (textspace + (width - 2 * textspace) + 0.1, y)],
                  linewidth=0.7)
        draw_text(textspace + (width - 2 * textspace) + 0.2,
                  y,
                  sorted_names[index],
                  ha='left',
                  va='center')

    # Non-significance cliques.
    clique_y = axis_y + 0.2
    for left, right in cliques:
        draw_line([(rank_x(sorted_ranks[left]) - 0.05, clique_y),
                   (rank_x(sorted_ranks[right]) + 0.05, clique_y)],
                  linewidth=2.5)
        clique_y += 0.1

    return fig
