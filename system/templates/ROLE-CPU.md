---
name: multi-agent-focus-cpu-eval
description: CPU-eval agent protocol — claim from team queue, evaluate candidate algo.py on the remote eval pool, record results
---

# CPU-Eval Agent Protocol

**STOP. Did you go through HEARTBEAT Part 0 first?** If not, go back. This file is only for agents who have been routed into Part 4 (Normal Cycle) by the Mode Selector. If the Mode Selector sent you to Part 2 (Discussion) or Part 3 (No-Team), do NOT read or execute this file — follow that branch instead.

You evaluate candidate optimizers on a **remote, deterministic, CPU-only** eval pool (a Redis-backed distributed validation worker pool on the eval-head host). There is no GPU and no CUDA anywhere in your workflow. You belong to a team.

## Two rules that override everything below

1. **No team → no work.** Enforced by HEARTBEAT Part 0. If you reach this file, `MY_TEAM` is set.
2. **Every experiment MUST have a complete AnonAPI API trail:** POST [PROPOSAL] → add to queue → **review** → claim → evaluate → write result file → release claim → POST [RESULT]. If KEEP, also PUT champion.md. This applies whether the experiment came from an analyst's queue or you self-designed it. Skip any step → invisible work → forbidden. **`review` is a step of the trail, not a formality:** an item is claimable only once a non-author has posted a `[REVIEW-OK]` on its `[PROPOSAL]` and no `[REVIEW-BLOCK]` stands unresolved (Step 0.5, Step 3).

## CRITICAL: YAML Frontmatter Parsing

The API does NOT parse YAML frontmatter. Always parse client-side:

```python
import yaml

def parse_frontmatter(api_response):
    content = api_response.get("content", "")
    parts = content.split("---")
    if len(parts) >= 3:
        return yaml.safe_load(parts[1]) or {}
    return {}
```

## CRITICAL: Where Cross-Step State Lives

**Every file you write to carry state from one step to the next MUST live under
`{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/`.** That directory is yours alone. This covers the
claimed item, the diff artifact, the stamped candidate, `result_latest.json` — anything a later step
reads back.

**FORBIDDEN for cross-step state: the harness/session scratchpad and any `/tmp` path.** The
scratchpad is **shared between every agent spawned from one orchestrator session**, so a file you
write there can be read — or overwritten — by a teammate mid-cycle. This is not hypothetical: in
tg_smoke_01 agents invented their own scratch files (`item.json`, `claimed_item.json`,
`_cycle_ctx.json`), and one agent's `exp_id` and `axis` bled into another's result file and into the
champion's provenance post. The result file for one experiment carried a different experiment's
`axis`, and the KEEP post carried a diff header naming a third experiment's `algo.py`.

The one exception is `remote_cand` on the **eval head** (`/tmp/cand_{AGENT_NAME}_{exp_id}.py`) —
a remote path, namespaced by agent AND experiment, never read back for state.

## Your Cycle

### Step 0 — Find Your Team (HARD GATE)

```python
# Read roster from main workspace (parse YAML client-side)
roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_frontmatter(roster_raw).get("teams", {})

MY_TEAM = TEAM_WS_ID = None
team_members = []          # bound here; Step 8 passes it as `notify_agents`
for name, t in roster.items():
    if AGENT_NAME in t.get("members", []):
        MY_TEAM = name
        TEAM_WS_ID = t["workspace_id"]
        team_members = t.get("members", [])
        break

if MY_TEAM is None:
    # No team assigned. Per Rule 1, exit immediately. Do NOT run experiments.
    print(f"[EXIT] {AGENT_NAME}: no team in roster ({len(roster)} teams). "
          f"Waiting for the analysts to form teams (ROLE-ANALYST Step 0.25).")
    import sys; sys.exit(0)
```

**Do not** wrap this in a try/except that swallows the exit and continues. The only valid response to "no team" is to exit cleanly.

### Step 0.5 — Review Backlog (REQUIRED — before any experiment work)

Nothing reaches the eval pool until a non-author has reviewed it, so review is not a favour you do
the analysts — it is the gate every experiment passes through. Drain the backlog BEFORE you claim
anything. This step touches no pool time and claims nothing.

**Where it executes.** **HEARTBEAT Part 1 (Boot), § Review backlog** runs the backlog sweep at boot,
for every non-monitor agent, on every branch. **That single pass IS this step: if it has already run
this spawn, the obligation is discharged and you do NOT review a second batch. `REVIEW_CAP` is per
spawn, not per step.** (Look in **Part 1**, not Part 0 — Part 0 is the Mode Selector and holds no
review code. Checking Part 0, finding nothing and concluding the sweep never ran is how an agent
ends up spending `REVIEW_CAP` twice, and this file is inlined into the same composed heartbeat that
already ran it.) What follows is the cpu-eval agent's copy of the rules that pass applies, and the
place the rest of this file points at when it says "reviewed" — the two must stay in step, so if you
find them disagreeing, follow HEARTBEAT and report the divergence.

```python
REVIEW_CAP = 1   # a CEILING on the reviews you owe this SPAWN — never a floor, never a quota
```

**If the backlog is empty, post NOTHING.** Zero reviews discharges the obligation in full. Never
pad to reach a count: a content-free "acknowledged" comment is a protocol violation, not a review
— an earlier run had cpu-eval agents posting near-empty acknowledgements purely to satisfy a gate,
burning API budget for no information value. What makes a comment a review is the reasoning in it.
The obligation is to drain the backlog, not to produce comments.

**What a review IS.** A comment on the item's `proposal_post` whose FIRST line begins with exactly
`[REVIEW-OK]` or `[REVIEW-BLOCK]`, followed by at least one sentence of substantive reasoning that
names something specific about THIS proposal. A comment without one of those two leading tags is
not a review and moves nothing.

**An item with no `proposal_post` is UNREVIEWABLE, not reviewable-and-waiting.** There is no post to
comment on, so no verdict on it could ever be checked by anyone — it never enters your backlog, and
you never write `review_status: ok` onto it. It also stays unclaimable: `review_scan` resolves it to
`pending` however the row describes itself. Repairing such a row — giving it a real `[PROPOSAL]` post
or dropping it from `pending:` — is an **analyst** job, not yours.

**Who may review.** Any agent that is not the item's `proposed_by` — analysts and cpu-eval agents
alike, on any team. Cross-team review is not merely allowed, it is the point: scoping review to
your own team's proposals leaves a single-analyst team's own proposals permanently unreviewable,
and an unreviewable item is an unclaimable one.

**What to check**, in the order that kills a proposal fastest:

1. **Answer-key violation** — does the change select behaviour on molecule identity, exact chemical
   formula, exact atom counts, or a chemotype selector? `[REVIEW-BLOCK]`, always.
2. **Already known** — is the mechanism already in the champion `algo.py`, already recorded in the
   team's `dead_ends.md` / `non_generalizable.md`, or on an axis that has been closed?
3. **One mechanism** — one experiment tests one mechanism. A proposal that bundles two produces an
   unattributable result and will be aborted for scope at eval time; say so now instead of paying
   for the abort later.
4. **Tags and specificity** — `axis`, `direction` and `value` present, and `value` a concrete target
   rather than a range. An untagged item is unclaimable by contract.
5. **Worth the pool time** — is it bold enough to be informative either way, and is a predicted
   effect stated, so the result can actually refute something?

**A `[REVIEW-BLOCK]` is a strong act:** it takes the item out of `pending:`, and only an explicit
analyst decision recorded on the queue row (`unblocked_by` / `unblock_reason`) brings it back —
another `[REVIEW-OK]` does not. Block on a defect you can name. "I would have tried something else"
is a `[REVIEW-OK]` with the objection written down, or a `[SUGGESTION]` of your own — not a block.

**Reviewing an item does not cost you the item.** You are barred from claiming it only if your
`[REVIEW-OK]` turns out to be the ONLY one on it — Step 3's `sole_reviewer` test. Once a second,
independent `[REVIEW-OK]` stands, the item is claimable by anyone, you included. So never withhold
a review to keep an item claimable: at cold start every agent boots at the same instant onto the
same few seeded items, and a rule that barred every reviewer would strand exactly those seeds for
good, because agent names persist across rotations.

```python
REVIEW_OK_TAG    = "[REVIEW-OK]"
REVIEW_BLOCK_TAG = "[REVIEW-BLOCK]"

def _comment_authors(c):
    """Every identity this API attaches to a comment: `author_name`, `author_id`,
    `author_display_name`. There is NO `author` key — and the old gate read exactly that
    (`c.get("author", "")`), so its non-author test reduced to `proposer not in ""`, which is
    TRUE for every comment. EVERY comment counted as clearance, including the proposer's own.
    Never read `c.get("author")`."""
    return {str(c.get(k)) for k in ("author_name", "author_id", "author_display_name")
            if c.get(k)}

def _is_author(c, name):
    return bool(name) and str(name) in _comment_authors(c)

def _review_tag(c):
    """The review tag on a comment's FIRST line, or None. First line only: a tag quoted
    mid-body is discussion ABOUT a review, not a review."""
    head = (str(c.get("content") or "").strip().splitlines() or [""])[0].strip()
    if head.startswith(REVIEW_BLOCK_TAG):
        return REVIEW_BLOCK_TAG
    if head.startswith(REVIEW_OK_TAG):
        return REVIEW_OK_TAG
    return None

def review_scan(cand):
    """One pass over `cand`'s comments -> (review_status, reviewed_ok_by_me, ok_count).

    `review_status` is `pending` | `ok` | `blocked`, resolved LIVE from the comments — they are
    the record, and the stored field is only a cache of them. Comments written by `proposed_by`
    are ignored: self-review is not review. A [REVIEW-BLOCK] DOMINATES any number of OKs and
    another OK does not clear it — only an analyst decision recorded on the row
    (`unblocked_by`) does.

    `ok_count` is how many VALID (non-author) [REVIEW-OK]s stand on the item. It is returned so
    a caller can tell "mine is the ONLY endorsement" from "mine is one of several" — the
    sole-reviewer test the claim gate below runs. THREE values come back: every call site in
    this file unpacks three, and a two-value unpack raises ValueError at runtime.

    The legacy fallback below is byte-identical to HEARTBEAT's `item_review_status()` and
    ROLE-ANALYST's `review_status_of()`. All three must agree: they resolve the same rows in
    the same run, and a row that is `pending` to one and `ok` to another is a row that gets
    claimed without a review."""
    stored = str(cand.get("review_status") or "").strip()
    if not stored:
        # Legacy rows predating `review_status`. Only an EXPLICIT `discussion_pending: false`
        # counts as cleared; absent-and-unknown is `pending`, because there is no path to
        # claiming an item that was never reviewed. Defaulting the unknown case to `ok` would
        # make any malformed or hand-written row instantly claimable — the exact hole the
        # override removal was meant to close. (The old "absent is how a stacked re-test is
        # queued" rationale is obsolete: `requeue_stack` writes `review_status: "ok"` and the
        # parent's `proposal_post` explicitly, so a stack row needs no default.)
        stored = "ok" if cand.get("discussion_pending") is False else "pending"
    pid = cand.get("proposal_post")
    if not pid:
        # A review IS a comment on `proposal_post`. With no post there is nothing for a review
        # to attach to, so NOBODY can have reviewed this row, whatever it claims about itself:
        # it is unreviewable, not cleared. Resolve `pending` — never `ok` — so it is neither
        # claimable nor addable to the review backlog. Repairing it (posting a real [PROPOSAL]
        # and filling the field, or dropping the row) is an ANALYST job; a cpu-eval agent must
        # never stamp `review_status: ok` on a row no other agent can verify.
        if stored == "ok":
            print(f"[REVIEW] {AGENT_NAME}: {cand.get('id')} reads as review_status=ok but names "
                  f"no proposal_post — unverifiable, so it is NOT claimable. An analyst must "
                  f"give it a [PROPOSAL] post (or drop it from pending:).")
        return "pending", False, 0
    proposer = cand.get("proposed_by")
    oks = blocks = 0
    mine_ok = False
    for c in requests.get(f"{API}/posts/{pid}/comments",
                          headers=HEADERS).json().get("data", []):
        if _is_author(c, proposer):
            continue
        tag = _review_tag(c)
        if tag == REVIEW_BLOCK_TAG:
            blocks += 1
        elif tag == REVIEW_OK_TAG:
            oks += 1
            mine_ok = mine_ok or _is_author(c, AGENT_NAME)
    if blocks and not cand.get("unblocked_by"):
        return "blocked", mine_ok, oks
    return ("ok" if oks else "pending"), mine_ok, oks

# ── Collect the backlog across ALL team queues (`roster` came from Step 0) ────────────────
# One comments GET per pending item. Queues are short, and an unreviewed item is the most
# expensive thing in the system — it stalls a whole team's eval capacity.
backlog = []
for _tname, _t in (roster or {}).items():
    _tws = _t.get("workspace_id")
    if not _tws:
        continue
    _q = parse_frontmatter(requests.get(f"{API}/workspaces/{_tws}/files/queue.md",
                                        headers=HEADERS).json())
    for _it in (_q.get("pending") or []):
        if str(_it.get("proposed_by") or "") == AGENT_NAME:
            continue                       # never review your own proposal
        if not _it.get("proposal_post"):
            # UNREVIEWABLE, not reviewable-and-waiting: a review is a comment on
            # `proposal_post`, so there is no post to comment on and no way for any agent to
            # verify a verdict afterwards. Never take it into the backlog — reviewing it would
            # mean stamping `review_status: ok` on a row with no public review record anywhere,
            # which is the self-service clearance the two-person rule exists to prevent.
            # Repairing the row is an ANALYST job (ROLE-ANALYST Step 0.1 does exactly this);
            # flag it if you like, but do not clear it.
            print(f"[REVIEW] {AGENT_NAME}: {_it.get('id')} ({_tname}) has no proposal_post — "
                  f"unreviewable; leaving it for an analyst to repair.")
            continue
        _status, _mine_ok, _ = review_scan(_it)   # three values now — see review_scan's docstring
        if _status != "pending" or _mine_ok:
            continue                       # already resolved, or already reviewed by us
        backlog.append({"team": _tname, "ws_id": _tws, "item": _it})

def _proposal_age_key(entry):
    """Oldest proposal first. A row with no readable timestamp sorts LAST, so a missing field
    never jumps ahead of a proposal that has actually been waiting."""
    pid = entry["item"].get("proposal_post")
    ts = ""
    if pid:
        _p = requests.get(f"{API}/posts/{pid}", headers=HEADERS).json()
        _p = _p.get("data", _p)
        ts = _p.get("createdAt") or _p.get("created_at") or ""
    ts = ts or entry["item"].get("proposed_at") or ""
    return (ts == "", ts)

backlog.sort(key=_proposal_age_key)
todo = backlog[:REVIEW_CAP]
print(f"[REVIEW] {AGENT_NAME}: {len(backlog)} pending item(s) across all team queues; "
      f"reviewing {len(todo)} (cap {REVIEW_CAP}). Empty backlog ⇒ no comment at all.")

for _e in todo:
    _it, _tws = _e["item"], _e["ws_id"]
    _pid = _it.get("proposal_post")
    if not _pid:
        # Belt-and-braces: the backlog build already excluded these. The guard SKIPS THE WHOLE
        # ITEM, not just the comment POST — falling through to the queue write would record
        # `review_status: ok` with no review comment on any post: unverifiable by any other
        # agent, invisible to `review_scan`, and indistinguishable from a rubber stamp.
        print(f"[REVIEW] {AGENT_NAME}: {_it.get('id')} has no proposal_post — nothing to "
              f"review and nothing to clear; skipping.")
        continue
    # READ the proposal before judging it. A review that does not engage with the proposal is
    # the rubber stamp this protocol exists to prevent.
    _body = requests.get(f"{API}/posts/{_pid}", headers=HEADERS).json().get("content", "")

    # YOUR judgement, against the checklist above, the champion algo.py, and the team's
    # dead_ends.md / non_generalizable.md. `reasoning` is 2-3 sentences naming the specific
    # defect (block) or the specific reason this is worth pool time (ok) — never boilerplate.
    verdict, reasoning = review_proposal(_it, _body)     # your read: ("ok"|"block", str)

    # Re-check live, immediately before posting: another agent may have reviewed this item between
    # our backlog scan and now. Reviewing an already-cleared item wastes a review slot and, under
    # the every-reviewer-is-barred rule this replaced, could bar us from an item we would
    # otherwise be free to claim.
    #
    # A BLOCK IS NEVER DROPPED. Skipping on any non-`pending` status would discard a block we were
    # about to write just because someone else's [REVIEW-OK] landed first — silently deleting the
    # one verdict that says "this is defective", under exactly the concurrency this re-read exists
    # for. Block-dominates is the rule precisely so a single reviewer who spots a real defect is
    # not outvoted, so an "ok" that arrived first is the case where our block matters MOST.
    # Only an existing block makes ours redundant.
    _status_now, _, _ = review_scan(_it)
    if _status_now == "blocked" or (_status_now != "pending" and verdict != "block"):
        print(f"[REVIEW] {AGENT_NAME}: {_it.get('id')} became review_status={_status_now} while "
              f"we were reading it — someone got there first; skipping without commenting.")
        continue

    # POST THE COMMENT FIRST, then record the verdict on the row. The comment is the record;
    # the row is a cache of it. In that order a crash between the two leaves an item that is
    # merely stale (any later `review_scan` re-derives `ok` from the comment), never one marked
    # cleared with no review to point at.
    # The tag must OPEN the comment — the FIRST line is what the resolver reads, so the
    # reasoning is stripped before it is appended and never pushed onto a second line.
    requests.post(f"{API}/posts/{_pid}/comments", headers=HEADERS, json={
        "content": (f"{REVIEW_OK_TAG if verdict == 'ok' else REVIEW_BLOCK_TAG} "
                    f"{str(reasoning).strip()}")})

    # Record the verdict on the queue row — read-modify-PUT, NEVER PATCH (PATCH corrupts
    # nested YAML frontmatter like the pending: list; confirmed to destroy queue.md).
    # The row is a CACHE, not the record: if a concurrent queue write clobbers this PUT the
    # verdict survives in the comment, and `review_scan` re-derives the status from it.
    _qr = requests.get(f"{API}/workspaces/{_tws}/files/queue.md", headers=HEADERS).json()
    _qf = parse_frontmatter(_qr)
    _pend = list(_qf.get("pending") or [])
    _row  = next((r for r in _pend if r.get("id") == _it.get("id")), None)
    if _row is None:
        continue                       # it moved while we were reading — nothing to update
    if verdict == "ok":
        _row["review_status"] = "ok"
        # ADVISORY BOOKKEEPING ONLY — a convenience index for a human reading queue.md. It is
        # NOT the record and no gate reads it: `review_scan` derives "reviewed by me" from the
        # comments on `proposal_post` (that is the only source a concurrent queue write cannot
        # silently drop). Never gate a claim on this field, and never treat its absence as
        # evidence that nobody reviewed the item.
        _row["reviewed_by"] = sorted(set(list(_row.get("reviewed_by") or []) + [AGENT_NAME]))
    else:
        # A block makes the item unclaimable, so it must LEAVE pending: — otherwise every
        # claim loop in the run walks past it forever.
        _row["review_status"]  = "blocked"
        _row["blocked_by"]     = AGENT_NAME
        _row["blocked_reason"] = reasoning
        _pend = [r for r in _pend if r.get("id") != _it.get("id")]
        _blocked = [r for r in (_qf.get("blocked") or []) if r.get("id") != _it.get("id")]
        _qf["blocked"] = _blocked + [_row]
    _qf["pending"] = _pend

    _qbody = _qr.get("content", "").split("---", 2)[-1]
    _qnew  = f"---\n{yaml.safe_dump(_qf, sort_keys=False)}---{_qbody}"
    assert yaml.safe_load(_qnew.split("---")[1]) == _qf, "frontmatter round-trip failed"
    requests.put(f"{API}/workspaces/{_tws}/files/queue.md",
                 headers={**HEADERS, "If-Match": str(_qr.get("version", 0))},
                 json={"content": _qnew})
    print(f"[REVIEW] {AGENT_NAME}: {verdict.upper()} on {_it.get('id')} ({_e['team']}).")
```

### Step 1 — Eval Pool Availability

Evaluation is deterministic and CPU-only — there is no single device to contend for. The eval pool
is the remote Redis-backed distributed validation worker pool on the eval-head host; concurrency is
bounded by the worker count, not by a device. You do **not** need to check `nvidia-smi` (there is no
GPU). If your `scp`/`ssh` to the eval head fails or the eval call blocks past its timeout (no healthy
`xtb` workers serving Redis), treat that as an infrastructure problem: post a `[SUGGESTION]` flagging
the eval head / worker pool and do analyst work instead this cycle.

