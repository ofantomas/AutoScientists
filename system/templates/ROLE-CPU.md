---
name: multi-agent-focus-cpu-eval
description: CPU-eval agent protocol — claim from team queue, evaluate candidate algo.py on the remote eval pool, record results
---

# CPU-Eval Agent Protocol

**STOP. Did you go through HEARTBEAT Part 0 first?** If not, go back. This file is only for agents who have been routed into Part 4 (Normal Cycle) by the Mode Selector. If the Mode Selector sent you to Part 2 (Discussion) or Part 3 (No-Team), do NOT read or execute this file — follow that branch instead.

You evaluate candidate optimizers on a **remote, deterministic, CPU-only** eval pool (a Redis-backed distributed validation worker pool on the eval-head host). There is no GPU and no CUDA anywhere in your workflow. You belong to a team.

## Two rules that override everything below

1. **No team → no work.** Enforced by HEARTBEAT Part 0. If you reach this file, `MY_TEAM` is set.
2. **Every experiment MUST have a complete AnonAPI API trail:** POST [PROPOSAL] → add to queue → claim → evaluate → write result file → release claim → POST [RESULT]. If KEEP, also PUT champion.md. This applies whether the experiment came from an analyst's queue or you self-designed it. Skip any step → invisible work → forbidden.

## CRITICAL: YAML Frontmatter Parsing

The API does NOT parse YAML frontmatter. Always parse client-side:

```python
import yaml

def parse_frontmatter(api_response):
    content = api_response.get("content", "")
    parts = content.split("---")
    if len(parts) >= 3:
        return yaml.safe_load(parts[1]) or {}
    return {}
```

## Your Cycle

### Step 0 — Find Your Team (HARD GATE)

```python
# Read roster from main workspace (parse YAML client-side)
roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_frontmatter(roster_raw).get("teams", {})

MY_TEAM = TEAM_WS_ID = None
for name, t in roster.items():
    if AGENT_NAME in t.get("members", []):
        MY_TEAM = name
        TEAM_WS_ID = t["workspace_id"]
        break

if MY_TEAM is None:
    # No team assigned. Per Rule 1, exit immediately. Do NOT run experiments.
    print(f"[EXIT] {AGENT_NAME}: no team in roster ({len(roster)} teams). "
          f"Waiting for monitor to form teams.")
    import sys; sys.exit(0)
```

**Do not** wrap this in a try/except that swallows the exit and continues. The only valid response to "no team" is to exit cleanly.

### Step 1 — Eval Pool Availability

Evaluation is deterministic and CPU-only — there is no single device to contend for. The eval pool
is the remote Redis-backed distributed validation worker pool on the eval-head host; concurrency is
bounded by the worker count, not by a device. You do **not** need to check `nvidia-smi` (there is no
GPU). If your `scp`/`ssh` to the eval head fails or the eval call blocks past its timeout (no healthy
`xtb` workers serving Redis), treat that as an infrastructure problem: post a `[SUGGESTION]` flagging
the eval head / worker pool and do analyst work instead this cycle.

### Step 1.5 — Shared-Baseline Coordination — REQUIRED

If the champion file is in `awaiting_baseline` state (no metric_value
set yet), the WHOLE SYSTEM needs exactly ONE baseline run — not one per
team. Use claim-based coordination to avoid duplicated baselines.

```python
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)

if champ.get("status") == "awaiting_baseline":
    # Try to claim the baseline lock with If-None-Match (atomic).
    # First CPU-eval agent to arrive wins the lock and runs the baseline; every
    # other agent reads a real experiment from queue instead.
    r = requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/baseline_lock.md",
                     headers={**HEADERS, "If-None-Match": "*"},
                     json={"content": f"holder: {AGENT_NAME}\nclaimed_at: {NOW}\n"})
    if r.status_code in (200, 201):
        # We got the lock — run champion unchanged as the shared baseline,
        # then seed champion.md for everyone.
        item = {"id": "baseline_shared",
                "axis": "baseline",
                "direction": "none",
                "value": 0,
                "diff": "Evaluate champion algo.py unchanged (shared baseline)",
                "infrastructure_probe": True}
    else:
        # Someone else holds the lock — skip baseline, proceed to real
        # experiment from queue. If queue is empty, wait one rotation.
        print("[BASELINE] another agent is running the shared baseline; "
              "picking a real experiment from queue instead")
        # fall through to Step 3 (queue claim)
```

**Never run baseline on a team-by-team basis.** The champion metric is
global — one run is sufficient.

### Step 2 — Read Champion Config

```python
# Read champion config from main workspace
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)
champ_version = champ_raw.get("version", 0)  # Save for race condition check later

# Read canonical champion algo.py (SINGLE SOURCE OF TRUTH)
# Located at: {FOCUS_ROOT}/champion/algo.py
# Copy it to your workspace before making changes. `algo.py` is the ONLY file
# you edit and the ONLY file the evaluator needs — there is no prepare.py /
# pyproject.toml / uv.lock for this task. The evaluator (eval_candidate.py,
# validate.py, molecules/, metrics.yaml) lives in the sella checkout on the
# remote eval head; you never copy it locally.
import shutil
from pathlib import Path
workdir = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")
workdir.mkdir(parents=True, exist_ok=True)
src = Path(f"{FOCUS_ROOT}/champion/algo.py")
if not src.exists():
    # Fallback to the task's shipped baseline algo.py.
    src = Path(f"{FOCUS_ROOT}/task/repo/algo.py")
shutil.copy(src, workdir / "algo.py")
```

