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

RUN apt-get update && \
    apt-get install -y \
        build-essential \
        git \
        curl \
        wget \
        make \
        lsb-release software-properties-common gnupg

# The libafl_cc 0.15 CmpLog/SanitizerCoverage passes use PassBuilder APIs
# (e.g. registerOptimizerEarlyEPCallback) that don't exist in the base image's
# LLVM-15, so its libafl_cc build script fails to compile the pass on clang-15.
# Install LLVM-17 (same major version fuzzbench's own `libafl` integration uses)
# and make the unversioned clang/clang++/llvm-config names resolve to it on
# PATH, so both the cargo build of the wrappers and the wrappers at target-build
# time pick up LLVM-17. The symlinks land in /usr/local/bin, which precedes
# /usr/bin, overriding the base toolchain's clang-15.
RUN wget https://apt.llvm.org/llvm.sh && chmod +x llvm.sh && ./llvm.sh 17 && \
    rm -f llvm.sh && \
    for f in /usr/bin/clang-17 /usr/bin/clang++-17 /usr/bin/llvm-config-17 \
             /usr/bin/opt-17 /usr/bin/llc-17; do \
        ln -sf "$f" "/usr/local/bin/$(basename "$f" | sed 's/-17$//')"; \
    done && \
    clang --version && llvm-config --version

# Rust toolchain for the cargo build of the magma-inproc staticlib + wrappers.
# The vendored lod-sketch is edition 2024 (toolchain 1.90), same pin as the
# forkserver integration.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --profile minimal --default-toolchain 1.90

# Build the in-process LOD fuzzer toolchain. Unlike libafl_lod_fs there is no
# AFLplusplus build: the magma-inproc libafl_cc/libafl_cxx wrappers wrap the
# base image's clang (LLVM-15), instrument the target with SanitizerCoverage
# (edges + cmp), and link it against libmagma_inproc.a (which owns the whole
# in-process fuzzing loop: edges map, cmplog/I2S, MOpt, and the LOD grammar
# stage + entropy feedback). The result is a single self-contained fuzzer
# binary -- no fork/exec/IPC per execution, which is the point versus the
# forkserver.
#
# LIBAFL_EDGES_MAP_SIZE bakes the same large edges-map size into the sancov
# runtime that the magma targets use (kept consistent with the forkserver).
COPY lod-sketch /lod-sketch
RUN cd /lod-sketch/magma-inproc && \
    export CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse && \
    export LIBAFL_EDGES_MAP_SIZE=2621440 && \
    PATH="/root/.cargo/bin:$PATH" cargo build --release

# stub_rt.a: provides the process `main` (which calls libafl_main) plus weak
# fallbacks for the sancov/cmplog hooks. Passed as the "fuzzing engine" library
# (FUZZER_LIB) when linking the target; the wrapper appends libmagma_inproc.a
# after it.
RUN cd /lod-sketch/magma-inproc && \
    clang -O3 -c stub_rt.c -o /stub_rt.o && \
    ar rcs /stub_rt.a /stub_rt.o && \
    rm -f /stub_rt.o
