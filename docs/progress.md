# Progress log

Factual record of gates, commands and next steps. Newest entries at the bottom.

## Predeclaration record

- 2026-09-28T17:38:12Z — `config/validation_policy.toml` (policy v1) written before any
  statistical Monte Carlo replication run or fixture comparison.
  SHA-256 `46d524fc2ed8443164ead30168cf282b3e26a7563445a5c57a09c19140be139a`.
  No commits existed at that time, so git history cannot attest to this ordering; this log
  entry is the record. Any later change to the policy must
  appear below with its new hash and reason.
- 2026-09-28T17:40:31Z — policy amended before any check was executed: added
  `tolerance.finite_difference_sweep.regimes` (declares which regimes the FD sweep gate
  covers). New SHA-256 `b341906a9fc9dcc96a7efb624e05fb274cc353fd56b9f93196c8d636a4cb565b`. Fixtures were generated with the previous hash
  (fixture provenance records it); the stress matrix itself is unchanged.
- 2026-09-28T19:54:52Z — policy amended: added `[surface_gate]` (M6 acceptance rules) before any surface-fitting
  code was written or run. New SHA-256 `6e5ec2ff3d415618c5731b4050e058fbb1ee79997a9ae213edd4eb8abe834aee`. Existing sections unchanged.
- 2026-09-29T17:38:21Z — policy amended: added Heston tolerances and parameter sets before any Heston code
  was written. New SHA-256 `b297fc256edb81bcf33cb05002bb853c614afcb7e82fd1c694e03b1184b930c2`. Existing sections unchanged.
- 2026-09-29T17:40:58Z — policy corrected: `heston_deterministic_limit` had assumed the Heston-BSM gap
  vanishes below 1e-9 at sigma = 1e-7 for any rho. A scratch run showed the gap is first order
  in sigma when rho != 0 (gap/sigma -> -1.7212 for K=110, rho=-0.7), as the small-vol-of-vol
  expansion predicts (skew term ~ rho*sigma). The 1e-9 tolerance now applies at rho = 0 (gap is
  second order: 3e-14 observed); for rho != 0 a first-order convergence criterion is used. Not a
  loosening: the original criterion was mathematically wrong. New SHA-256 `e257e0c953f3b0711fd3ac363f8217689ba27f52abd5963247f5eb68084d4a81`.
- 2026-09-29T17:52:19Z — policy amended: added `[lsm]` (Longstaff-Schwartz acceptance rules, case matrix and
  statistical SE-calibration band) before any LSM code was written. New SHA-256
  `76e2db68f216a7d9d078b9a0ef5e06902f65c93702fb57581a8552c7b71358f8`. Existing sections unchanged.
- 2026-09-30T19:00:59Z — policy amended: added `[heston_batch]`, `[heston_calibration]`, `[hedging]`,
  `[lsm.upper_bound]` and `[real_data_study]` before any code for them was written.
  New SHA-256 `797f0225d22963ac0bfa064d26ef515e54e4a500c21fc42843fb742cea47e82a`. Existing sections unchanged.
- 2026-09-30T19:05:32Z — policy amended before any formal Heston-batch check ran:
  `heston_batch.fourier_min_total_variance` 4e-4 → 1e-5. A scratch run of the first (fixed-node)
  implementation missed the gate (worst scaled error 35 vs QuantLib; e^{iux} under-resolved at
  large |x|), so the node rule was made adaptive; with it, all 540 sweep points down to I = 1e-5
  are within 2e-11 of the adaptive pricer. Lowering the threshold routes fewer prices to the
  deterministic-variance approximation and puts more points under the unchanged 1e-7 gate: a
  tightening, not a loosening. New SHA-256 `fdf412bb369705ba78921a5df7760ffe0ef153ca5320ba9d392410fa8a0dacc0`.
- 2026-10-01T14:24:00Z — policy amended: added `[svi_slices]` and `[heston_term_structure]`, and two models in
  `[real_data_study].models`, before any code for them was written. The real-data metrics and
  comparison metrics are unchanged. New SHA-256 `c1b9783c1f13a73a2842af14d2958436a6cb8f12e044530b5f0bd4a37a5b640a`.

## Defects found by validation

- 2026-09-28 — **Near-forward-ATM accuracy defect (fixed).** Hypothesis property tests emitted
  `scipy.integrate.quad` IntegrationWarnings. Investigation: for |x/s| << 1 with moderate s the
  cancellation fallback used a quadrature whose integrand drops sharply at r ~ |x/s|; relative
  errors reached 1.4e-5. The stress matrix missed it (no case with ln(F/K) that close to 0).
  Fix: a near-money rearrangement b = (A-B)cosh(x/2) + (A+B)sinh(x/2) with Gauss-Legendre
  A-B for |x/s| <= 1; quadrature only for |x/s| > 1. Added fixture
  `normalized_black_grid_v1.json` (3,850 points, |x| down to 1e-14) and check
  `normalized_black_vs_mpmath`, using the predeclared `bsm_vs_mpmath.price` rtol (1e-11).
