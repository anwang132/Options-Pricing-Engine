# Using real option-chain data

The repository contains only synthetic data. This guide shows how to run the same pipeline
(quality checks, SSVI and Heston fits, the two-day study) on option chains you export yourself.
Imported data stays in the git-ignored `data/` directory; do not commit it. Most vendor and
broker data is licensed for your own use only.

## 1. Choose the right contracts

The surface and Heston fits use **European** quotes only. Good choices:

- **SPX, XSP, NDX, RUT index options:** European, cash-settled. Use one expiry cycle and
  consider excluding PM/AM settlement variants you do not want mixed.
- **Single-stock or ETF options (AAPL, SPY, ...)** are American. They can be imported, but
  every row is excluded from the fits with the reason `american_exercise_not_european_observation`.
  Price them with the CRR or Longstaff–Schwartz engines instead.

Export two snapshots of the same chain on consecutive days, taken close to the close so quotes
are comparable.

## 2. Write a mapping file

The importer needs to know which column is which. Start from one of the examples:

- `examples/csv/mapping_long.toml`: one row per contract (symbol, expiry, strike, type, bid, ask,
  ...);
- `examples/csv/mapping_straddle.toml`: calls and puts side by side per strike (the usual
  broker "option chain" export).

Edit the column names under `[mapping]` to match your header row. Also set:

- `[formats]`: `expiry` and `quote_time` as strptime formats (or a list, or `"iso8601"`),
  `timezone` (e.g. `"America/New_York"`), and `expiry_time` for date-only expiries (SPX PM
  expiries settle on the 16:00 ET close);
- `[contract]`: `exercise_style` (required), settlement, multiplier, deliverable;
- `skip_rows` if the export has lines above the header.

## 3. Import and ingest

```bash
uv run options-engine snapshot import-csv chain_day1.csv --mapping my_mapping.toml \
    --underlying SPX --spot 5712.20 --as-of 2026-09-29T20:00:00Z \
    --spot-observed-at 2026-09-29T20:00:00Z --ingest
```

Spot and the valuation time are required and are never read from the file. `--rate` and
`--dividend-yield` are optional: forwards and discount factors are estimated per expiry from
put-call parity, and the carry inputs are only a fallback.

The command prints the snapshot id and the quality report: how many quotes were accepted and
how many were quarantined, for each reason (crossed, stale, zero bid, wide spread, unparseable,
...). Inspect a snapshot with `options-engine snapshot show <id>` or in the UI's Market data tab.

## 4. Fit and compare

```bash
uv run options-engine surface fit <day1-id> --later <day2-id>
uv run options-engine heston calibrate <day1-id> --later <day2-id>
uv run options-engine study <day1-id> <day2-id>      # writes data/studies/<day1>__<day2>/
```

The study report lists exactly the metrics declared in advance in `[real_data_study]` of
`config/validation_policy.toml`, and compares models only on held-out and next-day metrics.
In-sample fit quality is not evidence that a model is better.

## A first run: SPXW, 14–15 September 2022

Data: the free July–December 2022 sample from [HistoricalData.net](https://historicaldata.net/)
(end-of-day chains; credit required by its license for published results; the files themselves
are not in this repository). Mapping: `examples/csv/mapping_historicaldata_spxw.toml` (SPXW
only: European, PM-settled). Spot = official close (3946.01, then 3901.35: −1.1%); no carry
inputs, so forwards come from put-call parity. Quote times are blank in this archive era, so
freshness is flagged as unknown.

Ingestion: 11,492 of 12,760 quotes accepted on day 1 (quarantined: 376 same-day expiries,
865 one-sided quotes, 216 wide spreads); 11,737 of 13,086 on day 2. The fits used 31 expiries
from 1 day to 9 months, with 3,980 in-sample and 1,320 held-out quotes.

| metric (predeclared) | SSVI | Heston |
|---|---|---|
| held-out bid/ask containment | 15.9% | 6.9% |
| held-out IV RMSE (vol points) | 3.37 | 4.00 |
| day 2, no refit: containment | 10.7% | 6.2% |
| day 2, no refit: IV RMSE (vol points) | 3.03 | 3.57 |
| day 2, Heston v0 refit: containment | — | 6.1% |

Exploratory breakdown by maturity (not a predeclared metric): the median bid–ask width is only
0.13–0.33 vol points, while Heston misses by 8.5 vol points RMSE under one week, 3.6 at 8–30
days and 2.9–3.2 beyond (SSVI: 7.0, 2.2, 2.8–3.3). Neither single, constant-parameter model can
fit 31 SPX expiries to within spreads that tight. The short end is worst, which is the known
weakness of both SSVI's global parameters and Heston's rigid term structure. Unlike the
synthetic case, refitting v0 alone does not help: the structural parameters are what is wrong.
The calibrated Heston has high vol-of-vol (σ = 1.30) and violates Feller (ratio 0.44), which is
typical of equity-index fits. Next steps this suggests: fit per-expiry smiles (SVI slices) or a
term structure of Heston parameters, and weight or restrict the sub-week expiries.

### Follow-up: term-structure models (ADR 0024)

Both suggested models were added (rules predeclared, validated on synthetic data first) and the
same two days rerun:

| metric (predeclared) | SSVI | Heston | SVI slices | Heston, θ term structure |
|---|---|---|---|---|
| held-out bid/ask containment | 15.9% | 6.9% | **22.3%** | 6.7% |
| held-out IV RMSE (vol points) | 3.37 | 4.00 | **0.52** | 3.99 |
| day 2, no refit: containment | 10.7% | 6.2% | **14.7%** | 6.7% |
| day 2, no refit: IV RMSE (vol points) | 3.03 | 3.57 | **0.91** | 3.59 |
| day 2, v0 refit: containment | — | 6.1% | — | 6.6% |

- **SVI slices cut the held-out error 6.5×** (3.37 → 0.52 vol points) and fix the short end
  (0.46 vol points under one week, against 7.0 for SSVI and 8.5 for Heston). The improvement
  survives to day 2 (0.91). Containment stays modest because SPX spreads are narrower (0.13–0.33
  vol points) than even this error.
- **The price is arbitrage.** Independent slices have no guarantee, and here they cross: 95
  butterfly violations and 336 calendar violations where both expiries are quoted (1,612 on the
  wider k ∈ [−1, 1] grid). SSVI is arbitrage-free by construction but misses by 3 vol points.
  Reconciling the two needs a constrained cross-slice fit (eSSVI-type), which is the next step.
- **A θ term structure does not help Heston** (3.99 vs 4.00 vol points). The misfit is in each
  expiry's skew and curvature, which a mean-reversion level cannot change. The calibration also
  flags v0 and the first-week θ as not separable (correlation −0.96), as expected.

## What to expect

On real data, expect lower containment than on the synthetic fixtures. Wing quotes are wide
and stale; the index forward moves with rates and dividends; SSVI's global ρ, η, γ cannot fit
every expiry; and Heston's term structure is rigid. The next-day numbers show how much of a fit
survives one day. Report them as measured.
