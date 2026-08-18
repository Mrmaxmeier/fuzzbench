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
# (LibAFL 0.16.1's workspace declares rust-version = 1.93.1; use latest
# stable so we always satisfy that MSRV).
RUN if which rustup; then rustup self uninstall -y; fi && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs > /rustup.sh && \
    sh /rustup.sh --default-toolchain stable -y && \
    rm /rustup.sh

# Build dependencies only. Do NOT install a second clang/llvm or run
# createAliases.sh — that overwrites the OSS-Fuzz clang and breaks libc++
# (__config_site) on C++ benchmarks. LibAFL builds against the parent
# image's clang.
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        lsb-release wget software-properties-common gnupg \
        wget libstdc++5 libtool-bin automake flex bison \
        libglib2.0-dev libpixman-1-dev python3-setuptools unzip \
        apt-utils apt-transport-https ca-certificates joe curl

# Download libafl.
RUN git clone https://github.com/AFLplusplus/LibAFL /libafl

# Pin to the 0.16.1 release (fuzzbench harness lives under fuzzers/inprocess/).
RUN cd /libafl && git checkout 92ad89454765440c7c03b87b11d5dbde753f9633

# LibAFL 0.16.1 assumes that on LLVM >= 22, PassPlugin.h lives under
# llvm/Plugins/ instead of llvm/Passes/ (it was moved upstream in LLVM
# commit f54df0d09e19, first tagged in an LLVM 22 release). The parent
# image's clang 22 build predates that header move, so the include still
# needs to come from llvm/Passes/ here. Patch both spots that branch on
# LLVM_VERSION_MAJOR >= 22 to keep using the old path.
RUN cd /libafl && \
    sed -i 's#llvm/Plugins/PassPlugin.h#llvm/Passes/PassPlugin.h#' \
        crates/libafl_cc/src/common-llvm.h \
        crates/libafl_cc/src/function-logging.cc

# Compile libafl.
# NOTE: as of 0.16, the edges map size is split into an allocated (max) size
# and a default (power-of-two, growable) size, replacing the old single
# LIBAFL_EDGES_MAP_SIZE variable. Both libafl_cc (instrumentation side) and
# libafl_targets (runtime harness side) must agree on the allocated size or
# the compiled coverage map and the harness's map buffer can disagree in
# size, so it is set explicitly here for both rather than relying on
# per-crate defaults (which happen to differ: 2097152 vs 2621440).
RUN cd /libafl && \
    unset CFLAGS CXXFLAGS && \
    export LIBAFL_EDGES_MAP_ALLOCATED_SIZE=2621440 && \
    cd ./fuzzers/inprocess/fuzzbench && \
    PATH="/root/.cargo/bin/:$PATH" cargo build --profile release-fuzzbench --features no_link_main

# Auxiliary weak references.
RUN cd /libafl/fuzzers/inprocess/fuzzbench && \
    clang -c stub_rt.c && \
    ar r /stub_rt.a stub_rt.o
