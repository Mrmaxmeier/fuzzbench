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

This repository is a slim, local-only fork focused on running experiments with
Docker on your own machine.

## Run an experiment locally

See
[docs/running-a-local-experiment/](docs/running-a-local-experiment/running_a_local_experiment.md)
for setup and configuration.

After integrating a fuzzer, follow
[docs/getting-started/](docs/getting-started/getting_started.md)
to build and test it, then run an experiment with `experiment/run_experiment.py`.

Reports are written to the `report_filestore` path in your experiment config
(for example `/tmp/report-data/$EXPERIMENT_NAME/index.html`).

## Sample reports

You can view a
[sample report](https://www.fuzzbench.com/reports/sample/index.html) and
[periodically generated reports](https://www.fuzzbench.com/reports/index.html)
from past upstream FuzzBench experiments. The sample report uses 10 fuzzers
against 24 real-world benchmarks, with 20 trials each over 24 hours.

When analyzing reports, we recommend:
* Checking the strengths and weaknesses of a fuzzer against various benchmarks.
* Looking at aggregate results to understand the overall significance of the
  result.

Please provide feedback by opening a GitHub issue
[here](https://github.com/Mrmaxmeier/fuzzbench/issues/new).

## Documentation

See the [docs/](docs/) directory (or serve it with `make docs-serve`) for the
full documentation set.

## Contacts

This fork is maintained at
[github.com/Mrmaxmeier/fuzzbench](https://github.com/Mrmaxmeier/fuzzbench).
The upstream project mailing list is
[fuzzbench-users](https://groups.google.com/forum/#!forum/fuzzbench-users).
