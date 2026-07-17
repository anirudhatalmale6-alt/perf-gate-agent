"""Tests for the added language detectors (C#, C, Node/Next/Angular) and the
HTML report renderer."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perf_gate.detectors import analyze_file
from perf_gate import report


def _rules(path, src):
    return {f.rule_id for f in analyze_file(path, src)}


# ---------------------------------------------------------------- C# / .NET

def test_csharp_ef_n_plus_one_and_concat():
    src = (
        "public class S {\n"
        "  public void Run(List<int> ids) {\n"
        "    var report = \"\";\n"
        "    foreach (var id in ids) {\n"
        "      var o = _context.Orders.Where(x => x.Id == id).FirstOrDefault();\n"
        "      report += o.Name + \";\";\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    rules = _rules("S.cs", src)
    assert "cs.ef_n_plus_one" in rules
    assert "cs.string_concat_loop" in rules


def test_csharp_sync_over_async():
    src = "class S { void M() { var u = GetUserAsync(1).Result; } }\n"
    assert "cs.sync_over_async" in _rules("S.cs", src)


# ---------------------------------------------------------------- C

def test_c_strlen_and_alloc_in_loop():
    src = (
        "void f(char** items, int n) {\n"
        "  for (int i = 0; i < strlen(dest); i++) {\n"
        "    char* t = malloc(64);\n"
        "    strcat(buf, items[i]);\n"
        "  }\n"
        "}\n"
    )
    rules = _rules("f.c", src)
    assert "c.strlen_in_condition" in rules
    assert "c.alloc_in_loop" in rules
    assert "c.strcat_in_loop" in rules


# ---------------------------------------------------------------- Node / TS

def test_node_sync_fs_and_await_in_loop():
    src = (
        "import fs from 'fs';\n"
        "export async function h(ids) {\n"
        "  const c = fs.readFileSync('./c.json');\n"
        "  for (const id of ids) {\n"
        "    const r = await db.query('SELECT * FROM t WHERE id=' + id);\n"
        "  }\n"
        "  const hash = bcrypt.hashSync(pw, 10);\n"
        "}\n"
    )
    rules = _rules("api.ts", src)
    assert "node.sync_fs" in rules
    assert "node.await_in_loop" in rules
    assert "node.sync_crypto" in rules


def test_angular_ngfor_without_trackby():
    src = (
        "@Component({ template: `\n"
        "  <li *ngFor=\"let item of items\">{{ item.name }}</li>\n"
        "` })\n"
        "export class C {}\n"
    )
    assert "ng.ngfor_no_trackby" in _rules("c.component.ts", src)


def test_ts_still_runs_react_rules():
    """A .tsx file must still be scanned by the React front-end detectors too."""
    src = (
        "function C() {\n"
        "  useEffect(() => { setInterval(tick, 1000); }, []);\n"
        "  return <div/>;\n"
        "}\n"
    )
    assert "fe.missing_cleanup" in _rules("C.tsx", src)


# ---------------------------------------------------------------- HTML report

def test_html_report_is_self_contained():
    from perf_gate.detectors.base import Finding
    findings = [Finding("cs.ef_n_plus_one", "EF N+1", "IO_DB", "CRITICAL",
                        "S.cs", 5, "x", "why", "fix")]
    out = report.to_html(findings, llm_used=False, repo_name="demo")
    assert out.startswith("<!DOCTYPE html>")
    assert "EF N+1" in out
    assert "<script" not in out          # no external/inline JS
    assert "http://" not in out and "https://" not in out  # no external asset fetches


def test_html_report_escapes_content():
    from perf_gate.detectors.base import Finding
    findings = [Finding("x", "<script>alert(1)</script>", "MEMORY", "LOW",
                        "a.ts", 1, "<b>", "why", "fix")]
    out = report.to_html(findings, llm_used=True)
    assert "<script>alert(1)</script>" not in out   # must be escaped
    assert "&lt;script&gt;" in out
