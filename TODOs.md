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
