"""Deterministic performance detectors for C# / .NET / EF Core.

Loop-aware like the Java rules: most of these patterns (a query, a concat, a
`new`) only matter inside a loop, so we lean on the loop_depth annotation from
base.scan_lines instead of blind regex matching.
"""

from __future__ import annotations

import re
from typing import List

from .base import Finding, scan_lines, LineInfo

_RE_EF_QUERY = re.compile(
    r"\.(ToList|ToArray|ToDictionary|First|FirstOrDefault|Single|SingleOrDefault|"
    r"Count|Any|Find|FindAsync|Where|Include)\s*\(")
_RE_EF_MATERIALIZE = re.compile(r"\.(ToList|ToArray|First|FirstOrDefault|Single|SingleOrDefault)\s*\(")
_RE_SYNC_OVER_ASYNC = re.compile(r"\.(Result\b|Wait\s*\(\s*\)|GetAwaiter\s*\(\s*\)\s*\.\s*GetResult\s*\(\s*\))")
_RE_STR_CONCAT = re.compile(r"\b(\w+)\s*\+=\s*")
_RE_NEW_REGEX = re.compile(r"new\s+Regex\s*\(")
_RE_NEW_ALLOC = re.compile(r"=\s*new\s+\w")


def analyze(path: str, source: str) -> List[Finding]:
    lines = scan_lines(source, "csharp")
    findings: List[Finding] = []
    for li in lines:
        s = li.text.strip()
        if not s or s.startswith("//") or s.startswith("*") or s.startswith("///"):
            continue
        findings += _per_line(path, li, s)
    findings += _whole_file(path, source)
    return findings


def _per_line(path: str, li: LineInfo, s: str) -> List[Finding]:
    out: List[Finding] = []
    in_loop = li.loop_depth > 0

    # EF Core N+1: a DB materialization / lazy-nav query inside a loop.
    if in_loop and _RE_EF_QUERY.search(s) and (
            "_context" in s or "Context" in s or "db." in s or "_db" in s
            or "Repository" in s or "dbContext" in s.lower() or ".Set<" in s):
        out.append(Finding(
            "cs.ef_n_plus_one", "EF Core query inside a loop (N+1)", "IO_DB", "CRITICAL",
            path, li.number, s,
            "A LINQ query is sent to the database once per iteration. For N rows this is N "
            "round-trips - latency and DB load grow linearly with the input.",
            "Load the data in one set-based query (a single Where(x => ids.Contains(x.Id)) or "
            ".Include(...) for the navigation), then join in memory. Use AsNoTracking() for "
            "read-only paths.",
        ))

    # LINQ chain re-materialized inside a loop (repeated enumeration).
    if in_loop and _RE_EF_MATERIALIZE.search(s) and (".Where(" in s or ".Select(" in s):
        out.append(Finding(
            "cs.linq_materialize_in_loop", "LINQ re-materialized inside a loop", "ALGORITHMIC",
            "HIGH", path, li.number, s,
            "Calling ToList()/First() on a LINQ chain inside a loop re-runs the whole query "
            "(and any DB/IEnumerable work) every iteration.",
            "Materialize once outside the loop, or build a Dictionary/HashSet for O(1) lookups "
            "instead of scanning the sequence each pass.",
        ))

    # Sync-over-async: blocks a thread and can deadlock in an async context.
    if _RE_SYNC_OVER_ASYNC.search(s) and "async" not in s:
        out.append(Finding(
            "cs.sync_over_async", "Blocking on async code (.Result / .Wait / .GetResult)",
            "CONCURRENCY", "HIGH", path, li.number, s,
            "Blocking on a Task with .Result/.Wait()/.GetResult() ties up a thread-pool thread "
            "and can deadlock under a sync-context (ASP.NET). It kills the scalability async "
            "was meant to give.",
            "Make the method async and await the Task all the way up the call chain.",
        ))

    # String concatenation in a loop (strings are immutable in .NET too).
    if in_loop and _RE_STR_CONCAT.search(s) and ('"' in s or "string" in s.lower() or "sql" in s.lower()):
        out.append(Finding(
            "cs.string_concat_loop", "String concatenation in a loop", "MEMORY", "HIGH",
            path, li.number, s,
            "System.String is immutable, so += in a loop allocates a new string and copies the "
            "buffer every iteration - O(n^2) work and heavy GC pressure.",
            "Use a StringBuilder outside the loop and Append() inside it.",
        ))

    # new Regex(...) inside a loop instead of a compiled static field.
    if in_loop and _RE_NEW_REGEX.search(s):
        out.append(Finding(
            "cs.regex_in_loop", "Regex constructed inside a loop", "ALGORITHMIC", "MEDIUM",
            path, li.number, s,
            "Building a Regex per iteration re-parses and re-compiles the pattern each time.",
            "Hoist it to a static readonly Regex (add RegexOptions.Compiled) or use the "
            "GeneratedRegex source generator, and reuse it.",
        ))

    # Object / collection allocation inside a loop.
    if in_loop and _RE_NEW_ALLOC.search(s) and re.search(
            r"new\s+(List<|Dictionary<|HashSet<|StringBuilder|\w+\[)", s):
        out.append(Finding(
            "cs.alloc_in_loop", "Object/collection allocation inside a loop", "MEMORY", "MEDIUM",
            path, li.number, s,
            "A new collection/array is allocated every iteration. If it does not depend on the "
            "loop variable it is wasted allocation and garbage.",
            "Hoist the allocation outside the loop and Clear()/reuse it, or size the collection "
            "once with its expected capacity.",
        ))

    # LINQ .Count() > 0 instead of .Any() (forces a full enumeration).
    if re.search(r"\.Count\s*\(\s*\)\s*[><=!]", s):
        out.append(Finding(
            "cs.count_instead_of_any", "Count() used for an existence check", "ALGORITHMIC", "LOW",
            path, li.number, s,
            "Count() enumerates the entire sequence just to compare against a number; on a DB "
            "query it can force a full COUNT.",
            "Use Any() (or Any(predicate)) - it stops at the first match.",
        ))

    return out


def _whole_file(path: str, source: str) -> List[Finding]:
    out: List[Finding] = []
    lines = source.split("\n")
    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        if not s or s.startswith("//"):
            continue

        # SELECT * in a raw SQL string.
        if re.search(r"SELECT\s+\*", s, re.IGNORECASE) and ('"' in s or "@\"" in s):
            out.append(Finding(
                "cs.select_star", "SELECT * in a query", "IO_DB", "MEDIUM",
                path, i, s,
                "SELECT * pulls every column (including large/unused ones) over the wire and "
                "prevents covering-index-only reads.",
                "Select only the columns you need, or project into a DTO with .Select(x => new {...}).",
            ))

        # EF lazy-loading enabled globally (navigation loads trigger hidden queries).
        if "UseLazyLoadingProxies" in s:
            out.append(Finding(
                "cs.lazy_loading_proxies", "EF lazy-loading proxies enabled", "ORM_METADATA", "MEDIUM",
                path, i, s,
                "Lazy-loading proxies turn every navigation-property access into a separate hidden "
                "query - the classic source of N+1 that never shows up in the code that reads it.",
                "Prefer explicit .Include(...) / projection and load related data deliberately; "
                "keep lazy loading off unless a screen genuinely needs it.",
            ))
    return out
