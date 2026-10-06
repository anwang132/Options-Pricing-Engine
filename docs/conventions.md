# Mathematical conventions and units

All definitions below are implemented in `src/options_engine/domain/conventions.py` and the
engines; ADR 0002 records the reasoning.

## Inputs

| Quantity | Symbol | Unit / convention |
|---|---|---|
| Valuation time | t₀ | timezone-aware timestamp (`as_of`), never the wall clock |
| Expiry | T_exp | timezone-aware exercise/expiration timestamp |
| Time to expiry | T | (T_exp − t₀) in seconds / (365·86400) — ACT/365F |
| Spot | S | currency per unit of underlying |
| Strike | K | currency per unit of underlying |
| Rate | r | continuously compounded, decimal per year (0.05 = 5%); may be negative |
| Dividend yield | q | continuous, decimal per year; may be negative |
| Cash dividend | Dᵢ at tᵢ | currency per unit, ex-date tᵢ (also used for discounting) |
| Volatility | σ | decimal per year (0.2 = 20%), model parameter, σ ≥ 0 |
| Multiplier | m | units of underlying per contract (instrument metadata) |
| Heston initial / long-run variance | v₀, θ | decimal² per year (0.04 = 20% vol); UI takes √v₀, √θ in % |
| Heston mean reversion | κ | per year; half-life ln 2/κ |
| Heston vol of variance | σ (Heston) | per √year; σ = 0 is deterministic variance |
| Heston correlation | ρ | spot/variance shocks, −1 < ρ < 1 |

Derived: D = e^{−rT}, F = S·e^{(r−q)T} (with cash dividends S is replaced by
S* = S − Σ Dᵢe^{−r tᵢ} over ex-dates in (t₀, T_exp]).

## Prices

Per unit of underlying, unrounded float64. One long contract = price × m. Signed positions
(Release B) = quantity × m × price.

European BSM: C = D[F·N(d₁) − K·N(d₂)], P = D[K·N(−d₂) − F·N(−d₁)],
d₁ = ln(F/K)/(σ√T) + σ√T/2, d₂ = d₁ − σ√T.

Limits: T = 0 → max(±(S−K), 0) (undiscounted); σ = 0, T > 0 → D·max(±(F−K), 0).

Heston European (ADR 0017): C = D(F − √(FK)/π ∫₀^∞ Re[e^{iux}φ(u − i/2)]/(u² + 1/4) du),
x = ln(F/K), φ the characteristic function of ln(S_T/F). With σ = 0 it is BSM with volatility
√(I(T)/T), I(T) = θT + (v₀ − θ)(1 − e^{−κT})/κ.

Hedging experiment (ADR 0021): short one European option, hedged at N equal intervals; P&L
discounted to t = 0 is V0 − e^{−rT}·payoff + Σ Δ_i (e^{−r t_{i+1}} S_{i+1} e^{qΔt} − e^{−r t_i} S_i)
(dividends reinvested in the stock over each interval). The minimum-variance delta is
∂C/∂S + (ρσ/S)·∂C/∂v.

Longstaff–Schwartz (ADR 0018) prices American exercise as Bermudan on M equally spaced dates
plus t = 0; its estimate is low-biased for that Bermudan price and carries a standard error.

## Identities (valid only under their assumptions)

European exercise, deterministic continuous carry (and escrowed dividends with S*):
- C − P = D(F − K)
- D·max(F−K, 0) ≤ C ≤ D·F;  D·max(K−F, 0) ≤ P ≤ D·K

American exercise (same tree): American ≥ European and ≥ intrinsic. American call = European
call only when q = 0, no cash dividends and r ≥ 0 — not assumed otherwise.

## Greeks

| Greek | Raw definition and unit | Display |
|---|---|---|
| delta | ∂V/∂S (per 1.0 spot) | same |
| gamma | ∂²V/∂S² (delta per 1.0 spot) | same |
| vega | ∂V/∂σ per 1.00 absolute volatility | × 0.01 → per vol point |
| theta | ∂V/∂t₀ per year of calendar time, S, σ, r, q held fixed (= −∂V/∂T); dividend dates roll with t₀ | ÷ 365 → per calendar day |
| rho | ∂V/∂r per 1.00 | × 0.01 → per percentage point |
| dividend rho | ∂V/∂q per 1.00 | × 0.01 → per percentage point |

Statuses: `ok`, `one_sided` (vega at σ = 0), `undefined_at_kink` (payoff kink within 8 ulps),
`not_applicable_at_expiry` (theta at T = 0), `not_supported` (no valid estimator in that
engine, e.g. MC gamma), `failed`. A missing value is never reported as 0.

Methods: analytic (closed form), tree_nodes (CRR levels 1–2), central_bump_reprice (CRR),
pathwise (MC, with standard error).

## Implied volatility

Status set and procedure: ADR 0004. Quote resolution defaults to half a unit in the last
supplied decimal place; `unstable_low_vega` when resolution/vega > 0.01.

## Uncertainty vocabulary

- **Sampling uncertainty** (MC): standard error from independent observations and a confidence
  interval. Not a bound on bias.
- **Discretisation error** (tree): deterministic; odd/even spread is a heuristic indicator.
- **Model limitation:** e.g. flat volatility, escrowed dividends. Not quantified by either.

## Snapshots and quality (ADR 0013)

Quotes are kept as decimal text. A quote is *accepted* when it has no quarantine reason; flags
(`quote_time_unknown`, `missing_size`) never exclude a quote but are reported. Unknown fields stay
`null`.

## Volatility surface (ADR 0015)

k = ln(K/F) with F the forward for that expiry (put-call-parity implied, else snapshot carry);
w(k, t) = σ²t is total implied variance; the surface is SSVI with power-law φ. Model prices use
the Black formula on (F, K, D, σ = √(w/t)).

## Portfolio (ADR 0014)

Value = Σ quantity × multiplier × price. Aggregated Greeks are in position units (delta in units
of underlying; also delta notional = Δ·S; vega per vol point; theta per calendar day; rho per 1%).
Shocks: spot %, vol points (absolute), rate bp, calendar days. P&L approximation:
ΔV ≈ Δ·dS + ½Γ·dS² + vega·dσ + Θ·dt + ρ·dr; "unexplained" = full − approximation.
