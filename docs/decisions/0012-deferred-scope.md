# 0012 — Deferred scope

Status: accepted

- **PDE and Heston:** deferred until Release B is complete and verified; when added, follow the
  plan's refinement studies and the deterministic-variance limit
  I(T) = θT + (v0 − θ)(1 − e^{−κT})/κ.
- **VaR / expected shortfall:** not implemented. The simulators are risk-neutral pricing tools;
  they do not forecast real-world loss probabilities.
- **Live data providers (e.g. yfinance):** not implemented. Snapshots come from a documented
  file format and a synthetic generator; any provider adapter must stay outside the numerical
  core and its terms must be checked first.
- **Settlement lag, adjusted deliverables, multi-currency aggregation, trinomial, local vol,
  jumps:** out of scope; rejected explicitly where they could be requested.
- **Cancellation of running computations:** not needed at current measured budgets (0009).
