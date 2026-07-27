trim dependencies
candidates: orange3, clusterfuzz, alembic, psutil, transitive pins (done)

strip google-isms: .allstar, CLA,  (done)

replace gcr.io/..
eventually update docs and replace github.com/google/fuzzbench

make job inputs fully content-addressed? -> all images: base, builders, fuzzers, benchmarks, coverage measurement

  decided: don't chase hermetic builds. build once, then address the *result*.
  recipe hash = cache key ("have i built this?"), image digest = identity.
  i.e. a lockfile: the recipe no longer has to determine the content, it only
  has to decide whether a build is needed.

  recipe hash must cover: dockerfile bytes, build context, build args, and the
  parent's *digest* (not its name) -> keeps the merkle chain intact even though
  contents are unreproducible. under-hashing is the dangerous direction: false
  cache hit = silently reusing a stale image forever. over-hashing just costs a
  rebuild.

  today everything is mutable names:
    - docker/image_types.yaml -> tags like builders/benchmark/{benchmark}
    - children reference parents by name, e.g. image_types.yaml:37
      parent_image=gcr.io/fuzzbench/builders/benchmark/{benchmark}
    - common/benchmark_utils.py:73 tags runners with the *experiment name*
    - artifacts namespaced by experiment, not by content (build_utils.py:41)

  consequence that actually bites: benchmarks silently drift. any recipe edit
  changes the key -> rebuild -> whatever upstream is that day. some benchmarks
  pin (curl_curl_fuzzer_http/Dockerfile:21), others clone --depth 1 of a moving
  branch (:22) + pull latest deps (:25). so "libpng" in march != "libpng" in
  september, but analysis groups on the bare benchmark *name*
  (database/models.py:50 is a plain String; analysis/data_utils.py:236,259,367).
  -> record the resolved digest per trial and compare on digest, not name.
  turns a silent wrong answer into a loud one.

  also: the image becomes the source of truth, not the recipe. can't rebuild it
  (upstream may be gone / force-pushed), so image retention == data retention.
  don't let anything GC them.

  races: two builders missing the same key concurrently produce two different
  digests. need atomic first-writer-wins on the recipe->digest mapping, else
  experiments quietly diverge. matters more once builds are distributed.

