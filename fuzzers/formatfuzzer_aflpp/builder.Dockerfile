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
        libgtk-3-dev


# Download AFL++ v2.60 with FormatFuzzer patches.
RUN git clone https://github.com/uds-se/AFLplusplus /afl && \
    cd /afl && \
    git checkout 27db6451dd3c1db26b801ace7a1f7d0f9ffd869f

RUN git clone https://github.com/uds-se/FormatFuzzer /formatfuzzer && \
    cd /formatfuzzer && \
    git checkout ec42fcdae5329c9c9ff56bf589acb5c4257046cb


# Build without Python support as we don't need it.
# Set AFL_NO_X86 to skip flaky tests.
RUN cd /afl && \
    unset CFLAGS CXXFLAGS && \
    CC=clang CXX=clang++ AFL_NO_X86=1 PYTHON_INCLUDE=/ make

# Use afl_driver.cpp from LLVM as our fuzzing library.
RUN apt-get update && \
    apt-get install wget -y && \
    wget https://raw.githubusercontent.com/llvm/llvm-project/5feb80e748924606531ba28c97fe65145c65372e/compiler-rt/lib/fuzzer/afl/afl_driver.cpp -O /afl/afl_driver.cpp && \
    clang -Wno-pointer-sign -c /afl/llvm_mode/afl-llvm-rt.o.c -I/afl && \
    clang++ -stdlib=libc++ -std=c++11 -O2 -c /afl/afl_driver.cpp && \
    ar r /libAFLDriver.a *.o


# Build custom format mutators
RUN apt-get install -y python3-pip zlib1g-dev libboost1.71-dev
COPY build_formats.sh /
RUN cd /formatfuzzer && \
    python3 -m pip install -r requirements.txt && \
    bash /build_formats.sh
