---
name: molopt-relax-steps
task_type: optimization
metric: fitness
direction: minimize
---

# Molecular-geometry optimizer — minimize relaxation steps

You are improving `algo.py`, a compact geometry optimizer that relaxes molecules on a
quantum-chemical (xTB) potential. Its job: drive each molecule to its energy minimum using as
few force evaluations as possible, without degrading the final geometry.

## What you edit
**Only `algo.py`.** It defines `minimize_func(positions, atomic_numbers, calc, max_force_calls,
converged)` and returns `(optimized_positions, n_force_calls)`. `calc(pos)` returns
`(energy, forces)` and counts as one force call. Do not touch the evaluator, the molecule set, or
any other file.

## Objective (lower is better)
**`fitness` = `mean_rel_steps`** = mean over molecules of (your force calls) / (reference force
calls). Lower = fewer steps than the reference. This is the only quantity being optimized.

## Hard validity gate (per molecule)
A run is **valid only if every molecule** finishes within **1 kcal/mol** of the reference final
energy (`max_final_energy_delta_kcal_mol < 1.0`). If **any** molecule falls outside that band the
**whole run is invalid** → `fitness = 1000`. A faster-but-invalid optimizer is worthless:
correctness first, then speed.

## How a candidate is scored
Each candidate `algo.py` is evaluated on a fixed molecule set; the evaluator returns `fitness`,
`is_valid`, `mean_rel_steps`, and `max_final_energy_delta_kcal_mol`. A result counts as an
improvement only if it is **valid AND** has lower `fitness` than the current best.

## The convergence test (fixed and external — read it, don't try to change it)
`converged(...)` is passed into `minimize_func`. It is the SHARED, FIXED stopping rule used to score
**every** algorithm identically — it lives outside `algo.py` on purpose, so different optimizers are
compared on equal footing. You **cannot** edit it and must not try. But you SHOULD read and understand
it, because your entire job is to make each molecule satisfy *this exact test* in as few force calls as
possible. A step converges (xTB mode) only once **all five** of these hold between consecutive steps:

- energy change `|ΔE| < 5e-6` Hartree
- max gradient component `g_max < 3e-4` (Hartree/Bohr)
- RMS gradient `g_rms < 1e-4`
- max displacement `d_max < 4e-3` Bohr
- RMS displacement `d_rms < 2e-3` Bohr

(The first step never converges — there's no previous point to compare to.) Your lever is the
**trajectory**: step direction/size, curvature/preconditioning — especially for the slowest
molecules — so each one enters this five-way tolerance band sooner.
