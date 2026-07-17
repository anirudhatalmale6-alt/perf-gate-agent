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
    "history": {
        # Record every run into a local DB to track findings + fix-rate over time.
        "enabled": False,
        # Default backend is SQLite (a local file). For Postgres, set the env var
        # PERF_GATE_DB_URL - it is operator-only and cannot be set from the repo.
        "sqlite_path": ".perf-gate/history.db",
        "store_code": True,                # store the offending line locally (never leaves box)
    },
    "jira": {
        # Auto-create tickets for high-severity findings in your on-prem Jira.
        # The base URL + API token are operator-only (env PERF_GATE_JIRA_URL /
        # PERF_GATE_JIRA_TOKEN); they are NEVER read from this file.
        "enabled": False,
        "project_key": "YOUR-PROJECT-KEY",  # placeholder - set to your Jira project key
        "issue_type": "Bug",
        "min_severity": "CRITICAL",         # file tickets at/above this severity
        "labels": ["performance-gate"],
        "include_code": True,               # include the offending line in the ticket body
        "auth": "bearer",                   # bearer (Data Center PAT) | basic
        "timeout": 20,
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

    @property
    def history(self):
        return self.raw["history"]

    @property
    def jira(self):
        return self.raw["jira"]

    @staticmethod
    def db_url() -> str:
        """Postgres connection URL, if any. Operator-only (env), never from repo."""
        return os.environ.get("PERF_GATE_DB_URL", "")

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


# Only these keys may be set from a .env file. Deliberately EXCLUDES anything that
# could redirect where data goes (endpoint host, backend, base URL, tokens) so a
# .env can tune behaviour but never point the tool off-box.
_DOTENV_ALLOWED = {
    "PERF_GATE_MODEL",
    "PERF_GATE_LLM_DISABLED",
    "PERF_GATE_FAIL_ON",
}


def _apply_dotenv(repo_root: str) -> None:
    """Load KEY=VALUE lines from a .env file so settings live in one editable
    file - no command-line flags or manual `export`/`$env:` needed.

    SECURITY: this tool runs on every push over code we do not control, so a .env
    sitting *inside the reviewed repo* must never be trusted - otherwise a repo
    could set the LLM endpoint and quietly exfiltrate the code snippets we send to
    Stage 2. We therefore read .env ONLY from operator-controlled locations:
      1. the agent's own install folder (where you edit perf-gate-agent/.env), and
      2. an explicit path in the real env var PERF_GATE_ENV_FILE (a repo cannot set
         this - it is read from the process environment, not from any .env).
    We never read .env from the reviewed repo or the current working directory, and
    even then only a safe allow-list of keys is honoured (never host/backend/token).
    """
    agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [os.path.join(agent_root, ".env")]
    explicit = os.environ.get("PERF_GATE_ENV_FILE")
    if explicit:
        candidates.append(explicit)
    seen = set()
    for path in candidates:
        real = os.path.abspath(path)
        if real in seen or not os.path.isfile(real):
            continue
        seen.add(real)
        try:
            with open(real, "r") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    if key not in _DOTENV_ALLOWED:
                        continue  # ignore anything that could redirect data off-box
                    # Strip surrounding quotes and any trailing inline comment.
                    val = val.strip()
                    if val and val[0] in ("'", '"') and val[-1:] == val[0]:
                        val = val[1:-1]
                    else:
                        val = val.split(" #", 1)[0].strip()
                    os.environ[key] = val
        except OSError:
            pass


def load(repo_root: str, path: str = "perf-gate.yml") -> Config:
    cfg = dict(DEFAULTS)
    _apply_dotenv(repo_root)
    full = os.path.join(repo_root, path)
    if yaml is not None and os.path.exists(full):
        with open(full, "r") as fh:
            user = yaml.safe_load(fh) or {}
        cfg = _deep_merge(cfg, user)
    # SECURITY: perf-gate.yml is read from the reviewed repo, which we do not
    # control. The LLM endpoint (backend + base_url) decides where the code
    # snippets in Stage 2 are sent, so a repo must NOT be able to set it - that
    # would be a code-exfiltration channel. Force these back to the safe defaults;
    # only the operator can change them, via the env vars handled just below.
    cfg["llm"]["backend"] = DEFAULTS["llm"]["backend"]
    cfg["llm"]["base_url"] = DEFAULTS["llm"]["base_url"]
    # SECURITY: the history DB path is a local file we WRITE to. A scanned repo
    # must not be able to point that write elsewhere (path traversal / clobber),
    # so the SQLite path is operator-only: forced back to the default here, and
    # only an operator env var may override it. The Postgres URL and all Jira
    # endpoint/token values are read from the process env in their own modules and
    # are likewise never taken from this repo-supplied file.
    cfg["history"]["sqlite_path"] = DEFAULTS["history"]["sqlite_path"]
    if os.environ.get("PERF_GATE_DB_PATH"):
        cfg["history"]["sqlite_path"] = os.environ["PERF_GATE_DB_PATH"]
    if os.environ.get("PERF_GATE_HISTORY", "").strip().lower() in ("1", "true", "yes", "on"):
        cfg["history"]["enabled"] = True
    if os.environ.get("PERF_GATE_JIRA", "").strip().lower() in ("1", "true", "yes", "on"):
        cfg["jira"]["enabled"] = True
    # SECURITY: strip any endpoint/credential keys a scanned repo tried to smuggle
    # into the jira config. These are ALWAYS read from the operator environment
    # (PERF_GATE_JIRA_URL / PERF_GATE_JIRA_TOKEN / PERF_GATE_JIRA_USER), so a repo
    # can never point ticket creation at an outside host.
    for _k in ("base_url", "url", "token", "api_token", "host", "user", "password"):
        cfg["jira"].pop(_k, None)
    # Env overrides (now including anything loaded from .env above).
    if os.environ.get("PERF_GATE_BASE_URL"):
        cfg["llm"]["base_url"] = os.environ["PERF_GATE_BASE_URL"]
    if os.environ.get("PERF_GATE_MODEL"):
        cfg["llm"]["model"] = os.environ["PERF_GATE_MODEL"]
    if os.environ.get("PERF_GATE_BACKEND"):
        cfg["llm"]["backend"] = os.environ["PERF_GATE_BACKEND"]
    _disabled = os.environ.get("PERF_GATE_LLM_DISABLED")
    if _disabled is not None and _disabled.strip().lower() in ("1", "true", "yes", "on"):
        cfg["llm"]["enabled"] = False
    if os.environ.get("PERF_GATE_FAIL_ON"):
        cfg["gate"]["fail_on"] = os.environ["PERF_GATE_FAIL_ON"]
    return Config(cfg)
