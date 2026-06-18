"""profile.py — proteingym-spike (task_type: proteingym).

Python port of task-protein-gym/LAUNCH.md. Baseline-evolution: agents iteratively
improve task/repo/kermut.py. GPU agents run STRICTLY sequentially (parallel GPU
contention drops fold_contiguous_5 from 0.68 to 0.54). Each GPU agent has a
10-submission leaderboard budget; the run stops when all budgets are exhausted.

Requires env vars (set before launching the orchestrator):
  PYTHON      — absolute path to the python interpreter with the kermut env
  KERMUT_DATA — absolute path to the kermut/data directory
"""

import json
import os
from datetime import datetime, timezone

import requests

PROTEIN = "SPIKE_SARS2_Starr_2020_binding"
MAX_SUBMISSIONS_PER_AGENT = 10


# ── bootstrap: env + submission budget (persists across restarts) ────────────

def bootstrap_extras(orch):
    s = orch.state
    s["PROTEIN"] = PROTEIN
    s["KERMUT_DATA"] = os.environ.get("KERMUT_DATA", "")
    s["PYTHON"] = os.environ.get("PYTHON", "python")
    if not s["KERMUT_DATA"]:
        print("  WARNING: KERMUT_DATA env var not set — agents need it to load data.")

    budget_file = orch.FOCUS_ROOT / "logs" / "submission_budget.json"
    budget_file.parent.mkdir(parents=True, exist_ok=True)
    if budget_file.exists():
        s["budget"] = json.loads(budget_file.read_text())
        print(f"  Resuming. Budget state: {s['budget']}")
    else:
        s["budget"] = {a: 0 for a in orch.gpu_agents}
        budget_file.write_text(json.dumps(s["budget"], indent=2))
    s["budget_file"] = budget_file


def _save_budget(orch):
    orch.state["budget_file"].write_text(json.dumps(orch.state["budget"], indent=2))


def _agent_remaining(orch, agent_name):
    return MAX_SUBMISSIONS_PER_AGENT - orch.state["budget"].get(agent_name, 0)


def _total_remaining(orch):
    return sum(_agent_remaining(orch, a) for a in orch.gpu_agents)


def _all_exhausted(orch):
    return all(orch.state["budget"].get(a, 0) >= MAX_SUBMISSIONS_PER_AGENT
               for a in orch.gpu_agents)


def _sync_budget_from_results(orch):
    for agent_name in orch.gpu_agents:
        rp = orch.FOCUS_ROOT / "agents" / agent_name / "workspace" / "result_latest.json"
        if not rp.exists():
            continue
        try:
            reported = json.loads(rp.read_text()).get("num_submissions",
                                                       orch.state["budget"].get(agent_name, 0))
            if reported > orch.state["budget"].get(agent_name, 0):
                orch.state["budget"][agent_name] = reported
        except Exception:
            pass
    _save_budget(orch)


# ── discussion: lightweight, one concrete kermut.py modification per agent ───

def discussion_policy(orch):
    extra = (
        "TASK: proteingym-spike.\n"
        "BASELINE: task/repo/kermut.py — a working Kermut GP. Read it before posting.\n"
        "PRIMARY METRIC: mean_spearman (fold_contiguous_5 + fold_modulo_5 + fold_random_5).\n"
        "HARDEST SPLIT: fold_contiguous_5 — focus there first.\n\n"
        "Your [DISCUSSION] post should propose ONE concrete modification to kermut.py "
        "(e.g. mutation_pos_embeddings instead of mean-pooled, Matern vs RBF kernel, more "
        "optimisation steps, ESM2 zero-shot scores as an extra feature). Read existing "
        "[DISCUSSION] posts; do not duplicate. Be specific about which lines change.\n"
    )
    return ("run", extra)


def monitor_extra_instructions(orch):
    return (
        "TASK: proteingym-spike. Agents evolve task/repo/kermut.py — not from scratch.\n"
        "GPU agents run SEQUENTIALLY (never two simultaneously).\n\n"
        "After forming teams, seed each team's queue.md with the specific kermut.py "
        "modification that team proposed in [DISCUSSION]. Each seed: which lines change, "
        "the change, and why it should improve mean_spearman (especially fold_contiguous_5). "
        "No two teams seeded with the same modification."
    )


