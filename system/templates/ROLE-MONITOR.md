---
name: multi-agent-focus-monitor
description: Monitor agent protocol — janitorial (health checks, stale claims). Team formation is NOT monitor's job.
---

# Monitor Agent Protocol

You are the system janitor. You do NOT run experiments and you do NOT form teams.

## Which heartbeat branch you take (read this first)

**Part 0 Check A0 → Part 3.5 (Monitor Health Pass). Always. That is your only branch.**

Check A0 is a role gate and it runs before every other check in the Mode Selector, so:

- You never evaluate Check A (`MODE`), Check A2 (discussion trigger), Check B (team membership),
  Check C (pending result) or Check D. Whatever `MODE` the launch prompt carries, you run the
  health pass.
- You never take **Part 2 (discussion)** — this file forbids it — and you never take **Part 3
  (no-team exit)**, which would mean your health pass never ran.
- Your `MY_TEAM` is empty on every cycle. That is correct by design: teams are for agents that run
  experiments. It is not the coordination bug Part 3 exists to report, so never report it, and
  never add yourself to `teams/roster.md`.
- `branch_taken = "monitor-health"` is set for you at Check A0 and carried into Part 6a.

Part 3.5 boots credentials and the roster itself (you skipped Check B, so nothing is loaded when
you arrive), then runs the health pass below **once** and exits via Part 6 (6a → 6d → 6e).
If HEARTBEAT and this file ever appear to disagree about your route, they do not: both name Part
3.5, and there is nothing to resolve at runtime.

## What monitor is FOR

1. **Phase 3 health check:** release stale claims, post `[AUDIT]` summaries, flag coordination bugs.

**Cadence — the orchestrator invokes you once per cycle. Run this ONCE, then exit — do not loop or
sleep.** You are not a daemon and you do not schedule yourself. A sleep-loop holds an agent slot for
the whole rotation and blocks the next invocation; if there is more to check, the next cycle's
invocation checks it.

## What monitor is NOT for

- **Bootstrap.** `launch.py` already created the workshop, registered and subscribed all
  agents, created the main workspace with its seeded files, and posted the kickoff. PHASES.md
  Phase 1 documents that state; it is not a to-do list. Never re-run any of it — the
  ClawInstitute DB is shared and re-running duplicates state.
- **Cold-start team formation.** launch.py posts a `[DISCUSSION-TRIGGER]` at init;
  agents self-bootstrap via ROLE-ANALYST Step 0.25 (the lexicographically-last
  registered analyst identity writes `teams/roster.md` after every registered
  non-monitor identity in the parallel wave has contributed and voted).
  Monitor does NOT intervene.
- **Mid-run regroup.** Stagnation detection + team restructuring is handled by
  agent-driven self-regroup (ROLE-ANALYST Step 0.2 / 0.25). Any analyst can
  post a `[DISCUSSION-TRIGGER]` when stagnation is detected. Monitor does NOT
  intervene.
- **Deciding which hypotheses to test.** Agents propose; monitor does not override.

If you find yourself wanting to write `teams/roster.md` or pick hypotheses,
stop — that's an agent's job. Post an `[AUDIT]` summary if the system seems
stuck and exit.

## Health Check (the orchestrator invokes you once per cycle — run this ONCE, then exit; do not loop or sleep)

**Read this before the code.** Mutual exclusion in this run is a per-item file,
`claims/{exp_id}.md` in the team workspace, whose holder is `updatedBy` on **version 1** — an
immutable record, so every agent computes the same winner no matter when it reads. The queue item
stays in `pending:` the whole time; `pending:` is the single source of truth for what work exists.

Two consequences define your sweep:

- **You never edit `queue.md`.** Not `pending:`, not `completed:`, not a legacy `claims:` map (which
  excludes nothing now and is inert bookkeeping — report it if it accumulates, do not clean it).
  Your only write is the claim file.
- **Releasing cannot lose an experiment.** The item is still in `pending:`; a released claim is
  re-claimable immediately. Release by writing a `released: true` marker that names the successor
  path `claims/{exp_id}.r{n}.md` — never by deleting, and never by rewriting the same path in
  place, because v1 of a path is frozen to its first holder forever.

