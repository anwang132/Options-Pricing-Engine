"""Heston (1993) stochastic-volatility model specification.

    dS/S = (r - q) dt + sqrt(v) dW1,   dv = kappa (theta - v) dt + sigma sqrt(v) dW2,
    d<W1, W2> = rho dt,  v(0) = v0.

The Feller condition 2*kappa*theta >= sigma^2 keeps v strictly positive. Its
violation is reported, not rejected: the model and its characteristic function
remain well defined when v can touch zero (ADR 0017).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from options_engine.domain.errors import DomainError, ErrorCode

HESTON = "heston"

# Engineering domain limits, rejected rather than clipped.
MAX_VARIANCE = 4.0  # 200% vol
MAX_KAPPA = 50.0
MAX_VOL_OF_VOL = 5.0


def reversion_weight(kappa: float, T: float) -> float:
    """(1 - e^{-kappa T}) / kappa via expm1, with its series near kappa = 0."""
    kt = kappa * T
    return T * (1.0 - kt / 2.0 + kt * kt / 6.0) if kt < 1e-8 else -math.expm1(-kt) / kappa


@dataclass(frozen=True, slots=True)
class HestonModel:
    v0: float  # initial variance (decimal^2 per year)
    kappa: float  # mean-reversion speed (per year)
    theta: float  # long-run variance
    sigma: float  # volatility of variance ("vol of vol")
    rho: float  # correlation between spot and variance shocks
    family: str = HESTON

    def __post_init__(self) -> None:
        values = {
            "v0": self.v0,
            "kappa": self.kappa,
            "theta": self.theta,
            "sigma": self.sigma,
            "rho": self.rho,
        }
        bad = [k for k, v in values.items() if not math.isfinite(v)]
        if not 0.0 <= self.v0 <= MAX_VARIANCE or not 0.0 <= self.theta <= MAX_VARIANCE:
            bad.append(f"0 <= v0, theta <= {MAX_VARIANCE}")
        if not 0.0 <= self.kappa <= MAX_KAPPA:
            bad.append(f"0 <= kappa <= {MAX_KAPPA}")
        if not 0.0 <= self.sigma <= MAX_VOL_OF_VOL:
            bad.append(f"0 <= sigma <= {MAX_VOL_OF_VOL}")
        if not -1.0 < self.rho < 1.0:
            bad.append("-1 < rho < 1")
        if self.v0 == 0.0 and self.theta == 0.0:
            bad.append("v0 and theta cannot both be zero (no variance at all)")
        if bad:
            raise DomainError(
                ErrorCode.INVALID_MODEL_PARAMETER, "invalid Heston parameters: " + "; ".join(bad)
            )

    @property
    def feller_satisfied(self) -> bool:
        return 2.0 * self.kappa * self.theta >= self.sigma**2

    def reversion_weight(self, T: float) -> float:
        return reversion_weight(self.kappa, T)

    def integrated_variance(self, T: float, v: float | None = None) -> float:
        """I(T) = theta*T + (v - theta)(1 - e^{-kappa T})/kappa, v defaulting to v0.

        The expected variance integrated over [0, T] given the current variance v.
        """
        v = self.v0 if v is None else v
        return self.theta * T + (v - self.theta) * self.reversion_weight(T)

    def integrated_variance_array(self, T: float, v: np.ndarray) -> np.ndarray:
        """I(T; v) for an array of current variances."""
        return np.asarray(self.theta * T + (v - self.theta) * self.reversion_weight(T))

    def theta_at(self, t: float) -> float:
        return self.theta

    def effective_volatility(self, T: float) -> float:
        """BSM volatility of the deterministic-variance limit (sigma -> 0)."""
        return math.sqrt(self.integrated_variance(T) / T)

    @property
    def volatility(self) -> float:
        """Instantaneous volatility sqrt(v0), for labels only; pricing uses the full model."""
        return math.sqrt(self.v0)


@dataclass(frozen=True, slots=True)
class HestonTSModel:
    """Heston with a piecewise-constant long-run variance theta(t) (ADR 0024).

    theta(t) = thetas[i] for pillars[i-1] < t <= pillars[i] (pillars in years from the
    valuation time; the last bucket extends to infinity). kappa, sigma, rho are constant.
    Because the Riccati coefficient D(u, tau) does not involve theta, the characteristic
    function stays exact: C(T) = kappa * sum_i theta_i * integral of D over bucket i.
    The model is anchored at the valuation time (it is not shifted for later dates).
    """

    v0: float
    kappa: float
    sigma: float
    rho: float
    thetas: tuple[float, ...]
    pillars: tuple[float, ...]
    family: str = HESTON

    def __post_init__(self) -> None:
        bad = []
        if len(self.thetas) != len(self.pillars) + 1:
            bad.append("need one more theta than pillars")
        if any(b <= a for a, b in zip(self.pillars, self.pillars[1:], strict=False)) or any(
            p <= 0 for p in self.pillars
        ):
            bad.append("pillars must be positive and increasing")
        if bad:
            raise DomainError(ErrorCode.INVALID_MODEL_PARAMETER, "; ".join(bad))
        # Each bucket must be a valid constant-parameter model (same limits, same errors).
        for th in self.thetas:
            HestonModel(self.v0, self.kappa, th, self.sigma, self.rho)

    @property
    def theta(self) -> float:
        """Long-run level of the last bucket (for labels)."""
        return self.thetas[-1]

    @property
    def feller_satisfied(self) -> bool:
        return 2.0 * self.kappa * min(self.thetas) >= self.sigma**2

    def theta_at(self, t: float) -> float:
        for p, th in zip(self.pillars, self.thetas, strict=False):
            if t < p:
                return th
        return self.thetas[-1]

    def buckets(self, T: float) -> list[tuple[float, float, float]]:
        """(start, end, theta) of each bucket intersected with [0, T]."""
        edges = [0.0, *self.pillars, math.inf]
        out = []
        for i, th in enumerate(self.thetas):
            a, b = edges[i], min(edges[i + 1], T)
            if b > a:
                out.append((a, b, th))
        return out

    def reversion_weight(self, T: float) -> float:
        return reversion_weight(self.kappa, T)

    def _theta_part(self, T: float) -> float:
        """sum_i theta_i * integral over bucket i of (1 - e^{-kappa (T - s)}) ds."""
        w = self.reversion_weight
        return math.fsum(th * ((b - a) - (w(T - a) - w(T - b))) for a, b, th in self.buckets(T))

    def integrated_variance(self, T: float, v: float | None = None) -> float:
        v = self.v0 if v is None else v
        return v * self.reversion_weight(T) + self._theta_part(T)

    def integrated_variance_array(self, T: float, v: np.ndarray) -> np.ndarray:
        return np.asarray(v * self.reversion_weight(T) + self._theta_part(T))

    def effective_volatility(self, T: float) -> float:
        return math.sqrt(self.integrated_variance(T) / T)

    @property
    def volatility(self) -> float:
        return math.sqrt(self.v0)
