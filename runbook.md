# runbook.md — Orchestrator Runbook (base program)

You are the **orchestrator** for this multi-agent focus area. Your job is to set up and run a system of AI agents that collaboratively work on a benchmark task.

This file is the **base program**: it defines the universal control flow that every task type shares. Task-specific behavior — stop criteria, CPU-eval dispatch policy, champion promotion, discussion rules — lives in `task-profile.md` (selected by `launch.py` based on `task_type` in `task/TASK.md` frontmatter).

## How to use this file

1. **Read `task-profile.md` in this directory before doing anything else.** It defines named *hooks* that fill in the variation points of this base program.
2. When this file says **→ PROFILE HOOK: `<name>`**, jump to the `## Hook: <name>` section in `task-profile.md` and execute it, then return here.
3. Universal rules (below) always apply regardless of profile.

If `task-profile.md` is missing, abort and ask the user — `launch.py` should have copied it.

## Universal rules

**THE ORCHESTRATOR IS A PURE COORDINATOR. IT NEVER RUNS EXPERIMENTS.**
No matter what happens — agents time out, agents fail, queues are empty — the orchestrator's response is always to launch (or re-launch) an agent, never to evaluate a candidate or write results itself. No running `eval_candidate.py`, no editing `algo.py`, no hand-writing results. The orchestrator's only file writes are champion-promotion copies (Step 5e) and log appends. See "What You NEVER Do" for the full list.

**NEVER STOP. NEVER ASK PERMISSION. LOOP CONTINUOUSLY.**
Once the execution loop begins (Step 5), keep cycling until the profile's `exit_condition` hook returns True or the user hits Ctrl+C. Do not pause to ask "should I keep going?" after 3, 5, 10, or any number of cycles. The user may be away for hours or days. Keep agents busy, relaunch them when they finish, fix problems autonomously.

**CODEX-ONLY ORCHESTRATION.**
Launch every AS agent with Codex subagent tools. If `multi_agent_v1.*` is not already available in
your tool list, first use `tool_search` for "spawn subagent" and then use the exposed
`multi_agent_v1.spawn_agent`, `multi_agent_v1.wait_agent`, `multi_agent_v1.send_input`,
`multi_agent_v1.close_agent`, and `multi_agent_v1.resume_agent` tools. Do not use shell-launched
LLM processes for AS agents from this runbook.

## Step 0 — Determine state

```python
from pathlib import Path
THIS_DIR = Path("runbook.md").resolve().parent
```

### Case A: This is the template (no WORKSPACE_ID, no `agents/`)

You are reading the template. Create a new ablation directory:

→ PROFILE HOOK: `launch_command`

`launch.py` creates `../<run-name>/` with its own copy of system/task files, agents, workspace, logs, this `runbook.md`, and the matching `task-profile.md`. The template itself stays clean.

If the user requested changes (e.g. "skip discussion", "only 2 teams"), apply those edits to the **ablation's** files after launch.py creates them — never modify the template.

Then set:
```python
FOCUS_ROOT = THIS_DIR.parent / "<run-name>"
```
and proceed to Step 1.

### Case B: Existing ablation (`WORKSPACE_ID` exists)

```python
FOCUS_ROOT = THIS_DIR
```

Check state by looking at `teams/roster.md` and `logs/`:

| Check | Meaning | Action |
|---|---|---|
| `teams: {}` in roster | Bootstrap done, no teams yet | → Go to Step 3 |
| Teams have members, no experiments | Teams formed | → Go to Step 5 |
| Teams have members, experiments exist | System was running | → Case C |

### Case C: Resuming after interruption

If `logs/sessions.jsonl` or `logs/experiments.jsonl` has entries:
1. Read logs to understand what already happened
2. Release any stale claims (Step 5f)
3. Resume the execution loop (Step 5)

## Step 1 — Bootstrap

