# 0017 — Heston stochastic volatility (Fourier pricing)

Status: accepted (2026-09-29)

## Context
Flat-volatility GBM cannot produce a smile. Heston (1993) is the standard stochastic-volatility
benchmark with a semi-closed-form European price, so it adds a second model family that can be
checked against an independent implementation. ADR 0012 deferred it until Release B was done.

## Decision
- **Model** (`models/heston.py`): v0, κ, θ, σ, ρ as decimals per year. Domain limits are
  rejected, not clipped (variances ≤ 4, κ ≤ 50, σ ≤ 5, −1 < ρ < 1). A Feller violation
  (2κθ < σ²) is **reported, not rejected**: the characteristic function stays well defined
  when the variance can touch zero, and real calibrations often violate the condition.
- **Pricing** (`engines/heston_fourier.py`): Lewis's single integral
  C = D(F − √(FK)/π ∫₀^∞ Re[e^{iux} φ(u − i/2)]/(u² + 1/4) du), x = ln(F/K), put by parity.
  One integral instead of the two P1/P2 integrals of the original paper, and no damping
  parameter to tune (unlike Carr–Madan).
- **Characteristic function:** Albrecher et al.'s "little Heston trap" form (principal branch of
  the complex log stays continuous), rearranged so nothing is divided by σ². At σ = 0 the
  formula reduces algebraically to the BSM characteristic function with integrated variance
  I(T) = θT + (v0 − θ)(1 − e^{−κT})/κ, and σ = 0 itself is routed to closed-form BSM with
  vol √(I(T)/T). I(T) uses a series near κT = 0.
- **Quadrature:** `scipy.integrate.quad` on [0, ∞) with configurable tolerances
  (`HestonConfig`, default epsabs 1e-12, epsrel 1e-10). The quadrature error estimate is
  converted into a price error bound and reported; a price below the no-arbitrage lower bound
  by more than that bound raises `numerical_failure`.
- **Greeks:** delta, gamma, theta, rho, dividend rho by central bump-and-reprice. **Vega is
  `not_supported`**: Heston has no single volatility parameter, and a sensitivity to v0 or θ is
  not a BSM vega. Displaying one under that name would invite misuse in hedging.
- **Scope:** European exercise, continuous yield only. American Heston would need a PDE or
  simulation engine; cash dividends would need a model choice for the variance process around
  ex-dates. Both are rejected by capability checks with the reason.
- **Smile view:** `/api/v1/visuals/smile` prices 41 strikes (out-of-the-money side, k = ln(K/F)
  in ±2.5 total standard deviations) and inverts each with the existing BSM IV solver. For a
  BSM model the result is flat to 1e-9 (a consistency check); for Heston it shows the ρ-driven
  skew and the σ-driven curvature.

## Validation (predeclared in `[tolerance.heston_*]` before any code was written)
- 200 prices vs QuantLib 1.43 `AnalyticHestonEngine` (4 parameter sets incl. two Feller
  violations, negative rates, 10 days to 3 years, strikes 60–150): atol 1e-8, rtol 1e-7.
- Deterministic-variance limit: ρ = 0 gap below 1e-9 at σ = 1e-7; ρ ≠ 0 gap first order in σ
  (ratio gap/σ stable to 1e-3 between σ = 1e-5 and 1e-7). The first draft of this criterion
  assumed second order for every ρ, which is mathematically wrong; the correction is logged
  with its hash in `docs/progress.md`.
- Put-call parity (atol 1e-9) and agreement between two quadrature settings (1e-9).
- Fault injection: a flipped correlation sign and a dropped mean-reversion term are detected.

## Not done
Calibration of Heston to quotes (the SSVI surface fit remains the calibration path), Heston
Greeks with respect to model parameters, American exercise, and COS/FFT batch pricing (not
needed at the current single-contract latency of a few milliseconds).