```python
def health_check(main_ws_id, roster):
    # `roster` here is the roster.md FRONTMATTER mapping (it has a `teams` key) — that is
    # `roster_fm` in HEARTBEAT Part 3.5a, not the bare team mapping bound there as `roster`.
    # 1. Count outcomes from the CANONICAL LEDGER: logs/experiments.jsonl.
    #    One flat JSON record per line; the keys you need — `team` and `outcome`
    #    — are already on every row, so no search query is involved.
    #    outcome ∈ KEEP | NEAR_MISS | DISCARD | REJECTED_TEST | FAILED.
    #    Early rows may be missing keys — always use .get() with a default.
    #
    # EVERY module import this function needs is HERE, at the top, and nowhere else.
    # This is not style. An `import json` placed LATER in this same function makes `json` a
    # FUNCTION-LOCAL name for the WHOLE of health_check, so the `json.loads(line)` a few lines
    # down raises `UnboundLocalError` — which the `except Exception: continue` below swallows.
    # `rows_by_team` then stays {} forever and the failure is SILENT: the ts sort, the baseline
    # filter, the stagnation streak and the duplicate-exp_id assertion all read an empty ledger,
    # every team reports 0 cycles, and no streak can ever reach the threshold. That is exactly
    # the failure step 3b tells you to suspect. NEVER add an `import` further down this function.
    import json, os, re
    from collections import Counter
    from datetime import datetime, timezone
    from pathlib import Path
    rows_by_team = {}
    log = Path(f"{FOCUS_ROOT}/logs/experiments.jsonl")
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                rows_by_team.setdefault(rec.get("team", "?"), []).append(rec)

    # Fallback if the ledger is missing or a team has no rows in it: list
    # `results/` in the main workspace and parse each file's frontmatter
    # (`team`, `outcome`). Producers tag `team:{name}` and write
    # `results/{exp_id}.md` — there is no `zone` field anywhere; never query one.

    # 1b. RUN-GLOBAL COORDINATION ASSERTION: no exp_id may appear TWICE in the
    #     ledger. A repeat is the duplicate-execution signature — two agents ran the
    #     same item because ownership was decided by something mutable (a PUT status
    #     code, a read-back, the claim file's current contents) instead of by
    #     `updatedBy` on VERSION 1 of the claim file. This server enforces neither
    #     `If-Match` nor `If-None-Match`, so only the immutable v1 record decides.
    #     Report it as a COORDINATION DEFECT, never as two independent confirmations
    #     of a result: a duplicated row is ONE experiment counted twice, and calling
    #     it replication inflates every count downstream of it.
    _all_rows = [r for rows in rows_by_team.values() for r in rows]
    dup_exp_ids = [e for e, n in Counter(
        r.get("exp_id") for r in _all_rows if r.get("exp_id")).items() if n > 1]

    # Accumulators for the per-team assertions (health check 3a below) and the claim sweep.
    future_stamps = []
    axis_mismatch = []
    released_claims = []   # stale claims handed back this pass
    stuck_claims = []      # look stale by age, but the holder is demonstrably still working

    # ---- Claim files: the run's mutual exclusion, and the ONLY thing you ever write ----
    # An item's claim is the file `claims/{exp_id}.md` in the TEAM workspace. Its holder is
    # `updatedBy` on VERSION 1 of that file — an immutable record every agent reads the same way
    # at any time, so there is no interleaving that produces two winners and no settle delay to
    # tune. The queue item itself NEVER leaves `pending:`.
    #
    # INVARIANT: THE MONITOR NEVER EDITS `queue.md` — not `pending:`, not `completed:`, not a
    # legacy `claims:` map. It only marks claim files released. That invariant is what makes a
    # release safe: the item is still sitting in `pending:`, so a released claim is immediately
    # re-claimable and NOTHING CAN BE LOST. (The old sweep popped a claim while the item was out
    # of `pending:`, which lost the experiment permanently.) A monitor PUT to queue.md would also
    # be unsafe on its own terms: `If-Match` is unenforced, so it can clobber a concurrent write.
    STALE_CLAIM_MIN = 30    # minutes; the age half of the staleness test
    MAX_CLAIM_ATTEMPTS = 8  # bound the released-chain walk; a longer chain is an [AUDIT] line

    def claim_path_for(exp_id, attempt=0):
        """attempt 0 is the path every FIRST claim uses; each release opens the next attempt."""
        return f"claims/{exp_id}.md" if attempt == 0 else f"claims/{exp_id}.r{attempt}.md"

    # Bind the two names every age computation below depends on (`now` here, `parse` next).
    # Without these, `now - parse(...)` NameErrors and the whole stale-claim sweep dies silently.
    # ONE spelling of "now" everywhere: tz-aware UTC. (`datetime`/`timezone` are imported at the
    # top of this function with everything else — see the note there on why nothing imports here.)
    now = datetime.now(timezone.utc)

    def parse(ts):
        """Parse an ISO-8601 timestamp to a tz-AWARE datetime. A naive value would raise on
        subtraction against `now`, so anything without an offset is treated as UTC."""
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    def is_future(ts):
        """True only when `ts` parses AND lands after `now`. Every clock assertion below goes
        through this, so it must never raise: an UNPARSEABLE stamp is a different defect from
        a FUTURE one, and letting it propagate would take the whole sweep down over one
        malformed line in one file."""
        try:
            return bool(ts) and parse(ts) > now
        except Exception:
            return False

    def ts_key(r):
        """Sort key for ledger rows: chronological, missing-or-unparseable `ts` LAST.
        `logs/experiments.jsonl` is append-only and its on-disk order is whatever the harvest
        appended — a harvest that walks the forum newest-first writes the file backwards. Every
        count that depends on ORDER (above all the stagnation streak, which walks from the newest
        row until it hits a real result) must therefore sort first, or it reads the run in
        reverse and reports the opposite verdict. Nulls sort LAST, never first: a row with no
        stamp must not masquerade as the oldest row in the file. Feed this to a STABLE sort so
        rows sharing a key keep their ledger order."""
        try:
            return (0, parse(r.get("ts"))) if r.get("ts") else (1, now)
        except Exception:
            return (1, now)

    def is_baseline(r):
        """The shared BASELINE probe — the run's starting-point measurement, not an experiment.
        It is ledgered with `outcome: KEEP` under whichever team ran it, so an unfiltered walk
        hits it, reads a promotion, and RESETS the stagnation streak to 0 — the run reads as
        productive at exactly the moment it has stopped being so, and the team's KEEP count is
        inflated by a row that tested no mechanism. Skip it the way FAILED is skipped: it
        neither advances nor breaks a streak. Check BOTH tags — either one alone may be what
        the producer wrote."""
        return (r.get("exp_id") == "baseline_shared"
                or str(r.get("infrastructure_probe", "")).lower() == "true")

    def parse_claim(content):
        """Claim files are flat `key: value` lines, not frontmatter. Values stay strings."""
        fm = {}
        for line in (content or "").splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        return fm

    def claim_v1_holder(ws_id, path):
        """Holder = `updatedBy` on version 1. None when there is no readable history."""
        r = requests.get(f"{API}/workspaces/{ws_id}/files/{path}/history", headers=HEADERS)
        if r.status_code != 200:
            return None
        body = r.json()
        hist = body.get("history", body.get("data", [])) if isinstance(body, dict) else body
        for h in (hist or []):
            if int(h.get("version", 0) or 0) == 1:
                return h.get("updatedBy")
        return None

    def resolve_claim(ws_id, exp_id):
        """Newest attempt for `exp_id`: walk 0,1,2,... while the file exists. None ⇒ never
        claimed. `released` True ⇒ the item is AVAILABLE and the next claim belongs at
        attempt+1 — never at this path, whose v1 is frozen to the old holder forever."""
        live = None
        for attempt in range(MAX_CLAIM_ATTEMPTS):
            path = claim_path_for(exp_id, attempt)
            r = requests.get(f"{API}/workspaces/{ws_id}/files/{path}", headers=HEADERS)
            if r.status_code != 200:
                break
            fm = parse_claim((r.json() or {}).get("content"))
            live = {"exp_id": exp_id, "path": path, "attempt": attempt,
                    "holder": claim_v1_holder(ws_id, path),
                    "claimed_at": fm.get("claimed_at"),
                    "released": str(fm.get("released", "")).lower() == "true"}
        return live

    def holder_session_ended(holder, exp_id):
        """True when `holder` is demonstrably not working on `exp_id` any more. READ-ONLY: you
        may READ another agent's AGENT.md and sentinel, never write either."""
        a_dir = Path(f"{FOCUS_ROOT}/agents/{holder}")
        sent = a_dir / "workspace" / "result_latest.json"
        if sent.is_file():
            try:
                s = json.loads(sent.read_text())
            except Exception:
                s = {}
            # NEVER gate the pid check on a status VALUE. `status` tracks the TRAIN eval only and
            # flips to "complete" the moment it returns — while the held-out TEST eval (Step 4c,
            # timeout=3600) may still be running for another hour. Gating on status == "running"
            # therefore declares a live agent dead for the entire duration of the test gate, and
            # releasing there hands the same experiment to a second agent: the exact duplicate
            # execution claim files exist to prevent, reintroduced through the stale-claim path.
            # An agent is alive iff its PID is alive. The only thing that ends a cycle is the
            # result being posted.
            if s.get("exp_id") == exp_id and not s.get("posted_to_workshop"):
                try:
                    os.kill(int(s.get("pid")), 0)
                    return False   # the session is LIVE (train eval, test gate, or writing up)
                except Exception:
                    pass           # pid gone: the session really did die mid-cycle
        # `last_seen` is written by HEARTBEAT Part 6a at the END of every session. No check-in
        # inside the stale window — or none on record at all — means the session has ended.
        md = (a_dir / "AGENT.md").read_text() if (a_dir / "AGENT.md").is_file() else ""
        m = re.search(r'^last_seen:\s*"?([^"\n]+?)"?\s*$', md, re.MULTILINE)
        if not m:
            return True
        # A `last_seen` in the FUTURE is UNUSABLE, never maximal freshness. `now - last_seen`
        # goes NEGATIVE, and a negative age passes the "checked in recently" test no matter how
        # far ahead the stamp is — so an agent whose clock runs ahead of UTC reads as live
        # forever, its claim never ages out, and the item is pinned to a dead holder for the
        # rest of the run. That is the exact failure this sweep exists to prevent, arrived at
        # from the opposite direction.
        # Fall back to the only other evidence there is: the PID check above, which already
        # returned False if the holder was demonstrably alive. Reaching this line means it did
        # not, so treat an unusable stamp exactly like an ABSENT one and call the session ended.
        # (The clock defect itself is reported once, run-globally, in step 1c — do not append it
        # here too, or one bad clock produces one [AUDIT] line per claim that agent holds.)
        if is_future(m.group(1)):
            return True
        return (now - parse(m.group(1))).total_seconds() / 60 > STALE_CLAIM_MIN

    def release_claim(ws_id, c):
        """Hand a stale item back. This is NOT a delete: overwrite the claim file with a
        `released: true` marker that names the successor path, so the NEXT claim is version 1 of
        a file nobody has written yet. Why a marker and not DELETE: `DELETE /posts/{id}` is 403
        on this server and file DELETE is unverified — and if DELETE tombstoned the file while
        KEEPING its history, the next PUT would land as vN+1 with v1 still naming the old holder,
        so the item would be UNCLAIMABLE forever (a silent deadlock is worse than a lost item).
        Rewriting in place WITHOUT a successor path has that same defect. The marker is
        idempotent — a later sweep rewrites the same bytes — and it needs no server capability
        beyond PUT + history, both of which are verified."""
        nxt = claim_path_for(c["exp_id"], c["attempt"] + 1)
        requests.put(f"{API}/workspaces/{ws_id}/files/{c['path']}", headers=HEADERS,
                     json={"content": (f"holder: {c['holder']}\n"
                                       f"claimed_at: {c.get('claimed_at') or ''}\n"
                                       f"released: true\n"
                                       f"released_at: {now.isoformat()}\n"
                                       f"released_by: {AGENT_NAME}\n"
                                       f"release_reason: stale_claim\n"
                                       f"next_claim_path: {nxt}\n")})
        return nxt

    # 1c. RUN-GLOBAL CLOCK ASSERTION — `agents/*/AGENT.md` `last_seen`. (It sits here rather
    #     than beside 1b because it needs `now`, `parse` and `is_future`, all bound just above.)
    #     Of every timestamp this pass reads, this is the one with the sharpest downstream
    #     consequence: `holder_session_ended` subtracts it from `now`, so an agent whose local
    #     wall clock is labelled `+00:00` while running ahead of real UTC makes its own ages
    #     negative and its claims un-ageable. Report each offending agent ONCE, here — the
    #     liveness check itself simply refuses to trust the stamp. Report only: you never write
    #     another agent's AGENT.md, and a clock defect is a finding, not something you repair.
    for _a in sorted(os.listdir(f"{FOCUS_ROOT}/agents")):
        _p = Path(f"{FOCUS_ROOT}/agents/{_a}/AGENT.md")
        if _a.startswith(".") or not _p.is_file():
            continue
        _m = re.search(r'^last_seen:\s*"?([^"\n]+?)"?\s*$', _p.read_text(), re.MULTILINE)
        if _m and is_future(_m.group(1)):
            future_stamps.append(
                f"agents/{_a}/AGENT.md last_seen={_m.group(1)} (now={now.isoformat()}) "
                f"— clock ahead of UTC; that agent's claims cannot age out")

    for team_name, team in roster["teams"].items():
        team_ws_id = team["workspace_id"]
        # SORT BEFORE YOU WALK. Every count below is order-dependent and the ledger's on-disk
        # order is whatever the harvest appended, so sort your OWN copy first (never rewrite
        # the ledger — it is append-only and not yours). Nulls last, per `ts_key`.
        results = sorted(rows_by_team.get(team_name, []), key=ts_key)
        # Count THREE separate things:
        #   - stagnation streak = consecutive DISCARD + REJECTED_TEST since the last
        #     KEEP *or NEAR_MISS* (both are real scientific results: a mechanism was
        #     tested and rejected). A NEAR_MISS both breaks and does not advance the
        #     streak: it passed both gates, and by construction some other candidate
        #     WAS promoted in that rotation, so the run is demonstrably not stuck.
        #   - near_miss_count = candidates that passed train AND test but lost the
        #     promotion race. Healthy signal, not a fault — see the table below.
        #   - failed_count = FAILED cycles (diff did not apply, or eval returned no score).
        #     FAILED tested NOTHING. Never add it to the stagnation streak and never
        #     report it as a tested mechanism.
        # Dropped from ALL of them: the shared baseline probe (`is_baseline`). It carries
        # `outcome: KEEP` but tested no mechanism, so it is neither one of this team's cycles
        # nor a promotion that can break a streak.
        counts = Counter((r.get("outcome") or "?") for r in results if not is_baseline(r))

        # The streak walks BACKWARDS from the newest row — which is why `results` had to be
        # sorted above; on an unsorted (or reversed) ledger this walk starts at the wrong end
        # of the run and returns a number that describes history running the other way.
        streak = 0
        for r in reversed(results):
            o = (r.get("outcome") or "")
            if o == "FAILED" or is_baseline(r):
                continue          # tested nothing: skipped, so it neither advances nor resets
            if o in ("KEEP", "NEAR_MISS"):
                break             # a real promotion (or a candidate that earned one): streak ends
            if o in ("DISCARD", "REJECTED_TEST"):
                streak += 1

        # 2. STALE CLAIM SWEEP — over claim FILES. The queue is read ONLY (see the invariant).
        queue_raw = requests.get(
            f"{API}/workspaces/{team_ws_id}/files/queue.md",
            headers=HEADERS).json()
        queue = parse_frontmatter(queue_raw)
        team_claims = []   # every claim file resolved this pass; clock-checked in 3a(i)

        #    A claim is STALE only when ALL THREE hold:
        #      (a) it is older than STALE_CLAIM_MIN,
        #      (b) no `results/{exp_id}.md` exists in the main workspace, and
        #      (c) the holder's SESSION HAS ENDED — no live eval for this exp_id in its
        #          sentinel, and no `last_seen` check-in inside the stale window. A FUTURE
        #          `last_seen` is unusable and counts as NO check-in (see
        #          `holder_session_ended`), because a negative age would otherwise pin the
        #          item to that holder forever. That trade has one residual case, and it is
        #          the one this rule can get wrong: a holder that is genuinely ALIVE, has not
        #          yet written `result_latest.json` (ROLE-CPU writes that sentinel at its
        #          Step 4, i.e. after claim + champion read + diff design), and whose clock
        #          runs ahead of UTC, leaves no evidence of liveness at all and is released.
        #          It needs all three at once — skewed clock AND pre-sentinel AND >30 min —
        #          and the alternative pins every skewed-clock item for the rest of the run.
        #          When 1c reports a future `last_seen` for an agent, say in the `[AUDIT]`
        #          that any release under that holder rests on age alone.
        #    (c) is not optional. Age alone releases items out from under agents that are
        #    merely slow, which manufactures the two-agents-one-experiment bug from the other
        #    direction. Because the item never left `pending:`, an over-eager release is the
        #    only way this sweep can do damage — so make the holder prove it is gone.
        for it in (queue.get("pending") or []):
            eid = it.get("id")
            if not eid:
                continue
            c = resolve_claim(team_ws_id, eid)
            if c and c.get("claimed_at"):
                team_claims.append(c)   # collected for the 3a(i) clock assertion below
            if not c or c["released"] or not c["holder"]:
                continue          # never claimed, or already handed back — nothing to do
            age_min = ((now - parse(c["claimed_at"])).total_seconds() / 60
                       if c.get("claimed_at") else float("inf"))
            result_exists = requests.get(
                f"{API}/workspaces/{main_ws_id}/files/results/{eid}.md",
                headers=HEADERS).status_code == 200
            if age_min <= STALE_CLAIM_MIN or result_exists:
                continue
            if not holder_session_ended(c["holder"], eid):
                stuck_claims.append(f"{team_name}/{eid}: holder {c['holder']} still live at "
                                    f"{age_min:.0f} min — NOT released")
                continue
            nxt = release_claim(team_ws_id, c)
            released_claims.append(f"{team_name}/{eid}: {c['path']} released "
                                   f"(holder {c['holder']}, {age_min:.0f} min) -> next {nxt}")

            # THAT IS THE WHOLE RELEASE. The item is still in `pending:` and is claimable the
            # moment the marker lands — you never edit the queue to give it back, and you never
            # re-create a row. Nothing is lost even if this sweep is wrong.
            #
            # CRITICAL: do NOT WRITE `agents/{holder}/workspace/result_latest.json`. Reading it
            # above is how you tell a dead holder from a live one; clobbering it re-creates the
            # orphaned-result bug, because that sentinel is what HEARTBEAT Part 0 Check C /
            # Part 5 use to resume an unposted result. If the holder does come back, Part 5's
            # own 5a2 ownership check sees this release and closes out as FAILED(claim_lost) —
            # it will not post a second result for an item somebody else now owns.

        # 3. Check queue depth
        pending = queue.get("pending", [])
        if len(pending) < 3:
            # Alert analyst to propose more experiments
            pass

        # 3a. PER-TEAM COORDINATION ASSERTIONS — report only, never repair.
        #
        #  (i) No file may be stamped in the FUTURE. Every timestamp in this system
        #      is `datetime.now(timezone.utc).isoformat()`. A local wall-clock string
        #      labelled `+00:00` lands hours ahead of real UTC, and every age
        #      computation downstream (`now - parse(...)`) then goes negative — stale
        #      claims never look stale and grace periods never expire.
        #      Assert on the timestamps this pass ACTUALLY READS, ordered by how much
        #      damage a bad one does:
        #        - `claims/*.md claimed_at` — feeds `age_min` in step 2. A future stamp
        #          makes the age negative, the claim never trips STALE_CLAIM_MIN, and the
        #          item stays pinned to a holder that may be long dead. Checked here.
        #        - `agents/*/AGENT.md last_seen` — feeds holder liveness. Same negative-age
        #          failure; asserted run-globally in 1c, so it is not repeated per team.
        #        - `queue.md updated_at` — the only one of the three with NO age math
        #          downstream, so it is the cheapest to be wrong about. It stays checked
        #          because a clock wrong here is wrong for the other two as well, and this
        #          is the earliest place it shows.
        for label, ts in ([("queue.md updated_at", queue.get("updated_at"))]
                          + [(f"{c['path']} claimed_at", c.get("claimed_at"))
                             for c in team_claims]):
            if is_future(ts):
                future_stamps.append(
                    f"{team_name}/{label}={ts} (now={now.isoformat()})")
        # Do the same for any other file whose frontmatter you read this pass
        # (strategy.md, dead_ends.md, non_generalizable.md) — and prefer `is_future`
        # over a bare `parse(ts) > now`, so one malformed stamp cannot abort the sweep.

        # (ii) Every ledger row's `axis` must match the queue `completed:` row for
        #      the same exp_id. A mismatch is the state-bleed signature: an agent
        #      carried one item's variables into another item's write, so the
        #      result on record is attributed to the wrong axis. That poisons the
        #      empirical-priors ranking and the axis-closure count downstream.
        completed_axis = {it.get("id"): it.get("axis")
                          for it in (queue.get("completed") or []) if it.get("id")}
        for r in results:
            eid = r.get("exp_id")
            if eid in completed_axis and r.get("axis") != completed_axis[eid]:
                axis_mismatch.append(
                    f"{eid}: ledger axis={r.get('axis')!r} "
                    f"vs queue axis={completed_axis[eid]!r}")

        # Report `future_stamps`, `dup_exp_ids`, `axis_mismatch`, `released_claims` and
        # `stuck_claims` in the [AUDIT]. Do NOT rewrite the ledger, the queue, or any result
        # file to make them agree — the divergence IS the finding, and erasing it erases the
        # evidence. The one thing you ever repair is a stale claim FILE, per step 2.

        # 3b. SELF-CHECK on your own counting — do not skip.
        #     If a team shows 0 results but its queue has completed items (or
        #     released claims, or a `completed:` list), your read is wrong, not the
        #     team. Say exactly that in the [AUDIT] — "0 results for {team}; queue
        #     shows N completed, so my ledger read is suspect" — rather than
        #     reporting 0 cycles / 0 KEEP as fact. A silently-empty query makes
        #     every team look dead and makes the stagnation streak unreachable.

    # 4. (no compute-utilization check — evaluation runs on a remote CPU-only
    #    worker pool, not on any local device; there is no nvidia-smi to poll.)
```