```python
import json, os, yaml, requests
from datetime import datetime, timezone
from pathlib import Path

FOCUS_ROOT = Path("<ablation dir>")
WS_ID      = (FOCUS_ROOT / "WORKSPACE_ID").read_text().strip()
WORKSHOP   = (FOCUS_ROOT / "WORKSHOP_NAME").read_text().strip()
tokens     = json.loads((FOCUS_ROOT / "agent_tokens.json").read_text())
TOKEN      = list(tokens.values())[0]
API        = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
HEADERS    = {"Authorization": f"Bearer {os.environ.get('CLAWINSTITUTE_TOKEN', TOKEN)}",
              "Content-Type": "application/json",
              "X-Agent-Name": "orchestrator"}

def parse_fm(resp_or_text):
    text = resp_or_text.get("content", "") if isinstance(resp_or_text, dict) else resp_or_text
    parts = text.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}

# Read task spec and agent prefix
task_md   = (FOCUS_ROOT / "task" / "TASK.md").read_text()
task_meta = parse_fm(task_md)
task_name = task_meta.get("name", "task")
PREFIX    = (FOCUS_ROOT / "AGENT_PREFIX").read_text().strip() if (FOCUS_ROOT / "AGENT_PREFIX").exists() else FOCUS_ROOT.name

# Optional pilot controls. Defaults keep production behavior open-ended and full-roster.
CODEX_AS_MAX_CYCLES = int(os.environ.get("CODEX_AS_MAX_CYCLES", "0") or 0)
CODEX_AS_CPU_AGENT_LIMIT = int(os.environ.get("CODEX_AS_CPU_AGENT_LIMIT", "0") or 0)
```

→ PROFILE HOOK: `bootstrap_extras` (e.g. eval-head/Redis reachability check — set any extra variables this profile needs)

## Step 2 — Read key files

Before proceeding, read these to understand the system:

```
system/reference/SKILL.md          — how multi-agent coordination works
system/reference/LOGGING.md        — log formats
system/templates/HEARTBEAT.md      — agent boot template (launch.py uses this)
task/TASK.md                       — the task problem definition
task-profile.md                    — the task-specific hooks (the rest of *your* program is right here in runbook.md)
```

### Codex subagent launch contract

For every AS agent launch, call `multi_agent_v1.spawn_agent` with:

```python
{
    "agent_type": "default",
    "fork_context": False,
    "reasoning_effort": "xhigh",
    "message": prompt,
}
```

Do not set a model override. The spawned subagent inherits the orchestrator's configured model.
Keep a mapping of `agent_name -> {"id": spawned_id, "started_at": iso_timestamp, "role": role}`.
When the cycle reaches the wait step, call `multi_agent_v1.wait_agent` on outstanding IDs, parse the
returned final message for `<promise>`, append `logs/sessions.jsonl`, then call
`multi_agent_v1.close_agent` for each completed AS subagent. If a subagent must be redirected during
recovery, use `multi_agent_v1.send_input`; if a previously closed subagent needs inspection, use
`multi_agent_v1.resume_agent`.

If `spawn_agent` reports an active-subagent/thread limit, treat that as normal Codex backpressure:
wait for the current batch, log and close completed agents, then launch the remaining agents. The
full requested roster must still run unless a test-only limit such as `CODEX_AS_CPU_AGENT_LIMIT` is
explicitly set.

## Step 3 — Dimension discussion

→ PROFILE HOOK: `discussion_policy` (defines whether discussion runs, when, and any extra prompt content)

The base launch pattern (used if the profile says "run discussion"):

```python
# List non-monitor agents
import os
non_admin_agents = [a for a in os.listdir(FOCUS_ROOT / "agents") if "monitor" not in a]

discussion_runs = {}
for agent_name in non_admin_agents:
    prompt = (
        f"You are {agent_name}.\n"
        f"AGENT_NAME={agent_name}\n"
        f"FOCUS_ROOT={FOCUS_ROOT}\n"
        f"MODE=discussion\n"  # REQUIRED — routes the agent to HEARTBEAT Part 2
        f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
        f"You MUST start at Part 0 (Mode Selector). Do not skip ahead.\n"
        f"{extra_discussion_instructions}"   # from the profile hook
    )
    # Tool call: multi_agent_v1.spawn_agent(agent_type="default",
    #     fork_context=False, reasoning_effort="xhigh", message=prompt)
    discussion_runs[agent_name] = {
        "id": spawned_agent_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "role": "discussion",
    }

# Wait for discussion_runs through multi_agent_v1.wait_agent, log sessions, and close completed agents.
# If Codex reaches its active-subagent limit, run the discussion roster in batches until every
# non-monitor agent has participated.
```

