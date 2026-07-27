#!/bin/bash -eu
# Copyright 2022 Google Inc.
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

FUZZ_TARGET_NAME="${FUZZ_TARGET:-fuzz-link-parser}"

# systemd's own OSS-Fuzz script, copied and patched by the Dockerfile.
"$SRC/oss-fuzz.sh"

# It installs all 15 fuzz targets from src/fuzz, along with their dictionaries,
# options files and seed corpora. FuzzBench runs one of them, and everything
# else is carried by every builder and runner image built from here.
find "$OUT" -maxdepth 1 -type f -name 'fuzz-*' \
    ! -name "${FUZZ_TARGET_NAME}" \
    ! -name "${FUZZ_TARGET_NAME}.*" \
    ! -name "${FUZZ_TARGET_NAME}_*" \
    -delete

test -x "$OUT/${FUZZ_TARGET_NAME}"
