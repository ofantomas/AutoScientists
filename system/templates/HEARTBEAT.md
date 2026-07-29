---
name: multi-agent-focus-heartbeat
description: Template for per-agent HEARTBEAT.md. launch.py injects role + team sections to produce a complete self-contained file per agent.
---

# Agent Heartbeat

**This file is YOUR complete guide. Read it top to bottom on every invocation.**

The heartbeat has 5 parts. Part 0 (Mode Selector) is mandatory and routes you to the correct branch. **Do NOT skip Part 0. Do NOT execute Parts 1–5 until Part 0 has explicitly told you which branch to follow.**

```
Part 0   Mode Selector ......... pick your branch (5 min, mandatory)
Part 1   Boot ................... credentials, paths, identity
Part 2   Branch — Discussion .... CPU-only thinking, post [DISCUSSION], exit
Part 3   Branch — No-Team ....... exit cleanly, no work
Part 3.5 Branch — Monitor ....... run the health pass ONCE, exit (monitor role only)
Part 4   Branch — Normal Cycle .. orient, role-specific work, record, post
Part 5   Branch — Resume & Post . finish an unposted result from a prior session
Part 6   Always-Last ............ update AGENT.md, mirror to API, exit with promise
```

---

## Part 0: Mode Selector — DO THIS FIRST

Before ANY other work, you must determine which branch to execute. Follow the checks below **in order**. Stop at the first branch that matches.

**Bind the branch variable FIRST.** Part 6a and Part 6e both interpolate `branch_taken`, and they
run on EVERY branch — including the ones that never execute Check C's Python (monitor via Check A0,
discussion via Check A or A2, no-team via Check B). Bind the default now, before any check, and
re-assign it on whichever route you take:

```python
branch_taken = None   # set by whichever check matches:
                      #   "monitor-health" | "discussion" | "no-team" | "normal"
                      #   | "resume-waiting" | "resume-and-post"
```

### Check A0: Are you the monitor? (role gate — runs before every other check)

Every other check in this Mode Selector asks a question about *work*: what MODE the launch prompt
requested, whether a discussion round is open, which team you are on. The monitor answers "none of
those" to all of them — it is deliberately **not** a member of any team (teams are for agents that
run experiments), so a team-membership test can never route it correctly, and a discussion trigger
must not divert it either (ROLE-MONITOR forbids the discussion branch). Ask the role question first,
so the monitor never falls through to a check that has no correct answer for it.

```python
import re
from pathlib import Path

AGENT_DIR = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}")

# MY_ROLE comes from AGENT.md frontmatter (`role: monitor` | `cpu` | `analyst`), written by
# launch.py. Local file read only — no credentials needed yet.
_agent_md = (AGENT_DIR / "AGENT.md").read_text() if (AGENT_DIR / "AGENT.md").is_file() else ""
_m = re.search(r"^role:\s*(\S+)", _agent_md, re.MULTILINE)
MY_ROLE = _m.group(1).strip() if _m else "unknown"

# Name fallback: if AGENT.md is missing or its frontmatter is unreadable, the agent name still
# identifies the monitor (launch.py names it `{prefix}_monitor`). Without this, an unreadable
# AGENT.md would drop the monitor back into the checks that cannot route it.
if MY_ROLE == "unknown" and "monitor" in AGENT_NAME:
    MY_ROLE = "monitor"

if MY_ROLE == "monitor":
    branch_taken = "monitor-health"
    print(f"[MODE] {AGENT_NAME}: role=monitor -> Part 3.5 (Monitor Health Pass)")
    # STOP HERE. Do not evaluate Check A, A2, B, C, or D.
```

- **`MY_ROLE == "monitor"`** → set `branch_taken = "monitor-health"` and go to **Part 3.5 (Monitor
  Health Pass)**. This is the only branch a monitor ever takes, on every invocation, in every phase
  — cold start included. It ignores `MODE` entirely: a monitor launched with `MODE=discussion` still
  runs the health pass, because ROLE-MONITOR forbids the discussion branch.
- **any other role** → continue to Check A.

**Never route a monitor to Part 2 or Part 3.** Part 2 (discussion) is forbidden to it by
ROLE-MONITOR; Part 3 (no-team exit) would mean the monitor's health pass never runs at all, which is
how the run loses its only janitor. Its empty team membership is correct by design, not the
coordination bug Part 3 exists to report.

`MY_ROLE` stays bound for the rest of the heartbeat (Check C and Part 6a both read it).

### Check A: Did the launch prompt set MODE?

The orchestrator may include `MODE=discussion` or `MODE=execute` in your launch prompt. Read your launch prompt carefully now.

- **`MODE=discussion`** → set `branch_taken = "discussion"` and go to **Part 2 (Discussion Branch)**. CPU-only. No experiments. Even if you are a CPU-eval agent, you do thinking work this cycle.
- **`MODE=execute`** (or no MODE set) → continue to Check A2.

### Check A2: Workshop-triggered discussion — agents self-regroup

*(Experiment-running roles only. A monitor never reaches this check — it left at Check A0.)*

Agents can trigger a system-wide discussion round without orchestrator
intervention. Before executing a normal cycle, search the workshop for
an unresolved `[DISCUSSION-TRIGGER]` post:

```python
recent = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit=30",
                      headers=HEADERS).json().get("data", [])
trigger_posts = [p for p in recent if "[DISCUSSION-TRIGGER]" in p.get("title", "")]

# A trigger is "active" if:
#   - it was posted within the last 3 rotations, AND
#   - its purpose is not already served, AND
#   - fewer than QUORUM [DISCUSS-DONE] comments exist on it
#
# QUORUM SCALES WITH THE ROSTER. launch.py sizes the roster (--cpu / --analysts), so a
# hardcoded count silently becomes a supermajority on a small roster and deadlocks the run:
# 5-of-9 is a simple majority, but 5-of-6 is 83%. Compute it from the actual roster.
import math, os
# Count only real agent DIRECTORIES. os.listdir returns ANY entry, so a stray
# .DS_Store or a leftover file inflates the roster and silently raises the quorum.
_agents_dir = f"{FOCUS_ROOT}/agents"
_non_monitor = [a for a in os.listdir(_agents_dir)
                if not a.startswith(".")
                and os.path.isdir(os.path.join(_agents_dir, a))
                and "monitor" not in a]
DISCUSS_QUORUM = max(2, math.ceil(len(_non_monitor) / 2))   # 9 -> 5, 6 -> 3, 4 -> 2

# PURPOSE-SERVED SHORT-CIRCUIT: a cold-start trigger exists to produce a roster. Once teams
# are committed (roster `phase: executing` / non-empty `teams`), the trigger has done its job
# and must NOT keep re-routing execute-mode agents into discussion — otherwise the queues stay
# full and no experiment ever runs, regardless of the vote tally.
_roster = parse_frontmatter(requests.get(
    f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md", headers=HEADERS).json())
_teams_committed = bool(_roster.get("teams")) or _roster.get("phase") == "executing"

if trigger_posts:
    active_trigger = trigger_posts[0]  # most recent
    done_count = count_comments_matching(active_trigger["id"], "[DISCUSS-DONE]")
    if done_count < DISCUSS_QUORUM and not _teams_committed:
        # Switch THIS agent into discussion mode
        print(f"[DISCUSSION-TRIGGER active] switching to Part 2")
        MODE = "discussion"
        branch_taken = "discussion"
        # fall through to Part 2
```

If an active trigger exists → go to **Part 2 (Discussion Branch)**.
Otherwise → continue to Check B.

### Check B: Do teams exist in the roster?

