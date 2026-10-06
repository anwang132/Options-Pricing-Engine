"""Black-Scholes implied volatility by bracketed Brent inversion (ADR 0004).

Procedure:
1. Reject non-finite/negative quotes and T = 0 (no volatility information).
2. Compare the quote with the model-free bounds under deterministic carry:
   call: D*max(F-K, 0) <= C <  D*F ;  put: D*max(K-F, 0) <= P < D*K.
   Quotes below the lower bound are arbitrage violations; quotes at the lower
   bound do not identify sigma; quotes at/above the upper bound need sigma -> inf.
3. Invert the out-of-the-money time value (quote minus discounted forward
   intrinsic), which is exactly the OTM option price by parity.
4. Grow the upper bracket geometrically up to ``sigma_cap``, then run
   ``scipy.optimize.brentq``. A root must also satisfy a price-residual test.
5. Report vega and the implied-vol uncertainty implied by the quote
   resolution; flag solutions whose uncertainty exceeds a threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from scipy import optimize

from options_engine.domain.conventions import MAX_VOLATILITY
from options_engine.engines.bsm_analytic import SQRT_2PI, black_scholes_price, d1_d2


class IVStatus(StrEnum):
    OK = "ok"
    UNSTABLE_LOW_VEGA = "unstable_low_vega"
    BELOW_LOWER_BOUND = "below_lower_bound"
    AT_LOWER_BOUND = "at_lower_bound"
    AT_OR_ABOVE_UPPER_BOUND = "at_or_above_upper_bound"
    NO_BRACKET = "no_bracket"
    MAX_ITERATIONS = "max_iterations"
    RESIDUAL_CHECK_FAILED = "residual_check_failed"
    AT_EXPIRY = "at_expiry"
    INVALID_QUOTE = "invalid_quote"


SOLVED = frozenset({IVStatus.OK, IVStatus.UNSTABLE_LOW_VEGA})

EXPLANATIONS: dict[IVStatus, str] = {
    IVStatus.OK: "Unique volatility reproduces the quote within the residual tolerance.",
    IVStatus.UNSTABLE_LOW_VEGA: (
        "A root exists, but vega is so small that the quote's resolution moves the implied "
        "volatility by more than the stability threshold. Treat the value as poorly determined."
    ),
    IVStatus.BELOW_LOWER_BOUND: (
        "The quote is below the discounted forward intrinsic value. No non-negative volatility "
        "reproduces it; under the stated carry assumptions it violates no-arbitrage bounds."
    ),
    IVStatus.AT_LOWER_BOUND: (
        "The quote equals the zero-volatility value within tolerance, so volatility is not "
        "identifiable from it."
    ),
    IVStatus.AT_OR_ABOVE_UPPER_BOUND: (
        "The quote is at or above the model's upper bound (discounted forward for calls, "
        "discounted strike for puts), which is reached only as volatility tends to infinity."
    ),
    IVStatus.NO_BRACKET: "Reproducing the quote requires volatility above the configured cap.",
    IVStatus.MAX_ITERATIONS: "The root finder did not converge within the iteration limit.",
    IVStatus.RESIDUAL_CHECK_FAILED: (
        "The solver terminated but the model price at the returned volatility does not match "
        "the quote within tolerance."
    ),
    IVStatus.AT_EXPIRY: "At expiry the price is the payoff and carries no volatility information.",
    IVStatus.INVALID_QUOTE: "The quote must be a finite, non-negative number.",
}


@dataclass(frozen=True, slots=True)
class IVConfig:
    sigma_cap: float = MAX_VOLATILITY
    initial_upper: float = 0.5
    xtol: float = 1e-15
    rtol: float = 1e-14
    maxiter: int = 200
    price_atol: float = 1e-10
    price_rtol: float = 1e-9
    # A solution is flagged unstable when resolution / vega exceeds this (1 vol point).
    max_vol_uncertainty: float = 0.01


@dataclass(frozen=True, slots=True)
class IVResult:
    status: IVStatus
    implied_vol: float | None
    quote: float
    lower_bound: float
    upper_bound: float
    price_resolution: float | None
    explanation: str
    price_residual: float | None = None
    bracket: tuple[float, float] | None = None
    iterations: int | None = None
    function_calls: int | None = None
    vega: float | None = None
    vol_uncertainty: float | None = None
    # IV implied by quote -/+ resolution; None for an end means unbounded or not identifiable.
    iv_interval: tuple[float | None, float | None] | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def quote_resolution(quote: Decimal) -> float:
    """Half a unit in the last supplied decimal place ("1.25" -> 0.005)."""
    exponent = quote.as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - finite decimals only
        raise ValueError("non-finite decimal")
    return 0.5 * 10.0**exponent


def _bounds(sign: int, F: float, K: float, D: float) -> tuple[float, float]:
    lower = D * max(sign * (F - K), 0.0)
    upper = D * F if sign > 0 else D * K
    return lower, upper


def _price(sign: int, S: float, K: float, T: float, r: float, q: float, vol: float) -> float:
    return float(black_scholes_price(sign, S, K, T, r, q, vol)[0])


def _vega(S: float, K: float, T: float, r: float, q: float, vol: float) -> float:
    if vol * math.sqrt(T) == 0.0:
        return 0.0
    d1, _ = d1_d2(S, K, T, r, q, vol)
    return S * math.exp(-q * T) * math.exp(-0.5 * d1 * d1) / SQRT_2PI * math.sqrt(T)


@dataclass(frozen=True, slots=True)
class _Solve:
    status: IVStatus
    vol: float | None = None
    bracket: tuple[float, float] | None = None
    iterations: int | None = None
    calls: int | None = None


def _solve(
    sign: int, S: float, K: float, T: float, r: float, q: float, target: float, cfg: IVConfig
) -> _Solve:
    """Find sigma with BSM(sigma) = target, assuming lower < target < upper."""

    def f(vol: float) -> float:
        return _price(sign, S, K, T, r, q, vol) - target

    lo, hi = 0.0, min(cfg.initial_upper, cfg.sigma_cap)
    while f(hi) < 0.0:
        if hi >= cfg.sigma_cap:
            return _Solve(IVStatus.NO_BRACKET, bracket=(0.0, cfg.sigma_cap))
        lo, hi = hi, min(2.0 * hi, cfg.sigma_cap)
    try:
        root, info = optimize.brentq(
            f,
            lo,
            hi,
            xtol=cfg.xtol,
            rtol=cfg.rtol,
            maxiter=cfg.maxiter,
            full_output=True,
            disp=False,
        )
    except ValueError:  # pragma: no cover - bracket verified above
        return _Solve(IVStatus.NO_BRACKET, bracket=(lo, hi))
    if not info.converged:
        return _Solve(IVStatus.MAX_ITERATIONS, bracket=(lo, hi), iterations=info.iterations)
    return _Solve(IVStatus.OK, float(root), (lo, hi), info.iterations, info.function_calls)


def implied_volatility(
    quote: float,
    sign: int,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    price_resolution: float | None = None,
    config: IVConfig | None = None,
) -> IVResult:
    """Invert a European BSM quote. ``S`` must already be net of escrowed dividends."""
    cfg = config or IVConfig()
    D = math.exp(-r * T)
    F = S * math.exp((r - q) * T)
    lower, upper = _bounds(sign, F, K, D)

    def result(status: IVStatus, **kw: object) -> IVResult:
        return IVResult(
            status=status,
            quote=quote,
            lower_bound=lower,
            upper_bound=upper,
            price_resolution=price_resolution,
            explanation=EXPLANATIONS[status],
            **kw,  # type: ignore[arg-type]
        )

    if not math.isfinite(quote) or quote < 0:
        return result(IVStatus.INVALID_QUOTE, implied_vol=None)
    if T <= 0.0:
        return result(IVStatus.AT_EXPIRY, implied_vol=None)
    bound_tol = cfg.price_atol + cfg.price_rtol * max(quote, upper)
    if quote < lower - bound_tol:
        return result(IVStatus.BELOW_LOWER_BOUND, implied_vol=None)
    if quote <= lower + bound_tol:
        return result(IVStatus.AT_LOWER_BOUND, implied_vol=None)
    if quote >= upper - bound_tol:
        return result(IVStatus.AT_OR_ABOVE_UPPER_BOUND, implied_vol=None)

    # Invert the OTM time value: identical root, better-conditioned target.
    otm_sign = -sign if lower > 0.0 else sign
    target = quote - lower
    solved = _solve(otm_sign, S, K, T, r, q, target, cfg)
    if solved.status is not IVStatus.OK or solved.vol is None:
        return result(
            solved.status, implied_vol=None, bracket=solved.bracket, iterations=solved.iterations
        )
    vol = solved.vol
    residual = _price(sign, S, K, T, r, q, vol) - quote
    common: dict[str, object] = {
        "price_residual": residual,
        "bracket": solved.bracket,
        "iterations": solved.iterations,
        "function_calls": solved.calls,
    }
    if abs(residual) > cfg.price_atol + cfg.price_rtol * quote:
        return result(IVStatus.RESIDUAL_CHECK_FAILED, implied_vol=None, **common)
    vega = _vega(S, K, T, r, q, vol)
    common["vega"] = vega
    status = IVStatus.OK
    interval: tuple[float | None, float | None] | None = None
    notes: list[str] = []
    if price_resolution is not None and price_resolution > 0:
        uncertainty = price_resolution / vega if vega > 0 else math.inf
        common["vol_uncertainty"] = uncertainty
        if uncertainty > cfg.max_vol_uncertainty:
            status = IVStatus.UNSTABLE_LOW_VEGA
        interval = (
            _interval_end(quote - price_resolution, sign, S, K, T, r, q, lower, upper, cfg),
            _interval_end(quote + price_resolution, sign, S, K, T, r, q, lower, upper, cfg),
        )
        if interval[0] == 0.0:
            notes.append("quote minus resolution reaches the zero-volatility bound")
        if interval[1] is None:
            notes.append("quote plus resolution is not attainable below the volatility cap")
    return result(status, implied_vol=vol, iv_interval=interval, notes=tuple(notes), **common)


def _interval_end(
    target: float,
    sign: int,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    lower: float,
    upper: float,
    cfg: IVConfig,
) -> float | None:
    if target <= lower:
        return 0.0
    if target >= upper:
        return None
    otm_sign = -sign if lower > 0.0 else sign
    solved = _solve(otm_sign, S, K, T, r, q, target - lower, cfg)
    return solved.vol if solved.status is IVStatus.OK else None
