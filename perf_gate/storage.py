"""Local findings history + fix-rate trends.

Records every review run and its findings into a database so you can see how many
issues each push introduced or fixed, and the fix-rate trend over time. The
default backend is **SQLite** - a single local file (`.perf-gate/history.db`),
zero setup. Point it at an on-prem **Postgres** instead by setting the env var
`PERF_GATE_DB_URL` (e.g. `postgresql://user:pass@localhost:5432/perfgate`).

SECURITY / on-prem guarantees:
  * The database is local / on your own infrastructure. Nothing here talks to the
    internet.
  * The connection URL is **operator-only** (read from the process env var
    `PERF_GATE_DB_URL`), and can NEVER be set from the scanned repo's
    `perf-gate.yml`. A reviewed repo therefore cannot redirect findings to an
    outside database - same guard used for the LLM endpoint.
  * Only source-derived data is stored (rule ids, file paths, the offending code
    line). No production/customer data is ever read. Storing the code snippet is
    configurable (`history.store_code`); turn it off to keep only fingerprints.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .detectors.base import Finding

_SEVS = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
_WS = re.compile(r"\s+")


def fingerprint(rule_id: str, file: str, code: str) -> str:
    """A stable id for 'the same finding' across runs.

    Deliberately excludes the line number - line numbers drift as unrelated code
    is added above, but the (rule, file, normalised code) triple stays constant,
    so we can tell that a finding from three pushes ago is the same one that is
    still open (or was fixed) today.
    """
    norm = _WS.sub(" ", (code or "").strip())
    raw = f"{rule_id}|{file}|{norm}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    """Thin backend-agnostic wrapper over SQLite / Postgres.

    SQL is written with a placeholder marker the wrapper rewrites per backend, so
    the same statements run on both (`?` for SQLite, `%s` for psycopg).
    """

    def __init__(self, url: Optional[str] = None, sqlite_path: str = ".perf-gate/history.db",
                 store_code: bool = True):
        url = (url or "").strip()
        self.store_code = store_code
        if url.startswith(("postgres://", "postgresql://")):
            self.backend = "postgres"
            self._url = url
            self._pk = "SERIAL PRIMARY KEY"
        else:
            self.backend = "sqlite"
            self._path = sqlite_path
            self._pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
        self._conn = None

    # -- connection -----------------------------------------------------------
    def connect(self):
        if self._conn is not None:
            return self._conn
        if self.backend == "postgres":
            try:
                import psycopg2  # noqa: F401
                self._conn = __import__("psycopg2").connect(self._url)
            except ImportError:
                try:
                    self._conn = __import__("psycopg").connect(self._url)
                except ImportError:
                    raise RuntimeError(
                        "PERF_GATE_DB_URL points at Postgres but no driver is "
                        "installed. Run: pip install 'perf-gate-agent[postgres]' "
                        "(or pip install psycopg2-binary).")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self._path)
        self._init_schema()
        return self._conn

    def _ph(self, sql: str) -> str:
        return sql if self.backend == "sqlite" else sql.replace("?", "%s")

    def _exec(self, cur, sql: str, params=()):
        cur.execute(self._ph(sql), params)

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(f"""CREATE TABLE IF NOT EXISTS runs (
            id {self._pk},
            ts TEXT NOT NULL, repo TEXT NOT NULL, commit_sha TEXT, branch TEXT,
            total INTEGER, critical INTEGER, high INTEGER, medium INTEGER,
            low INTEGER, info INTEGER, engine TEXT)""")
        cur.execute(f"""CREATE TABLE IF NOT EXISTS findings (
            id {self._pk},
            run_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, rule_id TEXT,
            title TEXT, category TEXT, severity TEXT, file TEXT, line INTEGER,
            code TEXT, why TEXT, fix TEXT, confirmed INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS tickets (
            fingerprint TEXT PRIMARY KEY, issue_key TEXT, url TEXT,
            created_ts TEXT, severity TEXT, title TEXT)""")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_findings_run ON findings(run_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_findings_fp ON findings(fingerprint)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_runs_repo ON runs(repo)")
        self._conn.commit()

    # -- recording ------------------------------------------------------------
    def record_run(self, repo: str, commit_sha: str, branch: str,
                   findings: List[Finding], engine: str) -> int:
        conn = self.connect()
        cur = conn.cursor()
        counts = {s: 0 for s in _SEVS}
        for f in findings:
            if f.severity in counts:
                counts[f.severity] += 1
        self._exec(cur,
            "INSERT INTO runs (ts, repo, commit_sha, branch, total, critical, "
            "high, medium, low, info, engine) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_now_iso(), repo, commit_sha, branch, len(findings),
             counts["CRITICAL"], counts["HIGH"], counts["MEDIUM"],
             counts["LOW"], counts["INFO"], engine))
        run_id = self._last_run_id(cur, repo)
        for f in findings:
            fp = fingerprint(f.rule_id, f.file, f.code)
            code = f.code if self.store_code else ""
            conf = None if f.confirmed is None else (1 if f.confirmed else 0)
            self._exec(cur,
                "INSERT INTO findings (run_id, fingerprint, rule_id, title, "
                "category, severity, file, line, code, why, fix, confirmed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, fp, f.rule_id, f.title, f.category, f.severity, f.file,
                 f.line, code, f.why, f.fix, conf))
        conn.commit()
        return run_id

    def _last_run_id(self, cur, repo: str) -> int:
        # Portable "id of the row we just inserted" without relying on RETURNING.
        self._exec(cur, "SELECT id FROM runs WHERE repo=? ORDER BY id DESC LIMIT 1", (repo,))
        row = cur.fetchone()
        return int(row[0])

    # -- trends ---------------------------------------------------------------
    def _fps_for_run(self, cur, run_id: int):
        self._exec(cur, "SELECT DISTINCT fingerprint FROM findings WHERE run_id=?", (run_id,))
        return {r[0] for r in cur.fetchall()}

    def run_history(self, repo: str, limit: int = 20) -> List[dict]:
        """Newest-first list of run summaries with introduced/fixed deltas."""
        conn = self.connect()
        cur = conn.cursor()
        self._exec(cur,
            "SELECT id, ts, commit_sha, branch, total, critical, high, medium, "
            "low, info, engine FROM runs WHERE repo=? ORDER BY id DESC LIMIT ?",
            (repo, limit))
        cols = ["id", "ts", "commit_sha", "branch", "total", "critical", "high",
                "medium", "low", "info", "engine"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        # Compute introduced/fixed vs the immediately preceding run (chronological).
        chron = list(reversed(rows))
        prev_fps = None
        deltas = {}
        for r in chron:
            fps = self._fps_for_run(cur, r["id"])
            if prev_fps is None:
                deltas[r["id"]] = (len(fps), 0)  # first run: all introduced
            else:
                deltas[r["id"]] = (len(fps - prev_fps), len(prev_fps - fps))
            prev_fps = fps
        for r in rows:
            r["introduced"], r["fixed"] = deltas[r["id"]]
        return rows

    def fix_rate(self, repo: str) -> dict:
        """Lifecycle fix-rate across the repo's whole history.

        A distinct finding (by fingerprint) is 'resolved' if it does NOT appear in
        the most recent run, and 'open' if it does. fix_rate = resolved / distinct.
        """
        conn = self.connect()
        cur = conn.cursor()
        self._exec(cur, "SELECT id FROM runs WHERE repo=? ORDER BY id DESC LIMIT 1", (repo,))
        row = cur.fetchone()
        if not row:
            return {"distinct": 0, "open": 0, "resolved": 0, "fix_rate": 0.0, "runs": 0}
        latest = int(row[0])
        open_fps = self._fps_for_run(cur, latest)
        self._exec(cur, "SELECT DISTINCT fingerprint FROM findings WHERE run_id IN "
                        "(SELECT id FROM runs WHERE repo=?)", (repo,))
        all_fps = {r[0] for r in cur.fetchall()}
        self._exec(cur, "SELECT COUNT(*) FROM runs WHERE repo=?", (repo,))
        n_runs = int(cur.fetchone()[0])
        distinct = len(all_fps)
        open_n = len(open_fps)
        resolved = distinct - open_n
        rate = (resolved / distinct) if distinct else 0.0
        return {"distinct": distinct, "open": open_n, "resolved": resolved,
                "fix_rate": rate, "runs": n_runs}

    # -- ticket dedup (used by the Jira integration) --------------------------
    def get_ticket(self, fp: str) -> Optional[dict]:
        conn = self.connect()
        cur = conn.cursor()
        self._exec(cur, "SELECT fingerprint, issue_key, url, created_ts, severity, "
                        "title FROM tickets WHERE fingerprint=?", (fp,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip(["fingerprint", "issue_key", "url", "created_ts",
                         "severity", "title"], row))

    def save_ticket(self, fp: str, issue_key: str, url: str, severity: str, title: str) -> None:
        conn = self.connect()
        cur = conn.cursor()
        self._exec(cur, "DELETE FROM tickets WHERE fingerprint=?", (fp,))
        self._exec(cur, "INSERT INTO tickets (fingerprint, issue_key, url, "
                        "created_ts, severity, title) VALUES (?,?,?,?,?,?)",
                   (fp, issue_key, url, _now_iso(), severity, title))
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
