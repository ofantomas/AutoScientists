# task-profile.md — molopt-relax-steps

This profile fills in the hooks from `runbook.md` for **open-ended optimization** of a compact
molecular-geometry optimizer (`task_type: optimization`) — the goal is to drive `fitness`
(`mean_rel_steps`, lower is better) down indefinitely, subject to a hard energy validity gate
(`mean_rel_energy >= 1.0`). There is no wall-clock deadline; the loop runs until user interrupt.

**Promotion is gated on held-out TEST, not on train alone.** A candidate becomes champion only if it
improves on train AND improves on the held-out test split AND is still valid there — see
`champion_promotion`. A train-only win that does not carry to test is `REJECTED_TEST`, not a KEEP.
A candidate that clears BOTH gates but loses the promotion race is `NEAR_MISS` — positive evidence,
not a failure. This is the contract the whole profile is built around; every other hook assumes it.

**Key shape:** CPU-only deterministic evaluation. Candidates (`algo.py`) are scored by a remote
Redis-backed distributed validation worker pool — bounded by the worker count, NOT by any GPU
device. Evaluation is deterministic (no seed noise), so there is no multi-seed / noise-floor
machinery anywhere in this profile.

---

## Hook: launch_command

```bash
cd THIS_DIR
python3 launch.py <run-name> --task task-sella
```

`launch.py` walks up from `--task` looking for the nearest `LAUNCH.md` and copies it into the run
directory as `task-profile.md`. For this task that resolves to `task-sella/LAUNCH.md` (this file).

The candidate file `algo.py` and the evaluator (`eval_candidate.py`, `validate.py`, `molecules/`,
`metrics.yaml`) live in the **sella checkout on the eval-head host** — there is no upstream clone
step. The cpu-eval agents `scp` each candidate `algo.py` to the eval head and run
`eval_candidate.py` there; they do not need a local training repo.

---

## Hook: bootstrap_extras

No extras. The base bootstrap (`FOCUS_ROOT`, `WS_ID`, `tokens`, `HEADERS`, `API`, `task_md`, `PREFIX`)
is sufficient.

---

## Hook: discussion_policy

**OPTIONAL on cold start.** Skip the standalone discussion phase if the user wants the fastest
possible first eval dispatch. Instead, run it *in parallel* with the first cpu-eval agent's
evaluation in Step 5.

If you do run it standalone, cap discussion at ONE round of posts (3–8 minutes total) before
proceeding to seeding. Do not let it grow to 9+ posts; that delays the first candidate evaluation.

### Cold-start fast path

**Goal: first candidate dispatched to the eval pool within ~10 minutes of orchestrator start.**

| Window | Activity |
|---|---|
| 0–5 min | Read TASK.md, form roster (3 teams of ~3 agents) |
| 5–10 min | Each team posts **at least 2** seed proposals (2 is the floor, not a target to beat) — see `seeding_policy` |
| 10–15 min | **First cpu-eval agent dispatched** — shared baseline first; seeds become claimable as reviews land |
| 15–40 min | Parallel: evaluations continue; analysts post more proposals; discussion threads grow |
| 40–50 min | Harvest results, ledger the cycle, champion settles |

These windows are **soft signals, not alarms.** Extended thinking is on by default, so every agent
is slower than the numbers older runs were calibrated against, and a cpu-eval agent that scores a
**provisional train keep pays for a SECOND eval** (`--split test`) before it can promote, so a
winning cycle takes longer than a losing one by design. A cycle that runs long because candidates are winning on train is
healthy. Watch **two consecutive cycles** before concluding anything is wrong.

Rules:
1. Skip extended discussion before any evaluation.
2. Seed-queue minimum, not maximum — but the minimum is **two** proposals per team, not one: the
   seed count must exceed `REVIEW_CAP` (1 per agent per spawn) so review coverage distributes
   across the booting agents instead of concentrating in whichever one boots first.
3. Dispatch the first cpu-eval agent the moment the first queue.md is written (it holds the team's
   full seed set — the PUT is a single write, so it is never observed part-filled).
4. Eval concurrency is bounded by the remote distributed-validation worker pool, NOT by any device
   count. Multiple cpu-eval agents may dispatch concurrently; each candidate gets a unique remote
   path so they don't collide.
5. Do NOT block on perfect discussion before evaluation starts.

**`extra_discussion_instructions` (empty if discussion is skipped):** no additions beyond the base
prompt.

---

## Hook: seeding_policy

**Orchestrator-seeded.** After teams are formed, the orchestrator itself posts **at least two**
`[PROPOSAL]`s per team — one per seeded item — and writes them into the team's `queue.md` in a single
PUT. Dispatch the first cpu-eval agent as soon as the first team's `queue.md` has been written — do
not wait for all teams to be seeded.

### Three invariants this hook must not violate

**1. `[PROPOSAL]` first, queue entry second — never the reverse, never a substitute.**
The queue entry for a seed does not exist until the seed's own `[PROPOSAL]` post has been created
and has returned an id. That id — and only that id — goes in the item's `proposal_post`.

`proposal_post` MUST point at a post whose title starts with `[PROPOSAL]` and whose tags contain
`type:proposal`. It must **never** point at a `[DYNAMICS]`, `[DISCUSSION]`, `[AUDIT]`, `[GAPS]`,
`[TEAM-REFORMED]` or `[DISCUSSION-TRIGGER]` thread. Those are *analysis*, not proposals: they
describe the champion, they do not commit to a change, so a reviewer following the citation finds no
mechanism to evaluate and the item can never clear review. A seed is very often
*derived* from such a thread — that is healthy, and the right way to record it is the
`## Derived from` line in the `[PROPOSAL]` body, not the `proposal_post` field. If you find yourself
writing the id of a post you did not create in this hook, stop: the `[PROPOSAL]` has not been posted
yet.

**2. An item missing `axis` / `direction` / `value` is UNCLAIMABLE and must not be written.**
ROLE-CPU Step 3 rejects the claim on any item lacking those three tags (they feed the
empirical-priors ranking, the direction-diversity check and the failure-range check), so a
half-tagged seed does not merely degrade the cycle — it *blocks* a cpu-eval agent and costs a
rotation. Validate before the PUT and raise instead of writing a partial item — and do not quietly
drop it either, because invariant 3's floor still has to be met: replace it with a complete seed.

**3. At least TWO items per team — one seed per team does not spread review coverage.**
The seed count must exceed `REVIEW_CAP` (1 per agent per spawn — HEARTBEAT Part 1 (Boot), § Review
backlog) so review coverage distributes. With one seed per team, a single booting agent's two review
slots swallow two entire teams' queues, and every agent behind it finds nothing left to review; with
two or more per team the run-wide seed count sits well above any one agent's cap, so the first
cycle's endorsements come from several independent agents rather than from whichever agent boots
first. Two is a floor, not a target: seed the smallest number ≥ 2 of *complete* items you can name.
This floor is not a licence to pad the count with a half-tagged item, and invariant 2 is not a
licence to fall below the floor — a team that can only name one complete seed is a seed-spec failure
to fix, and the code below raises rather than PUT a short queue.

### REQUIRED queue-item schema (every seeded item, no exceptions)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | `exp_<team-prefix>_<mechanism>`, unique across the run |
| `description` | yes | one line naming the mechanism being changed |
| `priority` | yes | `high` / `medium` / `low` |
| `axis` | yes | the named knob or structure under test — the attribution key for the ledger |
| `direction` | yes | `increase` \| `decrease` \| `replace` \| `add` \| `restructure` |
| `value` | yes | the new numeric or symbolic value this experiment sets the axis to |
| `proposal_post` | yes | id of the `[PROPOSAL]` created immediately above — never an analysis thread |
| `diff` | yes | the exact code change, or an unambiguous statement of the mechanism and where in `algo.py` it applies (function/behaviour named, not a line number — the champion moves) |
| `review_status` | yes | `pending` \| `ok` \| `blocked`. Every new item is written `pending`; only `ok` is claimable |
| `proposed_at` | yes | ISO-8601 UTC. The review backlog is drained oldest-first on the **`[PROPOSAL]` post's `created_at`**; `proposed_at` is the recorded fallback for when that timestamp cannot be read, and an item with neither readable sorts LAST in every reviewer's backlog |
| `proposed_by` | yes | `orchestrator` for seeds. The reviewer-eligibility key: an item's proposer may not review it |
| `current_value` | recommended | what the champion does today, so the delta is legible |
| `cold_axis` | recommended | `true` at cold start — every axis is cold |

`review_status`, `proposed_at` and `proposed_by` are **required**, not recommended, because all three
are load-bearing for the review protocol: `review_status` decides claimability, `proposed_at` is the
fallback ordering key for the review backlog, and `proposed_by` decides who is *allowed* to review —
an item with no `proposed_by` has no author to exclude, so the proposer's own `[REVIEW-OK]` counts as
clearance and the two-person rule is void (ROLE-ANALYST requires it for exactly this reason). A field
marked "recommended" is a field writers omit — and an item nobody can order is an item nobody
reviews, which is indistinguishable from an item nobody wanted.

### Cold-start seeds are `review_status: pending` — there is no seeding exemption

Orchestrator-seeded items are written with **`review_status: pending`**, exactly like every other
queue item, and they are therefore NOT claimable until a non-author agent posts a `[REVIEW-OK]` on
their `[PROPOSAL]`.

