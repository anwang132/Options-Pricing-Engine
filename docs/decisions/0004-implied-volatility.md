# 0004 — Implied volatility by bracketed Brent inversion

Status: accepted

## Decision
1. Reject non-finite/negative quotes and T = 0 (`invalid_quote`, `at_expiry`).
2. Check model-free bounds under deterministic carry: below lower → `below_lower_bound`; within
   1e-10 + 1e-9·scale of the lower bound → `at_lower_bound` (σ not identifiable); at/above upper
   → `at_or_above_upper_bound`.
3. Invert the OTM time value (quote − discounted forward intrinsic); same root, better conditioned.
4. Bracket [0, 0.5] and double up to the cap σ = 5 (`no_bracket` beyond). `scipy.optimize.brentq`
   with xtol 1e-15, rtol 1e-14, maxiter 200 (`max_iterations`).
5. Require a price residual ≤ 1e-10 + 1e-9·quote (`residual_check_failed` otherwise).
6. **Stability:** the quote resolution defaults to half a unit in the last supplied decimal place
   ("4.70" → 0.005). If resolution / vega > 0.01 (one vol point) the status is
   `unstable_low_vega` and the value is still returned with the IV interval implied by
   quote ± resolution. Bid and ask are inverted separately.
7. American quotes are rejected: they are not European BSM observations.

## Why not Newton
Newton is fast but unreliable where vega is small; Brent on a verified bracket always terminates,
and the residual + conditioning checks catch solutions that terminate but are meaningless.
