"""Visualisation data: checked against independent routes, not just re-computed."""

from __future__ import annotations

import itertools
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from options_engine.application import visuals
from options_engine.application.pricing import PricingService
from options_engine.application.surface import SurfaceService, black_price
from options_engine.domain.conventions import ExerciseStyle, OptionType
from options_engine.domain.errors import DomainError
from options_engine.engines.base import PricingProblem
from options_engine.engines.crr import build_tree
from options_engine.models.heston import HestonModel
from options_engine.models.ssvi import SSVISurface
from tests.conftest import make_request

SVC = PricingService()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "snapshots"


def test_profile_centre_matches_single_price_and_greeks():
    req = make_request()
    prof = visuals.value_profile(SVC, req)
    centre = prof["spots"].index(prof["current_spot"])
    single = SVC.price(req)
    assert prof["horizons"][0]["values"][centre] == single.price
    delta = next(g for g in single.output.greeks.values() if g.name.value == "delta")
    assert prof["greeks"]["delta"][centre] == pytest.approx(delta.value)
    vega = next(g for g in single.output.greeks.values() if g.name.value == "vega")
    assert prof["greeks"]["vega"][centre] == pytest.approx(vega.value * 0.01)  # per vol point


def test_profile_shape():
    prof = visuals.value_profile(SVC, make_request())
    spots = np.array(prof["spots"])
    assert np.all(np.diff(spots) > 0)
    assert spots[0] < prof["strike"] < spots[-1]
    assert prof["payoff"] == [max(s - 40.0, 0.0) for s in prof["spots"]]
    assert [h["elapsed_fraction"] for h in prof["horizons"]] == [0.0, 0.5, 0.9]
    delta = np.array(prof["greeks"]["delta"])
    assert np.all(np.diff(delta) >= -1e-12)  # call delta non-decreasing in spot
    # Time value decays toward the payoff (call, q = 0, r > 0: value >= payoff throughout).
    centre = prof["spots"].index(prof["current_spot"])
    at_money = [h["values"][centre] for h in prof["horizons"]]
    assert at_money[0] > at_money[1] > at_money[2] >= prof["payoff"][centre]


def test_american_profile_uses_tree():
    prof = visuals.value_profile(
        SVC, make_request(contract={"exercise_style": ExerciseStyle.AMERICAN})
    )
    assert prof["engine"].startswith("CRR")


def test_put_boundary_properties():
    req = make_request(
        contract={"exercise_style": ExerciseStyle.AMERICAN, "option_type": OptionType.PUT}
    )
    out = visuals.exercise_boundary(SVC, req)
    pts = [p["boundary_spot"] for p in out["points"] if p["boundary_spot"] is not None]
    assert pts
    assert max(pts) <= out["strike"]
    spacing = math.exp(0.2 * math.sqrt(0.5 / out["steps"]))
    assert all(b >= a / spacing for a, b in itertools.pairwise(pts))  # rises toward expiry
    assert pts[-1] > 0.95 * out["strike"]


def test_boundary_approaches_perpetual_put_limit():
    r, v, K = 0.05, 0.25, 100.0
    perpetual = K * 2 * r / (2 * r + v * v)  # McKean/Merton perpetual put, q = 0
    problem = PricingProblem(OptionType.PUT, ExerciseStyle.AMERICAN, 100.0, K, 50.0, r, 0.0, v)
    raw: list[tuple[float, float | None]] = []
    build_tree(problem, 4000, raw)
    first = next(b for _, b in sorted(raw) if b is not None)
    spacing = v * math.sqrt(50.0 / 4000)
    assert perpetual <= first <= perpetual * math.exp(spacing)


def test_boundary_recording_does_not_change_price():
    problem = PricingProblem(
        OptionType.PUT, ExerciseStyle.AMERICAN, 100.0, 100.0, 1.0, 0.05, 0.0, 0.3
    )
    assert build_tree(problem, 500, []).price == build_tree(problem, 500).price


def test_call_without_dividends_has_no_exercise_nodes():
    out = visuals.exercise_boundary(
        SVC, make_request(contract={"exercise_style": ExerciseStyle.AMERICAN})
    )
    assert out["no_node_exercised"] is True


def test_boundary_rejected_for_european():
    with pytest.raises(DomainError):
        visuals.exercise_boundary(SVC, make_request())


def _fitted(tmp_path) -> tuple[SSVISurface, dict[str, Any]]:
    svc = SurfaceService(tmp_path)
    sid = svc.snapshots.ingest_file(FIXTURES / "synthetic_clean_day1.json")[0]["snapshot_id"]
    art, _ = svc.fit(sid)
    return svc.surface(art["fit_id"])


def test_density_integrates_and_is_a_martingale(tmp_path):
    surface, art = _fitted(tmp_path)
    views = visuals.surface_views(surface, art)
    for d in views["densities"]:
        assert d["min_density"] >= 0.0
        assert d["mass_on_grid"] == pytest.approx(1.0, abs=2e-3)
        assert d["mean_forward_ratio_on_grid"] == pytest.approx(1.0, abs=2e-3)


