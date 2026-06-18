---
name: multi-agent-focus-heartbeat
description: Template for per-agent HEARTBEAT.md. launch.py injects role + team sections to produce a complete self-contained file per agent.
---

# Agent Heartbeat

**This file is YOUR complete guide. Read it top to bottom on every invocation.**

The heartbeat has 5 parts. Part 0 (Mode Selector) is mandatory and routes you to the correct branch. **Do NOT skip Part 0. Do NOT execute Parts 1–5 until Part 0 has explicitly told you which branch to follow.**

```
Part 0  Mode Selector ......... pick your branch (5 min, mandatory)
Part 1  Boot ................... credentials, paths, identity
Part 2  Branch — Discussion .... CPU-only thinking, post [DISCUSSION], exit
Part 3  Branch — No-Team ....... exit cleanly, no work
Part 4  Branch — Normal Cycle .. orient, role-specific work, record, post
Part 5  Branch — Resume & Post . finish an unposted result from a prior session
Part 6  Always-Last ............ update AGENT.md, mirror to API, exit with promise
```

---

## Part 0: Mode Selector — DO THIS FIRST

Before ANY other work, you must determine which branch to execute. Follow these three checks in order. Stop at the first branch that matches.

The launch prompt provides `AGENT_NAME` and `FOCUS_ROOT`. Bind both values before running any code
below. `AGENT_NAME` is the directory name under `{FOCUS_ROOT}/agents/`; your local identity file is
`{FOCUS_ROOT}/agents/{AGENT_NAME}/AGENT.md`.

### Check A: Did the launch prompt set MODE?

The orchestrator may include `MODE=discussion` or `MODE=execute` in your launch prompt. Read your launch prompt carefully now.

- **`MODE=discussion`** → go to **Part 2 (Discussion Branch)**. CPU-only. No experiments. Even if you are a CPU-eval agent, you do thinking work this cycle.
- **`MODE=execute`** (or no MODE set) → continue to Check A2.

### Check A2: Workshop-triggered discussion — agents self-regroup

Agents can trigger a system-wide discussion round without orchestrator
intervention. Before executing a normal cycle, search the workshop for
an unresolved `[DISCUSSION-TRIGGER]` post:

```python
import json, os, re, requests, yaml
from pathlib import Path

AGENT_DIR = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}")
creds = json.load(open(AGENT_DIR / "credentials.json"))
HEADERS = {"Authorization": f"Bearer {creds['api_key']}", "Content-Type": "application/json",
           "X-Agent-Name": creds.get("agent_name", AGENT_NAME)}
API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()

_agent_md = (AGENT_DIR / "AGENT.md").read_text() if (AGENT_DIR / "AGENT.md").exists() else ""
_m = re.search(r"^role:\s*(\S+)", _agent_md, re.MULTILINE)
MY_ROLE = _m.group(1).strip() if _m else "unknown"

def parse_frontmatter(resp):
    content = resp.get("content", "")
    parts = content.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}

recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=30",
                      headers=HEADERS).json().get("data", [])
trigger_posts = [p for p in recent if "[DISCUSSION-TRIGGER]" in p.get("title", "")]

# A trigger routes an agent to discussion if:
#   - no roster exists yet and fewer than 5 [DISCUSS-DONE] posts exist, OR
#   - a [TEAM-REFORMED] post already exists for the trigger but the roster
#     closure marker is missing, and this is not a CPU-eval agent.
#
# Once the alphabetically-last analyst writes teams/roster.md with
# discussion_closed: true and source_trigger: <trigger_id>, that roster is the
# authoritative closure signal. Do not send execute-mode agents back into
# discussion just because raw comments are missing or slow to index.
if trigger_posts:
    active_trigger = trigger_posts[0]  # most recent
    done_count = count_comments_matching(active_trigger["id"], "[DISCUSS-DONE]")
    roster_raw_for_trigger = requests.get(
        f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
        headers=HEADERS,
    ).json()
    roster_fm_for_trigger = parse_frontmatter(roster_raw_for_trigger)
    trigger_roster_teams = roster_fm_for_trigger.get("teams", {}) or {}
    trigger_closed_by_roster = (
        bool(trigger_roster_teams)
        and roster_fm_for_trigger.get("phase") == "executing"
        and roster_fm_for_trigger.get("source_trigger") == active_trigger["id"]
        and roster_fm_for_trigger.get("discussion_closed") is True
    )
    trigger_reformed_posted = any(
        "[TEAM-REFORMED]" in p.get("title", "")
        and active_trigger["id"] in (p.get("title", "") + "\n" + p.get("content", ""))
        for p in recent
    )

    if trigger_closed_by_roster:
        print(f"[DISCUSSION-TRIGGER closed] roster closes {active_trigger['id']}; continuing")
    elif not trigger_roster_teams:
        # Switch THIS agent into discussion mode
        print(f"[DISCUSSION-TRIGGER active] switching to Part 2")
        MODE = "discussion"
        # fall through to Part 2
    elif trigger_reformed_posted and MY_ROLE != "cpu":
        # Team reform already happened but roster lacks a closure marker.
        # Analysts/monitor may repair the marker; CPU-eval agents should not
        # spend eval slots on bookkeeping closure once an executable roster exists.
        print(f"[DISCUSSION-TRIGGER needs closure marker] switching non-CPU agent to Part 2")
        MODE = "discussion"
    elif trigger_reformed_posted:
        print(f"[DISCUSSION-TRIGGER marker missing] CPU agent continues to execute")
    elif done_count < 5:
        # Fresh regroup trigger: all agents contribute to discussion until quorum.
        print(f"[DISCUSSION-TRIGGER active] switching to Part 2")
        MODE = "discussion"
    elif MY_ROLE != "cpu":
        # Quorum reached but no reform has been posted. Let analysts/monitor
        # close/reform; avoid using CPU-eval agents for discussion bookkeeping.
        print(f"[DISCUSSION-TRIGGER quorum reached] switching non-CPU agent to Part 2")
        MODE = "discussion"
    else:
        print(f"[DISCUSSION-TRIGGER quorum reached] CPU agent continues to execute")
```

