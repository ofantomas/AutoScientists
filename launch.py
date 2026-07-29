#!/usr/bin/env python3
"""Launch a fresh AutoScientists experiment from this template.

Creates a NEW experiment directory, copies system/task files into it, then
bootstraps agents, workspace, and kickoff post.

Usage:
  python3 launch.py                                                        # auto-names: {template}_{timestamp}/
  python3 launch.py my-experiment                                          # creates ../my-experiment/
  python3 launch.py my-experiment --task task-sella                       # bundled task (relative path)
  python3 launch.py my-experiment --task /absolute/path/to/task-dir
  python3 launch.py my-experiment --output-dir /tmp/runs                  # create in a specific parent dir
  python3 launch.py --regenerate-heartbeats /path/to/run-dir              # maintenance: rebuild agents/*/HEARTBEAT.md

Heartbeat regeneration (maintenance mode, launches nothing):
  Each agent reads `agents/{name}/HEARTBEAT.md`, which is a rendered COPY of
  system/templates/HEARTBEAT.md with the role doc inlined at launch. Agents never read
  system/templates/ directly, so editing a ROLE-*.md after launch changes NOTHING until
  the copies are rebuilt. `--regenerate-heartbeats <run-dir>` re-runs only that
  composition, reading the RUN's own system/templates/, and touches nothing else. It is a
  required step for any meta-improvement pass that edits a role template
  (see system/reference/META-IMPROVEMENT.md).

Task directories are bundled as subdirectories of this repo:
  task-sella/                 — Molecular-geometry optimizer; agents improve repo/algo.py,
                                scored by eval_candidate.py (fitness = mean_rel_steps, lower is better)

Additional tasks can be pointed to via an absolute or relative path to any directory
containing a TASK.md file.

Task types (set via task_type: in TASK.md frontmatter):
  optimization — Open-ended optimization of a baseline. The bundled task-sella ships its
                own repo/ (algo.py) and champion/, and a candidate is scored by
                eval_candidate.py (minimise fitness). Example:
                  python3 launch.py my-run --task task-sella

Profile resolution: launch.py walks up from the --task path looking for the
nearest LAUNCH.md (bounded to this repo), letting a family-level LAUNCH.md
cover many subtasks while still allowing per-task overrides. Every task must
ship a LAUNCH.md somewhere on that walk; there is no generic fallback.

For all task types, after launch the orchestrator reads runbook.md + task-profile.md:
  cd <run-dir>
  # Resume the long-lived Codex session that created the run and continue the runbook.
"""

import argparse
import json
import os
import shutil
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Template directory (where this script lives) ────────────

TEMPLATE_DIR = Path(__file__).resolve().parent

# ── Heartbeat composition (used at launch AND by --regenerate-heartbeats) ────
#
# An agent's `agents/{name}/HEARTBEAT.md` is a rendered COPY of
# system/templates/HEARTBEAT.md with the role doc (ROLE-CPU / ROLE-ANALYST /
# ROLE-MONITOR) and ROLE-TEAM.md inlined into its two placeholders. That copy is
# the ONLY thing an agent reads at dispatch time — it never opens
# system/templates/ itself.
#
# That is why the composition lives in a reusable function instead of inline in
# setup_agent(): editing a role template AFTER launch changes nothing for the
# life of the run unless the copies are rebuilt. A meta pass that edits a
# system/templates/ROLE-*.md must therefore re-run this composition (see
# `--regenerate-heartbeats`) before the next dispatch, or the edit stays
# invisible to every agent while the meta log records it as applied.

ROLE_FILE_MAP = {
    "gpu": "ROLE-CPU.md",   # legacy "gpu" role also maps to the CPU-eval doc
    "cpu": "ROLE-CPU.md",   # CPU-eval agents use ROLE-CPU.md
    "analyst": "ROLE-ANALYST.md",
    "monitor": "ROLE-MONITOR.md",
}

# The two placeholder blocks in system/templates/HEARTBEAT.md, matched
# LITERALLY. If that template's comment wording ever changes, these must change
# with it: a silent no-match would ship a heartbeat with no role section at all.
ROLE_PLACEHOLDER = (
    "<!-- ROLE_CONTENT_PLACEHOLDER -->\n"
    "<!-- launch.py replaces this with system/templates/ROLE-{role}.md -->"
)
TEAM_PLACEHOLDER = (
    "<!-- TEAM_CONTENT_PLACEHOLDER -->\n"
    "<!-- launch.py replaces this with system/templates/ROLE-TEAM.md -->"
)


def _strip_role_frontmatter(text):
    """Drop a role doc's leading frontmatter — the heartbeat already carries one."""
    parts = text.split("---")
    if len(parts) >= 3:
        return "---".join(parts[2:]).strip()
    return text


def compose_heartbeat(role, templates_dir):
    """Render one agent's HEARTBEAT.md text from the templates in `templates_dir`.

    `templates_dir` is a `system/templates/` directory: this template's own at
    launch, and the RUN's own copy when regenerating — the run's copy is the one
    a meta pass edits, because the orchestrator works inside the run directory.
    Pure: reads templates, returns text, writes nothing.
    """
    templates_dir = Path(templates_dir)
    heartbeat = (templates_dir / "HEARTBEAT.md").read_text()

    # The ROLE-CPU.md default is a last-resort fallback, never a routing rule:
    # callers are expected to pass a ROLE_FILE_MAP key (regenerate_heartbeats
    # skips an agent whose role it cannot map). Say so out loud if it ever fires,
    # because a wrong-role heartbeat looks perfectly healthy from the outside.
    if role not in ROLE_FILE_MAP:
        print(f"  WARNING: unknown role '{role}' — falling back to ROLE-CPU.md. "
              f"Known roles: {sorted(ROLE_FILE_MAP)}")
    role_src = templates_dir / ROLE_FILE_MAP.get(role, "ROLE-CPU.md")
    role_content = _strip_role_frontmatter(role_src.read_text()) if role_src.exists() else ""

    team_src = templates_dir / "ROLE-TEAM.md"
    team_content = _strip_role_frontmatter(team_src.read_text()) if team_src.exists() else ""

    # Warn loudly rather than silently emitting a heartbeat with an empty role
    # section — an agent with no role doc still boots, and the failure would only
    # surface as inexplicably off-protocol behaviour many cycles later.
    if ROLE_PLACEHOLDER not in heartbeat:
        print(f"  WARNING: no ROLE_CONTENT_PLACEHOLDER in {templates_dir / 'HEARTBEAT.md'} "
              f"— the '{role}' role doc was NOT inlined")
    if TEAM_PLACEHOLDER not in heartbeat:
        print(f"  WARNING: no TEAM_CONTENT_PLACEHOLDER in {templates_dir / 'HEARTBEAT.md'} "
              f"— ROLE-TEAM.md was NOT inlined")

    heartbeat = heartbeat.replace(ROLE_PLACEHOLDER, role_content)
    heartbeat = heartbeat.replace(TEAM_PLACEHOLDER, team_content)
    return heartbeat


