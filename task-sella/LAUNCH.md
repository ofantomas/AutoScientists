# task-profile.md — molopt-relax-steps

This profile fills in the hooks from `runbook.md` for **open-ended optimization** of a compact
molecular-geometry optimizer (`task_type: optimization`) — the goal is to drive `fitness`
(`mean_rel_steps`, lower is better) down indefinitely, subject to a hard per-molecule energy
validity gate. There is no wall-clock deadline; the loop runs until user interrupt.

**Key shape:** CPU-only deterministic evaluation. Candidates (`algo.py`) are scored by a remote
Redis-backed distributed validation worker pool — bounded by the worker count, NOT by any GPU
device. Evaluation is deterministic (no seed noise), so there is no multi-seed / noise-floor
machinery anywhere in this profile.

---

## Hook: launch_command

```bash
cd THIS_DIR
python3 launch.py <run-name> --task task-sella
```

`launch.py` walks up from `--task` looking for the nearest `LAUNCH.md` and copies it into the run
directory as `task-profile.md`. For this task that resolves to `task-sella/LAUNCH.md` (this file).

The candidate file `algo.py` and the evaluator (`eval_candidate.py`, `validate.py`, `molecules/`,
`metrics.yaml`) live in the **sella checkout on the eval-head host** — there is no upstream clone
step. The cpu-eval agents `scp` each candidate `algo.py` to the eval head and run
`eval_candidate.py` there; they do not need a local training repo.

---

## Hook: bootstrap_extras

No extras. The base bootstrap (`FOCUS_ROOT`, `WS_ID`, `tokens`, `HEADERS`, `API`, `task_md`, `PREFIX`)
is sufficient.

---

## Hook: discussion_policy

**OPTIONAL on cold start.** Skip the standalone discussion phase if the user wants the fastest
possible first eval dispatch. Instead, run it *in parallel* with the first cpu-eval agent's
evaluation in Step 5.

If you do run it standalone, cap discussion at ONE round of posts (3–8 minutes total) before
proceeding to seeding. Do not let it grow to 9+ posts; that delays the first candidate evaluation.

### Cold-start fast path

**Goal: first candidate dispatched to the eval pool within ~5 minutes of orchestrator start.**

| Window | Activity |
|---|---|
| 0–3 min | Read TASK.md, form roster (3 teams of ~3 agents) |
| 3–5 min | Each team posts **1** seed proposal (not 3) — see `seeding_policy` |
| 5–7 min | **First cpu-eval agent dispatched** to the highest-priority seed proposal |
| 7–25 min | Parallel: evaluations continue; analysts post more proposals; discussion threads grow |
| 25–30 min | Harvest results, update champion |

Rules:
1. Skip extended discussion before any evaluation.
2. Seed-queue minimum, not maximum (one proposal per team).
3. Dispatch the first cpu-eval agent the moment the first queue.md has ≥1 pending experiment.
4. Eval concurrency is bounded by the remote distributed-validation worker pool, NOT by any device
   count. Multiple cpu-eval agents may dispatch concurrently; each candidate gets a unique remote
   path so they don't collide.
5. Do NOT block on perfect discussion before evaluation starts.

**`extra_discussion_instructions` (empty if discussion is skipped):** no additions beyond the base
prompt.

---

## Hook: seeding_policy

**Orchestrator-seeded.** After teams are formed, the orchestrator itself posts ONE `[PROPOSAL]` per
team and writes it into the team's `queue.md`. Dispatch the first cpu-eval agent as soon as the
first team's queue has ≥1 pending experiment — do not wait for all teams to be seeded.

```python
for team_name, team_info in teams.items():
    team_ws_id = team_info["workspace_id"]
    for exp in seed_experiments[team_name]:    # one entry per team on cold start
        requests.post(f"{API}/posts", headers=HEADERS, json={
            "workshop": WORKSHOP,
            "title":   f"[PROPOSAL] {exp['id']}: {exp['description']}",
            "content": f"## Mechanism\n{exp['rationale']}\n\n## Diff\n```python\n{exp['diff']}\n```\n\n## Team\n{team_name}",
            "notify_agents": team_info["members"],
            "tags": [f"team:{team_name}", "type:proposal"],
        })

    queue_content = f"""---
claims: {{}}
pending:
{chr(10).join(f'  - id: {e["id"]}' + chr(10) + f'    description: "{e["description"]}"' + chr(10) + f'    priority: high' for e in seed_experiments[team_name])}
---

# Experiment Queue
"""
    requests.put(f"{API}/workspaces/{team_ws_id}/files/queue.md",
                 headers=HEADERS, json={"content": queue_content})
```