*(Experiment-running roles only. A monitor never reaches this check — it left at Check A0. The
roster omits the monitor on purpose, so both of this check's outcomes would be wrong for it.)*

```python
import json, os, requests, yaml
from pathlib import Path

AGENT_DIR = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}")
creds = json.load(open(AGENT_DIR / "credentials.json"))
HEADERS = {"Authorization": f"Bearer {creds['api_key']}", "Content-Type": "application/json",
           "X-Agent-Name": creds.get("agent_name", AGENT_NAME)}
API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()

def parse_frontmatter(resp):
    """Frontmatter dict, or {} — never raises, never returns None. `resp.get("content", "")` is
    NOT enough: a 404 comes back with the key PRESENT and set to None, so the default never
    fires and `None.split(...)` raises AttributeError. An empty frontmatter block makes
    `yaml.safe_load` return None. Both must resolve to {} — every caller treats this as a dict."""
    content = (resp or {}).get("content") or ""
    parts = content.split("---")
    return (yaml.safe_load(parts[1]) or {}) if len(parts) >= 3 else {}

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

- **`roster` is empty (no teams formed yet)** → set `branch_taken = "discussion"` and go to **Part 2 (Discussion Branch)**. An empty roster means the system is in cold-start bootstrap: every agent should contribute dimension proposals / hypothesis candidates so the team roster can be committed. Do NOT exit idle — that wastes an agent-slot. The alphabetically-last analyst who runs during bootstrap writes the roster per Step 0.25 of ROLE-ANALYST.
- **`roster` has teams but `MY_TEAM is None` (you are not on any team)** → set `branch_taken = "no-team"` and go to **Part 3 (No-Team Branch)**. Exit cleanly. (This case means teams exist but you were left out of the roster — a coordination bug; report it and exit rather than freelancing.)
- **`MY_TEAM` is set** → continue to Check C.

### Check C: Pending result from a prior session? (CPU-eval agents only)

If a prior invocation backgrounded a candidate evaluation and exited before posting
`[RESULT]`, finish that first. The sentinel is `agents/{AGENT_NAME}/workspace/result_latest.json`.
Only CPU-eval agents create this sentinel, so skip this check for other roles.

```python
import json, os, re
from pathlib import Path

branch_taken = None   # re-affirm the Part 0 default: this block must LEAVE it bound on every route

# MY_ROLE was already derived in Check A0; re-deriving is idempotent and keeps this
# block runnable on its own. Check C is CPU-eval-only, hence the role test below.
_agent_md = (AGENT_DIR / "AGENT.md").read_text() if (AGENT_DIR / "AGENT.md").is_file() else ""
_m = re.search(r"^role:\s*(\S+)", _agent_md, re.MULTILINE)
MY_ROLE = _m.group(1).strip() if _m else "unknown"

if MY_ROLE != "cpu":
    pending_result = None  # non-eval roles never create result_latest.json — skip to Check D
else:
    pending_path = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/result_latest.json")
    pending_result = json.loads(pending_path.read_text()) if pending_path.is_file() else None

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
    # `.is_file()`, not `.exists()`: stdout_path comes out of the sentinel JSON, so a
    # stale/garbled value could name a DIRECTORY — and `.stat().st_size` on a directory is
    # nonzero, which would fake an eval artifact that does not exist.
    eval_artifact_exists = (
        out_path and Path(out_path).is_file() and Path(out_path).stat().st_size > 0
    )
    if status == "running" and pid_dead and eval_artifact_exists:
        status = "complete"; pending_result["status"] = status
        pending_result["salvaged_from"] = "Check C promote: pid dead, eval output present"
        pending_path.write_text(json.dumps(pending_result, indent=2))

    if status == "running" and _alive(pending_result.get("pid")):
        branch_taken = "resume-waiting"   # eval still running — log and exit via Part 6e, no new work
    elif status in ("complete", "failed"):
        # "failed" is ROLE-CPU's TRAIN-eval failure sentinel (unapplied diff, scope abort,
        # answer-key reject, harness/Redis error). It tested nothing, but it still owes the
        # workshop a [RESULT] and owes the queue a RE-QUEUE, so it resumes exactly like
        # "complete" and Part 5's ladder turns it into outcome=FAILED. Falling through to
        # Check D here would start a brand-new experiment, never post the FAILED result, and
        # strand both the claim and the item. BOTH statuses bind branch_taken here.
        branch_taken = "resume-and-post"  # go to Part 5 after minimal Part 1 boot
    else:
        branch_taken = None               # status="posted" → nothing to resume, fall to Check D
else:
    branch_taken = None                   # no sentinel / non-cpu role → fall to Check D
```

Routing: missing / `posted` → Check D. `running`+alive → resume-waiting (straight to Part 6e). `complete`, `failed`, or dead PID + eval output on disk → **Part 5**. Every route out of this block — including `status: "failed"`, the non-`cpu` role skip, and the fall-through to Check D — leaves `branch_taken` bound (Check D then sets it to `"normal"`), so Part 6a never NameErrors.

**`status` tracks the TRAIN eval only.** The held-out test gate has its own sentinel keys —
`test_status` (`not_run` / `running` / `complete` / `failed`), `test_fitness`, `test_is_valid`,
`test_stdout_path`. A sentinel with `status: complete` but `test_status != "complete"` means the
agent died between the two evals; that is still a Part 5 resume, and Part 5 runs the test gate on
the already-frozen candidate before deciding anything.

**Salvage path for orchestrator-driven recovery.** When an agent dies after
the eval finishes but before posting (rate limit, OOM kill, ungraceful exit),
simply relaunching it triggers the Check C promotion above and Part 5 reads
`fitness` from the sentinel (or re-parses the eval JSON from `stdout_path` if
missing). If for some reason the sentinel itself is corrupt and the agent
can't self-recover, the orchestrator may post the [RESULT] directly using the
agent's token (read `stdout_path` for the eval JSON, write a [RESULT] post
tagged `salvaged:true`, release the queue claim, mark sentinel posted). An
orchestrator salvage NEVER promotes the champion: without a completed held-out
test gate the candidate is not promotable, so it is re-queued instead.

### Check D: Normal cycle

You have a team, no pending result, and the launch prompt did not request discussion mode → set `branch_taken = "normal"` and go to **Part 4 (Normal Cycle Branch)**.

### Mode Selector summary table

The table is read **top to bottom**; the first row that matches is your branch. The role row is
first because it is the only one decided without asking about teams or discussion state.

| Role | Launch MODE | Roster | MY_TEAM | Pending result? | Branch | What you do |
|---|---|---|---|---|---|---|
| `monitor` | any (ignored) | any, empty included | always None (by design) | n/a | **Part 3.5** | Health pass ONCE — stale claims, outcome counts, queue depth, falsification counters — then exit. No queue claims, no experiments, no `teams/roster.md`, no `champion/` |
| `cpu` | any | any | any | unposted, eval still alive | resume-waiting (Part 6 only) | Log, exit, don't claim new work |
| `cpu` | any | any | any | unposted, eval finished or `status: failed` | Part 5 | Finish the held-out test gate if missing, decide KEEP/NEAR_MISS/DISCARD/REJECTED_TEST/FAILED, write `results/{exp_id}.md`, post [RESULT], promote on KEEP, re-queue as `{exp_id}_stack` on NEAR_MISS, re-queue on FAILED, mark posted |
| `cpu` / `analyst` | `discussion` | any | any | none | Part 2 | CPU-only thinking, read + respond + propose |
| `cpu` / `analyst` | `execute` or unset | empty | — | none | Part 2 | Cold-start bootstrap: contribute to dimension discussion so a roster can be committed |
| `cpu` / `analyst` | `execute` or unset | non-empty | None | none | Part 3 | Exit cleanly (you are not on any team — coordination bug) |
| `cpu` / `analyst` | `execute` or unset | non-empty | set | none | Part 4 | Normal cycle: orient, role work, record |

**Routing notes.**

- **The monitor row is unconditional.** It is not a special case of the rows beneath it — the
  monitor matches on role alone, before `MODE`, before the discussion trigger, before the roster is
  ever read. Every column to its right is "any" on purpose.
- **The monitor's blank `MY_TEAM` is not the Part 3 case.** Part 3 exists for an
  experiment-running agent that teams forgot; the monitor is excluded from teams deliberately, so
  routing it to Part 3 would silently retire the run's only janitor.
- **Every branch leaves `branch_taken` bound** — `"monitor-health"` on this row — because Part 6a
  and 6e interpolate it on every route.

**Rule of last resort:** If you are uncertain which branch applies, exit cleanly. It is always safer to do nothing than to freelance.

---

## Part 1: Boot

These imports and IDs are needed by every branch. You already loaded credentials and the roster in Part 0 Check B (or, on the monitor branch, in Part 3.5a); this section just consolidates everything else.

```python
import os, shutil
from datetime import datetime, timezone

# Identity
session_count_marker = AGENT_DIR / "memory" / ".session_count"
session_count = int(session_count_marker.read_text().strip()) if session_count_marker.is_file() else 0
NOW = datetime.now(timezone.utc).isoformat()

# Read AGENT.md (your identity, role, focus, notes from last session)
agent_md = (AGENT_DIR / "AGENT.md").read_text()

# Read MEMORY.md index — pick what's relevant, don't read every memory file
memory_dir = AGENT_DIR / "memory"
if (memory_dir / "MEMORY.md").is_file():
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

### Review backlog — clear it BEFORE your branch's main work (every non-monitor agent)

A proposal is not claimable until somebody other than its author has reviewed it: every queue item
carries `review_status: pending | ok | blocked`, and a cpu-eval agent may claim only `ok`. There is
no time grace and no starvation override — an unreviewed item is simply never claimed — so when
nobody drains the backlog the queue stops moving while still looking perfectly healthy. That is why
this runs at boot, before your branch's own work.

**A review is a comment on the item's `proposal_post` whose FIRST line begins with exactly
`[REVIEW-OK]` or `[REVIEW-BLOCK]`**, followed by at least one sentence of substantive reasoning. A
comment without one of those two leading tags is ordinary discussion and moves nothing. **You may
never review your own proposal — that exclusion is absolute.** The claim-side rule is narrower than
it once was, and it narrows only WHO is barred: `review_status: ok` is still required, so an item
nobody has reviewed is claimable by nobody. What changed is that a cpu-eval agent is barred only
from an item whose ONLY `[REVIEW-OK]` is its own — there the single endorsement it would be relying
on is its own, which is self-service. Once a second, independent `[REVIEW-OK]` exists the item is
claimable by anyone, including you if you also reviewed it, because that other review supplies the
independent scrutiny the two-person rule is asking for. Barring every reviewer instead is what
strands a cold start: all agents boot at the same instant, all review the same few seeded items, and
all would then be barred from them permanently, since agent names persist across rotations. **Cross-team review is allowed and encouraged.** Scoping review to your own
team left the sole team analyst as the only eligible reviewer, so every item that analyst proposed
was permanently unreviewable and therefore permanently unclaimable.

**`REVIEW_CAP` is a CEILING on the work you owe, never a floor.** If the backlog is empty, post
NOTHING — zero reviews discharges the obligation completely. NEVER pad to reach a count: a
content-free "acknowledged" comment is a protocol violation, and producing them is exactly what the
old comment quota caused. The obligation is to drain the backlog, not to emit comments.

**Who skips this entirely:** the monitor (it audits results, it does not judge proposals, and it
posts only `[AUDIT]` / `[HYPOTHESIS-FALSIFIED]`), a no-team agent (Part 3 forbids it from posting at
all), and the resume-waiting exit (it does no work of any kind). Everyone else — analysts AND
cpu-eval agents, on the discussion, normal-cycle and resume-and-post branches — owes the backlog.

**This pass IS your role file's review-backlog step (ROLE-CPU Step 0.5 / ROLE-ANALYST Step 0.1) —
do not run a second batch; `REVIEW_CAP` is per spawn, not per step.** There is ONE obligation with
ONE ceiling, and its single execution site is right here: **Part 1 (Boot), § Review backlog**. Your
role file states the same rules because that is where the rest of your cycle points when it says
"reviewed", not because it is a second batch to run. An agent that drains `REVIEW_CAP` items here
and another `REVIEW_CAP` at its role step has spent four review slots in one spawn, padded past the
ceiling this section calls a violation, and starved the ordering the FIFO sort exists to enforce.

**On the resume-and-post branch this still runs FIRST**, before Part 5 posts the `[RESULT]` it
exists to salvage — the obligation has no branch carve-out. That is a deliberate trade-off, not an
oversight: it puts up to `REVIEW_CAP` comment POSTs and queue PUTs in front of a result that is
already once-stranded. Keep the pass exactly this size on that branch and never expand it; if a run
ever loses a result inside that window, change the protocol rather than reordering this file on
your own authority.

```python
# Needs HEADERS / API / ALL_TEAM_WS_IDS / MY_TEAM / MY_ROLE / parse_frontmatter — all bound in
# Part 0 (Check A0 and Check B), along with `requests` and `yaml`.
REVIEW_CAP   = 2                  # CEILING on reviews owed per spawn — never a quota to fill
REVIEW_OK    = "[REVIEW-OK]"      # the two exact tags. Nothing else is a review: do not invent
REVIEW_BLOCK = "[REVIEW-BLOCK]"   # variants, and do not lead a review with any other marker.

def item_review_status(cand):
    """The item's RECORDED status, read off the row, with the legacy fallback.

    THE LEGACY DEFAULT IS `pending`, NEVER `ok`, and the three helpers that resolve it must stay
    byte-identical: `review_scan()` (ROLE-CPU Step 0.5), this one, and `review_status_of()`
    (ROLE-ANALYST Step 0.1). All three live in the SAME composed file, so a row one of them
    calls cleared-and-claimable and another calls unreviewed splits the run's behaviour by
    whichever doc the agent happened to read.

    Distinct from ROLE-ANALYST's `review_status_of(item)`, which RE-COMPUTES the status from the
    proposal post's comments. This one only reports what the queue already says — that is what
    tells you whether a review is still owed, without a request per item."""
    stored = str(cand.get("review_status") or "").strip()
    if not stored:
        # Legacy rows predating `review_status`. Only an EXPLICIT `discussion_pending: false`
        # counts as cleared; absent-and-unknown is `pending`, because there is no path to
        # claiming an item that was never reviewed. Defaulting the unknown case to `ok` would
        # make any malformed or hand-written row instantly claimable — the exact hole the
        # override removal was meant to close. (The old rationale — "absent is how a stacked
        # re-test is queued" — is obsolete: stack rows now set `"review_status": "ok"`
        # explicitly at their creation sites, here in 5e and in ROLE-CPU Step 7.)
        stored = "ok" if cand.get("discussion_pending") is False else "pending"
    # Never WRITE `discussion_pending` on an item again — read-only legacy fallback.
    if not cand.get("proposal_post"):
        # UNREVIEWABLE, not cleared. A review IS a comment on `proposal_post`, so a row naming
        # no post gives every reviewer nowhere to put one and NOBODY can have reviewed it,
        # whatever the row claims about itself. Resolve `pending` — never `ok` — so it is
        # neither claimable nor addable to the backlog below. Repairing it (posting a real
        # [PROPOSAL] and filling the field in, or dropping the row) is an ANALYST job; it is
        # not something a reviewer clears. Same rule in ROLE-CPU's `review_scan()`.
        return "pending"
    return stored

def comment_is_by(c, name):
    """True when comment `c` was written by agent `name`. The comments API returns
    `author_name` / `author_id` / `author_display_name` — it does NOT return `author`.
    `c.get("author")` is None on every comment, so an author filter written against it
    matches nobody: the proposer's own comments then count as reviews and the two-person
    rule quietly disappears. (Same rule as ROLE-ANALYST's `_authored_by`.)"""
    if not name:
        return False
    return any(f and str(f) == str(name)
               for f in (c.get("author_name"), c.get("author_id"), c.get("author_display_name")))

def review_tag(c):
    """`[REVIEW-OK]` / `[REVIEW-BLOCK]` if the comment's FIRST line opens with one, else None."""
    _lines = (c.get("content") or "").strip().splitlines()
    _first = _lines[0].strip() if _lines else ""
    if _first.startswith(REVIEW_BLOCK):
        return REVIEW_BLOCK
    if _first.startswith(REVIEW_OK):
        return REVIEW_OK
    return None

def resolve_review_status(comments, proposed_by, unblocked=False):
    """One BLOCK outweighs any number of OKs; the author's own comments never count.
    `unblocked=True` (the row carries an analyst's `unblocked_by`) is the ONE thing that
    clears a standing block — without it this scan re-blocks the item on every pass and
    silently undoes the analyst's decision. Another `[REVIEW-OK]` never clears a block."""
    tags = [review_tag(c) for c in comments if not comment_is_by(c, proposed_by)]
    if REVIEW_BLOCK in tags and not unblocked:
        return "blocked"
    if REVIEW_OK in tags:
        return "ok"
    return "pending"

review_backlog = []
# Bind the "do I owe reviews at all" predicate ONCE, so this code and the "Who skips this
# entirely" paragraph above cannot drift apart. The summary print at the bottom is gated on it
# too: a monitor that reaches Part 1 must not log participation in an obligation it does not have.
OWES_REVIEW = (MY_ROLE != "monitor") and bool(MY_TEAM)
if OWES_REVIEW:
    for _tname, _tws in ALL_TEAM_WS_IDS.items():        # ALL teams, not just yours
        # One unreadable queue must NEVER kill the boot of every agent on every branch. A team
        # whose queue.md does not exist yet (roster just committed, its analyst has not run its
        # first Step 5 PUT) 404s, and any transport error raises. Warn, skip that team, keep
        # draining the others — this block runs BEFORE the branch's real work.
        try:
            _qfm = parse_frontmatter(requests.get(
                f"{API}/workspaces/{_tws}/files/queue.md", headers=HEADERS).json()) or {}
        except Exception as _qe:
            print(f"[REVIEW] queue.md unreadable for team {_tname}: {_qe!r} — skipping that team.")
            continue
        for _it in (_qfm.get("pending", []) or []):
            if item_review_status(_it) != "pending":
                continue                                 # already ok/blocked — nothing owed
            if _it.get("proposed_by") == AGENT_NAME:
                continue                                 # never review your own proposal
            if not _it.get("proposal_post"):
                # A review IS a comment on `proposal_post`. An item naming none cannot be
                # reviewed by anybody, so it is excluded here and NOBODY may stamp
                # `review_status: ok` on it — that would be clearance with no public record on
                # any post, unverifiable by every later reader. It stays `pending`, therefore
                # unclaimable. Repairing it (post a real [PROPOSAL], fill the field) is an
                # ANALYST job; if you are not an analyst, flag it in 6b and leave the row alone.
                continue
            review_backlog.append({"team": _tname, "ws": _tws, "item": _it})

# Drop what we already reviewed, then sort OLDEST FIRST so the backlog drains FIFO instead of every
# agent piling onto the newest proposal. A missing timestamp sorts LAST, never first.
_rows = []
for _b in review_backlog:
    _pid = _b["item"]["proposal_post"]
    try:
        _post = requests.get(f"{API}/posts/{_pid}", headers=HEADERS).json()
        _cs   = requests.get(f"{API}/posts/{_pid}/comments",
                             headers=HEADERS).json().get("data") or []
    except Exception as _pe:
        print(f"[REVIEW] proposal {_pid} unreadable: {_pe!r} — skipping that item.")
        continue                                         # a dead post is not a review we owe
    if any(comment_is_by(c, AGENT_NAME) and review_tag(c) for c in _cs):
        continue                                         # our review is already on record
    _b.update({"post_id": _post.get("id", _pid),
               "created_at": _post.get("createdAt") or _post.get("created_at") or "",
               # Keep the proposal text from THIS guarded read, so the review loop below needs no
               # second (unguarded) GET to judge the proposal.
               "body": _post.get("content") or ""})
    _rows.append(_b)
_rows.sort(key=lambda b: b["created_at"] or "9999")

for _b in _rows[:REVIEW_CAP]:
    _it = _b["item"]
    # READ the proposal, then judge it on substance: is the mechanism plausible and concretely
    # specified, does its axis already sit in that team's dead_ends.md / non_generalizable.md,
    # does it duplicate a pending item, does it change more than one mechanism at once? BLOCK is
    # for a defect that would waste an eval — not for taste, and not for "I would have proposed
    # something else". OK means "worth an eval", not "I agree with it".
    _prop_body = _b.get("body") or ""     # fetched in the guarded read above — READ IT, then judge

    # YOUR judgement, written as the SAME call ROLE-CPU Step 0.5 makes so neither half of the
    # composed file reads like a form to fill in. `review_proposal` is not a library function:
    # it stands for YOUR read of `_prop_body` against the checks above, returning
    # ("ok" | "block", reasoning) — 2-3 sentences naming something specific about THIS proposal.
    _verdict, reasoning = review_proposal(_it, _prop_body)
    verdict = REVIEW_BLOCK if str(_verdict).lower().startswith("block") else REVIEW_OK

    # HARD GUARD — never POST a template. An unsubstituted placeholder is exactly the
    # content-free comment this section calls a protocol violation, and it would mark the item
    # reviewed on the strength of nothing. Skipping costs one backlog slot; posting corrupts the
    # record for everyone who reads the post later.
    reasoning = str(reasoning or "").strip()
    if not reasoning or reasoning.startswith("<"):
        print(f"[REVIEW] SKIPPED {_it.get('id')}: no substantive reasoning produced — posting "
              f"nothing rather than a content-free comment.")
        continue

    # Re-check live, immediately before posting: another agent may have reviewed this item
    # between our backlog scan and now. Reviewing an already-cleared item wastes a review slot
    # and, under the every-reviewer-is-barred rule this replaced, could bar us from an item we
    # would otherwise be free to claim. This does NOT eliminate the simultaneous-boot race — every
    # agent boots at the same instant — it narrows the window from "every agent collides" to "at
    # most the few that read inside the same instant", which the sole-reviewer rule then absorbs.
    # Placed here, after your judgement, because that is the slow part: the closer this read sits
    # to the POST, the smaller the window it leaves.
    try:
        _cs_now = requests.get(f"{API}/posts/{_b['post_id']}/comments",
                               headers=HEADERS).json().get("data") or []
    except Exception as _ce:
        # Unreadable now (it was readable during the backlog build). Skip rather than post onto an
        # item whose current status we cannot confirm — the same call the backlog build makes for
        # a post it cannot read. It stays `pending` and the next spawn picks it up.
        print(f"[REVIEW] {_it.get('id')}: live re-check failed ({_ce!r}) — posting nothing.")
        continue
    _status_now = resolve_review_status(_cs_now, _it.get("proposed_by"),
                                        unblocked=bool(_it.get("unblocked_by")))
    # A BLOCK IS NEVER DROPPED. Skipping on any non-`pending` status would discard a block we were
    # about to write just because someone else's [REVIEW-OK] landed first — silently deleting the
    # one verdict that says "this is defective", under exactly the concurrency this re-read exists
    # for. Block-dominates is the rule precisely so a single reviewer who spots a real defect is
    # not outvoted, so an "ok" that arrived first is the case where our block matters MOST.
    # Only an existing block makes ours redundant.
    # (`verdict` here is the TAG constant set at :536, not the raw "ok"/"block" string —
    #  compare against REVIEW_BLOCK, never the literal "block", or this test never fires.)
    if _status_now == "blocked" or (_status_now != "pending" and verdict != REVIEW_BLOCK):
        print(f"[REVIEW] {_it.get('id')} is already review_status={_status_now} — another agent "
              f"reviewed it while we were reading; skipping without posting.")
        continue

    requests.post(f"{API}/posts/{_b['post_id']}/comments", headers=HEADERS,
                  json={"content": f"{verdict} {reasoning}"})

    # Update the item's review_status — read-modify-PUT on THAT team's queue.md, never PATCH
    # (dotted-key PATCH on nested frontmatter flattens `pending:` across teams). Re-read the
    # comments so our own review is included, then resolve with the rule above.
    # The whole read-modify-PUT is wrapped: it writes ANOTHER team's file, and the comment we
    # just posted is already the durable record — `review_scan` re-derives the status from it —
    # so a failure here must cost this one row, never the boot of every agent on every branch.
    try:
        _cs2 = requests.get(f"{API}/posts/{_b['post_id']}/comments",
                            headers=HEADERS).json().get("data") or []
        if not any(comment_is_by(c, AGENT_NAME) and review_tag(c) for c in _cs2):
            # Read-back lag (or a comment POST that silently failed): count OUR review anyway.
            # Resolving without it writes back `pending` for an item we just reviewed, and the
            # item stays unclaimable — the exact failure this whole obligation exists to prevent.
            _cs2 = _cs2 + [{"author_name": AGENT_NAME, "content": f"{verdict} {reasoning}"}]
        _q_raw  = requests.get(f"{API}/workspaces/{_b['ws']}/files/queue.md", headers=HEADERS).json()
        _q_fm   = parse_frontmatter(_q_raw)
        # De-duplicate `blocked:` BY ID up front. A re-block of a row that is already in the list
        # (an earlier reviewer blocked it, we read a stale `pending:` copy) would otherwise append
        # a SECOND row with the same id, and every consumer that resolves by id then reads
        # whichever copy it happens to hit first.
        _keep = []
        _blocked = [r for r in (_q_fm.get("blocked") or []) if r.get("id") != _it.get("id")]
        _status = None      # bound even when the row has left `pending:` since we read it
        for _row in (_q_fm.get("pending", []) or []):
            if _row.get("id") != _it.get("id"):
                _keep.append(_row)
                continue
            _row = dict(_row)
            _status = resolve_review_status(_cs2, _row.get("proposed_by"),
                                            unblocked=bool(_row.get("unblocked_by")))
            _row["review_status"] = _status
            _row.pop("discussion_pending", None)  # legacy twin — one item must not carry two truths
            if _status == "blocked":
                # Record the BLOCKING reviewer's reasoning. If our verdict was OK and an earlier
                # reviewer blocked it, name THEM and quote THEIR comment — never overwrite a
                # standing block with our own (non-blocking) reasoning.
                if verdict == REVIEW_BLOCK:
                    _row["blocked_reason"], _row["blocked_by"] = reasoning[:200], AGENT_NAME
                else:
                    _bc = next((c for c in _cs2 if review_tag(c) == REVIEW_BLOCK
                                and not comment_is_by(c, _row.get("proposed_by"))), {})
                    _row.setdefault("blocked_by", _bc.get("author_name")
                                    or _bc.get("author_display_name") or _bc.get("author_id")
                                    or "unknown")
                    _row.setdefault("blocked_reason", (_bc.get("content") or "")[:200]
                                    or "see the [REVIEW-BLOCK] comment on the proposal post")
                _blocked.append(_row)         # OUT of pending: — a blocked item is not claimable
            else:
                # `reviewed_by` records WHO has already reviewed this item. The two-person rule
                # is enforced from the COMMENTS, which are the record (ROLE-CPU's `review_scan`
                # returns `(review_status, mine_ok, oks)` and its claim loop bars only the SOLE
                # reviewer, `mine_ok and oks == 1`); this field is advisory bookkeeping on top of
                # it, so never treat it as the gate, and never derive a claim bar by counting its
                # length. Record yourself even though the item stays claimable BY SOMEBODY ELSE —
                # and, once a second independent [REVIEW-OK] lands on it, by you as well.
                _rb = list(_row.get("reviewed_by") or [])
                if AGENT_NAME not in _rb:
                    _rb.append(AGENT_NAME)
                _row["reviewed_by"] = _rb
                _keep.append(_row)
        if _status is None:
            # Our row is not in the `pending:` we just read — either another agent moved it while
            # we were reading, or the read came back empty/404 (`parse_frontmatter` now returns
            # {} instead of raising). PUT nothing: writing `_keep`/`_blocked` built from a queue
            # we never actually read would push `pending: []` over that team's live file and
            # delete every item on it. The comment we posted is the record.
            print(f"[REVIEW] {_it.get('id')} no longer in {_b['team']}'s pending: (or queue.md "
                  f"unreadable) — comment posted, queue row not rewritten.")
            continue
        _q_fm["pending"], _q_fm["blocked"] = _keep, _blocked
        _q_body = (_q_raw.get("content") or "").split("---", 2)[-1]
        _q_new  = f"---\n{yaml.safe_dump(_q_fm, sort_keys=False)}---{_q_body}"
        # Round-trip assert — REQUIRED, the same one ROLE-CPU Step 0.5 and Step 6 make. Proves no
        # value we just wrote (a `---`-leading reasoning string, an odd queue `value`) truncated
        # the frontmatter block. This PUT lands on ANOTHER team's queue: verify before you send.
        assert yaml.safe_load(_q_new.split("---")[1]) == _q_fm, "frontmatter round-trip failed"
        requests.put(f"{API}/workspaces/{_b['ws']}/files/queue.md",
            headers={**HEADERS, "If-Match": str(_q_raw.get("version", 0))},
            json={"content": _q_new})
        print(f"[REVIEW] {verdict} {_it.get('id')} (team {_b['team']}) -> review_status={_status}")
    except Exception as _re:
        print(f"[REVIEW] {_it.get('id')}: queue row NOT updated ({_re!r}). The comment is the "
              f"record — review_scan re-derives the status from it on the next pass.")

if OWES_REVIEW and not _rows:
    print("[REVIEW] backlog empty — posting nothing. Zero reviews discharges the obligation.")
```

Reviews posted here do NOT count against Part 4b's 3-comment engagement cap: that cap limits
optional commentary, this is an obligation with its own ceiling. Never skip the backlog because the
cap was already spent, and never spend the cap on padding instead of the backlog.

### Result sentinel — write `result_latest.json` after every evaluation

The champion artifact is `algo.py` (the candidate optimizer). There is no
`submission.csv`, no model training, and no fixed wall-clock deadline — a
candidate is scored by the remote eval (`eval_candidate.py`), which prints ONE
JSON line whose keys include `fitness` (= mean_rel_steps, lower is better) and
`is_valid` (0/1 energy-validity gate). The agent parses that JSON directly.
A candidate is scored on TWO splits: the train split first (`--split train`),
and — only if the train result is a provisional keep — the held-out test split
(`--split test`) on the SAME frozen file. Both measurements go in the sentinel.
Every eval command carries `JAX_ENABLE_X64=1`, an explicit `--split`, and
`--molecules-dir` — omitting the split or the molecules dir is a harness error,
not a measurement (all 250 molecules fail in ~2.6s).

**ISOLATION RULE (read this first):** Your working candidate lives in your own
agent-local workspace (`agents/{AGENT_NAME}/workspace/repo/algo.py`, plus a stamped
`algo_<expid>.py`). You MUST NOT write `champion/algo.py` outside a won promotion:
not while editing in Step 4, not speculatively, not on DISCARD / REJECTED_TEST /
FAILED, and not when you lose the champion race (that case is **NEAR_MISS**: both gates
passed, no promotion, so neither `champion.md` nor `champion/algo.py` is written). The ONE place the copy is allowed —
and where it is **REQUIRED** — is **ROLE-CPU Step 7b1**, after a KEEP that passed both
the train and the held-out test gate and whose `champion.md` PUT won the race. That
agent-side copy is the only mechanism that advances the local champion file; the
orchestrator does NOT do it for you. Writing early overwrites other agents' work;
skipping the Step 7b1 copy leaves `champion.md` and `champion/algo.py` disagreeing, and
every later experiment silently builds on a stale baseline.

After every evaluation, write a local result summary so the orchestrator can
find your best score AND so Part 0 Check C can tell whether a prior session's
result still needs narrating. Always point to the stamped agent-local path
(never `champion/`):

```python
import json, shutil
from pathlib import Path

agent_workspace = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

# Save a stamped copy of the candidate (isolation rule — champion/algo.py is written
# ONLY by ROLE-CPU Step 7b1, after a won promotion)
shutil.copy(agent_workspace / "algo.py", agent_workspace / f"algo_{exp_id}.py")

result_summary = {
    # Train score + path. Promotion also requires the held-out test gate below.
    "score": your_train_score_dict,  # the full parsed TRAIN eval JSON (Part 5 reads `error` /
                                     # `num_results` from it to tell INFRA failure from DISCARD)
    "fitness": your_fitness_value,   # from the TRAIN eval JSON (= mean_rel_steps, lower is better)
    "is_valid": your_is_valid_flag,  # from the TRAIN eval JSON (0/1 energy-validity gate)
    # Held-out TEST gate (ROLE-CPU Step 5). Same frozen candidate, `--split test`.
    # Only run on a provisional train keep; stays None/"not_run" otherwise.
    # THE VALUES BELOW ARE THE DEFAULTS you write right after the TRAIN eval.
    # `test_status` flips to "running" when you launch the `--split test` eval and
    # to "complete" ONLY after that eval returns and you fill in test_fitness /
    # test_is_valid (it is "failed" if that eval errored). Never write "complete"
    # with test_fitness=None: Part 5 would skip the test-gate re-run and turn a
    # genuinely promotable candidate into FAILED.
    "test_fitness": None,            # test-split fitness — filled by the `--split test` eval
    "test_is_valid": None,           # 0/1 on held-out molecules — same
    "test_status": "not_run",        # "not_run" | "running" | "complete" | "failed"
    "test_pass": None,               # True/False once the test gate has been decided
    "test_reason": None,             # test-invalid | insufficient-test-improvement | both
    "test_score": None,              # the full parsed test eval JSON
    "test_mean_rel_energy": None,    # held-out energy diagnostic; mirrors ROLE-CPU Step 7c, which
                                     # fills it from (test_score or {}).get("mean_rel_energy")
    "test_stdout_path": None,        # captures the test eval JSON line
    # EXPERIMENT AXIS direction from the queue item (increase / decrease / none).
    # NEVER the champion's optimisation sense ("minimize") — champion.md's `direction:`
    # is a different field that happens to share the name. Do not copy one into the other.
    "direction": (item.get("direction") if "item" in dir() and item else None),
    "exp_id": exp_id, "agent": AGENT_NAME,
    "algo_path": str(agent_workspace / f"algo_{exp_id}.py"),
    "remote_cand": remote_cand,   # the /tmp path the frozen candidate was scp'd to on the eval head;
                                  # Part 5 reuses it when it has to re-run the held-out test gate
    "description": description,   # one-line experiment description, reused verbatim in [RESULT]
    "launched_at": datetime.now(timezone.utc).isoformat(),
    # The final verdict, written once the outcome is decided (ROLE-CPU Step 7c / HEARTBEAT 5h).
    # One of the FIVE values: KEEP | NEAR_MISS | DISCARD | REJECTED_TEST | FAILED.
    "outcome": None,
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
# Do NOT copy to champion/ from here. The copy happens in exactly one place —
# ROLE-CPU Step 7b1, after a KEEP that passed the train + test gates and won the
# champion.md race — and there it is REQUIRED, not optional.
```

### YAML Frontmatter Parsing

The API does NOT auto-parse YAML. Always parse client-side. This is the canonical definition —
Part 0 Check B and Part 3.5a repeat it byte-for-byte, and every caller in this file (and in your
role section, if it does not define its own) relies on the two `or` guards below:

```python
def parse_frontmatter(resp):
    """Frontmatter dict, or {} — never raises, never returns None. `resp.get("content", "")` is
    NOT enough: a 404 comes back with the key PRESENT and set to None, so the default never
    fires and `None.split(...)` raises AttributeError. An empty frontmatter block makes
    `yaml.safe_load` return None. Both must resolve to {} — every caller treats this as a dict."""
    content = (resp or {}).get("content") or ""
    parts = content.split("---")
    return (yaml.safe_load(parts[1]) or {}) if len(parts) >= 3 else {}
```

**A `{}` from a failed read is not the same as an empty file.** It keeps one missing file from
killing a whole boot, but it also means a read-modify-PUT can no longer tell "this queue is empty"
from "this queue did not load". Never PUT a file back on the strength of a `{}` — check that the
row you meant to edit was actually found first (Part 1's review-backlog PUT does exactly that).

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
champion_code = champion_path.read_text() if champion_path.is_file() else None

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
  little new content. The system exits discussion mode once
  `DISCUSS_QUORUM` agents post `[DISCUSS-DONE]`.

This is a self-regulating termination signal. No orchestrator decides
when to stop discussing — the agents do, by simple majority of the
non-monitor roster (`DISCUSS_QUORUM`, computed in Part 0 Check A2 —
never a hardcoded count, which becomes a supermajority on a small
roster and deadlocks the round).

### 2b3. Analysts only: run the ROLE-ANALYST discussion-branch steps — REQUIRED

**If your role is `analyst`, this step is mandatory before you exit.** Several
ROLE-ANALYST steps are *written* under Part 4 but *apply on this branch* — they
are the discussion round's own machinery, and Part 4's "not this file" framing
does not exclude them. Open your ROLE-ANALYST section (Part 4-Role, below) and
execute, in this order:

1. **Step 0.2** — stagnation detection / `[DISCUSSION-TRIGGER]`.
2. **Step 0.2b** — search-class-diversity trigger.
3. **Step 0.7** — update the `knowledge/unqueued_axes.md` backlog ledger with the
   axes this round surfaced.
4. **Step 0.25** — team formation / reform. Run this **after** you have posted
   your own `[DISCUSS-MORE]` / `[DISCUSS-DONE]` comment in 2b2, so your vote is
   counted. It is gated on `MODE=discussion` and on you being the
   alphabetically-last analyst to have run this round; if you are not, it is a
   no-op and the last one handles it.

**Step 0.25 is the ONLY writer of `teams/roster.md`.** If no analyst executes it
on this branch, the roster is never written, and the run cannot leave cold start.
Nothing else in the system — not the monitor, not the orchestrator — will do it
for you. If you are an analyst and you skip this step, you have broken the run.

CPU-eval agents skip 2b3 entirely. (The monitor never reaches Part 2 at all — Part 0 Check A0 sends
it to Part 3.5.)

### 2c. Engagement rules

- Post at most **1 new thread** per round (avoid flooding)
- Comment on at most **5 existing threads** (substantive, not "I agree")
- **Length budget:** a thread is ≤ 400 words, a comment ≤ 120 words. Lead with the claim,
  then the evidence. No preamble, no restating the task, no summary of your own post.
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
      f"Waiting for an analyst to write teams/roster.md. No work performed.")
import sys; sys.exit(0)
```

**Forbidden in this branch:**
- Running ANY candidate evaluation
- Editing `champion/algo.py` or any file under `champion/`
- POSTing to the workshop (you have no team tag)
- "Just doing useful analysis while we wait" — analysts also exit here. Useful work requires a team context.

---

## Part 3.5: Branch — Monitor Health Pass (monitor role only)

You reached this branch from Part 0 Check A0 because `MY_ROLE == "monitor"`. This is the **only**
branch you ever take, and you take it on every invocation. `branch_taken` is already
`"monitor-health"`.

**Run the health pass ONCE, then exit.** The orchestrator invokes you once per cycle. Do not loop,
do not sleep, do not schedule yourself — a sleeping agent holds its slot for the whole rotation and
blocks the next invocation. Anything you did not get to this cycle, the next cycle checks.

### 3.5a. Boot

You skipped Check B, so nothing is loaded yet. Load credentials, IDs and the roster here.

```python
import json, os, requests, yaml
from pathlib import Path

AGENT_DIR = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}")
creds = json.load(open(AGENT_DIR / "credentials.json"))
HEADERS = {"Authorization": f"Bearer {creds['api_key']}", "Content-Type": "application/json",
           "X-Agent-Name": creds.get("agent_name", AGENT_NAME)}
