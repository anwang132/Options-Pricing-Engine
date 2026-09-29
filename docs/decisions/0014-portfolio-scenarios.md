# 0014 — Portfolio valuation and deterministic scenarios

Status: accepted

## Decision
- Position value = signed contract quantity × contract multiplier × per-unit price.
- One underlying and one currency per request; anything else is rejected (no FX model).
- Full repricing through the same `PricingService` as single-contract pricing (European →
  closed form; American → CRR, default 400 steps, refinement allowed and reported).
- Shock conventions: spot in % of spot, volatility in absolute vol points, rate in basis
  points, time roll in calendar days. Shocks that would make volatility negative are rejected,
  not clipped.
- Surface dynamics are explicit: `sticky_strike` (each position keeps its volatility) or
  `sticky_moneyness` (volatility re-read from a fitted SSVI surface at the scenario's forward
  moneyness; requires `fit_id` and the fit's underlying must match). Recalibrated scenarios are a
  separate operation and not implemented.
- Time rolls at or past expiry settle the position at intrinsic value at scenario spot, held as
  cash without interest; Greek approximations are then reported as unavailable. Dividends
  whose ex-date passes leave the schedule; spot moves only by the shock.
- Attribution: full P&L vs Δ·dS + ½Γ·dS² + vega·dσ + Θ·dt + ρ·dr with base Greeks; the
  difference is reported as "unexplained".
- Work limits: ≤ 50 positions, ≤ 1,000 scenarios, ≤ 50,000 repricings, ≤ 10,000 American
  repricings and ≤ 4e9 American tree node-steps (Σ steps²). The node-step budget was added after
  measuring an 89 s worst case under count-only limits. Bulk repricing uses
  `PricingService.evaluate` (no request hash or assumption text, ~30% of a closed-form repricing)
  and turns off the CRR diagnostic trees; measured worst cases are 6.8 s and 8.4 s (Apple M4,
  reports/post-review), below the 30 s client timeout.
- Portfolios are neither persisted nor logged.
