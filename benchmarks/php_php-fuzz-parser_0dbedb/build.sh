#!/bin/bash -eu
# Copyright 2019 Google Inc.
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

# PHP's zend_function union is incompatible with the object-size sanitizer
export CFLAGS="$CFLAGS -fno-sanitize=object-size"
export CXXFLAGS="$CXXFLAGS -fno-sanitize=object-size"

# Disable JIT profitability checks.
export CFLAGS="$CFLAGS -DPROFITABILITY_CHECKS=0"

# Make sure the right assembly files are picked
BUILD_FLAG=""
if [ "$ARCHITECTURE" = "i386" ]; then
    BUILD_FLAG="--build=i686-pc-linux-gnu"
fi

FUZZ_TARGET_NAME="${FUZZ_TARGET:-php-fuzz-parser}"

# build project
./buildconf --force
./configure $BUILD_FLAG \
    --disable-all \
    --enable-debug-assertions \
    --enable-option-checking=fatal \
    --enable-fuzzer \
    --enable-exif \
    --without-pcre-jit \
    --disable-phpdbg \
    --disable-cgi \
    --with-pic
make -j$(nproc) sapi/cli/php "sapi/fuzzer/${FUZZ_TARGET_NAME}"

# Generate the corpus for this fuzzer only; generate_all.php also builds the
# corpora and dictionaries of the five fuzzers that are no longer built.
corpus_name="${FUZZ_TARGET_NAME#php-fuzz-}"
sapi/cli/php "sapi/fuzzer/generate_${corpus_name}_corpus.php"

cp "sapi/fuzzer/dict/${corpus_name}" "$OUT/${FUZZ_TARGET_NAME}.dict"
cp "sapi/fuzzer/${FUZZ_TARGET_NAME}" "$OUT/"
zip -j "$OUT/${FUZZ_TARGET_NAME}_seed_corpus.zip" \
    "sapi/fuzzer/corpus/${corpus_name}"/*
