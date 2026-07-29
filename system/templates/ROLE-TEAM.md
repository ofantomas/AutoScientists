---
name: multi-agent-focus-team
description: How agents within a team coordinate using their team workspace
---

# Team Coordination Protocol

Each team has its own workspace. All team members can read/write all files.

## Experiment Flow

```
1.  Analyst checks existing results for duplicates  ← dedup check
2.  Analyst posts [PROPOSAL] on workshop            ← the post reviews attach to
3.  Analyst queues it as review_status: pending     ← team workspace
4.  Non-author posts [REVIEW-OK] on the proposal    ← any team; review gate
5.  CPU-eval agent claims an ok item from queue.md  ← claims/{exp_id}.md, v1 updatedBy decides
6.  CPU-eval agent checks results/ for existing result ← dedup check
7.  CPU-eval agent copies champion code to workspace ← canonical source
8.  CPU-eval agent applies ONE change and evaluates ← remote eval pool
9.  CPU-eval agent re-reads champion (race condition) ← version check
10. CPU-eval agent writes result to main workspace  ← results/{exp_id}.md
11. CPU-eval agent posts [RESULT] on workshop       ← cross-team visibility
12. CPU-eval agent updates dead_ends.md if DISCARD  ← team knowledge
13. CPU-eval agent updates non_generalizable.md if REJECTED_TEST ← team knowledge
14. CPU-eval agent re-queues {exp_id}_stack if NEAR_MISS ← retest on the NEW champion
15. CPU-eval agent releases claim                   ← released: true marker + next_claim_path
```

Outcomes are `KEEP` / `NEAR_MISS` / `DISCARD` / `REJECTED_TEST` / `FAILED`.

A `NEAR_MISS` cycle passed the train gate **and** the held-out test gate but was not promoted: it
lost the promotion race (an equal-or-better champion landed while it was evaluating, or
`champion.md` no longer records its `exp_id`). It is **positive evidence** — the mechanism worked.
It writes to **NEITHER** `dead_ends.md` **NOR** `non_generalizable.md`, `champion.md` is not
written and `champion/algo.py` is not copied, and the same change is re-queued as `{exp_id}_stack`
so it is re-tested against the new champion. Never treat it as a failure and never re-propose it —
its retest is already in the queue.

A `FAILED` cycle is an infra/harness failure (eval returned no score, diff did not apply, scope
abort) and **tested nothing**: it goes in neither `dead_ends.md` nor `non_generalizable.md`, and
its queue item returns to `pending:` instead of being closed out.

## File Discovery Protocol

Agents do NOT follow hardcoded lists of files to read. Instead, they discover what exists and decide what is relevant to their current task.

### The LIST → DECIDE → READ loop

```python
# 1. LIST — cheap metadata, no content loaded (~50 tokens)
main_files = requests.get(f"{API}/workspaces/{MAIN_WS_ID}/files",
                          headers=HEADERS).json()["files"]
team_files = requests.get(f"{API}/workspaces/{TEAM_WS_ID}/files",
                          headers=HEADERS).json()["files"]
# Returns: [{path, version, updatedAt, updatedBy}, ...]

# 2. DECIDE — scan paths, timestamps, authors. Ask yourself:
#    - Is this file relevant to what I'm doing right now?
#    - Has it been updated since I last saw it? (high version = active)
#    - Was it written by a teammate whose work I depend on?

# 3. READ — only fetch files you actually need
for f in team_files:
    if is_relevant(f["path"], f["updatedAt"]):
        content = requests.get(
            f"{API}/workspaces/{TEAM_WS_ID}/files/{f['path']}",
            headers=HEADERS).json()
```

### When to SEARCH instead of LIST

If you need something specific but don't know which file has it:
```python
hits = requests.get(
    f"{API}/workspaces/{MAIN_WS_ID}/search?q={keyword}",
    headers=HEADERS).json()["results"]
# Returns: [{path, version, matches: [{line, text}]}]
```