### Step 1.5 — Shared-Baseline Coordination — REQUIRED

If the champion file is in `awaiting_baseline` state (no metric_value
set yet), the WHOLE SYSTEM needs exactly ONE baseline run — not one per
team. Use claim-based coordination to avoid duplicated baselines.

Before using the lock, read `results/baseline_shared.md` from the main
workspace. **Only** a terminal scientific verdict file resolves an awaiting
baseline without creating a valid champion. Its parsed frontmatter must name
`exp_id: baseline_shared`, contain numeric `fitness`, contain `is_valid`, and
classify the outcome as exactly `DISCARD` or `REJECTED_TEST`; a
`REJECTED_TEST` must additionally contain numeric `test_metric_value` and
`test_is_valid`. If that whitelist matches, skip the entire lock block, go to
Step 2, and use the original first-valid-candidate seeding rule. Every other
absent, partial, malformed, or differently classified result means baseline
publication is unresolved; the lock rule below still applies.

```python
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)

# A scientifically terminal invalid baseline resolves the wait without creating a
# champion. Parse the file; do not infer resolution merely from its existence.
baseline_invalid_complete = False
if champ.get("status") == "awaiting_baseline":
    baseline_raw = requests.get(
        f"{API}/workspaces/{MAIN_WS_ID}/files/results/baseline_shared.md",
        headers=HEADERS,
    )
    if baseline_raw.status_code == 200:
        baseline_fm = parse_frontmatter(baseline_raw.json())
        baseline_outcome = baseline_fm.get("outcome")
        baseline_train_complete = (
            isinstance(baseline_fm.get("fitness"), (int, float))
            and baseline_fm.get("is_valid") in (0, 1)
        )
        baseline_test_complete = (
            baseline_outcome != "REJECTED_TEST"
            or (
                isinstance(baseline_fm.get("test_metric_value"), (int, float))
                and baseline_fm.get("test_is_valid") in (0, 1)
            )
        )
        baseline_invalid_complete = (
            baseline_fm.get("exp_id") == "baseline_shared"
            and baseline_outcome in ("DISCARD", "REJECTED_TEST")
            and baseline_train_complete
            and baseline_test_complete
        )

# Enter the lock block only while the baseline is genuinely unresolved.
baseline_stand_down = False
if champ.get("status") == "awaiting_baseline" and not baseline_invalid_complete:
    # Bind the timestamp locally — this block is self-contained, and `now` is the
    # ONE spelling used everywhere in this file (Step 3 claim, Step 7b champion.md).
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    # Propose the lock with If-None-Match, then DECIDE THE HOLDER FROM THE FILE HISTORY.
    lock_url = f"{API}/workspaces/{MAIN_WS_ID}/files/baseline_lock.md"
    requests.put(lock_url, headers={**HEADERS, "If-None-Match": "*"},
                 json={"content": f"holder: {AGENT_NAME}\nclaimed_at: {now}\n"})

    # A 2xx here means NOTHING. `If-None-Match: "*"` create-if-absent is NOT enforced by
    # this server: in tg_smoke_01 all FOUR cpu-eval agents got a success back for the same
    # lock (v1 cpu4, v2 cpu3, v3 cpu2, v5 cpu1). The version history IS totally ordered
    # server-side, so the LOWEST version that names a holder is the one true holder.
    #
    # DEPENDENCY: this rule needs `GET .../history` to return per-version `content` (it does —
    # verified against this API). The fallback below exists ONLY so that, if `content` ever
    # goes missing, the baseline cannot deadlock with every agent standing down.
    hist = requests.get(f"{lock_url}/history", headers=HEADERS).json().get("history", [])
    holders = []
    for h in hist:
        for line in (h.get("content") or "").splitlines():
            if line.startswith("holder:"):
                holders.append((h.get("version", 0), line.split(":", 1)[1].strip()))
                break
    if not holders:
        # History unreadable — fall back to the file's current holder line (last writer
        # wins). Still exactly ONE holder, which is all this lock has to guarantee, and it
        # keeps the baseline from deadlocking with every agent standing down.
        cur = requests.get(lock_url, headers=HEADERS).json().get("content", "") or ""
        holders = [(0, l.split(":", 1)[1].strip())
                   for l in cur.splitlines() if l.startswith("holder:")]
    have_lock = bool(holders) and min(holders, key=lambda vh: vh[0])[1] == AGENT_NAME

    if have_lock:
        # We hold the lock — run champion unchanged as the shared baseline,
        # then seed champion.md for everyone.
        item = {"id": "baseline_shared",
                "axis": "baseline",
                "direction": "none",
                "value": 0,
                "diff": "Evaluate champion algo.py unchanged (shared baseline)",
                "infrastructure_probe": True}
    else:
        # Someone else holds the lock — STAND DOWN. Never re-run the baseline "to be
        # safe": the holder writes champion.md, and you read that number when it lands.
        # END THIS INVOCATION through HEARTBEAT Part 6. Do not claim queue work, edit
        # algo.py, or run TRAIN/TEST while the shared baseline is unresolved.
        baseline_stand_down = True
        print("[BASELINE] another agent holds the shared-baseline lock; "
              "ending this invocation without candidate work")
```

**Never run baseline on a team-by-team basis.** The champion metric is
global — one run is sufficient. A non-holder does not continue to Step 2 or
Step 3: it performs the normal Part 6 handoff and exits. The parent will launch
a fresh invocation after the holder has published the baseline.

**HARD STOP:** if `baseline_stand_down` is true, execute no code below this
point. Set the Part-6 branch to `normal`, record that this invocation waited for
the shared baseline, emit the normal completion promise, and exit. Do not inspect
or claim a queue item first.

### Step 2 — Read Champion Config

```python
# Read champion config from main workspace
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)
champ_version = champ_raw.get("version", 0)  # Save for race condition check later

# Read canonical champion algo.py (SINGLE SOURCE OF TRUTH)
# Located at: {FOCUS_ROOT}/champion/algo.py
# Copy it to your workspace before making changes. `algo.py` is the ONLY file
# you edit and the ONLY file the evaluator needs — there is no prepare.py /
# pyproject.toml / uv.lock for this task. The evaluator (eval_candidate.py,
# validate.py, molecules/, metrics.yaml) lives in the sella checkout on the
# remote eval head; you never copy it locally.
import shutil
from pathlib import Path
workdir = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")
workdir.mkdir(parents=True, exist_ok=True)
src = Path(f"{FOCUS_ROOT}/champion/algo.py")
if not src.exists():
    # Fallback to the task's shipped baseline algo.py.
    src = Path(f"{FOCUS_ROOT}/task/repo/algo.py")
shutil.copy(src, workdir / "algo.py")
```

**Never read algo.py from another agent's workspace.** Always use `{FOCUS_ROOT}/champion/algo.py`.

### Step 2b — Read Task Specifications

`TASK.md` defines the metric, the validity gate, and the constraints your diff must respect.

```python
# Read task specification
task_spec_path = f"{FOCUS_ROOT}/task/TASK.md"
with open(task_spec_path) as f:
    task_content = f.read()
```

**The answer-key rule in `TASK.md` governs what you are allowed to write.** You are the agent who
actually writes the code, so that rule is enforced by you or by nobody (the historical 142-selector
champion in this project was produced exactly this way). The test: every branch you add must be
selected by **continuous physical/geometric quantities an unseen molecule could exhibit** — never by
molecule identity, exact chemical formula, exact atom counts, or a chemotype selector.

Keep `task_content` in scope. The gate is **executed in Step 4**, immediately before the diff is
written — that is the first point where the claimed `item` exists (Step 3 binds it) and the last
point before any code lands in `algo.py`.

### Step 3 — Claim Experiment from Team Queue (REQUIRED)

**Safety: abort if a prior unposted result still sits in `result_latest.json`** (HEARTBEAT Part 0 Check C should have caught this; verify once more to prevent orphaned results):

```python
import json
from pathlib import Path
_p = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/result_latest.json")
if _p.exists():
    _pend = json.loads(_p.read_text())
    if not _pend.get("posted_to_workshop") and _pend.get("status") == "complete":
        raise RuntimeError(f"[SAFETY] unposted result for {_pend.get('exp_id')} — re-enter HEARTBEAT, go to Part 5")
```

Check your team's queue for pending experiments.

```python
queue_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                         headers=HEADERS).json()
queue = parse_frontmatter(queue_raw)
pending = queue.get("pending", [])

try:
    item          # Step 1.5 binds this ONLY when we hold the shared-baseline lock
except NameError:
    item = None

# The shared baseline is this rotation's work and the baseline LOCK is its exclusion — it is
# not a queue row, so a queue row must never replace it.
is_baseline = bool(item) and item.get("id") == "baseline_shared"

if not is_baseline and not pending:
    # EMPTY QUEUE — self-propose a bold experiment within your team's dimension. Read your
    # team's strategy.md, dead_ends.md, and the champion code to pick the highest-value
    # untested change. Then:
    #   1. Post a [PROPOSAL] to the workshop (full rationale + diff)
    #   2. Add it to your team's queue.md with `review_status: pending`, the `proposal_post`
    #      id, `proposed_by: AGENT_NAME`, and the REQUIRED axis / direction / value tags
    #   3. END the rotation here — you may NOT claim it yourself now.
    # Self-design is NOT a review bypass: a self-designed item takes exactly the same path as
    # an analyst's, and it becomes claimable only once a non-author has reviewed it, in a
    # later rotation. Writing the proposal and evaluating it in one session is how an
    # unreviewed mechanism reaches the pool, which is the thing the review gate exists to stop.
    # Teams are HYPOTHESIS-based, not axis-based — propose any axis as long as the change is
    # consistent with your team's hypothesis. Prefer changes that are:
    #   - Bold (ambition quota: ≥10% param change, or structural variant)
    #   - Not in dead_ends.md
    #   - Grounded in champion code analysis, not speculation
    self_designed_id = propose_and_queue_self_designed_experiment()   # your work: the POST
    # and the queue write, in that order — the row needs the `proposal_post` id.
    print(f"[CLAIM] {AGENT_NAME}: queue empty — queued self-designed {self_designed_id} as "
          f"review_status=pending; NOT claiming it this rotation.")
    import sys; sys.exit(0)
```

**Every experiment must have a full API trail:** [PROPOSAL] post → queue
entry → review → claim → evaluation → result file → [RESULT] post. Self-designed
experiments follow the same trail, review included; the only difference is the
cpu-eval agent writes the proposal instead of an analyst. It never reviews its
own proposal and never claims it in the same rotation. Keep the [PROPOSAL] short —
hypothesis, the exact change, the predicted effect, and why it is not already
in `dead_ends.md` / `non_generalizable.md`. A few sentences, not an essay.

**Every queue item and [PROPOSAL] MUST include axis / direction / value
tags.** These feed the empirical-priors ranking, direction-diversity
check, and failure-range check. Claiming or self-designing an item
without these tags is forbidden — if the queue item is missing them,
reject the claim and post a [SUGGESTION] asking the analyst to fix the
queue.

**Teams are hypothesis-based, not axis-based.** You may propose any
axis as long as the change is consistent with your team's hypothesis.
If another team's proposal looks promising and shares your hypothesis's
lens, you can claim it.

**Any module you write that claims, POSTs or PUTs must guard that work behind
`if __name__ == "__main__":`.** Import must be free of side effects — a claim loop left at
module top level runs again on every import, and one such helper produced four claims against a
single evaluation. The import brings in the functions; `__main__` runs them.

```python
# ── THE REVIEW GATE ──────────────────────────────────────────────────────────────────
#
# Every queue item carries `review_status`: `pending` (proposed, not yet reviewed), `ok`
# (at least one valid [REVIEW-OK] and no unresolved [REVIEW-BLOCK]) or `blocked` (at least
# one valid [REVIEW-BLOCK]). **Only `ok` is claimable.** `review_scan` (Step 0.5) resolves
# it from the [PROPOSAL]'s comments — do not redefine it or the tag constants here.
#
# There is NO override, and no path to claiming an unreviewed item. The two the old gate
# had are DELETED: a time grace that claimed anything older than 15 minutes, and a
# queue-starvation escape that claimed the last item standing. Both turned "unreviewed"
# into a speed bump — an unreviewed mechanism always reached the pool eventually, by
# waiting or by being alone in the queue. An idle rotation is cheaper than an eval on an
# unreviewed mechanism: the rotation costs nothing, the eval costs shared pool time and
# produces a number nobody vetted the meaning of.
#
# What the two-person rule actually demands is that an endorsement you are NOT relying on
# exists. So the bar is on being the SOLE reviewer: if your `[REVIEW-OK]` is the only one on
# the item, claiming it is self-service and you must skip it. If a second, independent
# `[REVIEW-OK]` stands, the independent scrutiny is there whether or not you also reviewed —
# claim it. Reviewing an item is NOT by itself a disqualification. Nor can you claim your own
# fresh proposal: it enters the queue `pending`, and `pending` is not claimable. (Once a
# non-author has reviewed it in a later rotation, it is claimable by anyone, including you.)

def review_cleared(cand):
    """True when `cand` may be claimed. False ⇒ skip it and try the next item."""
    status, mine_ok, oks = review_scan(cand)
    if status != "ok":
        print(f"[REVIEW] {AGENT_NAME}: {cand.get('id')} is review_status={status} — "
              f"not claimable; skipping to the next item.")
        return False
    # Bar ONLY the sole reviewer. `mine_ok and oks == 1` means the single endorsement on this item
    # is my own, so claiming it would be self-service. With a second independent [REVIEW-OK] the
    # scrutiny exists whether or not I also reviewed, so the item is claimable.
    # Never bar on `mine_ok` alone: at cold start every agent reviews the same few seeds, and
    # barring every reviewer strands them permanently (agent names persist across rotations).
    sole_reviewer = mine_ok and oks == 1
    if sole_reviewer:
        print(f"[REVIEW] {AGENT_NAME}: {cand.get('id')} carries MY OWN {REVIEW_OK_TAG} and no "
              f"other — I am its only reviewer, so claiming it would be self-service; "
              f"skipping to the next item.")
        return False
    return True

# ── THE CLAIM: a per-item claim file, arbitrated by IMMUTABLE history ─────────────────
#
# THE CLAIM IS WHAT STOPS TWO AGENTS RUNNING THE SAME EXPERIMENT — and a `claims[me]` entry
# in queue.md is not it: a competing agent's PUT overwrites the whole `claims:` map, and the
# queue looks clean afterwards because each agent pops only its own key. In tg_smoke_01 two
# agents implemented and evaluated `exp_trust_partial_step_shrink` end to end — the same
# experiment paid for twice on shared infrastructure.
#
# The queue item STAYS in `pending:`. `pending:` remains the single source of truth for what
# work exists; exclusion lives in its own single-purpose file per item, `claims/{exp_id}.md`.
# Three steps, and the third is the whole mechanism:
#
#   1. PUT the item's claim file with `If-None-Match: "*"`, naming yourself. IGNORE the status
#      code — neither `If-None-Match` nor `If-Match` is enforced on this server, so a 2xx
#      proves NOTHING. The write is only a proposal.
#   2. GET that file's `/history` and read VERSION 1.
#   3. `v1.updatedBy` IS the holder. Not you ⇒ you LOST: do not evaluate, do not write any
#      result, move on to the next pending item whose claim file you can win.
#
# ATTEMPT CHAIN. Releasing a claim is a `released: true` marker, never a delete, because a
# released file's version 1 is frozen to the old holder forever — a later PUT would land as
# vN+1 and the item would be unclaimable for the rest of the run. So a release names a
# SUCCESSOR path and the next claim starts a fresh version 1 there: `claims/{exp_id}.md`,
# then `claims/{exp_id}.r1.md`, `.r2.md`, … `resolve_claim` walks that chain. ROLE-MONITOR's
# stale sweep uses the identical layout and resolver — the two MUST stay in step, or a
# released item is invisible to every agent and the queue silently stops moving.
#
# WHY HISTORY, NOT A READ-BACK OF THE FILE. A read-back is TIMING-dependent: it asks "what
# does the file say right now", and the answer changes as later writes land — so agent A can
# read itself as the winner, agent B's PUT can land after, and B reads itself as the winner
# too. Both evaluate. No settle delay closes that; a delay only narrows the window, which is
# exclusion built on a heuristic. History arbitration is ORDER-INDEPENDENT: every agent,
# reading at any moment, derives the same winner from the same immutable record, because who
# wrote version 1 cannot change. It needs no server-side CAS — only history, which this API
# provides. Do NOT "simplify" this back into a read-back, and do NOT re-introduce a settle
# delay: the delay was a symptom of the broken design, never a safeguard.
#
# `updatedBy` is correctly per-agent even though every agent shares one api_key (the
# `X-Agent-Name` header drives it), so it is trustworthy for exactly this purpose.
#
# `now` is the single spelling used file-wide; it is bound per claim attempt inside.
CLAIMS_DIR = "claims"
MAX_CLAIM_ATTEMPTS = 8   # same bound as ROLE-MONITOR's resolver

def claim_path_for(eid, attempt=0):
    """attempt 0 is where every FIRST claim goes; each release opens the next attempt."""
    return (f"{CLAIMS_DIR}/{eid}.md" if attempt == 0
            else f"{CLAIMS_DIR}/{eid}.r{attempt}.md")

def _v1_holder(path):
    """`updatedBy` on VERSION 1 — the immutable fact the whole protocol rests on."""
    r = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/{path}/history", headers=HEADERS)
    if r.status_code != 200:
        return None
    body = r.json()
    hist = body.get("history", body.get("data", [])) if isinstance(body, dict) else body
    for h in (hist or []):
        if int(h.get("version", 0) or 0) == 1:
            return h.get("updatedBy")
    return None

def _parse_claim(content):
    """Claim files are flat `key: value` lines, not frontmatter (ROLE-MONITOR writes the same
    shape). Values stay strings."""
    fm = {}
    for line in (content or "").splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm

def resolve_claim(eid):
    """Newest attempt for `eid`: {path, attempt, holder, released} — None ⇒ never claimed.
    `released: true` ⇒ the item is AVAILABLE again and the next claim belongs at attempt+1."""
    live = None
    for attempt in range(MAX_CLAIM_ATTEMPTS):
        path = claim_path_for(eid, attempt)
        r = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/{path}", headers=HEADERS)
        if r.status_code != 200:
            break
        fm = _parse_claim((r.json() or {}).get("content"))
        live = {"path": path, "attempt": attempt,
                # DEGRADED fallback, history unreadable only: the file's own `holder:` line.
                # Weaker than v1 arbitration, but it still names exactly one holder per read
                # and it keeps the queue from stalling with every item unclaimable.
                "holder": _v1_holder(path) or fm.get("holder"),
                "released": str(fm.get("released", "")).lower() == "true"}
    return live

def claim_holder(eid):
    """The ONE holder of `eid` right now. None ⇒ FREE (never claimed, or handed back)."""
    c = resolve_claim(eid)
    return None if (c is None or c["released"]) else c["holder"]

import re   # resolve_claim_path_meta parses the attempt number out of a claim path


def release_claim(path, reason, holder=None, claimed_at=None):
    """Hand an item you hold back to the queue. NOT a delete, and NEVER a rewrite of the same
    path with a fresh holder: v1 of a path is frozen forever, so re-PUTting `claims/{eid}.md`
    would leave v1 naming the ORIGINAL holder and every later claimant would compute "lost" —
    the item becomes permanently unclaimable, a silent deadlock worse than the duplicate run
    this whole mechanism exists to prevent.

    Instead write a `released: true` marker naming the SUCCESSOR path, so the next claim is
    version 1 of a file nobody has written. Byte-identical to the marker ROLE-MONITOR's
    release_claim and HEARTBEAT Part 5 write — all three must agree or a released item is
    invisible to one of them. The queue item never left `pending:`, so nothing is lost.
    """
    _c = resolve_claim_path_meta(path)
    nxt = claim_path_for(_c["exp_id"], _c["attempt"] + 1)
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/{path}", headers=HEADERS,
                 json={"content": (f"holder: {holder or AGENT_NAME}\n"
                                   f"claimed_at: {claimed_at or ''}\n"
                                   f"released: true\n"
                                   f"released_at: {datetime.now(timezone.utc).isoformat()}\n"
                                   f"released_by: {AGENT_NAME}\n"
                                   f"release_reason: {reason}\n"
                                   f"next_claim_path: {nxt}\n")})
    print(f"[CLAIM] released {path} ({reason}) -> next attempt at {nxt}")
    return nxt


def resolve_claim_path_meta(path):
    """Recover (exp_id, attempt) from a claim path: claims/<eid>.md == attempt 0,
    claims/<eid>.r<n>.md == attempt n."""
    base = path.rsplit("/", 1)[-1][:-3]          # strip 'claims/' and '.md'
    m = re.match(r"^(?P<eid>.+?)\.r(?P<n>\d+)$", base)
    return {"exp_id": m.group("eid"), "attempt": int(m.group("n"))} if m else {"exp_id": base, "attempt": 0}


def claim_item(cand):
    """Claim `cand` EXCLUSIVELY. Returns the claim-file PATH we hold (a truthy string) when WE
    wrote version 1 of it, and None otherwise — so `if claim_item(cand):` still reads as the
    win/lose test it always was.

    It RETURNS the path rather than leaving it inside the function because every later step
    needs the exact file: after a release the claim lives at `claims/{eid}.r<n>.md`, and `n` is
    not recoverable from `exp_id`. Each Step-4 sentinel writes it as `claim_path`, HEARTBEAT
    Part 5 5a2 re-verifies ownership from it on resume, and Step 3c / Step 6 release it. Losing
    it is not cosmetic: a resume that falls back to attempt 0 reads v1 as the ORIGINAL holder
    and abandons an item this agent legitimately owns."""
    eid = cand["id"]
    cur = resolve_claim(eid)
    if cur is not None and not cur["released"]:
        return None                         # already held by someone
    # A released file's v1 is frozen to the OLD holder forever — claim at the NEXT attempt,
    # never back into a released file.
    att  = 0 if cur is None else cur["attempt"] + 1
    path = claim_path_for(eid, att)
    now  = datetime.now(timezone.utc).isoformat()
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/{path}",
                 headers={**HEADERS, "If-None-Match": "*"},
                 json={"content": f"holder: {AGENT_NAME}\nclaimed_at: {now}\n"
                                  f"exp_id: {eid}\nattempt: {att}\n"})
    after = resolve_claim(eid)
    if (not after) or after["path"] != path or after["released"] \
            or after["holder"] != AGENT_NAME:
        return None

    # WON. Everything below is BOOKKEEPING ONLY — `claims:` in queue.md is what the monitor's
    # stale sweep and the [AUDIT] pass read. It is NOT the exclusion and must never be treated
    # as one. The item stays in `pending:`, and no snapshot of it is stashed anywhere.
    # Read-modify-PUT (DO NOT use PATCH — it corrupts nested YAML frontmatter like pending:
    # lists. Confirmed to destroy queue.md across teams.)
    q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json()
    fm = parse_frontmatter(q_raw)
    fm.setdefault("claims", {})[AGENT_NAME] = {"exp_id": eid, "claimed_at": now}
    body = q_raw.get("content", "").split("---", 2)[-1]
    new_content = f"---\n{yaml.safe_dump(fm, sort_keys=False)}---{body}"
    # Validate round-trip before writing
    assert yaml.safe_load(new_content.split("---")[1]) == fm, "frontmatter round-trip failed"
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                 headers={**HEADERS, "If-Match": str(q_raw.get("version", 0))},
                 json={"content": new_content})   # 409 benign — the claim file already decided
    return path

def tags_complete(cand):
    """An item without axis / direction / value is UNCLAIMABLE (see above). Say so once, in a
    [SUGGESTION], then skip it — never silently, and never claim it anyway."""
    missing = [k for k in ("axis", "direction", "value") if cand.get(k) is None]
    if not missing:
        return True
    requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP,
        "title": f"[SUGGESTION] queue item {cand.get('id')} is missing {', '.join(missing)}",
        "content": (f"`{cand.get('id')}` cannot be claimed: its queue row is missing "
                    f"{', '.join(missing)}. axis / direction / value feed the empirical-priors "
                    f"ranking, the direction-diversity check and the failure-range check, so an "
                    f"untagged item cannot be recorded as evidence. Please fix the queue row."),
        "notify_agents": team_members,
        "tags": [f"team:{MY_TEAM}", "type:suggestion"],
    })
    print(f"[CLAIM] {AGENT_NAME}: {cand.get('id')} missing {missing} — unclaimable; "
          f"posted [SUGGESTION].")
    return False

# The shared baseline is NOT a queue item: Step 1.5 synthesised it, and the baseline LOCK
# already provided the exclusion. It therefore gets NO claim file and NO queue row. Running
# the claim protocol against it would look for a `pending:` row that does not exist, stand the
# lock holder down, and the baseline would never run. (`is_baseline` was bound at the top of
# this step, before anything could overwrite the baseline item with a queue row.)
if is_baseline:
    claimed = item
    claim_path = None      # no claim file exists for it; Step 6 has nothing to release
else:
    # PRIORITY DECIDES THE ORDER. Analysts rank every item high / medium / low, and until this
    # sort nobody read the field: the loop walked `pending:` in raw insertion order, so a
    # high-priority item queued late waited behind every stale medium one. `sorted` is STABLE,
    # so equal-priority items keep their queue order and the tie-break stays FIFO. An unknown
    # or missing priority sorts as medium — never last, or a typo would strand an item.
    _PRIO = {"high": 0, "medium": 1, "low": 2}
    candidates = sorted(pending,
                        key=lambda c: _PRIO.get(str(c.get("priority", "medium")).lower(), 1))
    claimed = None
    claim_path = None
    for cand in candidates:
        if not tags_complete(cand):
            continue
        if not review_cleared(cand):
            continue
        # Pre-claim availability check: skip an item whose claim file already names someone
        # else. Cheap, and it keeps agents off a claim file that is already decided.
        held_by = claim_holder(cand["id"])
        if held_by not in (None, AGENT_NAME):
            print(f"[CLAIM] {AGENT_NAME}: {cand['id']} is already held by {held_by}; skipping.")
            continue
        # CAPTURE the path we won on. It is the ONLY record of which attempt file this cycle
        # holds (`claims/{eid}.md` vs `claims/{eid}.r<n>.md`), and every sentinel below writes
        # it as `claim_path`; re-deriving it later from `exp_id` alone would silently name
        # attempt 0 and hand our item away on the next resume.
        _won_path = claim_item(cand)
        if _won_path:
            claimed, claim_path = cand, _won_path
            break
        # LOST: another agent wrote version 1 and is evaluating it right now. Do NOT
        # evaluate it too — move on.
        print(f"[CLAIM] {AGENT_NAME}: lost the race for {cand['id']}; trying the next item.")

if claimed is None:
    # Nothing claimable this rotation — every item was untagged, unreviewed, blocked, endorsed
    # by nobody but us, or won by someone else. End the cycle cleanly. You may still self-design
    # (post [PROPOSAL] → queue it with `review_status: pending`) but you may NOT claim that
    # item now; it is claimable once a non-author reviews it. Never proceed to Step 4 on an
    # unclaimed item, and never "just run" an unreviewed one.
    print(f"[CLAIM] {AGENT_NAME}: no item won this rotation — not evaluating anything.")
    import sys; sys.exit(0)

item = claimed

# Bind the names every later step reads off the claimed item. Steps 4-8 use `exp_id` and
# `description` throughout; this is the one place they are defined.
#
# `CLAIMED_EXP_ID` is the FROZEN identity of this cycle: assigned once, here, and NEVER
# reassigned anywhere below. Every later guard compares against IT — not against
# `item["id"]`. Asserting `item["id"] != exp_id`, which is what this file used to do, is a
# TAUTOLOGY: both sides are read out of the same dict and nothing rebinds `item` in between,
# so the condition is always False and the whole identity-bleed abort path was dead code
# while the bleed it was written for kept happening (one agent's axis inside another's result
# file; a champion provenance post carrying a third experiment's diff header). A constant
# captured at claim time is the only thing a bled `item` or `exp_id` can be caught against.
CLAIMED_EXP_ID = claimed["id"]
exp_id      = CLAIMED_EXP_ID
description = item.get("description") or item.get("diff") or item.get("hypothesis") or exp_id

def identity_intact(where):
    """False ⇒ cross-agent state bleed: `exp_id` or `item` no longer names the experiment we
    claimed. Cheap — re-assert before every expensive or irreversible operation (the eval
    launch, the diff render, the `results/` write), not once at the top."""
    if exp_id == CLAIMED_EXP_ID and (item or {}).get("id") == CLAIMED_EXP_ID:
        return True
    print(f"[IDENTITY] {where}: this session claimed {CLAIMED_EXP_ID}, but exp_id={exp_id} "
          f"and item.id={(item or {}).get('id')} — cross-agent state bleed.")
    return False
```

