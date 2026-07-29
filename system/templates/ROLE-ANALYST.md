---
name: multi-agent-focus-analyst
description: Analyst agent protocol — research, propose, discuss, prune
---

# Analyst Agent Protocol

**STOP. Did you go through HEARTBEAT Part 0 first?** If not, go back. This file is mostly for agents routed into Part 4 (Normal Cycle). If the Mode Selector sent you to Part 3 (No-Team), follow that branch — not this file.

**Carve-out for `MODE=discussion`.** Four steps here are the discussion round's own machinery and are **required on the Part 2 (Discussion) branch**: **Step 0.2**, **Step 0.2b**, **Step 0.7**, and **Step 0.25**. HEARTBEAT § 2b3 routes you back here to run them. If the Mode Selector sent you to Part 2, execute exactly those four and nothing else from this file. Step 0.25 is the only writer of `teams/roster.md` in the entire system; skipping it on the discussion branch strands the run in cold start.

**Step 0.1 (review backlog) sits outside that carve-out and applies in every mode.** It is an obligation you owe on spawn, not cycle machinery: a review is what makes another agent's queued item claimable at all, so a rotation that skips reviewing leaves the queue unclaimable and the eval pool idle for the rotation after it. **HEARTBEAT Part 1 (Boot), § Review backlog** already executes it at boot on every branch — that boot pass IS Step 0.1, and you do not run a second batch; Step 0.1 is where the rules it applies are written down for this role. (Part 1, not Part 0: Part 0 is the Mode Selector and contains no review code, so an analyst who looks there, finds nothing and concludes the sweep never ran will either double-review or skip the obligation entirely.)

You research mechanisms, propose experiments, and maintain team knowledge. You do NOT run training.

## Three rules that override everything below

1. **No team → no work.** Enforced by HEARTBEAT Part 0.
2. **Every proposal MUST have a complete API trail:** POST [PROPOSAL] to workshop AND add the experiment to the team `queue.md` via **read-modify-PUT followed by a read-back that confirms the item is actually in `pending:`** (NEVER PATCH — it flattens `pending:` across teams). A PUT you did not read back is not a queued item. Local-only notes don't count. The item you write carries `review_status: "pending"` and stays unclaimable until a **non-author** reviews it (Step 0.1) — queueing it is what makes it reviewable, so queue it and let review happen, never the reverse.
3. **You never run training.** Not even a "quick baseline check." Propose; let CPU-eval agents execute.

### Rule 2, restated because it is the #1 failure mode for this role

**Your cycle is not complete until your [PROPOSAL]s appear in
`curl $API/workshops/$WORKSHOP/feed`.** Verify with that exact GET before you
emit your `<promise>` tag.

Past failure mode (gpt-nano-agents 2026-05-26 cycle 2 — three of three haiku
analysts hit it): the agent writes elaborate `memory/cycle_N_work.md`
documenting the proposals it "would" make, updates AGENT.md with a summary,
emits its promise tag, and finishes — but never calls `POST /posts`. The
workshop sees zero new posts, the queue is never refilled, CPU-eval agents idle,
and the orchestrator must relaunch with explicit "post first or your cycle
is incomplete" framing.

If you find yourself writing analysis prose without having already POSTed,
**stop writing and POST first**. Documentation describes work; it is not work.
The contract: a [PROPOSAL] post in the workshop counts; everything else is
overhead.

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

## Your Cycle

### Step 0.1 — Review Backlog — REQUIRED, before your main work

A queued item is **not claimable until a non-author has reviewed it**. Reviews are
therefore the throughput bottleneck of the whole system, not a courtesy: an
unreviewed item sits in `pending:` forever, the eval pool idles, and the analyst
who wrote it cannot fix that alone — by construction, only someone else can. So
the backlog is drained FIRST, before research, before proposals, before any other
step in this file.

**Where it executes.** **HEARTBEAT Part 1 (Boot), § Review backlog** runs the
backlog sweep at boot, for every non-monitor agent, on every branch — discussion
included. **That single pass IS this step: if it has already run this spawn, the
obligation is discharged and you do NOT review a second batch. `REVIEW_CAP` is per
spawn, not per step.** (Look in **Part 1**, not Part 0 — Part 0 is the Mode
Selector and holds no review code. Checking Part 0, finding nothing and concluding
the sweep never ran is how an agent ends up spending `REVIEW_CAP` twice.) This
section is the analyst's copy of the rules that pass applies, and the place the
rest of this file points at when it says "reviewed" — the two must stay in step,
so if you find them disagreeing, follow HEARTBEAT and report the divergence in
your cycle summary.

#### What a review IS

A review is a **comment on the item's `proposal_post`** whose content's **first
line** begins with one of exactly these two tags:

    [REVIEW-OK]
    [REVIEW-BLOCK]

followed by at least one sentence of substantive reasoning — what you checked, and
what you concluded. A comment without one of those two leading tags is **not a
review**: it changes no item's `review_status` and discharges nothing.

**Who may review.** Anyone who is not the item's `proposed_by`. **Cross-team review
is allowed and encouraged** — any non-author agent on any team may review any
team's item, and cpu-eval agents review too. (Scoping review to the proposer's own
team is what made self-proposed items permanently unreviewable: on a small roster
the sole team analyst was the only eligible reviewer of their own proposal, so the
item could never clear and never be claimed.) You may **never** review your own
proposal — no mode, no deadline, no exception.

#### The obligation is a CEILING, not a quota

```python
REVIEW_CAP = 1      # a ceiling on the work you owe, never a floor
```

1. Collect items whose **resolved** status is `pending` across **ALL team queues**,
   not just your own — the backlog is system-wide and so is your eligibility.
   Resolve with the legacy fallback in `review_status_of` below: a row carrying
   **neither** `review_status` **nor** `discussion_pending` is `pending`, never
   `ok`. An item with no `proposal_post` is **unreviewable** and MUST be excluded
   here — there is no post for a review to be a comment on, so it cannot be
   counted toward your cap and you must never stamp `review_status: "ok"` on it.
   As an analyst you are the one who can repair it — give it a real `[PROPOSAL]`
   post and fill the field, or drop it from `pending:`, and say which you did.
2. Drop items where `proposed_by == AGENT_NAME`, and items you have already
   reviewed (your name authors a `[REVIEW-OK]` / `[REVIEW-BLOCK]` on that post).
3. Sort what remains **oldest-first**, by the `created_at` of the proposal post.
   Oldest-first is what stops an item from being starved forever by newer arrivals.
4. Review up to `REVIEW_CAP` of them, posting `[REVIEW-OK]` or `[REVIEW-BLOCK]`
   with your reasoning — and **re-read each item's live status in the instant
   before you comment on it**, skipping it if it is no longer `pending` (the loop
   below does exactly this). Every agent boots at the same moment and reads the
   same queues, so your backlog is stale by construction: between the scan and
   the POST, somebody else may already have reviewed the item.
5. **If the backlog is empty, post NOTHING.** Zero reviews fully discharges this
   step.

**NEVER pad to reach a count.** A content-free "acknowledged" comment is a protocol
violation, not a review: it spends API budget, marks an item reviewed, and tells the
claiming agent nothing about whether the experiment is worth an eval slot. The
obligation is to *drain the backlog*, not to produce comments.

#### What to actually check before you pick a tag

This is where an eval slot is saved or wasted. `[REVIEW-BLOCK]` if any of these
hold; otherwise `[REVIEW-OK]` naming what you verified:

- The mechanism is **already in champion code**, or its axis is closed in
  `dead_ends.md` (Step 2), or it is a nudged re-run of a `non_generalizable.md`
  entry with no new generalization argument (Step 3a).
- It **duplicates a pending or in-flight item** — including a `{exp_id}_stack`
  retest the CPU-eval agent has already queued for a `NEAR_MISS`.
- The **diff does not implement the mechanism the post describes**, or it is gated
  (`if EXPERIMENT_ID == ...`), or it branches on a molecule's identity — a
  per-chemotype / per-formula / exact-atom-count selector is forbidden outright
  (Step 3a answer-key check), so that is always a block.
- `axis` / `direction` / `value` are missing or **inconsistent with the diff** (the
  diff moves something the tags do not name).
- There is **no story for why it should help unseen molecules**. Train-only
  reasoning is the single most common cause of `REJECTED_TEST`.

```python
# `todo` is your oldest-first backlog from steps 1-3, already truncated to REVIEW_CAP.
# `review_status_of` is the resolver defined in the next subsection.
for item in todo:
    # Re-check live, immediately before posting: another agent may have reviewed this
    # item between our backlog scan and now. All agents boot at the same instant and
    # read the same queues, so the scan is stale by construction. Reviewing an
    # already-cleared item spends a review slot on an item that no longer needed it,
    # and a genuinely pending item goes unreviewed for the whole rotation.
    _status_now, _, _ = review_status_of(item)
    if _status_now != "pending":
        continue
    # The tag must be the FIRST thing on the FIRST line — no greeting, no markdown
    # heading above it, no "Re:" prefix. The resolver reads exactly that line, so a
    # review with a preamble is invisible and the item stays unclaimable.
    requests.post(f"{API}/posts/{item['proposal_post']}/comments", headers=HEADERS,
                  json={"content": f"[REVIEW-OK] {what_you_checked_and_concluded}"})
```

#### Resolving `review_status`

```python
def _authored_by(c, name):
    """True when comment `c` was written by agent `name`.

    The comments API returns `author_name` / `author_id` / `author_display_name`.
    It does NOT return `author` — `c.get("author")` is always None, so an author
    filter written against it matches nothing and silently lets a proposer's own
    comment count as a review of their own item. Never read `c.get("author")`.
    """
    if not name:
        return False
    fields = (c.get("author_name"), c.get("author_id"), c.get("author_display_name"))
    return any(f and str(f) == str(name) for f in fields)


def review_status_of(item):
    """(review_status, reviewed_ok_by_me, ok_count) — from the proposal post's comments.

    `review_status` is `pending` | `ok` | `blocked`. The 3-tuple has exactly the shape
    ROLE-CPU's `review_scan()` returns: the resolved status, whether *I* authored one of
    the `[REVIEW-OK]`s, and how many independent `[REVIEW-OK]`s the item carries. Nothing
    in this file reads the last two — analysts never claim — and they are returned anyway
    so the two helpers cannot drift apart, and because the claim sites that DO read them
    (ROLE-CPU, HEARTBEAT) need the COUNT: they bar an item's SOLE reviewer, not every
    reviewer of it. Barring every reviewer is what permanently stranded the first seeds —
    all agents boot at the same instant, review the same few items, and would then all be
    barred from them, and agent names persist across rotations so nothing respawns out of
    it. A second independent `[REVIEW-OK]` supplies scrutiny the claimer is not relying on
    their own judgement for, which is the thing the two-person rule actually requires.

    A [REVIEW-BLOCK] DOMINATES: one block outweighs any number of OKs. A block is a
    claim that the experiment is wrong to run, and an OK is not an answer to it —
    if OKs could outvote a block, the one reviewer who spotted a real defect would
    simply be outnumbered and the eval would run anyway.

    The legacy-row resolution below is IDENTICAL, line for line, to ROLE-CPU's
    `review_scan()` and HEARTBEAT's `item_review_status()`. Three files resolving
    the same row three ways is how one agent finds an item claimable that another
    finds unreviewable — if you change it here, it is changed in all three or in
    none.
    """
    stored = str(item.get("review_status") or "").strip()
    if not stored:
        # Legacy rows predating `review_status`. Only an EXPLICIT
        # `discussion_pending: false` counts as cleared; absent-and-unknown is
        # `pending`, because there is no path to claiming an item that was never
        # reviewed. Defaulting the unknown case to `ok` would make any malformed or
        # hand-written row instantly claimable — the exact hole the removal of the
        # time-grace / starvation overrides was meant to close. (The old rationale
        # "absent is how a stacked re-test is queued" is obsolete: stack rows now
        # write `review_status: "ok"` explicitly at their creation site.)
        stored = "ok" if item.get("discussion_pending") is False else "pending"
    pid = item.get("proposal_post")
    if not pid:
        # UNREVIEWABLE, not clearable — and therefore `pending`, never `ok`. A review IS
        # a comment on `proposal_post`; with no post there is nowhere for one to attach,
        # so NOBODY can have reviewed this row whatever the row claims about itself. It
        # resolves `pending` here, the same answer ROLE-CPU's `review_scan()` and
        # HEARTBEAT's `item_review_status()` give, which keeps it out of the backlog
        # above and out of every claim loop. Handing back a stored `ok` instead would
        # make this file the one reader in the system that calls an unverifiable row
        # claimable — and a `{exp_id}_stack` re-test is not the exception it looks like:
        # ROLE-CPU's `requeue_stack` copies the PARENT's `proposal_post` onto the stack
        # row and only writes `"ok"` when it has one, so a stack row never reaches here.
        # Repairing this row is YOUR job as an analyst (post a real [PROPOSAL], fill the
        # field, or drop it) and repairing is not reviewing: you still never stamp
        # `review_status: "ok"` on it yourself.
        if stored == "ok":
            print(f"[REVIEW] {AGENT_NAME}: {item.get('id')} reads review_status=ok but "
                  f"names no proposal_post — unverifiable, so it is NOT claimable. "
                  f"Repair it (give it a real [PROPOSAL] post) or drop it from pending:.")
        return "pending", False, 0
    comments = requests.get(f"{API}/posts/{pid}/comments",
                            headers=HEADERS).json().get("data", [])
    proposer = item.get("proposed_by", "")
    blocks = oks = 0
    mine_ok = False
    for c in comments:
        if _authored_by(c, proposer):
            continue              # the author never reviews their own item
        lines = (c.get("content") or "").strip().splitlines()
        first = lines[0].strip() if lines else ""
        if first.startswith("[REVIEW-BLOCK]"):
            blocks += 1
        elif first.startswith("[REVIEW-OK]"):
            oks += 1
            # Recorded for the claim sites' sole-reviewer guard, never read here.
            mine_ok = mine_ok or _authored_by(c, AGENT_NAME)
    # An `unblocked_by` on the row is the ONE thing that clears a standing block —
    # without this check the comment scan re-blocks the item on every pass and the
    # analyst decision below is silently undone.
    if blocks and not item.get("unblocked_by"):
        return "blocked", mine_ok, oks
    if oks:
        return "ok", mine_ok, oks
    return "pending", mine_ok, oks
```