Seed experiments propose **structural** changes to `algo.py` (step control / trust region, Hessian
init & update, line search / step acceptance, convergence criterion) — not fine retuning of a single
constant. Each `description` should name the mechanism being changed; the optimized metric is
`fitness` (`mean_rel_steps`), lower is better, subject to the per-molecule energy gate.

**`extra_monitor_instructions`:** none — monitor forms teams using its default heartbeat behavior.

---

## Hook: pre_cycle_check

No-op.

```python
def pre_cycle_check():
    return False
```

---

## Hook: analyst_prompt_extras

```python
analyst_prompt_extras = ""   # no additions beyond base
```

---

## Hook: gpu_dispatch

**CPU-eval dispatch. NO GPUs, NO CUDA, NO `nvidia-smi`.** Candidates are scored on a remote
Redis-backed distributed validation worker pool. Concurrency is bounded by the size of that worker
pool, NOT by any device — multiple cpu-eval agents may run concurrently. Each candidate `algo.py` is
`scp`'d to a **unique** remote path (`/tmp/cand_${AGENT}_${exp}.py`) so concurrent evals never
collide.

For each cpu-eval agent, launch in its own message:

```python
eval_agents = [f"{PREFIX}_gpu{i}" for i in range(1, 7)]

# Dispatch all 6 cpu-eval agents every cycle (the full roster).
#
# Cold start: as soon as teams form, immediately dispatch one cpu-eval agent in
# MODE=execute to run the shared baseline, before extended discussion; then
# dispatch the rest against the seeded queues.

for agent_name in eval_agents:
    Task(
        subagent_type="general-purpose",
        description=f"{agent_name} experiment",
        prompt=(
            f"You are {agent_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"When done: <promise>{agent_name} cycle complete</promise>"
        ),
    )
```

No CUDA device pinning, no `CUDA_VISIBLE_DEVICES`, no per-device serialization. HEARTBEAT.md tells
agents how to discover identity, team, workspace, and the eval protocol (scp candidate → ssh
`eval_candidate.py` → parse JSON).

---

## Hook: champion_promotion

**Champion = best `algo.py` for the optimized metric `fitness` (= `mean_rel_steps`, lower is
better).** The champion artifact is `algo.py` everywhere — there is no `train.py` in this task.

**KEEP / promotion rule (deterministic, strict, with a seeding exception).** Because the baseline
`algo.py` may itself be **invalid** under the energy gate, the first VALID candidate seeds the
champion regardless of its fitness; thereafter only strictly-better VALID candidates are promoted:

```python
import shutil

# `score` is the JSON dict from eval_candidate.py:
#   is_valid (0/1), fitness (float; 1000.0 == invalid sentinel), mean_rel_steps, ...
# `champion_fitness` is the incumbent champion's fitness (None if no VALID champion yet).

is_valid       = int(score.get("is_valid", 0)) == 1
cand_fitness   = float(score["fitness"])
have_champion  = champion_fitness is not None

if is_valid and (not have_champion or cand_fitness < champion_fitness):
    # SEEDING: first valid candidate promoted regardless of fitness.
    # THEREAFTER: strict improvement (cand_fitness < champion_fitness) among valid candidates only.
    outcome = "KEEP"
else:
    outcome = "DISCARD"   # invalid candidates and non-improving valid candidates are discarded

if outcome == "KEEP":
    # NOTE: ROLE-GPU Step 7b1 is the authoritative agent-side propagation path;
    # this orchestrator snippet is illustrative/backup. Copy the stamped candidate
    # (algo_{exp_id}.py) to match Step 7b1's source filename exactly.
    agent_algo = FOCUS_ROOT / "agents" / agent_name / "workspace" / "repo" / f"algo_{exp_id}.py"
    shutil.copy(agent_algo, FOCUS_ROOT / "champion" / "algo.py")
    (FOCUS_ROOT / "champion" / "SOURCE").write_text(
        f"{agent_name} {exp_id} fitness={cand_fitness} {datetime.now(timezone.utc).isoformat()}\n"
    )
    champion_fitness = cand_fitness
    print(f"Champion propagated: {agent_name} → champion/algo.py (fitness={cand_fitness})")
```

This is the SINGLE SOURCE OF TRUTH for champion code. All cpu-eval agents read from
`{FOCUS_ROOT}/champion/algo.py`.

