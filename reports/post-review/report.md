# Validation report

- Generated: 2026-09-29T01:31:03+00:00 (full run)
- Overall: **PASS**
- Code: commit `0848255c56296d311328b703ef726cafa025aad4`, dirty=True, dirty-state sha256 `4e1a85a470e109e3244f47cb267be8fd224261dfb671965524a303b9b8a8c058` (tracked diff + untracked files under result-affecting paths)
- Lockfile sha256 `11e7e43e7ebd5339e1c3d57867cd8bea8db164de89eccbd06c44775aa784bc21`; policy sha256 `6e5ec2ff3d415618c5731b4050e058fbb1ee79997a9ae213edd4eb8abe834aee`
- Platform: macOS-15.6.1-arm64-arm-64bit-Mach-O; CPU Apple M4 (10 logical); Python 3.13.14, NumPy 2.5.3, SciPy 1.18.1, BLAS {'name': 'accelerate', 'version': 'unknown'}
- Thread env: {'OMP_NUM_THREADS': None, 'OPENBLAS_NUM_THREADS': None, 'MKL_NUM_THREADS': None, 'VECLIB_MAXIMUM_THREADS': None, 'NUMEXPR_NUM_THREADS': None}
- Runtime of this report: 50.5 s

## Deterministic gates

| check | result | cases | failed | excluded | max abs error | max scaled error (≤1 passes) |
|---|---|---|---|---|---|---|
| `bsm_price_vs_mpmath` | pass | 332 | 0 | 0 | 1.14e-13 | 0.00396 |
| `normalized_black_vs_mpmath` | pass | 3850 | 0 | 0 | 2.22e-16 | 0.178 |
| `bsm_greeks_vs_mpmath` | pass | 1992 | 0 | 0 | 4.55e-13 | 6.27e-07 |
| `bsm_price_vs_quantlib` | pass | 480 | 0 | 0 | 3.55e-14 | 0.000128 |
| `bsm_greeks_vs_quantlib` | pass | 2880 | 0 | 0 | 5.97e-13 | 9.42e-06 |
| `bsm_published_examples` | pass | 2 | 0 | 0 | 0.0014 | 0.28 |
| `identities_bsm_analytic` | pass | 498 | 0 | 0 | 1.42e-14 | 9.9e-05 |
| `boundary_behaviour` | pass | 8 | 0 | 0 | 1.78e-14 | 0.0158 |
| `greeks_fd_sweep` | pass | 1548 | 0 | 74 | 4.52e-08 | 0.138 |
| `implied_vol_round_trip` | pass | 332 | 0 | 0 | 1.15e-09 | 3.81e-06 |
| `implied_vol_failure_cases` | pass | 10 | 0 | 0 | 0.0 | 0.0 |
| `crr_european_vs_bsm` | pass | 664 | 0 | 0 | 0.00837 | 0.27 |
| `american_identities` | pass | 593 | 0 | 0 | 0.0 | 0.0 |
| `crr_vs_quantlib_fd` | pass | 192 | 0 | 0 | 0.00165 | 0.101 |
| `surface_calibration_gate` | pass | 8 | 0 | 0 | 0.000216 | 0.0433 |

Descriptions, tolerances, exclusions and failures are in `report.json`.

- `greeks_fd_sweep` exclusions: {'regime deep_itm_otm excluded by policy': 32, 'regime short_maturity excluded by policy': 18, 'regime low_vol excluded by policy': 24}

## Monte Carlo statistical gates

500 independent replications × 20000 payoff evaluations per case and method; master seed 918273645; family α = 0.01 split over 60 tests (α per test = 0.000167).