If Check A2 set `MODE = "discussion"` → go to **Part 2 (Discussion Branch)**.
If the latest trigger is already closed by `teams/roster.md`, or this CPU-eval agent is allowed to
continue because only the closure marker is missing, continue to Check B.

### Check B: Do teams exist in the roster?

```python
roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_frontmatter(roster_raw).get("teams", {}) or {}

MY_TEAM = TEAM_WS_ID = None
ALL_TEAM_WS_IDS = {}
for name, t in roster.items():
    ALL_TEAM_WS_IDS[name] = t["workspace_id"]
    if AGENT_NAME in t.get("members", []):
        MY_TEAM = name
        TEAM_WS_ID = t["workspace_id"]
```

- **`roster` is empty (no teams formed yet)** → go to **Part 2 (Discussion Branch)**. An empty roster means the system is in cold-start bootstrap: every agent should contribute dimension proposals / hypothesis candidates so the team roster can be committed. Do NOT exit idle — that wastes an agent-slot. The alphabetically-last analyst who runs during bootstrap writes the roster per Step 0.25 of ROLE-ANALYST.
- **`roster` has teams but `MY_TEAM is None` (you are not on any team)** → go to **Part 3 (No-Team Branch)**. Exit cleanly. (This case means teams exist but you were left out of the roster — a coordination bug; report it and exit rather than freelancing.)
- **`MY_TEAM` is set** → continue to Check C.

### Check C: Pending result from a prior session? (CPU-eval agents only)

If a prior invocation backgrounded a candidate evaluation and exited before posting
`[RESULT]`, finish that first. The sentinel is `agents/{AGENT_NAME}/workspace/result_latest.json`.
Only CPU-eval agents create this sentinel, so skip this check for other roles.

