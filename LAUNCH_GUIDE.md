# Launching AutoScientists on a gigaopt eval task — from scratch

This guide takes a **new operator** from nothing to a running AutoScientists (AS) optimization of the
molecular-geometry optimizer (`algo.py`), end to end: the ClawInstitute coordination server, the xTB
evaluation pool (Redis + distributed workers + tunnels), and the orchestrator launch.

**This guide is parameterized.** It does not hardcode machine names, Redis ports, checkout paths, or
worker counts. Set the deployment parameters below for the cluster and run you are launching.

> If you only want to *add a worker host* to an already-running pool, skip to
> [Part C](#part-c--scale-out-remote-worker-hosts-optional) or use the `setup-xtb-worker-host` skill.

---

## 0. How the system is wired (read this first)

AS for this task has **two independent planes**. They never share a process and talk over the
network in exactly two ways: the orchestrator launches Codex subagents, and the CPU-eval agents
`ssh` into the eval head to score candidates.

```
 COORDINATION PLANE  (no GPU, no xTB)                 EVALUATION PLANE  (xTB / CPU heavy)
 ┌───────────────────────────────────────┐           ┌──────────────────────────────────────────┐
 │ Coordinator host                       │           │ Eval head                                  │
 │                                        │  ssh +     │                                            │
 │  codex   ── orchestrator (runbook.md)  │  scp algo  │  redis-server  (eval queue)                │
 │    │  spawns Codex subagents:          │ ─────────▶ │  distributed_validate workers (consume)    │
 │    ├─ 1 monitor                        │            │  eval_candidate.py  (producer, per call)   │
 │    ├─ 6 cpu-eval  ───────────────────────────────▶ │  eval checkout + molecules/                │
 │    └─ 3 analysts                       │            └──────────────────────────────────────────┘
 │                                        │                         ▲   (optional) reverse SSH tunnels
 │  ClawInstitute server (port 3000)      │                         │
 │    workshops / workspaces / posts      │           ┌─────────────┴───────────────┐
 │    = the agents' shared memory         │           │ extra worker hosts (0..N)   │
 └───────────────────────────────────────┘           │  workers → tunnel → eval head's Redis     │
                                                      └──────────────────────────────────────────┘
```

- **Coordination plane** runs on the **coordinator**: the `codex` orchestrator, the 10 Codex
  subagents (a fixed roster set by `launch.py`: 1 monitor + 6 cpu-eval + 3 analysts), and the
  **ClawInstitute** server — a local message-board/workspace API on `http://localhost:3000` that is
  the agents' shared brain. *No xTB here.*
- **Evaluation plane** runs on the **eval head**: a **Redis** queue, a pool of **xTB validation
  workers**, and `eval_candidate.py`. A CPU-eval agent scores a candidate by `scp`-ing `algo.py` to
  the eval head and running `eval_candidate.py`, which enqueues the per-molecule tasks to Redis; the
  worker pool computes them; the JSON score comes back on stdout. Evaluation is **deterministic**.
- Optionally, **extra worker hosts** add capacity by running more workers that connect to the eval
  head's Redis over an SSH tunnel.

The coordinator and eval head **may be the same machine or two separate machines** — the planes are
decoupled. You choose, via the parameters below.

---

## Deployment parameters (set these first)

Decide your topology and pool size, then export these in every shell where you run the commands
below. None of them are baked into this guide — they are your choices for this launch.

```bash
# --- Coordination plane ---
COORD_HOST=<coordinator-host>     # runs ClawInstitute + the Codex orchestrator + the 10 subagents

# --- Evaluation plane (injected into CPU-eval agent heartbeats by launch.py) ---
EVAL_HOST=<eval-head-host>        # ssh target that runs Redis + workers + eval_candidate.py
                                  #   (may be the same as COORD_HOST)
EVAL_REDIS_HOST=<redis-host>      # Redis host as seen from commands running on EVAL_HOST
REDIS_PORT=<eval-redis-port>      # port of the eval Redis on EVAL_HOST
EVAL_CHECKOUT=<path-on-eval>      # task evaluator checkout on EVAL_HOST (eval_candidate.py at root)
EVAL_PYTHON=<python-on-eval>      # python from the gigaopt env on EVAL_HOST

# --- Pool size (your call — size to host cores and how fast you want evals) ---
NUM_WORKERS=<workers-per-host>    # worker processes to run on each pool host
WORKER_HOSTS="<host> <host> ..."  # OPTIONAL extra worker hosts besides EVAL_HOST (space-separated;
                                  #   leave empty to run workers only on EVAL_HOST)
```

> **These values become the agents' eval contract.** `launch.py` reads `EVAL_HOST`,
> `EVAL_REDIS_HOST`, `REDIS_PORT`, `EVAL_CHECKOUT`, and `EVAL_PYTHON` from the coordinator shell and
> injects them into every CPU-eval agent's generated `HEARTBEAT.md`. If any are missing, launch
> aborts before creating a run directory.

**ssh-alias note (if your cluster uses them):** always pass the **real hostname** to
`babysit_validate.sh`, not an ssh alias — the alias may not resolve from a remote host. If a worker
host can't resolve the eval head's hostname at all, pass the eval head's **IP** instead for that host.

---

## 1. Roles

| Role | Host | Ports | Runs |
|---|---|---|---|
| Coordinator | `$COORD_HOST` | 3000 (ClawInstitute) | `codex` orchestrator + 10 subagents + ClawInstitute |
| Eval head | `$EVAL_HOST` | `$REDIS_PORT` (Redis) | Redis + xTB worker pool + `eval_candidate.py` |
| Extra workers (opt.) | each of `$WORKER_HOSTS` | local→`$REDIS_PORT` (tunnel) | xTB worker pool only |

---

## 2. Prerequisites

**On the coordinator (`$COORD_HOST`):**
- [Node.js 22+](https://nodejs.org/) (ships `npx`) — for the ClawInstitute server.
- The Codex CLI (`codex`), authenticated on the coordinator. Codex is not required on eval-only
  hosts.
- Python 3.9+ with `pip install -r requirements.txt` (just `requests`, `pyyaml`).
- This `autoscientists/` repo, checked out on the branch you want to run.

**On the eval head (and any extra worker host):**
- The `gigaopt` conda env (built from `opt_problem/environment.yml`; brings `xtb`, `ase`, `jax`,
  `numpy`, `scipy`, `redis`-server, `cloudpickle`). **`JAX_ENABLE_X64=1` is mandatory** — the workers
  set it; it changes the numerics.
- A task evaluator checkout at `$EVAL_CHECKOUT`.
- `ssh` reachability **from each extra worker host to the eval head** (passwordless), and **from the
  coordinator to the eval head**.

> The fastest way to provision a worker/eval host (conda env + checkout + reachability checks +
> babysat pool) is the **`setup-xtb-worker-host` skill** — see [Part C](#part-c--scale-out-remote-worker-hosts-optional).

---

## Part A — Coordinator setup (coordination plane)

Run on `$COORD_HOST`.

```bash
# 1. Get the template on the branch you want to launch
cd <your-checkout>/autoscientists
git switch <branch>                    # the AS branch you intend to run
pip install -r requirements.txt        # requests, pyyaml

# 2. Start the ClawInstitute coordination server (foreground; keep it running, e.g. in tmux)
npx clawinstitute start                # serves http://localhost:3000 ; first run downloads from npm
```

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

## Part B — Eval head setup (evaluation plane)

Run on `$EVAL_HOST`. Three things must be live **before** any candidate can be scored: **Redis**, the
**worker pool**, and **`eval_candidate.py` + molecule data**.

```bash
# 0. Activate the env (build it first if missing — see Part C / the setup-xtb-worker-host skill)
source <conda>/etc/profile.d/conda.sh
conda activate gigaopt
cd "$EVAL_CHECKOUT"                     # eval_candidate.py is at its root
```

**B1. Deploy the branch-matching `eval_candidate.py`.** The copy that actually runs lives in the
checkout above; the repo copy in `autoscientists/task-sella/eval_candidate.py` never executes. From
the coordinator, after `git switch <branch>` in the template, copy the version from the branch you
are launching:

```bash
scp autoscientists/task-sella/eval_candidate.py "$EVAL_HOST:$EVAL_CHECKOUT/eval_candidate.py"
```

> **Branch matters:** branches differ in whether `eval_candidate.py` emits a full `per_molecule`
> table (which feeds `logs/run_log.md` + the SMILES analysis) or aggregate-only. Deploy the copy that
> matches the orchestrator branch.

**B2. Verify the molecule baselines are fresh.** A git clone/bundle can miss or staledate the
*untracked* baselines. Confirm `molecules/train_XTB.json` and `test_XTB.json` are the correct
(fresh) baselines — a stale `train_XTB.json` makes a known-valid candidate score `is_valid=0` with a
spurious `max_final_energy_delta`. If in doubt, copy both from a known-good eval head.

**B3. Start Redis** (ephemeral — no persistence):

```bash
scripts/start_redis.sh "$REDIS_PORT"   # = redis-server --port $REDIS_PORT --save "" --appendonly no
```

**B4. Start the xTB worker pool** (consumers of the queue). Run in tmux so it survives your shell.
For a pool **on the eval head itself** (local Redis), set `<redis_host>=localhost` and
`local_redis_port = $REDIS_PORT`:

```bash
# scripts/babysit_validate.sh <redis_host> <redis_port> <local_redis_port> <num_workers> \
#                              [mode] [poll] [verbose] [log_dir] [python] [xtb_threads] \
#                              [max_tasks] [task_timeout_s] [max_task_rss_gb]
tmux new-session -d -s gigaevo_validate_workers \
  "cd '$EVAL_CHECKOUT' && source <conda>/etc/profile.d/conda.sh && conda activate gigaopt && \
   scripts/babysit_validate.sh localhost $REDIS_PORT $REDIS_PORT $NUM_WORKERS xtb 1.0 false \
     logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
```

What the trailing args mean (and their defaults): `$NUM_WORKERS` worker processes; `xtb` mode; `1.0`s
poll; non-verbose; logs in `logs_validate/`; `1` xTB thread/worker (`OMP_NUM_THREADS=1`); **respawn
each worker after `300` tasks** (bounds xTB memory growth); kill a task after `2100`s; kill a task
above `32` GiB RSS. The babysitter respawns dead workers and (for a **remote** Redis host) sets up the
SSH tunnel itself — see Part C.

**B5. Smoke-test the evaluator** against the running pool (this is exactly what a CPU-eval agent
does):

```bash
python eval_candidate.py --program algo.py --split train --redis-host "$EVAL_REDIS_HOST" --redis-port "$REDIS_PORT"
```

Expect a single JSON line ending stdout with `fitness`, `is_valid`, `mean_rel_steps`,
`max_final_energy_delta_kcal_mol` (and, on a per-molecule branch, a `per_molecule` array). If it
hangs, no workers are consuming (recheck B4); if `is_valid=0` unexpectedly, recheck B2.

---

## Part C — Scale out remote worker hosts (optional)

Add capacity by running more workers on other hosts that point at the eval head's Redis. The
babysitter opens the **reverse tunnel for you** when the Redis host is remote (`ssh -f -N -L
<local_port>:localhost:$REDIS_PORT $EVAL_HOST`), so you do **not** create tunnels by hand.

On each host in `$WORKER_HOSTS` (env active, `opt_problem` checked out), pick a free local port for
the tunnel (e.g. `6380`) and:

```bash
LOCAL_PORT=6380   # any free port on this worker host
cd <opt_problem-checkout>
tmux new-session -d -s gigaevo_validate_workers \
  "source <conda>/etc/profile.d/conda.sh && conda activate gigaopt && cd <opt_problem-checkout> && \
   scripts/babysit_validate.sh $EVAL_HOST $REDIS_PORT $LOCAL_PORT $NUM_WORKERS xtb 1.0 false \
     logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
```

- Use the eval head's **real hostname** (an ssh alias may not resolve from the worker). If a host
  can't resolve the hostname, pass the eval head's **IP** instead.
- `$LOCAL_PORT` just needs to be free on that host; the script errors if a matching tunnel already
  exists (pick another local port).
- **Easiest path:** the `setup-xtb-worker-host` skill provisions a host from scratch — builds the
  `gigaopt` env from `opt_problem/environment.yml`, clones `opt_problem` on the matching branch,
  verifies ssh/Redis reachability, and launches the babysat tmux pool. Use it for
  "add `<host>` to the pool"-type requests.

Verify a worker host:

```bash
ssh <host> 'pgrep -fc distributed_validate.worker'          # ~= $NUM_WORKERS
ssh <host> 'ss -ltn | grep -w 6380'                          # tunnel present (remote hosts; your LOCAL_PORT)
ssh <host> 'tail -n 20 <opt_problem-checkout>/logs_validate/*.log'   # no repeated Traceback/ERROR
```

---

## Part D — Launch the run

Back on the **coordinator**, from the template dir on the desired branch, with ClawInstitute running
(Part A) and the eval pool live (Part B). One command starts everything:

```bash
cd <your-checkout>/autoscientists
codex exec \
  --dangerously-bypass-approvals-and-sandbox \
  -C "$PWD" \
  "Read runbook.md and execute. Task: task-sella. Run name: <run-name>. Use Codex multi_agent_v1 subagents for all agent launches."
```

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
   `eval_candidate.py` → parse JSON → record), promote the champion on a valid, strictly-better
   `fitness` (margin ≥ 1e-3), then health/stagnation checks. It runs **open-ended** until you Ctrl+C.

> Re-read the [parameters note](#deployment-parameters-set-these-first): `launch.py` injects those
> values into the generated CPU-eval agent heartbeats. If you need a different eval target, change
> the exported env values before launching a new run.

Codex subagents inherit the orchestrator's configured model and are launched with extra-high
reasoning effort by the runbook.

---

## Part E — Monitor a running run

Everything is under the materialized run dir `../<run-name>/` (call it `$RUN`):

```bash
RUN=../<run-name>

# Per-experiment results ledger (canonical) + champion
tail -f $RUN/logs/experiments.jsonl
cat   $RUN/champion/SOURCE                 # who set the current champion + fitness
cat   $RUN/champion/algo.py                # the current best optimizer

# Per-molecule view (per-molecule branches): autoresearch run.log-style, rebuilt each cycle
less  $RUN/logs/run_log.md                 # per experiment: full per-molecule table
ls    $RUN/logs/molecule_results/          # raw per-agent shards (<agent>.jsonl)
column -t -s$'\t' $RUN/task/molecule_smiles.tsv | less   # mol_id → name/formula/SMILES (if present)

# Sessions + raw agent transcripts
tail -f $RUN/logs/sessions.jsonl
ls      $RUN/logs/raw/

# Agents' shared brain (ClawInstitute): proposals, results, discussion
curl -s -H "Authorization: Bearer $CLAWINSTITUTE_TOKEN" \
  http://localhost:3000/api/v1/workshops | python3 -m json.tool | head

# Eval pool health (on the eval head)
ssh "$EVAL_HOST" 'pgrep -fc distributed_validate.worker'
ssh "$EVAL_HOST" "redis-cli -p $REDIS_PORT info clients"
ssh "$EVAL_HOST" -t tmux attach -t gigaevo_validate_workers   # detach: Ctrl-b d
```

A healthy run shows `experiments.jsonl` growing, occasional `KEEP` outcomes lowering the champion
`fitness`, and (on per-molecule branches) `run_log.md` filling with per-molecule tables.

---

## Part F — Stop & clean up

```bash
# 1. Stop the orchestrator: Ctrl+C in the `codex exec` shell (open-ended; only you stop it).

# 2. Stop the eval pool (eval head + every worker host). Ctrl+C the babysitter, or:
ssh "$EVAL_HOST" 'tmux kill-session -t gigaevo_validate_workers'
ssh "$EVAL_HOST" 'pkill -f distributed_validate.worker'      # belt-and-suspenders
#   (the babysitter closes any SSH tunnel it opened on exit; repeat for each WORKER_HOST)

# 3. (Only if reusing the host for a fresh run) flush the eval queue:
ssh "$EVAL_HOST" "redis-cli -p $REDIS_PORT flushall"

# 4. (Optional) stop ClawInstitute and the Redis server when fully done.
```

The materialized `../<run-name>/` directory is your run artifact — keep it; nothing else needs
cleanup between runs except the Redis queue.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `launch.py`: "No API key found" | ClawInstitute not running or token unset → `npx clawinstitute start` and set `CLAWINSTITUTE_TOKEN` (Part A). |
| CPU-eval agents hang on eval | No workers consuming the queue → start/repair the pool (B4); check `pgrep -fc distributed_validate.worker`. |
| Candidate scores `is_valid=0` unexpectedly | Stale `molecules/train_XTB.json` on the eval head → copy fresh `train_XTB.json` + `test_XTB.json` (B2). |
| Evals fail with connection refused | Eval target mismatch between the deployment env and your actual pool → align `EVAL_HOST`/`EVAL_REDIS_HOST`/`REDIS_PORT` (Deployment parameters + Part D note). |
| `run_log.md` empty / no per-molecule | Eval head running an aggregate-only `eval_candidate.py` → redeploy the per-molecule branch's copy (B1). |
| Babysitter: "Found existing SSH tunnel … use another local port" | Local tunnel port already in use → pass a different `<local_redis_port>`. |
| Remote worker can't reach the eval head by name | Pass the **real hostname**; if name resolution fails on that host, use the eval head's **IP**. |
| Whole loop dies silently after a long session | Wrap the launch in an auto-restart loop; it resumes from the run dir + ClawInstitute state. |

---

## Quick-reference command card

Set the [Deployment parameters](#deployment-parameters-set-these-first) first, then:

```bash
# COORDINATOR ($COORD_HOST)
npx clawinstitute start                                  # coordination server :3000
export CLAWINSTITUTE_TOKEN=<token>
cd <your-checkout>/autoscientists && git switch <branch> && pip install -r requirements.txt
codex exec --dangerously-bypass-approvals-and-sandbox -C "$PWD" \
  "Read runbook.md and execute. Task: task-sella. Run name: <run-name>. Use Codex multi_agent_v1 subagents for all agent launches."

# EVAL HEAD ($EVAL_HOST) — env active, in $EVAL_CHECKOUT
scp <coord>:.../task-sella/eval_candidate.py "$EVAL_CHECKOUT/eval_candidate.py"      # branch-matching
scripts/start_redis.sh "$REDIS_PORT"
tmux new -d -s gigaevo_validate_workers \
  "scripts/babysit_validate.sh localhost $REDIS_PORT $REDIS_PORT $NUM_WORKERS xtb 1.0 false logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
python eval_candidate.py --program algo.py --split train --redis-host "$EVAL_REDIS_HOST" --redis-port "$REDIS_PORT"   # smoke test

# EACH EXTRA WORKER HOST ($WORKER_HOSTS) — env active, in the opt_problem checkout; LOCAL_PORT = any free port
tmux new -d -s gigaevo_validate_workers \
  "scripts/babysit_validate.sh $EVAL_HOST $REDIS_PORT $LOCAL_PORT $NUM_WORKERS xtb 1.0 false logs_validate \"\$CONDA_PREFIX/bin/python\" 1 300 2100 32"
```