- 2026-09-28 — `normalized_otm_price` crashed on 0-d inputs when a fallback was needed
  (engines unaffected: they pass 1-d arrays). Fixed; covered by unit tests.
- 2026-09-28 — Zero-volatility kink detection used exact float equality of S e^{-qT} and
  K e^{-rT}; inputs equal up to rounding reported a confident delta. Now a kink is declared
  within 8 ulps. Found by `boundary_behaviour` check on first run.

## Performance changes (measure first)

- 2026-09-28 — **Vectorised cancellation fallback.** Profiling the 1,000,000-option batch benchmark
  showed ~7.5% of options in the cancellation regime, each calling scalar `scipy.integrate.quad`
  (~95% of runtime). The split formula is now vectorised and used for |x/s| <= 8; quadrature only
  beyond. Its error grows like h^4·eps, so the cut-off was chosen by scanning the 60-digit grid
  fixture: max relative error unchanged at 1.78e-12 (worst point is in the direct branch).
  GL sums use a fixed-order loop so scalar and batch results stay bit-identical.
  Before: median 1.519 s (reports/release-a). After: see reports/release-b.

## Milestones

### Release A — offline pricing and validation workbench: GATE PASSED (2026-09-28)

Evidence: `reports/release-a/report.md|json` (all 14 deterministic checks and 60 statistical
tests pass; dirty-state sha256 de9951d0…, base commit 0848255, uncommitted working tree).
Commands run for the gate:
- `uv run ruff format --check . && uv run ruff check . && uv run mypy` — clean (strict on src/scripts)
- `uv run pytest -q` — 165 passed (deterministic tier)
- `uv run --group reference pytest -q -m "statistical or reference"` — 4 passed
- `cd ui && npm run typecheck && npm test && npm run build` — 14 tests passed, build OK
- `node ui/e2e/smoke.mjs http://127.0.0.1:8765 <dir>` — browser walk-through of every view,
  including tree rejection, impossible quote, low-vega IV, American engine filtering,
  390 px layout; no console errors
- `docker build -t options-workbench:dev .` + container smoke (health, price, UI, non-root)
- `uv run options-engine demo` — offline demo incl. four failure cases

Known limitations at this gate: UI bundle 660 kB (Recharts, not code-split); timeouts do not kill
workers (bounded by measured budgets, ADR 0009); the working tree was not yet committed.

### Release B — auditable market-data and scenario workbench: GATE PASSED (2026-09-28)

| Milestone | Gate evidence |
|---|---|
| M3 snapshots | `tests/unit/test_snapshots.py` (15): each injected defect → specific reason; file-level rejection; idempotent, immutable, tamper-detecting store; API endpoints |
| M4 American + cash dividends | `crr_vs_quantlib_fd` (192 cases incl. escrowed dividends); `tests/unit/test_dividend_events.py` (8): continuity across ex-dates, early exercise before a dividend, unsupported cases rejected |
| M5 portfolio | `tests/unit/test_portfolio.py` (19): offsets cancel exactly, linear scaling, multiplier mutant detected, attribution residual, expiry settlement, work limits |
| M6 surface | predeclared `[surface_gate]` passes on both synthetic fixtures (`surface_calibration_gate` in the report); `tests/unit/test_surface.py` (12) incl. failed-fit recording and stale labelling |

Evidence: `reports/release-b/report.md|json` — all 15 deterministic checks and 60 statistical
tests pass (dirty-state sha256 637a1a02…, base commit 0848255, uncommitted tree).
Commands run for the gate:
- `uv run ruff format --check . && uv run ruff check . && uv run mypy` — clean
- `uv run pytest -q` — 220 passed; `uv run --group reference pytest -q -m "statistical or reference"` — 4 passed
- `cd ui && npm run gen:types` (no diff) `&& npm run typecheck && npm test && npm run build` — 14 passed
- `node ui/e2e/smoke.mjs http://127.0.0.1:8765 <dir>` — 13 checks incl. snapshot quality report,
  surface fit with later snapshot, surface-driven portfolio, underlying-mismatch failure
- `uv run options-engine demo` — sections 1–10
- `uv run options-engine validate --out reports/release-b` — PASS (62 s)

