"""Render findings as Markdown / JSON, and post to the commit or PR on GitHub."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from collections import Counter
from typing import List

from .detectors.base import Finding

_EMOJI = {"CRITICAL": "\U0001F534", "HIGH": "\U0001F7E0", "MEDIUM": "\U0001F7E1",
          "LOW": "\U0001F535", "INFO": "⚪"}
_CAT_LABEL = {
    "ALGORITHMIC": "Algorithmic", "MEMORY": "Memory", "IO_DB": "I/O & Database",
    "CONCURRENCY": "Concurrency", "RESOURCE": "Resource", "NETWORK": "Network",
    "ORM_METADATA": "ORM / Metadata",
}


def summarize(findings: List[Finding]) -> Counter:
    return Counter(f.severity for f in findings)


def to_markdown(findings: List[Finding], llm_used: bool) -> str:
    if not findings:
        return ("## Performance Gate\n\n✅ No performance risks found in the changed lines.\n")

    counts = summarize(findings)
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    badges = "  ".join(f"{_EMOJI[s]} {counts[s]} {s.title()}" for s in order if counts.get(s))

    lines = ["## Performance Gate", "",
             f"Reviewed the changed lines and found {len(findings)} performance "
             f"{'risk' if len(findings) == 1 else 'risks'}.", "",
             badges, ""]
    engine = ("Two-stage: static rules + local LLM confirmation" if llm_used
              else "Static rules only (local LLM not reachable - findings not LLM-confirmed)")
    lines.append(f"_Engine: {engine}. Everything ran on-prem; no code left the network._")
    lines.append("")

    for f in sorted(findings, key=lambda x: x.sort_key()):
        tag = "" if f.confirmed is None else (" ✓ confirmed" if f.confirmed else " ⚠ possible false positive")
        lines.append(f"### {_EMOJI.get(f.severity, '')} {f.severity} — {f.title}{tag}")
        lines.append(f"- File: `{f.file}:{f.line}`")
        lines.append(f"- Category: {_CAT_LABEL.get(f.category, f.category)}  |  Rule: `{f.rule_id}`")
        if f.code:
            lines.append(f"- Code: `{f.code[:200]}`")
        lines.append(f"- Why: {f.why}")
        explanation = f.llm_explanation or ""
        if explanation:
            lines.append(f"- Reviewer: {explanation}")
        lines.append(f"- Fix: {f.fix}")
        if f.kb_refs:
            lines.append(f"- Reference: {', '.join(f.kb_refs)}")
        lines.append("")
    return "\n".join(lines)


def to_json(findings: List[Finding], llm_used: bool) -> str:
    return json.dumps({
        "engine": "static+llm" if llm_used else "static",
        "total": len(findings),
        "by_severity": dict(summarize(findings)),
        "findings": [f.to_dict() for f in findings],
    }, indent=2)


def write_step_summary(markdown: str) -> None:
    """Append to the GitHub Actions job summary if running in Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(markdown + "\n")


def post_commit_comment(markdown: str, findings: List[Finding]) -> bool:
    """Post the report as a commit comment via the GitHub API (self-hosted or cloud runner).

    Uses GITHUB_TOKEN + GITHUB_REPOSITORY + GITHUB_SHA that Actions provides. Only
    metadata (the report text) is sent to the GitHub API you already push code to;
    no source leaves anywhere it is not already. Silently no-ops off CI.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("GITHUB_SHA")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not (token and repo and sha):
        return False
    url = f"{api}/repos/{repo}/commits/{sha}/comments"
    body = json.dumps({"body": markdown}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False
