# Architecture

## Components and dependency direction

```
ui/ (React + TypeScript)            -- HTTP JSON only; never computes prices
   │
interfaces/  cli.py, http/app.py, http/worker.py, handlers.py, schemas.py (pydantic v1 schemas)
   │         CLI and HTTP call the same handlers; schemas are the only serialisation layer
application/ pricing.py (validate, capability check, resolve inputs, result metadata)
   │         analysis.py (compare, convergence, IV service), hashing.py
   │         snapshots.py (parse → quality assessment → immutable store), portfolio.py
   │         (positions, scenario grid, full repricing, attribution), surface.py (SSVI fit,
   │         diagnostics, fit artifacts), svi_slices.py (per-expiry SVI + arbitrage diagnostics),
   │         heston_calibration.py (Heston and θ-term-structure Heston fit, uncertainty,
   │         next-day evaluation), hedging.py (GBM/QE paths, hedging P&L), study.py (two-day
   │         SSVI vs Heston study)
   ├── analytics/  implied_vol.py
   ├── engines/    base.py (PricingProblem, EngineCapabilities, PricingEngine protocol)
   │               bsm_analytic.py, crr.py, mc_terminal.py, heston_fourier.py,
   │               heston_batch.py (vectorised Heston + Greeks), lsm_american.py, registry.py
   ├── models/     black_scholes.py, heston.py (model specifications), ssvi.py (surface +
   │               constraints)
   └── domain/     contracts, market, quotes (quote + quality policy), conventions,
                   numerics (configs + work limits), results (Greek statuses), errors
adapters/    environment.py (git/platform capture), snapshots/format_v1.py (file format),
             snapshots/store.py (immutable content-addressed store), snapshots/synthetic.py
             (SSVI- and Heston-generated fixtures), snapshots/csv_chain.py (mapped CSV import)
validation/  policy.py, checks.py, lsm.py, heston_batch.py, calibration.py, hedging.py,
             term_structure.py,
             statistical.py, benchmarks.py, report.py
```

Rules (checked by reading imports; `domain/`, `models/`, `engines/`, `analytics/` import no
FastAPI, pydantic, pandas or plotting code):

- **Domain** objects are frozen dataclasses validated on construction. Market inputs are
  `Decimal`; the application converts to float64 once (`resolve_inputs`).
- **Engines** receive a `PricingProblem` (plain floats) and a config dataclass; they return
  `EngineOutput` (price, per-Greek results, diagnostics, optional sampling uncertainty).
- **Two entry points into pricing:** `PricingService.price` returns a fully described
  `PricingResult` (request hash, versions, assumptions); `PricingService.evaluate` runs the same
  validation and capability checks but returns only the engine output, for bulk repricing.
- **Capabilities** are data on each engine. `PricingService.check_supported` rejects unsupported
  combinations before any work and names the engines that would support the request.
- **Model families** are data too: an engine declares the families it prices (`black_scholes`,
  `heston`), and a Heston request can only reach the Heston engine. The request schema accepts
  either model (`{"volatility": ...}` or `{"family": "heston", ...}`) and picks the default
  engine by family when none is given.
- **Registry** is an explicit list (`engines/registry.py`). Adding an engine means: implement the
  protocol, declare capabilities and config type, register it, and pass the shared checks.
- **Validation** code takes the implementation under test as a callable, so the same checks run
  in pytest, in the report and against fault-injected mutants.

## Request flow (price)

1. HTTP body → `PriceRequestIn` (schema validation, 422 on failure).
2. Worker process: schema → domain objects (domain validation) → `resolve_inputs` (T from
   timestamps, dividend filtering, float conversion, domain limits).
3. Capability check → engine → `EngineOutput`.
4. `PricingResult` with request hash, engine version, assumptions → `PriceResponse`.

## Data flow (Release B)

```
snapshot file ──parse──▶ quotes (+ row-level issues) ──assess(policy)──▶ accepted / quarantined
      │                                                                    │
      └── raw bytes, normalized.json, manifest (hashes, counts) ──▶ data/snapshots/<snap-id>/
data/snapshots/<id> ──eligible European quotes──▶ parity forwards ──▶ SSVI fit ──▶ data/fits/<fit-id>/
positions + market (+ fit for vols) ──▶ PortfolioService ──PricingService per position/scenario──▶ P&L grid
```

`OPTIONS_ENGINE_DATA_DIR` (default `./data`, git-ignored) holds snapshots, surface fits, Heston
calibrations (`heston_fits/`), two-day studies (`studies/`) and paper-trading ledgers. Snapshots and fits are written once and never overwritten; ledgers are append-only,
hash-chained JSON-lines files (`adapters/paper_store.py`, `application/paper.py`, ADR 0016).

## Runtime

Single process serving API + static UI (`options-engine serve`), CPU work in a bounded process
pool (ADR 0009). Container: `Dockerfile` (multi-stage: Node build of the UI, then uv-installed
Python runtime, non-root user).
