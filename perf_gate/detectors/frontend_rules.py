"""Lightweight performance detectors for React / TypeScript front-end files."""

from __future__ import annotations

import re
from typing import List

from .base import Finding


def analyze(path: str, source: str) -> List[Finding]:
    findings: List[Finding] = []
    lines = source.split("\n")
    has_use_effect = "useEffect" in source

    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        if not s or s.startswith("//"):
            continue

        # setInterval / addEventListener with no cleanup in the file = leak.
        if ("setInterval(" in s or "addEventListener(" in s) and has_use_effect \
                and "removeEventListener" not in source and "clearInterval" not in source:
            findings.append(Finding(
                "fe.missing_cleanup", "Timer/listener without cleanup", "MEMORY", "HIGH",
                path, i, s,
                "A setInterval / event listener registered in an effect with no cleanup keeps "
                "firing after unmount - a classic React memory leak.",
                "Return a cleanup function from useEffect that clears the interval / removes the "
                "listener.",
            ))

        # Inline arrow/object created in JSX props on every render.
        if re.search(r"=\{\s*\(\)\s*=>", s) or re.search(r"onClick=\{\s*\(\)\s*=>", s):
            findings.append(Finding(
                "fe.inline_function", "Inline function created in render", "ALGORITHMIC", "LOW",
                path, i, s,
                "A new function identity every render breaks memoization of child components and "
                "can cause avoidable re-renders in large lists.",
                "Hoist the handler with useCallback (or define it outside the component).",
            ))

        # Rendering a large list without virtualization (heuristic: .map inside JSX).
        if re.search(r"\.map\s*\(", s) and "return" in "".join(lines[max(0, i - 3):i]).lower():
            # Only flag as INFO to avoid noise.
            findings.append(Finding(
                "fe.list_no_virtualization", "List render (check virtualization)", "MEMORY", "INFO",
                path, i, s,
                "Mapping a potentially large array straight to DOM nodes renders everything at "
                "once; big lists cause jank and high memory.",
                "For long lists use windowing (react-window / react-virtualized) so only visible "
                "rows mount.",
            ))
    # De-duplicate the map heuristic (keep at most one per file).
    seen_map = False
    deduped: List[Finding] = []
    for f in findings:
        if f.rule_id == "fe.list_no_virtualization":
            if seen_map:
                continue
            seen_map = True
        deduped.append(f)
    return deduped
