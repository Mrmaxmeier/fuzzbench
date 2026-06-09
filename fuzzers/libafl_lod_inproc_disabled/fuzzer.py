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
"""Byte-mutation baseline variant of the in-process LOD fuzzer.

Identical build/run to ``libafl_lod_inproc``; the only difference is the fuzzer
name. The base ``fuzzer.py`` resolves the experiment to ``lod-disable`` whenever
``'disabled'`` appears in ``$FUZZER`` (and the in-process fuzzer gates the LOD
stage + entropy feedback on ``!disabled``), so this variant runs the pure
byte-mutation baseline. Used as the A/B control to isolate what the LOD grammar
stage contributes.
"""

# pylint: disable=unused-import
from fuzzers.libafl_lod_inproc.fuzzer import build, fuzz  # noqa: F401
