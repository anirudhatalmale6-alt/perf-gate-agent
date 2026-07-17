"""Tests for the history DB (fix-rate trends) and the Jira integration."""

import os

from perf_gate.detectors.base import Finding
from perf_gate.storage import Store, fingerprint
from perf_gate import jira_client
from perf_gate import config as config_mod


def _finding(rule_id, sev, file="a.py", line=1, code="x = slow()"):
    return Finding(rule_id=rule_id, title=f"t-{rule_id}", category="ALGORITHMIC",
                   severity=sev, file=file, line=line, code=code,
                   why="because", fix="do better")


def _store(tmp_path):
    return Store(sqlite_path=str(tmp_path / "h.db"))


def test_fingerprint_stable_across_line_moves():
    # Same rule + file + code -> same fingerprint even if the line number changed.
    a = fingerprint("py.n_plus_one", "svc.py", "  user = db.get(id)  ")
    b = fingerprint("py.n_plus_one", "svc.py", "user = db.get(id)")
    assert a == b
    # Different code -> different fingerprint.
    assert a != fingerprint("py.n_plus_one", "svc.py", "user = db.get(other)")


def test_record_and_fix_rate(tmp_path):
    st = _store(tmp_path)
    run1 = [_finding("r1", "HIGH", code="a"), _finding("r2", "CRITICAL", code="b"),
            _finding("r3", "MEDIUM", code="c")]
    st.record_run("demo", "sha1", "main", run1, "static")
    # Second run: r1 fixed (gone), r2 still open, r3 still open, r4 new.
    run2 = [_finding("r2", "CRITICAL", code="b"), _finding("r3", "MEDIUM", code="c"),
            _finding("r4", "LOW", code="d")]
    st.record_run("demo", "sha2", "main", run2, "static")

    fr = st.fix_rate("demo")
    assert fr["runs"] == 2
    assert fr["distinct"] == 4          # a,b,c,d
    assert fr["open"] == 3              # b,c,d in latest run
    assert fr["resolved"] == 1         # a (r1) disappeared
    assert 0.24 < fr["fix_rate"] < 0.26

    hist = st.run_history("demo")
    assert hist[0]["id"] > hist[1]["id"]         # newest first
    assert hist[0]["introduced"] == 1            # r4
    assert hist[0]["fixed"] == 1                 # r1
    st.close()


def test_store_code_toggle_off(tmp_path):
    st = Store(sqlite_path=str(tmp_path / "h.db"), store_code=False)
    st.record_run("demo", "s", "main", [_finding("r1", "HIGH", code="secret_line")], "static")
    conn = st.connect()
    cur = conn.cursor()
    cur.execute("SELECT code FROM findings")
    assert cur.fetchone()[0] == ""     # code not persisted when store_code is off
    st.close()


def test_jira_dry_run_when_no_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("PERF_GATE_JIRA_URL", raising=False)
    monkeypatch.delenv("PERF_GATE_JIRA_TOKEN", raising=False)
    cfg = jira_client.JiraConfig({"enabled": True, "project_key": "PERF",
                                  "min_severity": "CRITICAL"})
    assert cfg.credentialed() is False
    findings = [_finding("r1", "CRITICAL"), _finding("r2", "HIGH")]
    res = jira_client.sync_findings(cfg, findings, _store(tmp_path))
    assert res["dry_run"] is True
    assert res["eligible"] == 1                  # only the CRITICAL is at/above threshold
    assert len(res["created"]) == 1
    assert res["created"][0]["issue_key"] == "(dry-run)"


def test_jira_skips_already_ticketed(monkeypatch, tmp_path):
    # A finding whose fingerprint already has a ticket must not be re-filed.
    st = _store(tmp_path)
    f = _finding("r1", "CRITICAL", code="boom")
    fp = fingerprint(f.rule_id, f.file, f.code)
    st.save_ticket(fp, "PERF-123", "http://jira/browse/PERF-123", "CRITICAL", f.title)
    cfg = jira_client.JiraConfig({"enabled": True, "project_key": "PERF"})
    res = jira_client.sync_findings(cfg, [f], st)
    assert len(res["created"]) == 0
    assert len(res["skipped"]) == 1
    assert res["skipped"][0]["issue_key"] == "PERF-123"
    st.close()


def test_jira_payload_shape():
    cfg = jira_client.JiraConfig({"project_key": "PERF", "issue_type": "Bug",
                                  "labels": ["performance-gate"], "include_code": True})
    p = jira_client._payload(cfg, _finding("cs.ef_n_plus_one", "CRITICAL",
                                           file="Svc.cs", line=10, code="ctx.X.Where(...)"))
    assert p["fields"]["project"]["key"] == "PERF"
    assert p["fields"]["issuetype"]["name"] == "Bug"
    assert "CRITICAL" in p["fields"]["summary"]
    assert "sev-critical" in p["fields"]["labels"]
    assert "ctx.X.Where" in p["fields"]["description"]


def test_repo_cannot_set_db_path_or_jira_endpoint(tmp_path, monkeypatch):
    # SECURITY: a perf-gate.yml inside the scanned repo tries to redirect the DB
    # write and point Jira at an attacker host. Both must be ignored.
    monkeypatch.delenv("PERF_GATE_DB_PATH", raising=False)
    repo = tmp_path / "evil"
    repo.mkdir()
    (repo / "perf-gate.yml").write_text(
        "history:\n  enabled: true\n  sqlite_path: /tmp/evil-clobber.db\n"
        "jira:\n  enabled: true\n  base_url: http://attacker.example\n")
    cfg = config_mod.load(str(repo))
    # DB path forced back to the safe operator default, not the repo's value.
    assert cfg.history["sqlite_path"] == config_mod.DEFAULTS["history"]["sqlite_path"]
    # There is no jira.base_url key at all - the endpoint only comes from env.
    assert "base_url" not in cfg.jira
    jcfg = jira_client.JiraConfig(cfg.jira)
    assert jcfg.base_url == ""       # not the attacker host
