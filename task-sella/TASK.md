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

## Hard validity gate (energy)
A run is **valid only if `mean_rel_energy >= 1.0`** — on average across all molecules your optimizer
must recover **at least as much energy lowering as the reference** (`rel_energy_i = (E_start - E_final) /
(E_start - E_final_ref)`; a value `>= 1.0` means molecule `i` reached a final energy at least as deep as
the reference). If the average dips below 1.0 the **whole run is invalid** → `fitness = 1000`. A
faster-but-shallower optimizer is worthless: correctness (genuine relaxation) first, then speed. (Hard
pre-conditions also apply: every molecule must `converged()` within the force-call budget and positions
must be finite.) `max_final_energy_delta_kcal_mol` is still reported, but as a diagnostic only — it no
longer gates validity.

## How a candidate is scored
Each candidate `algo.py` is evaluated on a fixed molecule set; the evaluator returns `fitness`,
`is_valid`, `mean_rel_steps`, `mean_rel_energy`, and `max_final_energy_delta_kcal_mol`. A result counts
as an improvement only if it is **valid AND** has lower `fitness` than the current best.

## What counts as cheating (forbidden — do not propose, code, or accept these)
The score rewards **genuinely faster relaxation**, not a lower recorded step count. Any change whose
effect is to make the evaluator *record* convergence sooner without the geometry actually being relaxed
faster is **cheating** and is forbidden. You may not propose, implement, or keep an experiment whose
mechanism is any of:
- **Terminal displacement capping / step shrinking** to trip the convergence test — scaling, clipping,
  or zeroing the final step so the displacement criterion is met before the geometry is stationary.
- **Same-geometry "polishing" / "confirmation" calls** — re-evaluating `calc()` at the same (or a
  tiny-perturbation) geometry so the displacement/energy criteria trip for free.
- **Convergence/force "polishing" of any kind** — late-stage mechanisms whose purpose is to satisfy the
  convergence criteria rather than to descend the potential further.
- **Energy-guard / gate-margin tricks** — engineering a result to sit just inside the validity boundary.
- **Tuning to the convergence tolerances** — reverse-engineering, hard-coding, or branching on the
  convergence thresholds or step budget.
- **Per-molecule / per-formula gate-trip coverage** — branching on exact atom counts, element formulas,
  or molecule identity to apply a stopping shortcut to specific benchmark molecules.

The `mean_rel_energy >= 1.0` gate is designed to catch these: an under-relaxed geometry recovers less
energy than the reference, so `mean_rel_energy` drops below 1.0 and the run is invalid. If you are unsure
whether an idea is a genuine optimizer improvement or a way to end the trajectory sooner, it is the
latter — skip it.

## Where genuine improvement has come from
These are the *areas* that have historically yielded real, valid speedups — figure out the actual
mechanism and any constants yourself from per-molecule evidence; this is directional, not a recipe:
trust-region / step-acceptance **structure** (how the radius grows/shrinks and steps are accepted, not
just sweeping a coefficient); Hessian **initialization & conditioning** (model-Hessian priors, stiff/soft
mode conditioning); Hessian **update hygiene** (update choice, damping, secant safeguards, rebuild
timing); internal-coordinate **construction** (bonds/angles/dihedrals/near-linear angles/impropers,
rebuild policy); geometry **realization** (predictor/corrector, partial step, line search); and
**restart/re-blend** of the curvature model on a stalled trajectory.

**Pursue genuinely novel optimizer mechanisms** — the largest, most generalizable gains come from ideas
not yet tried here, not from re-tuning constants. Take inspiration broadly and translate the underlying
principle into a concrete change to `algo.py`: the optimization of ML models / neural networks / LLMs
(momentum and Nesterov acceleration, adaptive-step and learning-rate-schedule ideas, preconditioning,
second-order / natural-gradient / quasi-Newton variants, variance reduction, warm restarts), and
optimization methods from other parts of physics and chemistry (relaxation and annealing schemes,
basin-hopping, multigrid, continuation/homotopy, RFO and other geometry-optimization advances). A
well-motivated novel mechanism beats another constant sweep.

## The convergence test (fixed and external — don't try to change it)
`converged(...)` is passed into `minimize_func`. It is the SHARED, FIXED stopping rule used to score
**every** algorithm identically — it lives outside `algo.py` on purpose, so different optimizers are
compared on equal footing. You **cannot** edit it, and you **must not** target, reverse-engineer, or
game it. Its exact numeric tolerances are **not disclosed** and must not be hard-coded or branched on.

Qualitatively, a step converges only once the geometry is genuinely at a stationary point: small energy
change between consecutive evaluations, small gradient (max and RMS), **and** small atomic displacement
(max and RMS) — all simultaneously. The first step never converges (no previous point to compare to).
Your only lever is the **trajectory**: step direction/size, curvature/preconditioning — especially for
the slowest molecules — so each one genuinely enters this stationary band in as few force calls as
possible. Do **not** try to make `converged()` fire while the geometry is still under-relaxed (see "What
counts as cheating").

## Per-molecule analysis: for hypotheses
The per-molecule table (`logs/run_log.md`) and `task/molecule_smiles.tsv` are advisory aids for finding
the *structural* reason a class of molecules is slow (size, flexible rings, heavy atoms, bonding motifs)
so you can fix the underlying mechanism and have it **generalize**. A good mechanism is one you'd expect
to help on *unseen* drug-like molecules, not just these.
