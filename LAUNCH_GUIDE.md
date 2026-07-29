# Launching AutoScientists on the gigaopt (sella) task — from scratch

This guide takes a **new operator** from nothing to a running AutoScientists (AS) optimization of the
molecular-geometry optimizer (`algo.py`), end to end: the ClawInstitute coordination server, the xTB
evaluation pool (Redis + distributed workers + tunnels), and the orchestrator launch.

**The evaluation plane for this run already exists and is SHARED.** You do **not** start Redis, you do
**not** start workers, and you do **not** touch `/home/tsypin/opt_problem_optbench`. Other clients are
on the same pool; flushing Redis or restarting workers destroys their in-flight evals. Your eval-plane
job is a **read-only preflight** ([Part B](#part-b--eval-plane-preflight-read-only)) plus deploying
this run's **dedicated client checkout**.

The concrete values for this run are pinned in
[Deployment parameters](#deployment-parameters-set-these-first); every command references them.

> Standing up your **own private** pool instead (not this run)? See the
> [Appendix](#part-c--appendix--standing-up-your-own-private-pool-not-this-run) or the
> `setup-xtb-worker-host` skill.

---

## 0. How the system is wired (read this first)

AS for this task has **two independent planes**. They never share a process and talk over the
network in exactly two ways: the Codex orchestrator launches native subagents, and the CPU-eval agents
`ssh` into the eval head to score candidates.

```
 COORDINATION PLANE  (no GPU, no xTB)                 EVALUATION PLANE  (xTB / CPU heavy)
 ┌───────────────────────────────────────┐           ┌──────────────────────────────────────────┐
 │ Coordinator host                       │           │ Eval head                                  │
 │                                        │  ssh +     │                                            │
 │ codex exec ─ orchestrator (runbook.md) │  scp algo  │  redis-server  (eval queue)                │
 │    │  spawns native Codex subagents:   │ ─────────▶ │  distributed_validate workers (consume)    │
 │    ├─ 1 monitor                        │            │  eval_candidate.py  (producer, per call)   │
 │    ├─ 6 cpu-eval  ───────────────────────────────▶ │  opt_problem sella checkout + molecules/   │
 │    └─ 3 analysts                       │            └──────────────────────────────────────────┘
 │                                        │                         ▲   (optional) reverse SSH tunnels
 │  ClawInstitute server (port 3000)      │                         │
 │    workshops / workspaces / posts      │           ┌─────────────┴───────────────┐
 │    = the agents' shared memory         │           │ extra worker hosts (0..N)   │
 └───────────────────────────────────────┘           │  workers → tunnel → eval head's Redis     │
                                                      └──────────────────────────────────────────┘
```

- **Coordination plane** runs on the **coordinator**: the `codex exec` orchestrator, the 10 Codex
  subagents (a fixed roster set by `launch.py`: 1 monitor + 6 cpu-eval + 3 analysts), and the
  **ClawInstitute** server — a local message-board/workspace API on `http://localhost:3000` that is
  the agents' shared brain. *No xTB here.*
- **Evaluation plane** runs on the **eval head**: a **Redis** queue, a pool of **xTB validation
  workers**, and `eval_candidate.py`. A CPU-eval agent scores a candidate by `scp`-ing `algo.py` to
  the eval head and running `eval_candidate.py`, which enqueues the per-molecule tasks to Redis; the
  worker pool computes them; the JSON score comes back on stdout. Evaluation is **deterministic**.
- **Extra worker hosts** add capacity by running workers that connect to the eval head's Redis over an
  SSH tunnel. For this run there are **four** of them (192 workers total), already running.

The coordinator and eval head **may be the same machine or two separate machines** — the planes are
decoupled. For this run the coordinator is your laptop and the eval head is `cpu-33`.

> **The evaluation plane is shared, long-lived infrastructure.** It was not started by this run and
> must not be stopped, flushed, or reconfigured by it. Everything you run against it in Parts B and F
> is read-only.

---

## Deployment parameters (set these first)

These are the **live** values for this run (pool verified up and idle 2026-07-27 13:22 MSK). Export
them in every shell where you run the commands below.

```bash
# --- Coordination plane ---
COORD_HOST=<your-laptop>          # runs ClawInstitute + the Codex orchestrator + the 10 subagents

# --- Evaluation plane (SHARED; must agree with system/templates/ROLE-CPU.md — see note) ---
EVAL_HOST=cpu-33                  # ssh alias for a002dc-0002: Redis head + eval_candidate.py
REDIS_HOST=localhost              # Redis host *as seen from the eval head*
REDIS_PORT=6385                   # eval Redis on a002dc-0002 (the old :6390 pool is DEAD)
SELLA_CHECKOUT=/home/tsypin/opt_problem_as_testgate_opus5   # THIS RUN's dedicated client checkout
EVAL_PYTHON=/home/tsypin/miniconda3/envs/gigaopt/bin/python
EVAL_MOLECULES_DIR=/home/tsypin/as_testgate_molecules   # MANDATORY on every eval command
```

> **Why `--molecules-dir` is mandatory on every eval command.** The client bakes **absolute** xyz
> paths into every Redis task, and the worker opens that path on **its own** filesystem. The worker
> hosts do not have `$SELLA_CHECKOUT`, so omitting the flag fails **250/250** molecules in ~2.6 s with
> `No such file or directory: .../molecules/xyz/<mol>_mm.xyz`. `$EVAL_MOLECULES_DIR` exists on all
> four worker hosts and its TRAIN/TEST metadata SHAs match the pool (`9ec64608…` / `8a1b4708…`,
> 250 molecules each). Read-only use of that directory is exactly what it is for.
>
> **What lives in `/home/tsypin/as_testgate_molecules`:** this run's **regenerated** split metadata —
> `train_XTB.json` and `test_XTB.json` (250 molecules each), plus an `xyz` **symlink** into the
> workers' repo geometries. The metadata was regenerated against this exact worker/`algo.py` pair, so
> the baseline is exactly self-consistent: the unmodified baseline `algo.py` reproduces the split
> metadata exactly, scoring **1.000000** on both splits (see B5/B6). **This path must
> exist, with identical contents, on every worker host as well as the eval head**, and that is
> precisely why `--molecules-dir` is mandatory on every eval command: it is the only way the worker
> resolves geometries and the only way it scores against the regenerated baseline.

**Worker pool (already running — do not start, stop, or reconfigure):**

| Host (alias → real) | Workers | Reaches the head via |
|---|---|---|
| `cpu-220` → a002dc-0004 (\*) | 48 | host-local `localhost:6389` SSH tunnel → `a002dc-0002:6385` |
| `cpu-217` → a002dc-0005 | 48 | same |
| `cpu-128` → a002dc-0006 | 48 | same |
| `cpu-149` → a002dc-0007 | 48 | same |
| **total** | **192** | |

(\*) `cpu-220` (a002dc-0004) **also hosts a second, unrelated 48-worker pool** belonging to another user
on a different Redis port. Its 48 workers therefore share the box with someone else's 48 and its
effective throughput for our pool is lower than the other three hosts. **This is expected, not a
fault** — do not "fix" it, and do not touch the other pool.

Worker configuration (fixed, for reference only): xTB/GFN2, **one thread per worker**,
`JAX_ENABLE_X64=1`, `--max-tasks 50`, `--task-timeout 600`, `--max-rss-gb 16`, python
`/home/tsypin/miniconda3/envs/gigaopt/bin/python`, repo `/home/tsypin/opt_problem_optbench`.
Worker SHA `35af7756...`; TRAIN metadata SHA `9ec64608...`, TEST metadata SHA `8a1b4708...`,
250 molecules each.

> ⚠️ **`/home/tsypin/opt_problem_optbench` is the WORKERS' repo and is shared.** Never edit, redeploy,
> or `git`-operate in it, and never restart the workers that run from it. Your run's files go in
> `$SELLA_CHECKOUT` only.

> **Keep these in sync with the agents' contract.** The CPU-eval agents read the eval target from
> `system/templates/ROLE-CPU.md` — **all six** constants: `EVAL_HOST`, `EVAL_REDIS_HOST`,
> `EVAL_REDIS_PORT`, `SELLA_CHECKOUT`, `EVAL_PYTHON`, and `EVAL_MOLECULES_DIR` (the last one is the
> one whose absence fails *every* molecule — see the note above). Confirm the committed values equal
> the block above — the template is injected into every agent's `HEARTBEAT.md` at launch; editing a
> per-run copy afterward won't propagate:
> ```bash
> grep -nE "EVAL_HOST|EVAL_REDIS_HOST|EVAL_REDIS_PORT|SELLA_CHECKOUT|EVAL_PYTHON|EVAL_MOLECULES_DIR" system/templates/ROLE-CPU.md
> ```

**ssh-alias note:** aliases like `cpu-33` resolve **from your laptop only**. Anything that runs *on a
cluster host* and names another host must use the **real hostname** (`a002dc-0002`) or its IP.

---

## ⚠️ STOP — the shared checkout's `validate.py` is STALE (read before deploying anything)

`/home/tsypin/opt_problem_optbench/validate.py` is an **old, incorrect** validator (SHA `d4109064...`).
The **workers never import it**, so the pool is fine — but the **client** side (`eval_candidate.py`)
does, and a client running the stale validator scores candidates wrongly.

**Every client must use the corrected validator** from `opt_problem` commit
`46b91c20b54b3c7abe5d4ab6702bedd289ced08f` (SHA `e968f6db...`). A known-good copy is on the eval head at
`/home/tsypin/opt_problem_force_call_revalidation_20260724/evaluator/validate.py`.

That is why `$SELLA_CHECKOUT` is a **dedicated per-run checkout**, never the workers' repo: it holds
the corrected `validate.py` **plus this run's `eval_candidate.py`**. Verify both before launch:

```bash
# 1. corrected validator deployed? must print e968f6db...
ssh "$EVAL_HOST" "sha256sum $SELLA_CHECKOUT/validate.py"
#    known-good reference on the same host (must match):
ssh "$EVAL_HOST" 'sha256sum /home/tsypin/opt_problem_force_call_revalidation_20260724/evaluator/validate.py'
#    if it does NOT match, install the known-good copy into THIS RUN's checkout only:
ssh "$EVAL_HOST" "cp /home/tsypin/opt_problem_force_call_revalidation_20260724/evaluator/validate.py \
                     $SELLA_CHECKOUT/validate.py"

# 2. deployed eval_candidate.py == the branch copy? the two hashes must be equal
shasum -a 256 task-sella/eval_candidate.py
ssh "$EVAL_HOST" "sha256sum $SELLA_CHECKOUT/eval_candidate.py"
```

Do **not** "fix" `/home/tsypin/opt_problem_optbench/validate.py`. It is shared; leave it alone.

---

## 1. Roles

| Role | Host | Ports | Runs |
|---|---|---|---|
| Coordinator | `$COORD_HOST` | 3000 (ClawInstitute) | `codex exec` orchestrator + 10 subagents + ClawInstitute |
| Eval head | `$EVAL_HOST` (a002dc-0002) | 6385 (Redis) | Redis + `eval_candidate.py` (client) — **shared, pre-existing** |
| Workers ×4 | a002dc-0004/0005/0006/0007 | local 6389 → head 6385 (tunnel) | 48 xTB workers each — **shared, pre-existing** |

---

## 2. Prerequisites

**On the coordinator (`$COORD_HOST`):**
- [Node.js 22+](https://nodejs.org/) (ships `npx`) — for the ClawInstitute server.
- The [Codex CLI](https://developers.openai.com/codex/cli) (`codex`), logged in. Part D uses one
  persisted `codex exec` session and resumes that exact session ID after interruption.
- Python 3.9+ with `pip install -r requirements.txt` (just `requests`, `pyyaml`).
- This `autoscientists/` repo, checked out on the branch you want to run.
- Passwordless `ssh` to `$EVAL_HOST`.

**On the eval head (already provisioned — verify, don't build):**
- The `gigaopt` conda env at `$EVAL_PYTHON`. **`JAX_ENABLE_X64=1` is mandatory** — the workers set it;
  it changes the numerics.
- `$SELLA_CHECKOUT` — this run's dedicated client checkout: corrected `validate.py`, this branch's
  `eval_candidate.py`, and `molecules/`.
- Redis on `localhost:6385` with the 192-worker pool attached (Part B verifies this).

> The `setup-xtb-worker-host` skill provisions a host from scratch. **Not needed for this run** — the
> pool already exists; see the [Appendix](#part-c--appendix--standing-up-your-own-private-pool-not-this-run).

---

## Part A — Coordinator setup (coordination plane)

Run on `$COORD_HOST`.

```bash
# 1. Get the template on the branch you want to launch
cd <your-checkout>/autoscientists
git switch <branch>                    # the AS branch you intend to run
pip install -r requirements.txt        # requests, pyyaml

# 2. ClawInstitute: CHECK, do not start. A server has been up since Jul 24 (node PID 72118) and its
#    DB holds every prior run's forum history.
curl -sf http://localhost:3000/api/v1/workshops -o /dev/null && echo "clawinstitute UP — do nothing"
#    ONLY if that fails (server genuinely down) start it, foreground, e.g. in tmux:
# npx clawinstitute start              # serves http://localhost:3000 ; first run downloads from npm
```

> **Never run `npx clawinstitute reset`.** It wipes the shared DB — every prior run's forum history.
> See [Part D](#part-d--launch-the-run) for run-name hygiene against that same DB.

`launch.py` talks to ClawInstitute at `CLAWINSTITUTE_API` (default `http://localhost:3000/api/v1`) and
needs an **admin token**, which it resolves in this order:

1. a `.key` file in the template dir,
2. the `CLAWINSTITUTE_TOKEN` env var,
3. `~/.clawinstitute/token`.

Make sure one of these holds the token the server gave you, e.g.:

```bash
export CLAWINSTITUTE_TOKEN="<token printed/stored by clawinstitute>"
# sanity check the server + token:
curl -s -H "Authorization: Bearer $CLAWINSTITUTE_TOKEN" http://localhost:3000/api/v1/workshops | head -c 200
```

That's all for the coordinator until launch — the orchestrator (Part D) runs `launch.py` itself.

---

## Part B — Eval-plane preflight (read-only)

The pool already runs. **You start nothing here.** Run these checks from your laptop **immediately
before** launch — a stale check is worthless, the pool is shared and its state moves. Every command
below is read-only; the only write is deploying *this run's* `eval_candidate.py` into *this run's*
checkout (B1).

**B1. Deploy the branch-matching `eval_candidate.py` into `$SELLA_CHECKOUT`.** The copy that actually
runs lives on the eval head; the repo copy in `autoscientists/task-sella/eval_candidate.py` never
executes. From the coordinator, after `git switch <branch>` in the template:

```bash
scp task-sella/eval_candidate.py "$EVAL_HOST:$SELLA_CHECKOUT/eval_candidate.py"
shasum -a 256 task-sella/eval_candidate.py
ssh "$EVAL_HOST" "sha256sum $SELLA_CHECKOUT/eval_candidate.py"     # must match the line above
```

> **This branch's evaluator is aggregate-only by design.** `eval_candidate.py` emits **only** the
> aggregate score (`fitness` / `is_valid` / `mean_rel_steps` / `mean_rel_energy` /
> `max_final_energy_delta_kcal_mol` / `converged` + diagnostics); the per-molecule summary is
> deliberately disabled, so **agents never see a per-molecule breakdown** and no per-molecule
> artifacts are produced anywhere in the run. Deploy the copy that matches the orchestrator branch —
> it is also the copy that accepts `--split test`, which this run's promotion gate requires.
> Also re-run the [validator SHA check](#-stop--the-shared-checkouts-validatepy-is-stale-read-before-deploying-anything) — it is the single most common silent misconfiguration.

**B2. Redis alive, and how loaded is it?**

```bash
ssh "$EVAL_HOST" 'redis-cli -p 6385 ping'          # PONG
ssh "$EVAL_HOST" 'redis-cli -p 6385 dbsize'        # queue backlog — near 0 on an idle pool
ssh "$EVAL_HOST" 'redis-cli -p 6385 llen xtb_tasks'   # xTB queue depth; a big number = someone else is running
```

A non-trivial backlog is **not** an error — it means another client is using the pool, and your evals
will simply be slower. Do **not** flush it.

**B3. 192 workers connected, 48 per host.**

```bash
ssh "$EVAL_HOST" 'redis-cli -p 6385 info clients | grep connected_clients'   # ≈192 (+ your clients)
for h in cpu-220 cpu-217 cpu-128 cpu-149; do
  echo -n "$h: "; ssh "$h" 'pgrep -fc distributed_validate.worker'           # 48 each
done
```

**B4. Worker hash + `JAX_ENABLE_X64`** — the workers must all be the expected build, with x64 on
(it changes the numerics, so a mismatched worker silently produces different scores):

```bash
for h in cpu-220 cpu-217 cpu-128 cpu-149; do
  echo -n "$h worker: "; ssh "$h" 'sha256sum /home/tsypin/opt_problem_optbench/distributed_validate/worker.py'
  echo -n "$h x64:    "; ssh "$h" 'tr "\0" "\n" < /proc/$(pgrep -f distributed_validate.worker | head -1)/environ | grep JAX_ENABLE_X64'
done
# expect worker SHA 35af7756…  and  JAX_ENABLE_X64=1  on every host
```

Also confirm the split metadata the workers score against: TRAIN `9ec64608...`, TEST `8a1b4708...`,
250 molecules each. A mismatched TEST metadata SHA is a top suspect when *every* candidate comes back
`REJECTED_TEST` (see [Troubleshooting](#troubleshooting)).

**B5. Smoke-test the evaluator on TRAIN** (this is exactly what a CPU-eval agent does — one train
eval, **~95–125 s** on the healthy pool). Note `--molecules-dir`: it is **mandatory**, because the client
bakes absolute xyz paths into every Redis task and the worker opens them on its own filesystem, where
`$SELLA_CHECKOUT` does not exist. Use this exact command:

```bash
ssh "$EVAL_HOST" "cd $SELLA_CHECKOUT && JAX_ENABLE_X64=1 $EVAL_PYTHON eval_candidate.py \
  --program algo.py --split train \
  --molecules-dir $EVAL_MOLECULES_DIR \
  --redis-host localhost --redis-port 6385"
```

Expect a **single JSON line** ending stdout, aggregate-only (no `per_molecule` key on this branch):
`fitness`, `is_valid`, `mean_rel_steps`, `mean_rel_energy`, `max_final_energy_delta_kcal_mol`,
`converged`, `invalid_reason`, `duration_s`, `num_results`, `num_errors`, `lower_is_better`.

**Verified baseline (measured on this pool 2026-07-27, stock Sella `algo.py`, against the
**regenerated** metadata in `$EVAL_MOLECULES_DIR`) — TRAIN:**

| Field | Expected |
|---|---|
| `fitness` | **1.000000** |
| `is_valid` | `1` |
| `mean_rel_energy` | `1.0000000` |
| `max_final_energy_delta_kcal_mol` | ≈ `0` |
| `converged` | `1.0` |
| `num_results` / `num_errors` | `250` / `0` |
| `duration_s` | ≈ **95–125** |

> The invariant: **the unmodified baseline reproduces the split metadata exactly**, because that
> metadata was regenerated against this same worker build and `algo.py`. So the baseline scores
> exactly **1.000000**. Any other baseline value — in a guide, a note, or a seeded anchor — means the
> anchor was taken against different metadata and is wrong; correct it where you find it.

Failure modes: if it **hangs** → nothing is consuming the queue (recheck B2/B3, and that the port
really is `6385`). If it returns in **~2.6 s with 250/250 errors** and `No such file or directory:
.../molecules/xyz/<mol>_mm.xyz` → you dropped `--molecules-dir`. If `is_valid=0` unexpectedly → wrong
`validate.py` or stale `molecules/*_XTB.json` in `$SELLA_CHECKOUT`.

**B6. Smoke-test the TEST split too** — this run promotes on a held-out test gate (Part E), so the
test path must work *before* launch, not on the first provisional keep. Identical command, only
`--split test` differs:

```bash
ssh "$EVAL_HOST" "cd $SELLA_CHECKOUT && JAX_ENABLE_X64=1 $EVAL_PYTHON eval_candidate.py \
  --program algo.py --split test \
  --molecules-dir $EVAL_MOLECULES_DIR \
  --redis-host localhost --redis-port 6385"
```

**Verified baseline — TEST:** `fitness` **1.000000**, `is_valid=1`, `mean_rel_energy` `1.0000000`,
`max_final_energy_delta_kcal_mol` ≈ `0`, `converged` `1.0`, `num_results=250`, `num_errors=0`,
`duration_s` ≈ **34–49** (the test eval is *faster* than train).

Record both numbers: they are what the run's two anchors (`metric_value` = **1.000000**,
`test_metric_value` = **1.000000**) get seeded from, and they are how you tell a healthy cycle 1 from
a broken one. Both splits landing on exactly 1.000000 with `is_valid=1` and 250/250 is the
signature that the baseline reproduces the split metadata exactly. **Any other value** means you are
pointed at the wrong `--molecules-dir` or at unregenerated metadata, and must be fixed before launch.

**B7. Pool is clean afterwards.** Right after a completed eval:

```bash
ssh "$EVAL_HOST" 'redis-cli -p 6385 dbsize; redis-cli -p 6385 info clients | grep connected_clients'
```

`dbsize` back at **0** and **192** workers connected is the "pool drained cleanly, nothing leaked" signal.

---

## Part C — APPENDIX — standing up your OWN private pool (NOT this run)

> **Not applicable to this run.** This run uses the shared 192-worker pool described above; running
> anything in this appendix against it would disrupt other clients. This section exists only for the
> separate case where you are building a *private* pool on hosts you own.

<details>
<summary>Private-pool setup (Redis + babysat workers + tunnels)</summary>

Pick your own `$REDIS_PORT` (not 6385) and `NUM_WORKERS=<workers-per-host>`, sized to host cores.
Start Redis on your own eval head (ephemeral — no persistence), then a babysat worker pool:

```bash
scripts/start_redis.sh "$REDIS_PORT"   # = redis-server --port $REDIS_PORT --save "" --appendonly no

# scripts/babysit_validate.sh <redis_host> <redis_port> <local_redis_port> <num_workers> \
#                              [mode] [poll] [verbose] [log_dir] [python] [xtb_threads] \
#                              [max_tasks] [task_timeout_s] [max_task_rss_gb]
tmux new-session -d -s gigaevo_validate_workers \
  "cd '$SELLA_CHECKOUT' && source <conda>/etc/profile.d/conda.sh && conda activate gigaopt && \
   scripts/babysit_validate.sh localhost $REDIS_PORT $REDIS_PORT $NUM_WORKERS xtb 1.0 false \
     logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
```

Trailing args: `$NUM_WORKERS` worker processes; `xtb` mode; `1.0`s poll; non-verbose; logs in
`logs_validate/`; `1` xTB thread/worker (`OMP_NUM_THREADS=1`); **respawn each worker after `300`
tasks** (bounds xTB memory growth); kill a task after `2100`s; kill a task above `32` GiB RSS.

To add remote worker hosts, the babysitter opens the tunnel for you when the Redis host is remote
(`ssh -f -N -L <local_port>:localhost:$REDIS_PORT $EVAL_HOST`) — do not create tunnels by hand:

```bash
LOCAL_PORT=6380   # any free port on this worker host
tmux new-session -d -s gigaevo_validate_workers \
  "source <conda>/etc/profile.d/conda.sh && conda activate gigaopt && cd <opt_problem-checkout> && \
   scripts/babysit_validate.sh <eval-head-real-hostname> $REDIS_PORT $LOCAL_PORT $NUM_WORKERS xtb 1.0 false \
     logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
```

- Use the eval head's **real hostname** (an ssh alias may not resolve from the worker); if name
  resolution fails on that host, use its **IP**.
- `$LOCAL_PORT` just needs to be free on that host; the script errors if a matching tunnel exists.
- The `setup-xtb-worker-host` skill does all of the above from scratch (env, checkout, reachability,
  babysat tmux pool).

Verify a private worker host:

```bash
ssh <host> 'pgrep -fc distributed_validate.worker'
ssh <host> 'ss -ltn | grep -w 6380'
ssh <host> 'tail -n 20 <opt_problem-checkout>/logs_validate/*.log'   # no repeated Traceback/ERROR
```

</details>

---

## Part D — Launch the run

Back on the **coordinator**, from the template dir on the desired branch, with ClawInstitute running
(Part A) and the eval pool live (Part B).

**Run the orchestrator as one long-lived, persisted `codex exec` session that you keep open and
watch.** Give it one prompt:

```bash
cd <your-checkout>/autoscientists

# Preflight: verify this CLI exposes GPT-5.6-Sol and xhigh.
codex debug models | python3 -c \
  'import json,sys; m=next(x for x in json.load(sys.stdin)["models"] if x["slug"]=="gpt-5.6-sol"); assert "xhigh" in {r["effort"] for r in m["supported_reasoning_levels"]}; print(m["slug"], "xhigh")'

codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  -m gpt-5.6-sol \
  -c 'model_reasoning_effort="xhigh"' \
  -c 'agents.default_subagent_model="gpt-5.6-sol"' \
  -c 'agents.default_subagent_reasoning_effort="xhigh"' \
  -c agents.max_concurrent_threads_per_session=10 \
  "Read runbook.md and execute continuously as the only top-level orchestrator. Task: task-sella. Run name: <run-name>. Use fresh native Codex subagents only for the materialized roster."
```

It loops open-ended until you `Ctrl+C`. Record the session ID printed by `codex exec`. If the
process ends or dies, continue that exact session:

```bash
codex exec resume \
  --dangerously-bypass-approvals-and-sandbox \
  -m gpt-5.6-sol \
  -c 'model_reasoning_effort="xhigh"' \
  -c 'agents.default_subagent_model="gpt-5.6-sol"' \
  -c 'agents.default_subagent_reasoning_effort="xhigh"' \
  -c agents.max_concurrent_threads_per_session=10 \
  <SESSION_ID> \
  "Continue the existing open-ended runbook loop from persisted run state. Do not restart or reseed."
```

Do not start a fresh `codex exec` after a crash and do not use `--last`; resume the recorded ID.
A fresh context can misread the non-persisted `cycle_count` and reseed the search from baseline.
That previously wasted hours in `sella_nocheat_r1`, even though the champion data itself remained
protected by the pre-PUT gate.

The command's concurrency cap fits the default roster (1 monitor + 6 CPU-eval + 3 analysts). If
you deliberately launch a larger roster, set
`agents.max_concurrent_threads_per_session` to at least the largest parallel wave.

What happens:

1. The orchestrator reads `runbook.md` + the `task-sella` profile and runs the `launch_command` hook:
   `python3 launch.py <run-name> --task task-sella`.
2. `launch.py` **materializes a fresh sibling run directory** `../<run-name>/` with its own copy of
   `system/`, `task/`, `runbook.md`, the resolved `task-profile.md`, `champion/`, `logs/`, and one
   `agents/<name>/` dir per agent. The template stays clean. It registers the **roster** on
   ClawInstitute — `1 monitor + 6 cpu-eval + 3 analysts` (`<prefix>_monitor`, `<prefix>_cpu1..6`,
   `<prefix>_analyst1..3`) — writing each agent's `credentials.json` + `HEARTBEAT.md`, and creates the
   workshop + main workspace.
3. The orchestrator then forms teams, seeds one proposal per team, and begins the cycle loop:
   dispatch analysts (propose) + CPU-eval agents (claim → `scp` candidate to the eval head → `ssh`
   `eval_candidate.py --split train` → parse JSON → record). A train improvement (≥ 1e-3) is only
   **provisional**: the *same, frozen* candidate is then re-scored with `--split test`, and the
   champion advances only if the test score also improves (> 1e-4) **and** the test run is valid
   (see [Part E](#part-e--monitor-a-running-run)). Then health/stagnation checks. It runs
   **open-ended** until you Ctrl+C.

> Re-read the [parameters note](#deployment-parameters-set-these-first): the agents take the eval
> target from `system/templates/ROLE-CPU.md`. Confirm those values match `$EVAL_HOST` /
> `$REDIS_PORT` (**6385**) / `$SELLA_CHECKOUT` / `$EVAL_PYTHON` / `$EVAL_MOLECULES_DIR` **before** launching; edit the
> template (not a per-run copy) if not. Per-run tweaks go in the materialized `../<run-name>/` files,
> never the template.

The run uses **GPT-5.6-Sol at `xhigh`** for the parent and all rostered subagents. The child defaults
are explicit in the command, while each native `spawn_agent` call leaves model and effort unset so
no role is quietly downgraded.

### Run-name and ClawInstitute hygiene (do this before you type the prompt)

- **Do NOT run `npx clawinstitute start`** — a server has been up since Jul 24 (node PID 72118) and
  its DB holds every prior run's forum history. **Never run `npx clawinstitute reset`.**
- **Pick a run name that is absent from BOTH** the sibling dirs and the live workshop list:

  ```bash
  ls -d /Users/tsypin/Documents/gigaopt/*
  curl -s -H "Authorization: Bearer $CLAWINSTITUTE_TOKEN" \
    http://localhost:3000/api/v1/workshops | python3 -m json.tool | grep -i name
  ```

  `launch.py:466-472` validates **neither charset nor length** and will silently **reuse an existing
  workshop** if the derived name collides — the new run then writes into an old run's forum. Keep it
  **≤ 16 chars, lowercase, underscores only** (the agent prefix is truncated above 16 chars).
- **Never leave a materialized run dir inside the template checkout.** `launch.py` writes to
  `../<run-name>/`, i.e. a *sibling* of `autoscientists/`; a prior run (`sella_nocheat_r1/`) ended up
  inside the template dir, where it is untracked and **not gitignored**. Because of that, **never run
  `git add -A` in this repo** — check `git status` before any staging.

---

## Part E — Monitor a running run

### The promotion contract: a held-out TEST gate

A candidate becomes champion **only** when all three hold:

| # | Condition | Threshold |
|---|---|---|
| a | valid TRAIN run and `current_best_train − our_train ≥ KEEP_MARGIN` | `1e-3` |
| b | `current_best_test − our_test > TEST_MARGIN` | `1e-4` |
| c | the TEST run has `is_valid == 1` | held-out energy gate |

Ties and exact-margin improvements are **rejects**. The test eval re-scores the *same, already-frozen*
candidate (same remote path, same `eval_candidate.py`, only `--split test` added — never a re-edit
between the two), and runs **only** after a provisional train keep, so a train DISCARD never spends a
test eval. `champion.md` carries **two** anchors — `metric_value` (train) and `test_metric_value` —
which advance **together**, only on an accepted promotion.

A candidate that passes (a) but fails (b) or (c) is recorded as **`REJECTED_TEST`**, distinct from
`DISCARD`: it lands in the team workspace file `non_generalizable.md` (not `dead_ends.md`), and its
`[RESULT]` post states whether it failed on test *invalidity*, insufficient test *improvement*, or
both. Analysts read `non_generalizable.md` before proposing, and the mechanism family is abandoned.

### `NEAR_MISS` — passed everything, lost the race

There is one more way a candidate can clear all three conditions and still not become champion: it
loses the **promotion race**. Agents evaluate in parallel, so while one candidate is out on the
worker pool another can land on `champion.md` first. The cpu-eval agent detects this — either its
pre-put gate aborts (an equal-or-better champion arrived mid-eval) or its race guard fires
(`champion.md` no longer records this `exp_id`) — and records the outcome as **`NEAR_MISS`**.

What that means for you as operator:

- **The science succeeded.** Train gate passed, held-out test gate passed. Only the scheduling lost.
- **Nothing was promoted.** `champion.md` is not written and `champion/algo.py` is not copied — so
  a `NEAR_MISS` row in the ledger is *not* a champion advance and the anchors do not move.
- **It is auto-re-queued** as `{exp_id}_stack`, so the identical change is re-tested against the
  NEW champion without any operator action. You should see that `_stack` item appear in the team
  queue and report an outcome a rotation or two later.
- **It is never a fault.** It writes to neither `dead_ends.md` nor `non_generalizable.md`, never
  counts toward a dead end or a hypothesis falsification, and is excluded from the stagnation
  window (by construction, some candidate *was* promoted in that rotation).

Occasional `NEAR_MISS`es are a sign of a **healthy, productive** run: two agents in one rotation
both produced promotable candidates. If they become frequent, the only thing worth considering is
rotation sizing — the roster is racing itself, and some eval time is being spent re-testing changes
that already passed. `task/meta_diagnostics.py` prints `near_miss_count` and a
`has_frequent_near_miss` flag for exactly this.

**What this changes operationally.** Expect **roughly twice the eval load per provisional keep**
(a train eval is ~95–125 s and its test twin ~34–49 s on an idle pool; the pool is shared, so both stretch
under load), and expect `REJECTED_TEST` to appear regularly — it is a **healthy, informative**
outcome, not a failure: a real train improvement that did not generalize.
This gate exists because a prior run promoted a champion that was −5 % on train and **INVALID** on
held-out test.

### Commands

Everything is under the materialized run dir `../<run-name>/` (call it `$RUN`):

```bash
RUN=../<run-name>

# Per-experiment results ledger (canonical) + champion
tail -f $RUN/logs/experiments.jsonl
cat   $RUN/champion/SOURCE                 # who set the current champion + fitness
cat   $RUN/champion/algo.py                # the current best optimizer

# BOTH champion anchors (champion.md lives on ClawInstitute, not on disk)
curl -s -H "Authorization: Bearer $CLAWINSTITUTE_TOKEN" \
  "http://localhost:3000/api/v1/workspaces/$MAIN_WS_ID/files/champion.md" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['content'][:600])" \
  | grep -E "metric_value|test_metric_value|experiment_id"

# Outcome mix, incl. REJECTED_TEST, and both anchors over time
python3 -c "
import json,sys
for l in open('$RUN/logs/experiments.jsonl'):
    r=json.loads(l)
    print(r.get('cycle'), r.get('outcome'), r.get('fitness'), r.get('is_valid'),
          r.get('test_fitness'), r.get('test_is_valid'))
" | tail -40
python3 -c "
import json,collections
c=collections.Counter(json.loads(l).get('outcome') for l in open('$RUN/logs/experiments.jsonl'))
print(dict(c))
"

# Sessions + run-local native child completion artifacts
tail -f $RUN/logs/sessions.jsonl
ls      $RUN/logs/raw/

# Agents' shared brain (ClawInstitute): proposals, results, discussion
curl -s -H "Authorization: Bearer $CLAWINSTITUTE_TOKEN" \
  http://localhost:3000/api/v1/workshops | python3 -m json.tool | head

# Eval pool health — READ-ONLY (shared pool: never attach-and-Ctrl+C, never flush, never restart)
ssh "$EVAL_HOST" 'redis-cli -p 6385 ping; redis-cli -p 6385 llen xtb_tasks'
ssh "$EVAL_HOST" 'redis-cli -p 6385 info clients | grep connected_clients'
for h in cpu-220 cpu-217 cpu-128 cpu-149; do echo -n "$h: "; ssh "$h" 'pgrep -fc distributed_validate.worker'; done
```

`$MAIN_WS_ID` is the main workspace id `launch.py` printed at materialization; it is also in any
agent's `agents/<name>/credentials.json`.

`logs/experiments.jsonl` is one flat row per experiment:
`ts, cycle, exp_id, team, agent, axis, direction, value, fitness, is_valid, test_fitness,
test_is_valid, outcome, delta, post_id`. `fitness`/`is_valid` are the **train** numbers;
`test_fitness`/`test_is_valid` are populated only on candidates that reached the test gate (they are
`null` on train DISCARDs). `outcome` is exactly one of:

| `outcome` | Meaning |
|---|---|
| `KEEP` | Promoted — train **and** held-out test both passed, and it won the promotion race. |
| `NEAR_MISS` | Train **and** held-out test both passed, but it lost the promotion race, so it was **not** promoted (anchors unchanged, `champion/algo.py` unchanged). Positive evidence, in **neither** dead-end file; auto-re-queued as `{exp_id}_stack`. Healthy, not a fault. |
| `DISCARD` | A real, valid experiment that did not improve. Scientific evidence → team `dead_ends.md`. |
| `REJECTED_TEST` | Improved on train, failed the held-out test gate. Evidence about *generalization* → team `non_generalizable.md`, **never** `dead_ends.md`; closes that mechanism family. |
| `FAILED` | Infra/harness failure, an unapplied diff, or a scope abort. **It tested nothing** — it is never evidence, never counts toward a dead-end or stagnation streak, and its queue item is **re-queued**, not closed. |

A healthy run shows `experiments.jsonl` growing; a **mix** of `DISCARD`, `REJECTED_TEST`, occasional
`KEEP`, and the odd `NEAR_MISS`; and both `metric_value` and `test_metric_value` stepping down
**together** on each `KEEP` (never one without the other, and never on a `NEAR_MISS` — that one does
not promote). `REJECTED_TEST` is **normal and informative** — it is the gate doing its job, not the
search failing. Zero `REJECTED_TEST` over many cycles is as suspicious as all-`REJECTED_TEST` — see
[Troubleshooting](#troubleshooting). `NEAR_MISS` is likewise not a problem: see the section above.

A **burst of `FAILED`**, by contrast, is never a scientific signal: it means the **pool or harness is
unhealthy** (workers gone, wrong port, missing `--molecules-dir`, dead tunnel), *not* that the search
space is exhausted. Re-run the Part B preflight rather than reading anything into the outcome mix.

---

## Part F — Stop & clean up

```bash
# 1. Stop the orchestrator: Ctrl+C in the long-lived `codex exec` process (open-ended; only you stop it).
#    That is the ONLY teardown this run performs.

# 2. Eval plane: DO NOTHING. The pool, its Redis, and its workers are shared and pre-existing.
#    Do NOT kill workers, do NOT `tmux kill-session`, do NOT `redis-cli flushall`,
#    do NOT touch /home/tsypin/opt_problem_optbench.

# 3. ClawInstitute: leave it running. Do NOT `npx clawinstitute reset` — its DB holds every
#    prior run's forum history.
```

> The teardown commands for a **private** pool (kill the babysitter session, flush your own Redis)
> live in the [Appendix](#part-c--appendix--standing-up-your-own-private-pool-not-this-run) case only.

The materialized `../<run-name>/` directory is your run artifact — keep it. Nothing else needs
cleanup between runs.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `launch.py`: "No API key found" | ClawInstitute token unset (the server is already running) → `export CLAWINSTITUTE_TOKEN=…` (Part A). Do **not** `npx clawinstitute start` a second server. |
| **ALL cpu agents hanging on eval** | Wrong Redis port (`6390` is dead — it must be **6385**) or the pool is down → run the **Part B preflight** (B2–B4). Fix `EVAL_REDIS_PORT` in `system/templates/ROLE-CPU.md` **before** launch; a running run's agents have it baked into their `HEARTBEAT.md`. |
| **Every candidate comes back `REJECTED_TEST`** | Either the test anchor is mis-seeded (a `test_metric_value` seeded from something better than the real baseline makes (b) unreachable — compare it against the Part B6 baseline test score) or the deployed TEST metadata doesn't match (expected SHA `8a1b4708...`, 250 molecules). Also check the corrected `validate.py` is deployed (SHA `e968f6db...`). |
| **`champion.md` advances but `champion/algo.py` keeps the seed md5** | Step 7b1 propagation failed — the champion record moved without the code. The recorded champion is now a lie. Compare `md5 $RUN/champion/algo.py` against the seed and against the promoted `exp_id`'s candidate; stop and reconcile before more cycles. |
| **`logs/experiments.jsonl` stays empty** | The ledger writer hook never fired (the orchestrator writes this file, not the agents) → confirm the orchestrator is past step 5 and that `$RUN/logs/` exists and is writable; inspect the matching `logs/raw/*.json` completion artifact and, when needed, the full native child transcript attached to the parent Codex session. |
| Candidate scores `is_valid=0` unexpectedly | Wrong `validate.py` in `$SELLA_CHECKOUT`, or stale `molecules/train_XTB.json` / `test_XTB.json` there → re-run the validator SHA check and B5. |
| Evals fail with connection refused | Eval target mismatch between `ROLE-CPU.md` and the live pool → align `EVAL_HOST` / `EVAL_REDIS_HOST` / `EVAL_REDIS_PORT` (Deployment parameters + Part D note). |
| **Eval returns in ~2.6 s with `num_errors=250`**, `No such file or directory: .../molecules/xyz/<mol>_mm.xyz` | `--molecules-dir` was omitted (or points at a path the workers don't have) → always pass `--molecules-dir $EVAL_MOLECULES_DIR` (see B5). |
| **A burst of `FAILED` outcomes** | Infra, not science: pool/harness broken (workers gone, wrong port, missing `--molecules-dir`, dead tunnel). `FAILED` tested nothing — re-run the Part B preflight; the items should be re-queued, not written off as dead ends. |
| Zero `REJECTED_TEST` over many cycles, many `KEEP`s | Suspect the test gate is not actually running — check that `test_fitness`/`test_is_valid` are non-null in `experiments.jsonl` and that `--split test` works (B6). |
| **Frequent `NEAR_MISS` outcomes** | Not a fault — both gates passed and only the promotion race was lost; each one is auto-re-queued as `{exp_id}_stack`. It means the roster is racing itself, so some eval time goes to re-testing changes that already passed. Consider rotation sizing. Only investigate if the matching `_stack` items never appear in a team `queue.md`, or if `champion.md` *did* advance to a `NEAR_MISS` `exp_id` (that would be a promotion bug). |
| Remote worker can't reach the eval head by name | Pass the **real hostname** (`a002dc-0002`); if name resolution fails on that host, use its **IP**. |
| Whole loop dies silently after a long session | Resume the recorded session ID with `codex exec resume ... <SESSION_ID>` and the same model/effort/child-default flags. Do not start a fresh session or use `--last`; a fresh context plus non-persisted `cycle_count` can reseed the search from baseline (see Part D). |
| Session resumed on the wrong model | Always repeat `-m gpt-5.6-sol`, `model_reasoning_effort="xhigh"`, and both child defaults on `codex exec resume`; the Part D model-catalog preflight verifies support. |

---

## Quick-reference command card

Set the [Deployment parameters](#deployment-parameters-set-these-first) first, then:

```bash
# COORDINATOR — nothing to start; ClawInstitute is already up (never `start` a 2nd one, never `reset`)
curl -sf http://localhost:3000/api/v1/workshops -o /dev/null && echo "clawinstitute UP"
export CLAWINSTITUTE_TOKEN=<token>
cd <your-checkout>/autoscientists && git switch <branch> && pip install -r requirements.txt
codex debug models | python3 -c 'import json,sys; m=next(x for x in json.load(sys.stdin)["models"] if x["slug"]=="gpt-5.6-sol"); assert "xhigh" in {r["effort"] for r in m["supported_reasoning_levels"]}; print(m["slug"], "xhigh")'
codex exec --dangerously-bypass-approvals-and-sandbox -m gpt-5.6-sol \
  -c 'model_reasoning_effort="xhigh"' \
  -c 'agents.default_subagent_model="gpt-5.6-sol"' \
  -c 'agents.default_subagent_reasoning_effort="xhigh"' \
  -c agents.max_concurrent_threads_per_session=10 \
  "Read runbook.md and execute continuously as the only top-level orchestrator. Task: task-sella. Run name: <run-name>. Use fresh native Codex subagents only for the materialized roster."   # <=16 chars, unused name
# After interruption, repeat the same flags with:
# codex exec resume ... <SESSION_ID> "Continue the existing run; do not restart or reseed."

# EVAL PLANE — SHARED, ALREADY RUNNING. Deploy this run's client files, then PREFLIGHT (read-only).
scp task-sella/eval_candidate.py "$EVAL_HOST:$SELLA_CHECKOUT/eval_candidate.py"   # branch-matching
ssh "$EVAL_HOST" "sha256sum $SELLA_CHECKOUT/validate.py"          # must be e968f6db… (corrected)
ssh "$EVAL_HOST" "sha256sum $SELLA_CHECKOUT/eval_candidate.py"    # must equal the local branch copy
ssh "$EVAL_HOST" 'redis-cli -p 6385 ping; redis-cli -p 6385 dbsize; redis-cli -p 6385 llen xtb_tasks'
ssh "$EVAL_HOST" 'redis-cli -p 6385 info clients | grep connected_clients'        # ~192
for h in cpu-220 cpu-217 cpu-128 cpu-149; do echo -n "$h: "; ssh "$h" 'pgrep -fc distributed_validate.worker'; done   # 48 each
# baselines: train fitness 1.000000 (~95-125 s) · test fitness 1.000000 (~34-49 s) · both is_valid=1,
# converged=1.0, 250 results / 0 errors, max_final_energy_delta_kcal_mol ~0.
# --molecules-dir is MANDATORY: workers lack $SELLA_CHECKOUT, and $EVAL_MOLECULES_DIR
# (/home/tsypin/as_testgate_molecules, present on EVERY worker host) holds the REGENERATED split
# metadata the baseline reproduces exactly.  ANY baseline != 1.000000 = wrong dir / old metadata.
ssh "$EVAL_HOST" "cd $SELLA_CHECKOUT && JAX_ENABLE_X64=1 $EVAL_PYTHON eval_candidate.py --program algo.py --split train --molecules-dir $EVAL_MOLECULES_DIR --redis-host localhost --redis-port 6385"
ssh "$EVAL_HOST" "cd $SELLA_CHECKOUT && JAX_ENABLE_X64=1 $EVAL_PYTHON eval_candidate.py --program algo.py --split test  --molecules-dir $EVAL_MOLECULES_DIR --redis-host localhost --redis-port 6385"
ssh "$EVAL_HOST" 'redis-cli -p 6385 dbsize'                                       # 0 afterwards = pool clean

# NEVER on this run: start_redis.sh · babysit_validate.sh · pkill workers · redis-cli flushall ·
#                    any write to /home/tsypin/opt_problem_optbench · npx clawinstitute reset · git add -A
```
