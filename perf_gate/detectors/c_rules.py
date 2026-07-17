"""Deterministic performance detectors for C (and C-family .h/.cpp/.cc).

Loop-aware: allocations, string ops and I/O are flagged mainly when they sit
inside a loop, using the loop_depth annotation from base.scan_lines.
"""

from __future__ import annotations

import re
from typing import List

from .base import Finding, scan_lines, LineInfo

_RE_ALLOC = re.compile(r"\b(malloc|calloc|realloc)\s*\(")
_RE_STRLEN = re.compile(r"\bstrlen\s*\(")
_RE_STRCAT = re.compile(r"\b(strcat|strcpy|sprintf)\s*\(")
_RE_IO = re.compile(r"\b(fopen|fread|fwrite|fprintf|fscanf|open|read|write)\s*\(")


def analyze(path: str, source: str) -> List[Finding]:
    lines = scan_lines(source, "c")
    findings: List[Finding] = []
    for li in lines:
        s = li.text.strip()
        if not s or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        findings += _per_line(path, li, s)
    return findings


def _per_line(path: str, li: LineInfo, s: str) -> List[Finding]:
    out: List[Finding] = []
    in_loop = li.loop_depth > 0

    # Heap allocation inside a loop.
    if in_loop and _RE_ALLOC.search(s):
        out.append(Finding(
            "c.alloc_in_loop", "Heap allocation inside a loop", "MEMORY", "HIGH",
            path, li.number, s,
            "malloc/calloc/realloc per iteration pays the allocator cost every pass and, if the "
            "matching free() is missed on any path, leaks steadily.",
            "Allocate once before the loop and reuse the buffer (grow it geometrically only when "
            "needed), or use a fixed-size stack buffer when the size is bounded.",
        ))

    # strlen() in a loop condition -> O(n^2) scans of the same string.
    if _RE_STRLEN.search(s) and re.search(r"\bfor\s*\(|\bwhile\s*\(", s):
        out.append(Finding(
            "c.strlen_in_condition", "strlen() in a loop condition (O(n^2))", "ALGORITHMIC", "HIGH",
            path, li.number, s,
            "Calling strlen() in the loop condition rescans the whole string on every iteration, "
            "turning a linear walk into O(n^2).",
            "Compute the length once into a variable before the loop and compare against that.",
        ))

    # strcat/strcpy/sprintf inside a loop (repeated re-scan + overflow risk).
    if in_loop and _RE_STRCAT.search(s):
        out.append(Finding(
            "c.strcat_in_loop", "strcat/sprintf inside a loop", "ALGORITHMIC", "MEDIUM",
            path, li.number, s,
            "strcat walks to the end of the destination every call, so appending in a loop is "
            "O(n^2); sprintf/strcpy in loops also risk buffer overruns.",
            "Track the current end pointer/length and append there (or snprintf with the running "
            "offset); prefer bounded variants (strncat/snprintf).",
        ))

    # Blocking file I/O inside a loop.
    if in_loop and _RE_IO.search(s):
        out.append(Finding(
            "c.io_in_loop", "File I/O inside a loop", "IO_DB", "MEDIUM",
            path, li.number, s,
            "A syscall per iteration (fopen/fread/fwrite/read/write) is dominated by per-call "
            "overhead; opening a file every pass is especially expensive.",
            "Open the file once outside the loop; batch reads/writes into a larger buffer to cut "
            "the number of syscalls.",
        ))

    # Nested loops doing element comparison -> O(n^2).
    if li.loop_depth >= 2 and ("==" in s or "strcmp" in s):
        out.append(Finding(
            "c.nested_loop", "Nested loop comparison (O(n^2))", "ALGORITHMIC", "MEDIUM",
            path, li.number, s,
            "Two nested loops comparing elements is quadratic and degrades sharply as n grows.",
            "Sort once and use a linear merge, or build a hash table for O(1) lookups instead of "
            "the inner scan.",
        ))

    # system() / popen in a loop - process spawn per iteration.
    if in_loop and re.search(r"\b(system|popen)\s*\(", s):
        out.append(Finding(
            "c.process_spawn_in_loop", "Spawning a process inside a loop", "RESOURCE", "HIGH",
            path, li.number, s,
            "system()/popen() fork+exec a whole shell/process each iteration - extremely heavy "
            "compared to doing the work in-process.",
            "Do the work with a library call, or spawn once and stream data to it, instead of "
            "launching a process per item.",
        ))

    return out
