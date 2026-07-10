#!/bin/bash -ex
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

set -e
set -u

FUZZ_TARGET_NAME="${FUZZ_TARGET:-libjpeg_turbo_fuzzer}"

cd "$SRC/libjpeg-turbo"
cmake . -DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_STATIC=1 -DENABLE_SHARED=0 \
    -DCMAKE_C_FLAGS_RELWITHDEBINFO="-g -DNDEBUG" \
    -DCMAKE_CXX_FLAGS_RELWITHDEBINFO="-g -DNDEBUG" \
    -DCMAKE_INSTALL_PREFIX="$WORK" \
    -DWITH_FUZZ=1 -DFUZZ_BINDIR="$OUT" -DFUZZ_LIBRARY="$LIB_FUZZING_ENGINE"
cmake --build . --target "$FUZZ_TARGET_NAME" -j"$(nproc)"

cp "fuzz/${FUZZ_TARGET_NAME}" "$OUT/"
cp "$SRC/decompress_fuzzer_seed_corpus.zip" \
    "$OUT/${FUZZ_TARGET_NAME}_seed_corpus.zip"