### Essential anchors (always read, never skip)

These files are structural — every agent reads them every cycle:

| File | Workspace | Who reads it | Why |
|---|---|---|---|
| `champion.md` | main | CPU-eval agents | The baseline to beat |
| `queue.md` | team | CPU-eval agents | Work items to claim |
| `teams/roster.md` | main | all agents | Team membership + workspace IDs |

Everything else is **discovered via LIST**, not prescribed.

### File Naming Convention

Use descriptive, self-documenting paths so that LIST output alone tells agents whether a file is worth reading:

| Pattern | Example | Purpose |
|---|---|---|
| `results/{exp_id}.md` | `results/exp_042.md` | Experiment outcome (write-once) |
| `dead_ends.md` | — | Per-DISCARD entries **plus axis-closure records** (the closure record is the only durable trace that an axis was closed, and its count includes REJECTED_TEST results); REJECTED_TEST entries themselves go to `non_generalizable.md` |
| `non_generalizable.md` | — | Mechanisms that improved on train but failed the held-out test gate (REJECTED_TEST); counts toward axis closure, but is recorded here and never mixed into `dead_ends.md` |
| `strategy.md` | — | Current team approach |
| `analysis/{topic}.md` | `analysis/{topic}.md` | Deep-dive on a topic |
| `knowledge/{topic}.md` | `knowledge/{topic}.md` | Cross-team insight |

When creating new files, ask: **"Would another agent reading just the filename know whether this is relevant to them?"**

### Writing new files

You can create files freely in your team workspace. Other agents will discover them on their next LIST call. Use descriptive paths — don't call it `notes.md`, call it `analysis/{specific-topic}.md`.

## Team Queue (queue.md)

```yaml
---
claims:   # INERT bookkeeping. Exclusion is claims/{exp_id}.md (v1 updatedBy), NOT this map.
  agent_1:
    exp_id: exp_foo
    claimed_at: "2026-03-29T10:00:00Z"
  agent_2: null
pending:
  - id: exp_foo
    description: "Wolfe-condition line search instead of the fixed backtrack"
    axis: line_search        # the closure unit — see Axis Closure below
    direction: replace       # increase | decrease | replace
    value: "wolfe c2=0.9"    # a concrete target, never a range
    priority: high
    bold_bet: true
    diff: "Add mechanism X to minimize_func..."
    paper: "arXiv:XXXX.XXXXX"
    proposed_by: analyst_1
    proposed_at: "2026-03-29T09:41:00Z"
    proposal_post: "post-uuid-1"
    review_status: pending   # pending | ok — the only two states of an item in `pending:`
  - id: exp_bar
    description: "Raise the trust-region growth factor"
    axis: step_size
    direction: increase
    value: 1.4
    priority: medium
    diff: "Change param Y from A to B..."
    proposed_by: analyst_1
    proposed_at: "2026-03-29T09:52:00Z"
    proposal_post: "post-uuid-2"
    review_status: ok        # a non-author posted [REVIEW-OK] — claimable
blocked:                     # NOT a sub-state of pending: — a separate top-level list
  - id: exp_baz
    description: "Drop the curvature guard from the trust-region update"
    axis: trust_region
    direction: replace
    value: "no curvature guard"
    priority: medium
    diff: "Remove the curvature guard..."
    proposed_by: analyst_2
    proposed_at: "2026-03-29T08:10:00Z"
    proposal_post: "post-uuid-3"
    review_status: blocked
    blocked_by: cpu_eval_3           # the reviewer who posted [REVIEW-BLOCK]
    blocked_reason: "Removes the guard that made exp_017 valid — re-introduces the overshoot."
---
```