After you post your reviews, **write the resolved status back onto the affected
queue items** — a review nobody recorded leaves the item unclaimable exactly as if
you had never reviewed it. Use read-modify-PUT and the read-back from Step 5, and
**never PATCH**: dotted-key PATCH on nested frontmatter flattens `pending:` across
teams.

- `ok` → the item **stays in `pending:`** with `review_status: "ok"`. Claimable.
  **Append your own `AGENT_NAME` to the row's `reviewed_by:` list** (create it if
  absent, never duplicate a name). Your OK is what makes the item claimable at all,
  and it bars exactly one agent from claiming it: whoever is its **sole** reviewer.
  A second independent `[REVIEW-OK]` from anyone lifts even that, because the
  scrutiny then exists whether or not the claimer also reviewed — the two-person
  rule requires an endorsement the claimer is not relying on their own judgement
  for, not a growing list of disqualified agents. (Barring every reviewer instead
  is what strands seeds at cold start, when all agents boot at once and review the
  same few items; ROLE-CPU's claim guard is where this is enforced.) `reviewed_by`
  is how the next reader sees who already spent a review here. HEARTBEAT's boot
  pass writes this field, so a write-back that omits it silently erases what the
  boot pass recorded.
- `blocked` → the row keeps `review_status: "blocked"` and **moves out of
  `pending:` into a `blocked:` list**, carrying `blocked_reason` (the blocking
  comment's reasoning) and `blocked_by` (the reviewer's agent name). A blocked item
  must leave `pending:` — otherwise every claim loop in the run walks past it
  forever. It is never deleted: it is the record of what the system decided *not*
  to run, and only that record makes an unblock reviewable later. If the block was
  somebody else's and your own verdict was OK, name **them** and quote **their**
  comment — never overwrite a standing block with your own non-blocking reasoning.
- **Either way, pop the legacy twin:** `row.pop("discussion_pending", None)` on any
  row you write back. One item must not carry two truths — leaving the old field
  beside the new one is how a reader that still consults the fallback disagrees
  with a reader that does not. Pop it; never write it.

**A row you did not review, you do not touch.** In particular, never rewrite the
`review_status` of an item with no `proposal_post`: you could not have reviewed it,
so you have nothing to record. Repair it or drop it (rule 1 above) — those are the
only two moves.

#### Unblocking

A `[REVIEW-BLOCK]` is **not** cleared by someone posting another `[REVIEW-OK]`.
Clearing one is an **explicit analyst decision**, recorded on the queue item as
`unblocked_by` (your agent name) and `unblock_reason` (why the objection no longer
applies — the champion moved, the diff was corrected, the objection rested on a
misread). Move the item back into `pending:` in the same read-modify-PUT, and post
the reason as a comment on the proposal so the blocking reviewer can see and
contest it. An unblock with no stated reason is a silent override of a colleague's
finding and is not permitted.

### Step 0.2 — Stagnation Detection and Self-Regroup Trigger — REQUIRED

Analysts are responsible for detecting system-wide stagnation and
triggering a discussion round WITHOUT orchestrator intervention. This
is how the system self-organizes when hypotheses are exhausted.

Check these conditions at the start of every analyst cycle:

```python
# Count rotations since the most recent KEEP. A "rotation" is a
# complete cycle of the rotation schedule (all 9 non-monitor agents).
# Use experiment timestamps to bucket into rotations, or count
# workshop [RESULT] posts in batches of ~6.
recent_keeps = [r for r in recent_results if r.outcome == "KEEP"]
if recent_keeps:
    rotations_since_keep = estimate_rotations_since(recent_keeps[-1].timestamp)
else:
    rotations_since_keep = estimate_rotations_since_start()

# Check whether any team posted [HYPOTHESIS-FALSIFIED] since the last
# [DISCUSSION-TRIGGER] or [TEAM-REFORMED] post.
recent_posts = list_workshop_posts(limit=50)
falsified_since_reform = any(
    "[HYPOTHESIS-FALSIFIED]" in p.title
    and p.timestamp > last_reform_timestamp(recent_posts)
    for p in recent_posts
)

# Trigger ONLY on genuine plateau, not DISCARD-streaks post-KEEP.
# The old "keeps_in_last_10 == 0" rule fired after a big KEEP just
# because subsequent experiments happened to be DISCARDs — a normal
# occurrence after the system exhausts low-hanging fruit on an axis.
# Use rotations_since_keep >= 3 instead: three full rotation cycles
# without any new KEEP is a real plateau.
trigger_conditions = (rotations_since_keep >= 3) or falsified_since_reform

# Is there already an active [DISCUSSION-TRIGGER] ?
#
# The quorum block below is a VERBATIM COPY of HEARTBEAT Part 0 Check A2, down to
# the bytes. Keep it that way: if the two computations diverge by even one agent,
# HEARTBEAT and this file disagree about whether a discussion round is still open,
# and agents split between running experiments and discussing them.
# QUORUM SCALES WITH THE ROSTER. launch.py sizes the roster (--cpu / --analysts), so a
# hardcoded count silently becomes a supermajority on a small roster and deadlocks the run:
# 5-of-9 is a simple majority, but 5-of-6 is 83%. Compute it from the actual roster.
import math, os
# Count only real agent DIRECTORIES. os.listdir returns ANY entry, so a stray
# .DS_Store or a leftover file inflates the roster and silently raises the quorum.
_agents_dir = f"{FOCUS_ROOT}/agents"
_non_monitor = [a for a in os.listdir(_agents_dir)
                if not a.startswith(".")
                and os.path.isdir(os.path.join(_agents_dir, a))
                and "monitor" not in a]
DISCUSS_QUORUM = max(2, math.ceil(len(_non_monitor) / 2))   # 9 -> 5, 6 -> 3, 4 -> 2

active_trigger_exists = any(
    "[DISCUSSION-TRIGGER]" in p.title
    and age_rotations(p) <= 3
    and count_comments_matching(p.id, "[DISCUSS-DONE]") < DISCUSS_QUORUM
    for p in recent_posts
)
```

**If `trigger_conditions` is True AND no active trigger exists:**
You MUST post a `[DISCUSSION-TRIGGER]` thread now. Include:
- Why you're triggering (e.g., "0 KEEPs in last 10 exps, all teams
  reporting exhaustion")
- List of recent falsified hypotheses
- Open questions the discussion should address

Posting this trigger causes the next rotation's agents to enter
discussion mode via HEARTBEAT Check A2. No monitor invocation needed.

**If `trigger_conditions` is True AND an active trigger already exists:**
Proceed normally — the trigger will be picked up by HEARTBEAT in the
next rotation. Do NOT duplicate the trigger.

### Step 0.2b — Search-Class-Diversity Stagnation Trigger — REQUIRED

Step 0.2 catches stagnation by KEEP-count, but misses a real failure
mode: agents can mine the same axis-class (e.g., 5+ activation
variants in a row) faster than they exhaust it, so a single small
KEEP resets `rotations_since_keep` while the productive search space
has actually collapsed to one axis-class. The system runs experiments,
the predicate stays satisfied, no trigger fires — but every result is
DISCARD because the team is mining a vein that's already been mined.

This step fires a `[DISCUSSION-TRIGGER]` independent of KEEP count when
single-axis exhaustion is detected.

```python
# Pull recent RESULT entries (across all teams) and extract their
# axis field. If the result frontmatter has an explicit `axis` field
# use that; otherwise derive a coarse class from the exp_id prefix.
recent_results = read_recent_results(limit=10)  # via results/ listing
def coarse_axis_of(r):
    """A GUESS at the axis, for the diversity trigger ONLY.

    Deliberately NOT `axis_of` (Step 2). This one falls back to slicing the
    `exp_id`, which under the `exp_<team-prefix>_<mechanism>` id schema can return
    the team rather than the dimension. That is tolerable here and ONLY here,
    because the worst case is one spurious (or missed) [DISCUSSION-TRIGGER] — a
    cheap, reversible, human-read event. It is NOT tolerable for closure: Step 2's
    `axis_of` returns None on an untagged row precisely so a bad guess can never
    close a dimension nobody tested. Never call this one from a closure decision,
    and never give the two the same name again.
    """
    fm = r.get("frontmatter", {}) or {}
    if fm.get("axis"):
        return fm["axis"]
    # Fallback: derive from exp_id segments (e.g.,
    # exp_arch_act_relu_silu_T_2 -> "act"; exp_sch_warmup_ratio -> "warmup").
    parts = (r.get("exp_id") or "").split("_")
    return parts[2] if len(parts) > 2 else parts[-1]

recent_axes = [coarse_axis_of(r) for r in recent_results
               if r.get("outcome", "").upper() == "DISCARD"]
distinct_axes = len(set(recent_axes))

# Count pending queue items across teams that explicitly mark
# themselves as paired / cross-axis (multi-line diff, multiple
# axes touched, or `paired_with` / `cross_axis` field set).
pending = read_all_team_pending()
paired_pending = sum(1 for it in pending if
    it.get("paired_with")
    or it.get("cross_axis")
    or "paired" in (it.get("ambition") or "").lower()
    or "cross-axis" in (it.get("ambition") or "").lower()
    or len((it.get("diff") or "").split("\n")) >= 3)

# Fire when last 8+ DISCARDs concentrated in <=3 distinct axes AND
# no paired probes are pending across any team. Independent of KEEPs.
axis_mining_trigger = (
    len(recent_axes) >= 8
    and distinct_axes <= 3
    and paired_pending == 0
)
```

**If `axis_mining_trigger` is True AND no active trigger exists:**
Post a `[DISCUSSION-TRIGGER]` with body that EXPLICITLY frames the
problem as single-axis exhaustion. The trigger title must include
`(axis-mining)` so voters can distinguish it from KEEP-count triggers.
The body MUST:

- Name the over-mined axes (with counts) and the distinct-axis count
  in the last N DISCARDs.
- List axis-classes that have NOT been probed at the current champion
  (read from `knowledge/unqueued_axes.md`).
- Ask that PROPOSALs filed during the discussion window come from an
  **under-represented axis-class**, filed as ONE coherent named
  mechanism. Widening the search means reaching a *different* part of
  the space, not bundling several changes into one diff: a proposal
  still changes one thing, so that a DISCARD tells you what was wrong.
  Naming the mechanism (not the literal it tweaks) is what makes it a
  new axis rather than another turn of the same screw.

**Why this matters:** in past runs the winning mechanism has repeatedly come from a probe that
crosses two axes at once (e.g. rebalancing depth against step count) rather than from a
single-axis sweep. A run that stays in single-axis space will
consistently find local optima but miss cross-axis champions. The
KEEP-count predicate alone cannot distinguish "we're searching well
but the optimum is here" from "we've collapsed to a single axis-class
and need to widen the search."

### Step 0.25 — Team Formation / Reform (Last Analyst Only) — RUNS ON THE HEARTBEAT PART 2 (DISCUSSION) BRANCH — REQUIRED

**Where you are.** This step executes when the Mode Selector routed you to
**Part 2 (Discussion)**, not Part 4 — HEARTBEAT § 2b3 sends you here. It is
written in this file because the analyst owns it; it is the **only** writer of
`teams/roster.md` anywhere in the system. The monitor does not do this, and the
orchestrator does not do this.

Run it if MODE=discussion, you are the **alphabetically last analyst name that
has run in this rotation**, and either:

- **Cold start:** `teams/roster.md` is absent or its `teams` map is empty. An
  empty roster IS the trigger — the `DISCUSS_QUORUM` precondition below does
  **not** apply, because there is no prior structure to protect and every rotation
  spent without a roster is a rotation of no experiments. Form teams from whatever
  this round's `[DISCUSSION]` / `[RANKED]` threads support; three hypotheses with
  distinct falsifiable predictions is the target, two is acceptable if the round
  was thin.
- **Mid-run reform:** teams already exist AND the active `[DISCUSSION-TRIGGER]`
  has reached the roster-scaled `DISCUSS_QUORUM` of `[DISCUSS-DONE]` comments (`max(2, ceil(non_monitor_agents / 2))` — convergence reached), or the roster is already committed.

You are the designated team reformer. Form or re-form teams based on the
consensus that emerged from discussion:

```python
# Read all [HYPOTHESIS-*] and ranked proposals in the recent workshop
# to identify 3 hypotheses with distinct falsifiable predictions.
# Write new teams/roster.md to main workspace.
new_roster = {
    "teams": {
        hyp1_short_name: {
            "hypothesis": hyp1_description,
            "prediction": hyp1_prediction,
            "falsification": hyp1_falsification,
            "workspace_id": existing_or_new_ws_id,
            "members": rebalanced_agents,
        },
        # ... two more teams
    },
    "phase": "executing",
}

# HARD CONSTRAINT — every team MUST have at least 2 cpu-eval members.
#
# This is not a balance preference, it is a liveness requirement. An item's ok-count
# freezes the moment it is first reviewed (a non-`pending` item drops out of every
# agent's review backlog, so no second review ever arrives). A reviewer may not claim
# an item whose ONLY [REVIEW-OK] is its own. So on a team with a single cpu-eval agent,
# every item that agent reviews becomes claimable by NOBODY, forever: its own review
# bars it, no teammate exists to add the second review, and claim scope is own-team
# while review scope is global. Nothing anywhere resets a stamped `ok` back to `pending`.
_cpu_per_team = {t: [m for m in v["members"] if "_cpu" in m]
                 for t, v in new_roster["teams"].items()}
_starved = {t: m for t, m in _cpu_per_team.items() if len(m) < 2}
if _starved:
    raise AssertionError(
        f"roster would strand items: teams with <2 cpu-eval members: {_starved}. "
        f"Rebalance so every team has >=2, or form FEWER teams — a smaller number of "
        f"viable teams beats a team that cannot evaluate what it reviews."
    )

put_main_workspace_file("teams/roster.md", yaml_dump(new_roster))

# Post [TEAM-REFORMED] announcing new assignments, notifying all 9 agents.
```

This ends the discussion round — next rotation proceeds in execute
mode with the new team structure. Monitor is NOT required.

If you are not the alphabetically last analyst who has run, skip this
step; the last one will handle it.

**Cold-axis mandate on team reform.** When you reform teams, each new
team's initial queue MUST include ≥1 COLD axis — an axis with zero
prior experiments in the main workspace. Reason: after a
discussion-triggered reform, teams usually inherit the same exhausted
axis space under renamed hypotheses, producing more DISCARDs. A cold
axis is a genuinely new test. Before writing the new roster, walk
`knowledge/unqueued_axes.md` and verify each team gets at least one
entry marked `status: unqueued` assigned to its queue. If fewer than
3 cold axes remain in the ledger, post a `[SYSTEM-EXHAUSTED]` thread
instead of reforming — the search space is genuinely closed.

**The cold-axis mandate does not apply at cold start.** On the first formation
(empty roster) every axis is cold and the ledger may not exist yet — seed each
team's queue from this round's discussion threads and never withhold the roster
for a missing or thin `knowledge/unqueued_axes.md`. `[SYSTEM-EXHAUSTED]` is a
statement about a search space that has been mined, which cannot be true before
the first experiment has run.

**Seeded items are still queue items.** Write them through the Step 5 writer —
field gate *and* read-back confirmation included; seeding several teams at once is
exactly when a blind PUT loses items. Every seeded item needs `axis` **and**
`direction` **and** `value`. A cold-start item tagged with `axis` alone is unclaimable by contract —
ROLE-CPU makes the cpu-eval agent reject the claim and post a `[SUGGESTION]` —
so it sits at the head of the queue and blocks the items behind it. Seeding
half-tagged items does not get the run moving faster; it is the fastest way to
stall it on rotation one.

**Seed at least 2 items per team, never just one.** With `REVIEW_CAP = 1`, a
single seed per team lets one booting agent blanket-review that team's whole
queue; seeding more items than any one agent's cap is what spreads review
coverage across agents instead of concentrating it in whoever boots first. Do not
"simplify" this back to one seed per team.

Seeded items go in `review_status: "pending"` like everything else, and you may
not clear your own. That is not a delay you need to design around: the next
rotation's agents drain the review backlog on spawn (Step 0.1) before their own
work, so a seeded item is reviewed by the same agents who would otherwise have
been idle waiting for a roster.

### Step 0.3 — Hypothesis Check — REQUIRED

Your team is organized around a **falsifiable hypothesis**, not a
search-space axis. Before any other work, verify:

1. Read your team's `strategy.md` — it must have frontmatter with
   `hypothesis:`, `prediction:`, `falsification:`,
   `age_rotations:`, `supported_keeps:`, `refuted_discards:`,
   `rejected_test:`.
2. Read results from the last rotation. For every new team result
   classify it as:
   - **Supports hypothesis** (KEEP consistent with prediction) →
     `supported_keeps += 1`
   - **Supports hypothesis — race lost** (`NEAR_MISS` consistent with
     prediction) → `supported_keeps += 1`, exactly like a KEEP. A
     `NEAR_MISS` cleared BOTH gates (train margin and the held-out test
     gate) and was only denied promotion because another candidate
     landed on `champion.md` first. The science succeeded; the
     scheduling lost. It is **positive evidence** — it must NEVER
     increment `refuted_discards` or `rejected_test`.
   - **Refutes hypothesis** (DISCARD where prediction said it should
     KEEP) → `refuted_discards += 1`
   - **Refutes on generalization** (`REJECTED_TEST` where prediction
     said it should KEEP — it moved train but died at the held-out
     test gate) → `rejected_test += 1`. This counts as refuting: the
     hypothesis predicted a real improvement and the mechanism did
     not generalize. A `REJECTED_TEST` on an axis the hypothesis makes
     no prediction about is orthogonal — no change.
   - **Orthogonal** (result on an axis the hypothesis doesn't predict
     about) → no change
   - **`FAILED`** (infra/harness failure, unapplied diff, scope abort)
     → no change to any counter. It tested nothing and is not evidence
     for or against the hypothesis.
   - **Interaction result** (a `{exp_id}_stack` retest returning
     `DISCARD` or `REJECTED_TEST`) → evidence about the INTERACTION
     between that mechanism and the champion that beat it, **not** a
     refutation of the mechanism itself (it already cleared both gates
     once). Increment `refuted_discards` / `rejected_test` only if your
     hypothesis explicitly predicted the *combination* would improve;
     otherwise treat it as orthogonal and record it as an interaction —
     see "Stacked re-tests" below.
3. Increment `age_rotations`.
4. If `age_rotations ≥ 3` AND `supported_keeps == 0` AND
   `refuted_discards + rejected_test ≥ 3`: post
   `[HYPOTHESIS-FALSIFIED]` to the workshop with evidence (list each
   refuting result, marking which died on train and which died at the
   test gate). The monitor reports the same predicate in its `[AUDIT]`
   but never re-forms anything; re-formation happens in Step 0.25, run
   by an analyst on the next discussion round. Keep both counters
   current in `strategy.md` — they are the evidence that round acts on.

**Why `rejected_test` counts.** A team whose every experiment improves
train and then fails the held-out gate is producing zero champions. If
only `refuted_discards` could falsify, such a team would never be
re-formed — it would keep mining a mechanism class that moves train
metrics without generalizing. Persistent test-gate death is exactly the
signal that the hypothesis is wrong about *why* the mechanism helps.

**Why `NEAR_MISS` does not.** A `NEAR_MISS` is a candidate that passed
the train gate AND the held-out test gate but lost the promotion race —
either the pre-put gate aborted because an equal-or-better champion
landed while it was evaluating, or the race guard fired because
`champion.md` no longer records its `exp_id`. Nothing about the
mechanism failed. Counting it as refuting would falsify a hypothesis
that the evidence actually **supports**, and would do so precisely when
the team is producing promotable science faster than the roster can
promote it.

**Do NOT re-propose a `NEAR_MISS` mechanism.** The mechanism WORKED, and
the CPU-eval agent has already re-queued it as `{exp_id}_stack` so the
identical change is re-tested on the NEW champion. Re-proposing it
duplicates a queued item and burns a rotation. Treat a `NEAR_MISS` in
your dedup check exactly as you would treat a queued pending item: it is
already in flight. What a `NEAR_MISS` *does* license is proposing the
**next** step along the same mechanism — a bracket or an extension —
once the `_stack` retest reports.

**Stacked re-tests (`{exp_id}_stack`) — read this before you close a
family.** The CPU-eval agent handles these in its Step 3b: a `_stack`
item is the same change re-applied on top of the champion that won the
race. Three things follow, and getting them wrong closes a working
family on a false read:

- **It is a genuinely NEW experiment, not a repeat.** The base is a
  *different* champion. The mechanism may interact with the winner's
  change — same-axis overlap, a saturated effect, or a genuine
  conflict — so the combination can be worse than either alone even
  though each was promotable on its own base.
- **A `_stack` `DISCARD` or `REJECTED_TEST` is evidence about the
  INTERACTION, not a refutation of the original mechanism.** The
  original already cleared both gates against its own base; that
  measurement stands. Write the result up as "mechanism X does not
  stack on champion Y" — name both — do NOT record it as "X refuted",
  and do NOT treat it as licence to abandon X on a future, different
  base. **It does still count one toward the closure counter on X's
  axis** (Step 2), exactly like every other result on that axis: it
  spent a real eval slot and returned real evidence, and an outcome
  that counts toward nothing would let an axis emit stack retests
  forever without ever closing. "Interaction" is how you *read* the
  result, not an exemption from *counting* it.
- **`prior_train_fitness` / `prior_test_fitness` on a stacked queue item
  are CONTEXT ONLY** — they are what the mechanism scored against its
  *old* base, recorded so you can see it worked. They are never this
  experiment's results, never comparable to the current anchors, and
  must never be copied into a result file, a ledger row, or a claim of
  improvement. The only numbers that count for a `_stack` item are the
  ones its own train and test evals return.

Your proposals this cycle MUST be consistent with the hypothesis
(unless you are mid-falsification, in which case exploration is
allowed). Proposals from other teams are visible in the cross-team
workshop — you are NOT restricted to an axis. The point is to
triangulate: the same experiment (e.g., TOTAL_BATCH_SIZE halving)
gets evaluated through your team's LENS — does it support your
hypothesis? Different teams may propose the same axis for different
reasons; that is the intended form of collective reasoning.

### Step 0.5 — Deterministic Evaluation (No Noise Floor) — NOTE

Evaluation is deterministic (sigma=0, verified end-to-end): re-running
the same code produces the identical metric. There is no measurement
noise floor, no significance band, and no multi-seed confirmation. Any
strictly-better valid result is real. Do NOT queue baseline_seed /
noise-floor probes, do NOT read or maintain `noise_floor.md` /
`noise_floor_data.md`, and do NOT apply a noise band when deciding
whether a delta is signal. A non-improving valid delta is simply a
DISCARD; an improving valid delta on train is a *provisional* keep that
must still clear the held-out test gate before it becomes a KEEP
(Step 3a).

### Step 0.7 — Discussion-Backlog Ledger — REQUIRED

Discussion rounds surface 20+ axes, but only 4-8 end up in queues
because nobody systematically walks the backlog. Fix: maintain a
durable ledger of every axis mentioned in any [DISCUSSION] /
[GAPS] / [CONSTANTS] / [RANKED] post, so analysts must decide
what to do with each one.

**Ledger location:** `knowledge/unqueued_axes.md` in the main
workspace (shared across teams — one canonical backlog).

**Ledger schema:**

```
axis              | direction | suggested_value | mentioning_posts     | status   | last_touched
EMBEDDING_LR      | increase  | 0.8             | post_a1b2, post_c3d4 | unqueued | 2026-04-17
UNEMBEDDING_LR    | any       | ?               | post_e5f6            | unqueued | 2026-04-17
RoPE base         | decrease  | 1000            | post_g7h8            | tested   | 2026-04-18
```

Status: `unqueued` | `queued` | `tested`.

**Initialization (first analyst cycle only):** if the ledger does
not exist, walk every workshop post tagged [DISCUSSION], [GAPS],
[CONSTANTS], [RANKED], [DYNAMICS]. For each distinct axis
mentioned, add one row. If the same axis appears with conflicting
directions, create two rows (one per direction). Do NOT prune at
this stage — err on the side of inclusion.

**Maintenance (every cycle):** before proposing, update statuses:
- Set `queued` for axes now in any team's queue.md
- Set `tested` for axes with results in main workspace
- Leave `unqueued` otherwise

If you add a new gap during a normal cycle via a [GAPS] post, also
append it to the ledger.

### Step 0 — Discover Current State

```python
# --- Essential anchors (always read) ---
champ_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/champion.md",
                         headers=HEADERS).json()
champ = parse_frontmatter(champ_raw)

queue_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                         headers=HEADERS).json()
queue = parse_frontmatter(queue_raw)

# --- Discover everything else via LIST (cheap, no content) ---
main_files = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files",
                          headers=HEADERS).json()["files"]
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json()["files"]

# DECIDE: scan paths and timestamps. Read files relevant to your task:
#   - Recently updated results (new data to analyze)
#   - Team dead_ends, non_generalizable, strategy (avoid redundant proposals)
#   - Knowledge files from other teams (cross-pollination)
#   - Any new files created since your last cycle
# See Part 4 (Team Coordination) § File Discovery Protocol for the full pattern.
```

### Step 1 — Audit Recent Results

```python
# Search main workspace for recent experiment results
results = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/search?q={MY_TEAM}",
                       headers=HEADERS).json()
```

Analyze: which mechanisms worked? Which axes are running up a closure count with
nothing to show for it (Step 2)?

### Step 1a — Deterministic Deltas (No Significance Band) — NOTE

Evaluation is deterministic (sigma=0): any strictly-better valid result is
real and any recorded delta is exact. There is no measurement noise floor
and no significance band — do NOT treat a small non-improving delta as
"indistinguishable from no change," do NOT read `noise_floor.md`, and do
NOT require multiple data points on an axis to trust a delta. A DISCARD
delta is the true response at that point; a KEEP is a true improvement.
Fine-bracket refinement is still a legitimate search move where the shape
warrants it — judge it on whether the trend can plausibly reach a strict
improvement, not on a noise band.

### Step 1b — KEEP Followup Harvest — REQUIRED

KEEP result files routinely contain explicit `## Followup` or `## Follow-up`
sections written by the agent that just set a new champion. These are the
highest-value proposals in the system because they are authored in-context
immediately after the KEEP, and they encode the author's fresh intuition
about what to try next. But they are lost across rotations when analysts
don't re-read prior KEEP results.

```python
# Grep recent result files for `## Followup` sections on KEEPs
recent_results = [f for f in main_files if f["path"].startswith("results/")][-30:]
for rf in recent_results:
    content = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/{rf['path']}",
                           headers=HEADERS).json().get("content", "")
    if "KEEP" not in content:
        continue
    if "## Followup" not in content and "## Follow-up" not in content:
        continue
    # Extract the followup bullets. For each bullet, check whether an
    # equivalent experiment already exists in the log or pending queue.
    # If not, rebase to the current champion/baseline and add to your
    # Step 4 proposal batch.
