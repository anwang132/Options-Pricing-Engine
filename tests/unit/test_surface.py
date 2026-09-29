from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from options_engine.application.surface import SurfaceConfig, SurfaceService, arbitrage_diagnostics
from options_engine.domain.errors import DomainError
from options_engine.models.ssvi import SSVISurface
from options_engine.validation.policy import load_policy

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "snapshots"
T = (30 / 365, 91 / 365, 1.0)


@pytest.fixture
def svc(tmp_path) -> SurfaceService:
    return SurfaceService(tmp_path)


def ingest(svc: SurfaceService, name: str | None = None, raw: bytes | None = None) -> str:
    data = raw if raw is not None else (FIXTURES / str(name)).read_bytes()
    return str(svc.snapshots.ingest(data)[0]["snapshot_id"])


@pytest.mark.parametrize(
    "kw",
    [
        {"rho": 1.0},
        {"eta": 1.5, "rho": -0.5},  # eta * (1 + |rho|) = 2.25 > 2
        {"gamma": 0.6},
        {"thetas": (0.02, 0.01, 0.05)},  # calendar arbitrage in ATM variance
    ],
)
def test_ssvi_rejects_constraint_violations(kw):
    base = {"rho": -0.5, "eta": 1.0, "gamma": 0.4, "expiries": T, "thetas": (0.004, 0.01, 0.04)}
    base.update(kw)
    with pytest.raises(DomainError, match="no-arbitrage"):
        SSVISurface(**base)


@settings(max_examples=150, deadline=None)
@given(
    st.floats(-0.99, 0.99),
    st.floats(0.01, 1.0),
    st.floats(0.01, 0.5),
    st.lists(st.floats(1e-4, 0.2), min_size=3, max_size=3),
)
def test_constrained_ssvi_has_no_sampled_arbitrage(rho, u, gamma, increments):
    thetas = tuple(np.cumsum(increments))
    s = SSVISurface(rho, 2 * u / (1 + abs(rho)), gamma, T, thetas)
    k = np.linspace(-3, 3, 241)
    for t in (*T, 2.0):
        assert np.min(s.butterfly_density(k, t)) >= -1e-10
    W = np.array([s.total_variance(k, t) for t in np.linspace(0.01, 2.0, 30)])
    assert np.all(np.diff(W, axis=0) >= -1e-14)


def test_sampled_diagnostics_detect_an_arbitrageable_surface():
    s = SSVISurface(-0.9, 1.05, 0.5, T, (0.004, 0.01, 0.04))
    object.__setattr__(s, "eta", 6.0)  # bypass validation to build a bad surface
    diag = arbitrage_diagnostics(s, SurfaceConfig())
    assert diag["sampled_violations_total"] > 0
    assert diag["min_butterfly_density_g"] < 0


def test_surface_gate_from_policy(svc):
    gate = load_policy()["surface_gate"]
    for name in gate["fixtures"]:
        art, created = svc.fit(ingest(svc, name))
        assert created
        assert art["status"] == "ok", name
        assert art["held_out"]["bid_ask_containment"] >= gate["min_holdout_bid_ask_containment"]
        assert art["truth_recovery"]["max_abs_iv_error"] <= gate["max_abs_iv_error_vs_truth"]
        assert art["truth_recovery"]["k_range"] == gate["k_range_for_truth"]
        assert art["arbitrage_diagnostics"]["sampled_violations_total"] == 0
        cond = art["guarantee"]["conditions"]
        assert cond["eta_bound_ok"] and cond["gamma_in_(0,0.5]"] and cond["theta_non_decreasing"]


def test_american_quotes_excluded_with_reason(svc):
    art, _ = svc.fit(ingest(svc, "synthetic_day1.json"))
    assert art["exclusions"]["american_exercise_not_european_observation"] == 1
    assert art["exclusions"]["crossed_quote"] == 1


def test_fit_is_immutable_and_reused(svc):
    sid = ingest(svc, "synthetic_clean_day1.json")
    a1, c1 = svc.fit(sid)
    a2, c2 = svc.fit(sid)
    assert c1 and not c2
    assert a1["created_at"] == a2["created_at"]


def test_failed_fit_is_recorded_and_previous_fit_kept(svc):
    good_id = ingest(svc, "synthetic_clean_day1.json")
    good, _ = svc.fit(good_id)
    doc = json.loads((FIXTURES / "synthetic_day2.json").read_bytes())
    first_expiry = doc["quotes"][0]["expiry"]
    doc["quotes"] = [q for q in doc["quotes"] if q["expiry"] == first_expiry]
    bad_id = ingest(svc, raw=json.dumps(doc).encode())
    bad, created = svc.fit(bad_id)
    assert created
    assert bad["status"] == "failed"
    assert bad["failure_reason"] == "insufficient_data"
    fits = {f["fit_id"]: f for f in svc.list()}
    assert fits[good["fit_id"]]["created_at"] == good["created_at"]
    assert fits[good["fit_id"]]["stale"] is True  # a newer snapshot exists
    assert fits[bad["fit_id"]]["status"] == "failed"
    with pytest.raises(DomainError, match="failed fit"):
        svc.surface(bad["fit_id"])


def test_later_snapshot_evaluated_without_refit(svc):
    sid = ingest(svc, "synthetic_day1.json")
    later = ingest(svc, "synthetic_day2.json")
    art, _ = svc.fit(sid, later)
    ls = art["later_snapshot"]
    assert ls["snapshot_id"] == later
    assert ls["elapsed_days"] == pytest.approx(1.0)
    # The day-2 generating surface is 1 vol point lower: a fixed surface must misprice it.
    assert ls["price_rmse"] > 5 * art["held_out"]["price_rmse"]