def _agent_role(agent_dir):
    """Best-effort role for an EXISTING agent directory, or None if undecidable.

    Returns a key of ROLE_FILE_MAP, or None. Primary source is the `role:` field
    written into AGENT.md at launch. The name-suffix fallback covers a hand-made
    or hand-edited agent directory. We return None rather than guessing, because
    handing an analyst the CPU-eval doc is far worse than leaving its heartbeat
    stale.

    A `role:` value we cannot map is NOT a guess we are willing to make. It is
    discarded and we fall through to the name suffix, then to None. This is not
    hypothetical: HEARTBEAT.md sets `MY_ROLE = "unknown"` when it cannot read
    AGENT.md at boot and Part 6a writes that straight back, so `role: unknown`
    is a value a live run really does produce. Trusting it would hit
    compose_heartbeat's `ROLE_FILE_MAP.get(role, "ROLE-CPU.md")` default and
    hand an analyst the CPU-eval doc silently — and it would also defeat the
    name suffix, which recovers "analyst" from `..._analyst1` correctly.
    """
    agent_dir = Path(agent_dir)
    agent_md = agent_dir / "AGENT.md"
    if agent_md.exists():
        # Hand-parse the one field we need: this runs before the scaffolding
        # section below imports yaml, and a single scalar needs no YAML parser.
        lines = agent_md.read_text().splitlines()
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("role:"):
                    # Normalised: compose_heartbeat's lookup is case-sensitive,
                    # so `role: CPU` must not silently become the default doc.
                    role = line.split(":", 1)[1].strip().strip("\"'").lower()
                    if role in ROLE_FILE_MAP:
                        return role
                    break  # one role field; an unusable value falls through
    name = agent_dir.name
    for suffix, role in (("monitor", "monitor"), ("analyst", "analyst"), ("cpu", "cpu")):
        if suffix in name:
            return role
    return None


def regenerate_heartbeats(run_dir, verbose=True):
    """Rebuild every agents/*/HEARTBEAT.md in an EXISTING run directory.

    Re-runs ONLY the heartbeat composition: no agent registration, no workspace
    writes, no file copies, no kickoff — nothing else about the run is re-created.
    Safe to run repeatedly; an agent whose heartbeat is already current is left
    untouched.

    Templates are read from the RUN's own `system/templates/`, never from this
    template checkout. That is deliberate: the run's copy is what a meta pass
    edits, and reading the checkout instead would leak unrelated template edits
    (possibly from another run) into a live run.

    Returns (written, unchanged, skipped). `skipped > 0` means at least one agent
    still holds a stale heartbeat — treat that as a failure, not a warning.
    """
    run_dir = Path(run_dir).resolve()
    templates_dir = run_dir / "system" / "templates"
    agents_dir = run_dir / "agents"

    if not (templates_dir / "HEARTBEAT.md").exists():
        print(f"ERROR: {templates_dir / 'HEARTBEAT.md'} not found.")
        print("  --regenerate-heartbeats expects a RUN directory (the one holding")
        print("  agents/ and system/), not the template directory. No fallback to the")
        print("  template's own system/templates/ is attempted on purpose: the run's")
        print("  copy is the one a meta pass edits.")
        sys.exit(1)
    if not agents_dir.is_dir():
        print(f"ERROR: no agents/ directory in {run_dir} — nothing to regenerate.")
        sys.exit(1)

    print(f"Regenerating heartbeats in {run_dir}")
    print(f"  Templates: {templates_dir}")

    written = unchanged = skipped = 0
    # Directories only, dotfiles skipped — the same roster filter the orchestrator
    # and HEARTBEAT use, so a stray .DS_Store is never mistaken for an agent.
    for agent_dir in sorted(p for p in agents_dir.iterdir()
                            if p.is_dir() and not p.name.startswith(".")):
        role = _agent_role(agent_dir)
        if role is None:
            print(f"  SKIP  {agent_dir.name}: AGENT.md has no usable role (missing, or a value "
                  f"outside {sorted(ROLE_FILE_MAP)}) and none is inferable from the name "
                  f"— HEARTBEAT.md left as-is (stale beats wrong-role). Fix the `role:` field "
                  f"in its AGENT.md and re-run.")
            skipped += 1
            continue
        heartbeat = compose_heartbeat(role, templates_dir)
        hb_path = agent_dir / "HEARTBEAT.md"
        if hb_path.exists() and hb_path.read_text() == heartbeat:
            unchanged += 1
            if verbose:
                print(f"  ok    {agent_dir.name} ({role}) — already current")
            continue
        hb_path.write_text(heartbeat)
        written += 1
        if verbose:
            print(f"  wrote {agent_dir.name} ({role})")

    print(f"  {written} rewritten, {unchanged} already current, {skipped} skipped")
    print("  Takes effect on the NEXT dispatch — an agent already running keeps the")
    print("  copy it read when it was spawned. Regenerate between dispatches.")
    return (written, unchanged, skipped)


# ── ClawInstitute API ────────────────────────────────────────

API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")

# Load admin (privileged bootstrap) token.
# Priority: template .key file > CLAWINSTITUTE_TOKEN env var > ~/.clawinstitute/token
def _load_token():
    for key_path in [TEMPLATE_DIR / ".key"]:
        if key_path.exists():
            return key_path.read_text().strip()
    if os.environ.get("CLAWINSTITUTE_TOKEN"):
        return os.environ["CLAWINSTITUTE_TOKEN"]
    default = Path.home() / ".clawinstitute" / "token"
    if default.exists():
        return default.read_text().strip()
    print("ERROR: No API key found. Start the local server first:")
    print("  npx clawinstitute start")
    print("Or set CLAWINSTITUTE_TOKEN explicitly.")
    sys.exit(1)

# ── Parse arguments ─────────────────────────────────────────

_TIMESTAMP = datetime.now(timezone.utc).strftime("%m%d_%H%M")

_parser = argparse.ArgumentParser(description="Launch a fresh AutoScientists experiment.")
_parser.add_argument("name", nargs="?", default=None,
                     help="Experiment name (default: {template}_{timestamp})")