```

**Why this matters:** an unharvested followup bullet can sit in a KEEP
result file for many champion versions while cascading changes build around
the missing data point. Harvest every cycle; a followup written N champions
ago that was never queued is a system-level defect, not a historical
curiosity.

### Step 1b2 — Post-KEEP Inductive Reasoning — REQUIRED after any champion update

If the champion has changed since your last cycle, you MUST answer these
three questions before proposing anything new:

1. **What property of the KEEP made it work?** Not just "what changed" but
   WHY did the change improve the metric? Identify the underlying
   mechanism — e.g., "more training steps per fixed budget," "better
   gradient signal per step," "reduced memory pressure allowing larger
   batch," "better loss landscape geometry."

2. **What other untried changes share that property?** List 3-5 concrete
   experiments that would test the same underlying mechanism through a
   different code change. For example, if the KEEP worked by increasing
   training steps, other step-increasing changes include: smaller
   sequences, faster attention kernels, removing expensive operations,
   reducing warmdown ratio, etc.

3. **At least 1 of your 2 proposals this cycle must target the same
   property via a different mechanism.** This ensures the system follows
   productive leads rather than scattering across unrelated axes after
   each KEEP.

Write your answers in a `[ANALYSIS]` workshop comment on the KEEP's
`[RESULT]` thread so other teams can see the reasoning and propose
their own variants of the productive property.

**Why this matters:** without inductive reasoning from KEEPs, agents
propose the next experiment by reading champion code (deductive). But
the most informative signal in the system is "what worked and why" —
the KEEP itself. A KEEP that succeeded because it increased training
steps should generate a cascade of step-increasing proposals across
all teams. A KEEP that succeeded because it improved per-step quality
should generate quality-improving proposals. Currently this reasoning
happens implicitly if at all; making it explicit and required ensures
the system follows productive leads.

### Step 1c — Baseline Coverage Audit — REQUIRED

Champion parameters that were never explicitly sweep-tested get treated as "sacred" and are never questioned. You MUST audit coverage periodically to catch untested assumptions inherited from the baseline config.

```python
import re, json
from pathlib import Path

