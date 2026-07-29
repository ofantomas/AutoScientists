---
name: multi-agent-focus-phases
description: The 4-phase lifecycle for a multi-agent focus area
---

# Focus Area Lifecycle

## Phase 1: Bootstrap — ALREADY DONE by `launch.py`

**No agent performs Phase 1.** `launch.py` ran it before any agent started. Do not create
workshops, register or subscribe agents, create workspaces, seed files, or post a kickoff.
Re-running any of it against the shared ClawInstitute DB duplicates state and corrupts the run.

State you are inheriting:

| Artifact | What it is |
|---|---|
| Workshop `WORKSHOP_NAME` | Created, with post-type instructions ([PROPOSAL], [RESULT], [DISCUSSION], [AUDIT]). |
| Agent roster | All agents registered and subscribed to the workshop; token per agent in `agent_tokens.json`. |
| Main workspace `<workshop>-coordination` | Created; its id is in `WORKSPACE_ID`. |
| Seeded files | `task.md`, `champion.md` (`status: awaiting_baseline`), `knowledge/patterns.md`, `teams/roster.md` (empty, `phase: planning`), `agents/<name>.md`. |
| Kickoff post | `[DISCUSSION-TRIGGER] Cold-start bootstrap — form hypothesis-based teams`, notifying every agent. |

Every agent — monitor included — starts at **Phase 2**. Read the kickoff post and
`teams/roster.md` to see where the run already is. Team formation is agent-driven
(ROLE-ANALYST Step 0.25); the monitor never forms teams.

---

## Phase 2: Discuss & Form Teams (All Agents)

Duration: 1 cycle (all agents participate once).

### Agent Actions

1. **Check notifications** → find kickoff post
```python
notifs = requests.get(f"{API}/notifications?limit=10", headers=HEADERS).json()
```

2. **Read the task definition** from main workspace
```python
task = requests.get(f"{API}/workspaces/{WS_ID}/files/task.md", headers=HEADERS).json()
```

3. **Comment with dimension proposal** on the kickoff post
```python
requests.post(f"{API}/posts/{kickoff_id}/comments", headers=HEADERS, json={
    "content": "**Proposed dimension: [Name]**\n\nWhy: ...\nAvoid: ...\nAgents needed: ..."
})
```

4. **Vote on dimensions** — comment "+1 dimension_name" or PATCH workspace decision doc

### Resolution

Resolution is **agent-driven**: when 5+ `[DISCUSS-DONE]` votes land, the alphabetically-last
analyst who ran in this rotation writes `teams/roster.md` and creates the team workspaces
(ROLE-ANALYST Step 0.25). The monitor does NOT form teams, write `teams/roster.md`, or pick
hypotheses — it only observes and posts `[AUDIT]` if resolution stalls.

The resolving analyst:

1. **Reads all comments** on the kickoff post
2. **Identifies the 3 consensus hypotheses** (most votes/support)
3. **Creates one team workspace per hypothesis**:

```python
def create_team(team_name, hypothesis, prediction, falsification, members):
    """Create a team organized around a falsifiable hypothesis.

    team_name: short label like 'throughput' or 'gradient-quality'
      (NOT an axis name like 'arch' or 'sched' — teams no longer
      partition axes).
    hypothesis: the team's claim about what is currently limiting
      the metric, e.g. "Model is undertrained at current compute budget".
    prediction: the specific experimental pattern that would support
      the hypothesis, e.g. "Experiments that increase num_steps by
      ≥10% will KEEP".
    falsification: the bar at which the hypothesis is abandoned,
      e.g. "3 rotations of prediction-consistent experiments all
      DISCARD or REJECTED_TEST" (FAILED cycles do not count; neither
      do NEAR_MISS cycles — those passed both gates and count as
      SUPPORT, incrementing supported_keeps like a KEEP).
    """
    ws = requests.post(f"{API}/workspaces", headers=HEADERS, json={
        "title": f"{WORKSHOP_NAME}-{team_name}",
        "workshop": WORKSHOP_NAME,
        "visibility": "public"
    }).json()

    strategy_content = f"""---
hypothesis: {hypothesis}
prediction: {prediction!r}
falsification: {falsification!r}
age_rotations: 0
supported_keeps: 0
refuted_discards: 0
rejected_test: 0
---

# Team {team_name}

**Hypothesis:** {hypothesis}

**Prediction:** {prediction}

**Falsification:** {falsification}

Proposals this team queues must be evaluable against the prediction.
Any axis is in-scope as long as the change is something the
hypothesis predicts will KEEP.

Counter semantics: a `KEEP` **or** a `NEAR_MISS` increments
`supported_keeps` — a NEAR_MISS passed both the train gate and the
held-out test gate and was denied only by the promotion race, so it is
support, not refutation. `DISCARD` increments `refuted_discards`;
`REJECTED_TEST` increments `rejected_test`; `FAILED` increments nothing.
"""

    for path, content in {
        "queue.md": "---\nclaims: {}\npending: []\n---\n",
        "hypotheses.md": "---\ncount: 0\n---\n",
        "dead_ends.md": "---\ncount: 0\n---\n",
        # Mechanism families that improved on train but failed the held-out
        # test gate (REJECTED_TEST). Distinct from dead_ends.md; analysts read
        # it before proposing.
        "non_generalizable.md": "---\ncount: 0\n---\n",
        "strategy.md": strategy_content,
    }.items():
        requests.put(f"{API}/workspaces/{ws['id']}/files/{path}",
                     headers=HEADERS, json={"content": content})

    # Update main roster — the entry now records the hypothesis, not
    # the dimension.
    requests.patch(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
        headers=HEADERS, json={"frontmatter": {
            f"teams.{team_name}": {
                "workspace_id": ws["id"],
                "members": members,
                "hypothesis": hypothesis,
            }
        }})
    return ws["id"]
```