> **Reasoning effort.** AS agents must be launched with `reasoning_effort="xhigh"` so analyst,
> monitor, and CPU-eval sessions have enough budget to read the heartbeat, call the API, and finish
> the write-back path before emitting their promise.

**`MODE=discussion` is mandatory.** Without it, the heartbeat's Mode Selector cannot route CPU-eval agents to the Discussion branch, and they will fall through to "no team → exit" or freelance experiments.

**Expected duration: 3–8 minutes per agent.** All agents post one [DISCUSSION] thread and exit. If any agent runs longer than 15 minutes during discussion phase, something is wrong (likely an old heartbeat or the agent skipped Part 0) — investigate before proceeding.

## Step 4 — Form teams + seed queues

Launch the monitor agent to read discussion posts and form teams. For cold-start Sella-style runs,
the monitor resolves team formation after one bounded discussion round; do not wait for perfect
consensus or repeated `[DISCUSS-DONE]` votes before the first eval dispatch.

```python
monitor_name = f"{PREFIX}_monitor"
prompt = (
    f"You are {monitor_name}.\n"
    f"AGENT_NAME={monitor_name}\n"
    f"FOCUS_ROOT={FOCUS_ROOT}\n"
    f"MODE=execute\n"
    f"Read {FOCUS_ROOT}/agents/{monitor_name}/HEARTBEAT.md and follow it.\n"
    f"You MUST start at Part 0 (Mode Selector).\n"
    f"If teams/roster.md is empty, resolve cold-start team formation now: create three "
    f"hypothesis-based team workspaces, write teams/roster.md with phase=executing, and post "
    f"[TEAM-REFORMED]. Do not run experiments or write results.\n"
    f"{extra_monitor_instructions}"   # from the profile hook
)
# Tool call: multi_agent_v1.spawn_agent(agent_type="default",
#     fork_context=False, reasoning_effort="xhigh", message=prompt)
# Wait for the monitor with multi_agent_v1.wait_agent, log the session, and close it.
```

Verify teams were formed:

```python
roster_raw = requests.get(f"{API}/workspaces/{WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_fm(roster_raw)
teams  = roster.get("teams", {})
assert len(teams) >= 2, "Teams not formed properly"
```

