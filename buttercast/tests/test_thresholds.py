import pytest

from buttercast.data import AUDIT, load_eu
from buttercast.store import Store


def test_threshold_boundary_reset_and_duplicate_observation(tmp_path):
    from buttercast.monitoring import evaluate_input

    store = Store(tmp_path)
    def check(value, token):
        return evaluate_input(store, "ks", value, .5, token, "dataset-v1")
    assert check(.49, "1")["count"] == 0
    assert check(.5, "2")["count"] == 0
    assert check(.6, "3")["count"] == 1
    assert check(.6, "3")["count"] == 1
    assert not store.read("alerts")
    assert check(.6, "4")["count"] == 2
    assert len(store.read("alerts")) == 1
    assert check(.6, "4")["count"] == 2
    assert len(store.read("alerts")) == 1
    assert check(.3, "5")["count"] == 0
    assert store.latest("alerts")["status"] == "resolved"
    assert check(.6, "6")["count"] == 1


def test_calibration_uses_only_past_and_preserves_units():
    from buttercast.data import AUDIT, load_eu
    from buttercast.monitoring import calibrate

    rows = load_eu(AUDIT / "eu-butter-weekly.csv")
    result = calibrate(rows, cutoff=rows[200]["available_on"])
    assert result["calibration_n"] >= 20
    assert result["end"] < rows[200]["available_on"]
    assert result["unit"] == "EUR/100kg"
    altered = [dict(row) for row in rows]
    for row in altered[200:]:
        row["midpoint"] *= 100
    assert result == calibrate(altered, cutoff=rows[200]["available_on"])


def test_uncalibrated_cannot_open_an_alert(tmp_path):
    from buttercast.monitoring import evaluate_input

    store = Store(tmp_path)
    result = evaluate_input(store, "ks", 1, None, "1", "dataset-v1")
    assert result["status"] == "uncalibrated"
    assert not store.read("alerts")


def test_input_drift_uses_returns_so_price_level_alone_is_not_drift():
    from buttercast.metrics import drift
    from buttercast.monitoring import weekly_returns

    rows = load_eu(AUDIT / "eu-butter-weekly.csv")[:200]
    scaled = [dict(row, midpoint=row["midpoint"]*10) for row in rows]
    assert weekly_returns(scaled) == pytest.approx(weekly_returns(rows))
    assert drift(weekly_returns(scaled), unit="log return")["ks"]["value"] == pytest.approx(
        drift(weekly_returns(rows), unit="log return")["ks"]["value"])
