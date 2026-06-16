#!/usr/bin/env python3
"""Evaluate ONE candidate algo.py against a running Redis-backed worker pool.

Prints the score dict as a single JSON object on stdout (last line). The score is
identical to validate() (it reuses Evaluator + score_results), plus an additional
`per_molecule` field summarizing where the optimizer struggles — so analysts can
target proposals at specific molecule regimes instead of the 250-molecule average.

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
        # Per-molecule summary intentionally DISABLED for this run: the agent sees only the
        # aggregate score (parity with the canonical autoresearch / ProteinGym coarseness, and a
        # test of whether per-molecule guidance actually helped or just funnelled the swarm onto
        # the single stiff molecule). Re-enable by uncommenting; per_molecule_summary() is kept above.
        # if result["results"]:
        #     score["per_molecule"] = per_molecule_summary(result["results"])
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