Found and fixed during Release B:
- Portfolio worst case under count-only limits took 89 s (> 30 s timeout) → added an American
  tree node-step budget (ADR 0014); measured worst cases now 10.5–14.6 s.
- The report's own benchmark book reused position ids; rejected by portfolio validation; fixed.
- Duplicate-quote key ignored the deliverable (adjusted contracts counted as duplicates); fixed.
- A surface fitted for one underlying could supply vols to another; now rejected.

## Code review (2026-09-28, after Release B)

Full read-through of the Python and TypeScript code. Changes (all tests, the browser walk-through
and a fresh report `reports/post-review` pass; API schema unchanged):
- **Bug:** HTTP admission control released a slot when the client timed out although the job kept
  running, so capacity was overstated after timeouts. Slots are now released by the job's own
  completion callback; queued jobs are cancelled on timeout. Regression test added.
- **Performance (measured first):** the request hash was 29% of a closed-form repricing and the
  CRR engine always built an extra European tree for American contracts. Added
  `PricingService.evaluate` for bulk use and `CRRConfig.early_exercise_diagnostic`. Portfolio worst
  cases 10.5/14.0/3.4 s → 6.8/8.4/2.0 s.
- **Clarity:** diagnostic `tail_quadrature_used` renamed `cancellation_fallback_used` (it flags
  either fallback); `d1_d2` shared by the Greeks, diagnostics and IV vega; surface fitting split
  into `_initial_points`, `_optimize`, `_bound_hits`, `_residual_rows`, `_black_iv`; engine schema
  defaults derived from the domain configs (no duplicated numbers); field-copying conversions
  replaced by `model_dump`/`dataclasses.asdict`/`replace`; unused `worker.register`, dead CRR
  `warnings`, unused parameters and redundant checks removed; leftover inline imports hoisted.
- **UI:** a `useAction` hook replaces 22 hand-written busy/error blocks across seven views; the
  Compare chart takes its CI from the API instead of a hard-coded z.
- Verified the surface refactor reproduces the earlier local fit bit-for-bit; the comparison also
  exposed cross-platform last-bit differences (documented in docs/validation.md).

## Visualisations (2026-09-28)

Added engine-computed chart data and UI: value/Greek profiles (`/api/v1/visuals/profile`),
American early-exercise boundary (`/api/v1/visuals/exercise-boundary`, recorded by an optional
hook in `build_tree`), and surface views (`/api/v1/surface/fits/{id}/views`: IV grid, ATM term
structure, risk-neutral density). Typed pydantic responses, so frontend types are generated.
Tests: 12 new (`tests/unit/test_visuals.py`); browser walk-through extended to 17 checks. Chart
components load lazily; initial bundle unchanged at ~258 kB.

## Paper trading (2026-09-28)

Goal: make the app usable for tracking trading decisions with money at stake. Of the options
considered (paper ledger / real data import / live broker), the **paper-trading ledger** was
chosen as the first step.
Implemented per ADR 0016: `adapters/paper_store.py`, `application/paper.py`, nine
`/api/v1/paper/...` endpoints with typed schemas, and a **Paper trading** tab. Tests: 18 in
`tests/unit/test_paper.py` with hand-worked numbers (average cost, crossing zero, fees,
settlement, voids, backdating, tamper detection, concurrent writers), plus a browser session
(equity 10,079.35 and P&L 79.35 checked on screen). Found and fixed on the way: a trade-id race
under concurrent writers; a UI bug where an account opened at 03:42:53 rejected a trade entered
at 03:42 (minute-precision field). Also: tests now always use a temporary data directory (a stray
`./data` from an earlier run was removed).

## Heston and Longstaff–Schwartz (2026-09-29)

Goal: strengthen the quantitative depth with a stochastic-volatility model and a regression
Monte Carlo American engine, each validated against an independent reference. Both policy
sections were predeclared and hashed before the code existed (entries above).

- **Heston** (ADR 0017): `models/heston.py`, `engines/heston_fourier.py`, `HestonConfig`,
  fixture `heston_quantlib_v1.json` (200 QuantLib cases), checks `heston_price_vs_quantlib`,
  `heston_deterministic_variance_limit`, `heston_put_call_parity`,
  `heston_integration_settings`, two mutants. API: `HestonModelIn` (default engine chosen by
  model family), `/api/v1/visuals/smile`; CLI `--heston`; UI model selector, Feller note and smile
  chart. Found on the way: the predeclared limit criterion was wrong for ρ ≠ 0 (corrected and
  logged above); the Heston profile omits the vega chart because vega is not defined.
