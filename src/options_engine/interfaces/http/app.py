"""FastAPI application: versioned JSON API plus the built web UI.

Run: ``uv run options-engine serve`` (see README). Configuration via env:
OPTIONS_ENGINE_WORKERS (default 2; 0 = in-thread), OPTIONS_ENGINE_MAX_IN_FLIGHT
(default 8), OPTIONS_ENGINE_TIMEOUT_SECONDS (default 30).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from options_engine import __version__
from options_engine.domain.errors import DomainError, ErrorCode
from options_engine.interfaces import handlers
from options_engine.interfaces.http.worker import BusyError, ComputeRunner
from options_engine.interfaces.schemas import (
    CompareRequestIn,
    CompareResponse,
    ErrorResponse,
    ExerciseBoundaryResponse,
    ImpliedVolIn,
    ImpliedVolResponse,
    LedgerEventOut,
    MarkResultOut,
    MCConvergenceIn,
    MCConvergenceResponse,
    PaperAccountIn,
    PaperAccountRow,
    PaperHistoryPoint,
    PaperMarkIn,
    PaperSettlementIn,
    PaperSummaryOut,
    PaperTradeIn,
    PaperVoidIn,
    PortfolioRequestIn,
    PortfolioResponse,
    PriceRequestIn,
    PriceResponse,
    ProfileResponse,
    RunManifest,
    SurfaceFitIn,
    SurfaceViewsResponse,
    TreeConvergenceIn,
    TreeConvergenceResponse,
)

log = logging.getLogger("options_engine.http")
UI_DIST = Path(__file__).resolve().parents[4] / "ui" / "dist"

STATUS_FOR: dict[str, int] = {
    ErrorCode.NOT_FOUND.value: 404,
    ErrorCode.UNKNOWN_ENGINE.value: 404,
    ErrorCode.WORK_LIMIT_EXCEEDED.value: 413,
}


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status, content=body)


def _setting[T: (int, float)](explicit: T | None, env: str, default: T) -> T:
    """Explicit argument, else environment variable, else default (same type)."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(env)
    return default if raw is None else type(default)(raw)


