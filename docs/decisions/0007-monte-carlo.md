# 0007 — Terminal Monte Carlo design

Status: accepted

## Decision
- Sample S_T exactly from the terminal GBM distribution; no time stepping for vanilla payoffs
  (measured 57× faster than a 252-step simulation at equal accuracy).
- RNG: NumPy PCG64 seeded via `SeedSequence(seed).spawn(2)`: child 0 for the estimator, child 1
  for the control-variate pilot. Draws are consumed sequentially, so results are independent of
  chunk size (tested).
- Moments stream through chunks with Chan's parallel update: memory is O(chunk).
- Antithetic: the independent observation is the pair average; SE uses pair averages only.
- Control variate: the discounted terminal underlying (known mean S*e^{−qT}), β from the
  independent pilot. Using the target's own BSM value as a control would make the estimate
  trivially exact and is deliberately not offered.
- Greeks: pathwise delta, vega, rho with SEs. Gamma/theta/dividend rho are `not_supported`
  (pathwise differentiation of the payoff indicator is not a valid gamma estimator).
- Validation: coverage, bias and SE-calibration tests over 500 independent replications with
  predeclared binomial/χ² acceptance regions (Bonferroni over the family).

## Note
When a pair average is deterministic (e.g. ATM-forward with ln(F/K) = σ²T/2, pathwise rho),
the reported SE is rounding-level; this is correct and covered by a unit test.
