# 0003 — Stable Black-Scholes evaluation

Status: accepted; amended twice after validation findings

## Decision
Price the forward-OTM option via the normalised Black function
b(x,s) = e^{x/2}N(h+t) − e^{−x/2}N(h−t), x = ln(F/K) ≤ 0, s = σ√T, h = x/s, t = s/2, then add the
discounted forward intrinsic. Evaluate each term as exp(±x/2 + log N(·)) (no premature
underflow). Measure the cancellation term1/b; above 30 switch formula:
- |h| ≤ 8: b = (A−B)cosh(x/2) + (A+B)sinh(x/2), with A−B integrated by 16-point Gauss–Legendre
  when t ≤ 0.5 and |x| ≤ 2 (vectorised, fixed summation order).
- |h| > 8: b = (1/√2π)∫₀ˢ exp(−x²/2u² − u²/8) du (positive integrand = normalised vega) by
  adaptive quadrature.
At the forward (x = 0), b = erf(s/(2√2)) exactly. σ = 0 and T = 0 use exact limits. Payoff kinks
are detected within 8 ulps; Greeks there are `undefined_at_kink`, vega at σ = 0 is `one_sided`.

## Evidence and history
- Initial threshold 1e3 lost digits (2.7e-11); measured scan → threshold 30.
- A property-test warning exposed 1.4e-5 relative error near the forward ATM (|h| ≪ 1) in the
  quadrature branch → split formula for small |h| (docs/progress.md).
- Profiling showed scalar quadrature dominated batch runtime → vectorised split formula for
  |h| ≤ 8. Error of the split formula grows like h⁴ε, so the cut-off at 8 was chosen by scan.
- Current evidence: 3,850-point 60-digit grid, max relative error 1.8e-12 (reports/).

## Alternatives
Jäckel's "Let's Be Rational" rational approximations are more complete but much larger to
implement and verify; not needed for the accuracy targets here.
