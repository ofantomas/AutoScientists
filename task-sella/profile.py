"""profile.py — molopt-relax-steps / sella (task_type: optimization).

Python port of task-sella/LAUNCH.md. Open-ended optimization of a compact
molecular-geometry optimizer (`algo.py`): drive `fitness` (= `mean_rel_steps`,
lower is better) down indefinitely, subject to a hard per-molecule energy
validity gate.

CPU-only deterministic evaluation: candidates are scored by a remote Redis-backed
distributed validation worker pool (no GPU, no CUDA, no nvidia-smi). Concurrency
is bounded by the worker pool, NOT by any device — all eval agents dispatch
concurrently. Evaluation is deterministic, so there is no multi-seed / noise-floor
machinery.

Eval agents are the CPU-eval role; the orchestrator exposes them as
``orch.eval_agents`` (also aliased ``orch.cpu_agents`` / ``orch.gpu_agents``).
The orchestrator calls ``cpu_dispatch`` (not ``gpu_dispatch``) because this module
defines it. See orchestrator.py DEFAULTS for hooks this profile does not override.
"""

import json
from datetime import datetime, timezone

from runtime import wait_all   # system/ is on sys.path (orchestrator inserts it)

DIRECTION = "minimize"        # fitness / mean_rel_steps: lower is better
KEEP_MARGIN = 1e-3            # promote only on a real margin in mean_rel_steps
INVALID_SENTINEL = 1000.0     # eval_candidate.py fitness value for invalid candidates


def discussion_policy(orch):
    # Cold-start fast path: do not block on a standalone discussion round. The
    # [DISCUSSION-TRIGGER] kickoff post lets agents self-regroup via HEARTBEAT
    # Part 0 while the first candidate is dispatched to the eval pool.
    return ("parallel", "")


def seeding_policy(orch, teams):
    """Orchestrator-seeded: one high-priority proposal per team so eval agents
    have differentiated work on cycle 1. Seeds propose STRUCTURAL changes to
    algo.py (step control / trust region, Hessian init & update, line search /
    step acceptance, convergence criterion) — not fine retuning of one constant.
    The cold-start minimal seed is 'rerun the champion algo.py for sanity';
    analysts replace it with real structural proposals during the first cycle."""
    for team_name, team_info in teams.items():
        ws = team_info["workspace_id"]
        exp_id = f"seed_{team_name}"
        orch.post(
            title=f"[PROPOSAL] {exp_id}: baseline sanity eval ({team_name})",
            content=(f"## Mechanism\nEstablish this team's starting point by evaluating the "
                     f"current champion algo.py unchanged, then iterate on a STRUCTURAL axis "
                     f"(step control / trust region, Hessian init & update, line search / "
                     f"step acceptance, convergence criterion).\n\n## Team\n{team_name}"),
            notify_agents=team_info.get("members", []),
            tags=[f"team:{team_name}", "type:proposal"],
        )
        queue = (f"---\nclaims: {{}}\npending:\n"
                 f"  - id: {exp_id}\n"
                 f"    description: \"baseline sanity eval\"\n"
                 f"    priority: high\n"
                 f"---\n\n# Experiment Queue\n")
        orch.put_file("queue.md", queue, ws_id=ws)
        print(f"  Seeded {team_name} queue with {exp_id}")


def cpu_dispatch(orch, teams):
    """CPU-eval dispatch. NO GPUs, NO CUDA, NO device pinning. Candidates are
    scored on a remote worker pool; concurrency is bounded by that pool, so all
    eval agents may run concurrently. Each candidate algo.py is scp'd to a unique
    remote path by the agent (see ROLE-CPU.md), so concurrent evals never collide."""
    handles = [orch.spawn(a, mode="execute", model="sonnet", background=True)
               for a in orch.eval_agents]
    wait_all(handles, timeout=60 * 60)
    for h in handles:
        orch.harvest_session(h, role="cpu")


