"""Surface SVI (Gatheral & Jacquier, 2014) with power-law phi.

Total implied variance at forward log-moneyness k = ln(K/F) and maturity t:

    w(k, t) = theta_t / 2 * (1 + rho*phi*k + sqrt((phi*k + rho)^2 + 1 - rho^2)),
    phi = phi(theta_t) = eta * theta_t^(-gamma) * (1 + theta_t)^(gamma - 1).

Static-arbitrage conditions enforced at construction (ADR 0015):
* eta * (1 + |rho|) <= 2 and 0 < gamma <= 1/2  => butterfly conditions of
  Theorem 4.2 (theta*phi*(1+|rho|) < 4 and theta*phi^2*(1+|rho|) <= 4) hold for
  every theta > 0, and the calendar condition of Theorem 4.1 on theta*phi holds;
* theta_t non-decreasing in t (piecewise-linear interpolation of the fitted
  ATM total variances, linear extrapolation with non-negative slope).
These give a guarantee for the parametric surface, conditional on the stated
interpolation/extrapolation rules. Sampled checks are reported separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from options_engine.domain.errors import DomainError, ErrorCode

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class SSVISurface:
    rho: float
    eta: float
    gamma: float
    expiries: tuple[float, ...]  # year fractions, strictly increasing
    thetas: tuple[float, ...]  # ATM total variance at each expiry, non-decreasing

    def __post_init__(self) -> None:
        bad = []
        if not -1.0 < self.rho < 1.0:
            bad.append("|rho| < 1")
        if not self.eta > 0.0:
            bad.append("eta > 0")
        if not 0.0 < self.gamma <= 0.5:
            bad.append("0 < gamma <= 1/2")
        if self.eta * (1.0 + abs(self.rho)) > 2.0 + 1e-12:
            bad.append("eta*(1+|rho|) <= 2")
        if not self.expiries or len(self.expiries) != len(self.thetas):
            bad.append("one theta per expiry")
        elif any(t <= 0 for t in self.expiries) or any(
            b <= a for a, b in zip(self.expiries, self.expiries[1:], strict=False)
        ):
            bad.append("expiries positive and strictly increasing")
        elif any(th <= 0 for th in self.thetas) or any(
            b < a for a, b in zip(self.thetas, self.thetas[1:], strict=False)
        ):
            bad.append("thetas positive and non-decreasing")
        if bad:
            raise DomainError(
                ErrorCode.INVALID_MODEL_PARAMETER,
                "SSVI parameters violate no-arbitrage constraints: " + ", ".join(bad),
            )

    # --- term structure -------------------------------------------------------------------

    def theta(self, t: ArrayLike) -> FloatArray:
        """ATM total variance: 0 at t=0, piecewise linear, linear extrapolation beyond."""
        tt = np.asarray(t, dtype=np.float64)
        xs = np.array((0.0, *self.expiries))
        ys = np.array((0.0, *self.thetas))
        inside = np.interp(tt, xs, ys)
        slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        return np.asarray(np.where(tt > xs[-1], ys[-1] + slope * (tt - xs[-1]), inside))

    def phi(self, theta: ArrayLike) -> FloatArray:
        th = np.asarray(theta, dtype=np.float64)
        return np.asarray(self.eta * th ** (-self.gamma) * (1.0 + th) ** (self.gamma - 1.0))

    # --- smile ----------------------------------------------------------------------------

    def total_variance(self, k: ArrayLike, t: ArrayLike) -> FloatArray:
        th = self.theta(t)
        p = self.phi(th)
        kk = np.asarray(k, dtype=np.float64)
        z = p * kk + self.rho
        return np.asarray(0.5 * th * (1.0 + self.rho * p * kk + np.sqrt(z * z + 1.0 - self.rho**2)))

    def implied_vol(self, k: ArrayLike, t: ArrayLike) -> FloatArray:
        tt = np.asarray(t, dtype=np.float64)
        return np.asarray(np.sqrt(self.total_variance(k, tt) / tt))

    def butterfly_density(self, k: ArrayLike, t: float) -> FloatArray:
        """Gatheral's g(k); g >= 0 everywhere <=> no butterfly arbitrage in that slice."""
        th = float(self.theta(t))
        p = float(self.phi(th))
        kk = np.asarray(k, dtype=np.float64)
        z = p * kk + self.rho
        R = np.sqrt(z * z + 1.0 - self.rho**2)
        w = 0.5 * th * (1.0 + self.rho * p * kk + R)
        w1 = 0.5 * th * (self.rho * p + p * z / R)
        w2 = 0.5 * th * p * p * (1.0 - self.rho**2) / R**3
        return np.asarray(
            (1.0 - kk * w1 / (2.0 * w)) ** 2 - 0.25 * w1**2 * (1.0 / w + 0.25) + 0.5 * w2
        )

    def wing_slopes(self) -> tuple[float, float]:
        """Asymptotic dw/d|k| at the last expiry; Lee's moment bound requires <= 2."""
        th = self.thetas[-1]
        p = float(self.phi(th))
        return 0.5 * th * p * (1.0 - self.rho), 0.5 * th * p * (1.0 + self.rho)

    def to_dict(self) -> dict[str, object]:
        return {
            "family": "ssvi_power_law",
            "rho": self.rho,
            "eta": self.eta,
            "gamma": self.gamma,
            "expiries": list(self.expiries),
            "thetas": list(self.thetas),
            "interpolation": "theta piecewise linear in t; theta(0)=0; linear extrapolation "
            "beyond the last expiry with the last segment's (non-negative) slope",
        }


def guarantee_conditions(s: SSVISurface) -> dict[str, bool | float]:
    """Explicit check of the sufficient conditions used for the no-arbitrage claim."""
    return {
        "eta_times_one_plus_abs_rho": s.eta * (1 + abs(s.rho)),
        "eta_bound_ok": s.eta * (1 + abs(s.rho)) <= 2.0 + 1e-12,
        "gamma_in_(0,0.5]": 0.0 < s.gamma <= 0.5,
        "theta_non_decreasing": all(b >= a for a, b in zip(s.thetas, s.thetas[1:], strict=False)),
        "max_wing_slope": max(s.wing_slopes()),
        "lee_wing_bound_ok": max(s.wing_slopes()) <= 2.0,
        "min_atm_vol": math.sqrt(min(th / t for th, t in zip(s.thetas, s.expiries, strict=True))),
    }
