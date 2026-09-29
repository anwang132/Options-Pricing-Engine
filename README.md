# Options Pricing & Model-Validation Workbench

A reproducible workbench for pricing vanilla options and — more importantly — for showing how
far each number can be trusted. Every result carries its assumptions, numerical method,
per-Greek status, uncertainty and a hash that lets it be replayed.

Status: **Release A and Release B complete** (gates and evidence in `docs/progress.md`).
PDE and Heston are deferred (ADR 0012).

## Quick start (offline, no credentials)

Requirements: [uv](https://docs.astral.sh/uv/) ≥ 0.11, Node ≥ 20 (only to build the UI).

```bash
uv sync                                   # Python 3.13 env from uv.lock
uv run options-engine demo                # both releases, incl. six meaningful failure cases
uv run pytest -q                          # deterministic tier (~15 s, offline)
uv run options-engine validate --out reports/local   # full validation report (~60 s)

cd ui && npm ci && npm run build && cd .. # build the web UI once
uv run options-engine serve               # API + UI on http://127.0.0.1:8000
```

In the UI, price a contract to see its **value profile** (today, mid-life, near expiry, payoff),
**Greek profiles** across spot and, for American contracts, the **early-exercise boundary**.
Then open **Market data → Load bundled synthetic snapshots** and **Surface → Fit surface** for the
**implied-vol heat map**, **ATM term structure** and **risk-neutral density**, and **Portfolio**
for surface-driven scenario P&L. **Paper trading** records trades you make (or would make) and
tracks their P&L against market marks and the model over time; no real money moves.

Slow tier: `uv run --group reference pytest -m "statistical or reference"` (Monte Carlo
replications and live QuantLib/mpmath fixture reproduction).
Frontend dev: `uv run options-engine serve` and `cd ui && npm run dev` (Vite proxies `/api`).
Browser smoke test: `node ui/e2e/smoke.mjs http://127.0.0.1:8000 <screenshot-dir>` (needs
Google Chrome installed; uses `playwright-core`, no browser download).

## Deployment

One container serves the API and the built UI:

```bash
docker build -t options-workbench .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" options-workbench
```

Runtime settings: `OPTIONS_ENGINE_WORKERS` (process-pool size, default 2),
`OPTIONS_ENGINE_MAX_IN_FLIGHT` (admission limit, default 8), `OPTIONS_ENGINE_TIMEOUT_SECONDS`
(client wait, default 30), `OPTIONS_ENGINE_DATA_DIR` (snapshots, fits and paper
ledgers, default `./data`, git-ignored).
Health: `GET /api/v1/health`; counters: `GET /api/v1/metrics`. Verified locally: image builds,
container serves health, pricing and UI as a non-root user.

## CLI

```bash
uv run options-engine price --input examples/price_hull_call.json
uv run options-engine price --as-of 2026-09-28T20:00:00Z --expiry 2027-03-30T08:00:00Z \
    --type put --spot 42 --strike 40 --rate 0.10 --vol 0.20 --engine crr_tree --steps 2000 \
    --manifest run.json
uv run options-engine replay run.json          # recompute; exit 0 only if bit-identical
uv run options-engine compare --input examples/compare_otm_put.json
uv run options-engine iv --input examples/iv_quotes.json
uv run options-engine engines                  # capability matrix
uv run options-engine snapshot ingest fixtures/snapshots/synthetic_day1.json
uv run options-engine snapshot list
uv run options-engine surface fit <snapshot-id> --later <later-snapshot-id>
```

## Supported scope

| | Closed-form BSM | CRR tree | Terminal MC |
|---|---|---|---|
| Exercise | European | European, American | European |
| Dividends | continuous yield; escrowed cash | continuous yield; escrowed cash | continuous yield; escrowed cash |
| σ = 0 / at expiry | yes (exact limits) | rejected | rejected |
| Greeks | all six, analytic | Δ, Γ, Θ from nodes; vega, ρ, ρ_q by bump | Δ, vega, ρ pathwise with SE; Γ, Θ, ρ_q not supported |
| Uncertainty | — | odd/even spread (heuristic) | SE + CI from independent observations |

- **Implied volatility:** European BSM; bracketed Brent with bound checks, residual test and a
  quote-resolution stability check; bid and ask inverted separately. American quotes rejected.
- **Market snapshots:** documented JSON format, immutable content-addressed store, 15 quarantine
  reasons and 2 flags, counts per reason; unknown metadata stays unknown.
- **Surface:** SSVI (power-law φ) fitted in price space to European quotes with parity-implied
  forwards, held-out strikes, optional later-snapshot evaluation, immutable artifacts
  (including failed fits). Static-arbitrage freedom is *guaranteed for the parametric surface*
  under stated conditions and *checked on a sampled grid* separately.
- **Visualisations** (all numbers computed by the engines, never in the browser): value and
  Greek profiles against spot; American early-exercise boundary from the CRR tree; implied-vol
  heat map, ATM term structure and risk-neutral density of a fitted surface; convergence, CI,
  smile and P&L heat-map views. Checked against independent routes: the closed-form density
  matches Breeden–Litzenberger differentiation of Black prices, integrates to 1 with E[F_T/F] = 1,
  and the exercise boundary approaches the perpetual-put limit K·2r/(2r+σ²).
- **Paper trading** (no real money moves; not investment advice): accounts with starting cash,
  trade entry, exact-decimal average-cost accounting with fees, marks to market prices and/or
  the model (model-vs-market gap recorded), expiry settlement, P&L history chart, and an
  append-only hash-chained audit log with visible voids. See ADR 0016.
- **Portfolio:** signed positions, one underlying and currency, full repricing over spot/vol/
  rate/time grids, sticky-strike or sticky-moneyness (from a fit), expiry settlement, Greek
  attribution with an unexplained residual.

Conventions (ACT/365F from timestamps, decimal annual rates, per-unit prices, Greek units):
`docs/conventions.md`.

## Measured results

From `reports/release-b/report.md` (Apple M4, NumPy 2.5.3, SciPy 1.18.1) unless noted.

- Closed-form price vs an independent 60-digit mpmath implementation over a 332-case stress
  matrix: max abs error 1.1e-13; 3,850-point normalised-price grid: max relative error 1.8e-12.
- Analytic Greeks vs high-precision numerical derivatives: max abs error 4.6e-13; vs QuantLib
  1.43: 6.0e-13.
- CRR (2000/2001 steps) within the predeclared bound k·S·σ·√T/N on all 664 stress runs; CRR
  American (4000 steps) vs QuantLib FD within 1.7e-3 on 192 cases, including cash dividends.
- Monte Carlo: all 60 coverage / bias / SE-calibration tests pass (500 replications each).
- Surface on synthetic snapshots with a known generating surface: held-out bid/ask containment
  100% (25 quotes each), max IV error vs truth 2.2e-4 (gate 5e-3), no sampled arbitrage
  violations. With the surface held fixed, the next-day snapshot (1 vol point lower, spot +1%)
  is priced inside bid/ask for 0% of quotes — the expected sign that in-sample fit says little
  about the next day.
- Performance, before/after at comparable accuracy:
  - Vectorised cancellation fallback in the BSM kernel: 1 M mixed options 1.52 s
    (`reports/release-a`) → 0.30 s, accuracy checks unchanged.
  - Antithetic + control variate vs plain MC at equal 95% CI half-width (≈0.01): 29× faster in
    both saved reports.
  - Exact terminal sampling vs a 252-step simulation, same accuracy per path: 27× and 58× in the
    two saved reports (the time-stepped baseline itself varied between runs).
- BSM price + 6 Greeks: 36–38 µs per call (median). Portfolio worst cases at the work limits:
  6.8–8.4 s (`reports/post-review`, after the code-review changes; 10.5–14.0 s before).

These describe the tested matrix, synthetic data and one machine; they are not general accuracy,
speed or real-market reliability claims.

## Documentation

- `docs/architecture.md` — components, dependency boundaries, data flow
- `docs/conventions.md` — definitions and units
- `docs/validation.md` — references, stress matrix, tolerances, statistical methods, Release B evidence
- `docs/decisions/` — 16 decision records
- `docs/progress.md` — gates passed, commands run, defects found, next steps

## Limitations

Flat-volatility GBM pricing engines (the surface feeds per-position volatilities, not a local-vol
model); escrowed dividend model (not spot-jump); no settlement lag; tree Greeks by bumping are
noisier than analytic ones; worker timeouts do not kill computations (bounded by measured work
limits instead); snapshot data in this repository is synthetic; no live data adapter.

## Credits

References: QuantLib 1.43 and mpmath 1.4.1 (pinned, used to generate committed fixtures); Hull's
BSM worked example; Gatheral & Jacquier, "Arbitrage-free SVI volatility surfaces" (2014,
arXiv:1204.0646).
