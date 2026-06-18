"""profile.py — biomlbench (task_type: biomlbench).

Python port of task-biomlbench/LAUNCH.md. Fixed-deadline Kaggle-style tasks with
a mandatory submission.csv. 1 GPU (or none); GPU agents serialize on the GPU
while CPU experiments run in parallel; loop exits when the deadline is near.
Meta-improvement is disabled. Every cycle checks time remaining and triggers an
emergency submission if close to the deadline.

State (deadline clock, GPU detection) lives in ``orch.state`` and is populated by
``bootstrap_extras``.
"""

import json
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from runtime import wait_all


# ── bootstrap: deadline clock + GPU/CPU detection ────────────────────────────

def _gpu_is_available():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        return any(l.strip() for l in r.stdout.splitlines())
    except Exception:
        return False


def bootstrap_extras(orch):
    s = orch.state
    s["DEADLINE_BUFFER_MINUTES"] = 15
    task_md = orch.task_md

    gpu = _gpu_is_available()
    s["GPU_AVAILABLE"] = gpu
    s["WALL_CLOCK_HOURS"] = 16 if ("A100" in task_md or gpu) else 8
    task_says_cpu = 'CUDA_VISIBLE_DEVICES=""' in task_md or "CPU-only" in task_md

    if task_says_cpu and not gpu:
        s["IS_CPU_ONLY"], s["CUDA_SETTING"] = True, '""'
    elif gpu:
        s["IS_CPU_ONLY"], s["CUDA_SETTING"] = False, "0"
    else:
        s["IS_CPU_ONLY"], s["CUDA_SETTING"] = True, '""'

    print(f"  GPU_AVAILABLE={gpu}  IS_CPU_ONLY={s['IS_CPU_ONLY']}  CUDA_SETTING={s['CUDA_SETTING']}")

    ts_file = orch.FOCUS_ROOT / "logs" / "launch_timestamp.txt"
    ts_file.parent.mkdir(parents=True, exist_ok=True)
    if ts_file.exists():
        launch_time = datetime.fromisoformat(ts_file.read_text().strip())
        print(f"  Resuming. Original launch: {launch_time.isoformat()}")
    else:
        launch_time = datetime.now(timezone.utc)
        ts_file.write_text(launch_time.isoformat())
        print(f"  Fresh start. Launch time: {launch_time.isoformat()}")
    if launch_time.tzinfo is None:
        launch_time = launch_time.replace(tzinfo=timezone.utc)
    s["DEADLINE"] = launch_time + timedelta(hours=s["WALL_CLOCK_HOURS"])
    print(f"  Deadline: {s['DEADLINE'].isoformat()} — {_deadline_status(orch)}")


def _time_remaining_minutes(orch):
    return max(0, (orch.state["DEADLINE"] - datetime.now(timezone.utc)).total_seconds() / 60)


def _deadline_status(orch):
    return f"{_time_remaining_minutes(orch):.0f} min remaining (deadline {orch.state['DEADLINE'].isoformat()})"


# ── discussion: domain-aware approach menu (forces method diversity) ─────────

