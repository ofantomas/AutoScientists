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

### Return contract (hard — a violation errors *every* molecule)
The evaluator enforces the contract below. Breaking it does not merely score badly: each molecule
raises immediately and the whole evaluation is wasted, so check your control flow against these
before running.

- **`n_force_calls` must be an integer equal to the evaluator's own count of `calc()` calls.** The
  evaluator counts calls independently; the returned number is only a cross-check. A non-integer, or
  any mismatch with the measured count, makes the run invalid.
- **`final_positions` must be the last geometry passed to a successful in-budget `calc()` call.**
  Both sides are converted to `float64` and compared with `np.array_equal` — **exact equality, no
  tolerance**. The evaluator reuses the energy and convergence state recorded by that call; it does
  *not* spend an uncounted energy evaluation after `minimize_func` returns.
- **Never return an extrapolated, interpolated, restored, or otherwise unevaluated geometry** —
  including an earlier "best so far" geometry you rolled back to but did not re-evaluate last. If the
  geometry you want to return is not the most recently evaluated one, spend an in-budget `calc()`
  call on it first.

Budget and finiteness are enforced the same way:

- Reaching `max_force_calls` without `converged()` is invalid. A molecule that converges on the final
  allowed call is fine and proceeds to the energy gate.
- Exceeding `max_force_calls` is invalid.
- Non-finite energy or forces must be treated as a failed step (reject or abort — never accept it);
  non-finite final positions make the run invalid.

## Objective (lower is better)
**`fitness` = `mean_rel_steps`** = mean over molecules of (your force calls) / (reference force
calls). Lower = fewer steps than the reference. This is the only quantity being optimized.

## Hard validity gate (energy)
A run is **valid only if `mean_rel_energy >= 1.0`** — on average across all molecules your optimizer
must recover **at least as much energy lowering as the reference** (`rel_energy_i = (E_start - E_final) /
(E_start - E_final_ref)`; a value `>= 1.0` means molecule `i` reached a final energy at least as deep as
the reference). If the average dips below 1.0 the **whole run is invalid** → `is_valid = 0`, and the
run is rejected **no matter how good its `fitness` is**. A faster-but-shallower optimizer is worthless:
correctness (genuine relaxation) first, then speed. (Hard pre-conditions also apply: every molecule must
`converged()` within the force-call budget and positions must be finite.)
`max_final_energy_delta_kcal_mol` is still reported, but as a diagnostic only — it no longer gates
validity.

**An invalid run still reports its honest `fitness`.** Failing the energy gate does not blank out the
measurement: you still see the true `mean_rel_steps` the optimizer achieved, so you can tell a mechanism
that was *fast but under-relaxing* (worth retrying if the relaxation depth can be recovered without
giving the speed back) from one that was *slow and under-relaxing* (a genuine dead end). `fitness = 1000` is reserved for runs that produced no
usable trajectory at all — a harness error, a molecule that stopped before convergence, or one that blew
the force-call budget. Validity is carried by `is_valid`, never inferred from the fitness value.

## How a candidate is scored
Each candidate `algo.py` is evaluated on a fixed molecule set; the evaluator returns `fitness`,
`is_valid`, `mean_rel_steps`, `mean_rel_energy`, `max_final_energy_delta_kcal_mol`, and a
**`per_molecule` breakdown** — `worst_by_rel_steps` (where the step budget goes),
`nearest_energy_gate` (the most under-relaxed molecules), and `non_converged` (which molecules never
converged). **`per_molecule` is returned for every evaluation that produced any result, including
invalid ones**, so a run that failed still tells you *which* molecules failed and why. Use it: a
change that breaks 3 molecules and one that breaks all of them score identically in the aggregate.
There are two
fixed splits: **train** (the default) and a **held-out test** split nothing has ever been tuned on. A
result on either split counts only if it is **valid**; a lower train `fitness` alone is *not* enough to
promote a candidate — see the promotion contract below.

## Promotion contract: train improvement AND held-out test
A candidate becomes the new champion **only** when all three conditions hold:

1. **Train improvement** — the train run is valid AND
   `current_champion_train_fitness - candidate_train_fitness >= 1e-4`.
2. **Test improvement** — `current_champion_test_fitness - candidate_test_fitness > 1e-4`
   (`mean_rel_steps` on the held-out test split).
3. **Test validity** — the test run has `is_valid == 1`, i.e. `mean_rel_energy >= 1.0` on the
   held-out molecules.

Read the comparisons exactly as written: the train margin is inclusive (`>= 1e-4` passes), the test
margin is strict — a test delta of exactly `1e-4`, or a tie, is a **rejection**. Regressions on either
split are rejections. The test evaluation runs the *same frozen* `algo.py` that produced the train
number — never re-edit between the two — and only after (1) already holds; a train DISCARD never
spends a test evaluation.

Why: the train molecules are a *sample*, not the target. A change that is fast on train but
under-relaxes on unseen molecules (`mean_rel_energy < 1.0` on test) or needs more steps there is not an
improvement — it is memorization of this particular set.

A candidate that passes (1) but fails (2) or (3) is **REJECTED_TEST**. Its mechanism family is
abandoned, and it is recorded in the team's `non_generalizable.md` (not `dead_ends.md`), stating which
of the two test conditions failed — test invalidity, insufficient test improvement, or both. Read
`non_generalizable.md` before proposing, so a known non-generalizing family is not re-tried.

