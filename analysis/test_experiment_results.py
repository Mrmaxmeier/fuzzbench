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
# See the License for the specific language governing permissions andsss
# limitations under the License.
"""Tests for experiment_results.py"""
import os
import re
from unittest import mock

from analysis import experiment_results
from analysis import test_data_utils

REPORT_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__),
                                    'report_templates')


@mock.patch('common.benchmark_config.get_config', return_value={})
def test_linkify_fuzzer_names_in_ranking(_):
    """Tests turning fuzzer names into links."""
    experiment_df = test_data_utils.create_experiment_data()
    results = experiment_results.ExperimentResults(experiment_df,
                                                   coverage_dict=None,
                                                   output_directory=None,
                                                   plotter=None)
    ranking = results.rank_by_median_and_average_rank

    ranking = results.linkify_names(ranking)

    assert ranking.index[0] == (
        '<a href="https://github.com/google/fuzzbench/blob/'
        'master/fuzzers/afl">afl</a>')


@mock.patch('common.benchmark_config.get_config', return_value={})
def test_summary_tables_convert_to_html(_):
    """Tests that the Styler tables the report template renders can produce
    HTML. pandas 2.x removed Styler.render(); nothing caught it because report
    generation only runs at the end of a real experiment."""
    experiment_df = test_data_utils.create_experiment_data()
    results = experiment_results.ExperimentResults(experiment_df,
                                                   coverage_dict=None,
                                                   output_directory=None,
                                                   plotter=None)
    html = results.relative_code_summary_table.to_html()
    assert '<table' in html


def test_report_templates_do_not_use_removed_styler_api():
    """Tests that no template calls Styler.render(), which pandas 2.x removed
    in favour of to_html(). The templates are rendered only during report
    generation, so a stale call here fails at the very end of an experiment."""
    offenders = []
    for filename in os.listdir(REPORT_TEMPLATES_DIR):
        if not filename.endswith('.html'):
            continue
        path = os.path.join(REPORT_TEMPLATES_DIR, filename)
        with open(path, encoding='utf-8') as file_handle:
            contents = file_handle.read()
        if re.search(r'_table\s*\.\s*render\s*\(', contents):
            offenders.append(filename)
    assert not offenders, f'Templates using removed Styler.render(): {offenders}'
