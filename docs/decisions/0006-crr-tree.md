# 0006 — CRR binomial tree

Status: accepted

## Decision
- u = e^{σ√Δt}, d = 1/u, p = (e^{(r−q)Δt} − d)/(u − d) computed as
  (expm1((r−q)Δt) − expm1(−σ√Δt)) / (2 sinh(σ√Δt)).
- p must lie strictly in (0,1). Otherwise `invalid_tree_probability` with the minimum valid N.
  `allow_step_refinement` (opt-in) raises N to that minimum within `max_steps`, and the step count
  used is reported. Probabilities are never clipped (a fault-injection test shows clipping is
  detected by the validation suite).
- Rolling 1-D arrays (O(N) memory). Work limit 20,000 steps (measured worst case with all Greeks:
  1.9 s).
- Delta/gamma/theta from nodes at steps 1–2; vega/rho/dividend rho by central bump-and-reprice at
  the same N (bumps 1e-3 vol, 1e-4 rate). σ = 0 and T = 0 are not supported (analytic engine is).
- Diagnostics: odd/even spread |V(N) − V(N+1)| — a heuristic indicator, not an error bound — and,
  for American exercise, the early-exercise premium on the same tree.