def _domain_approach_menu(task_md, gpu_available):
    is_protein = any(k in task_md for k in [
        "ProteinGym", "DMS", "deep mutational", "variant effect", "fitness landscape",
        "protein fitness", "amino acid", "mutation", "substitution", "UniProt",
        "Uniprot", "MSA", "multiple sequence alignment"])
    is_mol = any(k in task_md for k in [
        "SMILES", "RDKit", "fingerprint", "ADME", "lipophilicity", "clearance",
        "solubility", "PKIS"])
    is_image = any(k in task_md for k in [
        "image", "histopathologic", "tumor", "MRI", "segmentation"])
    is_sc = any(k in task_md for k in [
        "single-cell", "single cell", "scRNA", "CITE-seq", "multimodal",
        "cross-modality", "perturbation", "label projection", "gene expression",
        "anndata", "h5ad", "BMMC", "PBMC"])

    if is_protein:
        gpu_methods = ("GPU-ENABLED methods (preferred if GPU_AVAILABLE):\n"
                       "  - ESM2 / ESM-1v / ProtBert / ProtT5 embeddings + supervised head or zero-shot scoring\n"
                       "  - MSA Transformer / EVE / DeepSequence (alignment-based generative)\n"
                       "  - Tranception / ProGen / RITA (autoregressive protein LMs)\n"
                       "  - Fine-tuned ESM (LoRA) for fitness regression; Kermut / ProteinNPT\n") if gpu_available else ""
        cpu_methods = ("CPU methods (diversify):\n"
                       "  - One-hot features + Ridge/Lasso/LightGBM\n"
                       "  - Precomputed ESM/ProtT5 embeddings + Ridge/SVR/KNN/GBM\n"
                       "  - PSSM from MSA; independent site model; EVE zero-shot scores\n"
                       "  - GP on sequence-similarity (Hamming/BLOSUM) kernel; stacking ensemble\n")
        return gpu_methods + cpu_methods
    if is_mol:
        gpu_methods = ("GPU-ENABLED methods (preferred if GPU_AVAILABLE):\n"
                       "  - Chemprop (D-MPNN); PyG/DGL GCN/GAT on molecular graphs\n"
                       "  - ChemBERTa/MolBERT/MolFormer SMILES transformer; UniMol (3D)\n") if gpu_available else ""
        cpu_methods = ("CPU methods (diversify):\n"
                       "  - RDKit descriptors + LightGBM; Morgan fingerprints + SVR\n"
                       "  - Mordred + RF/ExtraTrees; Tanimoto-kernel GP; AutoML; stacking ensemble\n")
        return gpu_methods + cpu_methods
    if is_image:
        gpu_methods = ("GPU-ENABLED image methods (preferred if GPU_AVAILABLE):\n"
                       "  - Fine-tuned ResNet/EfficientNet/ViT; pathology foundation models\n"
                       "  - DINO/MAE pretrain + linear probe; CNN ensemble + TTA; U-Net/nnU-Net\n") if gpu_available else ""
        cpu_methods = "CPU methods: histogram features + SVM, HOG + RF, handcrafted texture.\n"
        return gpu_methods + cpu_methods
    if is_sc:
        principles = ("METHODOLOGICAL PRINCIPLES:\n"
                      "  - Compute a trivial baseline (constant/mean/random) before any model.\n"
                      "  - Match CV to the held-out axis (batch/donor/cell type/compound/spatial).\n")
        gpu_methods = ("GPU-ENABLED methods:\n"
                       "  - scVI/totalVI latent + linear/KNN; Geneformer/scGPT/UCE\n"
                       "  - batch-conditional MLP / gradient-reversal; CLIP-style contrastive; GNN on kNN graph\n") if gpu_available else ""
        cpu_methods = ("CPU methods (diversify):\n"
                       "  - Per-target Ridge/Lasso; KNN on TruncatedSVD/PCA; LightGBM/XGBoost\n"
                       "  - Harmony/ComBat-seq batch correction + head; Moran's I/SpatialDE; SingleR; stacking\n")
        return principles + gpu_methods + cpu_methods
    return ("Consider a range: classical ML (XGBoost, SVR), neural nets (MLP, CNN, "
            "Transformer), pretrained embeddings, ensembles.\n")


def discussion_policy(orch):
    """MANDATORY discussion with a domain approach menu — method diversity is the
    single biggest performance factor for benchmarks."""
    menu = _domain_approach_menu(orch.task_md, orch.state["GPU_AVAILABLE"])
    orch.state["approach_menu"] = menu
    extra = (
        f"GPU_AVAILABLE={orch.state['GPU_AVAILABLE']}\n\n"
        "DIVERSITY INSTRUCTION: read existing [DISCUSSION] posts; propose a model "
        "family/approach NO other agent has claimed. Do NOT propose incremental HP "
        "tuning of the simplest baseline. Include >=2 concrete experiment proposals "
        "in your chosen paradigm.\n\n"
        f"APPROACH MENU (domain options; you may invent others):\n{menu}"
        "Pick ONE paradigm from the menu (or a novel one). Avoid paradigms already taken."
    )
    return ("run", extra)


