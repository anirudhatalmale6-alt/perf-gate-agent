"""Deterministic performance detectors for Java / Spring / JPA.

Each detector returns Finding objects. They are intentionally conservative and
loop-aware: many patterns (a query, a string concat, a `new Thread`) are only a
problem when they sit inside a loop, so we use the loop_depth annotation from
base.scan_lines rather than blind regex matching.
"""

from __future__ import annotations

import re
from typing import List

from .base import Finding, scan_lines, LineInfo

_RE_QUERY = re.compile(r"\b(executeQuery|executeUpdate|execute|createStatement|prepareStatement)\s*\(")
_RE_REPO_CALL = re.compile(r"\.(find|findBy|findAll|get|load|save|query|select)\w*\s*\(")
_RE_STR_CONCAT = re.compile(r"\b(\w+)\s*\+=\s*")
_RE_NEW_ALLOC = re.compile(r"=\s*new\s+\w")
_RE_PATTERN_COMPILE = re.compile(r"Pattern\.compile\s*\(")


def analyze(path: str, source: str) -> List[Finding]:
    lines = scan_lines(source, "java")
    findings: List[Finding] = []
    for li in lines:
        s = li.text.strip()
        if not s or s.startswith("//") or s.startswith("*"):
            continue
        findings += _per_line(path, li, s)
    findings += _whole_file(path, source)
    return findings


