"""Vectorised Heston European pricing on a fixed quadrature (ADR 0019).

Used where thousands of prices are needed at once (calibration, hedging
simulations); single contracts keep using the adaptive pricer in
``heston_fourier``. The same Lewis integral is evaluated on a composite
Gauss-Legendre rule built per maturity and group of rows: dyadic segments,
split so that the oscillating factor e^{iux} turns through at most 12 radians
per 32-node segment, and truncated where the characteristic function has
decayed below 1e-16. The characteristic function is split as
log phi = C(u, tau) + D(u, tau) * v, so one evaluation of C and D on the nodes
serves every strike and every variance state at that maturity.

Returned alongside the price (call or put):
* dP/dF, the derivative with respect to the forward (delta = dP/dF * F/S);
* dP/dv, the derivative with respect to the current variance.
Both come from differentiating the integrand, not from bumping.

Routing: the fixed rule is accurate when enough variance remains
(I(tau; v) >= ``min_total_variance``) and the strike is not far in the tails
(|ln(F/K)| <= ``max_abs_x_in_sd`` * sqrt(I)). Elsewhere the price is taken from
the deterministic-variance limit, BSM at sqrt(I/tau), whose error in that
region is measured and reported by the validation suite (not claimed exact).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
from scipy import special

from options_engine.engines.heston_fourier import characteristic_coefficients
from options_engine.models.heston import HestonModel, HestonTSModel

NODES_PER_SEGMENT = 32
MAX_PHASE_PER_SEGMENT = 12.0  # radians of e^{iux} per 32-node segment (about 2 oscillations)
TOP = 65536.0  # hard upper limit of the integration range
TAIL_TOLERANCE = 1e-16  # |phi(u - i/2)| / (u^2 + 1/4) below this ends the range
MIN_TOTAL_VARIANCE = 1e-5
MAX_ABS_X_IN_SD = 10.0
CHUNK = 2048  # rows per block: bounds memory at CHUNK * nodes complex values

_GL_X, _GL_W = np.polynomial.legendre.leggauss(NODES_PER_SEGMENT)
_PROBE = np.concatenate([[0.0], np.geomspace(0.25, TOP, 400)])


def node_set(
    tau: float, m: HestonModel | HestonTSModel, v_min: float, max_abs_x: float
) -> tuple[np.ndarray, np.ndarray]:
    """Composite Gauss-Legendre nodes and weights for one maturity and group of rows.

    The range ends where the integrand bound |phi|/(u^2 + 1/4) at the slowest-decaying
    variance (v_min) stays below TAIL_TOLERANCE; segments are dyadic, split further so
    that the phase |x| * length of each stays below MAX_PHASE_PER_SEGMENT.
    """
    c, d = characteristic_coefficients(_PROBE - 0.5j, tau, m)
    bound = np.exp((c + d * v_min).real) / (_PROBE * _PROBE + 0.25)
    above = np.flatnonzero(bound >= TAIL_TOLERANCE)
    u_end = TOP if above.size == 0 or above[-1] + 1 >= _PROBE.size else _PROBE[above[-1] + 1]
    edges = [0.0, 0.5]
    while edges[-1] < u_end:
        edges.append(min(2.0 * max(edges[-1], 0.5), u_end))
    us, ws = [], []
    for a, b in itertools.pairwise(edges):
        pieces = max(1, math.ceil((b - a) * max_abs_x / MAX_PHASE_PER_SEGMENT))
        for lo, hi in itertools.pairwise(np.linspace(a, b, pieces + 1)):
            us.append(0.5 * (hi - lo) * _GL_X + 0.5 * (hi + lo))
            ws.append(0.5 * (hi - lo) * _GL_W)
    return np.concatenate(us), np.concatenate(ws)


@dataclass(frozen=True, slots=True)
class BatchResult:
    price: np.ndarray
    d_forward: np.ndarray  # dP/dF
    d_variance: np.ndarray  # dP/dv
    fourier: np.ndarray  # bool: True where the Fourier rule was used


def _bsm_limit(
    sign: np.ndarray,
    F: np.ndarray,
    K: np.ndarray,
    D: float,
    tau: float,
    total_var: np.ndarray,
    weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Black price at total variance I and its F and v derivatives (dI/dv = weight)."""
    sd = np.sqrt(np.maximum(total_var, 1e-300))
    d1 = np.log(F / K) / sd + 0.5 * sd
    d2 = d1 - sd
    price = D * sign * (F * special.ndtr(sign * d1) - K * special.ndtr(sign * d2))
    d_forward = D * sign * special.ndtr(sign * d1)
    # dP/dI = D F n(d1) / (2 sqrt(I)); dI/dv = weight
    d_variance = D * F * np.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (2 * sd) * weight
    zero = total_var <= 0.0
    if np.any(zero):
        intrinsic = np.maximum(sign * (F - K), 0.0)
        price = np.where(zero, D * intrinsic, price)
        d_forward = np.where(zero, D * sign * (sign * (F - K) > 0), d_forward)
        d_variance = np.where(zero, 0.0, d_variance)
    return price, d_forward, d_variance