API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
MAIN_WS_ID = open(f"{FOCUS_ROOT}/WORKSPACE_ID").read().strip()
WORKSHOP = open(f"{FOCUS_ROOT}/WORKSHOP_NAME").read().strip()

def parse_frontmatter(resp):
    """Frontmatter dict, or {} — never raises, never returns None. `resp.get("content", "")` is
    NOT enough: a 404 comes back with the key PRESENT and set to None, so the default never
    fires and `None.split(...)` raises AttributeError. An empty frontmatter block makes
    `yaml.safe_load` return None. Both must resolve to {} — every caller treats this as a dict."""
    content = (resp or {}).get("content") or ""
    parts = content.split("---")
    return (yaml.safe_load(parts[1]) or {}) if len(parts) >= 3 else {}

roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster_fm = parse_frontmatter(roster_raw) or {}       # whole frontmatter: `teams`, `phase`, …
roster = roster_fm.get("teams", {}) or {}             # team_name -> {workspace_id, members, …}

# You are not on a team and never will be. This is by design, not a coordination bug —
# do NOT report it, and do NOT add yourself to the roster.
MY_TEAM = TEAM_WS_ID = None
```

Both names are bound because your ROLE-MONITOR health-check sketch takes the roster **frontmatter**
(it indexes `roster["teams"]`): pass `roster_fm` if you call it as written, or iterate `roster`
directly. Then run Part 1 (Boot) for the remaining shared identity/time variables Part 6 needs.

### 3.5b. Cold start: no teams yet

```python
if not roster:
    print(f"[MONITOR] {AGENT_NAME}: roster has no teams yet (cold start). "
          f"Nothing to audit. Not forming teams — that is ROLE-ANALYST Step 0.25.")
    # -> go straight to Part 6 (6a -> 6d -> 6e). Post nothing.