def test_density_matches_breeden_litzenberger(tmp_path):
    """Closed-form density vs second strike-derivative of Black prices on the same surface."""
    surface, _ = _fitted(tmp_path)
    t = surface.expiries[-1]
    k = np.linspace(-0.4, 0.3, 41)
    h = 1e-3
    K = np.exp(k)
    ones = np.ones_like(K)

    def call(strikes: np.ndarray) -> np.ndarray:
        vol = surface.implied_vol(np.log(strikes), t)
        return black_price(ones, ones, strikes, np.full_like(strikes, t), ones, vol)

    dK = K * h  # uniform central difference in K
    d2c = (call(K + dK) - 2 * call(K) + call(K - dK)) / dK**2
    bl = K * d2c  # density of k = ln K (F = 1, D = 1)
    closed = visuals.density(surface, k, t)
    np.testing.assert_allclose(closed, bl, rtol=5e-3, atol=1e-4)


def test_surface_views_grid_matches_surface(tmp_path):
    surface, art = _fitted(tmp_path)
    views = visuals.surface_views(surface, art)
    i, j = 3, 7
    expected = surface.implied_vol(views["k_grid"][j], views["t_grid"][i])
    assert views["implied_vol"][i][j] == pytest.approx(float(expected))
    regions = {p["region"] for p in views["term_structure"]}
    assert regions == {"interpolated", "extrapolated"}


def test_api_visual_endpoints(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from options_engine.interfaces.http.app import create_app

    monkeypatch.setenv("OPTIONS_ENGINE_DATA_DIR", str(tmp_path))
    body = json.loads(
        (Path(__file__).resolve().parents[2] / "examples" / "price_hull_call.json").read_text()
    )
    with TestClient(create_app(workers=0)) as c:
        r = c.post("/api/v1/visuals/profile", json=body)
        assert r.status_code == 200, r.text
        assert len(r.json()["spots"]) == visuals.PROFILE_POINTS
        r = c.post("/api/v1/visuals/exercise-boundary", json=body)
        assert r.status_code == 422  # European: no boundary
        body["contract"]["exercise_style"] = "american"
        body["contract"]["option_type"] = "put"
        body["engine"] = {"engine": "crr_tree"}
        r = c.post("/api/v1/visuals/exercise-boundary", json=body)
        assert r.status_code == 200, r.text
        ids = [b["snapshot_id"] for b in c.post("/api/v1/snapshots/bundled").json()]
        fit_id = c.post("/api/v1/surface/fits", json={"snapshot_id": ids[0]}).json()["artifact"][
            "fit_id"
        ]
        r = c.get(f"/api/v1/surface/fits/{fit_id}/views")
        assert r.status_code == 200, r.text
        assert len(r.json()["densities"]) == 5


def _heston_request(sigma: float = 0.5, rho: float = -0.7, **kw: Any):
    return make_request(
        model=HestonModel(0.04, 1.5, 0.04, sigma, rho),
        engine_id="heston_fourier",
        market={"spot": Decimal("100"), "dividend_yield": Decimal("0.01")},
        contract={"strike": Decimal("100")},
        **kw,
    )


def test_black_scholes_smile_is_flat():
    smile = visuals.model_smile(SVC, make_request())
    ivs = [p["implied_vol"] for p in smile["points"]]
    assert all(v == pytest.approx(0.2, abs=1e-9) for v in ivs)
    assert abs(smile["skew"]) < 1e-9


def test_heston_smile_has_negative_correlation_skew():
    smile = visuals.model_smile(SVC, _heston_request(rho=-0.7))
    ivs = [p["implied_vol"] for p in smile["points"]]
    assert None not in ivs
    assert smile["skew"] > 0.02  # downside strikes richer than upside
    assert ivs[0] > smile["atm_implied_vol"] > ivs[len(ivs) * 3 // 4]


def test_heston_smile_is_a_smile_at_zero_correlation():
    smile = visuals.model_smile(SVC, _heston_request(rho=0.0))
    ivs = [p["implied_vol"] for p in smile["points"]]
    atm = smile["atm_implied_vol"]
    assert ivs[0] > atm and ivs[-1] > atm  # convex in strike: vol of vol fattens both tails


def test_heston_smile_flattens_as_vol_of_vol_vanishes():
    req = _heston_request(sigma=1e-6)
    smile = visuals.model_smile(SVC, req)
    T = smile["time_to_expiry"]
    target = req.model.effective_volatility(T)
    assert all(p["implied_vol"] == pytest.approx(target, abs=1e-5) for p in smile["points"])


def test_heston_profile_uses_fourier_engine_and_boundary_is_rejected():
    req = _heston_request()
    assert visuals.value_profile(SVC, req)["engine"].startswith("Heston")
    american = make_request(
        model=req.model,
        contract={"exercise_style": ExerciseStyle.AMERICAN, "strike": Decimal("100")},
    )
    with pytest.raises(DomainError):
        visuals.exercise_boundary(SVC, american)
