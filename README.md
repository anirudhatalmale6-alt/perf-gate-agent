# Performance Gate Agent

A static, **on-prem** performance reviewer that runs on **every code push** and
flags performance regressions before they reach production — the changes that
slip through because a project was marked *"performance testing not required"* at
design time, but a later code/metadata change quietly introduced a slowdown.

**Nothing leaves your network.** It reads source diffs only — it never runs your
app, touches a database, or sees production/customer data — and its LLM stage
uses a **local** model (Ollama by default). No PII, no code, no data ever leaves
the machine.

---

## The problem it solves

The performance incident this was built for:

1. A change went to production and caused slowness.
2. The performance team never tested it, because the project team marked the Jira
   package **"performance testing not required"** and the HLD was approved on that
   basis. That decision was *correct at design time*.
3. The degradation was introduced **later, during development** — a code/metadata
   change (e.g. a JPA fetch strategy flipped `LAZY → EAGER`, a cache/pool config,
   a query added inside a loop). The HLD never described it.
4. There was no performance regression suite, so QA (which checks correctness, not
   speed) let it through.

The root gap: **the "not required" decision is never re-validated against the
actual code that gets written.** This agent closes that gap. It looks at the real
diff on every push, independent of any upfront judgement, and raises a flag the
moment a change carries performance risk.

---

## How it works — two stages

**Stage 1 — deterministic rules (fast, no LLM).**
Diff-scoped detectors scan only the changed lines for known degradation patterns:
N+1 queries, `LAZY → EAGER` fetch changes, connection-per-request, string concat
in loops, unbounded thread pools, busy-waits, `Thread.sleep` in synchronized
blocks, autoboxing/allocation in loops, regex compiled in loops, `SELECT *`,
non-sargable SQL predicates, leading-wildcard `LIKE`, cartesian joins, blocking
I/O in async code, React timer/listener leaks, and more. This stage is instant
and needs no model.

**Stage 2 — local-LLM confirmation (optional, grounded).**
Each candidate from Stage 1 is handed to a **local** model *one at a time*, with
only the small offending snippet plus the 1–2 most relevant notes retrieved from
a local knowledge base. The model confirms it is a real risk (or drops it as a
false positive), sharpens the severity, and writes a one-line explanation and fix.
Small, focused prompts are exactly what a local model does well — fast and
accurate. **If no local model is reachable, Stage 1 stands on its own** and the
report simply notes it wasn't LLM-confirmed.

This is deliberate: the deterministic layer carries the *recall*, the model only
does the *precision + explanation* it's good at. That's the answer to "Ollama is
slow and its reasoning isn't as strong as OpenAI" — you make the model do far
less work, not find a bigger model.

---

## Quick start

```bash
# 1. Install (only PyYAML is required at runtime; everything else is stdlib)
pip install -e .

# 2. (Optional) run a local model for Stage 2 — keeps everything on-prem
ollama pull qwen2.5-coder:7b     # code-specialised; use :14b or :32b for stronger reasoning
ollama serve

# 3. Review the current change (diff against the previous commit)
perf-gate review

# Review the whole repo instead of just the diff
perf-gate review --all

# Write machine-readable findings too
perf-gate review --json findings.json

# Write a self-contained HTML report for stakeholders (open in any browser)
perf-gate review --html perf-report.html
```

### Languages covered

| Language / stack | Extensions | Examples of what it catches |
|---|---|---|
| Java / Spring / JPA | `.java` | N+1 queries, `LAZY→EAGER`, string concat in loops, unbounded pools |
| Python | `.py` | N+1, nested-loop O(n²), blocking I/O in async, connection-per-iter |
| SQL | `.sql` | `SELECT *`, non-sargable predicates, leading-wildcard `LIKE`, cartesian joins |
| C# / .NET / EF Core | `.cs` | EF Core N+1, LINQ re-materialized in loops, sync-over-async (`.Result`/`.Wait`), lazy-loading proxies |
| C / C++ | `.c` `.h` `.cpp` `.cc` `.hpp` | `malloc` in loops, `strlen` in loop conditions (O(n²)), `strcat` in loops, process spawn in loops |
| React / TypeScript | `.ts` `.tsx` `.js` `.jsx` | timer/listener leaks, inline handlers, un-virtualized lists |
| Node.js / Next.js / Angular | `.ts` `.tsx` `.js` `.jsx` `.mjs` `.cjs` | sync `fs`/crypto blocking the event loop, `await` in loops (N+1), Next.js uncached `fetch`, `*ngFor` without `trackBy` |

Adding another language is a single new file under `perf_gate/detectors/` plus one
line in the extension map — the engine, gate, LLM stage and reports are shared.

Exit code is non-zero when the gate trips (`fail_on` severity or above), so it
can block a merge/deploy in CI.

---