An earlier version of this hook exempted seeds — they were written pre-cleared — on the grounds
that at cold start nobody has run yet, so no review can exist and the first cycle would deadlock
against the very agents waiting on the queue. **That rationale no longer holds.** Reviewing is now a
*spawn-time* obligation: every non-monitor agent drains the review backlog before its own work, and
cross-team review is allowed, so a seed posted by this hook is reviewable by the first agent of the
first rotation whatever team it lands on. And the first cpu-eval agent of a cold start runs the
**shared baseline**, which is not a queue item at all — it is never idled by an unreviewed queue.

Cold start is also where a review is worth the most. Seeds are written by the orchestrator from
TASK.md alone with no evidence behind them; they are the least-vetted mechanisms of the entire run.
A seed no agent will endorse is a seed that should not cost an eval.

**Never write `discussion_pending` on a new item.** Legacy items that carry it are read as
`review_status: pending` when it is `true` and as `ok` when it is `false`; nothing writes it any
more. An item carrying **neither** `review_status` nor `discussion_pending` resolves to `pending`,
never `ok` — a row nobody can show a review for has demonstrably not been reviewed, and defaulting
it to `ok` would make any malformed or hand-written item instantly claimable. (This is exactly what
`review_scan` in ROLE-CPU, `item_review_status` in HEARTBEAT and `review_status_of` in ROLE-ANALYST
implement; they must stay identical.) And do not re-seed through this hook later in the run:
seeding is a cold-start path, not a way to put an item into a queue without a review.

```python
from datetime import datetime, timezone
import re, yaml, requests

FENCE = "`" * 3   # built at runtime so the markdown fence around this block stays intact

# --- REQUIRED item schema. An item that cannot satisfy this is NOT written. ---
# `review_status` gates claiming, `proposed_at` is the fallback ordering key for the review
# backlog, and `proposed_by` names the one agent who may NOT review the item: an item missing
# any of the three is not merely untidy, it is unreachable (never claimable), unordered, or
# self-clearable by its own author.
REQUIRED_ITEM_FIELDS = ("id", "description", "priority", "axis", "direction",
                        "value", "proposal_post", "diff", "review_status", "proposed_at",
                        "proposed_by")

# Cold start seeds AT LEAST this many items per team. The floor must exceed REVIEW_CAP (1 review
# per agent per spawn) so review coverage distributes: with one seed per team a single booting
# agent's two slots cover two whole teams' queues and the agents behind it have nothing to review.
# Do not "simplify" this back to 1.
MIN_SEEDS_PER_TEAM = 2

def _safe_text(s):
    # Queue readers parse frontmatter with a naive content.split("---"), so a literal
    # "---" inside any field corrupts queue.md for every agent on the team.
    return re.sub(r"-{3,}", "--", str(s))

def validate_seed_item(item):
    """Return the item, or raise. Missing axis/direction/value => UNCLAIMABLE:
    fix the seed spec, never write a partial item to queue.md."""
    missing = [k for k in REQUIRED_ITEM_FIELDS
               if item.get(k) is None or item.get(k) == ""]
    if missing:
        raise ValueError(f"seed {item.get('id')!r} is unclaimable — missing {missing}")
    return item

def post_proposal(team_name, team_info, exp):
    """POST the [PROPOSAL] and return its id. Runs BEFORE the queue is written."""
    r = requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP,
        "title":   f"[PROPOSAL] {exp['id']}: {exp['description']}",
        "content": (
            f"## Mechanism\n{exp['rationale']}\n\n"
            f"## Axis\n"
            f"axis: {exp['axis']}\n"
            f"direction: {exp['direction']}\n"
            f"value: {exp['value']}\n"
            f"current_value: {exp.get('current_value', 'champion default')}\n\n"
            f"## Diff\n{FENCE}python\n{exp['diff']}\n{FENCE}\n\n"
            f"## Team\n{team_name}\n\n"
            # Cite the analysis thread HERE — not in the item's proposal_post field.
            f"## Derived from\n{exp.get('derived_from', 'cold-start reading of TASK.md + champion')}"
        ),
        "notify_agents": team_info["members"],
        "tags": [f"team:{team_name}", "type:proposal",
                 f"axis:{exp['axis']}", f"direction:{exp['direction']}"],
    })
    r.raise_for_status()
    body = r.json()
    post_id = (body.get("data") or body).get("id")
    if not post_id:
        raise RuntimeError(f"no post id for seed {exp['id']} — do NOT write the queue entry")
    return post_id


for team_name, team_info in teams.items():
    team_ws_id = team_info["workspace_id"]
    seeded = []
    specs = list(seed_experiments[team_name])    # list(): the floor check below needs len()

    # Check the floor BEFORE posting anything: a team that cannot name MIN_SEEDS_PER_TEAM complete
    # mechanisms is a seed-spec failure to fix, not a queue to write short.
    if len(specs) < MIN_SEEDS_PER_TEAM:
        raise ValueError(f"team {team_name!r} has {len(specs)} seed(s); cold start requires "
                         f">= {MIN_SEEDS_PER_TEAM} so review coverage distributes — name another "
                         f"complete mechanism, do NOT PUT a short queue")

    # Distinct ids, checked here because seeding more than one item per team is what makes a
    # copy-pasted id possible: two rows sharing an id make claims and ledger attribution ambiguous.
    _ids = [str(e.get("id", "")).strip() for e in specs]
    if len(set(_ids)) != len(_ids):
        raise ValueError(f"team {team_name!r} seeds have duplicate ids {_ids} — each item id must "
                         f"be unique across the run; fix the seed spec, do NOT post or queue it")

    # 0. Check EVERY spec in the team before posting ANY of them, so an unclaimable seed never
    #    leaves an orphan [PROPOSAL] the queue then fails to cite. Checking inside the post loop
    #    is not enough now that a team seeds several items: a bad spec reached on the second
    #    iteration would orphan the [PROPOSAL] already posted for the first.
    for exp in specs:
        for k in ("id", "description", "rationale", "axis", "direction", "value", "diff"):
            if not str(exp.get(k, "")).strip():
                raise ValueError(f"seed {exp.get('id')!r} is unclaimable — missing {k}; "
                                 f"fix the seed spec, do NOT post or queue it")

    for exp in specs:                            # >= MIN_SEEDS_PER_TEAM entries on cold start
        # 1. [PROPOSAL] FIRST. No post id => no queue entry.
        proposal_post_id = post_proposal(team_name, team_info, exp)

        # 2. Build against the REQUIRED schema; raise rather than write a half-tagged item.
        seeded.append(validate_seed_item({
            "id":            exp["id"],
            "description":   _safe_text(exp["description"]),
            "priority":      exp.get("priority", "high"),
            "axis":          exp["axis"],
            "direction":     exp["direction"],
            "value":         _safe_text(exp["value"]),
            "proposal_post": proposal_post_id,   # the post just created — never an analysis thread
            "diff":          _safe_text(exp["diff"]),
            "review_status": "pending",          # NOT claimable until a non-author [REVIEW-OK]
            # REQUIRED, not decorative: reviewers drain the backlog oldest-first by the
            # [PROPOSAL] post's `created_at` and fall back to this stamp when that read fails,
            # so it is what still gets the seed looked at in the first rotation.
            "proposed_at":   datetime.now(timezone.utc).isoformat(),
            # REQUIRED: reviewer eligibility. The proposer may not review its own item, so an
            # item with no proposed_by can be cleared by whoever wrote it.
            "proposed_by":   "orchestrator",
            "current_value": _safe_text(exp.get("current_value", "champion default")),
            "cold_axis":     True,
        }))

    # 3. Only now write the queue. Every item in it has a live [PROPOSAL] behind it, and there
    #    are at least MIN_SEEDS_PER_TEAM of them so no one booting agent can review the lot.
    assert len(seeded) >= MIN_SEEDS_PER_TEAM, \
        f"team {team_name!r} would be PUT with {len(seeded)} seed(s) — below the review floor"
    fm_yaml = yaml.safe_dump({"claims": {}, "pending": seeded}, sort_keys=False)
    queue_content = f"---\n{fm_yaml}---\n\n# Experiment Queue\n"
    parts = queue_content.split("---")          # same naive parse the agents use
    assert len(parts) >= 3 and yaml.safe_load(parts[1])["pending"] == seeded, \
        "queue frontmatter round-trip failed — do not PUT"
    requests.put(f"{API}/workspaces/{team_ws_id}/files/queue.md",
                 headers=HEADERS, json={"content": queue_content})