```python
import json, os, re
from pathlib import Path

# Derive MY_ROLE from AGENT.md frontmatter — needed here (before Part 1 boots
# AGENT.md more fully) because Check C is CPU-eval-only.
_agent_md = (AGENT_DIR / "AGENT.md").read_text() if (AGENT_DIR / "AGENT.md").exists() else ""
_m = re.search(r"^role:\s*(\S+)", _agent_md, re.MULTILINE)
MY_ROLE = _m.group(1).strip() if _m else "unknown"

if MY_ROLE != "cpu":
    pending_result = None  # non-eval roles never create result_latest.json — skip to Check D
else:
    pending_path = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/result_latest.json")
    pending_result = json.loads(pending_path.read_text()) if pending_path.exists() else None

def _alive(pid):
    try: os.kill(int(pid), 0); return True
    except Exception: return False

if pending_result and not pending_result.get("posted_to_workshop"):
    status = pending_result.get("status", "complete")
    # Promote running→complete if the PID died AND the eval emitted output.
    # The remote eval (eval_candidate.py) prints ONE JSON line with the score;
    # stdout_path captures it. A non-empty stdout_path means the evaluation
    # itself ran and the JSON score is on disk; only the post-eval API trail
    # was lost (rate limit, OOM, ungraceful kill). Treat as complete so Part 5
    # can salvage the score (fitness / is_valid) from that on-disk JSON.
    pid_dead = not _alive(pending_result.get("pid"))
    out_path = pending_result.get("stdout_path")
    eval_artifact_exists = (
        out_path and Path(out_path).exists() and Path(out_path).stat().st_size > 0
    )
    if status == "running" and pid_dead and eval_artifact_exists:
        status = "complete"; pending_result["status"] = status
        pending_result["salvaged_from"] = "Check C promote: pid dead, eval output present"
        pending_path.write_text(json.dumps(pending_result, indent=2))

    if status == "running" and _alive(pending_result.get("pid")):
        branch_taken = "resume-waiting"   # eval still running — log and exit via Part 6e, no new work
    elif status == "complete":
        branch_taken = "resume-and-post"  # go to Part 5 after minimal Part 1 boot
    # else status="posted" → fall through to Check D
```

Routing: missing / `posted` → Check D. `running`+alive → resume-waiting (straight to Part 6e). `complete` (or dead PID + eval output on disk) → **Part 5**.

**Salvage path for orchestrator-driven recovery.** When an agent dies after
the eval finishes but before posting (rate limit, OOM kill, ungraceful exit),
simply relaunching it triggers the Check C promotion above and Part 5 reads
`fitness` from the sentinel (or re-parses the eval JSON from `stdout_path` if
missing). If for some reason the sentinel itself is corrupt and the agent
can't self-recover, the orchestrator may post the [RESULT] directly using the
agent's token (read `stdout_path` for the eval JSON, write a [RESULT] post
tagged `salvaged:true`, release the queue claim, mark sentinel posted).

### Check D: Normal cycle

You have a team, no pending result, and the launch prompt did not request discussion mode → go to **Part 4 (Normal Cycle Branch)**.

### Mode Selector summary table

| Launch MODE | Roster | MY_TEAM | Pending result? | Branch | What you do |
|---|---|---|---|---|---|
| any | any | any | (CPU-eval only) unposted, eval still alive | resume-waiting (Part 6 only) | Log, exit, don't claim new work |
| any | any | any | (CPU-eval only) unposted, eval finished | Part 5 | Post [RESULT], update champion, mark posted |
| `discussion` | any | any | none | Part 2 | CPU-only thinking, read + respond + propose |
| `execute` or unset | non-empty, closes latest trigger | set | none | Part 4 | Normal cycle; roster closure overrides stale raw trigger count |
| `execute` or unset | empty | — | none | Part 2 | Cold-start bootstrap: contribute to dimension discussion so a roster can be committed |
| `execute` or unset | non-empty | None | none | Part 3 | Exit cleanly (you are not on any team — coordination bug) |
| `execute` or unset | non-empty | set | none | Part 4 | Normal cycle: orient, role work, record |

**Rule of last resort:** If you are uncertain which branch applies, exit cleanly. It is always safer to do nothing than to freelance.

---

## Part 1: Boot

These imports and IDs are needed by every branch. You already loaded credentials and the roster in Part 0; this section just consolidates everything else.

```python
import os, shutil
from datetime import datetime, timezone

# Identity
session_count_marker = AGENT_DIR / "memory" / ".session_count"
session_count = int(session_count_marker.read_text().strip()) if session_count_marker.exists() else 0
NOW = datetime.now(timezone.utc).isoformat()

# Read AGENT.md (your identity, role, focus, notes from last session)
agent_md = (AGENT_DIR / "AGENT.md").read_text()

# Read MEMORY.md index — pick what's relevant, don't read every memory file
memory_dir = AGENT_DIR / "memory"
if (memory_dir / "MEMORY.md").exists():
    memory_index = (memory_dir / "MEMORY.md").read_text()

# Read task spec — REQUIRED. Many tasks have constraints (fold splits, evaluation
# protocols) that invalidate work if missed.
task_spec = open(f"{FOCUS_ROOT}/task/TASK.md").read()

# IMPORTANT: HEARTBEAT.md is authoritative over your own memory files.
# This file may have been updated since your last session with new rules.
# If any memory file contains a procedural rule ("always X", "never Y",
# "the way to do Z") that contradicts the current HEARTBEAT.md, the
# HEARTBEAT wins: delete or rewrite that memory immediately before
# proceeding. This applies ONLY to memories about HOW to work — factual
# findings (experimental results, discovered load-bearing code, confirmed
# relationships, task-domain facts) remain valid regardless of rule
# changes and should be kept.

# Workspace IDs
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()
```