def create_app(
    workers: int | None = None,
    max_in_flight: int | None = None,
    timeout_seconds: float | None = None,
) -> FastAPI:
    workers = _setting(workers, "OPTIONS_ENGINE_WORKERS", 2)
    max_in_flight = _setting(max_in_flight, "OPTIONS_ENGINE_MAX_IN_FLIGHT", 8)
    timeout_seconds = _setting(timeout_seconds, "OPTIONS_ENGINE_TIMEOUT_SECONDS", 30.0)
    metrics: Counter[str] = Counter()
    runner = ComputeRunner(workers, max_in_flight, timeout_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(runner.warm)
        yield
        runner.shutdown()

    app = FastAPI(
        title="Options Pricing & Validation Workbench",
        version=__version__,
        lifespan=lifespan,
        responses={422: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    )
    app.state.runner = runner
    app.state.metrics = metrics

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        response.headers["x-request-id"] = request_id
        # Operational metadata only: request bodies may contain private positions.
        log.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration * 1000,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        metrics["error.invalid_request"] += 1
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return error_response(
            422, "invalid_request", "request failed schema validation", {"errors": details}
        )

    async def dispatch(name: str, body: Any) -> Response:
        try:
            status, payload = await runner.run(name, body.model_dump_json())
        except BusyError:
            metrics["error.busy"] += 1
            return error_response(503, "busy", "compute capacity exhausted; retry later")
        except TimeoutError:
            metrics["error.timeout"] += 1
            return error_response(504, "timeout", f"computation exceeded {timeout_seconds}s")
        if status == "error":
            err = json.loads(payload)
            metrics[f"error.{err['code']}"] += 1
            return JSONResponse(
                status_code=STATUS_FOR.get(err["code"], 422), content={"error": err}
            )
        metrics[f"ok.{name}"] += 1
        return Response(content=payload, media_type="application/json")

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "workers": workers,
            "in_flight": runner.in_flight,
        }

    @app.get("/api/v1/metrics")
    def get_metrics() -> dict[str, int]:
        return dict(metrics)

    @app.get("/api/v1/engines")
    def engines() -> list[dict[str, Any]]:
        return handlers.engines()

    @app.post("/api/v1/price", response_model=PriceResponse)
    async def price(body: PriceRequestIn) -> Response:
        return await dispatch("price", body)

    @app.post("/api/v1/price/manifest", response_model=RunManifest)
    async def manifest(body: PriceRequestIn) -> Response:
        return await dispatch("manifest", body)

    @app.post("/api/v1/replay")
    async def replay(body: RunManifest) -> Response:
        return await dispatch("replay", body)

    @app.post("/api/v1/compare", response_model=CompareResponse)
    async def compare(body: CompareRequestIn) -> Response:
        return await dispatch("compare", body)

    @app.post("/api/v1/convergence/tree", response_model=TreeConvergenceResponse)
    async def tree_convergence(body: TreeConvergenceIn) -> Response:
        return await dispatch("tree_convergence", body)

    @app.post("/api/v1/convergence/mc", response_model=MCConvergenceResponse)
    async def mc_convergence(body: MCConvergenceIn) -> Response:
        return await dispatch("mc_convergence", body)

    @app.post("/api/v1/implied-vol", response_model=ImpliedVolResponse)
    async def implied_vol(body: ImpliedVolIn) -> Response:
        return await dispatch("implied_vol", body)

    @app.post("/api/v1/portfolio/scenarios", response_model=PortfolioResponse)
    async def portfolio(body: PortfolioRequestIn) -> Response:
        return await dispatch("portfolio", body)

    @app.post("/api/v1/visuals/profile", response_model=ProfileResponse)
    async def profile(body: PriceRequestIn) -> Response:
        return await dispatch("profile", body)

    @app.post("/api/v1/visuals/exercise-boundary", response_model=ExerciseBoundaryResponse)
    async def exercise_boundary(body: PriceRequestIn) -> Response:
        return await dispatch("exercise_boundary", body)

    @app.get("/api/v1/surface/fits/{fit_id}/views", response_model=SurfaceViewsResponse)
    def surface_views(fit_id: str) -> SurfaceViewsResponse:
        return handlers.surface_views(fit_id)

    @app.post("/api/v1/surface/fits")
    async def surface_fit(body: SurfaceFitIn) -> Response:
        return await dispatch("surface_fit", body)

    @app.get("/api/v1/surface/fits")
    def surface_fits() -> list[dict[str, Any]]:
        return handlers.surface_fits()

    @app.get("/api/v1/surface/fits/{fit_id}")
    def surface_artifact(fit_id: str) -> dict[str, Any]:
        return handlers.surface_artifact(fit_id)

    # --- Paper trading: ledger file I/O plus light pricing; threadpool (sync handlers).
    # Request bodies (positions, prices) are private and are never logged.
    @app.get("/api/v1/paper/accounts")
    def paper_accounts() -> list[PaperAccountRow]:
        return handlers.paper_accounts()

    @app.post("/api/v1/paper/accounts", status_code=201)
    def paper_open(body: PaperAccountIn) -> PaperSummaryOut:
        return handlers.paper_open(body)

    @app.get("/api/v1/paper/accounts/{account_id}")
    def paper_summary(account_id: str) -> PaperSummaryOut:
        return handlers.paper_summary(account_id)

    @app.post("/api/v1/paper/accounts/{account_id}/trades")
    def paper_trade(account_id: str, body: PaperTradeIn) -> PaperSummaryOut:
        return handlers.paper_trade(account_id, body)

    @app.post("/api/v1/paper/accounts/{account_id}/voids")
    def paper_void(account_id: str, body: PaperVoidIn) -> PaperSummaryOut:
        return handlers.paper_void(account_id, body)

    @app.post("/api/v1/paper/accounts/{account_id}/settlements")
    def paper_settle(account_id: str, body: PaperSettlementIn) -> PaperSummaryOut:
        return handlers.paper_settle(account_id, body)

    @app.post("/api/v1/paper/accounts/{account_id}/marks")
    def paper_mark(account_id: str, body: PaperMarkIn) -> MarkResultOut:
        return handlers.paper_mark(account_id, body)

    @app.get("/api/v1/paper/accounts/{account_id}/history")
    def paper_history(account_id: str) -> list[PaperHistoryPoint]:
        return handlers.paper_history(account_id)

    @app.get("/api/v1/paper/accounts/{account_id}/events")
    def paper_events(account_id: str) -> list[LedgerEventOut]:
        return handlers.paper_events(account_id)

    # --- Snapshots: file I/O plus light parsing; runs in the threadpool (sync handlers).
    @app.get("/api/v1/snapshots")
    def list_snapshots() -> list[dict[str, Any]]:
        return handlers.snapshots_list()

    @app.post("/api/v1/snapshots", status_code=201)
    async def ingest_snapshot(request: Request) -> JSONResponse:
        raw = await request.body()
        out = await run_in_threadpool(handlers.snapshot_ingest, raw)
        return JSONResponse(status_code=201 if out["created"] else 200, content=out)

    @app.post("/api/v1/snapshots/bundled")
    def ingest_bundled() -> list[dict[str, Any]]:
        return handlers.snapshot_ingest_bundled()

    @app.get("/api/v1/snapshots/{snapshot_id}")
    def snapshot_manifest(snapshot_id: str) -> dict[str, Any]:
        return handlers.snapshot_manifest(snapshot_id)

    @app.get("/api/v1/snapshots/{snapshot_id}/quotes")
    def snapshot_quotes(snapshot_id: str) -> list[dict[str, Any]]:
        return handlers.snapshot_quotes(snapshot_id)

    @app.exception_handler(DomainError)
    async def on_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        metrics[f"error.{exc.code.value}"] += 1
        return error_response(
            STATUS_FOR.get(exc.code.value, 422), exc.code.value, exc.message, exc.details
        )

    if UI_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        def spa(path: str) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return error_response(404, "not_found", f"no endpoint /{path}")
            candidate = (UI_DIST / path).resolve()
            if path and candidate.is_file() and UI_DIST in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

    return app