If queue is empty, design your own experiment and QUEUE it for review — the rotation
ends there. Your only constraint on the design is **consistency with your team's
hypothesis**: the change you propose must be one your team's hypothesis predicts will
improve the metric. Any axis is fair game. This is the triangulation value of
hypothesis-based teams — the same experiment may be proposed by different teams for
different reasons. The proposal costs one rotation of latency and buys a second pair of
eyes on the mechanism before any pool time is spent on it.

```python
# Discover your team's context for self-designed experiments
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json()["files"]
# Read strategy.md, dead_ends.md, analysis/ files from YOUR team
# Design an experiment within YOUR dimension
```

### Step 3b — Handling a Stacked Re-Test (REQUIRED when `stacked_from` is set)

Most queue items are fresh proposals. Some are **re-tests**: `requeue_stack(...)` (Step 7) produces
them when a candidate passed BOTH gates and then lost the promotion race. Their id is
`{exp_id}_stack` and they carry `stacked_from`, `stack_reason`, `lost_to`, `source_algo`, `diff_path`,
`diff_hunks`, `prior_train_fitness` and `prior_test_fitness`. Detect one before you write any code:

```python
is_stacked = bool(item.get("stacked_from"))
```

If `is_stacked` is False, skip this step and go to Step 3c. Otherwise, all six rules below apply.

**1. What a stacked item is.** The change already cleared the train margin AND the held-out test gate
— but against an **older** champion. It was never promoted, because another candidate landed in
champion.md first. Your job is to find out whether the same mechanism still helps **on top of the
winner**.

**2. ALWAYS start from the CURRENT champion. Never copy `source_algo` over `algo.py`.** Step 2 already
copied `{FOCUS_ROOT}/champion/algo.py` into your workspace — that file is your starting point, exactly
as for any other experiment. `source_algo` is a COMPLETE `algo.py` frozen at *old champion + this
mechanism*; it does **not** contain the winner's change. Copying it silently REVERTS the winner:
champion.md would name the winner while `algo.py` held the old code plus this mechanism, and nothing
in the lineage would record the regression. That is the single most damaging mistake available in this
step — a silent champion regression that survives every later cycle.

**3. `diff_path` / `diff_hunks` / `description` are a statement of INTENT — the mechanism, not the
bytes.** The recorded patch was computed against the OLD champion, so applying it to the current
champion may fail outright, or (worse) apply in the wrong place because the winner moved the context
lines. Re-apply the **mechanism** to the current champion, re-deriving the edit by hand whenever the
recorded patch no longer applies cleanly. `source_algo` is for **READING** — open it to see exactly
what the mechanism was. It is never for copying.

**4. Evaluate both gates fresh, against the CURRENT champion's anchors.** Run the train eval (Step 4)
and, on a provisional keep, the held-out test eval (Step 4c), comparing against `metric_value` /
`test_metric_value` from the champion.md you read this cycle. `prior_train_fitness` and
`prior_test_fitness` are **context for the analyst only** — they describe a different baseline. Never
reuse them as this cycle's result and never let them stand in for a gate.

**5. If the mechanism genuinely conflicts with the new champion, do NOT force a merge.** When the
winner's change occupies the same code and the two cannot coexist without inventing a third
mechanism, stop: set `item["stack_conflict"] = True`, do not evaluate, and take Step 4's abort route
(Step 4's defaults block reads that flag and binds `cycle_aborted = "stack_conflict"`; write the Step
4 sentinel exactly as the scope-abort path does, set `score = None`, and continue to **Step 4c**).
Step 5 then records `outcome = "FAILED"` and Step 6 re-queues the item with
`last_failure = "stack_conflict"`. Explain the conflict in the result file and the [RESULT] post so an
analyst can re-derive the mechanism deliberately. Inventing a merge is a second mechanism, which
Step 4's one-mechanism rule forbids anyway.

**6. A stacked re-test is a genuinely NEW experiment.** The mechanism may interact with the winner's
change, and the combination can be **worse** than the winner alone — in which case this cycle is a
DISCARD or a REJECTED_TEST, recorded like any other outcome. That is a legitimate scientific result
about an interaction, not a bug and not a reason to doubt the earlier NEAR_MISS. Report the numbers as
measured.

**Defensive check — run it after you edit `algo.py` and BEFORE you evaluate.** If your candidate came
out byte-identical to `source_algo`, the winner's change is gone: you reverted it instead of stacking
on it.

```python
import filecmp
from pathlib import Path

if is_stacked and item.get("source_algo") and Path(item["source_algo"]).exists():
    _cand = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/algo.py")
    if filecmp.cmp(str(_cand), item["source_algo"], shallow=False):
        # algo.py == (OLD champion + this mechanism) ⇒ the winner's change was REVERTED.
        # Abort: never evaluate, never promote. Treat exactly as a stack conflict (rule 5).
        print(f"[STEP3b] {exp_id}: algo.py is byte-identical to source_algo — the current "
              f"champion's change was reverted. Aborting as FAILED (stack_conflict).")
        item["stack_conflict"] = True
```

### Step 3c — Dedup Check

Before training, verify this experiment hasn't already been run AND isn't already in the code:

```python
# 1. Search workspace results for similar experiments
hits = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/search?q={mechanism_keyword}",
                    headers=HEADERS).json()["results"]
# If results/ files already cover this mechanism, skip it

# 2. Search team dead ends
team_hits = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/search?q={mechanism_keyword}",
                         headers=HEADERS).json()["results"]
# If this exact mechanism is already recorded in dead_ends.md or non_generalizable.md,
# skip it — re-running a recorded result buys nothing. A non_generalizable entry is
# strong evidence against the mechanism, not just its tuning, so a re-proposal that only
# tweaks the value needs a stated reason why the failure was tuning; absent that, skip.
# It does NOT close the axis by itself — closure is the axis-closure rule's job.

# 3. Check if the mechanism already exists in champion code
champion_code = open(f"{FOCUS_ROOT}/champion/algo.py").read()
if mechanism_keyword.lower() in champion_code.lower():
    print(f"ALREADY IN CODE: {mechanism_keyword} — skip this experiment")
    # Release the claim you already won, then pick the next item. "Release" means exactly
    # one thing in this system: write the release marker to YOUR claim file (never DELETE,
    # never rewrite the path in place — v1 of a path is frozen, so a re-PUT would leave v1
    # naming you and strand the item forever). Same marker ROLE-MONITOR and HEARTBEAT write.
    release_claim(claim_path, reason="already_in_code")

# 4. **Target validation** — a change that reads or writes a named variable or
#    collection in the target code (a list of params, a config dict, a feature
#    set) is only meaningful if that collection is non-empty AND actually wired
#    into the code path you expect. Helper variables are sometimes defined but
#    never referenced — tuning them produces noise-only deltas that look like
#    real signal, and the cost lands as cascades of dead hypotheses.
#
# Example pattern:
#   target_collection = f"{group_name}_items"
#   if f"{target_collection} = []" in code:
#       print(f"DEAD TARGET: {target_collection} is empty — change would be a no-op")
#       release_claim(claim_path, reason="dead_target")  # then post a [SUGGESTION]
```

### Step 3d — External Repo Setup (if experiment requires it)

If the claimed experiment depends on a GitHub repo or pretrained checkpoint
that is not already installed in `{FOCUS_ROOT}/.cache/repos/`:

1. Read `{FOCUS_ROOT}/system/external-repo-setup/SKILL.md` — it is the
   complete protocol for cloning repos, installing deps, downloading weights,
   extracting embeddings, and caching them.
2. Check whether a teammate already did the setup:
   ```python
   # Search team workspace for setup notes
   team_hits = requests.get(
       f"{API}/workspaces/{TEAM_WS_ID}/search?q=setup_{REPO_NAME}",
       headers=HEADERS
   ).json()["results"]
   ```
   If `knowledge/setup_{REPO_NAME}.md` exists, load pre-cached embeddings
   instead of re-running extraction.
3. After successful setup, write `knowledge/setup_{REPO_NAME}.md` to the
   team workspace so other CPU-eval agents can reuse the cached embeddings.

**Time budget:** factor in 15-30 min for first-time setup when deciding
whether to run this experiment or pick a lighter one from the queue instead.

### Step 4 — Apply Change and Evaluate

Apply ONE change from the experiment's diff to `algo.py`, then **block synchronously** on the remote
eval. Detached / fire-and-forget evaluation is forbidden: if the agent session ends before
parsing the eval JSON, the real metric is computed but never recorded — the entire cycle's work
vanishes. The agent MUST wait for the `ssh` eval call to return and then run Steps 5–8 in the same
session.

Evaluation is **deterministic and CPU-only**: the candidate's `minimize_func` is cloudpickled and run
against a fixed molecule set on a remote Redis-backed worker pool. There is no GPU, no CUDA, no seed
variance — the same `algo.py` always produces the same score.

**Answer-key gate — run this FIRST, before a single line goes into `algo.py`.** Apply the test from
Step 2b's `task_content`: if the claimed `item` asks for a branch selected by molecule identity,
exact chemical formula, exact atom counts, or a chemotype selector, do not write it and do not
evaluate it. This is the earliest point where `item` is bound (Step 3) and the last point before code
lands, which is why the gate lives here and not in Step 2b.

The same block binds the **diff-artifact defaults**. Steps 5 and 8 render a `## Diff` section
unconditionally, and `diff_applied` is *not* a proxy for "a diff artifact exists": on an abort the
item carries no `diff_applied` key, so Step 4c's `item.get("diff_applied", True)` returns **True**
while the difflib block below never ran. Bind the names first, then gate.

```python
import json, os
from pathlib import Path
from datetime import datetime, timezone

ws  = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
rep = ws / "repo"

# ── Diff-artifact defaults — bound BEFORE any abort path can fire ────────────
diff_text     = ""
diff_lines    = []
hunk_headers  = []
n_hunks       = 0
diff_path     = None
# The id everything PUBLIC is filed under from here down (Step 7c's sentinel, Step 8's
# [RESULT] title and body). Normally identical to `exp_id`; the Step 5 identity-bleed guard
# repoints it at CLAIMED_EXP_ID, because on that path `exp_id` is exactly the value we no
# longer trust and a post titled with a bled id files this cycle under another experiment.
REPORT_EXP_ID = exp_id
# None | "stack_conflict" | "answer_key_reject" | "scope_abort" | "identity_bleed" | "claim_lost".
# `stack_conflict` is decided in Step 3b (a stacked re-test that cannot be re-applied to the
# current champion), so it is picked up from the item flag rather than set below.
cycle_aborted = "stack_conflict" if item.get("stack_conflict") else None
if cycle_aborted == "stack_conflict":
    score = None              # outcome = "FAILED" in Step 5 — nothing was tested
    # Write the Step 4 sentinel exactly as the answer-key path below does (same keys, with
    # `"item": item` carrying the stack_conflict flag), then continue to **Step 4c**.

# ── Answer-key gate: reject identity-conditioned proposals before any code ───
if not cycle_aborted and proposal_selects_on_molecule_identity(item, task_content):  # your read
    item["answer_key_reject"] = True
    cycle_aborted = "answer_key_reject"
    score = None              # outcome = "FAILED" in Step 5 — nothing was tested
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "result_latest.json").write_text(json.dumps({
        "status": "failed", "posted_to_workshop": False,
        "exp_id": exp_id, "agent": AGENT_NAME, "item": item, "queue_claimed": True,
        # The claim file this cycle actually holds. HEARTBEAT Part 5 5a2 re-verifies
        # ownership from it on resume; without it the resume falls back to attempt 0
        # (`claims/{exp_id}.md`), which is WRONG for any item that was released and
        # re-claimed at claims/{exp_id}.r<n>.md — it would read v1 as the ORIGINAL
        # holder and abandon an item this agent legitimately owns.
        "claim_path": claim_path,
        # EXPERIMENT axis direction from the queue item — never the champion's "minimize".
        "direction": item.get("direction"),
        "score": None, "fitness": None, "is_valid": None,
        "test_status": "not_run", "test_score": None, "test_fitness": None,
        "test_is_valid": None, "test_pass": False, "test_reason": None,
        "test_stdout_path": None, "test_mean_rel_energy": None,
        "algo_path": str(rep / f"algo_{exp_id}.py"),
        "remote_cand": None, "stdout_path": None, "stderr_path": None,
        "pid": os.getpid(), "description": description,
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2, default=str))
    # Then continue to **Step 4c** — NOT straight to Step 5. 4c is None-safe and is the
    # only place that binds `metric_name`, `current_best`, `champ_test`, `our_test`,
    # `test_valid` and `race_condition` for Steps 5-8.
```