_parser.add_argument("--task", default=None,
                     help="Path to a bundled task directory (e.g. task-sella) or an absolute path to any directory containing TASK.md. Defaults to $TASK_DIR.")
_parser.add_argument("--output-dir", default=None, metavar="DIR",
                     help="Parent directory for the new experiment (default: next to this template)")
_parser.add_argument("--protein", default=None, metavar="PROTEIN_ID",
                     help="Protein/assay to focus on, e.g. SPIKE_SARS2_Starr_2020_binding. "
                          "Substituted into task/TASK.md and task/LAUNCH.md after copying.")
_parser.add_argument("--cpu", type=int, default=6, metavar="N",
                     help="Number of CPU-eval agents (default: 6). Use a smaller roster for smoke "
                          "runs. The orchestrator enumerates agents from the run directory, so any "
                          "size works without editing runbook.md or the task profile.")
_parser.add_argument("--analysts", type=int, default=3, metavar="N",
                     help="Number of analyst agents (default: 3). Team formation needs at least 2 "
                          "so the discussion round can produce >= 2 competing hypotheses.")
_parser.add_argument("--regenerate-heartbeats", nargs="?", const=".", default=None,
                     metavar="RUN_DIR",
                     help="Maintenance mode — launches nothing. Rebuild agents/*/HEARTBEAT.md in an "
                          "EXISTING run directory (default: the current directory) from that run's "
                          "own system/templates/. REQUIRED after a meta pass edits a "
                          "system/templates/ROLE-*.md: without it the edit never reaches any agent.")
_args = _parser.parse_args()

# Maintenance mode is handled here, before ANY launch scaffolding (and before the
# token load, which it does not need), so regenerating heartbeats for a live run
# cannot re-register agents, re-copy files, or re-post a kickoff.
if _args.regenerate_heartbeats is not None:
    _written, _unchanged, _skipped = regenerate_heartbeats(_args.regenerate_heartbeats)
    sys.exit(1 if _skipped else 0)

if _args.cpu < 1:
    _parser.error("--cpu must be >= 1")
if _args.analysts < 2:
    _parser.error("--analysts must be >= 2 (team formation needs >= 2 competing hypotheses)")

# Load token after argparse so `--help` works without credentials.
ADMIN_TOKEN = _load_token()
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"}

RUN_NAME = _args.name or f"{TEMPLATE_DIR.name}_{_TIMESTAMP}"
PARENT_DIR = Path(_args.output_dir).resolve() if _args.output_dir else TEMPLATE_DIR.parent

# ── Create ablation directory ───────────────────────────────

RUN_DIR = PARENT_DIR / RUN_NAME

if RUN_DIR.exists():
    print(f"ERROR: {RUN_DIR} already exists. Pick a different name.")
    sys.exit(1)

# Copy task directory: --task flag > TASK_DIR env var.
task_source = _args.task or os.getenv("TASK_DIR")
if not task_source:
    print("ERROR: --task is required. Pass a bundled task directory or an absolute path:")
    print("  python3 launch.py my-run --task task-sella")
    sys.exit(1)

# Resolve relative to template if not absolute, otherwise honour the absolute path.
_task_path = Path(task_source)
if not _task_path.is_absolute():
    _task_path = (TEMPLATE_DIR / task_source).resolve()
if not _task_path.is_dir():
    raise RuntimeError(f"Task directory not found: {_task_path}")
task_source = str(_task_path)

# Require TASK.md at the --task root. Without this, passing a directory that
# only holds a README would silently fall back to that README, fail to parse
# its frontmatter, default to task_type=optimization, and copy an unintended
# tree into the run dir. Validate here, BEFORE mkdir, so failure leaves no
# empty dir.
if not (_task_path / "TASK.md").exists():
    print(f"ERROR: {task_source} has no TASK.md.")
    print(f"  --task must point at a task directory containing TASK.md.")
    print(f"  Example:")
    print(f"    python3 launch.py my-run --task task-sella")
    sys.exit(1)

print(f"Creating experiment: {RUN_DIR}")
RUN_DIR.mkdir(parents=True)

# ── Detect task type from TASK.md frontmatter (read source before copy) ─────
import yaml as _yaml


def _read_task_md(task_dir):
    """Find a task description file in `task_dir`, falling back through a
    candidate list. Returns (text, source_name). Source_name is one of
    "TASK.md", "program.md", "README.md", or "synthesized" when none of
    the candidates exist (we synthesize a minimal stub so downstream code
    has frontmatter to parse instead of crashing)."""
    task_dir = Path(task_dir)
    for name in ("TASK.md", "program.md", "README.md"):
        p = task_dir / name
        if p.exists():
            return p.read_text(), name
    return (
        "---\ntask_type: optimization\nmetric: fitness\n---\n"
        f"# Task\n\nFound no TASK.md/program.md/README.md in {task_dir}. "
        "Using minimal defaults.\n",
        "synthesized",
    )


_task_type = "optimization"  # default
_task_md_content, _src_task_md_source = _read_task_md(Path(task_source))
_task_md_parts = _task_md_content.split("---")
if len(_task_md_parts) >= 3:
    _task_meta = _yaml.safe_load(_task_md_parts[1]) or {}
    _task_type = _task_meta.get("task_type", "optimization")

print(f"  Task type: {_task_type}")

# Copy template files into the ablation directory
shutil.copytree(TEMPLATE_DIR / "system", RUN_DIR / "system")

# Always copy to "task/" in run directory for consistency.
# autoscientists_submission/ and private/ are always excluded — they hold reference
# solutions / held-out answers that must never be visible to agents.
# .git is excluded so a bundled task repo doesn't drag a large .git tree into every
# run directory; __pycache__ keeps stale bytecode from leaking across runs.
_always_exclude = ("autoscientists_submission", "private", ".git", "__pycache__")
_task_copy_ignore = shutil.ignore_patterns(*_always_exclude)
shutil.copytree(TEMPLATE_DIR / task_source, RUN_DIR / "task", ignore=_task_copy_ignore, symlinks=True)

# Copy the base runbook plus the matching task profile.
# The base file (`runbook.md`) defines the universal control flow and
# references named hooks; the profile (a LAUNCH.md bundled with the task,
# found by walking up from the --task path) fills in those hooks. The
# profile is renamed to `task-profile.md` in the run dir so the base file
# can always reference it by a fixed name.
program_file = "runbook.md"
base_src = TEMPLATE_DIR / program_file
if not base_src.exists():
    print(f"  WARNING: {program_file} not found in template — orchestrator program missing")