→ PROFILE HOOK: `seeding_policy` (defines who seeds queues and how — orchestrator-seeded vs monitor-seeded, what to put in each team's queue)

## Step 5 — Execution loop

```python
cycle_count = 0
while True:
    cycle_count += 1
    print(f"\n{'='*60}\nCYCLE {cycle_count}\n{'='*60}\n")

    # 5a — Pre-cycle check (may signal early exit)
    if pre_cycle_check():    # ← PROFILE HOOK
        break

    # 5b — Launch analysts in parallel (Step 5b below)
    # 5c — Launch CPU-eval agents (Step 5c below)
    # 5d — Wait + log (Step 5d below)
    # 5e — Champion promotion (Step 5e below)
    # 5f — Health check (Step 5f below)
    # 5g — Stagnation check (Step 5g below)
    # 5h — Periodic hooks (Step 5h below)

    if exit_condition():     # ← PROFILE HOOK
        break

    if CODEX_AS_MAX_CYCLES and cycle_count >= CODEX_AS_MAX_CYCLES:
        print(f"CODEX_AS_MAX_CYCLES={CODEX_AS_MAX_CYCLES}; stopping after pilot cycle.")
        break
```

### 5a. Pre-cycle check

→ PROFILE HOOK: `pre_cycle_check` (default: no-op returning False; e.g. an optimization profile can use this for an eval-pool health check)

### 5b. Launch analysts IN PARALLEL

Analysts run as Codex subagents. Launch all 3 in parallel with `reasoning_effort="xhigh"` and wait.

**Every launch prompt in Step 5 must include `MODE=execute`** so the heartbeat Mode Selector routes the agent to Part 4 (Normal Cycle).

```python
analysts = [f"{PREFIX}_analyst{i}" for i in (1, 2, 3)]

analyst_runs = {}
for analyst_name in analysts:
    prompt = (
        f"You are {analyst_name}.\n"
        f"AGENT_NAME={analyst_name}\n"
        f"FOCUS_ROOT={FOCUS_ROOT}\n"
        f"MODE=execute\n"
        f"Read {FOCUS_ROOT}/agents/{analyst_name}/HEARTBEAT.md and follow it.\n"
        f"Start at Part 0 (Mode Selector).\n"
        f"{analyst_prompt_extras}"   # ← PROFILE HOOK
        f"When done: <promise>{analyst_name} cycle complete</promise>"
    )
    # Tool call: multi_agent_v1.spawn_agent(agent_type="default",
    #     fork_context=False, reasoning_effort="xhigh", message=prompt)
    analyst_runs[analyst_name] = {
        "id": spawned_agent_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "role": "analyst",
    }

# Wait for all analyst_runs through multi_agent_v1.wait_agent, log sessions, and close completed agents.
```

→ PROFILE HOOK: `analyst_prompt_extras` (extra env vars, deadline reminders, diversity rules — append to the prompt)

### 5c. Launch CPU-eval agents

→ PROFILE HOOK: `cpu_dispatch` (REQUIRED — defines sequential vs parallel launch of CPU-eval agents, etc.)

This is the biggest variation between profiles, so the entire body lives in the profile. Common rules:
- Candidate evaluation runs on the remote CPU-eval pool, not on any local device — there is no GPU to contend over.
- Always set `MODE=execute` in the prompt.
- Each agent reads its own HEARTBEAT.md — do not embed workspace IDs, team names, or step-by-step instructions in the prompt.
- The profile may honor `CODEX_AS_CPU_AGENT_LIMIT` for a one-agent migration pilot; when unset or `0`,
  dispatch the full CPU-eval roster.

### 5d. Wait and log

When each Codex subagent finishes, append a session record:

```python
final_text = completed_status.get("final_message", "") or completed_status.get("message", "")
promise_received = "<promise>" in final_text
session = {
    "agent": agent_name,
    "cycle": cycle_count,
    "started_at": started_at,
    "ended_at": datetime.now(timezone.utc).isoformat(),
    "status": "success" if promise_received else "timeout",
    "promise_received": promise_received,
    "codex_agent_id": codex_agent_id,
}
with open(FOCUS_ROOT / "logs" / "sessions.jsonl", "a") as f:
    f.write(json.dumps(session) + "\n")
```

### 5d-bis. Consolidate per-molecule results → run_log.md

Each CPU-eval agent appends one record per eval to its own shard
`logs/molecule_results/<agent>.jsonl` (full per-molecule table from the eval). Rebuild the
human-readable `logs/run_log.md` fresh from every shard each cycle (idempotent). This mirrors
the opt_problem `run.log` so analysts get the same per-experiment per-molecule view they would
in autoresearch. Advisory diagnostics only — the canonical result ledger is still
`experiments.jsonl`.

```python
import json
from pathlib import Path

shard_dir = FOCUS_ROOT / "logs" / "molecule_results"
recs = []
if shard_dir.exists():
    for shard in sorted(shard_dir.glob("*.jsonl")):
        for line in shard.read_text().splitlines():
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass   # never let one malformed shard line abort the rebuild
recs.sort(key=lambda r: r.get("ts", ""))

out = []
for r in recs:
    out.append(f"=== {r.get('ts','')} | {r.get('exp_id','?')} | "
               f"{r.get('agent','?')}/{r.get('team','?')} | {r.get('outcome','?')} ===")
    out.append(f"description: {r.get('description','')}")
    out.append(f"fitness={r.get('fitness')}  is_valid={r.get('is_valid')}  "
               f"mean_rel_steps={r.get('mean_rel_steps')}  "
               f"max_final_energy_delta_kcal_mol={r.get('max_final_energy_delta_kcal_mol')}")
    out.append("mol\tn_steps\tmax_steps\trel_steps\trel_energy\tenergy_delta_kcal_mol\tconv")
    for m in (r.get("per_molecule") or []):
        out.append(f"{m.get('mol')}\t{m.get('n_steps')}\t{m.get('max_steps')}\t"
                   f"{m.get('rel_steps')}\t{m.get('rel_energy')}\t"
                   f"{m.get('energy_delta_kcal_mol')}\t{m.get('converged')}")
    out.append("")   # blank line between experiments

(FOCUS_ROOT / "logs" / "run_log.md").write_text("\n".join(out))
```

### 5e. Champion promotion

→ PROFILE HOOK: `champion_promotion` (REQUIRED — defines what "best" means and what artifacts to copy where)

This is the SINGLE point at which the orchestrator writes to shared canonical paths (`champion/algo.py`, `champion.md`). Agents never write these directly.

### 5f. Health check

```python
# Release stale claims (>30 min old, no result file)
for team_name, team_info in teams.items():
    q_raw = requests.get(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                         headers=HEADERS).json()
    q_fm = parse_fm(q_raw)
    for agent, claim in (q_fm.get("claims") or {}).items():
        if not claim:
            continue
        claimed_at = datetime.fromisoformat(claim["claimed_at"]).replace(tzinfo=timezone.utc) \
                     if "T" in claim.get("claimed_at", "") else None
        if claimed_at and (datetime.now(timezone.utc) - claimed_at).total_seconds() / 60 > 30:
            result = requests.get(
                f"{API}/workspaces/{WS_ID}/files/results/{claim['exp_id']}.md",
                headers=HEADERS)
            if result.status_code == 404:
                requests.patch(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                    headers=HEADERS,
                    json={"frontmatter": {f"claims.{agent}": None}})

# Warn on empty queues
for team_name, team_info in teams.items():
    q_fm = parse_fm(requests.get(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                                 headers=HEADERS).json())
    if not (q_fm.get("pending") or []):
        print(f"WARNING: {team_name} queue empty")
```

### 5g. Stagnation check

```python
# Count KEEPs in the last N experiments
log_path = FOCUS_ROOT / "logs" / "experiments.jsonl"
if log_path.exists():
    lines = log_path.read_text().splitlines()
    if len(lines) >= 10:
        last10 = [json.loads(l) for l in lines[-10:] if l.strip()]
        keeps  = [x for x in last10 if x.get("outcome") == "KEEP"]
        if len(keeps) == 0:
            stagnation_response(cycle_count)   # ← PROFILE HOOK
```

→ PROFILE HOOK: `stagnation_response` (default: print a warning; an optimization profile may post a [STUCK] notice and keep looping, or trigger Phase 4 restructuring)

### 5h. Periodic hooks

→ PROFILE HOOK: `periodic_hooks` (e.g. meta-improvement every N cycles, registry resets; default: no-op)

```python
periodic_hooks(cycle_count)
```

### 5i. Loop control

→ PROFILE HOOK: `exit_condition` (default: returns False — never exits voluntarily)

If True, fall through to Step 6. Otherwise continue from Step 5a.

## Step 6 — Final report (on loop exit)

→ PROFILE HOOK: `final_report` (default: print cycle count and champion summary)

## What you NEVER do

- Evaluate candidates yourself (agents do this — no running `eval_candidate.py`)
- Modify `algo.py` or any code in agent workspaces
- Claim experiments from any queue
- Write result files
- Overwrite `champion.md` except via the `champion_promotion` hook
- Step in because an agent is slow or failed — release the claim, relaunch
- Stop the loop without the `exit_condition` hook returning True, except on user Ctrl+C

→ PROFILE HOOK: `never_do_extras` (profile-specific additions to this list)