def monitor_extra_instructions(orch):
    s = orch.state
    return (
        f"GPU_AVAILABLE={s['GPU_AVAILABLE']}\nIS_CPU_ONLY={s['IS_CPU_ONLY']}\n\n"
        "DIVERSITY INSTRUCTION: after forming teams, seed each team's queue.md with a "
        "DIFFERENT starting experiment (no two teams share a model family/featurization). "
        "Exactly ONE team gets the classical ML baseline; the rest get qualitatively "
        "different paradigms. If GPU is available, >=half of non-baseline teams get "
        "GPU-native methods. Give the fastest approach to the team most likely to produce "
        "a submission.csv early. Also POST a [PROPOSAL] per seed, and write "
        "logs/approach_registry.json with {\"cycle\": 0, \"taken\": [...]}."
    )


def seeding_policy(orch, teams):
    """Monitor seeds; orchestrator verifies every queue is non-empty (fallback is
    a notice — a real fallback seed needs a chosen approach, left to the next
    analyst cycle)."""
    for team_name, team_info in teams.items():
        q_fm = orch.parse_fm(orch.get_file("queue.md", ws_id=team_info["workspace_id"]))
        if not q_fm.get("pending"):
            print(f"  WARNING: {team_name} queue empty after monitor — needs a fallback seed")


# ── per-cycle deadline gate + emergency submission ───────────────────────────

def pre_cycle_check(orch):
    s = orch.state
    rem = _time_remaining_minutes(orch)
    buf = s["DEADLINE_BUFFER_MINUTES"]
    submission_exists = (orch.FOCUS_ROOT / "task" / "submission.csv").exists()
    agent_subs = list(orch.FOCUS_ROOT.glob("agents/*/workspace/repo/submission.csv"))

    print(f"\n{'=' * 60}\nDEADLINE CHECK: {_deadline_status(orch)}")
    print(f"submission.csv exists: {submission_exists or bool(agent_subs)}\n{'=' * 60}")

    if rem <= buf:
        if not submission_exists and not agent_subs:
            print("  EMERGENCY: deadline approaching with no submission.csv — emergency save")
            _trigger_emergency_submission(orch)
        else:
            print("  Deadline approaching — final submission saved. Stopping.")
        return True

    if rem <= buf * 2:
        print(f"  WARNING: {rem:.0f} min remaining — prioritise submission.csv this cycle")
    return False


def _trigger_emergency_submission(orch):
    """Launch one agent with direct imperative instructions that BYPASS HEARTBEAT
    Part 0 (in an emergency there may be no teams; Part 0 would route to No-Team
    exit and produce nothing)."""
    s = orch.state
    agent_name = f"{orch.PREFIX}_gpu1"
    cuda = s["CUDA_SETTING"]
    root = orch.FOCUS_ROOT

    existing_subs = list(root.glob("agents/*/workspace/repo/submission.csv"))
    sub_hint = (f"An existing submission.csv is at: {existing_subs[0]}\n"
                f"  If you cannot produce better in time, copy it to {root}/task/submission.csv.\n"
                ) if existing_subs else ""
    existing_trains = list(root.glob("agents/*/workspace/repo/train.py"))
    train_hint = (f"An existing train.py is at: {existing_trains[0]}\n"
                  f"  Copy it: cp {existing_trains[0]} {root}/agents/{agent_name}/workspace/repo/train.py\n"
                  ) if existing_trains else ""

    body = (
        f"You are {agent_name}. This is an EMERGENCY session.\n"
        f"FOCUS_ROOT={root}\nCUDA_VISIBLE_DEVICES={cuda}\nBIOMLBENCH=true\n"
        f"TIME_REMAINING_MINUTES={_time_remaining_minutes(orch):.0f}\n"
        f"DEADLINE_BUFFER_MINUTES={s['DEADLINE_BUFFER_MINUTES']}\n\n"
        "DO NOT read HEARTBEAT.md or follow normal protocol. DO NOT check roster/teams/queue. "
        "DO NOT run a second experiment. DO NOT post to the workshop.\n\n"
        "YOUR ONLY JOB:\n"
        f"STEP 1: creds = json.load(open('{root}/agents/{agent_name}/credentials.json'))\n"
        f"STEP 2: Get a working train.py.\n  {train_hint}"
        f"  If none exists: read {root}/task/TASK.md and write the simplest model that "
        f"produces a valid submission.csv (mean/zero predictions acceptable).\n"
        f"STEP 3: cd {root}/agents/{agent_name}/workspace/repo && "
        f"CUDA_VISIBLE_DEVICES={cuda} python train.py\n"
        f"STEP 4: shutil.copy('{root}/agents/{agent_name}/workspace/repo/submission.csv', "
        f"'{root}/task/submission.csv')\n{sub_hint}"
        f"STEP 5: Exit. Print: <promise>{agent_name} emergency submission complete</promise>\n"
    )
    h = orch.spawn(agent_name, model="sonnet", prompt=body, background=False)
    orch.harvest_session(h, role="gpu")


