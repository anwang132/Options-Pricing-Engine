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
| `heston_quantlib_v1.json` | QuantLib 1.43 AnalyticHestonEngine (relTolerance 1e-12, 10⁶ evaluations) | 200 cases: 4 parameter sets (two violate Feller), calls/puts, K 60–150, 10 days–3 years, negative rates |
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
| heston_price_vs_quantlib | 200 Heston prices | atol 1e-8, rtol 1e-7 |
| heston_deterministic_limit | σ → 0 vs BSM at √(I(T)/T); ρ ∈ {0, −0.7, 0.4}, κ down to 1e-10 | ρ = 0: 1e-9 at σ = 1e-7; ρ ≠ 0: gap/σ stable to 1e-3 (first order) |
| heston_parity | C − P = D(F − K) | atol 1e-9, rtol 1e-11 |
| heston_integration_settings | default vs tight quadrature | atol 1e-9, rtol 1e-9 |
| lsm_vs_bermudan_crr | Longstaff–Schwartz vs a CRR tree exercising on the same 50 dates (26 cases) | L − B ≤ 4·SE + ε_tree; B − L ≤ 4·SE + ε_tree + 0.005·S/100 + 0.005·B |
| lsm_se_calibration | 200 independent LSM runs | sd(estimates)/mean(SE) in [0.8, 1.25] |
| lsm_dual_upper_bound | Andersen–Broadie bound, 6 cases vs the Bermudan tree | U ≥ B − 4 SE − ε_tree; U − B ≤ 0.01 + 1%·B + 4 SE + ε_tree |
| heston_batch_vs_quantlib | vectorised Heston pricer, 200 cases | atol 1e-8, rtol 1e-7 |
| heston_batch_vs_adaptive_sweep | 540 points: τ × v × moneyness, 3 parameter sets | atol 1e-7, rtol 1e-7 |
| heston_batch_greeks_vs_fd | analytic dP/dS, dP/dv vs central differences | atol 1e-6, rtol 1e-5 |
| heston_calibration_exact_recovery | calibration to exact prices, 2 parameter sets | relative parameter error ≤ 1e-3 |
| heston_calibration_noisy_snapshot | noisy snapshot with defects, next day | held-out containment ≥ 90%; IV error vs truth ≤ 0.005; each parameter within 3 SE; next-day v0-refit containment ≥ 90% |
| hedging_gbm | delta hedging under GBM | slope of log std vs log N in [−0.55, −0.45]; std / leading order in [0.9, 1.1]; wrong-vol mean = Gamma identity within 4 SE + 0.01 |
| heston_qe_simulator | QE paths, 100,000 × 200 steps | martingale within 4 SE + 0.01; calls vs Fourier within 4 SE + 0.02 |
| heston_min_variance_hedge | minimum-variance vs Heston delta | paired z ≥ 3 |
| heston_ts_reduces_to_constant | θ term-structure Heston with equal θᵢ, 200 cases | atol 1e-12 |
| heston_ts_vs_monte_carlo | piecewise-θ characteristic function vs QE with θ(t) | within 4 SE + 0.02 |
| heston_ts_exact_recovery | nine-parameter calibration to exact prices | relative error ≤ 1e-2 |
| svi_slices_synthetic | per-expiry SVI on both synthetic snapshots | held-out containment ≥ 90%; no butterfly or calendar violations |

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
probabilities, naive antithetic SE (fails SE calibration), Heston correlation sign flipped,
Heston mean-reversion term dropped (fails the deterministic-variance limit).

## Advanced models (after Release B; ADRs 0017, 0018)

Both sections of the policy were added, and their hashes logged, before any code for the
model existed.

- **Heston.** The QuantLib comparison is the independent check of the characteristic function
  and integration. The deterministic-variance limit checks the model's structure (mean
  reversion, integrated variance) through a route that shares no code with the Fourier pricer
  except the model object. Its first predeclared form was wrong (it assumed a second-order gap
  for all ρ; the ρσ skew term makes it first order); the correction and its reasoning are in
  `docs/progress.md`. The smile view is checked for a flat BSM smile, negative skew for ρ < 0, a
  symmetric smile at ρ = 0, and flattening to √(I(T)/T) as σ → 0 (`tests/unit/test_visuals.py`).
- **Longstaff–Schwartz.** The reference is exact for the contract LSM actually prices (Bermudan
  on the same dates), so the gate measures regression error, not the Bermudan-vs-American gap
  (which is reported separately). Both tails are gated: the pricing estimator must not be biased
  upward, and its low bias must stay within a predeclared allowance. The first run passed, but
  one case (American call, two cash dividends) used 93% of its allowance with the in-sample
  estimate also below the reference. That pointed at the regression basis, so the European value
  of the remaining contract was added as a regressor; that case is now within about 1 SE. The gate
  was not changed.

## Calibration, hedging, duality and real data (ADRs 0019–0023)

All five policy sections were predeclared and hashed before their code. Two findings changed the
implementation, not the gates:

- **Vectorised Heston pricer.** A fixed node set missed the QuantLib gate by a factor of 35: the
  oscillating factor e^{iux} was under-resolved at large |x|. Node sets are now built per
  maturity and row group from the characteristic function's decay and the phase |x|·Δu.
- **Calibration standard errors.** Quote-noise-only Gauss–Newton errors put σ 4 SE from the truth.
  With the true forwards every parameter was within 2 SE, so the forwards' estimation error was
  missing. It is now propagated by the delta method from the parity regressions.

What the checks establish: the hedging simulator reproduces the textbook discrete-hedging
theory (N^−1/2 decay, the Gamma-weighted variance, the vol-mismatch identity); the QE simulator
is unbiased at the stated tolerance even with the Feller condition violated; calibration
recovers known parameters with honest uncertainty; the dual bound brackets the exact Bermudan
price. The real-data study has no gates. Its metrics are fixed in `[real_data_study]` so that
none is chosen after seeing results.

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
