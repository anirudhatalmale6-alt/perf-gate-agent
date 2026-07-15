"""Detector registry - maps a file to the right language ruleset."""

from __future__ import annotations

import os
from typing import List

from .base import Finding
from . import java_rules, python_rules, sql_rules, frontend_rules

_EXT_MAP = {
    ".java": java_rules,
    ".py": python_rules,
    ".sql": sql_rules,
    ".ts": frontend_rules,
    ".tsx": frontend_rules,
    ".js": frontend_rules,
    ".jsx": frontend_rules,
}

SUPPORTED_EXTS = set(_EXT_MAP.keys())


def language_for(path: str):
    _, ext = os.path.splitext(path.lower())
    return _EXT_MAP.get(ext)


def analyze_file(path: str, source: str) -> List[Finding]:
    module = language_for(path)
    if module is None:
        return []
    try:
        return module.analyze(path, source)
    except Exception as exc:  # a detector bug must never break the whole run
        return [Finding(
            "engine.detector_error", f"Detector error on {path}", "INFO", "INFO",
            path, 1, "", f"The static analyzer raised: {exc}", "This is a tool issue, not a "
            "code issue - please report it.",
        )]