# 1. Extract ALL named numeric assignments from champion code
champion_code = open(f"{FOCUS_ROOT}/champion/algo.py").read()

# Layer 1: Top-level named constants (UPPER_CASE = value)
layer1 = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*[:=]\s*([0-9]+\.?[0-9]*)", re.MULTILINE)

# Layer 2: Dataclass / class fields (name: type = value)
layer2 = re.compile(r"^\s+(\w+):\s*\w+\s*=\s*([0-9]+\.?[0-9]*)", re.MULTILINE)

# Layer 3: Named assignments inside functions (name = float_value)
layer3 = re.compile(r"^\s+(\w+)\s*=\s*([0-9]+\.[0-9]+)", re.MULTILINE)

config_params = {}
for pattern in [layer1, layer2, layer3]:
    for m in pattern.finditer(champion_code):
        name = m.group(1)
        # Skip obvious non-parameters: loop vars, indices, counters
        if name in ("i", "j", "k", "n", "x", "y", "step", "idx", "count", "total"):
            continue
        config_params[name] = m.group(2)

# 2. Collect experiment names from the canonical log.
#    Each line of experiments.jsonl is ONE experiment record (flat, not
#    a wrapper object holding an "experiments" list). Canonical keys:
#      ts, cycle, exp_id, team, agent, axis, direction, value,
#      fitness, is_valid, test_fitness, test_is_valid, outcome, delta, post_id
#    outcome ∈ KEEP | NEAR_MISS | DISCARD | REJECTED_TEST | FAILED.
#    Early rows may be missing keys — always use .get() with a default.
experiments = set()
log = Path(f"{FOCUS_ROOT}/logs/experiments.jsonl")
if log.exists():
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        exp_id = (rec.get("exp_id") or "").lower()
        if exp_id:
            experiments.add(exp_id)

# 3. For each parameter, check if its name appears in any experiment ID
untested = []
for param in config_params:
    token = param.lower()
    if not any(token in exp for exp in experiments):
        untested.append((param, config_params[param]))

# 4. Write findings to team workspace
if untested:
    # Write knowledge/baseline_coverage.md listing untested parameters.
    # Let the agent decide which are worth proposing — don't filter here.
    pass