def _per_line(path: str, li: LineInfo, s: str) -> List[Finding]:
    out: List[Finding] = []
    in_loop = li.loop_depth > 0

    # N+1 queries: a DB call inside a loop.
    if in_loop and (_RE_QUERY.search(s) or (_RE_REPO_CALL.search(s) and "Repository" in s)):
        out.append(Finding(
            "java.n_plus_one", "Database query inside a loop (N+1)", "IO_DB", "CRITICAL",
            path, li.number, s,
            "A query is executed once per iteration. For N items this is N round-trips to "
            "the database - latency and DB load grow linearly with input size.",
            "Fetch in a single set-based query (WHERE id IN (...)), a JOIN, or a JPA batch "
            "fetch / @EntityGraph, then map results in memory.",
        ))

    # String concatenation in a loop (+= on a String).
    if in_loop and _RE_STR_CONCAT.search(s) and ('"' in s or "String" in s or "report" in s or "sql" in s.lower()):
        out.append(Finding(
            "java.string_concat_loop", "String concatenation in a loop", "MEMORY", "HIGH",
            path, li.number, s,
            "Java strings are immutable, so += in a loop allocates a new String and copies "
            "the whole buffer every iteration - O(n^2) work and heavy GC pressure.",
            "Use a StringBuilder outside the loop and append() inside it.",
        ))

    # Object / array allocation inside a loop.
    if in_loop and _RE_NEW_ALLOC.search(s) and "new " in s and "Thread" not in s:
        if re.search(r"new\s+(int|long|double|byte|char|Object|\w+)\s*\[", s) or \
           re.search(r"new\s+(ArrayList|HashMap|HashSet|LinkedList|StringBuilder)\b", s):
            out.append(Finding(
                "java.alloc_in_loop", "Object/array allocation inside a loop", "MEMORY", "MEDIUM",
                path, li.number, s,
                "A new object/array is allocated on every iteration. If it does not depend on "
                "the loop variable it is wasted work and garbage.",
                "Hoist the allocation outside the loop and reuse/clear it, or size the "
                "collection once up front.",
            ))

    # Autoboxing in a loop (adding primitives to a List<Integer>/<Long> etc.).
    if in_loop and re.search(r"(Integer|Long|Double|Boolean|Character)\s+\w+\s*=", s):
        out.append(Finding(
            "java.autoboxing_loop", "Autoboxing inside a loop", "MEDIUM", "MEDIUM",
            path, li.number, s,
            "Boxing a primitive to its wrapper on every iteration allocates an object each "
            "time and adds GC pressure in hot paths.",
            "Work with primitive arrays / IntStream, or a primitive-specialised collection "
            "(e.g. Eclipse Collections / fastutil) instead of List<Integer>.",
        ))

    # new Thread(...) - especially inside a loop = thread-per-item.
    if "new Thread(" in s or "new Thread (" in s:
        sev = "HIGH" if in_loop else "MEDIUM"
        why = ("A new OS thread is started per iteration. Thread creation is expensive and "
               "unbounded threads exhaust memory and cause context-switch thrashing under load."
               if in_loop else
               "Manually spawning raw threads bypasses pooling and back-pressure.")
        out.append(Finding(
            "java.thread_per_item", "Raw thread creation" + (" in a loop" if in_loop else ""),
            "CONCURRENCY", sev, path, li.number, s, why,
            "Submit tasks to a bounded, shared ExecutorService (or a virtual-thread executor) "
            "sized to your resources instead of new Thread().start().",
        ))

    # Pattern.compile inside a loop.
    if in_loop and _RE_PATTERN_COMPILE.search(s):
        out.append(Finding(
            "java.regex_compile_loop", "Regex compiled inside a loop", "ALGORITHMIC", "MEDIUM",
            path, li.number, s,
            "Compiling the same regex on every iteration repeats expensive parsing work.",
            "Compile the Pattern once into a static final field and reuse it.",
        ))

    # Thread.sleep inside a synchronized block.
    if "Thread.sleep" in s and li.in_synchronized:
        out.append(Finding(
            "java.sleep_in_sync", "Thread.sleep inside a synchronized block", "CONCURRENCY", "HIGH",
            path, li.number, s,
            "Sleeping while holding a monitor blocks every other thread waiting on that lock "
            "for the full sleep duration - a throughput killer.",
            "Move the sleep/retry-backoff outside the synchronized region, or use a "
            "ScheduledExecutor so the lock is released while waiting.",
        ))

    # Busy-wait.
    if re.search(r"while\s*\(\s*!.*\)", s) and ("isDone()" in s or "Thread.yield" in s):
        out.append(Finding(
            "java.busy_wait", "Busy-wait / spin loop", "CONCURRENCY", "HIGH",
            path, li.number, s,
            "Spinning on a condition burns a CPU core doing no useful work while it waits.",
            "Block on the result instead - Future.get(), a CountDownLatch, or "
            "CompletableFuture callbacks.",
        ))

    # SELECT * / missing LIMIT hints in embedded SQL.
    if "select *" in s.lower() or "SELECT *" in s:
        out.append(Finding(
            "java.select_star", "SELECT * (over-fetching)", "NETWORK", "MEDIUM",
            path, li.number, s,
            "Selecting all columns fetches and serialises data you do not use, inflating "
            "network, memory and (in JPA) mapping cost.",
            "Select only the columns you need, or use a projection/DTO query.",
        ))

    return out


