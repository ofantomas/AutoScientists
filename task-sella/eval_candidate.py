#!/usr/bin/env python3
"""Evaluate ONE candidate algo.py against a running Redis-backed worker pool.

Prints the score dict as a single JSON object on stdout (last line). The score is
identical to validate() (it reuses Evaluator + score_results), plus two extra fields:
  - `per_molecule`         — the FULL per-molecule table (one row per molecule), mirroring
                             opt_problem's run.log columns so the orchestrator can render a
                             run.log-style per-experiment view.
  - `per_molecule_summary` — a compact view (worst-by-steps / nearest-energy-gate / non-converged).
Both are advisory: agents may use them to reason about specific molecule regimes / chemistry
instead of the 250-molecule average, but are not required to (see ROLE-ANALYST Step 1e).

Run from the opt_problem repo root (branch ralph-autoresearch-sella-baseline).

Usage:
  python eval_candidate.py --program /abs/path/candidate_algo.py \
      --split train --redis-host localhost --redis-port 6379
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from validate import Evaluator, score_results, serialize_program_minimize_func
from distributed_validate.optimizer import normalize_optimizer_spec


def per_molecule_summary(results: list[dict]) -> dict:
    """Compact per-molecule view for analyst targeting. Eval is deterministic, so
    these numbers reproduce exactly. Energy validity gate = 1.0 kcal/mol."""
    rows = [{
        "mol": r.get("mol_name"),
        "rel_steps": round(float(r.get("rel_steps", 0.0)), 4),
        "n_steps": r.get("n_steps"),
        "energy_delta_kcal_mol": round(float(r.get("energy_delta_kcal_mol", 0.0)), 4),
        "converged": int(bool(r.get("converged"))),
    } for r in results]
    by_steps = sorted(rows, key=lambda x: x["rel_steps"], reverse=True)
    by_gate = sorted(rows, key=lambda x: x["energy_delta_kcal_mol"], reverse=True)
    return {
        "n_molecules": len(rows),
        "worst_by_rel_steps": by_steps[:8],   # where the step budget is spent
        "nearest_energy_gate": by_gate[:5],   # validity risk (gate at 1.0 kcal/mol)
        "non_converged": [r["mol"] for r in rows if not r["converged"]],
    }


def per_molecule_full(results: list[dict]) -> list[dict]:
    """Full per-molecule table — one row per molecule, columns mirroring opt_problem's run.log
    (`molecule n_steps max_steps rel_steps rel_energy energy_delta_kcal_mol conv`). Eval is
    deterministic, so these reproduce exactly. Sorted by molecule name (None last) for stable diffs."""
    rows = [{
        "mol": r.get("mol_name"),
        "n_steps": r.get("n_steps"),
        "max_steps": r.get("max_steps"),
        "rel_steps": round(float(r.get("rel_steps", 0.0)), 6),
        "rel_energy": round(float(r.get("rel_energy", 0.0)), 6),
        "energy_delta_kcal_mol": round(float(r.get("energy_delta_kcal_mol", 0.0)), 6),
        "converged": int(bool(r.get("converged"))),
    } for r in results]
    return sorted(rows, key=lambda x: (x["mol"] is None, x["mol"] or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one candidate algo.py -> JSON score (+ per-molecule summary)")
    parser.add_argument("--program", required=True, help="Path to candidate algo.py")
    parser.add_argument("--split", choices=["train", "test"], default="train")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    args = parser.parse_args()

    # Serialize the candidate's minimize_func (raises if minimize_func is missing/not callable).
    optimizer_spec = normalize_optimizer_spec(
        serialize_program_minimize_func(args.program)
    )

    try:
        # Same path validate() takes, but keep the per-molecule results to summarize.
        evaluator = Evaluator(
            split=args.split,
            redis_host=args.redis_host,
            redis_port=args.redis_port,
        )
        start = time.time()
        result = evaluator.evaluate(optimizer_spec)
        score = score_results(result["results"], result["num_errors"])
        score["duration_s"] = time.time() - start
        score["num_results"] = len(result["results"])
        score["num_errors"] = result["num_errors"]
        # Per-molecule breakdown — RE-ENABLED for sella-run5 (was disabled in sella-run4 as an
        # ablation testing whether per-molecule guidance funnels the swarm onto the single stiff
        # molecule). Emit BOTH the full table (run.log parity) and the compact summary. Advisory
        # only — see ROLE-ANALYST Step 1e. To revert to the aggregate-only ablation, comment out
        # the two assignments below.
        if result["results"]:
            score["per_molecule"] = per_molecule_full(result["results"])
            score["per_molecule_summary"] = per_molecule_summary(result["results"])
    except Exception as exc:  # surface harness/Redis failures as machine-readable JSON
        print(json.dumps({
            "fitness": 1000.0,
            "is_valid": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }))
        return 1

    # Single clean JSON line on stdout; the agent parses this.
    print(json.dumps(score, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
