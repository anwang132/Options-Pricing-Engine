"""Explicit engine registry. Adding an engine = implement, register, pass conformance tests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.engines.base import PricingEngine
from options_engine.engines.bsm_analytic import BlackScholesAnalyticEngine
from options_engine.engines.crr import CRREngine
from options_engine.engines.mc_terminal import MonteCarloTerminalEngine


class EngineRegistry:
    def __init__(self, engines: Iterable[PricingEngine]) -> None:
        self._engines: dict[str, PricingEngine] = {}
        for engine in engines:
            if engine.engine_id in self._engines:
                raise ValueError(f"duplicate engine id {engine.engine_id}")
            self._engines[engine.engine_id] = engine

    def get(self, engine_id: str) -> PricingEngine:
        try:
            return self._engines[engine_id]
        except KeyError:
            raise DomainError(
                ErrorCode.UNKNOWN_ENGINE,
                f"unknown engine '{engine_id}'",
                {"available": sorted(self._engines)},
            ) from None

    def ids(self) -> list[str]:
        return list(self._engines)

    def capability_matrix(self) -> list[dict[str, Any]]:
        return [
            {
                "engine_id": e.engine_id,
                "version": e.version,
                "description": e.description,
                "config_type": e.config_type.__name__,
                "capabilities": e.capabilities.to_dict(),
            }
            for e in self._engines.values()
        ]


def default_registry() -> EngineRegistry:
    return EngineRegistry([BlackScholesAnalyticEngine(), CRREngine(), MonteCarloTerminalEngine()])
