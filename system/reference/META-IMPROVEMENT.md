---
name: meta-improvement
description: Guide for the orchestrator to critically review and improve the multi-agent system
---

# Meta-Improvement

Every 3 execution cycles, the orchestrator pauses to critically examine how the agent team is operating and makes one concrete improvement. This is not an automated diagnostic — it requires genuine judgment about what is and isn't working.

## What This Is Not

Meta-improvement is not:
- Running a script and applying whatever it suggests
- Writing a report or checklist
- Checking boxes or logging observations without changing anything
- Declaring "system operating normally" without evidence

If you finish this step and no file has changed, you did not do meta-improvement.

## The Core Question

**"Is the team making the best possible use of its time and knowledge?"**

If the answer is anything other than a confident yes, find the most significant gap and fix it.

---

## Step 1 — Read the Evidence

Before forming any opinion, read what actually happened:

- `logs/experiments.jsonl` and individual `agents/*/cycle_result.json` files — what did agents try, what were the outcomes, is the metric improving or flat?
- `logs/sessions.jsonl` — did all agents complete? did any time out or fail silently?
- Each team's `queue.md` — are there pending experiments? are queues going empty? are the same ideas appearing repeatedly?
- Workshop posts — are agents posting substantive [RESULT] and [SUGGESTION] content, or formulaic boilerplate? are ideas being picked up across teams?
- `champion/SOURCE` — when did the champion last improve? how many cycles ago?
- `system/templates/ROLE-ANALYST.md` and `ROLE-CPU.md` — what are agents actually being asked to do?
- `agents/{name}/HEARTBEAT.md` — what agents were **actually** told. This is the compiled copy an
  agent reads; the templates above are only its source. If the two disagree, an earlier pass edited
  a template and never regenerated the heartbeats, so the heartbeat is what ran. Diagnose against
  the heartbeat, not against the template you wish had been in effect.

Read the real files. Do not rely on memory or assumptions.

---

## Step 2 — Form a Diagnosis

Identify the single most significant dysfunction. Be specific and honest. Some things to watch for:

**Exploration problems**
- Analysts keep proposing small variations on the same idea — no diversity
- Agents are ignoring the hardest cases and only working on easy wins
- The same dead ends are being re-attempted by different agents

**Knowledge sharing problems**
- A KEEP happened several cycles ago but other agents haven't built on it
- [RESULT] posts are too thin to be useful — no mechanism explanation, no suggested follow-ups
- Teams are working in isolation when they should be learning from each other

**Pipeline problems**
- CPU-eval agents are waiting for work because analyst queues ran dry
- Experiments are being proposed that are obviously redundant across teams
- Agents are spending time on infrastructure issues rather than experiments

**Quality problems**
- KEEP rate has been zero for many cycles — proposals are consistently weak
- Proposals lack a clear hypothesis about why they should improve the metric
- Agents aren't incorporating lessons from past failures

**Protocol problems**
- Agents are skipping steps in their role docs (often because instructions are unclear or too long)
- Role docs have accumulated contradictory or outdated instructions from past edits
- An earlier meta-improvement added something that turned out to add friction without benefit

Name the problem specifically. "Coordination seems weak" is not a diagnosis. "Team B has had 6 consecutive DISCARDs and has not looked at Team A's two recent KEEPs" is a diagnosis.

---

## Step 3 — Make One Targeted Change

Based on your diagnosis, make the most direct fix you can. Edit the relevant file — don't describe the change you would make, make it.

**What you can change:**
- `system/templates/ROLE-ANALYST.md` — how analysts propose and prioritize experiments
- `system/templates/ROLE-CPU.md` — how compute agents run experiments and share results
- `system/templates/ROLE-MONITOR.md` — what the monitor checks, reports, and repairs
- `system/templates/ROLE-TEAM.md` — the shared team protocol every non-monitor agent is given
  *(all four are compiled into the heartbeats — an edit is not live until the heartbeats are
  regenerated; see below. This is the full set of role docs `launch.py` inlines, so it is also the
  full set of edits that need regeneration.)*
- `system/reference/*.md` — coordination protocols and shared guidelines
- Team `queue.md` files directly — seed experiments if queues are empty