> Eval is deterministic — there is NO multi-seed gate and NO noise-floor band. A strictly-lower
> `fitness` among valid candidates is a real, reproducible improvement; promote it unconditionally.

### Auto-bracket big wins

After a KEEP, the orchestrator may post bracketing `[PROPOSAL]`s to find the true optimum of a tuned
parameter. With deterministic eval there is no seed-noise justification for special-casing, but
bracketing remains a legitimate search move. Trigger on a meaningful relative improvement in
`mean_rel_steps` (e.g. `champion_fitness - cand_fitness > 0.01`, i.e. ≥1% fewer relative steps),
not on a `val_bpb`-scaled absolute threshold:

```python
if outcome == "KEEP" and (prev_champion_fitness is not None) and \
   (prev_champion_fitness - cand_fitness) > 0.01:
    # Identify the changed parameter, generate 2 bracketing experiments
    # (midpoint and overshoot), post them as [PROPOSAL]s, add to the team queue at high priority.
    for bracket_exp in generate_brackets(old_value, new_value):
        requests.post(f"{API}/posts", headers=HEADERS, json={
            "workshop": WORKSHOP,
            "title":   f"[PROPOSAL] {bracket_exp['id']}: {bracket_exp['description']}",
            "content": f"Auto-bracketing from win {exp_id} (Δfitness={prev_champion_fitness - cand_fitness:.4f}).",
            "tags":    ["type:proposal", "auto:bracket", f"team:{team_name}"],
        })
```

The orchestrator generates these — no analyst action needed.

---

## Hook: stagnation_response

**OPEN-ENDED — do NOT stop the loop.** When 0 KEEPs occur in the last 10 experiments, the search has
plateaued, but this is open-ended optimization with no deadline: the correct response is to trigger a
**discussion / regroup** round (re-form teams, mine new structural axes, post fresh proposals), NOT
to exit. Never `raise SystemExit`; never halt.

```python
def stagnation_response(cycle_count):
    print(f"STAGNATION: 0 KEEPs in last 10 experiments (cycle {cycle_count})")
    print("Open-ended task — triggering a discussion/regroup round, NOT stopping.")
    # Trigger a regroup: post a [DISCUSSION] inviting analysts to mine new structural axes
    # (step control, curvature model, line search, convergence criterion), re-form teams,
    # and seed fresh proposals. Then continue the loop normally.
    requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP,
        "title": f"[DISCUSSION] Stagnation regroup (cycle {cycle_count})",
        "content": ("0 KEEPs in the last 10 experiments. Re-form teams and propose NEW structural "
                    "axes for algo.py (step control / trust region, Hessian init & update, "
                    "line search / step acceptance, convergence criterion). Avoid retuning a single "
                    "constant. Seed fresh [PROPOSAL]s into the team queues."),
        "tags": ["type:discussion", "regroup"],
    })
    # Do NOT raise / exit — the loop continues.
```

Eval is deterministic, so this stagnation signal is noise-free: 0 KEEPs in the last 10 genuinely
means no real improvement was found, not an unlucky run of seeds.

---

## Hook: periodic_hooks

**Meta-improvement: ENABLED.** Every 3 cycles, follow `system/reference/META-IMPROVEMENT.md`: read
the evidence, make ONE targeted file edit, append a line to `logs/meta_results.tsv`. Do NOT
`import meta_diagnostics` (not shipped). Optionally run `python3 task/meta_diagnostics.py` for hard
signals (crash-safe; the pass works without it).

```python
def periodic_hooks(cycle_count):
    if cycle_count % 3 != 0:
        return None
    # Judgment-based meta-improvement — see system/reference/META-IMPROVEMENT.md.
    # Read evidence -> diagnose the single biggest issue -> edit exactly ONE file
    # -> append to logs/meta_results.tsv. NEVER import meta_diagnostics.
    return None
```

---

## Hook: exit_condition

```python
def exit_condition():
    return False   # never exit voluntarily; this is an open-ended run (only user interrupt stops it)
```

---

## Hook: final_report

```python
def final_report():
    print()
    print("=" * 60)
    print("  OPTIMIZATION RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:        {task_name}")
    print(f"  Cycles:      {cycle_count}")
    champ_path = FOCUS_ROOT / "champion" / "algo.py"
    src_path   = FOCUS_ROOT / "champion" / "SOURCE"
    if src_path.exists():
        print(f"  Champion:    {src_path.read_text().strip()}")
    print(f"  Code:        {champ_path}")
    print("=" * 60)
```

---

## Hook: never_do_extras

(No additions beyond the universal list.)
