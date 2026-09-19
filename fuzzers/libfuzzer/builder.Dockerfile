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

# Fetch only the pinned commit. A full clone of llvm-project is several GB, and
# one runs per benchmark, so an experiment's worth of them at once gets
# throttled by GitHub.
RUN git init /llvm-project && \
    cd /llvm-project && \
    git fetch --depth 1 https://github.com/llvm/llvm-project.git \
        5cda4dc7b4d28fcd11307d4234c513ff779a1c6f && \
    git checkout FETCH_HEAD && \
    cd compiler-rt/lib/fuzzer && \
    (for f in *.cpp; do \
      clang++ -stdlib=libc++ -fPIC -O2 -std=c++11 $f -c & \
    done && wait) && \
    ar r libFuzzer.a *.o && \
    cp libFuzzer.a /usr/lib
