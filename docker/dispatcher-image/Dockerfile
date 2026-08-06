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

FROM gcr.io/oss-fuzz-base/base-clang@sha256:d3d07062af1beb5d83466fedea02cc832355e0da2453e9c9df3159bae256510c AS base-clang

ARG base_image=localhost/fuzzbench/base-image
FROM $base_image

ENV WORK /work
WORKDIR $WORK

# Install runtime dependencies for benchmarks, easy json parsing, and fuzzbench.
RUN apt-get update -y && apt-get install -y \
    jq \
    libglib2.0-0 \
    libxml2 \
    libarchive13 \
    libgss3 \
    libgl1

# Install docker cli.
RUN DOCKER_VERSION=29.5.3 && \
    curl -O https://download.docker.com/linux/static/stable/x86_64/docker-$DOCKER_VERSION.tgz && \
    tar -xvzf docker-$DOCKER_VERSION.tgz && \
    mv docker/docker /usr/bin/ && \
    rm -rf docker docker-$DOCKER_VERSION.tgz
# Install docker buildx plugin
COPY --from=docker/buildx-bin /buildx /usr/libexec/docker/cli-plugins/docker-buildx

# Copy llvm-tool binaries needed for clang source-code based coverage and
# symbolization.
COPY --from=base-clang /usr/local/bin/llvm-cov /usr/local/bin/
COPY --from=base-clang /usr/local/bin/llvm-profdata /usr/local/bin/
COPY --from=base-clang /usr/local/bin/llvm-symbolizer /usr/local/bin/

COPY startup-dispatcher.sh $WORK/