```

**Cost of the fix: one extra field-fill per seed, and one more `[PROPOSAL]` POST per team.** It is
still one POST per seed and a single PUT per team, with no waiting on comments *inside this hook* and
no second round — the first **dispatch** still happens as soon as the first team's PUT returns. The
first **claim** waits on a review, which the dispatched agents perform themselves on spawn; that is
not this hook's problem to solve, and it is not a reason to add a discussion round here.

Seed experiments propose **structural** changes to `algo.py` (step control / trust region, Hessian
init & update, line search / step acceptance, internal-coordinate construction, restart/re-blend) — not
fine retuning of a single constant, and **never** the convergence test (it is fixed and external; do not
propose changing or gaming it — see TASK.md "What counts as cheating"). Each `description` should name
the mechanism being changed; the optimized metric is `fitness` (`mean_rel_steps`), lower is better,
subject to the validity gate `mean_rel_energy >= 1.0` — and promotion additionally requires the
held-out test gate (`champion_promotion`), so prefer mechanisms that are chemistry-general over ones
tuned to the train molecules.

Because `direction` and `value` are required, each seed must be a **committed** change, not a
direction of enquiry: "restructure the trust-radius response to rho" is an axis, not an experiment —
say which response and to what. If a mechanism is genuinely not yet concrete enough to name a value,
it is not ready to seed; post it as `[DISCUSSION]` and seed something that is.

**Keep seed posts short:** `rationale` ≤ 120 words, `diff` ≤ 30 lines. A seed proposal is a pointer to
a mechanism, not an essay; long seeds delay the first dispatch and are not read more carefully.

**`extra_monitor_instructions`:** none — the monitor runs its default heartbeat health pass. It does
**not** form teams: `teams/roster.md` is written by the alphabetically-last analyst of the discussion
round (HEARTBEAT § 2b3 → ROLE-ANALYST Step 0.25).

---

## Hook: pre_cycle_check

No-op.

```python
def pre_cycle_check():
    return False
```

---

## Hook: analyst_prompt_extras

```python
analyst_prompt_extras = ""   # no additions beyond base
```

---

## Hook: cpu_dispatch

**CPU-eval dispatch. NO GPUs, NO CUDA, NO `nvidia-smi`.** Candidates are scored on a remote
Redis-backed distributed validation worker pool. Concurrency is bounded by the size of that worker
pool, NOT by any device — multiple cpu-eval agents may run concurrently. Each candidate `algo.py` is
`scp`'d to a **unique** remote path (`/tmp/cand_${AGENT}_${exp}.py`) so concurrent evals never
collide.

For each cpu-eval agent, launch in its own message:

```python
# Enumerate the ACTUAL roster from disk — launch.py sizes it via --cpu / --analysts,
# so never hardcode a range here.
import os
eval_agents = sorted(a for a in os.listdir(f"{FOCUS_ROOT}/agents") if "_cpu" in a)

# Dispatch every cpu-eval agent in the roster each cycle (size set by launch.py --cpu).
#
# Cold start: as soon as teams form, immediately dispatch one cpu-eval agent in
# MODE=execute to run the shared baseline, before extended discussion; then
# dispatch the rest against the seeded queues.

for agent_name in eval_agents:
    Task(
        subagent_type="general-purpose",
        description=f"{agent_name} experiment",
        prompt=(
            f"You are {agent_name}.\n"
            f"FOCUS_ROOT={FOCUS_ROOT}\n"
            f"MODE=execute\n"
            f"Read {FOCUS_ROOT}/agents/{agent_name}/HEARTBEAT.md and follow it.\n"
            f"Start at Part 0 (Mode Selector).\n"
            f"When done: <promise>{agent_name} cycle complete</promise>"
        ),
    )
```

No CUDA device pinning, no `CUDA_VISIBLE_DEVICES`, no per-device serialization. HEARTBEAT.md tells
agents how to discover identity, team, workspace, and the eval protocol (scp candidate → ssh
`eval_candidate.py` → parse JSON).

---

## Hook: cycle_ledger

**Called at the end of every cycle (runbook Step 5d), after all agents of the cycle have returned.**
This hook is what makes `logs/experiments.jsonl` exist. Nothing else writes it: agents write result
files and `[RESULT]` posts, and four consumers (the Step 5g stagnation check, the analysts'
`ROLE-ANALYST` coverage reader, `task/meta_diagnostics.py`, and the operator's `tail -f`) read this
ledger. If this hook does not run, all four silently degrade to "no data".

**Schema: one flat JSON object per experiment, per line. No nesting, no `{"experiments": [...]}`
wrapper.** Keys — always write all of them, using `None` for what you could not determine:

| key | meaning |
|---|---|
| `ts` | ISO-8601 UTC timestamp of the result |
| `cycle` | cycle number that produced it |
| `exp_id` | experiment id (e.g. `exp_tr_grow_band`) |
| `team` | team name from the post's `team:` tag; `None` for an infrastructure probe, which belongs to no team |
| `agent` | cpu-eval agent that ran it |
| `axis`, `direction`, `value` | the single variable changed, from the queue item |
| `fitness`, `is_valid` | TRAIN eval (`is_valid` is 0/1) |
| `mean_rel_energy` | TRAIN energy-gate diagnostic — WHY an `is_valid: 0` row was rejected |
| `test_fitness`, `test_is_valid` | HELD-OUT TEST eval; `None` when no test eval was run |
| `outcome` | `KEEP` \| `NEAR_MISS` \| `DISCARD` \| `REJECTED_TEST` \| `FAILED` |
| `delta` | signed train improvement vs the champion at eval time (negative = better) |
| `post_id` | id of the `[RESULT]` post the row was harvested from |

```python
import json, re
from datetime import datetime, timezone

RESULT_RE = re.compile(r"\[RESULT\]\s+(\S+?):\s*.*?=\s*([0-9.]+)\s*\((\w+)\)")

# INFRASTRUCTURE PROBES — ledgered, but never counted as science.
# The shared baseline evaluates the champion UNCHANGED to establish the anchor everyone else
# is measured against. Its number is worth keeping, but it is not a mechanism anybody proposed
# and its `KEEP` is not a promotion anybody earned: counting it credits a team with a win that
# did not happen AND parks a KEEP in the stagnation window, suppressing the regroup for ten
# experiments at precisely the moment the run needs one. It is posted teamless and
# `type:infrastructure` (ROLE-CPU); in the ledger the discriminator is the exp_id.
INFRA_EXP_IDS = ("baseline_shared",)

# Exact key set AND order of a ledger row. Consumers read positionally-by-name;
# do not add, remove or reorder without updating the schema table above.
LEDGER_KEYS = ("ts", "cycle", "exp_id", "team", "agent", "axis", "direction", "value",
               "fitness", "is_valid", "mean_rel_energy", "test_fitness", "test_is_valid",
               "outcome", "delta", "post_id")

def _first(*sources):
    """Take the first non-None value from a sequence of (dict, key) pairs.

    Belt-and-braces: for the METRIC fields the authoritative source is the result
    file's frontmatter (ROLE-CPU Step 5), but the queue's `completed:` row (ROLE-CPU
    Step 6) carries `val_score` / `test_fitness` / `outcome` / `is_valid` / `delta` /
    `mean_rel_energy` too. Falling back to it means a missing frontmatter key degrades to
    the queue value instead of writing null. Every result-file field the `completed:` row
    ALSO carries gets a fallback — a field with a single source is a field that becomes a
    silent null the one time that source is unreadable, and a null `is_valid` cannot be told
    apart from a genuine invalid run by anything downstream. `test_is_valid` is the one field
    with no second source, because Step 6 does not write it to the queue row; see its call
    site below.

    The PROVENANCE fields (`axis`, `direction`, `value`) invert that preference — see
    the call site below for why.

    NOTE on the held-out key: the queue `completed:` row's float used to be called
    `test_score`; it was renamed to `test_fitness` to stop it colliding with the
    sentinel key of the same name. Read `test_fitness` first and keep `test_score`
    as a tolerated LEGACY fallback so rows written before the rename still resolve.
    """
    for d, k in sources:
        v = (d or {}).get(k)
        if v is not None:
            return v
    return None