**All three of `axis`, `direction` and `value` are mandatory on every item** — not `axis` alone.
`axis` is what the closure rule counts against; `direction` (`increase` | `decrease` | `replace`)
and `value` (a concrete target, never a range) feed the empirical-priors ranking and the
direction-diversity check. A cpu-eval agent must **refuse to claim** an item missing any of the
three and post a `[SUGGESTION]` asking the analyst to fix the row (ROLE-CPU). The full set an
analyst writes is `id`, `description`, `priority`, `axis`, `direction`, `value`, `diff`,
`proposed_by`, `proposed_at`, `proposal_post`, `review_status`.

**`pending:` and `blocked:` are separate lists, not one list with a status flag.** A
`[REVIEW-BLOCK]` **moves** the row out of `pending:` into `blocked:`; it is never deleted, because
it is the record of what the system decided *not* to run. A blocked row left in `pending:` makes
every claim loop in the run walk past it forever. It returns only by an explicit analyst decision
that writes `unblocked_by` (the analyst) and `unblock_reason` (why the objection no longer applies)
onto the row and moves it back into `pending:` — another `[REVIEW-OK]` does **not** clear a block.

**Claim/Release:** Use **read-modify-PUT with If-Match**. Do NOT use PATCH on queue.md —
dotted-key PATCH on nested frontmatter (`claims.agent_1`) flattens `pending:` lists and
corrupts the YAML across teams. See ROLE-CPU.md Step 3/6 for the correct recipe. Carry **every**
top-level key through each read-modify-PUT — `claims:`, `pending:`, `blocked:`, and `completed:`
if present. A dropped `blocked:` is a silent unblock and a dropped `completed:` a silent re-queue;
neither is reported by anything.

## Review Before Claiming (replaces Discussion-Before-Queuing)

Every experiment MUST have a `[PROPOSAL]` post first, and its queue item MUST carry that post's id
in `proposal_post`. The item enters the queue as `review_status: pending` and becomes claimable only
once a **non-author agent from ANY team** has posted a review on that `[PROPOSAL]`: a comment whose
first line begins with the exact tag

    [REVIEW-OK]

followed by at least one sentence of substantive reasoning. `[REVIEW-BLOCK]` is the other valid tag
and dominates — one block outweighs any number of OKs. A comment carrying neither tag is not a
review and changes nothing. This is what prevents wasting eval time on poorly-thought-out ideas.

**Cross-team review is the point, not a fallback.** The old rule asked for a comment from another
member of the *proposing team*, which silently assumed such a member exists — a team with a single
analyst has no one else, so everything that analyst proposed became permanently unreviewable and
the queue stalled. Any non-author agent may review, analyst or cpu-eval, on any team.

The reviewer must not be the item's `proposed_by`. That exclusion is absolute and unchanged: you may
never review your own proposal.

**Claiming bars the SOLE reviewer, not every reviewer.** A cpu-eval agent may not claim an item when
its own `[REVIEW-OK]` is the **only** `[REVIEW-OK]` on that item — the single endorsement it would be
relying on is its own, so the claim is self-service and the two-person rule is void. Once a second,
**independent** `[REVIEW-OK]` from any other agent exists, the item is claimable by anyone, including
an agent who also reviewed it: the independent scrutiny is there whether or not that agent reviewed
as well, which is the principle the rule actually enforces.

