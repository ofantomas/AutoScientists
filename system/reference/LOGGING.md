---
name: multi-agent-focus-logging
description: Structured logging for full experiment tracking and ablation analysis
---

# Logging & Tracking

Every action in the system is logged for full traceability.

## Single Canonical Log

**`{FOCUS_ROOT}/logs/experiments.jsonl`** is the SINGLE SOURCE OF TRUTH for all experiment results. The **orchestrator** writes this file — agents do NOT write to it directly.

### How it works

1. CPU-eval agents run experiments, write `results/{exp_id}.md`, and POST a `[RESULT]` to the workshop
2. At the end of each cycle the orchestrator runs the `cycle_ledger` hook (runbook Step 5d), which harvests this cycle's `[RESULT]` posts + their result files + the queue's `completed:` rows into ONE line per experiment
3. Each line contains everything needed for stagnation checks and analysis

### Ordering guarantee: the file is chronological

Rows are appended **oldest-first**. The posts API returns results **newest-first**, so `cycle_ledger`
must iterate `reversed(...)` when it appends. This is not cosmetic: every consumer takes a *trailing*
window (`rows[-10:]`), so a newest-first file makes that window read the OLDEST rows in the run and
can invert the stagnation verdict — reporting a healthy run as stuck, or a stuck one as healthy.

Because one bad harvest silently corrupts the order for the rest of the run, **every consumer sorts
defensively by `ts` before taking a window** — runbook Step 5g, `task/meta_diagnostics.py` and
ROLE-MONITOR all do. Rows whose `ts` is `null` or unparseable sort **LAST, never first**: an undated
row read as "oldest" is pushed out of every trailing window and silently vanishes from the signal,
while a staler real row takes the slot it should have had. Sorting it last keeps the newest evidence
— which is what a recency window is asking for — in the window. The sort must be stable, so rows
sharing a timestamp — and all undated rows together — keep their file order, which is the best
remaining evidence of sequence.

### Format

One **flat** JSON object per experiment, per line — no nesting, no `{"experiments": [...]}` wrapper.
Always write all 16 keys, using `null` for what could not be determined.

```json
{
  "ts": "2026-07-27T10:06:30Z",
  "cycle": 12,
  "exp_id": "exp_tr_grow_band",
  "team": "trust_region",
  "agent": "run01_cpu1",
  "axis": "rho_inc",
  "direction": "increase",
  "value": 1.4,
  "fitness": 0.998097,
  "is_valid": 1,
  "mean_rel_energy": 1.000412,
  "test_fitness": 0.999012,
  "test_is_valid": 1,
  "outcome": "KEEP",
  "delta": -0.006974,
  "post_id": "post_8f21"
}
```

## Other Logs (secondary, not authoritative)

```
{FOCUS_ROOT}/
├── logs/
│   ├── experiments.jsonl       ← CANONICAL (orchestrator writes)
│   ├── sessions.jsonl          ← One line per agent session (orchestrator writes)
│   └── raw/
│       └── {agent}_{timestamp}_{nonce}.json  ← Native child completion artifact
│
├── agents/{name}/
│   └── actions.md              ← Human-readable session history per agent
│
└── Main workspace
    ├── results/{exp_id}.md     ← Structured result per experiment (agent writes)
    └── agents/{name}.md        ← Last-seen, session count, last outcome
```

These are all useful for context but `experiments.jsonl` is the one the stagnation check reads.

## 1. sessions.jsonl — Session Tracking

**Written by:** Orchestrator, after each agent session finishes.
**Format:** One JSON line per session.

```json
{
  "agent": "run01_cpu1",
  "role": "cpu",
  "team": "architecture",
  "session_id": "uuid",
  "started_at": "2026-03-29T10:00:00Z",
  "ended_at": "2026-03-29T10:08:30Z",
  "duration_seconds": 510,
  "status": "success",
  "promise_received": true,
  "experiments_run": 2,
  "experiments": [
    {"exp_id": "exp_kv_shift", "metric": 0.985, "outcome": "KEEP", "delta": -0.005},
    {"exp_id": "exp_gated_attn", "metric": 1.002, "outcome": "DISCARD", "delta": 0.012}
  ],
  "error": null
}
```

**Failed session:**
```json
{
  "agent": "run01_cpu2",
  "role": "cpu",
  "team": "optimizer",
  "started_at": "2026-03-29T10:00:05Z",
  "ended_at": "2026-03-29T10:20:05Z",
  "duration_seconds": 1200,
  "status": "timeout",
  "promise_received": false,
  "experiments_run": 1,
  "experiments": [
    {"exp_id": "exp_muon_warmup", "metric": null, "outcome": null, "delta": null}
  ],
  "error": "Agent timed out after 1200s — training may have completed but result not written"
}
```

