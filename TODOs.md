trim dependencies
candidates: orange3, clusterfuzz, alembic, psutil, transitive pins (done)

strip google-isms: .allstar, CLA,  (done)

docs retargeted to this fork (Mrmaxmeier/fuzzbench); catalogs synced with
current fuzzers/ and benchmarks/

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

  --- built. it works end to end. ---

  experiment/build/{recipe_hash,image_lock,image_resolver}.py. the resolver
  replaces make on the experiment path; generated.mk stays for `make run-*`
  and friends, which are the only things that still read a mutable tag.

  build args carry the parent's immutable {registry}/{tag}:{recipe_hash};
  the hash payload carries the parent's *digest*. both are needed:
  buildkit rejects a bare digest in FROM (parses sha256:... as a repo name),
  but `docker run <digest>` is fine. so tags at build time, digests for
  identity and for launching.

  the guard that matters is recipe_hash.check_parents_are_hashed: parse the
  Dockerfile's FROMs, expand args, and refuse anything that is neither a
  resolved image nor pinned by @sha256. ran it over all 1685 images and it
  found two real holes:
    - base-image was FROM ubuntu:focal, a rolling tag, and it is the root of
      nearly the whole tree. now pinned by digest like the benchmarks were.
    - benchmark-runner/Dockerfile composed both parents from
      $registry/$fuzzer/$benchmark inside the Dockerfile, so neither parent's
      identity could enter the hash. now passed in as builder_image /
      parent_image.
  keep this test (test_recipe_hash.py); it is the regression guard for the
  whole scheme.

  context hashing is whole-context, memoized, .dockerignore-aware. 0.18s cold
  for the repo. narrowing it to what the Dockerfile COPYs is the under-hashing
  direction, and unnecessary: a source edit re-keys the recipe, but docker's
  layer cache still hits, so the rebuild lands on the *same digest*. verified
  in practice -- edited the repo, runner rebuilt, digest unchanged. over-
  hashing costs a build invocation, not an identity.

  nice fallout: the per-fuzzer intermediate runner is byte-identical across
  all 29 benchmarks, so it collapses 29 builds -> 1 (319 -> 11 over the
  matrix). it is literally the same digest as base-image for libfuzzer.

  also fixes a real correctness hole nobody had noticed: build_all_measurers
  and build_all_fuzzer_benchmarks each resolved the benchmark image by name,
  in separate phases, so a rebuild in between could pair coverage binaries
  from one source snapshot with a fuzz target from another. one memoized
  resolver makes that impossible by construction.

  lock lives at {experiment_filestore}/image-lock/, one file per key,
  published with link() -> first-writer-wins, loser adopts the winner's
  digest. sibling of the experiment dirs, not inside one, because the whole
  point is sharing across experiments.

  stale lock (entry exists, image does not) raises rather than rebuilding.
  rebuilding would hand out a different digest and silently contradict what
  past experiments recorded. `--allow-stale-lock` opts out and takes the new
  identity. this is the operational face of image retention == data retention.

  trials record runner_image_digest + benchmark_digest. benchmark_digest is
  the project-builder's, not the runner's: analysis groups per benchmark
  *across* fuzzers, where runner digests differ by construction, so the
  project-builder is the right grain. analysis/data_utils.validate_data now
  refuses to merge experiments whose benchmark digests disagree; a null
  digest (pre-lock data) warns instead.

  full manifest also written to {experiment}/config/images.yaml.

  `python3 -m experiment.build.image_resolver -f X -b Y --dry-run` reports
  cached/build/stale/unknown. unknown is honest, not a gap: a child's key
  covers its parent's digest, which does not exist until the parent is built.

  --- unrelated pre-existing bug found while smoke testing. fixed. ---

  measurement was stuck on master: zero snapshots recorded for the whole
  experiment. confirmed not mine by running the identical experiment from a
  clean HEAD worktree -- same 21 profraw errors, same 0 measured cycles, same
  empty snapshot table.

  cause: b7aee44e. it made do_coverage_run return early on an empty
  new_units_dir to stop libfuzzer's guaranteed-nonzero merge exit from logging
  "Coverage run failed." on quiet cycles. right diagnosis, wrong lever.

  the run is not optional. the binary writes its .profraw when the process
  exits, whatever the merge did -- verified directly: empty input dir, exit
  code 1, and still an 8328-byte profraw. that side effect is load-bearing:

    no profraw -> generate_coverage_information returns early
               -> no cov_summary_file
               -> measure_snapshot_coverage returns None
               -> snapshot never saved
               -> _get_unmeasured_first_snapshots keeps returning cycle 0
               -> that trial never advances past the cycle it cannot measure

  so one unmeasurable cycle zeroes out the entire trial, not just that cycle.
  with -ns cycle 0 is always empty, so every no-seeds experiment recorded
  nothing. with seeds it would instead freeze at the first plateau cycle,
  which is worse: it looks fine until the fuzzer stops finding things.
  the commit message's "the cycle's coverage archive is still written" was
  the incorrect assumption.

  fix: keep the run, drop the error report when the input dir was empty. that
  was the actual goal. verified end to end -- 3/3 cycles measured, 0 profraw
  errors, 0 "Coverage run failed", snapshots (0,0) (60,446) (120,446). the
  446 matches smoke-4's pre-regression number for the same pair exactly.

  still latent, not fixed: a cycle that genuinely cannot be measured (a broken
  coverage binary, say) still blocks every later cycle for that trial and ends
  the experiment with no snapshots and no error. the retry has no bound and
  nothing reports the trial as unmeasured at the end. worth a real failure
  path.

  --- fixed: measurement retries are now bounded. ---

  NUM_RETRIES (3) is wired into the measure manager's RetryRequest path. after
  exhaustion it records a zero-coverage snapshot so later cycles can advance,
  and drains the response queue once more after trials end. also delete
  cov_summary.json at the start of each cycle so a failed run cannot reuse a
  prior cycle's summary. characterization tests cover both.

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

    done, on the content-addressing side. get_runner_image_ref(digest) is now
    the single point where a trial's identity becomes something runnable, and
    scheduler threads {{runner_image_ref}} into the template. swapping docker
    for apptainer means changing what that ref materialises into (digest ->
    <sha256>.sif on the shared fs) and replacing the `docker run` block in
    runner-startup-script-template.sh with a slurm job step. nothing above
    those two places needs to know.

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

