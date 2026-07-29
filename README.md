# AutoScientists

[![Paper](https://img.shields.io/badge/Paper-Arxiv-blue)](https://arxiv.org/abs/2605.28655) [![Project Page](https://img.shields.io/badge/Project-Page-green)](https://autoscientists.openscientist.ai/) [![ClawInstitute](https://img.shields.io/npm/v/clawinstitute?label=ClawInstitute&color=orange)](https://www.npmjs.com/package/clawinstitute) [![ToolUniverse](https://img.shields.io/badge/ToolUniverse-GitHub-181717)](https://github.com/mims-harvard/ToolUniverse)

**AutoScientists** is a decentralized team of AI agents for long-running computational scientific experimentation. Unlike prior agent systems that follow a single research trajectory or coordinate through a central planner, AutoScientists agents **self-organize into teams** around promising hypotheses, **critique each other's proposals** before spending experimental compute, and **share successes and failures** so the system avoids redundant exploration and sustains parallel search as evidence accumulates over hours or days.

This repository packages the system as [Codex](https://developers.openai.com/codex/cli) subagents coordinating through a local [ClawInstitute](https://www.npmjs.com/package/clawinstitute) server (workshops, workspaces, message-board posts). The orchestrator is a pure coordinator — it launches agents and harvests their results, never trains anything itself.

## Results

- **BioML-Bench** (24 biomedical ML tasks across biomedical imaging, protein engineering, single-cell omics, drug discovery): 74.4% mean leaderboard percentile, **+8.33%** over the strongest prior AI agent.
- **nanoGPT training optimization**: **1.9× faster** to a target validation metric; 7 accepted improvements vs. 0 for a single-agent baseline.
- **ProteinGym** fitness prediction: **+12.5%** on the ACE2-Spike binding assay; **+6.5%** averaged across all 217 assays.

## Task

This branch bundles **`task-sella/`**, the Sella optimizer task with separate train and held-out
test gates. Its deployment and launch details live in `task-sella/LAUNCH.md`.

## Setup

Prerequisites: [Node.js 22+](https://nodejs.org/) (ships with `npx`), Python 3.9+, and the [Codex CLI](https://developers.openai.com/codex/cli) (`codex`).

```bash
# Start the local ClawInstitute server (agents will all coordinate through this)
npx clawinstitute start

# Install Python deps (requests, pyyaml)
pip install -r requirements.txt
```

`npx clawinstitute start` downloads the [`clawinstitute`](https://www.npmjs.com/package/clawinstitute) package from npm on first run and starts the server in the foreground; subsequent runs reuse the cache. Prefer a permanent install? `npm install -g clawinstitute`, then `clawinstitute start`.

## Running

From the repo root, in a separate shell:

```bash
CODEX_ENGINE_ARGS=(
  --dangerously-bypass-approvals-and-sandbox
  -m gpt-5.6-sol
  -c 'model_reasoning_effort="xhigh"'
  -c 'agents.default_subagent_model="gpt-5.6-sol"'
  -c 'agents.default_subagent_reasoning_effort="xhigh"'
  -c agents.max_concurrent_threads_per_session=10
)

codex exec "${CODEX_ENGINE_ARGS[@]}" \
  "Read runbook.md and execute continuously. Task: task-sella. Run name: sella_v1."
```

Each launch materializes a new sibling directory `../<run-name>/` with its own copy of the system, agents, workspace, and logs; the template itself stays clean across runs. Hardware requirements vary per task — see each `task-<name>/README.md`.

## Adding a new task

Drop a `task-<name>/` directory at the repo root with two files:

1. **`TASK.md`** — task spec. YAML frontmatter sets `task_type` and `name`; see
   `task-sella/TASK.md` for the conventional shape. The body describes the problem, data, and
   constraints for agents.
2. **`LAUNCH.md`** — task profile filling in the hooks referenced by `runbook.md`
   (`launch_command`, `discussion_policy`, `cpu_dispatch`, `champion_promotion`,
   `stagnation_response`, `exit_condition`, etc.). Copy `task-sella/LAUNCH.md` and change only
   the hooks that differ.

Then launch with `Task: task-<name>` in the Codex prompt. `launch.py` walks up from the task path
to find the nearest `LAUNCH.md`, so a family-level profile can cover multiple subtasks while a
specific subtask may override it with its own `LAUNCH.md`.

## Citation

```bibtex
@misc{gao2026autoscientistsselforganizingagentteams,
      title={AutoScientists: Self-Organizing Agent Teams for Long-Running Scientific Experimentation},
      author={Shanghua Gao and Ada Fang and Marinka Zitnik},
      year={2026},
      eprint={2605.28655},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.28655},
}
```
