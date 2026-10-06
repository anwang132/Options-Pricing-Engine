# 0015 — SSVI surface calibration with immutable fit artifacts

Status: accepted

## Decision
- **Model:** SSVI with power-law φ(θ) = η θ^{−γ}(1+θ)^{γ−1} (Gatheral & Jacquier 2014,
  arXiv:1204.0646). Constraints: |ρ| < 1; η = 2u/(1+|ρ|) with u ∈ (0, 1] so η(1+|ρ|) ≤ 2;
  γ ∈ [0.01, 0.5]; θ₁ > 0 and non-negative increments (θ non-decreasing).
- **Guarantee (conditional):** with these constraints the butterfly conditions of Theorem 4.2
  hold: θφ(θ)(1+|ρ|) = η(θ/(1+θ))^{1−γ}(1+|ρ|) < 2 < 4, and
  θφ(θ)²(1+|ρ|) = η²(θ/(1+θ))^{1−2γ}(1+θ)^{−1}(1+|ρ|) ≤ 4/(1+|ρ|) ≤ 4 (using γ ≤ ½).
  The calendar condition of Theorem 4.1 holds because θ is non-decreasing in t, θφ(θ) is
  increasing, and ∂θ(θφ)/φ = (1−γ)/(1+θ) ≤ 1 ≤ (1+√(1−ρ²))/ρ². θ is interpolated piecewise
  linearly in t (θ(0) = 0) and extrapolated linearly with the last segment's non-negative slope,
  which preserves monotonicity. Wing slopes θφ(1±ρ)/2 < 1 respect Lee's moment bound (≤ 2).
  The claim is about the fitted parametric surface, not the market quotes.
- **Sampled diagnostics (separate):** Gatheral's g(k) ≥ 0, calendar monotonicity of w, and
  call-price monotonicity/convexity in strike on a k ∈ [−1.5, 1.5] × 60-maturity grid including
  extrapolated maturities; reported as "no violations detected on the tested grid". A test
  shows these diagnostics detect a deliberately arbitrageable surface.
- **Data:** only accepted European quotes with standard terms; American quotes are excluded with
  reason `american_exercise_not_european_observation`. Forward and discount per expiry from
  put-call parity (OLS of C−P on K, ≥ 4 pairs, sanity-checked), else the snapshot's carry inputs,
  recorded per expiry. OTM quotes only; every 4th strike per expiry held out.
- **Objective:** price-space least squares, residual = (model − mid)/w with
  w = max(half-spread, 0.05, 0.5%·mid); 5 deterministic restarts (seed 7), TRF with bounds,
  ≤ 3,000 evaluations each. Reported: optimizer status per start, bound hits, price RMSE,
  spread-weighted residuals, bid/ask containment — in-sample, held-out, and optionally on a
  later snapshot with the surface held fixed in (k, T).
- **Artifacts:** fit id = hash(snapshot id, later snapshot id, config, code version); stored once,
  including failed fits (`insufficient_data`, `optimizer_did_not_converge`). Listing marks fits
  of older snapshots as stale; previous fits keep their timestamps and are never substituted.
- **Gate:** `[surface_gate]` in the policy, predeclared before fitting code existed.

## Why SSVI rather than per-slice raw SVI
Per-slice SVI can fit each expiry more closely but offers no continuous static-arbitrage
guarantee across strikes and maturities; SSVI's constrained family makes the claim defensible.