**Orchestrator writes this:**
```python
import json, uuid
from datetime import datetime, timezone

def log_session(agent, role, team, started, status, experiments, error=None):
    entry = {
        "agent": agent,
        "role": role,
        "team": team,
        "session_id": str(uuid.uuid4()),
        "started_at": started,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": (datetime.now(timezone.utc) - parse(started)).total_seconds(),
        "status": status,
        "promise_received": status == "success",
        "experiments_run": len(experiments),
        "experiments": experiments,
        "error": error
    }
    with open(f"{FOCUS_ROOT}/logs/sessions.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
```

## 2. experiments.jsonl — Experiment Tracking

**Written by:** the **orchestrator**, once per cycle via the `cycle_ledger` profile hook (runbook Step 5d). Agents never append to this file.
**Format:** One flat JSON line per experiment — the 16 keys shown under "Format" above.

| key | meaning |
|---|---|
| `ts` | ISO-8601 UTC timestamp of the result. Rows are written oldest-first and consumers sort by this key before windowing; a `null` here sorts last, so never leave it unset if the timestamp is knowable |
| `cycle` | cycle number that produced it |
| `exp_id` | experiment id (e.g. `exp_tr_grow_band`) |
| `team` | team name from the post's `team:` tag |
| `agent` | cpu-eval agent that ran it |
| `axis`, `direction`, `value` | the single variable changed, from the queue item |
| `fitness`, `is_valid` | **TRAIN** eval (`is_valid` is `1` / `0`, or `null` when the harvest could not determine it — `null` means UNKNOWN, not invalid; see "Counting rules" below). `fitness` is the honest `mean_rel_steps` whenever a trajectory was produced — **including runs that failed the energy gate** — so it is `1000.0` only for infra errors, non-convergence, or a blown force-call budget. Never infer validity from `fitness`; read `is_valid`. |
| `mean_rel_energy` | **TRAIN** energy ratio; the validity gate is `>= 1.0`. This is what makes an `is_valid: 0` row interpretable: just under `1.0` with a low `fitness` = fast but under-relaxing (the mechanism works, the energy leak is the bug); well under `1.0` with a high `fitness` = losing on both axes. |
| `test_fitness`, `test_is_valid` | **HELD-OUT TEST** eval; `null` when no test eval ran — i.e. on train `DISCARD`s and on `FAILED` |
| `outcome` | `KEEP` \| `NEAR_MISS` \| `DISCARD` \| `REJECTED_TEST` \| `FAILED` |
| `delta` | signed train improvement vs the champion at eval time (negative = better) |
| `post_id` | id of the `[RESULT]` post the row was harvested from |

Outcome vocabulary — **five** values:

| value | meaning |
|---|---|
| `KEEP` | Promoted: train gate **and** held-out test gate both passed, and it won the promotion race. `champion.md` + `champion/algo.py` were written. |
| `NEAR_MISS` | Train gate **and** held-out test gate both passed, but it was **not** promoted: it lost the promotion race (an equal-or-better champion landed while it was evaluating, or `champion.md` no longer records its `exp_id`). Positive evidence. Written to **neither** `dead_ends.md` **nor** `non_generalizable.md`; `champion.md` untouched; the same change is re-queued as `{exp_id}_stack` for retest against the new champion. |
| `DISCARD` | A real, valid experiment that did not improve on train (or was invalid on train). Evidence → team `dead_ends.md`. |
| `REJECTED_TEST` | Improved on train, failed the held-out test gate. Evidence about generalization → team `non_generalizable.md`, never `dead_ends.md`. |
| `FAILED` | Infra/harness failure, unapplied diff, or scope abort — it **tested nothing**. |

`FAILED` rows must be excluded from stagnation streaks and dead-end counting, and their queue item
is re-queued rather than closed. `NEAR_MISS` rows are likewise excluded from the stagnation window
(a `NEAR_MISS` proves that some candidate WAS promoted in that rotation) and must never count as a
failure anywhere: not toward a dead end, not toward `refuted_discards` / `rejected_test`, not
toward a hypothesis falsification. `test_fitness` / `test_is_valid` are populated on `NEAR_MISS`
rows exactly as on `KEEP` rows — the test eval did run and did pass.

### Counting rules

