#!/bin/bash -eu
# Copyright 2020 Google Inc.
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

FUZZ_TARGET_NAME="${FUZZ_TARGET:-stbi_read_fuzzer}"

# Upstream ossfuzz.sh also builds stb_png_read_fuzzer (PNG-only variant).
$CXX $CXXFLAGS -std=c++11 -I. \
    $SRC/stb/tests/stbi_read_fuzzer.c \
    -o "$OUT/${FUZZ_TARGET_NAME}" "$LIB_FUZZING_ENGINE"

tar xvzf "$SRC/stbi/jpg.tar.gz" --directory "$SRC/stb/tests"
tar xvzf "$SRC/stbi/gif.tar.gz" --directory "$SRC/stb/tests"
unzip "$SRC/stbi/bmp.zip" -d "$SRC/stb/tests"
unzip "$SRC/stbi/tga.zip" -d "$SRC/stb/tests"

find "$SRC/stb/tests" -name "*.png" -o -name "*.jpg" -o -name "*.gif" \
    -o -name "*.bmp" -o -name "*.tga" -o -name "*.TGA" \
    -o -name "*.ppm" -o -name "*.pgm" \
    | xargs zip "$OUT/${FUZZ_TARGET_NAME}_seed_corpus.zip"

echo "" >> "$SRC/stbi/gif.dict"
cat "$SRC/stbi/gif.dict" "$SRC/stb/tests/stb_png.dict" \
    > "$OUT/${FUZZ_TARGET_NAME}.dict"