def _whole_file(path: str, source: str) -> List[Finding]:
    """Structural patterns that are easier to match against the whole file."""
    out: List[Finding] = []
    lines = source.split("\n")

    def line_of(substr_regex):
        for i, l in enumerate(lines, start=1):
            if re.search(substr_regex, l):
                return i, l.strip()
        return None

    # Connection created per call (no pool).
    hit = line_of(r"DriverManager\.getConnection\s*\(")
    if hit:
        out.append(Finding(
            "java.connection_per_request", "New DB connection per request (no pool)", "IO_DB",
            "CRITICAL", path, hit[0], hit[1],
            "Opening a raw JDBC connection per call skips connection pooling. TCP + auth "
            "handshakes dominate latency and the DB runs out of connections under load.",
            "Use a pooled DataSource (HikariCP / Spring's DataSource) and borrow connections "
            "from it.",
        ))

    # Unbounded thread pool.
    hit = line_of(r"Executors\.newCachedThreadPool\s*\(")
    if hit:
        out.append(Finding(
            "java.unbounded_pool", "Unbounded cached thread pool", "CONCURRENCY", "HIGH",
            path, hit[0], hit[1],
            "newCachedThreadPool() has no upper bound on threads. A burst of tasks spawns "
            "unlimited threads and can OOM the JVM.",
            "Use a bounded ThreadPoolExecutor with a fixed max pool size and a bounded queue "
            "plus a sensible rejection policy.",
        ))

    # Double-checked locking without a volatile field. Strip comments first so the
    # word "volatile" inside a code comment does not mask a real missing keyword.
    code_only = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))
    has_volatile_field = bool(re.search(r"\bvolatile\s+\w", code_only))
    if re.search(r"if\s*\(\s*\w+\s*==\s*null\s*\)", code_only) and "synchronized" in code_only \
            and "getInstance" in code_only and not has_volatile_field:
        i, l = line_of(r"static\s+\w+\s+getInstance") or (1, "getInstance()")
        out.append(Finding(
            "java.dcl_no_volatile", "Double-checked locking without volatile", "CONCURRENCY",
            "HIGH", path, i, l,
            "Double-checked locking on a non-volatile field is broken under the Java Memory "
            "Model: another thread can see a partially-constructed instance.",
            "Mark the field volatile, or use the initialization-on-demand holder idiom / an enum "
            "singleton.",
        ))

    # finalize() override.
    hit = line_of(r"protected\s+void\s+finalize\s*\(")
    if hit:
        out.append(Finding(
            "java.finalizer", "finalize() override", "MEMORY", "MEDIUM",
            path, hit[0], hit[1],
            "Finalizers are deprecated and slow: they delay object reclamation across multiple "
            "GC cycles and stall the finalizer thread.",
            "Use try-with-resources / AutoCloseable or java.lang.ref.Cleaner instead.",
        ))

    # synchronized method (coarse lock).
    hit = line_of(r"public\s+synchronized\s+\w")
    if hit:
        out.append(Finding(
            "java.synchronized_method", "Whole method synchronized", "CONCURRENCY", "MEDIUM",
            path, hit[0], hit[1],
            "Synchronizing an entire method (especially one doing I/O) serialises all callers "
            "and holds the lock across slow operations.",
            "Narrow the lock to only the critical section, or use a ConcurrentHashMap / "
            "finer-grained lock.",
        ))

    # Exceptions for flow control (parseInt in a try used as a boolean test).
    if re.search(r"Integer\.parseInt", source) and re.search(r"catch\s*\(\s*NumberFormatException", source):
        i, l = line_of(r"Integer\.parseInt") or (1, "")
        out.append(Finding(
            "java.exception_flow_control", "Exceptions used for control flow", "ALGORITHMIC",
            "LOW", path, i, l,
            "Throwing and catching exceptions to test a normal condition is far slower than a "
            "plain check - stack-trace capture is expensive.",
            "Validate with a cheap check (regex / Character.isDigit loop) instead of catching "
            "an exception.",
        ))

    # JPA metadata: EAGER fetch - the classic 'metadata change that degrades perf'.
    hit = line_of(r"FetchType\.EAGER")
    if hit:
        out.append(Finding(
            "java.eager_fetch", "JPA FetchType.EAGER", "ORM_METADATA", "HIGH",
            path, hit[0], hit[1],
            "EAGER associations are loaded on every query of the owning entity, often via extra "
            "queries or huge joins - a common silent regression introduced by a mapping change.",
            "Prefer FetchType.LAZY and load associations explicitly with a fetch-join or "
            "@EntityGraph only where needed.",
        ))

    # @OneToMany / @ManyToMany without a fetch strategy note (info-level metadata heads-up).
    hit = line_of(r"@(OneToMany|ManyToMany)")
    if hit and "FetchType.LAZY" not in source:
        out.append(Finding(
            "java.collection_mapping", "Collection association mapping", "ORM_METADATA", "INFO",
            path, hit[0], hit[1],
            "Collection associations default to LAZY but are easy to trip into N+1 or unbounded "
            "loading when iterated. Metadata changes here are a frequent perf-regression source.",
            "Ensure iteration over this collection uses a fetch-join / batch size and is "
            "paginated if it can be large.",
        ))

    return out