These bind every consumer of the ledger — the queries below, runbook Step 5g, ROLE-MONITOR and
`task/meta_diagnostics.py`. They are stated once here so the same run cannot yield two verdicts.

1. **FAILED is never a denominator.** `keep_rate`, `near_miss_rate`, `valid_rate` and
   `duplicate_rate` are all computed over rows whose outcome is **not** `FAILED`. A `FAILED` row
   tested nothing, so dividing by it lets one bad afternoon on the eval head halve the keep rate and
   manufacture a "the science has stalled" reading out of pure infra noise. Report the `FAILED`
   count on its own line instead — a rising one is a real signal, just an infrastructure one.
2. **FAILED is excluded from the duplicate scan too.** A `FAILED` item is re-queued by contract and
   legitimately re-run under the **same** `exp_id`, so its second row is a retry, not a duplicate
   proposal. Counting it flags the recovery path as a defect.
3. **`is_valid: null` is unknown, not invalid.** It means the harvest could not read the gate result
   (missing result file, row reconstructed from the post alone). Scoring unknowns as invalid turns a
   logging gap into an apparent science problem. Compute `valid_rate` over rows with a *known*
   `is_valid` and report the unknown count separately; a row of unknown validity is also not
   eligible to set `best_fitness`.
4. **The shared baseline probe is not a team's KEEP.** The baseline is posted with
   `type:infrastructure` and team `none`, so it should never reach the ledger as a team result — but
   consumers still exclude `exp_id == "baseline_shared"` (or `infrastructure_probe: true`)
   defensively from KEEP counting and from the stagnation window. Counted as a real `KEEP` it
   inflates one team's keep rate and parks a success nobody earned in the window, where it
   suppresses exactly the regroup the run needs.
   Know which of those two checks is actually load-bearing: `infrastructure_probe` is **not** one of
   the 16 keys above, and `cycle_ledger` coerces every harvested row to exactly those keys, so a row
   this orchestrator wrote can never carry the marker — for ledger rows the `exp_id` check is the one
   that fires. The marker check covers rows from any other producer (hand-written or repaired rows, a
   future harvest path) and is forward compatibility, **not** redundancy. Do not rely on it as a
   second line of defence; if a future infrastructure probe needs a name other than
   `baseline_shared`, add `infrastructure_probe` to the ledger keys at the same time.
5. **`NEAR_MISS` stays in the rate denominators** (it is a real experiment that cleared both gates)
   but is excluded from the *stagnation window*, per the paragraph above.

## 3. Native cycle-child completion artifacts

**Written by:** Orchestrator for each analyst or CPU-eval cycle child harvested in runbook Step 5d.
**Location:** `{FOCUS_ROOT}/logs/raw/{agent}_{timestamp}_{nonce}.json`

```json
{
  "agent": "run01_cpu1",
  "cycle": 3,
  "codex_target": "/root/run01_cpu1_cpu_cycle_3_a1b2c3d4e5f60718",
  "codex_agent_id": null,
  "codex_task_name": "run01_cpu1_cpu_cycle_3_a1b2c3d4e5f60718",
  "terminal_status": "completed",
  "promise_received": true,
  "final_message": "<promise>run01_cpu1 cycle complete (branch=normal)</promise>",
  "ended_at": "2026-07-29T12:00:00+00:00"
}
```

This is the run-local handoff evidence available from a native Codex child, not a full stdout/tool
transcript. The full child thread remains attached to the persisted parent Codex session.

## 4. agents/{name}/actions.md — Per-Agent History

**Written by:** Each agent at the end of its session (Step 4 in HEARTBEAT.md).
**Format:** Human-readable markdown, auto-truncated at 100 lines.

```markdown
## Session 2026-03-29T10:00:00Z — run01_cpu1

### State
- Champion: 0.990 (run_baseline)
- Team: architecture (2 pending, 0 claims)

### Experiments
1. exp_kv_shift → 0.985 KEEP (delta=-0.005) ← NEW CHAMPION
2. exp_gated_attn → 1.002 DISCARD (delta=+0.012)

### Duration: 8m 30s
```

## 5. Workspace Agent Files — Cross-Agent Visibility

**Written by:** Each agent via PATCH after session.
**Read by:** Orchestrator + other agents to see who's active.

```yaml
---
agent: run01_cpu1
last_seen: "2026-03-29T10:08:30Z"
status: idle
session_count: 5
last_experiment: exp_gated_attn
last_outcome: DISCARD
last_metric: 1.002
---
```

## Analysis Queries

