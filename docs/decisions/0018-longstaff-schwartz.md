# 0018 — Longstaff–Schwartz Monte Carlo for American exercise

Status: accepted (2026-09-29)

## Context
The CRR tree is the American engine, and QuantLib FD fixtures check it. A regression-based
Monte Carlo engine is a second, structurally independent method, and it is the one that extends
to path-dependent payoffs and higher dimensions where trees do not. Its failure modes are
different: a biased regression, and a standard error that ignores the regression step.

## Decision
- **Bermudan approximation:** exercise on M equally spaced dates plus t = 0 (default M = 50).
  The difference to the American price is reported (the validation report lists the largest
  CRR Bermudan–American gap on the matrix) rather than hidden.
- **Two independent path sets** (`SeedSequence(seed).spawn(2)`): the regression set fits one
  coefficient vector per date; the pricing set applies that fixed rule. Given the rule, pricing
  paths are i.i.d., so the reported SE and CI are valid, and the estimate is **low-biased**
  (a suboptimal rule cannot beat the optimal one). The in-sample regression-set estimate,
  which is typically biased high, is reported as a diagnostic only.
- **Paths generated backwards by Brownian bridge:** W(t_j) = (j/(j+1)) W(t_{j+1}) +
  √(Δt·j/(j+1)) Z. Memory is O(paths) instead of O(paths × dates), and the backward induction
  needs no stored path matrix. Antithetic sampling negates every normal, so pairs stay exact
  mirror paths; the pair average is the independent observation.
- **Regression basis:** 1, x, x², x³ in x = S/K plus the closed-form European value of the
  remaining contract, fitted on in-the-money paths only (fewer than 20 paths per coefficient:
  no exercise at that date, counted in diagnostics). The European regressor was added after
  the first validation run: with polynomials alone, the American call with two cash dividends
  sat 7.4 SE below the exact Bermudan price (93% of its predeclared allowance) and even the
  in-sample estimate was below it, which points at the basis, not overfitting. With the
  regressor the same case is within about 1 SE; the acceptance criteria were not changed.
- **Control variate:** the discounted European payoff of the same path, whose mean is the
  closed-form price on the escrowed spot, with β estimated on the regression set, so the
  pricing estimator stays unbiased. Unlike ADR 0007's rule against using the target's own BSM
  value, this control is not the target: the American premium is still estimated.
- **Dividends:** continuous yield, and cash dividends through the same escrowed model as the
  CRR tree (the exercise value adds back the PV of dividends still to be paid).
- **Greeks:** `not_supported` with a pointer to the tree. Regression-based Greeks are biased and
  noisy, and a validated alternative already exists.
- **Work limit:** (pricing + regression paths) × exercise dates ≤ 5·10⁷; the default
  (100,000 + 50,000 paths, 50 dates) takes a median 0.21 s on an Apple M4.

## Validation (predeclared in `[lsm]` before any LSM code was written)
- The exact reference is a CRR tree that exercises only on the same dates
  (`build_tree(..., exercise_every=m)`, 200 steps per date), so the comparison isolates the
  regression error from the Bermudan-vs-American gap. Per case: L − B ≤ 4·SE + ε_tree (no upward
  bias) and B − L ≤ 4·SE + ε_tree + 0.005·S/100 + 0.005·B (bounded suboptimality); 26 cases
  (puts, calls with q > r, cash dividends both ways, calls where early exercise is never
  optimal against the closed-form European price).
- SE calibration (statistical tier): 200 independent runs; the spread of the estimates over
  the mean reported SE must lie in [0.8, 1.25].
- Unit tests: bit-identical replay for a seed, European < Bermudan < American on the tree,
  immediate exercise for a deep ITM put, the control variate reducing the SE, configuration
  limits, routing.

## Not done
Andersen–Broadie dual upper bounds (would give a two-sided interval for the true price),
path-dependent payoffs, and multi-asset exercise.