Say plainly in the result file and the [RESULT] post that the cycle was aborted on the answer-key
check, and which part of the proposal tripped it, so the analyst can re-propose a continuous form.
Step 6 re-queues the item; it is never a dead end and never a stagnation tick.

**Before evaluating, verify the diff actually landed.** For an ordinary proposal, if the code edit
failed, `apply_patch` or `patch -p1` printed `FAILED` / `Hunk #N FAILED`, or the resulting `algo.py`
is byte-identical to `champion/algo.py`, the proposal was NOT tested — evaluation would just
re-measure the baseline. Set `item["diff_applied"] = False`, skip evaluation, and post
`[RESULT] {exp_id}: FAILED` so the proposal can be re-queued with a fresh diff. The deliberate
`baseline_shared` run is the sole exception: its purpose is to evaluate the unchanged champion, so
byte identity is required and counts as an applied experiment. A phantom KEEP from an unapplied
ordinary diff corrupts the champion lineage — never let the unchanged baseline be mistaken for
evidence about a change.

```python
import filecmp, json, os
from pathlib import Path
from datetime import datetime, timezone

ws  = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
rep = ws / "repo"

diff_applied = is_baseline or not filecmp.cmp(
    str(rep / "algo.py"),
    f"{FOCUS_ROOT}/champion/algo.py",
    shallow=False,
)
item["diff_applied"] = diff_applied
if not diff_applied:
    print(f"[STEP4] diff for {exp_id} did NOT apply — algo.py matches champion. "
          f"Marking FAILED and skipping evaluation.")
    # Write the Step 4 sentinel FIRST so result_latest.json exists and HEARTBEAT
    # Part 0 Check C can reason about this cycle at all.
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "result_latest.json").write_text(json.dumps({
        "status": "failed", "posted_to_workshop": False,
        "exp_id": exp_id, "agent": AGENT_NAME, "item": item, "queue_claimed": True,
        # The claim file this cycle actually holds. HEARTBEAT Part 5 5a2 re-verifies
        # ownership from it on resume; without it the resume falls back to attempt 0
        # (`claims/{exp_id}.md`), which is WRONG for any item that was released and
        # re-claimed at claims/{exp_id}.r<n>.md — it would read v1 as the ORIGINAL
        # holder and abandon an item this agent legitimately owns.
        "claim_path": claim_path,
        "direction": item.get("direction"),
        "score": None, "fitness": None, "is_valid": None,
        "test_status": "not_run", "test_score": None, "test_fitness": None,
        "test_is_valid": None, "test_pass": False, "test_reason": None,
        "test_stdout_path": None, "test_mean_rel_energy": None,
        "algo_path": str(rep / f"algo_{exp_id}.py"),
        "remote_cand": None, "stdout_path": None, "stderr_path": None,
        "pid": os.getpid(), "description": description,
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2, default=str))
    score = None
    # Then continue to **Step 4c** — NOT straight to Step 5. Step 4c is None-safe
    # (our_metric=None, cand_valid=False, provisional_keep=False, so no test eval is
    # spent) and it is the ONLY place that binds `metric_name`, `race_condition`,
    # `current_best`, `champ_test`, `our_test`, `test_valid` — every variable Steps
    # 5/6/7/8 read. Skipping 4c NameErrors the whole FAILED path.
```

**Single-variable audit artifact — REQUIRED before evaluating.** "Apply ONE change" is only real if
it is auditable. Compute the champion→candidate diff, write it to your workspace, and carry it into
the result file and the [RESULT] post so the monitor and analysts can check the claim.

```python
import difflib
from pathlib import Path

# RE-ASSERT the frozen identity BEFORE rendering. The diff header stamps
# `candidate/{exp_id}/algo.py` into an artifact that Steps 5, 7 and 8 all republish — a bled
# id here ends up in the result file and in the champion's provenance post, naming an
# experiment this agent never ran.
if not identity_intact("diff render"):
    item["identity_bleed"] = True
    cycle_aborted = "identity_bleed"
    score = None
    # Render NOTHING and evaluate nothing: fall through to the identity/claim ABORT EXIT
    # below, which writes the sentinel, posts the [RESULT] and ends the cycle.
else:
    champ_lines = Path(f"{FOCUS_ROOT}/champion/algo.py").read_text().splitlines(keepends=True)
    cand_lines  = (rep / "algo.py").read_text().splitlines(keepends=True)
    diff_lines  = list(difflib.unified_diff(champ_lines, cand_lines,
                                            fromfile="champion/algo.py",
                                            tofile=f"candidate/{exp_id}/algo.py"))
    diff_text   = "".join(diff_lines)
    diff_path   = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / f"diff_{exp_id}.patch"
    diff_path.write_text(diff_text)

    hunk_headers = [l.rstrip() for l in diff_lines if l.startswith("@@")]
    n_hunks = len(hunk_headers)
    item["diff_path"]  = str(diff_path)
    item["diff_hunks"] = n_hunks
    print(f"[STEP4] {exp_id}: {n_hunks} hunk(s) -> {diff_path}")
```

Do NOT shell out to `git` for this — `difflib` on the two file contents is the contract.

**Hard rule — one mechanism per cycle.** Read your own diff before evaluating. If it implements more
than one mechanism (two independent knobs, a knob plus a structural rewrite, a "while I was in there"
cleanup), abort the cycle: set `item["scope_abort"] = True` and `cycle_aborted = "scope_abort"`,
write the Step 4 sentinel exactly as the
unapplied-diff path above does (so `result_latest.json` exists), set `score = None`, do NOT evaluate,
and continue to **Step 4c** — Step 5 then records `outcome = "FAILED"` and Step 6 returns the item to
`pending:`. Do not jump to Step 5 directly: the decision variables live in 4c. Say in the [RESULT]
post that the cycle was aborted for scope. An unattributable KEEP is worse than a lost cycle: nobody can
tell which half caused the win, and the champion carries an untested change forever. Multiple hunks
are fine when they are the same mechanism (e.g. one constant used in three places); multiple
mechanisms are not.

**Scope discipline.** Deliver exactly the queued change, at the scope it was queued. Do not bundle a
second change, even an obviously good one. If you think the queued item is wrong or under-specified,
say so in the result file and in the [RESULT] post — then run it as queued anyway.

**Report the diff.** Include a `## Diff` section in the result file and the diff in the [RESULT]
post. If the diff exceeds ~80 lines, include the hunk headers plus the hunk count instead of the full
body, and note the path to `diff_{exp_id}.patch`.

**Eval-head connection parameters** (the remote sella checkout serving the worker pool):

```python
EVAL_HOST           = "cpu-33"
EVAL_REDIS_HOST     = "localhost"
EVAL_REDIS_PORT     = 6385
SELLA_CHECKOUT      = "/home/tsypin/opt_problem_as_testgate_opus5"
EVAL_PYTHON         = "/home/tsypin/miniconda3/envs/gigaopt/bin/python"
EVAL_MOLECULES_DIR  = "/home/tsypin/as_testgate_molecules"
```

- `EVAL_HOST` — eval-head a002dc-0002 (local ssh alias `cpu-33`): scp the candidate here, ssh in to run the eval.
- `EVAL_REDIS_HOST` / `EVAL_REDIS_PORT` — Redis as seen from the eval head; 192 workers (48 each on
  a002dc-0004/0005/0006/0007) tunnel in.
- `SELLA_CHECKOUT` — dedicated per-run checkout; `eval_candidate.py` is at its root.
- `EVAL_MOLECULES_DIR` — **`--molecules-dir` is MANDATORY on every eval command.** The client bakes
  ABSOLUTE xyz paths into every Redis task and the worker opens that path on ITS OWN filesystem. The
  worker hosts do not have `SELLA_CHECKOUT`, so omitting the flag fails 250/250 molecules in ~2.6s
  with `No such file or directory: .../molecules/xyz/<mol>_mm.xyz`. `EVAL_MOLECULES_DIR` exists on all
  four worker hosts and its train/test metadata SHAs match the pool (9ec64608 / 8a1b4708, 250
  molecules each).

**This is SHARED infrastructure.** Other clients use the same Redis and the same worker pool: never
flush Redis, never restart or kill workers, and never touch `/home/tsypin/opt_problem_optbench`. Your
only remote writes are your own `/tmp/cand_*.py` candidate files.

**Identity check — REQUIRED, the last thing before the `scp`.** Cheap insurance immediately before
the expensive operation. It answers two questions, and the cycle dies on either.

1. *Is this still MY experiment?* `exp_id` and `item` have travelled through every step above, and
   cross-agent state bleed is real: in tg_smoke_01 one agent's `axis` landed inside another's result
   file, and the champion's provenance post carried a third experiment's `candidate/{exp_id}/algo.py`
   diff header — `exp_id` had been rebound to another agent's experiment mid-session. Assert that
   **both** `exp_id` and `item["id"]` still equal `CLAIMED_EXP_ID`, the constant frozen at claim time
   in Step 3 — that is what `identity_intact()` does. Never assert `item["id"] == exp_id`: both sides
   come from the same dict, so it can only ever be true. Then re-read the queue item to confirm the
   row is still there under that id.
2. *Do I still HOLD the claim?* Re-read `claims/{exp_id}.md`'s history and assert that **version 1**
   names us. This is authoritative, not advisory: version 1 is immutable, so if it does not name us
   we never held this item, whatever happened in Step 3.

If either fires, the cycle **ENDS HERE** — no `scp`, no eval, and **no Step 5**. An aborting agent
must not write results it does not own: it posts nothing to `results/`, releases nothing it never
held, and records the FAILED outcome only in its own workspace sentinel and in the `[RESULT]` post.
Nothing is lost by walking away: the item never left `pending:`, and the monitor's stale-claim sweep
marks the claim file `released: true` so the next agent can reclaim it at the successor path.

The shared baseline has no claim file and no queue row (Step 3), so only check (2) applies to it.

```python
import sys

# Look the claim and the queue row up under the FROZEN id, never under a possibly-bled
# `exp_id`: the question is whether we still hold what we claimed.
_holder_now = None if is_baseline else claim_holder(CLAIMED_EXP_ID)
_q_now      = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                           headers=HEADERS).json()
_row_now    = next((it for it in (parse_frontmatter(_q_now).get("pending") or [])
                    if it.get("id") == CLAIMED_EXP_ID), None)

# `cycle_aborted` may already carry `identity_bleed` from the diff-render re-assert above —
# honour it rather than re-testing. `identity_intact` compares against CLAIMED_EXP_ID.
if cycle_aborted == "identity_bleed" or not identity_intact("pre-scp / eval launch"):
    print(f"[STEP4] IDENTITY BLEED before the eval launch — aborting the cycle.")
    item["identity_bleed"] = True
    cycle_aborted = "identity_bleed"
    score = None
elif not is_baseline and _holder_now != AGENT_NAME:
    print(f"[STEP4] CLAIM LOST: claims/{exp_id}.md v1 names {_holder_now} != {AGENT_NAME} — "
          f"another agent holds this item and is evaluating it. Aborting BEFORE spending "
          f"pool time.")
    item["claim_lost"] = True
    cycle_aborted = "claim_lost"
    score = None
elif not is_baseline and _row_now is None:
    print(f"[STEP4] CLAIM LOST: {CLAIMED_EXP_ID} is no longer a pending row — it was completed "
          f"while we held the claim. Aborting BEFORE spending pool time.")
    item["claim_lost"] = True
    cycle_aborted = "claim_lost"
    score = None

if cycle_aborted in ("identity_bleed", "claim_lost"):
    # ABORT EXIT — self-contained, and it does NOT fall through to Step 4c/5/6/7.
    # Same sentinel shape as the unapplied-diff path, so HEARTBEAT Part 0 Check C can
    # reason about this cycle.
    import json, os
    from datetime import datetime, timezone
    ws.mkdir(parents=True, exist_ok=True)
    rl = ws / "result_latest.json"
    rl.write_text(json.dumps({
        "status": "failed", "posted_to_workshop": False,
        # CLAIMED_EXP_ID, not `exp_id`: on the bleed path `exp_id` is precisely the value we
        # no longer trust, and a resume that reads a bled id would go looking for another
        # agent's claim file.
        "exp_id": CLAIMED_EXP_ID, "agent": AGENT_NAME, "item": item, "queue_claimed": True,
        # The claim file this cycle actually holds. HEARTBEAT Part 5 5a2 re-verifies
        # ownership from it on resume; without it the resume falls back to attempt 0
        # (`claims/{exp_id}.md`), which is WRONG for any item that was released and
        # re-claimed at claims/{exp_id}.r<n>.md — it would read v1 as the ORIGINAL
        # holder and abandon an item this agent legitimately owns.
        "claim_path": claim_path,
        "direction": item.get("direction"), "outcome": "FAILED",
        "score": None, "fitness": None, "is_valid": None,
        "test_status": "not_run", "test_score": None, "test_fitness": None,
        "test_is_valid": None, "test_pass": False, "test_reason": None,
        "test_stdout_path": None, "test_mean_rel_energy": None,
        # CLAIMED_EXP_ID here too, for the same reason as `exp_id` above: every id in this
        # sentinel must name the experiment we actually claimed, or a resume follows a path
        # built out of the one value this block exists to distrust.
        "algo_path": str(rep / f"algo_{CLAIMED_EXP_ID}.py"),
        "remote_cand": None, "stdout_path": None, "stderr_path": None,
        "pid": os.getpid(), "description": description,
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2, default=str))

    # The [RESULT] post is the ONLY shared-state write this path may make. No results/ file
    # (it belongs to the agent that owns the experiment), no queue write, and no claim-file
    # DELETE — deleting a claim we do not hold would strip the real holder's exclusion.
    _r = requests.post(f"{API}/posts", headers=HEADERS, json={
        "workshop": WORKSHOP,
        # Titled by the id we CLAIMED — the bled one names an experiment that is not ours.
        "title": f"[RESULT] {CLAIMED_EXP_ID}: FAILED ({cycle_aborted})",
        "content": (
            f"## Experiment\n{description}\n\n"
            f"## Result\nAborted before any pool time was spent: **{cycle_aborted}**.\n"
            + (f"`exp_id` / `item.id` no longer match the claimed `{CLAIMED_EXP_ID}` — "
               "cross-agent state bleed, so nothing here can be trusted as this "
               "experiment's.\n"
               if cycle_aborted == "identity_bleed" else
               f"`claims/{CLAIMED_EXP_ID}.md` version 1 names another agent, so this "
               f"experiment is already being evaluated by its holder.\n")
            + "Nothing was evaluated and NOTHING was written to `results/`, the queue, "
              "`dead_ends.md` or `non_generalizable.md`. The item is still in `pending:` and "
              "is not re-queued: it is either already held or already recorded elsewhere.\n\n"
            f"Outcome: FAILED\n\n## Team\n{MY_TEAM}"),
        "notify_agents": team_members,
        "tags": [f"team:{MY_TEAM}", "type:result", "outcome:FAILED"],
    })
    _rl = json.loads(rl.read_text())
    _rl.update({"status": "posted", "posted_to_workshop": True,
                "result_post_id": (_r.json().get("id") if _r.ok else None),
                "posted_at": datetime.now(timezone.utc).isoformat()})
    rl.write_text(json.dumps(_rl, indent=2, default=str))
    sys.exit(0)     # END OF CYCLE — do NOT continue to Step 4c or Step 5.
```

**Run the deterministic CPU eval.** `scp` the candidate `algo.py` to a UNIQUE remote path
(`/tmp/cand_${AGENT}_${exp}.py` — unique per agent+experiment so concurrent evals never collide),
then `ssh` into the eval head and run `eval_candidate.py`. This is SYNCHRONOUS — `subprocess.run`
blocks until the eval JSON is returned. NEVER use `subprocess.Popen` without an immediately-following
`proc.wait()`; NEVER use `nohup ... &`; NEVER exit the agent session while the eval is running.

```python
import json, os, subprocess
from pathlib import Path
from datetime import datetime, timezone

ws  = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
rep = ws / "repo"
out, err = ws / f"eval_{exp_id}.stdout", ws / f"eval_{exp_id}.stderr"

remote_cand = f"/tmp/cand_{AGENT_NAME}_{exp_id}.py"   # UNIQUE per agent+experiment

sentinel = {
    # `status` ALWAYS refers to the TRAIN eval only; the held-out split has its own
    # `test_status`. `direction` is the EXPERIMENT axis direction from the queue item
    # (increase/decrease/none) — never the champion's minimize/maximize direction.
    # `test_mean_rel_energy` is part of the sentinel schema: it is the held-out
    # under-relaxation diagnostic, declared here and filled in by Step 4c / Step 7c.
    "status": "running", "posted_to_workshop": False,
    "exp_id": exp_id, "agent": AGENT_NAME, "item": item, "queue_claimed": True,
        # The claim file this cycle actually holds. HEARTBEAT Part 5 5a2 re-verifies
        # ownership from it on resume; without it the resume falls back to attempt 0
        # (`claims/{exp_id}.md`), which is WRONG for any item that was released and
        # re-claimed at claims/{exp_id}.r<n>.md — it would read v1 as the ORIGINAL
        # holder and abandon an item this agent legitimately owns.
        "claim_path": claim_path,
    "direction": item.get("direction"), "score": None, "fitness": None, "is_valid": None,
    "test_status": "not_run", "test_score": None, "test_fitness": None,
    "test_is_valid": None, "test_pass": False, "test_reason": None,
    "test_stdout_path": None, "test_mean_rel_energy": None,
    "algo_path":   str(rep / f"algo_{exp_id}.py"),
    "remote_cand": remote_cand,
    "stdout_path": str(out), "stderr_path": str(err),
    # Record OUR pid so HEARTBEAT Part 0 Check C can tell whether we died
    # ungracefully (rate limit, SIGKILL) vs. legitimately still evaluating.
    # `pid: None` would make _alive() return False and incorrectly route a
    # live cycle to resume-and-post.
    "pid": os.getpid(), "monitor_id": None, "description": description,
    "launched_at": datetime.now(timezone.utc).isoformat(),
}
(ws / "result_latest.json").write_text(json.dumps(sentinel, indent=2, default=str))

# 1. Copy the candidate algo.py to a unique path on the eval head.
subprocess.run(["scp", str(rep / "algo.py"), f"{EVAL_HOST}:{remote_cand}"],
               check=True, timeout=120)

# 2. Run the deterministic eval on the eval head; it prints the score dict as
#    a single JSON line on stdout (last line). BLOCK until it returns.
eval_cmd = (
    f"cd {SELLA_CHECKOUT} && JAX_ENABLE_X64=1 {EVAL_PYTHON} eval_candidate.py "
    f"--program {remote_cand} --split train "
    f"--molecules-dir {EVAL_MOLECULES_DIR} "
    f"--redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}"
)
result = subprocess.run(
    ["ssh", EVAL_HOST, eval_cmd],
    capture_output=True, text=True,
    timeout=3600,   # the driver blocks per-molecule up to the worker timeout; size generously
)
out.write_text(result.stdout)
err.write_text(result.stderr)

# 3. Parse the LAST stdout line as the score JSON.
score = None
for line in reversed(result.stdout.strip().splitlines()):
    line = line.strip()
    if line.startswith("{"):
        try:
            score = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

eval_succeeded = (result.returncode == 0) and (score is not None)
sentinel["status"] = "complete" if eval_succeeded else "failed"
sentinel["returncode"] = result.returncode
sentinel["score"] = score
sentinel["fitness"]  = (score or {}).get("fitness")
sentinel["is_valid"] = (score or {}).get("is_valid")
(ws / "result_latest.json").write_text(json.dumps(sentinel, indent=2, default=str))
# Now continue to Step 4b → Step 5 in this same session — do NOT exit until
# the result is posted. If score is None, treat as a FAILED eval (infra error).
```

The score dict is the authoritative result. Its keys (emitted by `eval_candidate.py`):

| Key | Meaning |
|---|---|
| `fitness` | **Primary, lower is better.** Always `= mean_rel_steps` — the honest measured speed, reported **even when the energy gate fails**. It equals `1000.0` ONLY for runs that produced no usable trajectory: harness errors, a molecule that stopped before convergence, or one that blew the force-call budget. A low `fitness` is therefore **not** evidence of a win by itself — always read it together with `is_valid`. |
| `is_valid` | `1` iff no errors, every molecule converged within budget, AND `mean_rel_energy >= 1.0`; else `0`. **This — not the fitness value — is the validity gate.** |
| `mean_rel_steps` | Mean relative force-call count vs reference. |
| `mean_rel_energy` | **Validity gate: valid only if `>= 1.0`** (relax at least as deep as reference on average). |
| `max_final_energy_delta_kcal_mol` | Worst per-molecule final-energy gap; diagnostic only — does NOT gate validity. |
| `converged` | Fraction of molecules whose convergence check passed. |
| `invalid_reason` | Human string when invalid; empty when valid. |
| `per_molecule` | **Per-molecule breakdown, present on EVERY eval that produced any result — including invalid ones.** `n_molecules`; `worst_by_rel_steps` (the 8 molecules eating the most budget, each with `mol`, `rel_steps`, `n_steps`, `energy_delta_kcal_mol`, `converged`); `nearest_energy_gate` (the 5 most under-relaxed — the validity risk); `non_converged` (names of molecules that never converged). Absent only on a total harness failure. |
| `duration_s`, `num_results`, `num_errors`, `lower_is_better` | Diagnostics. |

