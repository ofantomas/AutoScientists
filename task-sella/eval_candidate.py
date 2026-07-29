#!/usr/bin/env python3
"""Evaluate ONE candidate algo.py against a running Redis-backed worker pool.

Prints the score dict as a single JSON object on stdout (last line). The score is
identical to validate() (it reuses Evaluator + score_results). The per-molecule
summary is intentionally DISABLED for this run — agents receive ONLY the aggregate
score (fitness / is_valid / mean_rel_steps / max_final_energy_delta), no per-molecule
breakdown.

Run from THIS RUN's dedicated client checkout on the eval head
(/home/tsypin/opt_problem_as_testgate_opus5) — never from the shared workers' repo.

Usage (the exact, canonical command shape — copy it verbatim). `JAX_ENABLE_X64=1`, an explicit
`--split`, and `--molecules-dir` are all MANDATORY: the client bakes absolute xyz paths into every
Redis task and the worker opens them on ITS OWN filesystem, so omitting --molecules-dir fails
250/250 molecules in ~2.6s. The eval Redis is on port 6385 (the old :6390 pool is dead):

  JAX_ENABLE_X64=1 /home/tsypin/miniconda3/envs/gigaopt/bin/python eval_candidate.py \
      --program /abs/path/candidate_algo.py \
      --split train \
      --molecules-dir /home/tsypin/as_testgate_molecules \
      --redis-host localhost --redis-port 6385

The held-out gate uses the identical command with `--split test` and nothing else changed — the
candidate is never re-edited between its train and test evals.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from validate import REPO_ROOT, Evaluator, score_results, serialize_program_minimize_func
from distributed_validate.optimizer import normalize_optimizer_spec


def per_molecule_summary(results: list[dict]) -> dict:
    """Compact per-molecule view for analyst targeting. Eval is deterministic, so
    these numbers reproduce exactly. Validity gate = mean_rel_energy >= 1.0; the most
    under-relaxed molecules (largest positive energy_delta) are the validity risk."""
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
        "nearest_energy_gate": by_gate[:5],   # validity risk: most under-relaxed (gate = mean_rel_energy >= 1.0)
        "non_converged": [r["mol"] for r in rows if not r["converged"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one candidate algo.py -> JSON score (+ per-molecule summary)")
    parser.add_argument("--program", required=True, help="Path to candidate algo.py")
    parser.add_argument("--split", choices=["train", "test"], default="train")
    parser.add_argument("--redis-host", default="localhost")
    parser.add_argument("--redis-port", type=int, default=6379)
    # The client bakes ABSOLUTE xyz paths into every Redis task; the worker opens that path
    # on ITS OWN filesystem. On a shared multi-host pool the workers do not have this
    # checkout, so molecules_dir MUST point at a directory that exists on every worker host.
    # Defaults to $AS_MOLECULES_DIR, else this repo's molecules/ (correct only when the
    # workers run from this same path).
    parser.add_argument(
        "--molecules-dir",
        default=os.environ.get("AS_MOLECULES_DIR") or str(REPO_ROOT / "molecules"),
        help="Molecules dir as seen BY THE WORKERS (absolute path, must exist on every worker host)",
    )
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
            molecules_dir=args.molecules_dir,
        )
        start = time.time()
        result = evaluator.evaluate(optimizer_spec)
        score = score_results(result["results"], result["num_errors"])
        score["duration_s"] = time.time() - start
        score["num_results"] = len(result["results"])
        score["num_errors"] = result["num_errors"]
        # Per-molecule summary is ENABLED for EVERY evaluation, valid or not — especially not.
        #
        # It was disabled to test whether per-molecule guidance helped or just funnelled the swarm
        # onto the single stiffest molecule. The answer, measured: without it an invalid run returns
        # a bare 1000.0 sentinel and NOTHING else, so a bold structural change that breaks
        # convergence on three molecules is indistinguishable from one that breaks everything, and
        # from a harness error. That is zero gradient on exactly the experiments worth learning
        # from, and a search that cannot tell those apart retreats to safe parameter tweaks.
        #
        # `result["results"]` is populated even when score_results() bailed early (non-convergence,
        # over-budget, or a partial set after a per-molecule error), so this fires for the invalid
        # runs that need it most. Only a total harness failure leaves it empty.
        if result["results"]:
            score["per_molecule"] = per_molecule_summary(result["results"])
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
