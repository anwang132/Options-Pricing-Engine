# 0005 — Dividend treatment: continuous yield and escrowed cash dividends

Status: accepted

## Decision
- Continuous dividend yield q is the base model.
- Known cash dividends use the **escrowed-dividend model**: the risky part S* = S − Σ Dᵢe^{−r tᵢ}
  (ex-dates in (as_of, expiry]) follows GBM with volatility σ. European prices are BSM on S*;
  the CRR tree runs on S* and evaluates early exercise at S* + PV(dividends still to be paid).
- The ex-date is used as the payment date for discounting (payment-date lag not modelled).
- Dividends with ex-date ≤ as_of are treated as already reflected in spot and ignored.
- Greeks: ∂S*/∂S = 1; theta adds Δ·(−r·PV); rho adds Δ·Σ Dᵢtᵢe^{−r tᵢ}.

## Why
The escrowed model recombines, has an exact European solution, and is one of the dividend models
QuantLib implements (`FdBlackScholesVanillaEngine.Escrowed`, `AnalyticDividendEuropeanEngine`),
giving an independent reference with matching conventions. It is a *different model* from a
spot-jump GBM with the same σ — prices differ, most for long maturities and large dividends —
and is labelled as such rather than as an approximation of the jump model.

## Consequences
Theta holding S fixed is discontinuous across an ex-date (the dividend leaves the schedule).