# ── analyst extras: deadline + approach diversity ────────────────────────────

def _approach_diversity_note(orch):
    reg = orch.FOCUS_ROOT / "logs" / "approach_registry.json"
    if not reg.exists():
        return ""
    try:
        taken = json.loads(reg.read_text()).get("taken", [])
    except Exception:
        return ""
    if not taken:
        return ""
    return ("APPROACH DIVERSITY: already running/completed this cycle — DO NOT duplicate:\n"
            + "\n".join(f"  - {a}" for a in taken)
            + "\nClaim something from a DIFFERENT paradigm instead.\n")


def analyst_prompt_extras(orch):
    s = orch.state
    rem = _time_remaining_minutes(orch)
    buf = s["DEADLINE_BUFFER_MINUTES"]
    return (
        f"BIOMLBENCH=true\nGPU_AVAILABLE={s['GPU_AVAILABLE']}\n"
        f"TIME_REMAINING_MINUTES={rem:.0f}\nDEADLINE_BUFFER_MINUTES={buf}\n"
        f"IMPORTANT: fixed-deadline benchmark. Prioritise experiments that finish in the "
        f"remaining time. If TIME_REMAINING_MINUTES < {buf * 3}, propose only fast HP "
        f"changes or inference-only improvements (<20 min).\n"
        f"{_approach_diversity_note(orch)}"
    )


# ── GPU dispatch: CPU-parallel vs mixed (1-GPU) ──────────────────────────────

def _gpu_prompt_extra(orch, cuda):
    s = orch.state
    agent_placeholder = "{AGENT}"  # filled per-agent below via extra_prompt formatting
    if cuda == '""':
        compute = ("COMPUTE: No GPU. All training on CPU, in parallel with other agents. "
                   "Diversify approaches — do not all pick the same model family/featurization.\n")
    else:
        compute = (
            "COMPUTE: GPU available (CUDA_VISIBLE_DEVICES=0).\n"
            "MIXED DISPATCH — declare compute mode BEFORE training by writing\n"
            f"  echo 'gpu' > {orch.FOCUS_ROOT}/logs/<your-name>.gpu_claim   (GPU experiment)\n"
            f"  echo 'cpu' > {orch.FOCUS_ROOT}/logs/<your-name>.gpu_claim   (CPU-only)\n"
            "within ~60s. The orchestrator reads this to block on you or launch the next "
            "agent in parallel.\n")
    rem = _time_remaining_minutes(orch)
    buf = s["DEADLINE_BUFFER_MINUTES"]
    return (
        f"GPU_AVAILABLE={s['GPU_AVAILABLE']}\nBIOMLBENCH=true\n"
        f"TIME_REMAINING_MINUTES={rem:.0f}\nDEADLINE_BUFFER_MINUTES={buf}\n"
        f"{compute}{_approach_diversity_note(orch)}"
        f"IMPORTANT: if TIME_REMAINING_MINUTES < {buf + 20}, skip the second experiment "
        f"and go to Part 5 to save your best submission.csv to "
        f"{orch.FOCUS_ROOT}/task/submission.csv, then exit.\n"
    )


