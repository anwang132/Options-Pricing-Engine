# 0019 — Vectorised Heston pricing on an adaptive fixed rule

Status: accepted (2026-09-30)

## Context
The adaptive Heston pricer (ADR 0017) takes about 5 ms per price. Calibration needs a few
hundred prices per objective evaluation, and the hedging experiment needs a delta for every path
at every rebalancing date (10⁵–10⁶ prices). Both need prices for many strikes, or many variance
states, at one maturity.

## Decision
- **Split the characteristic function** as log φ = C(u, τ) + D(u, τ)·v. One evaluation of C and
  D on a node set serves every strike and every variance state at that maturity; each row then
  costs one complex exponential per node.
- **Node set built per maturity and group of rows:** composite 32-point Gauss–Legendre on dyadic
  segments of [0, U]. U is where |φ(u − i/2)|/(u² + 1/4) at the group's smallest variance falls
  below 1e-16, probed on a log grid up to 65,536. Segments are split so that e^{iux} turns
  through at most 12 radians per segment at the group's largest |x|. Rows are grouped by remaining
  integrated variance in factor-2 bins.
- **Derivatives by differentiating the integrand:** dP/dF uses the factor (1/2 + iu), and dP/dv
  uses D(u − i/2). The minimum-variance hedge needs dP/dv, and bumping it would double the cost.
- **Routing:** rows with I(τ; v) < 1e-5, or strikes more than 10 SD out, are priced by the
  deterministic-variance limit (BSM at √(I/τ)). In the validation sweep no point needed it.

## History (logged in docs/progress.md)
The first version used one fixed node set (32 nodes per dyadic segment up to 4096). A scratch
run against the QuantLib fixture missed the predeclared tolerance by a factor of 35: at large
|x| the oscillating factor was under-resolved (42 oscillations across one 32-node segment). The
adaptive node rule fixed it (worst error 4e-12). The routing threshold was then lowered from
4e-4 to 1e-5 before the formal check ran; this routes less to the approximation and puts more
points under the unchanged gate.

## Validation
`heston_batch_vs_quantlib` (200 cases, atol 1e-8, rtol 1e-7), `heston_batch_vs_adaptive_sweep`
(540 points across maturity × variance state × moneyness for three parameter sets including two
Feller violations), and `heston_batch_greeks_vs_fd` (analytic dP/dS and dP/dv against central
differences of the adaptive pricer).
