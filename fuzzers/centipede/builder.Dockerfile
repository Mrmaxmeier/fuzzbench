# Copyright 2022 Google LLC
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

ENV CENTIPEDE_SRC=/src/centipede

# Rebuild Centipede. Pin bazel 7.4.1 via direct binary download — bazel 9
# removed native cc_* rules that this archived centipede commit still needs,
# and the bazel apt GPG URL is gone.
RUN rm -rf "$CENTIPEDE_SRC" && \
  git clone -n \
    https://github.com/google/centipede.git "$CENTIPEDE_SRC" && \
  echo 'build --client_env=CC=clang --cxxopt=-std=c++17 ' \
    '--cxxopt=-stdlib=libc++ --linkopt=-lc++' >> ~/.bazelrc && \
  apt-get update && \
  apt-get install -y curl git binutils libssl-dev unzip && \
  curl -L -o /usr/local/bin/bazel \
    https://github.com/bazelbuild/bazel/releases/download/7.4.1/bazel-7.4.1-linux-x86_64 && \
  chmod +x /usr/local/bin/bazel && \
  mkdir -p /clang && \
  curl -L \
    https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-llvmorg-14-init-9436-g65120988-1.tgz \
    | tar zx -C /clang && \
  (cd "$CENTIPEDE_SRC" && \
    git checkout 2a2c78a2c161d99f5962b9710bce61feb00acc3d && \
    bazel build -c opt :all) && \
  cp "$CENTIPEDE_SRC/bazel-bin/centipede" '/out/centipede'

RUN /clang/bin/clang "$CENTIPEDE_SRC/weak_sancov_stubs.cc" -c -o /lib/weak.o
