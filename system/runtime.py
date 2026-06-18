"""system/runtime.py — opencode agent runtime.

This is the SINGLE chokepoint through which the orchestrator launches agent
sessions. Everything Claude-Code-specific that used to be expressed as
`Agent(...)` / `Task(subagent_type=...)` tool calls now goes through
``spawn_agent`` here, which shells out to ``opencode run``.

Design goals:
  * Be the *only* module that names ``opencode`` — the orchestrator and the
    task profiles stay framework-agnostic and talk to ``spawn_agent`` /
    ``AgentHandle`` instead.
  * Map the system's friendly model labels ("sonnet" / "opus") to opencode
    ``provider/model`` strings, overridable per-deployment via env vars.
  * Support both foreground (blocking) and background (``Popen``) launches, with
    per-agent ``CUDA_VISIBLE_DEVICES`` pinning.
  * Harvest the ``<promise>…</promise>`` completion sentinel out of the agent's
    stdout, and tee raw output to ``logs/raw/<agent>_<ts>.log``.

Nothing else in the codebase imports ``subprocess`` to launch agents — if you
need to change how agents are spawned, change it here.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Model mapping ────────────────────────────────────────────────────────────
#
# The rest of the system keeps saying "sonnet" / "opus"; we resolve those to
# opencode `provider/model` identifiers here. opencode resolves models via
# models.dev — verify the exact IDs available to you with:
#     opencode models anthropic
# Override either label per-deployment with the env vars below.

MODEL_MAP = {
    "sonnet": os.environ.get("AUTOSCI_MODEL_SONNET", "anthropic/claude-sonnet-4-6"),
    "opus": os.environ.get("AUTOSCI_MODEL_OPUS", "anthropic/claude-opus-4-8"),
    "haiku": os.environ.get("AUTOSCI_MODEL_HAIKU", "anthropic/claude-haiku-4-5"),
}

# The opencode executable. Override with AUTOSCI_OPENCODE_BIN if it is not on PATH.
OPENCODE_BIN = os.environ.get("AUTOSCI_OPENCODE_BIN", "opencode")

_PROMISE_RE = re.compile(r"<promise>(.*?)</promise>", re.DOTALL)


def resolve_model(model: str) -> str:
    """Map a friendly label ("sonnet"/"opus") to an opencode provider/model id.

    If ``model`` already looks like a provider/model string (contains "/"), it is
    passed through unchanged so callers can pin an exact model when they want to.
    """
    if "/" in model:
        return model
    return MODEL_MAP.get(model, MODEL_MAP["sonnet"])


class AgentHandle:
    """Handle to one launched agent session (foreground or background).

    Exposes the bits the orchestrator needs to log a session and harvest its
    result: ``wait()``, ``promise_received``, ``output``, ``returncode``,
    ``started_at``, ``log_path``.
    """

    def __init__(self, name: str, proc: subprocess.Popen, log_path: Path, started_at: str):
        self.name = name
        self._proc = proc
        self.log_path = log_path
        self.started_at = started_at
        self._output: Optional[str] = None
        self._lock = threading.Lock()
        # For background launches we stream stdout to the log file on a thread so
        # the child never blocks on a full pipe buffer.
        self._reader: Optional[threading.Thread] = None
        self._chunks: list[str] = []
        if proc.stdout is not None:
            self._reader = threading.Thread(target=self._pump, daemon=True)
            self._reader.start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        with open(self.log_path, "a", encoding="utf-8") as logf:
            for line in self._proc.stdout:
                with self._lock:
                    self._chunks.append(line)
                logf.write(line)
                logf.flush()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def poll(self) -> Optional[int]:
        """Return the exit code if the process has finished, else None."""
        return self._proc.poll()

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        """Block until the agent exits (or ``timeout`` seconds elapse).

        Returns the exit code, or None on timeout. On timeout the process is left
        running — the caller decides whether to ``terminate()``.
        """
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        if self._reader is not None:
            self._reader.join(timeout=10)
        return self._proc.returncode

    def terminate(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()

    @property
    def returncode(self) -> Optional[int]:
        return self._proc.returncode

    @property
    def output(self) -> str:
        """Captured stdout so far (also persisted to ``log_path``)."""
        with self._lock:
            if self._chunks:
                return "".join(self._chunks)
        # Foreground fallback: read whatever landed in the log file.
        if self.log_path.exists():
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        return ""

    @property
    def promise(self) -> Optional[str]:
        """The text inside the last ``<promise>…</promise>`` tag, if any."""
        matches = _PROMISE_RE.findall(self.output)
        return matches[-1].strip() if matches else None

    @property
    def promise_received(self) -> bool:
        return self.promise is not None


def _build_prompt(agent_name: str, focus_root: Path, mode: str, extra: str = "",
                  cuda: Optional[str] = None, body: Optional[str] = None) -> str:
    """Assemble the standard 3–5 line agent launch prompt.

    Mirrors the prompt shape the old runbook/LAUNCH hooks built inline. If
    ``body`` is given it fully replaces the default boot prompt (used for the
    biomlbench emergency-submission path, which deliberately bypasses HEARTBEAT
    Part 0).
    """
    if body is not None:
        return body
    lines = [f"You are {agent_name}.", f"FOCUS_ROOT={focus_root}"]
    if cuda is not None:
        lines.append(f"CUDA_VISIBLE_DEVICES={cuda}")
    lines.append(f"MODE={mode}")
    lines.append(f"Read {focus_root}/agents/{agent_name}/HEARTBEAT.md and follow it.")
    lines.append("Start at Part 0 (Mode Selector).")
    if extra:
        lines.append(extra.rstrip())
    lines.append(f"When done: <promise>{agent_name} cycle complete</promise>")
    return "\n".join(lines)


def spawn_agent(
    agent_name: str,
    focus_root,
    *,
    mode: str = "execute",
    model: str = "sonnet",
    cuda: Optional[str] = None,
    extra_prompt: str = "",
    prompt: Optional[str] = None,
    background: bool = False,
    agent: Optional[str] = None,
    extra_env: Optional[dict] = None,
    log_dir: Optional[Path] = None,
) -> AgentHandle:
    """Launch one agent session via ``opencode run``.

    Parameters
    ----------
    agent_name : the agent identity (e.g. ``ar_v1_gpu1``); used in the prompt and
        log filename.
    focus_root : the run directory (a ``Path`` or str).
    mode : ``"execute"`` or ``"discussion"`` (sets ``MODE=`` in the prompt and
        routes HEARTBEAT Part 0).
    model : friendly label or explicit ``provider/model``; resolved via
        :func:`resolve_model`.
    cuda : value for ``CUDA_VISIBLE_DEVICES`` (e.g. ``"0"``, ``"1"``, or ``""``
        for CPU-only). ``None`` ⇒ leave the parent's environment untouched.
    extra_prompt : profile-specific text appended to the default boot prompt.
    prompt : full prompt override; bypasses :func:`_build_prompt` entirely.
    background : ``True`` ⇒ ``Popen`` (returns immediately); ``False`` ⇒ blocks
        until the process exits before returning the handle.
    agent : optional opencode ``--agent`` name.
    extra_env : extra environment variables for the child.
    log_dir : where to write the raw log (default ``<focus_root>/logs/raw``).

    Returns an :class:`AgentHandle`.
    """
    focus_root = Path(focus_root)
    full_prompt = prompt if prompt is not None else _build_prompt(
        agent_name, focus_root, mode, extra=extra_prompt, cuda=cuda
    )

    cmd = [OPENCODE_BIN, "run", full_prompt,
           "--model", resolve_model(model),
           "--dangerously-skip-permissions"]
    if agent:
        cmd += ["--agent", agent]

    env = os.environ.copy()
    if cuda is not None:
        env["CUDA_VISIBLE_DEVICES"] = cuda
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    ld = Path(log_dir) if log_dir else (focus_root / "logs" / "raw")
    ld.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = ld / f"{agent_name}_{ts}.log"
    started_at = datetime.now(timezone.utc).isoformat()

    # Header line in the log for traceability.
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write(f"# {agent_name} @ {started_at}\n# cmd: {shlex.join(cmd[:2])} <prompt> "
                   f"--model {resolve_model(model)}\n# cwd: {focus_root}\n\n")

    proc = subprocess.Popen(
        cmd,
        cwd=str(focus_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    handle = AgentHandle(agent_name, proc, log_path, started_at)

    if not background:
        handle.wait()
    return handle


def wait_all(handles, timeout: Optional[float] = None):
    """Block until every handle in ``handles`` has exited (or timeout each).

    Returns the list of handles for convenience. Background handles that exceed
    ``timeout`` are left running; the caller inspects ``poll()`` / ``terminate()``.
    """
    for h in handles:
        h.wait(timeout=timeout)
    return list(handles)