```

An empty roster is the analysts' bootstrap in progress, not a fault to fix and not a reason for you
to enter discussion. There are no claims to sweep and no outcomes to count, so exit quietly.

### 3.5c. Run the health pass

With teams present, execute your ROLE-MONITOR protocol (Part 4-Role, below — for you that section
is the Monitor Agent Protocol). In short:

1. Count outcomes per team from the canonical ledger `logs/experiments.jsonl`, broken out by all
   five outcomes, with `FAILED` reported separately from the scientific ones.
2. Release stale claims by marking the per-item claim file `claims/{exp_id}.md` released — never
   by editing `queue.md`. The item never left `pending:`, so a release cannot lose an experiment;
   it just makes the item claimable again. Never touch any agent's `result_latest.json` sentinel
   (read-only is fine — that is how you tell a dead holder from a live one).
3. Check queue depth and the per-team falsification counters; post `[HYPOTHESIS-FALSIFIED]` when a
   team's counters trip it.
4. Post one `[AUDIT]` summarising cycle health. If you have nothing new since the last audit, post
   nothing.

`[AUDIT]` and `[HYPOTHESIS-FALSIFIED]` are the only posts you make. Tag them with the workshop, not
a team — you have no `MY_TEAM`.

### 3.5d. Exit

Go to Part 6. Run 6a (AGENT.md with `branch_taken = "monitor-health"`, `team: null`), then 6d and
6e. Skip 6b — your findings belong in the `[AUDIT]`, not in a `[SUGGESTION]` tagged to a team you
are not on. Skip 6c unless you genuinely learned something reusable across sessions.

**Forbidden in this branch** (unchanged from ROLE-MONITOR — this path grants no new powers):

- Claiming a queue item, running an experiment, or editing `algo.py` / anything under `champion/`
- Writing `queue.md` at all — not `pending:`, not `completed:`, not a legacy `claims:` map. Your
  only write during the sweep is the claim file `claims/{exp_id}.md`
- Writing `teams/roster.md`, forming or restructuring teams, or picking hypotheses
- Writing result files, or editing any team's `dead_ends.md` / `non_generalizable.md` /
  `strategy.md` — you flag problems in the `[AUDIT]`, you do not fix other agents' files
- Re-running any part of bootstrap (workshop, registration, subscriptions, workspaces, kickoff)
- Touching the shared eval pool: no Redis flush, no worker restart, no edits to remote checkouts
- Entering Part 2 (discussion), looping, or sleeping

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
# Comment on: [SUGGESTION], [PROPOSAL] from ANY team, [RESULT] cross-team if relevant
# Cap at 3 comments. Then move on.
```

**`[PROPOSAL]` is not scoped to your team.** Any non-author agent, on any team, may review any
proposal, and cross-team review is encouraged: the old "from your team" scoping left the sole team
analyst as the only eligible reviewer, so every item that analyst proposed could never be reviewed
and therefore never became claimable. To *review* rather than merely comment, lead the comment's
first line with `[REVIEW-OK]` or `[REVIEW-BLOCK]` and give your reasoning (Part 1, *Review
backlog*); anything else is ordinary commentary and leaves `review_status` untouched. You still may
not review your own proposal.

This 3-comment cap governs optional engagement only. The Part 1 review backlog is a separate
obligation with its own ceiling (`REVIEW_CAP`) — never skip it because you already spent the cap here.

### 4c. Self-triggered discussion (optional escape hatch)

If `keeps_in_last_30 == 0` (read recent results from main workspace), you may switch to Part 2 (Discussion Mode) for this cycle instead of running an experiment. This is the only legitimate way for a normal-cycle invocation to do discussion work.

**The window is 30, not 10, and the switch is capped.** With a `KEEP_MARGIN` of 1e-3 on top of a
held-out test gate, zero KEEPs across 10 experiments is an ordinary plateau, not a stall — firing the
opt-out there lets a whole rotation produce no experiments at all, which does not even advance the
stagnation streak. So:

- **Window:** the last **30** recorded experiments, not 10.
- **Cap: at most 2 of the 6 cpu-eval agents may take this branch per rotation.** Before switching,
  check whether two others already did — search this rotation's workshop posts for the
  `[DISCUSSION-SWITCH]` marker. If two are already present, run your experiment normally.
- If you do switch, post `[DISCUSSION-SWITCH] {AGENT_NAME} rotation={rotation}` (a one-line comment on
  the active discussion thread is enough) **before** doing discussion work, so the next agent can count it.
- **Never skip a claimable queue item because the cap was reached** — the cap means "run the
  experiment", not "idle".

### 4d. Execute your role

Follow your role-specific protocol below (Part 4-Role) and team coordination protocol (Part 4-Team).

### 4e. Mandatory API trail

Every experiment, proposal, or knowledge artifact you produce in this branch MUST be reflected in the AnonAPI API:
- **CPU-eval agents**: claim from queue → train eval → held-out test eval (only on a provisional train keep) → write `results/{exp_id}.md` to main workspace → release claim → POST `[RESULT]` to workshop. Only on a KEEP that passed both gates: PUT `champion.md` (train `metric_value` + `test_metric_value`) and propagate `champion/algo.py` per ROLE-CPU Step 7b1. On NEAR_MISS (both gates passed, promotion race lost) touch neither champion file and re-queue the change as `{exp_id}_stack`. On REJECTED_TEST, record it in the team's `non_generalizable.md`.
- **Analysts**: POST `[PROPOSAL]` to workshop → add the experiment to the team `queue.md` via **read-modify-PUT with If-Match** (NEVER PATCH — it flattens `pending:` across teams).

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

Finish a prior session's unposted result. Do NOT claim new work, do NOT edit `algo.py` — the candidate is frozen and must stay byte-identical across both splits. **If `MY_ROLE != "cpu"`, you should never have been routed here — skip Part 5 entirely and fall through to Part 6.** Only CPU-eval agents write `result_latest.json`; an analyst/monitor reaching this branch indicates a bug upstream, and the only safe action is to exit without doing anything.

**Where your item is, and what proves it is yours.** The claim never removed anything from the
queue: your item is still a row in `pending:`, and `pending:` remains the single source of truth for
what work exists. Exclusion lives in a separate one-item file, `claims/{exp_id}.md` in the TEAM
workspace, and the holder is decided by an immutable fact — `updatedBy` on **version 1** of that
file. So this branch does two things a resume must never skip: it re-verifies ownership from v1
**before** spending anything (5a2), and it finds its row in `pending:` to complete or re-queue it
(5e) — the ordinary move, because the row was never taken out.

This branch inlines ROLE-CPU Step 5 (KEEP decision + held-out test gate **+ the `results/{exp_id}.md` write**, 5f2), Step 6 (release the claim, complete the item — or RE-QUEUE it on FAILED), Step 7b (pre-PUT double-anchor gate + `champion.md` PUT), Step 7b1 (`champion/algo.py` propagation, REQUIRED once you win the race) and Step 8b (`post_id` backfill, in 5h). **A resume writes the result file like any other cycle.** It is not optional bookkeeping: `is_valid`, `delta` and `mean_rel_energy` are read from `results/{exp_id}.md` by the ledger and by every analyst prior, so skipping it nulls them for every resumed experiment — and a resumed KEEP would promote a champion with no result record at all. **The eval is deterministic: there is NO noise gate and NO second-seed confirmation.** Never re-run the train eval to "confirm" a delta — re-running the same file produces byte-identical scores. The one re-run this branch may perform is the held-out **test** eval of the already-frozen candidate, when the prior session died between the two evals.

Outcomes — FIVE values, same ladder as ROLE-CPU Steps 5/6/7:

| Outcome | Meaning | Team file written | Queue lands in |
|---|---|---|---|
| **KEEP** | train + test gates both passed AND the promotion race was won → `champion.md` + `champion/algo.py` written | none | `completed:` |
| **NEAR_MISS** | train + test gates both passed but the promotion **race was lost** — the 5d PRE-PUT gate aborted (an equal/better champion landed while we were evaluating) or the 5d RACE GUARD fired (`champion.md` no longer records our `exp_id`). The science succeeded, the scheduling lost. Champion files are NOT touched. | **none** — never `dead_ends.md`, never `non_generalizable.md`; this is POSITIVE evidence | `completed:` (outcome `NEAR_MISS`) **plus** a new `{exp_id}_stack` item in `pending:` |
| **DISCARD** | invalid on train, or not better than the champion by `KEEP_MARGIN` | `dead_ends.md` | `completed:` |
| **REJECTED_TEST** | train-better but invalid on the held-out test and/or not better there by `TEST_MARGIN` | `non_generalizable.md` (NEVER `dead_ends.md`) | `completed:` |
| **FAILED** | diff never applied, no usable metric, a train- or test-eval infra error, or the frozen candidate is gone — the proposal was never really tested | none | stays in `pending:` (re-queue bookkeeping + claim-file release in 5e), never a dead end |

**One FAILED case never reaches the table: `claim_lost`.** If 5a2 finds that v1 of the claim file
names someone else, you are not the holder — post no `[RESULT]`, write no team file, **write no
result file** (5f2 is a write like any other and is skipped with the rest), touch neither champion
file and make no queue edit. The sentinel is closed as `FAILED` / `last_failure: claim_lost` and
the cycle ends at Part 6. Everything else in Part 5 assumes 5a2 passed.