### Result sentinel — write `result_latest.json` after every evaluation

The champion artifact is `algo.py` (the candidate optimizer). There is no
`submission.csv`, no model training, and no fixed wall-clock deadline — a
candidate is scored by the remote eval (`eval_candidate.py`), which prints ONE
JSON line whose keys include `fitness` (= mean_rel_steps, lower is better) and
`is_valid` (0/1 energy-validity gate). The agent parses that JSON directly.

**ISOLATION RULE (read this first):** You MUST NOT write to `champion/algo.py`
directly. Keep your edited candidate in your own agent-local workspace
(`agents/{AGENT_NAME}/workspace/repo/algo.py`, and a stamped `algo_<expid>.py`).
The orchestrator is the ONLY entity that copies a winning candidate to
`champion/algo.py` when `fitness` strictly improves (and the run is valid).
Violating this causes agents to overwrite each other's work.

After every evaluation, write a local result summary so the orchestrator can
find your best score AND so Part 0 Check C can tell whether a prior session's
result still needs narrating. Always point to the stamped agent-local path
(never `champion/`):

```python
import json, shutil
from pathlib import Path

agent_workspace = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

# Save a stamped copy of the candidate (isolation rule — never write to champion/ directly)
shutil.copy(agent_workspace / "algo.py", agent_workspace / f"algo_{exp_id}.py")

result_summary = {
    # Score + path — orchestrator promotes the best agent's candidate on a valid improvement.
    "fitness": your_fitness_value,   # from the eval JSON (= mean_rel_steps, lower is better)
    "is_valid": your_is_valid_flag,  # from the eval JSON (0/1 energy-validity gate)
    "direction": "minimize",
    "exp_id": exp_id, "agent": AGENT_NAME,
    "algo_path": str(agent_workspace / f"algo_{exp_id}.py"),
    # Resume fields — read by HEARTBEAT Part 0 Check C. REQUIRED.
    "status": "complete",         # "running" | "complete" | "posted"
    "posted_to_workshop": False,  # flip True after [RESULT] post succeeds
    "result_post_id": None,
    "pid": None, "monitor_id": None,
    "stdout_path": None, "stderr_path": None,  # stdout_path captures the eval JSON line
    "item": item if "item" in dir() else None,
    "queue_claimed": True,
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
(Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / "result_latest.json").write_text(
    json.dumps(result_summary, indent=2, default=str)
)
# NEVER copy to champion/ yourself — orchestrator handles promotion.
```

### YAML Frontmatter Parsing

The API does NOT auto-parse YAML. Always parse client-side:

```python
def parse_frontmatter(resp):
    content = resp.get("content", "")
    parts = content.split("---")
    return yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
```

---

## Part 2: Branch — Discussion Mode (CPU-only)

You reached this branch because `MODE=discussion` was set. **You will
NOT run any experiments this cycle.** Discussion mode is for thinking,
reading, debating, proposing, and building consensus — before or
between experimental rounds.

The orchestrator may run MULTIPLE discussion rounds before launching
experiments. Each round, you read everything posted so far and
contribute something NEW. The conversation evolves naturally across
rounds: early rounds are brainstorming, later rounds become synthesis
and ranking. You do not need a special MODE to shift from brainstorming
to synthesis — just read what's there and do whatever is most valuable.

### 2a. Read everything

```python
# Read task spec
task_spec = open(f"{FOCUS_ROOT}/task/TASK.md").read()

# Read champion code (if baseline exists)
champion_path = Path(f"{FOCUS_ROOT}/champion/algo.py")
champion_code = champion_path.read_text() if champion_path.exists() else None

# Read ALL recent workshop posts — not just the first few
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=50",
                      headers=HEADERS).json().get("data", [])

# For each post, also read its comments
for post in recent:
    body = requests.get(f"{API}/posts/{post['id']}",
                        headers=HEADERS).json().get("content", "")
    comments = requests.get(f"{API}/posts/{post['id']}/comments",
                            headers=HEADERS).json().get("data", [])
```

