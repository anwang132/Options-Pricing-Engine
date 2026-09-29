# 0010 — Validation layers, references and tiers

Status: accepted

## Decision
Independent evidence layers, none derived from the implementation under test:
1. **Identities** with explicit assumptions (parity, bounds, American ≥ European, homogeneity,
   monotonicity/convexity) — unit and property (Hypothesis) tests.
2. **High-precision fixtures:** an mpmath (60-digit) BSM written separately in
   `scripts/generate_reference_fixtures.py`; Greeks are mpmath numerical derivatives of that
   price, not the analytic formulas. Plus a dense normalised-price grid.
3. **Pinned reference library:** QuantLib 1.43 (AnalyticEuropean, AnalyticDividendEuropean,
   FD American with escrowed dividends). Fixtures record library versions, generator hash and
   policy hash; a `reference`-marked test regenerates them to detect drift.
4. **Cross-engine convergence and statistics:** CRR vs BSM with a regime-aware bound
   k·S·σ·√T/N (k = 0.5); MC coverage/bias/SE calibration over independent replications.
5. **Fault injection:** mutants (discount sign, call/put swap, dividend carry errors, vol in
   percent, ACT/360, vega/theta unit or sign errors, probability clipping, naive antithetic SE)
   must fail the checks.

Tolerances and statistical rules live in `config/validation_policy.toml`, written before the
first run; amendments are logged with hashes in `docs/progress.md`.

Tiers: deterministic tests on every PR (offline); statistical, live-reference and the full
report on a slow/nightly tier.
