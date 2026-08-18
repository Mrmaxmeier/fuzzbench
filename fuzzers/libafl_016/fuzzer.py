# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Integration code for LibAFL 0.16.

Only the pinned LibAFL revision differs from the libafl fuzzer (see
builder.Dockerfile); the fuzzbench harness this builds against and its command
line are the same, so the integration is shared.
"""

from fuzzers.libafl import fuzzer as libafl_fuzzer


def build():
    """Build benchmark."""
    libafl_fuzzer.build()


def fuzz(input_corpus, output_corpus, target_binary):
    """Run fuzzer."""
    libafl_fuzzer.fuzz(input_corpus, output_corpus, target_binary)