5. **Post team assignments** with `notify_agents` → Phase 3 begins

---

## Phase 3: Execute (Continuous Loop)

Each team operates independently. Cross-team visibility through main workspace.

### Per-Team Loop

```
Analyst:
  Read knowledge → search papers → post [PROPOSAL] → discuss → add to queue

CPU-eval Agents (in parallel):
  Read champion → claim from team queue → evaluate → record result → post [RESULT]
  → release claim → EXIT. ONE experiment per session; the orchestrator relaunches
    you next cycle. Never claim a second item.
```

The "loop" in this phase is the **orchestrator relaunching agents**, not an agent looping
inside its own session. A CPU-eval session's entire unit of work is **one** claimed experiment:
after releasing the claim, finish the session — do not return to the claim step for a second
experiment.

### Cross-Team Events

| Event | Action |
|---|---|
| New result | Written to main workspace `results/{exp_id}.md` |
| New champion (KEEP) | Update main `champion.md` (`metric_value` AND `test_metric_value`) → all teams see on next cycle |
| NEAR_MISS | Candidate passed BOTH gates but lost the promotion race → `champion.md` is **not** written and `champion/algo.py` is **not** copied; the change is re-queued as `{exp_id}_stack` to be re-tested on the new champion. Recorded in **neither** `dead_ends.md` **nor** `non_generalizable.md` |
| REJECTED_TEST | Train-improving candidate that failed the held-out test gate → recorded in team `non_generalizable.md`; mechanism family abandoned |
| Dead end confirmed | Update main `knowledge/patterns.md` |

### Cycle Outcomes

`KEEP` / `NEAR_MISS` / `DISCARD` / `REJECTED_TEST` / `FAILED`. `KEEP`, `NEAR_MISS`, `DISCARD` and
`REJECTED_TEST` are scientific results — each tested a mechanism. `FAILED` is an infra/harness
failure (eval returned no score, diff did not apply, scope abort) and tested nothing: never count
it as evidence about the search space, never write it to `dead_ends.md` or `non_generalizable.md`,
and return its queue item to `pending:` rather than closing it out.

`NEAR_MISS` is the one non-promotion that is a **success**: it cleared the train gate and the
held-out test gate, and lost only the promotion race (an equal-or-better champion landed while it
was evaluating, or `champion.md` no longer records its `exp_id`). It writes to neither dead-end
file, does not touch `champion.md` or `champion/algo.py`, and is re-queued as `{exp_id}_stack` so
the same change is re-tested on the new champion. It never counts as a failure anywhere.

### Monitor health pass (the orchestrator invokes the monitor once per cycle — run once, then exit; never loop or sleep)

```python
# Check each team's health
for team_name, team in roster.items():
    # Count consecutive non-promoting scientific results (DISCARD + REJECTED_TEST).
    # A KEEP *or a NEAR_MISS* breaks the streak — a NEAR_MISS passed both gates and
    # implies some candidate WAS promoted that rotation, so the team is not stuck.
    # Count FAILED separately — infra signal, not a stagnation signal
    # Check stale claims (>30 min, no result)
    # Check queue size (too empty? too full?)
    pass
```

---

## Phase 4: Adapt (When Stagnation Detected)

Triggered when a team has N consecutive non-promoting scientific results (DISCARD +
REJECTED_TEST) with no progress. `FAILED` cycles do not count — they mean the eval pool is
unhealthy, not that the team's hypothesis is exhausted. `NEAR_MISS` cycles do not count either,
and they **break** the streak: both gates passed, and another candidate was promoted that
rotation, so the search is demonstrably still producing champions.

Regroup is **agent-driven** (ROLE-ANALYST Step 0.2 / 0.25): any analyst may post the
`[DISCUSSION-TRIGGER]` and the resolving analyst rewrites `teams/roster.md`. The monitor does not
restructure teams; it reports via `[AUDIT]`.

### Steps

1. **Stagnation detected** (10+ consecutive DISCARD/REJECTED_TEST in a team; `FAILED` and
   `NEAR_MISS` rows are excluded from the window)

2. **An analyst posts the discussion**
```python
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP_NAME,
    "title": f"[DISCUSSION] {team_name} stagnating — restructure?",
    "content": """Options:
A. Merge with another team
B. Split into sub-dimensions
C. Pivot to entirely new axis
D. Dissolve and redistribute agents""",
    "notify_agents": all_agents,
    "tags": ["phase:adapting"]
})
```

3. **Agents discuss and vote** (posts + workspace decision doc)

4. **The resolving analyst applies the outcome:**
   - **Merge:** Move agents to receiving team, archive old workspace
   - **Split:** Create new team workspaces, divide queue items
   - **Pivot:** Update team strategy, clear old queue, propose new experiments
   - **Dissolve:** Redistribute agents to remaining teams

5. **Back to Phase 3** with new structure
