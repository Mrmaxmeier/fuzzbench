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
"""Minimal ASan/libFuzzer stacktrace parser for FuzzBench crash processing.

Replaces ClusterFuzz's StackParser with the subset of behavior
needed for clang coverage binaries (ASan, UBSan, libFuzzer timeout/OOM).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

MAX_CRASH_STATE_FRAMES = 3

_ASAN_ERROR_RE = re.compile(
    r'ERROR: (?:HWAddressSanitizer|AddressSanitizer)[: ]*[ ]*'
    r'(?P<type>.*?) on (?:unknown address |address |)(?P<addr>0x[0-9a-fA-F]+)')
_ASAN_ERROR_NO_ADDR_RE = re.compile(
    r'ERROR: (?:HWAddressSanitizer|AddressSanitizer)[: ]*[ ]*(?P<type>[^(:;\n]+)')
_UBSAN_RUNTIME_RE = re.compile(r'runtime error:\s+(.*)')
_UBSAN_SUMMARY_RE = re.compile(
    r'SUMMARY: UndefinedBehaviorSanitizer:\s+(\S+)')
_LIBFUZZER_TIMEOUT_RE = re.compile(r'ERROR:\s*libFuzzer:\s*timeout',
                                   re.IGNORECASE)
_OUT_OF_MEMORY_RE = re.compile(r'out of memory|Out of memory|allocator is '
                               r'trying to allocate', re.IGNORECASE)
_ACCESS_SIZE_RE = re.compile(
    r'^(READ|WRITE)\s+of\s+size\s+(\d+)\s+at\s+0x', re.MULTILINE)
_SEGV_ACCESS_RE = re.compile(
    r'The signal is caused by a (READ|WRITE) memory access')
_FRAME_RE = re.compile(
    r'^\s*#(?P<id>\d+)\s+0x[0-9a-fA-F]+\s+(?:in\s+)?'
    r'(?P<func>.+?)(?:\s+(?P<path>/\S+|\S+\.[a-zA-Z0-9]{1,4}:\d+.*))?$')

# Frames that do not belong in crash_state (ClusterFuzz-compatible subset).
_IGNORE_FUNCS = re.compile(
    r'^(?:abort|exit|raise|tgkill|pthread_kill|main|__assert_|__asan|__sanitizer|'
    r'__libc_start|start_thread|clone|Abort\(|SignalHandler|'
    r'fuzzer::|__Fuzzer::)')


@dataclass
class CrashInfo:
    """Parsed crash information."""
    crash_type: str = ''
    crash_address: str = ''
    crash_state: str = ''
    crash_stacktrace: str = ''
    frame_count: int = 0
    frames: list[str] = field(default_factory=list)


class StackParser:
    """Parse sanitizer/libFuzzer stacktraces into CrashInfo."""

    def __init__(self,
                 symbolized=True,
                 detect_ooms_and_hangs=True,
                 detect_v8_runtime_errors=False,
                 custom_stack_frame_ignore_regexes=None,
                 fuzz_target=None,
                 include_ubsan=True):
        del detect_v8_runtime_errors, custom_stack_frame_ignore_regexes
        self.symbolized = symbolized
        self.detect_ooms_and_hangs = detect_ooms_and_hangs
        self.include_ubsan = include_ubsan
        self.fuzz_target = fuzz_target or 'NULL'

    def parse(self, stacktrace: str) -> CrashInfo:
        """Parse |stacktrace| and return CrashInfo."""
        state = CrashInfo(crash_stacktrace=stacktrace)
        if not stacktrace:
            return state

        if self.detect_ooms_and_hangs:
            if _LIBFUZZER_TIMEOUT_RE.search(stacktrace):
                state.crash_type = 'Timeout'
                state.crash_state = f'{self.fuzz_target}\n'
                return state
            if _OUT_OF_MEMORY_RE.search(stacktrace):
                state.crash_type = 'Out-of-memory'
                state.crash_state = f'{self.fuzz_target}\n'
                return state

        asan_match = _ASAN_ERROR_RE.search(stacktrace)
        if not asan_match:
            asan_match = _ASAN_ERROR_NO_ADDR_RE.search(stacktrace)
            addr = ''
        else:
            addr = asan_match.group('addr')

        if asan_match:
            raw_type = asan_match.group('type').strip()
            # Prefer the first token when leftover prose slipped into the type.
            raw_type = raw_type.split()[0] if raw_type else raw_type
            state.crash_type = _fix_sanitizer_crash_type(raw_type)
            state.crash_address = addr

            access = _ACCESS_SIZE_RE.search(stacktrace)
            if access:
                state.crash_type += f'\n{access.group(1)} {access.group(2)}'
            elif state.crash_type == 'Segv':
                state = _refine_segv(state, stacktrace)

        elif self.include_ubsan and ('runtime error:' in stacktrace or
                                     'UndefinedBehaviorSanitizer' in stacktrace):
            state.crash_type = _parse_ubsan_type(stacktrace)

        state.frames = _extract_state_frames(stacktrace)
        if state.crash_type and not state.frames:
            state.crash_state = f'{self.fuzz_target}\n'
        elif state.frames:
            state.crash_state = '\n'.join(
                state.frames[:MAX_CRASH_STATE_FRAMES]) + '\n'
            state.frame_count = min(len(state.frames), MAX_CRASH_STATE_FRAMES)

        return state


def _fix_sanitizer_crash_type(crash_type: str) -> str:
    return crash_type.lower().replace('_', '-').capitalize()


def _refine_segv(state: CrashInfo, stacktrace: str) -> CrashInfo:
    """Map null SEGV to Null-dereference with access direction when present."""
    try:
        address = int(state.crash_address, 16) if state.crash_address else -1
    except ValueError:
        address = -1
    access = _SEGV_ACCESS_RE.search(stacktrace)
    direction = access.group(1) if access else ''
    if address == 0:
        state.crash_type = 'Null-dereference'
        if direction:
            state.crash_type += f' {direction}'
    elif direction:
        state.crash_type = f'Segv\n{direction}'
    return state


def _parse_ubsan_type(stacktrace: str) -> str:
    summary = _UBSAN_SUMMARY_RE.search(stacktrace)
    if summary and summary.group(1) != 'undefined-behavior':
        return _fix_sanitizer_crash_type(summary.group(1))

    runtime = _UBSAN_RUNTIME_RE.search(stacktrace)
    if not runtime:
        return 'Unknown-crash'
    message = runtime.group(1).lower()
    if 'integer overflow' in message or 'signed integer overflow' in message:
        return 'Integer-overflow'
    if 'divide by zero' in message or 'division by zero' in message:
        return 'Divide-by-zero'
    if 'null pointer' in message:
        return 'Null-dereference'
    return _fix_sanitizer_crash_type(message.split(':')[0].strip()[:40])


def _extract_state_frames(stacktrace: str) -> list[str]:
    """Return filtered crash-state frame labels from a stacktrace."""
    frames = []
    for line in stacktrace.splitlines():
        match = _FRAME_RE.match(line)
        if not match:
            continue
        func = match.group('func').strip()
        path = match.group('path') or ''

        # Drop trailing file/line from func if the regex left them attached.
        if ' /' in func:
            func, path = func.split(' /', 1)
            path = '/' + path

        label = _frame_label(func.strip(), path.strip())
        if not label or _IGNORE_FUNCS.search(label):
            continue
        # Also ignore labels that are still LLVMFuzzer after failed override.
        if label.startswith('LLVMFuzzerTestOneInput'):
            continue
        frames.append(label)
        if len(frames) >= MAX_CRASH_STATE_FRAMES:
            break
    return frames


def _frame_label(func: str, path: str) -> str:
    """Build a crash-state line for one frame."""
    if func.startswith('LLVMFuzzerTestOneInput'):
        if path:
            filename = os.path.basename(path.split(':')[0].replace('\\', '/'))
            if filename:
                return filename
        return ''
    # Prefer bare function name (strip template args already handled upstream).
    return func.split('(')[0].strip()