**Never read algo.py from another agent's workspace.** Always use `{FOCUS_ROOT}/champion/algo.py`.

### Step 2b — Verify Task Specifications

**Before claiming any experiment, verify you understand the task requirements:**

```python
# Read task specification
task_spec_path = f"{FOCUS_ROOT}/task/TASK.md"
with open(task_spec_path) as f:
    task_content = f.read()

# Confirm you understand the metric, the validity gate, and any constraints the
# task spec imposes before applying a diff. Misreading the task requirements will
# invalidate your results.
```

### Step 3 — Claim Experiment from Team Queue (REQUIRED)

**Safety: abort if a prior unposted result still sits in `result_latest.json`** (HEARTBEAT Part 0 Check C should have caught this; verify once more to prevent orphaned results):

```python
import json
from pathlib import Path
_p = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/result_latest.json")
if _p.exists():
    _pend = json.loads(_p.read_text())
    if not _pend.get("posted_to_workshop") and _pend.get("status") == "complete":
        raise RuntimeError(f"[SAFETY] unposted result for {_pend.get('exp_id')} — re-enter HEARTBEAT, go to Part 5")
```

Check your team's queue for pending experiments.

```python
queue_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                         headers=HEADERS).json()
queue = parse_frontmatter(queue_raw)
pending = queue.get("pending", [])

if pending:
    # Normal path: claim from queue
    item = pending[0]
else:
    # EMPTY QUEUE — self-propose a bold experiment within your team's
    # dimension. Read your team's strategy.md, dead_ends.md, and the
    # champion code to pick the highest-value untested change. Then:
    #   1. Post a [PROPOSAL] to the workshop (full rationale + diff)
    #   2. Add it to your team's queue.md
    #   3. Claim it below
    # This maintains the full API trail while not wasting eval time.
    # Teams are HYPOTHESIS-based, not axis-based — propose any axis as
    # long as the change is consistent with your team's hypothesis.
    # Prefer changes that are:
    #   - Bold (ambition quota: ≥10% param change, or structural variant)
    #   - Not in dead_ends.md
    #   - Grounded in champion code analysis, not speculation
    item = self_designed_item  # you create this from your analysis
```

**Every experiment must have a full API trail:** [PROPOSAL] post → queue
entry → claim → evaluation → result file → [RESULT] post. Self-designed
experiments follow the same trail; the only difference is the cpu-eval agent
writes the proposal instead of an analyst.

**Every queue item and [PROPOSAL] MUST include axis / direction / value
tags.** These feed the empirical-priors ranking, direction-diversity
check, and failure-range check. Claiming or self-designing an item
without these tags is forbidden — if the queue item is missing them,
reject the claim and post a [SUGGESTION] asking the analyst to fix the
queue.

**Teams are hypothesis-based, not axis-based.** You may propose any
axis as long as the change is consistent with your team's hypothesis.
If another team's proposal looks promising and shares your hypothesis's
lens, you can claim it.
# **Discussion-gate check:** if the item is `discussion_pending: true`,
# verify its [PROPOSAL] post has at least one comment from a non-author
# before claiming. A comment from the proposer themselves does not count.
# Skip items that don't yet meet this bar and pick the next one.
#
# Two auto-clear overrides prevent the gate from starving the queue
# (observed in gpt-nano-agents 2026-05-26: cycles 7-12 had CPU-eval agents
# posting near-empty "[CPU-REVIEW] acknowledged" comments just to satisfy
# the gate, burning API budget for no information value):
#
#   1. Time-based: if the proposal was posted more than DISCUSSION_GRACE
#      ago (default 15 min), claim it anyway. The discussion window has
#      passed; agents who wanted to comment had their chance.
#   2. Queue-starvation: if THIS is the only `discussion_pending: true`
#      item remaining and there are no non-pending items either, claim
#      it. A blocked CPU-eval agent is worse than a thinly-discussed proposal.
import time
DISCUSSION_GRACE_SEC = 15 * 60

if item.get("discussion_pending"):
    proposal_id = item.get("proposal_post")
    cleared = False

    # Override 1: time-based grace
    proposed_at = item.get("proposed_at") or item.get("created_at")
    if proposed_at:
        try:
            from datetime import datetime, timezone
            t0 = datetime.fromisoformat(proposed_at.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - t0).total_seconds() > DISCUSSION_GRACE_SEC:
                cleared = True  # waited long enough
        except Exception:
            pass

    # Override 2: starvation — this is the only claimable item
    if not cleared:
        other_claimable = [it for it in (queue.get("pending") or [])
                           if it.get("id") != item["id"]
                           and not it.get("discussion_pending")]
        if not other_claimable:
            cleared = True  # rather claim discussion-pending than idle the agent

    # Default path: require a non-author comment
    if not cleared and proposal_id:
        comments = requests.get(f"{API}/posts/{proposal_id}/comments",
                                headers=HEADERS).json().get("data", [])
        proposer = item.get("proposed_by", "")
        non_author = [c for c in comments
                      if proposer not in str(c.get("author", ""))]
        if not non_author:
            # Not yet discussed — skip to next item
            item = None  # fall through to next pending item or self-design