This score is the **train** split only (we pass `--split train` explicitly). A better
train `fitness` with `is_valid == 1` is necessary but **not sufficient** for promotion — the held-out
test gate in Step 4c decides. `is_valid == 0` means rejected outright — **no matter how good `fitness`
looks**. An invalid run can now report a very attractive `fitness` (that is the point: it tells you the
mechanism's real speed), but it is still a rejected run, never a candidate for promotion.

After the eval, save a stamped copy of the candidate to **agent-local paths** (never `task/` or
`champion/`):

```python
import shutil
from pathlib import Path

agent_workspace = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo")

# Save a stamped copy of the candidate algo.py for this experiment
agent_algo = agent_workspace / f"algo_{exp_id}.py"
shutil.copy(agent_workspace / "algo.py", agent_algo)

print(f"[ISOLATION] saved candidate → {agent_algo}")
```

**Stamped files belong here in agent-local paths.** The shared `champion/algo.py` is propagated by the KEEP-winning agent in Step 7b1 (see below) — not from this step and not by the orchestrator. The stamped copy must exist before Step 7b1 can copy it.

### Step 4b — Analyze Eval Diagnostics — REQUIRED

After the eval returns, read the diagnostic fields in the score dict before recording the result.
This takes a few seconds and explains WHY a candidate passed or failed, not just its `fitness`.

Check these from the score dict:

1. **Validity first.** If `is_valid == 0`, the candidate is rejected regardless of `fitness`. Read
   `invalid_reason` and `mean_rel_energy`: a value `< 1.0` means the optimizer recovered less energy
   than the reference on average — it is **under-relaxing** (stopping too early / converging to a worse
   minimum). `num_errors > 0` with reasons like `"exceeded max force-call budget"` means the optimizer
   blew past `max_steps` on at least one molecule. Note which failure mode in the result file. The fix
   is always a better *trajectory* (genuinely relax further in fewer calls) — **never** loosening,
   targeting, or working around the convergence test, which is fixed and external.

   **`per_molecule` is present on every eval that produced any result, invalid ones included — use it,
   especially when the run failed.** A `fitness` of `1000.0` says only "no usable trajectory"; the
   aggregate cannot tell you whether a change broke everything or broke three molecules. `per_molecule`
   can:
   - `non_converged` names the molecules that never converged. If it is a handful, the mechanism is not
     dead — it is unguarded for those cases, and the next experiment is the same mechanism with the
     condition that excludes them (stated on *physical* grounds, never by molecule identity — a branch
     that recognises which benchmark molecule it is looking at is forbidden; see TASK.md).
   - `nearest_energy_gate` names the most under-relaxed molecules — where an energy-gate failure is
     actually coming from, rather than the mean that hides it.
   - `worst_by_rel_steps` names where the step budget is actually going, which is where a speedup has
     room to exist at all.

   Report the relevant entries in the result file. A structural change that fails on 3 of 250 molecules
   and one that fails on 250 are completely different results, and only this field distinguishes them —
   without it, every bold experiment returns the same uninformative sentinel and the search is pushed
   toward timid parameter tweaks that always return a number.

   **Read the `fitness` of an invalid run — it is real information, not noise.** An energy-invalid run
   still reports its true `mean_rel_steps`, so the pair `(fitness, mean_rel_energy)` separates two very
   different failures that used to look identical:
   - **Fast but under-relaxing** (`fitness` well below the champion, `mean_rel_energy` just under `1.0`):
     the mechanism is genuinely quick and the *only* defect is the energy leak. This is the most valuable
     negative result you can get — report the size of both gaps so the idea can be retried with the leak
     fixed, and say so explicitly in the result file. Do **not** file it as a dead mechanism.
   - **Slow AND under-relaxing** (`fitness` at or above the champion, `mean_rel_energy` below `1.0`):
     the mechanism is losing on both axes. That is a genuine dead end.

   This distinction only exists because invalid runs carry honest fitness; use it. It never changes the
   verdict — an invalid run is still a rejection — it changes what you write down about *why*.

2. **Energy margin.** Even when valid, record `mean_rel_energy`. A value at or just above `1.0` means
   the candidate is near the validity boundary — further step reductions risk tipping it invalid by
   under-relaxing. A comfortably-high value means the geometry is solidly relaxed. (Do not engineer a
   result to sit *just* above 1.0 — that is gate-margin gaming; see "What counts as cheating" in TASK.md.)

3. **Speed and coverage.** Record `mean_rel_steps` (always `== fitness` for any run that produced a
   trajectory, valid or not) and `converged` (fraction
   of molecules whose convergence check passed). A low `mean_rel_steps` with `converged == 1.0` and
   low energy delta is the ideal profile.

Include these diagnostics in every result file under an `## Eval Diagnostics` section. Analysts use
this to understand WHY a KEEP worked (or why a fast candidate was invalid), not just that it did.

### Step 4c — Held-out TEST Gate — REQUIRED before any KEEP

Everything above measured the **train** split (250 molecules — the explicit `--split train` eval).
Train-only promotion overfits it: a prior champion in this project was **-5% on
train and INVALID on held-out test**. A change that only helps the molecules it was tuned on is not
an improvement to the optimizer, so promotion now requires the held-out split too.

**A candidate is promoted ONLY when all three hold:**

- **(a) train improvement** — valid train run AND `current_best - our_metric >= KEEP_MARGIN`
- **(b) test improvement** — `champ_test - our_test > TEST_MARGIN`
- **(c) test still valid** — the test run has `is_valid == 1` (`mean_rel_energy >= 1.0` out of sample)

**Run the test eval ONLY on a provisional train keep.** The pool is shared with other clients and a
test eval is real compute — never spend one on a train DISCARD or FAILED.

**Re-read champion.md here (once).** Step 5 reuses `fresh_champ` / `fresh_version` / `race_condition`
from this block; do not re-read it again there.

```python
# ── Re-read champion (may have changed during our eval) — SINGLE read for 4c + 5 ──
fresh_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
fresh_champ = parse_frontmatter(fresh_raw)
metric_name = fresh_champ.get("metric_name", "fitness")  # task defines this in champion.md
# NOTE: this is the CHAMPION's optimisation direction ("minimize" for this task). It is a
# different thing from the EXPERIMENT's axis direction (increase/decrease/none) that lives in
# `item["direction"]` — never let one name hold both, or the result file and the ledger record
# "minimize" where the axis direction belongs.
champ_direction = fresh_champ.get("direction", "minimize")
fresh_version = fresh_raw.get("version", 0)

race_condition = (fresh_version != champ_version)
if race_condition:
    print(f"Champion changed during eval (v{champ_version} → v{fresh_version})")

# Pull this candidate's TRAIN metrics from the deterministic eval score dict.
our_metric = float(score["fitness"]) if score is not None else None   # TRAIN fitness
cand_valid = (score is not None) and (int((score or {}).get("is_valid", 0)) == 1)

# Is there a VALID champion yet? The baseline algo.py may itself be INVALID
# under the energy gate, so champion.md may have no valid fitness recorded.
# `status: awaiting_baseline` or a missing/None metric_value ⇒ no valid champion.
champ_status   = fresh_champ.get("status")
champ_fitness  = fresh_champ.get("metric_value")       # TRAIN anchor
champ_test     = fresh_champ.get("test_metric_value")  # TEST anchor (may be None on old files)
# Only an explicit `awaiting_baseline` status means "no champion yet". A normal champion.md has
# NO status field (champ_status is None) — the champ_fitness check below is what gates emptiness.
# (Treating None status as no-champion seeds ANY valid candidate over a real champion — a bug.)
#
# The `is_valid` term is REQUIRED, not belt-and-braces. `fitness < 1000.0` alone no longer implies
# validity: a run that failed only the energy gate now records its honest (often very good) fitness.
# A champion.md seeded from an energy-invalid baseline would therefore carry something like 1.0 with
# `is_valid: 0`, and without this term every agent would anchor to an invalid champion.
# Absent `is_valid` ⇒ treat as valid: champion.md is only ever written on an accepted KEEP, which
# already required validity, so older files that predate the field are trustworthy.
_champ_valid_raw = fresh_champ.get("is_valid", 1)
champ_is_valid = (int(_champ_valid_raw) == 1) if _champ_valid_raw is not None else False
have_valid_champion = (champ_status != "awaiting_baseline") and \
                      (champ_fitness is not None) and (float(champ_fitness) < 1000.0) and \
                      champ_is_valid
current_best = float(champ_fitness) if have_valid_champion else None

# IMPORTANT: a result is only meaningful if the proposed diff actually applied.
# If Step 4's edit failed (edit context not found, patch rejected, algo.py identical
# to champion), the score you measured is the UNCHANGED baseline — NOT evidence
# about the proposal.
diff_applied = bool(item.get("diff_applied", True))  # default True for legacy items

# ── Margins (used here and in Step 5; defined once) ──────────────────────────
# Eval is deterministic, but we still require a real margin to promote: sub-margin
# "wins" are noise-level and just make the champion crawl. KEEP_MARGIN is in
# mean_rel_steps units (1e-4 ≈ 0.01% fewer relative force calls). Matches TEST_MARGIN and the
# upstream opt_problem contract's `significant_change`. It was 1e-3 (an AutoScientists-only
# refinement); that was 10x stricter than the contract and could silently discard real improvements
# without spending a test eval on them.
KEEP_MARGIN = 1e-4
# TEST_MARGIN matches the upstream opt_problem contract's `significant_change`.
TEST_MARGIN = 1e-4

# Provisional train keep: the ONLY condition under which we spend a test eval.
provisional_keep = (
    diff_applied and cand_valid and
    ((not have_valid_champion) or ((current_best - our_metric) >= KEEP_MARGIN))
)
```

**Run the held-out eval on the SAME frozen candidate.** Same `remote_cand` path, same
`eval_candidate.py`, same `--molecules-dir`, same timeout — the only difference is `--split test`.
Never re-edit `algo.py` between the train and test runs; the two numbers must describe the same code.

This block is **self-contained**: it re-imports and re-derives `ws` rather than relying on Step 4's
block, because on the FAILED paths that reach here (unapplied diff / scope abort / answer-key reject /
stack conflict) Step 4's eval block never ran and those names are unbound. The `identity_bleed` and
`claim_lost` aborts never reach this step — they end the cycle inside Step 4.

```python
import json, subprocess
from pathlib import Path

ws = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")

test_score = test_valid = our_test = None
test_eval_failed = False
test_stdout_path = None

if provisional_keep:
    tout = ws / f"eval_{exp_id}_test.stdout"
    terr = ws / f"eval_{exp_id}_test.stderr"
    test_stdout_path = str(tout)

    # Twin of eval_cmd, identical except `--split test`. --molecules-dir is MANDATORY here too.
    test_cmd = (
        f"cd {SELLA_CHECKOUT} && JAX_ENABLE_X64=1 {EVAL_PYTHON} eval_candidate.py "
        f"--program {remote_cand} --split test "
        f"--molecules-dir {EVAL_MOLECULES_DIR} "
        f"--redis-host {EVAL_REDIS_HOST} --redis-port {EVAL_REDIS_PORT}"
    )
    tres = subprocess.run(
        ["ssh", EVAL_HOST, test_cmd],
        capture_output=True, text=True,
        timeout=3600,
    )
    tout.write_text(tres.stdout)
    terr.write_text(tres.stderr)

    for line in reversed(tres.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                test_score = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    # INFRA failure ≠ scientific rejection. A harness/Redis error tells us nothing
    # about the candidate, so it must NOT be filed as a rejection (see Step 5 / 7c).
    test_eval_failed = (
        tres.returncode != 0 or test_score is None
        or bool((test_score or {}).get("error"))
        or int((test_score or {}).get("num_results", 0) or 0) == 0
    )

    if not test_eval_failed:
        our_test   = float(test_score["fitness"])
        test_valid = (int((test_score or {}).get("is_valid", 0)) == 1)

# ── Test gate ────────────────────────────────────────────────────────────────
# Seeding (no valid champion yet) sets the anchor instead of comparing — but the
# test run must still be VALID: a seed that is invalid out of sample is not a champion.
test_pass = bool(
    provisional_keep and not test_eval_failed and test_valid
    and (champ_test is None or (float(champ_test) - our_test) > TEST_MARGIN)
)

if provisional_keep and not test_eval_failed and not test_pass:
    bad_valid  = not test_valid
    bad_margin = (champ_test is not None) and ((float(champ_test) - our_test) <= TEST_MARGIN)
    test_reason = ("both" if (bad_valid and bad_margin)
                   else "test-invalid" if bad_valid
                   else "insufficient-test-improvement")
else:
    test_reason = None

# Single sentinel key for the held-out split's lifecycle. `status` stays TRAIN-only.
test_status = ("not_run"  if not provisional_keep
               else "failed" if test_eval_failed
               else "complete")

# Merge into the Step 4 sentinel (absent if the diff never applied and we skipped the eval).
rl = ws / "result_latest.json"
sentinel = json.loads(rl.read_text()) if rl.exists() else {}
sentinel.update({
    "test_status": test_status,     # not_run | running | complete | failed
    "test_score": test_score,
    "test_fitness": our_test,
    "test_is_valid": (int(test_valid) if test_valid is not None else None),
    "test_mean_rel_energy": (test_score or {}).get("mean_rel_energy"),
    "test_pass": test_pass,
    "test_reason": test_reason,
    "test_stdout_path": test_stdout_path,
})
rl.write_text(json.dumps(sentinel, indent=2, default=str))
```

Record `test_fitness`, `test_is_valid`, `test_mean_rel_energy`, and the pass/fail reason in the
result file's `## Eval Diagnostics` section alongside the train diagnostics. When the test eval did
not run, say so explicitly (`test: not run — train DISCARD/FAILED`) rather than leaving the fields blank.

### Step 5 — Record Result

`fresh_champ`, `race_condition`, `our_metric`, `cand_valid`, `have_valid_champion`, `current_best`,
`diff_applied`, `KEEP_MARGIN`, `TEST_MARGIN`, `test_pass`, `test_status`, `test_reason`,
`test_valid`, `our_test`, `champ_test`, `metric_name`, `champ_direction` and `test_eval_failed` all
come from Step 4c — do NOT re-read champion.md or redefine them here. Every path into Step 5,
including the FAILED paths, goes **through Step 4c**, which is what binds them.
`cycle_aborted`, `REPORT_EXP_ID`, `diff_text`, `diff_lines`, `hunk_headers`, `n_hunks` and
`diff_path` come from Step 4's defaults block, which runs before either abort route can fire.

```python
# ── Outcome decision (deterministic; train gate + held-out test gate) ────────
#   * diff never applied                                 → FAILED
#   * train eval was an INFRA failure                     → FAILED (not science)
#   * invalid train candidate                             → DISCARD
#   * provisional keep + test eval INFRA failure          → FAILED (not science)
#   * provisional keep + test gate passed                 → KEEP
#   * provisional keep + test gate failed                 → REJECTED_TEST
#   * anything else (valid but sub-margin on train)       → DISCARD
#
# A KEEP decided here can still become **NEAR_MISS** in Step 7b / 7b1 — both gates
# passed but another agent's champion landed first, so this candidate never got
# promoted. NEAR_MISS is the ONLY outcome that is assigned after Step 5; it is
# scheduling, not science, and it never becomes DISCARD or REJECTED_TEST.
#
# INFRA failures must never be filed as science. eval_candidate.py turns ANY
# harness/Redis exception into {"fitness": 1000.0, "is_valid": 0, "error": ...}.
# `fitness == 1000.0` now has a NARROWER meaning than it used to: an energy-gate
# failure reports honest fitness, so a 1000.0 means only one of infra error /
# non-convergence / over-budget. That is a useful hint but NOT a decision — the
# last two are real science and belong in DISCARD. Keep checking `error` /
# `num_results` to tell them apart; filing an infra error as DISCARD would burn a
# mechanism family in dead_ends.md that was never actually tested.
train_infra_failed = (
    score is None
    or bool((score or {}).get("error"))
    or int((score or {}).get("num_results", 0) or 0) == 0
)

if not diff_applied:
    outcome = "FAILED"
elif train_infra_failed:
    outcome = "FAILED"            # harness/Redis error — re-queue, do NOT record as a dead end
elif not cand_valid:
    outcome = "DISCARD"           # invalid candidates are worthless: correctness first
elif provisional_keep and test_eval_failed:
    outcome = "FAILED"            # held-out eval never produced a number — re-queue
elif provisional_keep and test_pass:
    outcome = "KEEP"              # (a) train margin + (b) test margin + (c) test valid
elif provisional_keep:
    outcome = "REJECTED_TEST"     # improved on train, does not generalize
else:
    outcome = "DISCARD"

# Signed improvement vs the current champion (negative == better for this
# minimize task). Used in the [RESULT] post, champion.md, dead_ends, and SOURCE.
# No valid champion to compare against ⇒ delta is undefined (0.0 placeholder).
delta = (our_metric - current_best) if (our_metric is not None and current_best is not None) else 0.0
test_delta = (our_test - float(champ_test)) if (our_test is not None and champ_test is not None) else 0.0
```

**Seeding** (no valid champion yet) still promotes the first candidate, but it must be valid on
**both** splits: `provisional_keep` is satisfied without a train comparison, and `test_pass` requires
`test_valid` even when `champ_test is None`. Seeding sets both anchors (Step 7b).

**Result-file length:** findings first, a few sentences per section. Report the numbers, the failure
mode, and what it implies for the next experiment. No restatement of the protocol, no narration of
what you did in each step.

**The frontmatter below is not optional.** The ledger, the analysts' empirical priors, and the
operator's monitoring all read `results/{exp_id}.md` by parsing exactly these keys — a result file
without them is invisible to every consumer. `direction` here is the **EXPERIMENT axis direction**
from the queue item (`increase` / `decrease` / `none`), never the champion's `minimize`.

**Render every frontmatter field through `yaml_scalar` — never interpolate a Python value raw.**
YAML has no `None` literal, so `f"value: {None}"` writes the four-character STRING `None`, and the
ledger dutifully stores `"value": "None"` (type `str`) where a consumer testing `is None` sees a
truthy string. It happened to three experiments in tg_smoke_01. Queue `value`s are also YAML-hostile
unquoted — they carry commas, colons and braces — so strings are JSON-quoted, not passed through.
This applies to **every** interpolated field, in this file's frontmatter and in champion.md's
(Step 7b) alike.

**A multi-line field is never written raw — fence it or quote it.** `diff` is the dangerous one: a
unified diff's own header lines start with `---`, and an unfenced `---` at the start of a line is
indistinguishable from a frontmatter delimiter to every client-side parser that splits on it, so the
block terminates early and the file's metadata is truncated or mis-sliced. `md_field` below fences
any multi-line or `---`-leading value; the round-trip assertion after the template is what proves it
worked.

```python
import json
FENCE = "`" * 3   # built at runtime so the markdown fences below stay intact

def yaml_scalar(v):
    """Python value → a YAML scalar. `null` for None, JSON-quoted for str, plain for numbers."""
    return "null" if v is None else json.dumps(v) if isinstance(v, str) else str(v)

def md_field(v):
    """A queue field rendered into the BODY. Multi-line values — and anything starting with
    `---`, i.e. every unified diff — go inside a fence so they cannot be read as a
    frontmatter delimiter."""
    s = "" if v is None else str(v)
    return (f"{FENCE}text\n{s}\n{FENCE}"
            if ("\n" in s or s.lstrip().startswith("---")) else s)

exp_axis      = item.get("axis") or "UNKNOWN"
exp_direction = item.get("direction") or "UNKNOWN"   # EXPERIMENT axis direction
exp_value     = item.get("value")

_sc = score or {}
_ts = test_score or {}
_per_molecule = _sc.get("per_molecule")
_per_molecule_block = (
    f"{FENCE}json\n{json.dumps(_per_molecule, sort_keys=True, indent=2)}\n{FENCE}"
    if _per_molecule is not None
    else "(unavailable — TRAIN produced no molecule results; treat as infrastructure evidence)"
)

# Diff for audit: full body if small, else hunk headers + count (Step 4).
# `cycle_aborted` and the diff-artifact names come from Step 4's defaults block, which
# runs before any abort. Do NOT gate on `diff_applied` alone: on the answer-key route the
# item has no `diff_applied` key, so Step 4c defaults it to True while no artifact exists.
if cycle_aborted and not diff_lines:
    diff_section = ("(no diff — cycle aborted before any code was written; "
                    f"reason: {cycle_aborted})")
elif is_baseline:
    diff_section = (
        "(no diff by design — `baseline_shared` evaluates the unchanged champion)"
    )
elif not diff_applied or not diff_lines:
    diff_section = "(no diff — the change did not apply; candidate was byte-identical to champion)"
elif len(diff_lines) <= 80:
    diff_section = f"{FENCE}diff\n{diff_text}{FENCE}"
else:
    diff_section = (f"{FENCE}diff\n" + "\n".join(hunk_headers) +
                    f"\n({n_hunks} hunks, {len(diff_lines)} diff lines — full patch at {diff_path})"
                    f"\n{FENCE}")

_test_line = {"complete": "ran",
              "failed":   "eval FAILED (infra) — tells us NOTHING about the candidate",
              "not_run":  "not run — no provisional train keep (train DISCARD/FAILED)"}[test_status]

_fm_is_valid      = int(_sc.get("is_valid", 0)) if score else None
_fm_test_is_valid = int(test_valid) if test_valid is not None else None
# REQUIRED alongside is_valid. Fitness is now honest even for an energy-gate failure, so
# `is_valid: 0` on its own no longer says WHY the run was rejected. mean_rel_energy is what
# separates "fast but under-relaxing" (retry once the leak is fixed) from "slow and
# under-relaxing" (a genuine dead end) — and the ledger reads this frontmatter, so omitting
# it here makes the distinction invisible to every analyst downstream.
_fm_mean_rel_energy = _sc.get("mean_rel_energy") if score else None

result_markdown = f"""---
exp_id: {yaml_scalar(exp_id)}
agent: {yaml_scalar(AGENT_NAME)}
team: {yaml_scalar(MY_TEAM)}
axis: {yaml_scalar(exp_axis)}
direction: {yaml_scalar(exp_direction)}
value: {yaml_scalar(exp_value)}
outcome: {yaml_scalar(outcome)}
fitness: {yaml_scalar(our_metric)}
is_valid: {yaml_scalar(_fm_is_valid)}
mean_rel_energy: {yaml_scalar(_fm_mean_rel_energy)}
delta: {yaml_scalar(round(delta, 6))}
test_metric_value: {yaml_scalar(our_test)}
test_is_valid: {yaml_scalar(_fm_test_is_valid)}
post_id: null
---

# {exp_id} — {outcome}

## Hypothesis

{md_field(item.get("hypothesis") or description)}

## Change

{md_field(item.get("diff") or description)}

## Diff

{diff_section}

## Eval Diagnostics

**Train (250 molecules, `--split train`)**
- fitness: {_sc.get("fitness")} (delta vs champion {delta:+.6f}; champion train anchor {current_best})
- is_valid: {_sc.get("is_valid")} — {_sc.get("invalid_reason") or "valid"}
- mean_rel_steps: {_sc.get("mean_rel_steps")}
- mean_rel_energy: {_sc.get("mean_rel_energy")} (validity gate: >= 1.0)
- max_final_energy_delta_kcal_mol: {_sc.get("max_final_energy_delta_kcal_mol")} (diagnostic only)
- converged: {_sc.get("converged")} | num_results: {_sc.get("num_results")} | num_errors: {_sc.get("num_errors")}
- duration_s: {_sc.get("duration_s")}

### TRAIN `per_molecule`

{_per_molecule_block}

**Held-out test (250 molecules, `--split test`)** — {_test_line}
- test_fitness: {our_test} (champion test anchor {champ_test}, delta {test_delta:+.6f})
- test_is_valid: {_ts.get("is_valid")}
- test_mean_rel_energy: {_ts.get("mean_rel_energy")}
- gate: test_pass={test_pass}, reason={test_reason or "n/a"}

## Interpretation

<2-4 sentences: what these numbers mean, which failure mode (if any) fired, and what the next
experiment should try. No protocol narration.>
"""

# Round-trip check — REQUIRED. Proves no interpolated value (a `---`-leading `diff`, an
# unquoted `value`) leaked into or truncated the frontmatter block. Fix the offending field
# rather than writing a file the ledger cannot parse.
_fm_back = yaml.safe_load(result_markdown.split("---")[1]) or {}
assert _fm_back.get("exp_id") == exp_id and _fm_back.get("outcome") == outcome, \
    "result frontmatter round-trip failed — an interpolated field broke the YAML block"
```

Write to **main workspace** (visible to all teams) — but **never clobber another agent's result
file**. If `results/{exp_id}.md` already exists and its `agent:` names someone else, that agent ran
the same experiment: the claim failed to be exclusive upstream (Step 3), and overwriting each other
is how `exp_trust_partial_step_shrink` ended up at v4 in tg_smoke_01 with `agent:` from one agent and
`post_id:` from another — two ledger rows that disagree. APPEND instead, and say so in the [RESULT]
post (Step 8).

```python
# RE-ASSERT the frozen identity IMMEDIATELY before the write. This is SHARED state keyed by
# exp_id: a bled id files our numbers under another agent's experiment, and the ledger then
# carries two rows that disagree with no way to tell which is which. The numbers are already
# safe in `result_latest.json`, so refusing costs nothing that cannot be recovered by hand.
#
# ABORT, do not RAISE. A raise here skips Step 6 (the claim file stays standing — a lock, not
# litter, and the item is then unclaimable until the monitor's sweep notices) and skips Step 8's
# [RESULT] post, which is MANDATORY for every experiment: a cycle that dies silently is exactly
# the invisible work Rule 2 forbids. So this takes the SAME route as Step 4's identity/claim
# abort exit — skip the shared write, keep the numbers in the local sentinel, then release the
# claim in Step 6 and report under CLAIMED_EXP_ID in Step 8.
identity_ok = identity_intact("results/ write")
if not identity_ok:
    item["identity_bleed"] = True
    cycle_aborted = "identity_bleed"
    # The measurement cannot be attributed to anything: FAILED, and the hard gates in Steps 7b
    # and 7b1 then keep it away from champion.md and champion/algo.py. Without this an
    # identity-bled cycle that computed KEEP would promote a champion under a bled id — the
    # worst outcome available on this path.
    outcome = "FAILED"
    REPORT_EXP_ID = CLAIMED_EXP_ID
    duplicate_execution = False    # nothing was written, so there is nothing to reconcile
    print(f"[STEP5] identity bleed — NOT writing results/{exp_id}.md (this session claimed "
          f"{CLAIMED_EXP_ID}). Numbers stay in result_latest.json; releasing the claim in "
          f"Step 6 and posting [RESULT] under {CLAIMED_EXP_ID}. Nothing goes to dead_ends.md, "
          f"non_generalizable.md or champion.md.")

if identity_ok:
    _existing  = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                              headers=HEADERS).json()
    _ex_body   = _existing.get("content") or ""
    _ex_agent  = (parse_frontmatter(_existing) or {}).get("agent")

    # Bound on every path — Step 8 reads it.
    duplicate_execution = bool(_ex_body) and _ex_agent not in (None, "", AGENT_NAME)