Never bar on "I reviewed it" alone. That earlier form permanently strands the run at cold start:
every agent boots at the same instant, sees the same handful of seeded items, reviews them, and is
then barred from all of them — and agent names persist across rotations, so respawning does not
release anything. (`review_scan` in ROLE-CPU expresses the guard as `sole_reviewer = mine_ok and
oks == 1`; HEARTBEAT's duplicated claim logic uses the identical guard.)

This narrows **who** is barred; it creates **no** path to claiming an unreviewed item. An item is
still claimable only once its status resolves to `ok` — at least one non-author `[REVIEW-OK]` with no
unresolved `[REVIEW-BLOCK]`.

**An item with no `proposal_post` is unreviewable, not clearable.** A review is a comment on the
proposal post, so a row naming no post gives a reviewer nowhere to put one. Its status is
`pending`, it is never claimable, it is **excluded** from the review backlog, and no agent may ever
stamp `review_status: ok` on it. Repairing it — posting a real `[PROPOSAL]` and filling the field
in — is an analyst job, not something a reviewer clears.

**A row carrying no `review_status` at all resolves to `pending`, never `ok`.** A legacy row that
predates the field, or a malformed hand-written one, has demonstrably not been reviewed; defaulting
the unknown case to `ok` would make any such row instantly claimable, which is the exact hole the
review gate exists to close. The one exception is a legacy row with an explicit
`discussion_pending: false` — that was cleared under the old protocol, so it maps to `ok`. Nothing
writes `discussion_pending` any more; it is read-only legacy. (`review_scan` in ROLE-CPU,
`item_review_status` in HEARTBEAT and `review_status_of` in ROLE-ANALYST implement exactly this and
must stay identical.)

## Strategy Discussions

Use workspace file comments for async discussion:
```python
requests.post(f"{API}/workspaces/{TEAM_WS_ID}/files/strategy.md/comments",
    headers=HEADERS, json={"content": "X looks exhausted well before its axis closes — pivot to Y?"})
```

Or create a workshop post for bigger strategy changes:
```python
requests.post(f"{API}/posts", headers=HEADERS, json={
    "workshop": WORKSHOP_NAME,
    "title": f"[DISCUSSION] {team_name}: pivoting from X to Y",
    "notify_agents": team_members,
    "tags": [f"team:{team_name}", "type:discussion"]
})
```

## Axis Closure (dead ends)

**A family is an `axis`** — the queue item's `axis` string, exactly. Never a prefix of `exp_id`:
with the mandated `exp_<team-prefix>_<mechanism>` id schema, the leading tokens of an exp_id name the
**team**, so counting by prefix closes an entire team's queue on evidence about one mechanism. A
`{exp_id}_stack` retest has no axis of its own — use its parent item's axis.

After each result, count for that axis:

    closure_count(axis) = number of DISCARD plus REJECTED_TEST results on that axis
                          SINCE the most recent KEEP or NEAR_MISS on that axis
                          (all-time if that axis has never had one)

- **KEEP resets the count to 0**, and **NEAR_MISS resets it to 0** as well: a NEAR_MISS cleared both
  gates and only lost the promotion race, so the axis demonstrably contains a working mechanism.
- **FAILED never counts.** It tested nothing.
- **A `{exp_id}_stack` retest counts exactly like any other result on its parent's axis.** Its
  DISCARD or REJECTED_TEST adds 1; its KEEP or NEAR_MISS resets that axis to 0. There is **no**
  stack carve-out: a stack retest is a real experiment that consumes real eval-pool time and
  returns real evidence about whether the mechanism survives on top of the new champion. Exempting
  it would also be an unbounded loophole — an axis could emit stack retests forever and never
  close. The `_stack` suffix is keyed to the parent's axis for one reason only: so the retest is
  never mistaken for a NEW axis.
- **REJECTED_TEST counts 1**, exactly like a DISCARD — but it is recorded in
  `non_generalizable.md`, with which test condition failed (test invalid, insufficient test
  improvement, or both), and **never** in `dead_ends.md`. It is evidence about generalization, not
  about the mechanism being useless on train, and analysts read the two files for exactly that
  distinction.

**`closure_count(axis) >= 10` → close the axis:** remove that axis's `pending:` items and record the
closure in `dead_ends.md` with the axis name and the count.

Ten is deliberately high. An axis holds many mechanisms and many parameterisations; closing one
after two or three non-improving results throws away a live direction on noise, and the resulting
gap in the search is invisible afterwards.

A closed axis reopens **only** by an explicit analyst decision with a written reason recorded in
`dead_ends.md`. Never automatically, and never as a side effect of someone proposing on it again.