def gpu_dispatch(orch, teams):
    s = orch.state
    if s["IS_CPU_ONLY"]:
        # All GPU agents share the CPU pool — full parallel.
        extra = _gpu_prompt_extra(orch, '""')
        handles = [orch.spawn(a, mode="execute", model="sonnet", cuda='""',
                              extra_prompt=extra,
                              extra_env={"BIOMLBENCH": "true"}, background=True)
                   for a in orch.gpu_agents]
        wait_all(handles, timeout=int(_time_remaining_minutes(orch) * 60))
        for h in handles:
            orch.harvest_session(h, role="gpu")
        return

    # 1 GPU: mixed dispatch — serialize 'gpu' claims, background 'cpu' claims.
    buf = s["DEADLINE_BUFFER_MINUTES"]
    extra = _gpu_prompt_extra(orch, "0")
    for agent_name in orch.gpu_agents:
        if _time_remaining_minutes(orch) <= buf:
            print(f"  Deadline imminent — not launching {agent_name}")
            break
        claim_path = orch.FOCUS_ROOT / "logs" / f"{agent_name}.gpu_claim"
        claim_path.unlink(missing_ok=True)
        h = orch.spawn(agent_name, mode="execute", model="sonnet", cuda="0",
                       extra_prompt=extra, extra_env={"BIOMLBENCH": "true"}, background=True)
        claim = _wait_for_gpu_claim(orch, agent_name)
        print(f"  [{agent_name}] declared: {claim}")
        if claim == "gpu":
            _block_until_gpu_free(orch, agent_name, h)
        orch.harvest_session(h, role="gpu")


def _wait_for_gpu_claim(orch, agent_name, timeout_s=120, poll_s=5):
    claim_path = orch.FOCUS_ROOT / "logs" / f"{agent_name}.gpu_claim"
    waited = 0
    while waited < timeout_s:
        if claim_path.exists():
            val = claim_path.read_text().strip().lower()
            return val if val in ("gpu", "cpu") else "gpu"
        time.sleep(poll_s)
        waited += poll_s
    print(f"  [{agent_name}] no gpu_claim after {timeout_s}s — assuming GPU")
    return "gpu"


def _block_until_gpu_free(orch, agent_name, handle):
    base = orch.FOCUS_ROOT / "agents" / agent_name / "workspace"
    result_path = base / "result_latest.json"
    sub_path = base / "repo" / "submission.csv"
    buf = orch.state["DEADLINE_BUFFER_MINUTES"]
    max_s = min(60 * 60, int((_time_remaining_minutes(orch) - buf) * 60))
    waited = 0
    while waited < max_s:
        if handle.poll() is not None or result_path.exists() or sub_path.exists():
            print(f"  [{agent_name}] GPU training complete (waited {waited}s)")
            return
        time.sleep(30)
        waited += 30
    print(f"  [{agent_name}] GPU wait timed out — proceeding")


# ── champion: best submission.csv across agents (always propagate for safety) ─

