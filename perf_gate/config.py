"""Configuration loading and the release-gate policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:
    import yaml
except ImportError:
    yaml = None

DEFAULTS = {
    "llm": {
        "enabled": True,
        "backend": "ollama",              # ollama | openai   (openai = vLLM / Azure private endpoint)
        "model": "qwen2.5-coder:7b",
        "base_url": "",                    # blank = backend default (localhost)
        "timeout": 60,
        "max_findings": 40,
    },
    "knowledge_base": {
        "index_path": ".perf-gate/kb-index.json",   # git-ignored local PDF index (optional)
    },
    "gate": {
        # Fail the build (non-zero exit) if any finding is at or above this severity.
        "fail_on": "HIGH",                 # CRITICAL | HIGH | MEDIUM | LOW | INFO | NONE
        "ignore_rules": [],                # rule_ids to silence, e.g. ["fe.inline_function"]
        "ignore_paths": ["test/", "tests/", "generated/"],
    },
}

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4, "NONE": 99}


@dataclass
class Config:
    raw: dict = field(default_factory=lambda: dict(DEFAULTS))

    @property
    def llm(self):
        return self.raw["llm"]

    @property
    def gate(self):
        return self.raw["gate"]

    @property
    def kb_index_path(self):
        return self.raw["knowledge_base"]["index_path"]

    def should_fail(self, severities: List[str]) -> bool:
        threshold = _SEVERITY_RANK.get(str(self.gate.get("fail_on", "HIGH")).upper(), 1)
        if threshold == 99:
            return False
        return any(_SEVERITY_RANK.get(s, 4) <= threshold for s in severities)

    def is_ignored_path(self, path: str) -> bool:
        return any(seg in path for seg in self.gate.get("ignore_paths", []))


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(repo_root: str, path: str = "perf-gate.yml") -> Config:
    cfg = dict(DEFAULTS)
    full = os.path.join(repo_root, path)
    if yaml is not None and os.path.exists(full):
        with open(full, "r") as fh:
            user = yaml.safe_load(fh) or {}
        cfg = _deep_merge(cfg, user)
    # Env overrides so CI can flip things without editing files.
    if os.environ.get("PERF_GATE_MODEL"):
        cfg["llm"]["model"] = os.environ["PERF_GATE_MODEL"]
    if os.environ.get("PERF_GATE_BACKEND"):
        cfg["llm"]["backend"] = os.environ["PERF_GATE_BACKEND"]
    if os.environ.get("PERF_GATE_LLM_DISABLED"):
        cfg["llm"]["enabled"] = False
    if os.environ.get("PERF_GATE_FAIL_ON"):
        cfg["gate"]["fail_on"] = os.environ["PERF_GATE_FAIL_ON"]
    return Config(cfg)