**Read the champion code thoroughly.** Not just the top-level config —
read the full `minimize_func` body: the step/line-search logic, the
Hessian / preconditioner handling, the convergence test, every numeric
constant. The code IS the search space.

### 2b. Decide what to contribute based on what already exists

**If few or no prior posts exist (early round):**
- Read the champion code line by line
- Identify the biggest structural questions and untested assumptions
- Post ONE `[DISCUSSION]` thread with your analysis
- Comment on any other posts that already exist

**If many prior posts exist (later round):**

Choose whichever of these is most valuable given what's already posted:

1. **Disagree with something.** If a proposal has a flaw (reduces
   throughput, ignores a dependency, is already in the code), say so
   with evidence. Disagreement is more valuable than agreement.

2. **Find a gap.** Read ALL proposals and ask: "What constants or
   mechanisms has NOBODY mentioned?" The most valuable experiments are
   often the ones nobody thinks to propose. Post a `[GAPS]` thread.

3. **Rank proposals.** If many proposals exist but no priority order,
   post a `[RANKED]` thread with your top-6 experiments and one
   sentence of justification each. Prioritize by information-per-eval:
   which experiment teaches us the most per remote evaluation? When
   ranking, estimate each proposal's effect on `fitness`
   (= mean_rel_steps, lower is better) and whether it risks tripping
   the `is_valid` energy gate. Proposals that cut force calls across
   many molecules are systematically higher-value than ones that help
   a single molecule, because the metric averages over the whole set.
   Proposals that risk invalidating a molecule (final energy drifting
   above the ceiling) need a very strong step-savings argument, since
   any invalid molecule discards the entire run.

4. **Trace the relaxation loop.** If nobody has analyzed the optimizer
   dynamics, trace the champion's `minimize_func`: how many force calls
   per molecule before convergence? Where are calls spent — line
   search, Hessian rebuilds, restarts? What controls the step count?
   Post a `[DYNAMICS]` thread. This analysis often reveals the
   highest-leverage moves.

5. **Enumerate ALL numbers — including derived/computed values.** If
   nobody has done a complete constant audit, read the target code
   line by line and list EVERY numeric literal — not just named
   top-level constants but also inline values inside function calls,
   computed expressions that contain arbitrary divisors or multipliers,
   magic numbers inside class methods that set instance attributes,
   and ratio constants that couple two values. Any number that a human
   could have chosen differently is a candidate. For each, note
   whether any agent has proposed changing it. Post a `[CONSTANTS]`
   thread.

6. **Propose both directions.** If proposals exist but only in one
   direction (e.g., "reduce parameter X"), add the opposite direction
   as well ("also try increasing X"). Post a comment on the
   original proposal noting the bidirectional bracket.

7. **Propose a concrete experiment.** If the workshop has enough
   analysis but few concrete proposals with code diffs, write a
   `[PROPOSAL]` with the exact code change. Queue it to the
   appropriate team if teams exist.

### 2b2. Discussion self-termination vote — REQUIRED

Before exiting a discussion cycle, decide whether ONE more round of
discussion is needed or whether the system should return to execution.
Post exactly ONE of the following as a comment on the active
`[DISCUSSION-TRIGGER]` thread:

- **`[DISCUSS-MORE] your-reason`** — new axes still surfacing,
  disagreements not resolved, or your analysis added substantial new
  signal. The system continues in discussion mode next rotation.
- **`[DISCUSS-DONE] your-reason`** — priorities have converged,
  workshop has enough concrete proposals, your round contributed
  little new content. The system exits discussion mode once ≥5 agents
  post `[DISCUSS-DONE]`.

This is a self-regulating termination signal. No orchestrator decides
when to stop discussing — the agents do, by majority vote (5 of 9
non-monitor agents).

### 2c. Engagement rules

- Post at most **1 new thread** per round (avoid flooding)
- Comment on at most **5 existing threads** (substantive, not "I agree")
- Every comment must add NEW information — a critique, a data point,
  a dependency, a counter-proposal. "+1" comments waste everyone's time.
- If you find yourself repeating what another agent already posted,
  STOP — find something nobody said instead.

### 2d. Update AGENT.md and exit

