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
"""Tests for stack_parser.py."""

from common import stack_parser


def _parse(stacktrace, fuzz_target='fuzz-target'):
    return stack_parser.StackParser(
        fuzz_target=fuzz_target,
        symbolized=True,
        detect_ooms_and_hangs=True,
        include_ubsan=True).parse(stacktrace)


def test_parse_abrt():
    """ABRT ignores raise/abort and uses the LLVMFuzzer source file."""
    stacktrace = """
==1==ERROR: AddressSanitizer: ABRT on unknown address 0x000000000001 (pc 0x7f)
    #0 0x7f in raise
    #1 0x7f in abort
    #2 0x4 in LLVMFuzzerTestOneInput /src/fuzz_target.c:10:3
    #3 0x5 in main

SUMMARY: AddressSanitizer: ABRT
"""
    result = _parse(stacktrace)
    assert result.crash_type == 'Abrt'
    assert result.crash_address == '0x000000000001'
    assert result.crash_state == 'fuzz_target.c\n'


def test_parse_heap_buffer_overflow():
    """Heap-buffer-overflow includes access size and top frames."""
    stacktrace = """
==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000010
READ of size 4 at 0x602000000010 thread T0
    #0 0x4 in foo /src/a.cc:1:1
    #1 0x5 in bar /src/b.cc:2:2
    #2 0x6 in LLVMFuzzerTestOneInput /src/target.cc:3:3

SUMMARY: AddressSanitizer: heap-buffer-overflow /src/a.cc:1:1 in foo
"""
    result = _parse(stacktrace)
    assert result.crash_type == 'Heap-buffer-overflow\nREAD 4'
    assert result.crash_address == '0x602000000010'
    assert result.crash_state == 'foo\nbar\ntarget.cc\n'


def test_parse_null_segv():
    """Null SEGV becomes Null-dereference with access direction."""
    stacktrace = """
==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000
==1==The signal is caused by a READ memory access.
    #0 0x4 in crashy /src/x.c:5:1
    #1 0x5 in LLVMFuzzerTestOneInput /src/x.c:10:1

SUMMARY: AddressSanitizer: SEGV /src/x.c:5:1 in crashy
"""
    result = _parse(stacktrace)
    assert result.crash_type == 'Null-dereference READ'
    assert result.crash_state == 'crashy\nx.c\n'


def test_parse_ubsan_integer_overflow():
    """UBSan integer overflow."""
    stacktrace = """
/src/foo.cc:12:5: runtime error: signed integer overflow: 2147483647 + 1
    #0 0x4 in add /src/foo.cc:12:5
    #1 0x5 in LLVMFuzzerTestOneInput /src/foo.cc:20:3

SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior /src/foo.cc:12:5
"""
    result = _parse(stacktrace)
    assert result.crash_type == 'Integer-overflow'
    assert result.crash_state == 'add\nfoo.cc\n'


def test_parse_timeout_and_oom():
    """libFuzzer timeout and ASan OOM."""
    timeout = _parse('==1== ERROR: libFuzzer: timeout after 5 seconds\n')
    assert timeout.crash_type == 'Timeout'
    assert timeout.crash_state == 'fuzz-target\n'

    oom = _parse('ERROR: AddressSanitizer: out of memory: allocator is '
                 'trying to allocate 0x100 bytes\n')
    assert oom.crash_type == 'Out-of-memory'
