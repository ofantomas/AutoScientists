---
name: multi-agent-focus
description: >
  Generic skill for self-organizing multi-agent teams that collaborate on an
  optimization problem. Agents discuss dimensions, form teams, run experiments,
  and adapt when stagnating. Uses AnonAPI posts for discussion and
  workspaces for shared state.
---

# Multi-Agent Focus Area

A **focus area** is a group of AI agents collaborating on an optimization problem. Agents self-organize into teams, each attacking a different dimension of the problem.

## Core Concepts

| Concept | What it is | AnonAPI feature |
|---|---|---|
| **Workshop** | The focus area container — all agents subscribe | `POST /workshops` |
| **Main workspace** | Shared state: champion config, all results, cross-team knowledge | `POST /workspaces` |
| **Team workspace** | Team-internal state: queue, hypotheses, dead ends, strategy | One per team |
| **Posts** | Discussion: proposals, results, strategy debates, votes | `POST /posts` |
| **Workspace files** | Structured data with YAML frontmatter, versioned, searchable | `PUT /workspaces/{id}/files/{path}` |

## How It Works

```
1. BOOTSTRAP    — launch.py creates the workshop, roster, main workspace + kickoff post (no agent does this)
2. DISCUSS      — All agents propose dimensions, debate, vote on teams
3. EXECUTE      — Teams run experiments in parallel, share results
4. ADAPT        — Stagnating teams restructure via discussion + vote
```

See `reference/PHASES.md` for detailed lifecycle.

## Agent Roles

| Role | Count per team | What they do |
|---|---|---|
| **Monitor** | 1 (global) | Bootstrap, facilitate team formation, monitor health |
| **CPU-eval Agent** | 2 per team | Claim experiments, evaluate candidates, record results |
| **Analyst** | 1 per team | Research mechanisms, propose experiments, prune dead ends |

See `templates/ROLE-MONITOR.md`, `templates/ROLE-CPU.md`, `templates/ROLE-ANALYST.md`, `templates/ROLE-TEAM.md`.

## Main Workspace — Initial Files

These files are created during bootstrap. Agents may create additional files as needed — other agents discover them via LIST.

```
champion.md                — Current best config (ESSENTIAL ANCHOR — always read)
results/{exp_id}.md        — One file per experiment result (write-once)
teams/roster.md            — Team assignments and workspace IDs (ESSENTIAL ANCHOR)
```

Additional files are created organically by agents (e.g., `knowledge/lr-schedules.md`). Use `GET /files` to discover what exists.

## Team Workspace — Initial Files

```
queue.md                   — Pending experiments + active claims (ESSENTIAL ANCHOR)
dead_ends.md               — Mechanisms ruled out by this team
strategy.md                — Current team approach
```

Agents may create additional files (analysis docs, hypothesis lists, etc.). Use descriptive paths — see `templates/ROLE-TEAM.md` § File Naming Convention.

## Coordination Model

- **Discovery over prescription** — agents LIST workspace files each cycle and decide what to read, rather than following hardcoded file checklists. See `templates/ROLE-TEAM.md` § File Discovery Protocol
- **Posts for discussion** — proposals get debated before entering a queue
- **Workspaces for state** — structured data with version history
- **Notifications for alerts** — `notify_agents` on post creation
- **PATCH for concurrency, but only on flat frontmatter** — dot-notation updates to a *flat,
  single-key* frontmatter don't conflict. **NEVER PATCH `queue.md`**, or any file whose frontmatter
  holds nested structures or lists: a dotted-key PATCH (`claims.agent_1`) flattens the `pending:`
  list and destroys other teams' entries. Those files use **read-modify-PUT with `If-Match`** — see
  `templates/ROLE-TEAM.md` § Team Queue (queue.md)
- **Client-side YAML parsing** — the API stores files as raw text. Agents must parse YAML frontmatter themselves (see `API-REFERENCE.md`)
- **Champion propagation** — on a KEEP, the winning CPU-eval agent writes `champion.md` AND copies its own candidate to `{FOCUS_ROOT}/champion/algo.py` itself, per `templates/ROLE-CPU.md` Step 7b1. The orchestrator never writes `champion/`. All CPU-eval agents read from this canonical path

## Review-Before-Claiming Rule

Every experiment MUST start as a `[PROPOSAL]` post, and every queue item carries
`review_status: pending | ok | blocked`. **Only `ok` is claimable.** An item becomes `ok` when a
**non-author** — from any team, analyst or cpu-eval — comments on its `[PROPOSAL]` with a verdict:

- `[REVIEW-OK]` + substantive reasoning → claimable
- `[REVIEW-BLOCK]` + substantive reasoning → moved out of `pending:` into `blocked:`; only an
  analyst clears it, explicitly and with a stated reason. Another `[REVIEW-OK]` does **not**.
- an untagged comment is **not** a review and changes nothing

There is no path to claiming an unreviewed item — no time grace, no starvation override. An idle
rotation is cheaper than an eval spent on a mechanism nobody checked. Two consequences follow:

- Reviewing is a **backlog to drain, not a quota to fill.** On spawn each non-monitor agent reviews
  the oldest still-`pending` items it did not propose, up to `REVIEW_CAP`, and posts nothing when
  the backlog is empty. A padded, content-free review defeats the whole gate.
- An agent may not claim an item when its own `[REVIEW-OK]` is the **only** one on it — that would
  be self-service. A second independent review makes it claimable by anyone.

An item naming no `proposal_post` is **unreviewable**, not waiting: there is no post for a review to
attach to, so it can never become claimable. Repairing such a row is an analyst job.

## Cross-Team Coordination

1. All results go to **main workspace** `results/` — visible to every team
2. A **`NEAR_MISS` outcome** (candidate passed BOTH gates but lost the promotion race) needs no cross-team action — the cpu-eval agent auto-re-queues it as `{exp_id}_stack` against the new champion
3. **KEEP results** (new champion) update main `champion.md` — all teams rebase
4. Monitor posts periodic `[AUDIT]` summarizing all team progress

## Using This System

This system is **problem-agnostic**. The specific optimization problem is defined in a **task file** (a `TASK.md` inside the directory passed via `--task` to `launch.py`). The task defines:

- What metric to optimize
- How to run an experiment
- What the search space looks like
- Hardware constraints

To start a new focus area: read your task file, then follow `PHASES.md`.
