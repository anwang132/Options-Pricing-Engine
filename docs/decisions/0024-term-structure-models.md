# 0024 — Term-structure models: SVI slices and Heston with θ(t)

Status: accepted (2026-10-01)

## Context
The first real-data study (SPXW, 14–15 Sep 2022) showed both global models missing by 2–4 vol
points against bid–ask widths of 0.13–0.33 vol points, and by 7–8.5 vol points on expiries
under one week. A single SSVI (ρ, η, γ shared by all expiries) and a constant-parameter Heston
cannot represent 31 expiries from 1 day to 9 months. The two standard remedies were added as
further models, with their acceptance rules predeclared before any code existed.

## Decision
- **Per-expiry raw SVI** (`application/svi_slices.py`): each expiry has its own five-parameter
  smile, fitted on the SSVI pipeline's quotes, forwards, weights and held-out strikes. The
  minimum total variance is a parameter (w_min ≥ 0), so variance can never go negative.
  Independent slices have no arbitrage guarantee: butterfly arbitrage (Gatheral's g(k) ≥ 0 on
  each slice) and calendar arbitrage (w non-decreasing in T between slices) are checked on
  grids and reported, never silently repaired. Between slices, total variance is linear in T
  at fixed k.
- **Heston with a piecewise-constant long-run variance θ(t)** (`HestonTSModel`): buckets
  [0, 7], (7, 30], (30, 91], (91, 182] and (182, ∞) days; v0, κ, σ, ρ are constant. The
  Riccati coefficient D(u, τ) does not involve θ, so the characteristic function stays exact:
  C(T) = κ Σᵢ θᵢ [I_D(T − aᵢ) − I_D(T − bᵢ)] with I_D(τ) = ∫₀^τ D, which the constant-θ
  formula already computes. The vectorised pricer, calibration (now through a
  `Parameterisation` of names, bounds and starts), forward-uncertainty propagation and the
  v0-only next-day refit all work unchanged for both Heston forms.

## Validation (predeclared in `[svi_slices]` and `[heston_term_structure]`)
- Equal θᵢ reproduces constant Heston on all 200 QuantLib fixture cases (1.4e-14, gate 1e-12).
- The characteristic function agrees with an independent QE Monte Carlo in which θ switches at
  the pillars (the time grid contains them): 6 prices within 4 SE + 0.02.
- Calibration to its own exact prices recovers all nine parameters (relative 6e-8, gate 1e-2).
- SVI slices on the SSVI- and Heston-generated synthetic snapshots: held-out containment 100%
  and 95.7% (gate 90%), with no butterfly or calendar violations.

## Not done
An arbitrage-free SVI parameterisation across slices (SSVI-constrained or eSSVI), jumps for the
short end, and time-dependent κ or σ.