**NEAR_MISS is not a failure.** Analysts must read it as *supporting* evidence for its hypothesis: it
must not increment `refuted_discards` or `rejected_test`, must not count toward any dead-end rule,
and **does not count toward the stagnation streak** (by construction another candidate WAS promoted
in that rotation, so the run is demonstrably not stuck — counting it would fire a false regroup).

**The cycle you are resuming may itself BE a stacked re-test** (`{exp_id}_stack`, item carrying
`stacked_from`). Nothing changes about the ladder: a stack item is re-scored from scratch on BOTH
splits, exactly like any other experiment. Two consequences for this branch:

- **`prior_train_fitness` / `prior_test_fitness` on the item are CONTEXT, never a result and never a
  gate.** Every number Part 5 posts, compares, or writes to `champion.md` comes from THIS cycle's
  sentinel / eval JSON. Reusing a prior fitness here would report a measurement that was taken
  against a champion that has since moved. Part 5 never reads those keys, and must not start.
- **`source_algo` is for READING only.** The stacked change is re-applied to the CURRENT champion;
  copying `source_algo` over `algo.py` would revert the race winner. Part 5 does not edit `algo.py`
  at all, so this is automatic here — but see **ROLE-CPU Step 3b (Handling a stacked re-test)** for
  the rules that apply when a *fresh* cycle picks up a stack item, including the
  `FAILED` / `last_failure = "stack_conflict"` route for a genuine conflict.