- **Longstaff–Schwartz** (ADR 0018): `engines/lsm_american.py`, `LSMConfig`, Bermudan option on
  the CRR tree (`build_tree(..., exercise_every=m)`), `validation/lsm.py` with
  `lsm_vs_bermudan_crr` and `lsm_se_calibration`. API/CLI/UI engine settings.
  Found on the way: with a polynomial-only basis, the American call with two cash dividends
  passed but used 93% of its allowance (−7.4 SE) with the in-sample estimate also below the
  reference, a basis deficiency. Adding the European value of the remaining contract as a
  regressor brought it to within about 1 SE; criteria unchanged. Computing that regressor with
  the cancellation-safe BSM kernel made pricing 10× slower (0.25 s → 2–3.7 s); a plain `ndtr`
  formula (a regressor needs no 1e-12 accuracy) restored 0.2 s with identical results.
- **UI:** chart data (profile, boundary, smile) is now fetched concurrently. The browser test
  failed once when the smile waited behind a 3.6 s Heston profile on cold workers; after the
  change it passed 3 of 3 runs on freshly started servers.
- **README:** highlights and images generated by `ui/e2e/screenshots.mjs` from synthetic inputs.

Evidence: `reports/release-c/report.md|json` — all 21 deterministic checks and 60 statistical
tests pass (81.7 s; dirty-state sha256 246c891d…, base commit da26c51, uncommitted tree).
Commands run:
- `uv run ruff format --check . && uv run ruff check . && uv run mypy` — clean (85 files)
- `uv run pytest -q` — 293 passed; `uv run --group reference pytest -q -m "statistical or reference"` — 6 passed
- `cd ui && npm run gen:types && npm run typecheck && npm test && npm run build` — 15 passed
- `node ui/e2e/smoke.mjs http://127.0.0.1:8765 <dir>` — 26 checks, 3 of 3 runs

## Calibration, hedging, duality bound and real-data import (2026-09-30)

Items built, each with its acceptance rules predeclared and hashed first (entries above):

- **Vectorised Heston pricer** (ADR 0019): `engines/heston_batch.py`, with the characteristic
  function split as C + D·v, adaptive composite Gauss–Legendre nodes, and analytic dP/dF and dP/dv.
  Checks `heston_batch_vs_quantlib`, `heston_batch_vs_adaptive_sweep`, `heston_batch_greeks_vs_fd`.
- **Heston calibration** (ADR 0020): `application/heston_calibration.py`, CLI `heston calibrate`,
  `POST /api/v1/heston/calibrations`, UI panel in the Surface tab, synthetic Heston fixture pair.
  Checks `heston_calibration_exact_recovery`, `heston_calibration_noisy_snapshot`.
- **Hedging experiment** (ADR 0021): `application/hedging.py` (GBM and Andersen QE paths; Black–
  Scholes, Heston and minimum-variance deltas), CLI `hedge`, `POST /api/v1/analysis/hedging`, UI
  Hedging tab. Checks `hedging_gbm`, `heston_qe_simulator`, `heston_min_variance_hedge`.
- **Andersen–Broadie upper bound** (ADR 0022): `dual_upper_bound` in `engines/lsm_american.py`,
  an `LSMConfig.upper_bound` option, and a price bracket in the UI. Check `lsm_dual_upper_bound`.
- **Real-data import and study** (ADR 0023): `adapters/snapshots/csv_chain.py`,
  `application/study.py`, CLI `snapshot import-csv` and `study`, `docs/real-data.md`, and
  synthetic example CSVs in both layouts.

Defects and findings on the way:
- **Fixed-node Heston batch rule failed its gate in a scratch run** (worst scaled error 35 vs
  QuantLib; e^{iux} under-resolved at large |x|). Node sets are now adaptive. The routing
  threshold was lowered before the formal check (a tightening, logged above).
- **Calibration standard errors were too small.** Quote noise alone put σ 4.0 SE from truth; with
  the true forwards every parameter was within 2 SE. The missing term, the forwards' estimation
  error, is now propagated by the delta method (max 2.5 SE). Gate unchanged.
- **The `snapshot` and `surface` CLI commands were never registered** (handlers existed and the
  README documented them, but no test ran them; git history shows no registration ever). They
  are registered now, with CLI tests for every command.
- **The CSV importer quarantined a valid timestamp** that lacked fractional seconds under a single
  strptime format. Formats now accept a list or ISO-8601.
- **Under Heston, the Heston delta hedges worse than the Black–Scholes implied-vol delta** (std
  1.48 vs 1.27); the minimum-variance delta is best (1.14). This is expected theory (spot/vol
  correlation), recorded as a result.
