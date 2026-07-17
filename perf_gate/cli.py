"""Command-line entry point for the Performance Gate agent.

Usage:
  perf-gate review [--repo .] [--base SHA] [--head SHA] [--all] [--json out.json]
  perf-gate build-kb <reference.pdf> [--out .perf-gate/kb-index.json]

`review` is what runs on every push: diff the change, scan the changed lines,
optionally LLM-confirm, print a report, and exit non-zero if the gate trips.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Set

from . import config as config_mod
from . import diff as diff_mod
from . import report as report_mod
from .detectors import analyze_file, SUPPORTED_EXTS, language_for
from .knowledge import kb as kb_mod
from .llm import LLMClient, review as llm_review


def _collect_all_files(repo: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for root, dirs, files in os.walk(repo):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTS:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, repo)
                try:
                    with open(full, "r", errors="replace") as fh:
                        out[rel] = fh.read()
                except Exception:
                    pass
    return out


def cmd_review(args) -> int:
    repo = os.path.abspath(args.repo)
    cfg = config_mod.load(repo)
    # CLI overrides win over perf-gate.yml / env so switching models is one flag.
    if getattr(args, "model", ""):
        cfg.llm["model"] = args.model
    if getattr(args, "no_llm", False):
        cfg.llm["enabled"] = False

    # 1. Determine the review surface (changed lines, or the whole repo with --all).
    sources: Dict[str, str] = {}
    changed: Dict[str, Set[int]] = {}
    if args.all:
        sources = _collect_all_files(repo)
        changed = {f: None for f in sources}  # None = report every line
    else:
        base, head = diff_mod.resolve_range(repo, args.base, args.head)
        changed = diff_mod.changed_line_ranges(repo, base, head)
        for path in list(changed):
            if language_for(path) is None or cfg.is_ignored_path(path):
                changed.pop(path, None)
                continue
            content = diff_mod.read_file_at(repo, head, path)
            if content is None:
                changed.pop(path, None)
            else:
                sources[path] = content

    if not sources:
        print("Performance Gate: no supported source changes to review.")
        report_mod.write_step_summary(report_mod.to_markdown([], False))
        return 0

    # 2. Stage 1 - deterministic detectors, scoped to changed lines.
    findings: List = []
    ignore_rules = set(cfg.gate.get("ignore_rules", []))
    for path, content in sources.items():
        allowed = changed.get(path)
        for f in analyze_file(path, content):
            if f.rule_id in ignore_rules:
                continue
            if allowed is not None and f.line not in allowed:
                continue
            findings.append(f)

    # 3. Stage 2 - local LLM confirmation + KB grounding (optional, graceful).
    llm_used = False
    if not cfg.llm.get("enabled", True):
        print("Stage 2 (local LLM): disabled by config/env - running static rules only.")
    elif findings:
        client = LLMClient(
            backend=cfg.llm.get("backend", "ollama"),
            model=cfg.llm.get("model", "qwen2.5-coder:7b"),
            base_url=cfg.llm.get("base_url") or None,
            timeout=int(cfg.llm.get("timeout", 60)),
        )
        if client.available():
            print(f"Stage 2 (local LLM): using {client.backend} model "
                  f"'{client.model}' at {client.base_url} - confirming "
                  f"{len(findings)} finding(s)...")
            kb = kb_mod.load(os.path.join(repo, cfg.kb_index_path))
            findings = llm_review(findings, sources, kb, client,
                                  max_findings=int(cfg.llm.get("max_findings", 40)))
            llm_used = True
            # Drop findings the model judged clear false positives.
            findings = [f for f in findings if f.confirmed is not False]
        else:
            print(f"Stage 2 (local LLM): {client.backend} not reachable at "
                  f"{client.base_url} - running static rules only. "
                  f"Start it with `ollama serve` and pull the model.")

    # 4. Report.
    markdown = report_mod.to_markdown(findings, llm_used)
    print(markdown)
    report_mod.write_step_summary(markdown)
    report_mod.post_commit_comment(markdown, findings)
    if args.json:
        with open(args.json, "w") as fh:
            fh.write(report_mod.to_json(findings, llm_used))
    if getattr(args, "html", ""):
        with open(args.html, "w") as fh:
            fh.write(report_mod.to_html(findings, llm_used, os.path.basename(repo)))
        print(f"\nHTML report written to {args.html}")

    # 5. Gate decision.
    severities = [f.severity for f in findings]
    if cfg.should_fail(severities):
        tripped = cfg.gate.get("fail_on", "HIGH")
        print(f"\nPerformance Gate: FAILED (found findings at or above {tripped}). "
              f"Recommend performance-lead review before this goes to production.")
        return 1
    print("\nPerformance Gate: passed.")
    return 0


def cmd_build_kb(args) -> int:
    out = args.out or ".perf-gate/kb-index.json"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    n = kb_mod.build_from_pdf(args.pdf, out)
    print(f"Built knowledge-base index with {n} chunks from {args.pdf} -> {out}")
    print("Note: this file contains text from your reference PDF - keep it git-ignored.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="perf-gate", description="Static performance review gate.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="Review changed lines for performance risks.")
    r.add_argument("--repo", default=".", help="Path to the git repo (default: .)")
    r.add_argument("--base", default=os.environ.get("PERF_GATE_BASE", ""), help="Base SHA")
    r.add_argument("--head", default=os.environ.get("PERF_GATE_HEAD", ""), help="Head SHA")
    r.add_argument("--all", action="store_true", help="Scan every file, not just the diff")
    r.add_argument("--json", default="", help="Also write JSON findings to this path")
    r.add_argument("--html", default="", help="Also write a self-contained HTML report to this path")
    r.add_argument("--model", default="", help="Ollama/LLM model to use, e.g. llama3.1:latest "
                                               "(overrides perf-gate.yml and PERF_GATE_MODEL)")
    r.add_argument("--no-llm", action="store_true", help="Skip Stage 2; run static rules only")
    r.set_defaults(func=cmd_review)

    b = sub.add_parser("build-kb", help="Build a local KB index from a reference PDF.")
    b.add_argument("pdf", help="Path to your reference PDF (kept local, never committed)")
    b.add_argument("--out", default="", help="Output index path")
    b.set_defaults(func=cmd_build_kb)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
