"""Bounded execution of CPU-bound handlers (ADR 0009).

* ``workers > 0``: a spawn-context ``ProcessPoolExecutor`` so numerical work
  never runs on the event loop and cannot starve other requests of the GIL.
* ``workers == 0``: run in a thread pool instead (tests, single-user development).
* Admission control: at most ``max_in_flight`` jobs are accepted; more are
  rejected immediately with ``busy`` rather than queued without bound. A slot is
  released when the job itself finishes (or is cancelled before starting), not
  when the client stops waiting, so a timeout never frees capacity that is
  still busy.
* ``timeout_seconds`` bounds how long a client waits. A job that has not started
  is cancelled; a running job is not killed: its runtime is bounded by the work
  limits validated before submission.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from collections.abc import Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel

from options_engine.domain.errors import DomainError
from options_engine.interfaces import handlers
from options_engine.interfaces.schemas import (
    CompareRequestIn,
    HedgingIn,
    HestonCalibrationIn,
    ImpliedVolIn,
    MCConvergenceIn,
    PortfolioRequestIn,
    PriceRequestIn,
    RunManifest,
    SurfaceFitIn,
    TreeConvergenceIn,
)

HANDLERS: dict[str, tuple[type[BaseModel], Callable[[Any], Any]]] = {
    "price": (PriceRequestIn, handlers.price),
    "manifest": (PriceRequestIn, handlers.manifest),
    "replay": (RunManifest, handlers.replay),
    "compare": (CompareRequestIn, handlers.compare),
    "tree_convergence": (TreeConvergenceIn, handlers.tree_convergence),
    "mc_convergence": (MCConvergenceIn, handlers.mc_convergence),
    "implied_vol": (ImpliedVolIn, handlers.implied_vol),
    "portfolio": (PortfolioRequestIn, handlers.portfolio),
    "surface_fit": (SurfaceFitIn, handlers.surface_fit),
    "heston_calibrate": (HestonCalibrationIn, handlers.heston_calibrate),
    "hedging": (HedgingIn, handlers.hedging_experiment),
    "profile": (PriceRequestIn, handlers.profile),
    "exercise_boundary": (PriceRequestIn, handlers.exercise_boundary),
    "smile": (PriceRequestIn, handlers.smile),
}


def invoke(name: str, payload: str) -> tuple[str, str]:
    """Worker entry point: JSON in, ("ok"|"error", JSON) out. Must stay picklable."""
    schema, fn = HANDLERS[name]
    try:
        result = fn(schema.model_validate_json(payload))
    except DomainError as exc:
        return "error", json.dumps(exc.to_dict())
    if isinstance(result, BaseModel):
        return "ok", result.model_dump_json()
    return "ok", json.dumps(result)


def _warm() -> int:
    """Executed once per worker at startup so the first request skips imports."""
    return len(HANDLERS)


class BusyError(Exception):
    pass


class ComputeRunner:
    def __init__(self, workers: int, max_in_flight: int, timeout_seconds: float) -> None:
        self.workers = workers
        self.max_in_flight = max_in_flight
        self.timeout_seconds = timeout_seconds
        self._in_flight = 0
        self._executor: Executor = (
            ProcessPoolExecutor(
                max_workers=workers, mp_context=multiprocessing.get_context("spawn")
            )
            if workers > 0
            else ThreadPoolExecutor(max_workers=max(1, max_in_flight))
        )

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def _release(self) -> None:
        self._in_flight -= 1

    async def run(self, name: str, payload: str) -> tuple[str, str]:
        if self._in_flight >= self.max_in_flight:
            raise BusyError
        self._in_flight += 1
        loop = asyncio.get_running_loop()
        job: Future[tuple[str, str]] = self._executor.submit(invoke, name, payload)
        job.add_done_callback(lambda _: loop.call_soon_threadsafe(self._release))
        # On timeout wait_for cancels the wrapper, which cancels the job only if it
        # has not started; the done-callback above still releases the slot.
        return await asyncio.wait_for(asyncio.wrap_future(job), self.timeout_seconds)

    def warm(self) -> None:
        if isinstance(self._executor, ProcessPoolExecutor):
            for fut in [self._executor.submit(_warm) for _ in range(self.workers)]:
                fut.result(timeout=120)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
