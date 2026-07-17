# Running the demo on Windows (end-to-end)

You can see the tool working in about 5 minutes, even without the AI stage. All
commands are PowerShell.

## 1. Extract the zip
Right-click `perf-gate-demo.zip` → **Extract All**. You get a `perf-gate-demo`
folder containing two subfolders:
- `perf-gate-agent` — the tool
- `sample-perf-issues` — sample code with real performance bugs to scan

## 2. Install Python (skip if you already have Python 3)
Download Python 3 from <https://www.python.org/downloads/>. On the first installer
screen, **tick "Add python.exe to PATH"**, then Install. Verify in PowerShell:
```powershell
python --version      # should print Python 3.x
```

## 3. Install the tool
Point the `cd` at wherever you extracted the folder:
```powershell
cd $HOME\Downloads\perf-gate-demo\perf-gate-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```
If `Activate.ps1` errors with "running scripts is disabled", run this once (affects
only the current window, nothing permanent) and retry the activate line:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 4. Run it — static mode (instant, no AI needed)
```powershell
perf-gate review --repo ..\sample-perf-issues --all --no-llm
```
You'll see ~58 findings across Java, Python, SQL, C#, C, React and Node.js.

## 5. Stakeholder HTML report
```powershell
perf-gate review --repo ..\sample-perf-issues --all --no-llm --html perf-report.html
start perf-report.html
```

## 6. History + fix-rate trend
```powershell
perf-gate review --repo ..\sample-perf-issues --all --no-llm --record
perf-gate trends --repo ..\sample-perf-issues --html trends.html
start trends.html
```
Run the `review ... --record` line a few times (edit or delete a sample file in
between to "fix" something) and the trend chart fills in. This uses a local SQLite
file by default — nothing to install.

## 7. Jira dry-run (no credentials needed)
```powershell
perf-gate review --repo ..\sample-perf-issues --all --no-llm --jira
```
It prints the tickets it WOULD create for the critical findings. To file them for
real in your on-prem Jira, follow `docs\HISTORY_AND_JIRA.md` (set two env vars).

## Optional — turn on the local AI stage (Stage 2)
1. Install Ollama for Windows from <https://ollama.com/download> — it runs
   automatically in the background after install.
2. Pull a code model:
   ```powershell
   ollama pull qwen2.5-coder:7b
   ```
3. Re-run any command above **without** `--no-llm`, e.g.:
   ```powershell
   perf-gate review --repo ..\sample-perf-issues --all
   ```
   Each finding is now confirmed and explained by the local model. Everything
   stays on your laptop — nothing leaves the network.

## Notes / troubleshooting
- `--all` needs no git, so you can ignore `run_demo.sh` (that script is Mac/Linux
  bash only).
- If `perf-gate` is "not recognised", use the module form with the same arguments:
  `python -m perf_gate review --repo ..\sample-perf-issues --all --no-llm`
- Real Postgres + Jira setup for your own environment: `docs\HISTORY_AND_JIRA.md`.
