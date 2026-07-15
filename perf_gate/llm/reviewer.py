"""Stage 2: use the local LLM to confirm and explain each candidate finding.

The model is given ONE small, focused task at a time - a single flagged snippet
plus the retrieved knowledge-base passages - which is exactly what a small local
model does well, and keeps every prompt tiny and fast. It returns a strict JSON
verdict. If the model is unreachable or replies with junk, the finding is kept
as-is (Stage 1 stands on its own).
"""

from __future__ import annotations

import json
from typing import List

from .client import LLMClient, LLMUnavailable
from ..detectors.base import Finding
from ..knowledge.kb import KnowledgeBase

SYSTEM = (
    "You are a senior performance engineer reviewing a single code snippet that a static "
    "analyzer flagged as a possible performance risk. Decide if it is a real performance "
    "concern in a production system under load. Use the provided reference notes. "
    "Respond ONLY with a JSON object of the form: "
    '{"confirmed": true|false, "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO", '
    '"explanation": "one or two sentences", "fix": "one concrete sentence"}. '
    "Set confirmed=false only if it is clearly a false positive."
)


def _context_window(source: str, line: int, radius: int = 6) -> str:
    lines = source.split("\n")
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line + radius)
    numbered = []
    for i in range(lo, hi):
        marker = ">>" if (i + 1) == line else "  "
        numbered.append(f"{marker} {i + 1}: {lines[i]}")
    return "\n".join(numbered)


def review(findings: List[Finding], sources: dict, kb: KnowledgeBase,
           client: LLMClient, max_findings: int = 40) -> List[Finding]:
    if not findings:
        return findings
    if not client.available():
        # Graceful fallback - Stage 1 findings pass through unmodified.
        return findings

    for f in findings[:max_findings]:
        source = sources.get(f.file, "")
        query = f"{f.title} {f.category} {f.why}"
        refs = kb.retrieve(query, k=2)
        f.kb_refs = [r["topic"] for r in refs]
        ref_text = "\n".join(f"- {r['topic']}: {r['text']}" for r in refs)
        snippet = _context_window(source, f.line)
        user = (
            f"Reference notes:\n{ref_text}\n\n"
            f"Static rule: {f.rule_id} - {f.title}\n"
            f"File: {f.file} (focus line marked >>)\n"
            f"```\n{snippet}\n```\n\n"
            "Is this a real performance risk? Reply with the JSON verdict."
        )
        try:
            raw = client.complete(SYSTEM, user)
            verdict = _parse(raw)
        except LLMUnavailable:
            break  # backend died mid-run; keep the rest as Stage-1 findings
        except Exception:
            continue
        if verdict is None:
            continue
        f.confirmed = bool(verdict.get("confirmed", True))
        f.llm_explanation = str(verdict.get("explanation", "")).strip()
        sev = str(verdict.get("severity", "")).upper()
        if sev in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}:
            f.severity = sev
        fix = str(verdict.get("fix", "")).strip()
        if fix:
            f.fix = fix
    return findings


def _parse(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose/code fences - grab the first {...} block.
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None
