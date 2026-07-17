"""Detector registry - maps a file to the right language ruleset(s).

An extension can map to more than one module (e.g. a .ts file is scanned by both
the React front-end rules and the Node/Next/Angular rules); every module's
findings are merged.
"""

from __future__ import annotations

import os
from typing import List

from .base import Finding
from . import (java_rules, python_rules, sql_rules, frontend_rules,
               csharp_rules, c_rules, node_rules)

# Each extension maps to a tuple of detector modules (run in order, results merged).
_EXT_MAP = {
    ".java": (java_rules,),
    ".py": (python_rules,),
    ".sql": (sql_rules,),
    ".cs": (csharp_rules,),
    ".c": (c_rules,),
    ".h": (c_rules,),
    ".cpp": (c_rules,),
    ".cc": (c_rules,),
    ".hpp": (c_rules,),
    # JS/TS ecosystem: React front-end rules + Node/Next/Angular server rules.
    ".ts": (frontend_rules, node_rules),
    ".tsx": (frontend_rules, node_rules),
    ".js": (frontend_rules, node_rules),
    ".jsx": (frontend_rules, node_rules),
    ".mjs": (frontend_rules, node_rules),
    ".cjs": (frontend_rules, node_rules),
}

SUPPORTED_EXTS = set(_EXT_MAP.keys())


def modules_for(path: str):
    _, ext = os.path.splitext(path.lower())
    return _EXT_MAP.get(ext, ())


# Back-compat: some callers/tests still ask for a single module.
def language_for(path: str):
    mods = modules_for(path)
    return mods[0] if mods else None


def analyze_file(path: str, source: str) -> List[Finding]:
    findings: List[Finding] = []
    for module in modules_for(path):
        try:
            findings += module.analyze(path, source)
        except Exception as exc:  # a detector bug must never break the whole run
            findings.append(Finding(
                "engine.detector_error", f"Detector error on {path}", "INFO", "INFO",
                path, 1, "", f"The static analyzer raised: {exc}", "This is a tool issue, not a "
                "code issue - please report it.",
            ))
    return findings
