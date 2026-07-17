"""Auto-create Jira tickets for high-severity findings (on-prem Jira).

Targets **on-prem Jira** (Data Center / Server) via its REST API v2
(`POST {base}/rest/api/2/issue`). One ticket per new finding at/above a severity
threshold (default CRITICAL), de-duplicated so the same finding never opens two
tickets across pushes.

SECURITY / on-prem guarantees:
  * The Jira base URL and API token are **operator-only** - read from the process
    environment (`PERF_GATE_JIRA_URL`, `PERF_GATE_JIRA_TOKEN`), never from the
    scanned repo's `perf-gate.yml`. A reviewed repo therefore cannot point the
    integration at an attacker's server to exfiltrate findings. Same guard the
    LLM endpoint uses.
  * The token is never written to the repo, logs, or the report. Only what you
    already put in a ticket (title, file:line, why, fix, and - if
    `jira.include_code` is on - the offending line) is sent, and only to YOUR
    on-prem Jira.
  * With no URL/token set, the integration runs in **dry-run**: it prints the
    exact tickets it would create so you can validate the flow before wiring
    credentials. Nothing leaves the machine in dry-run.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from .detectors.base import Finding, SEVERITY_ORDER
from .storage import Store, fingerprint

# Placeholder markers so a shipped perf-gate.yml is obviously "fill me in".
_PLACEHOLDERS = {"", "https://jira.example.com", "https://your-jira.example.com",
                 "YOUR-PROJECT-KEY", "PROJ", "REPLACE_ME"}


class JiraConfig:
    def __init__(self, cfg: dict):
        j = cfg or {}
        self.enabled = bool(j.get("enabled", False))
        self.project_key = str(j.get("project_key", "")).strip()
        self.issue_type = str(j.get("issue_type", "Bug")).strip() or "Bug"
        self.min_severity = str(j.get("min_severity", "CRITICAL")).upper().strip()
        self.labels = list(j.get("labels", ["performance-gate"]))
        self.include_code = bool(j.get("include_code", True))
        self.auth_scheme = str(j.get("auth", "bearer")).lower().strip()  # bearer | basic
        self.timeout = int(j.get("timeout", 20))
        # Endpoint + credentials come ONLY from the operator environment, never
        # from the repo's perf-gate.yml (see module docstring).
        self.base_url = (os.environ.get("PERF_GATE_JIRA_URL", "")).rstrip("/")
        self.token = os.environ.get("PERF_GATE_JIRA_TOKEN", "")
        self.basic_user = os.environ.get("PERF_GATE_JIRA_USER", "")

    def credentialed(self) -> bool:
        return bool(self.base_url and self.base_url not in _PLACEHOLDERS
                    and self.project_key and self.project_key not in _PLACEHOLDERS
                    and self.token)


def _summary(f: Finding) -> str:
    return f"[Perf Gate] {f.severity}: {f.title} ({f.file}:{f.line})"[:250]


def _description(f: Finding, include_code: bool) -> str:
    # Jira Server/DC wiki markup. Kept plain so it also reads fine as raw text.
    lines = [
        f"*Performance Gate finding* — auto-filed for a {f.severity} issue.",
        "",
        f"*Rule:* {f.rule_id}",
        f"*Category:* {f.category}",
        f"*Location:* {{{{{f.file}:{f.line}}}}}",
        "",
        f"*Why it matters:* {f.why}",
        f"*Suggested fix:* {f.fix}",
    ]
    if f.llm_explanation:
        lines.append(f"*Reviewer note:* {f.llm_explanation}")
    if include_code and f.code:
        lines += ["", "{code}", f.code[:500], "{code}"]
    lines += ["", "----",
              "_Filed by the on-prem Performance Gate. Nothing here left your "
              "network._"]
    return "\n".join(lines)


def _payload(cfg: JiraConfig, f: Finding) -> dict:
    labels = list(cfg.labels) + [f"sev-{f.severity.lower()}"]
    return {
        "fields": {
            "project": {"key": cfg.project_key},
            "summary": _summary(f),
            "description": _description(f, cfg.include_code),
            "issuetype": {"name": cfg.issue_type},
            "labels": [l.replace(" ", "-") for l in labels],
        }
    }


def _create_issue(cfg: JiraConfig, payload: dict) -> dict:
    url = f"{cfg.base_url}/rest/api/2/issue"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if cfg.auth_scheme == "basic" and cfg.basic_user:
        import base64
        raw = f"{cfg.basic_user}:{cfg.token}".encode("utf-8")
        req.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
    else:  # Jira Data Center Personal Access Token
        req.add_header("Authorization", f"Bearer {cfg.token}")
    with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    key = body.get("key", "")
    return {"key": key, "url": f"{cfg.base_url}/browse/{key}" if key else ""}


def sync_findings(cfg: JiraConfig, findings: List[Finding],
                  store: Optional[Store]) -> dict:
    """Create tickets for findings at/above the threshold, skipping any already
    filed. Returns a summary dict. Never raises on a single ticket failure - it
    records the error and moves on so the gate itself is never blocked by Jira.
    """
    threshold = SEVERITY_ORDER.get(cfg.min_severity, 0)
    eligible = [f for f in findings
                if SEVERITY_ORDER.get(f.severity, 9) <= threshold]
    result = {"eligible": len(eligible), "created": [], "skipped": [],
              "errors": [], "dry_run": not cfg.credentialed()}

    for f in eligible:
        fp = fingerprint(f.rule_id, f.file, f.code)
        existing = store.get_ticket(fp) if store else None
        if existing:
            result["skipped"].append({"fingerprint": fp, "issue_key": existing["issue_key"],
                                      "title": f.title})
            continue
        if result["dry_run"]:
            result["created"].append({"fingerprint": fp, "issue_key": "(dry-run)",
                                      "summary": _summary(f), "payload": _payload(cfg, f)})
            continue
        try:
            issue = _create_issue(cfg, _payload(cfg, f))
            if store and issue["key"]:
                store.save_ticket(fp, issue["key"], issue["url"], f.severity, f.title)
            result["created"].append({"fingerprint": fp, "issue_key": issue["key"],
                                      "url": issue["url"], "summary": _summary(f)})
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as e:
            result["errors"].append({"fingerprint": fp, "title": f.title, "error": str(e)})
    return result


def format_summary(result: dict) -> str:
    if result["dry_run"]:
        head = (f"Jira (dry-run): {len(result['created'])} ticket(s) WOULD be "
                f"created for {result['eligible']} eligible finding(s). "
                f"Set PERF_GATE_JIRA_URL + PERF_GATE_JIRA_TOKEN to file them for real.")
        body = "\n".join(f"  • {c['summary']}" for c in result["created"])
        return head + ("\n" + body if body else "")
    parts = [f"Jira: {len(result['created'])} created, "
             f"{len(result['skipped'])} already open, {len(result['errors'])} error(s)."]
    for c in result["created"]:
        parts.append(f"  • {c['issue_key']}  {c.get('summary','')}")
    for e in result["errors"]:
        parts.append(f"  ! failed: {e['title']} — {e['error']}")
    return "\n".join(parts)