**What you cannot change:**
- `task/TASK.md` — the task definition
- The task-contract portions of `task/LAUNCH.md` — the metric contract, the validity gate, the
  promotion gates and their margins, and the anti-cheating rules
- Agent workspaces (`agents/*/workspace/`)
- Logs (`logs/`)
- Champion code (`champion/`)
- Run metadata (`WORKSPACE_ID`, `run_metadata.json`)

The task definition, metric contract, validity gate, promotion gates and anti-cheating rules are
FROZEN for the duration of the run. Never edit them. If you believe the task itself is wrong, say
so in your meta report and stop — changing what you are scored against invalidates every result in
the run.

*(Deliberate scope change, 2026-07-28: `task/TASK.md` and `task/LAUNCH.md` used to be listed above
as things a meta pass may edit "for task clarity, hints, evaluation guidance". They are not, under
this run's no-cheating contract — a pass that softens the gate it is measured by can manufacture an
improvement without improving anything. Recorded here so a later pass reads the tightening as
intended, not as drift, and does not restore the old permission.)*

**Principles for a good change:**
- It addresses the root cause, not a symptom
- It is specific — an agent reading the updated file will behave differently in a concrete way
- It does not add unnecessary steps or complexity
- If it fixes something, consider whether an earlier instruction caused the problem and remove it

One change only. If you see multiple problems, fix the most important one. Bundling changes makes it impossible to know what worked.

### A role-template edit is not live until heartbeats are regenerated

`launch.py` compiles `system/templates/HEARTBEAT.md` plus the matching role doc (`ROLE-CPU.md` /
`ROLE-ANALYST.md` / `ROLE-MONITOR.md`) and `ROLE-TEAM.md` into each agent's
`agents/{name}/HEARTBEAT.md` **once, at launch**. Agents read only that compiled copy — they never
open `system/templates/`. So editing a file under `system/templates/` changes NOTHING about how
agents behave for the rest of the run: every agent keeps following the old text while the meta log
records the change as `applied`.

That is the most expensive way to waste a meta pass. You get a plausible fix, zero behaviour change,
and a diagnosis that looks already-addressed to the next pass — which then goes looking for a
different problem.

**If your change touched any file under `system/templates/`, regenerating the heartbeats is part of
the change, not an optional follow-up.** Run this from the run directory (`FOCUS_ROOT`), after the
edit and before the next dispatch:

```bash
TEMPLATE=$(python3 -c 'import json; print(json.load(open("run_metadata.json"))["template"])')
python3 "$TEMPLATE/launch.py" --regenerate-heartbeats .
```

- It rebuilds `agents/*/HEARTBEAT.md` from **this run's own** `system/templates/` — the copy you
  just edited — and touches nothing else: no agent registration, no workspace writes, no kickoff,
  no re-scaffolding.
- If `run_metadata.json`'s `template` path no longer exists (the template checkout moved), any copy
  of `launch.py` will do: it supplies only the code, and the text it inlines comes from the run
  directory you point it at, never from the checkout it happens to live in.
- Exit status 0 means every agent is current. Non-zero means the regeneration did not complete
  cleanly — either an agent was skipped (it is still carrying the old text) or the run directory
  was not readable (wrong directory, or no `agents/` in it). Read the printed reason and resolve it
  before dispatching; do not assume which of the two it was.
- It takes effect on the NEXT dispatch. An agent already running keeps the copy it read when it was
  spawned, so regenerate between dispatches — never mid-cycle.
- It OVERWRITES each `agents/*/HEARTBEAT.md`. Never hand-edit one agent's HEARTBEAT.md: that edit is
  silently discarded at the next regeneration, and it reaches one agent instead of every agent in
  the role. Edit the role template and regenerate.

Regeneration is not a second change and does not breach the one-change-per-pass rule — it is how the
one change is delivered. The `agents/*/HEARTBEAT.md` files it rewrites are build output, not
authored files, so a scope limit worded as "ONE file, ONE targeted edit" (as your task profile's
meta hook may word it) is not violated by running it: you still made exactly one authored edit, and
the command is what makes that edit real. A pass that skips regeneration to stay inside a literal
file count has made zero changes, not one. Files agents read by path at runtime
(`system/reference/*.md`, team `queue.md` files) need no regeneration; those take effect the moment
you save them.

---

## Step 4 — Log What You Did and Why