## Run it on every push (GitHub Actions)

A ready workflow is in `.github/workflows/perf-gate.yml`. Copy it into your repo.

- **Recommended: a self-hosted runner inside your network.** That's what keeps the
  local LLM reachable and guarantees nothing leaves your infrastructure. Set
  `runs-on: self-hosted`.
- On a GitHub-hosted runner it still works — it just runs **static-only** unless a
  local model is reachable.

The workflow reviews the pushed diff, writes a summary to the Actions run, posts
the report as a commit comment, and uploads the JSON findings as an artifact.

---

## Using your reference PDF as the knowledge base

You can ground Stage 2 in your own reference (e.g. the Java Performance guide):

```bash
perf-gate build-kb /path/to/java-performance-the-definitive-guide.pdf
```

This extracts and indexes the PDF **locally** into `.perf-gate/kb-index.json`,
which is **git-ignored on purpose** — the book is copyrighted, so its text is
never committed or shipped. The agent merges it with the built-in rulebook
(`perf_gate/knowledge/kb.py`), which contains original, safe-to-ship explanations
and works with no PDF at all.

---

## Configuration (`perf-gate.yml`)

```yaml
llm:
  enabled: true
  backend: ollama            # ollama | openai (openai = vLLM or Azure OpenAI private endpoint)
  model: qwen2.5-coder:7b    # swap to :14b / :32b for stronger reasoning
  base_url: ""               # blank = localhost default
gate:
  fail_on: HIGH              # CRITICAL | HIGH | MEDIUM | LOW | INFO | NONE
  ignore_rules: []
  ignore_paths: [test/, tests/, generated/]
```

The model layer is swappable by config alone. To move from Ollama to a faster
local server (vLLM) or an Azure OpenAI **private endpoint** later, set
`backend: openai` and `base_url` — no code changes.

### Making the local model faster / better
- Use a **code-specialised** model (`qwen2.5-coder`, `deepseek-coder`) rather than
  a generic one — big quality jump at the same speed class.
- If the box has a GPU, serve the model with **vLLM** instead of Ollama for several
  times the throughput (`backend: openai`, point `base_url` at it).
- Stage 1 already caps the model to the handful of flagged snippets and only the
  changed lines, so most pushes need only a few small, fast calls.

---

## What it does *not* do (by design)

- It does **not** run your code, tests, or load tests — it's purely static.
- It does **not** connect to a database or read production/customer data.
- It does **not** send code or diffs to any external service.

### Why a reviewed repo can't redirect it (data-exfiltration guard)

Because this runs on **every push over code you don't fully control**, the tool
treats the reviewed repo as untrusted for anything that decides *where data goes*:

- The **LLM endpoint** (`backend`, `base_url`, host, API key) can only be set by the
  **operator** — via real environment variables on the runner, or the agent's own
  `.env`. A `perf-gate.yml` or `.env` committed *inside the scanned repo* can never
  change the endpoint; those values are forced back to the safe local default.
- `.env` is read **only** from the agent's own folder (or an explicit
  `PERF_GATE_ENV_FILE` path), **never** from the scanned repo or the working dir,
  and only a safe allow-list of keys (`PERF_GATE_MODEL`, `PERF_GATE_LLM_DISABLED`,
  `PERF_GATE_FAIL_ON`) is honoured.
- Git revisions are validated so a crafted `--base`/`--head` can't be read as a git
  option (argument-injection guard), and all git calls use argument lists — no shell.
- Config is parsed with `yaml.safe_load`; there is no `eval`/`exec`/`pickle`.

Runtime load/regression testing catches more but needs a test environment and
representative (synthetic) data — a sensible **later** phase. This gate is the
cheap, safe first line that would have caught the incident above.

---

## Layout

```
perf_gate/
  cli.py                 # entry point: review / build-kb
  diff.py                # git diff -> changed files + changed line ranges
  config.py              # perf-gate.yml + env, gate policy
  report.py              # Markdown / JSON / HTML, commit-comment + job summary
  detectors/
    base.py              # Finding + loop-scope scanner
    java_rules.py        # Java / Spring / JPA detectors
    python_rules.py      # Python detectors
    sql_rules.py         # SQL detectors
    frontend_rules.py    # React / TS detectors
    csharp_rules.py      # C# / .NET / EF Core detectors
    c_rules.py           # C / C++ detectors
    node_rules.py        # Node.js / Next.js / Angular detectors
  knowledge/
    kb.py                # built-in rulebook + local PDF index + TF-IDF retriever
  llm/
    client.py            # swappable local-LLM client (Ollama / OpenAI-compatible)
    reviewer.py          # Stage-2 confirm + explain
tests/                   # unit tests (pytest)
.github/workflows/       # run-on-every-push workflow
```

Run the tests with `python -m pytest tests/`.