## What counts as cheating (forbidden — do not propose, code, or keep these)
The score rewards **genuinely faster relaxation**, not a lower recorded step count. Any change whose
effect is to make the evaluator *record* convergence sooner without the geometry actually being relaxed
faster is **cheating** and is forbidden. You may not propose, implement, or keep an experiment whose
mechanism is any of the following (illustrative, not exhaustive — the principle is "never trip the
convergence test on an under-relaxed geometry, and never tune to the gate"):

- **Terminal displacement capping / step shrinking to trip convergence.** Detecting that
  gradients/energy are "close enough" and then scaling, clipping, or zeroing the final step so the
  *displacement* criterion is satisfied on the next `calc()`, ending the trajectory before the geometry
  is actually stationary.
- **Same-geometry / stationary "polishing" or "confirmation" calls.** Re-evaluating `calc()` at the
  same (or a deliberately tiny-perturbation) geometry so the displacement/energy criteria trip "for
  free" without a real geometry update.
- **Convergence/force "polishing" of any kind** — late-stage mechanisms whose purpose is to satisfy the
  force/displacement criteria rather than to descend the potential further.
- **Energy-guard / gate-margin tricks.** Adding a guard that allows the above only when the
  per-molecule energy gap is "just inside" tolerance, i.e. engineering the result to sit as close to the
  validity boundary as possible.
- **Tuning to the (hidden) convergence tolerances** — reverse-engineering, hard-coding, or branching on
  the convergence thresholds, the energy gate, or the step budget.
- **Per-molecule / per-formula specialization (train-set memorization).** Branching `algo.py` on
  molecule identity or an exact composition/graph fingerprint — element formula, exact atom or element
  counts, a specific local-environment signature, or any predicate that singles out one or a few of the
  benchmark molecules — to change **any** behavior for them, whether a stopping shortcut **or** a
  "mechanism" (e.g. a per-chemotype Hessian/momentum/torsion selector). The molecule set is a fixed
  proxy for *unseen* drug-like molecules, so a branch that only fires for molecules already in the set
  encodes the answer key instead of improving the optimizer — it is forbidden **even when it passes
  validity and lowers train `mean_rel_steps`**. A growing registry of `_use_<chemotype>()` selectors or
  `formula == ...` / `env_count(...) == k` exact-match gates is the canonical instance.

**The answer-key test (applies to every experiment).** Every code path must be selected by *general,
continuous* physical or geometric quantities that an unseen molecule could also exhibit — local
curvature, gradient/energy magnitudes, bond/angle/dihedral character, ring flexibility, coordination
patterns, size-scaled thresholds — never by recognizing *which* benchmark molecule you are looking at.
Before coding any predicate, ask: *"on a new drug-like molecule I have never seen, would this branch
ever fire, and would I justify it by the physics or by naming the molecule it helps?"* If it only fires
for specific known molecules, or its justification is molecule identity rather than mechanism, it is
overfitting and forbidden.

The `mean_rel_energy >= 1.0` gate is designed to catch the stopping-shortcut family: an under-relaxed
geometry recovers less energy than the reference, so `mean_rel_energy` drops below 1.0 and the run is
invalid. If you are unsure whether an idea is a genuine optimizer improvement or a way to end the
trajectory sooner, it is the latter — skip it.

## Where genuine improvement has come from
These are the *areas* that have historically yielded real, valid speedups — figure out the actual
mechanism and any constants yourself; this is directional, not a recipe:
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
compared on equal footing. It is exposed to your code only as an **opaque** callable: you **cannot**
edit it, and you **must not** target, reverse-engineer, or game it. Its exact numeric tolerances are
**not disclosed** and must not be hard-coded, branched on, or inferred and then targeted.

Qualitatively, `converged()` returns `True` for a step only once the geometry is genuinely at a
stationary point: small energy change between consecutive evaluations, small gradient (max and RMS),
**and** small atomic displacement (max and RMS) — all simultaneously, measured between successive
`calc()` evaluations. The first `calc()` call never converges (no previous point to compare against).
Your only lever is the **trajectory**: step direction/size, curvature/preconditioning — especially for
the slowest molecules — so each one genuinely enters this stationary band in as few force calls as
possible. Do **not** try to make `converged()` fire while the geometry is still under-relaxed (see "What
counts as cheating").

## Research strategy: mechanism-first, not tuning-first
Prefer experiments that introduce or replace an algorithmic **mechanism** — how the optimizer reasons
about geometry: step realization, internal coordinates, trust-model consistency, curvature transport. A
strong experiment is describable as a new mechanism in one sentence before it is coded, and its win must
come from descending the potential in fewer real steps.

Keep three different things separate:

- **FORBIDDEN — identity-gated branches.** Molecule-name gates, exact-formula or exact-atom-count
  tables, and graph/fingerprint matches that single out specific benchmark molecules (see "What counts
  as cheating"). Per-molecule reasoning *does not license a per-molecule branch*; it must drive a
  general mechanism instead.
- **DISCOURAGED — pure parameter sweeps.** Numeric constant edits, tolerance sweeps, trust-radius
  coefficient sweeps, or one more branch in an existing policy of the current `algo.py`. Allowed, but
  should be rare, and used only to calibrate a mechanistic change — never as the main idea.
- **ENCOURAGED — continuous, generally-applicable thresholds.** A size- or curvature-scaled criterion
  that would also act on unseen molecules — e.g. "soften trust for molecules above ~60 atoms" — is a
  general mechanism, not an identity gate. That distinction is the whole point.
