"""Core data structures and the loop-scope scanner shared by all detectors."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


@dataclass
class Finding:
    rule_id: str
    title: str
    category: str          # ALGORITHMIC | MEMORY | IO_DB | CONCURRENCY | RESOURCE | NETWORK | ORM_METADATA
    severity: str          # CRITICAL | HIGH | MEDIUM | LOW | INFO
    file: str
    line: int
    code: str              # the offending line (trimmed)
    why: str               # plain-English reason this is a performance risk
    fix: str               # concrete suggested fix
    # Filled in by Stage 2 (LLM). Stage 1 leaves these at defaults.
    confirmed: Optional[bool] = None      # None = not reviewed by LLM yet
    llm_explanation: str = ""
    kb_refs: List[str] = field(default_factory=list)

    def sort_key(self):
        return (SEVERITY_ORDER.get(self.severity, 9), self.file, self.line)

    def to_dict(self):
        return asdict(self)


@dataclass
class LineInfo:
    """One physical source line plus the loop-nesting depth it sits at."""
    number: int            # 1-based line number
    text: str
    loop_depth: int        # how many enclosing for/while loops
    in_synchronized: bool  # inside a synchronized(...) { } block (Java)


def scan_lines(source: str, language: str) -> List[LineInfo]:
    """Annotate each line with its loop-nesting depth.

    Uses brace matching for C-family languages (java/ts/js/c#) and indentation
    for Python. This is a heuristic - good enough to answer the question every
    performance detector actually needs: "is this statement inside a loop?".
    """
    lines = source.split("\n")
    if language == "python":
        return _scan_python(lines)
    return _scan_bracey(lines)


def _scan_bracey(lines: List[str]) -> List[LineInfo]:
    out: List[LineInfo] = []
    # Stack of open scopes; each entry marks whether that scope is a loop and/or
    # a synchronized block. We push on the line that opens a brace and pop when
    # the matching close brace is seen.
    scope_stack: List[dict] = []
    # A loop/synchronized header may span before its "{"; remember what the next
    # opened brace should be tagged as.
    pending = {"loop": False, "sync": False}

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        loop_depth = sum(1 for s in scope_stack if s["loop"])
        in_sync = any(s["sync"] for s in scope_stack)
        out.append(LineInfo(idx, raw, loop_depth, in_sync))

        # Detect headers on this line (they take effect for the block they open).
        header_loop = _starts_loop_bracey(stripped)
        header_sync = stripped.startswith("synchronized") or " synchronized (" in (" " + stripped)
        if header_loop:
            pending["loop"] = True
        if header_sync:
            pending["sync"] = True

        # Walk braces on the line to open/close scopes.
        for ch in raw:
            if ch == "{":
                scope_stack.append({"loop": pending["loop"], "sync": pending["sync"]})
                pending = {"loop": False, "sync": False}
            elif ch == "}":
                if scope_stack:
                    scope_stack.pop()

        # A single-statement loop with no braces (for(...) doSomething();) affects
        # only its own line - already accounted for since we compute loop_depth
        # from the stack BEFORE processing this header, so bump the recorded line
        # if this very line is a brace-less loop body carrier. Handled leniently.
    return out


def _scan_python(lines: List[str]) -> List[LineInfo]:
    out: List[LineInfo] = []
    # Stack of (indent_of_loop_body, kind). A loop introduces a body that is more
    # indented than the `for`/`while` line; everything at >= that indent is inside.
    loop_stack: List[int] = []
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        # Pop loops whose body we've dedented out of.
        if stripped and not raw.lstrip().startswith("#"):
            while loop_stack and indent <= loop_stack[-1]:
                loop_stack.pop()
        out.append(LineInfo(idx, raw, len(loop_stack), False))
        if stripped.startswith("for ") or stripped.startswith("while ") \
                or stripped.startswith("for(") or stripped.startswith("while("):
            loop_stack.append(indent)
    return out


def _starts_loop_bracey(stripped: str) -> bool:
    for kw in ("for", "while", "do"):
        if stripped == kw or stripped.startswith(kw + " ") or stripped.startswith(kw + "("):
            # Avoid matching identifiers like "form" - handled by the char after kw.
            return True
    # Java enhanced-for and streams that iterate are covered by for(.
    return False
