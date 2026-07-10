#!/bin/bash -eu
# Copyright 2018 Google Inc.
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

FUZZ_TARGET_NAME="${FUZZ_TARGET:-fuzz_dtlsclient}"

pip3 install -r $SRC/mbedtls/scripts/basic.requirements.txt

perl scripts/config.pl set MBEDTLS_PLATFORM_TIME_ALT
mkdir build
cd build
cmake -DENABLE_TESTING=OFF ..
cmake --build . --target "$FUZZ_TARGET_NAME" -j"$(nproc)"

cp "programs/fuzz/${FUZZ_TARGET_NAME}" "$OUT/"
if [ -f "../programs/fuzz/${FUZZ_TARGET_NAME}.options" ]; then
  cp "../programs/fuzz/${FUZZ_TARGET_NAME}.options" "$OUT/"
fi
# The corpus for fuzz_<name> lives in programs/fuzz/corpuses/<name>.
zip -jr "$OUT/${FUZZ_TARGET_NAME}_seed_corpus.zip" \
    "../programs/fuzz/corpuses/${FUZZ_TARGET_NAME#fuzz_}"
