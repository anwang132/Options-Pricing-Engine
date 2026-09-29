"""Load the versioned validation policy and expand the stress matrix."""

from __future__ import annotations

import itertools
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from options_engine.adapters.environment import PROJECT_ROOT, file_sha256
from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.engines.base import PricingProblem

POLICY_PATH = PROJECT_ROOT / "config" / "validation_policy.toml"
FIXTURE_DIR = PROJECT_ROOT / "fixtures" / "reference"
AXES = ("spot", "strike", "time", "vol", "rate", "dividend_yield")


@dataclass(frozen=True, slots=True)
class StressCase:
    regime: str
    option_type: OptionType
    spot: float
    strike: float
    time: float
    vol: float
    rate: float
    dividend_yield: float

    @property
    def case_id(self) -> str:
        return (
            f"{self.regime}:{self.option_type.value}:S{self.spot:g}:K{self.strike:g}:"
            f"T{self.time:g}:v{self.vol:g}:r{self.rate:g}:q{self.dividend_yield:g}"
        )

    def problem(self, exercise: ExerciseStyle = ExerciseStyle.EUROPEAN) -> PricingProblem:
        return PricingProblem(
            self.option_type,
            exercise,
            self.spot,
            self.strike,
            self.time,
            self.rate,
            self.dividend_yield,
            self.vol,
        )


@lru_cache(maxsize=4)
def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def policy_sha256(path: Path = POLICY_PATH) -> str | None:
    return file_sha256(path)


def stress_cases(policy: dict[str, Any] | None = None) -> list[StressCase]:
    policy = policy or load_policy()
    cases: list[StressCase] = []
    for regime, axes in policy["stress"].items():
        for values in itertools.product(*(axes[a] for a in AXES)):
            for opt in OptionType:
                cases.append(StressCase(regime, opt, *(float(v) for v in values)))
    return cases


def within(error: float, reference: float, atol: float, rtol: float) -> bool:
    return abs(error) <= atol + rtol * abs(reference)