def seeding_policy(orch, teams):
    for team_name, team_info in teams.items():
        q_fm = orch.parse_fm(orch.get_file("queue.md", ws_id=team_info["workspace_id"]))
        if not q_fm.get("pending"):
            print(f"  WARNING: {team_name} queue empty after monitor — needs a fallback seed")


def pre_cycle_check(orch):
    _sync_budget_from_results(orch)
    rem = _total_remaining(orch)
    print(f"\n{'=' * 60}\nBUDGET CHECK: {rem} submission(s) remaining\n{'=' * 60}")
    if _all_exhausted(orch):
        print("  All agents exhausted their 10-submission budget. Stopping.")
        return True
    return False


def analyst_prompt_extras(orch):
    s = orch.state
    return (
        "TASK: proteingym-spike\n"
        "BASELINE CODE: task/repo/kermut.py — read it before proposing changes.\n"
        "PRIMARY_METRIC: mean_spearman (fold_contiguous_5 + fold_modulo_5 + fold_random_5)\n"
        f"BUDGET_REMAINING: {_total_remaining(orch)} submissions across all GPU agents\n"
        "GPU agents run SEQUENTIALLY. Analysts review leaderboard scores, read literature, "
        "and queue proposals — they do not run code.\n"
        f"KERMUT_DATA={s['KERMUT_DATA']}\n"
        "Do NOT use task/embeddings_*/ directories (mutant ordering bug).\n"
    )


# ── GPU dispatch: STRICTLY sequential ────────────────────────────────────────

def _gpu_extra(orch, remaining):
    s = orch.state
    return (
        f"TASK=proteingym-spike\nPROTEIN={PROTEIN}\n"
        f"PYTHON={s['PYTHON']}\nKERMUT_DATA={s['KERMUT_DATA']}\n"
        f"CHAMPION_CODE=task/repo/kermut.py\n"
        f"SUBMISSIONS_REMAINING={remaining}\nMAX_SUBMISSIONS_PER_AGENT={MAX_SUBMISSIONS_PER_AGENT}\n\n"
        "You are evolving task/repo/kermut.py — copy it to your workspace and modify it.\n"
        "Do NOT use task/embeddings_*/ (mutant ordering bug). Load from KERMUT_DATA h5 files.\n"
        "Runtime: ~32s per split (5 folds), ~96s for all 3 splits. Run splits SEQUENTIALLY.\n"
        "Leaderboard: proteingym-spike at clawlab-api.aiscientist.tools\n"
    )


def gpu_dispatch(orch, teams):
    for agent_name in orch.gpu_agents:
        remaining = _agent_remaining(orch, agent_name)
        if remaining <= 0:
            print(f"  Skipping {agent_name} — budget exhausted")
            continue
        print(f"  Launching {agent_name} (budget remaining: {remaining}) ...")
        h = orch.spawn(agent_name, mode="execute", model="sonnet", cuda="0",
                       extra_prompt=_gpu_extra(orch, remaining), background=False)
        orch.harvest_session(h, role="gpu")
        # Sync budget after the agent completes.
        rp = orch.FOCUS_ROOT / "agents" / agent_name / "workspace" / "result_latest.json"
        if rp.exists():
            try:
                reported = json.loads(rp.read_text()).get("num_submissions",
                                                           orch.state["budget"].get(agent_name, 0))
                if reported > orch.state["budget"].get(agent_name, 0):
                    orch.state["budget"][agent_name] = reported
                    _save_budget(orch)
            except Exception as e:
                print(f"  [{agent_name}] budget sync failed: {e}")


# ── champion: best mean_spearman; propagate code to task/repo/kermut.py ───────

