# 0022 — Andersen–Broadie dual upper bound for Longstaff–Schwartz

Status: accepted (2026-09-30)

## Decision
- **Optional** (`LSMConfig.upper_bound`), because it needs nested simulation. Outer paths run
  forward and apply the fitted rule. At each in-the-money date, inner paths follow the rule from
  there and estimate its value C_j. With L_j = h_j where the rule exercises and C_j otherwise,
  M_k = Σ_{j<k}(L_{j+1} − C_j) is a martingale (the inner estimates are unbiased). It telescopes
  to h_k − M_k = L_0 + (h_k − L_k) + Σ_{j<k exercised}(C_j − h_j), and
  U = E[max(h_0, L̂_0 + max_k(...))] is an upper bound on the Bermudan price.
- **Only in-the-money dates get inner paths.** Out of the money, h_k − L_k = −C_k ≤ 0 is
  replaced by 0, which can only raise U, so the bound stays valid and becomes slightly
  conservative.
- **Inner control variate:** the European payoff of each inner path, whose conditional mean is
  the closed-form price, with one pooled β per date.
- **Reported:** U, its SE (outer-sample variance plus the lower estimate's), the duality gap
  U − L, and the bracket [L − z·SE_L, U + z·SE_U].
- **Work limit:** outer × inner × dates²/2 ≤ 4·10⁸ inner path-steps.

## Validation (predeclared in `[lsm.upper_bound]`)
Six cases against the CRR tree exercising on the same 25 dates: U ≥ B − 4 SE − ε_tree (valid)
and U − B ≤ 0.01 + 1%·B + 4 SE + ε_tree (tight). All pass.

## Finding
The gap is almost entirely inner-simulation noise, not a poor exercise rule. For the ITM put
(K = 110), U − B = 0.28, 0.069 and 0.004 (± 0.015) at 125, 500 and 2000 inner paths, roughly
proportional to 1/inner. At 2000 inner paths the bracket is essentially tight, so the fitted
rule is close to optimal.