| case | method | coverage | region | bias z | SE ratio | ratio region | var/eval | efficiency vs plain | pass |
|---|---|---|---|---|---|---|---|---|---|
| atm_call | plain | 473/500 | (455, 491) | +0.77 | 1.084 | [0.779, 1.256] | 202 | 1× | pass |
| atm_call | antithetic | 477/500 | (455, 491) | +0.79 | 0.973 | [0.779, 1.256] | 106 | 2.41× | pass |
| atm_call | control_variate | 479/500 | (455, 491) | +0.79 | 0.925 | [0.779, 1.256] | 31.3 | 3.72× | pass |
| atm_call | antithetic_control_variate | 469/500 | (455, 491) | +1.00 | 1.042 | [0.779, 1.256] | 8.8 | 15.8× | pass |
| otm_call | plain | 465/500 | (455, 491) | -0.18 | 1.068 | [0.779, 1.256] | 33.6 | 1× | pass |
| otm_call | antithetic | 471/500 | (455, 491) | +0.35 | 1.055 | [0.779, 1.256] | 31.3 | 1.36× | pass |
| otm_call | control_variate | 466/500 | (455, 491) | -0.65 | 1.048 | [0.779, 1.256] | 20.8 | 0.929× | pass |
| otm_call | antithetic_control_variate | 475/500 | (455, 491) | -0.27 | 1.030 | [0.779, 1.256] | 3.4 | 6.72× | pass |
| itm_put | plain | 461/500 | (455, 491) | -0.63 | 1.110 | [0.779, 1.256] | 290 | 1× | pass |
| itm_put | antithetic | 474/500 | (455, 491) | -0.34 | 1.064 | [0.779, 1.256] | 15.5 | 23.1× | pass |
| itm_put | control_variate | 470/500 | (455, 491) | -0.55 | 1.000 | [0.779, 1.256] | 35.3 | 4.2× | pass |
| itm_put | antithetic_control_variate | 473/500 | (455, 491) | -2.41 | 1.048 | [0.779, 1.256] | 1.72 | 104× | pass |
| otm_put_negative_rate | plain | 474/500 | (455, 491) | +0.16 | 1.033 | [0.779, 1.256] | 150 | 1× | pass |
| otm_put_negative_rate | antithetic | 477/500 | (455, 491) | +0.79 | 0.973 | [0.779, 1.256] | 83.6 | 2.23× | pass |
| otm_put_negative_rate | control_variate | 480/500 | (455, 491) | +0.74 | 0.932 | [0.779, 1.256] | 70.6 | 1.17× | pass |
| otm_put_negative_rate | antithetic_control_variate | 473/500 | (455, 491) | +0.59 | 1.053 | [0.779, 1.256] | 19.9 | 5.49× | pass |
| deep_itm_call | plain | 469/500 | (455, 491) | +0.49 | 1.117 | [0.779, 1.256] | 454 | 1× | pass |
| deep_itm_call | antithetic | 478/500 | (455, 491) | +0.59 | 1.003 | [0.779, 1.256] | 17.4 | 32.3× | pass |
| deep_itm_call | control_variate | 473/500 | (455, 491) | +0.91 | 0.921 | [0.779, 1.256] | 0.0888 | 2.77e+03× | pass |
| deep_itm_call | antithetic_control_variate | 466/500 | (455, 491) | +1.65 | 1.089 | [0.779, 1.256] | 0.0828 | 3.94e+03× | pass |

Efficiency vs plain = (variance × runtime) of plain / (variance × runtime) of the method, i.e. the speed-up at equal accuracy, measured on this machine.

## Benchmarks

- Cold start (fresh interpreter, median of 5): import 162 ms, first price 0.12 ms. No JIT compilation is used; cold cost is module import plus first-call setup.
- BSM scalar price + 6 Greeks (engine call): median 36.4 µs (p10 36.2, p90 36.8).
- BSM vectorised batch, 1,000,000 mixed options: median 0.274 s (3.64e+06 options/s).
- CRR engine, 1000 steps, price + all Greeks (7 extra trees for bumps/odd-even): median 9.4 ms.

| CRR single tree | steps | median ms | p90 ms | peak memory KiB |
|---|---|---|---|---|
| european | 500 | 0.54 | 0.55 | 24 |
| american | 500 | 1.05 | 1.06 | 24 |
| european | 1000 | 1.18 | 1.20 | 47 |
| american | 1000 | 2.36 | 2.45 | 47 |
| european | 2000 | 2.65 | 2.66 | 94 |
| american | 2000 | 5.50 | 5.59 | 94 |
| european | 5000 | 59.73 | 59.78 | 235 |
| american | 5000 | 96.18 | 96.19 | 235 |

| MC terminal | paths | chunk | median ms | peak memory KiB |
|---|---|---|---|---|
| antithetic | 100000 | 131072 | 3.6 | 8207 |
| antithetic | 1000000 | 131072 | 30.2 | 13317 |

- Surface fit incl. ingestion (synthetic day 1, 5 restarts): median 0.268 s.
- Portfolio scenarios, 10 European + 5 American (400 steps) positions x 45 scenarios: median 0.229 s.
- Portfolio worst cases at the work limits (single run, seconds): american_2000_steps_1000_repricings 6.8, american_400_steps_10000_repricings 8.4, european_50000_repricings 2.0.

### Before/after at comparable accuracy

- **exact_terminal_vs_time_stepped** (200000 paths each; identical terminal distribution => equal sampling accuracy per path): 252-step log-Euler (exact increments) median 0.671 s → exact terminal sampling (engine) median 0.007 s; speed-up 92.0×.
- **variance_reduction_equal_accuracy** (path counts sized from an independent pilot so the 95% CI half-width is ~0.01; ATM call): plain median 0.280 s → antithetic + control variate median 0.011 s; speed-up 26.3×.
  Achieved CI half-widths: 0.0099 (plain, 7349200 paths) vs 0.0101 (319698 paths).

## Plots

![crr_convergence.svg](plots/crr_convergence.svg)
![mc_coverage.svg](plots/mc_coverage.svg)
![error_vs_runtime.svg](plots/error_vs_runtime.svg)
![fd_sweep.svg](plots/fd_sweep.svg)

## Scope and limitations

- Accuracy statements hold for the tested stress matrix and fixtures only.
- CRR error bound is a predeclared regime-aware bound, not a theorem for all inputs.
- American references (QuantLib FD) carry their own discretisation error.
- Timings are specific to the recorded machine and thread settings.
