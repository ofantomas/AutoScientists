#!/usr/bin/env python3
"""orchestrator.py — deterministic AutoScientists orchestrator (opencode runtime).

This is the executable port of ``runbook.md``. Where the old design had a Claude
Code session *interpret* runbook.md and spawn agents with the `Agent`/`Task`
tool, this program runs the same control flow as plain Python and spawns agents
as ``opencode run`` subprocesses via ``system/runtime.py``.

It honours the cardinal rule from runbook.md:

    THE ORCHESTRATOR IS A PURE COORDINATOR. IT NEVER RUNS EXPERIMENTS.

The orchestrator only ever: launches agents, harvests their results, releases
stale claims, promotes champions (via the profile hook), and appends logs. It
never trains, fits, or writes a submission by hand.

Usage:
    python3 orchestrator.py <run-dir>

``<run-dir>`` is the directory ``launch.py`` created (it contains WORKSPACE_ID,
WORKSHOP_NAME, agents/, task/, task-profile.py, system/runtime.py).

Task-specific behaviour (stop criteria, GPU dispatch, champion promotion,
discussion rules) lives in ``task-profile.py`` — the Python port of the task's
``LAUNCH.md``. See ``MIGRATION.md`` for the orchestrator⇄profile contract.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

# Make ``system/runtime.py`` importable regardless of cwd.
_THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS / "system"))
sys.path.insert(0, str(_THIS))

import runtime  # noqa: E402  (system/runtime.py)


def parse_fm(resp_or_text):
    """Parse YAML frontmatter out of an API file response or a raw string.

    The ClawInstitute API does NOT parse YAML — every caller does it client-side.
    """
    text = resp_or_text.get("content", "") if isinstance(resp_or_text, dict) else resp_or_text
    parts = text.split("---")
    if len(parts) >= 3:
        return yaml.safe_load(parts[1]) or {}
    return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    """Shared state + helpers handed to every profile hook.

    A profile hook receives this object as ``orch`` and reaches everything it
    needs through it: ``orch.API``, ``orch.HEADERS``, ``orch.WORKSHOP``,
    ``orch.WS_ID``, ``orch.task_md``, ``orch.PREFIX``, ``orch.gpu_agents``,
    ``orch.analysts``, ``orch.parse_fm``, ``orch.spawn(...)``, and the free-form
    ``orch.state`` dict for profile-owned state (deadline clock, budget, …).
    """

    def __init__(self, run_dir: Path):
        self.FOCUS_ROOT = run_dir.resolve()
        self.parse_fm = parse_fm

        self.WS_ID = (self.FOCUS_ROOT / "WORKSPACE_ID").read_text().strip()
        self.WORKSHOP = (self.FOCUS_ROOT / "WORKSHOP_NAME").read_text().strip()
        tokens = json.loads((self.FOCUS_ROOT / "agent_tokens.json").read_text())
        self.TOKEN = os.environ.get("CLAWINSTITUTE_TOKEN", next(iter(tokens.values())))
        self.API = os.environ.get("CLAWINSTITUTE_API", "http://localhost:3000/api/v1")
        self.HEADERS = {
            "Authorization": f"Bearer {self.TOKEN}",
            "Content-Type": "application/json",
            "X-Agent-Name": "orchestrator",
        }

        # Task spec + identity.
        self.task_md = (self.FOCUS_ROOT / "task" / "TASK.md").read_text()
        self.task_meta = parse_fm(self.task_md)
        self.task_name = self.task_meta.get("name", "task")
        self.task_type = self.task_meta.get("task_type", "optimization")

        prefix_file = self.FOCUS_ROOT / "AGENT_PREFIX"
        self.PREFIX = (prefix_file.read_text().strip() if prefix_file.exists()
                       else self._infer_prefix())

        # Discover the actual agents created by launch.py (eval agents may be the
        # CPU-eval role "cpu" — as in task-sella — or the legacy GPU role "gpu").
        self.eval_agents, self.analysts, self.monitor = self._discover_agents()
        # Back-compat aliases: existing profiles reference orch.gpu_agents; the
        # sella profile uses orch.eval_agents / orch.cpu_agents. All point at the
        # same discovered list of eval agents.
        self.gpu_agents = self.cpu_agents = self.eval_agents

        self.cycle_count = 0
        self.state: dict = {}          # profile-owned scratch space
        self.profile = None            # set by load_profile()

    def _discover_agents(self):
        """Scan agents/ and classify by role (from AGENT.md frontmatter, with a
        name-based fallback). Returns (eval_agents, analysts, monitor)."""
        import re
        agents_dir = self.FOCUS_ROOT / "agents"
        if not agents_dir.is_dir():
            return ([f"{self.PREFIX}_cpu{i}" for i in range(1, 7)],
                    [f"{self.PREFIX}_analyst{i}" for i in (1, 2, 3)],
                    f"{self.PREFIX}_monitor")
        eval_agents, analysts, monitor = [], [], None
        for name in sorted(os.listdir(agents_dir)):
            amd = agents_dir / name / "AGENT.md"
            role = None
            if amd.exists():
                m = re.search(r"^role:\s*(\S+)", amd.read_text(), re.MULTILINE)
                role = m.group(1).strip() if m else None
            if role is None:  # name heuristic
                if "_monitor" in name:
                    role = "monitor"
                elif "_analyst" in name:
                    role = "analyst"
                elif "_cpu" in name or "_gpu" in name:
                    role = "cpu"
            if role == "monitor":
                monitor = name
            elif role == "analyst":
                analysts.append(name)
            elif role in ("cpu", "gpu"):
                eval_agents.append(name)
        return eval_agents, analysts, (monitor or f"{self.PREFIX}_monitor")

    def _infer_prefix(self) -> str:
        """Recover the agent prefix from the agents/ directory names."""
        agents_dir = self.FOCUS_ROOT / "agents"
        for name in os.listdir(agents_dir):
            if name.endswith("_monitor"):
                return name[: -len("_monitor")]
        # Fallback: the run-dir name, same normalisation launch.py uses.
        rid = self.FOCUS_ROOT.name.replace("-", "_").replace(".", "")
        return rid[:6] + rid[-10:] if len(rid) > 16 else rid

    # ── API helpers ──────────────────────────────────────────────────────────

    def get_file(self, path: str, ws_id: str = None):
        ws = ws_id or self.WS_ID
        return requests.get(f"{self.API}/workspaces/{ws}/files/{path}", headers=self.HEADERS).json()

    def put_file(self, path: str, content: str, ws_id: str = None, version=None):
        ws = ws_id or self.WS_ID
        headers = dict(self.HEADERS)
        if version is not None:
            headers["If-Match"] = str(version)
        return requests.put(f"{self.API}/workspaces/{ws}/files/{path}",
                            headers=headers, json={"content": content})

    def patch_file(self, path: str, frontmatter: dict, ws_id: str = None):
        ws = ws_id or self.WS_ID
        return requests.patch(f"{self.API}/workspaces/{ws}/files/{path}",
                             headers=self.HEADERS, json={"frontmatter": frontmatter})

    def post(self, **kwargs):
        kwargs.setdefault("submolt", self.WORKSHOP)
        return requests.post(f"{self.API}/posts", headers=self.HEADERS, json=kwargs)

    def get_posts(self, limit: int = 30):
        return requests.get(f"{self.API}/posts?workshop={self.WORKSHOP}&limit={limit}",
                            headers=self.HEADERS).json().get("data", [])

    def read_roster(self) -> dict:
        roster = parse_fm(self.get_file("teams/roster.md"))
        return roster.get("teams", {}) or {}

    # ── Spawning (delegates to runtime) ────────────────────────────────────────

    def spawn(self, agent_name: str, **kwargs) -> runtime.AgentHandle:
        """Launch an agent. Thin wrapper over ``runtime.spawn_agent`` that fills
        in FOCUS_ROOT. All other kwargs (mode, model, cuda, extra_prompt,
        background, prompt, extra_env) pass straight through."""
        return runtime.spawn_agent(agent_name, self.FOCUS_ROOT, **kwargs)

    # ── Logging ────────────────────────────────────────────────────────────────

    def log_session(self, agent: str, started_at: str, status: str,
                    promise_received: bool, role: str = None, team: str = None,
                    error: str = None):
        entry = {
            "agent": agent,
            "role": role,
            "team": team,
            "session_id": str(uuid.uuid4()),
            "cycle": self.cycle_count,
            "started_at": started_at,
            "ended_at": _now(),
            "status": status,
            "promise_received": promise_received,
            "error": error,
        }
        path = self.FOCUS_ROOT / "logs" / "sessions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def harvest_session(self, handle: runtime.AgentHandle, role: str = None, team: str = None):
        """Wait on (already-finished) handle, log a session line, return it."""
        status = "success" if handle.promise_received else (
            "error" if handle.returncode not in (0, None) else "timeout")
        self.log_session(handle.name, handle.started_at, status,
                         handle.promise_received, role=role, team=team)
        return handle

    # ── Health: stale-claim release (runbook Step 5f) ──────────────────────────

    def release_stale_claims(self, teams: dict, max_age_min: float = 30):
        for team_name, team_info in teams.items():
            ws = team_info["workspace_id"]
            q_fm = parse_fm(self.get_file("queue.md", ws_id=ws))
            for agent, claim in (q_fm.get("claims") or {}).items():
                if not claim:
                    continue
                claimed_at_s = claim.get("claimed_at", "")
                if "T" not in claimed_at_s:
                    continue
                claimed_at = datetime.fromisoformat(claimed_at_s)
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - claimed_at).total_seconds() / 60
                if age > max_age_min:
                    exp_id = claim.get("exp_id")
                    res = requests.get(
                        f"{self.API}/workspaces/{self.WS_ID}/files/results/{exp_id}.md",
                        headers=self.HEADERS)
                    if res.status_code == 404:
                        self.patch_file("queue.md", {f"claims.{agent}": None}, ws_id=ws)
                        print(f"  Released stale claim: {team_name}/{agent} ({exp_id})")

    def warn_empty_queues(self, teams: dict):
        for team_name, team_info in teams.items():
            q_fm = parse_fm(self.get_file("queue.md", ws_id=team_info["workspace_id"]))
            if not (q_fm.get("pending") or []):
                print(f"  WARNING: {team_name} queue empty")

    # ── Stagnation (runbook Step 5g) ───────────────────────────────────────────

    def keeps_in_last_n(self, n: int = 10):
        """(keeps, total) over the last ``n`` experiments in experiments.jsonl."""
        log_path = self.FOCUS_ROOT / "logs" / "experiments.jsonl"
        if not log_path.exists():
            return None, 0
        lines = [l for l in log_path.read_text().splitlines() if l.strip()]
        if len(lines) < n:
            return None, len(lines)
        last = [json.loads(l) for l in lines[-n:]]
        keeps = [x for x in last if x.get("outcome") == "KEEP"]
        return len(keeps), len(last)

    # ── Profile hook dispatch ──────────────────────────────────────────────────

    def hook(self, name: str, *args, **kwargs):
        """Call a profile hook, falling back to the DEFAULTS table when the
        profile does not define it."""
        fn = getattr(self.profile, name, None) or DEFAULTS.get(name)
        if fn is None:
            raise RuntimeError(f"No hook '{name}' in profile or defaults")
        return fn(self, *args, **kwargs)


# ── Default hook implementations (a profile overrides only what it cares about) ─

def _default_bootstrap_extras(orch):
    return None


def _default_discussion_policy(orch):
    # ("run" | "skip" | "parallel", extra_instructions)
    return ("run", "")


def _default_seeding_policy(orch, teams):
    return None


def _default_pre_cycle_check(orch):
    return False


def _default_analyst_prompt_extras(orch):
    return ""


def _default_monitor_extra_instructions(orch):
    return ""


def _default_gpu_dispatch(orch, teams):
    raise RuntimeError("gpu_dispatch is REQUIRED — the profile must define it")


def _default_champion_promotion(orch, teams):
    raise RuntimeError("champion_promotion is REQUIRED — the profile must define it")


def _default_stagnation_response(orch, cycle_count):
    print(f"  STAGNATION WARNING: 0 KEEPs in last 10 (cycle {cycle_count})")


def _default_periodic_hooks(orch, cycle_count):
    return None


def _default_exit_condition(orch):
    return False


def _default_final_report(orch):
    print("\n" + "=" * 60)
    print(f"  RUN COMPLETE — {orch.task_name}")
    print(f"  Cycles: {orch.cycle_count}")
    print("=" * 60)


DEFAULTS = {
    "bootstrap_extras": _default_bootstrap_extras,
    "discussion_policy": _default_discussion_policy,
    "seeding_policy": _default_seeding_policy,
    "pre_cycle_check": _default_pre_cycle_check,
    "analyst_prompt_extras": _default_analyst_prompt_extras,
    "monitor_extra_instructions": _default_monitor_extra_instructions,
    "gpu_dispatch": _default_gpu_dispatch,
    "champion_promotion": _default_champion_promotion,
    "stagnation_response": _default_stagnation_response,
    "periodic_hooks": _default_periodic_hooks,
    "exit_condition": _default_exit_condition,
    "final_report": _default_final_report,
}


def load_profile(orch: Orchestrator):
    """Import ``task-profile.py`` from the run dir as the active profile module."""
    profile_path = orch.FOCUS_ROOT / "task-profile.py"
    if not profile_path.exists():
        print(f"ERROR: {profile_path} missing — launch.py should have copied the "
              f"task's profile.py. Aborting.")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("task_profile", profile_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    orch.profile = mod
    return mod


# ── The control flow (runbook.md Steps 0–6) ─────────────────────────────────


def step3_discussion(orch: Orchestrator):
    """Step 3 — dimension discussion (profile decides run/skip/parallel)."""
    policy, extra = orch.hook("discussion_policy")
    if policy == "skip":
        print("[Step 3] Discussion skipped per profile.")
        return
    if policy == "parallel":
        print("[Step 3] Discussion runs in parallel with first GPU dispatch (profile).")
        # The cold-start fast path launches discussion alongside execution; here we
        # simply do not block. Agents pick up the [DISCUSSION-TRIGGER] post that
        # launch.py already created via HEARTBEAT Part 0 Check A2.
        return

    print("[Step 3] Launching discussion round (all non-monitor agents)...")
    handles = []
    for agent_name in orch.analysts + orch.gpu_agents:
        h = orch.spawn(agent_name, mode="discussion", model="sonnet",
                       extra_prompt=extra, background=True)
        handles.append(h)
    runtime.wait_all(handles, timeout=15 * 60)
    for h in handles:
        orch.harvest_session(h)


def step4_form_teams(orch: Orchestrator) -> dict:
    """Step 4 — launch the monitor to form teams, then verify + seed."""
    print("[Step 4] Launching monitor to form teams...")
    extra = orch.hook("monitor_extra_instructions")
    h = orch.spawn(orch.monitor, mode="execute", model="sonnet",
                   extra_prompt=extra, background=False)
    orch.harvest_session(h, role="monitor")

    teams = orch.read_roster()
    if len(teams) < 2:
        print(f"  WARNING: only {len(teams)} team(s) formed; expected >=2.")
    orch.hook("seeding_policy", teams)
    return teams


def step5_execution_loop(orch: Orchestrator, teams: dict):
    """Step 5 — the steady-state cycle."""
    while True:
        orch.cycle_count += 1
        c = orch.cycle_count
        print(f"\n{'=' * 60}\nCYCLE {c}\n{'=' * 60}")

        # 5a — pre-cycle check (may signal exit)
        if orch.hook("pre_cycle_check"):
            print("[5a] pre_cycle_check requested exit.")
            break

        # refresh roster each cycle (teams can reform)
        teams = orch.read_roster() or teams

        # 5b — launch analysts in parallel
        print("[5b] Launching analysts...")
        analyst_extra = orch.hook("analyst_prompt_extras")
        a_handles = [orch.spawn(a, mode="execute", model="sonnet",
                                extra_prompt=analyst_extra, background=True)
                     for a in orch.analysts]
        runtime.wait_all(a_handles, timeout=20 * 60)
        for h in a_handles:
            orch.harvest_session(h, role="analyst")

        # 5c — launch eval agents (profile-defined dispatch). A profile defines
        # either cpu_dispatch (CPU-eval, e.g. sella) or gpu_dispatch (GPU tasks).
        dispatch_hook = "cpu_dispatch" if hasattr(orch.profile, "cpu_dispatch") else "gpu_dispatch"
        print(f"[5c] Eval dispatch via {dispatch_hook} (profile)...")
        try:
            orch.hook(dispatch_hook, teams)
        except SystemExit:
            raise
        except Exception as e:
            print(f"  {dispatch_hook} error: {e!r}")

        # 5e — champion promotion (the ONLY orchestrator write to canonical paths)
        print("[5e] Champion promotion...")
        try:
            orch.hook("champion_promotion", teams)
        except Exception as e:
            print(f"  champion_promotion error: {e!r}")

        # 5f — health check
        print("[5f] Health check...")
        try:
            orch.release_stale_claims(teams)
            orch.warn_empty_queues(teams)
        except Exception as e:
            print(f"  health check error: {e!r}")

        # 5g — stagnation check
        keeps, total = orch.keeps_in_last_n(10)
        if keeps == 0 and total >= 10:
            orch.hook("stagnation_response", c)

        # 5h — periodic hooks
        try:
            orch.hook("periodic_hooks", c)
        except Exception as e:
            print(f"  periodic_hooks error: {e!r}")

        # 5i — loop control
        if orch.hook("exit_condition"):
            print("[5i] exit_condition met.")
            break


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nERROR: pass the run directory:\n  python3 orchestrator.py <run-dir>")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    if not run_dir.is_dir():
        print(f"ERROR: {run_dir} is not a directory.")
        sys.exit(1)
    if not (run_dir / "WORKSPACE_ID").exists():
        print(f"ERROR: {run_dir} has no WORKSPACE_ID — is this a launch.py run dir?")
        sys.exit(1)

    orch = Orchestrator(run_dir)
    print("=" * 60)
    print(f"  AutoScientists orchestrator (opencode)")
    print(f"  Run dir:   {orch.FOCUS_ROOT}")
    print(f"  Workshop:  {orch.WORKSHOP}")
    print(f"  Workspace: {orch.WS_ID}")
    print(f"  Prefix:    {orch.PREFIX}")
    print(f"  Task type: {orch.task_type}")
    print("=" * 60)

    load_profile(orch)

    # Step 1/2 — bootstrap extras (deadline clock, budget, GPU detection, …)
    orch.hook("bootstrap_extras")

    # Determine state (runbook Step 0): teams already formed?
    teams = orch.read_roster()
    if not teams:
        step3_discussion(orch)
        teams = step4_form_teams(orch)
    else:
        print(f"[Step 0] Resuming — {len(teams)} team(s) already in roster.")
        orch.release_stale_claims(teams)

    try:
        step5_execution_loop(orch, teams)
    except KeyboardInterrupt:
        print("\n[interrupt] Ctrl+C — stopping loop, running final report.")
    except SystemExit as e:
        print(f"\n[exit] {e}")

    # Step 6 — final report
    orch.hook("final_report")


if __name__ == "__main__":
    main()