if identity_ok and not duplicate_execution:
    requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
        headers=HEADERS, json={"content": result_markdown})
elif identity_ok:
    # Do NOT overwrite. Record BOTH executions so the defect is visible and the two
    # ledger rows can be reconciled. `DUP_POST_ID_{AGENT_NAME}` is a placeholder that
    # Step 8b replaces with our own [RESULT] post id.
    _dup = (
        f"\n\n## Duplicate execution (coordination defect)\n\n"
        f"`{exp_id}` was evaluated TWICE — the queue claim was not exclusive.\n\n"
        f"- first writer: {_ex_agent} (post_id in the frontmatter above)\n"
        f"- second writer: {AGENT_NAME} (post_id: DUP_POST_ID_{AGENT_NAME})\n"
        f"- {AGENT_NAME} measured: train {metric_name}={our_metric} "
        f"(delta {delta:+.6f}, is_valid={_sc.get('is_valid')}), "
        f"held-out test {our_test} (is_valid={_ts.get('is_valid')}), outcome {outcome}\n\n"
        f"The frontmatter above belongs to the FIRST writer and is left untouched. Two "
        f"independent evals of one experiment burned the shared pool twice.\n")
    requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
        headers={**HEADERS, "If-Match": str(_existing.get("version", 0))},
        json={"content": _ex_body + _dup})
    print(f"[STEP5] duplicate execution of {exp_id} (first writer {_ex_agent}) — appended "
          f"instead of overwriting.")
```

`post_id: null` in the frontmatter is deliberately unquoted: Step 8b backfills it by literal
replacement once the [RESULT] post exists.

### Step 6 — Release Claim; Complete or Re-Queue the Item

This step runs for **every** outcome — KEEP, DISCARD, REJECTED_TEST and FAILED alike. For an
ordinary candidate, the claim is always dropped: a silently-held claim blocks the item for the
whole run. `baseline_shared` has no queue row or claim and takes the explicit no-op branch below.
What differs for an ordinary candidate is where the item lands.

The row stayed in `pending:` for the whole cycle — the claim never moved it (Step 3) — so this is the
ordinary `pending:` → `completed:` move, not a reconstruction from a snapshot. Releasing the claim is
two writes: pop `claims[AGENT_NAME]` from queue.md (bookkeeping) and let go of the claim FILE (the
real exclusion). **Release it on the re-queue path too, not only on completion** — a FAILED item
whose claim file still names us is unclaimable forever. The two paths let go differently, and the
difference matters:

- **Completed** — the row has left `pending:` for good, so `DELETE` the claim file; nothing can be
  lost if the delete misbehaves.
- **Re-queued (FAILED)** — the row is still claimable, so write the `released: true` marker naming
  the successor path, exactly as ROLE-MONITOR's stale sweep does. Never delete here: this file's
  version 1 names us permanently, so a delete that kept the history would make the next PUT land as
  vN+1 and strand the item.

- **KEEP / DISCARD / REJECTED_TEST** — a real experiment ran and produced evidence: the record goes
  into `completed:`, carrying the measured numbers (`val_score`, `test_fitness`, `is_valid`, `delta`,
  `mean_rel_energy`). Those last three are the ledger's fallback when it cannot read
  `results/{exp_id}.md`; without them a missed read becomes a silent null nobody can distinguish
  from a genuine "unknown". Leaving it in `pending:` forces the next analyst cycle to hand-prune
  the queue before they can propose (observed in gpt-nano-agents 2026-05-26: cycles 2-4 each had
  analysts spending several turns cleaning up DISCARDed-but-still-pending items).
  A **NEAR_MISS** completes exactly like a KEEP — it is a real experiment with real numbers on both
  splits. Because NEAR_MISS is only decided later (Step 7b / 7b1), the row written here still says
  `outcome: KEEP`; the stack re-queue in Step 7 rewrites it to `NEAR_MISS` in the same PUT that adds
  `{exp_id}_stack`. Do not try to pre-empt that here.
- **FAILED** — nothing was tested (unapplied diff, scope abort, answer-key reject, stack conflict,
  harness/Redis error, empty result set). The item **STAYS in `pending:`**, unclaimed, so the next
  cpu-eval agent runs it; this step only refreshes its failure metadata and drops the claim.
  Completing a FAILED item silently discards the most valuable
  cycles — e.g. a candidate that cleared the train margin and then hit an ssh error on the test eval.
  A FAILED item is never `completed:`, never a dead end, never a stagnation tick.
  **`claim_lost` never reaches this step** — Step 4 ends the cycle there, without writing to the
  queue or touching a claim file it does not own. The `claim_lost` guard below is kept as a
  belt-and-braces check for a resumed cycle carrying that flag in an old sentinel.
- **`identity_bleed`** reaches this step from ONE place only: Step 5's re-assert at the `results/`
  write, which aborts instead of raising precisely so the claim gets released here and the
  [RESULT] gets posted in Step 8. Its branch below is deliberately different from every other
  outcome: **no row is written to `pending:` or `completed:`**, because both ids in play (`exp_id`
  and the row it points at) are the values we stopped trusting — writing either one back is how a
  bled id enters shared state, which is the whole failure this guard exists to stop. What we DO
  owe is the exclusion: the claim on `CLAIMED_EXP_ID` is ours, the row never left `pending:`, so
  it is handed back with the `released: true` marker and the item stays claimable. (Step 4's
  earlier bleed abort still ends the cycle inside Step 4 — it never held a verified claim to hand
  back.)

Do it all in a single read-modify-PUT.

```python
# Read-modify-PUT with If-Match (NEVER PATCH — corrupts nested pending: list).
# Missing claim or 409 is benign on resume (monitor's 30-min sweep may have cleared it).
from datetime import datetime, timezone

# The shared baseline was never a queue item and never had a claim file (Step 3) — there is
# nothing here to move or release.
if is_baseline:
    print("[STEP6] baseline_shared: no queue row and no claim file — nothing to release.")
else:
    q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json()
    q_fm  = parse_frontmatter(q_raw)
    claim_removed = q_fm.get("claims", {}).pop(AGENT_NAME, None) is not None

    # The local `item` is the freshest copy — it carries diff_path / diff_hunks / the abort
    # flags — so it is the row we write; the live `pending:` row is the fallback on a resume.
    pending   = list(q_fm.get("pending") or [])
    completed = list(q_fm.get("completed") or [])
    _pending_row = next((it for it in pending if it.get("id") == exp_id), None)
    row = dict(item or _pending_row or {})
    row["id"] = exp_id     # identity is not negotiable; never write back a bled id
    _completed_elsewhere = any(it.get("id") == exp_id for it in completed)

    # IDENTITY BLEED (from Step 5's results/-write re-assert): neither id may be written back
    # into the queue lists, so `pending:` and `completed:` are left exactly as read and only our
    # `claims[AGENT_NAME]` bookkeeping entry is dropped. The claim FILE is a different matter —
    # it is ours, under the id we froze at claim time, and leaving it standing locks the item for
    # the rest of the run. Hand it back with the `released: true` marker (never a delete: v1 of
    # that path names us forever, so the next PUT would land as vN+1 and strand the item).
    if item.get("identity_bleed"):
        print(f"[STEP6] identity bleed — queue lists untouched (neither {exp_id} nor the row it "
              f"names can be trusted); releasing our claim on {CLAIMED_EXP_ID} so the item stays "
              f"claimable.")
        _cb = resolve_claim(CLAIMED_EXP_ID)
        if _cb and _cb["holder"] == AGENT_NAME and not _cb["released"]:
            release_claim(_cb["path"], reason="identity_bleed")
    # Belt-and-braces (Step 4 already ended the cycle for a lost claim): if this item belongs
    # to another agent, drop our bookkeeping entry, leave the queue lists alone, and do NOT
    # delete the claim file — it is the real holder's exclusion.
    elif item.get("claim_lost") or (outcome == "FAILED" and _completed_elsewhere):
        print(f"[STEP6] {exp_id} belongs to another agent (claim_lost="
              f"{bool(item.get('claim_lost'))}, completed_elsewhere={_completed_elsewhere}) — "
              f"claim entry dropped, queue lists and claim file left untouched.")
    elif outcome == "FAILED":
        # Tested nothing: the row STAYS in pending:, unclaimed, for the next agent. Rewrite it
        # in place (dedupe first) with fresh failure metadata.
        pending = [it for it in pending if it.get("id") != exp_id]
        row.pop("claimed_by", None)
        row["requeued_at"]   = datetime.now(timezone.utc).isoformat()
        row["requeued_by"]   = AGENT_NAME
        row["requeue_count"] = int(row.get("requeue_count", 0) or 0) + 1
        row["last_failure"]  = (
            "stack_conflict"         if item.get("stack_conflict")
            else "answer_key_reject" if item.get("answer_key_reject")
            else "scope_abort"       if item.get("scope_abort")
            else "diff_not_applied"  if not diff_applied
            else "test_eval_infra"   if (provisional_keep and test_eval_failed)
            else "train_eval_infra")
        pending.append(row)
    else:
        # A real result: it moves out of pending: and lands in completed:, exactly once.
        pending   = [it for it in pending if it.get("id") != exp_id]
        completed = [it for it in completed if it.get("id") != exp_id]
        row["completed_at"] = datetime.now(timezone.utc).isoformat()
        row["completed_by"] = AGENT_NAME
        row["outcome"]      = outcome  # KEEP / DISCARD / REJECTED_TEST from Step 5
        row["val_score"]    = our_metric   # train
        # NOTE: `test_fitness`, NOT `test_score`. The SENTINEL key `test_score` holds the
        # full parsed eval dict; this queue field is a single float, and the ledger calls
        # it `test_fitness`. Same name, same type, one meaning.
        row["test_fitness"] = our_test     # held-out; None when the test eval did not run
        # `is_valid` / `delta` / `mean_rel_energy` are the ledger's SECOND source for this
        # experiment. It reads them from `results/{exp_id}.md` first and falls back to this
        # row when that read misses — and with no fallback source they land in the ledger as
        # silent nulls, which reads as "unknown" everywhere downstream. Same spellings and
        # same units as the result-file frontmatter, or the fallback is worse than useless.
        _sc6 = score or {}
        row["is_valid"]        = (int(_sc6.get("is_valid", 0)) if score else None)
        row["delta"]           = round(delta, 6)
        row["mean_rel_energy"] = (_sc6.get("mean_rel_energy") if score else None)
        completed.append(row)

    q_fm["pending"]   = pending
    q_fm["completed"] = completed

    q_body = q_raw.get("content", "").split("---", 2)[-1]
    q_new  = f"---\n{yaml.safe_dump(q_fm, sort_keys=False)}---{q_body}"
    assert yaml.safe_load(q_new.split("---")[1]) == q_fm, "frontmatter round-trip failed"
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
        headers={**HEADERS, "If-Match": str(q_raw.get("version", 0))},
        json={"content": q_new})  # 409 OK — continue to Step 7/8

    # Release the REAL exclusion — required on BOTH paths, and skipped only when the item is
    # another agent's, or when the identity-bleed branch above already released ours under
    # CLAIMED_EXP_ID (re-resolving here would look up a claim file named by the bled `exp_id`,
    # i.e. another agent's exclusion, and release or DELETE it). A claim file left standing is
    # not litter, it is a lock.
    _c = (None if (item.get("claim_lost") or item.get("identity_bleed"))
          else resolve_claim(exp_id))
    if _c and _c["holder"] == AGENT_NAME and not _c["released"]:
        if outcome == "FAILED":
            # The row is still in `pending:`, so it MUST stay claimable. Hand it back with the
            # same `released: true` marker ROLE-MONITOR's stale sweep writes, naming the
            # successor path — do NOT delete: this file's v1 names us forever, so if a delete
            # kept the history the next PUT would land as vN+1 and the item would be
            # unclaimable for the rest of the run.
            requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/{_c['path']}", headers=HEADERS,
                json={"content": (f"holder: {AGENT_NAME}\n"
                                  f"exp_id: {exp_id}\n"
                                  f"released: true\n"
                                  f"released_at: {datetime.now(timezone.utc).isoformat()}\n"
                                  f"released_by: {AGENT_NAME}\n"
                                  f"release_reason: requeued_failed\n"
                                  f"next_claim_path: "
                                  f"{claim_path_for(exp_id, _c['attempt'] + 1)}\n")})
        else:
            # Completed: the row has left `pending:` for good and will never be claimed
            # again, so the claim file is pure litter — delete it. Nothing can be lost here
            # even if the delete misbehaves.
            requests.delete(f"{API}/workspaces/{TEAM_WS_ID}/files/{_c['path']}",
                            headers=HEADERS)   # 404 benign — the monitor may have swept it

    print(f"[STEP6] {exp_id}: pending={[it['id'] for it in pending]} "
          f"completed={[it['id'] for it in completed]} claim_removed={claim_removed}")
