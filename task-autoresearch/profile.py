"""profile.py — autoresearch (task_type: optimization).

Python port of task-autoresearch/LAUNCH.md. Fills the runbook hooks for
open-ended nanoGPT ``val_bpb`` minimisation. 2x H100, GPU agents run one-per-GPU
(up to two concurrent), mandatory meta-improvement every 3 cycles, no deadline.

Each hook takes the Orchestrator instance ``orch`` (see MIGRATION.md for the
contract) and reaches shared state through it. See orchestrator.py DEFAULTS for
hooks this profile does not override.
"""

import json
import shutil
from datetime import datetime, timezone

from runtime import wait_all  # system/ is on sys.path (orchestrator inserts it)

DIRECTION = "minimize"   # val_bpb: lower is better


def discussion_policy(orch):
    # Cold-start fast path: do not block on a standalone discussion round; the
    # [DISCUSSION-TRIGGER] kickoff post lets agents self-regroup via HEARTBEAT
    # Part 0 while the first GPU dispatch proceeds.
    return ("parallel", "")


def seeding_policy(orch, teams):
    """Orchestrator-seeded: one high-priority proposal per team so GPU agents
    have differentiated work on cycle 1. On cold start the minimal safe seed is
    'rerun the champion baseline for sanity' — analysts replace it with real
    proposals during the first cycle."""
    for team_name, team_info in teams.items():
        ws = team_info["workspace_id"]
        exp_id = f"seed_{team_name}"
        orch.post(
            title=f"[PROPOSAL] {exp_id}: baseline sanity rerun ({team_name})",
            content=(f"## Mechanism\nEstablish this team's starting point by rerunning the "
                     f"current champion train.py unchanged, then iterate.\n\n## Team\n{team_name}"),
            notify_agents=team_info.get("members", []),
            tags=[f"team:{team_name}", "type:proposal"],
        )
        queue = (f"---\nclaims: {{}}\npending:\n"
                 f"  - id: {exp_id}\n"
                 f"    description: \"baseline sanity rerun\"\n"
                 f"    priority: high\n"
                 f"---\n\n# Experiment Queue\n")
        orch.put_file("queue.md", queue, ws_id=ws)
        print(f"  Seeded {team_name} queue with {exp_id}")


def gpu_dispatch(orch, teams):
    """2x H100. Up to two GPU agents concurrent — one pinned to CUDA 0, one to 1.
    NEVER two on the same device. Launch in pairs, wait for the pair, then next."""
    pair = []
    for i, agent_name in enumerate(orch.gpu_agents):
        cuda = "0" if i % 2 == 0 else "1"
        h = orch.spawn(agent_name, mode="execute", model="sonnet",
                       cuda=cuda, background=True)
        pair.append((h, cuda))
        # Once we have one agent on each device, wait for both before continuing.
        if len(pair) == 2:
            wait_all([p[0] for p in pair], timeout=60 * 60)
            for hh, _ in pair:
                orch.harvest_session(hh, role="gpu")
            pair = []
    if pair:
        wait_all([p[0] for p in pair], timeout=60 * 60)
        for hh, _ in pair:
            orch.harvest_session(hh, role="gpu")


def _read_agent_result(orch, agent_name):
    """Return (outcome, metric, train_path) for an agent's last experiment, or
    (None, None, None). Optimization GPU agents write cycle_result.json;
    result_latest.json is also accepted for cross-task robustness."""
    base = orch.FOCUS_ROOT / "agents" / agent_name
    for rel in ("cycle_result.json", "workspace/result_latest.json"):
        p = base / rel
        if p.exists():
            try:
                r = json.loads(p.read_text())
            except Exception:
                continue
            outcome = r.get("outcome")
            metric = r.get("metric", r.get("val_bpb", r.get("val_score")))
            train_path = base / "workspace" / "repo" / "train.py"
            return outcome, metric, train_path
    return None, None, None


def champion_promotion(orch, teams):
    """Champion = best train.py for val_bpb. Copy the winning agent's train.py to
    champion/ on a KEEP. This is the SINGLE source of truth for champion code."""
    best_metric, best_agent, best_train = None, None, None
    for agent_name in orch.gpu_agents:
        outcome, metric, train_path = _read_agent_result(orch, agent_name)
        if outcome != "KEEP" or metric is None or not train_path.exists():
            continue
        if best_metric is None or metric < best_metric:   # minimize
            best_metric, best_agent, best_train = metric, agent_name, train_path

    if best_train is None:
        print("  No KEEP this cycle — champion unchanged.")
        return

    champ_dir = orch.FOCUS_ROOT / "champion"
    champ_dir.mkdir(exist_ok=True)
    shutil.copy(best_train, champ_dir / "train.py")
    (champ_dir / "SOURCE").write_text(
        f"{best_agent} val_bpb={best_metric} {datetime.now(timezone.utc).isoformat()}\n")
    print(f"  Champion propagated: {best_agent} -> champion/train.py (val_bpb={best_metric})")


def stagnation_response(orch, cycle_count):
    """Stagnation is a real exit condition for open-ended optimization — stop."""
    print(f"  STAGNATION: 0 KEEPs in last 10 experiments (cycle {cycle_count}).")
    print("  Stopping loop — wait for user input.")
    raise SystemExit(0)


def periodic_hooks(orch, cycle_count):
    """Mandatory meta-improvement every 3 cycles (best-effort: requires the
    optional scripts/meta_diagnostics module; degrades to a notice if absent)."""
    if cycle_count % 3 != 0:
        return
    print(f"\n{'=' * 60}\nMETA-IMPROVEMENT (cycle {cycle_count})\n{'=' * 60}")
    try:
        import sys
        sys.path.insert(0, str(orch.FOCUS_ROOT / "scripts"))
        from meta_diagnostics import analyze_experiments, print_diagnostics  # type: ignore
    except Exception as e:
        print(f"  meta_diagnostics unavailable ({e}); skipping meta-improvement this cycle.")
        return
    diagnostics = analyze_experiments(
        experiments_file=orch.FOCUS_ROOT / "logs" / "experiments.jsonl",
        last_n=30, num_analysts=3)
    print_diagnostics(diagnostics)
    # (The role-template auto-edit step from LAUNCH.md can be added here once the
    # diagnostics module ships; it is intentionally conservative — one edit max.)


def final_report(orch):
    print("\n" + "=" * 60)
    print("  OPTIMIZATION RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:    {orch.task_name}")
    print(f"  Cycles:  {orch.cycle_count}")
    src = orch.FOCUS_ROOT / "champion" / "SOURCE"
    if src.exists():
        print(f"  Champion: {src.read_text().strip()}")
    print(f"  Code:    {orch.FOCUS_ROOT / 'champion' / 'train.py'}")
    print("=" * 60)