else:
    shutil.copy2(base_src, RUN_DIR / program_file)
    print(f"  Copied: {program_file}")

# Resolve the task-profile by walking up from the task dir looking for
# LAUNCH.md. Lets a family-level LAUNCH.md cover several subtasks, while a
# per-task LAUNCH.md (e.g. inside task-sella/) takes precedence.
# Bounded to TEMPLATE_DIR so external task paths don't leak in arbitrary
# LAUNCH.mds from the filesystem. Every task must ship a LAUNCH.md;
# there is no generic fallback.
def _find_bundled_launch_md(task_path, template_dir):
    p = Path(task_path).resolve()
    template = Path(template_dir).resolve()
    if template != p and template not in p.parents:
        # External task: only consider its own LAUNCH.md
        cand = p / "LAUNCH.md"
        return cand if cand.exists() else None
    # Bundled: walk up, stopping before template_dir itself
    while p != template and p != p.parent:
        cand = p / "LAUNCH.md"
        if cand.exists():
            return cand
        p = p.parent
    return None

_task_launch_md = _find_bundled_launch_md(_task_path, TEMPLATE_DIR)
if not _task_launch_md:
    print(f"ERROR: no LAUNCH.md found by walking up from {_task_path}.")
    print(f"  Every task must ship a LAUNCH.md (the task-profile that fills the")
    print(f"  hooks referenced by runbook.md). See task-sella/LAUNCH.md as a reference.")
    sys.exit(1)
shutil.copy2(_task_launch_md, RUN_DIR / "task-profile.md")
print(f"  Copied: {_task_launch_md.relative_to(TEMPLATE_DIR)} → task-profile.md")

# ── Protein substitution ─────────────────────────────────────
# If --protein is given, rewrite the placeholder protein name in task/*.md files.
# The task template uses "SPIKE_SARS2_Starr_2020_binding" as the default protein;
# this replaces it with the user-specified protein so agents know exactly what to work on.
PROTEIN_PLACEHOLDER = "SPIKE_SARS2_Starr_2020_binding"  # default in template
PROTEIN_TARGET = _args.protein  # None means no substitution
if PROTEIN_TARGET and PROTEIN_TARGET != PROTEIN_PLACEHOLDER:
    task_run_dir = RUN_DIR / "task"
    md_files = list(task_run_dir.glob("**/*.md"))
    substituted = []
    for md_path in md_files:
        text = md_path.read_text()
        if PROTEIN_PLACEHOLDER in text:
            md_path.write_text(text.replace(PROTEIN_PLACEHOLDER, PROTEIN_TARGET))
            substituted.append(md_path.name)
    if substituted:
        print(f"  Protein substitution: {PROTEIN_PLACEHOLDER} → {PROTEIN_TARGET}")
        print(f"    Updated: {', '.join(substituted)}")
    else:
        print(f"  NOTE: --protein specified but '{PROTEIN_PLACEHOLDER}' not found in any task/*.md")
elif PROTEIN_TARGET:
    print(f"  Protein: {PROTEIN_TARGET} (matches template default, no substitution needed)")

# ── Baseline table substitution ──────────────────────────────
# If the task directory has a baselines.csv, substitute {{BASELINE_TABLE}},
# {{BASELINE_NOTE}}, and {{SOTA_TABLE}} in task/TASK.md with protein-specific numbers.
import csv as _csv

def _load_baselines(task_src_dir, protein):
    """Return row dict for protein from baselines.csv, or None if not found."""
    csv_path = TEMPLATE_DIR / task_src_dir / "baselines.csv"
    if not csv_path.exists():
        return None
    with open(csv_path) as f:
        for row in _csv.DictReader(f):
            if row["protein"] == protein:
                return row
    return None

def _build_baseline_substitutions(row, protein):
    """Build the markdown strings to substitute into TASK.md/LAUNCH.md from a baselines.csv row."""
    c_pub = row["fold_contiguous_5_published"]
    m_pub = row["fold_modulo_5_published"]
    r_pub = row["fold_random_5_published"]
    c_our = row.get("fold_contiguous_5_repo_kermut") or row.get("fold_contiguous_5_ours", "")
    m_our = row.get("fold_modulo_5_repo_kermut") or row.get("fold_modulo_5_ours", "")
    r_our = row.get("fold_random_5_repo_kermut") or row.get("fold_random_5_ours", "")

    has_ours = c_our and m_our and r_our
    pub_mean = round((float(c_pub) + float(m_pub) + float(r_pub)) / 3, 4)

    if has_ours:
        our_mean = round((float(c_our) + float(m_our) + float(r_our)) / 3, 4)
        baseline_table = (
            f"| Split | Our reproduction | Published Kermut ({protein}) |\n"
            f"|---|---|---|\n"
            f"| `fold_contiguous_5` | {c_our} | {c_pub} |\n"
            f"| `fold_modulo_5` | {m_our} | {m_pub} |\n"
            f"| `fold_random_5` | {r_our} | {r_pub} |\n"
            f"| **Mean across splits** | **{our_mean}** | **~{pub_mean}** |"
        )
        baseline_note = "Results are within single-seed variance of the official published numbers."
        primary_metric_line = (
            f"`mean_spearman` baseline: **{our_mean}** (our reproduction). "
            f"Published Kermut: **~{pub_mean}**. Goal: beat both."
        )
        sota_last_line = (
            f"Our baseline (`kermut.py`) reproduces Kermut at {c_our} (fold_contiguous_5) "
            f"— within single-seed variance of the {c_pub} published number. "
            f"The goal is to improve beyond this."
        )
    else:
        # No reproduction yet — show published numbers only
        baseline_table = (
            f"| Split | Our reproduction | Published Kermut ({protein}) |\n"
            f"|---|---|---|\n"
            f"| `fold_contiguous_5` | *(not yet run)* | {c_pub} |\n"
            f"| `fold_modulo_5` | *(not yet run)* | {m_pub} |\n"
            f"| `fold_random_5` | *(not yet run)* | {r_pub} |\n"
            f"| **Mean across splits** | *(not yet run)* | **~{pub_mean}** |"
        )
        baseline_note = (
            "Reproduction not yet run for this protein. "
            "The first agent to run the baseline script should record results in "
            "the task's `baselines.csv` so future launches have accurate numbers."
        )
        primary_metric_line = (
            f"`mean_spearman` baseline: *(not yet run)*. "
            f"Published Kermut mean: **~{pub_mean}**. Goal: beat it."
        )
        sota_last_line = (
            f"Published Kermut score for this protein: {c_pub} (fold_contiguous_5). "
            f"Run `kermut.py {protein} fold_contiguous_5` to establish a reproduction baseline."
        )

    sota_table = (
        f"Benchmark results for `{protein}` on `fold_contiguous_5`\n"
        f"(source: `ProteinGym/benchmarks/DMS_supervised/substitutions/Spearman/"
        f"DMS_substitutions_Spearman_DMS_level_fold_contiguous_5.csv`):\n\n"
        f"| Model | Spearman | Type |\n"
        f"|-------|----------|------|\n"
        f"| **Kermut** (NeurIPS 2024) | **{c_pub}** | Composite GP: structure + sequence |\n"
        f"| ProteinNPT | 0.561 | Non-parametric transformer |\n"
        f"| Tranception Embeddings | 0.497 | Autoregressive LM |\n"
        f"| MSA Transformer Embeddings | 0.451 | MSA-based |\n"
        f"| ESM-1v Embeddings | 0.136 | Protein LM |\n\n"
        f"{sota_last_line}"
    )

    return baseline_table, baseline_note, primary_metric_line, sota_table