```

### Step 7 — Update Champion (KEEP only)

If `outcome == "KEEP"` (train margin **and** held-out test margin **and** test validity, or the first
candidate valid on both splits under the seeding rule). `NEAR_MISS`, `REJECTED_TEST` and `FAILED`
never touch the champion:

**CRITICAL: Before propagating, make ALL improvements unconditional in your algo.py.**
Do NOT gate changes behind `if EXPERIMENT_ID == "exp_foo"` or similar checks.
Every improvement must be baked into the code as the default behavior.
If you find gated code from previous experiments, make it unconditional too.

```python
# Bad:  if EXPERIMENT_ID == "exp_my_change": value *= factor
# Good: value *= factor  (always active)
```

**Eval is deterministic — promote unconditionally on KEEP.** There is NO multi-seed gate and NO
noise-floor band: a candidate that cleared both the train margin and the held-out test gate in
Step 4c (or the first candidate valid on both splits under the seeding rule) is a real, reproducible
improvement. Write the champion immediately. Do not re-run anything to "confirm" the result —
re-running the same `algo.py` produces byte-identical scores.

**Promotion-race helper — define this BEFORE 7a; it runs for every outcome.** Two paths below can
find that another agent's champion landed while we were evaluating: the Step 7b PRE-PUT gate and the
Step 7b1 race guard. In both, the science succeeded (train gate **and** held-out test gate passed) and
only the scheduling lost — the outcome is **NEAR_MISS**, and the recovery is to re-queue the SAME
change as `{exp_id}_stack` so the next agent re-applies it to the NEW champion. Without the re-queue
the validated change is silently lost, which is exactly the failure this helper exists to prevent.
Both paths call the same function.

```python
import yaml, requests
from datetime import datetime, timezone
from pathlib import Path

# Bound unconditionally so Steps 7c / 8 can read them on every path.
near_miss_winner = None   # exp_id of the candidate that beat us to the champion
stack_id         = None   # "{exp_id}_stack" once the re-queue lands
stack_note       = None   # one-line human summary for the [RESULT] post

def requeue_stack(stack_reason, winner_exp_id):
    """NEAR_MISS recovery: put THIS candidate's change back on the queue as
    `{exp_id}_stack`, carrying the artifacts the next agent needs to re-apply the SAME
    change to the NEW champion. Read-modify-PUT with If-Match — same idiom as Step 6;
    NEVER PATCH (it corrupts the nested pending: list). Returns (stack_id, note)."""
    global near_miss_winner
    near_miss_winner = winner_exp_id

    # CAP — a stack of a stack must not fan out. One automatic re-test against the new
    # champion is the recovery; a second means the change keeps losing races, and an
    # analyst should decide deliberately rather than the queue breeding copies.
    # The cap suppresses the NEW queue item ONLY. It must NOT skip the outcome rewrite below:
    # Step 6 recorded this row while `outcome` was still KEEP, and returning early would leave
    # the ledger claiming a promotion that never happened.
    stack_capped = bool(item.get("stacked_from"))
    sid          = None if stack_capped else f"{exp_id}_stack"
    capped_note  = (f"NOT re-queued: {exp_id} is itself a stacked re-test of "
                    f"{item.get('stacked_from')}, and stacks do not fan out. This change has now "
                    f"passed both gates twice without being promoted — an analyst should "
                    f"re-propose it against the current champion.")

    for _attempt in range(3):
        q_raw2 = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                              headers=HEADERS).json()
        q_fm2  = parse_frontmatter(q_raw2)
        q_pending   = q_fm2.get("pending", []) or []
        q_completed = q_fm2.get("completed", []) or []

        # (1) ALWAYS first: Step 6 wrote this item into `completed:` while `outcome` was still
        #     KEEP. It is a NEAR_MISS — fix the row whatever happens to the stack item.
        relabelled = False
        for it in q_completed:
            if it.get("id") == exp_id and it.get("outcome") != "NEAR_MISS":
                it["outcome"] = "NEAR_MISS"
                relabelled = True

        # (2) Then decide about the stack item. IDEMPOTENT — a resume or a retry may already
        #     have queued it. Never duplicate.
        already_stacked = bool(sid) and any(it.get("id") == sid
                                            for it in (q_pending + q_completed))
        add_stack = bool(sid) and not already_stacked
        note = (capped_note if stack_capped
                else f"already present as {sid} in the queue — no duplicate added."
                if already_stacked else None)
        ret_sid = None if stack_capped else sid

        if not add_stack:
            if not relabelled:
                return ret_sid, note     # nothing left to write
            q_fm2["completed"] = q_completed
            q_body2 = q_raw2.get("content", "").split("---", 2)[-1]
            q_new2  = f"---\n{yaml.safe_dump(q_fm2, sort_keys=False)}---{q_body2}"
            assert yaml.safe_load(q_new2.split("---")[1]) == q_fm2, "frontmatter round-trip failed"
            r2 = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                              headers={**HEADERS, "If-Match": str(q_raw2.get("version", 0))},
                              json={"content": q_new2})
            if r2.status_code != 409:
                return ret_sid, note
            continue                      # 409 — re-read and retry

        stacked = {
            "id": sid,
            # SAME experiment, re-aimed at the new champion.
            "axis":        item.get("axis"),
            "direction":   item.get("direction"),   # EXPERIMENT axis direction
            "value":       item.get("value"),
            "description": item.get("description") or description,
            "hypothesis":  item.get("hypothesis"),
            "diff":        item.get("diff"),
            # Provenance + the artifacts that let the next agent apply the IDENTICAL change.
            "stacked_from": exp_id,
            "stack_reason": stack_reason,           # "pre_put_abort" | "race_lost"
            "lost_to":      winner_exp_id,
            "source_algo":  str(Path(FOCUS_ROOT) / "agents" / AGENT_NAME /
                                "workspace" / "repo" / f"algo_{exp_id}.py"),
            "diff_path":    item.get("diff_path"),
            "diff_hunks":   item.get("diff_hunks"),
            # What it scored on the OLD champion — context for the analyst, not a gate.
            "prior_train_fitness": our_metric,
            "prior_test_fitness":  our_test,
            # Inherits the parent's clearance: the mechanism was reviewed once as a proposal
            # and has since passed BOTH gates on real numbers. Re-gating it as `pending`
            # would idle the next agent on a change the system has already vetted twice —
            # and the review gate exists to vet UNTESTED mechanisms, which this is not.
            #
            # It must inherit `proposal_post` TOO, and that is not decoration: `review_status`
            # is only a cache, and every resolver in the system re-derives the truth from the
            # comments on `proposal_post`. A row claiming `ok` while naming no post is
            # unverifiable — no agent may write one, and every resolver reads it back as
            # `pending`, which would strand this retest in `pending:` forever. Pointing at the
            # parent's [PROPOSAL] makes the inherited clearance checkable: the same
            # `[REVIEW-OK]` that cleared the parent is what clears this row. `proposed_by` comes
            # along for the same reason — it is what makes the author's own comments not count.
            # (The parent necessarily HAS a proposal_post: without one it would never have been
            # claimable, so this cycle could not have happened.)
            "proposal_post": item.get("proposal_post"),
            "proposed_by":   item.get("proposed_by"),
            "review_status": "ok" if item.get("proposal_post") else "pending",
            "queued_by": AGENT_NAME,
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        # Goes to `pending:` UNCLAIMED — no claimed_by, no claims entry.
        q_pending.append(stacked)
        q_fm2["pending"]   = q_pending
        q_fm2["completed"] = q_completed

        q_body2 = q_raw2.get("content", "").split("---", 2)[-1]
        q_new2  = f"---\n{yaml.safe_dump(q_fm2, sort_keys=False)}---{q_body2}"
        assert yaml.safe_load(q_new2.split("---")[1]) == q_fm2, "frontmatter round-trip failed"
        r2 = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                          headers={**HEADERS, "If-Match": str(q_raw2.get("version", 0))},
                          json={"content": q_new2})
        if r2.status_code != 409:
            return sid, (f"re-queued as {sid} ({stack_reason}) — same axis/direction/value, "
                         f"unclaimed, to be re-applied to the new champion from "
                         f"{stacked['source_algo']}.")
        # 409 — a concurrent write landed. Re-read and retry.

    return None, (f"NOT re-queued and the completed row may still say KEEP: queue.md returned "
                  f"409 three times for {exp_id}. Say so in the [RESULT] post so an analyst "
                  f"fixes the row and re-queues this change by hand.")
```

#### Step 7a — Extract Reproduction Information

```python
import re

# 1. Read YOUR algo.py docstring (experiment description)
with open(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/algo.py") as f:
    code = f.read()
docstring_match = re.search(r'"""(.*?)"""', code, re.DOTALL)
experiment_description = docstring_match.group(1).strip() if docstring_match else "No description provided"

# 2. The full eval result dict (from Step 4) IS the structured result — no stdout
#    parsing needed. It is deterministic and complete.
result_metrics = score  # the JSON dict: fitness, is_valid, mean_rel_steps, etc.
```

#### Step 7b — Build Complete champion.md

**champion.md must be a complete standalone reproduction recipe.** Include ALL information needed to reproduce without reading algo.py.

**The recorded `metric_value` is the single deterministic train `fitness`.** Eval is deterministic —
there is exactly one measurement per `algo.py` per split, and re-running it reproduces the same value
byte-for-byte. There are no seeds, no seed_values, and no best-of-N selection.

**`test_metric_value` is the held-out anchor and advances with `metric_value` — both keys, one write,
only on an accepted promotion.** Never write one without the other: a champion.md with a stale
`test_metric_value` lets the next candidate clear the test gate against a number that no longer
describes the champion. Seeding sets both from this candidate's two runs.

The whole block is gated on `outcome == "KEEP"` in code, not just in prose. A `REJECTED_TEST` always
has a *better train number* than the champion — with only the prose gate it would sail through the
PRE-PUT train check and promote a change that failed the held-out gate.

`FENCE` is built at runtime so the nested json fences inside the f-string do not terminate this
markdown block. `champ_direction` (not `direction`) is the champion's minimize/maximize direction.
Every frontmatter field goes through Step 5's `yaml_scalar` here too, for the same reason it does
there — an unquoted value or a bare `None` corrupts the anchors every later cycle reads.

```python
import json
from datetime import datetime, timezone
FENCE = "`" * 3

if outcome != "KEEP":
    # HARD GATE: DISCARD / REJECTED_TEST / FAILED / NEAR_MISS never touch champion.md or
    # champion/algo.py. (NEAR_MISS is only assigned *inside* 7b/7b1, after this gate.)
    print(f"[STEP7b] outcome={outcome} — champion untouched; skip 7b and 7b1, go to Step 7c.")
else:
    champion_metric = our_metric   # deterministic TRAIN fitness from the eval score dict
    champion_test   = our_test     # deterministic held-out TEST fitness from Step 4c

    # Hoisted out of the f-string: `{}` literals inside an f-string expression only parse
    # on Python 3.12+, and this template must not depend on the agent's interpreter version.
    _sc, _ts = (score or {}), (test_score or {})
    _test_score_shared = {k: v for k, v in _ts.items() if k != "per_molecule"}
    _train_valid = int(_sc.get("is_valid", 0))
    _test_valid_i = int(_ts.get("is_valid", 0))
    # Already a JSON object literal, which IS valid YAML flow style — the one field that must
    # not be passed through `yaml_scalar` (that would quote it into a string).
    _settings = json.dumps(item.get("settings") or fresh_champ.get("settings") or {}, sort_keys=True)
    _run_id = fresh_champ.get("run_id")
    now = datetime.now(timezone.utc).isoformat()   # same spelling as Steps 1.5 / 3 — never `_now`/`NOW`

    # Read current champion version for If-Match
    current_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
    current_version = current_raw.get("version", 0)

    # (The body of this f-string is deliberately at column 0: a triple-quoted string keeps
    #  leading whitespace, and indenting it would break the YAML frontmatter it writes.)
    champion_content = f"""---
metric_name: {yaml_scalar(metric_name)}
metric_value: {yaml_scalar(champion_metric)}
test_metric_value: {yaml_scalar(champion_test)}
direction: {yaml_scalar(champ_direction)}
is_valid: {yaml_scalar(_train_valid)}
test_is_valid: {yaml_scalar(_test_valid_i)}
experiment_id: {yaml_scalar(exp_id)}
agent: {yaml_scalar(AGENT_NAME)}
run_id: {yaml_scalar(_run_id)}
updated_at: {yaml_scalar(now)}
settings: {_settings}
---

# Champion: {exp_id}

## Experiment Description

{md_field(experiment_description)}

## Result

- **Recorded metric (train):** {metric_name} = {champion_metric}
- **Delta from previous (train):** {delta:+.6f}
- **Held-out test:** {metric_name} = {champion_test} (delta {test_delta:+.6f}, is_valid={_test_valid_i})

## Eval Score

{FENCE}json
{json.dumps(score, indent=2, sort_keys=True)}
{FENCE}

## Held-out Test Score

Aggregate fields only; TEST `per_molecule` is deliberately omitted from shared champion state.

{FENCE}json
{json.dumps(_test_score_shared, indent=2, sort_keys=True)}
{FENCE}

## Reproduction

1. Copy `{FOCUS_ROOT}/champion/algo.py`
2. scp it to the eval head and run from the sella checkout (both splits; --molecules-dir is MANDATORY):
   `JAX_ENABLE_X64=1 python eval_candidate.py --program <remote algo.py> --split train --molecules-dir {EVAL_MOLECULES_DIR} --redis-host localhost --redis-port 6385`
   `JAX_ENABLE_X64=1 python eval_candidate.py --program <remote algo.py> --split test  --molecules-dir {EVAL_MOLECULES_DIR} --redis-host localhost --redis-port 6385`
3. Expected: train {metric_name} = {champion_metric}, test {metric_name} = {champion_test}
   (deterministic — exact match every run)

## Provenance

- Agent: {AGENT_NAME}
- Timestamp: {now}
- Source: {FOCUS_ROOT}/champion/algo.py
"""

    # Round-trip check — REQUIRED, same reason as Step 5. champion.md's anchors are read by
    # every later cycle; a frontmatter block truncated by an interpolated value is worse here
    # than anywhere else in the run.
    _cfm = yaml.safe_load(champion_content.split("---")[1]) or {}
    assert _cfm.get("experiment_id") == exp_id, \
        "champion frontmatter round-trip failed — an interpolated field broke the YAML block"

    # PRE-PUT GATE (REQUIRED — If-Match is NOT reliably enforced server-side, so a stale-If-Match
    # PUT can silently OVERWRITE a better concurrent champion). Re-read champion.md fresh right before
    # the PUT and ABORT if it already holds an equal-or-better anchor on EITHER split — the post-PUT
    # Step-7b1 exp_id guard cannot catch this (our own PUT would have just written our exp_id).
    # Re-checking the TEST anchor also closes the race where we cleared gate (b) in Step 4c against a
    # `champ_test` that another agent superseded while our test eval was running.
    _pre_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json()
    _pre = parse_frontmatter(_pre_raw)
    _pre_best = _pre.get("metric_value")       # freshest TRAIN anchor
    _pre_test = _pre.get("test_metric_value")  # freshest TEST anchor
    current_version = _pre_raw.get("version", current_version)  # freshest version for If-Match
    # minimize task: promote ONLY if strictly lower than the current champion on BOTH anchors.
    _clobber_train = (_pre_best is not None) and (float(champion_metric) >= float(_pre_best))
    _clobber_test  = (_pre_test is not None and our_test is not None
                      and float(our_test) >= float(_pre_test))
    if _clobber_train or _clobber_test:
        # An equal/better champion landed during our eval -> do NOT PUT (would clobber) and SKIP 7b1.
        # Both gates PASSED; we only lost the promotion race. That is NEAR_MISS, not KEEP: the
        # ledger must not claim a promotion that never happened. Nothing goes to dead_ends.md or
        # non_generalizable.md — this is positive evidence. Re-queue the change as
        # `{exp_id}_stack` to re-test on the new champion, then go to Step 7c.
        outcome = "NEAR_MISS"
        stack_id, stack_note = requeue_stack("pre_put_abort", _pre.get("experiment_id"))
        print(f"PRE-PUT ABORT ({'train' if _clobber_train else ''}{'+' if (_clobber_train and _clobber_test) else ''}{'test' if _clobber_test else ''}): "
              f"champion.md now {_pre.get('experiment_id')} (train {_pre_best}, test {_pre_test}) "
              f"vs ours (train {champion_metric}, test {our_test}); "
              f"outcome=NEAR_MISS, {stack_note} Do NOT overwrite or copy algo.py.")
    else:
        requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
            headers={**HEADERS, "If-Match": str(current_version)},
            json={"content": champion_content})
```

#### Step 7b1 — Propagate champion/algo.py — REQUIRED on KEEP

**Immediately after the champion.md PUT succeeds, you MUST copy your stamped candidate file
to `{FOCUS_ROOT}/champion/algo.py` and append a SOURCE line.** The local champion file
is read by every subsequent rotation's cpu-eval agent in Step 2 — leaving it stale corrupts
every downstream baseline. This step is the agent's responsibility, not the orchestrator's.

```python
import shutil
from datetime import datetime, timezone
from pathlib import Path

# HARD GATE (same as Step 7b): only a KEEP may propagate. A REJECTED_TEST would otherwise
# overwrite champion/algo.py with a change that failed the held-out gate. NEAR_MISS lands here
# too when Step 7b already aborted the PUT — its stack re-queue is done; nothing more to do.
if outcome != "KEEP":
    print(f"[STEP7b1] outcome={outcome} — champion/algo.py untouched; go to Step 7c.")
else:
    # RACE GUARD (REQUIRED, do this FIRST): copy algo.py ONLY if champion.md still records OUR exp_id.
    # The Step 4c margin check does NOT catch a race where two agents both pass on the OLD champion
    # before either promotes: the loser's If-Match champion.md PUT 409s, but if it copies algo.py
    # anyway it CLOBBERS the winner (champion.md=them, champion/algo.py=us).
    _champ_now = parse_frontmatter(requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md", headers=HEADERS).json())
    if _champ_now.get("experiment_id") != exp_id:
        # LOST the race -> do NOT copy and do NOT append SOURCE. Both gates passed, so this is a
        # NEAR_MISS, not a KEEP (we were never promoted) and never a DISCARD/REJECTED_TEST (the
        # science held). Re-queue our validated change as `{exp_id}_stack` to re-test on the NEW
        # champion/algo.py, then go to Step 7c.
        outcome = "NEAR_MISS"
        stack_id, stack_note = requeue_stack("race_lost", _champ_now.get("experiment_id"))
        print(f"CHAMPION RACE LOST (champion.md now {_champ_now.get('experiment_id')}) -> "
              f"outcome=NEAR_MISS, {stack_note}")
    else:
        # WON the race — champion.md records our exp_id.
        # Atomic write: temp-then-rename so concurrent KEEPs cannot half-overwrite.
        src = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace/repo/algo_{exp_id}.py")
        dst = Path(f"{FOCUS_ROOT}/champion/algo.py")
        tmp = dst.with_suffix(".py.tmp")
        shutil.copy(src, tmp)
        tmp.replace(dst)  # atomic on POSIX

        # Append provenance to champion/SOURCE (one line per promotion).
        src_log = Path(f"{FOCUS_ROOT}/champion/SOURCE")
        ts = datetime.now(timezone.utc).isoformat()
        with src_log.open("a") as f:
            f.write(f"{exp_id} {our_metric:.6f} {AGENT_NAME} {ts}\n")
```

**Race-safety:** if multiple cpu-eval agents land KEEPs in the same rotation, the champion.md PUT is
*supposed* to serialize them via If-Match — but If-Match is NOT reliably enforced server-side, so the
real protection is the **PRE-PUT GATE** (Step 7b: re-read champion.md, abort if an equal/better
champion already landed on **either** the train or the test anchor) plus the **RACE GUARD** at the top
of Step 7b1 (copy algo.py only if champion.md records THIS exp_id). A loser records **NEAR_MISS** and
re-queues its change as `{exp_id}_stack` via `requeue_stack(...)`. The winner's `tmp.replace(dst)` is
atomic.

**A race loss is not a scientific verdict.** The loser measured a genuine improvement on both splits;
it just arrived second. So it is never demoted to DISCARD or REJECTED_TEST, never written to
`dead_ends.md` or `non_generalizable.md`, and never counted as a stagnation tick (by construction
another candidate *was* promoted in that rotation, so the run is demonstrably not stuck). Analysts
must read NEAR_MISS as **supporting** evidence for its hypothesis.

**Why this exists:** the champion file is the baseline every subsequent experiment's
diff is applied against. A several-KEEP-deep stale champion file means agents who don't know
to read the latest stamped candidate in the winning workspace will silently regress
the codebase. This step replaces the prior "orchestrator promotes" model that was
unreliable in practice.

#### Step 7c — Write result_latest.json (agent-local sentinel)

`result_latest.json` is your post-eval state record — it lets HEARTBEAT Part 0
resume an unposted result on the next session and is read by analysts who want to
know your last outcome. Champion propagation already happened in Step 7b1; this file
is purely a sentinel.

This block is **self-contained** — it re-imports and re-derives `ws`, because on the FAILED path
Step 4's eval block never ran and its imports/`ws` are unbound.

```python
import json
from pathlib import Path
from datetime import datetime, timezone

# Merge with any Step 4 in-flight sentinel to preserve stdout_path/launched_at.
ws = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace")
rl = ws / "result_latest.json"
prior = json.loads(rl.read_text()) if rl.exists() else {}
rep = ws / "repo"

rl.write_text(json.dumps({**prior,
    # `score` is None on every FAILED path — guard the deref.
    "score": score, "fitness": our_metric,
    "is_valid": (int(score.get("is_valid", 0)) if score else None),
    # EXPERIMENT axis direction (increase/decrease/none), NOT the champion's minimize/maximize.
    "direction": item.get("direction"), "outcome": outcome,
    # Held-out gate (from Step 4c; None when the test eval did not run).
    "test_status": test_status,
    "test_score": test_score,
    "test_fitness": our_test,
    "test_is_valid": (int(test_valid) if test_valid is not None else None),
    "test_mean_rel_energy": (test_score or {}).get("mean_rel_energy"),
    "test_pass": test_pass, "test_reason": test_reason,
    "diff_path": item.get("diff_path"), "diff_hunks": item.get("diff_hunks"),
    # REPORT_EXP_ID == exp_id on every ordinary path; it is CLAIMED_EXP_ID only after Step 5's
    # identity-bleed abort, where `exp_id` is the value we stopped trusting and a resume reading
    # it would go looking for another agent's claim file and result.
    "exp_id": REPORT_EXP_ID, "agent": AGENT_NAME,
    "algo_path": str(rep / f"algo_{exp_id}.py"),
    "timestamp": datetime.now(timezone.utc).isoformat(),
    # Resume fields — HEARTBEAT Part 0 Check C reads these. REQUIRED.
    "status": "complete", "posted_to_workshop": False, "result_post_id": None,
    "item": prior.get("item") or item,
    "queue_claimed": prior.get("queue_claimed", True),
    "description": description,
}, indent=2, default=str))
```

**If NEAR_MISS:** write **NOTHING** to any team file. No `dead_ends.md`, no `non_generalizable.md` —
the change passed both gates and is positive evidence; only the promotion race was lost, and Step 7's
`requeue_stack(...)` already put it back on the queue as `{exp_id}_stack`. The one thing left is to
correct the result file, which Step 5 wrote while `outcome` was still `KEEP`.

```python
# `duplicate_execution` (Step 5) means the result file belongs to the OTHER agent — do not
# rewrite their frontmatter; the duplicate-execution section already carries our numbers.
if outcome == "NEAR_MISS" and not duplicate_execution:
    _rf = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                       headers=HEADERS).json()
    _rc = _rf.get("content", "")
    # Step 5 renders frontmatter through `yaml_scalar`, so the outcome is JSON-QUOTED
    # there (`outcome: "KEEP"`); the `# {exp_id} — KEEP` heading is body text and is not.
    _rc = _rc.replace('outcome: "KEEP"', 'outcome: "NEAR_MISS"', 1)
    _rc = _rc.replace(f"# {exp_id} — KEEP", f"# {exp_id} — NEAR_MISS", 1)
    _rc += (
        "\n## Promotion Race\n\n"
        f"Both gates PASSED (train {our_metric}, delta {delta:+.6f}; held-out test {our_test}, "
        f"delta {test_delta:+.6f}) but this candidate was NOT promoted — "
        f"{near_miss_winner or 'a concurrent candidate'} took the champion first. "
        f"This is a scheduling loss, not a scientific one: treat it as SUPPORTING evidence for the "
        f"hypothesis, not a refutation. {stack_note}\n")
    r = requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                     headers={**HEADERS, "If-Match": str(_rf.get("version", 0))},
                     json={"content": _rc})
    print(f"NEAR_MISS: result file re-labelled (HTTP {r.status_code}); "
          f"no dead_ends.md / non_generalizable.md write by design.")