## Outcome Vocabulary

| Outcome | Meaning | Counts as a tested mechanism? |
|---|---|---|
| `KEEP` | Passed the train gate AND the held-out test gate; promoted to champion. | Yes |
| `NEAR_MISS` | Passed the train gate AND the held-out test gate, but was **not** promoted: it lost the promotion race (an equal-or-better champion landed while it evaluated, or `champion.md` no longer records its `exp_id`). Auto-re-queued as `{exp_id}_stack` to be re-tested on the NEW champion. Written to **neither** `dead_ends.md` nor `non_generalizable.md`. | Yes — and it **succeeded** |
| `DISCARD` | Ran, but did not improve on train (or was invalid on train). | Yes |
| `REJECTED_TEST` | Improved on train, then failed the held-out test gate — test invalid, insufficient test improvement, or both. Recorded in the team's `non_generalizable.md`; the mechanism family is abandoned. | Yes |
| `FAILED` | Infra/harness failure: the eval returned no score, the diff did not apply, or the agent aborted on scope. **Tested nothing.** | **No** |

Every `[AUDIT]` that summarises cycle health must break the counts out by all five, and must
state the `FAILED` count separately from the scientific outcomes.

**`NEAR_MISS` is a health signal, not a fault.** It means two agents in one rotation both produced
promotable candidates and only one could win — the run is finding real improvements faster than it
can promote them. Report the count, but never flag it as a problem, never write it into a dead-end
file, and never let it advance a stagnation streak: by construction another candidate WAS promoted
in that rotation. If `NEAR_MISS` becomes *frequent*, the only thing worth saying to the operator is
that rotation sizing may be worth a look — the roster is racing itself.

