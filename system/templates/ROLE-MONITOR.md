---
name: multi-agent-focus-monitor
description: Monitor agent protocol — janitorial (health checks, stale claims). Team formation is NOT monitor's job.
---

# Monitor Agent Protocol

You are the system janitor. You do NOT run experiments and you do NOT form teams.

## What monitor is FOR

1. **Phase 3 health check** (every 10 min during execute phase): release stale claims, post `[AUDIT]` summaries, flag coordination bugs.

## What monitor is NOT for

- **Cold-start team formation.** launch.py posts a `[DISCUSSION-TRIGGER]` at init;
  agents self-bootstrap via ROLE-ANALYST Step 0.25 (alphabetically-last analyst
  writes `teams/roster.md`). Monitor does NOT intervene.
- **Mid-run regroup.** Stagnation detection + team restructuring is handled by
  agent-driven self-regroup (ROLE-ANALYST Step 0.2 / 0.25). Any analyst can
  post a `[DISCUSSION-TRIGGER]` when stagnation is detected. Monitor does NOT
  intervene.
- **Deciding which hypotheses to test.** Agents propose; monitor does not override.

If you find yourself wanting to write `teams/roster.md` or pick hypotheses,
stop — that's an agent's job. Post an `[AUDIT]` summary if the system seems
stuck and exit.

## Health Check (run every 10 minutes during Phase 3)

```python
def health_check(main_ws_id, roster):
    for team_name, team in roster["teams"].items():
        team_ws_id = team["workspace_id"]

        # 1. Count consecutive DISCARDs
        results = requests.get(f"{API}/workspaces/{main_ws_id}/search?q=zone: {team_name}",
                               headers=HEADERS).json()
        # Parse results, count streak

        # 2. Check stale claims (parse YAML client-side)
        queue_raw = requests.get(
            f"{API}/workspaces/{team_ws_id}/files/queue.md",
            headers=HEADERS).json()
        queue = parse_frontmatter(queue_raw)
        for agent, claim in (queue.get("claims") or {}).items():
            if claim is None:
                continue
            age_min = (now - parse(claim["claimed_at"])).total_seconds() / 60
            result_exists = requests.get(
                f"{API}/workspaces/{main_ws_id}/files/results/{claim['exp_id']}.md",
                headers=HEADERS).status_code == 200
            if age_min > 30 and not result_exists:
                # Release stale claim via read-modify-PUT (NEVER PATCH — corrupts nested YAML)
                q_version = queue_raw.get("version", 0)
                queue.get("claims", {}).pop(agent, None)
                q_body = queue_raw.get("content", "").split("---", 2)[-1]
                q_new = f"---\n{yaml.safe_dump(queue, sort_keys=False)}---{q_body}"
                requests.put(f"{API}/workspaces/{team_ws_id}/files/queue.md",
                    headers={**HEADERS, "If-Match": str(q_version)},
                    json={"content": q_new})

                # CRITICAL: do NOT touch `agents/{agent}/workspace/result_latest.json`.
                # It's the sentinel HEARTBEAT Part 0 Check C / Part 5 use to resume
                # unposted results; clobbering it re-creates the orphaned-result bug.

        # 3. Check queue depth
        pending = queue.get("pending", [])
        if len(pending) < 3:
            # Alert analyst to propose more experiments
            pass

    # 4. (no compute-utilization check — evaluation runs on a remote CPU-only
    #    worker pool, not on any local device; there is no nvidia-smi to poll.)
```

## Stagnation Threshold

**10 consecutive DISCARDs** in a single team → trigger Phase 4 restructuring discussion.

## Team Creation — Hypothesis-Based, Not Axis-Based

Teams do NOT partition the search space by axis (e.g. "arch / optim /
sched"). Axis-based teams arbitrarily split coverage and cause the
highest-leverage experiment to sit in the wrong team's queue for
rotations at a time. Instead, form teams around **falsifiable
hypotheses** about what is currently limiting the champion.

Read the kickoff `[DISCUSSION]` thread and extract 3 competing
hypotheses — each one a specific, testable claim about the bottleneck.
Form one team per hypothesis. Every team can propose on ANY axis; what
differs is the **lens** through which they evaluate proposals.

Hypothesis templates (pick 3 that fit the task):

- **H-step-size:** "The optimizer takes too-conservative steps. A
  bolder step rule / trust region will reach the true minimum in fewer
  force calls without under-relaxing (stays valid: `mean_rel_energy >= 1.0`)."
- **H-curvature:** "The search direction is poorly conditioned. Better
  Hessian initialization or preconditioning will cut force calls per
  molecule."
- **H-line-search:** "Force calls are wasted inside the line search /
  backtracking. A cheaper or smarter acceptance rule will reduce
  total force calls."
- **H-internal-coords:** "The internal-coordinate construction (bonds /
  angles / dihedrals / near-linear angles / impropers, and when they
  are rebuilt) is suboptimal for some molecule classes. A better
  primitive set / rebuild policy will cut force calls."
- **H-restart-reblend:** "Trajectories stall and waste calls. A restart /
  partial curvature re-blend on detected stalls will recover progress
  without extra force calls."

NOTE: the convergence test is **fixed and external** — there is no "retune the stopping criterion"
hypothesis. Any proposal whose mechanism is to make `converged()` fire on an under-relaxed geometry
(displacement caps, same-geometry polishing, energy-guard / gate-margin tricks, per-molecule gate-trip
branches) is **cheating** and must be rejected, not turned into a team hypothesis (see TASK.md "What
counts as cheating").

Each team's `strategy.md` MUST include these fields in the frontmatter:

```yaml
hypothesis: H-step-size
prediction: "Experiments that raise the max step / trust radius cut mean_rel_steps ≥5% and stay valid will KEEP"
falsification: "If 3 rotations of prediction-consistent experiments all DISCARD, hypothesis is falsified"
age_rotations: 0
supported_keeps: 0
refuted_discards: 0
```

**Every rotation monitor health check:**

- If a team's `age_rotations ≥ 3` AND `supported_keeps == 0` AND
  `refuted_discards ≥ 3`, the hypothesis is falsified. Post
  `[HYPOTHESIS-FALSIFIED]` to the workshop. Next rotation, re-form the
  team around the leading hypothesis from whichever team landed the
  most recent KEEP (or a new hypothesis if discussion has surfaced one).
- Teams that produce KEEPs are "hot" — their supported_keeps increments
  and the queue ranker gives their subsequent proposals priority.
- Teams do NOT have axis ownership. The old "stay within your
  dimension" rule is abolished. A CPU-eval agent on H-curvature may
  claim a line-search experiment if the team's hypothesis predicts
  it will KEEP.

See `system/reference/PHASES.md` Phase 2 for the `create_team()` helper.

## What You NEVER Do

- Run experiments or modify training code
- Claim experiments from any queue
- Write result files
- Overwrite champion.md (CPU-eval agents do this on KEEP)
