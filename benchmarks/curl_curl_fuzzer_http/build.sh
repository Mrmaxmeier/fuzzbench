#!/bin/bash -eu
# Copyright 2016 Google Inc.
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
#
################################################################################

# FuzzBench sets FUZZ_TARGET from benchmark.yaml; curl-fuzzer uses FUZZ_TARGETS.
TARGET="${FUZZ_TARGET:-curl_fuzzer_http}"

cd "$SRC/curl_fuzzer"

# ossfuzz.sh sources scripts/fuzz_targets, which otherwise lists every target.
sed -i "s/^export FUZZ_TARGETS=.*/export FUZZ_TARGETS=\"${TARGET}\"/" scripts/fuzz_targets
sed -i "s/^make || exit 4\$/make ${TARGET} || exit 4/" scripts/compile_fuzzer.sh
sed -i 's/^make check || exit 5$/true/' scripts/compile_fuzzer.sh

./ossfuzz.sh