Append a brief entry to `logs/meta_results.tsv`:

```
cycle   pattern_diagnosed           file_changed                    outcome
6       low_keep_rate               system/templates/ROLE-ANALYST.md applied
9       queue_empty                 agents/team_a/workspace/...     seeded_queue
12      duplicate_proposals         system/templates/ROLE-ANALYST.md applied
15      role_doc_contradiction      system/templates/ROLE-CPU.md     applied
```

Include a one-line note on what you changed and why. This record lets future meta-improvement steps
see what was already tried.

For an edit under `system/templates/`, `applied` means *edited AND regenerated*: log it only after
the regeneration command has run and reported every agent current. If you made the edit but could
not regenerate, log `applied_not_propagated` with the reason instead. Those are different states —
one is live, the other is a file change no agent has seen — and a future pass reading `applied`
will assume the agents already got it.

---

## Judgment Heuristics

**If the champion hasn't improved in 5+ cycles:** The team is stuck. Look at whether proposals are genuinely diverse or converging on a local optimum. Consider whether the role docs are steering agents away from high-risk/high-reward ideas.

**If queues are repeatedly going empty:** Analysts aren't keeping up with CPU-eval agents. Either increase proposal diversity expectations, or check whether analysts are getting stuck on infrastructure issues.

**If KEEP rate is high but metric gain per KEEP is small:** The team is making incremental progress but not exploring enough. The role docs may be too conservative — penalizing bold proposals.

**If agents are not building on each other's KEEPs:** Look at the [RESULT] post format. If posts don't include mechanism explanations and suggested follow-ups, other agents have nothing to build on.

**If the same experiment appears across multiple teams:** The deduplication guidance in ROLE-ANALYST.md is either missing or not being followed. Check whether it's clear and early in the instructions.

**If a recent meta-improvement made things worse:** Revert it. Read the current role doc, find the change you made, remove it, and try something different. A revert of a role-template edit is itself a role-template edit — regenerate the heartbeats afterwards, or the revert is exactly as invisible as an unpropagated fix.

**If a change you made had no observable effect at all:** Before concluding the idea was wrong, check whether it ever reached the agents. Compare the file you edited against `agents/{name}/HEARTBEAT.md` for an affected role. If your text is missing from the heartbeat, the experiment never ran — regenerate and give it a fair cycle before discarding it.

---

## Documented Patterns From Past Runs

### Task Specification Drift (2026-04-03)

All CPU-eval agents used the wrong data split because example code in TASK.md contradicted the prose. Agents trusted the example over the text.

**Lesson:** When agents systematically make the same mistake, look for the instruction that is ambiguous or contradictory, and fix the source rather than the symptom. Note the boundary: the task definition is frozen, so the fix goes in the role docs — clarify there how agents are to read the task. If the ambiguity is genuinely inside the frozen task files, report it and stop; do not edit them.

### Orchestrator Autonomy Failure (2026-04-03)

Orchestrator paused after cycle 1 and asked "can you continue?" despite being told not to stop.

**Lesson:** Weak directives ("do not pause") are ignored. Strong prohibitions ("NEVER ASK PERMISSION — if you find yourself typing this, just continue") work better.

### Meta-Improvement Writing Reports Instead of Making Changes (2026-04-05)

After hundreds of cycles, meta_improvement/ contained thousands of identical report files. No role doc had ever been changed. The step ran but did nothing.

**Lesson:** The step is only complete when a file is different than it was before you started. If nothing changed, you did not do meta-improvement.

### Role-Template Edits That Never Reached the Agents (2026-07-28)

Meta passes edited `system/templates/ROLE-*.md` and logged `applied`, but the role docs are compiled
into `agents/*/HEARTBEAT.md` at launch and nothing rebuilt them. Agents ran the launch-time text for
the whole run. Every such pass was a no-op, and because the log said `applied`, later passes treated
those diagnoses as already handled and moved on to lesser problems.

**Lesson:** A file edit is not a change until the thing that reads the file sees it. Know which of
your levers are read live and which are compiled: `system/reference/*.md` and team `queue.md` are
live, `system/templates/*` is compiled. For anything compiled, propagation
(`launch.py --regenerate-heartbeats .`) is part of the change, and the log entry is only honest
after propagation succeeds.