```python
import json, yaml
from datetime import datetime, timezone
from pathlib import Path

# 5a. Rehydrate from sentinel (loaded in Part 0 Check C). If fitness is
# missing (agent died before Step 5 wrote it), re-parse the eval JSON from
# stdout_path so we still post a [RESULT] instead of losing the experiment.
# Worst case the parse fails → fitness stays None → 5b marks FAILED, the
# queue claim is released, and the proposal stays available for a fresh agent.
exp_id      = pending_result["exp_id"]
our_metric  = pending_result.get("fitness")        # TRAIN split
is_valid    = pending_result.get("is_valid")       # TRAIN split
test_fitness  = pending_result.get("test_fitness")     # held-out TEST split, or None
test_is_valid = pending_result.get("test_is_valid")    # held-out TEST split, or None
# `test_status` is the canonical key. Read it self-healingly: a sentinel written by
# an older or partially-completed path may be missing it entirely, in which case the
# presence of a test_fitness is the evidence the test eval actually ran.
test_status = pending_result.get("test_status") or (
    "complete" if pending_result.get("test_fitness") is not None else "not_run")
# The sentinel's `direction` is the EXPERIMENT AXIS direction (increase/decrease/none) from
# the queue item. It is NOT the champion's optimisation sense. Bind it under a distinct name
# so it can never be written into champion.md's `direction:` field, which is ALWAYS the
# literal "minimize" for this task.
axis_direction = pending_result.get("direction")
item        = pending_result.get("item") or {}
# If `item` carries `stacked_from`, this cycle IS a stacked re-test. Its `prior_train_fitness` /
# `prior_test_fitness` are CONTEXT ONLY — never read them as this cycle's measurement and never
# gate on them; `our_metric` / `test_fitness` above are the only numbers this branch may use.
# `source_algo` is likewise read-only (ROLE-CPU Step 3b); Part 5 never touches `algo.py`.
description = pending_result.get("description") or item.get("diff") or f"Resumed ({exp_id})"

# 5a2. OWNERSHIP RE-VERIFICATION — do this BEFORE anything expensive and before any write.
# Exclusion is the per-item claim file in the TEAM workspace, and the holder is `updatedBy` on
# VERSION 1 of that file. Nothing else decides it: `If-None-Match: "*"` is not enforced here, so a
# 2xx on the claim PUT proves nothing, and the file's CURRENT content proves nothing either
# (any agent can overwrite it). Version 1 is immutable — every agent that reads it, at any time,
# computes the SAME winner, which is why this needs no server CAS and no settle delay.
# Re-check it now: this sentinel may be hours old, and the monitor may have released the item.
#
# Claim paths. `claims/{exp_id}.md` is attempt 0, the path every first claim uses. A release does
# not delete the file (see 5e / ROLE-MONITOR): it marks it `released: true` and names the successor
# `claims/{exp_id}.r{n}.md`, so the NEXT claim is version 1 of a file nobody has written yet.
# Read the path you actually claimed out of the sentinel; fall back to attempt 0.
def claim_path_for(exp_id_, attempt=0):
    """attempt 0 = the base path; each release opens the next attempt."""
    return f"claims/{exp_id_}.md" if attempt == 0 else f"claims/{exp_id_}.r{attempt}.md"

def parse_claim(content):
    """Claim files are flat `key: value` lines — not frontmatter. Values stay strings."""
    fm = {}
    for line in (content or "").splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm

def claim_v1_holder(ws_id, path):
    """Holder of `path` = `updatedBy` on version 1. None if there is no readable history."""
    r = requests.get(f"{API}/workspaces/{ws_id}/files/{path}/history", headers=HEADERS)
    if r.status_code != 200:
        return None
    body = r.json()
    hist = body.get("history", body.get("data", [])) if isinstance(body, dict) else body
    for h in (hist or []):
        if int(h.get("version", 0) or 0) == 1:
            return h.get("updatedBy")
    return None

import re
claim_path    = pending_result.get("claim_path") or claim_path_for(exp_id)
_m_att        = re.match(r"^claims/.*\.r(\d+)\.md$", claim_path)
claim_attempt = int(_m_att.group(1)) if _m_att else 0   # 5e opens attempt+1 when it releases
claim_raw    = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/{claim_path}", headers=HEADERS)
claim_fm     = parse_claim((claim_raw.json() or {}).get("content")) if claim_raw.status_code == 200 else {}
claim_holder = claim_v1_holder(TEAM_WS_ID, claim_path)
claim_released = str(claim_fm.get("released", "")).lower() == "true"

resume_abort = (claim_holder != AGENT_NAME) or claim_released
if resume_abort:
    # We are not the holder any more (or never were): someone else's write is version 1, the file
    # is gone, or the monitor released it and the item is back in play. Post NOTHING and touch
    # NOTHING. A [RESULT] here would add a SECOND row for this exp_id — the duplicate-execution
    # signature the monitor audits for — and a queue edit would fight whoever holds it now. The
    # item is either being run by the current holder or sitting claimable in `pending:`; both are
    # self-healing, and nothing we do here improves either. The measurement is discarded on
    # purpose: a duplicated result is worse than a lost one.
    pending_result.update({
        "status": "posted",           # closes the sentinel so we do not re-enter Part 5 forever
        "posted_to_workshop": False,  # nothing was posted, and nothing should be
        "outcome": "FAILED", "last_failure": "claim_lost",
        "claim_path": claim_path, "claim_holder": claim_holder,
        "claim_released": claim_released,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    pending_path.write_text(json.dumps(pending_result, indent=2, default=str))
    print(f"[RESUME] CLAIM LOST: {claim_path} v1.updatedBy={claim_holder!r} "
          f"(released={claim_released}) != {AGENT_NAME} -> FAILED(claim_lost); "
          f"no [RESULT], no champion write, no queue edit.")
    # STOP HERE. Skip 5b-5h entirely and go to Part 6 (6a -> 6d -> 6e).

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

# 5b. TRAIN gate vs the CURRENT champion (it may have moved while we were gone).
# EVERYTHING FROM HERE TO 5h ASSUMES 5a2 PASSED. If `resume_abort` is set you are not the
# holder: run none of it — no gate, no test eval, no queue write, no post — and go to Part 6.
# This MIRRORS ROLE-CPU Step 5 — same margins, same seeding rule, same validity
# convention. Do NOT invent a different rule here.
#   * `diff_applied: false` in the sentinel (Step 4's edit didn't land — Edit reported
#     old_string not found, patch -p1 rejected hunks, algo.py identical to champion)
#     ⇒ the recorded metric is the UNCHANGED baseline, not evidence. FAILED.
#   * `fitness` still None after the 5a salvage ⇒ no usable measurement at all. FAILED.
#     (Never fall through to a comparison with None — that either raises TypeError or
#     silently DISCARDs every resumed result.)
#   * TRAIN eval was an INFRA failure (sentinel `status: "failed"`, or a score dict carrying
#     `error` / `num_results == 0`) ⇒ FAILED, never DISCARD. eval_candidate.py turns any
#     harness/Redis exception into {"fitness": 1000.0, "is_valid": 0, "error": ...}. A 1000.0
#     now narrows to infra error / non-convergence / over-budget (energy-gate failures carry
#     honest fitness), but the last two are real science — still check `error` / `num_results`
#     to tell them apart. Filing an infra error as DISCARD burns a mechanism family in
#     dead_ends.md that was never tested (ROLE-CPU Step 5).
KEEP_MARGIN = 1e-4   # train margin, mean_rel_steps units (ROLE-CPU Step 5) — matches TEST_MARGIN
TEST_MARGIN = 1e-4   # held-out test margin (upstream `significant_change`)

# Why this cycle failed, for the Step-6-style re-queue in 5e. Bound on every path.
resume_failure = None

diff_applied = bool(pending_result.get("diff_applied", item.get("diff_applied", True)))
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)
metric_name = champ.get("metric_name", "fitness")

# Is there a VALID champion yet? `status: awaiting_baseline`, a missing/1000.0 metric_value,
# or a recorded `is_valid: 0` ⇒ no champion, and the first fully-valid candidate seeds it.
champ_status  = champ.get("status")
champ_fitness = champ.get("metric_value")
champ_test    = champ.get("test_metric_value")   # held-out anchor, advances with metric_value
# The `is_valid` term is REQUIRED: `fitness < 1000.0` no longer implies validity, because a run
# that failed only the energy gate now records its honest (often very good) fitness. Absent
# `is_valid` ⇒ valid, since champion.md is only ever written on an accepted KEEP.
_champ_valid_raw = champ.get("is_valid", 1)
champ_is_valid = (int(_champ_valid_raw) == 1) if _champ_valid_raw is not None else False
have_valid_champion = (champ_status != "awaiting_baseline") and \
                      (champ_fitness is not None) and (float(champ_fitness) < 1000.0) and \
                      champ_is_valid
current_best      = float(champ_fitness) if have_valid_champion else None
current_best_test = float(champ_test) if (have_valid_champion and champ_test is not None) else None

# An ABSENT is_valid means INVALID, never valid (ROLE-CPU Step 5 convention): a run whose
# validity flag we cannot read has not passed the energy gate and can never be champion.
valid_run = (is_valid is not None) and (int(is_valid) == 1)

# INFRA failure ≠ scientific rejection (ROLE-CPU Step 5).
_score = pending_result.get("score") or {}
train_infra_failed = (
    pending_result.get("status") == "failed"
    or bool(_score.get("error"))
    or (bool(_score) and int(_score.get("num_results", 0) or 0) == 0)
)

if our_metric is None or not diff_applied:
    outcome = "FAILED"
    resume_failure = "diff_not_applied" if not diff_applied else "no_usable_metric"
elif train_infra_failed:
    outcome = "FAILED"            # harness/Redis error — re-queue, do NOT record as a dead end
    resume_failure = "train_eval_infra"
elif not valid_run:
    outcome = "DISCARD"
elif not have_valid_champion:
    outcome = "KEEP"                                  # SEEDING: first valid candidate
elif (current_best - our_metric) >= KEEP_MARGIN:      # minimize task; ties are DISCARD
    outcome = "KEEP"
else:
    outcome = "DISCARD"

# 5c. HELD-OUT TEST GATE — required for every promotion (ROLE-CPU Step 5).
# A train KEEP is only PROVISIONAL. Promotion additionally requires the SAME frozen
# candidate to be (b) better on the held-out test split by more than TEST_MARGIN and
# (c) still valid there. Never spend a test eval on a DISCARD/FAILED — it is a real
# eval on a SHARED pool — real compute, used by other clients too. 5a2 already proved we
# still hold `claims/{exp_id}.md`; that check exists precisely so this block never spends
# pool time on an item somebody else owns.
# Eval-head connection parameters — these are PINNED and must stay byte-identical to
# ROLE-CPU Step 4 ("Eval-head connection parameters"). If either place changes, BOTH must be
# updated together. Do not invent host/port/checkout values. Bound unconditionally (not inside
# the test-gate branch) because the champion.md Reproduction block in 5d also interpolates
# EVAL_MOLECULES_DIR. The pool is SHARED: never flush Redis, never restart or kill workers.
EVAL_HOST           = "cpu-33"
EVAL_REDIS_HOST     = "localhost"
EVAL_REDIS_PORT     = 6385
SELLA_CHECKOUT      = "/home/tsypin/opt_problem_as_testgate_opus5"
EVAL_PYTHON         = "/home/tsypin/miniconda3/envs/gigaopt/bin/python"
EVAL_MOLECULES_DIR  = "/home/tsypin/as_testgate_molecules"

if outcome == "KEEP" and test_status != "complete":
    # Prior session died between the two evals → run the test gate here, on the
    # already-frozen candidate. Do NOT re-edit or re-copy algo.py from the repo dir.
    import subprocess
    ws          = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
    # NEVER write `Path(x or "")`: Path("") normalises to PosixPath('.') and `.exists()` on
    # that is TRUE, so a sentinel missing `algo_path` would sail past the guard below and reach
    # `scp . cpu-33:/tmp/...` — scp'ing a DIRECTORY without -r, which raises CalledProcessError
    # mid-branch: no [RESULT] posted, claim never released, item never re-queued. Treat a
    # missing/empty algo_path as missing BEFORE constructing the Path, and gate on `.is_file()`
    # (not `.exists()`) so a directory can never masquerade as the frozen candidate.
    _algo_path  = pending_result.get("algo_path")
    stamped     = Path(_algo_path) if _algo_path else None
    remote_cand = pending_result.get("remote_cand") or f"/tmp/cand_{AGENT_NAME}_{exp_id}.py"
    if stamped is None or not stamped.is_file():
        # The exact frozen candidate is gone — we can never reproduce what was measured.
        outcome, test_status = "FAILED", "failed"
        resume_failure = "candidate_missing"
        print(f"[RESUME] stamped candidate missing (algo_path={_algo_path!r}); "
              f"cannot run test gate → FAILED")
    else:
        t_out = ws / f"eval_{exp_id}.test.stdout"
        # Re-scp the STAMPED candidate this session already froze — the byte-identical file the
        # train eval scored. Even on a stacked re-test, never substitute the item's `source_algo`
        # (that is READ-only context from the near-missed cycle; see ROLE-CPU Step 3b).
        subprocess.run(["scp", str(stamped), f"{EVAL_HOST}:{remote_cand}"], check=True, timeout=120)
        # --molecules-dir is MANDATORY: the client bakes ABSOLUTE xyz paths into every
        # Redis task and the worker opens that path on ITS OWN filesystem. Omit it and
        # all 250 molecules fail in seconds with "No such file or directory".
        t_cmd = (f"cd {SELLA_CHECKOUT} && JAX_ENABLE_X64=1 {EVAL_PYTHON} eval_candidate.py "
                 f"--program {remote_cand} --split test "
                 f"--molecules-dir {EVAL_MOLECULES_DIR} "
                 f"--redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}")
        t_res = subprocess.run(["ssh", EVAL_HOST, t_cmd], capture_output=True, text=True, timeout=3600)
        t_out.write_text(t_res.stdout)
        test_score = None
        for line in reversed(t_res.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    test_score = json.loads(line); break
                except json.JSONDecodeError:
                    continue
        if (t_res.returncode != 0 or test_score is None
                or bool((test_score or {}).get("error"))
                or int((test_score or {}).get("num_results", 0) or 0) == 0):
            outcome, test_status = "FAILED", "failed"   # infra error, not a verdict on the idea
            resume_failure = "test_eval_infra"
        else:
            test_fitness  = float(test_score["fitness"])
            test_is_valid = int(test_score.get("is_valid", 0))
            test_status   = "complete"
        # Crash-safe merge, written BEFORE the gate below is evaluated: if this session dies
        # here, the next resume must find the same sentinel SHAPE ROLE-CPU Step 4c writes.
        # `test_pass` / `test_reason` are part of that contract, so write them explicitly —
        # None means "the test eval ran, the gate has not been decided yet". 5h overwrites
        # both with the decided values.
        pending_result.update({"test_fitness": test_fitness, "test_is_valid": test_is_valid,
                               "test_status": test_status, "test_score": test_score,
                               "test_mean_rel_energy": (test_score or {}).get("mean_rel_energy"),
                               "test_stdout_path": str(t_out),
                               "test_pass": None, "test_reason": None})
        pending_path.write_text(json.dumps(pending_result, indent=2, default=str))

test_reject_reason = None   # human prose, for the [RESULT] post only
test_reason        = None   # ENUM, for non_generalizable.md: test-invalid |
                            # insufficient-test-improvement | both  (same as ROLE-CPU)
if outcome == "KEEP":
    if test_status != "complete" or test_fitness is None:
        outcome = "FAILED"        # no usable held-out measurement ⇒ never promote
        resume_failure = resume_failure or "test_eval_infra"
    else:
        test_valid = (test_is_valid is not None) and (int(test_is_valid) == 1)
        if current_best_test is None:
            test_improved = True  # no held-out anchor yet (seeding, or champion predates the
                                  # test gate) — validity alone gates, and this promotion sets it
        else:
            test_improved = (current_best_test - test_fitness) > TEST_MARGIN  # ties are REJECTS
        if not (test_valid and test_improved):
            reasons = []
            if not test_valid:
                reasons.append("held-out test run INVALID (is_valid != 1)")
            if not test_improved:
                reasons.append(f"insufficient test improvement "
                               f"({current_best_test} → {test_fitness}, "
                               f"gain {current_best_test - test_fitness:+.6f} <= TEST_MARGIN {TEST_MARGIN})")
            test_reject_reason = " AND ".join(reasons)
            test_reason = ("both" if (not test_valid and not test_improved)
                           else "test-invalid" if not test_valid
                           else "insufficient-test-improvement")
            outcome = "REJECTED_TEST"   # trained-set-only win: mechanism family does not generalize

# Signed deltas vs the champion (negative == better on this minimize task).
delta      = (our_metric - current_best) if (our_metric is not None and current_best is not None) else 0.0
test_delta = (test_fitness - current_best_test) if (test_fitness is not None and current_best_test is not None) else 0.0

# 5d. PROMOTE (KEEP only) — champion.md PUT per ROLE-CPU Step 7b, then the REQUIRED
#     champion/algo.py propagation per ROLE-CPU Step 7b1. Both anchors (train
#     `metric_value` and held-out `test_metric_value`) advance together, only here.
#     Run this BEFORE releasing the claim so the queue records the final outcome.
#     A pre-PUT abort or a lost race is a SCHEDULING outcome, not a scientific one: the outcome
#     becomes NEAR_MISS (both gates PASSED, no promotion happened), nothing is written to
#     dead_ends.md or non_generalizable.md, and the change is re-queued as `{exp_id}_stack`
#     to be re-tested against the NEW champion. Never demote it to DISCARD — and do not leave
#     it as KEEP either: KEEP claims a promotion that did not happen.
stack_requeue = None   # set to "{exp_id}_stack" when we abort the PUT or lose the race
if outcome == "KEEP":
    import shutil
    # Same missing-candidate guard 5c applies, repeated for the path where the sentinel ALREADY
    # had test_status == "complete" (5c's block was skipped, so nothing validated the frozen file
    # this session). Promotion is champion.md PUT *plus* the REQUIRED 7b1 algo.py copy; if the
    # frozen file is gone we cannot do the copy, and a PUT we cannot back with an algo.py leaves
    # the two permanently disagreeing. Check BEFORE the PUT, never after.
    _promo_src = pending_result.get("algo_path")
    if not (_promo_src and Path(_promo_src).is_file()):
        outcome = "FAILED"
        resume_failure = resume_failure or "candidate_missing"
        print(f"[RESUME] cannot promote: frozen candidate missing "
              f"(algo_path={_promo_src!r}) → FAILED, nothing written to champion/")

if outcome == "KEEP":
    # PRE-PUT GATE (REQUIRED — ROLE-CPU Step 7b): If-Match is not reliably enforced server-side,
    # so a stale PUT can silently clobber a better champion that landed while we were gone.
    # Check BOTH anchors: re-checking the TEST anchor closes the race where we cleared gate (b)
    # in 5c against a `champ_test` that another agent superseded while our test eval ran. A
    # resume that is better on train but worse on test must NEVER overwrite the test anchor —
    # nothing is promoted in this run unless it generalizes.
    _pre_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
    _pre = parse_frontmatter(_pre_raw)
    _pre_best = _pre.get("metric_value")       # freshest TRAIN anchor
    _pre_test = _pre.get("test_metric_value")  # freshest TEST anchor
    _clobber_train = (_pre_best is not None) and (float(our_metric) >= float(_pre_best))
    _clobber_test  = (_pre_test is not None and test_fitness is not None
                      and float(test_fitness) >= float(_pre_test))
    if _clobber_train or _clobber_test:
        outcome = "NEAR_MISS"          # both gates passed; the promotion race was lost
        stack_requeue = f"{exp_id}_stack"
        print(f"PRE-PUT ABORT ({'train' if _clobber_train else ''}"
              f"{'+' if (_clobber_train and _clobber_test) else ''}"
              f"{'test' if _clobber_test else ''}): champion.md now {_pre.get('experiment_id')} "
              f"(train {_pre_best}, test {_pre_test}) vs ours (train {our_metric}, "
              f"test {test_fitness}); do NOT overwrite and do NOT copy algo.py. "
              f"Outcome -> NEAR_MISS; re-queue as {stack_requeue}.")
    else:
        # Carry `run_id` and `settings` forward from the champion we are replacing — ROLE-CPU
        # Step 7b reads both back on every later promotion, so dropping them here makes them
        # permanently None/{} for the rest of the run. `direction` is ALWAYS the literal
        # "minimize" (the metric's optimisation sense) — never `axis_direction`.
        _run_id   = _pre.get("run_id") or champ.get("run_id")
        _settings = json.dumps(item.get("settings") or _pre.get("settings")
                               or champ.get("settings") or {}, sort_keys=True)
        _now_ts   = datetime.now(timezone.utc).isoformat()
        champion_content = f"""---
metric_name: {metric_name}
metric_value: {our_metric}
test_metric_value: {test_fitness}
direction: minimize
is_valid: {int(is_valid)}
test_is_valid: {int(test_is_valid)}
experiment_id: {exp_id}
agent: {AGENT_NAME}
run_id: {_run_id}
updated_at: {_now_ts}
settings: {_settings}
---

# Champion: {exp_id}

## Experiment Description

{description}

## Result

- **Train:** {metric_name} = {our_metric} (delta {delta:+.6f}, is_valid={int(is_valid)})
- **Held-out test:** {metric_name} = {test_fitness} (delta {test_delta:+.6f}, is_valid={int(test_is_valid)})
- Resumed from a prior session: true

## Reproduction

Same frozen `champion/algo.py` on both splits; `--split` and `--molecules-dir` are BOTH
mandatory (omitting the molecules dir fails 250/250 molecules in ~2.6s):

`JAX_ENABLE_X64=1 python eval_candidate.py --program <remote algo.py> --split train --molecules-dir {EVAL_MOLECULES_DIR} --redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}`
`JAX_ENABLE_X64=1 python eval_candidate.py --program <remote algo.py> --split test  --molecules-dir {EVAL_MOLECULES_DIR} --redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}`

Expected: train {metric_name} = {our_metric}, test {metric_name} = {test_fitness}
(deterministic — exact match every run).
"""
        requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
            headers={**HEADERS, "If-Match": str(_pre_raw.get("version", 0))},
            json={"content": champion_content})

        # RACE GUARD (ROLE-CPU Step 7b1): copy algo.py ONLY if champion.md records OUR exp_id.
        _now = parse_frontmatter(requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                                              headers=HEADERS).json())
        if _now.get("experiment_id") != exp_id:
            outcome = "NEAR_MISS"      # both gates passed; another agent's champion won
            stack_requeue = f"{exp_id}_stack"
            print(f"CHAMPION RACE LOST -> do NOT copy algo.py "
                  f"(champion.md now {_now.get('experiment_id')}). Outcome -> NEAR_MISS; "
                  f"re-queue as {stack_requeue} to re-test on the new champion.")
        else:
            # We won: propagating champion/algo.py is REQUIRED, not optional. Leaving it stale
            # makes every later experiment build on the wrong baseline.
            src = Path(_promo_src)                      # validated at the top of 5d
            dst = Path(f"{FOCUS_ROOT}/champion/algo.py")
            tmp = dst.with_suffix(".py.tmp")
            shutil.copy(src, tmp); tmp.replace(dst)          # atomic on POSIX
            with open(f"{FOCUS_ROOT}/champion/SOURCE", "a") as f:
                f.write(f"{exp_id} {our_metric:.6f} {AGENT_NAME} "
                        f"{datetime.now(timezone.utc).isoformat()}\n")

# 5e. Release claim; COMPLETE or RE-QUEUE the item (same ladder as ROLE-CPU.md Step 6).
#
# YOUR ITEM IS IN `pending:`. The claim never removed it — `pending:` is the single source of
# truth for what work exists, and `claims/{exp_id}.md` is the only thing that ever said "mine".
# So the loop below finds the row by id and moves it the ordinary way; if it is NOT there, that
# is a finding (see the `item_moved` check after the loop), not something to paper over by
# fabricating a row.
#
# The claim is always dropped. What differs is where the item lands:
#   * KEEP / NEAR_MISS / DISCARD / REJECTED_TEST — a real experiment produced evidence →
#     `completed:`, with `outcome:` recording which of the five it was.
#   * FAILED — tested NOTHING (unapplied diff, no usable metric, missing frozen candidate,
#     train- or test-eval infra error). The item is RE-QUEUED: drop the claim but leave it in
#     `pending:` with requeue bookkeeping so the next cpu-eval agent runs it. The FAILED cases
#     reachable here are the most expensive ones — a candidate that cleared the train margin
#     and then lost the test eval to an ssh error. A FAILED item is never `completed:`, never a
#     dead end, never a stagnation tick.
# NEAR_MISS does BOTH: the original item completes with `outcome: NEAR_MISS`, AND the same change
# is ADDITIONALLY queued as a NEW `{exp_id}_stack` item in `pending:` so it is re-tested against
# the champion that beat us. The stack item is unclaimed, is never created twice, and is never
# created from an item that is itself a stack (no stack-of-a-stack — an endless re-test chain).
# Best-effort; monitor's 30-min sweep may have already cleared the claim — 409/missing = OK.
# Bound BEFORE the try so 5g/5h never NameError when the queue read/PUT raises.
stack_queued      = False   # True once the `{exp_id}_stack` item is actually in `pending:`
stack_skip_reason = None    # "already queued" | "stack-of-a-stack" when the guards suppressed it
try:
    q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json()
    q_fm  = parse_frontmatter(q_raw)
    # Legacy bookkeeping only: a `claims:` map in queue.md excludes NOTHING under this design
    # (the claim FILE does). Drop your own key if it is there and never read it as ownership.
    claim_removed = q_fm.get("claims", {}).pop(AGENT_NAME, None) is not None
    pending   = q_fm.get("pending", []) or []
    completed = q_fm.get("completed", []) or []
    remaining = []
    item_moved = False
    for it in pending:
        if it.get("id") == exp_id:
            it = dict(it)
            if outcome == "FAILED":
                # RE-QUEUE (ROLE-CPU Step 6). The row never LEFT `pending:`, so this is
                # bookkeeping only — what actually hands the item back is the claim-file
                # RELEASE below. Skip that release and the item sits in `pending:` forever
                # behind a claim file whose v1 names US: every other agent computes "lost"
                # and walks past it, and the experiment is silently stranded.
                it.pop("claimed_by", None)
                it["requeued_at"]   = datetime.now(timezone.utc).isoformat()
                it["requeued_by"]   = AGENT_NAME
                it["requeue_count"] = int(it.get("requeue_count", 0) or 0) + 1
                it["last_failure"]  = resume_failure or "resume_no_usable_metric"
                remaining.append(it)
            else:
                it["completed_at"] = datetime.now(timezone.utc).isoformat()
                it["completed_by"] = AGENT_NAME
                it["outcome"]      = outcome   # KEEP / NEAR_MISS / DISCARD / REJECTED_TEST
                it["fitness"]      = our_metric
                it["test_fitness"] = test_fitness
                # The ledger reads `is_valid` / `delta` / `mean_rel_energy` from
                # `results/{exp_id}.md` and falls back to THIS row when that read misses.
                # Write them here too (same names ROLE-CPU Step 6 uses) so the fallback has
                # something to find instead of silently nulling three fields.
                it["is_valid"]        = int(is_valid) if is_valid is not None else None
                it["delta"]           = round(delta, 6)
                it["mean_rel_energy"] = _score.get("mean_rel_energy") if _score else None
                it["resumed"]      = True
                completed.append(it)
            item_moved = True
        else:
            remaining.append(it)
    if not item_moved:
        # The row is missing from `pending:` — and nothing in this design removes it, so
        # something out of contract did. Look in `completed:` before assuming: a completed row
        # we did not write means this exp_id was run twice (the monitor's duplicate-exp_id
        # assertion is the run-global form of this check). Either way do NOT re-create the row.
        _done = next((x for x in completed if (x or {}).get("id") == exp_id), None)
        print(f"[RESUME] {exp_id} not found in pending:; completed_by="
              f"{(_done or {}).get('completed_by', 'MISSING')} — writing no queue row for it "
              f"(possible duplicate execution or an out-of-contract queue edit).")
    if stack_requeue:
        # NEAR_MISS re-test — a SCHEDULING retry, not a new proposal: same change, new id,
        # unclaimed. Three guards, same as ROLE-CPU:
        #   * no duplicate      — the id may already exist from an earlier resume of this
        #                         same sentinel (5e is re-entrant; a 409 on the queue PUT
        #                         makes the next session redo this block).
        #   * no stack-of-stack — a `{...}_stack` item that near-misses again must NOT spawn
        #                         `{...}_stack_stack`; the chain ends after one re-test.
        #   * unclaimed         — strip every claim/requeue field so any agent can take it.
        _base    = pending_result.get("item") or {}
        _already = any((x or {}).get("id") == stack_requeue for x in (remaining + completed))
        _is_stack = str(exp_id).endswith("_stack") or bool(_base.get("stacked_from"))
        if _already or _is_stack:
            # Outcome STAYS NEAR_MISS — the guards only suppress the duplicate/endless queue
            # item, they never change the verdict. `stack_queued` keeps 5g/5h honest about
            # whether a new item actually exists.
            stack_skip_reason = "already queued" if _already else "stack-of-a-stack"
            print(f"[RESUME] stack re-queue skipped ({stack_skip_reason}): {stack_requeue}")
        else:
            _stack = {k: v for k, v in _base.items()
                      if k not in ("claimed_by", "claimed_at", "requeued_at", "requeued_by",
                                   "requeue_count", "last_failure", "completed_at",
                                   "completed_by", "outcome", "fitness", "test_fitness",
                                   "val_score", "test_score", "resumed")}
            _stack.update({
                "id":          stack_requeue,
                "axis":        _base.get("axis") or item.get("axis"),
                # EXPERIMENT axis direction (increase/decrease/none) — never "minimize".
                "direction":   _base.get("direction") or axis_direction,
                "value":       _base.get("value") if _base.get("value") is not None else item.get("value"),
                "description": _base.get("description") or description,
                "stacked_from": exp_id,
                "stack_reason": "near_miss_promotion_race_lost",
                # It inherits the parent's `proposal_post` TOO, and that is not decoration:
                # `review_status` is only ever verifiable against the comments on a real post,
                # so a row claiming `ok` while naming none is unverifiable and NO agent may
                # write it (`item_review_status` in Part 1 resolves such a row `pending`
                # regardless). The parent necessarily has one — without it the parent could
                # never have been reviewed, claimed and run — but write the conditional anyway
                # so this line can never become the one place that stamps an unverifiable `ok`.
                "proposal_post": _base.get("proposal_post") or item.get("proposal_post"),
                # Inherits the parent's clearance EXPLICITLY — never by omission. The mechanism
                # was reviewed once as a proposal and has since passed BOTH gates on real
                # numbers; re-gating it as `pending` would idle the next agent on a change the
                # system has already vetted twice, and the review gate exists to vet UNTESTED
                # mechanisms, which this is not. Written here because the legacy resolvers
                # default an absent status to `pending`: a stack row that says nothing would be
                # unclaimable forever. Same expression as ROLE-CPU Step 7's `requeue_stack` —
                # if one changes, change both.
                "review_status": ("ok" if (_base.get("proposal_post")
                                           or item.get("proposal_post")) else "pending"),
                # Where the already-validated candidate lives, so the next agent re-applies the
                # SAME change (rather than re-deriving it) on top of the new champion.
                "source_algo": pending_result.get("algo_path"),
                "diff_path":   _base.get("diff_path") or pending_result.get("diff_path"),
                "queued_by":   AGENT_NAME,
                "queued_at":   datetime.now(timezone.utc).isoformat(),
            })
            remaining.append(_stack)
            stack_queued = True
            item_moved = True
    q_fm["pending"]   = remaining
    q_fm["completed"] = completed
    if claim_removed or item_moved:
        body = q_raw.get("content", "").split("---", 2)[-1]
        requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
            headers={**HEADERS, "If-Match": str(q_raw.get("version", 0))},
            json={"content": f"---\n{yaml.safe_dump(q_fm, sort_keys=False)}---{body}"})

    # RELEASE THE CLAIM FILE — REQUIRED on FAILED, and ONLY on FAILED. Do it AFTER the queue
    # PUT, so the re-queue bookkeeping is already in place when the item becomes claimable.
    # Releasing does not delete: it overwrites the claim file with a `released: true` marker
    # naming the successor path, so the next claim is VERSION 1 of a file nobody has written —
    # the only release that keeps v1 arbitration meaningful. (Deleting would be simpler if it
    # worked: file DELETE is unverified on this server, and if it tombstoned the file while
    # KEEPING its history the next PUT would land as vN+1 with v1 still naming us — the item
    # would be permanently unclaimable. ROLE-MONITOR uses this same marker, byte for byte.)
    # Terminal outcomes (KEEP / NEAR_MISS / DISCARD / REJECTED_TEST) leave their claim file
    # alone: the item is in `completed:`, nobody can claim it, and the file is the record of
    # who ran it. A `{exp_id}_stack` item needs no release either — a new id is a new path.
    if outcome == "FAILED":
        _next = claim_path_for(exp_id, claim_attempt + 1)
        requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/{claim_path}", headers=HEADERS,
                     json={"content": (f"holder: {AGENT_NAME}\n"
                                       f"claimed_at: {claim_fm.get('claimed_at', '')}\n"
                                       f"released: true\n"
                                       f"released_at: {datetime.now(timezone.utc).isoformat()}\n"
                                       f"released_by: {AGENT_NAME}\n"
                                       f"release_reason: failed_requeue\n"
                                       f"next_claim_path: {_next}\n")})
        print(f"[RESUME] released {claim_path}; next claim goes to {_next} "
              f"(the item itself never left pending:).")
except Exception as e:
    print(f"[RESUME] claim release skipped: {e!r}")

# 5f. Record the negative result so nobody re-runs it.
#     ONLY these two outcomes write a team file (ROLE-CPU Step 7c):
#       * DISCARD        → dead_ends.md
#       * REJECTED_TEST  → non_generalizable.md — a real train win that failed to transfer;
#                          analysts must read that file before proposing.
#     KEEP, NEAR_MISS and FAILED write NOTHING — not dead_ends.md, not non_generalizable.md.
#     NEAR_MISS passed BOTH gates: it is POSITIVE evidence that lost only the promotion race, so
#     filing it anywhere negative would teach analysts to abandon a mechanism that WORKS (and
#     would wrongly feed refuted_discards / rejected_test counters). A FAILED tested nothing at
#     all. Create the file if absent. Keep entries structured and short — one record, no essay.
try:
    fname = "non_generalizable.md" if outcome == "REJECTED_TEST" else \
            ("dead_ends.md" if outcome == "DISCARD" else None)
    if fname:
        raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/{fname}", headers=HEADERS).json()
        head = "# Non-Generalizable\n\n" if fname == "non_generalizable.md" else "# Dead Ends\n\n"
        # Same field names ROLE-CPU Step 7c writes — analysts parse both files with one
        # reader. `reason` is the ENUM on REJECTED_TEST and the experiment description
        # on DISCARD. Do NOT rename it to reject_reason.
        entry = (f"\n- exp_id: {exp_id}\n"
                 f"  axis: {item.get('axis') or 'UNKNOWN'}\n"
                 f"  direction: {item.get('direction') or axis_direction or 'UNKNOWN'}\n"
                 f"  value: {item.get('value')}\n"
                 f"  train_fitness: {our_metric}\n"
                 f"  delta: {delta:+.6f}\n"
                 f"  test_fitness: {test_fitness}\n"
                 f"  test_is_valid: {test_is_valid}\n"
                 f"  reason: {test_reason if outcome == 'REJECTED_TEST' else description[:160].replace(chr(10), ' ')}\n"
                 # FAMILY IS THE AXIS, exactly — the same string recorded two lines above. Never
                 # rebuild it from the id by joining the first two underscore-separated segments
                 # of `exp_id`: under the `exp_<team-prefix>_<mechanism>` schema those segments
                 # ARE the team, so one dead end closed an entire team's queue. That expression
                 # is described here rather than written out because it must not survive
                 # anywhere in the template tree, not even as a quoted example. A
                 # `{exp_id}_stack` item carries its parent's axis (5e copies it), so reading
                 # `item["axis"]` is right for stacks too.
                 f"  family: {item.get('axis') or 'UNKNOWN'}\n"
                 f"  resumed: true\n"
                 f"  date: {datetime.now(timezone.utc).date()}\n")
        # `raw.get("content", head)` is WRONG here: a 404 comes back with the key
        # present and set to None, and None + entry raises TypeError — silently
        # swallowed by the except below, dropping the record entirely.
        requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/{fname}",
                     headers={**HEADERS, "If-Match": str(raw.get("version", 0))},
                     json={"content": (raw.get("content") or head) + entry})
except Exception as e:
    print(f"[RESUME] negative-result record skipped: {e!r}")

# 5f2. WRITE `results/{exp_id}.md` — REQUIRED, exactly as ROLE-CPU Step 5 does it.
#      A resume is a full cycle, not a post-only shortcut. The ledger, the analysts' empirical
#      priors and the operator's monitoring all parse THIS file's frontmatter for `is_valid`,
#      `delta` and `mean_rel_energy`; a branch that posts [RESULT] and writes no result file
#      nulls all three for every resumed experiment, and a resumed KEEP promotes a champion
#      with no result record behind it. Runs for EVERY outcome, KEEP through FAILED — a FAILED
#      result file is how the next agent learns the item was already attempted and why.
#      Field names, `yaml_scalar` / `md_field` and the duplicate-execution rule are copied from
#      ROLE-CPU Step 5 verbatim: if either side changes, change BOTH or the two shapes drift and
#      half the ledger rows stop parsing.
#      KNOWN WRINKLE of that shared guard, on the FAILED path only: 5e re-queues a FAILED item
#      under the SAME exp_id, so the agent that finally measures it finds THIS file, sees a
#      different `agent:`, and — by the duplicate-execution rule — appends its real result
#      instead of writing the frontmatter the ledger reads. Do NOT patch it by skipping the
#      write here: that would make the two files disagree about when a result file exists. The
#      guard is one rule shared with ROLE-CPU Step 5 and has to be fixed in both at once.
duplicate_execution = False   # bound on every path — 5g and 5h both read it
# `duplicate_execution` alone is NOT enough for 5h. It is only computed at the very END of this
# try, so any earlier exception (the round-trip assert, the results GET raising) leaves it False
# while a result file written by ANOTHER agent may be sitting on the server with `post_id: null`.
# 5h would then read that False as "the clean file is ours" and overwrite THEIR post link with
# our post id. Track whether WE actually wrote the file, and gate the backfill on that too.
result_file_written = False   # True only after our own PUT comes back OK, on either branch
try:
    FENCE = "`" * 3   # built at runtime so the markdown fences below stay intact

    def yaml_scalar(v):
        """Python value → a YAML scalar. `null` for None, JSON-quoted for str, plain for numbers.
        Never interpolate raw: YAML has no `None` literal, so `f"value: {None}"` writes the
        four-character STRING `None` and every consumer testing `is None` sees a truthy string."""
        return "null" if v is None else json.dumps(v) if isinstance(v, str) else str(v)

    def md_field(v):
        """A queue field rendered into the BODY. Multi-line values — and anything starting with
        `---`, i.e. every unified diff — go inside a fence so they cannot be read as a
        frontmatter delimiter and truncate the block."""
        s = "" if v is None else str(v)
        return (f"{FENCE}text\n{s}\n{FENCE}"
                if ("\n" in s or s.lstrip().startswith("---")) else s)

    _ts_score = pending_result.get("test_score") or {}   # bound whether or not 5c re-ran the eval

    # The diff was rendered by the session that DIED; nothing here re-derives it, and re-diffing
    # against the CURRENT champion would describe a change nobody evaluated. Use the artifact
    # that session left on disk when it is still there, and say so honestly when it is not.
    _dp    = pending_result.get("diff_path") or item.get("diff_path")
    _dnote = f" at {_dp}" if _dp else ""
    try:
        _dtxt = Path(_dp).read_text() if (_dp and Path(_dp).is_file()) else ""
    except Exception:
        _dtxt = ""
    _dlines = _dtxt.splitlines()
    if not _dtxt:
        diff_section = (f"(no diff artifact on disk{_dnote} — resumed session; "
                        f"the change is described under ## Change)")
    elif len(_dlines) <= 80:
        diff_section = f"{FENCE}diff\n{_dtxt}\n{FENCE}"
    else:
        diff_section = (f"{FENCE}diff\n" + "\n".join(l for l in _dlines if l.startswith("@@")) +
                        f"\n({len(_dlines)} diff lines — full patch{_dnote})\n{FENCE}")

    _test_state = {"complete": "ran",
                   "failed":   "eval FAILED (infra) — tells us NOTHING about the candidate",
                   "not_run":  "not run — no provisional train keep (train DISCARD/FAILED)"
                   }.get(test_status, f"unknown (test_status={test_status})")

    # `is_valid` and `test_is_valid` come from the sentinel (5a rehydration / 5c re-run), which is
    # the authoritative record of what was measured. `mean_rel_energy` is REQUIRED alongside
    # is_valid: fitness is honest even for an energy-gate failure, so `is_valid: 0` alone no
    # longer says WHY the run was rejected, and this frontmatter is where analysts read it.
    _fm_is_valid        = int(is_valid) if is_valid is not None else None
    _fm_test_is_valid   = int(test_is_valid) if test_is_valid is not None else None
    _fm_mean_rel_energy = _score.get("mean_rel_energy") if _score else None

    result_markdown = f"""---
exp_id: {yaml_scalar(exp_id)}
agent: {yaml_scalar(AGENT_NAME)}
team: {yaml_scalar(MY_TEAM)}
axis: {yaml_scalar(item.get("axis") or "UNKNOWN")}
direction: {yaml_scalar(item.get("direction") or axis_direction or "UNKNOWN")}
value: {yaml_scalar(item.get("value"))}
outcome: {yaml_scalar(outcome)}
fitness: {yaml_scalar(our_metric)}
is_valid: {yaml_scalar(_fm_is_valid)}
mean_rel_energy: {yaml_scalar(_fm_mean_rel_energy)}
delta: {yaml_scalar(round(delta, 6))}
test_metric_value: {yaml_scalar(test_fitness)}
test_is_valid: {yaml_scalar(_fm_test_is_valid)}
post_id: null
---

# {exp_id} — {outcome}

Resumed from a prior session: true

## Hypothesis

{md_field(item.get("hypothesis") or description)}

## Change

{md_field(item.get("diff") or description)}

## Diff

{diff_section}

## Eval Diagnostics

**Train (`--split train`)**
- fitness: {_score.get("fitness")} (delta vs champion {delta:+.6f}; champion train anchor {current_best})
- is_valid: {_score.get("is_valid")} — {_score.get("invalid_reason") or "valid"}
- mean_rel_steps: {_score.get("mean_rel_steps")}
- mean_rel_energy: {_score.get("mean_rel_energy")} (validity gate: >= 1.0)
- max_final_energy_delta_kcal_mol: {_score.get("max_final_energy_delta_kcal_mol")} (diagnostic only)
- converged: {_score.get("converged")} | num_results: {_score.get("num_results")} | num_errors: {_score.get("num_errors")}
- duration_s: {_score.get("duration_s")}

**Held-out test (`--split test`)** — {_test_state}
- test_fitness: {test_fitness} (champion test anchor {current_best_test}, delta {test_delta:+.6f})
- test_is_valid: {_ts_score.get("is_valid")}
- test_mean_rel_energy: {_ts_score.get("mean_rel_energy")}
- gate: reason={test_reason or "n/a"}

## Interpretation

<2-4 sentences: what these numbers mean, which failure mode (if any) fired, and what the next
experiment should try. No protocol narration.>
"""

    # Round-trip check — REQUIRED, same as ROLE-CPU Step 5. Proves no interpolated value (a
    # `---`-leading diff, an unquoted queue `value`) truncated the frontmatter block.
    _fm_back = yaml.safe_load(result_markdown.split("---")[1]) or {}
    assert _fm_back.get("exp_id") == exp_id and _fm_back.get("outcome") == outcome, \
        "result frontmatter round-trip failed — an interpolated field broke the YAML block"

    # Never clobber another agent's result file: if one exists and its `agent:` names someone
    # else, that agent ran the same experiment (the claim was not exclusive) — APPEND instead,
    # so both executions stay visible and the two ledger rows can be reconciled. A file whose
    # `agent:` is US is our own earlier partial write and is safe to replace.
    _existing = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                             headers=HEADERS).json()
    _ex_body  = _existing.get("content") or ""
    _ex_agent = (parse_frontmatter(_existing) or {}).get("agent")
    duplicate_execution = bool(_ex_body) and _ex_agent not in (None, "", AGENT_NAME)

    if not duplicate_execution:
        _rp = requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
            headers=HEADERS, json={"content": result_markdown})
        result_file_written = bool(getattr(_rp, "ok", False))
    else:
        # `DUP_POST_ID_{AGENT_NAME}` is the placeholder 5h replaces with our own [RESULT] id.
        _dup = (
            f"\n\n## Duplicate execution (coordination defect)\n\n"
            f"`{exp_id}` was evaluated TWICE — the queue claim was not exclusive.\n\n"
            f"- first writer: {_ex_agent} (post_id in the frontmatter above)\n"
            f"- second writer: {AGENT_NAME} (post_id: DUP_POST_ID_{AGENT_NAME}), resumed session\n"
            f"- {AGENT_NAME} measured: train {metric_name}={our_metric} "
            f"(delta {delta:+.6f}, is_valid={is_valid}), "
            f"held-out test {test_fitness} (is_valid={test_is_valid}), outcome {outcome}\n\n"
            f"The frontmatter above belongs to the FIRST writer and is left untouched. Two "
            f"independent evals of one experiment burned the shared pool twice.\n")
        _rp = requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
            headers={**HEADERS, "If-Match": str(_existing.get("version", 0))},
            json={"content": _ex_body + _dup})
        result_file_written = bool(getattr(_rp, "ok", False))
        print(f"[RESUME] duplicate execution of {exp_id} (first writer {_ex_agent}) — appended "
              f"instead of overwriting.")
