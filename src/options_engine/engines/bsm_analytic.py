"""Black-Scholes-Merton analytical prices and Greeks.

Numerical approach (see ADR 0003):

* Work in forward terms: D = exp(-rT), F = S exp((r-q)T), x = ln(F/K),
  s = sigma*sqrt(T). The normalised price of the out-of-the-money option is
  b(x, s) = e^{x/2} N(x/s + s/2) - e^{-x/2} N(x/s - s/2),  x <= 0,
  and the price is D*sqrt(F*K)*b plus the discounted intrinsic D*max(theta(F-K), 0).
  Pricing the OTM side directly avoids losing its time value in the
  subtraction an ITM formula would perform.
* Both terms are evaluated as exp(+-x/2 + log N(.)), so tail probabilities do
  not underflow before the multiplication.
* When the two terms nearly cancel (tiny s relative to |x|, or s -> 0 near
  the money) the subtraction loses digits. We measure the cancellation
  (term1 / b) and, above ``CANCELLATION_LIMIT``, switch formula:
  - |x/s| <= 8: b = (A-B) cosh(x/2) + (A+B) sinh(x/2) with
    A - B = N(h+t) - N(h-t) integrated directly by Gauss-Legendre (vectorised);
  - |x/s| > 8 (deep tails): the positive integral
    b = (1/sqrt(2 pi)) * int_0^s exp(-x^2/(2u^2) - u^2/8) du
    (the integrand is the normalised vega, db/ds) by adaptive quadrature.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import integrate, special

from options_engine.domain.conventions import DividendTreatment, ExerciseStyle, GreekName
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.domain.numerics import AnalyticConfig
from options_engine.domain.results import EngineOutput, GreekResult, GreekStatus
from options_engine.engines.base import EngineCapabilities, PricingProblem
from options_engine.models.black_scholes import BLACK_SCHOLES

SQRT_2PI = math.sqrt(2.0 * math.pi)
CANCELLATION_LIMIT = 30.0  # fall back when more than ~1.5 digits would be lost (ADR 0003)
QUADRATURE_RTOL = 1e-13
EPS = float(np.finfo(np.float64).eps)
KINK_ULPS = 8.0

FloatArray = NDArray[np.float64]


_GL_NODES, _GL_WEIGHTS = np.polynomial.legendre.leggauss(16)
# Split formula used for |x/s| <= SPLIT_MAX_H. Its error grows like h^4 * eps
# (h^2 conditioning of the components, amplified by an h^2 cancellation), so
# beyond this the scalar quadrature is used. Chosen from a measured scan against
# the 60-digit grid fixture (ADR 0003).
SPLIT_MAX_H = 8.0


def _split_otm(x: FloatArray, s: FloatArray) -> FloatArray:
    """Vectorised b(x, s) = (A - B) cosh(x/2) + (A + B) sinh(x/2), A,B = N(h +- t).

    A - B is integrated directly (16-point Gauss-Legendre of the normal density
    over [h-t, h+t]) when the interval is short and the density varies by at
    most e^2 across it (t <= 0.5, |x| <= 2), so no subtraction occurs there.
    """
    h = x / s
    t = 0.5 * s
    a = special.ndtr(h + t)
    b = special.ndtr(h - t)
    diff = np.asarray(a - b, dtype=np.float64)
    gl = (t <= 0.5) & (np.abs(x) <= 2.0)
    if np.any(gl):
        hg, tg = h[gl], t[gl]
        # Fixed-order accumulation: each element's result is independent of batch
        # shape (a BLAS matmul may reorder the sum and break scalar/batch equality).
        acc = np.zeros_like(hg)
        for node, weight in zip(_GL_NODES, _GL_WEIGHTS, strict=True):
            u = hg + tg * node
            acc += weight * np.exp(-0.5 * u * u)
        diff[gl] = tg * acc / SQRT_2PI
    return np.asarray(diff * np.cosh(0.5 * x) + (a + b) * np.sinh(0.5 * x), dtype=np.float64)


def _quadrature_otm(x: float, s: float) -> float:
    """b(x, s) for x < 0 via the cancellation-free integral, rescaled to [0, 1]."""
    h = x / s
    t = 0.5 * s
    log_scale = -0.5 * (h * h + t * t)
    if log_scale < -745.0:  # below the smallest subnormal: the price is 0 in float64
        return 0.0
    hh = 0.5 * h * h
    tt = 0.5 * t * t

    def integrand(r: float) -> float:
        if r <= 0.0:
            return 0.0
        return math.exp(-hh * (1.0 / (r * r) - 1.0) - tt * (r * r - 1.0))

    # Mass concentrates within ~1/h^2 of r = 1 when |h| is large.
    width = min(0.5, 1.0 / max(hh, 1.0))
    points = [1.0 - width, 1.0 - 0.1 * width]
    value, _ = integrate.quad(
        integrand, 0.0, 1.0, epsabs=0.0, epsrel=QUADRATURE_RTOL, limit=400, points=points
    )
    return s * math.exp(log_scale) / SQRT_2PI * value


def normalized_otm_price(x: ArrayLike, s: ArrayLike) -> tuple[FloatArray, NDArray[np.bool_]]:
    """Normalised OTM call value b(x, s) for x <= 0, s > 0.

    Returns (b, used_fallback): whether the cancellation fallback (split formula
    or quadrature) replaced the direct formula for each element.
    """
    x_b, s_b = np.broadcast_arrays(np.asarray(x, dtype=np.float64), np.asarray(s, np.float64))
    shape = x_b.shape
    x_arr, s_arr = x_b.ravel(), s_b.ravel()
    if np.any(x_arr > 0) or np.any(s_arr <= 0):
        raise ValueError("normalized_otm_price requires x <= 0 and s > 0")
    h = x_arr / s_arr
    t = 0.5 * s_arr
    term1 = np.exp(0.5 * x_arr + special.log_ndtr(h + t))
    term2 = np.exp(-0.5 * x_arr + special.log_ndtr(h - t))
    b = term1 - term2
    atm = x_arr == 0.0
    # At the money forward, b = N(s/2) - N(-s/2) = erf(s / (2 sqrt 2)) exactly.
    b = np.where(atm, special.erf(t / math.sqrt(2.0)), b)
    fallback = (~atm) & (term1 > 0.0) & ((b <= 0.0) | (term1 > CANCELLATION_LIMIT * b))
    b = np.array(b, dtype=np.float64, copy=True)
    split = fallback & (np.abs(h) <= SPLIT_MAX_H)
    if np.any(split):
        b[split] = _split_otm(x_arr[split], s_arr[split])
    for i in np.flatnonzero(fallback & ~split):
        b[i] = _quadrature_otm(float(x_arr[i]), float(s_arr[i]))
    return b.reshape(shape), fallback.reshape(shape)


def black_scholes_price(
    sign: ArrayLike,
    spot: ArrayLike,
    strike: ArrayLike,
    time: ArrayLike,
    rate: ArrayLike,
    dividend_yield: ArrayLike,
    volatility: ArrayLike,
) -> tuple[FloatArray, NDArray[np.bool_]]:
    """Vectorised European BSM price per unit. ``sign`` is +1 call, -1 put.

    Handles T = 0 (payoff, undiscounted) and sigma = 0 (discounted forward
    intrinsic) exactly. Returns (price, used_fallback).
    """
    arrays = np.broadcast_arrays(
        *(
            np.asarray(a, dtype=np.float64)
            for a in (sign, spot, strike, time, rate, dividend_yield, volatility)
        )
    )
    shape = arrays[0].shape
    w, S, K, T, r, q, vol = (a.ravel() for a in arrays)
    if np.any(T < 0) or np.any(vol < 0) or np.any(S <= 0) or np.any(K <= 0):
        raise ValueError("invalid domain: T >= 0, sigma >= 0, S > 0, K > 0 required")
    D = np.exp(-r * T)
    F = S * np.exp((r - q) * T)
    s = vol * np.sqrt(T)
    intrinsic_fwd = np.maximum(w * (F - K), 0.0)
    price = D * intrinsic_fwd  # exact for s == 0, and equal to the payoff at T == 0
    used_fallback = np.zeros(price.shape, dtype=bool)
    regular = s > 0
    if np.any(regular):
        x = np.log(S[regular] / K[regular]) + (r[regular] - q[regular]) * T[regular]
        y = w[regular] * x  # y <= 0: this option is OTM in forward terms
        b, fallback = normalized_otm_price(-np.abs(y), s[regular])
        time_value = D[regular] * np.sqrt(F[regular]) * np.sqrt(K[regular]) * b
        price[regular] = D[regular] * intrinsic_fwd[regular] + time_value
        used_fallback[regular] = fallback
    return price.reshape(shape), used_fallback.reshape(shape)


def d1_d2(S: float, K: float, T: float, r: float, q: float, vol: float) -> tuple[float, float]:
    s = vol * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q) * T) / s + 0.5 * s
    return d1, d1 - s


def black_scholes_greeks_regular(
    sign: float, S: float, K: float, T: float, r: float, q: float, vol: float
) -> dict[GreekName, float]:
    """Analytical Greeks for T > 0 and sigma > 0 (raw units, see conventions)."""
    sqrt_t = math.sqrt(T)
    s = vol * sqrt_t
    d1, d2 = d1_d2(S, K, T, r, q, vol)
    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / SQRT_2PI
    n_d1 = float(special.ndtr(sign * d1))
    n_d2 = float(special.ndtr(sign * d2))
    decay = -S * disc_q * pdf_d1 * vol / (2.0 * sqrt_t)
    return {
        GreekName.DELTA: sign * disc_q * n_d1,
        GreekName.GAMMA: disc_q * pdf_d1 / (S * s),
        GreekName.VEGA: S * disc_q * pdf_d1 * sqrt_t,
        GreekName.THETA: decay - sign * r * K * disc_r * n_d2 + sign * q * S * disc_q * n_d1,
        GreekName.RHO: sign * K * T * disc_r * n_d2,
        GreekName.DIVIDEND_RHO: -sign * T * S * disc_q * n_d1,
    }


def _boundary_greeks(
    sign: float, S: float, K: float, T: float, r: float, q: float
) -> dict[GreekName, GreekResult]:
    """Exact Greeks when sigma = 0 or T = 0, with explicit kink handling."""
    ok, kink = GreekStatus.OK, GreekStatus.UNDEFINED_AT_KINK
    method = "analytic_limit"
    if T == 0.0:
        moneyness = sign * (S - K)
        at_kink = S == K
        itm = moneyness > 0
        return {
            GreekName.DELTA: GreekResult(GreekName.DELTA, kink, note="payoff kink at S = K")
            if at_kink
            else GreekResult(GreekName.DELTA, ok, sign if itm else 0.0, method),
            GreekName.GAMMA: GreekResult(GreekName.GAMMA, kink, note="payoff kink at S = K")
            if at_kink
            else GreekResult(GreekName.GAMMA, ok, 0.0, method),
            GreekName.VEGA: GreekResult(
                GreekName.VEGA, ok, 0.0, method, note="payoff is known at expiry"
            ),
            GreekName.THETA: GreekResult(
                GreekName.THETA,
                GreekStatus.NOT_APPLICABLE_AT_EXPIRY,
                note="calendar time cannot roll past expiry",
            ),
            GreekName.RHO: GreekResult(GreekName.RHO, ok, 0.0, method),
            GreekName.DIVIDEND_RHO: GreekResult(GreekName.DIVIDEND_RHO, ok, 0.0, method),
        }
    # sigma == 0, T > 0: price = max(sign*(S e^{-qT} - K e^{-rT}), 0)
    fwd_s = S * math.exp(-q * T)
    fwd_k = K * math.exp(-r * T)
    # Within a few ulps of the kink we cannot tell which side the exact inputs lie on.
    at_kink = abs(fwd_s - fwd_k) <= KINK_ULPS * EPS * max(fwd_s, fwd_k)
    itm = sign * (fwd_s - fwd_k) > 0
    vega_note = "right derivative at sigma = 0 (domain boundary)"
    if at_kink:
        vega = fwd_s * math.sqrt(T) / SQRT_2PI
        undefined = "forward equals strike: deterministic payoff has a kink"
        return {
            GreekName.DELTA: GreekResult(GreekName.DELTA, kink, note=undefined),
            GreekName.GAMMA: GreekResult(GreekName.GAMMA, kink, note=undefined),
            GreekName.VEGA: GreekResult(
                GreekName.VEGA, GreekStatus.ONE_SIDED, vega, method, note=vega_note
            ),
            GreekName.THETA: GreekResult(GreekName.THETA, kink, note=undefined),
            GreekName.RHO: GreekResult(GreekName.RHO, kink, note=undefined),
            GreekName.DIVIDEND_RHO: GreekResult(GreekName.DIVIDEND_RHO, kink, note=undefined),
        }
    f = 1.0 if itm else 0.0
    return {
        GreekName.DELTA: GreekResult(GreekName.DELTA, ok, f * sign * math.exp(-q * T), method),
        GreekName.GAMMA: GreekResult(GreekName.GAMMA, ok, 0.0, method),
        GreekName.VEGA: GreekResult(
            GreekName.VEGA, GreekStatus.ONE_SIDED, 0.0, method, note=vega_note
        ),
        GreekName.THETA: GreekResult(
            GreekName.THETA, ok, f * sign * (q * fwd_s - r * fwd_k), method
        ),
        GreekName.RHO: GreekResult(GreekName.RHO, ok, f * sign * T * fwd_k, method),
        GreekName.DIVIDEND_RHO: GreekResult(
            GreekName.DIVIDEND_RHO, ok, -f * sign * T * fwd_s, method
        ),
    }


def _escrow_adjust(
    greeks: dict[GreekName, GreekResult], problem: PricingProblem
) -> dict[GreekName, GreekResult]:
    """Map Greeks w.r.t. the escrowed spot S* = S - PV(divs) to Greeks w.r.t. S.

    dS*/dS = 1, dS*/dt = -r PV(divs), dS*/dr = sum D_i t_i e^{-r t_i}.
    """
    if not problem.dividends:
        return greeks
    delta = greeks.get(GreekName.DELTA)
    out = dict(greeks)
    dpv_dt = -problem.rate * problem.dividend_pv()
    dpv_dr = math.fsum(a * t * math.exp(-problem.rate * t) for t, a in problem.dividends)
    for name, shift in ((GreekName.THETA, dpv_dt), (GreekName.RHO, dpv_dr)):
        g = out.get(name)
        if g is None or g.value is None:
            continue
        if delta is None or delta.value is None:
            out[name] = GreekResult(
                name, GreekStatus.UNDEFINED_AT_KINK, note="requires delta, undefined here"
            )
        else:
            out[name] = GreekResult(
                name, g.status, g.value + delta.value * shift, g.method, note=g.note
            )
    return out


class BlackScholesAnalyticEngine:
    engine_id = "bsm_analytic"
    version = "1.0.0"
    description = "Closed-form Black-Scholes-Merton (European exercise)"
    config_type: type = AnalyticConfig
    capabilities = EngineCapabilities(
        exercise_styles=frozenset({ExerciseStyle.EUROPEAN}),
        dividend_treatments=frozenset(
            {DividendTreatment.CONTINUOUS_YIELD, DividendTreatment.ESCROWED_CASH}
        ),
        model_families=frozenset({BLACK_SCHOLES}),
        greeks=dict.fromkeys(GreekName, "analytic"),
        stochastic=False,
        batching=True,
        diagnostics=("cancellation_fallback_used", "forward", "discount_factor", "d1", "d2"),
    )

    def price(
        self, problem: PricingProblem, config: Any, greeks: tuple[GreekName, ...]
    ) -> EngineOutput:
        if problem.exercise_style is not ExerciseStyle.EUROPEAN:
            raise DomainError(ErrorCode.UNSUPPORTED_COMBINATION, "analytic BSM is European only")
        sign = float(problem.option_type.sign)
        S = problem.escrowed_spot()
        K, T, r, q, vol = (
            problem.strike,
            problem.time_to_expiry,
            problem.rate,
            problem.dividend_yield,
            problem.volatility,
        )
        price_arr, fallback = black_scholes_price(sign, S, K, T, r, q, vol)
        price = float(price_arr)
        if not math.isfinite(price):
            raise DomainError(ErrorCode.NUMERICAL_FAILURE, "non-finite analytic price")
        diagnostics: dict[str, Any] = {
            "cancellation_fallback_used": bool(fallback),
            "forward": S * math.exp((r - q) * T),
            "discount_factor": math.exp(-r * T),
            "escrowed_spot": S if problem.dividends else None,
            "dividend_pv": problem.dividend_pv() if problem.dividends else 0.0,
        }
        all_greeks: dict[GreekName, GreekResult]
        if T == 0.0 or vol == 0.0:
            all_greeks = _boundary_greeks(sign, S, K, T, r, q)
            diagnostics["regime"] = "at_expiry" if T == 0.0 else "zero_volatility"
        else:
            raw = black_scholes_greeks_regular(sign, S, K, T, r, q, vol)
            all_greeks = {g: GreekResult(g, GreekStatus.OK, v, "analytic") for g, v in raw.items()}
            d1, d2 = d1_d2(S, K, T, r, q, vol)
            diagnostics.update({"regime": "regular", "d1": d1, "d2": d2})
        all_greeks = _escrow_adjust(all_greeks, problem)
        return EngineOutput(
            price=price,
            greeks={g: all_greeks[g] for g in greeks},
            diagnostics=diagnostics,
        )
