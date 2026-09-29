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

## Current state and next steps

Nothing is in progress. The project is committed in 15 pieces on branch
`build/options-workbench`; every commit passes lint, strict typing and its tests. The saved
reports predate the commits and record base commit 0848255 plus a dirty-state hash.
Suggested next steps, in order:
1. Regenerate `reports/post-review` so the reports reference a clean commit.
2. If advanced models are wanted: implement **one** of PDE or Heston (ADR 0012) with the plan's
   refinement studies and independent references (QuantLib `AnalyticHestonEngine` pinned).
3. Paper trading follow-ups if wanted: import of real quotes (broker CSV) to mark positions,
   per-lot (FIFO) tax reporting, a daily mark reminder. Live order placement only via the
   broker's own paper environment first, with per-order confirmation (ADR 0016).
4. Optional: American Greeks vs QuantLib FD (delta/gamma fixtures), a real data adapter emitting
   `options-snapshot/v1` (check provider terms first), recalibrated scenarios as a separate
   operation, cancellation of running jobs if workloads grow.
Known limitations are listed in README "Limitations".