# Claim via read-modify-PUT with If-Match (DO NOT use PATCH — it corrupts nested YAML
# frontmatter like pending: lists. Confirmed to destroy queue.md across teams.)
queue_version = queue_raw.get("version", 0)
fm = parse_frontmatter(queue_raw)
fm.setdefault("claims", {})[AGENT_NAME] = {"exp_id": item["id"], "claimed_at": now}
body = queue_raw.get("content", "").split("---", 2)[-1]
new_content = f"---\n{yaml.safe_dump(fm, sort_keys=False)}---{body}"
# Validate round-trip before writing
assert yaml.safe_load(new_content.split("---")[1]) == fm, "frontmatter round-trip failed"
r = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
    headers={**HEADERS, "If-Match": str(queue_version)},
    json={"content": new_content})
if r.status_code == 409:
    # Conflict — another agent claimed concurrently. Re-read and retry or pick a different item.
    pass
```

If queue is empty, design your own experiment. Your only constraint
is **consistency with your team's hypothesis**: the change you propose
must be one your team's hypothesis predicts will improve the metric.
Any axis is fair game. This is the triangulation value of
hypothesis-based teams — the same experiment may be proposed by
different teams for different reasons.

```python
# Discover your team's context for self-designed experiments
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json()["files"]
# Read strategy.md, dead_ends.md, analysis/ files from YOUR team
# Design an experiment within YOUR dimension
```

### Step 3b — Dedup Check

Before training, verify this experiment hasn't already been run AND isn't already in the code:

```python
# 1. Search workspace results for similar experiments
hits = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/search?q={mechanism_keyword}",
                    headers=HEADERS).json()["results"]
# If results/ files already cover this mechanism, skip it

# 2. Search team dead ends
team_hits = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/search?q={mechanism_keyword}",
                         headers=HEADERS).json()["results"]
# If your mechanism appears in dead_ends or similar analysis, skip it

# 3. Check if the mechanism already exists in champion code
champion_code = open(f"{FOCUS_ROOT}/champion/algo.py").read()
if mechanism_keyword.lower() in champion_code.lower():
    print(f"ALREADY IN CODE: {mechanism_keyword} — skip this experiment")
    # Release claim and pick next experiment

# 4. **Target validation** — if your change reads or writes a named variable or
#    collection in the target code (a list of params, a config dict, a feature
#    set), verify that collection is non-empty and actually wired into the code
#    path you expect. Helper variables are sometimes defined but never referenced
#    — tuning them produces noise-only deltas that look like real signal.
#    Catch this BEFORE running, not after cascades of dead hypotheses.
#
# Example pattern:
#   target_collection = f"{group_name}_items"
#   if f"{target_collection} = []" in code:
#       print(f"DEAD TARGET: {target_collection} is empty — change would be a no-op")
#       # Release claim, skip, and post a [SUGGESTION] for code cleanup
```

### Step 3c — External Repo Setup (if experiment requires it)

If the claimed experiment depends on a GitHub repo or pretrained checkpoint
that is not already installed in `{FOCUS_ROOT}/.cache/repos/`:

1. Read `{FOCUS_ROOT}/system/external-repo-setup/SKILL.md` — it is the
   complete protocol for cloning repos, installing deps, downloading weights,
   extracting embeddings, and caching them.
2. Check whether a teammate already did the setup:
   ```python
   # Search team workspace for setup notes
   team_hits = requests.get(
       f"{API}/workspaces/{TEAM_WS_ID}/search?q=setup_{REPO_NAME}",
       headers=HEADERS
   ).json()["results"]
   ```
   If `knowledge/setup_{REPO_NAME}.md` exists, load pre-cached embeddings
   instead of re-running extraction.
3. After successful setup, write `knowledge/setup_{REPO_NAME}.md` to the
   team workspace so other CPU-eval agents can reuse the cached embeddings.

**Time budget:** factor in 15-30 min for first-time setup when deciding
whether to run this experiment or pick a lighter one from the queue instead.

### Step 4 — Apply Change and Evaluate

Apply ONE change from the experiment's diff to `algo.py`, then **block synchronously** on the remote
eval. Detached / fire-and-forget evaluation is forbidden: if the agent's claude session ends before
parsing the eval JSON, the real metric is computed but never recorded — the entire cycle's work
vanishes. The agent MUST wait for the `ssh` eval call to return and then run Steps 5–8 in the same
session.

Evaluation is **deterministic and CPU-only**: the candidate's `minimize_func` is cloudpickled and run
against a fixed molecule set on a remote Redis-backed worker pool. There is no GPU, no CUDA, no seed
variance — the same `algo.py` always produces the same score.

**Before evaluating, verify the diff actually landed.** If the Edit tool said `old_string not found`,
`patch -p1` printed `FAILED` / `Hunk #N FAILED`, or the resulting `algo.py` is byte-identical to
`champion/algo.py`, the proposal was NOT tested — evaluation would just re-measure the baseline. Set
`item["diff_applied"] = False`, skip evaluation, and post `[RESULT] {exp_id}: FAILED` so the proposal
can be re-queued with a fresh diff. A phantom KEEP from an unapplied diff corrupts the champion
lineage — never let the unchanged baseline be mistaken for evidence about a change.

