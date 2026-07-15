"""Deterministic performance detectors for Python."""

from __future__ import annotations

import re
from typing import List

from .base import Finding, scan_lines


def analyze(path: str, source: str) -> List[Finding]:
    lines = scan_lines(source, "python")
    findings: List[Finding] = []
    in_async = _async_line_map(source)

    for li in lines:
        s = li.text.strip()
        if not s or s.startswith("#"):
            continue
        in_loop = li.loop_depth > 0

        # Nested-loop O(n^2): a comparison/membership inside depth>=2 loops.
        if li.loop_depth >= 2 and ("==" in s or " in " in s):
            findings.append(Finding(
                "py.nested_loop", "Nested loop over collections (O(n^2))", "ALGORITHMIC", "HIGH",
                path, li.number, s,
                "Two nested loops comparing items is quadratic; it explodes as the inputs grow.",
                "Index one side into a dict/set keyed on the match field, then do a single "
                "linear pass - O(n) instead of O(n^2).",
            ))

        # String concatenation in a loop.
        if in_loop and re.search(r"\b\w+\s*\+=\s*", s) and ("str(" in s or '"' in s or "'" in s):
            findings.append(Finding(
                "py.string_concat_loop", "String concatenation in a loop", "MEMORY", "MEDIUM",
                path, li.number, s,
                "Building a string with += in a loop reallocates and copies repeatedly.",
                "Collect parts in a list and ''.join(parts) once, or use io.StringIO.",
            ))

        # Reading a whole file into memory.
        if re.search(r"\.read\(\)", s) and "open(" not in s.split(".read")[0][-40:]:
            findings.append(Finding(
                "py.read_whole_file", "Loading an entire file into memory", "MEMORY", "MEDIUM",
                path, li.number, s,
                "f.read() pulls the whole file into RAM; large files blow up memory.",
                "Iterate line by line (for line in f) or read in chunks.",
            ))

        # Blocking I/O inside an async function.
        if in_async.get(li.number) and (re.search(r"\brequests\.(get|post|put|delete)\b", s)
                                        or re.search(r"\btime\.sleep\s*\(", s)):
            findings.append(Finding(
                "py.sync_io_in_async", "Blocking I/O in an async function", "IO_DB", "HIGH",
                path, li.number, s,
                "requests / time.sleep block the event loop, so the whole async server stalls "
                "instead of handling other requests concurrently.",
                "Use an async client (httpx.AsyncClient / aiohttp) and await asyncio.sleep().",
            ))

        # DB connection opened inside a loop.
        if in_loop and re.search(r"\.connect\s*\(", s):
            findings.append(Finding(
                "py.connection_per_iter", "Opening a DB connection inside a loop", "IO_DB", "HIGH",
                path, li.number, s,
                "Connecting per iteration pays the handshake cost every time and can exhaust the "
                "DB's connection limit.",
                "Open one connection (or use a pool) outside the loop and reuse it; batch the "
                "inserts with executemany().",
            ))

    return findings


def _async_line_map(source: str) -> dict:
    """Map each line number to whether it is inside an `async def` body (by indent)."""
    lines = source.split("\n")
    result = {}
    async_indent = None
    for i, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if async_indent is not None and stripped and indent <= async_indent \
                and not stripped.startswith("#"):
            async_indent = None
        if async_indent is not None:
            result[i] = True
        if stripped.startswith("async def "):
            async_indent = indent
    return result
