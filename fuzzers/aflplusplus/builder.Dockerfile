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
        python3-dev \
        python3-setuptools \
        automake \
        cmake \
        git \
        flex \
        bison \
        libglib2.0-dev \
        libpixman-1-dev \
        cargo \
        libgtk-3-dev \
        # for QEMU mode
        ninja-build \
        gcc-$(gcc --version|head -n1|sed 's/\..*//'|sed 's/.* //')-plugin-dev \
        libstdc++-$(gcc --version|head -n1|sed 's/\..*//'|sed 's/.* //')-dev

# Download afl++. Use a recent stable that understands LLVM 16+ (c++17).
RUN git clone -b stable https://github.com/AFLplusplus/AFLplusplus /afl && \
    cd /afl && \
    git checkout ad5304010ae3be9d5cdc1ba51b09e14169c5cb87

# OSS-Fuzz's LLVM 22 still exposes the pre-22 StringSwitch::Cases overloads;
# AFL++ stable assumes the initializer_list API for major >= 22. Keep the old
# overload until upstream bumps the guard.
RUN sed -i 's/LLVM_VERSION_MAJOR >= 22/LLVM_VERSION_MAJOR >= 23/g' \
        /afl/instrumentation/afl-llvm-common.cc

# Build without Python support as we don't need it.
# Set AFL_NO_X86 to skip flaky tests.
RUN cd /afl && \
    unset CFLAGS CXXFLAGS && \
    export CC=clang AFL_NO_X86=1 && \
    PYTHON_INCLUDE=/ make && \
    make -C utils/aflpp_driver && \
    cp utils/aflpp_driver/libAFLDriver.a /