```python
import filecmp
diff_applied = not filecmp.cmp(
    str(rep / "algo.py"),
    f"{FOCUS_ROOT}/champion/algo.py",
    shallow=False,
)
item["diff_applied"] = diff_applied
if not diff_applied:
    print(f"[STEP4] diff for {exp_id} did NOT apply — algo.py matches champion. "
          f"Marking FAILED and skipping evaluation.")
    # Jump to Step 5 with outcome="FAILED", score=None.
```

**Eval-head connection parameters** (the remote sella checkout serving the worker pool):

```python
EVAL_HOST       = "cpu-33"      # eval-head a002dc-0002 (local ssh alias cpu-33): scp candidate here, ssh in to run the eval
EVAL_REDIS_HOST = "localhost"   # Redis host as seen from the eval head
EVAL_REDIS_PORT = 6390          # Redis port as seen from the eval head (a002dc-0002 local redis, no tunnel)
SELLA_CHECKOUT  = "/home/tsypin/opt_problem_as_sella"   # checkout on the eval head; eval_candidate.py is at its root
EVAL_PYTHON     = "/home/tsypin/miniconda3/envs/gigaopt/bin/python"
```

**Run the deterministic CPU eval.** `scp` the candidate `algo.py` to a UNIQUE remote path
(`/tmp/cand_${AGENT}_${exp}.py` — unique per agent+experiment so concurrent evals never collide),
then `ssh` into the eval head and run `eval_candidate.py`. This is SYNCHRONOUS — `subprocess.run`
blocks until the eval JSON is returned. NEVER use `subprocess.Popen` without an immediately-following
`proc.wait()`; NEVER use `nohup ... &`; NEVER exit the agent session while the eval is running.

```python
import json, os, subprocess
from pathlib import Path
from datetime import datetime, timezone

ws  = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
rep = ws / "repo"
out, err = ws / f"eval_{exp_id}.stdout", ws / f"eval_{exp_id}.stderr"

remote_cand = f"/tmp/cand_{AGENT_NAME}_{exp_id}.py"   # UNIQUE per agent+experiment

sentinel = {
    "status": "running", "posted_to_workshop": False,
    "exp_id": exp_id, "agent": AGENT_NAME, "item": item, "queue_claimed": True,
    "direction": direction, "score": None, "fitness": None, "is_valid": None,
    "algo_path":   str(rep / f"algo_{exp_id}.py"),
    "remote_cand": remote_cand,
    "stdout_path": str(out), "stderr_path": str(err),
    # Record OUR pid so HEARTBEAT Part 0 Check C can tell whether we died
    # ungracefully (rate limit, SIGKILL) vs. legitimately still evaluating.
    # `pid: None` would make _alive() return False and incorrectly route a
    # live cycle to resume-and-post.
    "pid": os.getpid(), "monitor_id": None, "description": description,
    "launched_at": datetime.now(timezone.utc).isoformat(),
}
(ws / "result_latest.json").write_text(json.dumps(sentinel, indent=2, default=str))

# 1. Copy the candidate algo.py to a unique path on the eval head.
subprocess.run(["scp", str(rep / "algo.py"), f"{EVAL_HOST}:{remote_cand}"],
               check=True, timeout=120)

# 2. Run the deterministic eval on the eval head; it prints the score dict as
#    a single JSON line on stdout (last line). BLOCK until it returns.
eval_cmd = (
    f"cd {SELLA_CHECKOUT} && {EVAL_PYTHON} eval_candidate.py "
    f"--program {remote_cand} --redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}"
)
result = subprocess.run(
    ["ssh", EVAL_HOST, eval_cmd],
    capture_output=True, text=True,
    timeout=3600,   # the driver blocks per-molecule up to the worker timeout; size generously
)
out.write_text(result.stdout)
err.write_text(result.stderr)

# 3. Parse the LAST stdout line as the score JSON.
score = None
for line in reversed(result.stdout.strip().splitlines()):
    line = line.strip()
    if line.startswith("{"):
        try:
            score = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

eval_succeeded = (result.returncode == 0) and (score is not None)
sentinel["status"] = "complete" if eval_succeeded else "failed"
sentinel["returncode"] = result.returncode
sentinel["score"] = score
sentinel["fitness"]  = (score or {}).get("fitness")
sentinel["is_valid"] = (score or {}).get("is_valid")
(ws / "result_latest.json").write_text(json.dumps(sentinel, indent=2, default=str))
# Now continue to Step 4b → Step 5 in this same session — do NOT exit until
# the result is posted. If score is None, treat as a FAILED eval (infra error).
```

The score dict is the authoritative result. Its keys (emitted by `eval_candidate.py`):

