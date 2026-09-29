# Validation

Run everything: `uv run options-engine validate --out reports/<name>` (≈45 s on an Apple M4).
Policy: `config/validation_policy.toml` (tolerances, stress matrix, statistical rules), written
before the first run; amendments are logged with hashes in `docs/progress.md`.

## Tolerance rule

|error| ≤ atol + rtol·|reference|, with atol in price units at spot 100 scaled by spot/100.
The report lists the *scaled error* max|error|/(atol + rtol|ref|) per check (≤ 1 passes).

## Reference provenance

| Fixture | Source | Content |
|---|---|---|
| `bsm_european_mpmath_v1.json` | mpmath 1.4.1, 60 digits, independent formula in `scripts/generate_reference_fixtures.py` | 332 stress cases: price + 6 Greeks (numerical derivatives of the mpmath price) |
| `normalized_black_grid_v1.json` | mpmath 1.4.1 | 3,850-point grid of b(x,s), \|x\| from 1e-14 to 20, s from 1e-9 to 40 |
| `bsm_european_quantlib_v1.json` | QuantLib 1.43 AnalyticEuropeanEngine | 480 cases, integer-day maturities, price + Greeks |
| `american_quantlib_fd_v1.json` | QuantLib 1.43 FdBlackScholesVanillaEngine (Douglas, 2000×1600, damping 10; escrowed dividends) and AnalyticDividendEuropeanEngine | 192 American / cash-dividend cases |
| `bsm_published_examples_v1.json` | Hull's BSM worked example (4.76 / 0.81), compared at printed rounding | 1 case (transcribed; edition/page not re-verified) |

Regenerate: `uv run --group reference python scripts/generate_reference_fixtures.py`.
Drift check: `uv run --group reference pytest -m reference`.

## Stress matrix (policy v1)

Regimes × calls and puts: ordinary (90 combos), deep ITM/OTM, short maturity (1 day, 1e-4,
1e-6 years), long maturity (10, 30 years), low vol (1e-4 … 5e-3), high vol (150%, 300%),
negative rates/yields, high yield. 332 cases.

## Gates (deterministic)

| Check | What | Tolerance |
|---|---|---|
| bsm_price_vs_mpmath | price, full stress matrix | atol 1e-12, rtol 1e-11 |
| normalized_black_vs_mpmath | dense grid incl. near-ATM-forward and tails | rtol 1e-11 |
| bsm_greeks_vs_mpmath | 6 Greeks × 332 | atol 1e-9, rtol 1e-8 |
| bsm_price/greeks_vs_quantlib | 480 cases | 1e-10 / 1e-7 rel |
| bsm_published_examples | Hull example | half unit in last printed digit |
| identities | parity and bounds, all stress points | atol 1e-11, rtol 1e-12 |
| boundary_behaviour | T = 0, σ = 0, kinks → exact values or statuses | exact |
| greeks_fd_sweep | best central difference over 7 bump sizes | atol 1e-7, rtol 1e-6; low-vol, short-maturity and deep-tail regimes **excluded by policy** (kink/cancellation dominated) |
| implied_vol_round_trip | invert mpmath prices | vol error ≤ 1e-9 + 10·price_tol/vega; tiny time value must give `at_lower_bound` |
| implied_vol_failure_cases | 10 adversarial quotes | exact status |
| crr_european_vs_bsm | N = 2000, 2001 on the stress matrix | k·S·σ·√T/N, k = 0.5; invalid probabilities must be rejected |
| american_identities | American ≥ European, ≥ intrinsic, call equality when q = 0, r ≥ 0 | 1e-10 relative slack |
| crr_vs_quantlib_fd | CRR 4000 steps vs QuantLib FD / analytic dividend engine | atol 5e-3, rtol 1e-3 |

## Statistical gates (Monte Carlo)

5 cases × 4 methods (plain, antithetic, control variate, both), 500 independent replications
× 20,000 payoff evaluations, master seed 918273645. Per test α = 0.01/60 (Bonferroni):
- coverage count of nominal 95% CIs inside the central binomial region [455, 491];
- bias z-test |z| ≤ 3.76;
- SE calibration: var(estimates)/mean(SE²) inside the χ² region [0.779, 1.256].
Also reported: variance per payoff evaluation and efficiency (variance × runtime) vs plain.

## Fault injection (`tests/mutation/`)

Mutants that must fail: discount sign, call/put swap, dropped or sign-flipped dividend yield,
volatility read as percent, ACT/360 time, vega per vol point, theta sign, clipped tree
probabilities, naive antithetic SE (fails SE calibration).

## Release B evidence

- **Snapshots** (`tests/unit/test_snapshots.py`): every injected defect in
  `fixtures/snapshots/synthetic_day1.json` maps to its specific reason; counts reconcile to the
  total; file-level errors reject the file; idempotent, immutable, tamper-detecting store;
  malformed ids rejected.
- **American + cash dividends** (`tests/unit/test_dividend_events.py`, `crr_vs_quantlib_fd`):
  prices continuous across ex-dates (European analytic/tree, American put); American call
  exercises just before a large dividend; unsupported American combinations rejected.
- **Portfolio** (`tests/unit/test_portfolio.py`): exact cancellation of offsetting positions,
  linear scaling, multiplier mutant detected, zero-shock scenario reproduces base, attribution
  residual small for 1% shocks and larger for 10%, expiry crossing settles at intrinsic, work
  limits (including tree node-steps) enforced.
- **Surface** (`tests/unit/test_surface.py`, `surface_calibration_gate` in the report): the
  predeclared `[surface_gate]` (held-out containment ≥ 90%, IV error vs generating surface ≤ 0.005
  on k ∈ [−0.3, 0.2], no sampled violations, guarantee conditions) on both synthetic fixtures;
  Hypothesis test that constrained parameters are butterfly/calendar free on a grid; sampled
  diagnostics detect a deliberately arbitrageable surface; failed fits recorded; previous fits
  keep timestamps; later-snapshot evaluation without refit.

The synthetic snapshots are generated from a known SSVI surface
(`options-engine snapshot generate-synthetic`), so parameter recovery is testable. Real market
behaviour (American equity quotes, discrete dividends, stale and illiquid wings) is harder than
this fixture; results on it are not evidence about real markets.

## Visualisation data (`tests/unit/test_visuals.py`)

- Value profile: centre point equals the single-contract price and Greeks; call delta
  non-decreasing; at-the-money value decays across horizons toward the payoff.
- Early-exercise boundary: below the strike and rising toward it near expiry for a put; recording
  it leaves the tree price bit-identical; for T = 50 years its earliest resolvable value lies
  within one node spacing above the perpetual American put boundary K·2r/(2r+σ²)
  (61.54 → 62.18 at 4,000 steps).
- Risk-neutral density: Gatheral's closed form agrees with Breeden–Litzenberger second
  differences of Black prices from the same surface (rtol 5e-3); mass and E[F_T/F] within 2e-3
  of 1; non-negative on the grid.

## Reproducibility across platforms

Results are bit-reproducible on one platform and library build (manifest replay checks this).
Across BLAS/LAPACK builds they are not: a surface fit computed in the Linux container (OpenBLAS)
and on macOS (Accelerate) differed in the last bits, starting in the `numpy.linalg.lstsq`
parity regression. Reports therefore record NumPy/SciPy versions and the BLAS implementation.

## Findings recorded so far

See docs/progress.md "Defects found by validation": near-forward-ATM accuracy defect, 0-d input
crash, exact-equality kink detection — each found by the suite and fixed.
