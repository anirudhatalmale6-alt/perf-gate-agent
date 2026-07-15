"""Local performance knowledge base with a pure-Python retriever.

Two layers, both fully offline:

1. A built-in, curated rulebook (BUILTIN_KB below) - short, original explanations
   of each performance principle, written for this agent. Always available.

2. An optional index built from YOUR OWN copy of a reference PDF (e.g. the Java
   Performance guide). Because that book is copyrighted, its text is NEVER shipped
   in this repo - you build the index locally with `perf-gate build-kb <pdf>` and
   it is written to a git-ignored file. The retriever merges both layers.

Retrieval is TF-IDF cosine similarity in plain Python - no embedding model, no
network call, no external service. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from typing import List, Dict, Optional


# --- Layer 1: curated, original rulebook (safe to ship) ----------------------
BUILTIN_KB: List[Dict[str, str]] = [
    {"id": "kb.n_plus_one", "topic": "N+1 queries",
     "text": "Issuing one database query per element of a collection multiplies round trips. "
             "Total latency scales with the number of rows. Collapse into a single set-based "
             "query using an IN clause, a JOIN, or a batch/entity-graph fetch, then join in "
             "memory. The fix turns O(n) queries into O(1)."},
    {"id": "kb.connection_pool", "topic": "Connection pooling",
     "text": "Opening a new database connection per request pays TCP setup, authentication and "
             "session initialisation every time, and can exhaust the database's connection "
             "limit under load. A pool keeps warm connections and hands them out cheaply. Size "
             "the pool to the database, not the app's thread count."},
    {"id": "kb.string_builder", "topic": "String building",
     "text": "Immutable strings mean concatenation in a loop reallocates and copies the whole "
             "buffer each iteration, which is quadratic and produces heavy garbage. Accumulate "
             "into a StringBuilder (Java) or a list joined once (Python)."},
    {"id": "kb.thread_pool", "topic": "Thread pools and back-pressure",
     "text": "Creating raw threads per task, or using an unbounded pool, removes back-pressure: "
             "a burst spawns unlimited threads, memory and context-switching costs spike, and "
             "the process can OOM. Use a bounded executor with a fixed maximum and a bounded "
             "queue, and pick a rejection policy for overload."},
    {"id": "kb.locking", "topic": "Lock scope and contention",
     "text": "Holding a lock across slow work - I/O, sleeps, network - serialises every caller "
             "and destroys throughput. Keep critical sections tiny, never block or sleep while "
             "holding a monitor, and prefer concurrent data structures or fine-grained locks "
             "over synchronising whole methods."},
    {"id": "kb.jpa_fetch", "topic": "ORM fetch strategy (metadata)",
     "text": "A mapping/metadata change such as switching an association to EAGER, or iterating a "
             "lazy collection outside a fetch-join, silently adds queries or huge joins to every "
             "read of the entity. These regressions do not show up in the design document - they "
             "appear only in the code. Default to LAZY and fetch explicitly where needed, with "
             "pagination for collections that can grow."},
    {"id": "kb.pagination", "topic": "Pagination and result-set size",
     "text": "Loading an entire table or unbounded result set into memory scales cost with data "
             "volume and risks OOM as the table grows. Always paginate or stream large reads and "
             "select only the columns you use."},
    {"id": "kb.sargable", "topic": "Sargable predicates and indexes",
     "text": "Wrapping an indexed column in a function, or using a leading-wildcard LIKE, makes a "
             "predicate non-sargable so the index cannot be used and the engine scans the table. "
             "Keep the column bare (use range predicates instead of YEAR(col)=), anchor LIKE "
             "patterns, or add a computed/function index."},
    {"id": "kb.busy_wait", "topic": "Busy-waiting",
     "text": "Spinning on a condition burns a CPU core while waiting. Block on the result "
             "instead - a Future, latch, or completion callback - so the core is free for other "
             "work."},
    {"id": "kb.allocation", "topic": "Allocation in hot paths",
     "text": "Allocating objects, arrays or boxed primitives inside a loop adds GC pressure and "
             "cache misses. Hoist invariant allocations out of the loop, reuse buffers, and use "
             "primitive collections/streams to avoid autoboxing."},
    {"id": "kb.async_blocking", "topic": "Blocking an async/event loop",
     "text": "A synchronous network call or sleep inside an async handler blocks the event loop, "
             "stalling every other in-flight request. Use async clients and await non-blocking "
             "sleeps so concurrency is preserved."},
    {"id": "kb.regression_baseline", "topic": "Performance regression testing",
     "text": "Design-time 'performance not required' decisions do not catch degradations "
             "introduced later in code. A cheap microbenchmark (e.g. JMH) or a key-transaction "
             "timing check with a stored baseline, run in CI, gives a mechanical net that flags "
             "a slowdown before it reaches production."},
]

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+")


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 2]


class KnowledgeBase:
    def __init__(self, chunks: List[Dict[str, str]]):
        self.chunks = chunks
        self._doc_tokens = [_tokens(c["text"] + " " + c.get("topic", "")) for c in chunks]
        self._df = Counter()
        for toks in self._doc_tokens:
            for t in set(toks):
                self._df[t] += 1
        self._n = max(len(chunks), 1)
        self._doc_vecs = [self._tfidf(toks) for toks in self._doc_tokens]

    def _idf(self, term: str) -> float:
        return math.log((self._n + 1) / (self._df.get(term, 0) + 1)) + 1.0

    def _tfidf(self, tokens: List[str]) -> Dict[str, float]:
        tf = Counter(tokens)
        vec = {t: (c / len(tokens)) * self._idf(t) for t, c in tf.items()} if tokens else {}
        return vec

    @staticmethod
    def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def retrieve(self, query: str, k: int = 2) -> List[Dict[str, str]]:
        q = self._tfidf(_tokens(query))
        scored = [(self._cosine(q, dv), i) for i, dv in enumerate(self._doc_vecs)]
        scored.sort(reverse=True)
        return [self.chunks[i] for score, i in scored[:k] if score > 0.0]


def load(kb_path: Optional[str] = None) -> KnowledgeBase:
    """Build the KB: always the built-in rulebook, plus a local PDF index if present."""
    chunks = list(BUILTIN_KB)
    if kb_path and os.path.exists(kb_path):
        try:
            with open(kb_path, "r") as fh:
                extra = json.load(fh)
            for c in extra.get("chunks", []):
                if c.get("text"):
                    chunks.append({"id": c.get("id", "pdf"), "topic": c.get("topic", "reference"),
                                   "text": c["text"]})
        except Exception:
            pass
    return KnowledgeBase(chunks)


# --- KB builder: parse a local PDF into a git-ignored index -------------------
def build_from_pdf(pdf_path: str, out_path: str) -> int:
    """Extract text from a local reference PDF and write a chunked index.

    The output file is meant to be git-ignored; we never commit copyrighted text.
    Returns the number of chunks written.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pypdf is required to build the KB: pip install pypdf") from exc

    reader = PdfReader(pdf_path)
    text = []
    for page in reader.pages:
        try:
            text.append(page.extract_text() or "")
        except Exception:
            text.append("")
    full = "\n".join(text)

    # Chunk into ~1200-char windows on paragraph boundaries.
    paras = [p.strip() for p in re.split(r"\n\s*\n", full) if len(p.strip()) > 40]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) > 1200:
            if buf:
                chunks.append(buf.strip())
            buf = p
        else:
            buf += "\n" + p
    if buf.strip():
        chunks.append(buf.strip())

    payload = {"source": os.path.basename(pdf_path),
               "chunks": [{"id": f"pdf.{i}", "topic": "reference", "text": c}
                          for i, c in enumerate(chunks)]}
    with open(out_path, "w") as fh:
        json.dump(payload, fh)
    return len(chunks)
