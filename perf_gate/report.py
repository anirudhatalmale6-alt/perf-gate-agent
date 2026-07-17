"""Render findings as Markdown / JSON, and post to the commit or PR on GitHub."""

from __future__ import annotations

import html
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


_HTML_SEV_COLOR = {
    "CRITICAL": "#b3141d", "HIGH": "#d9640a", "MEDIUM": "#c99a06",
    "LOW": "#2563eb", "INFO": "#6b7280",
}


def to_html(findings: List[Finding], llm_used: bool, repo_name: str = "") -> str:
    """A single self-contained HTML report for stakeholders (no external assets).

    Open it in a browser, email it, or attach it to a ticket. All CSS is inline in
    a <style> block so the file works offline with nothing to fetch."""
    counts = summarize(findings)
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    engine = ("Two-stage: static rules + local LLM confirmation" if llm_used
              else "Static rules only (local LLM not reachable - not LLM-confirmed)")
    title = "Performance Gate report" + (f" - {repo_name}" if repo_name else "")

    cards = []
    for sev in order:
        n = counts.get(sev, 0)
        color = _HTML_SEV_COLOR[sev]
        dim = "" if n else "opacity:.35;"
        cards.append(
            f'<div class="card" style="border-top:4px solid {color};{dim}">'
            f'<div class="num">{n}</div><div class="lbl">{sev.title()}</div></div>')

    rows = []
    for f in sorted(findings, key=lambda x: x.sort_key()):
        color = _HTML_SEV_COLOR.get(f.severity, "#6b7280")
        conf = ("" if f.confirmed is None
                else ('<span class="chip ok">confirmed</span>' if f.confirmed
                      else '<span class="chip warn">possible FP</span>'))
        explanation = (f'<div class="rev"><b>Reviewer:</b> {html.escape(f.llm_explanation)}</div>'
                       if f.llm_explanation else "")
        refs = (f'<div class="ref"><b>Reference:</b> {html.escape(", ".join(f.kb_refs))}</div>'
                if f.kb_refs else "")
        code = (f'<pre class="code">{html.escape(f.code[:400])}</pre>' if f.code else "")
        rows.append(f"""
        <div class="finding">
          <div class="fhead">
            <span class="sev" style="background:{color}">{f.severity}</span>
            <span class="ftitle">{html.escape(f.title)}</span>{conf}
          </div>
          <div class="meta">
            <span class="loc">{html.escape(f.file)}:{f.line}</span>
            <span class="cat">{_CAT_LABEL.get(f.category, f.category)}</span>
            <span class="rule">{html.escape(f.rule_id)}</span>
          </div>
          {code}
          <div class="why"><b>Why it matters:</b> {html.escape(f.why)}</div>
          {explanation}
          <div class="fix"><b>Suggested fix:</b> {html.escape(f.fix)}</div>
          {refs}
        </div>""")

    body_findings = "\n".join(rows) if rows else (
        '<div class="empty">No performance risks found in the reviewed changes.</div>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         margin:0; background:#f4f5f7; color:#1a1a1a; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:#555; font-size:13px; margin-bottom:20px; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:8px; }}
  .card {{ background:#fff; border-radius:8px; padding:14px 18px; min-width:96px;
          box-shadow:0 1px 3px rgba(0,0,0,.08); text-align:center; }}
  .num {{ font-size:26px; font-weight:700; }}
  .lbl {{ font-size:12px; color:#666; text-transform:uppercase; letter-spacing:.04em; }}
  .engine {{ font-size:12px; color:#666; margin:14px 0 24px; padding:8px 12px;
            background:#eef0f3; border-radius:6px; }}
  .finding {{ background:#fff; border-radius:8px; padding:16px 18px; margin-bottom:14px;
             box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .fhead {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .sev {{ color:#fff; font-size:11px; font-weight:700; padding:3px 8px; border-radius:4px;
         letter-spacing:.03em; }}
  .ftitle {{ font-weight:600; font-size:15px; }}
  .chip {{ font-size:11px; padding:2px 7px; border-radius:10px; }}
  .chip.ok {{ background:#e4f4e7; color:#1c7c33; }}
  .chip.warn {{ background:#fdf0d8; color:#9a6b04; }}
  .meta {{ font-size:12px; color:#555; margin:8px 0; display:flex; gap:14px; flex-wrap:wrap; }}
  .meta .loc {{ font-family:ui-monospace,Menlo,Consolas,monospace; color:#222; }}
  .code {{ background:#1e2430; color:#e6edf3; padding:10px 12px; border-radius:6px;
          overflow-x:auto; font-size:12.5px; margin:8px 0; white-space:pre-wrap;
          word-break:break-word; }}
  .why,.fix,.rev,.ref {{ font-size:13.5px; margin:6px 0; line-height:1.5; }}
  .fix {{ color:#1c5e2a; }}
  .empty {{ background:#fff; border-radius:8px; padding:40px; text-align:center;
           color:#1c7c33; font-size:16px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .foot {{ margin-top:26px; font-size:11.5px; color:#888; text-align:center; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#0d1117; color:#e6edf3; }}
    .card,.finding,.empty {{ background:#161b22; box-shadow:none; border:1px solid #232a33; }}
    .lbl,.sub,.meta,.engine {{ color:#9aa4b0; }}
    .engine {{ background:#161b22; }}
    .meta .loc {{ color:#e6edf3; }}
    .fix {{ color:#5fd97e; }}
  }}
</style></head><body><div class="wrap">
  <h1>{html.escape(title)}</h1>
  <div class="sub">{len(findings)} finding{'' if len(findings)==1 else 's'} in the reviewed changes.</div>
  <div class="cards">{''.join(cards)}</div>
  <div class="engine">Engine: {engine}. Everything ran on-prem; no code left the network.</div>
  {body_findings}
  <div class="foot">Generated by Performance Gate - static, on-prem performance review.</div>
</div></body></html>"""


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
