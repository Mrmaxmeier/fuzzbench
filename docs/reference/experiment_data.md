---
layout: default
title: Experiment data
parent: Reference
nav_order: 6
permalink: /reference/experiment-data/
---

# Experiment data

This page describes how to obtain the raw data produced during an experiment.
This includes corpora, crashes and logs from fuzzers.
It does not include the metrics used to generate a report, that data is linked
to in the report itself.

## Getting the data

Experiment data is stored on the local filesystem under the `experiment_filestore`
path configured for your experiment (see the
[guide to running a local experiment]({{ site.baseurl }}/running-a-local-experiment/)).

For an experiment named `$EXPERIMENT_NAME`, the data directory is:

```
$EXPERIMENT_FILESTORE/$EXPERIMENT_NAME/
```

You can list and copy files from this directory using standard shell commands.
For example:

```bash
ls $EXPERIMENT_FILESTORE/$EXPERIMENT_NAME/
cp -r $EXPERIMENT_FILESTORE/$EXPERIMENT_NAME/experiment-folders/ .
```

HTML reports and summary data are written to the `report_filestore` path
configured for your experiment (for example
`$REPORT_FILESTORE/$EXPERIMENT_NAME/index.html`).

## Data layout

Let's see what the layout of this data directory looks like:

```
$EXPERIMENT_NAME
│
└───build-logs
│       oss-fuzz-$OSS_FUZZ_PROJECT-fuzzer-$FUZZER-hash-$DOCKER_HASH.txt  # Logs for an OSS-Fuzz benchmark build.
│       benchmark-$BENCHMARK-fuzzer-$FUZZER-hash-$DOCKER_HASH.txt  # Logs for a standard benchmark build.
│       ...
│
└───coverage-binaries
│       coverage-build-$BENCHMARK.tar.gz
│       ...
│
└───experiment-folders
│   │
│   └───$BENCHMARK-$FUZZER
│   │   │
│   │   └───trial-$TRIAL_NUM
│   │   │   │
│   │   │   └───corpus
│   │   │   │       corpus-archive-0001.tar.gz
│   │   │   │       ...
│   │   │   │
│   │   │   └───crashes
│   │   │   │       crashes-0001.tar.gz
│   │   │   │       ...
│   │   │   │
│   │   │   └─results
│   │   │          fuzzer-log.txt
│   │   │          unchanged-cycles
│   │   │
│   │   └─...
│   │
│   └─ ... 
│
└───input # Contains source code and config files used to run experiment.
```

### Corpus archive and unchanged-cycles

A trial is a run of a specific fuzzer on a specific benchmark. For each trial,
FuzzBench archives the `output_corpus` used in `fuzzer.py` every 15 minutes,
this is sometimes referred to as a "snapshot" (or "cycle"). As an optimization,
if the directory hasn't changed since the last cycle, the archiving step is
skipped and the cycle number is added to the `unchanged-cycles` file. For some
fuzzers like AFL, we ignore frequently changing non-corpus files like
`fuzzer_stats` when determining if the corpus changed since last cycle. Because
`fuzzer_stats` is included in the archive, you can obtain stats for an AFL-based
fuzzer using the last archive in a trial.

### Crashes

Though FuzzBench doesn't use crashes for measuring performance, it does save them.
When FuzzBench measures the coverage of a corpus snapshot, if it encounters any
crashes it adds them to the crashes archive for that cycle.

### fuzzer-log.txt

The stdout and stderr from running a fuzzer.
