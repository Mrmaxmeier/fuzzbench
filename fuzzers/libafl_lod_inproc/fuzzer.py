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
#
"""Integration code for the in-process LOD-grammar-aware LibAFL fuzzer.

Unlike the forkserver sibling (``libafl_lod_fs``), the benchmark is instrumented
by the magma-inproc ``libafl_cc`` / ``libafl_cxx`` wrappers (SanitizerCoverage +
cmplog) and linked *into* the fuzzer (``libmagma_inproc.a`` + ``stub_rt.a``) the
same way fuzzbench's own ``libafl`` integration works. The resulting target
binary IS the fuzzer and runs ``LLVMFuzzerTestOneInput`` in-process via a LibAFL
``InProcessExecutor`` -- no fork/exec/IPC per execution, which removes the
forkserver's throughput bottleneck.
"""

import os
import subprocess

from fuzzers import utils

INPROC_REL = '/lod-sketch/magma-inproc/target/release'

# Per-benchmark LOD grammar map. In-process coverage probing is fragile (a
# grammar skeleton that crashes the target aborts the whole process), so
# --lod-guess is OFF by default here; we pin a known-safe grammar per benchmark
# instead. Override on a single run with the LOD_GRAMMARS env var (space-
# separated), or opt into probing with LIBAFL_LOD_GUESS=1. Benchmarks not listed
# (and not guessed) fall back to the pure byte-mutation baseline.
LOD_GRAMMARS = {
    'libpng_libpng_read_fuzzer': ['png'],
    'libjpeg-turbo_libjpeg_turbo_fuzzer': ['jpeg'],
    'vorbis_decode_fuzzer': ['ogg'],
    'freetype2_ftfuzzer': ['ttf'],
    'libxml2_xml': ['xml'],
}


def build():
    """Build benchmark: instrument + link the target into the in-process fuzzer."""
    os.environ['CC'] = os.path.join(INPROC_REL, 'libafl_cc')
    os.environ['CXX'] = os.path.join(INPROC_REL, 'libafl_cxx')

    # AFL/libafl default build-time sanitizer behavior (don't abort during build).
    os.environ['ASAN_OPTIONS'] = 'abort_on_error=0:allocator_may_return_null=1'
    os.environ['UBSAN_OPTIONS'] = 'abort_on_error=0'

    # --libafl flips the wrappers into "instrument + link the staticlib" mode;
    # without it they behave as a plain clang (for configure-test compiles).
    cflags = ['--libafl']
    cxxflags = ['--libafl', '--std=c++14']
    utils.append_flags('CFLAGS', cflags)
    utils.append_flags('CXXFLAGS', cxxflags)
    utils.append_flags('LDFLAGS', cflags)

    # stub_rt.a carries the process `main` (-> libafl_main); the wrapper appends
    # libmagma_inproc.a after it.
    os.environ['FUZZER_LIB'] = '/stub_rt.a'

    utils.build_benchmark()


def prepare_fuzz_environment(input_corpus):
    """Prepare to fuzz with the in-process LibAFL fuzzer."""
    # libafl's in-process crash handler must catch crashes itself, so the
    # sanitizers must NOT install their own signal handlers.
    os.environ['ASAN_OPTIONS'] = ('abort_on_error=1:detect_leaks=0:'
                                  'malloc_context_size=0:symbolize=0:'
                                  'allocator_may_return_null=1:'
                                  'detect_odr_violation=0:handle_segv=0:'
                                  'handle_sigbus=0:handle_abort=0:'
                                  'handle_sigfpe=0:handle_sigill=0')
    os.environ['UBSAN_OPTIONS'] = ('abort_on_error=1:'
                                   'allocator_release_to_os_interval_ms=500:'
                                   'handle_abort=0:handle_segv=0:'
                                   'handle_sigbus=0:handle_sigfpe=0:'
                                   'handle_sigill=0:print_stacktrace=0:'
                                   'symbolize=0:symbolize_inline_frames=0')
    # The fuzzer needs at least one non-empty seed to start.
    utils.create_seed_file_for_empty_corpus(input_corpus)


def fuzz(input_corpus, output_corpus, target_binary):
    """Run the in-process LOD fuzzer (the target binary IS the fuzzer)."""
    prepare_fuzz_environment(input_corpus)

    # LOD experiment variant. Default to the full LOD pipeline; a "disabled"
    # fuzzer variant runs the pure byte-mutation baseline.
    experiment = os.environ.get('LIBAFL_LOD_EXPERIMENT')
    if experiment is None:
        experiment = ('lod-disable'
                      if 'disabled' in os.environ.get('FUZZER', '') else 'lod')

    # Resolve the grammar list: explicit override, else the per-benchmark map.
    benchmark = os.environ.get('BENCHMARK', '')
    grammars_override = os.environ.get('LOD_GRAMMARS')
    if grammars_override is not None:
        grammars = grammars_override.split()
    else:
        grammars = LOD_GRAMMARS.get(benchmark, [])

    command = [
        target_binary,
        '-o',
        output_corpus,
        '-i',
        input_corpus,
        '--logfile',
        os.path.join(output_corpus, 'libafl.log'),
        '--experiment',
        experiment,
    ]

    # Auto-detect the input format(s) by scoring registered LOD grammar
    # skeletons against the target's coverage. OFF by default in-process: a
    # skeleton that crashes the target aborts the process. Opt in explicitly.
    if os.environ.get('LIBAFL_LOD_GUESS', '0') == '1':
        command += ['--lod-guess']

    for grammar in grammars:
        command += ['--lod', grammar]

    dictionary_path = utils.get_dictionary_path(target_binary)
    if dictionary_path:
        command += ['-x', dictionary_path]

    print('[fuzz] Running command: ' + ' '.join(command))
    subprocess.check_call(command, cwd=os.environ['OUT'])