def _apply_baseline_substitutions(md_path, bt, bn, pml, st, protein):
    """Apply all baseline placeholders plus {PROTEIN} into a markdown file."""
    if not md_path.exists():
        return
    text = md_path.read_text()
    if not any(p in text for p in ("{{BASELINE_TABLE}}", "{{SOTA_TABLE}}", "{PROTEIN}")):
        return
    text = text.replace("{{BASELINE_TABLE}}", bt)
    text = text.replace("{{BASELINE_NOTE}}", bn)
    text = text.replace("{{PRIMARY_METRIC_LINE}}", pml)
    text = text.replace("{{SOTA_TABLE}}", st)
    text = text.replace("{PROTEIN}", protein)
    md_path.write_text(text)


_effective_protein = PROTEIN_TARGET or PROTEIN_PLACEHOLDER
_baseline_row = _load_baselines(task_source, _effective_protein)

if _baseline_row:
    _bt, _bn, _pml, _st = _build_baseline_substitutions(_baseline_row, _effective_protein)
    _apply_baseline_substitutions(
        RUN_DIR / "task" / "TASK.md", _bt, _bn, _pml, _st, _effective_protein
    )
    _apply_baseline_substitutions(
        RUN_DIR / "task" / "LAUNCH.md", _bt, _bn, _pml, _st, _effective_protein
    )
    print(f"  Baseline table: substituted from baselines.csv for {_effective_protein}")
else:
    _task_md_path = RUN_DIR / "task" / "TASK.md"
    if _task_md_path.exists() and "{{BASELINE_TABLE}}" in _task_md_path.read_text():
        print(f"  WARNING: {{{{BASELINE_TABLE}}}} placeholder in TASK.md but no baselines.csv "
              f"entry for '{_effective_protein}' — placeholders left unresolved")

for f in ["README.md", ".gitignore"]:
    src = TEMPLATE_DIR / f
    if src.exists():
        shutil.copy2(src, RUN_DIR / f)

# Copy .key into ablation so agents can use it
if (TEMPLATE_DIR / ".key").exists():
    shutil.copy2(TEMPLATE_DIR / ".key", RUN_DIR / ".key")
    (RUN_DIR / ".key").chmod(0o600)

# ── repo/ and champion/ setup ────────────────────────────────
#
# Two cases, determined by what's in the task source directory:
#
#   1. Task-bundled repo (task dir has its own repo/ and champion/) — copy
#      those. This is the path the bundled task-sella takes: it ships
#      repo/algo.py (the editable baseline) and champion/ (the seed champion).
#   2. Fallback: no bundled repo/ — agents work from a template repo/ if one
#      exists, otherwise from scratch.
#
_task_has_repo = (TEMPLATE_DIR / task_source / "repo").is_dir()
_task_has_champion = (TEMPLATE_DIR / task_source / "champion").is_dir()