def _read_agent_result(orch, agent_name):
    """Return the parsed result_latest.json dict for an eval agent, or None."""
    p = orch.FOCUS_ROOT / "agents" / agent_name / "workspace" / "result_latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def champion_promotion(orch, teams):
    """Champion = best algo.py for `fitness` (= mean_rel_steps, lower is better).

    Deterministic, strict, with a seeding exception: because the baseline algo.py
    may itself be INVALID under the energy gate, the first VALID candidate seeds
    the champion regardless of fitness; thereafter only strictly-better VALID
    candidates (by >= KEEP_MARGIN mean_rel_steps) are promoted. Eval is
    deterministic — no multi-seed gate, no noise band."""
    import shutil
    root = orch.FOCUS_ROOT

    prev_fm = orch.parse_fm(orch.get_file("champion.md"))
    champion_fitness = prev_fm.get("metric_value")   # None if no VALID champion yet

    # Best VALID candidate this cycle.
    best = None   # (fitness, agent_name, exp_id, algo_path, result)
    for agent_name in orch.eval_agents:
        r = _read_agent_result(orch, agent_name)
        if not r:
            continue
        if int(r.get("is_valid", 0)) != 1:
            continue
        fitness = r.get("fitness", r.get("mean_rel_steps"))
        if fitness is None:
            continue
        fitness = float(fitness)
        if fitness >= INVALID_SENTINEL:
            continue
        if best is None or fitness < best[0]:
            exp_id = r.get("exp_id", "cand")
            algo = root / "agents" / agent_name / "workspace" / "repo" / f"algo_{exp_id}.py"
            if not algo.exists():
                algo = root / "agents" / agent_name / "workspace" / "repo" / "algo.py"
            best = (fitness, agent_name, exp_id, algo, r)

    if best is None:
        print("  No VALID candidate this cycle — champion unchanged.")
        return

    cand_fitness, agent_name, exp_id, algo, _r = best
    have_champion = champion_fitness is not None
    keep = (not have_champion) or (float(champion_fitness) - cand_fitness) >= KEEP_MARGIN
    if not keep:
        print(f"  No improvement (cand={cand_fitness}, champion={champion_fitness}, "
              f"margin<{KEEP_MARGIN}) — champion unchanged.")
        return

    champ_dir = root / "champion"
    champ_dir.mkdir(exist_ok=True)
    if algo.exists():
        shutil.copy(algo, champ_dir / "algo.py")
    (champ_dir / "SOURCE").write_text(
        f"{agent_name} {exp_id} fitness={cand_fitness} {datetime.now(timezone.utc).isoformat()}\n")
    orch.put_file("champion.md", (
        f"---\nmetric_name: fitness\nmetric_value: {cand_fitness}\ndirection: minimize\n"
        f"agent: {agent_name}\nexp_id: {exp_id}\ncycle: {orch.cycle_count}\n"
        f"timestamp: {datetime.now(timezone.utc).isoformat()}\n---\n\n"
        f"# Champion (cycle {orch.cycle_count})\n\n"
        f"- **fitness (mean_rel_steps):** {cand_fitness}\n- **Agent:** {agent_name}\n"
        f"- **Experiment:** {exp_id}\n- **Previous:** {champion_fitness}\n"
        f"- **Champion code:** `champion/algo.py`\n"))
    print(f"  Champion propagated: {agent_name} {exp_id} -> champion/algo.py "
          f"(fitness={cand_fitness}, prev={champion_fitness})")


def stagnation_response(orch, cycle_count):
    """OPEN-ENDED — do NOT stop. Trigger a discussion/regroup round instead."""
    print(f"  STAGNATION: 0 KEEPs in last 10 (cycle {cycle_count}). "
          f"Open-ended task — triggering a regroup, NOT stopping.")
    orch.post(
        title=f"[DISCUSSION] Stagnation regroup (cycle {cycle_count})",
        content=("0 KEEPs in the last 10 experiments. Re-form teams and propose NEW structural "
                 "axes for algo.py (step control / trust region, Hessian init & update, line "
                 "search / step acceptance, convergence criterion). Avoid retuning a single "
                 "constant. Seed fresh [PROPOSAL]s into the team queues."),
        tags=["type:discussion", "regroup"],
    )


def periodic_hooks(orch, cycle_count):
    """Meta-improvement is judgment-based (see system/reference/META-IMPROVEMENT.md):
    read evidence, make ONE targeted file edit, append to logs/meta_results.tsv.
    That requires LLM judgment the deterministic orchestrator does not exercise, so
    here it is a no-op marker. NEVER import meta_diagnostics (not shipped);
    `python3 task/meta_diagnostics.py` may be run by hand for hard signals."""
    if cycle_count % 3 == 0:
        print(f"  [meta] cycle {cycle_count}: meta-improvement is judgment-based "
              f"(see system/reference/META-IMPROVEMENT.md) — not auto-applied.")
    return None


def final_report(orch):
    print("\n" + "=" * 60)
    print("  OPTIMIZATION RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:    {orch.task_name}")
    print(f"  Cycles:  {orch.cycle_count}")
    src = orch.FOCUS_ROOT / "champion" / "SOURCE"
    if src.exists():
        print(f"  Champion: {src.read_text().strip()}")
    print(f"  Code:    {orch.FOCUS_ROOT / 'champion' / 'algo.py'}")
    print("=" * 60)