def champion_promotion(orch, teams):
    import shutil
    root = orch.FOCUS_ROOT
    best_score = best_agent = best_result = None

    for agent_name in orch.gpu_agents:
        rp = root / "agents" / agent_name / "workspace" / "result_latest.json"
        if not rp.exists():
            continue
        try:
            r = json.loads(rp.read_text())
        except Exception:
            continue
        score = r.get("mean_spearman")
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_score, best_agent, best_result = score, agent_name, r

    if not best_result:
        print("  WARNING: no agent produced a result_latest.json with mean_spearman.")
        return

    prev_fm = orch.parse_fm(orch.get_file("champion.md"))
    prev_score = prev_fm.get("metric_value")
    if not (prev_score is None or best_score > prev_score):
        print(f"  No improvement (best={best_score}, prev={prev_score}) — champion unchanged")
        return

    code_file = best_result.get("code_file")
    if code_file:
        src = root / "agents" / best_agent / "workspace" / code_file
        if src.exists():
            shutil.copy(src, root / "task" / "repo" / "kermut.py")
            print(f"  Champion code propagated: {src} -> task/repo/kermut.py")

    orch.put_file("champion.md", (
        f"---\nmetric_name: mean_spearman\nmetric_value: {best_score}\ndirection: maximize\n"
        f"agent: {best_agent}\ncycle: {orch.cycle_count}\n"
        f"timestamp: {datetime.now(timezone.utc).isoformat()}\n"
        f"fold_contiguous_5: {best_result.get('fold_contiguous_5')}\n"
        f"fold_modulo_5: {best_result.get('fold_modulo_5')}\n"
        f"fold_random_5: {best_result.get('fold_random_5')}\n---\n\n"
        f"# Champion (cycle {orch.cycle_count})\n\n- **mean_spearman:** {best_score}\n"
        f"- **fold_contiguous_5:** {best_result.get('fold_contiguous_5')}\n"
        f"- **fold_modulo_5:** {best_result.get('fold_modulo_5')}\n"
        f"- **fold_random_5:** {best_result.get('fold_random_5')}\n"
        f"- **Agent:** {best_agent}\n- **Approach:** {best_result.get('approach', 'unknown')}\n"
        f"- **Previous best:** {prev_score}\n- **Champion code:** `task/repo/kermut.py`\n"))
    print(f"  Champion updated: {best_agent} mean_spearman={best_score} (prev={prev_score})")


def stagnation_response(orch, cycle_count):
    orch.post(
        title=f"[STUCK] Cycle {cycle_count}: no improvements in last 10 experiments",
        content=(f"No improvements to mean_spearman recently. Budget remaining: "
                 f"{_total_remaining(orch)} submissions.\n\nAnalysts: propose modifications "
                 f"to kermut.py from a different angle (kernel, embedding source, feature set)."),
        notify_agents=orch.analysts,
        tags=["type:stuck", f"cycle:{cycle_count}"],
    )


def exit_condition(orch):
    return _all_exhausted(orch)


def final_report(orch):
    champ = orch.parse_fm(orch.get_file("champion.md"))
    used = {a: orch.state["budget"].get(a, 0) for a in orch.gpu_agents}
    total_used = sum(used.values())
    print("\n" + "=" * 60)
    print("  PROTEINGYM-SPIKE RUN COMPLETE")
    print("=" * 60)
    print(f"  Total cycles:       {orch.cycle_count}")
    print(f"  Submissions used:   {total_used} / {len(orch.gpu_agents) * MAX_SUBMISSIONS_PER_AGENT}")
    for a in orch.gpu_agents:
        print(f"    {a}: {used[a]}/{MAX_SUBMISSIONS_PER_AGENT}")
    print(f"  Best mean_spearman: {champ.get('metric_value', 'none')}")
    print(f"    fold_contiguous_5: {champ.get('fold_contiguous_5', 'none')}")
    print(f"    fold_modulo_5:     {champ.get('fold_modulo_5', 'none')}")
    print(f"    fold_random_5:     {champ.get('fold_random_5', 'none')}")
    print(f"  Best agent:         {champ.get('agent', 'none')}")
    print(f"  Champion code:      task/repo/kermut.py")
    try:
        token = list(json.loads((orch.FOCUS_ROOT / "agent_tokens.json").read_text()).values())[0]
        top3 = requests.get(
            "https://clawlab-api.aiscientist.tools/api/v1/leaderboard/proteingym-spike/top",
            headers={"Authorization": f"Bearer {token}"}).json().get("top3", [])
        print(f"  Leaderboard top3:   {top3}")
    except Exception as e:
        print(f"  Leaderboard check failed: {e}")
    print("=" * 60)
