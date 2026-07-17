# Findings history (Postgres) + on-prem Jira tickets — local setup

Two optional integrations, both **on-prem**. Nothing in either one talks to the
public internet, and neither can be redirected off your network by the code being
scanned (see "Security model" at the end).

- **History** — records every review run and its findings so you can see the
  fix-rate trend over time (`perf-gate trends`).
- **Jira** — auto-creates a ticket in your on-prem Jira for each new
  high-severity finding, de-duplicated so the same issue never opens two tickets.

You can turn on either one independently.

---

## Part A — Findings history & fix-rate trends

### A0. Zero-setup default: SQLite (no database to install)
Out of the box the history uses a local SQLite file — nothing to install:

```bash
perf-gate review --all --record          # record this run
perf-gate trends                          # show the trend table
perf-gate trends --html trends.html       # + a stakeholder HTML report
```

The file lives at `.perf-gate/history.db` next to where you run it. That's enough
for a single machine / runner. Use Postgres (below) when several runners or people
need to share one history.

### A1. Set up Postgres locally

**Option 1 — Docker (fastest):**
```bash
docker run -d --name perfgate-pg \
  -e POSTGRES_USER=perfgate \
  -e POSTGRES_PASSWORD=perfgate \
  -e POSTGRES_DB=perfgate \
  -p 5432:5432 \
  postgres:16
```

**Option 2 — native install:**
```bash
# macOS
brew install postgresql@16 && brew services start postgresql@16
createdb perfgate

# Ubuntu/Debian
sudo apt-get install -y postgresql
sudo -u postgres psql -c "CREATE USER perfgate WITH PASSWORD 'perfgate';"
sudo -u postgres psql -c "CREATE DATABASE perfgate OWNER perfgate;"
```

### A2. Install the Postgres driver
```bash
pip install 'perf-gate-agent[postgres]'      # or: pip install psycopg2-binary
```

### A3. Point the agent at it (operator env var — never in the repo)
```bash
export PERF_GATE_DB_URL="postgresql://perfgate:perfgate@localhost:5432/perfgate"
```
Windows PowerShell: `$env:PERF_GATE_DB_URL="postgresql://perfgate:perfgate@localhost:5432/perfgate"`

That's it — the tables are created automatically on first use. No schema/migration
step to run.

### A4. Use it
```bash
perf-gate review --all --record           # record a run into Postgres
# ...more pushes over time, each with --record...
perf-gate trends --html trends.html       # table + fix-rate + bar chart
```

Or turn recording on permanently in `perf-gate.yml` so you don't need `--record`:
```yaml
history:
  enabled: true
```

**What "fix rate" means:** each finding gets a stable fingerprint from
`(rule, file, normalised code)` — not the line number, so it survives unrelated
edits above it. A fingerprint that appeared in an earlier run but is absent from
the latest run counts as *resolved*. `fix_rate = resolved / all-distinct-findings`.

---

## Part B — On-prem Jira auto-tickets

Targets **Jira Data Center / Server** via REST API v2. Cloud works too (use
`auth: basic`), but the design assumes your internal Jira.

### B1. Create a Jira API token (Personal Access Token)
On Jira Data Center / Server:
1. Click your avatar → **Profile** → **Personal Access Tokens**.
2. **Create token**, give it a name, and (optionally) an expiry.
3. Copy the token — you won't see it again.

> Jira Cloud instead: create an API token at
> `id.atlassian.com → Security → API tokens`, set `auth: basic` in `perf-gate.yml`,
> and also export `PERF_GATE_JIRA_USER=your-email`.

### B2. Set the endpoint + token as operator env vars (never in the repo)
```bash
export PERF_GATE_JIRA_URL="https://jira.your-company.local"
export PERF_GATE_JIRA_TOKEN="<the token from B1>"
# Jira Cloud / basic auth only:
# export PERF_GATE_JIRA_USER="you@company.com"
```
Windows PowerShell:
```powershell
$env:PERF_GATE_JIRA_URL="https://jira.your-company.local"
$env:PERF_GATE_JIRA_TOKEN="<the token from B1>"
```

### B3. Fill in the placeholder in `perf-gate.yml`
Only non-secret, non-endpoint settings live here:
```yaml
jira:
  enabled: true
  project_key: "PERF"        # <-- your Jira project key (the placeholder was YOUR-PROJECT-KEY)
  issue_type: Bug
  min_severity: CRITICAL     # file tickets at/above this severity
  labels: [performance-gate]
  include_code: true         # include the offending line in the ticket (goes only to your Jira)
  auth: bearer               # bearer = Data Center PAT | basic = Cloud/basic
```

### B4. Try it — dry-run first (no token needed)
With no `PERF_GATE_JIRA_URL`/`_TOKEN` set, it prints the tickets it *would* create
so you can validate the flow safely:
```bash
perf-gate review --all --jira
# Jira (dry-run): 4 ticket(s) WOULD be created ...
#   • [Perf Gate] CRITICAL: EF Core query inside a loop (N+1) (InvoiceService.cs:15)
```

Then set the two env vars and run for real:
```bash
perf-gate review --all --jira --record    # --record lets it remember filed tickets to de-dupe
```

**De-duplication:** filed tickets are remembered by fingerprint in the history DB,
so re-running never opens a second ticket for the same finding. (Run with
`--record`, or `history.enabled: true`, so the ticket memory persists.)

---

## Security model (why this is safe to run on every push)

The agent runs on code you don't fully control, so anything that decides *where
data goes* is **operator-only** and can never be set from the scanned repo:

| Setting | Where it comes from | Can the scanned repo set it? |
|---|---|---|
| Postgres URL | env `PERF_GATE_DB_URL` | **No** |
| SQLite path | operator default / env `PERF_GATE_DB_PATH` | **No** (forced back to default) |
| Jira base URL | env `PERF_GATE_JIRA_URL` | **No** (stripped from repo config) |
| Jira token/user | env `PERF_GATE_JIRA_TOKEN` / `_USER` | **No** |
| project key, severity, labels | `perf-gate.yml` | Yes (can't redirect data off-box) |

- The history DB is **local / on your own infrastructure**. Only source-derived
  data is stored (rule id, file path, the offending line) — no production or
  customer data is ever read. Set `history.store_code: false` to keep only
  fingerprints and store no code at all.
- The token is never written to the repo, the report, or logs. The only data sent
  to Jira is the ticket contents, and only to *your* Jira host.
- With no credentials set, Jira runs in dry-run and nothing leaves the machine.