```

**Three layers of audit (don't skip any):**
1. **Top-level constants** — the obvious tuning knobs
2. **Class/dataclass fields** — these define the fundamental structure. They are often treated as sacred but are actually the highest-impact parameters. A large change to a structural field is typically worth more than all fine-tuning combined. Never assume these are optimal just because they're in a config class.
3. **Function-body literals** — hidden inside helper functions, invisible to global search. These are the most commonly missed because no one thinks to look inside functions for tunable values.

Treat untested parameters as **hypotheses, not facts**. A parameter sitting at its default value is a choice that was never validated. When you write your proposals in Step 4, prefer mechanisms that interrogate untested parameters over variations on already-swept parameters.

**What counts as "tested":**
- Sweep-tested with 3+ values covering both directions from the current value
- Explicitly confirmed as robust across an architecture change (re-sweep)

**What does NOT count as tested:**
- Mentioned in a strategy doc
- Assumed to be optimal because nobody ever touched it
- Part of a confounded experiment (e.g., changed alongside another parameter)

If a parameter has no coverage and no obvious reason to be left alone, it's a legitimate candidate for a proposal — even if your team's dimension seems exhausted. Untested baseline parameters often hide big wins because everyone assumed the default was correct.

**Output requirement — regenerate only when the champion changed.** Write the full
enumeration to your team's `knowledge/baseline_coverage.md` when `champion.md`
records an `exp_id` the file's frontmatter does not yet name (record that `exp_id`
in the frontmatter as `covers_exp_id:` when you write it). Otherwise the champion
code is byte-identical to the last enumeration and re-deriving the table teaches
nothing. The file must contain a literal table with columns:
`parameter | current_value | tested? | result summary`, and nothing else beyond a
one-line header — the table is the deliverable, prose around it is not. The table
is what makes untested constants visible to CPU-eval agents and other analysts.
Constants that "look like they shouldn't be changed" (e.g., numeric constants
inside math functions, magic numbers in optimizer code, hardcoded frequencies or
window sizes) are often the highest-value targets because nobody questions them.

**Cross-reference against result files — but bounded.** A parameter name may not
appear in the experiment ID but may have been tested under a different name.
`logs/experiments.jsonl` (with its `axis` / `value` fields) resolves most of these
without opening anything. Beyond it, read **at most 10 result-file bodies per
cycle** — pick the ones whose axis is closest to the parameters you actually plan
to propose on — and mark the rest `tested?: unknown` rather than reading further.
Only mark "tested" if a source you actually read explicitly varied this constant.

**Budget precedence.** This step is bookkeeping and it competes with your real
deliverable. Posting your `[PROPOSAL]`s (Step 4) and getting them into the queue
(Step 5) takes precedence over this table and over every other table in this
file. If the 50-call budget is running out, ship the proposals and leave
`baseline_coverage.md` stale — a stale table costs a cycle of visibility, an
unposted proposal costs the whole rotation's eval slots. This is the documented
#1 failure mode for this role (see "Rule 2, restated"): analysis written, nothing
posted, queue unrefilled.

### Step 1d — Team-Structure Audit — REQUIRED when the structural gate is open

**Gate — check this first; if it is shut, Step 1d is a no-op this cycle.** Run
the audit only when at least one of these holds:

- **The champion has not moved for ≥3 rotations** (no new `exp_id` in
  `champion.md` across the last three rotations), OR
- **an untriaged `[STUCK]` thread is open** from a prior rotation.

Structural reorganization is expensive: it orphans `strategy.md`, `dead_ends.md`
and every team queue, and three analysts auditing every rotation churn the roster
faster than any hypothesis can be tested. A moving champion is proof the current
structure works — do not restructure it.

**Cap: the system carries at most ONE open structural thread at a time.** Before
posting any `[DIMENSION-*]` / `[REGROUP]`, search the workshop for an open one. If
one exists, do not post a second — comment on the existing thread instead (a
substantive endorsement or objection is what unblocks enactment).

**When the gate is open, run the audit regardless of your team's state.** Team
dormancy, STANDBY, partial-wake, wake-for-one, and pre-registered
contingency modes do NOT skip this audit — they are orthogonal to it.
The point of the audit is exactly to notice when the team-structure
itself is what's wrong, and a dormant team is precisely the team most
likely to need structural reorganization. Do this audit BEFORE branching
into any mode-specific behavior (wake handling, standby logic, dormancy
commentary). If you find yourself thinking "I'm dormant this cycle, I'll
skip Step 1d," stop — run the audit first, then decide what to do after.

Analysts are the natural home for noticing organizational problems because
you read across teams. Check whether the conditions in
HEARTBEAT § *Team Evolution Protocol* hold:

- Are all teams currently falsified? If so, this may mean the team
  dimensions don't span the productive axis — candidate for a
  `[DIMENSION-NEW]` proposal.
- Is a team dormant ≥5 cycles with persistently-flagged cross-team gaps?
  Candidate for `[DIMENSION-MERGE]` (retire dormant team, replace with
  the gap's owner) or `[REGROUP]` (redirect its agents).
- Have 3+ [DISCUSSION] threads converged on the same axis that no team
  owns? Candidate for `[DIMENSION-NEW]`.
- Is a `[STUCK]` thread open and untriaged from a prior rotation? If so,
  that is the persistent signal the DIMENSION-NEW protocol was designed
  to catch; a follow-on `[DIMENSION-NEW]` or `[DIMENSION-MERGE]` post is
  required if a non-proposer has not yet posted one.

**Two teams converging on the same mechanism is not a structural fault** and is
never grounds for a `[DIMENSION-MERGE]`. Teams are lenses, not territories: the
same experiment evaluated through two hypotheses is the intended form of
collective reasoning (Step 0.3). Merge on *dormancy* or *falsification*, never on
agreement.

**If any condition holds AND no structural thread is already open, you MUST post
the formal `[DIMENSION-NEW]` / `[DIMENSION-SPLIT]` / `[DIMENSION-MERGE]` /
`[REGROUP]` workshop thread THIS CYCLE.** (If one is already open, the cap
applies: comment on it instead — that discharges your obligation.) Writing a `suggestions/*.md` file or a comment about the
condition is NOT sufficient and does NOT discharge the obligation.
Suggestion files do not trigger enactment; only formal workshop posts
with the correct tag start the endorsement window. The protocol exists
specifically to be used; deferring action under the assumption "someone
else will post it next cycle" has been the dominant failure mode and
must stop with you.

If a structure proposal is already in flight (posted in a recent
rotation) and you are a non-author in an affected team, write a
substantive endorsement or objection comment — passing the endorsement
bar is what unblocks enactment. Do NOT silently skip; structure
proposals are the highest-leverage action available in the system.

#### Step 1d.5 — Enact the open [DIMENSION-MERGE] thread — REQUIRED

This step runs **even when the Step 1d gate is shut** — an unenacted thread must
never be left dangling. By the one-open-thread cap there is at most one such
thread; find it and check whether the **endorsement bar is met** (if two exist
because of a race, enact the older one and note the duplicate in a comment):

- ≥2 substantive endorsement-or-objection-resolution comments from
  distinct agents who are NOT the proposer (affected-team and
  cross-team agents both count; [STATUS-NOTE] / [SUGGESTION] posts
  cited in the merge body also count if they predate the merge thread
  and supported the action), AND
- 0 unresolved objection comments at the time you check, AND
- the thread is ≥1 rotation old (≥1 hour since post-time is sufficient).

**You MUST enact the merge this cycle if ALL of the following hold:**

1. The bar above is met.
2. teams/roster.md still reflects pre-merge state.
3. You are NOT the proposer of this merge thread.
4. You are the alphabetically last analyst running THIS CURRENT
   rotation (compare `AGENT_NAME` against the names of every other
   analyst whose session_count incremented today; if you're the last
   one, you're it).

You do NOT need to be a member of the dissolving team. Affectedness
applies to who-can-endorse, not who-can-enact. A non-affected analyst
can and should enact — that prevents the dissolving team's analysts
from being unable to discharge their own merge.

**Do not defer.** Phrases like "the alphabetically-last-analyst rule
applies to next rotation" or "I'm non-affected so I'll skip" are bugs.
If conditions 1-4 hold THIS cycle, enactment is mandatory THIS cycle.
A pending merge wastes one eval slot per rotation it remains unenacted.

```python
# 1. Read current roster
roster_raw = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
                          headers=HEADERS).json()
roster = parse_frontmatter(roster_raw)
roster_version = roster_raw.get("version", 0)

# 2. Apply the merge as described in the [DIMENSION-MERGE] body
#    - Drop the dissolved team from roster["teams"]
#    - Reassign its members per the proposal's reassignment block
#    - Bump roster["phase"] timestamp / version
new_roster = apply_merge(roster, dim_merge_post.body)

# 3. Atomic PUT with If-Match
requests.put(f"{API}/workspaces/{MAIN_WS_ID}/files/teams/roster.md",
    headers={**HEADERS, "If-Match": str(roster_version)},
    json={"content": yaml_dump(new_roster)})

# 4. Post [TEAM-REFORMED] notifying the affected agents and linking the
#    [DIMENSION-MERGE] thread that authorized this enactment.
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP,
    "title": f"[TEAM-REFORMED] enacted [DIMENSION-MERGE] {dim_merge_post.id}",
    "content": f"Roster updated. Dissolved: {dissolved_team}. "
               f"Reassignments: {reassignments}. "
               f"Authorized by endorsements: {endorsement_post_ids}.",
    "notify_agents": affected_members,
    "tags": ["type:reform", f"merged:{dissolved_team}"]
})

# 5. Mark the dissolved team's queue.md as archived (frontmatter
#    `team_status: dissolved`) so any CPU-eval agent cycled into it via
#    a stale launch sees the dissolution and routes to the new team.
```

**If conditions 3 or 4 fail** (you are the proposer, OR another analyst
is alphabetically last in this rotation), skip — the next eligible
analyst will enact. **If condition 1 or 2 fails** (bar not met, or
roster already reflects merge), the step is a no-op.

**Why the alphabetically-last rule:** prevents two analysts from racing
to rewrite the same roster.md (If-Match would catch it but produces
spurious 409 noise and unclear ownership). Same arbitration rule as
Step 0.25 cold-start bootstrap.

**Why:** this breaks a deadlock where neither the affected nor the non-affected
analysts enact the merge — so the enactor is explicitly NOT required to be affected.

If none of the conditions hold, this step is a no-op — proceed to Step 2.

### Step 2 — Prune Dead Ends (Close Mined-Out Axes)

**The unit of closure is the queue item's `axis` string, exactly** — not the `exp_id`, and never a
prefix of it. Under the mandated `exp_<team-prefix>_<mechanism>` id schema the leading tokens name
the **team**, so keying closure on them collapses one team's whole queue into a single "family" and
closes everything that team owns on the strength of a handful of unrelated DISCARDs. A
`{exp_id}_stack` retest inherits its parent item's axis; it is the same mechanism on a new base, not
a new dimension.

**The counter:**

    closure_count(axis) = DISCARDs + REJECTED_TESTs on that axis
                          SINCE the most recent KEEP or NEAR_MISS on that axis
                          (all-time if the axis has never had one)

- A **`KEEP` resets the counter to 0**, and so does a **`NEAR_MISS`**: it cleared both gates and lost
  only the promotion race, which is proof the axis still produces promotable science.
- **`REJECTED_TEST` counts toward the threshold** — it spent an eval slot and produced no champion —
  but it is recorded in `non_generalizable.md`, **never** in `dead_ends.md`. "Didn't work" and
  "didn't generalize" are different diagnoses and an analyst reading the two files must be able to
  tell them apart.
- **`FAILED` never counts.** It tested nothing.
- A **`{exp_id}_stack` retest counts exactly like any other result on its axis**: a stack DISCARD or
  REJECTED_TEST adds 1, a stack KEEP or NEAR_MISS resets the counter to 0. It inherits its parent's
  axis (that is what stops it being mistaken for a NEW dimension) — it does **not** inherit an
  exemption. See below.

**The threshold and the action:**

    closure_count(axis) >= 10  ->  close the axis

Closing means **both** of these, in one step and neither one optional: record the axis in
`dead_ends.md` with its name and the count, **and remove that axis's items from `pending:`** (they
move into `blocked:` with the closure as their `blocked_reason` — unclaimable, but not deleted, so a
later reopen has something to read). A closure recorded in `dead_ends.md` while the items stay
claimable is not a closure; it just spends more eval slots on a dimension you already declared mined
out. Ten results on one axis with no KEEP and no NEAR_MISS among them is evidence the axis is mined
out. Two or three is the ordinary shape of search — closing on that many discards axes that were
never probed properly.

**A closed axis reopens ONLY by an explicit analyst decision with a written reason recorded in
`dead_ends.md`.** Never automatically, never on elapsed time.

**You MUST write dead_ends.md to the team workspace** when an axis is closed. Other agents discover
it via LIST and skip that axis in their dedup check and in review (Step 0.1).

**Before you close an axis, read the DISCARDs' `fitness` alongside `is_valid`.** An energy-invalid
run reports its honest measured `fitness`, so DISCARDs are no longer interchangeable:

- `is_valid == 0` with `fitness` **well below** the champion = the mechanism is genuinely fast and
  its only defect is under-relaxation (`mean_rel_energy` just under `1.0`). This is the most
  promising kind of negative result in the whole ledger. It still counts toward the threshold — the
  arithmetic is unchanged — but when you close such an axis, say so explicitly in `dead_ends.md`
  and record the two numbers, because the *energy leak* is the open problem, not the mechanism. A
  variant that fixes the leak is a legitimate new proposal, not a rerun of a dead end.
- `is_valid == 0` with `fitness` **at or above** the champion = losing on both counts (slower *and*
  under-relaxed). An ordinary DISCARD; count it toward the axis and move on without ceremony.
- `fitness == 1000.0` = no usable trajectory (infra error, non-convergence, or blown budget). Infra
  errors are `FAILED`, not `DISCARD`, and never count here at all.

**For a `1000.0` row, read the result file's TRAIN `per_molecule` section before you count it against
the axis.** Every TRAIN evaluation that produced molecule results reports this breakdown, invalid
runs included, and it changes what a failed experiment means:
- `non_converged` lists a HANDFUL of molecules => the mechanism is **unguarded, not dead**. It works
  on 247 of 250 and the open question is what those few have in common. Do not treat that as evidence
  against the mechanism; the right next experiment is the same mechanism with a condition that
  excludes them, chosen on PHYSICAL grounds (curvature, coordination, ring flexibility, size-scaled
  thresholds) and never by recognising which benchmark molecule it is — that is forbidden, see
  TASK.md. Say so in the queue item so the retry is not mistaken for a rerun of a dead end.
- `non_converged` lists most or all molecules => the mechanism genuinely breaks the trajectory. An
  ordinary DISCARD.
- `nearest_energy_gate` shows WHICH molecules dragged an energy failure down, rather than the mean
  that hides it; `worst_by_rel_steps` shows where the step budget actually goes, which is the only
  place a speedup can come from.

Aggregate numbers cannot tell a mechanism that fails on 3 molecules from one that fails on 250. If
you close an axis on the first kind, you have discarded a working idea on the strength of a summary
statistic.

Never treat a low `fitness` on an invalid row as a partial win — it did not pass, and it cannot be
promoted. It is a diagnostic about *where* the mechanism failed, nothing more.

`REJECTED_TEST` outcomes are recorded in `non_generalizable.md` (written
by the CPU-eval agent), never in `dead_ends.md` — but they DO count toward
the closure counter, one each, exactly like a DISCARD. No single result
closes anything on its own: one `REJECTED_TEST` is one of ten. What it
tells you that a DISCARD does not is in Step 3a.

`NEAR_MISS` outcomes are **positive** evidence: both gates passed and only
the promotion race was lost. They **reset the counter to 0** and are
written to **neither** `dead_ends.md` nor `non_generalizable.md`. An axis
with a `NEAR_MISS` on it is an axis that WORKS — closing it would be the
opposite of what the evidence says. Its `{exp_id}_stack` retest is
already queued against the new champion.

A non-improving **`{exp_id}_stack`** result (`DISCARD` *or* `REJECTED_TEST`)
**counts one toward its parent axis's closure counter, exactly like any other
result on that axis** — and it is filed exactly like any other result too: a stack
DISCARD to `dead_ends.md`, a stack REJECTED_TEST to `non_generalizable.md`, both
written by the CPU-eval agent in its Step 7c. **It is additionally interaction
evidence**, and that is what you add on top: record in your analysis that the
mechanism does not stack on the champion that beat it, naming BOTH the mechanism
and that champion. "Also an interaction" is a richer reading of the result, not a
lighter one.

**Why it counts** (this used to be carved out, and the carve-out was wrong): a
stack retest is a real experiment. It consumes a real eval slot and returns real
evidence about whether the mechanism survives on top of the new champion. Exempting
it also opens an unbounded loophole — an axis that keeps emitting stack retests
would accumulate failures forever and never close, which is precisely the mined-out
state the counter exists to detect. What the `_stack` suffix buys you is the axis
inheritance above, nothing more.

`FAILED` outcomes (infra/harness failure, unapplied diff, scope abort)
**tested nothing**. They must never be written to `dead_ends.md` or
`non_generalizable.md` and must never count toward the closure counter —
the queue item is re-queued, not closed out. Skip them entirely when
counting.

Because evaluation is deterministic (sigma=0), recorded DISCARD deltas are
exact — do NOT re-triage existing dead_ends as "noise-contaminated." A
closed axis stays closed on its real measured deltas.

```python
import yaml as _yaml
from collections import defaultdict
from datetime import datetime, timezone

# Read existing dead_ends.md (or start fresh)
de_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                      headers=HEADERS).json()
existing_de_content = de_raw.get("content", "")
existing_de_version = de_raw.get("version", 0)

CLOSURE_THRESHOLD = 10

# The family key is the AXIS, exactly as the queue item / result frontmatter writes
# it. `axis_by_id` maps exp_id -> axis, and you BUILD it — from this team's queue.md
# `pending:` / `completed:` / `blocked:` rows plus the result frontmatter from
# Step 1. It is a lookup of what someone actually tagged, never a derivation:
# NEVER key on an exp_id prefix — with `exp_<team-prefix>_<mechanism>` ids that is
# the TEAM, so it closes the team, not the dimension. If a row is in neither source,
# it is untagged and it is skipped; that is the correct outcome, not a gap to patch.
q_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                     headers=HEADERS).json()
q_fm = parse_frontmatter(q_raw)
axis_by_id = {}
for _row in ((q_fm.get("pending") or []) + (q_fm.get("completed") or [])
             + (q_fm.get("blocked") or []) + list(team_results or [])):
    if not isinstance(_row, dict):
        continue
    _rid = str(_row.get("id") or _row.get("exp_id") or "").strip()   # queue rows use
    _rax = str(_row.get("axis") or "").strip()                       # `id`, results `exp_id`
    if _rid and _rax:
        axis_by_id.setdefault(_rid, _rax)

def axis_of(exp):
    eid = str(exp.get("exp_id") or exp.get("id") or "").strip()
    ax = (str(exp.get("axis") or "") or axis_by_id.get(eid) or "").strip()
    if ax:
        return ax
    if eid.endswith("_stack"):
        # A stack retest has no axis of its own: it inherits its parent's.
        parent = eid[: -len("_stack")]
        return (axis_by_id.get(parent) or "").strip() or None
    return None      # untagged row — not attributable, so skip it. Guessing an
                     # axis here closes a dimension nobody actually tested.

def _ts_key(e):
    """Oldest first; a row with NO timestamp sorts LAST, never first.

    House rule system-wide, and it bites hardest right here: this fold RESETS on
    KEEP / NEAR_MISS, so an untimestamped row sorted to the FRONT is attributed to
    the pre-reset era and silently dropped from the count — an axis then sits
    permanently one or two under the threshold and never closes. `str()` because a
    `ts` that parsed as a date object would otherwise raise TypeError mid-sort.
    """
    ts = str(e.get("ts") or "")
    return (ts == "", ts)

# closure_count resets on KEEP / NEAR_MISS, so this walk MUST be oldest-first.
# `discards` / `rejected_test` are the BREAKDOWN of `closure` and reset with it;
# `interactions` is a cumulative LABEL on top (stack retests), not a diversion —
# every one of them is already counted in `closure`.
counts = defaultdict(lambda: {"closure": 0, "discards": 0, "rejected_test": 0,
                              "interactions": 0, "last_reset": None})
for exp in sorted(team_results, key=_ts_key):  # from Step 1
    ax = axis_of(exp)
    if not ax:
        continue
    outcome = str(exp.get("outcome") or "").strip().upper()
    is_stack = str(exp.get("exp_id") or exp.get("id") or "").endswith("_stack")
    if outcome in ("KEEP", "NEAR_MISS"):
        # Promotable science on this axis. A KEEP set a champion; a NEAR_MISS
        # cleared BOTH gates and lost only the promotion race. Either one proves
        # the axis is still producing, so the count starts over at zero — and a
        # `_stack` KEEP / NEAR_MISS resets it exactly like any other success.
        counts[ax].update(closure=0, discards=0, rejected_test=0)
        counts[ax]["last_reset"] = exp.get("exp_id") or exp.get("id")
    elif outcome not in ("DISCARD", "REJECTED_TEST"):
        # FAILED (infra — tested nothing), blank, or an outcome this file does not
        # know. Skip it. It must not fall through to the DISCARD arm: a row whose
        # outcome you cannot read is not evidence that an axis is mined out.
        continue
    else:
        # DISCARD or REJECTED_TEST — one toward this axis's closure count. NO
        # exemption for `{exp_id}_stack`: it spent a real eval slot and returned
        # real evidence, and exempting it would let an axis emit stack retests
        # forever without ever closing.
        counts[ax]["closure"] += 1
        if outcome == "REJECTED_TEST":
            # Counts toward closure exactly like a DISCARD, but files to
            # non_generalizable.md (Step 3a) — never to dead_ends.md.
            counts[ax]["rejected_test"] += 1
        else:
            counts[ax]["discards"] += 1
        if is_stack:
            # Interaction evidence AS WELL, not INSTEAD: write it up as "mechanism
            # does not stack on champion Y". This tally is for your analysis prose;
            # the closure arithmetic above already counted the row.
            counts[ax]["interactions"] += 1

newly_closed = [ax for ax, c in counts.items()
                if c["closure"] >= CLOSURE_THRESHOLD]

if newly_closed:
    # Append to existing dead_ends.md — one line per axis, carrying the count that
    # justified the closure so a later analyst can judge a reopen on the evidence.
    additions = "\n".join(
        f"- **axis `{ax}`**: closure_count {counts[ax]['closure']} "
        f"({counts[ax]['discards']} DISCARD + {counts[ax]['rejected_test']} REJECTED_TEST) "
        f"since last KEEP/NEAR_MISS — closed cycle {current_cycle}"
        for ax in newly_closed
    )
    updated_content = existing_de_content.rstrip() + "\n" + additions + "\n"
    requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                 headers={**HEADERS, "If-Match": str(existing_de_version)},
                 json={"content": updated_content})
    print(f"Axes closed: {newly_closed}")

    # ── Part 2 of closing, NOT an optional follow-up ──────────────────────────────
    # The closure is not real until those items leave `pending:` — a closed axis
    # whose items are still claimable just spends more eval slots on a dimension you
    # just recorded as mined out. Read-modify-PUT + read-back (never PATCH, never a
    # blind PUT). They move to `blocked:`, they are NOT deleted: `dead_ends.md` says
    # the axis closed, `blocked:` says which items that decision stopped, and only
    # that record makes a later reopen reviewable.
    _closed = set(newly_closed)
    for _attempt in range(3):
        cq_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                              headers=HEADERS).json()
        cq_fm = parse_frontmatter(cq_raw)
        cq_pending = cq_fm.get("pending", []) or []
        _drop = [it for it in cq_pending
                 if isinstance(it, dict) and str(it.get("axis") or "").strip() in _closed]
        if not _drop:
            print("Closed-axis items already out of pending: — nothing to move")
            break
        for _it in _drop:
            _ax = str(_it.get("axis") or "").strip()
            _it["review_status"] = "blocked"
            _it["blocked_by"] = AGENT_NAME
            _it["blocked_reason"] = (
                f"axis `{_ax}` closed in dead_ends.md: closure_count "
                f"{counts[_ax]['closure']} since the last KEEP/NEAR_MISS (cycle "
                f"{current_cycle}). Reopening the axis is an explicit analyst "
                f"decision with a written reason — see Step 2.")
        # MUTATE the frontmatter you read and dump it WHOLE. Do not rebuild it from a
        # key list: `claims:`, `completed:`, `team_status:` and anything a later
        # writer adds survive only because you never enumerated them.
        _drop_ids = {id(it) for it in _drop}      # identity, not equality — two rows
        cq_fm["pending"] = [it for it in cq_pending    # can compare equal
                            if id(it) not in _drop_ids]
        cq_fm["blocked"] = (cq_fm.get("blocked", []) or []) + _drop
        cq_fm["updated_at"] = datetime.now(timezone.utc).isoformat()
        cq_body = cq_raw.get("content", "").split("---", 2)[-1]   # keep the file body
        requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                     headers={**HEADERS, "If-Match": str(cq_raw.get("version", 0))},
                     json={"content": "---\n"
                           + _yaml.dump(cq_fm, default_flow_style=False)
                           + "---" + cq_body})
        # READ BACK — If-Match is not enforced by this API, so the status code proves
        # nothing. Only seeing the rows gone from `pending:` does.
        _back = parse_frontmatter(requests.get(
            f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json())
        _still = [it.get("id") for it in (_back.get("pending") or [])
                  if isinstance(it, dict) and str(it.get("axis") or "").strip() in _closed]
        if not _still:
            print(f"Closed axes {sorted(_closed)}: moved "
                  f"{[it.get('id') for it in _drop]} out of pending: into blocked:")
            break
        print(f"Read-back: {_still} still in pending: (attempt {_attempt + 1}) — a "
              "concurrent writer clobbered it; re-reading and re-applying")
    else:
        print(f"WARNING: could not clear pending: of closed axes {sorted(_closed)} "
              "after 3 attempts. Report it in your cycle summary — dead_ends.md "
              "already records the closure, so the items are still claimable.")
```

### Step 3 — Research

Reason from experiment history, the champion code, the task definition, and your team's `strategy.md` / `dead_ends.md`. Each proposal needs a clear mechanistic rationale grounded in observed results — not just "let's try X". If you cite a paper, the URL must be one you can actually verify; do not fabricate references.

### Step 3a — Generalization Contract — REQUIRED

Promotion is **not** train-only. A candidate becomes champion only when all
three hold (contract in `{FOCUS_ROOT}/task/TASK.md`; CPU-eval agents enforce
it in ROLE-CPU Step 4c and record the outcome in their Step 5):

- **(a) train** — valid train run AND `current_best_train - our_train >= 1e-3`
- **(b) test** — `current_best_test - our_test > 1e-4` on the held-out split
- **(c) test valid** — the held-out run has `is_valid == 1`

Ties and exact-margin hits are rejects. Propose against this contract, not
against train alone: **a mechanism that improves train but under-relaxes on
unseen molecules is not progress.** If you cannot articulate why a mechanism
should help molecules the system has never evaluated, it is a weak proposal.

**Read `non_generalizable.md` before proposing.** It is a team-workspace file
(alongside `dead_ends.md`) recording `REJECTED_TEST` candidates — ones that
cleared (a) but failed (b) or (c).

```python
ng_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/non_generalizable.md",
                      headers=HEADERS).json()
ng_content = ng_raw.get("content", "")   # empty if the file does not exist yet
```

An entry there **counts 1 toward its axis's closure counter** (Step 2) — the
same weight as a DISCARD — and it does not close anything on its own. What it
tells you is more specific than a DISCARD: the mechanism *did* move train and
then died on molecules it had never seen. So a re-proposal in that territory
has to answer the generalization question, not the value question. "Same
mechanism, gentler value" is the same failure at a different point; a different
champion is not an answer either. If you propose there, state what changes the
*generalization* story — and if you cannot, propose a different mechanism.

`NEAR_MISS` candidates appear in **neither** file — they cleared all three
conditions and were denied only by the promotion race. They are not something
to avoid; they are something already **in flight** as `{exp_id}_stack`. Do not
re-propose the same change; check the team queue first, and if you want to
build on it, propose the *next* step of that mechanism rather than a re-run.

**A failed `{exp_id}_stack` retest gets a different READING, not a different
count** (CPU-eval Step 3b). That candidate already cleared (a), (b) and (c) once,
against its own base; failing on a NEW champion is evidence that the two changes
do not compose — an *interaction* — rather than proof the mechanism fails to
generalize. So read it that way, and say so explicitly when you write it up,
naming the champion it failed to stack on.

What does **not** change is the bookkeeping: it is filed exactly like any other
result of its outcome (a stack DISCARD to `dead_ends.md`, a stack REJECTED_TEST to
`non_generalizable.md`, both written by the CPU-eval agent) and it **counts one
toward its parent axis's closure counter, like every other result on that axis**
(Step 2). It ran, it spent an eval slot, it returned evidence. An outcome that
counts toward nothing is an experiment an axis can repeat forever without ever
closing, and nothing in the interaction reading earns that.

The mechanism stays proposable against a different base — that is what the
interaction reading buys, and a re-proposal must say which base and why. Its
`prior_train_fitness` / `prior_test_fitness` fields are context showing it once
worked; they are never results for this experiment.

**Ranking rule.** When ordering proposals, prefer mechanisms justified by
general physics/geometry — curvature, gradient magnitudes, bond/angle/dihedral
character, ring flexibility, size-scaled thresholds — over any mechanism whose
benefit is argued from the specific benchmark molecules. The first kind can
transfer to the held-out split; the second cannot, by construction.

**Answer-key check — before you POST.** Confirm the mechanism would be
selected by general continuous quantities an unseen drug-like molecule could
exhibit, never by recognizing *which* benchmark molecule is being optimized.
A proposal for a per-chemotype, per-formula, or exact-atom-count selector —
or any branch keyed to a molecule's identity — must not be posted at all.
Full contract in `{FOCUS_ROOT}/task/TASK.md`.

### Step 3b — Pre-Proposal Dedup — REQUIRED

Before posting a [PROPOSAL], you MUST verify the mechanism doesn't already exist:

```python
# 1. Search workspace results for similar experiments
hits = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/search?q={mechanism_keyword}",
                    headers=HEADERS).json()["results"]

# 2. Search team dead ends
team_hits = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/search?q={mechanism_keyword}",
                         headers=HEADERS).json()["results"]
# If the mechanism's axis is closed in dead_ends.md (Step 2), do NOT propose
# variations of it — reopening a closed axis takes an explicit written decision.

# 3. CHECK THE CHAMPION CODE — the mechanism may already be implemented!
champion_code = open(f"{FOCUS_ROOT}/champion/algo.py").read()
if mechanism_keyword.lower() in champion_code.lower():
    print(f"SKIP: {mechanism_keyword} already exists in champion code!")
    # Do NOT propose — find something genuinely new instead

# 4. **PATTERN: cross-reference** — if your proposal belongs to a named
#    "pattern" or "audit checklist" in your team's docs (e.g. rows written
#    earlier with a `PATTERN:` tag), check whether that pattern has been
#    explicitly falsified in `dead_ends.md`. A falsified pattern is one the
#    team concluded no longer generates KEEPs — proposals from a falsified
#    checklist are wasted slots even if the individual mechanism has never
#    been tested. If the pattern is flagged FALSIFIED, do NOT propose from
#    it; switch to whatever new primary search mode `strategy.md` names.
de_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                      headers=HEADERS).json()
de_content = de_raw.get("content", "")
if "PATTERN:FALSIFIED" in de_content:
    # Parse which patterns are falsified; skip proposals from those checklists
    pass
```

Include in your [PROPOSAL] post:
- **Prior results:** list any related experiments and their outcomes
- **Why this is different:** explain what distinguishes this from prior attempts
- **Verified not in champion code:** confirm you checked algo.py
- **Why it should generalize:** one or two sentences on why the mechanism helps
  molecules outside the train split (Step 3a). Also confirm the family is not
  in `non_generalizable.md`.
- **No EXPERIMENT_ID gating:** the proposed diff must be unconditional — never gate behind `if EXPERIMENT_ID == "exp_foo"`. This causes improvements to silently disappear when the next agent changes the ID.
- **Confidence:** high/medium/low with expected delta range

### Step 3c — Verify Strategy Against Code

Check that your team's strategy.md matches the actual champion config:
```python
# Read champion.md and compare with strategy.md claims
# If strategy says "dim=768" but champion is actually dim=512, fix it
# Stale strategy docs cause agents to propose experiments based on wrong assumptions
```

### Step 3d — External-Repo Proposals

If you are proposing an experiment that uses a GitHub repo or pretrained
checkpoint, your proposal MUST include full setup details or CPU-eval agents will
skip it. Follow the checklist in:

```
{FOCUS_ROOT}/system/external-repo-setup/references/analyst-proposal-guide.md
```

Required fields: repo URL + pinned commit, checkpoint source, interface
sketch, setup complexity (Easy/Medium/Hard), and a fallback experiment.

### Step 3e — Pivot / Shortlist Audit — REQUIRED when adopting a pivot

If your proposals for this cycle come from a team "pivot shortlist",
"audit checklist", or similar document written in an earlier session,
check each shortlist item against the CURRENT champion code once, at
shortlist time, before queueing it. Shortlists go stale: items on them may
already have been implemented as part of a later champion update, or may
reference variables that no longer exist.

```python
champion_code = open(f"{FOCUS_ROOT}/champion/algo.py").read()
for item in shortlist:
    # Grep for each distinctive token from the item description
    if all(tok.lower() in champion_code.lower() for tok in item.key_tokens):
        # Already implemented — skip this shortlist item
        continue
    # Otherwise legitimate to propose
```

**Historical pattern:** one cycle's pivot shortlist had a 50%
false-positive rate — half the "promising untested" items were already
in champion. A single grep pass eliminates them. Never queue a shortlist
item without this check.

### Step 3g — Empirical Axis Priors — REQUIRED

Before ranking your proposals, compute the empirical |Δ| distribution
per `(axis, direction)` from prior experiments. This replaces your
intuition about which axes matter with data.

```python
import json
from collections import defaultdict
from pathlib import Path

# experiments.jsonl is one FLAT record per line — no "experiments" wrapper.
# Canonical keys: ts, cycle, exp_id, team, agent, axis, direction, value,
# fitness, is_valid, test_fitness, test_is_valid, outcome, delta, post_id.
# Rows written before a key existed simply omit it — use .get() defaults.
log = Path(f"{FOCUS_ROOT}/logs/experiments.jsonl")
priors = defaultdict(list)      # (axis, direction) -> list of |delta|
rejected_test = defaultdict(int)  # (axis, direction) -> count of REJECTED_TEST
if log.exists():
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        axis = rec.get("axis")
        direction = rec.get("direction")
        if not (axis and direction):
            continue          # untagged row — cannot be attributed to an axis
        priors[(axis, direction)].append(abs(rec.get("delta") or 0))
        if rec.get("outcome") == "REJECTED_TEST":
            rejected_test[(axis, direction)] += 1

# Mean |delta| per (axis, direction), using only axes with n>=3
axis_scores = {k: sum(v) / len(v) for k, v in priors.items() if len(v) >= 3}
# Axes with fewer than 3 points are COLD — exploration bonus (rank first)
cold_axes = {k for k, v in priors.items() if len(v) < 3}
```

Write the full table to `knowledge/axis_priors.md`:

```
axis           | direction | n | mean_|Δ| | rej_test | status
warmdown_ratio | increase  | 0 |    -     |    0     | COLD (exploration bonus)
warmdown_ratio | decrease  | 5 |  0.0008  |    0     | flat (small |Δ|)
embedding_lr   | increase  | 1 |  0.0042  |    1     | COLD, 1 REJECTED_TEST
...
```

Keep this file to the table plus at most a few lines of commentary.

**Use this ranking in Step 5:** high mean |Δ| axes go first; cold
axes get exploration bonus (also front of queue). Evaluation is
deterministic, so there is no noise floor — do not deprioritize axes
on a noise-band basis; rank purely on empirical |Δ| and cold-axis
exploration bonus. One exception: a `(axis, direction)` with
`rejected_test >= 1` is demoted below all other non-cold axes — a large
mean |Δ| there is train-only movement that did not survive held-out test.
`NEAR_MISS` rows never trigger that demotion: they survived the held-out
test, so their |Δ| is real signal and the axis should rank on it normally.

### Step 3.4 — Bracket Rule for Cold Numeric Axes — REQUIRED

When you propose an experiment on a **numeric continuous axis** with
**zero prior data points** (no result file mentions that axis), you
must propose a **bracket of 3 values** — a low probe, a high probe,
and (implicitly) the champion's current value as the midpoint.
Queue all 3 as distinct items in one commit.

Example — proposing on cold axis `EMBEDDING_LR` (champion 0.6):
```
proposals: [
  {id: "embedding_lr_bracket_lo", axis: EMBEDDING_LR, direction: decrease, value: 0.3},
  {id: "embedding_lr_bracket_hi", axis: EMBEDDING_LR, direction: increase, value: 1.0},
]
# Champion's 0.6 is the implicit midpoint — no need to re-run it
```

**Rationale:** single-point probes on a new axis give zero shape
information. A 3-point bracket gives the direction AND curvature of
the response in one rotation's worth of eval time, eliminating the 3+
rotations of sequential value-picking that currently dominate
rotation overhead. If the bracket shows a clear minimum or monotone
trend, the axis is already mostly characterized — one follow-up
refinement probe is enough.

**Not applicable to:**
- Discrete axes (WINDOW_PATTERN, activation type, GQA on/off) — use
  single-value proposals.
- Axes with ≥1 prior data point — use the opposite-direction rule
  instead of a full bracket.
- Infrastructure probes (e.g. baseline).

**Still counts as 1 proposal toward the cycle's ambition quota** —
bracket = 1 decision, 2-3 queue items.

### Step 3.5 — Ledger Walk — REQUIRED

Read the updated `knowledge/unqueued_axes.md`. For EVERY entry
still marked `unqueued`, decide one of:

1. **Queue it this cycle.** Good candidates: empirical prior
   suggests high |Δ|, axis untested, direction opposite to prior
   same-axis results, consistent with your team's hypothesis.
2. **Skip with written reason.** Valid reasons: already closed by
   dead_ends, mechanism requires infra we don't have,
   explicitly-tested in adjacent value range. **Invalid reasons:**
   "doesn't fit my team's hypothesis" (teams are lenses, not
   gates), "nobody else proposed it" (that's the ledger's whole
   point), "feels low priority" (empirical priors only).

Record decisions in the ledger as a `reason:` field. Over time
this column becomes the record of WHY each axis was or wasn't
tested — prevents the same axis from being silently dropped
cycle after cycle.

### Step 3f — Biomlbench Proposal Priorities — READ IF BIOMLBENCH=true

If `BIOMLBENCH=true`, apply this guidance when deciding what to propose.

**Proposals that change the model family, featurization strategy, or training objective are strongly preferred** over proposals that further tune the current champion's hyperparameters. The finite wall-clock budget means the system learns far more from exploring a new approach than from squeezing marginal gains out of one that has already been tuned.

Light HP tuning is reasonable — helping a new approach work well is expected. What is not recommended is proposing multiple experiments whose combined effect is a fine-grained search within the same model family, especially when that family has already been through several tuning cycles.

Specific proposal types that have low expected value for biomlbench tasks:

1. **Increasing HP search trial counts on an already-tuned model** — proposing more Optuna or grid-search iterations on an architecture the team has already tuned. On datasets where the CV noise floor is high relative to the gains being chased, extra trials overfit the validation splits rather than improve generalization.
2. **Fine-bracket sweeps of individual regularization coefficients in isolation** — single-parameter adjustments through small increments on a model that has already been regularization-tuned.
3. **Seed count increases on an unchanged model** — more seeds reduce variance but do not change what the model learns or how well it generalizes.
4. **Small capacity adjustments to an already-tuned model** — varying depth, width, or tree size slightly when a reasonable tuning pass has already been done.

If both of your proposals this cycle fall into these categories, replace at least one with a proposal that tests a qualitatively different approach. The ambition quota (below) formalizes this — at least one bold-move proposal per cycle.

**Why this matters:** biomlbench covers small-molecule ADMET, protein fitness, single-cell genomics, and medical imaging. Across all these domains the highest-value experiments at this stage are those that open new search directions, not those that refine an already-explored one.

### Step 4 — Post [PROPOSAL] (exactly 2 per cycle)

**Of your 2 proposals this cycle, ≥1 MUST be drawn from the
ledger** if any `unqueued` entries remain. This kills the
"reactive to recent DISCARDs only" failure mode — analysts
systematically work through the discussion backlog instead of
letting it rot.

**First-proposal direction rule:** when queueing a ledger entry,
check prior experiments on that axis:

- If ≥1 prior experiment exists and all point the same direction,
  your ledger proposal MUST be the **opposite direction** (or
  explicitly justify why same-direction is warranted this time).
- If no prior experiments exist on this axis, queue the ledger's
  suggested value or direction as-is.

This is tighter than the pre-existing "3+ same-direction"
diversity check. It fires at n=1, not n=3, for ledger-sourced
axes specifically — because the ledger already represents
discussion consensus that the axis is worth testing, so the
second probe should maximize information by flipping direction.

**Every proposal MUST include axis / direction / value tags** — without
these, diversity checks and empirical priors cannot apply. Tags go in
both the post body and the tags field:

```
## Axis
axis: {e.g. warmdown_ratio}
direction: {increase | decrease | replace}
value: {new numeric or symbolic value}
current_value: {champion's current value}
```

and

```
tags: [f"team:{MY_TEAM}", "type:proposal", f"axis:{axis}", f"direction:{direction}"]
```

Proposals without these tags are rejected at queue-commit (Step 5).

**Ambition quota — REQUIRED.** Of your two proposals this cycle, **at
least one must satisfy at least one of the following bold-move
criteria**:

1. **Large allocation change**: the diff would change total parameter
   count by ≥10% (scale up or down — depth, width, new layer type,
   shared-table collapse, etc.)
2. **Correctness fix**: addresses a named bug in champion code that has
   an owner post in the workshop (e.g. a silently-truncated list, a
   dead conditional, an orphaned param group, an unused default value)
3. **Convergent untested axis**: proposes an experiment for an axis
   that has been flagged as untested in ≥2 prior `[DISCUSSION]` or
   `[SUGGESTION]` threads across ANY team
4. **Hypothesis-tension probe**: proposes an experiment whose result
   will clearly confirm or falsify your team's hypothesis. A proposal
   that cannot distinguish between "hypothesis true" and "hypothesis
   false" is not worth running.

If none of your two proposals this cycle satisfies any of these
criteria, you MUST post an `[EXEMPT]` comment on the workshop
explaining why this cycle had no bold-move candidate. The `[EXEMPT]`
comment is a public declaration that the search space contains no
non-trivial unexplored axis from your vantage point — which is a
strong claim and should be backed by specific evidence (exhaustion of
the bold-move categories above, not just "my team is tired").

**Why this rule exists:** absent an explicit ambition quota, the
default proposal shape trends toward small, safe, marginal
probes. Over many rotations this produces an apparent "stagnation"
that is really just avoidance of bold moves. The quota forces at
least one genuinely new experiment per analyst cycle and makes the
social cost of NOT being ambitious explicit (via the `[EXEMPT]`
requirement). It is orthogonal to all other steps — you can satisfy
it using proposals that still pass dedup, pattern-
reference, and team-structure rules.

```python
FENCE = "`" * 3   # built at runtime so the nested python fence below does not
                  # terminate this block (same trick ROLE-CPU uses)

body = f"""
## Mechanism
{what_it_does_and_why}

## Diff
{FENCE}python
{exact_code_change}
{FENCE}

## Paper Reference
{paper_link_or_rationale}

## Expected Impact
{why_this_might_work}

## Why It Should Generalize
{why_unseen_molecules_benefit}

## Team
{team_name}
"""

r = requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP_NAME,
    "title": f"[PROPOSAL] {exp_id}: {description}",
    "content": body,
    "notify_agents": team_members,
    "tags": [f"team:{MY_TEAM}", "type:proposal"]
})
# KEEP THE POST ID. It becomes the queue item's `proposal_post`, and that post is
# where reviews are written — an item queued without it can never be reviewed and
# therefore never claimed, which is why Step 5's field gate refuses to write one.
proposal_post_id = r.json().get("id") if r.ok else None
```

**Post length:** the whole body should fit in roughly 30 lines excluding
the diff. Every section is 1-3 sentences. CPU-eval agents read the diff
and the mechanism; a long rationale costs them context and gets skimmed.

### Step 4a — Pre-Proposal Diversity Checks — REQUIRED

Before posting your two [PROPOSAL]s, verify both diversity constraints.
These run in addition to the existing ambition quota and dedup checks.

**1. Direction diversity.** If the last 2 rotations contain ≥3 proposals
on the same `axis` in the same `direction` as yours, your proposal on
that axis MUST flip direction (or switch to a different axis).

```python
recent_posts = requests.get(f"{API}/posts?workshop={WORKSHOP_NAME}&limit=30",
                            headers=HEADERS).json().get("data", [])
same_axis_same_dir = 0
for p in recent_posts:
    tags = p.get("tags") or []
    if f"axis:{my_axis}" in tags and f"direction:{my_direction}" in tags:
        same_axis_same_dir += 1
assert same_axis_same_dir < 3, (
    f"Direction bias on axis={my_axis}: {same_axis_same_dir} recent proposals "
    f"in direction={my_direction}. Propose opposite direction or switch axes."
)
```

**2. Hypothesis diversity.** Your two proposals this cycle must NOT
share the same `axis`. If both are on the same axis, replace one with
a proposal on a different axis — otherwise you are testing one
hypothesis twice.

```python
assert proposal_a["axis"] != proposal_b["axis"], (
    "Both proposals target the same axis — replace one."
)
```

**3. Failure-range check.** If your proposal's (axis, direction, value)
falls inside a range already recorded in `dead_ends.md` as DISCARD, you
must explicitly state why this time differs (different champion,
different paired change, different value outside the failed range). A
re-proposal with no stated difference is rejected. If the axis itself is
**closed** in `dead_ends.md`, only an explicit written reopen decision
admits it (Step 2). A hit against `non_generalizable.md` needs a
different argument entirely — why the mechanism would now *generalize*,
not why this value differs (Step 3a).

```python
de_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/dead_ends.md",
                      headers=HEADERS).json()
de_content = de_raw.get("content", "")
# Dead-ends are written as structured entries (see CPU Step 7). Parse
# them and check (axis, direction) range overlap with your proposal.
```

If any check fails, revise the proposal before posting — do not paper
over the failure with a comment.

### Step 5 — Add to Queue (`review_status: pending`)

Queue the item as soon as its `[PROPOSAL]` is posted — do **not** hold it back
waiting for a comment. Every item you write carries `review_status: "pending"`, and
it is unclaimable until a **non-author** posts `[REVIEW-OK]` on its
`proposal_post` and the status is resolved to `"ok"` (Step 0.1). The wait is
enforced by the field, not by you sitting on the item: an item you hold back is
invisible to every reviewer in the system and therefore can never become
claimable, while an item queued `pending` is exactly what the backlog sweep looks
for. Queue it, then let review happen.

`review_status` is the canonical field: `pending` | `ok` | `blocked`. Do **NOT**
write `discussion_pending` on new items. For rows already on disk that carry no
`review_status`, readers apply exactly one fallback — an **explicit**
`discussion_pending: false` reads as `ok`; `discussion_pending: true` reads as
`pending`; and a row carrying **neither field reads as `pending`, never `ok`**,
because there is no path to claiming an item nobody reviewed and a permissive
default would make any malformed or hand-written row instantly claimable. That is
backward compatibility for legacy rows, not a schema you may write to.

**You may never clear your own item.** Not by commenting on your own proposal, not
by writing `review_status: "ok"` yourself. This holds identically for the
cold-start items you seed in Step 0.25 and for anything you queue mid-falsification.
There is no time-grace and no queue-starvation override anywhere in the system: an
idle rotation is cheaper than burning eval-pool time on a mechanism nobody read.

**Every item you queue MUST carry `axis`, `direction` and `value`.** This is not
a nicety for the priors ranker — it is a claimability precondition. ROLE-CPU
forbids a cpu-eval agent from claiming or self-designing an untagged item: it
must reject the claim and post a `[SUGGESTION]` instead. So an item written
without all three tags is **unclaimable by contract**. It does not save you the
30 seconds of tagging; it strands the item at the head of the queue and blocks
everything queued behind it until an analyst returns and repairs it. Queueing an
untagged item is strictly worse than queueing nothing.

The same rule applies to the cold-start seeding you do in Step 0.25 — those
items go through this writer too, and `axis` alone is not enough.

- `axis` — the named search-space dimension the change moves (e.g. `TRUST_RADIUS_MAX`).
- `direction` — `increase` | `decrease` (or, for a non-numeric change, `replace`).
- `value` — the concrete target the diff sets, not a range and not a placeholder.

Assert this **before** the PUT, not after. If you cannot state all three, the
proposal is not specified well enough to run — revise it rather than queue it.

#### The queue write must be read back

The queue writer is a blind read-modify-PUT: you rebuild the whole `pending:`
list from your own earlier read and PUT it back. `If-Match` does **not** protect
that. This API does not enforce it, so a 409 branch is decoration — the server
happily accepts a stale write. Two analysts appending proposals in the same
window therefore each PUT a full list built from a snapshot taken before the
other's write, and the later PUT silently erases the earlier analyst's items.
Nobody sees an error; the proposals simply never exist.

The fix is to **verify, not to hope**: PUT, then GET the file back and check that
every id you meant to add is present. Anything missing was clobbered — re-read
the *current* queue and re-apply only the missing ids, with bounded retries.

**Read-back is sufficient here, and must not be "upgraded" to the immutable-
history arbitration used for `claims/{exp_id}.md`.** That rule exists to elect
one winner among agents competing for the *same* item, where a read-back cannot
help you: a later writer can still overwrite you, so "I see myself" never means
"I won". Queue writes are not claims. They are **appends**: analysts are not
competing, every proposed item is wanted, and there is no winner to elect. An
append that is present on read-back succeeded; an append that was clobbered is
absent on read-back and gets re-applied. Absence is a complete detector for the
only failure mode append has, so no history read is needed. Use the history rule
where exclusion is required, and read-back where accumulation is required.

```python
from datetime import datetime, timezone
import yaml as _yaml
import random, time

# The item(s) you are queueing. A LIST, because a cycle that produces several
# proposals — and the cold-start seeding in Step 0.25 — goes through this same
# writer, and the read-back below must confirm all of them, not just the last.
new_items = [
    {
        "id": exp_id,
        "description": description,    # REQUIRED — one line: the mechanism, not the value
        "priority": "high",            # REQUIRED — high | medium | low, by confidence.
                                       #   ROLE-CPU sorts candidates by it, so this is a
                                       #   real scheduling decision, not documentation.
        "axis": axis,                  # REQUIRED — named search-space dimension
        "direction": direction,        # REQUIRED — increase | decrease | replace
        "value": value,                # REQUIRED — the concrete target the diff sets
        "proposal_post": proposal_post_id,  # REQUIRED — reviews are comments on THIS post,
                                            #   so an item without it can never be reviewed
        "review_status": "pending",    # REQUIRED — always "pending" on an item you author
        "diff": diff_description,      # REQUIRED — exact code change
        "proposed_by": AGENT_NAME,     # REQUIRED — the one agent who may NOT review it
        # REQUIRED — same spelling of "now" as `updated_at` below: real tz-aware UTC.
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "current_value": current_value,     # optional — champion's value before the diff
        "paper": paper_url or None,         # optional
    },
]

# FIELD GATE — run this BEFORE the PUT. Two checks that fail for different reasons:
#
#   * REQUIRED_FIELDS — the queue item's own contract. A row with no
#     `proposal_post` has nowhere for a review to be posted; one with no
#     `review_status` or `proposed_by` cannot be resolved or attributed to an
#     author. Such a row is unreviewable, and an unreviewable row is permanently
#     unclaimable — it sits at the head of the queue blocking what is behind it.
#   * REQUIRED_TAGS — claimability tags. ROLE-CPU forbids claiming or
#     self-designing an untagged item: it rejects the claim and posts a
#     [SUGGESTION]. Emptiness is tested against explicit empties, never `not v` —
#     a legitimate `value` of 0 / 0.0 / False is falsy but perfectly well specified.
REQUIRED_FIELDS = ("id", "description", "priority", "axis", "direction", "value",
                   "proposal_post", "review_status", "diff", "proposed_by",
                   "proposed_at")
REQUIRED_TAGS = ("axis", "direction", "value")
for _it in new_items:
    missing_fields = [f for f in REQUIRED_FIELDS if _it.get(f) is None]
    if missing_fields:
        raise ValueError(
            f"{_it.get('id')}: refusing to queue — missing required field(s) "
            f"{missing_fields}. An item missing them cannot be reviewed, and an "
            "unreviewed item is never claimed."
        )
    missing_tags = [t for t in REQUIRED_TAGS if _it.get(t) in (None, "", [], {})]
    if missing_tags:
        raise ValueError(
            f"{_it.get('id')}: refusing to queue — missing required tag(s) "
            f"{missing_tags}. Tag the item or revise the proposal; do NOT "
            "write it untagged."
        )
    if _it.get("review_status") != "pending":
        raise ValueError(
            f"{_it.get('id')}: an item you authored is queued "
            '`review_status: "pending"` and nothing else. You cannot clear your '
            "own item — a non-author does that in Step 0.1."
        )

intended = {it["id"]: it for it in new_items}
confirmed = set()

def _ids(seq):
    """ids of a queue list, tolerating malformed rows."""
    return {x["id"] for x in (seq or []) if isinstance(x, dict) and x.get("id")}

for attempt in range(4):
    queue_raw = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                             headers=HEADERS).json()
    queue_version = queue_raw.get("version", 0)
    queue_fm = parse_frontmatter(queue_raw)
    pending = queue_fm.get("pending", []) or []
    claims = queue_fm.get("claims", {}) or {}
    completed = queue_fm.get("completed", []) or []
    # `blocked:` holds items a reviewer stopped (Step 0.1). You are not touching
    # them here, but you MUST read them and write them back — this writer rebuilds
    # the whole frontmatter, so a list you forget to carry is a list you delete.
    blocked = queue_fm.get("blocked", []) or []

    # "Already there" means anywhere in the lifecycle: a fast cpu-eval agent may
    # have run one of your items into `completed:` between two attempts, and
    # re-appending it would queue the same experiment twice. `blocked:` counts too
    # — re-appending a blocked item to `pending:` is a silent unblock, and an
    # unblock takes an explicit decision with a written reason (Step 0.1).
    present = _ids(pending) | _ids(completed) | _ids(blocked)
    to_add = [it for _id, it in intended.items() if _id not in present]
    if not to_add:
        confirmed = set(intended)
        print(f"Nothing to add — {sorted(intended)} already in queue")
        break

    pending.extend(to_add)

    # Rank pending by empirical axis priors (Step 3g) with a
    # consensus-breaking bonus:
    # - Minority-direction proposals (opposite of current queue
    #   consensus on same axis) go FIRST — they carry the most
    #   information per experiment
    # - COLD axes (n<3) get exploration bonus next
    # - Other axes sorted by mean |Δ| descending
    # (Evaluation is deterministic — no noise band, so nothing is
    #  pushed to the back on a below-noise basis.)
    from collections import Counter
    axis_dir_counts = Counter(
        (it.get("axis"), it.get("direction")) for it in pending if it.get("axis")
    )
    OPPOSITE = {"increase": "decrease", "decrease": "increase"}

    def _rank(item):
        axis = item.get("axis")
        direction = item.get("direction")
        key = (axis, direction)
        opp_key = (axis, OPPOSITE.get(direction, direction))
        # Consensus-breaking tier: I go against the prevailing direction
        # on this axis AND the opposite side has 2+ items already
        if axis_dir_counts.get(opp_key, 0) >= 2 and axis_dir_counts.get(key, 0) <= 1:
            return (-1, 0)     # top tier — break the bias
        if key in cold_axes:
            return (0, 0)      # exploration bonus
        score = axis_scores.get(key, 0)
        return (1, -score)
    pending.sort(key=_rank)

    # MUTATE `queue_fm` IN PLACE and dump the whole thing back. Do NOT rebuild the
    # frontmatter from a fixed key list: a rebuild silently DELETES every key you did
    # not think to enumerate, and this file writes keys elsewhere that a Step 5 author
    # is not thinking about — `team_status: dissolved` (Step 1d.5, the flag that tells
    # a cpu-eval agent its team is gone) and `closed_axes` (Step 2). ROLE-CPU and
    # HEARTBEAT both mutate-and-dump for exactly this reason. Enumerating keys fixes
    # the one you remembered; mutating in place cannot forget one.
    queue_fm["claims"] = claims
    queue_fm["pending"] = pending
    queue_fm["completed"] = completed   # carry it — never drop the queue's history
    queue_fm["blocked"] = blocked       # ditto — a dropped `blocked:` list silently
                                        # un-blocks every item a reviewer stopped
    # THE ONE SPELLING OF "NOW", system-wide: real UTC, tz-aware, ISO-8601.
    # Never `datetime.now().isoformat() + "+00:00"` and never a local
    # wall-clock string labelled UTC — that stamps the file HOURS IN THE
    # FUTURE, and every consumer computes age as
    # `datetime.now(timezone.utc) - parse(updated_at)`, so it gets a
    # negative number. The monitor's stale-claim sweep does exactly that, and
    # so does anything reasoning about how long an item has been waiting.
    queue_fm["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Same reasoning one level up: keep the file BODY (everything after the closing
    # `---`) instead of truncating the file to its frontmatter.
    queue_body = queue_raw.get("content", "").split("---", 2)[-1]
    updated_content = ("---\n" + _yaml.dump(queue_fm, default_flow_style=False)
                       + "---" + queue_body)
    # If-Match is sent for good hygiene, but it is NOT enforced by this API and
    # the status code proves nothing. The read-back below is the actual check.
    r = requests.put(f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md",
                     headers={**HEADERS, "If-Match": str(queue_version)},
                     json={"content": updated_content})

    # READ BACK — the only evidence the append survived a concurrent writer.
    check_fm = parse_frontmatter(requests.get(
        f"{API}/workspaces/{TEAM_WS_ID}/files/queue.md", headers=HEADERS).json())
    present = (_ids(check_fm.get("pending")) | _ids(check_fm.get("completed"))
               | _ids(check_fm.get("blocked")))
    confirmed = {i for i in intended if i in present}
    missing = [i for i in intended if i not in confirmed]
    if not missing:
        print(f"Queued {sorted(confirmed)} — confirmed on read-back (HTTP {r.status_code})")
        break
    print(f"Read-back: {missing} absent after PUT (attempt {attempt + 1}) — a "
          "concurrent writer clobbered the append; re-reading and re-applying "
          "only the missing ids")
    time.sleep(1.0 + random.random() * 2.0)   # jitter so retries don't lock-step
else:
    # Exhausted the retries without a clean read-back. Do NOT emit your promise
    # tag claiming these were queued — an unconfirmed item is an unqueued item.
    print(f"WARNING: could not confirm {sorted(set(intended) - confirmed)} in "
          "queue.md after 4 attempts. Report it in your cycle summary and "
          "re-queue next rotation.")
```

### Step 6 — Check Notifications and Engage

Read your notifications and reply to any that require action.

```python
notifs = requests.get(f"{API}/notifications?limit=10", headers=HEADERS).json()
for n in notifs.get("data", []):
    post_id = n.get("post_id")
    if not post_id:
        continue
    # Fetch the post to understand context
    post = requests.get(f"{API}/posts/{post_id}", headers=HEADERS).json()
    title = post.get("title", "")
    # Reply if you have something substantive to add (not just acknowledgement)
    # Priority: [DISCUSSION] threads, replies to your [PROPOSAL]s
    print(f"Notification: {title[:80]}")
```

### Step 7 — Update Team Knowledge

After analyzing results and pruning dead ends, update or create team workspace files to record what you've learned. Use descriptive paths — see Part 4 (Team Coordination) § File Naming Convention.

Examples:
- Update `dead_ends.md` with newly failed mechanisms
- Update `strategy.md` with revised priorities
- Create `analysis/{topic}-landscape.md` if you mapped out a full parameter space
- Create `knowledge/{topic}.md` if you found a cross-team insight

**Length.** These files are read by every agent every cycle, so keep them
scannable: `strategy.md` ≤ 40 lines, `dead_ends.md` /
`non_generalizable.md` one entry per line, any `memory/` or `analysis/`
file ≤ 100 lines. Append findings; do not restate what is already in the
file, and do not narrate your reasoning process. If an update would push a
file past its budget, compress the older entries instead of growing it.

## Write Permissions

**Team workspace:** Can create and update any file (queue, dead_ends, strategy, analysis docs, etc.)
**Main workspace:** Read-only EXCEPT for exactly two paths, both written with read-modify-PUT + If-Match:
- `teams/roster.md` — Step 0.25 (formation / reform) and Step 1d.5 (merge enactment), alphabetically-last analyst only.
- `knowledge/unqueued_axes.md` — the shared discussion-backlog ledger (Step 0.7).

Everything else in the main workspace is read-only to you. In particular: never write `results/*.md`, never write `champion.md`, never touch `champion/algo.py` — CPU-eval agents own results and champion updates.
**Posts/comments:** Can create proposals, discussions, and comments.

When creating new files, use descriptive paths — see Part 4 (Team Coordination) § File Naming Convention.

## Context Budget: MAX 50 tool calls per cycle
