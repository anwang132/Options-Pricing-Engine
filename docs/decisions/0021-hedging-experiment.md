# 0021 — Discrete delta-hedging experiment

Status: accepted (2026-09-30)

## Decision
- **Set-up:** sell one European option at its model price and hedge in the underlying at N
  equally spaced times. Dividends are reinvested and cash earns r; all P&L is discounted to
  t = 0:
  P&L = V0 − e^{−rT}·payoff + Σ Δ_i (e^{−r t_{i+1}} S_{i+1} e^{qΔt} − e^{−r t_i} S_i).
- **Worlds:** GBM sampled exactly; Heston by Andersen's QE scheme (ψ_c = 1.5) with his central
  log-S discretisation, 200 steps per year, no martingale correction (its bias is measured).
- **Common random numbers:** paths are simulated once on the finest grid, and coarser N use
  subsets of the same points, so the curve of std against N is not dominated by independent
  noise.
- **Strategies:** Black–Scholes delta at a fixed hedge vol (default: the model's own implied
  vol); the Heston delta ∂C/∂S at the path's current variance; the minimum-variance delta
  ∂C/∂S + (ρσ/S)·∂C/∂v. The Heston Greeks come from the vectorised pricer (ADR 0019).
- **Theory on the same paths:** the leading-order hedging-error std √(½σ⁴Δt² Σ E[e^{−2rt}Γ²S⁴])
  and, for a wrong hedge vol, the Gamma identity Σ e^{−rt}·½Γ(σ_h)S²(σ_h² − σ_r²)Δt.

## Validation (predeclared in `[hedging]`)
GBM: slope of log std vs log N in [−0.55, −0.45]; std / leading-order prediction in [0.9, 1.1]
at N = 256; mean P&L with a wrong hedge vol equals the Gamma identity within 4 SE + 0.01.
QE simulator: discounted spot is a martingale and call prices match Fourier, for a Feller-
satisfying and a Feller-violating set. Heston: minimum-variance delta beats the Heston delta
(paired z ≥ 3).

## Result worth stating
Under Heston with ρ = −0.7, the *Heston delta* hedges worse than the Black–Scholes delta at the
implied vol (std 1.48 vs 1.27 at N = 64), and the minimum-variance delta is best (1.14). The
partial derivative ∂C/∂S holds variance fixed, but with negative ρ variance rises when spot
falls, and the implied-vol delta is closer to the minimum-variance delta. Delta hedging leaves
the volatility risk; only a second option (vega hedge) would remove it.