def already_ledgered(log_path, exp_id):
    """True when this exp_id already has a NON-FAILED row in the ledger.

    Post-id dedup alone is not enough: an experiment can be posted twice (duplicate
    [RESULT] posts for one eval), and because both rows resolve their fields from the
    SAME `results/{exp_id}.md`, they land identical except `post_id` — silently doubling
    the stagnation streak in ROLE-MONITOR and the analysts' dead-end / failure-range
    counters. Dedup on exp_id closes that.

    FAILED is deliberately exempt: a FAILED experiment tested nothing and is re-queued to
    `pending:` under the same id, so its genuine second [RESULT] must still be ledgered.
    Rows are read back from the file each call, so a duplicate appended earlier in THIS
    harvest is caught too (rows are written one-at-a-time, append-only).
    """
    try:
        with open(log_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("exp_id") == exp_id and (r.get("outcome") or "") != "FAILED":
                    return True
    except FileNotFoundError:
        pass
    return False

def _all_result_posts(seen):
    """Every workshop post, oldest included — paginated.

    A bare `limit=100` silently drops any [RESULT] older than the 100 most recent, and
    `.harvested_post_ids` cannot rescue them because they are never fetched: they are
    lost from experiments.jsonl permanently. That bites on a long run and on a resume
    that must catch up several cycles. Pages come newest-first, so we stop as soon as a
    whole page is already harvested — steady-state cost stays one request.

    A post deliberately left UNMARKED (one of OUR OWN unrecoverable [RESULT]s, below) keeps
    the early stop from firing on its page. That is the point: the extra page is what buys the
    retry — and it is why a post that can never produce a row for US (a foreign run's) must be
    marked as soon as it is recognised, or it holds that page open forever.
    """
    PAGE, MAX_PAGES = 100, 50
    posts, offset = [], 0
    for _ in range(MAX_PAGES):
        try:
            body = requests.get(f"{API}/posts?workshop={WORKSHOP}&limit={PAGE}&offset={offset}",
                                headers=HEADERS).json()
        except Exception as e:
            print(f"cycle_ledger: pagination stopped at offset {offset} ({e})")
            break
        page = body.get("data") or []
        if not page:
            break
        posts.extend(page)
        if all(p.get("id") in seen for p in page):
            break                                # rest of the history is older and already seen
        if not (body.get("pagination") or {}).get("hasMore"):
            break
        offset += len(page)                      # advance by what we actually got: monotone,
    return posts                                 # so a bad `pagination.offset` cannot loop us

def _mark_seen(seen, seen_path, pid):
    """Record a post id as HARVESTED — call only when the post can no longer produce a row.

    That means: after its row has been written, or after we have positively decided against
    it (foreign run, duplicate experiment). NEVER on PICKUP. This used to be appended the
    moment a [RESULT] was picked up, before the title was parsed, so a title that RESULT_RE
    could not match was marked harvested and its row was lost permanently — no row, no
    print, no possibility of retry. Marking follows the decision; it never precedes it.

    The foreign-run check is the one mark taken before the title is parsed, and it is not an
    exception to that rule: it decides on the post's `team:` TAG, so the post's fate is
    already fully known — it can never produce a row for us, and (unlike one of ours) it can
    never be recovered later either. Leaving it unmarked is what would be wrong: it would be
    re-fetched and re-warned every harvest forever and would hold the `_all_result_posts`
    early stop open on its page.
    """
    if pid in seen:
        return
    seen.add(pid)
    with open(seen_path, "a") as f:              # append-only, one write, crash-safe
        f.write(f"{pid}\n")

def _result_file_for_post(pid):
    """exp_id of the `results/*.md` whose frontmatter `post_id` is `pid`, or None.

    ROLE-CPU Step 8b backfills `post_id` into the result file, so the post id is a reliable
    REVERSE key into the experiment even when the post's free-text title is not. SEARCH
    first (one request); the prefix LIST + read is the fallback for a search backend that
    does not index frontmatter, bounded because this is a rare recovery path, not a scan.
    """
    def _confirm(path):
        """A hit is a CANDIDATE, not an answer. The id can appear in a file's body text, and
        filing a result under the wrong exp_id is worse than not recovering it at all — only
        the frontmatter `post_id` confirms the match."""
        if not (path.startswith("results/") and path.endswith(".md")):
            return False
        try:
            fm = parse_fm(requests.get(f"{API}/workspaces/{WS_ID}/files/{path}",
                                       headers=HEADERS).json())
        except Exception:
            return False
        return str(fm.get("post_id") or "") == str(pid)

    try:
        hits = (requests.get(f"{API}/workspaces/{WS_ID}/search?q={pid}",
                             headers=HEADERS).json() or {}).get("results") or []
        for h in hits:
            if _confirm(h.get("path") or ""):
                return (h["path"])[len("results/"):-len(".md")]
    except Exception as e:
        print(f"cycle_ledger: search for post {pid} failed ({e}) — falling back to LIST")
    try:
        files = (requests.get(f"{API}/workspaces/{WS_ID}/files?prefix=results/",
                              headers=HEADERS).json() or {}).get("files") or []
    except Exception as e:
        print(f"cycle_ledger: results/ LIST failed ({e}) — cannot recover post {pid}")
        return None
    # Newest first: a post we are recovering was written this cycle or the one before.
    files.sort(key=lambda f: str(f.get("updatedAt") or ""), reverse=True)
    for f in files[:40]:                         # bounded — recovery, not a full scan
        path = f.get("path") or ""
        if _confirm(path):
            return path[len("results/"):-len(".md")]
    return None

def _recover_unparsed_result(p, pid):
    """(exp_id, outcome) for a [RESULT] post whose TITLE RESULT_RE could not parse.

    A title is free text an agent composed; the row behind it is not optional. Two
    structured sources survive a mangled title, and both are written by the same agent in
    the same step: the post's TAGS (`type:result` + `outcome:<x>`, ROLE-CPU Step 8) and the
    result file that names this post_id (Step 8b). Fitness is deliberately NOT recovered
    from the title here — the title is exactly what we just failed to trust — it comes from
    the result file like every other metric.
    """
    tags = p.get("tags") or []
    outcome = next((t.split(":", 1)[1].strip().upper()
                    for t in tags if t.startswith("outcome:")), None)
    return _result_file_for_post(pid), outcome

def cycle_ledger(cycle_count):
    """Harvest this cycle's [RESULT] posts into logs/experiments.jsonl. Append-only,
    idempotent (dedup by post id AND by exp_id), crash-safe (one open+write per row)."""
    log_dir   = FOCUS_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path  = log_dir / "experiments.jsonl"
    seen_path = log_dir / ".harvested_post_ids"
    seen = set(seen_path.read_text().split()) if seen_path.exists() else set()

    # Queue `completed:` rows carry axis / direction / value / completed_by for each exp_id.
    meta = {}
    for team_name, team_info in teams.items():
        q_fm = parse_fm(requests.get(f"{API}/workspaces/{team_info['workspace_id']}/files/queue.md",
                                     headers=HEADERS).json())
        for it in (q_fm.get("completed") or []):
            if it.get("id"):
                meta[it["id"]] = {"team": team_name, **it}

    n_new = 0
    # OLDEST FIRST. `_all_result_posts` returns pages newest-first, and appending in that
    # order writes the ledger backwards: every consumer takes a `rows[-N:]` window, so a
    # backwards file makes each of them read the OLDEST N rows of the run and call it "the
    # last N experiments" — which silently inverts the stagnation verdict. The file is
    # chronological; `reversed()` is what makes it so.
    for p in reversed(_all_result_posts(seen)):
        pid, title = p.get("id"), p.get("title", "")
        if pid in seen or not title.startswith("[RESULT]"):
            continue
        # No post is marked harvested on PICKUP. See _mark_seen: marking before we know the
        # post's fate is what loses rows. Every path below either writes a row and then marks,
        # or positively decides against the post and marks, or gives up and leaves it unmarked
        # for the next harvest.
        #
        # FOREIGN-RUN CHECK RUNS FIRST, before the title parse. The workshop DB is shared across
        # runs, so [RESULT] posts belonging to other runs reach this loop. Foreignness is a
        # decision ABOUT the post (its team tag), not a parse of it, so deciding it here is not
        # premature marking. Doing it AFTER the parse was a permanent leak: an unparseable
        # foreign title fell into the recovery path, which searches only OUR workspace's
        # results/*.md and therefore can never resolve it, so the post `continue`d UNMARKED —
        # re-fetched and re-warned on every harvest for the rest of the run, and blocking the
        # `_all_result_posts` early stop on its page forever. Only OUR OWN unrecoverable posts
        # may be left unmarked, because only those can be recovered later.
        tags = p.get("tags") or []
        team = next((t.split(":", 1)[1] for t in tags if t.startswith("team:")), None)
        if (team or "").strip().lower() == "none":
            team = None     # infrastructure probe: posted teamless on purpose, not foreign
        if team is not None and team not in teams:
            _mark_seen(seen, seen_path, pid)
            continue        # foreign post: the workshop DB is shared across runs
        m = RESULT_RE.search(title)
        if m:
            exp_id, train_fit, outcome = m.group(1), float(m.group(2)), m.group(3).upper()
        elif "FAILED" in title.upper():
            # `[RESULT] {exp_id}: FAILED` (diff never applied) carries no metric — still ledger it,
            # otherwise repeatedly-unappliable proposals are invisible to the stagnation check.
            exp_id, train_fit, outcome = title.split(":")[0].split()[-1], None, "FAILED"
        else:
            # Unparseable title — RECOVER, never skip. A [RESULT] the regex cannot read is
            # still a real experiment that really spent pool time.
            exp_id, outcome = _recover_unparsed_result(p, pid)
            train_fit = None
            if not exp_id:
                # Genuinely unidentifiable, and it IS one of ours (the foreign check above
                # already ran and let it through). Leave the pid UNMARKED so a later harvest
                # can retry it (Step 8b may not have backfilled post_id yet), and say so
                # loudly: a silently dropped result is the one failure this hook must never
                # repeat.
                print(f"cycle_ledger: WARNING unparseable [RESULT] post {pid} title={title!r} "
                      f"— no outcome tag and no results/*.md claims this post_id. NOT ledgered, "
                      f"NOT marked harvested; the next harvest retries it.")
                continue
            print(f"cycle_ledger: recovered {exp_id} ({outcome}) from tags + result file "
                  f"for unparseable post {pid}")
        if already_ledgered(log_path, exp_id):
            # Duplicate [RESULT] post for an experiment already in the ledger. It must not
            # become a second row: two rows for one eval double the ROLE-MONITOR stagnation
            # streak and the analysts' dead-end / failure-range counters. Mark it harvested
            # (the decision is final, there is nothing to retry) and print it — a duplicated
            # experiment is a coordination fault worth seeing, not one to swallow silently.
            _mark_seen(seen, seen_path, pid)
            print(f"cycle_ledger: duplicate [RESULT] for {exp_id} (post {pid}) — not ledgered")
            continue
        it = meta.get(exp_id, {})
        # Per-experiment result file (ROLE-CPU Step 5) is the authoritative source. Its
        # frontmatter keys are exactly: exp_id, agent, team, axis, direction, value, outcome,
        # fitness, is_valid, mean_rel_energy, delta, test_metric_value, test_is_valid,
        # post_id. Read those names
        # and no others; fall back to the queue `completed:` row where it carries the same value.
        rf = parse_fm(requests.get(f"{API}/workspaces/{WS_ID}/files/results/{exp_id}.md",
                                   headers=HEADERS).json())
        # Pass the outcome through UNCHANGED — all FIVE values are legal, including
        # NEAR_MISS (both gates passed, promotion race lost). Never rewrite a
        # NEAR_MISS to KEEP (it was not promoted) or to DISCARD (it did not fail).
        if outcome not in ("KEEP", "NEAR_MISS", "DISCARD", "REJECTED_TEST", "FAILED"):
            outcome = _first((rf, "outcome"), (it, "outcome")) or outcome
        # The title's scraped number, wrapped so `_first` can rank it LAST — see `fitness`.
        title_vals = {"fitness": train_fit}
        # An infrastructure probe belongs to NO team, and the fallbacks must not put it back
        # under one: the result file records the running agent's OWN team, so
        # `_first((rf, "team"), ...)` would re-attach the shared baseline to whichever team
        # happened to hold the baseline lock — the exact mis-attribution the teamless post
        # exists to prevent.
        row_team = (None if exp_id in INFRA_EXP_IDS
                    else team or _first((rf, "team"), (it, "team")))
        row = {
            "ts":            p.get("createdAt") or p.get("created_at")
                             or datetime.now(timezone.utc).isoformat(),
            "cycle":         cycle_count,
            "exp_id":        exp_id,
            "team":          row_team,
            "agent":         _first((rf, "agent"), (it, "completed_by")),
            # PROVENANCE FIELDS — queue FIRST, result file second. Deliberately inverted
            # against every other field here. `results/{exp_id}.md` is agent-written, and
            # an agent whose context has bled from another team writes ANOTHER team's axis
            # into it (observed: an internal-step-cap experiment filed under a
            # curvature-transport axis, which mis-attributes the whole mechanism family).
            # The queue's `completed:` row is analyst-authored and single-writer, so it is
            # the trustworthy attribution key. Do NOT "tidy" this back to (rf, ...) first.
            "axis":          _first((it, "axis"), (rf, "axis")),
            "direction":     _first((it, "direction"), (rf, "direction")),
            "value":         _first((it, "value"), (rf, "value")),
            # RESULT FILE FIRST, post TITLE last. The title's number is scraped out of free
            # text an agent composed; `results/{exp_id}.md` and the queue `completed:` row are
            # rendered from the eval JSON. Preferring the title meant a typo, a rounding, or a
            # title describing a different run outranked the authoritative record — and the
            # two disagreeing was invisible, because the ledger only ever showed the title.
            "fitness":       _first((rf, "fitness"), (it, "val_score"),
                                    (title_vals, "fitness")),
            "is_valid":      _first((rf, "is_valid"), (it, "is_valid")),
            # Why an is_valid:0 row failed. Fitness is honest even for energy-gate failures,
            # so without this the ledger cannot distinguish a fast-but-under-relaxing
            # mechanism from one that lost on both axes.
            "mean_rel_energy": _first((rf, "mean_rel_energy"), (it, "mean_rel_energy")),
            # queue fallback: `test_fitness` is the current key; `test_score` is the
            # pre-rename legacy name, kept only so old rows still resolve.
            "test_fitness":  _first((rf, "test_metric_value"), (it, "test_fitness"),
                                    (it, "test_score")),
            # SINGLE-SOURCE on purpose: ROLE-CPU Step 6 writes only is_valid / delta /
            # mean_rel_energy onto the `completed:` row, so there is no queue fallback to
            # read. Do not add `(it, "test_is_valid")` — a fallback to a key nothing writes
            # is noise that reads as a guarantee.
            "test_is_valid": _first((rf, "test_is_valid")),
            # KEEP | NEAR_MISS | DISCARD | REJECTED_TEST | FAILED — written through as-is
            "outcome":       outcome,
            "delta":         _first((rf, "delta"), (it, "delta")),
            "post_id":       pid,
        }
        # Schema guard: exactly the 16 contracted keys, in the contracted order. Repair
        # rather than raise (rule 3 — the ledger never blocks the cycle); the print is the
        # signal that an edit above drifted from the schema table.
        if tuple(row) != LEDGER_KEYS:
            print(f"cycle_ledger: WARNING schema drift, coercing keys: {tuple(row)}")
            row = {k: row.get(k) for k in LEDGER_KEYS}
        with open(log_path, "a") as f:           # append mode, ONE write per row
            f.write(json.dumps(row, default=str) + "\n")
        _mark_seen(seen, seen_path, pid)         # ONLY now: the row exists, so it is harvested
        n_new += 1
    print(f"cycle_ledger: +{n_new} rows → {log_path}")
    return n_new
```

Rules:
1. **Append-only.** Never rewrite, sort, or truncate `experiments.jsonl`; never buffer rows to write
   at the end. One `open(..., "a")` + one `write` per row, so a crash mid-cycle loses at most the row
   in flight.
2. **Idempotent on TWO keys — post id and experiment id.** A post id already in
   `.harvested_post_ids` is skipped; so is a post whose `exp_id` already has a non-`FAILED` row in
   `experiments.jsonl` (that post IS marked harvested, so it is never reparsed, and the skip is
   printed). Post-id dedup alone does not stop a *duplicated experiment*: two `[RESULT]` posts for
   one eval have two fresh ids but resolve from the same `results/{exp_id}.md`, so they append two
   identical rows and inflate the ROLE-MONITOR stagnation streak and the analysts' dead-end and
   failure-range counters. `FAILED` is exempt because a FAILED item is re-queued under the same id
   and its real second result must still land. Re-running the hook, or resuming after an
   interruption (runbook Case C), must not duplicate rows. **When** a post is marked harvested is
   itself a rule — see rule 10.
3. **Never block the loop on it.** A missing result file or an API hiccup degrades that row's
   fields to `None` — it never raises out of the cycle. Degrading a *field* is acceptable; dropping
   a *row* is not (rule 2b).
4. **The orchestrator writes this file itself, inline.** Do not spawn a subagent to do bookkeeping.
5. **No prose in the ledger.** One machine-readable line per experiment; narrative belongs in the
   `[RESULT]` post.
6. **Read the contracted key names, not guesses.** `results/{exp_id}.md` frontmatter is exactly
   `exp_id, agent, team, axis, direction, value, outcome, fitness, is_valid, mean_rel_energy,
   delta, test_metric_value, test_is_valid, post_id` (ROLE-CPU Step 5) — note the held-out fitness is
   `test_metric_value`, and `direction` there is the *experiment axis* direction from the queue item,
   not the champion's minimize/maximize direction. Where the queue's `completed:` row carries the
   same value (`val_score`, `test_fitness`, `outcome`, `completed_by`, `is_valid`, `delta`,
   `mean_rel_energy` — ROLE-CPU Step 6), fall back
   to it so a missing frontmatter key degrades to a real number instead of `None`. Every
   result-file field the `completed:` row can also carry has such a fallback: a single-source field
   is a field that becomes a silent
   `null` the one time that source is unreadable, and a `null` `is_valid` is indistinguishable
   downstream from a run that genuinely failed the energy gate. **`test_is_valid` is the one
   exception** — ROLE-CPU Step 6 writes only `is_valid`, `delta` and `mean_rel_energy` onto the
   `completed:` row, so there is no second source to fall back to and it is read from the result
   file alone. Do not invent `(it, "test_is_valid")`: a fallback to a key nothing writes reads as a
   guarantee that does not exist. The queue's
   held-out float is `test_fitness`; `test_score` is its pre-rename LEGACY name (it collided with
   the sentinel key `test_fitness`) and is read only as a fallback for rows written before the
   rename. Do not write `test_score` in new queue rows.
   **Exception — the three provenance fields.** `axis`, `direction` and `value` read the queue
   `completed:` row FIRST and the result file second, the reverse of every other field. The result
   file is agent-written and a context bleed puts another team's axis in it, which mis-attributes
   the mechanism family in every downstream counter; the queue row is analyst-authored and
   single-writer. Keep the inversion.
   **`fitness` reads the result file FIRST and the post title LAST.** The title's number is scraped
   out of free text an agent composed; the result file and the queue row are rendered from the eval
   JSON. Ranking the title above them lets a typo or a title describing a different run overwrite
   the authoritative number, and the disagreement is invisible because the ledger then only ever
   shows the title.
7. **Outcomes pass through unchanged.** The ledger records what the cpu-eval agent decided —
   `KEEP`, `NEAR_MISS`, `DISCARD`, `REJECTED_TEST`, `FAILED`. The ledger is not a second gate: it
   never re-derives an outcome from the numbers, and in particular it never collapses `NEAR_MISS`
   into `KEEP` (nothing was promoted) or into `DISCARD` (nothing failed). A `NEAR_MISS` row carries
   real `test_fitness` / `test_is_valid` values, because the test eval ran and passed.
8. **Harvest the whole history, not the most recent page.** `GET /posts` is paginated: a bare
   `limit=100` never shows a `[RESULT]` older than the 100 most recent, and `.harvested_post_ids`
   cannot recover it because it was never fetched — the row is lost from `experiments.jsonl`
   permanently. Page with `offset` until `pagination.hasMore` is false, and stop early as soon as a
   whole page is already in `.harvested_post_ids` (pages are newest-first, so everything past it is
   older and already harvested). In steady state that is still one request; it only costs more on a
   long run or a resume that must catch up several cycles.
9. **Exactly 16 flat keys, in the contracted order** (`ts, cycle, exp_id, team, agent, axis,
   direction, value, fitness, is_valid, mean_rel_energy, test_fitness, test_is_valid, outcome,
   delta, post_id`),
   with `outcome` drawn only from the five-value set. `LEDGER_KEYS` pins both; the guard before the
   write coerces and prints rather than raising, per rule 3. Changing the key set means changing
   FOUR things in one edit — the schema table above, `LEDGER_KEYS`, the row-build dict (same order,
   or the guard trips) and this rule.
10. **Mark harvested LAST, never first.** `.harvested_post_ids` records a post only AFTER its row
   has been written, or after the post has been positively decided against (foreign run, duplicate
   experiment). Marking on pickup — before the post's fate is known — turns any parse failure into
   permanent, silent data loss: no row is written, and the post is never looked at again.
   A `[RESULT]` whose title `RESULT_RE` cannot read is therefore **recovered, not skipped**: the
   `outcome:<x>` tag every `[RESULT]` carries gives the outcome, and the `results/*.md` whose
   frontmatter `post_id` is this post gives the `exp_id` (ROLE-CPU Step 8b backfills it). Only when
   BOTH fail does the hook give up — and then it prints a loud warning and leaves the pid
   **unmarked**, so a later harvest retries it once the backfill lands. The cost of a retry is one
   extra page fetch; the cost of a premature mark is an experiment that, downstream, never happened.
   **The foreign-run check is the one decision made BEFORE the parse**, and it must stay there. It
   reads the post's `team:` tag rather than parsing its title, so it is a decision *about* the post,
   not a premature mark — and a foreign post can never be recovered by us (`_recover_unparsed_result`
   only searches OUR workspace), so deciding it after the parse would leave every unparseable
   foreign `[RESULT]` unmarked forever: re-fetched and re-warned on every harvest, and pinning the
   `_all_result_posts` early stop open on its page for the rest of the run. Leave unmarked only what
   a later harvest could actually resolve.
11. **Chronological order — write oldest-first.** `_all_result_posts` returns pages newest-first, so
   the harvest iterates `reversed(...)`. Every consumer of this file takes a `rows[-N:]` window; a
   backwards ledger makes each of them read the OLDEST N rows and report them as the most recent N,
   which inverts the stagnation verdict rather than merely blurring it. Consumers must ALSO sort by
   `ts` defensively before slicing (a resume, a manual append or a hand-repaired line can still land
   out of order), with missing / null / **empty-string** `ts` rows sorting LAST — an unknown
   timestamp is far more likely to be the newest write than the oldest, and sorting it first would
   silently redate the window. Key on truthiness (`(0, ts) if ts else (1, "")`), not on `is None`:
   `""` is not `None` and would otherwise sort ahead of every real stamp. This is the same
   expression as runbook Step 5g's `_ts_key` and ROLE-MONITOR's `ts_key` — one rule, one spelling.
12. **Infrastructure probes are ledgered but never counted.** The shared baseline evaluates the
   champion UNCHANGED to establish the anchor; it is posted `type:infrastructure` and teamless, and
   its row is written with `team: null` (the fallbacks are suppressed for it — the result file names
   the running agent's own team). Its `outcome` is passed through like any other, but nothing that
   counts promotions may count it: exclude `exp_id in INFRA_EXP_IDS` (or an `infrastructure_probe:
   true` marker on rows written elsewhere — tested as `str(...).lower() == "true"`, never
   `bool(...)`, which reads the string `"false"` as True and would split this verdict from the
   monitor's) from every KEEP count and every stagnation window —
   the Step 5g window, `stagnation_response`, ROLE-MONITOR's streak, and `meta_diagnostics`'
   `keep_rate`. Counting it credits a team with a win nobody made and parks a KEEP in the stagnation
   window, suppressing the regroup for ten experiments at exactly the moment the run needs one.

---

## Hook: champion_promotion

**Champion = best `algo.py` for the optimized metric `fitness` (= `mean_rel_steps`, lower is
better) that ALSO carries to the held-out test split.** The champion artifact is `algo.py`
everywhere — there is no `train.py` in this task.

**The orchestrator does NOT promote.** Champion promotion — the `champion.md` PUT and the
`champion/algo.py` + `champion/SOURCE` propagation — is owned end-to-end by the cpu-eval agent that
produced the candidate (ROLE-CPU Steps 7a/7b/7b1), full stop. The agent holds the frozen candidate,
the stamped `algo_{exp_id}.py`, and both eval score dicts; it is the only actor that can promote
race-safely. The orchestrator's job here is to **know the rule** (to read `champion.md`, judge
[RESULT] posts, write the ledger, and auto-bracket), not to execute it. There is no orchestrator-side
`shutil.copy` into `champion/`; if you feel the urge to "fix" a stale champion by copying files, that
is a bug report for the agent path, not a task for you.

`{FOCUS_ROOT}/champion/algo.py` remains the SINGLE SOURCE OF TRUTH for champion code — every cpu-eval
agent reads it in Step 2 — it is just written by the promoting agent, not by you.

**Promotion rule — THREE conditions, all required (deterministic, strict, with a seeding
exception).** Because the baseline `algo.py` may itself be **invalid** under the energy gate, the
first VALID candidate seeds the champion regardless of its fitness; thereafter:

```python
# `score` and `test_score` are the JSON dicts from eval_candidate.py for the TRAIN run and
# the `--split test` run of the SAME, ALREADY-FROZEN candidate algo.py:
#   is_valid (0/1), fitness (float; == mean_rel_steps whenever a trajectory was produced,
#   INCLUDING energy-gate failures — 1000.0 only for infra error / non-convergence /
#   over-budget), mean_rel_steps, ...  `is_valid`, never the fitness value, is the gate.
# `champion_fitness` / `champion_test_fitness` are the incumbent anchors, read from
# champion.md frontmatter (`metric_value` / `test_metric_value`); None if no VALID champion yet.
# `diff_applied` is False when the proposal's diff never landed on algo.py.

KEEP_MARGIN = 1e-4    # train, in mean_rel_steps units — same bar as test; matches upstream
TEST_MARGIN = 1e-4    # held-out test; matches the upstream contract's `significant_change`

# FAILED first: an unapplied diff or a harness/Redis error TESTED NOTHING. eval_candidate.py
# turns any harness exception into {"fitness": 1000.0, "is_valid": 0, "error": ...}. A 1000.0
# narrows to infra error / non-convergence / over-budget — the last two are real science, so
# `error` / `num_results` still decide. Filing an infra error as DISCARD would burn a mechanism
# family that was never tested.
def _infra_failed(s):
    return (s is None or bool(s.get("error"))
            or int(s.get("num_results", 0) or 0) == 0)

if (not diff_applied) or _infra_failed(score):
    outcome = "FAILED"           # re-queue the item; NOT science, no test eval spent
else:
    train_valid   = int(score.get("is_valid", 0)) == 1
    cand_fitness  = float(score["fitness"])
    have_champion = champion_fitness is not None

    # (a) TRAIN improvement — valid, and better by at least KEEP_MARGIN.
    train_pass = train_valid and (not have_champion or
                                  (champion_fitness - cand_fitness) >= KEEP_MARGIN)

    if not train_pass:
        outcome = "DISCARD"      # invalid or non-improving on train — NO test eval is spent
    else:
        # Provisional keep ⇒ the agent now runs the SECOND eval: same remote candidate path,
        # same eval_candidate.py, only `--split test` added. The candidate is NEVER re-edited
        # between the two evals.
        if _infra_failed(test_score):
            outcome = "FAILED"   # held-out eval never produced a number — re-queue
        else:
            test_valid        = int(test_score.get("is_valid", 0)) == 1    # (c) TEST still valid
            cand_test_fitness = float(test_score["fitness"])
            test_pass = test_valid and (champion_test_fitness is None or   # no anchor yet
                                        (champion_test_fitness - cand_test_fitness) > TEST_MARGIN)
            if not test_pass:
                outcome = "REJECTED_TEST"
            else:
                # Both gates passed. The LAST thing that can deny promotion is the
                # promotion RACE, resolved by the cpu-eval agent at ROLE-CPU Step 7b
                # (PRE-PUT gate: an equal-or-better champion landed while we evaluated)
                # and Step 7b1 (RACE GUARD: champion.md no longer records our exp_id).
                # `race_lost` is True when either fired.
                outcome = "NEAR_MISS" if race_lost else "KEEP"
```

- **(a) TRAIN improvement** — `is_valid == 1` on train AND `champion_fitness - cand_fitness >= KEEP_MARGIN`.
- **(b) TEST improvement** — `champion_test_fitness - cand_test_fitness > TEST_MARGIN` (strict).
- **(c) TEST still valid** — the test run has `is_valid == 1` (`mean_rel_energy >= 1.0` on held-out
  molecules). An invalid test run is fatal no matter how good its `fitness` looks.
- **NEAR_MISS is not a gate outcome either** — it is the *race* outcome. All three conditions
  passed; the candidate simply lost the promotion race. `champion.md` is NOT written and
  `champion/algo.py` is NOT copied, the change is re-queued as `{exp_id}_stack` to be re-tested
  against the NEW champion, and it is recorded in **neither** `dead_ends.md` **nor**
  `non_generalizable.md`. It is positive evidence: analysts count it as *supporting* the
  hypothesis, and it never counts toward a dead end, a falsification, or the stagnation streak
  (by construction another candidate WAS promoted in that rotation).
- **FAILED is not a gate outcome at all** — an unapplied diff, a harness/Redis error on either
  split, an empty result set, or a scope abort means the experiment **tested nothing**. It is never
  a DISCARD, never a REJECTED_TEST, never a NEAR_MISS, never a dead end, never evidence, and it
  never counts toward the stagnation streak; the queue item goes back to `pending:`, not
  `completed:`-and-closed.

A tie is always a reject. On test, an improvement of *exactly* `TEST_MARGIN` is also a reject (`>`,
not `>=`). Both anchors advance **together**, only on an accepted promotion; seeding (no valid
champion yet) sets both at once.

**Never spend a test eval on a train DISCARD or FAILED.** The worker pool is shared with other
clients and every eval is real compute. The test eval exists to
*veto* provisional keeps, not to characterise losers.

### champion.md frontmatter (both anchors)

`champion.md` carries the two anchors side by side. `launch.py` seeds every key, with both anchors
`null` and `status: awaiting_baseline` — a `null` (or absent) anchor reads as `None`, which is the
"no anchor yet" state for that gate. The first accepted promotion — seeding or otherwise — replaces
the seed frontmatter, drops `status: awaiting_baseline`, and MUST set **both** anchors in the same
write:

```yaml
metric_name: fitness
metric_value: <train fitness> # TRAIN anchor  → gate (a); null until the first promotion
test_metric_value: <test fitness>  # TEST anchor → gate (b); null until the first promotion
direction: minimize
experiment_id: exp_...
run_id: <run-id>
agent: <run>_cpuN
updated_at: "<ISO-8601 UTC>"
settings: {}
```

`test_metric_value: null` (or absent) means **no test anchor yet**: the seeding branch applies and
**test validity alone gates** — `test_pass` still requires `is_valid == 1` on the held-out split.
This is the same semantics ROLE-CPU uses (`champ_test is None` ⇒ validity-only gate); do not treat a
missing test anchor as a blocker.

A champion.md carrying a `metric_value` but a `null`/absent `test_metric_value` is an **anomaly**,
not a hard block: it means something promoted on train alone, contrary to the contract (seeding
requires validity on BOTH splits and sets BOTH anchors together). Do not patch it yourself — post a
`[DISCUSSION]` flagging it, and let the next cpu-eval agent re-establish both anchors together in one
write. Under the rule above that agent's promotion is gated on train margin plus held-out validity,
so the inconsistency self-heals on the next accepted promotion.

### REJECTED_TEST — a distinct outcome, not a DISCARD

A candidate that passes (a) but fails (b) or (c) is `REJECTED_TEST`: it was a real train win that did
**not generalize**. Recording it as a plain DISCARD loses exactly the signal this run exists to
capture.

- It is recorded in the team workspace file **`non_generalizable.md`** (created if absent) — **NOT**
  in `dead_ends.md`.
- Its `[RESULT]` post must state explicitly which gate failed: **test invalid**, **insufficient test
  improvement**, or **both** — with the train and test numbers.
- It **counts 1 toward its axis's closure count**, exactly like a DISCARD — it does not abandon the
  mechanism family on its own. An axis closes only when that count reaches the threshold in
  ROLE-TEAM § Axis Closure, and a KEEP or NEAR_MISS on the axis resets it to 0. (An older version of
  this line said the family was "abandoned, same as a dead end" — that single-result closure rule is
  gone: one result that failed to generalize is not evidence that a whole axis is empty.) Analysts
  still read `non_generalizable.md` before proposing; re-proposing the *same* mechanism listed there
  without addressing why it failed to generalize is a wasted cycle.

> Eval is deterministic — there is NO multi-seed gate and NO noise-floor band, on either split. A
> train win that vanishes on test is not noise; it is overfitting to the train molecules, and the
> `REJECTED_TEST` record is the evidence.

### Auto-bracket big wins

After a KEEP, the orchestrator may post bracketing `[PROPOSAL]`s to find the true optimum of a tuned
parameter. With deterministic eval there is no seed-noise justification for special-casing, but
bracketing remains a legitimate search move. Trigger on a meaningful relative improvement in
`mean_rel_steps` (e.g. `champion_fitness - cand_fitness > 0.01`, i.e. ≥1% fewer relative steps),
not on a `val_bpb`-scaled absolute threshold:

```python
if outcome == "KEEP" and (prev_champion_fitness is not None) and \
   (prev_champion_fitness - cand_fitness) > 0.01:
    # Identify the changed parameter, generate 2 bracketing experiments
    # (midpoint and overshoot), post them as [PROPOSAL]s, add to the team queue at high priority.
    for bracket_exp in generate_brackets(old_value, new_value):
        requests.post(f"{API}/posts", headers=HEADERS, json={
            "workshop": WORKSHOP,
            "title":   f"[PROPOSAL] {bracket_exp['id']}: {bracket_exp['description']}",
            "content": f"Auto-bracketing from win {exp_id} (Δfitness={prev_champion_fitness - cand_fitness:.4f}).",
            "tags":    ["type:proposal", "auto:bracket", f"team:{team_name}"],
        })
```

The orchestrator generates these — no analyst action needed. Trigger only on an accepted `KEEP`
(one that cleared the test gate); **never bracket a `REJECTED_TEST`** — bracketing a mechanism that
failed to generalize just spends evals refining an overfit. **Do not bracket a `NEAR_MISS` either**,
even though it cleared both gates: the champion it was measured against is already stale, and its
`{exp_id}_stack` retest against the new champion is queued. Bracket the `_stack` result if it KEEPs.
Keep each bracket post to 2–3 sentences.

---

## Hook: stagnation_response

**OPEN-ENDED — do NOT stop the loop.** When 0 KEEPs occur in the last 10 experiments that are
neither **FAILED** nor **NEAR_MISS** nor an **infrastructure probe** (FAILED tested nothing;
NEAR_MISS passed both gates and implies a promotion happened that rotation; the shared baseline is
not a mechanism anyone proposed — all three are filtered out of the window, runbook Step 5g), the search has
plateaued, but this is open-ended optimization with no deadline: the correct response is to trigger a
**discussion / regroup** round (re-form teams, mine new structural axes, post fresh proposals), NOT
to exit. Never `raise SystemExit`; never halt.

**Post at most once per stagnation episode.** Stagnation persists across cycles by nature; posting a
fresh `[DISCUSSION]` every cycle buries the workshop in identical notices that agents learn to skip.
Post only when the stagnation state **changes** (not stagnant → stagnant) or every
`STAGNATION_REPOST_EVERY` cycles while it persists — and make the message carry numbers agents can
act on: how long since the last accepted promotion, both current anchors, and how much of the
stagnation is `REJECTED_TEST` (a generalization problem) versus `DISCARD` (a train problem).

```python
# Self-contained on purpose. `json` and `INFRA_EXP_IDS` are also bound by the `cycle_ledger`
# fence above, but the orchestrator may evaluate these blocks in separate interpreters, and a
# NameError here would take the whole stagnation check out of the cycle — rule 3, this
# bookkeeping never blocks the loop. Re-binding through `globals()` reuses the cycle_ledger
# value whenever it IS present, so the two fences cannot drift apart.
import json

INFRA_EXP_IDS = globals().get("INFRA_EXP_IDS", ("baseline_shared",))

STAGNATION_REPOST_EVERY = 10          # cycles; only while stagnation persists
_stagnation = {"active": False, "last_post_cycle": None}

def _ledger_rows():
    rows, p = [], FOCUS_ROOT / "logs" / "experiments.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows

def _by_ts(rows):
    """Chronological, defensively — ALWAYS sort before taking a `[-N:]` window.

    `cycle_ledger` writes oldest-first, but a resume, a hand-repaired line or a row appended
    by anything else can still land out of order, and a window is a slice: one misplaced row
    and the check reads a different ten experiments than the ones it names. Rows with a
    missing, null OR EMPTY `ts` sort LAST — an unknown timestamp is far likelier to be the
    newest write than the oldest, and sorting it first would quietly redate the window.
    Testing `is None` was not enough: `ts: ""` is not None, so it landed in the first group
    and `""` sorts before every real ISO stamp — the exact inversion the rule forbids, and a
    disagreement with runbook Step 5g's `_ts_key` and ROLE-MONITOR's `ts_key`, which both key
    on truthiness. Same rule, same spelling, three files. All stamps are UTC ISO-8601, so
    string order is chronological order; `sorted` is stable, so equal keys keep ledger order.
    """
    def _key(r):
        ts = str(r.get("ts") or "")
        return (0, ts) if ts else (1, "")
    return sorted(rows, key=_key)

def _is_infra_row(r):
    """True for an infrastructure probe — a row that must never count as a promotion.

    The shared baseline runs the champion UNCHANGED to establish the anchor, so it is
    ledgered with a real outcome but it is not science: counting it credits a team with a win
    nobody made, and parks a KEEP inside the stagnation window, which suppresses the regroup
    for ten experiments at exactly the point the run needs one. `INFRA_EXP_IDS` is the
    discriminator in the ledger; the `infrastructure_probe` marker is honoured too, for rows
    written by anything that carries it.

    The marker test is `str(...).lower() == "true"` — byte-identical to runbook Step 5g and
    ROLE-MONITOR's `is_baseline`, and NOT `bool(...)`. The marker arrives as free-form text as
    often as as a real boolean, and `bool("false")` is True: with `bool` the same ledger row
    could be dropped from this window and counted by the monitor's streak, which is precisely
    the split-brain this shared predicate exists to prevent. `str()` also covers a real
    `True`/`False`, so one spelling serves both.
    """
    return (r.get("exp_id") in INFRA_EXP_IDS
            or str(r.get("infrastructure_probe", "")).lower() == "true")

def stagnation_clear(cycle_count):
    """Called when the last-10 window contains a KEEP — ends the stagnation episode."""
    if _stagnation["active"]:
        print(f"Stagnation cleared at cycle {cycle_count}.")
    _stagnation.update({"active": False, "last_post_cycle": None})

def stagnation_response(cycle_count):
    # Sort before slicing (see _by_ts), and drop infrastructure probes BEFORE anything counts
    # a KEEP: the shared baseline is an anchor, not a promotion. Leaving it in both inflates a
    # team's win count and pins this check at "recently promoted" — for ten experiments, the
    # regroup never fires.
    rows  = [r for r in _by_ts(_ledger_rows()) if not _is_infra_row(r)]
    keeps = [r.get("cycle") or 0 for r in rows if (r.get("outcome") or "") == "KEEP"]
    since = (cycle_count - max(keeps)) if keeps else cycle_count
    # Same window as runbook Step 5g: FAILED rows tested nothing, and NEAR_MISS rows
    # passed both gates (a promotion demonstrably happened that rotation). Both excluded.
    last10 = [r for r in rows
              if (r.get("outcome") or "") not in ("FAILED", "NEAR_MISS")][-10:]
    n_rej  = sum(1 for r in last10 if (r.get("outcome") or "") == "REJECTED_TEST")

    champ = parse_fm(requests.get(f"{API}/workspaces/{WS_ID}/files/champion.md",
                                  headers=HEADERS).json())
    train_anchor = champ.get("metric_value")
    test_anchor  = champ.get("test_metric_value")

    was_active = _stagnation["active"]
    _stagnation["active"] = True
    last = _stagnation["last_post_cycle"]
    should_post = (not was_active) or (last is None) or \
                  (cycle_count - last >= STAGNATION_REPOST_EVERY)
    print(f"STAGNATION: 0 KEEPs in last 10 experiments (cycle {cycle_count}, "
          f"{since} cycles since last promotion, {n_rej}/10 REJECTED_TEST)")
    if not should_post:
        print(f"  already posted at cycle {last} — not re-posting (next at "
              f"{last + STAGNATION_REPOST_EVERY}).")
        return
    _stagnation["last_post_cycle"] = cycle_count

    # Open-ended task — trigger a regroup, never stop. Keep the post SHORT (<= 150 words):
    # it is a work order, not a report.
    focus = ("Most recent failures are REJECTED_TEST: mechanisms win on train and do NOT generalize. "
             "Read non_generalizable.md first and propose chemistry-general mechanisms, not tuning."
             if n_rej >= 3 else
             "Most recent failures are train DISCARDs: the current axes are exhausted. "
             "Read dead_ends.md first and open a NEW structural axis.")
    requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP,
        "title": f"[DISCUSSION] Stagnation regroup (cycle {cycle_count})",
        "content": (f"0 KEEPs in the last 10 experiments; {since} cycles since the last accepted "
                    f"promotion. Anchors: train={train_anchor}, test={test_anchor}. "
                    f"Last 10: {n_rej} REJECTED_TEST.\n\n{focus}\n\n"
                    "Propose NEW structural axes for algo.py (step control / trust region, Hessian "
                    "init & update, line search / step acceptance, internal-coordinate construction, "
                    "restart/re-blend). Do NOT touch or game the convergence test (fixed/external). "
                    "Avoid retuning a single constant. Seed fresh [PROPOSAL]s into the team queues."),
        "tags": ["type:discussion", "regroup"],
    })
    # Do NOT raise / exit — the loop continues.
```

Eval is deterministic, so this stagnation signal is noise-free: 0 KEEPs in the last 10 genuinely
means no real improvement was found, not an unlucky run of seeds. Note that under the test gate a
"0 KEEPs" window can be full of *train* wins that were `REJECTED_TEST` — that is a different disease
(overfitting) than a window of plain DISCARDs (exhausted axis), which is why the post says which.

---

## Hook: periodic_hooks

**Meta-improvement: ENABLED.** Every 3 cycles, follow `system/reference/META-IMPROVEMENT.md`: read
the evidence, make ONE targeted file edit, append a line to `logs/meta_results.tsv`. Do NOT
`import meta_diagnostics` (not shipped). Optionally run `python3 task/meta_diagnostics.py` for hard
signals (crash-safe; the pass works without it).

```python
def periodic_hooks(cycle_count):
    if cycle_count % 3 != 0:
        return None
    # Judgment-based meta-improvement — see system/reference/META-IMPROVEMENT.md.
    # Read evidence -> diagnose the single biggest issue -> edit exactly ONE file
    # -> append to logs/meta_results.tsv. NEVER import meta_diagnostics.
    return None
```

**Scope + length.** ONE file, ONE targeted edit, ONE line appended to `logs/meta_results.tsv` (the
`reason` field ≤ 200 chars). Do the reading and the edit yourself — do not spawn a subagent for the
meta pass. If the diagnosis needs more than one file changed, it is too big for this hook: post it as
a `[DISCUSSION]` instead and change nothing.

---

## Hook: exit_condition

```python
def exit_condition():
    return False   # never exit voluntarily; this is an open-ended run (only user interrupt stops it)
```

**Returning `False` unconditionally is intended, not an oversight.** This is open-ended optimization:
there is no target fitness, no cycle budget, and no self-declared "done". **The operator stops the
run** (Ctrl+C / killing the session) after reading the champion anchors and the ledger. Stagnation is
handled by `stagnation_response` (regroup, keep looping) and never by exiting. Do not add a cycle cap,
a fitness threshold, a "no progress for N cycles" bail-out, or any other termination condition here.

---

## Hook: final_report

```python
def final_report():
    print()
    print("=" * 60)
    print("  OPTIMIZATION RUN COMPLETE")
    print("=" * 60)
    print(f"  Task:        {task_name}")
    print(f"  Cycles:      {cycle_count}")
    champ = parse_fm(requests.get(f"{API}/workspaces/{WS_ID}/files/champion.md",
                                  headers=HEADERS).json())
    # champion.md frontmatter has NO validity keys — it carries only the two anchors
    # (metric_value / test_metric_value) plus metric_name, direction, experiment_id, run_id,
    # agent, updated_at, settings. Validity is implied: an anchor can only be written by an
    # accepted promotion, which required is_valid == 1 on BOTH splits. Never invent
    # `is_valid` / `test_is_valid` here — they read as None and print a misleading "valid=None".
    print(f"  Train:       {champ.get('metric_value')}")
    print(f"  Test:        {champ.get('test_metric_value')}")
    print(f"  Set by:      {champ.get('experiment_id')} / {champ.get('agent')} "
          f"@ {champ.get('updated_at')}")
    # Per-split validity, if you want it explicitly, comes from the ledger row of the promoting
    # experiment — not from champion.md. The ledger is chronological (cycle_ledger rule 11), so
    # "last" is literally the last matching line; skip infrastructure probes (rule 12), which
    # carry a KEEP without being a promotion:
    #   row = last row in logs/experiments.jsonl with outcome == "KEEP"
    #         and exp_id not in INFRA_EXP_IDS
    #        -> row["is_valid"], row["test_is_valid"]
    champ_path = FOCUS_ROOT / "champion" / "algo.py"
    src_path   = FOCUS_ROOT / "champion" / "SOURCE"
    if src_path.exists():
        print(f"  Champion:    {src_path.read_text().strip().splitlines()[-1]}")
    print(f"  Code:        {champ_path}")
    print("=" * 60)
```

Both anchors are reported because a champion is only meaningful as a (train, test) pair — a train
number alone is the failure mode this contract exists to prevent.

---

## Hook: never_do_extras

- **Never promote a champion yourself.** No writing `champion.md`, no copying into `champion/algo.py`,
  no appending to `champion/SOURCE`. That is ROLE-CPU Steps 7a/7b/7b1 (see `champion_promotion`).
- **Never promote on train alone**, and never advance one anchor without the other.
- **Never spend a test eval on a train DISCARD/FAILED**, and never re-edit a candidate between its
  train and test evals.
- **Never touch the shared eval plane.** The Redis at `cpu-33:6385` and the 192 workers are shared
  with other clients: no `FLUSHDB`, no restarting or killing workers, no writes anywhere under
  `/home/tsypin/opt_problem_optbench`. The orchestrator has no business ssh-ing to the eval head at
  all — agents own the eval protocol.
- **Never spawn ad-hoc subagents** for orchestrator work (reading files, parsing results, writing the
  ledger, bookkeeping). Spawn exactly the rostered agents for the cycle.
- **Never rewrite or truncate `logs/experiments.jsonl`** — append-only (see `cycle_ledger`).
