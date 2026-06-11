#!/usr/bin/env python3
"""Evaluate ONE candidate algo.py against a running Redis-backed worker pool.

Prints the score dict as a single JSON object on stdout (last line).
Run from the opt_problem repo root (branch ralph-autoresearch-sella-baseline).

Usage:
  python eval_candidate.py --program /abs/path/candidate_algo.py \
      --split train --redis-host localhost --redis-port 6379
"""
from __future__ import annotations

import argparse
import json
import sys

from validate import serialize_program_minimize_func, validate
from distributed_validate.optimizer import normalize_optimizer_spec


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one candidate algo.py -> JSON score")
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
        score = validate(
            optimizer_spec,
            split=args.split,
            redis_host=args.redis_host,
            redis_port=args.redis_port,
        )
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
