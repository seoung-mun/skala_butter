from datetime import date, timedelta

import numpy as np
import pytest


def test_metric_contract_and_undefined_values():
    from buttercast.metrics import accuracy

    scores = accuracy([100, 200], [90, 220])
    assert scores["rmse"]["value"] == pytest.approx(15.8113883)
    assert scores["wape"]["value"] == 10
    assert scores["bias"]["value"] == -5
    assert accuracy([0, 0], [1, 2])["wape"]["value"] is None
    assert accuracy([2, 2], [1, 2])["pearson"]["status"] == "undefined"
    assert accuracy([], [])["rmse"]["status"] == "no_data"


def test_drift_requires_real_observations_and_calibration():
    from buttercast.metrics import drift

    assert drift(list(range(26)))["ks"]["status"] == "insufficient"
    result = drift(list(range(52)) + list(range(100, 113)))
    assert result["ks"]["value"] == 1
    assert result["ks"]["status"] == "uncalibrated"
    assert result["ks"]["n"] == 13


def test_mature_forecasts_exclude_future_labels_and_separate_versions():
    from buttercast.metrics import matured

    rows = [dict(issued_at="2025-01-01", target_date="2025-01-29", actual_known_at="2025-02-01",
                 actual=100, prediction=90, version="v1", horizon_days=28),
            dict(issued_at="2025-01-01", target_date="2025-01-29", actual_known_at="2025-03-01",
                 actual=9999, prediction=1, version="v1", horizon_days=28),
            dict(issued_at="2025-01-01", target_date="2025-01-29", actual_known_at="2025-02-01",
                 actual=100, prediction=1, version="v2", horizon_days=28)]
    assert len(matured(rows, "2025-02-05", "v1", 28)) == 1


def test_service_no_traffic_and_polling_exclusion():
    from buttercast.metrics import service_summary

    result = service_summary([], 300, now=1000)
    assert result["success_rate"]["value"] is None
    assert result["request_count"]["value"] == 0
    rows = [dict(ts=999, path="/api/metrics", status=200, latency_ms=3, category="observation"),
            dict(ts=999, path="/api/predict", status=500, latency_ms=2000, category="business"),
            dict(ts=998, path="/api/predict", status=200, latency_ms=100, category="business")]
    result = service_summary(rows, 300, now=1000)
    assert result["request_count"]["value"] == 2
    assert result["error_rate"]["value"] == 50
    assert result["success_latency_p95"]["value"] == 100


def test_training_samples_obey_availability_and_calendar():
    from buttercast.models import build_samples

    start = date(2020, 1, 1)
    rows = [dict(date=(start + timedelta(days=14*i)).isoformat(),
                 available_on=(start + timedelta(days=14*i+4)).isoformat(), midpoint=100+i)
            for i in range(80)]
    samples = build_samples(rows, sequence=6)
    assert samples[0]["target_date"] == "2020-04-08"
    assert samples[0]["issued_at"] < samples[0]["target_date"]
    assert np.asarray(samples[0]["x"]).shape == (6, 1)
    rows[7]["date"] = "2020-04-09"
    assert len(build_samples(rows, sequence=6)) < len(samples)


def test_csv_serialization_and_corrupt_file_is_not_swallowed(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from buttercast.store import Store

    store = Store(tmp_path)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: store.append("events", {"i": i}), range(40)))
    assert sorted(x["i"] for x in store.read("events")) == list(range(40))
    (tmp_path / "events.csv").write_text("id,payload\n1,broken\n")
    with pytest.raises(ValueError, match="events"):
        store.read("events")
