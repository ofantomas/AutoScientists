#!/usr/bin/env python3
"""Optional meta-improvement diagnostics for the molopt-relax-steps task.

Reads logs/experiments.jsonl from the run directory and prints HARD SIGNALS for the
orchestrator's judgment-based meta-improvement pass (see
system/reference/META-IMPROVEMENT.md). This script is NOT imported anywhere — the
meta-improvement hook works without it. It is a convenience the orchestrator MAY run:

    python3 task/meta_diagnostics.py                       # auto-detects the run dir (parent of task/)
    python3 task/meta_diagnostics.py <FOCUS_ROOT>
    python3 task/meta_diagnostics.py <FOCUS_ROOT> --teams team_a,team_b,team_c

PASS `--teams` WHENEVER THE ROSTER IS KNOWN — the bare invocation is the degraded one.
`--teams` (equivalently `AS_EXPECTED_TEAMS=team_a,team_b`) hands this script the roster it
cannot read for itself: `teams/roster.md` lives in the workspace behind the API, and this
script is deliberately offline and stdlib-only. It is what makes the idle-team check
possible at all — see `has_low_activation` below. Without it the check answers a strictly
weaker question (a team that ran before and went quiet, rather than a rostered team that has
never run at all), and it labels itself as such: it never reports "no idle team" on the
strength of not having looked. Read `has_low_activation: False` with no roster as "not
checked".

Three counting rules this script shares with runbook Step 5g, ROLE-MONITOR and LOGGING.md —
all four must agree or the same run gets two different verdicts:

  * rows are sorted by `ts` before any trailing window is taken. The ledger is supposed to
    be chronological already; the sort is defensive, because a `last N` window over a
    newest-first file reads the OLDEST rows and can invert the stagnation verdict outright.
    Rows with a null/unparseable `ts` sort LAST, never first.
  * FAILED rows are excluded from every rate denominator — they tested nothing.
  * the shared baseline probe is infrastructure, not a scientific result, and is excluded
    outright rather than counted as a KEEP for whichever team happened to run it.

It NEVER raises fatally on missing / empty / malformed logs — it prints
"insufficient data" and exits 0, so it can never block or crash the meta pass.
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def load_experiments(focus_root: Path):
    p = focus_root / "logs" / "experiments.jsonl"
    rows = []
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows, p


def fnum(r, *keys):
    for k in keys:
        v = r.get(k)
        if v is None or str(v) in ("", "None"):
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def exp_id_of(r):
    """Canonical ledger key is `exp_id`; the others are tolerated legacy spellings."""
    return str(r.get("exp_id") or r.get("experiment_id") or r.get("id") or "").strip()


def team_of(r):
    return str(r.get("team") or r.get("axis") or "?")


def outcome_of(r):
    return (r.get("outcome") or "?").upper()


def ts_key(r):
    """Chronological sort key — UNDATED ROWS SORT LAST, not first.

    A null or unparseable `ts` must never be read as "oldest": it would be pushed out of
    every trailing window, vanish from the stagnation signal entirely, and leave a staler
    real row occupying the slot it should have had. Sorting it last keeps the newest
    evidence — which is what a recency window is asking for — inside the window.
    Sorting is stable, so rows sharing a key — and all undated rows together — keep the
    order the ledger wrote them in, which is the best remaining evidence of sequence.
    """
    raw = r.get("ts")
    s = "" if raw is None else str(raw).strip()
    if s and s.lower() not in ("none", "null"):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:            # bare stamp with no offset — read it as UTC
                dt = dt.replace(tzinfo=timezone.utc)
            return (0, dt.timestamp())
        except Exception:
            pass
    return (1, 0.0)                          # undated / unparseable -> end of the list


def is_infrastructure(r):
    """True for the shared baseline probe, which is NOT a scientific result.

    The baseline is ledgered with `outcome: KEEP` under whichever team happened to run it.
    Counted as a real KEEP it inflates that team's keep_rate and parks a fake success in
    the stagnation window, where it suppresses exactly the regroup the run needs. Same
    exclusion as runbook Step 5g, the `stagnation_response` "cycles since last promotion"
    count and ROLE-MONITOR's streak — keep all four in agreement.

    Of the two checks below, only the `exp_id` one fires on a row this run's orchestrator
    wrote: `infrastructure_probe` is not one of the ledger keys and `cycle_ledger` coerces
    every harvested row to exactly those keys, so the marker cannot survive into the file.
    It is kept for rows from any other producer (hand-written or repaired rows, a future
    harvest path) — forward compatibility, not a second line of defence.
    """
    if exp_id_of(r) == "baseline_shared":
        return True
    probe = r.get("infrastructure_probe")
    return probe is not None and str(probe).strip().lower() in ("true", "1", "yes")


def validity(r):
    """1 (valid) / 0 (invalid) / None (UNKNOWN).

    `is_valid: null` means the ledger could not DETERMINE validity — the result-file read
    missed, or the row was harvested from a post alone. That is a logging gap, not a failed
    energy gate. Scoring unknowns as invalid drags `valid_rate` down and makes a harvest
    bug look like a science problem, so they are counted and reported separately instead.
    """
    v = r.get("is_valid", r.get("valid"))
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "none", "null"):
        return None
    if s in ("true", "yes"):
        return 1
    if s in ("false", "no"):
        return 0
    try:
        return 1 if int(float(s)) == 1 else 0
    except Exception:
        return None


def parse_args(argv):
    """-> (focus_root_or_None, expected_teams_set). Unknown flags are ignored, never fatal."""
    positional, raw_teams, i = [], "", 0
    while i < len(argv):
        a = argv[i]
        if a == "--teams" and i + 1 < len(argv):
            raw_teams = argv[i + 1]
            i += 2
            continue
        if a.startswith("--teams="):
            raw_teams = a.split("=", 1)[1]
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    raw_teams = raw_teams or os.environ.get("AS_EXPECTED_TEAMS", "")
    teams = {t.strip() for t in raw_teams.split(",") if t.strip()}
    return (Path(positional[0]).resolve() if positional else None), teams


def main():
    focus_root = Path(__file__).resolve().parent.parent  # task/ sits directly under FOCUS_ROOT
    arg_root, roster = parse_args(sys.argv[1:])
    if arg_root is not None:
        focus_root = arg_root

    all_rows, path = load_experiments(focus_root)
    print(f"# meta_diagnostics for {focus_root.name}")
    print(f"experiments_log: {path} ({'found' if path.exists() else 'MISSING'})")
    print(f"total_rows: {len(all_rows)}")
    if not all_rows:
        print("insufficient data: no experiments logged yet — skip automated signals; "
              "judge from team queue.md, workshop posts, and champion/SOURCE instead.")
        return

    # Defensive chronological sort BEFORE anything takes a window (see ts_key). Undated rows
    # land at the end; count them, because a run that stops stamping `ts` has a ledger bug
    # the meta pass should see rather than a science signal.
    all_rows = sorted(all_rows, key=ts_key)
    undated = sum(1 for r in all_rows if ts_key(r)[0] == 1)

    infra = [r for r in all_rows if is_infrastructure(r)]
    rows = [r for r in all_rows if not is_infrastructure(r)]
    n = len(rows)
    print(f"total_experiments: {n}  ({len(infra)} infrastructure/baseline row(s) excluded — "
          f"the baseline probe is not a team's KEEP)")
    if undated:
        print(f"undated_rows: {undated}  (null/unparseable ts — sorted LAST; the ledger "
              f"should stamp every row)")
    if n == 0:
        print("insufficient data: only infrastructure rows in the ledger — no experiment has "
              "been harvested yet; judge from team queue.md, workshop posts, and champion/SOURCE.")
        return

    outcomes = Counter(outcome_of(r) for r in rows)
    failed = outcomes.get("FAILED", 0)

    # DENOMINATOR RULE (LOGGING.md §2): FAILED rows TESTED NOTHING — unapplied diff, eval-pool
    # or Redis error, scope abort. Dividing by them lets one bad afternoon on the eval head
    # halve keep_rate and manufacture a "the science has stalled" verdict out of infra noise.
    # Every rate below is over `rated`, the rows that are real experiments; the FAILED count is
    # reported on its own line because a rising one is an INFRA signal worth acting on.
    rated = [r for r in rows if outcome_of(r) != "FAILED"]
    d = len(rated)

    def rate(k, denom):
        return (k / denom) if denom else None

    def fmt(x):
        return "n/a" if x is None else f"{x:.3f}"

    keeps = outcomes.get("KEEP", 0)
    keep_rate = rate(keeps, d)

    # NEAR_MISS = passed BOTH the train gate and the held-out test gate, but lost the
    # promotion race (an equal-or-better champion landed mid-eval, or champion.md no
    # longer records the exp_id). It is a SUCCESS, not a failure: the change is
    # auto-re-queued as `{exp_id}_stack` for retest against the new champion. Frequent
    # NEAR_MISSes mean the roster is racing itself — rotation sizing may need attention.
    near_misses = outcomes.get("NEAR_MISS", 0)
    near_miss_rate = rate(near_misses, d)

    validities = [validity(r) for r in rated]
    valid = sum(1 for v in validities if v == 1)
    valid_known = sum(1 for v in validities if v is not None)
    valid_unknown = len(validities) - valid_known
    valid_rate = rate(valid, valid_known)

    # FAILED rows are excluded from the duplicate scan too: a FAILED item is re-queued by
    # contract and legitimately re-run under the SAME exp_id, so its second row is a retry,
    # not a duplicate proposal. Counting it would flag the recovery path as a defect.
    ids = Counter(exp_id_of(r) for r in rated)
    ids.pop("", None)                    # a row with no id at all cannot be judged a duplicate
    dups = sum(c - 1 for c in ids.values() if c > 1)
    duplicate_rate = rate(dups, d)

    teams = Counter(team_of(r) for r in rows)   # FAILED rows still prove a team is ALIVE

    # best_fitness ranks VALID rows only. An energy-gate-invalid run now reports its HONEST
    # mean_rel_steps rather than a 1000.0 sentinel, so a fast-but-under-relaxed candidate would
    # otherwise take this minimum and misreport the frontier. Validity — not fitness magnitude —
    # decides eligibility. The `< 999` guard still drops errored / non-converged rows, which do
    # carry the sentinel. Unknown validity is not eligible either: an unranked row is safer than
    # a frontier claimed on a row whose gate result was never read.
    _valid_rows = [r for r in rated if validity(r) == 1]
    fits = [v for v in (fnum(r, "fitness", "metric_value") for r in _valid_rows)
            if v is not None and v < 999]
    best = min(fits) if fits else None

    # TWO outcomes are filtered OUT of the stagnation window before it is taken — same rule as
    # runbook.md Step 5g, PHASES.md and ROLE-MONITOR.md:
    #   FAILED    — infra/harness outcome (unapplied diff, Redis/eval error, scope abort) that
    #               TESTED NOTHING. Leaving them in would let a burst of eval-pool errors
    #               manufacture a scientific-stagnation signal out of pure infra noise.
    #   NEAR_MISS — passed both gates and lost only the promotion race, which means another
    #               candidate WAS promoted that rotation. Leaving them in would manufacture a
    #               stagnation signal out of the run being *too* productive.
    # The baseline probe is already gone (is_infrastructure), so it can no longer sit in this
    # window as a KEEP nobody earned and suppress a regroup.
    WINDOW_EXCLUDED = ("FAILED", "NEAR_MISS")
    scored = [r for r in rows if outcome_of(r) not in WINDOW_EXCLUDED]
    last10 = scored[-10:]
    keeps_last10 = sum(1 for r in last10 if outcome_of(r) == "KEEP")

    # Idle-team check. A Counter built from the rows can NEVER hold a zero — a team that ran
    # nothing contributes no rows — so `any(count == 0)` over it is unreachable by construction.
    # The check needs a roster from outside the ledger:
    #   * roster given (--teams / AS_EXPECTED_TEAMS) -> seed every known team at 0 and take the
    #     set difference; a team that has never run is now visible, which is the whole point.
    #   * roster absent -> fall back to what the ledger CAN prove: a team that has run before and
    #     has gone quiet (present all-time, absent from the window). Label it, so nobody reads a
    #     False here as "the roster was checked and every team is busy".
    for t in roster:
        teams.setdefault(t, 0)
    if roster:
        activation_basis = f"roster of {len(roster)} team(s) via --teams/AS_EXPECTED_TEAMS"
        idle_teams = sorted(t for t in roster if teams.get(t, 0) == 0)
    else:
        activation_basis = ("NO ROSTER SUPPLIED — ledger-only: teams that ran before and have gone "
                            "quiet. This answers a DIFFERENT question than the roster check, and in "
                            "a multi-team run against a 10-row window it fires routinely. Re-run "
                            "with --teams <the team names in teams/roster.md> for the real check")
        recent = {team_of(r) for r in last10}
        idle_teams = sorted(set(teams) - recent) if len(scored) >= 10 else []

    has_low_keep_rate = keep_rate is not None and keep_rate < 0.10
    has_frequent_near_miss = near_miss_rate is not None and near_miss_rate > 0.10
    has_high_duplicates = duplicate_rate is not None and duplicate_rate > 0.15
    has_low_activation = bool(idle_teams)
    stuck = (keeps_last10 == 0 and len(scored) >= 10)

    # A rate over an EMPTY denominator is unknown, not "fine". With d == 0 — every non-infra row
    # FAILED, i.e. the run produced no science at all — `keep_rate < 0.10` is False, and a flag
    # line reading `False` is indistinguishable from a healthy run. Render the undefined case as
    # `n/a` with its reason so the judgment pass cannot read missing evidence as good news.
    def flag(value, rate_value):
        if rate_value is None:
            return (f"n/a  (undefined: 0 rated experiments — all {failed} row(s) FAILED. "
                    f"This is an INFRA problem to act on, NOT a healthy rate)")
        return str(value)

    # Same principle for best_fitness: a bare `None` is ambiguous between "no valid row yet" and
    # "the field is broken". Say which, and point at the line that explains it.
    if best is not None:
        best_line = f"best_fitness: {best:.6f}  (min over {len(fits)} valid, non-sentinel row(s))"
    else:
        best_line = (f"best_fitness: None  (nothing rankable: {len(_valid_rows)} of {d} rated row(s) "
                     f"have is_valid==1, {len(fits)} of those a finite fitness — see valid_rate "
                     f"above; unknown validity is not eligible)")

    print(f"rate_denominator: {d}  (experiments excluding FAILED — see LOGGING.md §2)")
    print(f"failed_count: {failed}  (tested nothing; excluded from every rate below, and from "
          f"the duplicate scan — a FAILED item is legitimately re-run under the same exp_id)")
    print(f"keep_rate: {fmt(keep_rate)}  ({keeps}/{d})")
    print(f"near_miss_count: {near_misses}  (rate {fmt(near_miss_rate)}) "
          f"— passed BOTH gates, lost the promotion race; re-queued as {{exp_id}}_stack")
    print(f"valid_rate: {fmt(valid_rate)}  ({valid}/{valid_known} with a known gate result; "
          f"{valid_unknown} unknown — is_valid null is a logging gap, NOT an invalid run)")
    print(f"duplicate_rate: {fmt(duplicate_rate)}  ({dups} repeats)")
    print(best_line)
    print(f"windowed_experiments: {len(scored)}  (FAILED and NEAR_MISS excluded from the window)")
    print(f"keeps_in_last10_windowed: {keeps_last10}")
    print(f"per_team_experiments: {dict(teams)}")
    print(f"outcomes: {dict(outcomes)}")
    print("--- flags (for the judgment pass) ---")
    print(f"has_low_keep_rate (<0.10): {flag(has_low_keep_rate, keep_rate)}")
    print(f"has_high_duplicates (>0.15): {flag(has_high_duplicates, duplicate_rate)}")
    # A False with no roster means "not checked", not "every team is busy" — say so, because a
    # judgment pass reading a bare False will stop looking. A True is informative either way.
    print(f"has_low_activation (a team idle): "
          f"{has_low_activation if (roster or has_low_activation) else 'False — UNINFORMATIVE, not checked'}  "
          f"[{activation_basis}] idle={idle_teams}")
    print(f"has_frequent_near_miss (>0.10): {flag(has_frequent_near_miss, near_miss_rate)}  "
          f"— NOT a fault; the roster is racing itself, consider rotation sizing")
    print(f"stuck (0 KEEPs in last 10 windowed): {stuck}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never fatal — meta pass must not depend on this
        print(f"meta_diagnostics: non-fatal error ({e!r}); fall back to judgment over queues/posts/champion.")
