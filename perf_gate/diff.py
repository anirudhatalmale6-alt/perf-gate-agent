"""Git helpers: figure out which files/lines changed in a push."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, List, Set, Optional

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's canonical empty tree

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _run(args: List[str], cwd: str) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


def resolve_range(repo: str, base: Optional[str], head: Optional[str]) -> (str, str):
    """Return (base_sha, head_sha) to diff. Falls back sensibly on first push."""
    head = head or _run(["git", "rev-parse", "HEAD"], repo).strip()
    if base and base not in ("", "0000000000000000000000000000000000000000"):
        # Verify the base commit actually exists locally (shallow clones may miss it).
        try:
            _run(["git", "cat-file", "-e", base + "^{commit}"], repo)
            return base, head
        except subprocess.CalledProcessError:
            pass
    # Try the previous commit; if there is none (very first commit) use the empty tree.
    try:
        prev = _run(["git", "rev-parse", head + "^"], repo).strip()
        return prev, head
    except subprocess.CalledProcessError:
        return EMPTY_TREE, head


def changed_line_ranges(repo: str, base: str, head: str) -> Dict[str, Set[int]]:
    """Map each changed file to the set of line numbers added/modified on the head side."""
    out = _run(["git", "diff", "--unified=0", "--no-color", base, head], repo)
    ranges: Dict[str, Set[int]] = {}
    current: Optional[str] = None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            if current == "/dev/null":
                current = None
            elif current is not None:
                ranges.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                for n in range(start, start + max(count, 1)):
                    ranges[current].add(n)
    # Drop files that were deleted (no added lines).
    return {f: s for f, s in ranges.items() if s}


def read_file_at(repo: str, head: str, path: str) -> Optional[str]:
    """Read a file's content at the head commit (works even in CI detached state)."""
    try:
        return _run(["git", "show", f"{head}:{path}"], repo)
    except subprocess.CalledProcessError:
        full = os.path.join(repo, path)
        if os.path.exists(full):
            with open(full, "r", errors="replace") as fh:
                return fh.read()
        return None