Because a `NEAR_MISS` is positive evidence, it also must never feed the falsification predicate:
it increments a team's `supported_keeps` (exactly like a `KEEP`), never `refuted_discards` and
never `rejected_test`. If you see a `NEAR_MISS` counted as refuting in a team's `strategy.md`,
flag it in the `[AUDIT]` — do not edit the file yourself.

**`FAILED` is an operator signal, not a science signal.** A team (or the run) showing many
`FAILED` cycles means the eval pool is unhealthy — not that the search space is exhausted. If
`FAILED` exceeds ~20% of recent cycles, say so explicitly in the `[AUDIT]` and address it to the
operator. Do not attempt to fix it: the eval pool is SHARED infrastructure. Never flush Redis,
restart workers, or touch other clients' checkouts.

Because a `FAILED` cycle tested nothing, it must never appear in a team's `dead_ends.md` or
`non_generalizable.md`, never contribute to an axis-closure count, and never advance a
stagnation streak. Its queue item is supposed to go back to `pending:` for a retry — if you see a
`FAILED` experiment recorded as a tested mechanism, or its item closed out instead of re-queued,
flag it in the `[AUDIT]` (do not fix the file yourself).

## Stagnation Threshold

**10 consecutive DISCARD/REJECTED_TEST results** in a single team is the stagnation mark →
**report it in your `[AUDIT]` as `streak=S`, and stop there.** You do not trigger the Phase 4
restructuring discussion and you do not post a `[DISCUSSION-TRIGGER]`: the regroup is
analyst-driven (ROLE-ANALYST Step 0.2 / 0.25, and PHASES.md Phase 4 says the same — "the monitor
does not restructure teams; it reports via `[AUDIT]`"). See "What monitor is NOT for". This is
why the streak code above computes `streak` and takes no action on it — the number IS the
deliverable.

`FAILED` cycles are skipped, not counted, and do not reset the streak.
`NEAR_MISS` cycles are also skipped — they are not non-promotions in the scientific sense (both
gates passed) and a rotation containing one demonstrably produced a promotion, so counting them
would fire a false regroup. A `{exp_id}_stack` re-test is NOT skipped: its outcome counts exactly
like any other result on that axis (a DISCARD/REJECTED_TEST advances the streak, a KEEP/NEAR_MISS
breaks it). It consumed real eval-pool time and returned real evidence about whether the mechanism
survives on the new champion, and exempting it would let a team emit stack re-tests indefinitely
while never registering as stalled.

Count the streak over rows **sorted by `ts`**, and skip the shared **baseline probe** wherever it
appears. The baseline is infrastructure wearing a `KEEP`: it measured the run's starting point and
tested no mechanism, so it must never reset a streak, never count toward a team's KEEPs, and never
stand in for a promotion. An unsorted walk and an uncounted baseline fail the same way — a stalled
team reports itself as healthy — which is the one direction this check must never be wrong in.

## Reference: how teams are formed (ANALYST-owned — you never do this)

**This section is REFERENCE, not instructions.** It describes what the analysts do in ROLE-ANALYST
Step 0.25 so that you can recognise a malformed roster and flag it. Nothing here is an action for
you: you never read the kickoff thread to extract hypotheses, never pick templates, never write
`teams/roster.md`. If you find yourself doing any of it, stop and post an `[AUDIT]` instead.

Teams do NOT partition the search space by axis (e.g. "arch / optim /
sched"). Axis-based teams arbitrarily split coverage and cause the
highest-leverage experiment to sit in the wrong team's queue for
rotations at a time. Instead, the analysts form teams around **falsifiable
hypotheses** about what is currently limiting the champion.

The resolving analyst reads the kickoff `[DISCUSSION]` thread and extracts 3
competing hypotheses — each one a specific, testable claim about the bottleneck —
then creates one team per hypothesis. Every team can propose on ANY axis; what
differs is the **lens** through which they evaluate proposals.

Hypothesis templates the analysts choose 3 from:

- **H-step-size:** "The optimizer takes too-conservative steps. A
  bolder step rule / trust region will reach the true minimum in fewer
  force calls without under-relaxing (stays valid: `mean_rel_energy >= 1.0`)."
- **H-curvature:** "The search direction is poorly conditioned. Better
  Hessian initialization or preconditioning will cut force calls per
  molecule."
- **H-line-search:** "Force calls are wasted inside the line search /
  backtracking. A cheaper or smarter acceptance rule will reduce
  total force calls."
- **H-internal-coords:** "The internal-coordinate construction (bonds /
  angles / dihedrals / near-linear angles / impropers, and when they
  are rebuilt) is suboptimal for some molecule classes. A better
  primitive set / rebuild policy will cut force calls."
- **H-restart-reblend:** "Trajectories stall and waste calls. A restart /
  partial curvature re-blend on detected stalls will recover progress
  without extra force calls."

NOTE: the convergence test is **fixed and external** — there is no "retune the stopping criterion"
hypothesis. Any proposal whose mechanism is to make `converged()` fire on an under-relaxed geometry
(displacement caps, same-geometry polishing, energy-guard / gate-margin tricks, per-molecule gate-trip
branches) is **cheating** and the analysts must reject it rather than turn it into a team hypothesis.

Separately and equally forbidden: any proposal that branches on **molecule identity or an exact
composition/graph fingerprint** — element formula, exact atom or element counts, a specific
local-environment signature, a per-chemotype selector — to change *any* behavior, whether a stopping
shortcut or a "mechanism" (e.g. a per-chemotype Hessian/momentum/torsion selector). That is train-set
memorization and is forbidden **even when it passes validity and lowers train `mean_rel_steps`**, and
even though it never trips `converged()` early. It is not a legitimate team hypothesis; if you see one
in a roster or a `strategy.md`, flag it in the `[AUDIT]`. Full contract in
`task/TASK.md` ("What counts as cheating").

Each team's `strategy.md` carries these frontmatter fields — their absence is itself an `[AUDIT]`-worthy
coordination bug:

```yaml
hypothesis: H-step-size
prediction: "Experiments that raise the max step / trust radius cut mean_rel_steps ≥5% and stay valid will KEEP"
falsification: "If 3 rotations of prediction-consistent experiments all DISCARD, hypothesis is falsified"
age_rotations: 0
supported_keeps: 0
refuted_discards: 0
rejected_test: 0
```

**What you check on these counters each cycle:**

- If a team's `age_rotations ≥ 3` AND `supported_keeps == 0` AND
  `refuted_discards + rejected_test ≥ 3`, the hypothesis is falsified. (A team with any
  `NEAR_MISS` cannot reach this state: a `NEAR_MISS` increments `supported_keeps`.) Post
  `[HYPOTHESIS-FALSIFIED]` to the workshop, and note the falsified hypothesis in your `[AUDIT]`.
  That is the whole of your involvement — whether and how the team re-forms is the analysts' call.
- Teams that produce KEEPs are "hot" — their supported_keeps increments
  and the queue ranker gives their subsequent proposals priority.
- Teams do NOT have axis ownership. The old "stay within your
  dimension" rule is abolished. A CPU-eval agent on H-curvature may
  claim a line-search experiment if the team's hypothesis predicts
  it will KEEP.

See `system/reference/PHASES.md` Phase 2 for the `create_team()` helper (the resolving analyst
runs it — you do not).

## Length Expectations

You produce diagnostics, not prose. Keep every artifact tight:

- `[AUDIT]` post: **≤ 20 lines.** Per team, one line: `team — N cycles: k KEEP / m NEAR_MISS /
  d DISCARD / r REJECTED_TEST / f FAILED, streak=S, queue=P pending, stale claims released=C`. Then at most
  3 bullets of flagged problems. No restatement of the task, no methodology narration, no
  recommendations the agents did not ask for.
  The four coordination assertions (future timestamps — `claims/*.md claimed_at` and `queue.md
  updated_at` from health check 3a, `agents/*/AGENT.md last_seen` from 1c — plus duplicate
  `exp_id`, ledger/queue `axis` mismatch, plus each stale claim file released or deliberately left
  alone in step 2) are reported **in addition**, one line each, and only when they fire. Name them as coordination defects, not as scientific findings. They take precedence over
  the 3 discretionary bullets: drop a discretionary bullet before dropping an assertion.
- `[HYPOTHESIS-FALSIFIED]` post: **≤ 8 lines** — team, hypothesis, the counters that tripped it.
- Any file you write: frontmatter plus ≤ 10 lines of body.

If you have nothing new since the last audit, post nothing and exit.

## What You NEVER Do

- Take HEARTBEAT Part 2 (discussion) or Part 3 (no-team exit) — your branch is Part 3.5, always
- Run experiments or modify training code
- Claim experiments from any queue
- Write `queue.md` — no `pending:` edit, no `completed:` edit, no re-created rows, not even to
  clear a leftover `claims:` key. A stale claim is released by marking its claim FILE, and that
  is the only file you write all cycle
- Delete a claim file, or overwrite one in place without naming a successor path (either leaves
  v1 naming the old holder, which makes the item unclaimable forever)
- Write result files
- Overwrite champion.md — including `metric_value` / `test_metric_value` (CPU-eval agents
  advance both, together, only on an accepted promotion)
- Re-run any part of bootstrap (workshop, agent registration, subscriptions, workspace creation,
  seeded files, kickoff post) — `launch.py` did all of it
- Touch the shared eval pool: no Redis flush, no worker restart, no edits to other clients'
  remote checkouts
