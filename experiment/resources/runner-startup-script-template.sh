#!/bin/bash
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

# Configure the host. Both of these need privileges this may not have, and
# neither is fatal to the trial, so failures are ignored rather than left to
# pollute the output that the caller now checks for real launch errors.

# Make everything ptrace-able. Braced so that bash's own report of a failed
# redirection is suppressed too, not just the command's stderr.
{ echo 0 > /proc/sys/kernel/yama/ptrace_scope; } 2>/dev/null || true

# Do not notify external programs about core dumps.
{ echo core > /proc/sys/kernel/core_pattern; } 2>/dev/null || true

# Start docker.
# Run detached and stream logs with `docker logs -f` rather than attaching:
# a foreground `docker run` needs the API connection upgraded to a raw
# hijacked stream, which podman's Docker-API compatibility layer (used to run
# local experiments without a real dockerd) rejects with "unable to upgrade to
# tcp, received 500". `docker logs -f` is a plain log-streaming endpoint that
# behaves the same against real dockerd, so this is not local-only.
#
# No --cpuset-cpus: pinning a trial to a core needs the cpuset controller,
# which isn't delegated to podman containers nested this deeply. --cpus alone
# still caps each trial's CPU share, so trials stay bounded - just not pinned.
docker run -d \
--name {{instance_name}} \
--privileged --cpus={{num_cpu_cores}} --rm \
{% if memory_mb %}--memory={{memory_mb}}m --memory-swap={{memory_mb}}m \
{% endif %}-e INSTANCE_NAME={{instance_name}} \
-e FUZZER={{fuzzer}} \
-e BENCHMARK={{benchmark}} \
-e EXPERIMENT={{experiment}} \
-e TRIAL_ID={{trial_id}} \
-e TRIAL_GROUP_NUM={{trial_group_num}} \
-e MICRO_EXPERIMENT={{micro_experiment}} \
-e MAX_TOTAL_TIME={{max_total_time}} \
-e SNAPSHOT_PERIOD={{snapshot_period}} \
-e NO_SEEDS={{no_seeds}} \
-e NO_DICTIONARIES={{no_dictionaries}} \
-e CUSTOM_SEED_CORPUS_DIR={{custom_seed_corpus_dir}} \
-e DOCKER_REGISTRY={{docker_registry}} \
-e EXPERIMENT_FILESTORE={{experiment_filestore}} -v {{experiment_filestore}}:{{experiment_filestore}} \
-e REPORT_FILESTORE={{report_filestore}} -v {{report_filestore}}:{{report_filestore}} \
-e FUZZ_TARGET={{fuzz_target}} \
-e PRIVATE={{private}} \
-e LOCAL_EXPERIMENT=True \
--shm-size=2g \
--cap-add SYS_NICE --cap-add SYS_PTRACE \
--security-opt seccomp=unconfined \
{{runner_image_ref}} > /dev/null
run_status=$?
if [ $run_status -ne 0 ]; then
  echo "docker run for {{instance_name}} failed with status $run_status" >&2
  exit $run_status
fi

# Stream the container's log to a file. --rm deletes the container when it
# exits, so this is the only copy of it.
#
# Backgrounded, with its own stdio, so that this script exits as soon as the
# container is up. The script's exit status is the caller's only evidence that
# the trial actually started -- blocking here for the whole run made every
# launch look successful, including the ones where `docker run` had just
# failed. Detaching it also means an outstanding trial costs one process rather
# than a blocked shell plus this one.
setsid docker logs -f {{instance_name}} \
  < /dev/null > /tmp/runner-log-{{trial_id}}.txt 2>&1 &
