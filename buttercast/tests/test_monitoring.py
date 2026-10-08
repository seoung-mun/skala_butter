

def test_model_metric_timestamp_does_not_change_without_new_ground_truth(tmp_path):
    from fastapi.testclient import TestClient

    from buttercast.app import create_app

    app = create_app(tmp_path)
    with TestClient(app) as client:
        before = client.get("/api/metrics").json()["model"]["rmse"]["updated_at"]
        app.state.ops.collect()
        after = client.get("/api/metrics").json()["model"]["rmse"]["updated_at"]
        assert before == after


def test_service_alert_checks_are_at_least_sixty_seconds_apart(tmp_path):
    from buttercast.operations import check_alert
    from buttercast.store import Store

    store = Store(tmp_path)
    assert check_alert(store, "service", True, "minute-1", 59, 60) == 1
    assert check_alert(store, "service", True, "minute-2", 61, 60) is None
    assert check_alert(store, "service", True, "minute-2", 120, 60) == 2


def test_relationship_drift_uses_calibration_and_constant_is_undefined():
    from buttercast.metrics import relationship_drift

    rows = [dict(midpoint=5000+i, low=4900, high=4901+i) for i in range(52)]
    rows += [dict(midpoint=5200+i, low=4900, high=5100-i) for i in range(13)]
    result = relationship_drift(rows, {"price_width_level": [.1]*20})
    assert result["price_width_level"]["value"] == 2
    assert result["price_width_level"]["status"] == "exceeded"
    assert result["price_width_change"]["status"] == "undefined"