if _task_has_repo:
    # Copy the task's own repo/ into the run (not a shared mutable reference).
    _repo_src = TEMPLATE_DIR / task_source / "repo"
    shutil.copytree(_repo_src, RUN_DIR / "repo", symlinks=False,
                    ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"))
    print(f"  Copied: repo/ <- {task_source}/repo/")

    # Copy the task's own champion/ if present.
    if _task_has_champion:
        _champ_src = TEMPLATE_DIR / task_source / "champion"
        shutil.copytree(_champ_src, RUN_DIR / "champion", symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  Copied: champion/ <- {task_source}/champion/")
    else:
        (RUN_DIR / "champion").mkdir(exist_ok=True)

else:
    # Fallback: the task shipped no repo/. If the template carries a repo/,
    # copy (NOT symlink) it into the run so every run gets an isolated working
    # tree — no shared mutable state across runs. Otherwise agents work from
    # scratch.
    if (TEMPLATE_DIR / "repo").is_dir() or (TEMPLATE_DIR / "repo").is_symlink():
        repo_src = TEMPLATE_DIR / "repo"
        if repo_src.is_symlink():
            repo_src = repo_src.resolve()
        shutil.copytree(repo_src, RUN_DIR / "repo", symlinks=False,
                        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc"))
        print(f"  Copied: repo/ from {repo_src} (template fallback)")
    else:
        print(f"  NOTE: No repo/ source found in template. Agents will work from scratch.")

    (RUN_DIR / "champion").mkdir(exist_ok=True)

    # Seed champion/ with the repo's algo.py so agents have a real baseline to
    # copy from on their first cycle. Without this, champion/algo.py starts
    # empty and the first agent has to discover where the code lives.
    _repo_algo = RUN_DIR / "repo" / "algo.py"
    if _repo_algo.exists():
        shutil.copy2(_repo_algo, RUN_DIR / "champion" / "algo.py")
        (RUN_DIR / "champion" / "SOURCE").write_text(
            f"template repo/algo.py, populated {datetime.now(timezone.utc).isoformat()}\n"
        )
        print(f"  Seeded: champion/algo.py <- repo/algo.py")

# Symlink .cache (shared training caches)
cache_src = TEMPLATE_DIR / ".cache"
if cache_src.is_symlink():
    os.symlink(os.readlink(cache_src), RUN_DIR / ".cache")
elif cache_src.is_dir():
    os.symlink(cache_src, RUN_DIR / ".cache")
else:
    for cache_dir in [".cache/uv", ".cache/huggingface", ".cache/torch"]:
        (RUN_DIR / cache_dir).mkdir(parents=True, exist_ok=True)

# Create runtime directories inside ablation
(RUN_DIR / "logs" / "raw").mkdir(parents=True, exist_ok=True)

print(f"  Copied: system/, task/ (from {task_source}), {program_file}")
if (RUN_DIR / "repo").exists():
    print(f"  Repo:   {RUN_DIR / 'repo'}")
print(f"  Linked: .cache/")
_created = ["logs/"]
if (RUN_DIR / "champion").exists():
    _created.append("champion/")
print(f"  Created: {', '.join(_created)}")

# ── All paths now point into the ablation directory ─────────

ROOT = RUN_DIR
REPO_DIR = ROOT / "repo"
# Always use "task" subdirectory in run directory
TASK_DIR = ROOT / "task"
AGENTS_DIR = ROOT / "agents"

# ── Run ID & naming ────────────────────────────────────────

RUN_ID = RUN_NAME.replace("-", "_").replace(".", "")

# Workshop name: use env var if set, otherwise default to ar_{RUN_ID}
if os.environ.get("WORKSHOP_NAME"):
    WORKSHOP_NAME = os.environ["WORKSHOP_NAME"].replace("-", "_")
    DISPLAY_NAME = os.environ.get("WORKSHOP_DISPLAY_NAME", WORKSHOP_NAME)
    DESCRIPTION = os.environ.get("WORKSHOP_DESCRIPTION", f"Multi-agent focus area — run {RUN_ID}")
else:
    WORKSHOP_NAME = f"ar_{RUN_ID}".replace("-", "_")
    DISPLAY_NAME = f"Autoresearch: {RUN_ID}"
    DESCRIPTION = f"Multi-agent optimization — run {RUN_ID}"

# Agent prefix
PREFIX = RUN_ID
if len(PREFIX) > 16:
    PREFIX = PREFIX[:6] + PREFIX[-10:]

# Agent roster: name -> (description, role, server, gpu)
# NOTE: the experiment-running agents are CPU-eval agents — their names are now
# `_cpuN` (LAUNCH.md cpu_dispatch / periodic_hooks reference
# `f"{PREFIX}_cpu{i}" for i in range(1, 7)`). Their role is "cpu" and they carry no
# device index (gpu = -1). The "cpu" role loads ROLE-CPU.md via ROLE_FILE_MAP above.
# Roster size is set by --cpu / --analysts (defaults 6 / 3). The orchestrator and the task profile
# ENUMERATE agents from the run directory rather than hardcoding a range, so any size works without
# editing runbook.md or task/LAUNCH.md. `server` is a cosmetic grouping label only.
_SERVERS = ("server1", "server2", "server3")

AGENTS = {
    f"{PREFIX}_monitor": ("Focus area monitor — health checks and stale-claim janitor (analysts form teams)",
                          "monitor", _SERVERS[0], -1),
}
for _i in range(1, _args.cpu + 1):
    AGENTS[f"{PREFIX}_cpu{_i}"] = (
        f"CPU-eval agent {_i} — evaluates candidate algo.py on the remote eval pool",
        "cpu", _SERVERS[(_i - 1) % len(_SERVERS)], -1,
    )
for _i in range(1, _args.analysts + 1):
    AGENTS[f"{PREFIX}_analyst{_i}"] = (
        f"Analyst {_i} — researches mechanisms, proposes experiments",
        "analyst", _SERVERS[(_i - 1) % len(_SERVERS)], -1,
    )

NOW = datetime.now(timezone.utc).isoformat()


def setup_agent(name, desc, role, server, gpu):
    """Create local agent directory with credentials, AGENT.md, memory/."""
    agent_dir = AGENTS_DIR / name
    (agent_dir / "workspace" / "repo").mkdir(parents=True, exist_ok=True)
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)

    # Register on AnonAPI API
    r = requests.post(f"{API}/agents/register", headers=HEADERS, json={
        "name": name, "description": desc
    })
    token = None
    if r.status_code < 300:
        data = r.json()
        token = data.get("agent", {}).get("api_key")
        if token:
            print(f"  Registered: {name} (got unique token)")
        else:
            print(f"  Registered: {name} (no token in response, using shared)")
    else:
        print(f"  {name}: {r.status_code} (may already exist)")

    if not token:
        token = ADMIN_TOKEN

    # credentials.json
    creds_path = agent_dir / "credentials.json"
    if not creds_path.exists():
        creds_path.write_text(json.dumps({"api_key": token, "agent_name": name}, indent=2))
        creds_path.chmod(0o600)

    # AGENT.md — the agent's persistent AS identity file.
    agent_md_path = agent_dir / "AGENT.md"
    if not agent_md_path.exists():
        if role in ("cpu", "gpu"):  # "gpu" is a legacy alias for the CPU-eval role
            role_line = "CPU-eval agent — evaluates candidate algo.py on the remote eval pool."
        else:
            role_line = f"{role.title()} agent."
        agent_md_path.write_text(f"""---
name: {name}
role: {role}
team: null
gpu: {gpu}
server: {server}
last_seen: null
status: idle
session_count: 0
last_experiment: null
last_outcome: null
last_fitness: null
---

# {name}

{role_line}

## Current Focus
(not yet assigned to a team)

## Suggestions for System Improvement
(none yet)

## Notes for Next Session
(none yet)
""")

    # memory/MEMORY.md — empty index
    memory_index = agent_dir / "memory" / "MEMORY.md"
    if not memory_index.exists():
        memory_index.write_text("# Memory Index\n\n(no memories yet)\n")

    # Build HEARTBEAT.md for this agent (self-contained: boot + role + team + record + exit).
    # This is a one-time render at launch: the agent reads this COPY, not the
    # templates it was built from, so any later role-template edit needs
    # `launch.py --regenerate-heartbeats <run-dir>` to reach it.
    (agent_dir / "HEARTBEAT.md").write_text(
        compose_heartbeat(role, TEMPLATE_DIR / "system" / "templates")
    )

    # Copy the baseline code repo for experiment-running agents (CPU-eval),
    # optional - only if a repo source exists.
    if role in ("gpu", "cpu"):
        dst = agent_dir / "workspace" / "repo"
        repo_source = None

        # Pattern 1: Autoresearch - repo symlink at run root
        if REPO_DIR.exists() and REPO_DIR.is_symlink():
            repo_source = REPO_DIR
        # Pattern 2: Bio tasks - task/repo-* directory (e.g., task/repo-caco2/)
        else:
            task_repos = list(TASK_DIR.glob("repo-*"))
            if task_repos:
                repo_source = task_repos[0]
            # Pattern 3: Sella CPU-eval - a plain task/repo/ directory shipped with the
            # task (algo.py-centric; champion ships alongside). The eval head holds the
            # evaluator, so all we need locally is the editable algo.py baseline.
            elif (TASK_DIR / "repo").exists():
                repo_source = TASK_DIR / "repo"

        # Copy baseline code to agent workspace if found
        if repo_source:
            shutil.copytree(repo_source, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('.venv', '__pycache__', '*.pyc', '.git'))
            print(f"    Copied {repo_source.name} -> {name}/workspace/repo/")

    return token


def put_file(ws_id, path, content):
    """Write a file to the workspace."""
    r = requests.put(f"{API}/workspaces/{ws_id}/files/{path}",
                     headers=HEADERS, json={"content": content})
    status = "OK" if r.status_code < 300 else f"ERR {r.status_code}"
    print(f"  {path}: {status}")


def main():
    print()
    print("=" * 60)
    print("  Autoresearch Focus Area — Fresh Launch")
    print(f"  Template:   {TEMPLATE_DIR}")
    print(f"  Experiment: {ROOT}")
    print(f"  Run ID:     {RUN_ID}")
    print(f"  Workshop:   {WORKSHOP_NAME}")
    print(f"  Prefix:     {PREFIX}")
    print("=" * 60)

    # ── Step 1: Create NEW Workshop ─────────────────────────
    print(f"\n[1/6] Creating workshop: {WORKSHOP_NAME}")
    r = requests.post(f"{API}/workshops", headers=HEADERS, json={
        "name": WORKSHOP_NAME,
        "display_name": DISPLAY_NAME,
        "description": DESCRIPTION,
        # These instructions are posted ONCE, at launch, and every agent reads them
        # independently of the role templates — so they must state the SAME review
        # protocol the role docs enforce. A stale rule here ("any comment clears an
        # item") would be read as authoritative and would undo the two-person review
        # gate no matter what ROLE-CPU / ROLE-ANALYST / HEARTBEAT say.
        "instructions": (
            "Multi-agent focus area for benchmark optimization.\n\n"
            "Post types: [PROPOSAL], [RESULT], [DISCUSSION], [AUDIT], [SUGGESTION]\n"
            "Review tags (COMMENTS on a [PROPOSAL], not posts): [REVIEW-OK], [REVIEW-BLOCK].\n"
            "A review is a comment whose FIRST line begins with one of those two tags plus\n"
            "real reasoning. Any other comment is ordinary discussion and clears nothing.\n\n"
            "Rules:\n"
            "- Review before claiming: an item is claimable only after a NON-AUTHOR [REVIEW-OK]\n"
            "  on its proposal post, with no unresolved [REVIEW-BLOCK]. Never review your own\n"
            "  proposal, and never claim an item whose ONLY [REVIEW-OK] is your own — a second\n"
            "  independent [REVIEW-OK] makes it claimable by anyone, including a reviewer.\n"
            "- One [REVIEW-BLOCK] outweighs any number of [REVIEW-OK]s. Only an analyst clears\n"
            "  a block, explicitly and with a stated reason — another [REVIEW-OK] does not.\n"
            "- One change per experiment: apply champion config, then ONE modification\n"
            "- Results are write-once: never overwrite\n"
        ),
    })
    if r.status_code < 300:
        print(f"  Created: {WORKSHOP_NAME}")
    else:
        print(f"  Error: {r.status_code} — {r.text[:200]}")
        sys.exit(1)

    # ── Step 2: Register Agents + Create Directories ────────
    print(f"\n[2/6] Setting up {len(AGENTS)} agents...")
    agent_tokens = {}
    for name, (desc, role, server, gpu) in AGENTS.items():
        token = setup_agent(name, desc, role, server, gpu)
        agent_tokens[name] = token

    # ── Step 3: Subscribe Agents ────────────────────────────
    # Subscribe must include X-Agent-Name so the server knows WHICH agent is
    # subscribing — otherwise it returns {subscribed: false} silently and
    # workshop.subscriber_count stays at 0.
    print(f"\n[3/6] Subscribing agents to {WORKSHOP_NAME}...")
    subscribed = 0
    for name in AGENTS:
        r = requests.post(
            f"{API}/workshops/{WORKSHOP_NAME}/subscribe",
            headers={**HEADERS, "X-Agent-Name": name},
        )
        if r.status_code < 300:
            action = r.json().get("action", "")
            if action in ("subscribed", "already_subscribed"):
                subscribed += 1
            else:
                print(f"  Warning: {name} subscribe returned {r.status_code} {r.text[:120]}")
        else:
            print(f"  Warning: {name} subscribe returned {r.status_code} {r.text[:120]}")
    print(f"  Subscribed {subscribed}/{len(AGENTS)} agents")

    # ── Step 4: Create Main Workspace ───────────────────────
    print("\n[4/6] Creating main workspace...")
    ws = requests.post(f"{API}/workspaces", headers=HEADERS, json={
        "title": f"{WORKSHOP_NAME}-coordination",
        "description": "Main coordination workspace — champion, results, knowledge, teams",
        "workshop": WORKSHOP_NAME,
        "visibility": "public"
    })
    if ws.status_code >= 300:
        print(f"  Error: {ws.status_code} — {ws.text[:200]}")
        sys.exit(1)
    ws_id = ws.json()["id"]
    print(f"  Workspace ID: {ws_id}")

    # Save workspace ID and run metadata locally
    (ROOT / "WORKSPACE_ID").write_text(ws_id)
    (ROOT / "WORKSHOP_NAME").write_text(WORKSHOP_NAME)
    (ROOT / "run_metadata.json").write_text(json.dumps({
        "run_id": RUN_ID,
        "workshop": WORKSHOP_NAME,
        "workspace_id": ws_id,
        "prefix": PREFIX,
        "api": API,
        "created_at": NOW,
        "template": str(TEMPLATE_DIR),
        "ablation": str(ROOT),
        "protein": PROTEIN_TARGET or PROTEIN_PLACEHOLDER,
        "task_type": _task_type,
        "agents": list(AGENTS.keys())
    }, indent=2))
    print(f"  Saved WORKSPACE_ID, WORKSHOP_NAME, and run_metadata.json")

    # ── Step 5: Populate Workspace ──────────────────────────
    print("\n[5/6] Populating workspace...")

    task_md, _task_md_source = _read_task_md(TASK_DIR)
    if _task_md_source != "TASK.md":
        print(f"  Note: Using {_task_md_source} (no TASK.md found)")
    put_file(ws_id, "task.md", task_md)

    put_file(ws_id, "champion.md", f"""---
metric_name: fitness
metric_value: null
test_metric_value: null
direction: minimize
experiment_id: null
run_id: "{RUN_ID}"
agent: null
updated_at: "{NOW}"
status: awaiting_baseline
settings: {{}}
---

# Champion Configuration

No champion yet. The first agent to run the baseline will establish it.
Check task/TASK.md for the optimization metric and the promotion contract.

## Anchors

Promotion requires BOTH anchors to advance together:

- `metric_value` — TRAIN fitness (= `mean_rel_steps`; lower is better).
- `test_metric_value` — HELD-OUT TEST fitness, from the same frozen candidate
  re-evaluated with `--split test`.

A candidate is promoted only when (a) train improves by >= 1e-3, (b) test improves
by > 1e-4 against `test_metric_value`, and (c) the test run has `is_valid == 1`.
A candidate that improves on train but fails (b) or (c) is `REJECTED_TEST`: its
mechanism family is recorded in the team's `non_generalizable.md` and abandoned.

While `status: awaiting_baseline` is present there is no valid champion, and the
first candidate that is valid on BOTH splits seeds both anchors.
""")

    put_file(ws_id, "knowledge/patterns.md", f"""---
version: 1
updated_at: "{NOW}"
---

# Winning Patterns

(none yet — to be discovered)

# Dead Ends

| Axis | Best Delta | Why |
|---|---|---|

# Load-Bearing Parameters

(to be identified)
""")

    put_file(ws_id, "teams/roster.md", f"""---
teams: {{}}
updated_at: "{NOW}"
phase: planning
---

# Team Roster

Teams formed during Phase 2 discussion.
""")

    for name, (desc, role, server, gpu) in AGENTS.items():
        put_file(ws_id, f"agents/{name}.md",
                 f"---\nagent: {name}\nrole: {role}\nserver: {server}\ngpu: {gpu}\n"
                 f"status: idle\nlast_seen: null\nteam: null\n---\n")

    # ── Step 6: Post Kickoff ────────────────────────────────
    print("\n[6/6] Posting kickoff discussion...")

    # Read task definition to generate kickoff content
    import yaml
    task_content, _ = _read_task_md(TASK_DIR)
    task_parts = task_content.split("---")
    if len(task_parts) >= 3:
        task_meta = yaml.safe_load(task_parts[1]) or {}
        task_name = task_meta.get("name", "optimization")
        task_desc = task_meta.get("description", "Optimize the benchmark metric.")
    else:
        task_name = "optimization"
        task_desc = "Optimize the benchmark metric."

    # Extract first section after frontmatter as the problem description
    task_body = "---".join(task_parts[2:]) if len(task_parts) >= 3 else task_content
    problem_section = task_body.split("\n## ")[0].strip()  # First section before next ##

    kickoff = requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP_NAME,
        "title": "[DISCUSSION-TRIGGER] Cold-start bootstrap — form hypothesis-based teams",
        "content": f"""# Cold-Start Bootstrap

This post anchors the cold-start self-regroup: every agent that runs
before a roster is committed should contribute to this thread, then
the alphabetically-last analyst who participates writes
`teams/roster.md` per Step 0.25 of ROLE-ANALYST.

## The Task

{task_desc}

**Full task definition:** `task/TASK.md` in the workspace
**Negative-knowledge file (if shipped with task):** `task/EXPLORED.md`

{problem_section[:500]}...

## What each agent contributes

- **Dimension / hypothesis** you want the team structure to target. Describe
  it with `hypothesis / prediction / falsification` (see ROLE-ANALYST Step 0.3).
- **≥1 cold axis** per team (an axis with zero prior experiments in the
  workspace — see ROLE-ANALYST Step 0.25 cold-axis mandate).
- **Substantive new content per round**: a [GAPS]/[CONSTANTS]/[RANKED]
  post or comment that adds information nobody else has surfaced.
- **Cast a self-termination vote**: `[DISCUSS-MORE]` or `[DISCUSS-DONE]`
  as a comment ON THIS POST. Reform closes when ≥5 agents vote
  `[DISCUSS-DONE]`.

## How team reform closes

Per ROLE-ANALYST Step 0.25: when 5+ `[DISCUSS-DONE]` votes land, the
alphabetically-last analyst who has run in this rotation writes
`teams/roster.md` with 3 hypothesis-based teams (each with ≥1 cold
axis) and posts `[TEAM-REFORMED]`. This closes bootstrap.

No monitor intervention is required.

**Workspace:** `{ws_id}`
**System docs:** `system/reference/SKILL.md`, `system/reference/PHASES.md`, `task/TASK.md`
""",
        "notify_agents": list(AGENTS.keys()),
        "tags": ["phase:planning", "type:discussion-trigger", "cold-start"]
    })

    if kickoff.status_code < 300:
        post_id = kickoff.json().get("post", {}).get("id", "unknown")
        print(f"  [DISCUSSION-TRIGGER] post: {post_id}")
    else:
        print(f"  Warning: {kickoff.status_code} — {kickoff.text[:200]}")

    # Save agent tokens
    tokens_path = ROOT / "agent_tokens.json"
    with open(tokens_path, "w") as f:
        json.dump(agent_tokens, f, indent=2)

    # ── Done ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Launch complete!")
    print("=" * 60)
    program_file = "runbook.md"
    task_type_label = _task_type
    print(f"""
  Experiment dir: {ROOT}
  Workshop:      {WORKSHOP_NAME}
  Workspace ID:  {ws_id}
  Agents:        {len(AGENTS)} created in {ROOT / 'agents'}
  Task type:     {task_type_label}

  Continue in the same Codex session that invoked launch.py.
  If launch.py was run manually, start one persisted orchestrator with:

    codex exec --dangerously-bypass-approvals-and-sandbox \\
      -C {TEMPLATE_DIR} \\
      -m gpt-5.6-sol \\
      -c 'model_reasoning_effort="xhigh"' \\
      -c 'agents.default_subagent_model="gpt-5.6-sol"' \\
      -c 'agents.default_subagent_reasoning_effort="xhigh"' \\
      -c agents.max_concurrent_threads_per_session=10 \\
      "Read {ROOT / program_file} and execute continuously as the only top-level orchestrator. Use fresh native Codex subagents only for the materialized roster."
""")


if __name__ == "__main__":
    main()