| Key | Meaning |
|---|---|
| `fitness` | **Primary, lower is better.** `= mean_rel_steps` when valid; `= 1000.0` when invalid. |
| `is_valid` | `1` iff no errors AND `max_final_energy_delta_kcal_mol < 1.0`; else `0`. |
| `mean_rel_steps` | Mean relative force-call count vs reference. |
| `mean_rel_energy` | Diagnostic only — does NOT gate validity. |
| `max_final_energy_delta_kcal_mol` | Worst per-molecule final-energy gap; drives the validity gate. |
| `converged` | Fraction of molecules whose convergence check passed. |
| `invalid_reason` | Human string when invalid; empty when valid. |
| `duration_s`, `num_results`, `num_errors`, `lower_is_better` | Diagnostics. |

A candidate is an improvement **iff `is_valid == 1` AND `fitness` is lower than the champion**
(with the seeding exception in Step 5). `fitness == 1000.0` (or `is_valid == 0`) means rejected.

After the eval, save a stamped copy of the candidate to **agent-local paths** (never `task/` or
`champion/`):

```python
import shutil
from pathlib import Path

agent_workspace = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

# Save a stamped copy of the candidate algo.py for this experiment
agent_algo = agent_workspace / f"algo_{exp_id}.py"
shutil.copy(agent_workspace / "algo.py", agent_algo)

print(f"[ISOLATION] saved candidate → {agent_algo}")
```

**Stamped files belong here in agent-local paths.** The shared `champion/algo.py` is propagated by the KEEP-winning agent in Step 7b1 (see below) — not from this step and not by the orchestrator. The stamped copy must exist before Step 7b1 can copy it.

### Step 4b — Analyze Eval Diagnostics — REQUIRED

After the eval returns, read the diagnostic fields in the score dict before recording the result.
This takes a few seconds and explains WHY a candidate passed or failed, not just its `fitness`.

Check these from the score dict:

1. **Validity first.** If `is_valid == 0`, the candidate is rejected regardless of `fitness`. Read
   `invalid_reason` and `max_final_energy_delta_kcal_mol`: a value `>= 1.0` means some molecule's
   final energy drifted outside the 1 kcal/mol band (the optimizer stopped too early / converged to a
   worse minimum). `num_errors > 0` with reasons like `"exceeded max force-call budget"` means the
   optimizer blew past `max_steps` on at least one molecule. Note which failure mode in the result
   file — it tells analysts whether to loosen step-aggressiveness or tighten the convergence test.

2. **Energy headroom.** Even when valid, record `max_final_energy_delta_kcal_mol`. A value close to
   `1.0` means the candidate is near the validity cliff — further step reductions risk tipping it
   invalid. A comfortably-low value means there is room to trade accuracy for fewer steps.

3. **Speed and coverage.** Record `mean_rel_steps` (= `fitness` when valid) and `converged` (fraction
   of molecules whose convergence check passed). A low `mean_rel_steps` with `converged == 1.0` and
   low energy delta is the ideal profile.

Include these diagnostics in every result file under an `## Eval Diagnostics` section. Analysts use
this to understand WHY a KEEP worked (or why a fast candidate was invalid), not just that it did.

### Step 5 — Record Result

**Before recording: re-read champion.md to handle race conditions.**

```python
# Re-read champion (may have changed during our eval).
fresh_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
fresh_champ = parse_frontmatter(fresh_raw)
metric_name = fresh_champ.get("metric_name", "fitness")  # task defines this in champion.md
direction   = fresh_champ.get("direction", "minimize")   # always "minimize" for this task
fresh_version = fresh_raw.get("version", 0)

race_condition = (fresh_version != champ_version)
if race_condition:
    print(f"Champion changed during eval (v{champ_version} → v{fresh_version})")

# Pull this candidate's metrics from the deterministic eval score dict.
our_metric = float(score["fitness"]) if score is not None else None
cand_valid = (score is not None) and (int(score.get("is_valid", 0)) == 1)

# Is there a VALID champion yet? The baseline algo.py may itself be INVALID
# under the energy gate, so champion.md may have no valid fitness recorded.
# `status: awaiting_baseline` or a missing/None metric_value ⇒ no valid champion.
champ_status   = fresh_champ.get("status")
champ_fitness  = fresh_champ.get("metric_value")
# Only an explicit `awaiting_baseline` status means "no champion yet". A normal champion.md has
# NO status field (champ_status is None) — the champ_fitness check below is what gates emptiness.
# (Treating None status as no-champion seeds ANY valid candidate over a real champion — a bug.)
have_valid_champion = (champ_status != "awaiting_baseline") and \
                      (champ_fitness is not None) and (float(champ_fitness) < 1000.0)
current_best = float(champ_fitness) if have_valid_champion else None

# IMPORTANT: a result is only meaningful if the proposed diff actually applied.
# If Step 4's edit failed (old_string not found, patch rejected, algo.py identical
# to champion), the score you measured is the UNCHANGED baseline — NOT evidence
# about the proposal. Mark FAILED so the orchestrator skips champion promotion and
# analysts can re-queue the proposal with a fresh diff.
diff_applied = bool(item.get("diff_applied", True))  # default True for legacy items

# ── KEEP decision (deterministic; strict; with a seeding exception) ──────────
#   * Invalid candidate (is_valid==0 / fitness==1000.0)        → DISCARD.
#   * No VALID champion yet → first VALID candidate is a KEEP   (SEEDING),
#     regardless of its fitness.
#   * Otherwise KEEP iff VALID and fitness lower by at least KEEP_MARGIN.
# Eval is deterministic, but we still require a real margin to promote: sub-margin
# "wins" are noise-level and just make the champion crawl. KEEP_MARGIN is in
# mean_rel_steps units (1e-3 ≈ 0.1% fewer relative force calls).
KEEP_MARGIN = 1e-3
if not diff_applied:
    outcome = "FAILED"
elif not cand_valid:
    outcome = "DISCARD"          # invalid candidates are worthless: correctness first
elif not have_valid_champion:
    outcome = "KEEP"             # SEEDING: first valid candidate seeds the champion
elif (current_best - our_metric) >= KEEP_MARGIN:  # require >= 1e-3 real improvement
    outcome = "KEEP"
else:
    outcome = "DISCARD"

# Signed improvement vs the current champion (negative == better for this
# minimize task). Used in the [RESULT] post, champion.md, dead_ends, and SOURCE.
# No valid champion to compare against ⇒ delta is undefined (0.0 placeholder).
delta = (our_metric - current_best) if (our_metric is not None and current_best is not None) else 0.0
```

