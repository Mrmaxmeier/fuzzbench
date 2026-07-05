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

CONFIGURE_FLAGS=""
if [[ $CFLAGS = *sanitize=memory* ]]
then
  CONFIGURE_FLAGS="no-asm"
fi

if [ "$FUZZER" = "centipede" ]
then
  WITH_FUZZER_LIB="$FUZZER_LIB"
else
  WITH_FUZZER_LIB='/usr/lib/libFuzzingEngine'
fi

./config --debug enable-fuzz-libfuzzer -DPEDANTIC -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION no-shared enable-tls1_3 enable-rc5 enable-md2 enable-ec_nistp_64_gcc_128 enable-ssl3 enable-ssl3-method enable-nextprotoneg enable-weak-ssl-ciphers --with-fuzzer-lib=$WITH_FUZZER_LIB $CFLAGS -fno-sanitize=alignment $CONFIGURE_FLAGS

FUZZ_TARGET_NAME="${FUZZ_TARGET:-x509}"

# Build libs first, then one fuzzer. A single `make fuzz/$FUZZ_TARGET_NAME` races
# ahead of libcrypto/libssl; combining both targets in one make -j also races.
make -j$(nproc) build_libs LDCMD="$CXX $CXXFLAGS"
make -j$(nproc) "fuzz/${FUZZ_TARGET_NAME}" LDCMD="$CXX $CXXFLAGS"

cp "fuzz/${FUZZ_TARGET_NAME}" "$OUT/"
zip -j "$OUT/${FUZZ_TARGET_NAME}_seed_corpus.zip" "fuzz/corpora/${FUZZ_TARGET_NAME}"/*
cp fuzz/oids.txt "$OUT/x509.dict"

# OpenSSL's default build leaves ~3GB of unit-test binaries in test/.
rm -rf test
find fuzz -executable -type f '!' -name \*.py '!' -name \*-test '!' -name \*.pl \
    ! -name "${FUZZ_TARGET_NAME}" -delete
