# 0020 — Heston calibration with parameter uncertainty

Status: accepted (2026-09-30)

## Decision
- **Same data preparation as the SSVI fit** (ADR 0015): eligible European quotes, per-expiry
  forwards and discounts from put-call parity, the out-of-the-money quote per strike, every 4th
  strike held out, and weights max(half-spread, 0.05, 0.5% of mid). The two models are then
  comparable on identical quotes.
- **Objective:** Σ ((model − mid)/weight)² over in-sample quotes. `scipy.optimize.least_squares`
  (trf, x_scale = jac) runs from 5 starts, the first built from ATM Black vols (shortest expiry
  → v0, longest → θ). Box bounds: v0, θ ∈ [1e-4, 1]; κ ∈ [1e-3, 20]; σ ∈ [0.01, 3];
  ρ ∈ [−0.99, 0.99].
- **Uncertainty:** Gauss–Newton covariance s²(JᵀJ)⁻¹ at the optimum **plus the uncertainty of
  the forwards.** Each expiry's (F, D) has an OLS covariance from the parity regression. The
  optimum moves with them as ∂θ/∂η = −(JᵀJ)⁻¹Jᵀ∂r/∂η, so their covariance adds G Σ_η Gᵀ
  (delta method). The artifact reports standard errors with and without this term, the
  correlation matrix, the condition number, and parameter pairs with |corr| > 0.9 (the data do
  not separate them).
- **Next day:** held fixed (all parameters), and with only v0 refitted; v0 is the state variable,
  and κ, θ, σ, ρ are structural.
- **Artifacts** are content-addressed and immutable, like surface fits, including failures.

## Finding that shaped the uncertainty model
On the noisy synthetic snapshot, the first version (quote noise only) put σ 4.0 standard
errors from the truth, which would have failed the predeclared 3-SE gate. Re-running with the
*true* forwards put every parameter within 2 SE, so the missing term was the forwards'
estimation error. With the delta-method term the largest deviation is 2.5 SE. The gate did not
change; the standard errors became honest.

## Results (synthetic, `reports/release-d`)
Exact-price recovery to relative error ~1e-10 (two parameter sets); noisy snapshot: 100%
held-out containment, 0.03 vol-point IV error vs the generating model, all parameters within
3 SE. Next day with all parameters held: 0% containment; refitting v0 alone: 100%.

## Not done
Time-dependent parameters, joint calibration across days, and regularisation towards prior
estimates.
