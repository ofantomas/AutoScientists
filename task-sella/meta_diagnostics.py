#!/usr/bin/env python3
"""Optional meta-improvement diagnostics for the molopt-relax-steps task.

Reads logs/experiments.jsonl from the run directory and prints HARD SIGNALS for the
orchestrator's judgment-based meta-improvement pass (see
system/reference/META-IMPROVEMENT.md). This script is NOT imported anywhere — the
meta-improvement hook works without it. It is a convenience the orchestrator MAY run:

    python3 task/meta_diagnostics.py            # auto-detects the run dir (parent of task/)
    python3 task/meta_diagnostics.py <FOCUS_ROOT>

It NEVER raises fatally on missing / empty / malformed logs — it prints
"insufficient data" and exits 0, so it can never block or crash the meta pass.
"""
import json
import sys
from pathlib import Path
from collections import Counter


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


def main():
    focus_root = Path(__file__).resolve().parent.parent  # task/ sits directly under FOCUS_ROOT
    if len(sys.argv) > 1:
        focus_root = Path(sys.argv[1]).resolve()

    rows, path = load_experiments(focus_root)
    n = len(rows)
    print(f"# meta_diagnostics for {focus_root.name}")
    print(f"experiments_log: {path} ({'found' if path.exists() else 'MISSING'})")
    print(f"total_experiments: {n}")
    if n == 0:
        print("insufficient data: no experiments logged yet — skip automated signals; "
              "judge from team queue.md, workshop posts, and champion/SOURCE instead.")
        return

    outcomes = Counter((r.get("outcome") or "?").upper() for r in rows)
    keeps = outcomes.get("KEEP", 0)
    keep_rate = keeps / n

    valid = sum(1 for r in rows if int(r.get("is_valid", r.get("valid", 0)) or 0) == 1)
    valid_rate = valid / n

    ids = Counter(r.get("experiment_id") or r.get("exp_id") or r.get("id") or "" for r in rows)
    dups = sum(c - 1 for c in ids.values() if c > 1)
    duplicate_rate = dups / n

    teams = Counter(r.get("team") or r.get("axis") or "?" for r in rows)

    fits = [v for v in (fnum(r, "fitness", "metric_value") for r in rows) if v is not None and v < 999]
    best = min(fits) if fits else None

    last10 = rows[-10:]
    keeps_last10 = sum(1 for r in last10 if (r.get("outcome") or "").upper() == "KEEP")

    has_low_keep_rate = keep_rate < 0.10
    has_high_duplicates = duplicate_rate > 0.15
    has_low_activation = (len(teams) > 0 and any(c == 0 for c in teams.values()))
    stuck = (keeps_last10 == 0 and n >= 10)

    print(f"keep_rate: {keep_rate:.3f}  ({keeps}/{n})")
    print(f"valid_rate: {valid_rate:.3f}  ({valid}/{n})")
    print(f"duplicate_rate: {duplicate_rate:.3f}  ({dups} repeats)")
    print(f"best_fitness: {best}")
    print(f"keeps_in_last10: {keeps_last10}")
    print(f"per_team_experiments: {dict(teams)}")
    print(f"outcomes: {dict(outcomes)}")
    print("--- flags (for the judgment pass) ---")
    print(f"has_low_keep_rate (<0.10): {has_low_keep_rate}")
    print(f"has_high_duplicates (>0.15): {has_high_duplicates}")
    print(f"has_low_activation (a team idle): {has_low_activation}")
    print(f"stuck (0 KEEPs in last 10): {stuck}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never fatal — meta pass must not depend on this
        print(f"meta_diagnostics: non-fatal error ({e!r}); fall back to judgment over queues/posts/champion.")