Write to **main workspace** (visible to all teams):
```python
requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{item['id']}.md",
    headers=HEADERS, json={"content": result_markdown})
```

### Step 6 — Release Claim AND Move Item to Completed

Atomically do BOTH in a single read-modify-PUT: drop the claim AND move
the experiment record from `pending:` → `completed:`. Doing only the first
(the historic pattern) leaves stale rows in `pending:`, forcing the next
analyst cycle to hand-prune the queue before they can propose. Observed in
gpt-nano-agents 2026-05-26: cycles 2-4 each had analysts spending several
turns cleaning up DISCARDed-but-still-pending items.

```python
# Read-modify-PUT with If-Match (NEVER PATCH — corrupts nested pending: list).
# Missing claim or 409 is benign on resume (monitor's 30-min sweep may have cleared it).
from datetime import datetime, timezone
q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json()
q_fm  = parse_frontmatter(q_raw)

claim_removed = q_fm.get("claims", {}).pop(AGENT_NAME, None) is not None

# Move the just-finished item from pending → completed in the same write.
pending = q_fm.get("pending", []) or []
completed = q_fm.get("completed", []) or []
remaining = []
for it in pending:
    if it.get("id") == exp_id:
        it = dict(it)
        it["completed_at"] = datetime.now(timezone.utc).isoformat()
        it["completed_by"] = AGENT_NAME
        it["outcome"]      = outcome  # KEEP / DISCARD / FAILED from Step 5
        it["val_score"]    = our_metric
        completed.append(it)
    else:
        remaining.append(it)
q_fm["pending"]   = remaining
q_fm["completed"] = completed

if claim_removed or len(remaining) != len(pending):
    q_body = q_raw.get("content", "").split("---", 2)[-1]
    q_new  = f"---\n{yaml.safe_dump(q_fm, sort_keys=False)}---{q_body}"
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
        headers={**HEADERS, "If-Match": str(q_raw.get("version", 0))},
        json={"content": q_new})  # 409 OK — continue to Step 7/8
```

### Step 7 — Update Champion (KEEP only)

If `outcome == "KEEP"` (valid AND strictly better than the current champion, or the first valid
candidate under the seeding rule):

**CRITICAL: Before propagating, make ALL improvements unconditional in your algo.py.**
Do NOT gate changes behind `if EXPERIMENT_ID == "exp_foo"` or similar checks.
Every improvement must be baked into the code as the default behavior.
If you find gated code from previous experiments, make it unconditional too.

```python
# Bad:  if EXPERIMENT_ID == "exp_my_change": value *= factor
# Good: value *= factor  (always active)
```

**Eval is deterministic — promote unconditionally on KEEP.** There is NO multi-seed gate and NO
noise-floor band: a candidate that is valid and strictly lower in `fitness` than the champion (or the
first valid candidate under the seeding rule) is a real, reproducible improvement. Write the champion
immediately. Do not re-run anything to "confirm" the result — re-running the same `algo.py` produces
byte-identical scores.

#### Step 7a — Extract Reproduction Information

```python
import re

# 1. Read YOUR algo.py docstring (experiment description)
with open(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/algo.py") as f:
    code = f.read()
docstring_match = re.search(r'"""(.*?)"""', code, re.DOTALL)
experiment_description = docstring_match.group(1).strip() if docstring_match else "No description provided"

# 2. The full eval result dict (from Step 4) IS the structured result — no stdout
#    parsing needed. It is deterministic and complete.
result_metrics = score  # the JSON dict: fitness, is_valid, mean_rel_steps, etc.
```

#### Step 7b — Build Complete champion.md

**champion.md must be a complete standalone reproduction recipe.** Include ALL information needed to reproduce without reading algo.py.

**The recorded `metric_value` is the single deterministic `fitness`.** Eval is deterministic — there
is exactly one measurement per `algo.py`, and re-running it reproduces the same value byte-for-byte.
There are no seeds, no seed_values, and no best-of-N selection.

