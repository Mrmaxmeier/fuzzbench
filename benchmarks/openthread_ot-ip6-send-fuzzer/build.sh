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

TARGET="${FUZZ_TARGET:-ot-ip6-send-fuzzer}"
CORPUS_NAME="${TARGET%-fuzzer}"

(
    mkdir -p build
    cd build

    cmake -GNinja \
        -DCMAKE_C_FLAGS="${CFLAGS}" \
        -DCMAKE_CXX_FLAGS="${CXXFLAGS}" \
        -DOT_BUILD_EXECUTABLES=OFF \
        -DOT_FUZZ_TARGETS=ON \
        -DOT_MTD=OFF \
        -DOT_PLATFORM=external \
        -DOT_RCP=OFF \
        -DOT_BORDER_AGENT=ON \
        -DOT_BORDER_ROUTER=ON \
        -DOT_CHANNEL_MANAGER=ON \
        -DOT_CHANNEL_MONITOR=ON \
        -DOT_CHILD_SUPERVISION=ON \
        -DOT_COAP=ON \
        -DOT_COAPS=ON \
        -DOT_COAP_BLOCK=ON \
        -DOT_COAP_OBSERVE=ON \
        -DOT_COMMISSIONER=ON \
        -DOT_DATASET_UPDATER=ON \
        -DOT_DHCP6_CLIENT=ON \
        -DOT_DHCP6_SERVER=ON \
        -DOT_DNS_CLIENT=ON \
        -DOT_ECDSA=ON \
        -DOT_HISTORY_TRACKER=ON \
        -DOT_IP6_FRAGM=ON \
        -DOT_JAM_DETECTION=ON \
        -DOT_JOINER=ON \
        -DOT_LINK_RAW=ON \
        -DOT_LOG_OUTPUT=APP \
        -DOT_MAC_FILTER=ON \
        -DOT_MTD_NETDIAG=ON \
        -DOT_NETDATA_PUBLISHER=ON \
        -DOT_PING_SENDER=ON \
        -DOT_SERVICE=ON \
        -DOT_SLAAC=ON \
        -DOT_SNTP_CLIENT=ON \
        -DOT_SRP_CLIENT=ON \
        -DOT_SRP_SERVER=ON \
        -DOT_THREAD_VERSION=1.3 \
        -DOT_UPTIME=ON \
        ..
    ninja "$TARGET"
)

cp -v "build/tests/fuzz/${TARGET}" "$OUT/"
cp -v "tests/fuzz/${TARGET}.dict" "$OUT/" 2>/dev/null || true
cp -v "tests/fuzz/${TARGET}.options" "$OUT/" 2>/dev/null || true

if [ -d "tests/fuzz/corpora/${CORPUS_NAME}" ]; then
    zip -j "$OUT/${TARGET}_seed_corpus.zip" "tests/fuzz/corpora/${CORPUS_NAME}"/*
fi