- **The dual-bound gap is inner-path noise** (0.28 / 0.069 / 0.004 at 125 / 500 / 2000 inner paths).
  An inner European control variate was added; it reduced the gap only modestly.
- The e2e test selected snapshots by list index; with four bundled snapshots of which two share
  a timestamp, the order is by id hash. It now selects by provenance label.

`tzdata` (the IANA database for `zoneinfo`) was added as a dependency after the report ran,
because slim container images may lack the system database the CSV importer's timezones need;
the report's lockfile hash therefore predates it.

Evidence: `reports/release-d/report.md|json`: all 30 deterministic checks and the statistical
suite pass (146 s; dirty-state sha256 946c9fdd…, base commit da26c51, uncommitted tree).
Commands run:
- `uv run ruff format --check . && uv run ruff check . && uv run mypy`: clean (98 files)
- `uv run pytest -q`: 335 passed; `uv run --group reference pytest -q -m "statistical or reference"`: 12 passed
- `cd ui && npm run gen:types && npm run typecheck && npm test && npm run build`: 15 passed
- `node ui/e2e/smoke.mjs http://127.0.0.1:8765 <dir>`: 30 checks

## First real-data run (2026-09-30)

Two consecutive days of SPXW end-of-day chains (14–15 Sep 2022) from the free HistoricalData.net
2022H2 sample (evaluation use under its license; stored under git-ignored `data/raw/`, never
committed). Added mapping-file row filters to the importer (with a test) to keep SPXW PM-settled
European contracts only, and a committed mapping, `examples/csv/mapping_historicaldata_spxw.toml`.
Study results and the maturity breakdown are in `docs/real-data.md`. Held-out containment:
SSVI 15.9%, Heston 6.9%; next day without refit 10.7% / 6.2%; Heston v0-only refit 6.1%. Both
models miss by 2–4 vol points (7–8.5 under one week) against bid–ask widths of 0.13–0.33 vol
points. No thresholds applied; results reported as measured.

## Term-structure models (2026-10-01)

Following the first real-data run, added per-expiry raw SVI slices (`application/svi_slices.py`)
and Heston with a piecewise-constant θ(t) (`HestonTSModel`; exact characteristic function, since
D does not involve θ), per ADR 0024. Calibration now goes through a `Parameterisation`, so both
Heston forms share the fitting, uncertainty and next-day code. The QE simulator accepts explicit
time grids, θ(t), and a terminal-only mode (a 200k × 365 run would otherwise need over 1 GB). CLI:
`surface fit --model svi-slices`, `heston calibrate --pillars 7,30,91,182`; the study runs all
four models.

Checks (predeclared, all passed on the first formal run): `heston_ts_reduces_to_constant`
(1.4e-14), `heston_ts_vs_monte_carlo` (6 prices), `heston_ts_exact_recovery` (relative 6e-8),
`svi_slices_synthetic` (containment 100% and 95.7%, no arbitrage violations).

Real data (SPXW, 14–15 Sep 2022): SVI slices reduce the held-out IV error from 3.37 (SSVI) to
0.52 vol points and the next-day error from 3.03 to 0.91, but are not arbitrage-free (95
butterfly, 336 in-range calendar violations). The θ term structure leaves Heston unchanged (3.99).
Full table in `docs/real-data.md`. The first four-model run took about 10 minutes, most of it
the nine-parameter Heston calibration on about 4,000 real quotes.

Evidence: `reports/release-e/report.md|json`: all 34 deterministic checks and the statistical
suite pass (145 s; policy SHA-256 c1b9783c…; uncommitted tree). `ruff`, strict `mypy` (101 files)
clean; `uv run pytest -q`: 346 passed.

## Current state and next steps

Everything since the 15 committed pieces is **uncommitted** on `build/options-workbench`.
Suggested next steps, in order:
1. Commit the new work in pieces, then regenerate the latest report so it references a clean
   commit.
2. An arbitrage-free cross-slice fit (eSSVI-type) to combine SVI-slice accuracy (0.5 vol points
   on SPX) with SSVI's no-arbitrage guarantee; jumps (Bates) for the sub-week Heston fit.
3. A PDE engine (ADR 0012) as a third American method; vega hedging with a second option in the
   hedging experiment; term-structure extensions of Heston calibration.
4. Paper trading follow-ups if wanted: marks from imported real quotes, per-lot (FIFO) tax
   reporting. Live order placement only via the broker's own paper environment first, with
   per-order confirmation (ADR 0016).
5. Optional: American Greeks vs QuantLib FD (delta/gamma fixtures), recalibrated scenarios as a
   separate operation, cancellation of running jobs if workloads grow.

Known limitations are listed in README "Limitations".
