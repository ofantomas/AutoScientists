# Migration: Claude Code → opencode

This document is the plan-of-record for porting AutoScientists off the
[Claude Code](https://docs.claude.com/claude-code) runtime onto
[opencode](https://opencode.ai). It is also a map of *what changed and why*, so a
future reader can understand the new shape of the system.

## Decisions

Two architectural choices were made up front:

1. **Deterministic Python orchestrator (Strategy B).** The control loop that used
   to live in `runbook.md` and be *executed by an LLM* (a Claude Code session
   wielding the `Agent`/`Task` tool) is now a real program, `orchestrator.py`. This
   matches the system's own design rule — *"THE ORCHESTRATOR IS A PURE COORDINATOR.
   IT NEVER RUNS EXPERIMENTS"* — and gives precise control over background
   processes, GPU pinning, parallelism, timeouts, and promise harvesting that an
   LLM-driven `task` tool cannot.
2. **Full replacement.** opencode is the only supported agent runtime. Claude Code
   references are removed rather than abstracted behind a flag.

The **agents themselves remain LLM sessions** — now `opencode run` instead of
`claude -p`. Each still reads its `HEARTBEAT.md` and discovers everything from
files + the ClawInstitute API.

## What does NOT change

The entire **coordination layer** — workshops, workspaces, posts, queues, champion
promotion — is plain Python over the ClawInstitute HTTP API (`requests`). It is
runtime-agnostic and is untouched. `requirements.txt` is unchanged.

## The coupling surface (what the rewrite touches)

| Claude Code coupling | Where it lived | opencode replacement |
|---|---|---|
| `claude -p "<prompt>" --dangerously-skip-permissions` | AGENT-SETUP.md, README, launch.py | `opencode run "<prompt>" --dangerously-skip-permissions` |
| `Agent(...)` / `Task(subagent_type=...)` spawning | runbook.md, all 3 LAUNCH.md | `system/runtime.py:spawn_agent()` → `opencode run` subprocess |
| `run_in_background=True` | runbook.md, LAUNCH.md | `background=True` → `subprocess.Popen` |
| `model="sonnet"` / `model="opus"` | runbook.md, HEARTBEAT note | `--model anthropic/claude-sonnet-4-6` / `-opus-4-8` (via `MODEL_MAP`) |
| `<promise>…</promise>` harvest | HEARTBEAT Part 6e, runbook 5b | sentinel scanned out of `opencode run` stdout by `AgentHandle` |
| `CLAUDE.md` ↔ `AGENT.md` analogy | AGENT-SETUP.md | `AGENTS.md` (opencode convention); `AGENT.md` kept as the per-agent identity file |

## New / changed files

| File | Status | Role |
|---|---|---|
| `system/runtime.py` | **new** | The single chokepoint that launches an agent via `opencode run`. Model mapping, per-agent `CUDA_VISIBLE_DEVICES`, background/foreground, promise harvesting, raw-log capture. **Only file that names `opencode`.** |
| `orchestrator.py` | **new** | Deterministic port of `runbook.md` Steps 0–6. Holds shared state, API helpers, the spawn primitive, and the execution loop. Imports task hooks from `task-profile.py`. |
| `task-*/profile.py` | **new** | The 13 `runbook.md` hooks as importable Python functions (`bootstrap_extras`, `gpu_dispatch`, `champion_promotion`, `exit_condition`, …). One per task family, resolved by the same walk-up `launch.py` uses for `LAUNCH.md`. |
| `opencode.json` | **new** | opencode project config: Anthropic provider, model defaults, permission policy. Copied into each run dir. |
| `launch.py` | edited | Copies `orchestrator.py` + `system/runtime.py` + resolved `profile.py` (as `task-profile.py`) and `opencode.json` into the run dir; footer now prints `python orchestrator.py <run-dir>`. |
| `runbook.md` | annotated | Retained as the **design spec** behind `orchestrator.py`, not as an executed program. |
| `task-*/LAUNCH.md` | retained | Human-readable task profile docs. Their *executable* hook logic now lives in the sibling `profile.py`. |
| `system/templates/HEARTBEAT.md` | edited | Model-choice note reworded to opencode model IDs. `<promise>` tag kept (now the orchestrator's harvest sentinel). |
| `system/templates/ROLE-ANALYST.md` | edited | Haiku anecdote reworded; no functional change (speaks HTTP API). |
| `system/reference/AGENT-SETUP.md` | edited | "How to launch an agent" block → `opencode run`; CLAUDE.md↔AGENT.md table updated. |
| `system/reference/LOGGING.md` | edited | Raw-log capture example → `opencode run`. |
| `README.md` | edited | Prerequisite + run commands → opencode. |

## Orchestrator ⇄ profile contract

`orchestrator.py` constructs an `Orchestrator` object carrying all shared state
(`API`, `HEADERS`, `WORKSHOP`, `WS_ID`, `task_md`, `PREFIX`, `gpu_agents`,
`analysts`, `parse_fm`, a `spawn()` method, and a free-form `state` dict). Each
profile hook is a module-level function taking that object:

```python
def bootstrap_extras(orch): ...            # populate orch.state (deadline clock, budget, …)
def discussion_policy(orch): ...           # returns ("run"|"skip"|"parallel", extra_instructions)
def seeding_policy(orch, teams): ...
def pre_cycle_check(orch) -> bool: ...      # True ⇒ exit after this cycle
def analyst_prompt_extras(orch) -> str: ...
def gpu_dispatch(orch, teams): ...          # the biggest per-task variation
def champion_promotion(orch, teams): ...
def stagnation_response(orch, cycle): ...
def periodic_hooks(orch, cycle): ...
def exit_condition(orch) -> bool: ...
def final_report(orch): ...
def monitor_extra_instructions(orch) -> str: ...
```

A `DEFAULTS` table in `orchestrator.py` supplies no-op/sensible fallbacks so a
profile only overrides the hooks it cares about.

## Model IDs

Code keeps speaking `"sonnet"` / `"opus"`; `system/runtime.py:MODEL_MAP` resolves
those to opencode `provider/model` strings:

- `sonnet` → `anthropic/claude-sonnet-4-6`
- `opus`   → `anthropic/claude-opus-4-8`

Override per-deployment with `AUTOSCI_MODEL_SONNET` / `AUTOSCI_MODEL_OPUS`. Verify
the exact IDs available to you with `opencode models anthropic`.

## Build order & validation gates

1. `system/runtime.py` + single-agent smoke test (`task-autoresearch`).
2. `orchestrator.py` skeleton + `task-autoresearch/profile.py`; run bootstrap → 1 cycle.
3. Port `biomlbench` + `proteingym` profiles.
4. Doc/template cleanup.
5. Final grep gate: no `claude -p`, `Agent(`, `Task(`, `subagent_type`,
   `run_in_background`, `<promise>`-harvest-via-Task left in code paths.

**Gates:**
- *Smoke:* a single `opencode run` agent boots, reads HEARTBEAT, posts to
  ClawInstitute, emits `<promise>`, orchestrator harvests it.
- *Integration:* one full cycle (discussion → teams → propose → GPU run → champion
  promotion) on `task-autoresearch`.
- *Parity:* a biomlbench deadline run respects `TIME_REMAINING_MINUTES` and
  emergency-submits.

## Setup delta for operators

```bash
# Was: install the Claude Code CLI (`claude`)
# Now: install opencode
npm install -g opencode-ai      # or: curl -fsSL https://opencode.ai/install | bash

export ANTHROPIC_API_KEY=...     # or `opencode auth login`
npx clawinstitute start          # unchanged
pip install -r requirements.txt  # unchanged
```

Run an experiment:

```bash
python3 launch.py my-run --task task-autoresearch   # unchanged
python3 orchestrator.py ../my-run                    # was: claude -p "Read runbook.md and execute"
```