except Exception as e:
    # Do NOT let a broken render swallow the [RESULT] post — that would lose the experiment
    # entirely instead of only its result file. Fail LOUDLY and continue: the ledger can still
    # fall back to the `completed:` row 5e wrote.
    print(f"[RESUME] results/{exp_id}.md write FAILED: {e!r} — posting [RESULT] anyway; "
          f"fix the offending field rather than leaving the ledger without a result file.")

# 5g. Post [RESULT] — THE whole point of this branch. Keep it under ~200 words:
#     both split numbers, the outcome, (on REJECTED_TEST) the explicit reason, and
#     (on NEAR_MISS) that BOTH gates passed and only the promotion race was lost.
_test_line = (f"{metric_name} (held-out test): {test_fitness} "
              f"(delta {test_delta:+.6f}, is_valid={test_is_valid})"
              if test_status == "complete" else
              f"held-out test: not run (test_status={test_status})")
_reason = f"\nTest-gate rejection: {test_reject_reason}" if outcome == "REJECTED_TEST" else ""
# NEAR_MISS wording must be unambiguous: the change WORKED, it simply did not get promoted.
_stack_line = ""
if outcome == "NEAR_MISS":
    _stack_line = ("\nNEAR_MISS: train gate PASSED and held-out test gate PASSED, but the promotion "
                   "race was lost (a PRE-PUT abort or the champion race guard fired), so champion.md "
                   "and champion/algo.py are UNCHANGED. Not a dead end — the change is re-queued as "
                   f"{stack_requeue} to be re-tested on the new champion."
                   if stack_queued else
                   "\nNEAR_MISS: train gate PASSED and held-out test gate PASSED, but the promotion "
                   "race was lost, so champion.md and champion/algo.py are UNCHANGED. Not a dead end. "
                   f"Re-queue as {stack_requeue} SKIPPED ({stack_skip_reason}).")