def champion_promotion(orch, teams):
    import shutil
    root = orch.FOCUS_ROOT
    best_score = best_agent = best_sub = best_train = None
    best_direction = "maximize"

    for agent_name in orch.gpu_agents:
        rp = root / "agents" / agent_name / "workspace" / "result_latest.json"
        if not rp.exists():
            bare = root / "agents" / agent_name / "workspace" / "repo" / "submission.csv"
            if bare.exists() and best_sub is None:
                best_sub, best_agent = bare, agent_name
            continue
        try:
            r = json.loads(rp.read_text())
        except Exception:
            continue
        score = r.get("val_score")
        direction = r.get("direction", "maximize")
        sub = Path(r["submission_path"]) if r.get("submission_path") else \
            root / "agents" / agent_name / "workspace" / "repo" / "submission.csv"
        if not sub.exists():
            continue
        train = Path(r["train_path"]) if r.get("train_path") else \
            root / "agents" / agent_name / "workspace" / "repo" / "train.py"
        if score is None:
            if best_sub is None:
                best_sub, best_train, best_agent = sub, (train if train.exists() else None), agent_name
            continue
        better = (best_score is None or
                  (direction == "maximize" and score > best_score) or
                  (direction == "minimize" and score < best_score))
        if better:
            best_score, best_agent, best_sub = score, agent_name, sub
            best_train = train if train.exists() else None
            best_direction = direction

    if not best_sub:
        print("  WARNING: no agent produced a submission.csv this cycle.")
        return

    shutil.copy(best_sub, root / "task" / "submission.csv")
    print(f"  submission.csv propagated from {best_agent} (score={best_score})")

    prev_fm = orch.parse_fm(orch.get_file("champion.md"))
    prev_score = prev_fm.get("metric_value")
    metric_name = orch.task_meta.get("metric", "val_score")
    is_new = (prev_score is None or best_score is None or
              (best_direction == "maximize" and best_score > prev_score) or
              (best_direction == "minimize" and best_score < prev_score))
    if is_new:
        (root / "champion").mkdir(exist_ok=True)
        if best_train and best_train.exists():
            shutil.copy(best_train, root / "champion" / "train.py")
        (root / "champion" / "SOURCE").write_text(
            f"{best_agent} score={best_score} {datetime.now(timezone.utc).isoformat()}\n")
        orch.put_file("champion.md", (
            f"---\nmetric_name: {metric_name}\nmetric_value: {best_score}\n"
            f"direction: {best_direction}\nagent: {best_agent}\ncycle: {orch.cycle_count}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\nsubmission_saved: true\n---\n\n"
            f"# Champion (cycle {orch.cycle_count})\n\n- **Score:** {best_score}\n"
            f"- **Agent:** {best_agent}\n- **Previous:** {prev_score}\n"
            f"- **submission.csv:** `task/submission.csv`\n"))
        print(f"  Champion updated: {best_agent} score={best_score} (prev={prev_score})")
    else:
        print(f"  No improvement (best={best_score}, prev={prev_score}) — champion unchanged")


def stagnation_response(orch, cycle_count):
    """Do NOT stop — post [STUCK] to trigger a pivot."""
    if cycle_count < 3:
        return
    print(f"  STAGNATION: posting [STUCK] to trigger a pivot (cycle {cycle_count})")
    orch.post(
        title=f"[STUCK] Cycle {cycle_count}: 0 KEEPs — need pivot",
        content=(f"No improvements recently. {_deadline_status(orch)}.\n\nAnalysts: propose a "
                 f"qualitatively different approach (model family / features / training)."),
        notify_agents=orch.analysts,
        tags=["type:stuck", f"cycle:{cycle_count}"],
    )


def periodic_hooks(orch, cycle_count):
    """Meta-improvement DISABLED. Only reset the approach registry each cycle."""
    reg = orch.FOCUS_ROOT / "logs" / "approach_registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"cycle": cycle_count, "taken": []}, indent=2))
    print(f"  Approach registry reset for cycle {cycle_count}.")


def exit_condition(orch):
    return _time_remaining_minutes(orch) <= orch.state["DEADLINE_BUFFER_MINUTES"]


def final_report(orch):
    s = orch.state
    sub = orch.FOCUS_ROOT / "task" / "submission.csv"
    champ = orch.parse_fm(orch.get_file("champion.md"))
    used = s["WALL_CLOCK_HOURS"] * 60 - _time_remaining_minutes(orch)
    print("\n" + "=" * 60)
    print("  BIOMLBENCH RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:           {orch.task_name}")
    print(f"  Total cycles:   {orch.cycle_count}")
    print(f"  Time used:      {used:.0f} / {s['WALL_CLOCK_HOURS'] * 60:.0f} min")
    print(f"  Best score:     {champ.get('metric_value', 'none')}")
    print(f"  Best agent:     {champ.get('agent', 'none')}")
    print(f"  submission.csv: {'EXISTS' if sub.exists() else 'MISSING — CRITICAL'}")
    print(f"  Location:       {sub}")
    if not sub.exists():
        print("  CRITICAL: No submission.csv saved. Manual intervention required.")
    else:
        print("  submission.csv is ready for the biomlbench grader.")
    print("=" * 60)
