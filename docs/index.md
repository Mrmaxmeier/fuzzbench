---
layout: default
title: FuzzBench
permalink: /
nav_order: 1
has_children: true
has_toc: false
---

# FuzzBench: Fuzzer Benchmarking Platform

FuzzBench is an open source platform for rigorously evaluating fuzzers on a wide
variety of real-world benchmarks. The goal of FuzzBench is to make it painless
to evaluate fuzzing research and make fuzzing research easier for the community
to adopt.

FuzzBench provides:

* An easy API for integrating fuzzers.
* Benchmarks from real-world projects. FuzzBench can use any
  [OSS-Fuzz](https://github.com/google/oss-fuzz) project as a benchmark.
* A reporting library that produces reports with graphs and statistical tests
  to help you understand the significance of results.

## Run an experiment locally

Run FuzzBench on your own machine using Docker. See the
[guide to running a local experiment]({{ site.baseurl }}/running-a-local-experiment/)
for setup and configuration.

After integrating a fuzzer, follow the
[getting started guide]({{ site.baseurl }}/getting-started/) to build and test
it, then run an experiment with `experiment/run_experiment.py`.

Reports are written to the `report_filestore` path in your experiment config
(for example `/tmp/report-data/$EXPERIMENT_NAME/index.html`).

## Overview

![FuzzBench architecture](images/FuzzBench-architecture.png)

The process works like this:
1. A fuzzer developer
[integrates a fuzzer]({{ site.baseurl }}/getting-started/adding-a-new-fuzzer/)
with FuzzBench.
1. The integration is merged into the [
FuzzBench repo](https://github.com/google/fuzzbench).
1. You run a local experiment with the fuzzers and benchmarks you want to compare.
1. FuzzBench generates a report comparing fuzzer performance on individual
benchmarks and in aggregate.

## Adding a fuzzer

Follow [this guide]({{ site.baseurl }}/getting-started/) to add a fuzzer to
FuzzBench and test it locally.

## Sample reports

You can view a
[sample report](https://www.fuzzbench.com/reports/sample/index.html) and
[periodically generated reports](https://www.fuzzbench.com/reports/index.html)
from past FuzzBench experiments. The sample report uses 10 fuzzers against 24
real-world benchmarks, with 20 trials each over 24 hours.

When analyzing reports, we recommend:
* Checking the strengths and weaknesses of a fuzzer against various benchmarks.
* Looking at aggregate results to understand the overall significance of the
  result.

Please provide feedback on any inaccuracies and potential improvements (such as
integration changes, new benchmarks, etc.) by opening a GitHub issue
[here](https://github.com/google/fuzzbench/issues/new).

## Contacts

Join our [mailing list](https://groups.google.com/forum/#!forum/fuzzbench-users)
for discussions and announcements, or send us a private email at
[fuzzbench@google.com](mailto:fuzzbench@google.com).