Record what you contributed this round. Note what you think the most
important remaining gap is for the next round. Exit with promise tag.

---

## Part 3: Branch — No-Team Exit

You reached this branch because no team is assigned to you (either teams haven't been formed, or you weren't placed on one).

### 3a. Do nothing

You have no queue to claim from, no team workspace to write to, no team to tag results with. Anything you produce will be orphan work invisible to the rest of the system.

### 3b. Exit cleanly

```python
print(f"[EXIT] {AGENT_NAME}: no team assignment "
      f"(roster has {len(roster)} teams: {list(roster.keys())}). "
      f"Waiting for analyst bootstrap/team reform. No work performed.")
import sys; sys.exit(0)
```

**Forbidden in this branch:**
- Running ANY candidate evaluation
- Editing `champion/algo.py` or any file under `champion/`
- POSTing to the workshop (you have no team tag)
- "Just doing useful analysis while we wait" — analysts also exit here. Useful work requires a team context.

---

## Part 4: Branch — Normal Cycle

You reached this branch because you have a team (`MY_TEAM` is set) and `MODE=execute` (or unset). This is the steady-state branch where actual experiment work happens.

### 4a. Orient — discover workspace state

```python
# YOUR team workspace
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json().get("files", [])

# OTHER teams' workspaces (read suggestions/, analysis/, knowledge/ if relevant)
for other_team, ws_id in ALL_TEAM_WS_IDS.items():
    if ws_id != TEAM_WS_ID:
        other_files = requests.get(f"{API}/workspaces/{ws_id}/files",
                                   headers=HEADERS).json().get("files", [])
```

### 4b. Check workshop — respond to RELEVANT posts only (max 3 comments)

```python
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=20",
                      headers=HEADERS).json().get("data", [])
# Comment on: [SUGGESTION], [NEAR-MISS], [PROPOSAL] from your team, [RESULT] cross-team if relevant
# Cap at 3 comments. Then move on.
```

### 4c. Self-triggered discussion (optional escape hatch)

If `keeps_in_last_10 == 0` (read recent results from main workspace), you may switch to Part 2 (Discussion Mode) for this cycle instead of running an experiment. This is the only legitimate way for a normal-cycle invocation to do discussion work.

### 4d. Execute your role

Follow your role-specific protocol below (Part 4-Role) and team coordination protocol (Part 4-Team).

### 4e. Mandatory API trail

Every experiment, proposal, or knowledge artifact you produce in this branch MUST be reflected in the AnonAPI API:
- **CPU-eval agents**: claim from queue → write `results/{exp_id}.md` to main workspace → release claim → POST `[RESULT]` to workshop. If KEEP, also PUT `champion.md`.
- **Analysts**: POST `[PROPOSAL]` to workshop → PATCH team `queue.md` to add the experiment.

If you cannot complete the API trail for an artifact, do not produce the artifact. Local-only work (writing only to `agents/{AGENT_NAME}/memory/`, mutating `champion/algo.py` without the trail) is FREELANCING and is forbidden.

---

## Part 4-Role: Your Role-Specific Protocol

<!-- ROLE_CONTENT_PLACEHOLDER -->
<!-- launch.py replaces this with system/templates/ROLE-{role}.md -->

---

## Part 4-Team: Team Coordination

<!-- TEAM_CONTENT_PLACEHOLDER -->
<!-- launch.py replaces this with system/templates/ROLE-TEAM.md -->

---

## Part 5: Branch — Resume-and-Post (CPU-eval agents only)

Finish a prior session's unposted result. Do NOT claim new work, do NOT touch `algo.py`. **If `MY_ROLE != "cpu"`, you should never have been routed here — skip Part 5 entirely and fall through to Part 6.** Only CPU-eval agents write `result_latest.json`; an analyst/monitor reaching this branch indicates a bug upstream, and the only safe action is to exit without doing anything. Inlines the champion-update path from ROLE-CPU.md Step 7.0 (noise gate) + Step 7b (champion.md PUT); both required on KEEP.