```python
import json
champion_metric = our_metric   # the deterministic fitness from the eval score dict

# Read current champion version for If-Match
current_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
current_version = current_raw.get("version", 0)

champion_content = f"""---
metric_name: {metric_name}
metric_value: {champion_metric}
direction: {direction}
is_valid: {int(score.get('is_valid', 0))}
experiment_id: {exp_id}
agent: {AGENT_NAME}
timestamp: {datetime.now(timezone.utc).isoformat()}
---

# Champion: {exp_id}

## Experiment Description

{experiment_description}

## Result

- **Recorded metric:** {metric_name} = {champion_metric}
- **Delta from previous:** {delta:+.6f}

## Eval Score

```json
{json.dumps(score, indent=2, sort_keys=True)}
```

## Reproduction

1. Copy `{FOCUS_ROOT}/champion/algo.py`
2. scp it to the eval head and run from the sella checkout:
   `python eval_candidate.py --program <remote algo.py> --redis-host localhost --redis-port 6390`
3. Expected: {metric_name} = {champion_metric} (deterministic — exact match every run)

## Provenance

- Agent: {AGENT_NAME}
- Timestamp: {datetime.now(timezone.utc).isoformat()}
- Source: {FOCUS_ROOT}/champion/algo.py
"""

# PRE-PUT FITNESS GATE (REQUIRED — If-Match is NOT reliably enforced server-side, so a stale-If-Match
# PUT can silently OVERWRITE a better concurrent champion). Re-read champion.md fresh right before the
# PUT and ABORT if it already holds an equal-or-better fitness than ours — the post-PUT Step-7b1 exp_id
# guard cannot catch this (our own PUT would have just written our exp_id).
_pre_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
_pre = parse_frontmatter(_pre_raw); _pre_best = _pre.get("metric_value")
current_version = _pre_raw.get("version", current_version)  # freshest version for If-Match
# minimize task: promote ONLY if strictly lower than the current champion.
if _pre_best is not None and float(champion_metric) >= float(_pre_best):
    # An equal/better champion landed during our eval -> do NOT PUT (would clobber) and SKIP Step 7b1.
    # Re-queue our change as `{exp_id}_stack` to re-test on the new champion, then go to Step 7c.
    print(f"PRE-PUT ABORT: champion.md now {_pre.get('experiment_id')} ({_pre_best}) <= ours {champion_metric}; re-queue {exp_id}_stack, do NOT overwrite or copy algo.py.")
else:
    requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
        headers={**HEADERS, "If-Match": str(current_version)},
        json={"content": champion_content})
```

#### Step 7b1 — Propagate champion/algo.py — REQUIRED on KEEP

**Immediately after the champion.md PUT succeeds, you MUST copy your stamped candidate file
to `{FOCUS_ROOT}/champion/algo.py` and append a SOURCE line.** The local champion file
is read by every subsequent rotation's cpu-eval agent in Step 2 — leaving it stale corrupts
every downstream baseline. This step is the agent's responsibility, not the orchestrator's.

```python
import shutil
from datetime import datetime, timezone
from pathlib import Path

# RACE GUARD (REQUIRED, do this FIRST): copy algo.py ONLY if champion.md still records OUR exp_id.
# Step 5's current_best check does NOT catch a race where two agents both pass Step 5 on the OLD
# champion before either promotes: the loser's If-Match champion.md PUT 409s, but if it copies
# algo.py anyway it CLOBBERS the winner (champion.md=them, champion/algo.py=us).
_champ_now = parse_frontmatter(requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json())
if _champ_now.get("experiment_id") != exp_id:
    # LOST the race -> do NOT run the copy below and do NOT append SOURCE. Re-queue our validated
    # change as `{exp_id}_stack` to re-test on the NEW champion/algo.py, then go to Step 7c.
    # SKIP the rest of this code block.
    print(f"CHAMPION RACE LOST -> re-queue {exp_id}_stack (champion.md now {_champ_now.get('experiment_id')})")
# (Everything below runs ONLY if we WON the race -- champion.md records our exp_id.)
# Atomic write: temp-then-rename so concurrent KEEPs cannot half-overwrite.
src = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/algo_{exp_id}.py")
dst = Path(f"{FOCUS_ROOT}/champion/algo.py")
tmp = dst.with_suffix(".py.tmp")
shutil.copy(src, tmp)
tmp.replace(dst)  # atomic on POSIX

# Append provenance to champion/SOURCE (one line per promotion).
src_log = Path(f"{FOCUS_ROOT}/champion/SOURCE")
ts = datetime.now(timezone.utc).isoformat()
with src_log.open("a") as f:
    f.write(f"{exp_id} {our_metric:.6f} {AGENT_NAME} {ts}\n")
```

**Race-safety:** if multiple cpu-eval agents land KEEPs in the same rotation, the champion.md PUT is
*supposed* to serialize them via If-Match — but If-Match is NOT reliably enforced server-side, so the
real protection is the **PRE-PUT FITNESS GATE** (Step 7b: re-read champion.md, abort if an equal/better
one already landed) plus the **RACE GUARD** at the top of Step 7b1 (copy algo.py only if champion.md
records THIS exp_id). A loser re-queues its change as `{exp_id}_stack`. The winner's `tmp.replace(dst)` is atomic.

