# libafl_lod_inproc

In-process (libfuzzer-style) LOD-grammar-aware LibAFL fuzzer.

The benchmark is instrumented by the `magma-inproc` `libafl_cc` / `libafl_cxx`
wrappers (SanitizerCoverage edges + cmplog) and linked directly into the fuzzer
(`libmagma_inproc.a` + `stub_rt.a`), the same way fuzzbench's own `libafl`
integration works. The resulting target binary *is* the fuzzer and runs
`LLVMFuzzerTestOneInput` in-process via a LibAFL `InProcessExecutor` — no
fork/exec/IPC per execution.

This is the in-process sibling of `libafl_lod_fs` (which drives an
AFL++-instrumented target out-of-process via a `ForkserverExecutor`). Removing
the per-execution fork/exec is ~100× faster on grammar-heavy targets.

The fuzzing loop is MOpt havoc + cmplog/I2S plus a "Level of Detail" (LOD)
grammar mutation stage with per-edge entropy-minimum feedback. The grammar is
pinned per benchmark (see `LOD_GRAMMARS` in `fuzzer.py`). In-process coverage
guessing (`--lod-guess`) is on by default; set `LIBAFL_LOD_GUESS=0` to disable
it (a crashing grammar skeleton would abort the process).

`libafl_lod_inproc_disabled` is the byte-mutation control (LOD stage off).