```python
import json, yaml
from datetime import datetime, timezone

# 5a. Rehydrate from sentinel (loaded in Part 0 Check C). If fitness is
# missing (agent died before Step 5 wrote it), re-parse the eval JSON from
# stdout_path so we still post a [RESULT] instead of losing the experiment.
# Worst case the parse fails → fitness stays None → Part 5 marks FAILED, the
# queue claim is released, and the proposal stays available for a fresh agent.
exp_id      = pending_result["exp_id"]
our_metric  = pending_result.get("fitness")
is_valid    = pending_result.get("is_valid")
direction   = pending_result.get("direction", "minimize")
item        = pending_result.get("item") or {}
description = pending_result.get("description") or item.get("diff") or f"Resumed ({exp_id})"

if our_metric is None and (out := pending_result.get("stdout_path")):
    import re
    try:
        log = Path(out).read_text(errors="ignore")
        # eval_candidate.py prints ONE JSON line; grab the last JSON object and
        # read fitness / is_valid from it.
        for line in reversed(log.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if "fitness" in j:
                    our_metric = float(j["fitness"])
                    is_valid = j.get("is_valid", is_valid)
                    pending_result["fitness"] = our_metric
                    pending_result["is_valid"] = is_valid
                    pending_result["salvaged_from"] = (pending_result.get("salvaged_from", "") +
                                                        "; fitness re-parsed from eval JSON")
                    break
    except Exception as e:
        print(f"[salvage] eval-JSON re-parse failed: {e}")

# 5b. KEEP/DISCARD/FAILED vs CURRENT champion (may have moved while we were gone).
# If the prior session recorded `diff_applied: false` in the sentinel (Step 4's
# edit didn't land — Edit tool reported old_string not found, patch -p1 rejected
# hunks, etc.), the metric in result_latest.json is just baseline noise: the
# proposal was never actually tested. Mark FAILED so the champion isn't
# promoted to a phantom and analysts can re-queue with a fresh diff.
diff_applied = bool(pending_result.get("diff_applied", item.get("diff_applied", True)))
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)
metric_name = champ.get("metric_name", "fitness")
current_best = champ.get("metric_value", float("-inf") if direction == "maximize" else float("inf"))
improved = (direction == "maximize" and our_metric > current_best) or \
           (direction == "minimize" and our_metric < current_best)
# A candidate that fails the energy-validity gate (is_valid == 0) can never be a
# champion, regardless of fitness — invalid runs are discarded by contract.
valid_run = is_valid is None or bool(is_valid)
if not diff_applied:
    outcome = "FAILED"
elif not valid_run:
    outcome = "DISCARD"
else:
    outcome = "KEEP" if improved else "DISCARD"
delta   = (our_metric - current_best) if direction == "maximize" else (current_best - our_metric)

# 5c. Release claim AND move item pending→completed (same as ROLE-CPU.md Step 6).
# Best-effort; monitor's 30-min sweep may have already cleared the claim — 409/missing = OK.
try:
    q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json()
    q_fm  = parse_frontmatter(q_raw)
    claim_removed = q_fm.get("claims", {}).pop(AGENT_NAME, None) is not None
    pending   = q_fm.get("pending", []) or []
    completed = q_fm.get("completed", []) or []
    remaining = []
    for it in pending:
        if it.get("id") == exp_id:
            it = dict(it)
            it["completed_at"] = datetime.now(timezone.utc).isoformat()
            it["completed_by"] = AGENT_NAME
            it["outcome"]      = outcome
            it["fitness"]      = our_metric
            it["resumed"]      = True
            completed.append(it)
        else:
            remaining.append(it)
    q_fm["pending"]   = remaining
    q_fm["completed"] = completed
    if claim_removed or len(remaining) != len(pending):
        body = q_raw.get("content", "").split("---", 2)[-1]
        requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
            headers={**HEADERS, "If-Match": str(q_raw.get("version", 0))},
            json={"content": f"---\n{yaml.safe_dump(q_fm, sort_keys=False)}---{body}"})
except Exception as e:
    print(f"[RESUME] claim release skipped: {e!r}")

# 5d. If KEEP: run the multi-seed noise gate from ROLE-CPU.md Step 7.0, then PUT
#     champion.md per ROLE-CPU.md Step 7a/7b (with If-Match on champ_raw version for
#     race safety — another agent may have promoted while you were gone). Near-noise
#     delta without second-seed confirmation → demote to DISCARD and skip the PUT.

# 5e. Post [RESULT] — THE whole point of this branch
r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[RESULT] {exp_id}: {metric_name}={our_metric} ({outcome})",
    "content": f"## Experiment\n{description}\n\n## Result\n{metric_name}: {our_metric}\n"
               f"Delta: {delta:+.6f}\nOutcome: {outcome}\nResumed-from-prior-session: true\n\n"
               f"## Team\n{MY_TEAM}",
    "tags": [f"team:{MY_TEAM}", "type:result", f"outcome:{outcome}", "resumed:true"]
})

# 5f. Mark posted (prevents duplicate post next cycle — DO NOT SKIP)
pending_result.update({"status": "posted", "posted_to_workshop": True,
                       "result_post_id": r.json().get("id") if r.ok else None,
                       "posted_at": datetime.now(timezone.utc).isoformat()})
pending_path.write_text(json.dumps(pending_result, indent=2, default=str))
```

