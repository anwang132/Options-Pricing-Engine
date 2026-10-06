# 0002 — Time, units and quote conventions

Status: accepted

## Decision
- **Time:** timezone-aware timestamps only. T = (expiry − as_of) seconds / (365 × 86400), i.e.
  ACT/365F on exact elapsed time. No code reads the wall clock; `as_of` is always explicit.
- **Expiry states:** T < 0 → `expired_contract`; T = 0 → price is the undiscounted payoff and
  theta is `not_applicable_at_expiry`.
- **Rates, yields, vols:** decimal annual, continuously compounded (0.05 = 5%). Negative rates and
  yields are accepted. Domain limits (|r|,|q| ≤ 1, σ ≤ 5, T ≤ 100) are engineering limits and are
  rejected, never clipped.
- **Quote basis:** prices are per unit of underlying; contract value = price × multiplier, where
  the multiplier is instrument metadata (no default of 100 in the domain).
- **Precision:** inputs are ingested as `Decimal` (strings preserve trailing zeros) and converted
  to float64 once, at the numerical boundary. Results are never rounded except for display.
- **Greeks (raw units):** delta ∂V/∂S; gamma ∂²V/∂S²; vega per 1.00 σ; theta = ∂V/∂t per year of
  calendar time holding S, σ, r, q fixed (= −∂V/∂T); rho per 1.00 r; dividend rho per 1.00 q.
  Display units (vega/rho per percentage point, theta per calendar day) are separate, explicit
  conversions returned alongside raw values.
- **Settlement:** only settlement at expiry is priced; a positive settlement lag or adjusted
  deliverable is rejected as `unsupported_contract`.

## Consequences
Theta with cash dividends includes the effect of rolling the dividend PV (0005). A plan-level
note: there is no JIT in this project, so "cold vs steady state" is reported as import +
first-call cost.