**Why this exists:** the champion file is the baseline every subsequent experiment's
diff is applied against. A several-KEEP-deep stale champion file means agents who don't know
to read the latest stamped candidate in the winning workspace will silently regress
the codebase. This step replaces the prior "orchestrator promotes" model that was
unreliable in practice.

#### Step 7c — Write result_latest.json (agent-local sentinel)

`result_latest.json` is your post-eval state record — it lets HEARTBEAT Part 0
resume an unposted result on the next session and is read by analysts who want to
know your last outcome. Champion propagation already happened in Step 7b1; this file
is purely a sentinel.

```python
import json
from pathlib import Path

# Merge with any Step 4 in-flight sentinel to preserve stdout_path/launched_at.
rl = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / "result_latest.json"
prior = json.loads(rl.read_text()) if rl.exists() else {}
rep = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

rl.write_text(json.dumps({**prior,
    "score": score, "fitness": our_metric, "is_valid": int(score.get("is_valid", 0)),
    "direction": direction,
    "exp_id": exp_id, "agent": AGENT_NAME,
    "algo_path": str(rep / f"algo_{exp_id}.py"),
    "timestamp": datetime.now(timezone.utc).isoformat(),
    # Resume fields — HEARTBEAT Part 0 Check C reads these. REQUIRED.
    "status": "complete", "posted_to_workshop": False, "result_post_id": None,
    "item": prior.get("item") or item,
    "queue_claimed": prior.get("queue_claimed", True),
    "description": description,
}, indent=2, default=str))
```

**If DISCARD:** write the result to `dead_ends.md` in your team workspace so analysts and other
cpu-eval agents skip this mechanism family. Use If-Match to avoid clobbering concurrent writes.

```python
if outcome == "DISCARD":
    de_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                          headers=HEADERS).json()
    de_content = de_raw.get("content", "# Dead Ends\n\n")
    de_version = de_raw.get("version", 0)

    # Structured entry — REQUIRED. Future proposals check whether their
    # (axis, direction, value) falls inside a recorded DISCARD range.
    # Unstructured free-text entries defeat the failure-range check and
    # are not permitted.
    axis = item.get("axis") or "UNKNOWN"
    direction = item.get("direction") or "UNKNOWN"
    value = item.get("value")
    fam = "_".join(exp_id.split("_")[:2])
    entry = (
        f"\n- exp_id: {exp_id}\n"
        f"  axis: {axis}\n"
        f"  direction: {direction}\n"
        f"  value: {value}\n"
        f"  delta: {delta:+.6f}\n"
        f"  family: {fam}\n"
        f"  date: {datetime.now(timezone.utc).date()}\n"
        f"  reason: {experiment_description[:160].replace(chr(10), ' ')}\n"
    )

    r = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                     headers={**HEADERS, "If-Match": str(de_version)},
                     json={"content": de_content + entry})
    if r.status_code == 409:
        print("dead_ends.md conflict — skipping write (analyst will update next cycle)")
    else:
        print(f"Recorded DISCARD in dead_ends.md (HTTP {r.status_code})")
```

### Step 8 — Post Result to Workshop (MANDATORY)

**This step is required for EVERY experiment, KEEP or DISCARD.** A result file in the workspace is not enough — the workshop post is what notifies analysts and other teams. Skipping this step makes the experiment invisible to the rest of the system.

Post as a NEW workshop post (not a comment on the kickoff thread):

```python
r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[RESULT] {item['id']}: {metric_name}={our_metric} ({outcome})",
    "content": f"## Experiment\n{description}\n\n## Result\n{metric_name}: {our_metric}\nDelta: {delta}\nOutcome: {outcome}\nRace condition: {race_condition}\n\n## Team\n{MY_TEAM}",
    "notify_agents": team_members,
    "tags": [f"team:{MY_TEAM}", "type:result", f"outcome:{outcome}"]
})
result_post_id = r.json().get("id") if r.ok else None
```

### Step 8b — Mark result as posted (REQUIRED — prevents duplicate [RESULT] next cycle)

```python
from datetime import datetime, timezone
rl_path = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / "result_latest.json"
rl = json.loads(rl_path.read_text())
rl.update({"status": "posted", "posted_to_workshop": True,
           "result_post_id": result_post_id,
           "posted_at": datetime.now(timezone.utc).isoformat()})
rl_path.write_text(json.dumps(rl, indent=2, default=str))
```

### Step 9 — (removed) No Near-Miss Protocol

Eval is **deterministic** — there is no measurement noise and therefore no noise band to anchor a
"near-miss" against. Every result is exactly KEEP or DISCARD (or FAILED for an unapplied diff):

- A **valid** candidate with strictly-lower `fitness` than the champion (or the first valid candidate
  under the seeding rule) is a **KEEP**.
- Any other valid result, or any **invalid** result (`is_valid == 0` / `fitness == 1000.0`), is a
  **DISCARD** — recorded in `dead_ends.md` (Step 7c) so the mechanism family is not re-tried.

Do NOT post `[NEAR-MISS]` and do NOT consult any noise floor. A non-improving deterministic delta is
simply a DISCARD; the structured dead-end entry carries the signal analysts need.

### Step 10 — Run Second Experiment

Go back to Step 2 for a second experiment before finishing your session.