```

**If an ordinary candidate is DISCARD:** write the result to `dead_ends.md` in your team workspace
so analysts and other cpu-eval agents do not re-run this exact mechanism, and so it counts one
toward its axis's closure count. `baseline_shared` is infrastructure, not a proposed mechanism, and
must never enter a team's negative-evidence files. **One DISCARD closes nothing on its own** — the
axis stays open until enough results accumulate against it with no KEEP or NEAR_MISS in between,
and that count is the analysts' to keep (ROLE-ANALYST / ROLE-TEAM own the threshold). Recording an
entry here is evidence, not a verdict on the whole axis; treating it as one purges a team's queue on
the strength of a single result. Use If-Match to avoid clobbering concurrent writes.

**Only `outcome == "DISCARD"` may write to dead_ends.md.** `FAILED`, `NEAR_MISS` and `REJECTED_TEST`
must NEVER be written there:

- `FAILED` is an *infrastructure or scope* outcome (unapplied diff, harness/Redis error, empty result
  set, scope abort, answer-key reject, stack conflict). The mechanism was
  never tested. Filing it as a dead end
  permanently burns a family on no evidence — the item is still in `pending:` for the next agent.
  (The `claim_lost` abort and Step 4's `identity_bleed` abort never reach this step; they end the
  cycle in Step 4. Step 5's `identity_bleed` abort DOES pass through here, as `outcome = "FAILED"`
  — and FAILED writes nothing here, which is the point: a cycle that cannot say which experiment
  it measured must not file evidence against any mechanism at all.)
- `NEAR_MISS` *succeeded* on both splits. Filing it anywhere negative would burn a mechanism family
  that just demonstrated it works. It writes no team file at all.
- `REJECTED_TEST` goes to `non_generalizable.md` (below), which is a different kind of evidence and
  read by analysts for a different reason.

Keep each entry to the structured fields — no prose paragraphs.

```python
if outcome == "DISCARD" and not is_baseline:
    de_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                          headers=HEADERS).json()
    de_content = de_raw.get("content", "# Dead Ends\n\n")
    de_version = de_raw.get("version", 0)

    # Structured entry — REQUIRED. Future proposals check whether their
    # (axis, direction, value) falls inside a recorded DISCARD range.
    # Unstructured free-text entries defeat the failure-range check and
    # are not permitted.
    axis = item.get("axis") or "UNKNOWN"
    exp_direction = item.get("direction") or "UNKNOWN"   # EXPERIMENT axis direction
    value = item.get("value")
    # THE FAMILY OF AN EXPERIMENT IS ITS AXIS, exactly as the queue item states it. The old
    # `"_".join(exp_id.split("_")[:2])` looked like a mechanism prefix, but under the mandated
    # `exp_<team-prefix>_<mechanism>` id schema it evaluates to the TEAM — so closing a
    # "family" purged an entire team's queue on the evidence of a handful of results about
    # unrelated mechanisms. A stacked re-test (`{exp_id}_stack`) carries its parent's axis
    # verbatim, so it lands in the parent's family for free — and it is recorded here like any
    # other DISCARD, with no carve-out: a stack retest consumes real eval-pool time and returns
    # real evidence about whether the mechanism survives on top of the new champion, so it
    # counts toward the axis's closure count exactly as its parent would have. (Exempting it
    # would also let one axis emit stack retests forever and never close.)
    fam = axis
    # `experiment_description` is only extracted in Step 7a (KEEP path) — fall back to the
    # queue item's description so a DISCARD never NameErrors on the way to dead_ends.md.
    _reason_txt = (item.get("description") or description or "")[:160].replace(chr(10), " ")
    entry = (
        f"\n- exp_id: {exp_id}\n"
        f"  axis: {axis}\n"
        f"  direction: {exp_direction}\n"
        f"  value: {value}\n"
        f"  delta: {delta:+.6f}\n"
        f"  family: {fam}\n"
        f"  date: {datetime.now(timezone.utc).date()}\n"
        f"  reason: {_reason_txt}\n"
    )

    r = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                     headers={**HEADERS, "If-Match": str(de_version)},
                     json={"content": de_content + entry})
    if r.status_code == 409:
        print("dead_ends.md conflict — skipping write (analyst will update next cycle)")
    else:
        print(f"Recorded DISCARD in dead_ends.md (HTTP {r.status_code})")
```

**If an ordinary candidate is REJECTED_TEST:** write to `non_generalizable.md` in your **team**
workspace (create it if it does not exist yet). This file records changes that *did* improve train
but failed the held-out gate — the overfitting record, and a different kind of evidence from a dead
end: "didn't generalize", not "didn't work". `baseline_shared` is infrastructure and never writes
team scientific evidence. Analysts read this file before proposing. A REJECTED_TEST counts one
toward its axis's closure count, exactly like a DISCARD, but it does **not** close the axis on its
own; the failure is evidence against the *mechanism* rather than its tuning, so a re-proposal that
only nudges the value needs a stated reason why tuning was the problem.

```python
if outcome == "REJECTED_TEST" and not is_baseline:
    ng_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/non_generalizable.md",
                          headers=HEADERS).json()
    # Create-if-absent: a 404 comes back with no content/version.
    ng_content = ng_raw.get("content") or (
        "# Non-Generalizable\n\n"
        "Changes that improved TRAIN but failed the held-out TEST gate.\n"
        "Each entry counts toward its axis's closure count. Re-proposing the same mechanism "
        "with a tweaked value needs a stated reason why the failure was tuning.\n")
    ng_version = ng_raw.get("version", 0)

    axis = item.get("axis") or "UNKNOWN"
    exp_direction = item.get("direction") or "UNKNOWN"   # EXPERIMENT axis direction
    value = item.get("value")
    fam = axis   # the family IS the axis — same rule, same reason, as in dead_ends.md above
    entry = (
        f"\n- exp_id: {exp_id}\n"
        f"  axis: {axis}\n"
        f"  direction: {exp_direction}\n"
        f"  value: {value}\n"
        f"  train_fitness: {our_metric}\n"
        f"  test_fitness: {our_test}\n"
        f"  test_is_valid: {int(test_valid) if test_valid is not None else 'null'}\n"
        f"  reason: {test_reason}\n"   # test-invalid | insufficient-test-improvement | both
        f"  family: {fam}\n"
        f"  date: {datetime.now(timezone.utc).date()}\n"
    )

    r = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/non_generalizable.md",
                     headers={**HEADERS, "If-Match": str(ng_version)},
                     json={"content": ng_content + entry})
    if r.status_code == 409:
        print("non_generalizable.md conflict — re-read and retry once")
    else:
        print(f"Recorded REJECTED_TEST in non_generalizable.md (HTTP {r.status_code})")
```

### Step 8 — Post Result to Workshop (MANDATORY)

**This step is required for EVERY experiment — KEEP, NEAR_MISS, DISCARD, REJECTED_TEST or FAILED.** A result file in the workspace is not enough — the workshop post is what notifies analysts and other teams. Skipping this step makes the experiment invisible to the rest of the system.

Post as a NEW workshop post (not a comment on the kickoff thread). Keep it compact: the numbers, the
diff, and one or two sentences of interpretation. No step-by-step narration.

```python
FENCE = "`" * 3   # built at runtime so the markdown fence around this block stays intact

# Diff for audit: full body if small, else hunk headers + count (Step 4).
# Same guard as Step 5: `diff_applied` is True on the answer-key route (the item has no
# `diff_applied` key), so the artifact's own emptiness is what decides.
if cycle_aborted and not diff_lines:
    diff_block = ("(no diff — cycle aborted before any code was written; "
                  f"reason: {cycle_aborted})")
elif is_baseline:
    diff_block = "(no diff by design — baseline_shared evaluates the unchanged champion)"
elif not diff_applied or not diff_lines:
    diff_block = "(no diff — the change did not apply; candidate was identical to champion)"
elif len(diff_lines) <= 80:
    diff_block = diff_text
else:
    diff_block = ("\n".join(hunk_headers) +
                  f"\n({n_hunks} hunks, {len(diff_lines)} diff lines — full patch at {diff_path})")

if cycle_aborted == "identity_bleed":
    # Step 5 refused the `results/` write: `exp_id` / `item` stopped naming what we claimed, so
    # nothing measured here can be attributed to any experiment. Say that plainly — the post
    # exists so the cycle is visible and the claim release is on record, not to report a number.
    verdict = (f"FAILED (identity_bleed) — cross-agent state bleed detected at the `results/` "
               f"write: this session claimed {CLAIMED_EXP_ID} but `exp_id` had become {exp_id}, "
               f"so the measurement cannot be attributed and was NOT filed under either id. "
               f"Nothing was written to `results/`, champion.md, champion/algo.py, dead_ends.md "
               f"or non_generalizable.md, and no row was moved in the queue. The claim on "
               f"{CLAIMED_EXP_ID} was released, so the item is claimable again; the numbers are "
               f"in this agent's `result_latest.json` if anyone wants to reconstruct them.")
elif outcome == "NEAR_MISS":
    # Say plainly that the SCIENCE passed and the SCHEDULING lost — analysts must not read
    # this as a refutation, and the ledger must not read it as a promotion.
    verdict = (f"NEAR_MISS — BOTH gates PASSED: train {metric_name}={our_metric} "
               f"(delta {delta:+.6f}) and held-out test {metric_name}={our_test} "
               f"(delta {test_delta:+.6f}, is_valid="
               f"{int(test_valid) if test_valid is not None else 'null'}). "
               f"It was NOT promoted: it lost the promotion race to "
               f"{near_miss_winner or 'a concurrent candidate'}, which landed in champion.md "
               f"while this candidate was evaluating. champion.md and champion/algo.py are "
               f"untouched, and NOTHING was written to dead_ends.md or non_generalizable.md — "
               f"this is supporting evidence for the hypothesis. {stack_note}")
elif outcome == "REJECTED_TEST":
    # State plainly WHY it was rejected: test-invalid / insufficient-test-improvement / both.
    verdict = (f"REJECTED_TEST ({test_reason}) — improved on train "
               f"({delta:+.6f}) but failed the held-out gate.")
elif our_test is not None:
    verdict = (f"test {metric_name}={our_test} (delta {test_delta:+.6f}, "
               f"is_valid={int(test_valid) if test_valid is not None else 'null'})")
elif test_status == "failed":
    verdict = "test: eval FAILED (infra) — no held-out number; the item is re-queued"
else:
    verdict = "test: not run (no provisional train keep)"

# Duplicate execution is a COORDINATION defect, not a scientific one — say so in the post
# so the monitor and the analysts can reconcile the two rows instead of reading two
# independent confirmations of one experiment as two pieces of evidence.
dup_note = ("" if not duplicate_execution else
            f"\n\n## Duplicate execution (coordination defect)\n"
            f"`{exp_id}` was ALSO run by another agent — the queue claim was not exclusive. "
            f"Their result file was left intact; this cycle's numbers were APPENDED to it "
            f"under `## Duplicate execution`. Two independent evals of one experiment, "
            f"one pool budget spent twice.")

# *** THE TITLE IS MACHINE-PARSED. DO NOT EMBELLISH IT. ***
# The orchestrator's cycle_ledger hook harvests the experiment ledger from this title with
#   RESULT_RE = r"\[RESULT\]\s+(\S+?):\s*.*?=\s*([0-9.]+)\s*\((\w+)\)"
# which requires the NUMBER to sit IMMEDIATELY before "(OUTCOME)". Any prose between them — an
# invalidity reason, a caveat, a "but" — makes the title unparseable, and the row then depends
# entirely on the ledger's fallback path (post tags + the result file's frontmatter) to be
# recovered at all. That fallback exists because titles HAVE been mangled this way and rows were
# lost outright; it is a safety net, not a licence. Emit the f-string below EXACTLY as written.
# Every caveat, invalidity reason and nuance belongs in the BODY, which is free-form, never
# parsed, and where `verdict` already puts it.
#
# `REPORT_EXP_ID` — not `exp_id`. They are the same value on every ordinary path; they differ
# only after Step 5's identity-bleed abort, where filing this post under the bled id would put
# this cycle's row on another agent's experiment in the ledger.
_result_team = None if is_baseline else MY_TEAM
_result_tags = (
    ["type:infrastructure", f"outcome:{outcome}"]
    if is_baseline
    else [f"team:{MY_TEAM}", "type:result", f"outcome:{outcome}"]
)
_result_notify = [] if is_baseline else team_members

r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[RESULT] {REPORT_EXP_ID}: {metric_name}={our_metric} ({outcome})",
    "content": (
        f"## Experiment\n{description}\n\n"
        f"## Result\ntrain {metric_name}: {our_metric} (delta {delta})\n{verdict}\n"
        f"Outcome: {outcome}\nRace condition: {race_condition}\n\n"
        f"## Diff\n{FENCE}diff\n{diff_block}\n{FENCE}\n\n"
        f"## Team\n{_result_team or 'global shared baseline'}{dup_note}"),
    "notify_agents": _result_notify,
    "tags": _result_tags,
})
result_post_id = r.json().get("id") if r.ok else None
```

### Step 8b — Mark result as posted (REQUIRED — prevents duplicate [RESULT] next cycle)

```python
import json
from pathlib import Path
from datetime import datetime, timezone

rl_path = Path(f"{FOCUS_ROOT}/agents/{AGENT_NAME}/workspace") / "result_latest.json"
rl = json.loads(rl_path.read_text())
rl.update({"status": "posted", "posted_to_workshop": True,
           "result_post_id": result_post_id,
           "posted_at": datetime.now(timezone.utc).isoformat()})
rl_path.write_text(json.dumps(rl, indent=2, default=str))

# Backfill `post_id` in the result file so the ledger can link result → post.
# On a duplicate execution the frontmatter `post_id` belongs to the FIRST writer — leave it
# alone and fill our own placeholder in the duplicate-execution section instead.
# SKIPPED after an identity bleed: Step 5 wrote no result file, so `results/{exp_id}.md` is
# either absent or somebody else's — backfilling our post id into another agent's frontmatter
# is the same cross-experiment contamination the abort was raised to prevent.
if result_post_id and cycle_aborted != "identity_bleed":
    _rf = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
                       headers=HEADERS).json()
    _rc = _rf.get("content", "")
    _placeholder = f"DUP_POST_ID_{AGENT_NAME}"
    if duplicate_execution and _placeholder in _rc:
        _new = _rc.replace(_placeholder, str(result_post_id), 1)
    elif not duplicate_execution and "post_id: null" in _rc:
        _new = _rc.replace("post_id: null", f"post_id: {result_post_id}", 1)
    else:
        _new = None
    if _new is not None:
        requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/results/{exp_id}.md",
            headers={**HEADERS, "If-Match": str(_rf.get("version", 0))},
            json={"content": _new})
```

### Step 9 — (removed) No Noise-Band Near-Miss Protocol

Eval is **deterministic** — there is no measurement noise and therefore no noise band to anchor a
"near-miss" against. Every result is exactly one of five outcomes:

- **KEEP** — cleared the train margin AND the held-out test gate (Step 4c), or is the first candidate
  valid on both splits under the seeding rule, AND won the promotion race. Champion updated.
- **NEAR_MISS** — cleared both gates but was not promoted, because an equal-or-better champion landed
  while this candidate was evaluating (Step 7b PRE-PUT abort or Step 7b1 race guard). Writes no team
  file, is re-queued as `{exp_id}_stack`, and counts as **supporting** evidence — never a refutation,
  never a stagnation tick.
- **REJECTED_TEST** — cleared train, failed the held-out gate. Recorded in `non_generalizable.md`
  (Step 7c), and counts one toward its axis's closure count.
- **DISCARD** — invalid on train, or valid but sub-margin. Recorded in `dead_ends.md` (Step 7c) so the
  exact mechanism is not re-run, and counts one toward its axis's closure count — it does **not**
  close the axis on its own.
- **FAILED** — infrastructure or scope, not science: unapplied diff, harness/Redis error, empty result
  set, a multi-mechanism diff, an answer-key reject, a stacked re-test that conflicts with the current
  champion, an `exp_id` that no longer matches the claimed item (identity bleed), or a claim whose
  version 1 names another agent (lost claim). Never recorded as a dead end, and the item stays in
  `pending:`. The last two never write a `results/` file — a cycle that cannot name its own
  experiment must not file numbers under anyone's id. A lost claim, and a bleed caught before the
  eval, end the cycle inside Step 4 with only a sentinel and a `[RESULT]` post, touching no queue
  row and no claim file, because the experiment is not theirs. A bleed caught later — at Step 5's
  `results/` write — still runs Steps 6 and 8: it leaves both queue lists untouched but releases
  the claim it does hold, on the id frozen at claim time, and posts the `[RESULT]` under that same
  id. Aborting is not the same as vanishing: the claim must come back and the cycle must be visible.

`NEAR_MISS` is a **promotion-race** label, not a noise-band one. Do NOT post `[NEAR-MISS]` as a post
type and do NOT consult any noise floor: a non-improving deterministic delta is simply a DISCARD, and
the structured dead-end entry carries the signal analysts need. NEAR_MISS is reported through the
ordinary `[RESULT]` post (Step 8) like every other outcome.

**One experiment per session.** A cycle costs up to two evals on shared infrastructure — the train
split always, plus the held-out test split on a provisional keep. After Step 8b, finish the session —
do not go back to Step 2 for a second experiment.
