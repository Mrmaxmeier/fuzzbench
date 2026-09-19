---
layout: default
title: Running a local experiment
nav_order: 5
permalink: /running-a-local-experiment
---

# Running a local experiment

This page explains how to run a local [experiment]({{ site.baseurl }}/reference/glossary/#Experiment) on
your own.

- TOC
{:toc}

This page will walk you through on how to use `run_experiment.py` to start
a local experiment. The `run_experiment.py` script will
create and run a dispatcher docker container which runs the experiment,
including:
1. Building desired fuzzer-benchmark combinations.
1. Starting containers to run fuzzing trials with the fuzzer-benchmark
   builds and stopping them when they are done.
1. Measuring the coverage from these trials.
1. Generating reports based on these measurements.

The rest of this page will assume all commands are run from the root of
FuzzBench checkout.

## Experiment configuration file

You need to create an experiment configuration yaml file.
This file contains the configuration parameters for experiments that do not
change very often.
Below is an example configuration file with explanations of each required
parameter.

```yaml
# The number of trials of a fuzzer-benchmark pair.
trials: 5

# The amount of time in seconds that each trial is run for.
# 1 day = 24 * 60 * 60 = 86400
max_total_time: 86400

# The location of the docker registry.
# FIXME: Support custom docker registry.
# See https://github.com/Mrmaxmeier/fuzzbench/issues/777
docker_registry: localhost/fuzzbench

# The local experiment folder that will store most of the experiment data.
# Must be an absolute POSIX path.
experiment_filestore: /tmp/experiment-data

# The local report folder where HTML reports and summary data will be stored.
# Must be an absolute POSIX path.
report_filestore: /tmp/report-data
```

`local_experiment` defaults to `true` and does not need to be set for local
runs.

Each trial's container may use at most 4 GiB of memory, swap included, and a
trial that goes over is killed. To change this, set `runner_memory_mb`, where
`0` means no limit:

```yaml
runner_memory_mb: 8192
```

## Benchmarks

Pick the benchmarks you want to use from the `benchmarks/` directory.

For example: `freetype2_ftfuzzer` and `bloaty_fuzz_target`.

## Fuzzers

Pick the fuzzers you want to use from the `fuzzers/` directory.
For example: `libfuzzer` and `afl`.

## Executing run_experiment.py

Now that everything is ready, execute `run_experiment.py`:

```bash
PYTHONPATH=. python3 experiment/run_experiment.py \
--experiment-config experiment-config.yaml \
--benchmarks freetype2_ftfuzzer bloaty_fuzz_target \
--experiment-name $EXPERIMENT_NAME \
--fuzzers afl libfuzzer
```

where `$EXPERIMENT_NAME` is the name you want to give the experiment.

You can optionally add:
* `--no-seeds` - to skip using seed corpus across all benchmarks.
* `--no-dictionaries` - to skip using dictionaries across all benchmarks.
* `--concurrent-builds N` - to limit the number of concurrent builds, useful
  when having limited memory.
* `--runners-cpus` - to limit the number of usable CPUs by the runner containers
  (in which fuzzers run). See also [Running trials on a HyperQueue
  cluster](#running-trials-on-a-hyperqueue-cluster).
* `--measurers-cpus` - to limit the number of usable CPUs by the measurer
  containers.

## Running trials on a HyperQueue cluster

By default every trial runs in a container on the machine running the
dispatcher. With `executor: hyperqueue`, trials run on the workers of a
[HyperQueue](https://it4innovations.github.io/hyperqueue/) server instead.
The dispatcher still runs locally and still builds every image and measures
every trial; only the fuzzing moves to the cluster. Each trial becomes one
HyperQueue job named `r-$EXPERIMENT_NAME-$TRIAL_ID`, pinned to the cpus its
worker allocates it.

```yaml
executor: hyperqueue

# Both filestores must be on a filesystem that every worker mounts at the same
# path, such as an NFS export.
experiment_filestore: /mnt/hq/fuzzbench/experiment-data
report_filestore: /mnt/hq/fuzzbench/report-data

# Optional. Default to the `hq` on PATH and to $HQ_SERVER_DIR (or
# ~/.hq-server).
hq_binary: /mnt/hq/hq
hq_server_dir: /mnt/hq/.hq-server
```

Each worker needs `podman` (rootless is fine; on Debian and Ubuntu also
install `uidmap`, which `podman` only recommends) and `python3`. Nothing else
is installed on it and nothing needs to be built there: the dispatcher exports
each runner image once to `$EXPERIMENT_FILESTORE/runner-images/`, named by
digest, and a worker loads it the first time it runs one of its trials.

With this executor `--runners-cpus` no longer refers to this machine. It caps
the cluster cpus that the experiment's trials may hold at once, queued or
running, and leaving it out submits every trial straight away.

Each job also reserves its trial's `runner_memory_mb` from HyperQueue's `mem`
resource, so a worker only takes on as many trials as fit in its memory. At
the default of 4 GiB, a worker with 768 GiB runs at most 192 trials at once,
however many cpus it has, and the rest wait in the queue.

A trial counts as started when its container starts on a worker, not when it
is submitted, so queueing doesn't cost it measurement cycles. A job that fails
before getting that far is resubmitted, up to three times. Each attempt's
output is in `$EXPERIMENT_FILESTORE/$EXPERIMENT_NAME/hq/logs/`.

`experiment/stop_experiment.py` stops the dispatcher and cancels the
experiment's jobs, which would otherwise keep running on the cluster.

## Viewing reports

You should eventually be able to see reports from your experiment, that are
update at some interval throughout the experiment. However, you may have to wait
a while until they first appear since a lot must happen before there is data to
generate report. Once they are available, you should be able to view them at:
`$REPORT_FILESTORE/$EXPERIMENT_NAME/index.html` (using the `report_filestore`
path from your experiment config).
