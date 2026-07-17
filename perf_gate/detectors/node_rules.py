"""Performance detectors for Node.js / Next.js / Angular (server + framework side).

Runs on the same .js/.ts/.jsx/.tsx files as the React front-end rules, but looks
at a different layer: blocking the event loop, sequential awaits, N+1 over the
network, and framework data-fetch anti-patterns. Loop-aware via base.scan_lines.
"""

from __future__ import annotations

import re
from typing import List

from .base import Finding, scan_lines, LineInfo

_RE_SYNC_FS = re.compile(r"\b(readFileSync|writeFileSync|readdirSync|existsSync|appendFileSync)\s*\(")
_RE_SYNC_CRYPTO = re.compile(r"\b(pbkdf2Sync|scryptSync|randomBytesSync|bcrypt\.\w*Sync|hashSync|compareSync)\s*\(")
_RE_AWAIT = re.compile(r"\bawait\s+")
_RE_DB_CALL = re.compile(
    r"\b(await\s+)?\w*(query|find|findOne|findAll|findByPk|aggregate|save|insert|update|"
    r"fetch|axios|prisma|knex)\b.*\(")


def analyze(path: str, source: str) -> List[Finding]:
    lines = scan_lines(source, "js")
    findings: List[Finding] = []
    for li in lines:
        s = li.text.strip()
        if not s or s.startswith("//") or s.startswith("*"):
            continue
        findings += _per_line(path, li, s)
    findings += _framework(path, source)
    return findings


def _per_line(path: str, li: LineInfo, s: str) -> List[Finding]:
    out: List[Finding] = []
    in_loop = li.loop_depth > 0

    # Synchronous fs on the request path blocks the single event loop.
    if _RE_SYNC_FS.search(s):
        out.append(Finding(
            "node.sync_fs", "Synchronous fs call blocks the event loop", "IO_DB", "HIGH",
            path, li.number, s,
            "Node handles all requests on one event-loop thread. A *Sync fs call stalls that "
            "thread, so every concurrent request waits behind this one file operation.",
            "Use the async API (await fs.promises.readFile / fs.readFile with a callback) so the "
            "event loop stays free.",
        ))

    # Synchronous crypto/hashing (bcrypt/pbkdf2) blocks the loop under load.
    if _RE_SYNC_CRYPTO.search(s):
        out.append(Finding(
            "node.sync_crypto", "Synchronous crypto/hashing blocks the event loop", "CONCURRENCY",
            "HIGH", path, li.number, s,
            "CPU-heavy sync hashing (bcrypt.hashSync / pbkdf2Sync) blocks the event loop for the "
            "whole duration; under login bursts the server stops serving everyone else.",
            "Use the async variant (await bcrypt.hash / crypto.pbkdf2) so the work runs off the "
            "main thread.",
        ))

    # await inside a for/while loop -> sequential round-trips (should be parallel/batched).
    if in_loop and _RE_AWAIT.search(s) and not s.startswith("for ") and "Promise.all" not in s:
        out.append(Finding(
            "node.await_in_loop", "await inside a loop (sequential round-trips)", "IO_DB", "HIGH",
            path, li.number, s,
            "Awaiting inside a loop runs the calls one after another; N items take N x latency. "
            "When each call also hits a DB/API this is the JS form of an N+1.",
            "Kick the calls off together and await Promise.all([...]) (batch/limit concurrency for "
            "large N), or fetch everything in one query with an IN clause.",
        ))

    # JSON.parse of a synchronous whole-file read.
    if "JSON.parse(" in s and ("readFileSync" in s or "await" not in s and ".read(" in s):
        out.append(Finding(
            "node.json_parse_sync", "Parsing a large payload synchronously", "MEMORY", "MEDIUM",
            path, li.number, s,
            "JSON.parse on a big buffer is synchronous and blocks the event loop while it builds "
            "the whole object graph in memory.",
            "Stream large JSON (stream-json / clarinet) or paginate; avoid loading megabytes into "
            "a single parse on the request path.",
        ))

    return out


def _framework(path: str, source: str) -> List[Finding]:
    """Next.js / Angular template + data-fetch anti-patterns (whole-file heuristics)."""
    out: List[Finding] = []
    lines = source.split("\n")
    lower = path.lower()
    is_angular_tpl = "@component" in source.lower() or "*ngfor" in source.lower()

    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        if not s or s.startswith("//"):
            continue

        # Next.js: fetch without caching hints in a Server Component / route.
        if re.search(r"\bfetch\s*\(", s) and "next" not in s and "cache" not in s and "revalidate" not in s:
            if "getServerSideProps" in source or "async function" in source or "/app/" in lower:
                out.append(Finding(
                    "next.fetch_no_cache", "fetch() without a caching/revalidate policy", "NETWORK",
                    "LOW", path, i, s,
                    "In the Next.js App Router an uncached fetch re-hits the origin on every "
                    "render/request; without revalidate you lose the built-in data cache.",
                    "Pass { next: { revalidate: N } } (or cache: 'force-cache') when the data can "
                    "be reused, so identical requests are served from cache.",
                ))
                break  # one hint per file is enough

    # Angular: *ngFor without trackBy re-creates every DOM node on each change.
    if is_angular_tpl:
        for i, raw in enumerate(lines, start=1):
            s = raw.strip()
            if "*ngFor" in s and "trackBy" not in s:
                out.append(Finding(
                    "ng.ngfor_no_trackby", "*ngFor without trackBy", "MEMORY", "MEDIUM",
                    path, i, s,
                    "Without trackBy, Angular tears down and rebuilds the whole list's DOM on every "
                    "change-detection pass instead of reusing rows - janky on large lists.",
                    "Add trackBy: a function returning a stable id so Angular only touches rows that "
                    "actually changed.",
                ))
                break
        # Function/getter call in a template binding runs every change-detection cycle.
        for i, raw in enumerate(lines, start=1):
            s = raw.strip()
            if re.search(r"\{\{\s*\w+\([^)]*\)\s*\}\}", s):
                out.append(Finding(
                    "ng.method_in_template", "Method call in an Angular template binding", "ALGORITHMIC",
                    "LOW", path, i, s,
                    "A function invoked inside {{ }} re-runs on every change-detection cycle (many "
                    "times a second), even when its inputs never changed.",
                    "Precompute the value into a field, use a pure pipe, or OnPush change detection.",
                ))
                break

    return out