saturated corpora per benchmark -> corpus_store/

  want: a corpus per benchmark that is already at the plateau, so an
  experiment can start from saturation instead of spending its budget
  rediscovering the same edges. feed it new inputs over time, keep it
  minimized. not in git -- it is generated data that only grows.

  store layout is deliberately the one --custom-seed-corpus-dir already
  consumes ({store}/{benchmark}/units), so there is no packaging step between
  growing a corpus and seeding an experiment with it. verified against
  run_experiment.validate_custom_seed_corpus directly. bookkeeping (.meta,
  .work, .crashes) sits beside the benchmark dirs, never inside one, or it
  would be handed to a fuzzer as an input.

  units are named by sha1 of contents, which is also how libfuzzer names what
  it writes -- so units come back from a campaign already correctly named,
  exact duplicates collapse on import, and a unit keeps one identity across
  every round it survives.

  the thing that decides what to keep must be a *fuzzer's* build, not the
  coverage build. the coverage image is compiled -fprofile-instr-generate for
  llvm-cov and carries no sancov, so libfuzzer's -merge=1 sees zero features
  in it. measured: 12 inputs -> 0 kept through the coverage binary, 12 -> 6
  through the libfuzzer runner. run_coverage.py gets away with using the
  coverage binary because it only wants the .profraw side effect, never the
  merge's verdict. so: minimize against libfuzzer (richest feature set of the
  engines here, so it over-keeps relative to the others, which is the safe
  direction for a seed corpus) and record the image digest that did it.

  every round is a full distill from empty rather than a merge into the
  existing corpus. libfuzzer's merge only ever *adds*, so merging in place
  makes the store monotone -- a unit accepted in round one is never
  reconsidered when a smaller input covering the same features shows up in
  round five. the cost is real and so is the payoff: round 2 went 345 -> 389
  units while total bytes went 45560 -> 36258. more units, less corpus,
  because the re-distill swapped large units for smaller ones covering the
  same features.

  tried it: zlib_zlib_uncompress_fuzzer, libfuzzer.

    op         units   bytes   feat  edges
    seed           2    3478     36     35
    campaign     345   45560   1077    357
    campaign     389   36258   1105    358
    campaign     405   35969   1113    358
    campaign     435   37073   1133    358

  edges saturate after the first 60s round and then do not move. features
  keep creeping because libfuzzer counts value-profile features too, which
  are far finer-grained than edges -- so "features still rising" is not
  evidence the corpus is still learning anything a coverage report would
  show. edges are the signal to watch for saturation; features are not.

  the seed corpus zlib ships is 77 files of its own source tree (CMakeLists,
  adler32.c, .o files). none of them are zlib streams, so uncompress() bails
  on nearly all of them and they distill to 2 units. worth knowing before
  reading anything into a benchmark's shipped seeds.

  containers run --user $uid:$gid. everything they write lands in the store,
  and a store of root-owned units is one you cannot curate. same reason the
  apptainer work cared about ownership.

  the read-modify-write is locked, per benchmark, flock on
  {store}/.locks/{benchmark}.lock. the race is not in commit -- that is two
  renames -- it is that a round reads the corpus, spends however long a
  distillation takes, and then commit *replaces* rather than merges. two
  rounds whose distills overlap both read the same starting corpus and the
  second commit drops the first's finds.

  noticed it because a verification round and a still-running 4-round
  campaign overlapped. worth being accurate: that pair happened to serialize
  (units_before chained 435 -> 459 -> 470), so nothing was actually lost. the
  window is real, it just needs the *distills* to overlap, and zlib's distill
  is seconds.

  the lock covers only the rebuild, not the campaign. campaigns still fuzz in
  parallel and queue briefly to fold in, which is what you want: the one that
  folds second reads a corpus already containing the first's finds. holding
  it across the whole round would serialize the only part worth parallelising.
  verified with two concurrent 45s campaigns: both staged from 470, B folded
  470 -> 482, A then folded 482 -> 478 -- A's before was B's after, so both
  contributions survived, and the -4 is the re-distill dropping units that
  became redundant once B's smaller ones were there.

  flock rather than the link() first-writer-wins the image lock uses, because
  they are different problems. that one binds an immutable key to an immutable
  value, so the loser can adopt the winner's answer. here the resource is
  mutable and the section is long: what is wanted is exclusion for a duration.
  flock also releases on process exit, so a killed campaign cannot wedge a
  benchmark, which a sentinel file would need stale detection to match. not
  reentrant -- each entry opens its own fd -- so call sites are arranged not
  to nest.

  loose ends:
    - flock over NFS is only as good as the server's lock manager. fine on one
      host; re-check before the store lives on the cluster's shared fs.
    - only libfuzzer exercised. the campaign path goes through the fuzzer's
      own fuzz() entry point, so any engine in fuzzers/ should work, but
      untested. afl-family output layouts are the thing to check.
    - a campaign is bounded by `timeout` as the container's pid 1 rather
      than by asking the engine to stop. correct for engines that take no
      deadline, but it means the last chunk of a fork-mode run's finds can
      be lost. fine while rounds are cheap, less fine at 24h/round.
    - nothing decides when a benchmark is *done*. the edges column is the
      obvious stopping rule (n rounds with no new edges) but it is not
      automated -- you still eyeball `status -v`.