### "How many experiments per team?"
```bash
cat logs/experiments.jsonl | python3 -c "
import sys, json
from collections import Counter
teams = Counter()
for line in sys.stdin:
    d = json.loads(line)
    teams[d['team']] += 1
for team, count in teams.most_common():
    print(f'{team}: {count}')
"
```

### "What's the KEEP rate per team?"
FAILED rows tested nothing, so they are excluded from the denominator (Counting rule 1). `NEAR_MISS`
rows ARE real experiments and stay in the denominator (rule 5); they are reported alongside `KEEP`
because they also cleared both gates. The baseline probe is skipped so it cannot show up as a team's
KEEP (rule 4).
```bash
cat logs/experiments.jsonl | python3 -c "
import sys, json
from collections import defaultdict, Counter
stats = defaultdict(Counter)
for line in sys.stdin:
    d = json.loads(line)
    if d.get('exp_id') == 'baseline_shared' or d.get('infrastructure_probe'):
        continue
    stats[d['team']][d['outcome']] += 1
for team, s in stats.items():
    total = s['KEEP'] + s['NEAR_MISS'] + s['DISCARD'] + s['REJECTED_TEST']
    rate = s['KEEP'] / total * 100 if total else 0
    print(f'{team}: {s[\"KEEP\"]}/{total} KEEPs ({rate:.0f}%), {s[\"NEAR_MISS\"]} near-miss (both gates passed, lost the race), {s[\"REJECTED_TEST\"]} rejected on test, {s[\"FAILED\"]} failed')
"
```

### "How many sessions timed out?"
```bash
cat logs/sessions.jsonl | python3 -c "
import sys, json
from collections import Counter
status = Counter()
for line in sys.stdin:
    d = json.loads(line)
    status[d['status']] += 1
for s, c in status.most_common():
    print(f'{s}: {c}')
"
```

### "Timeline of champion improvements"
The ledger is chronological, so reading it top-to-bottom IS the timeline. The baseline probe is
skipped — it established the anchor, it did not improve on it (Counting rule 4).

Every numeric column may legitimately be `null` — that is this file's own format rule ("all 16 keys,
`null` for what could not be determined"), and `delta` / `mean_rel_energy` / `is_valid` are exactly
the fields a resumed experiment or a missed result-file read leaves unset. So the numbers go through
`num()` rather than straight into a `:.6f` slot: a bare `f'{None:.6f}'` raises `TypeError` and kills
the whole timeline mid-stream, losing every row after the first incomplete one. A single `n/a` cell
is the correct output for a missing number.
```bash
cat logs/experiments.jsonl | python3 -c "
import sys, json

def num(x, spec='.6f'):
    try:
        return format(float(x), spec)      # null / missing / non-numeric -> 'n/a',
    except (TypeError, ValueError):        # never an exception that ends the timeline
        return 'n/a'

for line in sys.stdin:
    d = json.loads(line)
    if d.get('exp_id') == 'baseline_shared' or d.get('infrastructure_probe'):
        continue
    if d.get('outcome') == 'KEEP':
        print(f'{d.get(\"ts\") or \"(undated)\"} {str(d.get(\"exp_id\") or \"?\"):40s} train={num(d.get(\"fitness\"))} test={num(d.get(\"test_fitness\"))} (delta={num(d.get(\"delta\"), \"+.6f\")}) by {d.get(\"agent\")} ({d.get(\"team\")})')
"
```

### "Which mechanisms improved on train but failed the held-out test gate?"
Same null-safe formatting as the timeline query above, and for the same reason: a `REJECTED_TEST` row
whose train `fitness` never made it into the ledger must show as `n/a`, not abort the listing.
```bash
cat logs/experiments.jsonl | python3 -c "
import sys, json

def num(x, spec='.6f'):
    try:
        return format(float(x), spec)
    except (TypeError, ValueError):
        return 'n/a'

for line in sys.stdin:
    d = json.loads(line)
    if d.get('outcome') == 'REJECTED_TEST':
        print(f'{d.get(\"ts\") or \"(undated)\"} {str(d.get(\"exp_id\") or \"?\"):40s} train={num(d.get(\"fitness\"))} test={num(d.get(\"test_fitness\"))} valid={d.get(\"test_is_valid\")} ({d.get(\"team\")})')
"
```

## Directory Setup

The orchestrator creates log directories before first launch:

```python
(FOCUS_ROOT / "logs" / "raw").mkdir(parents=True, exist_ok=True)
# sessions.jsonl and experiments.jsonl are created on first write (append mode)
```