def heston_batch(
    sign: np.ndarray | float,
    F: np.ndarray | float,
    K: np.ndarray | float,
    D: float,
    tau: float,
    v: np.ndarray | float,
    m: HestonModel | HestonTSModel,
    min_total_variance: float = MIN_TOTAL_VARIANCE,
    max_abs_x_in_sd: float = MAX_ABS_X_IN_SD,
) -> BatchResult:
    """European prices for arrays of (sign, F, K, v) sharing one maturity tau > 0."""
    sign_a, F_a, K_a, v_a = np.broadcast_arrays(
        np.asarray(sign, float), np.asarray(F, float), np.asarray(K, float), np.asarray(v, float)
    )
    shape = F_a.shape
    sign_a, F_a, K_a, v_a = (a.ravel() for a in (sign_a, F_a, K_a, v_a))
    weight = m.reversion_weight(tau)
    total_var = m.integrated_variance_array(tau, v_a)
    x = np.log(F_a / K_a)
    fourier = (min_total_variance <= total_var) & (
        np.abs(x) <= max_abs_x_in_sd * np.sqrt(np.maximum(total_var, 0))
    )

    price, d_forward, d_variance = _bsm_limit(sign_a, F_a, K_a, D, tau, total_var, weight)
    idx = np.flatnonzero(fourier)
    if idx.size:
        # Rows grouped by remaining variance (factor-2 bins): each group gets nodes
        # sized to its own decay and moneyness range.
        group = np.floor(np.log2(total_var[idx])).astype(int)
        for g in np.unique(group):
            rows = idx[group == g]
            u, wts = node_set(tau, m, float(v_a[rows].min()), float(np.abs(x[rows]).max()))
            c_coef, d_coef = characteristic_coefficients(u - 0.5j, tau, m)
            base = wts / (u * u + 0.25)
            for start in range(0, rows.size, CHUNK):
                sel = rows[start : start + CHUNK]
                xs, vs, Fs, Ks = x[sel], v_a[sel], F_a[sel], K_a[sel]
                # e^{i u x} phi(u - i/2) for every (row, node)
                kernel = np.exp(1j * np.outer(xs, u) + c_coef + np.outer(vs, d_coef))
                i0 = kernel.real @ base
                i1 = (kernel * (0.5 + 1j * u)).real @ base
                i2 = (kernel * d_coef).real @ base
                root = np.sqrt(Fs * Ks)
                call = D * (Fs - root / math.pi * i0)
                call_dF = D * (1.0 - np.sqrt(Ks / Fs) / math.pi * i1)
                call_dv = -D * root / math.pi * i2
                put = sign_a[sel] < 0
                price[sel] = np.where(put, call - D * (Fs - Ks), call)
                d_forward[sel] = np.where(put, call_dF - D, call_dF)
                d_variance[sel] = call_dv
    return BatchResult(
        price.reshape(shape),
        d_forward.reshape(shape),
        d_variance.reshape(shape),
        fourier.reshape(shape),
    )
