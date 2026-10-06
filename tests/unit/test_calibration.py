"""Heston calibration: exact recovery, noisy snapshot, artifacts and failure paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from options_engine.adapters.snapshots import synthetic
from options_engine.application.heston_calibration import (
    PARAM_NAMES,
    HestonCalibrationConfig,
    HestonCalibrationService,
    calibrate_arrays,
)
from options_engine.application.surface import SurfaceConfig, SurfaceService, weights
from options_engine.models.heston import HestonModel
from options_engine.validation.calibration import exact_chain
from options_engine.validation.policy import load_policy

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "snapshots"


def test_fixture_truth_matches_predeclared_policy():
    pol = load_policy()["heston_calibration"]
    assert list(synthetic.HESTON_TRUTH_DAY1) == pol["truth_day1"]
    assert pol["truth_day2_v0"] == synthetic.HESTON_TRUTH_DAY2_V0


def test_exact_prices_recover_parameters():
    truth = HestonModel(0.04, 1.5, 0.04, 0.3, -0.7)
    a = exact_chain(truth)
    _, best = calibrate_arrays(a, weights(a, SurfaceConfig()), HestonCalibrationConfig(restarts=2))
    assert best is not None
    for i, name in enumerate(PARAM_NAMES):
        assert best.x[i] == pytest.approx(getattr(truth, name), rel=1e-4)


@pytest.fixture(scope="module")
def calibrated(tmp_path_factory):
    svc = HestonCalibrationService(tmp_path_factory.mktemp("data"))
    d1 = svc.snapshots.ingest_file(FIXTURES / "synthetic_heston_day1.json")[0]["snapshot_id"]
    d2 = svc.snapshots.ingest_file(FIXTURES / "synthetic_heston_day2.json")[0]["snapshot_id"]
    art, created = svc.calibrate(d1, d2)
    return svc, d1, d2, art, created


def test_noisy_snapshot_calibration(calibrated):
    _, _, _, art, created = calibrated
    assert created and art["status"] == "ok"
    assert art["held_out"]["bid_ask_containment"] >= 0.9
    tr = art["truth_recovery"]
    assert tr["max_abs_iv_error_vs_truth"] < 0.005
    assert all(abs(z) <= 3 for z in tr["errors_in_standard_errors"].values())
    unc = art["uncertainty"]
    # forward uncertainty is propagated: total SE >= quote-noise-only SE
    for n in PARAM_NAMES:
        assert unc["standard_errors"][n] >= unc["standard_errors_quote_noise_only"][n]
    assert unc["forward_uncertainty_expiries"] == 5


def test_next_day_needs_only_the_variance_state(calibrated):
    later = calibrated[3]["later_snapshot"]
    assert later["no_refit"]["bid_ask_containment"] < 0.2
    assert later["v0_refit"]["held_out"]["bid_ask_containment"] >= 0.9
    assert later["v0_refit"]["v0"] == pytest.approx(synthetic.HESTON_TRUTH_DAY2_V0, rel=0.01)


def test_calibration_is_idempotent_and_listed(calibrated):
    svc, d1, d2, art, _ = calibrated
    again, created = svc.calibrate(d1, d2)
    assert not created and again["calibration_id"] == art["calibration_id"]
    assert [r["calibration_id"] for r in svc.list()] == [art["calibration_id"]]


def test_ssvi_fit_on_heston_snapshot_has_no_ssvi_truth(calibrated):
    svc, d1, *_ = calibrated
    fit, _ = SurfaceService(svc.snapshots.store.root.parent).fit(d1)
    assert fit["status"] == "ok" and fit["truth_recovery"] is None


def test_insufficient_data_is_recorded_as_failure(tmp_path):
    import json

    doc = json.loads((FIXTURES / "synthetic_heston_day1.json").read_text())
    doc["quotes"] = doc["quotes"][:6]
    svc = HestonCalibrationService(tmp_path)
    sid = svc.snapshots.ingest(json.dumps(doc).encode())[0]["snapshot_id"]
    art, _ = svc.calibrate(sid)
    assert art["status"] == "failed" and art["failure_reason"] == "insufficient_data"