_requeue_line = (f"\nItem RE-QUEUED to pending: (last_failure={resume_failure})."
                 if outcome == "FAILED" else "")
# A duplicate execution is a COORDINATION defect, not a scientific one — say so, or the monitor
# and the analysts read two independent evals of ONE experiment as two pieces of evidence.
_dup_note = ("" if not duplicate_execution else
             f"\n\n## Duplicate execution (coordination defect)\n"
             f"`{exp_id}` was ALSO run by another agent — the queue claim was not exclusive. "
             f"Their result file was left intact; this cycle's numbers were APPENDED to it "
             f"under `## Duplicate execution`.")
r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[RESULT] {exp_id}: {metric_name}={our_metric} ({outcome})",
    "content": f"## Experiment\n{description}\n\n## Result\n"
               f"{metric_name} (train): {our_metric} (delta {delta:+.6f}, is_valid={is_valid})\n"
               f"{_test_line}\n"
               f"Outcome: {outcome}{_reason}{_stack_line}{_requeue_line}\n"
               f"Resumed-from-prior-session: true\n\n"
               f"## Team\n{MY_TEAM}{_dup_note}",
    "tags": [f"team:{MY_TEAM}", "type:result", f"outcome:{outcome}", "resumed:true"]
})

# 5h. Mark posted (prevents duplicate post next cycle — DO NOT SKIP), then backfill `post_id`
#     into the result file exactly as ROLE-CPU Step 8b does, so a resumed result file ends up in
#     the same shape as a non-resumed one and the ledger can link result → post.
_result_post_id = r.json().get("id") if r.ok else None
if _result_post_id:
    # On a duplicate execution the frontmatter `post_id` belongs to the FIRST writer — leave it
    # alone and fill OUR placeholder in the duplicate-execution section instead. A missing file
    # (the 5f2 write failed) leaves `_rc` empty, both branches miss, and nothing is written.
    # BOTH branches also require `result_file_written`: if 5f2 threw before it computed
    # `duplicate_execution`, that flag is False by default while the file on the server may be
    # another agent's — and `post_id: null` in THEIR frontmatter is their unfilled link, not
    # ours. Backfill only into a file we actually wrote this cycle.
    _rf = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                       headers=HEADERS).json()
    _rc = _rf.get("content") or ""
    _placeholder = f"DUP_POST_ID_{AGENT_NAME}"
    if result_file_written and duplicate_execution and _placeholder in _rc:
        _new = _rc.replace(_placeholder, str(_result_post_id), 1)
    elif result_file_written and not duplicate_execution and "post_id: null" in _rc:
        _new = _rc.replace("post_id: null", f"post_id: {_result_post_id}", 1)
    else:
        _new = None
        if not result_file_written:
            print(f"[RESUME] post_id backfill SKIPPED for {exp_id}: 5f2 wrote no result file "
                  f"this cycle, so any `post_id: null` on the server belongs to another writer.")
    if _new is not None:
        requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
            headers={**HEADERS, "If-Match": str(_rf.get("version", 0))},
            json={"content": _new})

pending_result.update({"status": "posted", "posted_to_workshop": True,
                       "outcome": outcome,
                       "test_fitness": test_fitness, "test_is_valid": test_is_valid,
                       "test_status": test_status,
                       # NEAR_MISS PASSED the test gate — it lost the promotion race, not the gate.
                       "test_pass": (outcome in ("KEEP", "NEAR_MISS")) if test_status == "complete" else None,
                       "test_reason": test_reason,
                       # Bookkeeping only — `direction` in the sentinel stays the EXPERIMENT
                       # axis direction and is never overwritten with the champion's "minimize".
                       "stack_requeued_as": stack_requeue if stack_queued else None,
                       "stack_requeue_skipped": stack_skip_reason,
                       "requeued_to_pending": (outcome == "FAILED"),
                       # Which claim file this cycle held, and whether we handed it back.
                       "claim_path": claim_path,
                       "claim_released": (outcome == "FAILED"),
                       "last_failure": resume_failure,
                       "result_post_id": _result_post_id,
                       "posted_at": datetime.now(timezone.utc).isoformat()})
pending_path.write_text(json.dumps(pending_result, indent=2, default=str))
```

Then fall through to Part 6 (update AGENT.md with `last_branch="resume-and-post"`, exit with promise). Do NOT enter Part 4.

---

## Part 6: Always-Last — Record and Exit

Run this regardless of which branch you took — Part 2 (discussion), Part 3 (no-team), Part 3.5 (monitor health pass), Part 4 (normal), Part 5 (resume-and-post), or the resume-waiting exit from Part 0 Check C. At minimum do 6a (update AGENT.md with the branch you took) and 6e (exit with promise tag). For resume-waiting and for the monitor health pass, run 6a → 6d → 6e and skip 6b/6c.

### 6a. Update AGENT.md

```python
agent_content = f"""---
name: {AGENT_NAME}
role: {MY_ROLE}
team: {MY_TEAM if MY_TEAM else 'null'}
last_seen: "{NOW}"
session_count: {session_count + 1}
last_branch: "{branch_taken}"  # monitor-health / discussion / no-team / normal / resume-waiting / resume-and-post
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

If you noticed something worth flagging but aren't ready to propose an experiment, post a `[SUGGESTION]`. Keep it under ~150 words — three short sections, no narration of how you found it:

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

When you learn something reusable across sessions. **One memory file per session at most, and ≤ 30 lines each** — a finding plus the numbers that support it. If it does not change what a future session would DO, do not write it. Same for AGENT.md in 6a: `## Current Focus` and `## Notes for Next Session` are ≤ 5 lines each.
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

- [ ] I read my `role:` from AGENT.md first (Part 0 Check A0). If it is `monitor`, that alone decides my branch — Part 3.5 — and I skip the rest of this list except the monitor line.
- [ ] I read my launch prompt and noted whether `MODE=discussion` or `MODE=execute` was set.
- [ ] I read `teams/roster.md` and determined `MY_TEAM`.
- [ ] I checked `agents/{AGENT_NAME}/workspace/result_latest.json` for an unposted prior result (Part 0 Check C).
- [ ] I picked exactly ONE branch from the Part 0 table.
- [ ] If I am not the monitor and I have a team: I cleared the review backlog first (Part 1 (Boot), § Review backlog) — up to `REVIEW_CAP` oldest `review_status: pending` items across ALL team queues, none of them my own proposal, none of them an item with no `proposal_post` (unreviewable — an analyst repairs those), each reviewed with a leading `[REVIEW-OK]` / `[REVIEW-BLOCK]` and real reasoning; an empty backlog means I posted NOTHING, and I never padded to reach a count. **That one pass IS my role file's review step (ROLE-CPU Step 0.5 / ROLE-ANALYST Step 0.1) — I did not run a second batch there; `REVIEW_CAP` is per spawn, not per step.**
- [ ] If resume-waiting: I will NOT claim new work; the eval is still running from my own session.
- [ ] If Part 5 (resume-and-post): I will re-verify that v1 `updatedBy` of my claim file still names me BEFORE spending anything (5a2) and stop with `FAILED(claim_lost)` and no post if it does not; I will finish the held-out test gate if `test_status != "complete"`, decide KEEP / NEAR_MISS / DISCARD / REJECTED_TEST / FAILED, write `results/{exp_id}.md` (5f2 — a resume writes the result file like any other cycle), post the prior result, backfill its `post_id` and set `posted_to_workshop=true`; a FAILED item stays in `pending:` AND I release its claim file so somebody else can take it (never `completed:`, never dead_ends.md); a NEAR_MISS completes AND adds `{exp_id}_stack` to `pending:`, writes no team file, and leaves both champion files untouched; I will NOT edit `algo.py` and will NOT start a new experiment.
- [ ] If Part 2 (discussion): I will NOT touch any training code.
- [ ] If Part 3 (no-team): I will exit immediately after recording.
- [ ] If Part 3.5 (monitor health pass): I run the pass exactly once and exit — no queue claims, no experiments, no `teams/roster.md`, no `champion/`, no loop or sleep.
- [ ] If Part 4 (normal): every artifact I produce will have a corresponding AnonAPI API call.

If you cannot tick all boxes, exit cleanly via Part 6e.