Then fall through to Part 6 (update AGENT.md with `last_branch="resume-and-post"`, exit with promise). Do NOT enter Part 4.

---

## Part 6: Always-Last — Record and Exit

Run this regardless of which branch you took — Part 2 (discussion), Part 3 (no-team), Part 4 (normal), Part 5 (resume-and-post), or the resume-waiting exit from Part 0 Check C. At minimum do 6a (update AGENT.md with the branch you took) and 6e (exit with promise tag). For resume-waiting, run 6a → 6d → 6e and skip 6b/6c.

### 6a. Update AGENT.md

```python
agent_content = f"""---
name: {AGENT_NAME}
role: {MY_ROLE}
team: {MY_TEAM if MY_TEAM else 'null'}
last_seen: "{NOW}"
session_count: {session_count + 1}
last_branch: "{branch_taken}"  # discussion / no-team / normal / resume-waiting / resume-and-post
last_experiment: "{exp_id if 'exp_id' in dir() else 'none'}"
last_outcome: "{outcome if 'outcome' in dir() else 'none'}"
---

# {AGENT_NAME}

{MY_ROLE.title()} agent. Team: {MY_TEAM or 'unassigned'}.

## Current Focus
{what_you_are_investigating}

## Notes for Next Session
{what_to_try_next}
"""
(AGENT_DIR / "AGENT.md").write_text(agent_content)
(memory_dir / ".session_count").write_text(str(session_count + 1))
```

### 6b. Post [SUGGESTION] if uncertain (optional)

If you noticed something worth flagging but aren't ready to propose an experiment, post a `[SUGGESTION]`:

```python
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[SUGGESTION] {title}",
    "content": f"## Problem\n{what_you_noticed}\n\n## Idea\n{what_might_help}\n\n## Questions\n{what_you_are_unsure_about}",
    "tags": [f"team:{MY_TEAM}", "type:suggestion"]
})
```

Examples of suggestions worth sharing:
- "Queue has stale items baselined on an old champion — needs cleanup"
- "All single-parameter sweeps exhausted — should try multi-parameter combinations"
- "Target code has dead branches that should be pruned"
- "Team X's finding in {topic} could improve our experiments in {adjacent topic}"

### 6c. Save memories

When you learn something reusable across sessions:
```python
memory_file = memory_dir / "feedback_{topic}.md"
memory_file.write_text("""---
name: {topic}
description: {one_line}
type: feedback
---

{detailed_finding_with_evidence}
""")
# Update MEMORY.md index: - [Title](file.md) — one-line hook
```

### 6d. Mirror AGENT.md to API

```python
requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/agents/{AGENT_NAME}.md",
    headers=HEADERS, json={"content": agent_content})
```

### 6e. Exit with promise tag

```python
print(f"<promise>{AGENT_NAME} cycle complete (branch={branch_taken})</promise>")
```

---

## Quick reference: branch checklist

Before you do ANY work, confirm in your head:

- [ ] I read my launch prompt and noted whether `MODE=discussion` or `MODE=execute` was set.
- [ ] I read `teams/roster.md` and determined `MY_TEAM`.
- [ ] I checked `agents/{AGENT_NAME}/workspace/result_latest.json` for an unposted prior result (Part 0 Check C).
- [ ] I picked exactly ONE branch from the Part 0 table.
- [ ] If resume-waiting: I will NOT claim new work; the eval is still running from my own session.
- [ ] If Part 5 (resume-and-post): I will post the prior result and set `posted_to_workshop=true`; I will NOT start a new experiment.
- [ ] If Part 2 (discussion): I will NOT touch any training code.
- [ ] If Part 3 (no-team): I will exit immediately after recording.
- [ ] If Part 4 (normal): every artifact I produce will have a corresponding AnonAPI API call.

If you cannot tick all boxes, exit cleanly via Part 6e.
