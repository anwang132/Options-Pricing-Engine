from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from options_engine.interfaces.http.app import create_app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(workers=0, max_in_flight=4, timeout_seconds=30)) as c:
        yield c


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


def test_health_and_engines(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    engines = {e["engine_id"]: e for e in client.get("/api/v1/engines").json()}
    assert set(engines) == {
        "bsm_analytic",
        "crr_tree",
        "mc_terminal_gbm",
        "heston_fourier",
        "lsm_american",
    }
    assert engines["mc_terminal_gbm"]["capabilities"]["exercise_styles"] == ["european"]


def test_price(client):
    r = client.post("/api/v1/price", json=example("price_hull_call.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert round(body["price"], 2) == 4.76
    assert body["schema_version"] == "1"
    greeks = {g["name"]: g for g in body["greeks"]}
    assert greeks["vega"]["display_unit"] == "per 1 vol point"
    assert r.headers["x-request-id"]


def test_american_with_dividends(client):
    r = client.post("/api/v1/price", json=example("price_american_put_dividends.json"))
    assert r.status_code == 200, r.text
    assert r.json()["dividend_treatment"] == "escrowed_cash"


def test_schema_validation_error_is_structured(client):
    body = example("price_hull_call.json")
    body["contract"]["expiry"] = "2027-03-30T08:00:00"  # no timezone
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"


def test_domain_errors_are_structured(client):
    body = example("price_hull_call.json")
    body["contract"]["exercise_style"] = "american"
    body["engine"] = {"engine": "mc_terminal_gbm"}
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "unsupported_combination"
    assert err["details"]["engines_supporting_request"] == ["crr_tree", "lsm_american"]


def test_work_limit(client):
    body = example("price_hull_call.json")
    body["engine"] = {"engine": "mc_terminal_gbm", "paths": 50_000_000}
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "work_limit_exceeded"


def test_compare(client):
    r = client.post("/api/v1/compare", json=example("compare_otm_put.json"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reference_engine"] == "bsm_analytic"
    ids = [c["engine_id"] for c in body["comparisons"]]
    assert ids == ["bsm_analytic", "crr_tree", "mc_terminal_gbm"]
    mc = body["comparisons"][2]
    assert "standard errors" in mc["interpretation"]


def test_convergence_endpoints(client):
    base = example("compare_otm_put.json")
    r = client.post("/api/v1/convergence/tree", json={**base, "steps": [10, 11, 50, 51]})
    assert r.status_code == 200, r.text
    pts = r.json()["points"]
    assert [p["parity"] for p in pts] == ["even", "odd", "even", "odd"]
    r = client.post("/api/v1/convergence/mc", json={**base, "path_counts": [1000, 10000]})
    assert r.status_code == 200, r.text
    assert len(r.json()["points"]) == 2
    r = client.post("/api/v1/convergence/tree", json={**base, "steps": [5001]})
    assert r.status_code == 413


def test_implied_vol(client):
    r = client.post("/api/v1/implied-vol", json=example("iv_quotes.json"))
    assert r.status_code == 200, r.text
    res = {x["label"]: x for x in r.json()["results"]}
    assert res["bid"]["implied_vol"] < res["ask"]["implied_vol"]
    assert res["bid"]["price_resolution"] == pytest.approx(0.005)


def test_implied_vol_crossed_quote(client):
    body = example("iv_quotes.json")
    body["bid"], body["ask"] = "7.00", "6.00"
    r = client.post("/api/v1/implied-vol", json=body)
    assert r.status_code == 422
    assert "crossed" in r.json()["error"]["message"]


def test_manifest_replay_round_trip(client):
    body = example("price_hull_call.json")
    body["engine"] = {"engine": "mc_terminal_gbm", "paths": 20000, "seed": 5}
    m = client.post("/api/v1/price/manifest", json=body)
    assert m.status_code == 200, m.text
    manifest = m.json()
    assert manifest["environment"]["numpy"]
    r = client.post("/api/v1/replay", json=manifest)
    assert r.status_code == 200, r.text
    assert r.json()["bitwise_identical"] is True


def test_unknown_api_path(client):
    assert client.get("/api/v1/nope").status_code == 404


def test_admission_control():
    import asyncio

    from options_engine.interfaces.http.worker import BusyError, ComputeRunner

    runner = ComputeRunner(workers=0, max_in_flight=0, timeout_seconds=1)
    with pytest.raises(BusyError):
        asyncio.run(runner.run("price", "{}"))


def test_process_pool_worker():
    """The spawn-based pool imports cleanly and returns results."""
    with TestClient(create_app(workers=1, max_in_flight=2, timeout_seconds=60)) as c:
        r = c.post("/api/v1/price", json=example("price_hull_call.json"))
        assert r.status_code == 200, r.text


def test_timeout_does_not_release_capacity_until_job_finishes(monkeypatch):
    """Regression: a timed-out client must not free a slot whose job is still running."""
    import asyncio
    import threading

    from pydantic import BaseModel

    from options_engine.interfaces.http import worker

    release = threading.Event()

    class Empty(BaseModel):
        pass

    def slow(_: Empty) -> dict[str, bool]:
        release.wait(5)
        return {"done": True}

    monkeypatch.setitem(worker.HANDLERS, "slow", (Empty, slow))
    runner = worker.ComputeRunner(workers=0, max_in_flight=1, timeout_seconds=0.05)

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await runner.run("slow", "{}")
        assert runner.in_flight == 1  # still running: capacity must stay taken
        with pytest.raises(worker.BusyError):
            await runner.run("slow", "{}")
        release.set()
        for _ in range(100):
            if runner.in_flight == 0:
                break
            await asyncio.sleep(0.01)
        assert runner.in_flight == 0

    asyncio.run(scenario())
    runner.shutdown()


def test_heston_price_defaults_to_fourier_engine(client):
    body = example("price_heston_call.json")
    del body["engine"]
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["engine_id"] == "heston_fourier"
    assert out["assumptions"]["model"].startswith("Heston")
    greeks = {g["name"]: g for g in out["greeks"]}
    assert greeks["vega"]["status"] == "not_supported"
    assert greeks["delta"]["status"] == "ok"


def test_heston_rejected_by_black_scholes_engine(client):
    body = example("price_heston_call.json")
    body["engine"] = {"engine": "bsm_analytic"}
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unsupported_combination"


def test_invalid_heston_parameters(client):
    body = example("price_heston_call.json")
    body["model"]["rho"] = -1.0
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_model_parameter"


def test_smile_endpoint(client):
    r = client.post("/api/v1/visuals/smile", json=example("price_heston_call.json"))
    assert r.status_code == 200, r.text
    smile = r.json()
    assert smile["model"] == "Heston"
    assert len(smile["points"]) == 41
    assert smile["skew"] > 0


def test_lsm_american_price_with_uncertainty(client):
    body = example("price_american_put_dividends.json")
    body["engine"] = {"engine": "lsm_american", "paths": 20000, "regression_paths": 10000}
    r = client.post("/api/v1/price", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["engine_id"] == "lsm_american"
    assert out["uncertainty"]["standard_error"] > 0
    tree = client.post(
        "/api/v1/price", json={**body, "engine": {"engine": "crr_tree", "steps": 2000}}
    ).json()
    assert abs(out["price"] - tree["price"]) < 5 * out["uncertainty"]["standard_error"] + 0.05