make it work across multiple blades -> SLURM?

  cluster has a shared fs, so: no per-node image import at all. build once,
  convert to a single-file apptainer image, put it on shared fs, every node
  execs it in place.

    docker save img -o img.tar
    apptainer build img.sif docker-archive://img.tar

  why not docker load on each node: compute nodes generally have no daemon and
  no root (hence apptainer/podman-rootless/enroot at hpc sites), and even where
  they do it's decompress+unpack per node per image against small local disk.

  nice property: the sif's own sha256 *is* the content address. <sha256>.sif on
  shared fs is the entire distribution mechanism -> no registry, no per-node
  state, no coordination.

  gotcha: pick an identity that survives the transport. docker save/load keeps
  the image id (config digest) but drops the registry repo digest (computed
  over the pushed manifest). so don't record repo digests anywhere -> they
  dangle once images move as archives. record the sif sha256 as canonical,
  since that's what the nodes actually execute.

  benchmark_utils.py:73 + scheduler.py:354 (threads docker_image_url into a
  per-instance startup script) are the same call sites both this and the
  content-addressing item have to change -> do them as one change.

  fallback if needed: node-local staging, for a cluster w/o shared fs or if
  the fs chokes on hundreds of tasks faulting in the same image at job start.
  don't build that path up front.

  --- apptainer spike, done. results: ---

  conversion works, and docker save -> tar is unnecessary:
    apptainer build runner.sif docker-daemon://localhost/fuzzbench/runners/...
  skips the intermediate archive entirely. size: 2.36gb image -> 498mb sif
  (squashfs). that ratio is what makes shared-fs distribution cheap.

  runs as the invoking user (uid 1000), not root. fuzzing does not need root:
  libfuzzer + asan ran 2000 execs, found coverage, added units, no complaints.
  none of docker's --cap-add=SYS_PTRACE / SYS_NICE turned out to be required
  for libfuzzer. other fuzzers are unverified -- anything using ptrace
  (qemu-mode, symcc, honggfuzz) needs its own check before being trusted.

  the one real blocker: /out is read-only under apptainer. it holds both the
  immutable stuff (fuzz target, seeds) and everything the runner writes
  (corpus, logs, stats, $OUTPUT_CORPUS_DIR). docker papered over this with the
  container's writable layer.

  fix: --overlay <dir>, NOT --writable-tmpfs. both make /out writable, but the
  overlay keeps the image's own contents visible, persists to disk instead of
  ram, and can live on the shared fs next to the sif. verified: fuzzed with
  output into /out/corpus, files were still on the host after exit.
  layout: <dir>/upper + <dir>/work, one per trial.

  gotcha: startup-runner.sh does `nice -n -5`, which needs CAP_SYS_NICE. as
  non-root that prints "cannot set niceness: Permission denied" -- but gnu
  nice runs the command anyway and exits 0, so it degrades to a warning
  rather than a failure. consequence is real though: runners lose their
  priority boost over measurers, which is the thing niceness was there for.
  decide whether to drop it or ask slurm for the priority instead.

  side benefit: files land owned by the invoking user. under docker the
  runners write the filestore as root (corpus archives, fuzzer-log.txt,
  coverage tarballs all came back root-owned and undeletable as $USER).

  --- the rest of what docker was providing ---

  read experiment/resources/runner-startup-script-template.sh, not just the
  runner image: the template is where the privileges actually live, and it is
  the thing a slurm job step has to replace. it asks for

    --privileged
    --cap-add SYS_NICE --cap-add SYS_PTRACE
    --security-opt seccomp=unconfined
    --shm-size=2g
    --cpus / --cpuset-cpus

  none of these translate to apptainer as-is. mapping:
    - cpus/cpuset      -> slurm's job (--cpus-per-task, cgroup affinity).
                          drop from the launcher, let the scheduler own it.
    - shm-size         -> /dev/shm is the host's under apptainer. libfuzzer
                          was fine, but anything that sizes shm off the
                          container limit needs a check.
    - seccomp/privileged/ptrace -> only matter for fuzzers that ptrace. the
                          libfuzzer spike needed none of them. verify per
                          fuzzer before trusting: honggfuzz, and anything
                          qemu-mode, are the ones to test.

  also in that template, *outside* the container, run as root on the host:
    echo 0 > /proc/sys/kernel/yama/ptrace_scope
    echo core > /proc/sys/kernel/core_pattern
  these are node-level config, not job-level. on a cluster they belong in a
  slurm prolog or the node image -- a job step cannot set them. if the site
  will not change them, that constrains which fuzzers can run at all, so
  check the node's current values before committing to a fuzzer set.

  env the runner needs (template + runner image ENV), for the job script:
    INSTANCE_NAME FUZZER BENCHMARK EXPERIMENT TRIAL_ID TRIAL_GROUP_NUM
    MAX_TOTAL_TIME SNAPSHOT_PERIOD NO_SEEDS NO_DICTIONARIES OSS_FUZZ_CORPUS
    CUSTOM_SEED_CORPUS_DIR DOCKER_REGISTRY EXPERIMENT_FILESTORE
    REPORT_FILESTORE FUZZ_TARGET PRIVATE LOCAL_EXPERIMENT MICRO_EXPERIMENT
  and baked into the image: OUT=/out WORKDIR=/out ROOT_DIR=/src
    SEED_CORPUS_DIR=/out/seeds OUTPUT_CORPUS_DIR=/out/corpus PYTHONPATH=/src
  runner.py also writes os.path.abspath('results') -> /out/results, since
  workdir is /out. everything it writes is under /out, which is what makes
  the single --overlay sufficient.

  --- full entrypoint under apptainer, done. it works. ---

  ran startup-runner.sh -> runner.py (not just the fuzz target) for a 180s
  zlib/libfuzzer trial: exit 0, 65.5m execs, cov 362, corp 414, four corpus
  archives + fuzzer-log.txt synced to the filestore. throughput and coverage
  match the docker run of the same target within run-to-run variance
  (docker: cov 356/corp 391 at 170s; apptainer: cov 362/corp 414 at 170s).

  files came back owned by uid 1000, confirming the ownership benefit.

  it did NOT work at first, and the reason generalises. every remove-then-
  remake of a directory that ships in the image fails under the overlay:
  rmtree records a whiteout (a 0,0 char device in upper/), and creating the
  directory back over it needs trusted.overlay.opaque, which needs
  CAP_SYS_ADMIN. unprivileged -> EIO. hit initialize_directories first, then
  _clean_seed_corpus, each one only after fixing the last. fixed by emptying
  in place; see filesystem.recreate_directory. worth remembering as a class:
  anything that deletes an image-provided path will do this.

  loose ends before slurm:
    - overlay work/ dir ends up root-owned, so plain rm -rf of a trial's
      overlay fails as $USER. per-trial cleanup needs handling.
    - non-libfuzzer fuzzers still unverified. honggfuzz + a qemu-mode one
      are the ones to test, since they are what would need the ptrace and
      seccomp settings docker was passing.
    - the sif tested here was built before the fuzzer cull, with the fixed
      sources bind-mounted over /src. rebuild it from a current runner image
      before trusting a clean measurement.
