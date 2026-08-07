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

ARG parent_image
FROM $parent_image

# Uninstall old Rust & Install a toolchain that supports edition 2024
# (LibAFL 0.15.4 MSRV is 1.87).
RUN if which rustup; then rustup self uninstall -y; fi && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs > /rustup.sh && \
    sh /rustup.sh --default-toolchain 1.87.0 -y && \
    rm /rustup.sh

# Build dependencies only. Do NOT install a second clang/llvm or run
# createAliases.sh — that overwrites the OSS-Fuzz clang and breaks libc++
# (__config_site) on C++ benchmarks. LibAFL 0.15.4 supports LLVM 15–33 and
# builds against the parent image's clang 22.
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        lsb-release wget software-properties-common gnupg \
        wget libstdc++5 libtool-bin automake flex bison \
        libglib2.0-dev libpixman-1-dev python3-setuptools unzip \
        apt-utils apt-transport-https ca-certificates joe curl

# Download libafl.
RUN git clone https://github.com/AFLplusplus/LibAFL /libafl

# Pin to the 0.15.4 release (fuzzbench harness lives under fuzzers/inprocess/).
RUN cd /libafl && git checkout bd49bdf4f64a50349d803646fb2afa9b8b104fa5

# Compile libafl.
RUN cd /libafl && \
    unset CFLAGS CXXFLAGS && \
    export LIBAFL_EDGES_MAP_SIZE=2621440 && \
    cd ./fuzzers/inprocess/fuzzbench && \
    PATH="/root/.cargo/bin/:$PATH" cargo build --profile release-fuzzbench --features no_link_main

# Auxiliary weak references.
RUN cd /libafl/fuzzers/inprocess/fuzzbench && \
    clang -c stub_rt.c && \
    ar r /stub_rt.a stub_rt.o
